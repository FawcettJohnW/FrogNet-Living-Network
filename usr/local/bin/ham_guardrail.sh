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
# Guardrail rules for ham0
# Keeps ICMP + semantic traffic, blocks accidental bulk HTTP


IPT="/usr/sbin/iptables"
DEV="ham0"

# Clear any previous ham0-specific rules
$IPT -D OUTPUT -o "$DEV" -j HAM_GUARD 2>/dev/null || true
$IPT -F HAM_GUARD 2>/dev/null || true
$IPT -X HAM_GUARD 2>/dev/null || true

# Create chain
$IPT -N HAM_GUARD

# Allow ICMP (required)
$IPT -A HAM_GUARD -p icmp -j ACCEPT

# Allow semantic daemon
$IPT -A HAM_GUARD -p tcp --dport 9009 -j ACCEPT
$IPT -A HAM_GUARD -p tcp --sport 9009 -j ACCEPT

# Allow radio semantic UDP
$IPT -A HAM_GUARD -p udp --dport 17777 -j ACCEPT
$IPT -A HAM_GUARD -p udp --sport 17777 -j ACCEPT

# Log + drop everything else
$IPT -A HAM_GUARD -m limit --limit 3/min -j LOG --log-prefix "[HAM BLOCK] "
$IPT -A HAM_GUARD -j DROP

# Attach to OUTPUT on ham0
$IPT -A OUTPUT -o "$DEV" -j HAM_GUARD

echo "[HAM] guardrail active on $DEV"
