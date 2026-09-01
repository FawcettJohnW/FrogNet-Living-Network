#!/usr/bin/env bash
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
# frognet-node-guid.sh — canonical FrogNet machine identity (GUID).
#
# Decision 2026-06-02: machine identity is an install-time GUID (NOT a MAC).
# Stable across NIC swap / eth0<->wlan0 failover.
#
# [GUID_IDENTITY_V1 2026-06-19] The broker now keys identity on a real top-level
# "guid" field (no longer the "mac" reuse hack). frognet-tunnel-setup-v3.sh sends
# this GUID as "guid". The broker REVIVES a returning node's row on a matching
# GUID and only retires on an explicit regenerate. This script gains a
# `regenerate` subcommand for the deliberate-rotation case.
#
# Lifecycle:
#   - Allocated ONCE (generate-if-absent). NEVER regenerated automatically.
#   - File mode 0444 (-r--r--r--): readable; not casually overwritten/deleted.
#   - Stored at /etc/fnid (outside /etc/frognet) so a reinstall that wipes
#     /etc/frognet does NOT whack it.
#   - Intentional remove only (rm -f as root), OR explicit `regenerate`.
#
# NO `set -e`/`set -u`/`pipefail` and NO `2>/dev/null`: per the FrogNet rule,
# all failures must be VISIBLE, never masked. Errors are checked explicitly
# and reported loudly to stderr, with a non-zero exit.
#
# Usage:
#   frognet-node-guid.sh            # ensure-exists, then print the GUID
#   frognet-node-guid.sh --ensure   # ensure-exists only (install time), no print
#   frognet-node-guid.sh regenerate # DELIBERATE rotation: retire old GUID on the
#                                    # broker, then write a fresh one (0444).
GUID_FILE="${FROGNET_GUID_FILE:-/etc/fnid}"
# [ONE_CONF_V1] broker.conf folded into tunnel.conf.
BROKER_CONF="${FROGNET_BROKER_CONF:-${FROGNET_CONF:-/etc/frognet/tunnel.conf}}"
GATEWAYS_CONF="${FROGNET_GATEWAYS_CONF:-/etc/frognet/gateways.conf}"
err() { echo "frognet-node-guid: ERROR: $*" >&2; }

_gen_guid() {
    # 32 hex chars = a UUID with dashes stripped (matches prior node-id format).
    if command -v uuidgen >/dev/null; then
        uuidgen | tr -d '-' | tr 'A-F' 'a-f'
    elif command -v openssl >/dev/null; then
        openssl rand -hex 16
    else
        err "neither uuidgen nor openssl is available to generate a GUID"
        return 1
    fi
}

_write_guid() {
    # $1 = guid value. Writes GUID_FILE 0444. Caller must ensure any prior 0444
    # file is removable (we are root). Returns non-zero loudly on any failure.
    local guid="$1" dir
    dir="$(dirname "$GUID_FILE")"
    if ! mkdir -p "$dir"; then
        err "cannot create directory $dir"; return 1
    fi
    # A prior GUID file is 0444; remove it so the write succeeds.
    if [[ -e "$GUID_FILE" ]]; then
        if ! rm -f "$GUID_FILE"; then
            err "cannot remove existing $GUID_FILE for rewrite"; return 1
        fi
    fi
    umask 022
    if ! printf '%s\n' "$guid" > "$GUID_FILE"; then
        err "cannot write $GUID_FILE"; return 1
    fi
    if ! chmod 0444 "$GUID_FILE"; then
        err "cannot chmod 0444 $GUID_FILE"; return 1
    fi
    return 0
}

ensure_guid() {
    if [[ -s "$GUID_FILE" ]]; then
        return 0                      # already allocated — NEVER regenerate
    fi
    local guid
    guid="$(_gen_guid)" || return 1
    if [[ -z "$guid" ]]; then
        err "GUID generator produced empty output"; return 1
    fi
    _write_guid "$guid"
}

read_guid() {
    local g
    if [[ ! -s "$GUID_FILE" ]]; then
        err "$GUID_FILE absent or empty - this node has NO FrogNet identity."
        err "Not minting one: a fresh GUID registers as a NEW node and collides"
        err "with this node's existing broker row on (pond, name)."
        err "Restore the prior value, or rotate deliberately:"
        err "  frognet-node-guid.sh regenerate   # retires the old row first"
        return 1
    fi
    g="$(tr -d '[:space:]' < "$GUID_FILE")"
    if [[ -z "$g" ]]; then
        err "$GUID_FILE is empty or unreadable"; return 1
    fi
    printf '%s' "$g"
}

_pond_name() {
    # Pond for the broker retire call: POND_NAME in broker.conf, else
    # NETWORK_NAME in gateways.conf (same precedence frognet-tunnel-setup-v3.sh uses).
    local p=""
    if [[ -r "$BROKER_CONF" ]]; then
        p="$(grep '^POND_NAME=' "$BROKER_CONF" | cut -d= -f2- | tr -d '[:space:]')"
    fi
    if [[ -z "$p" && -r "$GATEWAYS_CONF" ]]; then
        p="$(grep '^NETWORK_NAME=' "$GATEWAYS_CONF" | cut -d= -f2- | tr -d '[:space:]')"
    fi
    printf '%s' "$p"
}

_broker_url() {
    [[ -r "$BROKER_CONF" ]] || return 0
    grep '^BROKER_URL=' "$BROKER_CONF" | cut -d= -f2- | tr -d '[:space:]'
}

regenerate_guid() {
    # DELIBERATE identity rotation. Retire the CURRENT GUID on the broker first
    # (so the old row becomes a ghost by intent, not as a side effect), then
    # write a fresh GUID. Broker retirement is best-effort: a node may be
    # offline/LAN-only at rotation time; the broker tolerates an un-retired old
    # row, and the node will register fresh regardless. We REPORT the outcome
    # loudly either way.
    local old new url pond
    if [[ -s "$GUID_FILE" ]]; then
        old="$(read_guid)" || return 1
    else
        old=""
    fi
    url="$(_broker_url)"
    pond="$(_pond_name)"
    if [[ -n "$old" && -n "$url" && -n "$pond" ]]; then
        if command -v curl >/dev/null; then
            echo "frognet-node-guid: retiring old GUID $old on broker (pond=$pond)" >&2
            local resp
            resp="$(curl -sk -X POST "${url%/}/api/v4/retire-guid" \
                -H "Content-Type: application/json" \
                -d "{\"pond\":\"${pond}\",\"guid\":\"${old}\"}")"
            echo "frognet-node-guid: broker retire response: ${resp}" >&2
        else
            err "curl not available — cannot retire old GUID on broker; rotating locally anyway"
        fi
    else
        echo "frognet-node-guid: no broker retire (old='${old}' url='${url}' pond='${pond}') — rotating locally" >&2
    fi
    new="$(_gen_guid)" || return 1
    if [[ -z "$new" ]]; then
        err "GUID generator produced empty output"; return 1
    fi
    _write_guid "$new" || return 1
    echo "frognet-node-guid: rotated GUID ${old:-<none>} -> ${new}" >&2
    return 0
}

case "${1:-}" in
    regenerate)
        regenerate_guid || exit 1 ;;
    --ensure)
        ensure_guid || { err "GUID allocation failed for $GUID_FILE"; exit 1; }
        ;;                            # INSTALL ONLY - the sole creation path
    --read|"")
        read_guid || exit 1 ;;        # register payload: read, never create
    *)
        err "unknown option: $1"; exit 2 ;;
esac
