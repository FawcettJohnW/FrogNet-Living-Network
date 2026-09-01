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
convert.py - faithful ports of the old `convertToShortIP` and
`convertToSubnetRange` (old_stuff, 2026). Pure, no I/O.

  to_short_ip("10.160.160.221")  -> "10.160.160.1"   (the network's .1)
  to_short_ip("0.0.0.x")         -> ""                (old code blanks 0.0.0.1)
  to_subnet_range("10.160.160.5")-> "10.160.160.0/24"

These two functions are the whole address algebra the old route code rested on:
every "Base" is a .1 and every "Range" is the .0/24. Kept byte-identical in
behaviour to the bash so the ported route logic matches the originals.
"""
from __future__ import annotations


def to_short_ip(ip: str) -> str:
    """Network .1 for any dotted IPv4. Mirrors convertToShortIP:
    awk -F. '{print $1"."$2"."$3".1"}', with the 0.0.0.1 -> "" guard."""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) < 3:
        return ""
    base = f"{parts[0]}.{parts[1]}.{parts[2]}.1"
    return "" if base == "0.0.0.1" else base


def to_subnet_range(ip: str) -> str:
    """The .0/24 for any dotted IPv4. Mirrors convertToSubnetRange:
    cut -d. -f1-3 then append .0/24."""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) < 3:
        return ""
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"


def in_domain(ip: str) -> bool:
    """The old `grep '^10\\.'` test: is this a FrogNet (10/8) address."""
    return bool(ip) and ip.split(".")[0] == "10"


def is_unspecified(ip: str) -> bool:
    """Advertised-gateway 'none' sentinel used throughout the old echo CSVs."""
    return ip in ("", "0.0.0.0", None)


if __name__ == "__main__":
    # quick self-proof against the old bash semantics
    assert to_short_ip("10.160.160.221") == "10.160.160.1"
    assert to_short_ip("10.250.250.221") == "10.250.250.1"
    assert to_short_ip("0.0.0.0") == ""
    assert to_short_ip("") == ""
    assert to_subnet_range("10.130.130.47") == "10.130.130.0/24"
    assert to_subnet_range("10.160.160.1") == "10.160.160.0/24"
    assert in_domain("10.1.2.3") and not in_domain("192.168.0.1")
    assert is_unspecified("0.0.0.0") and is_unspecified("") and not is_unspecified("10.1.1.1")
    print("convert.py: all self-checks pass")
