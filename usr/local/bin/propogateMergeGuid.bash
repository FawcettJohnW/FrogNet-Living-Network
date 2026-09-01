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
set -e
# set -x

GUID="$1"
SENT_DIR="/etc/sentinels/merge_guids_seen"
mkdir -p "$SENT_DIR"

# Dedup locally
[[ -f "$SENT_DIR/$GUID" ]] && exit 0
date > "$SENT_DIR/$GUID"

# Neighbors via dnsmasq leases
while read -r _ _ ip _; do
  [[ "$ip" == 10.* ]] || continue
  curl -s --max-time 1 "http://$ip/runMerge.php?guid=$GUID" >/dev/null || true
done < /var/lib/misc/dnsmasq.leases

exit 0
