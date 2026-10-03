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
# frognet-make-gateway.sh - Turn this node into an internet gateway (CLI)
#
# [MAKE_GATEWAY_CLI_V1] The single command-line path to register a node with the
# broker and join a pond. It is the CLI equivalent of the setup_frognet.html web
# form: it collects broker + pond details, writes them into the one config file
# (/etc/frognet/tunnel.conf, via conf.sh), ensures the node GUID exists, then runs
# the real v4 registration (frognet-tunnel-setup-v3.sh -> POST /api/v4/register).
#
# This does NOT replace frognet-tunnel-register.sh -- that script speaks the old
# /api/v1 protocol that NO current broker serves, and is dead. This speaks v4.
#
# Being a gateway also requires a real non-10 uplink (a WAN DHCP lease or a WiFi
# client link upstream). This script supplies the broker half; the uplink is
# physical. With broker config present but no uplink, the node simply stays
# LAN-only until an uplink appears, then flips to WAN mode on the next merge.
#
# Usage:
#   frognet-make-gateway.sh                         # prompts for everything
#   frognet-make-gateway.sh --host <h> --pond <p>   # flags; prompts for the rest
#   frognet-make-gateway.sh --host streamingfrog.com --port 18257 \
#       --scheme http --pond RatPond --password s3cret --chorus hometown,family
#
# Options:
#   --host <fqdn>        Broker host (e.g. streamingfrog.com)
#   --port <n>          Broker port (default 18257)
#   --scheme <http|https>  URL scheme (default http; the broker port is plain
#                          HTTP unless the broker has a TLS cert)
#   --path <p>          URL path (default empty; the broker serves /api/v4 at root)
#   --pond <name>       Pond to join (the group name shared by its nodes)
#   --password <pw>     Pond password (only if the pond has one; omit for open)
#   --chorus <csv>      Comma-separated chorus names (optional)
#   --no-register       Write config only; do not register now (defer to a merge)
#   -h, --help          This help
# =============================================================================
set -euo pipefail

CONF_LIB="${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
SETUP="/usr/local/bin/frognet-tunnel-setup-v3.sh"
GUIDGEN="/usr/local/bin/frognet-node-guid.sh"
FNID="/etc/fnid"

HOST=""; PORT="18257"; SCHEME="http"; UPATH=""; POND=""; PASSWORD=""; CHORUS=""
DO_REGISTER=1

log() { printf '%s [make-gateway] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '%s [make-gateway] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)     HOST="$2"; shift 2 ;;
        --host=*)   HOST="${1#*=}"; shift ;;
        --port)     PORT="$2"; shift 2 ;;
        --port=*)   PORT="${1#*=}"; shift ;;
        --scheme)   SCHEME="$2"; shift 2 ;;
        --scheme=*) SCHEME="${1#*=}"; shift ;;
        --path)     UPATH="$2"; shift 2 ;;
        --path=*)   UPATH="${1#*=}"; shift ;;
        --pond)     POND="$2"; shift 2 ;;
        --pond=*)   POND="${1#*=}"; shift ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --password=*) PASSWORD="${1#*=}"; shift ;;
        --chorus)   CHORUS="$2"; shift 2 ;;
        --chorus=*) CHORUS="${1#*=}"; shift ;;
        --no-register) DO_REGISTER=0; shift ;;
        -h|--help)  sed -n '2,45p' "$0"; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "must run as root (writes /etc/frognet and creates WireGuard keys)"
[[ -x "$SETUP" ]] || die "$SETUP not found - this node's tunnel setup is missing"
[[ -f "$CONF_LIB" ]] || die "$CONF_LIB not found - config library missing"

# --- collect anything not supplied ------------------------------------------
if [[ -z "$HOST" ]]; then
    read -rp "  Broker host (e.g. streamingfrog.com): " HOST
    HOST="$(echo "$HOST" | xargs)"
fi
[[ -n "$HOST" ]] || die "broker host is required"

if [[ -z "$POND" ]]; then
    read -rp "  Pond to join (group name shared by its nodes): " POND
    POND="$(echo "$POND" | xargs)"
fi
[[ -n "$POND" ]] || die "pond name is required"

case "$SCHEME" in http|https) ;; *) die "--scheme must be http or https (got: $SCHEME)" ;; esac

# --- build the broker URL ----------------------------------------------------
# Port 443 is implied by https and omitted; any other port is explicit. The path
# defaults empty because the broker serves /api/v4 at the root of its port.
HP="$HOST"
[[ -n "$PORT" && "$PORT" != "443" ]] && HP="${HOST}:${PORT}"
BROKER_URL="${SCHEME}://${HP}${UPATH}"

# --- ensure node identity (GUID) --------------------------------------------
# /api/v4/register 400s on an empty guid. /etc/fnid is the machine identity; it
# is generated once and never auto-regenerated, so create it only if absent.
if [[ ! -s "$FNID" ]]; then
    if [[ -x "$GUIDGEN" ]]; then
        log "no /etc/fnid - creating node GUID"
        # --ensure creates the GUID only if absent and never regenerates an
        # existing one (regenerating would give this node a new identity and
        # orphan its broker registration -> the node-collision failure mode).
        "$GUIDGEN" --ensure || die "GUID generation failed"
    else
        die "/etc/fnid missing and $GUIDGEN not found - cannot register without a GUID"
    fi
fi
log "node GUID: $(cat "$FNID" 2>/dev/null | tr -d '[:space:]')"

# --- write config (one file, key-wise; never truncate the membership card) ---
# shellcheck source=/dev/null
. "$CONF_LIB"
fn_conf_backup >/dev/null 2>&1 || true
fn_conf_set BROKER_URL "$BROKER_URL"
fn_pond_set "$POND"
[[ -n "$PASSWORD" ]] && fn_conf_set POND_PASSWORD "$PASSWORD"
[[ -n "$CHORUS" ]]   && fn_conf_set CHORUSES "$(echo "$CHORUS" | tr ',' ' ' | xargs)"
log "wrote broker config: $BROKER_URL pond=$POND"

# --- register now, or defer --------------------------------------------------
if [[ "$DO_REGISTER" -eq 0 ]]; then
    log "--no-register: config written; the node will enrol on its next merge."
    log "  force it now with: rm -f /etc/frognet/pond_bootstrap_done && runMerge.bash"
    exit 0
fi

log "registering with broker (this generates WireGuard keys and builds tunnels)..."
if "$SETUP"; then
    log "DONE - registered with the broker and tunnel setup ran."
    log "  This node is a gateway once it also has a non-10 uplink and the broker"
    log "  is reachable; the daemon flips to WAN mode on the merge that satisfies all three."
    log "  Verify:  wg show   and   ip route show default | grep -v '10\\.'"
else
    die "registration/tunnel setup failed. Check the broker URL is reachable and \
the pond name/password are correct. Config is written; re-run this script or a \
merge to retry."
fi
