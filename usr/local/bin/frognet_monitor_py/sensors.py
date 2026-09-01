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
frognet_monitor/sensors.py — Sensor data from the transient database.

All requests go to databasehost.frognet through the proxy.
The proxy routes 10.x destinations through daemon:9009 automatically.
databasehost.frognet can float — never hardwire its IP.

Two tables:
  sensors    — metadata (SensorID, SensorName, SensorType)
  sensor_data — payload (SensorID, jsonData)
Joined by SensorID.
"""

import json
import threading
import urllib.parse
from typing import Any, Dict, List, Optional

from .discovery import api_request
from .identity import LOCAL_DOMAIN
from .trace import trace


def _url_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe='')


def fetch_sensors_for_host(host_name: str) -> List[Dict]:
    """Fetch exactly this host's sensors, keyed on the domain-prefixed name.

    Every telemetry sensor this node's proxy and metric_upsert.sh emit is named
    "<domain>.<type>" (e.g. "NY1.SemanticProxy.Engine", "NY1.System.Perf.load"),
    and host_name IS that domain (discovery returns the part after "FrogNetHost.").
    An exact SensorName prefix therefore returns ALL of this host's telemetry and,
    because it is an exact prefix, NONE of any other host's.
    """
    if not host_name:
        return []
    try:
        # Filter SERVER-SIDE. api.php action=list builds a WHERE from allow-listed
        # fields (exact on a bare field, LIKE on <field>__like), so only this
        # host's rows come over the wire, not the whole ~thousands-row table.
        #
        # [SENSOR_FETCH_NAME_ONLY_V1] Name prefix ONLY. The prior version merged a
        # second SensorAddress LIKE "<ip /24>.%" query. That keyed on the /24
        # NETWORK, not the host: any node sharing the /24 leaked its rows into this
        # host's list. The only rows it added over the name query were SD:-prefixed
        # coordination tuples (capability, presence, call signaling) — not
        # telemetry, and carrying the node's EXACT .1, so a /24 was never the right
        # key for them either. Match the domain-prefixed name and nothing else, so
        # "sensors for <host>" is exactly this host's telemetry.
        base = ("http://databasehost.frognet/api.php"
                "?entity=sensors&action=list")
        u = f"{base}&SensorName__like={_url_encode(host_name + '.%')}"
        by_id = {}
        for r in (api_request(u, timeout=5) or []):
            if isinstance(r, dict) and r.get("SensorID") is not None:
                by_id[r["SensorID"]] = r
        data = list(by_id.values())
        trace(f"[SENSORS] fetch_sensors_for_host({host_name}): "
              f"{len(data)} rows (name-prefix, server-filtered)")
        return data
    except Exception as e:
        trace(f"[SENSORS] fetch_sensors_for_host({host_name}) failed: {e}")
        return []

def fetch_sensor_detail(sensor_name: str) -> Optional[Dict]:
    """Fetch a sensor's full data (metadata + jsonData).

    Step 1: entity=sensors&action=list&SensorName={name} → get SensorID
    Step 2: entity=sensor_data&action=get&SensorID={id}  → get jsonData
    Returns combined dict.
    """
    try:
        # Step 1: Find sensor metadata by exact name
        url = (
            f"http://databasehost.frognet/api.php?entity=sensors&action=list"
            f"&SensorName={_url_encode(sensor_name)}&limit=1"
        )
        sensors = api_request(url, timeout=3)
        if not isinstance(sensors, list) or not sensors:
            return None
        sensor = sensors[0]
        sid = sensor.get("SensorID")
        if not sid:
            return sensor  # metadata only, no SensorID

        # Step 2: Get jsonData by SensorID
        url2 = (
            f"http://databasehost.frognet/api.php?entity=sensor_data&action=get"
            f"&SensorID={_url_encode(sid)}"
        )
        sd = api_request(url2, timeout=3)
        if isinstance(sd, dict) and "jsonData" in sd:
            jd = sd["jsonData"]
            if isinstance(jd, str):
                try:
                    jd = json.loads(jd)
                except (json.JSONDecodeError, ValueError):
                    pass
            sensor["jsonData"] = jd
        return sensor
    except Exception as e:
        trace(f"[SENSORS] fetch_sensor_detail({sensor_name}) failed: {e}")
        return None


def fetch_engine_json() -> Optional[Dict]:
    """Fetch SemanticProxy.Engine sensor data for this node.

    Two-step: sensors lookup by name → sensor_data get by SensorID.
    Returns parsed jsonData dict or None.
    """
    sensor_name = f"{LOCAL_DOMAIN}.SemanticProxy.Engine"
    try:
        # Step 1: lookup sensor metadata
        url = (
            f"http://databasehost.frognet/api.php?entity=sensors&action=list"
            f"&SensorName={_url_encode(sensor_name)}&limit=1"
        )
        sensors = api_request(url, timeout=3)
        trace(f"[ENGINE] step1: sensors={sensors!r}")
        if not isinstance(sensors, list) or not sensors:
            trace(f"[ENGINE] step1: BAIL — empty or not list")
            return None
        sid = sensors[0].get("SensorID")
        if not sid:
            trace(f"[ENGINE] step1: BAIL — no SensorID in {sensors[0]!r}")
            return None

        # Step 2: get jsonData
        url2 = (
            f"http://databasehost.frognet/api.php?entity=sensor_data&action=get"
            f"&SensorID={_url_encode(sid)}"
        )
        sd = api_request(url2, timeout=3)
        trace(f"[ENGINE] step2: type={type(sd).__name__} "
              f"keys={list(sd.keys()) if isinstance(sd, dict) else 'N/A'}")
        if isinstance(sd, dict) and "jsonData" in sd:
            jd = sd["jsonData"]
            if isinstance(jd, str):
                jd = json.loads(jd)
            _trace_jsondata(jd)
            return jd
        return None
    except Exception as e:
        trace(f"[ENGINE] fetch_engine_json failed: {e}")
        return None


def fetch_daemon_json() -> Optional[Dict]:
    """Fetch SemanticDaemon.Cache sensor data for this node.

    Two-step: sensors lookup by name → sensor_data get by SensorID.
    Returns parsed jsonData dict or None.
    """
    sensor_name = f"{LOCAL_DOMAIN}.SemanticDaemon.Cache"
    try:
        url = (
            f"http://databasehost.frognet/api.php?entity=sensors&action=list"
            f"&SensorName={_url_encode(sensor_name)}&limit=1"
        )
        sensors = api_request(url, timeout=3)
        if not isinstance(sensors, list) or not sensors:
            return None
        sid = sensors[0].get("SensorID")
        if not sid:
            return None

        url2 = (
            f"http://databasehost.frognet/api.php?entity=sensor_data&action=get"
            f"&SensorID={_url_encode(sid)}"
        )
        sd = api_request(url2, timeout=3)
        if isinstance(sd, dict) and "jsonData" in sd:
            jd = sd["jsonData"]
            if isinstance(jd, str):
                jd = json.loads(jd)
            return jd
        return None
    except Exception as e:
        trace(f"[DAEMON] fetch_daemon_json failed: {e}")
        return None


def fetch_link_quality_json() -> Optional[Dict]:
    """[LINK_POTENTIAL_PING_V1 2026-05-25] Fetch this node's
    SemanticProxy.LinkQuality sensor.  Same two-step pattern as
    fetch_daemon_json.

    The payload shape is:
        {
          "timestamp": <int>,
          "window_sec": <float>,
          "min_window_sec_floor": 30.0,
          "peers": {
            "<peer_ip>": {
              "sample_count": ...,
              "success_rate": ...,
              "rtt_ok_p95_ms": ...,
              "saturation_ratio_p50": ...,    # may be None
              "saturation_ratio_p95": ...,    # may be None
              "link_potential_bandwidth_bps": ...,
              "link_potential_intercept_ms": ...,
              "link_baseline_age_sec": ...,
              "link_baseline_num_points": ...,
              ...
            },
            ...
          }
        }

    The monitor uses the per-peer saturation_ratio_p95 and
    rtt_ok_p95_ms to color the per-peer status indicator.
    """
    sensor_name = f"{LOCAL_DOMAIN}.SemanticProxy.LinkQuality"
    try:
        url = (
            f"http://databasehost.frognet/api.php?entity=sensors&action=list"
            f"&SensorName={_url_encode(sensor_name)}&limit=1"
        )
        sensors = api_request(url, timeout=3)
        if not isinstance(sensors, list) or not sensors:
            return None
        sid = sensors[0].get("SensorID")
        if not sid:
            return None

        url2 = (
            f"http://databasehost.frognet/api.php?entity=sensor_data&action=get"
            f"&SensorID={_url_encode(sid)}"
        )
        sd = api_request(url2, timeout=3)
        if isinstance(sd, dict) and "jsonData" in sd:
            jd = sd["jsonData"]
            if isinstance(jd, str):
                jd = json.loads(jd)
            return jd
        return None
    except Exception as e:
        trace(f"[LINK_QUALITY] fetch_link_quality_json failed: {e}")
        return None


def fetch_peer_sensors(peer_ips: list = None) -> Dict[str, Dict]:
    """Fetch SemanticCache.Peer.* sensor data for each known peer IP.

    Uses exact SensorName lookups (which work reliably) instead of
    SensorName__like queries (which the API returns empty for).

    Each peer runs as a parallel two-step fetch:
      sensor metadata by exact name → sensor_data by SensorID.

    Returns {peer_ip: jsonData_dict, ...} for direct lookup by IP.
    """
    if not peer_ips:
        return {}

    result = {}

    def _fetch_peer(peer_ip):
        sensor_name = f"{LOCAL_DOMAIN}.SemanticCache.Peer.{peer_ip}"
        try:
            # Step 1: lookup sensor by exact name
            url = (
                f"http://databasehost.frognet/api.php?entity=sensors&action=list"
                f"&SensorName={_url_encode(sensor_name)}&limit=1"
            )
            sensors = api_request(url, timeout=3)
            if not isinstance(sensors, list) or not sensors:
                trace(f"[PEER] {sensor_name}: no sensor found")
                return
            sid = sensors[0].get("SensorID")
            if not sid:
                trace(f"[PEER] {sensor_name}: no SensorID in response")
                return

            # Step 2: fetch jsonData by SensorID
            url2 = (
                f"http://databasehost.frognet/api.php?entity=sensor_data&action=get"
                f"&SensorID={_url_encode(sid)}"
            )
            sd = api_request(url2, timeout=3)
            if not isinstance(sd, dict) or "jsonData" not in sd:
                trace(f"[PEER] {sensor_name}: bad sensor_data response")
                return
            jd = sd["jsonData"]
            if isinstance(jd, str):
                jd = json.loads(jd)
            if isinstance(jd, dict):
                result[peer_ip] = jd
                trace(f"[PEER] {sensor_name} -> {peer_ip} OK")
            else:
                trace(f"[PEER] {sensor_name}: jsonData not a dict")
        except Exception as e:
            trace(f"[PEER] {sensor_name} failed: {e}")

    threads = []
    for ip in peer_ips:
        t = threading.Thread(target=_fetch_peer, args=(ip,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=8)

    trace(f"[PEER] final: {len(result)}/{len(peer_ips)} peers: {list(result.keys())}")
    return result


def _trace_jsondata(jd: Any) -> None:
    """Dump jsonData structure to trace log for debugging."""
    trace(f"[ENGINE] jsonData keys="
          f"{list(jd.keys()) if isinstance(jd, dict) else type(jd).__name__}")
    if isinstance(jd, dict):
        for k, v in jd.items():
            vtype = type(v).__name__
            vpreview = repr(v)[:120] if not isinstance(v, (int, float)) else repr(v)
            trace(f"[ENGINE]   {k}: ({vtype}) {vpreview}")
