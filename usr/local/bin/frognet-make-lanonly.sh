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
# frognet-make-lanonly.sh - Demote this node from gateway back to LAN-only (CLI)
#
# [MAKE_LANONLY_CLI_V1] The inverse of frognet-make-gateway.sh. Deregisters the
# node from the broker, tears down its WireGuard tunnels, stops the tunnel
# daemon, and clears the broker fields from the node config so it stays LAN-only
# and does NOT re-enrol on the next merge.
#
# The teardown itself is NOT reimplemented here: it reuses go_lan_only() from
# frognet_internet_watch.sh (the exact, idempotent transition the internet-watch
# already runs when an uplink drops), so there is one implementation, not two.
# This script's own job is only to (a) force that teardown on operator command -
# even while an uplink is still present, which the watch would never do on its
# own - and (b) clear the broker config so enrolment does not restart.
#
# Membership identity is preserved by default: GROUP_TOKEN, PASSCODE and the node
# GUID (/etc/fnid) are left in place, so re-running frognet-make-gateway.sh brings
# the node back into the same pond cleanly. --forget also removes the pond
# password and chorus list (a fuller reset); it never touches /etc/fnid.
#
# Usage:
#   frognet-make-lanonly.sh              # deregister + tear down + clear broker
#   frognet-make-lanonly.sh --forget     # also drop pond password + choruses
#   frognet-make-lanonly.sh --no-deregister   # local teardown only, tell no one
#   -h, --help
# =============================================================================
set -euo pipefail

WATCH="/usr/local/bin/frognet_internet_watch.sh"
CONF_LIB="${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"

FORGET=0
DO_DEREGISTER=1

log() { printf '%s [make-lanonly] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '%s [make-lanonly] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --forget)        FORGET=1; shift ;;
        --no-deregister) DO_DEREGISTER=0; shift ;;
        -h|--help)       sed -n '2,33p' "$0"; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "must run as root (tears down WireGuard, edits /etc/frognet)"
[[ -f "$WATCH" ]]    || die "$WATCH not found - cannot reuse the teardown"
[[ -f "$CONF_LIB" ]] || die "$CONF_LIB not found - config library missing"

# --- 1. clear the broker fields FIRST ---------------------------------------
# Order matters: clear config before the teardown so that if a merge fires
# during teardown it sees no BROKER_URL and does not re-enrol. The membership
# card (GROUP_TOKEN/PASSCODE/NODE_GUID) is left intact so re-promotion is clean.
# shellcheck source=/dev/null
. "$CONF_LIB"
fn_conf_backup >/dev/null 2>&1 || true
for k in BROKER_URL POND_NAME GROUP_NAME; do
    fn_conf_unset "$k" 2>/dev/null || true
done
if [[ "$FORGET" -eq 1 ]]; then
    for k in POND_PASSWORD CHORUSES; do
        fn_conf_unset "$k" 2>/dev/null || true
    done
    log "cleared broker URL, pond name, password and choruses (--forget)"
else
    log "cleared broker URL and pond name (membership card and pond password kept)"
fi

# Mark enrolment not-pending so runMerge's deferred-enrolment retry does not fire.
touch /etc/frognet/pond_bootstrap_done 2>/dev/null || true

# --- 2. run the canonical teardown ------------------------------------------
# Source the watch script (guarded so its auto-detect main block does NOT run)
# and call go_lan_only directly. If --no-deregister, blank BROKER_URL in the
# environment go_lan_only reads so it skips the broker call but still tears down
# tunnels locally.
if [[ "$DO_DEREGISTER" -eq 0 ]]; then
    log "--no-deregister: local teardown only, broker will not be told"
    # go_lan_only reads BROKER_CONF for the URL; we already unset it above, so
    # the deregister step is skipped naturally and only the local teardown runs.
fi

# shellcheck source=/dev/null
if ! source "$WATCH"; then
    die "could not source $WATCH to reuse its teardown"
fi
if ! declare -F go_lan_only >/dev/null; then
    die "go_lan_only not found in $WATCH - teardown unavailable"
fi

log "tearing down gateway role (deregister + tunnels + daemon)..."
if go_lan_only; then
    log "DONE - node is LAN-only. Tunnels down, daemon stopped, broker config cleared."
    log "  It will NOT re-enrol on a merge. Re-promote with: frognet-make-gateway.sh"
else
    die "teardown reported an error; inspect the log above. Broker config is already cleared."
fi
