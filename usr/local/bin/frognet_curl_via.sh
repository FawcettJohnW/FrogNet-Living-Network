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
# frognet_curl_via.sh — route-aware HTTP target resolution
#
# Source this file, then call:
#   _nh="$(frognet_next_hop "$ip")"
#   curl -H "Host: $ip" "http://${_nh}/path"
#
# Returns the next-hop gateway from the route table.
# If the IP is on a directly-connected subnet (no via), returns the IP itself.

frognet_next_hop() {
  local ip="$1" nh
  nh="$(ip route get "$ip" 2>/dev/null | awk '/via/{print $3; exit}')"
  echo "${nh:-$ip}"
}
