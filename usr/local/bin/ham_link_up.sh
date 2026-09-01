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
. /usr/local/bin/mapInterfaces

LOCAL_UNDERLAY="$1"
REMOTE_UNDERLAY="$2"
LOCAL_TRANSPORT_CIDR="$3"

[[ -n "$LOCAL_UNDERLAY" && -n "$REMOTE_UNDERLAY" && -n "$LOCAL_TRANSPORT_CIDR" ]] || {
  echo "usage: ham_link_up.sh <local_underlay_ip> <remote_underlay_ip> <local_transport_ip/30>" >&2
  exit 1
}

ip link del ham0 2>/dev/null || true

ip link add ham0 type gretap \
  local "$LOCAL_UNDERLAY" \
  remote "$REMOTE_UNDERLAY" \
  ttl 64

ip addr add "$LOCAL_TRANSPORT_CIDR" dev ham0
ip link set ham0 up
