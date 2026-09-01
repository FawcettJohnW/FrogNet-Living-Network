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
# Ensure FrogNet interface has the correct gateway + admin IPs at boot.
# Runs BEFORE transit boot to claim the interface.

CONF="/etc/frognet/gateways.conf"
[[ -f "$CONF" ]] || exit 0

source "$CONF"
[[ -n "$GATEWAY_IP" ]] || exit 0

# Derive admin alias from gateway IP:  10.x.y.1 -> 10.x.y.2
ADMIN_IP="${GATEWAY_IP%.*}.2"

# Resolve actual interface name
. /usr/local/bin/mapInterfaces
DEV="${eth0Name:-eth0}"

# Check if interface already has BOTH correct IPs
have_gw=$(ip -4 -o addr show dev "$DEV" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$GATEWAY_IP" || true)
have_admin=$(ip -4 -o addr show dev "$DEV" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$ADMIN_IP" || true)
[[ -n "$have_gw" && -n "$have_admin" ]] && exit 0

# Force the correct IPs
ip addr flush dev "$DEV" 2>/dev/null || true
ip addr add "${GATEWAY_IP}/24" dev "$DEV"
ip addr add "${ADMIN_IP}/24" dev "$DEV"
ip link set "$DEV" up

# Try to activate NM connection
nmcli conn up "$NETWORK_NAME" 2>/dev/null || true

exit 0
