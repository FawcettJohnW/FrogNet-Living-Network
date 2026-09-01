################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
kernel.py - the route-mutation chokepoint, faithfully ported from
sync_interfaces.sh's RTMUT (line 49) and its readback.

In bash, RTMUT is literally:  `$IP "$@"` ; then `ip -o -4 route show $dest`.
Everything that writes a route in discovery funnels through it. That makes
the *kernel itself* the real-vs-sim swap point, at `ip route` argv granularity.

We deliberately do NOT reuse frognet_route.iproute.FakeIPRoute: that double is
route-object granularity (install/remove) and collapses every install to
ROUTE_METRIC, keyed on (dest, dev, via). The oracle merge log proves the real
kernel keys replace/del on (dest, metric) - that is the only reason
10.130.130.0/24 can hold `dev wg1 metric 22` AND `dev wg2 metric 100` at once.
So discovery gets its own argv-faithful engine.

Contract (both backends expose the same two methods):
    route(*args) -> int          # runs `ip route <args>`, returns rc
    route_show(dest="") -> str    # `ip -o -4 route show [dest]`, ';'-joined like RTMUT readback

RTMUT() in routes.py calls these and logs the [DIAG-ROUTE] MUTATE line, so the
caller path matches bash exactly.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol


# ---------------------------------------------------------------------------
# dest / entry normalization - mirror how `ip route show` prints things
# ---------------------------------------------------------------------------

def normalize_dest(dest: str) -> str:
    """A host route is printed by `ip route show` WITHOUT the /32 suffix
    ("10.111.11.2"), a /24 or /30 keeps its suffix. Mirror that so a route
    installed as `10.111.11.2/32` is found by `route show 10.111.11.2` and
    vice-versa. (See iproute.py STALE_PROBE_FILTER_V2 note: /32 has no suffix.)"""
    if dest.endswith("/32"):
        return dest[:-3]
    return dest


def _ip_to_int(ip: str) -> int:
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def _dest_sort_key(dest: str):
    """Match the ordering observed in the oracle's final `ip r`: ascending by
    network address (prefix length is not the primary key - 10.253.203.88/30
    sorts before 10.254.1.0/24 by address)."""
    net = dest.split("/")[0]
    try:
        return _ip_to_int(net)
    except ValueError:
        return -1


@dataclass
class RouteEntry:
    """One kernel route line. The (dest, metric) pair is the FIB key for
    replace/del, exactly as the oracle proves."""
    dest: str                 # normalized (bare host, or x/24, x/30)
    dev: str = ""
    via: str = ""
    metric: int = 0
    onlink: bool = False
    src: str = ""
    scope: str = ""           # "link" for connected/dev routes; "" otherwise
    proto: str = ""           # "kernel" for connected routes
    linkflags: tuple = ()     # kernel link-state readback flags (linkdown/dead)

    def key(self) -> tuple:
        return (self.dest, self.metric)

    def show(self) -> str:
        """Render exactly like `ip -o -4 route show` so readback/snapshots
        compare byte-for-byte against the oracle [DIAG-ROUTE] lines."""
        parts = [self.dest]
        if self.via:
            parts += ["via", self.via]
        if self.dev:
            parts += ["dev", self.dev]
        if self.proto:
            parts += ["proto", self.proto]
        if self.scope:
            parts += ["scope", self.scope]
        if self.src:
            parts += ["src", self.src]
        if self.metric:
            parts += ["metric", str(self.metric)]
        if self.onlink:
            parts += ["onlink"]
        if self.linkflags:
            parts += list(self.linkflags)
        return " ".join(parts)


# ---------------------------------------------------------------------------
# argv parser - turn an `ip route replace/add/del ...` argv into intent
# ---------------------------------------------------------------------------

def _parse_route_argv(args: list[str]) -> tuple[str, RouteEntry | None, str]:
    """Returns (verb, entry_or_None, raw_dest).
    verb in {"replace","add","del","show",...}. For del we still parse attrs
    (esp. metric) because `del DEST metric M` is metric-scoped."""
    if not args:
        return ("", None, "")
    verb = args[0]
    rest = args[1:]
    if not rest:
        return (verb, None, "")
    raw_dest = rest[0]
    dest = normalize_dest(raw_dest)
    e = RouteEntry(dest=dest)
    i = 1
    metric_seen = False
    while i < len(rest):
        tok = rest[i]
        if tok == "via" and i + 1 < len(rest):
            e.via = rest[i + 1]; i += 2; continue
        if tok == "dev" and i + 1 < len(rest):
            e.dev = rest[i + 1]; i += 2; continue
        if tok == "metric" and i + 1 < len(rest):
            e.metric = int(rest[i + 1]); metric_seen = True; i += 2; continue
        if tok == "src" and i + 1 < len(rest):
            e.src = rest[i + 1]; i += 2; continue
        if tok == "onlink":
            e.onlink = True; i += 1; continue
        if tok in ("scope",) and i + 1 < len(rest):
            e.scope = rest[i + 1]; i += 2; continue
        if tok in ("proto",) and i + 1 < len(rest):
            e.proto = rest[i + 1]; i += 2; continue
        if tok in ("linkdown", "dead"):   # kernel link-state readback flag
            e.linkflags = e.linkflags + (tok,); i += 1; continue
        i += 1
    # Stash whether metric was explicit on a del (affects scoping).
    e._metric_explicit = metric_seen  # type: ignore[attr-defined]
    return (verb, e, raw_dest)


# ---------------------------------------------------------------------------
# Production backend
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> tuple[int, str]:
    # [KERNEL_CMD_DEADLINE_V1] An `ip` blocked on the rtnetlink lock (e.g. a
    # concurrent wg reconcile) previously hung this call FOREVER, wedging the
    # whole merge between a probe's route-replace and its route-del (proven on
    # New-York-1 + peer, 2026-07-06, both stopped at the same instruction).
    # 20s is geologic for iproute2; on expiry we kill it, say so loudly, and
    # return a distinct rc so the caller fails instead of waiting.
    try:
        r = subprocess.run(argv, capture_output=True, text=True, check=False,
                           timeout=20)
    except subprocess.TimeoutExpired:
        print(f"[KERNEL] DEADLINE {' '.join(argv)} -- killed after 20s "
              f"(rtnetlink stall? check for D-state ip/wg holders)",
              file=sys.stderr, flush=True)
        return 124, ""
    except OSError as e:
        print(f"[KERNEL] EXEC FAILED {' '.join(argv)} err={e}",
              file=sys.stderr, flush=True)
        return 127, ""
    return r.returncode, r.stdout


class RealKernel:
    """Shells out to /usr/sbin/ip, exactly as bash did."""

    def __init__(self, ip_bin: str = "/usr/sbin/ip"):
        self._ip = ip_bin

    def route(self, *args: str) -> int:
        rc, _ = _run([self._ip, "route", *args])
        return rc

    def route_show(self, dest: str = "") -> str:
        argv = [self._ip, "-o", "-4", "route", "show"]
        if dest:
            argv.append(dest)
        rc, out = _run(argv)
        if rc != 0:
            return ""
        # RTMUT joins newlines with ';'
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return ";".join(lines) + (";" if lines else "")

    def show_default(self) -> list:
        """`ip route show default` -> one line per default entry, for
        fixDefaultRoute.collect_defaults / _default_already_installed."""
        rc, out = _run([self._ip, "-o", "-4", "route", "show", "default"])
        if rc != 0:
            return []
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def table(self) -> list:
        """Real kernel table as a list of `ip route show` lines (for merge()'s
        final_table summary / the differential)."""
        return [ln for ln in self.route_show().split(";") if ln.strip()]


# ---------------------------------------------------------------------------
# Simulator backend - a faithful in-memory `ip route` engine
# ---------------------------------------------------------------------------

@dataclass
class FakeKernel:
    """In-memory route table that mirrors `ip route` semantics proven by the
    oracle. Seed `entries` with the connected/kernel routes the kernel already
    has (eth0 /24, wg /24s from bringup, transit /30s, frognet0 /24), then let
    discovery mutate it via route(). route_show() renders FIB-faithfully."""

    entries: list[RouteEntry] = field(default_factory=list)
    mutate_log: list[tuple] = field(default_factory=list)   # (verb, dest, metric, rc)
    # [ONLINK_RETRY_V1] Opt-in fidelity: model the real kernel's rc=2 "Nexthop has
    # invalid gateway" for a via-route whose gateway is NOT on a directly-connected
    # /24 and is NOT flagged onlink. Off by default so every existing oracle is
    # unaffected (the fake stays permissive); the child-uplink oracle turns it on to
    # exercise the rc=2 -> onlink retry a child needs to reach its parent through an
    # on-link uplink gateway.
    strict_gateway: bool = False

    def _gateway_reachable(self, via: str) -> bool:
        """Is `via` on one of our directly-connected /24s? (a connected route is a
        scope-link entry with no via covering the gateway's /24)."""
        if not via:
            return True
        pfx = ".".join(via.split(".")[:3])
        for e in self.entries:
            if not e.via and e.dest == f"{pfx}.0/24":
                return True
        return False

    # -- helpers ------------------------------------------------------------
    def _find(self, dest: str, metric: int):
        for idx, e in enumerate(self.entries):
            if e.dest == dest and e.metric == metric:
                return idx
        return -1

    def _src_iface_down(self, src: str) -> bool:
        """The kernel will not retain a route whose preferred-src lives on a
        no-carrier interface (linkdown/dead). Detected from the connected route
        covering src carrying a link-state flag. This is the modeled mechanism
        behind the Seattle6 negative case: with wlan0 (identity, src 10.160.160.1)
        linkdown, the src-pinned exit defaults the engine installs do not survive,
        which is exactly what the hardware ip r showed. Observable is reproduced;
        the exact kernel-level cause (reject-at-install vs drop-on-carrier-loss)
        is an on-box detail."""
        if not src:
            return False
        pfx = ".".join(src.split(".")[:3])
        for e in self.entries:
            if e.via:                       # only connected routes carry link state
                continue
            if e.dest == f"{pfx}.0/24" and ("linkdown" in e.linkflags or "dead" in e.linkflags):
                return True
        return False

    # -- the RTMUT-level contract ------------------------------------------
    def route(self, *args: str) -> int:
        verb, e, _raw = _parse_route_argv(list(args))
        rc = self._apply(verb, e)
        if e is not None:
            self.mutate_log.append((verb, e.dest, e.metric, rc))
        return rc

    def _apply(self, verb: str, e: RouteEntry | None) -> int:
        if e is None:
            return 2
        if verb in ("replace", "add", "append", "change"):
            if e.src and self._src_iface_down(e.src):
                return 2  # kernel won't keep a route sourced from a dead iface
            if (self.strict_gateway and e.via and not e.onlink
                    and not self._gateway_reachable(e.via)):
                return 2  # "Nexthop has invalid gateway" - needs onlink or a connected /24
            # `ip route replace` is keyed on (dest, metric): remove an existing
            # entry at the SAME (dest, metric), then insert. A different metric
            # for the same dest coexists (proven: 10.130.130.0/24 m22 + m100).
            idx = self._find(e.dest, e.metric)
            if idx >= 0:
                if verb == "add":
                    return 2  # add fails if exact key exists ("File exists")
                self.entries.pop(idx)
            elif verb == "change":
                return 2  # change fails if absent
            # kernel decorations the oracle shows on readback:
            if e.dev and not e.via and not e.scope:
                e.scope = "link"          # dev-only route -> scope link
            if e.onlink and not e.via:
                e.onlink = False          # onlink without via is meaningless
            self.entries.append(e)
            return 0
        if verb == "del":
            metric_explicit = getattr(e, "_metric_explicit", False)
            before = len(self.entries)
            if metric_explicit:
                self.entries = [
                    x for x in self.entries
                    if not (x.dest == e.dest and x.metric == e.metric)
                ]
            else:
                # `ip route del DEST` with no metric removes all entries for DEST
                self.entries = [x for x in self.entries if x.dest != e.dest]
            return 0 if len(self.entries) < before else 2  # rc=2 = No such process
        # show/get/flush etc. not used through route()
        return 0

    def route_show(self, dest: str = "") -> str:
        if dest:
            d = normalize_dest(dest)
            matches = [e for e in self.entries if e.dest == d]
            matches.sort(key=lambda e: e.metric)
            if not matches:
                return ""
            return ";".join(e.show() for e in matches) + " ;"  # RTMUT trailing
        # full table, oracle ordering: by network addr asc, then metric asc
        ordered = sorted(self.entries, key=lambda e: (_dest_sort_key(e.dest), e.metric))
        return "\n".join(e.show() for e in ordered)

    def show_default(self) -> list[str]:
        """`ip route show default` -> one show() line per default entry,
        ordered by metric (for fixDefaultRoute.collect_defaults)."""
        ds = [e for e in self.entries if e.dest == "default"]
        ds.sort(key=lambda e: e.metric)
        return [e.show() for e in ds]

    # -- convenience for the simulator/tests --------------------------------
    def table(self) -> list[str]:
        ordered = sorted(self.entries, key=lambda e: (_dest_sort_key(e.dest), e.metric))
        return [e.show() for e in ordered]

    def seed(self, *shows: str) -> None:
        """Seed from `ip route show`-style lines (what the kernel already has)."""
        for line in shows:
            verb_entry = _parse_route_argv(["__seed__", *line.split()])
            _, e, _ = verb_entry
            if e is not None:
                self.entries.append(e)


# ---------------------------------------------------------------------------
# ShadowKernel - "Kernel Route OK" dry-run for on-box bring-up.
#
# Reads the node's REAL `ip route` table once, then runs the port against an
# in-memory copy (the proven FakeKernel engine) so KEEP-vs-write and sweep
# readback stay faithful. Every mutation the port issues is APPLIED to the
# shadow and RECORDED as the exact `ip route ...` command - but NOTHING touches
# the real FIB. Use it to verify the port produces a sane real table on this
# hardware before ever letting it mutate anything.
# ---------------------------------------------------------------------------

class ShadowKernel:
    def __init__(self, real=None):
        self._real = real if real is not None else RealKernel()
        self._fake = FakeKernel()
        seed = self._real.route_show()          # real current table, ';'-joined
        lines = [s.strip() for s in seed.split(";") if s.strip()] if seed else []
        if lines:
            self._fake.seed(*lines)
        self.start_table = list(lines)
        self.planned = []                        # captured `ip route ...` commands

    def route(self, *args):
        self.planned.append("ip route " + " ".join(str(a) for a in args))
        return self._fake.route(*args)           # apply to shadow only

    def route_show(self, dest: str = "") -> str:
        return self._fake.route_show(dest)

    def show_default(self) -> list:
        return self._fake.show_default()

    def table(self):
        return self._fake.table()

    def final_table(self):
        return [ln for ln in self._fake.route_show().split(";") if ln.strip()]
