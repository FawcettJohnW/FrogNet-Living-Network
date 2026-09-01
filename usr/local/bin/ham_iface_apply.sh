#!/bin/bash
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
# /usr/local/bin/ham_iface_apply.sh
#
# Idempotent interface conditioning for ham simulation.
# Touches ONLY the specified dev.
#

DEV="ham0"
MTU="576"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV="${2:?}"; shift 2 ;;
    --mtu) MTU="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

IP="/usr/sbin/ip"
ETHTOOL="/usr/sbin/ethtool"

$IP link show "$DEV" >/dev/null 2>&1 || { echo "[ham_iface] missing dev $DEV" >&2; exit 1; }

echo "[ham_iface] dev=$DEV mtu=$MTU"

# MTU (frame size proxy)
$IP link set dev "$DEV" mtu "$MTU" 2>/dev/null || true

# TX queue: modestly higher absorbs microbursts without huge bufferbloat (tc still bounds)
$IP link set dev "$DEV" txqueuelen 2000 2>/dev/null || true

# Disable offloads so tc sees real packetization
if command -v "$ETHTOOL" >/dev/null 2>&1; then
  $ETHTOOL -K "$DEV" gro off gso off tso off 2>/dev/null || true
fi

exit 0
