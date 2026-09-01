#!/usr/bin/env python3
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
proxy/proxy_metrics.py

Metrics collection and emission for FrogNet Semantic Proxy.

v5.0 changes:
  ADD: bump_coalesce - tracks proxy-side request coalescing (free bandwidth)
  ADD: bump_lru - tracks SAME LRU cache hit/miss (avoids MySQL round-trips)
  ADD: bump_link - tracks wire bytes per next-hop link (physical link loading)
  ADD: bump_template - tracks template coverage (semantic vs raw path)
  ADD: Engine health summary sensor with at-a-glance metrics
  FIX: bump_real/bump_sem now feed into engine snapshot (were dead accumulators)
  FIX: bytes_would estimates now include WG encapsulation per-packet
  KEEP: All v4.2 emission infrastructure (two-table API, SensorID cache)

Stats are CUMULATIVE.  Reset via reset_all_stats().
"""

from __future__ import annotations

import os
import re
import time
import json
import threading
import urllib.request
import urllib.error
import urllib.parse
from collections import defaultdict, deque
from typing import Dict, Any, Optional
import random
import statistics
import uuid   # [NO_FALLBACK_V1] stdlib, imported at module scope

# [NO_FALLBACK_V1] These were imported lazily inside try/except Exception blocks
# scattered through the emit path, so a missing or broken first-party module
# printed one WARNING per flush cycle and the metric simply stopped existing.
# The NY2 log shows the shape this produces: ten copies of
#   [PROXY-METRICS] heartbeat cycle failed:
#       AttributeError("module 'core.frognet_tuples' has no attribute 'heartbeat'")
# which is version skew between nodes - a startup-class fault - reported forever
# as a routine per-cycle warning and never acted on.
#
# Importing at module scope separates the two failures that guard conflated:
#   - the module is missing or the wrong version  -> the process does not start
#   - the call failed at runtime (database down)  -> reported, loop continues
# The runtime handlers below are kept for exactly the second case.
from core import frognet_tuples as _T
from proxy import local_read_cache as _lrc
from proxy.cache import semcache_db as _proxy_cache

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_EMIT_INTERVAL = float(os.environ.get("FROGNET_METRICS_INTERVAL", "30"))
_DEBUG = os.environ.get("FROGNET_DEBUG", "0").strip() == "1"

_EMIT_PORT = int(os.environ.get("FROGNET_METRICS_EMIT_PORT", "80"))
_API_HOST = os.environ.get("FROGNET_DB_HOST", "databasehost.frognet")

# ---------------------------------------------------------------------------
# Locks and state
# ---------------------------------------------------------------------------
_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Structured cache stats per peer
# ---------------------------------------------------------------------------
_REQ_TYPES = ("REQ_FULL", "REQ_REPEAT", "REQ_DIFF", "REQ_RAW")
_RESP_TYPES = ("RESP_SAME", "RESP_DIFF", "RESP_RAW", "REQ_MISS", "ERROR")

_CACHE_STATS: Dict[str, Dict[str, Any]] = {}
_STATS_RESET_TS: float = time.time()

# Per-next-hop link stats (keyed by next-hop IP)
_LINK_STATS: Dict[str, Dict[str, int]] = {}

# Engine-wide counters (not per-peer)
_ENGINE: Dict[str, int] = {
    "coalesce_hits": 0,
    "lru_hits": 0,
    "lru_misses": 0,
    "template_hits": 0,
    "template_misses": 0,
    "local_requests": 0,
    "local_bytes_req": 0,
    "local_bytes_resp": 0,
    "semantic_wire_req": 0,
    "semantic_wire_resp": 0,
}

# Topology observations
_PEER_OBS: Dict[str, Dict[str, Any]] = {}
_PEER_OBS_LOCK = threading.RLock()


def _new_type_counters() -> Dict[str, int]:
    d = {}
    for rt in _REQ_TYPES:
        d[rt] = 0
    for rt in _RESP_TYPES:
        d[rt] = 0
    for rq in _REQ_TYPES:
        for rs in _RESP_TYPES:
            d[f"{rq}\u2192{rs}"] = 0
    return d


# ---------------------------------------------------------------------------
# [PEER_LINK_QUALITY_V1 2026-05-25] Per-peer rolling link-quality samples.
#
# Goal: emit a sliding-window view of (rtt, bytes_in, bytes_out, success/fail)
# per peer so the UI can derive a quality category (healthy / slow /
# saturated / flaky / gone) without committing to that category here.
# UI does the threshold work; this layer reports the numbers.
#
# Why a separate ring from rtt_samples:
#   - rtt_samples is success-only and feeds get_topology_snapshot's
#     existing avg/p95 - touching it would change those fields for
#     downstream consumers.
#   - link_quality_samples records BOTH ok and fail outcomes, with
#     bytes per sample, so window-rate (bytes_per_sec) and
#     success_rate can be computed in one pass at emit time.
#
# Sampling cap: the deque is bounded to PEER_LINK_SAMPLE_CAP entries to
# keep memory finite on chatty peers.  The effective time window
# applied at snapshot time is much tighter than the cap allows; the
# cap exists only to prevent unbounded growth on a peer that gets
# thousands of RPCs/sec.
#
# Time window: the emitter applies a sliding wall-clock window equal
# to the emission tick interval, clamped to a hard floor of
# PEER_LINK_QUALITY_MIN_WINDOW_SEC.  This means the user cannot
# silently turn the metric stale by setting the flusher interval to
# 10 minutes - the metric ticks no slower than its floor.  The
# flusher itself ticks at the user's interval; the *window* used to
# select samples is the larger of (interval, floor).  In practice
# this means an operator setting interval=10s gets 10s windows,
# interval=300s gets 300s windows; we never collect less than the
# floor's worth of data per emission.
PEER_LINK_SAMPLE_CAP = int(os.environ.get(
    "FROGNET_PEER_LINK_SAMPLE_CAP", "2048"))
PEER_LINK_QUALITY_MIN_WINDOW_SEC = 30.0  # HARD floor - not env-overridable
# ---------------------------------------------------------------------------


def _new_endpoint_stats() -> Dict[str, Any]:
    return {
        **_new_type_counters(),
        "bytes_would": 0,
        "bytes_actual": 0,
    }


def _get_cache_stats(peer_ip: str) -> Dict[str, Any]:
    if peer_ip not in _CACHE_STATS:
        _CACHE_STATS[peer_ip] = {
            **_new_type_counters(),
            "bytes_would": 0,
            "bytes_actual": 0,
            "rtt_samples": deque(maxlen=200),
            # [PEER_LINK_QUALITY_V1] Each entry is a tuple:
            #   (ts_monotonic, rtt_ms, bytes_out, bytes_in, status)
            # status is "ok" for a frame that completed, "fail" for one
            # that didn't (timeout / connection reset / connection
            # refused / RPC budget exceeded).  Failed samples have
            # rtt_ms == elapsed_until_failure_ms (still useful - tells
            # the UI HOW LONG things waited before giving up) and
            # bytes_in == 0.  bytes_out reflects what was actually
            # written to the socket before the failure even if the
            # peer never read it; that's what loaded the link.
            "link_quality_samples": deque(maxlen=PEER_LINK_SAMPLE_CAP),
            # [LINK_POTENTIAL_PING_V1 2026-05-25] Per-peer link
            # potential, set at connect-time by the ladder probe in
            # transport_semantic._run_link_potential_ladder.  Used by
            # get_link_quality_snapshot to compute saturation_ratio.
            # When None, the snapshot falls back to absolute thresholds
            # for the UI.  Refreshed on every 9009 reconnect - never
            # at runtime (would interfere with live RPCs).
            "link_baseline": None,   # set by record_link_baseline
            "by_endpoint": defaultdict(_new_endpoint_stats),
            # [STALE_PEER_AGEOUT_V1] Wall-clock timestamp of most recent
            # activity for this peer.  _CACHE_STATS is otherwise
            # append-only - every IP that ever surfaces as a target_ip
            # gets a permanent row, including transient LAN clients
            # that briefly appeared (e.g. a phone joining wifi, getting
            # one HTTP probe, then leaving).  Fleet evidence: seattlesix
            # had 112 failed probes/4h to 10.160.160.20 (a regular LAN
            # client, not a FrogNet node) because it once registered as
            # a peer and never aged out.  _age_out_stale_peers() drops
            # peers idle past STALE_PEER_AGEOUT_SEC.
            "last_seen_ts": time.time(),
        }
    else:
        # Refresh on every access - any caller of _get_cache_stats is
        # signalling activity.
        _CACHE_STATS[peer_ip]["last_seen_ts"] = time.time()
    return _CACHE_STATS[peer_ip]


def record_link_baseline(
    peer_ip: str,
    *,
    slope_us_per_byte: float,
    intercept_us: float,
    num_points: int,
    bandwidth_bps: float,
    ladder_diag: str = "",
) -> None:
    """[LINK_POTENTIAL_PING_V1 2026-05-25] Store this peer's connect-
    time link-potential baseline.  Called exactly once per 9009
    connect by the ladder probe.

    slope_us_per_byte: serialization cost per byte (us/byte).
        At runtime, expected_rtt_for_n_bytes = intercept + slope * n.

    intercept_us: unloaded round-trip latency in microseconds.

    num_points: how many ladder data points contributed to the fit.
        Higher is more reliable.  Below say 6 we should mark the
        baseline as low-confidence; for now the field is informational.

    bandwidth_bps: implied bandwidth in bits/sec, derived from slope.
        Stored for display only; saturation calc uses slope directly.

    ladder_diag: per-stage diagnostic string the ladder built.
        Stored for inclusion in the snapshot so an operator can see
        exactly what the ladder measured.

    Replaces any prior baseline for this peer - most recent connect
    wins.  In practice connect events are rare enough that this is
    fine; if you want history it's in the ladder logs.
    """
    if not peer_ip:
        return
    with _lock:
        s = _get_cache_stats(peer_ip)
        s["link_baseline"] = {
            "slope_us_per_byte": float(slope_us_per_byte),
            "intercept_us": float(intercept_us),
            "num_points": int(num_points),
            "bandwidth_bps": float(bandwidth_bps),
            "ladder_diag": str(ladder_diag),
            "measured_at_mono": time.monotonic(),
            "measured_at_wall": time.time(),
        }


# ---------------------------------------------------------------------------
# Domain and IP detection
# ---------------------------------------------------------------------------
_cached_domain: Optional[str] = None
_cached_local_ip: Optional[str] = None


def _get_domain() -> str:
    """Node identity = the DOMAIN (e.g. 'Seattle5'), read from the ONE
    authoritative source: domain= in /etc/dnsmasq.d/opts_only.conf - the same
    file the daemon reads. hostname is always 'FrogNetHost' and is NOT identity.
    No fallbacks: if the domain can't be read, RAISE. A missing domain must fail
    loudly, never silently mis-file sensors under 'FrogNetHost' or 'FrogNet'.
    """
    global _cached_domain
    if _cached_domain is not None:
        return _cached_domain

    with open("/etc/dnsmasq.d/opts_only.conf", "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("domain="):
                domain = line.split("=", 1)[1].strip()
                if domain:
                    _cached_domain = domain
                    return domain

    raise RuntimeError(
        "[PROXY-METRICS] FATAL: no 'domain=' in /etc/dnsmasq.d/opts_only.conf; "
        "cannot determine node identity - refusing to emit mis-filed sensors")

def _get_frognet_local_ip() -> str:
    """This node's own address = the .1 of its served subnet. Every FrogNet node
    is FrogNetHost at the .1, and its identity is the dnsmasq domain= (via
    _get_domain), so the node's IP is FrogNetHost.<domain> resolved in /etc/hosts.
    ONE authoritative source - no substring matching, no interface guessing, no
    loopback fallback. Missing => RAISE, same as _get_domain: never mis-file a
    sensor under the wrong node's address."""
    global _cached_local_ip
    if _cached_local_ip is not None:
        return _cached_local_ip

    want = "FrogNetHost." + _get_domain()          # e.g. FrogNetHost.Seattle3
    with open("/etc/hosts") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("10.") and want in parts[1:]:
                _cached_local_ip = parts[0]
                return _cached_local_ip

    raise RuntimeError(
        f"[PROXY-METRICS] FATAL: {want} not found in /etc/hosts; "
        "cannot determine this node's own address")


# ---------------------------------------------------------------------------
# Public bump functions
# ---------------------------------------------------------------------------

def live_endpoint_stats(path: str = "") -> Dict[str, Any]:
    """The endpoint counters as they are RIGHT NOW, without waiting for a flush.

    [COUNTERS_ARE_READABLE_WHEN_ASKED_V1] The flusher publishes these to a
    sensor about every thirty seconds, which is the right cadence for a mesh to
    observe itself and the wrong one for anybody measuring a workload: a run of
    a few seconds falls entirely inside one interval and has nothing to diff.
    Bracketing four phases that way produced four "no flush landed" reports and
    a forty-second poll after each that read as the run hanging. Measured
    2026-08-15.

    Same aggregation the flusher does -- per-peer stats summed by endpoint --
    read under the same lock, so what this returns is what would be published if
    a flush happened at this instant. It publishes nothing and resets nothing;
    asking cannot perturb what is being measured.

    `path` returns one endpoint's counters; empty returns them all.
    """
    with _lock:
        ep_agg: Dict[str, Dict[str, Any]] = defaultdict(_new_endpoint_stats)
        for _peer_ip, s in _CACHE_STATS.items():
            for p, ep in s["by_endpoint"].items():
                agg = ep_agg[p]
                for k in agg:
                    if isinstance(agg[k], int) and k in ep:
                        agg[k] += ep[k]
        out = {
            "ts": int(time.time()),
            "uptime_sec": int(time.time() - _STATS_RESET_TS),
            "endpoints": {
                p: {"bytes_would": ep["bytes_would"],
                    "bytes_actual": ep["bytes_actual"],
                    "requests": sum(ep.get(rt, 0) for rt in _REQ_TYPES)}
                for p, ep in ep_agg.items()
                if not path or p.split("?")[0] == path
            },
        }
    return out


def bump_real(peer_ip: str, wan_req: int, wan_resp: int) -> None:
    """Track raw HTTP bytes on LOCAL path (proxy->Apache direct)."""
    with _lock:
        _ENGINE["local_requests"] += 1
        _ENGINE["local_bytes_req"] += wan_req
        _ENGINE["local_bytes_resp"] += wan_resp


def bump_sem(peer_ip: str, wire_req: int, wire_resp: int) -> None:
    """Track semantic wire bytes (FNW1 framed, after compression)."""
    with _lock:
        _ENGINE["semantic_wire_req"] += wire_req
        _ENGINE["semantic_wire_resp"] += wire_resp


def bump_cache(
    peer_ip: str,
    *,
    req_type: str,
    resp_type: str,
    endpoint: str = "",
    bytes_would: int = 0,
    bytes_actual: int = 0,
    rtt_ms: float = 0.0,
) -> None:
    """Track cache performance for a request."""
    with _lock:
        s = _get_cache_stats(peer_ip)

        if req_type in s:
            s[req_type] += 1
        if resp_type in s:
            s[resp_type] += 1

        cross = f"{req_type}\u2192{resp_type}"
        if cross in s:
            s[cross] += 1

        s["bytes_would"] += bytes_would
        s["bytes_actual"] += bytes_actual

        if rtt_ms > 0:
            s["rtt_samples"].append(rtt_ms)

        if endpoint:
            path = endpoint.split("?")[0] if "?" in endpoint else endpoint
            ep = s["by_endpoint"][path]
            if req_type in ep:
                ep[req_type] += 1
            if resp_type in ep:
                ep[resp_type] += 1
            cross = f"{req_type}\u2192{resp_type}"
            if cross in ep:
                ep[cross] += 1
            ep["bytes_would"] += bytes_would
            ep["bytes_actual"] += bytes_actual


def bump_coalesce(peer_ip: str) -> None:
    """Track when a proxy-side RPC was served from an in-flight duplicate."""
    with _lock:
        _ENGINE["coalesce_hits"] += 1


def bump_lru(hit: bool) -> None:
    """Track SAME LRU cache hit or miss."""
    with _lock:
        if hit:
            _ENGINE["lru_hits"] += 1
        else:
            _ENGINE["lru_misses"] += 1


def bump_link(next_hop: str, wire_out: int, wire_in: int) -> None:
    """Track wire bytes per physical next-hop link."""
    with _lock:
        if next_hop not in _LINK_STATS:
            _LINK_STATS[next_hop] = {"wire_out": 0, "wire_in": 0, "rpc_count": 0}
        _LINK_STATS[next_hop]["wire_out"] += wire_out
        _LINK_STATS[next_hop]["wire_in"] += wire_in
        _LINK_STATS[next_hop]["rpc_count"] += 1


def bump_link_quality(
    peer_ip: str,
    *,
    rtt_ms: float,
    bytes_out: int,
    bytes_in: int,
    status: str,
) -> None:
    """[PEER_LINK_QUALITY_V1 2026-05-25] Push one (rtt, bytes, status)
    sample into peer_ip's rolling link-quality ring.

    Call this from EVERY proxy RPC path, success or failure.  The
    emitter derives the window stats; do not bucket or summarize here.

    Parameters:
      rtt_ms     - wall-clock ms elapsed for this RPC, success or
                   fail.  On fail, the time spent waiting before the
                   failure was raised (timeout = budget; refuse =
                   tiny; reset = mid-stream).  The number is still
                   useful: a flaky link shows fast-failing samples;
                   a saturated link shows slow-succeeding ones; a
                   dead link shows slow-failing ones.
      bytes_out  - wire bytes actually written to the socket for
                   this RPC (request frame, after FNW1 framing and
                   any compression).  Counted even on failure since
                   they loaded the link.
      bytes_in   - wire bytes received from the peer.  0 on failure
                   if no reply came back.
      status     - "ok" or "fail".  Any other string is treated as
                   "fail" - the UI only distinguishes those two.
    """
    if not peer_ip:
        return
    if status not in ("ok", "fail"):
        status = "fail"
    ts = time.monotonic()
    with _lock:
        s = _get_cache_stats(peer_ip)
        s["link_quality_samples"].append(
            (ts, float(rtt_ms), int(bytes_out), int(bytes_in), status))


def bump_template(hit: bool) -> None:
    """Track template coverage: semantic (hit) vs raw bootstrap (miss)."""
    with _lock:
        if hit:
            _ENGINE["template_hits"] += 1
        else:
            _ENGINE["template_misses"] += 1


# ---------------------------------------------------------------------------
# Peer observation
# ---------------------------------------------------------------------------

def observe_peer(
    *,
    peer_ip: str,
    peer_name: str,
    dev: str,
    via: str,
    kind: str,
    reason: str,
) -> None:
    if not peer_ip:
        return
    now = time.time()
    with _PEER_OBS_LOCK:
        existing = _PEER_OBS.get(peer_ip)
        if existing is None:
            _PEER_OBS[peer_ip] = {
                "peer_name": peer_name,
                "dev": dev,
                "via": via,
                "kind": kind,
                "reason": reason,
                "first_seen": now,
                "last_seen": now,
                "request_count": 1,
            }
        else:
            existing["peer_name"] = peer_name
            existing["dev"] = dev
            existing["via"] = via
            existing["kind"] = kind
            existing["reason"] = reason
            existing["last_seen"] = now
            existing["request_count"] = existing.get("request_count", 0) + 1


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def _safe_pct(num, denom, places=4):
    return round(num / denom, places) if denom > 0 else 0.0


def _rtt_stats(rtt_list):
    if not rtt_list:
        return {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
    s = sorted(rtt_list)
    n = len(s)
    return {
        "avg_ms": round(statistics.mean(s), 2),
        "p50_ms": round(statistics.median(s), 2),
        "p95_ms": round(s[int(n * 0.95)], 2) if n >= 20 else round(s[-1], 2),
        "p99_ms": round(s[int(n * 0.99)], 2) if n >= 100 else round(s[-1], 2),
    }


def _fmt_link_speed(bps: float) -> str:
    if bps <= 0:
        return "0 bps"
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.1f} Gbps"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} kbps"
    return f"{bps:.0f} bps"


def _effective_throughput(bytes_would: int, bytes_actual: int,
                          elapsed_sec: int) -> Dict[str, Any]:
    if elapsed_sec <= 0:
        elapsed_sec = 1
    eff_bps = (bytes_would * 8) / elapsed_sec
    act_bps = (bytes_actual * 8) / elapsed_sec
    return {
        "effective_bps": round(eff_bps, 2),
        "actual_bps": round(act_bps, 2),
        "amplification": round(eff_bps / act_bps, 2) if act_bps > 0 else 0,
        "effective_link": _fmt_link_speed(eff_bps),
        "actual_link": _fmt_link_speed(act_bps),
        "elapsed_sec": elapsed_sec,
    }


def get_cache_snapshot() -> Dict[str, Any]:
    """
    Per-endpoint breakdown of cache performance.

    This is the ONLY place endpoint-level stats are surfaced.
    Aggregate totals are in SemanticProxy.Engine.
    Per-peer detail is in SemanticCache.Peer.{ip} sensors.
    """
    with _lock:
        now_ts = int(time.time())
        uptime_sec = int(now_ts - _STATS_RESET_TS)

        ep_agg: Dict[str, Dict[str, Any]] = defaultdict(_new_endpoint_stats)
        for peer_ip, s in _CACHE_STATS.items():
            for path, ep in s["by_endpoint"].items():
                agg = ep_agg[path]
                for k in agg:
                    if isinstance(agg[k], int) and k in ep:
                        agg[k] += ep[k]

        top_endpoints = []
        for path, ep in ep_agg.items():
            saved = ep["bytes_would"] - ep["bytes_actual"]
            total = sum(ep.get(rt, 0) for rt in _REQ_TYPES)
            top_endpoints.append({
                "path": path,
                "request_types": {rt: ep.get(rt, 0) for rt in _REQ_TYPES},
                "response_types": {rt: ep.get(rt, 0) for rt in _RESP_TYPES},
                "matrix": {f"{rq}\u2192{rs}": ep.get(f"{rq}\u2192{rs}", 0)
                           for rq in _REQ_TYPES for rs in _RESP_TYPES
                           if ep.get(f"{rq}\u2192{rs}", 0) > 0},
                "total_requests": total,
                "bytes_would": ep["bytes_would"],
                "bytes_actual": ep["bytes_actual"],
                "bytes_saved": saved,
                "compression_ratio": _safe_pct(saved, ep["bytes_would"]),
            })
        top_endpoints.sort(key=lambda x: x["bytes_saved"], reverse=True)

        return {
            "since_ts": int(_STATS_RESET_TS),
            "timestamp": now_ts,
            "uptime_sec": uptime_sec,
            "top_endpoints": top_endpoints[:20],
        }


def get_peer_snapshots() -> Dict[str, Dict[str, Any]]:
    """Per-peer cache stats with fixed schema for stable template learning."""
    with _lock:
        now_ts = int(time.time())
        uptime_sec = int(now_ts - _STATS_RESET_TS)
        peers: Dict[str, Dict[str, Any]] = {}

        for peer_ip, s in _CACHE_STATS.items():
            rtt_list = list(s["rtt_samples"])
            p_total = sum(s.get(rt, 0) for rt in _REQ_TYPES)
            p_same = s.get("RESP_SAME", 0)
            p_saved = s["bytes_would"] - s["bytes_actual"]

            peers[peer_ip] = {
                "peer_ip": peer_ip,
                "timestamp": now_ts,
                "uptime_sec": uptime_sec,
                "request_types": {rt: s.get(rt, 0) for rt in _REQ_TYPES},
                "response_types": {rt: s.get(rt, 0) for rt in _RESP_TYPES},
                "total_requests": p_total,
                "cache_hit_rate": _safe_pct(p_same, p_total),
                "bytes_would": s["bytes_would"],
                "bytes_actual": s["bytes_actual"],
                "bytes_saved": p_saved,
                "compression_ratio": _safe_pct(p_saved, s["bytes_would"]),
                "rtt": _rtt_stats(rtt_list),
                "effective_throughput": _effective_throughput(
                    s["bytes_would"], s["bytes_actual"], uptime_sec),
            }

        return peers


def get_engine_snapshot() -> Dict[str, Any]:
    """
    Single snapshot of the entire semantic compression engine.

    This is THE sensor to look at first.  Everything else is detail.
    """
    with _lock:
        now_ts = int(time.time())
        uptime_sec = int(now_ts - _STATS_RESET_TS)

        total_would = 0
        total_actual = 0
        total_reqs = 0
        total_same = 0
        total_miss = 0
        total_error = 0
        all_rtt = []

        for s in _CACHE_STATS.values():
            total_would += s["bytes_would"]
            total_actual += s["bytes_actual"]
            total_reqs += sum(s.get(rt, 0) for rt in _REQ_TYPES)
            total_same += s.get("RESP_SAME", 0)
            total_miss += s.get("REQ_MISS", 0)
            total_error += s.get("ERROR", 0)
            all_rtt.extend(list(s["rtt_samples"]))

        total_saved = total_would - total_actual

        tpl_hits = _ENGINE["template_hits"]
        tpl_misses = _ENGINE["template_misses"]
        tpl_total = tpl_hits + tpl_misses

        coal_hits = _ENGINE["coalesce_hits"]

        lru_h = _ENGINE["lru_hits"]
        lru_m = _ENGINE["lru_misses"]
        lru_total = lru_h + lru_m

        links = {}
        for nh, ls in _LINK_STATS.items():
            total_link_bytes = ls["wire_out"] + ls["wire_in"]
            wire_bps = (total_link_bytes * 8) / uptime_sec if uptime_sec > 0 else 0
            links[nh] = {
                "wire_out": ls["wire_out"],
                "wire_in": ls["wire_in"],
                "wire_total": total_link_bytes,
                "rpc_count": ls["rpc_count"],
                "wire_bps": round(wire_bps, 2),
                "wire_rate": _fmt_link_speed(wire_bps),
            }

        return {
            "timestamp": now_ts,
            "uptime_sec": uptime_sec,
            # --- Headlines ---
            "compression_ratio": _safe_pct(total_saved, total_would),
            "amplification": round(total_would / total_actual, 2) if total_actual > 0 else 0,
            "same_rate": _safe_pct(total_same, total_reqs),
            "template_coverage": _safe_pct(tpl_hits, tpl_total),
            "error_rate": _safe_pct(total_miss + total_error, total_reqs),
            "lru_hit_rate": _safe_pct(lru_h, lru_total),
            # --- Volume ---
            "total_requests": total_reqs,
            "bytes_would": total_would,
            "bytes_actual": total_actual,
            "bytes_saved": total_saved,
            # --- Breakdown ---
            "template_hits": tpl_hits,
            "template_misses": tpl_misses,
            "lru_hits": lru_h,
            "lru_misses": lru_m,
            "coalesce_hits": coal_hits,
            "same_count": total_same,
            "miss_count": total_miss,
            "error_count": total_error,
            # --- LOCAL path ---
            "local_requests": _ENGINE["local_requests"],
            "local_bytes_req": _ENGINE["local_bytes_req"],
            "local_bytes_resp": _ENGINE["local_bytes_resp"],
            # --- Wire totals ---
            "semantic_wire_req": _ENGINE["semantic_wire_req"],
            "semantic_wire_resp": _ENGINE["semantic_wire_resp"],
            # --- Throughput ---
            "effective_throughput": _effective_throughput(total_would, total_actual, uptime_sec),
            "rtt": _rtt_stats(all_rtt),
            # --- Per-link ---
            "links": links,
        }


def get_topology_snapshot() -> Dict[str, Any]:
    now = time.time()
    links = []

    with _PEER_OBS_LOCK:
        for peer_ip, obs in _PEER_OBS.items():
            age_sec = now - obs.get("last_seen", now)
            links.append({
                "peer_ip": peer_ip,
                "peer_name": obs.get("peer_name", peer_ip),
                "dev": obs.get("dev", ""),
                "via": obs.get("via", ""),
                "kind": obs.get("kind", "UNKNOWN"),
                "reason": obs.get("reason", ""),
                "last_seen_sec_ago": round(age_sec, 1),
                "request_count": obs.get("request_count", 0),
            })

    with _lock:
        for link in links:
            pip = link["peer_ip"]
            if pip in _CACHE_STATS:
                rtt_list = list(_CACHE_STATS[pip]["rtt_samples"])
                link["rtt_avg_ms"] = round(statistics.mean(rtt_list), 2) if rtt_list else 0
                link["rtt_p95_ms"] = (
                    round(sorted(rtt_list)[int(len(rtt_list) * 0.95)], 2)
                    if len(rtt_list) >= 20
                    else (round(max(rtt_list), 2) if rtt_list else 0)
                )
            else:
                link["rtt_avg_ms"] = 0
                link["rtt_p95_ms"] = 0

    return {
        "timestamp": int(now),
        "since_ts": int(_STATS_RESET_TS),
        "links": links,
    }


def get_link_quality_snapshot(window_sec: float) -> Dict[str, Any]:
    """[PEER_LINK_QUALITY_V1 2026-05-25] Per-peer rolling-window
    link-quality stats over the last `window_sec` seconds.

    Returns RAW numbers only - no derived state.  The UI categorizes.

    Per-peer fields:
      sample_count       - total samples in window
      ok_count           - samples with status=="ok"
      fail_count         - samples with status=="fail"
      success_rate       - ok_count / sample_count, 0.0 if no samples
      rtt_ok_avg_ms      - mean RTT of successful samples (0 if none)
      rtt_ok_p50_ms      - median RTT of successful samples
      rtt_ok_p95_ms      - p95 RTT of successful samples (max for <20)
      rtt_fail_avg_ms    - mean elapsed-before-failure (0 if none)
      bytes_out          - total wire bytes sent (incl. fail samples)
      bytes_in           - total wire bytes received
      bytes_out_per_sec  - bytes_out / actual_window_sec
      bytes_in_per_sec   - bytes_in / actual_window_sec
      window_sec         - the effective window applied (= the larger
                           of the caller's request and the hard floor
                           PEER_LINK_QUALITY_MIN_WINDOW_SEC)
      oldest_sample_age_sec - wall-clock age of the oldest sample
                              actually selected, or null if no samples
                              fell inside the window
      newest_sample_age_sec - same, for newest

    Peers with zero samples in window are STILL returned with
    sample_count=0; this lets the UI distinguish "peer we know about
    but heard nothing from recently" from "peer doesn't exist."
    """
    effective_window = max(float(window_sec), PEER_LINK_QUALITY_MIN_WINDOW_SEC)
    now_mono = time.monotonic()
    cutoff = now_mono - effective_window
    peers: Dict[str, Any] = {}

    with _lock:
        for peer_ip, s in _CACHE_STATS.items():
            ring = s.get("link_quality_samples")
            if ring is None:
                # Pre-V1 entry without the new field; treat as empty.
                peers[peer_ip] = {
                    "sample_count": 0, "ok_count": 0, "fail_count": 0,
                    "success_rate": 0.0,
                    "rtt_ok_avg_ms": 0, "rtt_ok_p50_ms": 0, "rtt_ok_p95_ms": 0,
                    "rtt_fail_avg_ms": 0,
                    "bytes_out": 0, "bytes_in": 0,
                    "bytes_out_per_sec": 0.0, "bytes_in_per_sec": 0.0,
                    "window_sec": effective_window,
                    "oldest_sample_age_sec": None,
                    "newest_sample_age_sec": None,
                }
                continue
            # Filter ring entries to those inside the window.  Iterating
            # the deque is O(n); the cap keeps n small.  We don't pop
            # old entries here - the bounded deque does that lazily and
            # other emitters may want a wider window in the future.
            in_window = [t for t in ring if t[0] >= cutoff]
            sample_count = len(in_window)
            ok_rtts: list = []
            fail_rtts: list = []
            bytes_out_total = 0
            bytes_in_total = 0
            for _ts, rtt, b_out, b_in, status in in_window:
                bytes_out_total += b_out
                bytes_in_total += b_in
                if status == "ok":
                    ok_rtts.append(rtt)
                else:
                    fail_rtts.append(rtt)
            ok_count = len(ok_rtts)
            fail_count = len(fail_rtts)
            success_rate = (ok_count / sample_count) if sample_count else 0.0
            rtt_ok_avg = round(statistics.mean(ok_rtts), 2) if ok_rtts else 0
            if ok_rtts:
                sorted_ok = sorted(ok_rtts)
                rtt_ok_p50 = round(sorted_ok[len(sorted_ok) // 2], 2)
                rtt_ok_p95 = round(
                    sorted_ok[int(len(sorted_ok) * 0.95)]
                    if len(sorted_ok) >= 20 else max(sorted_ok),
                    2,
                )
            else:
                rtt_ok_p50 = 0
                rtt_ok_p95 = 0
            rtt_fail_avg = round(statistics.mean(fail_rtts), 2) if fail_rtts else 0
            bytes_out_per_sec = round(bytes_out_total / effective_window, 2)
            bytes_in_per_sec = round(bytes_in_total / effective_window, 2)
            if in_window:
                oldest_age = round(now_mono - in_window[0][0], 2)
                newest_age = round(now_mono - in_window[-1][0], 2)
            else:
                oldest_age = None
                newest_age = None

            peers[peer_ip] = {
                "sample_count": sample_count,
                "ok_count": ok_count,
                "fail_count": fail_count,
                "success_rate": round(success_rate, 4),
                "rtt_ok_avg_ms": rtt_ok_avg,
                "rtt_ok_p50_ms": rtt_ok_p50,
                "rtt_ok_p95_ms": rtt_ok_p95,
                "rtt_fail_avg_ms": rtt_fail_avg,
                "bytes_out": bytes_out_total,
                "bytes_in": bytes_in_total,
                "bytes_out_per_sec": bytes_out_per_sec,
                "bytes_in_per_sec": bytes_in_per_sec,
                "window_sec": effective_window,
                "oldest_sample_age_sec": oldest_age,
                "newest_sample_age_sec": newest_age,
            }

    return {
        "timestamp": int(time.time()),
        "window_sec": effective_window,
        "min_window_sec_floor": PEER_LINK_QUALITY_MIN_WINDOW_SEC,
        "peers": peers,
    }


def reset_all_stats() -> Dict[str, Any]:
    global _STATS_RESET_TS
    summary: Dict[str, Any] = {"reset_at": int(time.time())}

    with _lock:
        total_reqs = 0
        total_bytes_saved = 0
        for s in _CACHE_STATS.values():
            total_reqs += sum(s.get(rt, 0) for rt in _REQ_TYPES)
            total_bytes_saved += s.get("bytes_would", 0) - s.get("bytes_actual", 0)
        summary["cleared_requests"] = total_reqs
        summary["cleared_bytes_saved"] = total_bytes_saved
        _CACHE_STATS.clear()
        _LINK_STATS.clear()
        for k in _ENGINE:
            _ENGINE[k] = 0

    with _PEER_OBS_LOCK:
        summary["cleared_peers"] = len(_PEER_OBS)
        _PEER_OBS.clear()

    _STATS_RESET_TS = time.time()
    return summary


# ---------------------------------------------------------------------------
# Sensor emission - two-table API
# ---------------------------------------------------------------------------

_sensor_id_cache: Dict[str, tuple] = {}
_sensor_id_lock = threading.RLock()


def _invalidate_sensor_id(sensor_name: str) -> None:
    with _sensor_id_lock:
        old = _sensor_id_cache.pop(sensor_name, None)
        if old:
            print(f"[PROXY-METRICS] invalidated cached SensorID={old[0]} for {sensor_name}", flush=True)


def _api_url(entity: str, action: str, **params) -> str:
    # Connect to the ELECTED global data host (databasehost.frognet), not
    # loopback. The old `http://127.0.0.1/...` with `Host: databasehost.frognet`
    # only worked on the box that IS the databasehost (there loopback == the DB);
    # every other node wrote its engine/service telemetry into its OWN local
    # api.php - a dead end nothing reads - which is why non-DB dashboards showed
    # "No engine data yet". The Host header still carries the vhost name; the
    # CONNECTION now goes to the real host.
    base = f"http://{_API_HOST}:{_EMIT_PORT}/api.php"
    qs = f"entity={entity}&action={action}"
    for k, v in params.items():
        qs += f"&{k}={urllib.parse.quote(str(v), safe='')}"
    return f"{base}?{qs}"


def _api_get(entity: str, action: str, **params) -> Any:
    url = _api_url(entity, action, **params)
    headers = {"Host": _API_HOST, "X-FrogNet-Origin-Local": "1"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if isinstance(body, dict):
        if "rows" in body:
            return body["rows"]
        if "row" in body:
            return body["row"]
    return body


def _api_post(entity: str, action: str, payload: dict) -> Any:
    url = _api_url(entity, action)
    headers = {
        "Host": _API_HOST,
        "Content-Type": "application/json",
        "X-FrogNet-Origin-Local": "1",
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if isinstance(body, dict):
        if "row" in body:
            return body["row"]
        if "rows" in body:
            return body["rows"]
    return body


def _api_put(entity: str, action: str, payload: dict) -> Any:
    url = _api_url(entity, action)
    headers = {
        "Host": _API_HOST,
        "Content-Type": "application/json",
        "X-FrogNet-Origin-Local": "1",
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if isinstance(body, dict):
        if "row" in body:
            return body["row"]
    return body


def _resolve_sensor_id(sensor_name: str, sensor_type: str) -> tuple:
    with _sensor_id_lock:
        cached = _sensor_id_cache.get(sensor_name)
        if cached:
            return cached

    try:
        rows = _api_get("sensors", "list", SensorName=sensor_name, limit="1")
        if isinstance(rows, list) and rows:
            sid = rows[0].get("SensorID")
            fid = rows[0].get("FrogID")
            if sid:
                with _sensor_id_lock:
                    _sensor_id_cache[sensor_name] = (sid, fid)
                print(f"[PROXY-METRICS] resolved {sensor_name} -> SensorID={sid}", flush=True)
                return (sid, fid)
    except Exception as e:
        print(f"[PROXY-METRICS] sensor lookup failed for {sensor_name}: {e!r}", flush=True)
        return (None, None)

    try:
        local_ip = _get_frognet_local_ip()
        network = '.'.join(local_ip.split('.')[:3]) + ".0/24" if local_ip != "127.0.0.1" else ""
        frog_id = str(uuid.uuid4())

        result = _api_post("sensors", "create", {
            "FrogID": frog_id,
            "SensorAddress": local_ip,
            "SensorNetwork": network,
            "SensorName": sensor_name,
            "SensorType": sensor_type,
            "Tags": "",
        })

        sid = None
        if isinstance(result, dict):
            sid = result.get("SensorID")
        if not sid:
            rows = _api_get("sensors", "list", SensorName=sensor_name, limit="1")
            if isinstance(rows, list) and rows:
                sid = rows[0].get("SensorID")
                frog_id = rows[0].get("FrogID", frog_id)

        if not sid:
            print(f"[PROXY-METRICS] FAILED to create sensor {sensor_name}", flush=True)
            return (None, None)

        _api_post("sensor_data", "create", {
            "SensorID": sid,
            "FrogID": frog_id,
            "jsonData": {},
        })

        with _sensor_id_lock:
            _sensor_id_cache[sensor_name] = (sid, frog_id)

        print(f"[PROXY-METRICS] created sensor {sensor_name} SensorID={sid} FrogID={frog_id}", flush=True)
        return (sid, frog_id)

    except Exception as e:
        print(f"[PROXY-METRICS] sensor create FAILED for {sensor_name}: {e!r}", flush=True)
        return (None, None)


def _invalidate_all_sensor_ids(reason: str) -> None:
    """Flush entire SensorID cache - called on DB wipe detection."""
    with _sensor_id_lock:
        count = len(_sensor_id_cache)
        _sensor_id_cache.clear()
    if count > 0:
        print(f"[PROXY-METRICS] CACHE FLUSH: cleared {count} cached SensorIDs ({reason})", flush=True)


def _verify_cache_health() -> None:
    """Verify one cached SensorID still exists in DB.

    If it's gone, the database was wiped - flush the entire cache.
    Called once at the start of each flush cycle.  Cost: one GET.
    """
    with _sensor_id_lock:
        if not _sensor_id_cache:
            return
        # Pick any cached entry
        sensor_name, (sid, fid) = next(iter(_sensor_id_cache.items()))

    try:
        row = _api_get("sensors", "get", SensorID=sid)
        # _api_get unwraps {"row": {...}} -> the inner dict
        if isinstance(row, dict):
            if row.get("SensorID") or row.get("SensorName"):
                return  # still exists, cache is healthy
            if "error" in row or not row:
                _invalidate_all_sensor_ids(f"SensorID={sid} gone from DB (wipe detected)")
                return
        # Unexpected response shape - play it safe
        _invalidate_all_sensor_ids(f"SensorID={sid} verify returned unexpected: {type(row).__name__}")
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            _invalidate_all_sensor_ids(f"SensorID={sid} HTTP {e.code} (wipe detected)")
        else:
            print(f"[PROXY-METRICS] cache verify failed: HTTP {e.code} for SensorID={sid}", flush=True)
    except Exception as e:
        print(f"[PROXY-METRICS] cache verify failed: {e!r}", flush=True)


def _post_sensor(sensor_name: str, sensor_type: str, json_data: Dict[str, Any]) -> None:
    """Write sensor data to DB.  Verifies the update landed.

    If the PUT succeeds (HTTP 200) but affected zero rows (stale SensorID),
    invalidates the cache and re-creates the sensor.  No silent failures.
    """
    sid, fid = _resolve_sensor_id(sensor_name, sensor_type)
    if not sid:
        return

    headers = {
        "Host": _API_HOST,
        "Content-Type": "application/json",
        "X-FrogNet-Origin-Local": "1",
    }

    try:
        url = _api_url("sensor_data", "update", SensorID=sid)
        data = json.dumps({"jsonData": json_data}, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read()

        # Check if the update actually affected a row
        update_ok = False
        try:
            result = json.loads(resp_body)
            if isinstance(result, dict):
                # api.php may return affected_rows, or the updated row, or just {}
                affected = result.get("affected_rows", result.get("affected", -1))
                if affected == 0:
                    # Explicit zero - SensorID doesn't exist
                    update_ok = False
                elif affected > 0:
                    update_ok = True
                elif "SensorID" in result or "jsonData" in result:
                    # Returned the row - update worked
                    update_ok = True
                elif result == {} or result.get("error"):
                    # Empty response or error - suspicious
                    update_ok = False
                else:
                    # Unknown shape - assume it worked but verify next cycle
                    update_ok = True
            else:
                update_ok = True  # non-dict response, can't check
        except (json.JSONDecodeError, ValueError):
            update_ok = True  # can't parse response, assume ok

        if update_ok:
            print(f"[PROXY-METRICS] emitted {sensor_name} SensorID={sid}", flush=True)
            return

        # Update didn't land - stale SensorID
        print(f"[PROXY-METRICS] PUT returned 200 but 0 rows affected for {sensor_name} SensorID={sid} - re-creating", flush=True)
        _invalidate_sensor_id(sensor_name)
        sid, fid = _resolve_sensor_id(sensor_name, sensor_type)
        if not sid:
            print(f"[PROXY-METRICS] re-create FAILED for {sensor_name} - no SensorID", flush=True)
            return

        # Ensure SensorData row exists - UPDATE returns 0 when the row is missing
        try:
            _api_post("sensor_data", "create", {
                "SensorID": sid,
                "FrogID": fid,
                "jsonData": json_data,
            })
        except Exception:
            pass  # row may already exist, CREATE will 409/fail, that's fine

        url = _api_url("sensor_data", "update", SensorID=sid)
        data = json.dumps({"jsonData": json_data}, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print(f"[PROXY-METRICS] re-created and emitted {sensor_name} SensorID={sid}", flush=True)

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[PROXY-METRICS] SensorID={sid} 404 for {sensor_name}, re-creating", flush=True)
            _invalidate_sensor_id(sensor_name)
            try:
                sid, fid = _resolve_sensor_id(sensor_name, sensor_type)
                if not sid:
                    return
                url = _api_url("sensor_data", "update", SensorID=sid)
                data = json.dumps({"jsonData": json_data}, separators=(",", ":")).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                print(f"[PROXY-METRICS] re-created and emitted {sensor_name} SensorID={sid}", flush=True)
            except Exception as e2:
                print(f"[PROXY-METRICS] emit FAILED for {sensor_name} after re-create: {e2!r}", flush=True)
        else:
            print(f"[PROXY-METRICS] emit FAILED for {sensor_name}: HTTP {e.code} {e.reason}", flush=True)

    except Exception as e:
        print(f"[PROXY-METRICS] emit FAILED for {sensor_name}: {e!r}", flush=True)


def _emit_all_sensors() -> None:
    domain = _get_domain()

    # [METRICS_COALESCE_V1] Collect every metric this tick into ONE batch and send
    # it as a single fire-and-forget upsert_batch. Previously each metric was its
    # own resolve+PUT round-trip (plus a verify read and a 1s per-peer sleep),
    # producing a multi-second burst of dozens of requests per node per tick that
    # saturated the single elected database host into 503s. No verify: metrics are
    # fire-and-forget telemetry - a dropped sample self-heals next tick.
    items = []
    _laddr = _get_frognet_local_ip()
    _lnet = ('.'.join(_laddr.split('.')[:3]) + ".0/24"
             if _laddr and _laddr != "127.0.0.1" else "")

    def _add(sensor_name, sensor_type, json_data):
        items.append({
            "SensorName": sensor_name,
            "SensorType": sensor_type,
            "SensorAddress": _laddr,
            "SensorNetwork": _lnet,
            "jsonData": json_data,
        })

    try:
        _add(f"{domain}.SemanticCache.Endpoints", "SemanticCache.Endpoints",
             get_cache_snapshot())

        for peer_ip, peer_data in get_peer_snapshots().items():
            _add(f"{domain}.SemanticCache.Peer.{peer_ip}", "SemanticCache.Peer",
                 peer_data)

        _add(f"{domain}.Topology.Links", "Topology", get_topology_snapshot())

        _add(f"{domain}.SemanticProxy.LinkQuality", "SemanticProxy.LinkQuality",
             get_link_quality_snapshot(window_sec=_current_flusher_interval))

        _add(f"{domain}.SemanticProxy.Engine", "SemanticProxy.Engine",
             get_engine_snapshot())

        # [NO_FALLBACK_V1] The import stays lazy - proxy_metrics and
        # transport_semantic are circular at module load - but lazy is not
        # optional. An ImportError means the module is missing or broken and
        # propagates; only a runtime failure of the call is reported and
        # skipped, so one bad metric does not cost the whole batch.
        try:
            for ps in pipeline_stats():
                peer_ip = ps.get("peer", "")
                if peer_ip:
                    _add(f"{domain}.Pipeline.Peer.{peer_ip}", "Pipeline.Peer", ps)
        except Exception as e:
            print(f"[PROXY-METRICS] pipeline stats collection failed at "
                  f"runtime: {e!r}", flush=True)

        try:
            apache_snap = get_apache_snapshot()
            if apache_snap:
                _add(f"{domain}.Apache.Workers", "Apache.Workers", apache_snap)
        except Exception as e:
            print(f"[PROXY-METRICS] apache stats collection failed: {e!r}", flush=True)

        # [NO_FALLBACK_V1] See above: lazy for the circular import, not optional.
        try:
            resilience = routing_resilience_snapshot()
            if resilience:
                _add(f"{domain}.Routing.Resilience", "Routing.Resilience", resilience)
        except Exception as e:
            print(f"[PROXY-METRICS] routing resilience collection failed at "
                  f"runtime: {e!r}", flush=True)
    except Exception as e:
        print(f"[PROXY-METRICS] snapshot collection failed: {e!r}", flush=True)

    if not items:
        return
    # Single round-trip, fire-and-forget: send and do not read-verify.
    try:
        _api_post("sensor_data", "upsert_batch", {"items": items})
        print(f"[PROXY-METRICS] batch emitted {len(items)} sensors", flush=True)
    except Exception as e:
        # Telemetry only - never let a metrics failure disturb the node.
        print(f"[PROXY-METRICS] batch emit failed (ignored): {e!r}", flush=True)


# ---------------------------------------------------------------------------
# Apache worker stats and auto-tuning
# ---------------------------------------------------------------------------

def get_apache_snapshot() -> Optional[Dict[str, Any]]:
    """Collect Apache prefork worker stats and compute optimal MaxRequestWorkers.

    Reads:
      - /proc/meminfo for total/available RAM
      - apache2ctl fullstatus or /server-status for active workers
      - Current MaxRequestWorkers from apache config
      - Average worker RSS from /proc

    Computes recommended MaxRequestWorkers based on available RAM,
    reserving 1GB for OS + proxy + daemon + MySQL.
    """
    import subprocess as _sp

    snap: Dict[str, Any] = {}

    # --- Memory ---
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mi[parts[0].rstrip(":")] = int(parts[1])  # kB
            snap["mem_total_mb"] = mi.get("MemTotal", 0) // 1024
            snap["mem_available_mb"] = mi.get("MemAvailable", 0) // 1024
            snap["mem_free_mb"] = mi.get("MemFree", 0) // 1024
    except Exception:
        snap["mem_total_mb"] = 0
        snap["mem_available_mb"] = 0

    # --- Apache process stats ---
    try:
        r = _sp.run(
            ["pgrep", "-f", "apache2.*worker|apache2.*prefork|httpd"],
            capture_output=True, text=True, check=False
        )
        apache_pids = [p.strip() for p in r.stdout.splitlines() if p.strip()]
        snap["apache_worker_count"] = len(apache_pids)

        # Measure average RSS per worker
        rss_values = []
        for pid in apache_pids:
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss_kb = int(line.split()[1])
                            rss_values.append(rss_kb)
                            break
            except Exception:
                pass
        if rss_values:
            snap["worker_rss_avg_mb"] = round(sum(rss_values) / len(rss_values) / 1024, 1)
            snap["worker_rss_max_mb"] = round(max(rss_values) / 1024, 1)
            snap["worker_rss_total_mb"] = round(sum(rss_values) / 1024, 1)
        else:
            snap["worker_rss_avg_mb"] = 0
            snap["worker_rss_max_mb"] = 0
            snap["worker_rss_total_mb"] = 0
    except Exception:
        snap["apache_worker_count"] = 0
        snap["worker_rss_avg_mb"] = 0

    # --- Current MaxRequestWorkers ---
    try:
        r = _sp.run(
            ["grep", "-rh", "MaxRequestWorkers\\|MaxClients",
             "/etc/apache2/"],
            capture_output=True, text=True, check=False
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    snap["current_max_workers"] = int(parts[1])
                    break
                except ValueError:
                    pass
    except Exception:
        pass

    # --- Compute recommended MaxRequestWorkers ---
    # Reserve 1GB for OS + proxy + daemon + MySQL
    reserved_mb = 1024
    mem_total = snap.get("mem_total_mb", 0)
    worker_avg = snap.get("worker_rss_avg_mb", 0)

    if mem_total > 0 and worker_avg > 0:
        available_for_apache = max(0, mem_total - reserved_mb)
        recommended = int(available_for_apache / worker_avg)
        recommended = max(10, min(recommended, 512))  # sane bounds
        snap["recommended_max_workers"] = recommended
        snap["headroom_mb"] = snap.get("mem_available_mb", 0) - snap.get("worker_rss_total_mb", 0)
    else:
        # Fallback: estimate 8MB per worker
        if mem_total > 0:
            available_for_apache = max(0, mem_total - reserved_mb)
            snap["recommended_max_workers"] = max(10, min(int(available_for_apache / 8), 512))

    snap["mpm"] = "prefork"  # detected earlier; hardcode for now
    snap["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    return snap if snap.get("mem_total_mb", 0) > 0 else None


# ---------------------------------------------------------------------------
# Flusher thread
# ---------------------------------------------------------------------------

_flusher_started = False
_flusher_lock = threading.RLock()
# [PEER_LINK_QUALITY_V1 2026-05-25] The interval the flusher was
# started with - published as a module global so _emit_all_sensors
# can pass it to get_link_quality_snapshot as the window.  Default
# matches _EMIT_INTERVAL until the flusher starts.
_current_flusher_interval: float = _EMIT_INTERVAL

# [STALE_PEER_AGEOUT_V1] How long a peer's last_seen_ts can lag before
# we drop it from _CACHE_STATS.  Default 1 hour: long enough that a
# real FrogNet peer that briefly went silent doesn't lose its history;
# short enough that a one-shot LAN client (DHCP'd phone, transient
# browser probe, decommissioned dev box) doesn't accumulate forever.
# Env-overridable for ops who need a different floor.
_STALE_PEER_AGEOUT_SEC = float(os.environ.get(
    "FROGNET_STALE_PEER_AGEOUT_SEC", "3600"))


def _age_out_stale_peers() -> int:
    """[STALE_PEER_AGEOUT_V1] Drop _CACHE_STATS rows for peers idle past
    the age-out threshold.  Called once per flusher tick.

    Guard: NEVER drop a peer whose IP ends in `.1` - those are FrogNet
    host identities.  A real node that's silent for an hour (rare -
    proxy traffic at minimum is constant) is still a valid peer and
    we want to keep its accumulated stats.  Only `.2` admin probes
    and arbitrary other-octet IPs (LAN clients) are eligible for
    aging.

    Returns count of peers dropped.

    Fleet evidence motivating this: seattlesix had 112 failed probes
    over 4h to 10.160.160.20 because the proxy kept it in its peer
    set after a one-time interaction.  The proxy's flusher then
    posted `SeattleSix.SemanticCache.Peer.10.160.160.20` sensors
    indefinitely, and downstream consumers (UI / dashboard) treated
    .20 as a real peer.
    """
    now = time.time()
    cutoff = now - _STALE_PEER_AGEOUT_SEC
    to_drop: list[str] = []
    with _lock:
        for peer_ip, s in _CACHE_STATS.items():
            # Keep all .1 peers regardless of idle time
            if peer_ip.endswith(".1"):
                continue
            last = s.get("last_seen_ts", now)  # legacy rows: treat as now
            if last < cutoff:
                to_drop.append(peer_ip)
        for peer_ip in to_drop:
            del _CACHE_STATS[peer_ip]
    if to_drop:
        print(f"[PROXY-METRICS] aged out {len(to_drop)} stale peers: "
              f"{', '.join(to_drop[:10])}"
              f"{'...' if len(to_drop) > 10 else ''}", flush=True)
    return len(to_drop)


def _hb_register(process: str) -> None:
    """[NODE_HEARTBEAT_TS_V1] Wire this process's cache drops to the heartbeat."""
    # [NO_FALLBACK_V1] frognet_tuples and semcache_db are imported at module
    # scope now. This try covers only on_database_change() failing at runtime.
    try:
        _sc = _proxy_cache
        def _drop():
            n = _sc.clear_all_seen()
            print(f"[PROXY-METRICS] [NODE_HEARTBEAT_TS_V1] dropped {n} "
                  f"seen-marker(s)", flush=True)
            # Cached SensorIDs are PRIMARY KEYS from the old database: in a
            # rebuilt or floated-to database the same id is absent, or belongs
            # to a DIFFERENT sensor and mis-files telemetry under its row.
            # _invalidate_all_sensor_ids already exists for exactly this and is
            # reused rather than duplicated - _verify_cache_health() probes one
            # cached id per flush cycle to catch a wipe, and the heartbeat is
            # the general form of the same detection.
            _invalidate_all_sensor_ids("NODE_HEARTBEAT_TS_V1 database change")
            # [NO_FALLBACK_V1] Import hoisted; this covers only a runtime
            # clear_all() failure, which must not kill the heartbeat callback.
            try:
                m = _lrc.clear_all()
                print(f"[PROXY-METRICS] [NODE_HEARTBEAT_TS_V1] dropped {m} "
                      f"local read(s)", flush=True)
            except Exception as e:
                print(f"[PROXY-METRICS] local_read_cache drop failed at "
                      f"runtime: {e!r}", flush=True)
        _T.on_database_change(process, _drop)
    except Exception as e:
        print(f"[PROXY-METRICS] heartbeat registration failed: {e!r}", flush=True)


def _hb_cycle(process: str) -> None:
    """[NODE_HEARTBEAT_TS_V1] One heartbeat cycle. Never raises into the loop."""
    # [NO_FALLBACK_V1] frognet_tuples is imported at module scope, so a version
    # skew that removes heartbeat() is an AttributeError at startup with the
    # attribute named - not one warning per cycle for the life of the process,
    # which is what the NY2 log shows. This covers a runtime call failure.
    try:
        _T.heartbeat(process)
    except Exception as e:
        print(f"[PROXY-METRICS] heartbeat cycle failed at runtime: {e!r}",
              flush=True)


def _flusher_loop(interval: float) -> None:
    # [METRICS_STAGGER_V1] Random startup phase (0..interval) so every node does
    # NOT fire its batch on the same wall-clock boundary and pile onto the elected
    # database host simultaneously. One-time offset spreads the fleet's writes
    # across the whole interval.
    # [NO_FALLBACK_V1] `import random` in try/except Exception: pass. random is
    # stdlib - its absence is a broken interpreter - and time.sleep() on a
    # non-negative float cannot fail. The guard could only hide a real fault.
    time.sleep(random.uniform(0.0, max(0.0, interval)))
    _hb_register("proxy")
    while True:
        time.sleep(interval)
        try:
            # [NODE_HEARTBEAT_TS_V1] Read-then-write this process's heartbeat
            # BEFORE emitting. If the database changed, caches are dropped first
            # so nothing this cycle is computed from data that no longer exists.
            _hb_cycle("proxy")
            _emit_all_sensors()
            # [STALE_PEER_AGEOUT_V1] Age-out runs AFTER sensor emission
            # so the final snapshot of a soon-to-be-dropped peer still
            # makes it to the dashboard (so a real peer that dies
            # doesn't just vanish from history without a final reading).
            _age_out_stale_peers()
            print(f"[PROXY-METRICS] flusher fired at {time.strftime('%H:%M:%S')}", flush=True)
        except Exception as e:
            print(f"[PROXY-METRICS] flusher error: {e!r}", flush=True)


def start_flusher(interval: float = 30.0) -> None:
    global _flusher_started, _current_flusher_interval
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
        _current_flusher_interval = float(interval)
    t = threading.Thread(target=_flusher_loop, args=(interval,), daemon=True)
    # t = threading.Thread(target=_flusher_loop, args=(500,), daemon=True)
    t.start()
    print(f"[PROXY-METRICS] flusher started, interval={interval}s "
          f"(link_quality floor={PEER_LINK_QUALITY_MIN_WINDOW_SEC}s)", flush=True)
