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
# frognet_setup_v4_helper.bash
#
# v4-specific operations for setup_frognet.html.  Companion to
# frognet_setup_helper.bash (which handles identity/wifi/state).
#
# All subcommands emit a single JSON object on stdout.
# Errors emit {"ok":false,"error":"..."} and exit 0 so PHP can pass
# the body through to the browser unchanged.
#
# Subcommands:
#   broker_state                                — read current pond.conf
#   apply_broker <host> <port> <pond> <pw> <chorus_csv>
#                                               — write pond.conf
#   clear_broker                                — remove pond.conf
#   register <pubkey>                           — POST to broker /register
#   reboot                                      — schedule a reboot
#   qr <url>                                    — emit base64 PNG of url
#
# Requires: jq, curl, qrencode (Debian: apt install qrencode).
# PHP runs this via sudo; sudoers must permit it.
##############################################################


. "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"   # [ONE_CONF_V1]
POND_CONF="${FROGNET_POND_CONF:-/etc/frognet/pond.conf}"  # legacy, pre-consolidation only
GATEWAYS_CONF="${FROGNET_GATEWAYS_CONF:-/etc/frognet/gateways.conf}"
HOSTAPD_CONF="${FROGNET_HOSTAPD_CONF:-/etc/hostapd/hostapd.conf}"
WG_PRIV="${FROGNET_WG_PRIV:-/etc/frognet/wg.key}"
WG_PUB="${FROGNET_WG_PUB:-/etc/frognet/wg.pub}"

emit()  { jq -nc "$1"; }
fail()  { jq -nc --arg e "$1" '{ok:false,error:$e}'; exit 0; }

TUNNEL_CONF="${FROGNET_TUNNEL_CONF:-/etc/frognet/tunnel.conf}"

# Helper: parse a BROKER_URL into host/port/path
_parse_broker_url() {
    local url="$1"
    local u="${url#http://}"; u="${u#https://}"
    local hp="${u%%/*}"
    # [BROKER_URL_PATH_SLASH_V1] was p="/${u#"$hp"}" -- the remainder after the
    # host already begins with "/", so prepending another produced
    # https://host:18257//frognet-broker-v4.
    local p="${u#"$hp"}"; [[ "$p" == "/" ]] && p=""
    local h="$hp" pt=""
    if [[ "$hp" == *:* ]]; then h="${hp%:*}"; pt="${hp##*:}"; fi
    printf '%s\n%s\n%s\n' "$h" "$pt" "$p"
}

cmd_broker_state() {
    local enabled=false host="" port="" path="" pond="" has_pw=false choruses=""

    # tunnel.conf = live truth (BROKER_URL, GROUP_NAME)
    if [[ -f "$TUNNEL_CONF" ]]; then
        local burl="" gname=""
        burl=$(grep '^BROKER_URL=' "$TUNNEL_CONF" | head -1 | cut -d= -f2-)
        gname=$(grep '^GROUP_NAME=' "$TUNNEL_CONF" | head -1 | cut -d= -f2-)
        if [[ -n "$burl" ]]; then
            enabled=true
            { read -r host; read -r port; read -r path; } < <(_parse_broker_url "$burl")
        fi
        [[ -n "$gname" ]] && pond="$gname"
    fi

    # pond.conf = intent-only fields (CHORUSES, password presence)
    if [[ -f "$FROGNET_CONF" ]]; then
        local pn="" pw="" ch=""
        pn=$(grep -m1 -E '^(POND_NAME|GROUP_NAME)=' "$FROGNET_CONF" | cut -d= -f2-)
        # [CONF_INJECTION_V1] POND_PASSWORD is now written double-quoted; go
        # through fn_conf_get so the quotes and escapes are undone once, here.
        pw=$(fn_conf_get POND_PASSWORD)
        ch=$(fn_conf_get CHORUSES)
        [[ -z "$pond" && -n "$pn" ]] && { enabled=true; pond="$pn"; }
        [[ -n "$pw" ]] && has_pw=true
        choruses="$ch"
    fi

    jq -nc \
        --argjson enabled "$enabled" \
        --arg host "$host" --arg port "$port" --arg path "$path" \
        --arg pond "$pond" --arg choruses "$choruses" \
        --argjson has_pw "$has_pw" \
        '{ok:true,enabled:$enabled,host:$host,port:$port,path:$path,
          pond:$pond,pond_password_set:$has_pw,
          choruses:($choruses|split(" ")|map(select(.!="")))}'
}

cmd_apply_broker() {
    local host="${1:-}" port="${2:-}" pond="${3:-}" pw="${4:-}" csv="${5:-}"
    [[ -z "$host" || -z "$pond" ]] && fail "host and pond are required"

    # Preserve existing path from tunnel.conf if the form didn't supply one.
    local existing_path=""
    if [[ -f "$TUNNEL_CONF" ]]; then
        local oldurl
        oldurl=$(grep '^BROKER_URL=' "$TUNNEL_CONF" | head -1 | cut -d= -f2-)
        if [[ -n "$oldurl" ]]; then
            { read -r _; read -r _; read -r existing_path; } < <(_parse_broker_url "$oldurl")
        fi
    fi

    # If form didn't supply pw, preserve current pond.conf password.
    if [[ -z "$pw" && -f "$POND_CONF" ]]; then
        # Legacy pre-consolidation pond.conf: unquoted, so read it raw, but
        # strip a surrounding quote pair in case it was written post-fix.
        pw=$(grep '^POND_PASSWORD=' "$POND_CONF" | head -1 | cut -d= -f2-)
        pw="${pw#\"}"; pw="${pw%\"}"
    fi

    local choruses=""
    [[ -n "$csv" ]] && choruses="$(echo "$csv" | tr ',' ' ' | xargs)"

    # Build BROKER_URL: https://host[:port][path]
    local hp="$host"
    [[ -n "$port" && "$port" != "443" ]] && hp="${host}:${port}"
    local burl="https://${hp}${existing_path}"

    # [ONE_CONF_V1] One config file. This wrote pond.conf and then sed'd
    # tunnel.conf in place -- two files because two consumers each read only one
    # of them. Membership fields are never touched here.
    . "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
    fn_conf_backup >/dev/null
    fn_conf_set BROKER_URL "$burl"
    fn_pond_set "$pond"
    [[ -n "$pw" ]] && fn_conf_set POND_PASSWORD "$pw"
    fn_conf_set CHORUSES "$choruses"

    jq -nc --arg burl "$burl" --arg pond "$pond" --arg choruses "$choruses" \
        '{ok:true,wrote:["'"$FROGNET_CONF"'"],
          broker_url:$burl,pond:$pond,
          choruses:($choruses|split(" ")|map(select(.!="")))}'
}

# ---------------------------------------------------------------------
# clear_broker — remove pond.conf (user unchecked the broker box)
# ---------------------------------------------------------------------
cmd_clear_broker() {
    # [ONE_CONF_V1] The broker fields share a file with the membership card now,
    # so clearing must UNSET keys -- deleting the file would take GROUP_TOKEN and
    # NODE_GUID with it.
    . "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
    if [[ -f "$FROGNET_CONF" ]] && grep -q '^BROKER_URL=' "$FROGNET_CONF"; then
        fn_conf_backup >/dev/null
        for k in BROKER_URL POND_NAME GROUP_NAME POND_PASSWORD CHORUSES; do
            fn_conf_unset "$k"
        done
        jq -nc '{ok:true,cleared:true}'
    else
        jq -nc '{ok:true,cleared:false,note:"already absent"}'
    fi
}

# ---------------------------------------------------------------------
# register <pubkey>  (called by the registration script, optional from UI)
# Reads the node config, posts to broker /api/v4/register.
# [BROKER_API_V4_PREFIX_V1] was /api/v1/register -- frognet_broker_v4.py declares
# only /api/v4/* routes (no prefix, no include_router), so the v1 path 404'd and
# registration from the setup page could never succeed.  We don't generate
# the keypair here — that's done by the lillypad register flow.
# ---------------------------------------------------------------------
cmd_register() {
    local pubkey="${1:-}"
    [[ -z "$pubkey" ]] && fail "pubkey required"
    [[ ! -f "$FROGNET_CONF" ]] && fail "$FROGNET_CONF not found - run apply_broker first"

    # shellcheck disable=SC1090
    source "$FROGNET_CONF"
    [[ -z "${POND_NAME:-}" ]] && POND_NAME="${GROUP_NAME:-}"
    : "${BROKER_URL:?BROKER_URL missing}" "${POND_NAME:?POND_NAME missing}"

    local hostname subnet
    hostname="$(hostname)"
    if [[ -f "$GATEWAYS_CONF" ]]; then
        # shellcheck disable=SC1090
        source "$GATEWAYS_CONF"
        # GATEWAY_IP is x.y.z.1 — strip last octet, append /24
        subnet="${GATEWAY_IP%.*}.0/24"
    else
        subnet="10.1.1.0/24"
    fi

    local body resp code
    body=$(jq -nc \
        --arg pond "$POND_NAME" --arg pubkey "$pubkey" \
        --arg subnet "$subnet" --arg name "$hostname" \
        --arg pw "${POND_PASSWORD:-}" \
        '{pond:$pond,pubkey:$pubkey,subnet:$subnet,
          node_name:$name,pond_password:$pw}')
    resp=$(curl -sk -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" -d "$body" \
        "${BROKER_URL}/api/v4/register" 2>&1)
    code="${resp##*$'\n'}"
    body="${resp%$'\n'*}"
    if [[ "$code" =~ ^2 ]]; then
        jq -nc --argjson r "$body" '{ok:true,registered:$r}'
    else
        jq -nc --arg c "$code" --arg b "$body" \
            '{ok:false,error:("broker returned "+$c+": "+$b)}'
    fi
}

# ---------------------------------------------------------------------
# reboot — fire a delayed reboot so PHP can return first
# ---------------------------------------------------------------------
cmd_reboot() {
    ( sleep 3 && systemctl reboot ) >/dev/null 2>&1 &
    disown
    jq -nc '{ok:true,reboot_in_seconds:3}'
}

# ---------------------------------------------------------------------
# qr <text>  — base64-encoded PNG of the text encoded as a QR code.
# Used to show "scan this to come back after reboot" in the UI.
# ---------------------------------------------------------------------
cmd_qr() {
    local text="${1:-}"
    [[ -z "$text" ]] && fail "text required"
    command -v qrencode >/dev/null 2>&1 || fail "qrencode not installed"
    local png_b64
    png_b64="$(qrencode -o - -s 6 -m 2 "$text" 2>/dev/null | base64 -w0)"
    [[ -z "$png_b64" ]] && fail "qrencode produced no output"
    jq -nc --arg b64 "$png_b64" --arg text "$text" \
        '{ok:true,text:$text,png_base64:$b64}'
}

# =====================================================================
# SSID_PROJECTION — paste into /usr/local/bin/frognet_setup_v4_helper.bash
# Insert these two functions ALONGSIDE the other cmd_* functions
# (e.g. right after cmd_clear_broker, before cmd_register).
# =====================================================================

cmd_ssid_projection_get() {
    # Run the CLI in --json mode and pass through.  CLI is the source of
    # truth for parsing; we just shell out.
    local out
    if ! out="$(/usr/local/bin/frognet-ssid-projection --json get 2>&1)"; then
        fail "ssid_projection get failed: $out"
    fi
    # The CLI already emits valid JSON.  Just print it.
    printf '%s\n' "$out"
    exit 0
}

cmd_ssid_projection_set() {
    # Arg 1: "on" or "off".
    local target="${1:-}"
    case "$target" in
        on|off) ;;
        *) fail "ssid_projection_set requires 'on' or 'off' (got: '$target')" ;;
    esac
    local out
    if ! out="$(/usr/local/bin/frognet-ssid-projection --json "$target" 2>&1)"; then
        fail "ssid_projection set failed: $out"
    fi
    printf '%s\n' "$out"
    exit 0
}

# =====================================================================
# Then in the bottom dispatcher (the case "$1" in ... esac), add:
#
#     ssid_projection_get) shift; cmd_ssid_projection_get "$@" ;;
#     ssid_projection_set) shift; cmd_ssid_projection_set "$@" ;;
# =====================================================================
# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------
sub="${1:-}"; shift || true
case "$sub" in
    broker_state)         cmd_broker_state         "$@" ;;
    apply_broker)         cmd_apply_broker         "$@" ;;
    clear_broker)         cmd_clear_broker         "$@" ;;
    register)             cmd_register             "$@" ;;
    reboot)               cmd_reboot               "$@" ;;
    qr)                   cmd_qr                   "$@" ;;
    ssid_projection_get)  cmd_ssid_projection_get  "$@" ;;
    ssid_projection_set)  cmd_ssid_projection_set  "$@" ;;
    *)                    fail "unknown subcommand: $sub" ;;
esac
