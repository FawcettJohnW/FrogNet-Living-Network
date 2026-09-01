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
frognet_monitor/identity.py — Detect local node identity.

LOCAL_DOMAIN: node name (e.g. "HardBox") from dnsmasq config or fallback.
LOCAL_IP: the served FrogNet address (10.x.y.1) of this node, excluding
          reserved 10.253.x.x (tunnel transit) and 10.254.x.x (chorus).
"""

import socket
import subprocess


def _get_local_domain() -> str:
    """Get this node's FrogNet domain name."""
    try:
        with open("/etc/dnsmasq.d/opts_only.conf") as f:
            for line in f:
                if line.startswith("domain="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    try:
        with open("/etc/frognet_domain") as f:
            return f.read().strip()
    except OSError:
        pass
    return socket.gethostname().split(".")[0]


def parse_local_ip_from_ip_output(ip_addr_output: str) -> str:
    """Parse `ip -4 -o addr show` output and return the served-FrogNet
    address (10.x.y.1), excluding 10.253.0.0/16 (WG transit) and
    10.254.0.0/16 (chorus virtual) reserved ranges.  Returns
    "127.0.0.1" if no served address is found.

    Pure function: input as argument, no subprocess, no I/O.  Used by
    both _get_local_ip() (production) and frognet_sim.py (tests) so
    the parser is exercised the same way in both.
    """
    for line in ip_addr_output.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3].split("/")[0]
            if (addr.startswith("10.")
                    and not addr.startswith("10.253.")
                    and not addr.startswith("10.254.")):
                return addr
    return "127.0.0.1"


def _get_local_ip() -> str:
    """Get this node's served FrogNet address (10.x.y.1).

    Shells out to `ip -4 -o addr show` and delegates parsing to
    parse_local_ip_from_ip_output().
    """
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            timeout=2,
            stderr=subprocess.PIPE,
        ).decode()
        return parse_local_ip_from_ip_output(out)
    except Exception as e:
        from .trace import trace
        trace(f"[IDENTITY] ip addr failed: {e}")
    return "127.0.0.1"


LOCAL_DOMAIN = _get_local_domain()
LOCAL_IP = _get_local_ip()
LOCAL_LOWER = LOCAL_DOMAIN.lower()


# [DBHOST_IS_MARKED_V1] Which peer is currently the elected databasehost.
#
# The dashboard coloured a peer by its HEALTH -- green up, red down -- and by
# whether it was this box (cyan). Nothing on the screen said which peer the
# whole pond is reading and writing through, so the single most consequential
# fact about the mesh at any moment was the one thing the monitor did not show.
# On a pond where the role floats, "which one is it right now" is the question
# you ask first.
#
# Resolved from /etc/hosts ONLY, never the resolver: sensors.py's own header
# says databasehost.frognet can float and must never be hardwired, and a
# resolver answer can differ from the file every other component routes by --
# measured on a node with `nameserver 127.0.0.1`, the file said 10.250.250.1 and
# gethostbyname said 10.130.130.1. A monitor that highlights a different box
# from the one the traffic goes to is worse than no highlight.
#
# Re-read on a TTL because the role floats; cached because this runs inside the
# draw loop.
_DBHOST_NAME = "databasehost.frognet"
# [CONTROL_IS_MARKED_V1] The deterministic highest-.1. Every election reads it;
# unlike databasehost.frognet it does not float. Frequently a DIFFERENT box from
# the elected data host, which is exactly why it needs its own marker.
_CONTROL_NAME = "databasehost_control.frognet"
_DBHOST_TTL_S = 5.0
_host_ip = {}          # name -> (ip_or_None, monotonic_at)


def _hosts_lookup(name, hosts_path="/etc/hosts"):
    """The IP /etc/hosts currently maps `name` to, or None. TTL-cached.

    /etc/hosts ONLY, never the resolver: the two disagree in practice, and a
    monitor that highlights a different box from the one the traffic goes to is
    worse than no highlight. Measured 2026-08-08: on Seattle6 the file said
    databasehost.frognet = 10.250.250.1 while another node's file said
    10.160.160.1 -- the same name, two answers, one hop apart.

    None means the file did not name it, and that is a real state worth seeing
    as "nothing marked" rather than smoothed away.
    """
    import time as _t
    now = _t.monotonic()
    want = name.strip().lower()
    hit = _host_ip.get(want)
    if hit is not None and (now - hit[1]) < _DBHOST_TTL_S:
        return hit[0]
    found = None
    try:
        with open(hosts_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                # RFC 4343: names are case-insensitive and /etc/hosts is written
                # as mixed case elsewhere in this tree.
                if any(n.strip().lower() == want for n in parts[1:]):
                    found = parts[0].strip()
                    break
    except OSError as e:
        # [NO_FALLBACK_V1] An unreadable /etc/hosts is NOT "the name is absent".
        # Returning None here paints the dashboard exactly as it paints a pond
        # with no databasehost elected -- two very different states, one of
        # which is a monitor problem and the other a mesh problem. Do not cache
        # a guess; say it and let the caller see nothing marked THIS pass.
        print("[monitor] cannot read %s: %r -- role markers unavailable this "
              "pass" % (hosts_path, e), flush=True)
        raise
    _host_ip[want] = (found, now)
    return found


def databasehost_ip(hosts_path="/etc/hosts"):
    """The ELECTED data host. Floats."""
    return _hosts_lookup(_DBHOST_NAME, hosts_path)


def databasehost_control_ip(hosts_path="/etc/hosts"):
    """[CONTROL_IS_MARKED_V1] The control host. Deterministic, does not float."""
    return _hosts_lookup(_CONTROL_NAME, hosts_path)
