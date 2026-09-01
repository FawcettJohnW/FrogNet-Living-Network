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
# /usr/local/bin/check_remote_is_sem.bash
# FNW1 health check probe via frognet_alive.bash
#
# Usage: check_remote_is_sem.bash <ip> [port]
# Exit: 0 = daemon alive, 1 = unreachable, 2 = usage error
#
# Stdout (on success): <rtt_ms>
#
# Caches results per merge cycle in /etc/sentinels/frognet_hosts_checked

IP="${1:-}"
PORT="${2:-9009}"

[[ -z "$IP" || -z "$PORT" ]] && exit 2

SENT_DIR="/etc/sentinels"
CHECKED_FILE="${SENT_DIR}/frognet_hosts_checked"
ALIVE="/usr/local/bin/frognet_alive.bash"

mkdir -p "$SENT_DIR"
touch "$CHECKED_FILE"

KEY="${IP}:${PORT}"

# Check cache first
if grep -Fq "$KEY " "$CHECKED_FILE" 2>/dev/null; then
    cached=$(grep -F "$KEY " "$CHECKED_FILE" | tail -1)
    if echo "$cached" | grep -Fq "$KEY ok"; then
        # Extract cached RTT if present
        echo "$cached" | sed "s/.*ok //" | sed 's/ .*//'
        exit 0
    fi
    exit 1
fi

# Probe via frognet_alive (daemon-only mode — no HTTP fallback)
# We only want to know if the semantic daemon is up.
result=$(FROGNET_ALIVE_NO_HTTP=1 "$ALIVE" "$IP" 5 2>/dev/null)
if [[ $? -eq 0 && -n "$result" ]]; then
    rtt_ms="${result%%|*}"
    echo "$KEY ok ${rtt_ms}" >> "$CHECKED_FILE"
    echo "$rtt_ms"
    exit 0
else
    echo "$KEY fail" >> "$CHECKED_FILE"
    exit 1
fi
