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
##############################################################
# /usr/local/bin/frognet_diag.sh
#
# One-shot triage: dumps current FrogNet runtime state in a
# single output so debugging across multiple sources doesn't
# require running 8 different commands and correlating
# timestamps.  Read-only — does not modify any state.
#
# Usage: frognet_diag.sh [output_file]
#   No args: dumps to stdout.
#   With path: writes to file, prints summary line.
##############################################################

set -u
OUT="${1:-/dev/stdout}"

# When writing to a file, accumulate and write atomically.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

{
    echo "=== FrogNet Diagnostic Snapshot ==="
    echo "host: $(hostname -s 2>/dev/null || echo unknown)"
    echo "domain: $(/usr/local/bin/getOurDomain 2>/dev/null || echo unknown)"
    echo "eth0_ip: $(/usr/local/bin/getEth0Address 2>/dev/null || echo unknown)"
    echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "uptime: $(uptime 2>/dev/null)"
    echo

    echo "=== WireGuard interfaces ==="
    if command -v wg >/dev/null 2>&1; then
        wg show all dump 2>&1 || echo "(wg show failed)"
    else
        echo "(wg not installed)"
    fi
    echo

    echo "=== ip -4 route ==="
    /usr/sbin/ip -4 route show 2>&1 | sort
    echo

    echo "=== ip -4 neigh ==="
    /usr/sbin/ip -4 neigh show 2>&1 | sort
    echo

    echo "=== Daemon active tunnels ==="
    for jf in /var/lib/frognet-tunnel/active/*.json; do
        [[ -f "$jf" ]] || continue
        echo "--- $jf ---"
        cat "$jf" 2>/dev/null
        echo
    done

    echo "=== Recent observations (last 50) ==="
    if [[ -f /etc/sentinels/discovery_observations.tsv ]]; then
        tail -n 50 /etc/sentinels/discovery_observations.tsv
    else
        echo "(no observations file)"
    fi
    echo

    echo "=== Last MERGE-SUMMARY (last 5) ==="
    if [[ -d /var/log ]]; then
        grep -h "MERGE-SUMMARY" /var/log/syslog /var/log/messages 2>/dev/null \
            | tail -n 5 \
            || echo "(no MERGE-SUMMARY lines found in syslog/messages)"
    fi
    echo

    echo "=== Last 30 RPC-TRACE lines (proxy daemon) ==="
    if command -v journalctl >/dev/null 2>&1; then
        journalctl -u frognet-semantic-proxy --no-pager -n 1000 2>/dev/null \
            | grep "RPC-TRACE" | tail -n 30 \
            || echo "(no RPC-TRACE lines found)"
    else
        echo "(journalctl not available)"
    fi
    echo

    echo "=== Tunnel daemon status ==="
    if command -v systemctl >/dev/null 2>&1; then
        systemctl status frognet-tunnel-daemon-v3 --no-pager 2>&1 | head -n 20
    fi
    echo

    echo "=== Proxy daemon status ==="
    if command -v systemctl >/dev/null 2>&1; then
        systemctl status frognet-semantic-proxy --no-pager 2>&1 | head -n 20
    fi
    echo

    echo "=== /etc/hosts (10.x lines) ==="
    grep '^10\.' /etc/hosts 2>/dev/null | sort
    echo

    echo "=== Non-FrogNet lease cache ==="
    if [[ -f /etc/sentinels/non_frognet_leases ]]; then
        cat /etc/sentinels/non_frognet_leases
    else
        echo "(none)"
    fi
    echo

    echo "=== End of diagnostic ==="
} > "$TMP" 2>&1

if [[ "$OUT" == "/dev/stdout" ]]; then
    cat "$TMP"
else
    cp "$TMP" "$OUT"
    echo "Diagnostic written to $OUT ($(wc -l < "$OUT") lines)"
fi
