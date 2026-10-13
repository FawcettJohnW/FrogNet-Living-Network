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

LOCAL_DOMAIN: the domain this node's sensor names start with (sensors.py).
parse_echo_line / dot_one_of: the client identity parsers the shared vectors check.
The name, .1, self row and the databasehost marker come from the node snapshot (discovery.fetch_snapshot).
"""

import socket


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


LOCAL_DOMAIN = _get_local_domain()      # the prefix of this node's sensor names (sensors.py); the node's name, .1,
                                        # self row and elections are the snapshot's [ONE_MONITOR_TRUTH_V1]


# ---------------------------------------------------------------------------
# The client identity path, pure: the same rules as the C++ (parsers.cpp parseEchoLine, dotOneOf) and the C#
# (Parsers.cs), checked by the shared vectors (../frognet_monitor_shared/parser_vectors.json).
# ---------------------------------------------------------------------------
def parse_echo_line(body):
    """One frognet_echo.php line -> (name, eth0, wlan0, wlan1), or None. [IDENTITY_FAILS_LOUD_V1] Exactly four
    comma fields (empties kept, never collapsed) with a non-empty name, or it failed; the first line only."""
    line = (body or "").strip()
    if "\n" in line:
        line = line.split("\n", 1)[0].strip()
    if not line:
        return None
    f = [x.strip() for x in line.split(",")]
    if len(f) != 4 or not f[0]:
        return None
    return (f[0], f[1], f[2], f[3])


def dot_one_of(ip):
    """An address -> the .1 of its /24, or "" for anything outside 10/8, inside 10.253/16 or 10.254/16, or malformed."""
    s = (ip or "").strip()
    if not s.startswith("10.") or s.startswith("10.253.") or s.startswith("10.254."):
        return ""
    o = s.split(".")
    if len(o) != 4:
        return ""
    for part in o:
        if not part or len(part) > 3 or not part.isdigit() or int(part) > 255:
            return ""
    return "%s.%s.%s.1" % (o[0], o[1], o[2])
