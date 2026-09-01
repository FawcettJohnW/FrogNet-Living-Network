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
# frognet_netdrop_watch.sh — bracket the exact moment/cause of the install network drop.
#
# Run this FIRST, in the background, then run the installer in another shell:
#     nohup /usr/local/bin/frognet_netdrop_watch.sh >/dev/null 2>&1 &
#     bash /usr/local/bin/frognet_install.sh ...        # reproduce the drop
#
# Everything lands on disk, so it survives the drop killing your SSH session:
#   /var/log/frognet_netdrop_state.log  — link/route/NM-device state, only on CHANGE
#   /var/log/frognet_netdrop_nm.log     — NetworkManager's own journal (the WHY)
#   /var/log/frognet_netdrop_merge.log  — runMerge stage log, if the dispatcher fires it
#
# After it drops, the last CHANGE block in state.log is timestamped; find that same
# timestamp in nm.log to read NM's stated reason (unmanaged / carrier / dispatcher /
# deactivating), and in merge.log to see if runMerge rewrote the default route then.
set -u
S=/var/log/frognet_netdrop_state.log
NMLOG=/var/log/frognet_netdrop_nm.log
ML=/var/log/frognet_netdrop_merge.log
: > "$S"; : > "$NMLOG"; : > "$ML"

# 1) NM's own journal, live (authoritative reason for any deactivation)
if command -v journalctl >/dev/null 2>&1; then
    ( journalctl -u NetworkManager -f -o short-precise --no-pager >>"$NMLOG" 2>&1 ) &
    echo "$!" > /tmp/frognet_netdrop_nm.pid
fi

# 2) runMerge stage log, if the dispatcher triggers it during the install
if [[ -f /var/log/frognet_runmerge.log ]]; then
    ( tail -F /var/log/frognet_runmerge.log >>"$ML" 2>&1 ) &
    echo "$!" > /tmp/frognet_netdrop_merge.pid
fi

snap() {
    echo "===== $(date '+%Y-%m-%dT%H:%M:%S.%3N') ====="
    echo "-- default route --";  ip route show default 2>/dev/null
    echo "-- addrs --";          ip -br -4 addr show 2>/dev/null | grep -vE '^lo '
    echo "-- nm devices --"
    if command -v nmcli >/dev/null 2>&1; then
        nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null | grep -vE '^lo:'
    fi
    echo "-- unmanaged conf.d --"; ls -1 /etc/NetworkManager/conf.d/ 2>/dev/null | grep -i unmanaged || true
}

# 3) state loop — log a full snapshot only when something changes (plus a 10s heartbeat)
prev=""; beat=0
while :; do
    cur="$(snap)"
    sig="$(printf '%s' "$cur" | grep -vE '^=====')"   # compare ignoring the timestamp line
    if [[ "$sig" != "$prev" ]]; then
        printf '%s\n\n' "$cur" >>"$S"
        prev="$sig"; beat=0
    else
        (( beat++ ))
        if (( beat >= 33 )); then printf '%s   [no change]\n\n' "$(date '+%H:%M:%S')" >>"$S"; beat=0; fi
    fi
    sleep 0.3
done
