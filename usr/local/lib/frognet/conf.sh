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
# =============================================================================
# /usr/local/lib/frognet/conf.sh - [ONE_CONF_V1]
#
# Key-wise read/write for the node's single config file, /etc/frognet/tunnel.conf.
#
# PROVENANCE - READ THIS. This file was RECONSTRUCTED on 2026-08-29 from its call
# sites, not recovered from a node. No release ever packaged usr/local/lib/frognet
# (the world manifest named frognet_trace.sh and frognet_log.sh individually and
# stopped there), so install phase C1c died with
# "FATAL: /usr/local/lib/frognet/conf.sh not found" on a clean install. If an
# original turns up on a node, IT WINS - diff it against this and keep whichever
# the callers actually expect.
#
# The contract is not guesswork: eight scripts source this file and between them
# pin every name and signature below.
#
#   FROGNET_CONF        frognet-unmake-gateway.sh:25 gives the default
#                       (/etc/frognet/tunnel.conf); frognet-conf-consolidate.sh:28
#                       takes `dirname "$FROGNET_CONF"` as the config directory.
#   fn_conf_get  KEY    frognet-conf-consolidate.sh:83 tests its output for
#                       emptiness, so an absent key prints nothing and returns 0.
#   fn_conf_set  KEY V  Eleven call sites. Must be KEY-WISE: change_pond.bash:193
#                       and frognet_setup_v4_helper.bash:136 both state that
#                       rewriting the file wholesale would take GROUP_TOKEN,
#                       PASSCODE, MAX_TUNNELS and NODE_GUID with it.
#   fn_conf_unset KEY   frognet_setup_v4_helper.bash:143 - clearing the broker
#                       fields must REMOVE keys, not delete the file.
#   fn_conf_backup      frognet-conf-consolidate.sh:47 prints it
#                       (`log "  backed up $(fn_conf_backup)"`), so it writes a
#                       copy and echoes the path.
#   fn_pond_set  NAME   frognet-conf-consolidate.sh's own header states the rule:
#                       "Written to BOTH POND_NAME and GROUP_NAME."
#
# [NO_FALLBACK_V1] Nothing here substitutes a value it does not have. A missing
# key reads as empty, which is the true answer; a write that cannot happen fails.
# =============================================================================

FROGNET_CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"

# Read one key. Prints the value, or nothing if the key or the file is absent.
# Quotes around the value are stripped, matching how the callers' own inline
# readers parse it (frognet-conf-consolidate.sh:_get_from).
fn_conf_get() {
    local _k="${1:?fn_conf_get: KEY required}"
    [ -f "$FROGNET_CONF" ] || return 0
    local _v
    _v="$(grep -m1 "^${_k}=" "$FROGNET_CONF" 2>/dev/null | cut -d= -f2-)" || return 0
    _v="${_v%\"}"; _v="${_v#\"}"
    printf '%s' "$_v"
}

# Set one key, in place, leaving every other line untouched. Creates the file
# (and its directory) if absent. Appends if the key is new; replaces the first
# occurrence otherwise.
fn_conf_set() {
    local _k="${1:?fn_conf_set: KEY required}"
    local _v="${2-}"
    local _d; _d="$(dirname "$FROGNET_CONF")"
    mkdir -p "$_d" || { echo "fn_conf_set: cannot create $_d" >&2; return 1; }
    if [ ! -f "$FROGNET_CONF" ]; then
        printf '%s=%s\n' "$_k" "$_v" > "$FROGNET_CONF" || return 1
        chmod 600 "$FROGNET_CONF" 2>/dev/null || true
        return 0
    fi
    local _tmp; _tmp="$(mktemp "${FROGNET_CONF}.XXXXXX")" || return 1
    if grep -q "^${_k}=" "$FROGNET_CONF"; then
        local _done=0
        while IFS= read -r _line || [ -n "$_line" ]; do
            case "$_line" in
                "${_k}="*)
                    if [ "$_done" -eq 0 ]; then
                        printf '%s=%s\n' "$_k" "$_v"
                        _done=1
                    else
                        printf '%s\n' "$_line"
                    fi ;;
                *) printf '%s\n' "$_line" ;;
            esac
        done < "$FROGNET_CONF" > "$_tmp"
    else
        cat "$FROGNET_CONF" > "$_tmp" || { rm -f "$_tmp"; return 1; }
        printf '%s=%s\n' "$_k" "$_v" >> "$_tmp"
    fi
    # Preserve mode/owner of the original: this file holds membership material.
    chmod --reference="$FROGNET_CONF" "$_tmp" 2>/dev/null || chmod 600 "$_tmp"
    chown --reference="$FROGNET_CONF" "$_tmp" 2>/dev/null || true
    mv -f "$_tmp" "$FROGNET_CONF"
}

# Remove one key. Absent key or absent file is not an error - the requested state
# already holds.
fn_conf_unset() {
    local _k="${1:?fn_conf_unset: KEY required}"
    [ -f "$FROGNET_CONF" ] || return 0
    grep -q "^${_k}=" "$FROGNET_CONF" || return 0
    local _tmp; _tmp="$(mktemp "${FROGNET_CONF}.XXXXXX")" || return 1
    grep -v "^${_k}=" "$FROGNET_CONF" > "$_tmp" || true
    chmod --reference="$FROGNET_CONF" "$_tmp" 2>/dev/null || chmod 600 "$_tmp"
    chown --reference="$FROGNET_CONF" "$_tmp" 2>/dev/null || true
    mv -f "$_tmp" "$FROGNET_CONF"
}

# Timestamped copy beside the original. Prints the path it wrote.
#
# The name ends .conf.<UTC timestamp> deliberately: FROGNET_NEVER_SHIP matches
# '*.conf.20*', so these backups - which carry GROUP_TOKEN and PASSCODE - can
# never reach the public repo. Suffix-anchored, so it cannot catch a real script.
fn_conf_backup() {
    [ -f "$FROGNET_CONF" ] || return 0
    local _b="${FROGNET_CONF}.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p "$FROGNET_CONF" "$_b" || { echo "fn_conf_backup: could not write $_b" >&2; return 1; }
    printf '%s' "$_b"
}

# Set the pond name. It lives under TWO keys: POND_NAME is what the setup helper
# and change_pond write, GROUP_NAME is what the tunnel daemon and broker
# registration read. frognet-conf-consolidate.sh's merge rule states they are the
# same value, so one function owns both and they cannot drift apart.
fn_pond_set() {
    local _p="${1:?fn_pond_set: POND name required}"
    fn_conf_set POND_NAME  "$_p" || return 1
    fn_conf_set GROUP_NAME "$_p" || return 1
}
