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
# frognet-pond-bootstrap.sh - Deferred tunnel setup
#
# [DEFERRED_BROKER_SETUP_V1] Event-driven, not polled. Invoked from the tail of
# runMerge.bash while enrolment is pending, and a merge is what a network change
# already produces: the dnsmasq dhcp-script on lease events, and the
# NetworkManager dispatcher hooks on interface up/down (90-frognet-merge) and on
# connectivity-change (91-frognet-connectivity). The moment the box can actually
# reach the broker is a network event, so that is when this runs.
#
# Once credentials are established it starts the tunnel daemon and writes
# /etc/frognet/pond_bootstrap_done, which is what stops runMerge calling it.

LOG_TAG="frognet-pond-bootstrap"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $*"; logger -t "$LOG_TAG" "$*" 2>/dev/null || true; }

# [ONE_CONF_V1] pond.conf folded into tunnel.conf.
CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"
TUNNEL_CONF="/etc/frognet/tunnel.conf"
# [DEFERRED_BROKER_SETUP_V1] was /usr/local/bin/frognet_tunnel_setup.sh, which
# does not exist in any shipped tree -- so this timer could never complete. The
# real script is frognet-tunnel-setup-v3.sh, and it takes NO positional args:
# it reads BROKER_URL and the pond from the node config itself. Passing it
# <url> <pond> <chorus> (as this did) would have been rejected even had the
# path been right.
SETUP="/usr/local/bin/frognet-tunnel-setup-v3.sh"
PENDING="/etc/sentinels/broker_setup_pending"
DONE_SENTINEL="/etc/frognet/pond_bootstrap_done"

# Already done — shouldn't be running
if [[ -f "$DONE_SENTINEL" ]]; then
    log "ALREADY_DONE — disabling timer"
    systemctl stop frognet-pond-bootstrap.timer 2>/dev/null || true
    systemctl disable frognet-pond-bootstrap.timer 2>/dev/null || true
    exit 0
fi

# Config missing — nothing to do
if [[ ! -f "$CONF" ]]; then
    log "NO_CONFIG — $CONF not found, disabling"
    systemctl stop frognet-pond-bootstrap.timer 2>/dev/null || true
    systemctl disable frognet-pond-bootstrap.timer 2>/dev/null || true
    exit 0
fi

source "$CONF"
log "ATTEMPT broker=$BROKER_URL pond=$POND_NAME choruses=$CHORUSES"

# Test broker connectivity.
# [BROKER_API_V4_PREFIX_V1] was /api/v1/health. The broker declares no /health
# route at ANY version and only /api/v4/* paths, so that probe always 404'd --
# it passed only because the check below treats any HTTP response as reachable.
HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
    "${BROKER_URL}/api/v4/ponds" 2>/dev/null) || HTTP_CODE="000"

if [[ "$HTTP_CODE" == "000" ]]; then
    log "NO_INTERNET — cannot reach broker (connection failed), will retry"
    exit 0
fi

if [[ "$HTTP_CODE" != "200" ]]; then
    log "BROKER_ERROR — HTTP $HTTP_CODE from broker health check, will retry"
    exit 0
fi

log "BROKER_REACHABLE — HTTP $HTTP_CODE, proceeding with setup"

# Setup script must exist (from world tar)
if [[ ! -x "$SETUP" ]]; then
    log "FATAL — $SETUP not found or not executable"
    exit 1
fi

# [DEFERRED_BROKER_SETUP_V1] One call, no positional args. v3 reads BROKER_URL
# and POND_NAME/GROUP_NAME out of the node config and registers every channel it
# needs; there is nothing to iterate here. Chorus membership is broker-side.
SETUP_OK=true
if bash "$SETUP" ${POND_PASSWORD:+--pond-password "$POND_PASSWORD"} 2>&1 \
        | while read -r line; do log "  $line"; done; then
    log "SETUP_OK"
else
    SETUP_OK=false
    log "SETUP_FAILED - will retry on the next merge or timer tick"
fi

if [[ "$SETUP_OK" != "true" ]]; then
    log "INCOMPLETE - setup did not finish, will retry"
    exit 0
fi

# Verify tunnel.conf was written
if [[ ! -f "$TUNNEL_CONF" ]] || ! grep -q 'BROKER_URL' "$TUNNEL_CONF" 2>/dev/null; then
    log "TUNNEL_CONF_MISSING — setup ran but tunnel.conf not valid, will retry"
    exit 0
fi

# Success — start the daemon and disable this timer
#
# [BOOTSTRAP_NEVER_RESTARTS_A_LIVE_DAEMON_V1] This is enrolment, not a service
# manager. It used to `systemctl restart` unconditionally, and because runMerge
# calls this script on EVERY pass while the sentinel is missing -- plus
# frognet-pond-bootstrap.timer every 120s -- a node that never reached the
# sentinel bounced the tunnel daemon every couple of minutes, forever. Each
# bounce tears down and rebuilds every wg iface, which deletes and re-adds every
# channel /24, which is a real routing change, which propagates: the whole mesh
# merges because one node could not finish enrolling. That is what drove
# 10.120.120.0/24 and 10.130.130.0/24 in and out of every peer's table.
#
# So: start it if it is down, leave it entirely alone if it is up. There is no
# state this script produces that a running daemon needs to be restarted to see
# -- it reads tunnel.conf per poll.
TUNNEL_UNIT="frognet-tunnel-daemon-v3"
log "CREDENTIALS_ESTABLISHED - ensuring $TUNNEL_UNIT is running"
systemctl enable "$TUNNEL_UNIT" 2>/dev/null || true
if systemctl is-active --quiet "$TUNNEL_UNIT"; then
    log "TUNNEL_DAEMON_ALREADY_RUNNING - not restarting"
else
    log "TUNNEL_DAEMON_DOWN - starting"
    systemctl start "$TUNNEL_UNIT" 2>/dev/null || true
fi

# [BOOTSTRAP_TERMINATES_V1] The sentinel is keyed on ENROLMENT, which is what
# this script is responsible for and what it has just finished: credentials are
# established and tunnel.conf is valid. It used to be keyed on the daemon being
# active, so a daemon that failed to start -- for any reason, including one that
# has nothing to do with enrolment -- meant the sentinel was never written and
# the retry ran forever. Enrolment does not become un-done because a unit is
# down; that is the service manager's problem and Restart= handles it.
if ! systemctl is-active --quiet "$TUNNEL_UNIT"; then
    log "TUNNEL_DAEMON_NOT_RUNNING - enrolment is still complete; marking done anyway."
    log "  the unit is systemd's to restart: journalctl -u $TUNNEL_UNIT"
fi

# Mark done. runMerge tests for this sentinel and stops calling us.
touch "$DONE_SENTINEL"
log "DONE - enrolment complete; runMerge will stop retrying"
# Left over from the polled design; harmless if the unit is already disabled.
systemctl disable --now frognet-pond-bootstrap.timer 2>/dev/null || true
log "BOOTSTRAP_COMPLETE pond=$POND_NAME choruses=$CHORUSES"
