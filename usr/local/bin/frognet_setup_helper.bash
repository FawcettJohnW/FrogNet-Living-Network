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
# frognet_setup_helper.bash
# System operations for the FrogNet setup admin page.
# All subcommands emit a single JSON object on stdout.
#
# Subcommands:
#   state                              — node identity + every interface
#   wifi_devices                       — list wifi interface names
#   scan_wifi <iface>                  — list visible SSIDs on iface
#   other_ifaces                       — non-eth0 ethernet with carrier up
#   connect_wifi <iface> <ssid> [pass] — nmcli connect
#   disconnect <iface>                 — nmcli disconnect
#   auto_ip                            — random 10.X.Y.1, probed
#   apply_identity <name> <ip>         — re-run setup_lillypad if changed
#
# Requires: nmcli, jq, ip, ping. Must run as root (PHP invokes via sudo).
##############################################################


GATEWAYS_CONF="${FROGNET_GATEWAYS_CONF:-/etc/frognet/gateways.conf}"
SETUP_LILLYPAD="${FROGNET_SETUP_LILLYPAD:-/usr/local/bin/setup_lillypad.bash}"
ETH0="${FROGNET_ETH0:-eth0}"

fail() {
    jq -n --arg err "$1" '{ok:false, error:$err}'
    exit 0
}

read_conf() {
    NETWORK_NAME="FrogNet_Unnamed"
    GATEWAY_IP="10.1.1.1"
    if [[ -f "$GATEWAYS_CONF" ]]; then
        # shellcheck disable=SC1090
        source "$GATEWAYS_CONF" 2>/dev/null || true
        : "${NETWORK_NAME:=FrogNet_Unnamed}"
        : "${GATEWAY_IP:=10.1.1.1}"
    fi
}

iface_ip() {
    ip -4 -o addr show dev "$1" 2>/dev/null \
        | awk '{print $4}' | cut -d/ -f1 | head -1
}

cmd_state() {
    read_conf
    local host; host=$(hostname 2>/dev/null || echo "")
    local ifaces="[]"

    while IFS=: read -r dev type st conn; do
        [[ -z "$dev" || "$dev" == "lo" ]] && continue
        local ip4 ssid="" is_eth0="false"
        ip4=$(iface_ip "$dev")
        [[ "$dev" == "$ETH0" ]] && is_eth0="true"
        if [[ "$type" == "wifi" && "$st" == "connected" ]]; then
            ssid=$(nmcli -t -f ACTIVE,SSID dev wifi list ifname "$dev" 2>/dev/null \
                   | awk -F: '$1=="yes"{print $2; exit}')
        fi
        ifaces=$(jq -c \
            --arg dev "$dev" --arg type "$type" --arg state "$st" \
            --arg conn "$conn" --arg ip "$ip4" --arg ssid "$ssid" \
            --argjson is_eth0 "$is_eth0" \
            '. + [{device:$dev, type:$type, state:$state, connection:$conn, ip:$ip, ssid:$ssid, is_eth0:$is_eth0}]' \
            <<<"$ifaces")
    done < <(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>/dev/null)

    jq -n \
        --arg name "$NETWORK_NAME" \
        --arg ip   "$GATEWAY_IP"   \
        --arg host "$host"         \
        --arg eth0 "$ETH0"         \
        --argjson ifs "$ifaces"    \
        '{ok:true, network_name:$name, gateway_ip:$ip, hostname:$host, eth0_device:$eth0, interfaces:$ifs}'
}

cmd_wifi_devices() {
    local devs="[]"
    while IFS=: read -r dev type _; do
        [[ "$type" == "wifi" ]] && devs=$(jq -c --arg d "$dev" '. + [$d]' <<<"$devs")
    done < <(nmcli -t -f DEVICE,TYPE,STATE dev 2>/dev/null)
    jq -n --argjson d "$devs" '{ok:true, devices:$d}'
}

cmd_scan_wifi() {
    local iface="$1"
    [[ -z "$iface" ]] && fail "iface required"
    nmcli dev wifi rescan ifname "$iface" >/dev/null 2>&1 || true
    sleep 2
    local nets="[]"
    # Aggregate by SSID, take strongest signal seen
    declare -A seen
    while IFS=: read -r ssid signal security; do
        [[ -z "$ssid" ]] && continue
        local cur="${seen[$ssid]:-0:}"; cur="${cur%%:*}"; cur="${cur:-0}"
        if (( signal > cur )); then
            seen[$ssid]="$signal:$security"
        fi
    done < <(nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list ifname "$iface" 2>/dev/null)
    for ssid in "${!seen[@]}"; do
        local sigsec="${seen[$ssid]}"
        local sig="${sigsec%%:*}"
        local sec="${sigsec#*:}"
        nets=$(jq -c \
            --arg s "$ssid" --argjson sig "${sig:-0}" --arg sec "$sec" \
            '. + [{ssid:$s, signal:$sig, security:$sec}]' <<<"$nets")
    done
    nets=$(jq -c 'sort_by(-.signal)' <<<"$nets")
    jq -n --argjson n "$nets" '{ok:true, networks:$n}'
}

cmd_other_ifaces() {
    local arr="[]"
    for path in /sys/class/net/*; do
        local dev; dev=$(basename "$path")
        [[ "$dev" == "lo" || "$dev" == "$ETH0" ]] && continue
        [[ -e "$path/wireless" ]] && continue
        [[ ! -e "$path/device" ]] && continue
        local carrier; carrier=$(cat "$path/carrier" 2>/dev/null || echo 0)
        [[ "$carrier" != "1" ]] && continue
        local ip4; ip4=$(iface_ip "$dev")
        local st conn
        read -r st conn < <(nmcli -t -f DEVICE,STATE,CONNECTION dev 2>/dev/null \
            | awk -F: -v d="$dev" '$1==d{print $2" "$3; exit}')
        arr=$(jq -c \
            --arg d "$dev" --arg ip "$ip4" --arg st "${st:-unknown}" --arg conn "${conn:-}" \
            '. + [{device:$d, ip:$ip, state:$st, connection:$conn}]' <<<"$arr")
    done
    jq -n --argjson a "$arr" '{ok:true, interfaces:$a}'
}

cmd_connect_wifi() {
    local iface="$1" ssid="$2" pass="${3:-}"
    [[ -z "$iface" || -z "$ssid" ]] && fail "iface and ssid required"
    local out rc=0
    if [[ -z "$pass" ]]; then
        out=$(nmcli dev wifi connect "$ssid" ifname "$iface" 2>&1) || rc=$?
    else
        out=$(nmcli dev wifi connect "$ssid" password "$pass" ifname "$iface" 2>&1) || rc=$?
    fi
    if (( rc != 0 )); then
        jq -n --arg err "$out" '{ok:false, error:$err}'
        return 0
    fi
    sleep 1
    local ip4; ip4=$(iface_ip "$iface")
    jq -n --arg msg "$out" --arg ip "$ip4" --arg if "$iface" --arg ssid "$ssid" \
        '{ok:true, message:$msg, ip:$ip, iface:$if, ssid:$ssid}'
}

cmd_disconnect() {
    local iface="$1"
    [[ -z "$iface" ]] && fail "iface required"
    local out rc=0
    out=$(nmcli dev disconnect "$iface" 2>&1) || rc=$?
    if (( rc != 0 )); then
        jq -n --arg err "$out" '{ok:false, error:$err}'
        return 0
    fi
    jq -n --arg msg "$out" '{ok:true, message:$msg}'
}

cmd_auto_ip() {
    read_conf
    local tries=20 cand
    while (( tries-- > 0 )); do
        local x=$(( (RANDOM % 254) + 1 ))
        local y=$(( (RANDOM % 254) + 1 ))
        cand="10.${x}.${y}.1"
        [[ "$cand" == "$GATEWAY_IP" ]] && continue
        # Probe — silence is consent
        if ! ping -c1 -W1 "$cand" >/dev/null 2>&1; then
            jq -n --arg ip "$cand" '{ok:true, ip:$ip}'
            return 0
        fi
    done
    fail "could not find a free 10.X.Y.1 after 20 attempts"
}

cmd_apply_identity() {
    local new_name="$1" new_ip="$2"
    [[ -z "$new_name" || -z "$new_ip" ]] && fail "name and ip required"
    [[ "$new_ip" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || fail "ip must be 10.X.Y.1"
    [[ "$new_name" =~ ^[A-Za-z0-9_-]+$ ]] || fail "name must be [A-Za-z0-9_-]"

    read_conf
    local cur_name="$NETWORK_NAME" cur_ip="$GATEWAY_IP"

    if [[ "$cur_name" == "$new_name" && "$cur_ip" == "$new_ip" ]]; then
        jq -n '{ok:true, changed:false, message:"no change"}'
        return 0
    fi

    [[ -x "$SETUP_LILLYPAD" ]] || fail "setup_lillypad not executable at $SETUP_LILLYPAD"

    local logfile="/tmp/setup_lillypad.$$.log"
    if bash "$SETUP_LILLYPAD" "$new_name" "$new_ip" >"$logfile" 2>&1; then
        local log; log=$(tail -80 "$logfile")
        rm -f "$logfile"
        jq -n --arg log "$log" --arg name "$new_name" --arg ip "$new_ip" \
            '{ok:true, changed:true, network_name:$name, gateway_ip:$ip, log:$log}'
    else
        local log; log=$(cat "$logfile")
        rm -f "$logfile"
        jq -n --arg log "$log" '{ok:false, error:"setup_lillypad failed", log:$log}'
    fi
}

case "${1:-}" in
    state)          shift; cmd_state          "$@" ;;
    wifi_devices)   shift; cmd_wifi_devices   "$@" ;;
    scan_wifi)      shift; cmd_scan_wifi      "$@" ;;
    other_ifaces)   shift; cmd_other_ifaces   "$@" ;;
    connect_wifi)   shift; cmd_connect_wifi   "$@" ;;
    disconnect)     shift; cmd_disconnect     "$@" ;;
    auto_ip)        shift; cmd_auto_ip        "$@" ;;
    apply_identity) shift; cmd_apply_identity "$@" ;;
    *) fail "unknown subcommand: ${1:-<none>}" ;;
esac
