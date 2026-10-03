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

IFACE="${1:?iface required (e.g., wlan1)}"
RATE="${2:-5mbit}"        # throughput cap
DELAY_MS="${3:-80}"       # base RTT one-way delay; netem uses delay each direction at egress
JITTER_MS="${4:-10}"
LOSS_PCT="${5:-0.1}"

echo "[tc] applying on $IFACE rate=$RATE delay=${DELAY_MS}ms jitter=${JITTER_MS}ms loss=${LOSS_PCT}%"

# clean
tc qdisc del dev "$IFACE" root 2>/dev/null || true

# root HTB for rate limit
tc qdisc add dev "$IFACE" root handle 1: htb default 10
tc class add dev "$IFACE" parent 1: classid 1:10 htb rate "$RATE" ceil "$RATE"

# netem under the class to add delay/jitter/loss
tc qdisc add dev "$IFACE" parent 1:10 handle 10: netem delay "${DELAY_MS}ms" "${JITTER_MS}ms" loss "${LOSS_PCT}%"

tc qdisc show dev "$IFACE"
