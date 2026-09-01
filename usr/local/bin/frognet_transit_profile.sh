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
# /usr/local/bin/frognet_transit_profile.sh
# Change transit link baud rate on the fly

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

IP="/usr/sbin/ip"
TC="/sbin/tc"
ETHTOOL="/usr/sbin/ethtool"
SET_BAUD="/usr/local/bin/frognet_set_transit_baud.sh"
TRANSIT_CONF="/etc/frognet/transit.conf"

log(){ echo "[transit-profile] $*"; }
die(){ log "ERROR: $*"; exit 1; }

[[ -f /usr/local/bin/mapInterfaces ]] && . /usr/local/bin/mapInterfaces

show_status() {
    local dev="$1"
    echo "=== Interface: $dev ==="
    [[ ! -d "/sys/class/net/$dev" ]] && { echo "  ERROR: Not found"; return 1; }
    
    echo "Link State: $(cat /sys/class/net/$dev/operstate 2>/dev/null)"
    echo ""
    echo "IP Addresses:"
    $IP -4 addr show dev "$dev" 2>/dev/null | grep inet | while read -r line; do echo "  $line"; done
    
    local ip_transit
    ip_transit="$($IP -4 -o addr show dev "$dev" 2>/dev/null | awk '{print $4}' | grep -E '^10\.253\.253\..*/30$' | head -1)"
    echo ""
    [[ -n "$ip_transit" ]] && echo "Link Type: TRANSIT" || echo "Link Type: OTHER"
    
    echo "MTU: $(cat /sys/class/net/$dev/mtu 2>/dev/null)"
    echo "TX Queue: $(cat /sys/class/net/$dev/tx_queue_len 2>/dev/null)"
    echo ""
    echo "Traffic Control:"
    $TC qdisc show dev "$dev" 2>/dev/null | while read -r line; do echo "  $line"; done
    echo ""
    echo "Neighbors:"
    $IP neigh show dev "$dev" 2>/dev/null | while read -r line; do echo "  $line"; done
    echo ""
}

show_all_status() {
    echo "=== FrogNet Interface Status ==="
    for devpath in /sys/class/net/*; do
        local dev="$(basename "$devpath")"
        [[ "$dev" == "lo" ]] && continue
        case "$dev" in br*|virbr*|docker*|veth*) continue ;; esac
        show_status "$dev"
        echo "----------------------------------------"
    done
}

clear_shaping() {
    local dev="$1"
    $TC qdisc del dev "$dev" root 2>/dev/null || true
    $TC qdisc del dev "$dev" ingress 2>/dev/null || true
    $IP link set dev "$dev" mtu 1500 2>/dev/null || true
    $IP link set dev "$dev" txqueuelen 1000 2>/dev/null || true
    [[ -x "$ETHTOOL" ]] && $ETHTOOL -K "$dev" gro on gso on tso on 2>/dev/null || true
    log "Shaping cleared on $dev"
}

apply_profile() {
    local dev="$1" profile="$2"
    case "$profile" in
        full|FULL|0) clear_shaping "$dev" ;;
        1200|2400|4800|9600|19200)
            [[ -x "$SET_BAUD" ]] && "$SET_BAUD" "$dev" "$profile" || die "$SET_BAUD not found"
            ;;
        *) die "Unknown profile: $profile (valid: full, 1200, 2400, 4800, 9600, 19200)" ;;
    esac
}

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <interface> <profile>"
    echo "       $0 status"
    echo ""
    echo "Profiles: full, 1200, 2400, 4800, 9600, 19200"
    exit 1
fi

if [[ $# -eq 1 ]]; then
    [[ "$1" == "status" ]] && { show_all_status; exit 0; }
    die "Single argument must be 'status'"
fi

DEV="$1"
PROFILE="$2"

[[ ! -d "/sys/class/net/$DEV" ]] && die "Interface not found: $DEV"

case "$PROFILE" in
    status|STATUS) show_status "$DEV" ;;
    *)
        [[ "$(id -u)" -ne 0 ]] && die "Must be root (use sudo)"
        apply_profile "$DEV" "$PROFILE"
        show_status "$DEV"
        ;;
esac
exit 0
