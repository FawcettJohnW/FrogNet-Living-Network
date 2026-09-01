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
daemon/daemon_metrics.py

Metrics collection and emission for FrogNet Semantic Daemon.

v5.0 changes:
  FIX: Emission now uses proper two-table API (list/create/update).
       v4.x called action=upsert_by_name which doesn't exist - every
       emission silently 400'd.  Zero daemon telemetry ever reached DB.
  FIX: Stats are CUMULATIVE (no reset after emission).  Consistent with
       proxy_metrics.  Use reset_daemon_stats() explicitly if needed.
  ADD: bump_daemon_coalesce - tracks daemon-side request coalescing.
  ADD: SensorID caching (same pattern as proxy_metrics).
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import os
import time
import json
import socket
import threading
import statistics
import urllib.request
import urllib.error
import urllib.parse
from collections import defaultdict, deque
from typing import Dict, Any, Optional
import random
import uuid   # [NO_FALLBACK_V1] stdlib, imported at module scope

# [NO_FALLBACK_V1] Same change as proxy/proxy_metrics.py. These were imported
# lazily inside `except Exception` blocks on the emit path, so a missing or
# skewed first-party module printed one WARNING per flush cycle forever and the
# metric quietly stopped existing. The NY2 log carries ten copies of
#   [DAEMON-METRICS] heartbeat cycle failed:
#       AttributeError("module 'core.frognet_tuples' has no attribute 'heartbeat'")
# - a startup-class version skew reported as a routine per-cycle warning.
#
# Module scope separates the two failures the guards conflated: a missing or
# wrong-version module stops the process; a runtime call failure is reported and
# the loop continues. The handlers below now cover only the second.
from core import frognet_tuples as _T
from daemon.engine import data_cache as _dc

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEBUG = os.environ.get("FROGNET_DEBUG", "0").strip() == "1"
_EMIT_PORT = int(os.environ.get("FROGNET_METRICS_EMIT_PORT", "80"))
_API_HOST = os.environ.get("FROGNET_DB_HOST", "databasehost.frognet")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_lock = threading.RLock()

# Legacy semantic counter
_SEM_STATS: Dict[str, Dict[str, int]] = defaultdict(lambda: {"wire_in": 0, "wire_out": 0})

# Cache performance stats (per peer)
_CACHE_STATS: Dict[str, Dict[str, Any]] = {}

# Engine-wide counters
_ENGINE: Dict[str, int] = {
    "coalesce_hits": 0,
}

_STATS_RESET_TS: float = time.time()


# ---------------------------------------------------------------------------
# [PEER_LINK_QUALITY_V1 2026-05-25] Per-peer rolling link-quality samples.
# Mirror of the proxy-side ring (see proxy_metrics.py).  Daemon-side
# samples reflect the SERVER perspective: bytes_in is the frame we
# received from the peer; bytes_out is the reply we sent back; rtt_ms
# is the wall-clock time WE spent handling the request (effectively
# server processing latency, NOT network RTT).  Both halves are needed
# to compute end-to-end RTT - the proxy-side rtt is roughly
# server_rtt + 2*one_way_network_latency.
#
# As with the proxy ring, the deque cap is a memory bound; the
# effective window is applied at snapshot time using a wall-clock
# cutoff with a hard floor of PEER_LINK_QUALITY_MIN_WINDOW_SEC.
PEER_LINK_SAMPLE_CAP = int(os.environ.get(
    "FROGNET_PEER_LINK_SAMPLE_CAP", "2048"))
PEER_LINK_QUALITY_MIN_WINDOW_SEC = 30.0  # HARD floor - not env-overridable
# ---------------------------------------------------------------------------


def _get_cache_stats(peer_ip: str) -> Dict[str, Any]:
    if peer_ip not in _CACHE_STATS:
        _CACHE_STATS[peer_ip] = {
            "req_full": 0,
            "req_repeat": 0,
            "req_diff": 0,
            "req_raw": 0,
            "resp_same": 0,
            "resp_diff": 0,
            "resp_raw": 0,
            "req_miss": 0,
            "bytes_in": 0,
            "bytes_out": 0,
            "bytes_saved": 0,
            "exec_times_ms": deque(maxlen=200),
            # [PEER_LINK_QUALITY_V1] Same tuple shape as the proxy-side
            # ring: (ts_monotonic, rtt_ms, bytes_out, bytes_in, status).
            "link_quality_samples": deque(maxlen=PEER_LINK_SAMPLE_CAP),
            "by_opcode": defaultdict(lambda: {"same": 0, "diff": 0, "miss": 0, "exec_ms": []}),
        }
    return _CACHE_STATS[peer_ip]


# ---------------------------------------------------------------------------
# Public bump functions
# ---------------------------------------------------------------------------

def bump_semantic(peer_ip: str, wire_in: int, wire_out: int) -> None:
    """Track wire bytes (FNW1 framed)."""
    with _lock:
        _SEM_STATS[peer_ip]["wire_in"] += wire_in
        _SEM_STATS[peer_ip]["wire_out"] += wire_out


def bump_daemon_cache(
    peer_ip: str,
    *,
    req_type: str,
    resp_type: str,
    bytes_in: int = 0,
    bytes_out: int = 0,
    bytes_full_response: int = 0,
    exec_ms: float = 0.0,
    opcode: int = None,
) -> None:
    with _lock:
        s = _get_cache_stats(peer_ip)
        if req_type in s:
            s[req_type] += 1
        if resp_type in s:
            s[resp_type] += 1
        s["bytes_in"] += bytes_in
        s["bytes_out"] += bytes_out
        if resp_type == "resp_same" and bytes_full_response > 0:
            s["bytes_saved"] += (bytes_full_response - bytes_out)
        if exec_ms > 0:
            s["exec_times_ms"].append(exec_ms)
        if opcode is not None:
            op_stats = s["by_opcode"][opcode]
            if resp_type == "resp_same":
                op_stats["same"] += 1
            elif resp_type == "resp_diff":
                op_stats["diff"] += 1
            elif resp_type == "req_miss":
                op_stats["miss"] += 1
            if exec_ms > 0:
                op_stats["exec_ms"].append(exec_ms)
                if len(op_stats["exec_ms"]) > 100:
                    op_stats["exec_ms"] = op_stats["exec_ms"][-100:]


def bump_daemon_coalesce(peer_ip: str) -> None:
    """Track when a daemon-side request was served from an in-flight duplicate."""
    with _lock:
        _ENGINE["coalesce_hits"] += 1


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

    Daemon-side semantics:
      bytes_in   - wire bytes received from peer (request frame)
      bytes_out  - wire bytes sent in reply (0 on a failure where we
                   never wrote a reply)
      rtt_ms     - wall-clock ms spent processing this request inside
                   the daemon (not network RTT - see module doc).  On
                   failure, time-spent-before-failure.
      status     - "ok" or "fail".

    Call from every daemon RPC handler exit, success or failure.  The
    emitter derives window stats; do not bucket here.
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


def get_link_quality_snapshot(window_sec: float) -> Dict[str, Any]:
    """[PEER_LINK_QUALITY_V1 2026-05-25] Per-peer rolling-window link-
    quality stats over the last `window_sec` seconds.  Effective window
    is max(window_sec, PEER_LINK_QUALITY_MIN_WINDOW_SEC).

    See proxy_metrics.get_link_quality_snapshot for field semantics.
    Daemon-side rtt_* fields are server-processing latency, not network
    RTT.  Pair with proxy-side numbers in the UI to get end-to-end.
    """
    effective_window = max(float(window_sec), PEER_LINK_QUALITY_MIN_WINDOW_SEC)
    now_mono = time.monotonic()
    cutoff = now_mono - effective_window
    peers: Dict[str, Any] = {}

    with _lock:
        for peer_ip, s in _CACHE_STATS.items():
            ring = s.get("link_quality_samples")
            if ring is None:
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
                "bytes_out_per_sec": round(bytes_out_total / effective_window, 2),
                "bytes_in_per_sec": round(bytes_in_total / effective_window, 2),
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


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _safe_pct(num, denom, places=4):
    return round(num / denom, places) if denom > 0 else 0.0


def get_daemon_cache_snapshot() -> Dict[str, Any]:
    """Daemon cache performance snapshot."""
    with _lock:
        now_ts = int(time.time())
        uptime_sec = int(now_ts - _STATS_RESET_TS)

        totals = {
            "req_full": 0,
            "req_repeat": 0,
            "req_diff": 0,
            "req_raw": 0,
            "resp_same": 0,
            "resp_diff": 0,
            "resp_raw": 0,
            "req_miss": 0,
            "bytes_in": 0,
            "bytes_out": 0,
            "bytes_saved": 0,
        }

        by_peer = {}
        all_exec_times = []

        for peer_ip, s in _CACHE_STATS.items():
            for k in totals.keys():
                totals[k] += s.get(k, 0)

            exec_list = list(s["exec_times_ms"])
            all_exec_times.extend(exec_list)

            total_reqs = s["req_full"] + s["req_repeat"] + s["req_diff"] + s["req_raw"]
            cache_hits = s["resp_same"]

            by_peer[peer_ip] = {
                "req_full": s["req_full"],
                "req_repeat": s["req_repeat"],
                "req_diff": s["req_diff"],
                "req_raw": s["req_raw"],
                "resp_same": s["resp_same"],
                "resp_diff": s["resp_diff"],
                "resp_raw": s["resp_raw"],
                "req_miss": s["req_miss"],
                "total_requests": total_reqs,
                "cache_hit_rate": _safe_pct(cache_hits, total_reqs),
                "bytes_in": s["bytes_in"],
                "bytes_out": s["bytes_out"],
                "bytes_saved": s["bytes_saved"],
                "exec_avg_ms": round(statistics.mean(exec_list), 2) if exec_list else 0,
                "exec_p95_ms": round(sorted(exec_list)[int(len(exec_list) * 0.95)], 2) if len(exec_list) >= 20 else (round(max(exec_list), 2) if exec_list else 0),
            }

        total_reqs = totals["req_full"] + totals["req_repeat"] + totals["req_diff"] + totals["req_raw"]
        cache_hits = totals["resp_same"]

        totals["total_requests"] = total_reqs
        totals["cache_hit_rate"] = _safe_pct(cache_hits, total_reqs)
        totals["exec_avg_ms"] = round(statistics.mean(all_exec_times), 2) if all_exec_times else 0
        totals["exec_p95_ms"] = round(sorted(all_exec_times)[int(len(all_exec_times) * 0.95)], 2) if len(all_exec_times) >= 20 else (round(max(all_exec_times), 2) if all_exec_times else 0)
        totals["coalesce_hits"] = _ENGINE["coalesce_hits"]

        # Wire totals from legacy counters
        total_wire_in = 0
        total_wire_out = 0
        for peer_ip, ws in _SEM_STATS.items():
            total_wire_in += ws["wire_in"]
            total_wire_out += ws["wire_out"]
        totals["wire_in"] = total_wire_in
        totals["wire_out"] = total_wire_out

        opcode_totals: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"same": 0, "diff": 0, "miss": 0, "total": 0, "exec_ms": []})

        for peer_ip, s in _CACHE_STATS.items():
            for opcode, op_stats in s["by_opcode"].items():
                opcode_totals[opcode]["same"] += op_stats["same"]
                opcode_totals[opcode]["diff"] += op_stats["diff"]
                opcode_totals[opcode]["miss"] += op_stats["miss"]
                opcode_totals[opcode]["total"] += op_stats["same"] + op_stats["diff"] + op_stats["miss"]
                opcode_totals[opcode]["exec_ms"].extend(op_stats["exec_ms"])

        top_opcodes = []
        for opcode, stats in sorted(opcode_totals.items(), key=lambda x: x[1]["total"], reverse=True)[:20]:
            exec_ms = stats["exec_ms"]
            top_opcodes.append({
                "opcode": opcode,
                "same": stats["same"],
                "diff": stats["diff"],
                "miss": stats["miss"],
                "total": stats["total"],
                "cache_hit_rate": _safe_pct(stats["same"], stats["total"]),
                "exec_avg_ms": round(statistics.mean(exec_ms), 2) if exec_ms else 0,
            })

        return {
            "timestamp": now_ts,
            "uptime_sec": uptime_sec,
            "totals": totals,
            "by_peer": by_peer,
            "top_opcodes": top_opcodes,
        }


def reset_daemon_stats() -> Dict[str, Any]:
    """Explicit reset.  NOT called automatically after emission."""
    global _STATS_RESET_TS
    summary = {"reset_at": int(time.time())}
    with _lock:
        total_reqs = 0
        for s in _CACHE_STATS.values():
            total_reqs += s["req_full"] + s["req_repeat"] + s["req_diff"] + s["req_raw"]
        summary["cleared_requests"] = total_reqs
        _CACHE_STATS.clear()
        _SEM_STATS.clear()
        for k in _ENGINE:
            _ENGINE[k] = 0
    _STATS_RESET_TS = time.time()
    return summary


# ---------------------------------------------------------------------------
# Identity discovery
# ---------------------------------------------------------------------------

def _get_frognet_identity() -> tuple:
    """Discover this node's FrogNet hostname and LAN IP."""
    # Node identity = domain= in /etc/dnsmasq.d/opts_only.conf. ONE source, no
    # fallback: hostname is always "FrogNetHost" and is NOT identity. Missing
    # domain RAISES rather than silently mis-filing sensors.
    hostname = ""
    with open("/etc/dnsmasq.d/opts_only.conf", "r") as f:
        for line in f:
            if line.startswith("domain="):
                hostname = line.strip().split("=", 1)[1].strip()
                break
    if not hostname:
        raise RuntimeError(
            "[DAEMON-METRICS] FATAL: no 'domain=' in /etc/dnsmasq.d/opts_only.conf; "
            "cannot determine node identity")

    # This node's IP = the .1 of its served subnet, resolved authoritatively as
    # FrogNetHost.<domain> in /etc/hosts (every FrogNet node is FrogNetHost at its
    # .1). No ip-addr scan that can grab a borrowed guest lease. Missing => RAISE,
    # same as the domain check: never mis-file a sensor under the wrong address.
    want = "FrogNetHost." + hostname
    local_ip = None
    with open("/etc/hosts") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("10.") and want in parts[1:]:
                local_ip = parts[0]
                break
    if not local_ip:
        raise RuntimeError(
            f"[DAEMON-METRICS] FATAL: {want} not in /etc/hosts; "
            "cannot determine this node's own address")

    return hostname, local_ip


# ---------------------------------------------------------------------------
# Sensor emission - proper two-table API (matches proxy_metrics pattern)
# ---------------------------------------------------------------------------

_sensor_id_cache: Dict[str, tuple] = {}
_sensor_id_lock = threading.RLock()


def _api_url(entity: str, action: str, **params) -> str:
    # Connect to the ELECTED global data host (databasehost.frognet), not
    # loopback-with-a-Host-header. On a non-DB node the old 127.0.0.1 form wrote
    # daemon telemetry into its own local api.php, a dead end - the DB host only
    # ever saw its own. The CONNECTION goes to the real host now.
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


def _resolve_sensor_id(sensor_name: str, sensor_type: str) -> tuple:
    """Resolve SensorName -> (SensorID, FrogID), creating if needed."""
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
                print(f"[DAEMON-METRICS] resolved {sensor_name} -> SensorID={sid}", flush=True)
                return (sid, fid)
    except Exception as e:
        print(f"[DAEMON-METRICS] sensor lookup failed for {sensor_name}: {e!r}", flush=True)
        return (None, None)

    try:
        hostname, local_ip = _get_frognet_identity()
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
            print(f"[DAEMON-METRICS] FAILED to create sensor {sensor_name}", flush=True)
            return (None, None)

        _api_post("sensor_data", "create", {
            "SensorID": sid,
            "FrogID": frog_id,
            "jsonData": {},
        })

        with _sensor_id_lock:
            _sensor_id_cache[sensor_name] = (sid, frog_id)

        print(f"[DAEMON-METRICS] created sensor {sensor_name} SensorID={sid}", flush=True)
        return (sid, frog_id)

    except Exception as e:
        print(f"[DAEMON-METRICS] sensor create FAILED for {sensor_name}: {e!r}", flush=True)
        return (None, None)


def _invalidate_sensor_id(sensor_name: str) -> None:
    with _sensor_id_lock:
        _sensor_id_cache.pop(sensor_name, None)


def _invalidate_all_sensor_ids(reason: str) -> None:
    """Flush entire SensorID cache - called on DB wipe detection."""
    with _sensor_id_lock:
        count = len(_sensor_id_cache)
        _sensor_id_cache.clear()
    if count > 0:
        print(f"[DAEMON-METRICS] CACHE FLUSH: cleared {count} cached SensorIDs ({reason})", flush=True)


def _verify_cache_health() -> None:
    """Verify one cached SensorID still exists in DB.  Cost: one GET."""
    with _sensor_id_lock:
        if not _sensor_id_cache:
            return
        sensor_name, (sid, fid) = next(iter(_sensor_id_cache.items()))

    try:
        row = _api_get("sensors", "get", SensorID=sid)
        if isinstance(row, dict):
            if row.get("SensorID") or row.get("SensorName"):
                return
            if "error" in row or not row:
                _invalidate_all_sensor_ids(f"SensorID={sid} gone from DB (wipe detected)")
                return
        _invalidate_all_sensor_ids(f"SensorID={sid} verify returned unexpected: {type(row).__name__}")
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            _invalidate_all_sensor_ids(f"SensorID={sid} HTTP {e.code} (wipe detected)")
        else:
            print(f"[DAEMON-METRICS] cache verify failed: HTTP {e.code} for SensorID={sid}", flush=True)
    except Exception as e:
        print(f"[DAEMON-METRICS] cache verify failed: {e!r}", flush=True)


def _post_sensor(sensor_name: str, sensor_type: str, json_data: Dict[str, Any]) -> None:
    """Write sensor data to DB.  Verifies the update landed."""
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

        update_ok = False
        try:
            result = json.loads(resp_body)
            if isinstance(result, dict):
                affected = result.get("affected_rows", result.get("affected", -1))
                if affected == 0:
                    update_ok = False
                elif affected > 0:
                    update_ok = True
                elif "SensorID" in result or "jsonData" in result:
                    update_ok = True
                elif result == {} or result.get("error"):
                    update_ok = False
                else:
                    update_ok = True
            else:
                update_ok = True
        except (json.JSONDecodeError, ValueError):
            update_ok = True

        if update_ok:
            print(f"[DAEMON-METRICS] emitted {sensor_name} SensorID={sid}", flush=True)
            return

        print(f"[DAEMON-METRICS] PUT returned 200 but 0 rows affected for {sensor_name} SensorID={sid} - re-creating", flush=True)
        _invalidate_sensor_id(sensor_name)
        sid, fid = _resolve_sensor_id(sensor_name, sensor_type)
        if not sid:
            print(f"[DAEMON-METRICS] re-create FAILED for {sensor_name} - no SensorID", flush=True)
            return

        url = _api_url("sensor_data", "update", SensorID=sid)
        data = json.dumps({"jsonData": json_data}, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print(f"[DAEMON-METRICS] re-created and emitted {sensor_name} SensorID={sid}", flush=True)

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[DAEMON-METRICS] SensorID={sid} 404 for {sensor_name}, re-creating", flush=True)
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
                print(f"[DAEMON-METRICS] re-created and emitted {sensor_name} SensorID={sid}", flush=True)
            except Exception as e2:
                print(f"[DAEMON-METRICS] emit FAILED after re-create: {e2!r}", flush=True)
        else:
            print(f"[DAEMON-METRICS] emit FAILED for {sensor_name}: HTTP {e.code}", flush=True)

    except Exception as e:
        print(f"[DAEMON-METRICS] emit FAILED for {sensor_name}: {e!r}", flush=True)


def _emit_daemon_batch(window_sec: float) -> None:
    """[METRICS_COALESCE_V1] Emit the daemon's sensors (cache + link-quality) as a
    SINGLE fire-and-forget upsert_batch - one round-trip instead of two round-trips
    plus a verify read plus a 1s sleep between them. Telemetry only; a dropped
    sample self-heals next tick."""
    try:
        hostname, local_ip = _get_frognet_identity()
        network = ('.'.join(local_ip.split('.')[:3]) + ".0/24"
                   if local_ip and local_ip != "127.0.0.1" else "")
        items = [
            {"SensorName": f"{hostname}.SemanticDaemon.Cache",
             "SensorType": "SemanticDaemon.Cache",
             "SensorAddress": local_ip, "SensorNetwork": network,
             "jsonData": get_daemon_cache_snapshot()},
            {"SensorName": f"{hostname}.SemanticDaemon.LinkQuality",
             "SensorType": "SemanticDaemon.LinkQuality",
             "SensorAddress": local_ip, "SensorNetwork": network,
             "jsonData": get_link_quality_snapshot(window_sec=window_sec)},
        ]
    except Exception as e:
        print(f"[DAEMON-METRICS] snapshot collection failed: {e!r}", flush=True)
        return
    try:
        _api_post("sensor_data", "upsert_batch", {"items": items})
        print(f"[DAEMON-METRICS] batch emitted {len(items)} sensors", flush=True)
    except Exception as e:
        print(f"[DAEMON-METRICS] batch emit failed (ignored): {e!r}", flush=True)


# ---------------------------------------------------------------------------
# Flusher
# ---------------------------------------------------------------------------

_flusher_started = False
_flusher_lock = threading.RLock()


def _hb_register(process: str) -> None:
    """[NODE_HEARTBEAT_TS_V1] Wire the daemon's cache drops to the heartbeat."""
    # [NO_FALLBACK_V1] Both imports hoisted; this covers only a runtime failure
    # of on_database_change() / drop_and_reload().
    try:
        def _drop():
            try:
                _dc.drop_and_reload()
            except Exception as e:
                print(f"[DAEMON-METRICS] data_cache drop failed at runtime: "
                      f"{e!r}", flush=True)
            # Cached SensorIDs are PRIMARY KEYS from the old database - see
            # proxy_metrics._hb_register.
            _invalidate_all_sensor_ids("NODE_HEARTBEAT_TS_V1 database change")
        _T.on_database_change(process, _drop)
    except Exception as e:
        print(f"[DAEMON-METRICS] heartbeat registration failed: {e!r}",
              flush=True)


def _hb_cycle(process: str) -> None:
    """[NODE_HEARTBEAT_TS_V1] One heartbeat cycle. Never raises into the loop."""
    # [NO_FALLBACK_V1] See the module-scope import note. A version skew that
    # removes heartbeat() now fails at startup naming the attribute.
    try:
        _T.heartbeat(process)
    except Exception as e:
        print(f"[DAEMON-METRICS] heartbeat cycle failed at runtime: {e!r}",
              flush=True)


def _flusher_loop(interval: float) -> None:
    # [METRICS_STAGGER_V1] Random startup phase so daemons don't all fire on the
    # same boundary onto the elected database host.
    # [NO_FALLBACK_V1] random is stdlib and time.sleep() on a non-negative
    # float cannot fail; the guard could only hide a real fault.
    time.sleep(random.uniform(0.0, max(0.0, interval)))
    _hb_register("daemon")
    while True:
        time.sleep(interval)
        try:
            # [NODE_HEARTBEAT_TS_V1] see proxy_metrics: detect before emitting.
            _hb_cycle("daemon")
            _emit_daemon_batch(window_sec=interval)
        except Exception as e:
            print(f"[DAEMON-METRICS] flusher error: {e!r}", flush=True)


def start_daemon_flusher(interval: float = 300.0) -> None:
    global _flusher_started
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
    t = threading.Thread(target=_flusher_loop, args=(interval,), daemon=True)
    # t = threading.Thread(target=_flusher_loop, args=(500,), daemon=True)
    t.start()
    print(f"[DAEMON-METRICS] flusher started, interval={interval}s "
          f"(link_quality floor={PEER_LINK_QUALITY_MIN_WINDOW_SEC}s)", flush=True)
