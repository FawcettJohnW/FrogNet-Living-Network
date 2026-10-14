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
# [GUID_IDENTITY_V1 2026-06-19] /etc/fnid is the node's identity: the tunnel
# daemon announces it in the region (facts.guid) and rinab's machine_id reads it.
# `regenerate` is the deliberate-rotation case; it is local only (2026-10-12).
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
#   frognet-node-guid.sh regenerate # DELIBERATE rotation: write a fresh one (0444).
GUID_FILE="${FROGNET_GUID_FILE:-/etc/fnid}"
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
        err "Not minting one: a fresh GUID announces this node as a NEW node."
        err "Restore the prior value, or rotate deliberately:"
        err "  frognet-node-guid.sh regenerate"
        return 1
    fi
    g="$(tr -d '[:space:]' < "$GUID_FILE")"
    if [[ -z "$g" ]]; then
        err "$GUID_FILE is empty or unreadable"; return 1
    fi
    printf '%s' "$g"
}

regenerate_guid() {
    # DELIBERATE identity rotation: write a fresh GUID and say what it replaced.
    local old new
    if [[ -s "$GUID_FILE" ]]; then
        old="$(read_guid)" || return 1
    else
        old=""
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
        read_guid || exit 1 ;;        # read, never create
    *)
        err "unknown option: $1"; exit 2 ;;
esac
