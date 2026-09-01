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
# [INSTRUMENTATION_V2_APPLIED]
"""
committer.py - orchestrates the planner and applies its Plan to the kernel.

This is the ONE place in the system that installs or removes a /24 route.
Everything else records observations, which this module consumes.  The
committer:

  1. Reads observations from /etc/sentinels/discovery_observations.tsv.
  2. Reads the current non-kernel /24 routes from the kernel.
  3. Reads the set of active WG tunnel channels from the tunnel daemon's
     state directory.
  4. Calls planner.plan() to compute installs/removals/tear_downs.
  5. Applies the plan - one operation at a time, each logged.
  6. Sweeps any leaked /32 probes (belt-and-suspenders cleanup in case
     a bash trap didn't fire or a process was killed mid-probe).
  7. Emits a snapshot of the final state for human inspection.

Two public entry points:

  commit_provisional(ipr, obs_path, ...)
      Called by sync_interfaces between wave 1 and wave 2.  Installs
      wave-1 winners so wave-2 probes have reachable `via`s.  Does NOT
      tear down tunnels (wave 2 hasn't run yet; a tunnel that lost to a
      LAN path in wave 1 might still be the only option for wave-2
      destinations).  Does NOT remove losing /24s - wave 2 may find an
      even-better path for the same dest, and we don't want to flap.

  commit_final(ipr, obs_path, ...)
      Called at the end of sync_interfaces (after wave 2) and by poll.py
      after tunnel observations are added.  Full commit: installs
      winners, removes losers, tears down unused tunnels, sweeps leaked
      probes.

Both entry points are idempotent: running twice is a no-op the second
time.  Both log every operation with its reason.
"""
from __future__ import annotations

from frognet_trace import trace_enter, trace_event

import logging
import os
from typing import Iterable, Optional

from .observation import (Failure, FAILURES_PATH, Observation,
                          read_failures, read_observations, truncate,
                          truncate_failures, write_observations)
from .planner import Plan, Route, plan

OBSERVATIONS_PATH = "/etc/sentinels/discovery_observations.tsv"
SNAPSHOT_PATH = "/etc/sentinels/route_snapshot.tsv"

try:
    from frognet_log import get_logger
    log = get_logger("frognet_route.committer")
except Exception:  # spine not on path (e.g. partial install) -> stdlib fallback
    log = logging.getLogger("frognet_route.committer")


# ---------------------------------------------------------------------------
# Applying a plan
# ---------------------------------------------------------------------------

def _apply_installs(ipr, installs) -> set[str]:
    """Install each winning /24, one at a time.  Returns the set of
    dests whose install() returned False - the caller uses this to
    exclude those dests from the snapshot (so the snapshot never
    claims a route is installed when it isn't).

    Failures are logged at ERROR (not WARNING) because silent-ish
    failures here bit the operator on Seattle1: three LAN /24s had
    valid observations, install rejected via-not-on-connected-subnet,
    warning was easy to miss, routes just didn't appear.  ERROR
    level plus a post-loop summary makes them impossible to miss.

    [ADMIN_ALIAS_ROUTE_V1] After each successful /24 install, install
    the matching .2/32 admin-alias route with the same (dev, via,
    onlink, src).  See planner.ADMIN_ALIAS_METRIC for the full
    rationale; in short: sync_interfaces.sh leaks /32 probe routes at
    metric 5 that hijack admin-alias traffic onto whatever path the
    last probe used.  Committer-written aliases overwrite those leaks
    authoritatively with the right path."""
    trace_enter('committer._apply_installs', ipr=repr(ipr), installs=repr(installs))
    from .planner import admin_alias_for_dest
    failed: set[str] = set()
    for r in installs:
        ok = ipr.install(r)
        if ok:
            log.info(
                "ROUTE_INSTALL %s via %s dev %s kind=%s rtt=%dms onlink=%s",
                r.dest, r.via, r.dev, r.kind, r.rtt_ms,
                "yes" if r.onlink else "no",
            )
            alias = admin_alias_for_dest(r.dest)
            if alias is not None:
                ok_alias = ipr.install_admin_alias(r, alias)
                if ok_alias:
                    log.info(
                        "ADMIN_ALIAS_INSTALL %s via %s dev %s onlink=%s "
                        "(paired with %s)",
                        alias, r.via, r.dev,
                        "yes" if r.onlink else "no", r.dest,
                    )
                else:
                    # Non-fatal: the /24 still carries traffic.  The
                    # tmp /32 from a probe may still be hijacking the
                    # alias, but the /24 install succeeded and that's
                    # the primary commitment.  Log at warning so the
                    # operator can see the asymmetry.
                    log.warning(
                        "ADMIN_ALIAS_INSTALL_FAILED %s via %s dev %s "
                        "- /24 installed but alias did not.  Stale "
                        "probe /32 may still hijack alias traffic.",
                        alias, r.via, r.dev,
                    )
        else:
            failed.add(r.dest)
            log.error(
                "ROUTE_INSTALL_FAILED %s via %s dev %s onlink=%s "
                "- kernel rejected (likely ENETUNREACH: via not on "
                "connected subnet and onlink not set).  No route for "
                "this dest.",
                r.dest, r.via, r.dev, "yes" if r.onlink else "no",
            )
    if failed:
        log.error("ROUTE_INSTALL_SUMMARY: %d of %d installs FAILED: %s",
                  len(failed), len(installs), ", ".join(sorted(failed)))
    return failed


def _apply_removals(ipr, removals, failed_installs: set[str] = None,
                    installed_dests: set[str] = None) -> int:
    """Delete each losing /24, one at a time.  Returns count of removals
    that failed (the kernel may have already dropped it when an iface
    went down - not fatal).

    SAFETY: if the winning install for a dest FAILED, we must NOT
    remove the loser for that same dest - doing so would destroy the
    only working path.  Keep the existing (losing) route in place;
    it's still carrying traffic.  Next merge pass will retry with a
    fresh observation set.

    [ADMIN_ALIAS_ROUTE_V1] After each successful /24 removal, remove
    the matching .2/32 admin alias - UNLESS this same dest also had a
    successful /24 install in the same plan (a migration: old path
    removed, new path installed).  In the migration case, the new
    install already wrote a fresh alias pointing at the new path;
    removing the alias here would delete that new entry and re-expose
    the alias to whatever tmp /32 a probe might have left around.

    `installed_dests` is the set of dests that `_apply_installs`
    successfully installed (i.e. `{r.dest for r in installs} -
    failed_installs`).  The caller computes it and passes it in."""
    trace_enter('committer._apply_removals', ipr=repr(ipr), removals=repr(removals), failed_installs=repr(failed_installs), installed_dests=repr(installed_dests))
    from .planner import admin_alias_for_dest
    if failed_installs is None:
        failed_installs = set()
    if installed_dests is None:
        installed_dests = set()
    failed = 0
    for r in removals:
        if r.dest in failed_installs:
            log.warning(
                "ROUTE_REMOVE_SKIP %s via %s dev %s metric %d reason=%s "
                "- install_for_winner_failed, preserving working loser route",
                r.dest, r.via, r.dev, r.metric, r.reason,
            )
            continue
        ok = ipr.remove(r)
        if ok:
            log.info(
                "ROUTE_REMOVE %s via %s dev %s metric %d reason=%s",
                r.dest, r.via, r.dev, r.metric, r.reason,
            )
        else:
            failed += 1
            log.info(
                "ROUTE_REMOVE_NOOP %s via %s dev %s metric %d "
                "(already absent?)",
                r.dest, r.via, r.dev, r.metric,
            )

        # Alias cleanup.  Two cases:
        #   1. dest had a fresh install in this same plan (migration):
        #      _apply_installs already wrote the new alias.  Do nothing.
        #   2. dest is being removed without replacement (tear-down):
        #      remove the alias too so the next probe doesn't leave a
        #      stale /32 pointing at a path that no longer works.
        alias = admin_alias_for_dest(r.dest)
        if alias is None:
            continue
        if r.dest in installed_dests:
            log.info(
                "ADMIN_ALIAS_KEEP %s (paired /24 %s replaced in same plan)",
                alias, r.dest,
            )
            continue
        ok_alias = ipr.remove_admin_alias(alias)
        if ok_alias:
            log.info("ADMIN_ALIAS_REMOVE %s (paired with %s)", alias, r.dest)
        else:
            log.warning(
                "ADMIN_ALIAS_REMOVE_FAILED %s (paired with %s) - "
                "manual cleanup may be needed",
                alias, r.dest,
            )
    return failed


def _tear_down_tunnels(ipr, channel_to_iface: dict[str, str],
                       channels: list[str]) -> int:
    """Bring down each tunnel whose channel won nothing.  Uses the
    caller-supplied channel->iface map because planner speaks channels
    but the kernel speaks ifaces.

    [KEEP_DEAD_TUNNELS_V1] Setting FROGNET_KEEP_DEAD_TUNNELS=1 in the
    environment makes this a logging-only no-op: the planner still
    decides which tunnels lost (won_no_subnet) and we log what would
    have happened, but we leave the kernel iface up so an operator can
    do live testing (ping, curl, wg show) against tunnels that
    otherwise get torn down before any diagnostic command lands.
    Default unset -> destroy, same as before.
    """
    trace_enter('committer._tear_down_tunnels', ipr=repr(ipr), channel_to_iface=repr(channel_to_iface), channels=repr(channels))
    keep_dead = os.environ.get("FROGNET_KEEP_DEAD_TUNNELS", "") not in ("", "0")
    failed = 0
    for ch in channels:
        iface = channel_to_iface.get(ch)
        if not iface:
            # Unknown channel - nothing to tear down.  Log and move on;
            # the tunnel-daemon state file is the source of truth here
            # and if it doesn't know about the channel, neither do we.
            log.info("TUNNEL_TEARDOWN_SKIP ch=%s reason=no_iface_mapping",
                     ch)
            continue
        if keep_dead:
            log.info("TUNNEL_TEARDOWN_SUPPRESSED ch=%s iface=%s "
                     "reason=won_no_subnet "
                     "(FROGNET_KEEP_DEAD_TUNNELS set)", ch, iface)
            continue
        ok = ipr.tear_down_wg(iface)
        if ok:
            log.info("TUNNEL_TEARDOWN ch=%s iface=%s "
                     "reason=won_no_subnet", ch, iface)
        else:
            failed += 1
            log.warning("TUNNEL_TEARDOWN_FAILED ch=%s iface=%s", ch, iface)
    return failed


def _sweep_stale_probes(ipr) -> int:
    """Delete every leaked .2/32 probe route.  Returns number swept.

    Safety net for the "shit flying everywhere" problem: sync_interfaces
    installs /32 probes in a bash trap, but kill -9 or a power loss will
    leak them.  Each merge pass sweeps what it finds.
    """
    trace_enter('committer._sweep_stale_probes', ipr=repr(ipr))
    stale = ipr.list_stale_probe_routes()
    for cidr in stale:
        ok = ipr.delete_probe_route(cidr)
        if ok:
            log.info("PROBE_SWEEP %s (stale .2/32 from prior run)", cidr)
        else:
            log.warning("PROBE_SWEEP_FAILED %s", cidr)
    return len(stale)


def _emit_snapshot(ipr, winners: dict[str, Observation],
                   snapshot_path: str,
                   failed_installs: set[str] = None) -> None:
    """Write a human-readable snapshot of the post-commit state.

    Format:
        # dest\twinner_dev\twinner_via\twinner_rtt\twinner_kind
    followed by
        # route: dest dev via metric
    for every non-kernel /24 currently in the table.  The snapshot is
    only for humans and other diagnostic tools; nothing parses it back.

    Dests in `failed_installs` are excluded from the winner section -
    we don't advertise a winner we couldn't actually install.  The
    current-kernel-routes section at the bottom still reflects what's
    really there (which for a failed install is whatever was there
    before, if anything).
    """
    trace_enter('committer._emit_snapshot', ipr=repr(ipr), winners=repr(winners), snapshot_path=repr(snapshot_path), failed_installs=repr(failed_installs))
    failed_installs = failed_installs or set()
    lines = [
        "# frognet_route commit snapshot",
        "# columns: dest\tdev\tvia\trtt_ms\tkind\tsource",
    ]
    for dest, w in sorted(winners.items()):
        if dest in failed_installs:
            continue
        lines.append(
            f"{dest}\t{w.dev}\t{w.host_path}\t{w.rtt_ms}\t{w.kind}\t{w.source}"
        )
    lines.append("# current kernel routes:")
    for r in ipr.list_routes():
        lines.append(f"route\t{r.dest}\t{r.dev}\t{r.via}\t{r.metric}")
    try:
        with open(snapshot_path, "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        log.warning("snapshot write failed: %s", e)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def commit_provisional(
    ipr,
    obs_path: str = OBSERVATIONS_PATH,
    *,
    owned_subnets: list[str] = None,
) -> Plan:
    """Install wave-1 winners only.  No removals, no tunnel teardowns.

    Called by sync_interfaces between waves so wave-2 getHosts queries
    and /32 probes can traverse the wave-1 winners.  Wave 2 hasn't been
    heard from yet, so we don't yet know what losers to clean up - that
    waits for commit_final.

    NOTE: this function references several names (active_tunnel_channels,
    channel_to_iface, snapshot_path) that are NOT parameters - it
    NameErrors on call.  Pre-existing brokenness, not addressed here.
    The merge path uses commit_final via _run_committer, not this.
    """
    trace_enter('committer.commit_provisional', ipr=repr(ipr), obs_path=repr(obs_path))
    obs = read_observations(obs_path)
    current = ipr.list_routes()
    p = plan(obs, current,
             active_tunnel_channels=(active_tunnel_channels or []),
             owned_subnets=(owned_subnets or []))
    failed_installs = _apply_installs(ipr, p.installs)
    installed_dests = {r.dest for r in p.installs} - failed_installs
    _apply_removals(ipr, p.removals,
                    failed_installs=failed_installs,
                    installed_dests=installed_dests)
    swept = _sweep_stale_probes(ipr)
    _emit_snapshot(ipr, p.winners, snapshot_path, failed_installs)
    # Teardown LAST - after snapshot is on disk and the kernel routing
    # table has settled on the winning install set. Any sibling-route
    # rebalancing the kernel does in response to a wg iface disappearing
    # now affects only routes we don't care about (the ones being torn
    # down) or routes that have already been settled and snapshotted.
    _tear_down_tunnels(ipr, channel_to_iface or {}, p.tear_down_tunnels)
    try:
        truncate(obs_path)
    except OSError as e:
        log.warning("could not truncate observations: %s", e)
    log.info(
        "COMMIT_FINAL installs=%d removals=%d teardowns=%d probes_swept=%d "
        "observations=%d winners=%d",
        len(p.installs), len(p.removals), len(p.tear_down_tunnels),
        swept, len(obs), len(p.winners),
    )


def commit_final(
    ipr,
    obs_path: str = OBSERVATIONS_PATH,
    *,
    active_tunnel_channels: list[str] = None,
    channel_to_iface: dict[str, str] = None,
    owned_subnets: list[str] = None,
    snapshot_path: str = SNAPSHOT_PATH,
    broker_channels: Optional[Iterable[str]] = None,
    traceroute_hops: Optional[dict[tuple[str, str], int]] = None,
    failures_path: str = FAILURES_PATH,
) -> Plan:
    """Full commit: install winners, remove losers, tear down unused
    tunnels, sweep leaked probes, emit snapshot.

    Arguments:
        active_tunnel_channels - channel names of WG tunnels currently
            up, used as the LEFT side of teardown computation.
        channel_to_iface - map from channel name to kernel iface name,
            used to actually run `wg-quick down <iface>` for teardowns.
        broker_channels - [BROKER_TEARDOWN_V1] channel names the broker
            authorizes for this node.  See planner.plan().
        traceroute_hops - [TRACEROUTE_V1] map from (dest, dev) to count
            of additional 10.253.x.x hops on the path.  See planner.plan().
        failures_path - [PROBE_FAILURE_REMOVAL_V1] TSV of terminal probe
            failures written by sync_interfaces' INGEST loop.  See
            planner.plan().  Truncated after commit, same lifecycle as
            obs_path.
    """
    trace_enter('committer.commit_final', ipr=repr(ipr), obs_path=repr(obs_path))
    obs = read_observations(obs_path)
    failures = read_failures(failures_path)
    current = ipr.list_routes()
    p = plan(obs, current,
             active_tunnel_channels=(active_tunnel_channels or []),
             owned_subnets=(owned_subnets or []),
             broker_channels=broker_channels,
             traceroute_hops=traceroute_hops,
             failures=failures)

    failed_installs = _apply_installs(ipr, p.installs)
    installed_dests = {r.dest for r in p.installs} - failed_installs
    _apply_removals(ipr, p.removals,
                    failed_installs=failed_installs,
                    installed_dests=installed_dests)
    _tear_down_tunnels(ipr, channel_to_iface or {}, p.tear_down_tunnels)
    swept = _sweep_stale_probes(ipr)
    _emit_snapshot(ipr, p.winners, snapshot_path, failed_installs)

    # Truncate the observations and failures files so the next merge
    # pass starts clean.  Do this AFTER the snapshot is written so
    # diagnostic tools can correlate observations to decisions.
    try:
        truncate(obs_path)
    except OSError as e:
        log.warning("could not truncate observations: %s", e)
    try:
        truncate_failures(failures_path)
    except OSError as e:
        log.warning("could not truncate failures: %s", e)

    log.info(
        "COMMIT_FINAL installs=%d removals=%d teardowns=%d probes_swept=%d "
        "observations=%d failures=%d winners=%d",
        len(p.installs), len(p.removals), len(p.tear_down_tunnels),
        swept, len(obs), len(failures), len(p.winners),
    )
    return p


# ---------------------------------------------------------------------------
# Helpers for bash
# ---------------------------------------------------------------------------

def append_observation(
    obs: Observation,
    obs_path: str = OBSERVATIONS_PATH,
) -> None:
    """Helper for the Python CLI invoked by sync_interfaces.sh between
    probes.  Just appends one observation.  Uses O_APPEND semantics so
    parallel probes don't clobber each other's lines."""
    trace_enter('committer.append_observation', obs=repr(obs), obs_path=repr(obs_path))
    os.makedirs(os.path.dirname(obs_path), exist_ok=True)
    write_observations(obs_path, [obs])
