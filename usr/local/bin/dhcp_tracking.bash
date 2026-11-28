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

# [FROGNET_PLANE_IS_TEN_V1] The FrogNet plane is 10/8, minus the two reserved
# planes: 10.253.* (transit /30s) and 10.254.* (chorus on-link). This used to
# read `!= 10.10*.*`, a glob that requires the literal prefix "10.10" and so
# ignored 10.250.250.*, 10.199.199.*, 10.170.170.*, 10.130.130.*, 10.123.123.*
# and 10.155.155.* -- i.e. nearly every /24 in the mesh. Every lease on those
# nodes was dropped here with "ignoring non-FrogNet ip=", so a peer joining or
# leaving the LAN never triggered the merge that was supposed to notice it.
# Matches the test sync_interfaces.sh and mapinterfaces.py already use.
if [[ -z "$ip" || "$ip" != 10.* || "$ip" == 10.253.* || "$ip" == 10.254.* ]]; then
  /usr/local/bin/debugTag "dhcp_tracking: ignoring non-FrogNet ip=$ip"
  exit 0
fi

# This is a FrogNet peer add or del - we need to sync.
#
# [LEASE_ASKS_THE_WATCHER_V1] One action: a request to the merge watcher, the same as the NetworkManager hook
# ([HOOK_ASKS_THE_WATCHER_V1], etc/NetworkManager/dispatcher.d/90-frognet-merge). frognet-merge-watcher.sh owns
# $REQ_DIR; the merge it forks runs in the watcher's own service and writes to the watcher's log.
#
# What stood here was `setsid /usr/local/bin/runMerge.bash </dev/null >/dev/null 2>&1 &`. setsid gives the merge its
# own session; it does NOT take it out of dnsmasq.service's control group, and dnsmasq.service is
# KillMode=control-group. So the merge a lease started was, to systemd, part of dnsmasq - and when that merge found
# something to tell dnsmasq (a node has joined: /etc/hosts and the forwarders changed), its own
# `systemctl restart dnsmasq` (discovery/live.py _apply_dnsmasq) stopped the unit the merge was running in. Found
# 2026-11-28, five clean-installed nodes, three of seven lease merges (H1 09:24:18, H2 09:44:46, H3 10:12:25), each
# the same in the journal:
#
#   10:10:22 dnsmasq-dhcp: DHCPACK(eth1) 10.73.73.40          <- the lease; this script forks runMerge 38263
#   10:10:55 systemd: Stopping dnsmasq.service ...             <- that merge's own restart of dnsmasq
#   10:11:27 "GET /propogateNotification.php?event=f7ec..."    <- the joining node says it is here
#   10:11:58 [MERGE-ACCUM] fire decision=set_runAgain reason=merge_running
#   10:12:25 systemd: dnsmasq.service: State 'final-sigterm' timed out. Killing.
#            Killing process 38263 (runMerge.bash) with signal SIGKILL.
#            Killing process 38553 (python3) ... 39596 (systemctl) with signal SIGKILL.
#
# The node served no DHCP and no DNS for 90 s, the merge died before its tail (no default-route fix, no resolv.conf,
# no propagation, mergePending left behind), the joining node's notification landed on a lock held by a merge
# that was already dead, and H3 never learned H5 was there. With >/dev/null on top, none of it reached a log.
#
# dnsmasq runs dhcp-script SYNCHRONOUSLY and waits for it: this writes one empty file and returns.
REQ_DIR="/run/frognet/merge_requests"
if { [[ -d "$REQ_DIR" ]] || { mkdir -p "$REQ_DIR" && chmod 1777 "$REQ_DIR"; }; } \
   && : > "${REQ_DIR}/dhcp-${cmd}-${ip}-$$"; then
  /usr/local/bin/debugTag "dhcp_tracking: FrogNet peer $cmd ip=$ip - merge requested from the watcher [LEASE_ASKS_THE_WATCHER_V1]"
else
  /usr/local/bin/debugTag "dhcp_tracking: FrogNet peer $cmd ip=$ip - FAILED to write a merge request in $REQ_DIR (the next lease event asks again) [LEASE_ASKS_THE_WATCHER_V1]"
fi
exit 0
