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
iproute.py - thin abstraction over `ip route` so the committer can be
tested without touching the kernel.

The interface is just what the committer needs: list routes, install a
route, delete a route, delete a wg interface.  Two implementations:

  RealIPRoute - subprocess-calls /usr/sbin/ip and /usr/bin/wg.  Used in
                production by the committer's __main__.

  FakeIPRoute - an in-memory route table + tunnel-iface set.  Used by
                the test suite.  Records every call in a log so tests
                can assert on the exact sequence of operations.

Both expose the same method signatures.  No duck typing - committer.py
takes an IPRoute argument and calls the methods on it.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from .planner import Route, InstallRoute, RemoveRoute


class IPRoute(Protocol):
    def list_routes(self) -> list[Route]:
        """Return every non-kernel route in the main table."""
        ...

    def install(self, r: InstallRoute) -> bool:
        """Add one route.  Returns True on success."""
        ...

    def install_admin_alias(self, r: InstallRoute, alias_cidr: str) -> bool:
        """Add the .2/32 admin-alias route paired with `r`'s /24.  Same
        (dev, via, onlink, src) as the /24; metric=ADMIN_ALIAS_METRIC.
        Returns True on success.  See [ADMIN_ALIAS_ROUTE_V1] in planner.py
        for why this exists."""
        ...

    def remove_admin_alias(self, alias_cidr: str) -> bool:
        """Delete the .2/32 admin-alias route paired with a removed /24.
        Returns True on success (treats 'route doesn't exist' as success
        since concurrent probes may have already removed or rewritten it)."""
        ...

    def remove(self, r: RemoveRoute) -> bool:
        """Delete one specific route, matched by exact (dest, via, dev,
        metric).  Returns True on success."""
        ...

    def list_stale_probe_routes(self) -> list[str]:
        """Return any /32 routes whose destination ends in .2 - these
        are leaked probes from a previous run and should be deleted."""
        ...

    def delete_probe_route(self, probe_cidr: str) -> bool:
        """Delete a /32 route (any dev/via) by its destination CIDR."""
        ...

    def tear_down_wg(self, iface: str) -> bool:
        """Bring down and remove a wgN interface.  Returns True on
        success."""
        ...

    def list_active_wg(self) -> list[str]:
        """Return every wgN interface currently configured."""
        ...


# ---------------------------------------------------------------------------
# Production implementation
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> tuple[int, str, str]:
    r = subprocess.run(argv, capture_output=True, text=True, check=False)
    return r.returncode, r.stdout, r.stderr


class RealIPRoute:
    """Real implementation - shells out to /usr/sbin/ip and /usr/bin/wg."""

    def __init__(self, ip_bin: str = "/usr/sbin/ip",
                 wg_bin: str = "/usr/bin/wg",
                 wg_quick_bin: str = "/usr/bin/wg-quick"):
        self._ip = ip_bin
        self._wg = wg_bin
        self._wg_quick = wg_quick_bin

    def list_routes(self) -> list[Route]:
        rc, out, _err = _run([self._ip, "-4", "route", "show"])
        if rc != 0:
            return []
        routes: list[Route] = []
        for line in out.splitlines():
            parts = line.strip().split()
            if not parts or "dev" not in parts:
                continue
            dest = parts[0]
            # Skip defaults, linkdowns, and anything that isn't a /24 (we
            # only reason about FrogNet /24s here; /32 probes are swept
            # separately by list_stale_probe_routes).
            if not dest.endswith("/24"):
                continue
            # Skip kernel-owned scope-link entries.
            if "proto" in parts:
                i = parts.index("proto")
                if i + 1 < len(parts) and parts[i + 1] == "kernel":
                    continue
            dev = ""
            via = ""
            metric = 0
            if "dev" in parts:
                i = parts.index("dev")
                if i + 1 < len(parts):
                    dev = parts[i + 1]
            if "via" in parts:
                i = parts.index("via")
                if i + 1 < len(parts):
                    via = parts[i + 1]
            if "metric" in parts:
                i = parts.index("metric")
                if i + 1 < len(parts):
                    try:
                        metric = int(parts[i + 1])
                    except ValueError:
                        metric = 0
            routes.append(Route(dest=dest, dev=dev, via=via, metric=metric))
        return routes

    def install(self, r: InstallRoute) -> bool:
        """Install one route.

        Three shapes are valid:
            dest dev wgN                          (wg direct, allowed-ips
                                                   covers dest; no via,
                                                   no onlink)
            dest via NEXT_HOP dev DEV             (LAN; via is on a
                                                   connected subnet)
            dest via PEER_ONE dev wgN onlink      (wg relay; peer.1 is
                                                   outside dest, onlink
                                                   tells kernel to skip
                                                   the connected-subnet
                                                   check on the via)

        The planner picks shape via _winning_via / _requires_onlink;
        here we just translate that to argv. `onlink` without `via` is
        meaningless and the kernel rejects it - we drop the flag in
        that case as a defensive measure.
        """
        from .planner import ROUTE_METRIC
        # [FALLBACK_METRIC_V1 2026-05-31] Honor the metric field on the
        # InstallRoute when present.  Older callers that didn't set
        # `metric` get ROUTE_METRIC (winner) by default via the field's
        # default in the dataclass.  Defensive `getattr` covers any
        # caller still constructing InstallRoute via a path that
        # bypasses the dataclass default (raw `__new__`, deserialized
        # from cached JSON, etc.).
        metric = getattr(r, "metric", ROUTE_METRIC)
        argv = [self._ip, "route", "replace", r.dest]
        if r.via:
            argv += ["via", r.via]
        argv += ["dev", r.dev, "metric", str(metric)]
        if r.onlink and r.via:
            argv.append("onlink")
        # [TUNNEL_SRC_HINT_V1] Pin src for tunnel routes.  Without
        # this the kernel picks the wg iface's own /30 transit src,
        # and peers drop the decrypted packet at AllowedIPs check.
        if getattr(r, "src", ""):
            argv += ["src", r.src]
        rc, _out, _err = _run(argv)
        return rc == 0

    def remove(self, r: RemoveRoute) -> bool:
        argv = [self._ip, "route", "del", r.dest]
        if r.via:
            argv += ["via", r.via]
        if r.dev:
            argv += ["dev", r.dev]
        argv += ["metric", str(r.metric)]
        rc, _out, _err = _run(argv)
        return rc == 0

    def install_admin_alias(self, r: InstallRoute, alias_cidr: str) -> bool:
        """[ADMIN_ALIAS_ROUTE_V1] Install the .2/32 admin alias paired
        with a /24.  Same (dev, via, onlink, src) as `r`; metric is
        ADMIN_ALIAS_METRIC.

        Uses `ip route replace` so any leftover tmp /32 left behind by
        sync_interfaces.sh (KEEP_TMP_ROUTES_V1) at the same metric is
        overwritten authoritatively.
        """
        from .planner import ADMIN_ALIAS_METRIC
        argv = [self._ip, "route", "replace", alias_cidr]
        if r.via:
            argv += ["via", r.via]
        argv += ["dev", r.dev, "metric", str(ADMIN_ALIAS_METRIC)]
        if r.onlink and r.via:
            argv.append("onlink")
        if getattr(r, "src", ""):
            argv += ["src", r.src]
        rc, _out, _err = _run(argv)
        return rc == 0

    def remove_admin_alias(self, alias_cidr: str) -> bool:
        """[ADMIN_ALIAS_ROUTE_V1] Delete the admin alias /32 paired with
        a /24 we're removing.  Tolerant of 'route doesn't exist' since a
        concurrent probe may have already deleted or rewritten it -
        either way the desired end state (no /32 we own) is reached.

        Doesn't constrain (dev, via, metric) because the committer is the
        sole authoritative writer of this /32: whatever's there should
        match what the committer last wrote, but if it doesn't, removing
        whatever is there is still correct.
        """
        rc, _out, _err = _run([self._ip, "route", "del", alias_cidr])
        # rc=2 from `ip route del` means "No such process" i.e. route
        # absent.  Treat as success.
        return rc == 0 or rc == 2

    def list_stale_probe_routes(self) -> list[str]:
        """Return every host route whose destination is a .2 address at
        the probe metric.  These are the probe aliases installed by
        sync_interfaces - any that survive a merge pass are leaks and
        must be cleaned up.

        [STALE_PROBE_FILTER_V2] Two prior bugs in this function fixed:

        1. Filter required `dest.endswith("/32")` but `ip route show`
           does NOT emit a `/32` suffix for host routes - a /32 line
           looks like `10.111.11.2 dev wg0 scope link metric 6`, not
           `10.111.11.2/32 dev wg0 scope link metric 6`.  The check
           dropped EVERY line.  Function returned [] on every host.

        2. Filter matched `"metric 5"` - but planner.ADMIN_ALIAS_METRIC
           is 5 (committer-owned admin aliases), and sync_interfaces'
           MET_TMP is 6 (the actual probe metric).  Even if (1) were
           fixed, the filter would delete committer-owned aliases
           instead of probe leaks.

        Correct filter: a /32 host route as printed by `ip route show`
        (no /32 suffix on dest, single IP), with metric 6, with a
        destination ending in .2 (probe aliases for peer admin IPs).
        Returns the destination as a CIDR (`.../32`) for the caller's
        `ip route del` to be unambiguous.
        """
        from .planner import ADMIN_ALIAS_METRIC  # = 5, NOT what we want
        PROBE_METRIC = 6  # sync_interfaces.sh MET_TMP
        rc, out, _err = _run([self._ip, "-4", "route", "show"])
        if rc != 0:
            return []
        stale: list[str] = []
        for line in out.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            dest = parts[0]
            # `ip route show` formats a /24 as "10.x.y.0/24" and a host
            # route as bare "10.x.y.z" (no /32 suffix).  Distinguish by
            # presence of a slash.
            if "/" in dest:
                continue
            if not (dest.startswith("10.") and dest.endswith(".2")):
                continue
            if f"metric {PROBE_METRIC}" not in line:
                continue
            # Guard: never sweep a route that's at ADMIN_ALIAS_METRIC.
            # Defense-in-depth in case sync_interfaces is ever
            # reconfigured to a different MET_TMP.
            if f"metric {ADMIN_ALIAS_METRIC}" in line:
                continue
            stale.append(f"{dest}/32")
        return stale

    def delete_probe_route(self, probe_cidr: str) -> bool:
        rc, _out, _err = _run([self._ip, "route", "del", probe_cidr])
        return rc == 0

    def tear_down_wg(self, iface: str) -> bool:
        # Use wg-quick down so that the conf file's PostDown hooks run
        # (if any) and the iface is cleanly torn down.  Fall back to
        # `ip link del` if wg-quick can't manage it (e.g. no conf file).
        rc, _out, _err = _run([self._wg_quick, "down", iface])
        if rc == 0:
            return True
        rc, _out, _err = _run([self._ip, "link", "del", iface])
        return rc == 0

    def list_active_wg(self) -> list[str]:
        rc, out, _err = _run([self._wg, "show", "interfaces"])
        if rc != 0:
            return []
        return [x for x in out.strip().split() if x]


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------

@dataclass
class FakeIPRoute:
    """In-memory IPRoute for the test suite.

    Fields:
      routes      - current route table as a list of Route.  Tests seed
                    this to represent "what the kernel already has".
      wg_ifaces   - set of active wgN names.
      stale_32s   - /32 probe aliases pretending to be in the kernel.
      call_log    - every method call in order, as a list of tuples.
                    Tests assert on the sequence so we can verify the
                    one-at-a-time invariant.
      install_fails - destinations whose install() should return False,
                      for testing error paths.
      remove_fails  - set of (dest, dev, via, metric) tuples for which
                      remove() should return False.
    """
    routes: list[Route] = field(default_factory=list)
    wg_ifaces: set[str] = field(default_factory=set)
    stale_32s: list[str] = field(default_factory=list)
    call_log: list[tuple] = field(default_factory=list)
    install_fails: set[str] = field(default_factory=set)
    remove_fails: set[tuple] = field(default_factory=set)

    def list_routes(self) -> list[Route]:
        self.call_log.append(("list_routes",))
        # Return a copy so tests can't mutate our state accidentally.
        return list(self.routes)

    def install(self, r: InstallRoute) -> bool:
        self.call_log.append(("install", r.dest, r.dev, r.via, r.onlink))
        if r.dest in self.install_fails:
            return False
        # Simulate `ip route replace`: remove any existing route for
        # (dest, dev, via), then add.  Preserves the "one /24 per
        # (dest, dev, via)" invariant that the kernel itself gives us.
        self.routes = [
            rt for rt in self.routes
            if not (rt.dest == r.dest and rt.dev == r.dev and rt.via == r.via)
        ]
        from .planner import ROUTE_METRIC
        # [FALLBACK_METRIC_V1 fidelity] Honor the metric field exactly as
        # RealIPRoute.install does. Flattening every route to ROUTE_METRIC
        # made the fake diverge from the real kernel: the planner's
        # metric-100 fallback came back as metric-22, so the planner
        # remove+reinstalled it every merge (period-2 route churn) because
        # `ip route` keys a route by (dest, dev, metric). The default keeps
        # old callers (no metric set) at ROUTE_METRIC.
        metric = getattr(r, "metric", ROUTE_METRIC)
        self.routes.append(
            Route(dest=r.dest, dev=r.dev, via=r.via, metric=metric)
        )
        return True

    def remove(self, r: RemoveRoute) -> bool:
        self.call_log.append(("remove", r.dest, r.dev, r.via, r.metric))
        key = (r.dest, r.dev, r.via, r.metric)
        if key in self.remove_fails:
            return False
        before = len(self.routes)
        self.routes = [
            rt for rt in self.routes
            if not (rt.dest == r.dest and rt.dev == r.dev
                    and rt.via == r.via and rt.metric == r.metric)
        ]
        return len(self.routes) < before

    def install_admin_alias(self, r: InstallRoute, alias_cidr: str) -> bool:
        self.call_log.append(("install_admin_alias", alias_cidr, r.dev, r.via, r.onlink))
        if alias_cidr in self.install_fails:
            return False
        from .planner import ADMIN_ALIAS_METRIC
        # Simulate `ip route replace`: drop existing entries for this alias,
        # then add.
        self.routes = [rt for rt in self.routes if rt.dest != alias_cidr]
        self.routes.append(
            Route(dest=alias_cidr, dev=r.dev, via=r.via, metric=ADMIN_ALIAS_METRIC)
        )
        return True

    def remove_admin_alias(self, alias_cidr: str) -> bool:
        self.call_log.append(("remove_admin_alias", alias_cidr))
        before = len(self.routes)
        self.routes = [rt for rt in self.routes if rt.dest != alias_cidr]
        # Tolerant of absence - matches RealIPRoute semantics.
        return True

    def list_stale_probe_routes(self) -> list[str]:
        self.call_log.append(("list_stale_probe_routes",))
        return list(self.stale_32s)

    def delete_probe_route(self, probe_cidr: str) -> bool:
        self.call_log.append(("delete_probe_route", probe_cidr))
        if probe_cidr in self.stale_32s:
            self.stale_32s.remove(probe_cidr)
            return True
        return False

    def tear_down_wg(self, iface: str) -> bool:
        self.call_log.append(("tear_down_wg", iface))
        if iface in self.wg_ifaces:
            self.wg_ifaces.discard(iface)
            # Remove any routes that pointed at this iface, mirroring
            # the kernel's behavior when an iface goes down.
            self.routes = [rt for rt in self.routes if rt.dev != iface]
            return True
        return False

    def list_active_wg(self) -> list[str]:
        self.call_log.append(("list_active_wg",))
        return sorted(self.wg_ifaces)
