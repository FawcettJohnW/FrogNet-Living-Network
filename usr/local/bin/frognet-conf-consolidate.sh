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
# frognet-conf-consolidate.sh — fold broker.conf and pond.conf into tunnel.conf.
#
# [ONE_CONF_V1] Run once per node, at upgrade. Idempotent: running it again on an
# already-consolidated node is a no-op that exits 0.
#
# Merge rule, and why:
#   BROKER_URL   tunnel.conf wins.
#                The setup helper's own comment calls tunnel.conf "live truth"
#                and pond.conf "intent"; tunnel.conf is what the running daemon
#                has actually been using. Divergence is logged LOUDLY rather than
#                silently resolved -- if the three disagree, the operator needs to
#                know which one the node was really talking to.
#   POND_NAME    tunnel.conf:GROUP_NAME wins, else broker.conf/pond.conf
#                POND_NAME. Written to BOTH POND_NAME and GROUP_NAME.
#   everything else  copied across; tunnel.conf's own keys are never overwritten.
#
# The old files are backed up and then REMOVED. Leaving them would recreate the
# original bug in reverse: a stale broker.conf outliving a broker change is the
# state frognet-tunnel-setup-v3.sh explicitly warns about.
set -eu

LIB="${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
[[ -r "$LIB" ]] || { echo "FATAL: $LIB not found" >&2; exit 1; }
# shellcheck source=/dev/null
. "$LIB"

ETC="$(dirname "$FROGNET_CONF")"
BROKER_CONF="${FROGNET_BROKER_CONF:-$ETC/broker.conf}"
POND_CONF="${FROGNET_POND_CONF:-$ETC/pond.conf}"

log() { echo "[conf-consolidate] $*"; }

_get_from() {  # _get_from FILE KEY
    [[ -f "$1" ]] || return 0
    local v; v=$(grep -m1 "^${2}=" "$1" 2>/dev/null | cut -d= -f2-)
    v="${v%\"}"; v="${v#\"}"
    printf '%s' "$v"
}

if [[ ! -f "$BROKER_CONF" && ! -f "$POND_CONF" ]]; then
    log "already consolidated - no broker.conf or pond.conf present"
    exit 0
fi

log "consolidating into $FROGNET_CONF"
[[ -f "$FROGNET_CONF" ]] && log "  backed up $(fn_conf_backup)"

# ---- BROKER_URL: report divergence before choosing ----
t_url=$(_get_from "$FROGNET_CONF" BROKER_URL)
b_url=$(_get_from "$BROKER_CONF"  BROKER_URL)
p_url=$(_get_from "$POND_CONF"    BROKER_URL)
for pair in "tunnel:$t_url" "broker:$b_url" "pond:$p_url"; do
    [[ -n "${pair#*:}" ]] && log "  BROKER_URL (${pair%%:*}) = ${pair#*:}"
done
seen=$(printf '%s\n%s\n%s\n' "$t_url" "$b_url" "$p_url" | grep -v '^$' | sort -u | wc -l)
if [[ "$seen" -gt 1 ]]; then
    log "  WARNING: the files DISAGREE on BROKER_URL."
    log "  WARNING: taking tunnel.conf's value - that is what the daemon was using."
    log "  WARNING: if that is wrong, fix it now before starting the tunnel daemon."
fi
url="$t_url"; [[ -z "$url" ]] && url="$b_url"; [[ -z "$url" ]] && url="$p_url"
[[ -n "$url" ]] && fn_conf_set BROKER_URL "$url"

# ---- pond name: four spellings, one value ----
pond=$(_get_from "$FROGNET_CONF" GROUP_NAME)
[[ -z "$pond" ]] && pond=$(_get_from "$BROKER_CONF" POND_NAME)
[[ -z "$pond" ]] && pond=$(_get_from "$POND_CONF"   POND_NAME)
if [[ -n "$pond" ]]; then
    fn_pond_set "$pond"
    log "  pond = $pond (written as POND_NAME and GROUP_NAME)"
    net=$(_get_from "$ETC/gateways.conf" NETWORK_NAME)
    if [[ -n "$net" && "$net" != "$pond" ]]; then
        log "  WARNING: gateways.conf NETWORK_NAME='$net' != pond '$pond'."
        log "  WARNING: the tunnel daemon reads NETWORK_NAME for GUID re-registration."
    fi
fi

# ---- carry pond.conf's own fields across ----
for k in POND_PASSWORD CHORUSES; do
    v=$(_get_from "$POND_CONF" "$k")
    if [[ -n "$v" ]]; then
        [[ -z "$(fn_conf_get "$k")" ]] && { fn_conf_set "$k" "$v"; log "  carried $k"; }
    fi
done

# ---- retire the old files ----
for f in "$BROKER_CONF" "$POND_CONF"; do
    if [[ -f "$f" ]]; then
        cp -p "$f" "${f}.superseded.$(date +%Y%m%d-%H%M%S)"
        rm -f "$f"
        log "  removed $(basename "$f") (kept a .superseded copy)"
    fi
done

log "done - single config at $FROGNET_CONF"
