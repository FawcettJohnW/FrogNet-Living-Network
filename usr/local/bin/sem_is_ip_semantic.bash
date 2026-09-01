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
##########################################################################
#  sem_is_ip_semantic.bash
#
#  Determines whether an IP address should be treated as SEMANTIC.
#
#  Returns:
#       0  → yes, IP is semantic
#       1  → no
#
#  Usage:
#       sem_is_ip_semantic.bash 10.101.50.7 && echo "semantic"
#
##########################################################################

IP="$1"
SEM_HOST_FILE="/var/run/frognet/semantic_hosts"
ALIVE="/usr/local/bin/frognet_alive.bash"

if [[ -z "$IP" ]]; then
    echo "Usage: $0 <ip>" >&2
    exit 1
fi

############################################
# 1. If router/RTT logic already marked it slow → semantic
############################################
if [[ -f /var/run/frognet/slow_links ]]; then
    if grep -q "^${IP}$" /etc/sentinels/frognet_semantic_slow_hosts 2>/dev/null; then
        logger -t frognet "semantic check: $IP is semantic because it is slow"
        exit 0
    fi
fi

############################################
# 2. If in semantic_hosts list → semantic
############################################
if [[ -f "$SEM_HOST_FILE" ]]; then
    if grep -q "^${IP}$" "$SEM_HOST_FILE" 2>/dev/null; then
        logger -t frognet "semantic check: $IP explicitly listed as semantic"
        exit 0
    fi
fi

############################################
# 3. Check if semantic daemon is reachable via FNW1
############################################
# frognet_alive in daemon-only mode (~61 bytes round-trip)
if FROGNET_ALIVE_NO_HTTP=1 "$ALIVE" "$IP" 2 >/dev/null 2>&1; then
    logger -t frognet "semantic check: $IP has a reachable semantic daemon (FNW1)"
    exit 0
fi

############################################
# 4. Fallback: detect whether raw HTTP frognet_echo fails
#    but semantic echo works → semantic
############################################

# Quick raw test (interface-neutral)
RAW_OUT=$(curl -m 1 -s "http://${IP}/frognet_echo.php")

if [[ -z "$RAW_OUT" || "$RAW_OUT" == "No semantic template for opcode"* ]]; then
    # Try semantic fallback:
    SEM_ECHO=$(curl -m 1 -s "http://127.0.0.1:9000/semantic_echo?target=${IP}")

    if [[ -n "$SEM_ECHO" ]]; then
        logger -t frognet "semantic check: raw echo failed but semantic succeeded → $IP is semantic"
        exit 0
    fi
fi

############################################
# If none of the indicators matched → NOT semantic
############################################
exit 1
