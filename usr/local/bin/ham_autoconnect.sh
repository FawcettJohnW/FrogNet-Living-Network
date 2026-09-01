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
# /usr/local/bin/ham_autoconnect.sh
#
# FrogNet HAM overlay carrier bring-up (carrier only).
#
# IMPORTANT:
#   This script MUST NOT install FrogNet /24 routes.
#   It MUST NOT assume FrogNet gateways.
#   Routing and discovery are handled by mapInterfaces + transit autoconfig:
#     - Transit space: 10.253.253.0/24
#     - Discovery consumes FROGNET_UPSTREAM_SEEDS only
#
# What this script does:
#   - Choose a carrier underlay interface (typically eth1 in simulator)
#   - Determine local underlay IP on that carrier
#   - Determine remote underlay IP (override or neighbor)
#   - Create ham0 gretap carrier between underlay IPs
#   - Bring ham0 UP
#   - Optionally apply shaping via ham_tc.sh
#

. /usr/local/bin/mapInterfaces

TRANSPORT_CONF="/etc/frognet/transport.conf"
OVERRIDE_CONF="/etc/frognet/ham_override.conf"

die(){ echo "[HAM ERROR] $*" >&2; exit 1; }
log(){ echo "[HAM] $*"; }

[[ $EUID -eq 0 ]] || die "Must run as root"

# Carrier interface is wlan1Name slot by convention (overrideable)
RADIO_IFACE="${wlan1Name:-eth1}"

ip link show "$RADIO_IFACE" >/dev/null 2>&1 || die "Carrier iface $RADIO_IFACE not found"

LOCAL_UNDERLAY_IP="$(ip -4 addr show dev "$RADIO_IFACE" | awk '/inet /{print $2}' | head -n1 | cut -d/ -f1)"
[[ -n "$LOCAL_UNDERLAY_IP" ]] || die "No IPv4 on carrier iface $RADIO_IFACE"

log "carrier iface : $RADIO_IFACE"
log "local underlay: $LOCAL_UNDERLAY_IP"

REMOTE_UNDERLAY_IP=""

# Optional explicit overrides
if [[ -f "$OVERRIDE_CONF" ]]; then
  . "$OVERRIDE_CONF"
fi

if [[ -n "${HAM_REMOTE_UNDERLAY:-}" ]]; then
  REMOTE_UNDERLAY_IP="$HAM_REMOTE_UNDERLAY"
else
  # Best-effort from neighbor table
  REMOTE_UNDERLAY_IP="$(ip neigh show dev "$RADIO_IFACE" | awk '{print $1}' | grep -v "$LOCAL_UNDERLAY_IP" | head -n1)"
fi

[[ -n "$REMOTE_UNDERLAY_IP" ]] || die "Could not determine remote underlay peer on $RADIO_IFACE"

log "remote underlay: $REMOTE_UNDERLAY_IP"

# Recreate ham0 carrier
ip link del ham0 2>/dev/null

log "creating gretap ham0"
ip link add ham0 type gretap \
  local "$LOCAL_UNDERLAY_IP" \
  remote "$REMOTE_UNDERLAY_IP" \
  ttl 64

ip link set ham0 up

# Optional shaping (safe)
if command -v ham_tc.sh >/dev/null 2>&1; then
  ham_tc.sh \
  "${HAM_RATE_BPS:-1200}" \
  "${HAM_DELAY_MS:-600}" \
  "${HAM_JITTER_MS:-80}" \
  "${HAM_LOSS_PCT:-1.0}" \
  ham0
fi

log "ham0 carrier up"
ip -d link show ham0 | sed -n '1,30p'
exit 0
