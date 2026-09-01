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
planner.py - pure route planning function.

Given a set of Observations, the set of non-kernel routes currently in
the kernel route table, and the set of active WG tunnel channels, plan()
produces exactly the kernel operations needed to bring the table into
agreement with the evidence.

The planner does nothing the operator didn't authorize:
  * Lowest-RTT wins among observations that compete.  Within a dest,
    if traceroute data shows at least one observation with 0 additional
    253 hops, ONLY those zero-additional observations compete.
    Indirect-path observations cannot win regardless of RTT.  See
    [TRACEROUTE_V1] in plan().
  * Ties broken by (dev asc, via asc) - deterministic, no class magic.
  * One /24 per destination.  Losers get deleted, not demoted.
  * Every non-winning route for a dest that has a winner -> RemoveRoute
    entry.  One entry per distinct (dev, via, metric) tuple.
  * No removal for a dest where no observation succeeded.  If we don't
    know a path, we don't touch whatever's there.
  * [BROKER_TEARDOWN_V1] Tunnel teardown is driven by the broker
    channel list, NOT by observation winners.  A channel that the broker
    says we should have stays up regardless of whether its remote /24
    happens to have a faster non-WG observation this merge.  Only
    channels that are up in the kernel but ABSENT from the broker list
    (orphans) get torn down.  Observations still drive which kernel
    route serves each /24 - that's the install/remove plan.  Separating
    the two concerns: WG existence is broker-driven; WG usage is
    observation-driven.

    The pre-V1 behavior (tear_down = active - winning_channels) is
    preserved only when the caller passes broker_channels=None, with a
    warning.  This is for the transition window where old daemon
    callers haven't been updated yet to plumb the broker list through.

The planner does not know about /32 probes.  Those are managed by the
gatherer (sync_interfaces.sh) and swept defensively by the executor.
"""
from __future__ import annotations

from frognet_trace import trace_enter, trace_event

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .observation import Observation


# All FrogNet /24s go in at this metric.  One /24 per dest so metric no
# longer serves as a tiebreaker; a single value keeps the table legible
# and lets operators grep "metric 22" to find FrogNet's footprint.
ROUTE_METRIC = 22

# [FALLBACK_METRIC_V1 2026-05-31] When a /24 has multiple observed
# paths (e.g. a direct WG channel AND a LAN-recursive path through a
# peer's transit advertisement), the planner picks the lowest-RTT
# observation as the winner and installs it at ROUTE_METRIC.  Every
# OTHER observation for the same dest gets installed at
# ROUTE_FALLBACK_METRIC so it survives in the kernel as a standby
# alternative.  Kernel longest-prefix-match prefers the lower metric
# (=22) so steady-state traffic uses the winner; if the winner's iface
# goes down (wg handshake stops, link drops, peer reboots) the kernel
# falls through to the fallback automatically without waiting for the
# next merge cycle to re-plan.
#
# Stickiness loop (~line 510) MUST filter to routes at ROUTE_METRIC
# only - a fallback route matching an observation should NOT prevent
# the winner from switching when a better path is discovered.  See
# [STICKY_WINNER_ONLY_V1] tag at the loop.
#
# Stale-removal loop (~line 570) MUST distinguish between routes that
# match a current observation (keep - they're a fallback candidate)
# and routes that match no observation (remove - they're stale paths
# no longer reachable / measurable).
#
# Must be > ROUTE_METRIC so the winner wins, and > ADMIN_ALIAS_METRIC
# so the /32 admin alias still hijacks .2 traffic correctly.  100 is
# arbitrary but well-separated from both for grep'ing route tables and
# leaves room for a future intermediate tier without renumbering.
ROUTE_FALLBACK_METRIC = 100

# [ADMIN_ALIAS_ROUTE_V1] Every /24 the committer installs gets a
# matching .2/32 admin-alias route installed at this metric, with the
# same (dev, via, onlink, src) as the /24.  Rationale:
#
#   sync_interfaces.sh probes the admin alias (peer.2) for every
#   discovery target, and `ensure_tmp_route_to_ip` installs a /32 to
#   that alias at metric=5 to force the probe path.  The intended
#   lifecycle was install-on-probe, delete-after-probe.  Operator
#   requested probe /32s persist for inspection (KEEP_TMP_ROUTES_V1
#   in sync_interfaces.sh), which turned delete_tmp_route_to_ip into
#   a no-op.  Result: every probe leaves a /32 at metric 5; the next
#   `route replace` from the same merge clobbers it with whatever
#   (dev, via) that probe used.  Final state of the /32 after a merge
#   reflects whichever probe finished last for that target - not a
#   planner decision, not the best path, just last-write-wins.  That
#   /32 outranks the /24 (metric 5 vs 22) and hijacks every packet
#   that targets the admin alias, often pointing at a wg interface
#   that doesn't reach the dest at all.
#
# Fix: make the committer own the /32 alongside the /24.  Same
# decision, same path, written authoritatively on every commit.  Any
# leftover tmp /32 from a probe gets overwritten by the committer's
# entry because (dest=peer.2/32, metric=5) is identical and
# `ip route replace` replaces in place.
#
# Must be < ROUTE_METRIC so the /32 wins the longest-prefix tie when
# packets specifically target the .2.
ADMIN_ALIAS_METRIC = 5


def admin_alias_for_dest(dest_cidr: str) -> str | None:
    """Return the admin-alias /32 for a FrogNet /24 dest, or None if the
    dest isn't a /24 in the expected form.  10.111.11.0/24 -> 10.111.11.2/32.
    The committer uses this to derive the alias from each install/remove
    target without the planner needing to emit a separate route record."""
    trace_enter('planner.admin_alias_for_dest', dest_cidr=repr(dest_cidr))
    if not dest_cidr.endswith("/24"):
        return None
    net = dest_cidr.split("/", 1)[0]
    parts = net.split(".")
    if len(parts) != 4:
        return None
    return f"{parts[0]}.{parts[1]}.{parts[2]}.2/32"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    """A non-kernel route the planner sees in the kernel table.

    proto-kernel scope-link entries for directly-attached subnets are
    filtered out by the caller (they're owned by the kernel for the
    node's own /24s) so the planner never considers touching them.
    """
    dest: str
    dev: str
    via: str         # '' means no 'via' clause (on-link, same /30, etc)
    metric: int


@dataclass(frozen=True)
class InstallRoute:
    dest: str
    dev: str
    via: str         # next-hop IP, or '' for dev-only routes (wg with
                     # allowed-ips covering dest, or LAN direct on
                     # connected /24).
    onlink: bool     # True only for the tunnel-relay case (via=peer.1
                     # with peer.1 outside dest). LAN never sets this.
                     # Direct-allowed-ips tunnels have via='' and
                     # onlink=False - they're plain dev routes.
    kind: str        # informational
    rtt_ms: int      # informational
    # Optional `src <IP>` hint, emitted as `ip route ... src <IP>`.
    # The planner currently always emits ''.  The previous design
    # (TUNNEL_SRC_HINT_V1) pinned src=LOCAL_GW on tunnel routes - that
    # was wrong because LOCAL_GW lives on eth0, not wgN.  See the long
    # comment at the InstallRoute construction site for the full
    # rationale.  Kept as a dataclass field for the executor's
    # benefit (it knows how to emit src clauses when one is present)
    # and in case a future caller has a legitimate src to specify.
    src: str = ""
    # [FALLBACK_METRIC_V1 2026-05-31] Metric to install at.  Default
    # ROUTE_METRIC (winner).  Non-winning observations get installed
    # at ROUTE_FALLBACK_METRIC so they survive as kernel-level
    # standbys.  Backward compatible: callers that don't pass `metric`
    # behave exactly as before.
    metric: int = ROUTE_METRIC


@dataclass(frozen=True)
class RemoveRoute:
    dest: str
    dev: str
    via: str
    metric: int
    reason: str


@dataclass
class Plan:
    installs: list[InstallRoute] = field(default_factory=list)
    removals: list[RemoveRoute] = field(default_factory=list)
    tear_down_tunnels: list[str] = field(default_factory=list)
    # dest -> winning Observation, for logging / snapshot emission.
    winners: dict[str, Observation] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dest_contains_host_path(obs: Observation) -> bool:
    """True when obs.host_path is inside obs.dest.

    For a wg tunnel where the peer's .1 sits inside the destination
    /24 (the common case - peer's LAN /24 with peer.1 as gateway),
    the wg interface itself reaches the dest via allowed-ips. The
    kernel cannot ARP for peer.1 because wg has no L2 - installing
    `dest via peer.1 dev wgN onlink` makes the kernel try to resolve
    peer.1 as a next-hop on wgN, which is the same /24 it's trying
    to reach, producing OSError(113) No route to host on every send.

    Correct route shape for this case: `dest dev wgN`, no via.

    For a wg tunnel where the peer's .1 is OUTSIDE the dest (relay
    serving a downstream /24 it doesn't itself live in), via=peer.1
    onlink IS correct - the kernel sends to peer.1 over wg, peer.1
    forwards into the downstream net.
    """
    trace_enter('planner._dest_contains_host_path', obs=repr(obs))
    if not obs.host_path or not obs.dest:
        return False
    try:
        net, prefix_s = obs.dest.split("/")
        prefix = int(prefix_s)
        a = [int(x) for x in net.split(".")]
        b = [int(x) for x in obs.host_path.split(".")]
    except (ValueError, IndexError):
        return False
    if len(a) != 4 or len(b) != 4:
        return False
    a_int = (a[0] << 24) | (a[1] << 16) | (a[2] << 8) | a[3]
    b_int = (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return (a_int & mask) == (b_int & mask)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _winner_key(obs: Observation) -> tuple:
    """Sort key: rtt_ms first, then dev, then via.  Deterministic,
    no class preference."""
    trace_enter('planner._winner_key', obs=repr(obs))
    return (obs.rtt_ms, obs.dev, obs.via)

def _obs_wave(obs: Observation) -> int:
    """Wave number from obs.source ('sync_waveN'), 0 if unknown.

    sync_interfaces.sh stamps source='sync_wave1' / 'sync_wave2'
    ([RECORD_VIA_V1]).  Wave is the direct(=1) vs transitive(>=2)
    signal the gatherer already uses to decide record_via, so the
    planner keys on the same thing rather than re-deriving it.
    """
    trace_enter('planner._obs_wave', obs=repr(obs))
    s = obs.source or ""
    if s.startswith("sync_wave"):
        try:
            return int(s[len("sync_wave"):])
        except ValueError:
            return 0
    return 0

def _winning_via(obs: Observation) -> str:
    """The `via` clause of the /24 we install for this observation.

    Tunnel observations [TUNNEL_DEV_ROUTE_V1]:
        empty - always `dest dev wgN`, no via.  Broker sets
        AllowedIPs=10.0.0.0/8 on every channel, so wg accepts any
        10.x destination on the iface regardless of next-hop.  The
        peer's kernel routes the inner packet onward.  Same shape
        for the channel's own /24 and for any /24 the peer relays.

    LAN through a connected next-hop:
        obs.via - the ARPable next-hop on a connected subnet. May be
        a direct neighbor or a relay that knows how to reach dest.
        Either way, kernel ARPs for it on a connected subnet.
        sync_interfaces.sh ingest enforces that obs.via is on a
        connected subnet (or substitutes anchor) before it gets here.

    LAN with no via (rare - direct on connected /24, kernel usually
    owns this):
        empty - fall through to scope-link shape.
    """
    trace_enter('planner._winning_via', obs=repr(obs))
    if obs.kind == "tunnel":
        # [TUNNEL_DEV_ROUTE_V1] Tunnel routes are always `dest dev wgN`
        # with no via.  The broker sets AllowedIPs=10.0.0.0/8 on every
        # channel, so wg accepts any 10.x destination on the iface
        # regardless of next-hop.  The kernel hands the packet to wgN,
        # wg encapsulates, the peer's kernel does its own routing
        # decision on the inner packet.  No via is needed.
        #
        # This collapses the prior own-/24 vs relay-/24 distinction.
        # Previously relays got via=peer.1 onlink - onlink because
        # peer.1 isn't on wgN's connected /30.  Needing onlink is a
        # sign the route shape doesn't fit the link, and with broad
        # AllowedIPs the via clause carries no information the kernel
        # uses.  Same shape for own and relayed /24s.
        return ""
    # LAN.  Two sub-cases, the LAN twin of the tunnel branch above:
    # direct neighbor (wave 1) vs relayed-over-LAN (wave 2+).  The
    # original [LAN_FROGNET_VIA_V1] handled only the direct case and
    # wrongly applied it to both - the same over-correction that
    # FIX_TUNNEL_VIA_V1 fixed on the tunnel side.
    #
    # [LAN_TRANSITIVE_VIA_V1] Wave 2+ transitive: dest's node is NOT
    # on our segment - a relay node is.  obs.via is the relay's
    # FrogNet .1 (e.g. 10.250.250.1), stamped by sync_interfaces
    # [RECORD_VIA_V1] on wave>=2.  Install via the relay; the relay
    # forwards into dest, exactly as a wg channel peer forwards into
    # a /24 it relays.  obs.host_path (dest's own .1) is NOT usable
    # here: dest's node owns it on dest's interface, but that node
    # isn't on our segment, so nothing answers ARP for it.
    if _obs_wave(obs) >= 2 and obs.via:
        return obs.via
    # [LAN_FROGNET_VIA_V1] Wave 1 direct neighbor: dest's node IS on
    # our segment and owns dest's .1, so the kernel ARPs for
    # host_path on the LAN dev and the remote answers (Linux replies
    # for own addresses on whatever iface received the request).
    # obs.via on wave 1 is the LAN underlay contact (e.g.
    # 192.168.0.33) - a dev hint, never a FrogNet next-hop; using it
    # as via would tie the route to a DHCP/roam-volatile underlay
    # address and mix underlay into FrogNet routing.
    return obs.host_path

def _requires_onlink(obs: Observation) -> bool:
    """Onlink is needed only when no other route in the install set
    makes the `via` address reachable through `dev`.

    Tunnel transitive (obs.via set, peer.1 of the channel):
        NO onlink.  The channel's own primary /24 is installed by
        the same merge as `dest dev wgN scope link` - that route
        covers peer.1.  Kernel recursive lookup resolves the
        via through the connected /24 without needing the onlink
        hint.  Adding `onlink` here would tell the kernel to skip
        that resolution and trust us blindly, which can mask
        misconfiguration if the own /24 ever isn't installed.

    Tunnel own /24 (obs.via empty):
        NO onlink - plain `dev wgN scope link` route, no next-hop.

    Tunnel legacy (no via, peer.1 outside dest):
        Onlink - via falls back to host_path, which by definition
        isn't on a connected subnet.  Modern gatherer doesn't
        produce this; kept for old observation streams.

    LAN [LAN_FROGNET_VIA_V1]:
        Onlink - _winning_via returns obs.host_path (the FrogNet
        .1 of the destination /24), which is NOT on any of our
        connected subnets.  Our connected subnets are LAN underlay
        (192.168.x, etc.) and our own pond /24, never a remote
        peer's /24.  ARP for the FrogNet .1 works on the LAN dev
        because the remote host owns that IP on another interface
        and Linux answers ARP for own addresses on any interface
        by default."""
    trace_enter('planner._requires_onlink', obs=repr(obs))
    if obs.kind != "tunnel":
        # [LAN_TRANSITIVE_VIA_V1] Downstream-over-LAN (wave 2+): via is
        # the directly-connected relay's .1 (_winning_via returns obs.via),
        # reachable recursively through the connected subnet we share with
        # the relay.  NO onlink - onlink would make the kernel ARP the
        # relay .1 directly on the dev and skip recursion, breaking the
        # relay's own forwarding.  This is the LAN mirror of Rule B
        # (tunnel-transitive also omits onlink for the same reason).
        if _obs_wave(obs) >= 2 and obs.via:
            return False
        # [LAN_FROGNET_VIA_V1] Direct neighbor (wave 1): via is the dest's
        # own .1 (host_path), off every connected subnet - but the device
        # is physically on this segment and answers ARP for its .1, so
        # onlink is required.
        return True
    # Tunnel + via empty -> plain `dev wgN` route, no onlink concept.
    # Tunnel + via set -> channel's own /24 install covers it via
    # kernel recursive lookup, no onlink needed.
    if obs.via:
        return False
    # Legacy: no via, peer.1 outside dest -> via falls back to
    # host_path (not on a connected subnet) -> onlink required.
    return not _dest_contains_host_path(obs)

def plan(
    observations: Iterable[Observation],
    current_routes: Iterable[Route],
    active_tunnel_channels: Iterable[str] = (),
    *,
    owned_subnets: Iterable[str] = (),
    broker_channels: Optional[Iterable[str]] = None,
    traceroute_hops: Optional[dict[tuple[str, str], int]] = None,
    failures: Iterable = (),
) -> Plan:
    """Compute the route plan from observations and the current kernel state.

    Arguments:
        observations  - every successful probe.  The same dest may have
                        many observations (LAN + tunnel, or two LAN paths,
                        or the same path measured across two waves).
        current_routes - every non-kernel route the kernel currently has
                         for any /24 we might touch.  proto-kernel
                         scope-link entries MUST be filtered out by the
                         caller.
        active_tunnel_channels - channel names of WG tunnels currently up
                         (kernel/daemon truth).  Used as the LEFT side of
                         the teardown computation.
        broker_channels - [BROKER_TEARDOWN_V1] channel names the broker
                         says this node should have tunnels for.  See
                         section 4 below.
        traceroute_hops - [TRACEROUTE_V1] map from (dest, dev) to the
                         count of distinct 10.253.x.x addresses seen as
                         hops in a traceroute through that dev to that
                         dest.  Zero means the dest is one transit step
                         away - the path uses only `dev` itself (for wgN,
                         only the local /30; for eth0, only the connected
                         LAN segment).  Non-zero means the path traversed
                         additional transit segments.
                         Winner-selection rule: if at least one
                         observation for a dest has zero additional 253
                         hops, only those zero-additional observations
                         compete.  Indirect-path observations cannot win
                         regardless of RTT.  This prevents wg-vs-wg
                         RTT tiebreaks from rerouting traffic through
                         a peer's mesh when a direct path exists.
                         Within zero-additional observations, lowest-RTT
                         wins (plus the usual stickiness threshold).
                         If empty or None, all observations compete on
                         RTT - legacy behavior.
        owned_subnets - /24s this node owns (eth0 subnet, any wlan /24s it
                        serves).  Observations and routes for these are
                        ignored - the kernel owns their proto-kernel entry.

    Returns:
        A Plan whose installs/removals/tear_down_tunnels are applied in
        order by the executor.  No installs or removals target a dest
        for which no observation succeeded.
    """
    trace_enter('planner.plan', observations=repr(observations), current_routes=repr(current_routes), active_tunnel_channels=repr(active_tunnel_channels))
    owned = set(owned_subnets)

    # --- 1. Group observations by dest ---------------------------------
    by_dest: dict[str, list[Observation]] = {}
    for obs in observations:
        if obs.dest in owned:
            continue
        by_dest.setdefault(obs.dest, []).append(obs)

    # --- 1a. Per-path consolidation (Bug #29: reliability) -------------
    # Multiple observations for the same (dest, dev, via_or_host_path)
    # in a single run are just repeated noisy samples of the same path
    # - not independent candidates.  Keep the MINIMUM RTT per path as
    # a single consolidated observation.  This stops the planner from
    # treating "best sample of path A" as a separate bid against path B
    # when we actually have multiple samples of both.
    #
    # The key we consolidate on is (dev, installed_via), where
    # installed_via is host_path for tunnels and obs.via for LAN -
    # exactly what _winning_via() produces.  Two observations are the
    # "same path" iff they'd install to the same kernel route.
    consolidated_by_dest: dict[str, list[Observation]] = {}
    for dest, obs_list in by_dest.items():
        by_path: dict[tuple, Observation] = {}
        for obs in obs_list:
            key = (obs.dev, _winning_via(obs), obs.kind, obs.ch_name)
            best = by_path.get(key)
            if best is None or obs.rtt_ms < best.rtt_ms:
                by_path[key] = obs
        consolidated_by_dest[dest] = list(by_path.values())

    # --- 1b. Index current routes for stickiness (Bug #29) -------------
    # A dest may have multiple current routes in pathological cases
    # (metric dupes, stale entries); any of them matching an observation
    # qualifies as "the installed path we observed this run."
    routes_by_dest: dict[str, list[Route]] = {}
    for r in current_routes:
        if r.dest in owned:
            continue
        routes_by_dest.setdefault(r.dest, []).append(r)

    # --- 2. Pick winner per dest (with stickiness) ---------------------
    # Old rule: lowest-RTT wins, period.  Pure, but brittle: RTT
    # variance on a real network is routinely 25-40% sample-to-sample,
    # so two paths with similar true latency flip ownership every run
    # and the kernel route table churns.
    #
    # New rule: if a currently-installed route for `dest` was observed
    # this run, keep it as the winner UNLESS another observation is
    # meaningfully faster.  "Meaningfully" = at least 100ms AND at
    # least 20% faster.  Both thresholds together - a 100ms improvement
    # on a 50ms baseline would be huge (3x) and should switch; a 100ms
    # improvement on a 2000ms baseline is 5% and within noise, should
    # stick.  20% alone would allow thrash at <500ms paths; 100ms alone
    # would stick forever at >500ms paths.  The AND handles both.
    #
    # Fallback observations (rtt=FALLBACK_RTT, 9999ms by default) are
    # treated like any other RTT here - if a real measurement shows up,
    # 9999 vs 2000 = 80% improvement and 7999ms faster, easily switches.
    #
    # When there IS no observation matching the installed route this
    # run (the installed path is stale or probes failed for it), we
    # fall through to "lowest RTT wins" among the observations we do
    # have.  No stickiness for paths we can no longer see.
    SWITCH_MIN_ABS_MS = 100       # must be >=100ms faster
    SWITCH_MIN_REL    = 0.20      # AND >=20% faster

    winners: dict[str, Observation] = {}
    for dest, obs_list in consolidated_by_dest.items():
        # [PURE_RTT_V1] Pick winner by lowest RTT, period.
        #
        # Earlier design (TRACEROUTE_V1) preferred observations whose
        # path had additional_253_hops == 0, on the theory that
        # zero-additional means "directly attached, no extra transit."
        # That filter was wrong in both directions:
        #
        #   1. It treated a LAN reflection through our own wg tunnel as
        #      zero-additional, because the reflecting peer's iptables
        #      drops ICMP TTL-exceeded back across the LAN - traceroute
        #      returns all-asterisks and count_additional_253_hops
        #      reports 0, indistinguishable from a true direct path.
        #      The reflected path then beat the genuine wg path, which
        #      had legitimate transit hops > 0.  Result: route flipped
        #      to the LAN, which next packet sent back to us, which
        #      consulted the same flipped route, which sent it to the
        #      peer again - loop.
        #
        #   2. Even when the filter worked, it short-circuited RTT.
        #      A reflection is *always* slower than the direct wg path
        #      it loops through (extra round-trip across the LAN before
        #      reaching the same tunnel).  Pure RTT picks the right
        #      observation by definition.  The traceroute filter added
        #      complexity to solve a problem RTT already solved.
        #
        # Falling through to lowest-RTT also collapses the
        # zero-additional-with-traceroute-data branch into the
        # missing-traceroute-data branch - one rule, fewer surprises.
        obs_sorted = sorted(obs_list, key=_winner_key)
        best = obs_sorted[0]

        # Find a sticky candidate - an observation matching any of the
        # currently-installed routes for this dest.
        #
        # [STICKY_WINNER_ONLY_V1 2026-05-31] Restrict the sticky-route
        # search to routes at ROUTE_METRIC (winners).  Without this
        # filter, a fallback-metric route (kept across cycles per
        # [FALLBACK_METRIC_V1]) could match an observation and trigger
        # the switch-threshold gate, defeating the purpose of pure
        # RTT-driven selection for the active path.  We want stickiness
        # only to the CURRENTLY-WINNING path, not to old paths we keep
        # around as standbys.
        sticky: Observation | None = None
        for r in routes_by_dest.get(dest, []):
            if r.metric != ROUTE_METRIC:
                continue
            for obs in obs_list:
                if obs.dev == r.dev and _winning_via(obs) == r.via:
                    sticky = obs
                    break
            if sticky is not None:
                break

        if sticky is None or sticky is best:
            winners[dest] = best
            continue

        # Sticky exists and is not already the RTT winner.  Apply the
        # switch threshold.
        delta_abs = sticky.rtt_ms - best.rtt_ms
        delta_rel = delta_abs / sticky.rtt_ms if sticky.rtt_ms > 0 else 0.0
        if delta_abs >= SWITCH_MIN_ABS_MS and delta_rel >= SWITCH_MIN_REL:
            winners[dest] = best
        else:
            winners[dest] = sticky

    # --- 3. Decide installs and removals -------------------------------
    installs: list[InstallRoute] = []
    removals: list[RemoveRoute] = []

    for dest, win in winners.items():
        existing = routes_by_dest.get(dest, [])
        obs_list = consolidated_by_dest[dest]

        # [FALLBACK_METRIC_V1 2026-05-31] Build the set of (dev, via,
        # metric) routes this dest SHOULD have after this commit:
        #   - The winner at ROUTE_METRIC.
        #   - Every non-winner observation at ROUTE_FALLBACK_METRIC, so
        #     the kernel keeps them as standby candidates.
        # `desired` is keyed on (dev, via, metric) and maps to the
        # observation that justifies the route, used for the install's
        # `kind`/`rtt_ms`/`onlink` fields.
        win_dev = win.dev
        win_via = _winning_via(win)
        desired: dict[tuple[str, str, int], Observation] = {
            (win_dev, win_via, ROUTE_METRIC): win,
        }
        # Same observation can appear under multiple (dev, via)
        # consolidations; group by (dev, via) and take the lowest-RTT
        # representative so each fallback path is installed exactly
        # once at metric ROUTE_FALLBACK_METRIC.
        seen_fallback_paths: dict[tuple[str, str], Observation] = {}
        for obs in obs_list:
            obs_dev = obs.dev
            obs_via = _winning_via(obs)
            if (obs_dev, obs_via) == (win_dev, win_via):
                continue
            key = (obs_dev, obs_via)
            prev = seen_fallback_paths.get(key)
            if prev is None or obs.rtt_ms < prev.rtt_ms:
                seen_fallback_paths[key] = obs
        # [FALLBACK_LADDER_METRIC_V2 2026-05-31] Rank the fallback paths by
        # RTT and give each its OWN metric slot - ROUTE_FALLBACK_METRIC,
        # +1, +2, ...  The prior code stacked every fallback at
        # ROUTE_FALLBACK_METRIC, which collapsed the ladder to ECMP at
        # metric 100: the kernel hashed among them, the RTT ranking was
        # lost, and eviction couldn't promote a distinct next-best path
        # (peeling one metric-100 route left the rest at 100).  Sorting by
        # _winner_key (rtt, dev, via) is deterministic, so identical inputs
        # always yield the identical ladder; the per-metric-slot removal
        # logic below then handles persistence and middle-of-ladder swaps.
        for rank, obs in enumerate(
                sorted(seen_fallback_paths.values(), key=_winner_key)):
            fb_dev = obs.dev
            fb_via = _winning_via(obs)
            desired[(fb_dev, fb_via, ROUTE_FALLBACK_METRIC + rank)] = obs

        # Install any desired route that isn't already in the kernel
        # with the exact (dev, via, metric) tuple.  `ip route replace`
        # at a given metric replaces only that metric's slot, so the
        # winner and fallbacks coexist independently.
        for (dev, via, metric), obs in desired.items():
            already = any(
                r.dev == dev and r.via == via and r.metric == metric
                for r in existing
            )
            if already:
                continue
            # [TUNNEL_SRC_HINT_REMOVED_20260518] No src on any installed
            # route - see existing comment block earlier in this file.
            installs.append(InstallRoute(
                dest=dest, dev=dev, via=via,
                onlink=_requires_onlink(obs), kind=obs.kind,
                rtt_ms=obs.rtt_ms, src="",
                metric=metric,
            ))

        # [FALLBACK_PERSIST_V1 2026-05-31] Routes are STICKY: only
        # remove a kernel route if a different (dev, via) is desired
        # at the same metric for the same dest (i.e., the metric slot
        # is being claimed by a different path).  Do NOT remove a
        # route just because no current observation matches it - a
        # path that produced no observation this cycle may simply
        # have failed one probe; it should stay in the kernel as a
        # standby fallback until either:
        #   (a) something else claims its (dest, metric) slot, or
        #   (b) the iface it lives on goes away (handled at the
        #       tunnel-teardown step below; kernel auto-removes
        #       routes on iface down).
        #
        # Reported symptom this addresses: operator saw metric-100
        # fallback routes appear after discovery completed, then
        # disappear on a subsequent cycle.  Cause: the prior cycle
        # had observations for both paths, both got installed; a
        # later cycle had observations for only the winner; the
        # fallback was "not in desired" and got removed.  Per design,
        # the fallback should persist across observation gaps.
        #
        # The old behavior (remove everything not-in-desired) is
        # preserved for the SAME (dest, metric) slot - a metric-22
        # slot held by an old path must be cleared when a new path
        # wins it; otherwise we'd accumulate duplicate metric-22
        # entries.  But the metric-100 slot is allowed to hold
        # leftover fallbacks indefinitely.
        desired_at_metric: dict[int, set[tuple[str, str]]] = {}
        desired_paths: set[tuple[str, str]] = set()
        for (dev, via, metric) in desired:
            desired_at_metric.setdefault(metric, set()).add((dev, via))
            desired_paths.add((dev, via))

        for r in existing:
            if (r.dev, r.via, r.metric) in desired:
                continue  # exactly matches a desired route - keep
            # [LADDER_DEDUP_V1] If this (dev, via) IS desired this cycle but
            # at a DIFFERENT metric, the kernel entry is a stale ladder slot
            # the path has since vacated - RTT re-ranking moved it, or the
            # ladder shrank and it dropped to a lower slot while the higher
            # one lost its claimant.  Remove it; otherwise the same path
            # lingers at multiple metrics (observed live as duplicate
            # fallbacks, e.g. 10.160.160.1/wg3 at both 101 and 105).  This
            # does NOT weaken persistence: a path NOT observed this cycle is
            # absent from desired_paths and still falls through to the
            # standby-keep branch below.
            if (r.dev, r.via) in desired_paths:
                removals.append(RemoveRoute(
                    dest=dest, dev=r.dev, via=r.via, metric=r.metric,
                    reason=f"stale_ladder_slot:path_relocated,metric={r.metric}",
                ))
                continue
            claimants = desired_at_metric.get(r.metric, set())
            if claimants and (r.dev, r.via) not in claimants:
                # A different (dev, via) is being installed at the
                # SAME metric for this dest.  Kernel `ip route
                # replace dest metric M` would already replace
                # this row, but emit an explicit removal so the
                # executor can log it.
                removals.append(RemoveRoute(
                    dest=dest, dev=r.dev, via=r.via, metric=r.metric,
                    reason=(
                        f"slot_claimed:metric={r.metric},"
                        f"new=dev={next(iter(claimants))[0]},"
                        f"via={next(iter(claimants))[1]}"
                    ),
                ))
            # else: r.metric has no desired claimant - this is an old
            # fallback or stale entry.  Keep it; it stays as standby.

    # --- 3b. Failure-driven removals [PROBE_FAILURE_REMOVAL_V1] -------
    #
    # A dest with at least one Failure record this run and NO winning
    # observation means we probed (dev, via) for that dest and every
    # attempt failed terminally - typically a route bringup installed
    # from a broker-advertised remote_subnet that the peer cannot
    # actually forward, or a path whose intermediate node went away.
    # The current kernel route is stale; remove it.
    #
    # Safety:
    #   - winners take precedence.  A dest with even one winning
    #     observation doesn't reach this branch; the install/replace
    #     logic above already handled it.
    #   - owned subnets are skipped (kernel owns their proto-kernel).
    #   - if there's no kernel route for the dest, there's nothing to
    #     remove - a probe that fails for a dest we never had a route
    #     to produces no work here.
    #   - We do NOT distinguish "probe failed because peer can't
    #     forward" from "probe failed because of transient packet
    #     loss."  sync_interfaces' run_one_probe already retries
    #     three times with backoff before emitting a terminal
    #     failure, and bringup will re-establish the channel's own
    #     /24 on the next merge.  Transient flap is self-healing on
    #     the next pass.
    #
    # [PROVER_FAILURE_GUARD_V1] A far-edge .2 prover failing is NOT
    # evidence the /24 is unreachable.  On Seattle5 (2026-06-05 21:34)
    # 10.102.60.0/24 and 10.179.179.0/24 answered the data path on .1
    # for 3h while their .2 prover timed out every 5-min cycle; removing
    # the /24 on that signal cut the healthy .1 path (and led to the
    # wg0/1/2 teardown).  Per the model - failover is the proxy/daemon's
    # job on dead next-hops; the committer never deletes a live,
    # broker-authorized path - we suppress failure-driven removal for any
    # dest whose channel is BOTH up locally AND still in the broker list.
    # The prover being down at most warrants a metric demotion or alert,
    # never deletion of the path .1 is using.  Removal still fires for a
    # dest whose path genuinely went away (channel gone from broker /
    # torn down), which is the legitimate case this branch was added for.
    live_channels = set(active_tunnel_channels)
    if broker_channels is not None:
        live_channels &= set(broker_channels)
    failure_dests: dict[str, set[str]] = {}
    for f in failures:
        if f.dest in owned:
            continue
        failure_dests.setdefault(f.dest, set())
        if getattr(f, "ch_name", ""):
            failure_dests[f.dest].add(f.ch_name)
    for dest, ch_names in failure_dests.items():
        if dest in winners:
            continue
        if ch_names & live_channels:
            # Path is still up and broker-authorized - prover-only failure.
            continue
        for r in routes_by_dest.get(dest, []):
            removals.append(RemoveRoute(
                dest=dest, dev=r.dev, via=r.via, metric=r.metric,
                reason="all_probes_failed",
            ))

    # --- 4. Decide tunnel teardowns ------------------------------------
    #
    # [BROKER_TEARDOWN_V1] Tunnel existence is broker-driven, not
    # observation-driven.  The broker tells us which channels this node
    # should hold open; we tear down only channels that are up in the
    # kernel but absent from the broker list (orphans).  A channel that
    # happens to lose every /24 to a faster LAN observation this merge
    # is NOT redundant - the LAN observation may just be one hop through
    # a peer that itself runs the very tunnel we're considering tearing
    # down.  Killing the direct tunnel in that case strands traffic on
    # the indirect path the moment the intermediate peer goes away.
    # Bringup decides existence, planner decides usage; the two
    # decisions don't share a knob.
    #
    # Legacy path (broker_channels is None): preserve the pre-V1 rule
    # so untouched callers keep working until they're updated.  Any
    # caller that passes broker_channels (even an empty list/set) opts
    # into the V1 rule.
    active_set = set(active_tunnel_channels)
    if broker_channels is None:
        # Legacy: tear down channels that won no /24.
        winning_channels: set[str] = set()
        for win in winners.values():
            if win.kind == "tunnel" and win.ch_name:
                winning_channels.add(win.ch_name)
        tear_down: list[str] = sorted(active_set - winning_channels)
    else:
        # V1: tear down only orphans (active minus broker).
        tear_down = sorted(active_set - set(broker_channels))

    return Plan(
        installs=installs,
        removals=removals,
        tear_down_tunnels=tear_down,
        winners=winners,
    )
