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
#
# process_host.sh — SAFE FrogNet discovery
#
# CRITICAL INVARIANT:
#   - Discovery MUST NOT replace an existing route unless it is absent OR non-viable.
#   - Viable == existing route can return frognet_echo AND getHosts.
#

process_host() {
  local seed_ip="$1"
  local depth="${2:-1}"

  is_frognet_ip "$seed_ip" || return

  [[ -n "${SEEN[$seed_ip]:-}" ]] && return
  SEEN["$seed_ip"]=1
  HOST_DISCOVERED=$((HOST_DISCOVERED+1))

  local E0 W0 W1
  E0=$(/usr/local/bin/getEth0Address  || echo "")
  W0=$(/usr/local/bin/getWlan0IP    || echo "")
  W1=$(/usr/local/bin/getWlan1IP    || echo "")

  local dev="" peer_ip="" echo_url=""

  if [[ -n "$W0" && "$seed_ip" == "$W0" ]]; then
    dev="$wlan0Name"
    peer_ip="${seed_ip%.*}.1"
    echo_url="http://${peer_ip}/frognet_echo.php"
  elif [[ -n "$W1" && "$seed_ip" == "$W1" ]]; then
    dev="$wlan1Name"
    peer_ip="${seed_ip%.*}.1"
    echo_url="http://${peer_ip}/frognet_echo.php"
  else
    dev="$eth0Name"
    peer_ip="$seed_ip"
    echo_url="http://${peer_ip}:8080/frognet_echo.php"
  fi

  [[ -z "$dev" || -z "$peer_ip" || -z "$echo_url" ]] && return

  # Semantic daemon must exist
  if ! check_remote_is_sem.bash "$peer_ip" 9009 ; then
    return
  fi

  # Echo must succeed
  local ans
  ans=$(/usr/local/bin/frognet_echo_cached.bash "$dev" "$echo_url" "$peer_ip" || echo "")
  is_fn_answer "$ans" || return

  local HOST HOST_PATH GW0 GW1
  IFS=', ' read -r HOST HOST_PATH GW0 GW1 <<< "$ans"
  is_frognet_ip "$HOST_PATH" || return

  local net
  net=$(range "$HOST_PATH")
  [[ -z "$net" ]] && return

  # ------------------------------------------------------------
  # ROUTE SAFETY CHECK (NEW, AUTHORITATIVE)
  # ------------------------------------------------------------
  if ip route show "$net" | grep -vq 'proto kernel'; then
    # Existing non-kernel route exists — validate it
    if /usr/bin/curl -fsS --max-time 20 "http://${HOST_PATH}/frognet_echo.php" >/dev/null 2>&1 \
       && /usr/bin/curl -fsS --max-time 20 "http://${HOST_PATH}/getHosts.php"  >/dev/null 2>&1
    then
      # Route is present AND viable → DO NOTHING
      :
    else
      # Route exists but is broken → replace
      ip route replace "$net" via "$seed_ip" dev "$dev" metric 21 || true
    fi
  else
    # No existing route → install
    ip route replace "$net" via "$seed_ip" dev "$dev" metric 21 || true
  fi

  /usr/local/bin/addHostAndPropogate.bash "$HOST" "$HOST_PATH" "" "$seed_ip" "$dev" || true
  ADDHOST_CALLS=$((ADDHOST_CALLS+1))

  (( depth >= MAX_DEPTH )) && return

  local kids
  kids=$(/usr/local/bin/getHosts_cached.bash "$HOST_PATH" || echo "")
  [[ -z "$kids" ]] && return

  local row child_ip
  while read -r row; do
    child_ip=$(echo "$row" | jq -r '.ip' || echo "")
    is_frognet_ip "$child_ip" || continue
    process_host "$child_ip" $((depth+1))
  done < <(echo "$kids" | jq -c '.[]' || echo "")
}
