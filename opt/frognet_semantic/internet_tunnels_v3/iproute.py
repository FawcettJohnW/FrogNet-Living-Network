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
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event

import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from .planner import Route, InstallRoute, RemoveRoute


class IPRoute(Protocol):
    def list_routes(self) -> list[Route]:
        """Return every non-kernel route in the main table."""
        trace_enter('iproute.IPRoute.list_routes')
        ...

    def install(self, r: InstallRoute) -> bool:
        """Add one route.  Returns True on success."""
        trace_enter('iproute.IPRoute.install', r=repr(r))
        ...

    def install_admin_alias(self, r: InstallRoute, alias_cidr: str) -> bool:
        """Add the .2/32 admin-alias route paired with `r`'s /24.  Same
        (dev, via, onlink, src) as the /24; metric=ADMIN_ALIAS_METRIC.
        Returns True on success.  See [ADMIN_ALIAS_ROUTE_V1] in planner.py
        for why this exists."""
        trace_enter('iproute.IPRoute.install_admin_alias', r=repr(r), alias_cidr=repr(alias_cidr))
        ...

    def remove_admin_alias(self, alias_cidr: str) -> bool:
        """Delete the .2/32 admin-alias route paired with a removed /24.
        Returns True on success (treats 'route doesn't exist' as success
        since concurrent probes may have already removed or rewritten it)."""
        trace_enter('iproute.IPRoute.remove_admin_alias', alias_cidr=repr(alias_cidr))
        ...

    def remove(self, r: RemoveRoute) -> bool:
        """Delete one specific route, matched by exact (dest, via, dev,
        metric).  Returns True on success."""
        trace_enter('iproute.IPRoute.remove', r=repr(r))
        ...

    def list_stale_probe_routes(self) -> list[str]:
        """Return any /32 routes whose destination ends in .2 - these
        are leaked probes from a previous run and should be deleted."""
        trace_enter('iproute.IPRoute.list_stale_probe_routes')
        ...

    def delete_probe_route(self, probe_cidr: str) -> bool:
        """Delete a /32 route (any dev/via) by its destination CIDR."""
        trace_enter('iproute.IPRoute.delete_probe_route', probe_cidr=repr(probe_cidr))
        ...

    def tear_down_wg(self, iface: str) -> bool:
        """Bring down and remove a wgN interface.  Returns True on
        success."""
        trace_enter('iproute.IPRoute.tear_down_wg', iface=repr(iface))
        ...

    def list_active_wg(self) -> list[str]:
        """Return every wgN interface currently configured."""
        trace_enter('iproute.IPRoute.list_active_wg')
        ...


# ---------------------------------------------------------------------------
# Production implementation
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> tuple[int, str, str]:
    trace_enter('iproute._run', argv=repr(argv))
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
        trace_enter('iproute.RealIPRoute.list_routes')
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

        [DELETE_THEN_ADD_V1 2026-05-25] Previously used `ip route
        replace`.  Replace is convenient but obscures what's actually
        happening: when the (dev, via, metric) differs from the
        kernel's current entry, the kernel internally drops the old
        and adds the new in one syscall.  When it's the same, replace
        is a write-with-no-effect.  Per operator directive, use
        directed `ip route del` followed by `ip route add` so the two
        operations are visible in logs and stop the route from being
        rewritten unnecessarily.  The planner already filters out the
        no-op case before calling install(), so we know we have a
        real change to make.

        Tolerates ENOENT on the del (rc=2 from `ip route del`): the
        route may not have been there to delete (planner saw an empty
        existing-routes list for this dest, or the previous merge
        already removed it).  The add must succeed; that's what we
        report.
        """
        trace_enter('iproute.RealIPRoute.install', r=repr(r))
        from .planner import ROUTE_METRIC
        # 1. Directed delete of any current entry matching what we're
        #    about to write (same metric).  Constrains the delete to a
        #    specific (dest, metric) so we never sweep an unrelated
        #    route the operator installed at a different metric.
        del_argv = [self._ip, "route", "del", r.dest,
                    "metric", str(ROUTE_METRIC)]
        rc_del, _o_del, err_del = _run(del_argv)
        # rc=2 == "No such process" == route was absent.  Anything else
        # non-zero is unusual but not fatal - the add below is what
        # decides success.  Log only the unusual case.
        if rc_del not in (0, 2):
            # Not fatal; the kernel may reject the del with a different
            # error (e.g. RTNETLINK answers: No such file or directory)
            # if the existing entry has a different (dev,via).  Add
            # will atomically replace via NLM_F_REPLACE under the hood
            # if we use it.  But since we promised delete-then-add, we
            # keep going; if add then fails because of a residual
            # entry, we'll see it in the add error.
            (config_log := __import__("logging").getLogger(
                "frognet_route")).warning(
                "ROUTE_DEL_unexpected: %s rc=%d stderr=%s",
                r.dest, rc_del, (err_del or "").strip())

        # 2. Add the new route.
        argv = [self._ip, "route", "add", r.dest]
        if r.via:
            argv += ["via", r.via]
        argv += ["dev", r.dev, "metric", str(ROUTE_METRIC)]
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
        trace_enter('iproute.RealIPRoute.remove', r=repr(r))
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

        [DELETE_THEN_ADD_V1 2026-05-25] Was `ip route replace`.  The
        replace shape mattered here because sync_interfaces.sh's
        KEEP_TMP_ROUTES_V1 / TMP_ROUTE_SWEEP_V3 sometimes leaves a
        leftover tmp /32 at the same metric (5 == ADMIN_ALIAS_METRIC
        == MET_TMP).  Replace clobbered whatever was there in one
        syscall.  With the new del-then-add: explicitly delete any
        entry for this exact alias_cidr (no metric/dev/via match
        required - there should only ever be one /32 for this dest),
        then add.

        The unconstrained `ip route del <cidr>` removes ANY entry for
        that dest regardless of (dev, via, metric).  That's correct
        for the alias: per planner.ADMIN_ALIAS_METRIC, the committer
        and sync_interfaces are the only writers; whichever of them
        last touched it, we want our new entry to be authoritative.
        """
        trace_enter('iproute.RealIPRoute.install_admin_alias', r=repr(r), alias_cidr=repr(alias_cidr))
        from .planner import ADMIN_ALIAS_METRIC
        # 1. Delete any current entry for this alias.  Unconstrained
        #    on (dev, via, metric) - see docstring.
        rc_del, _o_del, _e_del = _run(
            [self._ip, "route", "del", alias_cidr])
        # rc=0 deleted; rc=2 absent.  Either is fine.
        if rc_del not in (0, 2):
            (config_log := __import__("logging").getLogger(
                "frognet_route")).warning(
                "ADMIN_ALIAS_DEL_unexpected: %s rc=%d", alias_cidr, rc_del)

        # 2. Add the new alias entry.
        argv = [self._ip, "route", "add", alias_cidr]
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
        trace_enter('iproute.RealIPRoute.remove_admin_alias', alias_cidr=repr(alias_cidr))
        rc, _out, _err = _run([self._ip, "route", "del", alias_cidr])
        # rc=2 from `ip route del` means "No such process" i.e. route
        # absent.  Treat as success.
        return rc == 0 or rc == 2

    def list_stale_probe_routes(self) -> list[str]:
        """Return every /32 route whose destination is a .2 address.
        These are the probe aliases installed by sync_interfaces - any
        that survive a merge pass are leaks and must be cleaned up."""
        trace_enter('iproute.RealIPRoute.list_stale_probe_routes')
        rc, out, _err = _run([self._ip, "-4", "route", "show"])
        if rc != 0:
            return []
        stale: list[str] = []
        for line in out.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            dest = parts[0]
            if not dest.endswith("/32"):
                continue
            host = dest[:-3]
            # Only sweep .2 probe aliases; never touch other /32s
            # (operator-installed, kernel-owned, etc.)
            if host.endswith(".2") and host.startswith("10.") and "metric 5" in line:
                stale.append(dest)
        return stale

    def delete_probe_route(self, probe_cidr: str) -> bool:
        trace_enter('iproute.RealIPRoute.delete_probe_route', probe_cidr=repr(probe_cidr))
        rc, _out, _err = _run([self._ip, "route", "del", probe_cidr])
        return rc == 0

    def tear_down_wg(self, iface: str) -> bool:
        # Use wg-quick down so that the conf file's PostDown hooks run
        # (if any) and the iface is cleanly torn down.  Fall back to
        # `ip link del` if wg-quick can't manage it (e.g. no conf file).
        trace_enter('iproute.RealIPRoute.tear_down_wg', iface=repr(iface))
        rc, _out, _err = _run([self._wg_quick, "down", iface])
        if rc == 0:
            return True
        rc, _out, _err = _run([self._ip, "link", "del", iface])
        return rc == 0

    def list_active_wg(self) -> list[str]:
        trace_enter('iproute.RealIPRoute.list_active_wg')
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
        trace_enter('iproute.FakeIPRoute.list_routes')
        self.call_log.append(("list_routes",))
        # Return a copy so tests can't mutate our state accidentally.
        return list(self.routes)

    def install(self, r: InstallRoute) -> bool:
        trace_enter('iproute.FakeIPRoute.install', r=repr(r))
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
        self.routes.append(
            Route(dest=r.dest, dev=r.dev, via=r.via, metric=ROUTE_METRIC)
        )
        return True

    def remove(self, r: RemoveRoute) -> bool:
        trace_enter('iproute.FakeIPRoute.remove', r=repr(r))
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
        trace_enter('iproute.FakeIPRoute.install_admin_alias', r=repr(r), alias_cidr=repr(alias_cidr))
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
        trace_enter('iproute.FakeIPRoute.remove_admin_alias', alias_cidr=repr(alias_cidr))
        self.call_log.append(("remove_admin_alias", alias_cidr))
        before = len(self.routes)
        self.routes = [rt for rt in self.routes if rt.dest != alias_cidr]
        # Tolerant of absence - matches RealIPRoute semantics.
        return True

    def list_stale_probe_routes(self) -> list[str]:
        trace_enter('iproute.FakeIPRoute.list_stale_probe_routes')
        self.call_log.append(("list_stale_probe_routes",))
        return list(self.stale_32s)

    def delete_probe_route(self, probe_cidr: str) -> bool:
        trace_enter('iproute.FakeIPRoute.delete_probe_route', probe_cidr=repr(probe_cidr))
        self.call_log.append(("delete_probe_route", probe_cidr))
        if probe_cidr in self.stale_32s:
            self.stale_32s.remove(probe_cidr)
            return True
        return False

    def tear_down_wg(self, iface: str) -> bool:
        trace_enter('iproute.FakeIPRoute.tear_down_wg', iface=repr(iface))
        self.call_log.append(("tear_down_wg", iface))
        if iface in self.wg_ifaces:
            self.wg_ifaces.discard(iface)
            # Remove any routes that pointed at this iface, mirroring
            # the kernel's behavior when an iface goes down.
            self.routes = [rt for rt in self.routes if rt.dev != iface]
            return True
        return False

    def list_active_wg(self) -> list[str]:
        trace_enter('iproute.FakeIPRoute.list_active_wg')
        self.call_log.append(("list_active_wg",))
        return sorted(self.wg_ifaces)
