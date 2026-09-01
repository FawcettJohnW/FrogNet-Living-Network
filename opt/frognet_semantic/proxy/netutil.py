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
proxy/netutil.py

Kernel routing helpers + strict hosts-first resolution + local IP detection
+ interface-role loading (mapInterfaces) + DEFAULT_UPSTREAM_IFACES.

This file MUST export:
  - DEFAULT_UPSTREAM_IFACES
  - target_host_and_ip
  - route_get
  - is_local_ip
"""

from __future__ import annotations

import os
import re
import time
import socket
import subprocess
import threading
from typing import Dict, Optional, Set, Tuple

from proxy.constants import debug


# -------------------------------------------------------------------
# FAILURE TYPES
# -------------------------------------------------------------------
# [NO_FALLBACK_V1] Every function in this file answers a question about the
# machine: which interfaces exist, what /etc/hosts says, which addresses are
# ours, where a packet would go. There is no such thing as a default answer to
# any of those. Previously each one swallowed its failure and returned a guess
# ("eth0"/"wlan0"), an empty map, {"127.0.0.1"}, or ("", "", "") - and every
# caller downstream treated the guess as measurement. These types exist so a
# failed read is reported as a failed read.
#
# Nothing in this module catches them. The proxy converts them to an explicit
# 503 at the request boundary (proxy_main), which names the failing read.

class NetutilFailure(RuntimeError):
    """An environment read this module depends on could not be performed."""


class InterfaceRolesUnavailable(NetutilFailure):
    pass


class HostsMapUnreadable(NetutilFailure):
    pass


class LocalAddressesUnavailable(NetutilFailure):
    pass


class RouteQueryFailed(NetutilFailure):
    pass


_HOSTS_PATH = "/etc/hosts"
# [HOSTS_ONLY_V1] ALLOW_DNS_FALLBACK is gone. It was True, and a True DNS fallback is
# a different routing table from the one /etc/hosts describes. There is no switch
# because there is no case where DNS is the right answer here.


# -------------------------------------------------------------------
# INTERFACE ROLE LOADING (mapInterfaces)
# -------------------------------------------------------------------

MAP_INTERFACES_PATH = "/usr/local/bin/mapInterfaces"
_ROLE_KEYS = ("eth0Name", "wlan0Name", "wlan1Name")


def _load_interface_roles() -> Dict[str, str]:
    """[NO_FALLBACK_V1] mapInterfaces is the only authority for which physical
    device carries which role. It is not a hint with defaults behind it.

    This previously seeded roles with {"eth0Name": "eth0", "wlan0Name": "wlan0",
    "wlan1Name": "wlan1"}, swallowed any read error, and then re-defaulted a
    second time through IF_ROLES.get(key, "eth0"). A node whose mapInterfaces
    was missing, unreadable, or silently truncated ran the proxy against
    invented device names and built DEFAULT_UPSTREAM_IFACES out of them. On a
    box where wlan0 is a dead built-in radio and the real uplink is
    wlx90de80b193db, that is not a degraded mode - it is a proxy making upstream
    decisions about interfaces that do not carry traffic, with nothing in the
    log to say so.

    Raises at import. A node that cannot name its own interfaces has no business
    serving; systemd reports it and the failure is visible in one place.
    """
    try:
        with open(MAP_INTERFACES_PATH, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError as e:
        raise InterfaceRolesUnavailable(
            f"{MAP_INTERFACES_PATH}: {type(e).__name__} errno={e.errno} "
            f"({e.strerror}) - interface roles unknown and will not be guessed"
        ) from e

    roles: Dict[str, str] = {}
    missing = []
    for key in _ROLE_KEYS:
        m = re.search(rf'export\s+{key}\s*=\s*"?([^"\n]+)"?', txt)
        val = m.group(1).strip() if m else ""
        if not val:
            missing.append(key)
        else:
            roles[key] = val
    if missing:
        raise InterfaceRolesUnavailable(
            f"{MAP_INTERFACES_PATH}: no value for {', '.join(missing)} "
            f"(found {sorted(roles.items())}, file is {len(txt)}B) - "
            f"interface roles incomplete and will not be guessed")
    return roles


IF_ROLES = _load_interface_roles()
ETH0_NAME = IF_ROLES["eth0Name"]
WLAN0_NAME = IF_ROLES["wlan0Name"]
WLAN1_NAME = IF_ROLES["wlan1Name"]

# Default interfaces eligible for "upstream" decisions
DEFAULT_UPSTREAM_IFACES: Set[str] = {WLAN0_NAME, WLAN1_NAME}


# -------------------------------------------------------------------
# HOST RESOLUTION (hosts-first)
# -------------------------------------------------------------------

def _is_ipv4_literal(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        # Not a fallback: a non-numeric octet IS the answer "not a v4 literal".
        # Narrowed from `except Exception` so a genuine bug in this predicate
        # surfaces instead of being reported as "that string is a hostname".
        return False

def _read_hosts_map() -> Dict[str, str]:
    """[NO_FALLBACK_V1] Raise if /etc/hosts cannot be read or carries no
    entries.

    This used to `except Exception: pass` and return whatever it had, which for
    a read failure is {}. resolve_frognet_host() then returned None, and the
    proxy answered "Missing/invalid Host header" - a message about the request
    for a fault in the file. That misdirection is documented in the transition
    primer as having cost real time. An empty map is treated the same way: a
    node with no parseable hosts entries cannot resolve anything, so every
    request would 400 with the same wrong reason.
    """
    m: Dict[str, str] = {}
    try:
        with open(_HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip = parts[0].strip()
                for name in parts[1:]:
                    # Both sides lowered - RFC 4343. The lookup in
                    # resolve_frognet_host() lowers too; lowering only one side
                    # breaks the other case.
                    m[name.strip().lower()] = ip
    except OSError as e:
        raise HostsMapUnreadable(
            f"{_HOSTS_PATH}: {type(e).__name__} errno={e.errno} ({e.strerror}) "
            f"- no name can be resolved") from e
    if not m:
        raise HostsMapUnreadable(
            f"{_HOSTS_PATH}: read OK but contains no host entries "
            f"- no name can be resolved")
    return m

def resolve_frognet_host(host: str) -> Optional[str]:
    if not host:
        return None
    host = host.strip()
    if not host:
        return None

    if _is_ipv4_literal(host):
        return host

    hosts_map = _read_hosts_map()

    # [HOSTS_ONLY_V1] The file, or nothing. What used to be a special case for
    # databasehost.frognet is the rule for every name, and there is no DNS fallback
    # behind it: a resolver answer can differ from the file the kernel and every other
    # component route by, and then the proxy forwards to a machine nobody named.
    # Measured on a node with `nameserver 127.0.0.1`: file said 10.250.250.1,
    # gethostbyname said 10.130.130.1. A name not in the file is unresolved, and
    # unresolved is the honest answer.
    return hosts_map.get(host.lower())

def target_host_and_ip(host_header: str) -> Tuple[Optional[str], Optional[str]]:
    if not host_header:
        return (None, None)
    host_only = host_header.split(":", 1)[0].strip()
    if not host_only:
        return (None, None)
    return host_only, resolve_frognet_host(host_only)


# -------------------------------------------------------------------
# LOCAL IP DETECTION
# -------------------------------------------------------------------

_LOCAL_IPS_CACHE = {"ts": 0.0, "ips": set()}
_LOCAL_IPS_TTL_SEC = 2.0

def _local_ipv4_set() -> Set[str]:
    """[NO_FALLBACK_V1] Raise if the address list cannot be enumerated.

    The old body swallowed the failure and returned {"127.0.0.1"}, so
    is_local_ip() answered False for every real address this node owns. Two
    consumers act on that answer: decision.py routes a local target out to the
    network instead of to Apache, and the reflect handler stops recognising its
    own probes coming back - i.e. loop detection goes blind at exactly the
    moment the machine is sick enough that `ip addr` failed.
    """
    ips: Set[str] = {"127.0.0.1"}
    cmd = ["ip", "-4", "-o", "addr", "show"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
    except (OSError, subprocess.SubprocessError) as e:
        raise LocalAddressesUnavailable(
            f"{' '.join(cmd)}: {type(e).__name__}: {e} - "
            f"this node's own addresses are unknown") from e
    if r.returncode != 0:
        raise LocalAddressesUnavailable(
            f"{' '.join(cmd)}: rc={r.returncode} stderr={r.stderr.strip()[:200]!r} "
            f"- this node's own addresses are unknown")
    for line in r.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            i = parts.index("inet")
            addr = parts[i + 1]
            ip = addr.split("/", 1)[0].strip()
            if ip:
                ips.add(ip)
    if len(ips) == 1:
        raise LocalAddressesUnavailable(
            f"{' '.join(cmd)}: rc=0 but no inet address on any interface "
            f"- this node has no IPv4 identity")
    return ips

def is_local_ip(ip: Optional[str]) -> bool:
    if not ip:
        return False
    ip = ip.strip()
    if ip.startswith("127."):
        return True
    now = time.time()
    if (now - _LOCAL_IPS_CACHE["ts"]) > _LOCAL_IPS_TTL_SEC:
        _LOCAL_IPS_CACHE["ips"] = _local_ipv4_set()
        _LOCAL_IPS_CACHE["ts"] = now
    return ip in _LOCAL_IPS_CACHE["ips"]


# -------------------------------------------------------------------
# ROUTING HELPERS
# -------------------------------------------------------------------

def ip_to_24(ip: str) -> str:
    parts = (ip or "").strip().split(".")
    if len(parts) != 4:
        return ""
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"

_ROUTE_GET_CACHE: Dict[str, Tuple[float, Tuple[str, str, str]]] = {}
_ROUTE_GET_TTL_SEC = float(os.environ.get("FROGNET_ROUTE_GET_TTL", "10.0"))
_route_get_lock = threading.Lock()
_route_get_inflight: Dict[str, threading.Event] = {}


def _route_get_raw(dst_ip: str) -> Tuple[str, str, str]:
    """Run `ip route get`.

    Two outcomes, kept distinct:
      - rc != 0 ("Network is unreachable", "No route to host") is an ANSWER.
        Returned as ("", "", <stderr>) so the caller sees no device AND the
        reason, instead of a blank third element it cannot interpret.
      - the command not running at all is a FAILURE and raises.
    stderr is no longer sent to DEVNULL: discarding the one line that says which
    of those two happened is what made them indistinguishable.
    """
    cmd = ["ip", "route", "get", dst_ip]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
    except (OSError, subprocess.SubprocessError) as e:
        raise RouteQueryFailed(
            f"{' '.join(cmd)}: {type(e).__name__}: {e} - "
            f"kernel route for {dst_ip} unknown") from e
    if r.returncode != 0:
        return ("", "", r.stderr.strip())
    out = r.stdout.strip()
    parts = out.split()
    dev = ""
    via = ""
    if "dev" in parts:
        i = parts.index("dev")
        if i + 1 < len(parts):
            dev = parts[i + 1].strip()
    if "via" in parts:
        i = parts.index("via")
        if i + 1 < len(parts):
            via = parts[i + 1].strip()
    return dev, via, out


def route_get(dst_ip: str) -> Tuple[str, str, str]:
    """[ROUTE_GET_CACHE_V1] `ip route get` is a fork+exec. This is called on the
    per-request path decision (ONLINK_DIRECT), so calling it raw fork-storms under a
    concurrent burst - 40% of burst CPU was in subprocess spawns, collapsing
    throughput. Cache per target with a short TTL and single-flight: N concurrent
    callers for the same target share ONE subprocess; steady-state is a dict hit."""
    now = time.time()
    with _route_get_lock:
        ent = _ROUTE_GET_CACHE.get(dst_ip)
        if ent is not None and (now - ent[0]) <= _ROUTE_GET_TTL_SEC:
            return ent[1]
        ev = _route_get_inflight.get(dst_ip)
        leader = ev is None
        if leader:
            ev = threading.Event()
            _route_get_inflight[dst_ip] = ev
    if not leader:
        # A concurrent caller is already running the subprocess - wait for it.
        ev.wait(timeout=_ROUTE_GET_TTL_SEC)
        with _route_get_lock:
            ent = _ROUTE_GET_CACHE.get(dst_ip)
        if ent is not None:
            return ent[1]
        # The leader raised. Raise the same way rather than silently running a
        # second subprocess to reach the same failure.
        raise RouteQueryFailed(
            f"ip route get {dst_ip}: leader query failed or did not complete "
            f"within {_ROUTE_GET_TTL_SEC:.0f}s")

    # [NO_FALLBACK_V1] This was `except Exception: result = ("", "", "")`. An
    # empty tuple is indistinguishable from a real answer of "no device", and
    # decision.py reads exactly that field to decide ONLINK_DIRECT. A failed
    # `ip route get` was therefore silently routed as "not on-link".
    #
    # The try/finally is not error handling: it releases the followers. Without
    # it a raising leader left its Event unset and its _route_get_inflight entry
    # in place, so every subsequent caller for this destination blocked the full
    # TTL and then took the (now removed) fabricate-a-route path.
    try:
        result = _route_get_raw(dst_ip)
    finally:
        with _route_get_lock:
            _route_get_inflight.pop(dst_ip, None)
        ev.set()
    with _route_get_lock:
        _ROUTE_GET_CACHE[dst_ip] = (time.time(), result)
    return result

def routes_for_dest(dst_ip: str) -> list:
    """
    Return all kernel routes to dst_ip as a list of dicts with keys:
      via, dev, metric (all strings, may be empty)

    Uses 'ip route show <dst_ip>' which returns one line per route when
    there are ECMP or policy routes.

    [NO_FALLBACK_V1] Returns [] only when the kernel genuinely has no route.
    A query that could not be run raises: "[] because there is no route" and
    "[] because we could not ask" are opposite facts and must not share a
    return value.
    """
    results = []
    cmd = ["ip", "route", "show", dst_ip]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
    except (OSError, subprocess.SubprocessError) as e:
        raise RouteQueryFailed(
            f"{' '.join(cmd)}: {type(e).__name__}: {e}") from e
    if r.returncode != 0:
        raise RouteQueryFailed(
            f"{' '.join(cmd)}: rc={r.returncode} "
            f"stderr={r.stderr.strip()[:200]!r}")
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        entry = {"via": "", "dev": "", "metric": ""}
        if "via" in parts:
            i = parts.index("via")
            if i + 1 < len(parts):
                entry["via"] = parts[i + 1]
        if "dev" in parts:
            i = parts.index("dev")
            if i + 1 < len(parts):
                entry["dev"] = parts[i + 1]
        if "metric" in parts:
            i = parts.index("metric")
            if i + 1 < len(parts):
                entry["metric"] = parts[i + 1]
        if entry["dev"]:
            results.append(entry)
    return results


def tcp_connect_rtt_ms(ip: str, port: int, timeout_sec: float = 1.0) -> Optional[float]:
    """Measured connect RTT, or None when the connect itself did not succeed.

    None is the measurement, not a fallback: refused/timed-out/unreachable is
    what this probe exists to detect. The catch is narrowed to OSError so a
    programming error here (bad argument types, an exhausted fd table) is not
    reported to the caller as "that peer is unreachable" - the two look
    identical downstream and only one is about the peer.
    """
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout_sec)
        s.connect((ip, port))
        return (time.time() - t0) * 1000.0
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError as e:
            # Not swallowed: a probe socket that will not close is an fd leak,
            # and an fd leak is the thing that makes every later probe fail.
            print(f"[NETUTIL] probe socket close failed for {ip}:{port}: {e!r}",
                  flush=True)
