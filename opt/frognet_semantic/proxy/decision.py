#!/opt/frognet_semantic/venv/bin/python3
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
proxy/decision.py

Hop-local decision based on NEXT HOP (adjacent gateway).

Behavior (per current FrogNet model):
- Default path is FAST across ALL interfaces (no allow-listing).
- SEMANTIC is invoked only when the *next-hop* looks slow (RTT above threshold),
  OR when explicit semantic policy says the next-hop is semantic.
- To prevent flapping, we apply:
    * EWMA smoothing of RTT samples
    * hysteresis (different thresholds to enter vs exit semantic)
    * hold-down (minimum time to keep a mode after switching)

Key invariants preserved:
- "Semantic-ness" is evaluated at the NEXT HOP (via if present, else dest).
- Explicit semantic policy overrides RTT-based decisions.
"""

from __future__ import annotations
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event

import os
import time
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from proxy.constants import DecisionPath, DecisionReason, debug
from proxy.netutil import ip_to_24, tcp_connect_rtt_ms, is_local_ip, route_get
from proxy.policy import (
    refresh_policy_if_changed,
    SEMANTIC_OVERRIDE_IPS,
    SEMANTIC_OVERRIDE_NETS,
    SEMANTIC_EDGE_VIA,
)


def _is_frognet_target(ip: str) -> bool:
    """10/8 minus 10.253/16 (transit overlay) and 10.254/16 (chorus virtual)."""
    trace_enter('decision._is_frognet_target', ip=repr(ip))
    if not ip:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 10 and b not in (253, 254)

# --------------------------------------------------------------------
# Tuning knobs (seconds / milliseconds)
# --------------------------------------------------------------------
# Thresholds expressed in milliseconds.
# - Enter semantic when EWMA >= ENTER_SEM_MS
# - Exit semantic when EWMA <= EXIT_SEM_MS (hysteresis: EXIT < ENTER)
ENTER_SEM_MS = 250.0
EXIT_SEM_MS = 200.0

# EWMA alpha (0..1). Higher = more responsive, lower = smoother.
EWMA_ALPHA = 0.25

# Hold-down (seconds): once we switch modes for a next-hop, keep it for this long.
HOLD_DOWN_SEC = 30.0

# How often we're willing to actively probe RTT per next-hop (seconds).
PROBE_MIN_INTERVAL_SEC = 2.0

# TCP probe timeout for daemon RTT measurement (seconds)
PROBE_TIMEOUT_SEC = 2.0


# --------------------------------------------------------------------
# RTT probe + smoothing state
# --------------------------------------------------------------------
@dataclass
class _HopState:
    ewma_ms: Optional[float] = None
    last_sample_ms: Optional[float] = None
    last_probe_ts: float = 0.0
    mode: DecisionPath = DecisionPath.FAST
    last_switch_ts: float = 0.0


_HOPS: dict[str, _HopState] = {}

def flush_hop_cache() -> int:
    trace_enter('decision.flush_hop_cache')
    n = len(_HOPS)
    _HOPS.clear()
    return n

def _now() -> float:
    trace_enter('decision._now')
    return time.time()


def _daemon_rtt_ms(dev: str, ip: str) -> Optional[float]:
    """
    Return TCP connect RTT in ms to the next-hop proxy on port 80.
    No ICMP - measures actual reachability.
    NEVER probe port 9009 directly - only the proxy talks to the daemon.
    """
    trace_enter('decision._daemon_rtt_ms', dev=repr(dev), ip=repr(ip))
    if not dev or not ip:
        return None
    return tcp_connect_rtt_ms(ip, 80, timeout_sec=PROBE_TIMEOUT_SEC)


# [HOPS_CLEAR_EVERY_MERGE_V1] _HOPS caches per-next-hop RTT EWMA and a latched
# FAST/SLOW mode, and mode selects the data path. That is discovery state: a
# merge rewrites the routing table, so an EWMA and a mode measured against the
# old topology must not survive it. flush_hop_cache() already existed but its
# only caller was simulation/proxy_dataplane.py -- production never cleared it,
# so a latched mode outlived every merge until the proxy restarted.
#
# The merge runs in the discovery process and _HOPS lives in the proxy, so an
# in-process flush call cannot reach it. The boundary is observed on disk: the
# merge's flush stage (runmerge.py, beside the not_frognet flush that already
# defines "one fresh probe per merge") stamps MERGE_GEN, and every _HOPS read
# drops the cache when that stamp moves. No new notion of a run is introduced --
# it is the same merge-top event not_frognet already keys off.
MERGE_GEN = os.path.join(
    os.environ.get("FROGNET_SENTINEL_DIR", "/etc/sentinels"), "merge_generation")

_merge_gen_seen: float = -1.0
_merge_gen_absent_reported: bool = False


def _clear_hops_if_merged() -> None:
    """Drop all hop state if a merge has run since the last look.

    [NO_FALLBACK_V1] The bare `except OSError: return` here meant that if
    MERGE_GEN was missing, hop state was never cleared for the life of the
    process - a latched FAST/SLOW mode and an EWMA measured against a routing
    table that merges had since rewritten, applied forever, with nothing in the
    log. That is the same shape as the retire()-set bug: the clearing mechanism
    ran in a process that never saw the thing it was clearing.

    Missing stamp is now reported once (not once per request - this is called on
    the per-request path) and again if it disappears after having been present,
    which is the case that actually breaks the invariant.
    """
    global _merge_gen_seen, _merge_gen_absent_reported
    try:
        mt = os.stat(MERGE_GEN).st_mtime
    except OSError as e:
        if not _merge_gen_absent_reported:
            _merge_gen_absent_reported = True
            print(f"[DECISION] [HOPS_CLEAR_EVERY_MERGE_V1] {MERGE_GEN} "
                  f"unreadable ({type(e).__name__} errno={e.errno}) - hop state "
                  f"(RTT EWMA + latched FAST/SLOW mode) will NOT be cleared on "
                  f"merge; path decisions will use measurements taken against a "
                  f"routing table that merges have since rewritten "
                  f"(seen={_merge_gen_seen}, hops={len(_HOPS)})", flush=True)
        return
    _merge_gen_absent_reported = False
    if mt != _merge_gen_seen:
        if _merge_gen_seen >= 0.0 and _HOPS:
            print(f"[DECISION] [HOPS_CLEAR_EVERY_MERGE_V1] merge observed - "
                  f"cleared {len(_HOPS)} hop state(s)", flush=True)
        _HOPS.clear()
        _merge_gen_seen = mt


def _get_state(nh: str) -> _HopState:
    trace_enter('decision._get_state', nh=repr(nh))
    _clear_hops_if_merged()
    st = _HOPS.get(nh)
    if st is None:
        st = _HopState()
        _HOPS[nh] = st
    return st


def _update_rtt_state(*, nh: str, dev: str) -> _HopState:
    """
    Probe RTT to the next-hop (rate-limited), update EWMA, and return hop state.
    """
    trace_enter('decision._update_rtt_state')
    st = _get_state(nh)
    now = _now()

    # If we are already semantic and inside hold-down,
    # do NOT probe - preserve airtime
    if st.mode == DecisionPath.SEMANTIC and (now - st.last_switch_ts) < HOLD_DOWN_SEC:
        return st

    # Rate-limit probes.
    if (now - st.last_probe_ts) < PROBE_MIN_INTERVAL_SEC:
        return st

    st.last_probe_ts = now
    sample = _daemon_rtt_ms(dev, nh)
    st.last_sample_ms = sample

    if sample is None:
        # No new information; keep EWMA unchanged.
        return st

    if st.ewma_ms is None:
        st.ewma_ms = sample
    else:
        st.ewma_ms = (EWMA_ALPHA * sample) + ((1.0 - EWMA_ALPHA) * st.ewma_ms)

    return st


# --------------------------------------------------------------------
# Explicit semantic policy (unchanged behavior)
# --------------------------------------------------------------------
def next_hop_is_semantic(dst_ip: str, daemon_port: int) -> Tuple[bool, str, str]:
    """
    Semantic-ness is evaluated at the destination identity.
    No kernel route lookup; /etc/hosts is the authority via target_ip.
    """
    trace_enter('decision.next_hop_is_semantic', dst_ip=repr(dst_ip), daemon_port=repr(daemon_port))
    refresh_policy_if_changed()

    if not dst_ip:
        return (False, "", "no_dst")

    nh = dst_ip
    nh24 = ip_to_24(nh)

    # Dest override membership
    if (nh in SEMANTIC_OVERRIDE_IPS) or (nh24 in SEMANTIC_OVERRIDE_NETS):
        return (True, nh, "forced_semantic_override")

    # Any "via" listed for any semantic subnet means nh is a semantic peer
    is_edge_peer = any(nh in s for s in SEMANTIC_EDGE_VIA.values())
    if is_edge_peer:
        # if daemon_reachable(nh, daemon_port, timeout_sec=1.0):
        st = _get_state(nh)
        if st.ewma_ms is not None:  # we've seen it respo
            return (True, nh, "edge_peer")
        return (True, nh, "edge_peer_daemon_unreachable")

    return (False, nh, "fast_next_hop")


# --------------------------------------------------------------------
# Main decision
# --------------------------------------------------------------------
def decide_path_for_target(
    target_ip: str,
    daemon_port: int,
    eligible_ifaces: Set[str],  # retained for call compatibility; ignored by design
) -> Tuple[DecisionPath, str, str, DecisionReason, Optional[float], str]:
    """
    Decide hop-local forwarding path.

    NOTE: eligible_ifaces is intentionally ignored.
    FAST is allowed across all interfaces; RTT governs SEMANTIC.
    """
    trace_enter('decision.decide_path_for_target', target_ip=repr(target_ip), daemon_port=repr(daemon_port), eligible_ifaces=repr(eligible_ifaces))
    refresh_policy_if_changed()

    subnet24 = ip_to_24(target_ip)
    nh = target_ip   # next hop is the destination identity; kernel handles actual routing

    debug(f"[HOP-DEC] dest={target_ip} subnet={subnet24}")

    # 1) Local target -> Apache 8080
    if is_local_ip(target_ip):
        return (DecisionPath.LOCAL, "lo", subnet24, DecisionReason.KERNEL_LOCAL, None, target_ip)

    # 1b) [ONLINK_DIRECT_V1] Directly-connected neighbour -> DIRECT to the wire,
    #     NEVER through the daemon. A target on one of our own connected subnets
    #     (e.g. a DHCP-attached FrogNet node on eth0 like 10.250.250.191) is ONE hop:
    #     the kernel reaches it on-link with no gateway. Sending it through the
    #     daemon (FAST or SEMANTIC both do) makes the daemon - correctly - see it is
    #     not a LOCAL interface IP and forward it back to the proxy:80, which lands
    #     here again -> an infinite proxy<->daemon loop that never replies and hangs
    #     every caller to the 60s safety cap. The HAM branch is the only one that
    #     goes straight out to <target>:80 via MarkedHTTPConnection (SO_MARK=1,
    #     bypassing our own REDIRECT), which is exactly what a direct neighbour
    #     needs. `ip route get` is authoritative: a real dev with an empty `via`
    #     (and not the WAN default) is on-link.
    #     NOTE: wg tunnel peers are on-link too (via='' over AllowedIPs) and MUST also
    #     take HAM -- routing them through the daemon re-triggers the same proxy<->daemon
    #     REDIRECT loop this branch exists to prevent. (Reverted an attempt to send wg
    #     through semantic for coalescing: it broke semantic-to-tunnel entirely.)
    if _is_frognet_target(target_ip):
        # [NO_FALLBACK_V1] This was `except Exception: _dev, _via, _out = "", "", ""`.
        # `ip route get` is the authority for this branch - it decides whether a
        # peer is on-link and therefore whether the request goes DIRECT or back
        # through the daemon. Substituting an empty route for a failed query
        # answers "not on-link" with no evidence, which is precisely the
        # proxy<->daemon REDIRECT loop this branch exists to prevent, and it is
        # silent. RouteQueryFailed now propagates to the request boundary.
        # A genuine "Network is unreachable" is NOT an exception: route_get
        # returns ("", "", <reason>) for that, which falls through correctly.
        _dev, _via, _out = route_get(target_ip)
        if (_dev and not _via and not _dev.startswith("wl")
                and _dev not in ("lo",) and "default" not in (_out or "")):
            debug(f"[HOP-DEC] on-link direct: dest={target_ip} dev={_dev} -> DIRECT/HAM")
            return (DecisionPath.HAM, _dev, subnet24, DecisionReason.FORWARD_FAST, None, target_ip)

    # 2) Non-frognet target (not in 10/8 or in reserved 10.253/10.254) -> real outbound
    #    Reuse HAM as the "out of frognet" outcome; caller dispatches accordingly.
    if not _is_frognet_target(target_ip):
        return (DecisionPath.HAM, "", subnet24, DecisionReason.FORWARD_FAST, None, target_ip)

    # 3) Frognet target -> SEMANTIC vs FAST decided by policy + RTT
    is_sem, _nh2, why = next_hop_is_semantic(target_ip, daemon_port)
    if is_sem:
        debug(f"[HOP-DEC] policy semantic: dest={target_ip} why={why}")
        return (DecisionPath.SEMANTIC, "", subnet24, DecisionReason.NEXT_HOP_SEMANTIC, None, target_ip)

    # RTT-based semantic decision (smoothed, with hysteresis + hold-down)
    st = _update_rtt_state(nh=nh, dev="")
    ewma = st.ewma_ms
    now = _now()

    if (now - st.last_switch_ts) < HOLD_DOWN_SEC:
        if st.mode == DecisionPath.SEMANTIC:
            debug(f"[HOP-DEC] hold-down SEMANTIC nh={nh} ewma_ms={ewma}")
            return (DecisionPath.SEMANTIC, "", subnet24, DecisionReason.NEXT_HOP_SEMANTIC, ewma, nh)
        debug(f"[HOP-DEC] hold-down FAST nh={nh} ewma_ms={ewma}")
        return (DecisionPath.FAST, "", subnet24, DecisionReason.FORWARD_FAST, ewma, nh)

    if ewma is None:
        # [UNKNOWN_RTT_FAST_DEFAULT_V1]
        # Architecture rule: FAST is default, SEMANTIC only when the link
        # measures slow.  Previously this branch defaulted unknown RTT to
        # SEMANTIC, which made template bootstrap impossible: the very
        # first request to any new peer hit SEMANTIC with no template,
        # 503'd, and never got the chance to learn one via REQ_RAW.  The
        # probe in _update_rtt_state already triggered on this call; we
        # just don't have a sample yet (probe is async-ish via TCP
        # connect timeout).  Default to FAST so bootstrap can run;
        # subsequent calls will have a real EWMA and switch to SEMANTIC
        # if the link warrants it.
        debug(f"[HOP-DEC] rtt unknown -> FAST (bootstrap-friendly) nh={nh}")
        return (DecisionPath.FAST, "", subnet24, DecisionReason.FORWARD_FAST, None, nh)

    if st.mode != DecisionPath.SEMANTIC:
        if ewma >= ENTER_SEM_MS:
            st.mode = DecisionPath.SEMANTIC
            st.last_switch_ts = now
            debug(f"[HOP-DEC] FAST->SEMANTIC nh={nh} ewma_ms={ewma:.1f} enter>={ENTER_SEM_MS}")
            return (DecisionPath.SEMANTIC, "", subnet24, DecisionReason.NEXT_HOP_SEMANTIC, ewma, nh)
        debug(f"[HOP-DEC] FAST keep nh={nh} ewma_ms={ewma:.1f}")
        return (DecisionPath.FAST, "", subnet24, DecisionReason.FORWARD_FAST, ewma, nh)

    if ewma <= EXIT_SEM_MS:
        st.mode = DecisionPath.FAST
        st.last_switch_ts = now
        debug(f"[HOP-DEC] SEMANTIC->FAST nh={nh} ewma_ms={ewma:.1f} exit<={EXIT_SEM_MS}")
        return (DecisionPath.FAST, "", subnet24, DecisionReason.FORWARD_FAST, ewma, nh)

    debug(f"[HOP-DEC] SEMANTIC keep nh={nh} ewma_ms={ewma:.1f}")
    return (DecisionPath.SEMANTIC, "", subnet24, DecisionReason.NEXT_HOP_SEMANTIC, ewma, nh)
