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
# v4 operations for setup_frognet.html.  Companion to
# frognet_setup_helper.bash (which handles identity/wifi/state).
#
# All subcommands emit a single JSON object on stdout.
# Errors emit {"ok":false,"error":"..."} and exit 0 so PHP can pass
# the body through to the browser unchanged.
#
# Subcommands:
#   reboot                                      - schedule a reboot
#   qr <url>                                    - emit base64 PNG of url
#   ssid_projection_get                         - current SSID projection state
#   ssid_projection_set <on|off>                - switch SSID projection
#
# Requires: jq, qrencode (Debian: apt install qrencode).
# PHP runs this via sudo; sudoers must permit it.
##############################################################

emit()  { jq -nc "$1"; }
fail()  { jq -nc --arg e "$1" '{ok:false,error:$e}'; exit 0; }

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

# ---------------------------------------------------------------------
# ssid_projection_get / ssid_projection_set <on|off>
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------
sub="${1:-}"; shift || true
case "$sub" in
    reboot)               cmd_reboot               "$@" ;;
    qr)                   cmd_qr                   "$@" ;;
    ssid_projection_get)  cmd_ssid_projection_get  "$@" ;;
    ssid_projection_set)  cmd_ssid_projection_set  "$@" ;;
    *)                    fail "unknown subcommand: $sub" ;;
esac
