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
frognet_tuples.py -- variables in the transient DB, the UnREST way.

Exchange memory, not messages. A variable is a tuple addressed
<Service><VarName><Session/Host> and read by associative query. Nobody sends
anybody anything: a node writes its variable, every node reads the converged
value on its next read. The transient floats (databasehost.frognet re-elects to
the highest IP), so writers re-resolve the host and re-assert on a heartbeat;
stale variables age out because the writer stopped re-asserting.

Mapping onto the real api.php sensor schema (SensorName is the unique key; we
own the JSON contents):

    SensorType    = <Service>                 e.g. "communicator"
    SensorName    = <VarName>.<scope>          scope = host:<ip>:<pid>  (node-scoped)
                                                       session:<id>     (call-scoped)
    SensorAddress = writer's 10/8 IP           ("who reported it")
    jsonData      = the value (a blob we define)

Write : POST api.php?entity=sensor_data&action=upsert_by_name  (atomic resolve-or-create)
Read  : GET  api.php?entity=sensors&action=values&SensorType=<svc>&parse=1
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import time


def _warn(msg: str) -> None:
    """Surface a non-fatal data problem on stderr (the merge journal captures it).
    Used when a single peer row is malformed: we drop that row but must leave a
    trace, never fail silently."""
    try:
        print(f"[TUPLE] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass
import urllib.request
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_DBHOST = "databasehost_control.frognet"   # the SD: coordination plane lives on
# the deterministic control host (highest .1), NOT on the elected data host. capability,
# presence, beacons and the Services table resolve here; durable sensor data stays on
# databasehost.frognet (the elected, capacity-clocked data host).

# Variables THIS process wrote, for atexit self-cleanup. atexit is the courtesy:
# it fires on normal exit / graceful signals, NOT on SIGKILL, power loss, or a
# crash -- which on a contested mesh is exactly when nodes die. So the reaper
# (frognet_reaper.py, 30-min ts sweep) is the guarantee; this just keeps the
# space tidy in the common case.
_OWNED: Set[Tuple[str, str]] = set()      # (dbhost, SensorName)
_ATEXIT_ARMED = False


def _local_ipv4s():
    """[(ifname, ipv4)] for up interfaces, via SIOCGIFADDR. Linux-only; returns []
    on any platform/permission issue (caller falls back). Lazy fcntl import so this
    module still imports on the Windows communicator."""
    out = []
    try:
        import fcntl, struct
        names = os.listdir("/sys/class/net")
    except Exception:
        return out
    for name in names:
        if name == "lo":
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ip = socket.inet_ntoa(fcntl.ioctl(
                s.fileno(), 0x8915,                       # SIOCGIFADDR
                struct.pack("256s", name[:15].encode()))[20:24])
            out.append((name, ip))
        except OSError:
            pass
        finally:
            s.close()
    return out


def my_ip() -> str:
    """This node's FrogNet 10/8 identity (the SensorAddress / scope key).

    Enumerate local IPv4s and return the FrogNet address: a 10/8 that is NOT the
    synthetic infra planes (10.253 cross-NAT transit, 10.254 chorus). Prefer the
    served-subnet host address (.1), then an eth* address, then the lowest.

    The old `connect(("10.0.0.1", 9))` trick returned the WRONG address: the FrogNet
    plane carries per-/24 routes, not a 10/8 default, so the connect fell through to
    the WAN default route (e.g. 192.168.0.21 dev wlan1) and keyed the capability tuple
    off-plane. The connect trick is kept only as a fallback, and only if it yields a
    10/8 address."""
    cands = [(ifn, ip) for ifn, ip in _local_ipv4s()
             if ip.split(".")[0] == "10" and ip.split(".")[1] not in ("253", "254")]
    if cands:
        cands.sort(key=lambda c: (not c[1].endswith(".1"),
                                  not c[0].startswith("eth"), c[1]))
        return cands[0][1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.0.0.1", 9))
        ip = s.getsockname()[0]
        return ip if ip.startswith("10.") else "0.0.0.0"
    except Exception:
        return "0.0.0.0"
    finally:
        s.close()


def host_scope(pid: Optional[int] = None) -> str:
    """Node scope: host:<ip>:<pid>. PID makes it unique per running instance and
    self-expiring -- a new instance writes a new key, the dead one stops asserting."""
    return f"host:{my_ip()}:{pid if pid is not None else os.getpid()}"


def node_scope() -> str:
    """Stable per-node scope: host:<ip> (NO pid). For a periodic REFRESH writer (the
    candidate advertiser) whose tuple must be ONE upserted row per host that outlives
    any single process -- not a fresh pid-keyed key every 60s. Pair with put(own=False)
    so it isn't atexit-reaped. Contrast host_scope(), whose pid suits a long-running
    service that wants its own tuple reaped when that instance stops."""
    return f"host:{my_ip()}"


def role_scope(role: str) -> str:
    """Stable per-(node, role) scope: host:<ip>:<role>. A capability tuple is written
    under MULTIPLE services at one node (mediahost AND databasehost). SensorName is the
    DB's unique key and does NOT carry SensorType, so a bare node_scope (host:<ip>)
    yields the SAME SensorName for both roles -- upsert_by_name then overwrites one with
    the other, leaving a single row whose role flip-flops. Qualifying the scope with
    the role keeps the two rows distinct. role is alphabetic, so it never collides with
    a pid suffix and the self-prune can tell them apart."""
    return f"host:{my_ip()}:{role}"


def session_scope(session_id: str) -> str:
    """Call scope: session:<id>."""
    return f"session:{session_id}"


SD_PREFIX = "SD:"   # service-variable marker. A tuple whose SensorName starts SD:
                    # is ephemeral coordination state (caps, level, presence, call
                    # signaling) -- self-identifying, so a dead service's orphans stay
                    # recognizable. Observed sensor data (DHT/GPS/System/LinkState)
                    # carries NO SD: prefix and is therefore never a reap candidate.


def var_name(var: str, scope: str) -> str:
    return f"{SD_PREFIX}{var}.{scope}"


def put(service: str, var: str, scope: str, value: Dict[str, Any],
        dbhost: str = DEFAULT_DBHOST, timeout: float = 4.0,
        own: bool = True) -> bool:
    """Write/refresh a variable. Stamps ts so readers can age it out.

    own=True (default): this PROCESS owns the tuple -- it is registered for atexit
    cleanup so a long-running service's ephemeral coordination state (presence, call
    signaling) is removed when the service stops.
    own=False: a fire-and-forget REFRESH write -- a oneshot/periodic writer (e.g. the
    60s candidate advertiser) whose tuple must OUTLIVE the short process and age out
    by ts staleness instead. Arming cleanup here would delete the tuple on the very
    next exit (milliseconds later), so a oneshot registrar's writes would never
    persist -- which is exactly the failure that left the transient with no
    <role>/capability rows for the election to read.
    """
    payload = dict(value)
    payload.setdefault("ts", int(time.time()))
    url = f"http://{dbhost}/api.php?entity=sensor_data&action=upsert_by_name"
    body = json.dumps({
        "SensorName": var_name(var, scope),
        "SensorType": service,
        "SensorAddress": my_ip(),
        "jsonData": payload,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = bool(json.loads(r.read().decode() or "{}").get("ok", True))
        if ok and own:
            _OWNED.add((dbhost, var_name(var, scope)))
            _arm_atexit()
        return ok
    except Exception:
        return False


def _arm_atexit() -> None:
    global _ATEXIT_ARMED
    if not _ATEXIT_ARMED:
        atexit.register(cleanup_owned)
        _ATEXIT_ARMED = True


def cleanup_owned() -> None:
    """Delete the tuples this process wrote. Registered via atexit; also callable
    explicitly. Best-effort -- never raises."""
    for dbhost, name in list(_OWNED):
        try:
            rows = _values_raw(SERVICE_ANY, dbhost, name_like=name)
            for row in rows:
                if row.get("SensorName") == name and row.get("SensorID") is not None:
                    _delete_by_id(dbhost, row["SensorID"])
        except Exception:
            pass
        _OWNED.discard((dbhost, name))


SERVICE_ANY = ""   # sentinel: query without a SensorType filter (any service)


def _values_raw(service: str, dbhost: str = DEFAULT_DBHOST,
                name_like: Optional[str] = None,
                timeout: float = 4.0) -> List[Dict[str, Any]]:
    """Raw sensors/values rows (with SensorID), parsed JSON folded into 'data'.
    Filters by SensorType when service is given, and/or by exact SensorName."""
    q = "http://%s/api.php?entity=sensors&action=values&parse=1" % dbhost
    if service:
        q += f"&SensorType={service}"
    if name_like is not None:
        q += f"&SensorName={urllib.parse.quote(name_like)}"
    req = urllib.request.Request(q, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rows = json.loads(r.read().decode()).get("rows", [])
    for row in rows:
        if not isinstance(row.get("data"), dict):
            raw = row.get("jsonData")
            try:
                row["data"] = json.loads(raw) if isinstance(raw, str) else None
            except Exception:
                row["data"] = None
    return rows


def _delete_by_id(dbhost: str, sensor_id: Any, timeout: float = 4.0) -> bool:
    """Delete a sensor row by PK. SensorData cascades (ON DELETE CASCADE)."""
    url = f"http://{dbhost}/api.php?entity=sensors&action=delete&SensorID={sensor_id}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(json.loads(r.read().decode() or "{}").get("ok", False))
    except Exception:
        return False


def prune_self_stale_capability(role: str, var: str = "capability",
                                dbhost: str = DEFAULT_DBHOST) -> int:
    """Delete THIS host's DEPRECATED capability rows for `role`, keeping only the
    current role_scope row (host:<ip>:<role>). Clears the bare node_scope (host:<ip>,
    which collided across roles) and pid-keyed (host:<ip>:<pid>) variants left by
    earlier code, instead of waiting for the 30-min reaper. _values_raw is filtered by
    SensorType=role, so the OTHER role's row (host:<ip>:<other>) is never touched, and
    we only act on THIS host's own ip -- non-racy across nodes."""
    ip = my_ip()
    if not ip.startswith("10."):
        return 0
    keep = var_name(var, f"host:{ip}:{role}")
    base = f"{SD_PREFIX}{var}.host:{ip}"        # matches bare and any :suffix
    n = 0
    try:
        for row in _values_raw(role, dbhost):
            nm = row.get("SensorName", "")
            if nm == keep:
                continue
            if (nm == base or nm.startswith(base + ":")) and row.get("SensorID") is not None:
                if _delete_by_id(dbhost, row["SensorID"]):
                    n += 1
    except Exception:
        pass
    return n


def get_all(service: str, dbhost: str = DEFAULT_DBHOST,
            fresh_s: int = 0, timeout: float = 4.0) -> List[Dict[str, Any]]:
    """Read every variable for a service. Returns rows as
    {var, scope, name, addr, value(dict)}. fresh_s>0 drops stale (un-reasserted)
    variables by their ts."""
    rows = _values_raw(service, dbhost, timeout=timeout)
    now = int(time.time())
    out: List[Dict[str, Any]] = []
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        # [MALFORMED_ROW_GUARD_V1] One corrupt peer blob must never abort the read for
        # an entire service. Seen in the wild: a probe clobbered fields so `ts` held a
        # temps_c array instead of an int -- int() then raised, unwound the eager get(),
        # and the caller's bare except turned it into ZERO candidates for the role while
        # every other (valid, fresh) row was silently discarded. A blob whose ts isn't a
        # number can't be assessed for freshness and isn't trustworthy: drop THIS row,
        # log it, keep the rest.
        try:
            ts = int(data.get("ts", 0) or 0)
        except (TypeError, ValueError):
            _warn(f"get_all service={service} drop_malformed_ts "
                  f"name={row.get('SensorName', '')!r} ts={data.get('ts')!r}")
            continue
        if fresh_s and (now - ts) > fresh_s:
            continue
        sn = row.get("SensorName", "")
        name = sn[len(SD_PREFIX):] if sn.startswith(SD_PREFIX) else sn
        var, _, scope = name.partition(".")
        out.append({"var": var, "scope": scope, "name": sn,
                    "addr": row.get("SensorAddress", ""), "value": data})
    return out


def get(service: str, var: str, dbhost: str = DEFAULT_DBHOST,
        fresh_s: int = 0, timeout: float = 4.0) -> List[Dict[str, Any]]:
    """Read all instances of one variable across scopes (e.g. every node's caps)."""
    return [r for r in get_all(service, dbhost, fresh_s, timeout) if r["var"] == var]


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DBHOST
    sc = host_scope()
    put("communicator", "av_caps", sc,
        {"no_cam": False, "no_mic": False, "no_video": False, "no_audio": False}, dbhost=db)
    print("wrote av_caps for", sc)
    for r in get("communicator", "av_caps", dbhost=db):
        print("  read:", r["name"], "from", r["addr"], "->", r["value"])
