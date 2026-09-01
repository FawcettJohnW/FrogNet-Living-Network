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
Channel name parsing, deterministic tunnel naming, and joiner/host directionality.

Identity for a FrogNet is the served /24, represented here as the first
three octets - '10.x.y' - matching the broker's channel-name format
`{node_name}-{subnet.replace('.0/24','')}`.  The (x, y) pair is the
identity; neither octet alone is meaningful.

Pure-parser functions in this module take their input as arguments and
produce their output as return values - no subprocess, no file I/O, no
module-level side effects.  This makes them trivially importable by
the sim and other tests.
"""

import re
from typing import Dict, Tuple


# Channel name format: '{node_name}-{a.b.c}', where '{a.b.c}' is the first
# three octets of the served /24.  Node names may themselves contain '-'
# (e.g. 'New-York-1'), so we anchor on the trailing dotted-octet group.
_CH_NAME_RE = re.compile(r'^(.+)-(\d+\.\d+\.\d+)$')


def _prefix_to_tuple(prefix: str) -> Tuple[int, int, int]:
    """Convert '10.x.y' to (10, x, y) for numeric comparison.

    Returns (0, 0, 0) on parse failure - a value that sorts below any
    valid prefix, which keeps malformed peers from winning a tie.
    """
    try:
        parts = prefix.split(".")
        if len(parts) != 3:
            return (0, 0, 0)
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return (0, 0, 0)


def parse_channel_name(ch_name: str) -> Tuple[str, str]:
    """Parse '{node}-{a.b.c}' -> (node_name, subnet_prefix).

    Examples:
        'IronBox-10.20.30'        -> ('IronBox', '10.20.30')
        'New-York-1-10.241.241'   -> ('New-York-1', '10.241.241')

    Returns ('', '') on parse failure.
    """
    m = _CH_NAME_RE.match(ch_name)
    if not m:
        return ("", "")
    return (m.group(1), m.group(2))


def parse_local_subnet(ip_addr_show_output: str) -> Dict[str, str]:
    """Parse `ip -4 addr show` output and return served-FrogNet subnet info.

    Returns a dict with:
        SUBNET_PREFIX  - e.g. '10.101.10'
        LOCAL_SUBNET   - e.g. '10.101.10.0/24'
        LOCAL_GW       - e.g. '10.101.10.1'

    Excludes the reserved 10.253.0.0/16 (WG transit) and 10.254.0.0/16
    (chorus virtual) ranges.  Returns {} if no served address is found.

    Pure function: takes the text output as input, no subprocess, no
    file I/O.  Both internet_tunnels_v3/config.py (production) and
    frognet_sim.py (tests) call this with the same parser.
    """
    for line in ip_addr_show_output.splitlines():
        line = line.strip()
        if ("inet 10." in line
                and "inet 10.253." not in line
                and "inet 10.254." not in line):
            parts = line.split()
            idx = next((i for i, p in enumerate(parts) if p == "inet"), -1)
            if idx >= 0 and idx + 1 < len(parts):
                addr_cidr = parts[idx + 1]
                ip = addr_cidr.split("/")[0]
                prefix = ip.rsplit(".", 1)[0]
                return {
                    "SUBNET_PREFIX": prefix,
                    "LOCAL_SUBNET":  f"{prefix}.0/24",
                    "LOCAL_GW":      f"{prefix}.1",
                }
    return {}



def deterministic_tunnel_name(our_name: str, our_prefix: str,
                               remote_name: str, remote_prefix: str) -> str:
    """Pick a tunnel name that both endpoints will independently agree on.

    Order by (numeric subnet tuple, name) so endpoints with colliding
    subnets still get a deterministic order via the name tiebreaker.
    """
    our_key    = (_prefix_to_tuple(our_prefix), our_name)
    remote_key = (_prefix_to_tuple(remote_prefix), remote_name)
    if our_key < remote_key:
        return f"{our_name}-to-{remote_name}"
    return f"{remote_name}-to-{our_name}"


def we_are_joiner(remote_prefix: str) -> bool:
    """True iff this node is the joiner side of the tunnel to remote.

    Joiner = the side whose served-subnet tuple sorts numerically
    higher.  Symmetric: the other side is the host.  Both sides reach
    the same answer from this same comparator.
    """
    # Lazy import: config.py runs subprocess / reads files at module
    # load time, so we defer until first call to keep names.py cleanly
    # importable in test harnesses.
    from . import config
    return _prefix_to_tuple(config.SUBNET_PREFIX) > _prefix_to_tuple(remote_prefix)
