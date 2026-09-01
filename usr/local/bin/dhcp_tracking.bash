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
##############################################################
# /usr/local/bin/dhcp_tracking.bash
#
# Called by dnsmasq on lease events:
#   $1 = action: "add", "del", "old" (renew)
#   $2 = MAC address
#   $3 = IP address
#   $4 = hostname (optional)
#
# Only triggers merge on:
#   - "add" of a FrogNet IP (new peer joined)
#   - "del" of a FrogNet IP (peer left)
# Ignores:
#   - "old" (lease renewals)
#   - Non-FrogNet IPs
##############################################################

cmd="${1:-}"
mac="${2:-}"
ip="${3:-}"
hostname="${4:-}"

SENT_DIR="/etc/sentinels"
mkdir -p "$SENT_DIR"

/usr/local/bin/debugTag "dhcp_tracking: event cmd=$cmd mac=$mac ip=$ip hostname=$hostname"

# Only care about add/del, not old (renewals)
if [[ "$cmd" != "add" && "$cmd" != "del" ]]; then
  /usr/local/bin/debugTag "dhcp_tracking: ignoring cmd=$cmd (not add/del)"
  exit 0
fi

# Only care about FrogNet IPs (10.10x.y.z)
if [[ -z "$ip" || "$ip" != 10.10*.* ]]; then
  /usr/local/bin/debugTag "dhcp_tracking: ignoring non-FrogNet ip=$ip"
  exit 0
fi

# This is a FrogNet peer add or del - we need to sync.
#
# Detached, not exec'd. dnsmasq runs dhcp-script SYNCHRONOUSLY: it waits for this
# process before handling the next lease event. exec'ing runMerge held that slot
# for the whole merge, so a slow merge stalled DHCP for every other client on the
# segment. setsid + disown also survives dnsmasq reaping its script's process
# group, which a bare & does not reliably do.
/usr/local/bin/debugTag "dhcp_tracking: FrogNet peer $cmd ip=$ip — backgrounding runMerge"
setsid /usr/local/bin/runMerge.bash </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
