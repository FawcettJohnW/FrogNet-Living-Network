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
# /usr/local/bin/frognet_transit_boot.sh
# Initialize transit links at boot with ARP discovery
# v2: Honors /etc/frognet/transit_exclude_interfaces

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

IP="/usr/sbin/ip"
PING="/bin/ping"
ARPING="/usr/sbin/arping"
DATE="/bin/date"
SLEEP="/bin/sleep"
AWK="/usr/bin/awk"
CUT="/usr/bin/cut"
SORT="/usr/bin/sort"
GREP="/usr/bin/grep"

LINK_AUTOCONFIG="/usr/local/bin/frognet_link_autoconfig.sh"
SET_BAUD="/usr/local/bin/frognet_set_transit_baud.sh"
NM_UNMANAGE="/usr/local/bin/frognet_nm_unmanage_if.sh"
# Companion script to NM_UNMANAGE that reverses its action — restores
# NM management of the iface, removes the iface from the persistent
# unmanaged-devices list, triggers reconnect.  May not exist on every
# node; rollback also has an inline fallback if it doesn't.
# Persistent NM unmanaged-devices file maintained by NM_UNMANAGE — used
NM_UNMANAGED_CONF="/etc/NetworkManager/conf.d/99-frognet-unmanaged.conf"

# Exclude file - interfaces listed here will NEVER get transit IPs
EXCLUDE_FILE="/etc/frognet/transit_exclude_interfaces"

# In /usr/local/bin/frognet_transit_boot.sh, near the top, before any
# reference to TRANSIT_MODE or TRANSIT_BAUD:
ARG1="${1:-${TRANSIT_MODE:-auto}}"
ARG2="${2:-${TRANSIT_BAUD:-full}}"

ts(){ $DATE '+%Y-%m-%d %H:%M:%S.%3N'; }
log(){ echo "[frognet-transit-boot][$(ts)][pid=$$] $*"; }

is_excluded() {
  local dev="$1"
  [[ -f "$EXCLUDE_FILE" ]] || return 1
  $GREP -qxF "$dev" "$EXCLUDE_FILE" 2>/dev/null
}

list_potential_transit_interfaces() {
  $IP -o link show | $AWK -F': ' '{print $2}' | $CUT -d'@' -f1 | while read -r dev; do
    # Skip loopback and virtual interfaces
    [[ "$dev" == "lo" ]] && continue
    [[ "$dev" == veth* ]] && continue
    [[ "$dev" == docker* ]] && continue
    [[ "$dev" == br-* ]] && continue
    [[ "$dev" == virbr* ]] && continue
    
    # Skip wireless interfaces
    [[ -d "/sys/class/net/$dev/wireless" ]] && continue
    
    # CRITICAL: Check exclude file FIRST
    if is_excluded "$dev"; then
      log "EXCLUDE: $dev is in $EXCLUDE_FILE"
      continue
    fi
    
    # If interface already has a transit IP, it's a candidate (for re-init)
    # BUT only if not excluded above
    if $IP -4 -o addr show dev "$dev" 2>/dev/null | $GREP -q '10\.253\.253\..*/30'; then
      echo "$dev"
      continue
    fi
    
    # Skip interfaces with no IP at all (not yet configured)
    if ! $IP -4 addr show dev "$dev" 2>/dev/null | $GREP -q 'inet '; then
      echo "$dev"
      continue
    fi
    
    # Skip interfaces that are FrogNet gateways (10.x.x.1/24)
    local primary_ip
    primary_ip="$($IP -4 addr show dev "$dev" 2>/dev/null | $AWK '/inet /{print $2}' | head -1)"
    if [[ "$primary_ip" == 10.*.*.1/24 ]]; then
      log "SKIP: $dev is a FrogNet gateway ($primary_ip)"
      continue
    fi
    
    # This interface is a candidate for transit
    echo "$dev"
  done | $SORT -u
}

# Rollback all the state changes configure_transit_interface made when
# we discover the wire isn't a FrogNet peer.  Idempotent — safe to call
# whether or not every step actually happened.  Logs every step.
# Called from the NO_PEER_FROGNET branch and from the final verify when
# transit setup didn't complete.
rollback_transit_interface() {
  local DEV="$1"
  local reason="${2:-no_frognet_peer}"
  log "[$DEV] ROLLBACK reason=$reason"

  # 1. Remove any 10.253.253.x/30 addresses we (or a prior failed run)
  #    bound to this iface.  There should be at most one.
  local addr
  while read -r addr; do
    [[ -n "$addr" ]] || continue
    log "[$DEV] ROLLBACK addr_del $addr"
    $IP addr del "$addr" dev "$DEV" 2>/dev/null || true
  done < <($IP -4 -o addr show dev "$DEV" 2>/dev/null \
           | $AWK '/inet 10\.253\.253\./{print $4}')

  # 2. Restore NM management: edit the persistent file and use nmcli.
  # [ONE_IMPLEMENTATION_V1] This used to prefer a companion
  # /usr/local/bin/frognet_nm_manage_if.sh and fall back to the code below.
  # That script has never shipped -- the tree has frognet_nm_unmanage_if.sh, the
  # OTHER direction -- so the guard was always false and this block is the only
  # rollback that has ever run. Keeping the branch made it look like the
  # maintained path lived somewhere else, and made the log line read as a
  # degraded mode when it was the normal one. There is one implementation.
  {
    log "[$DEV] ROLLBACK nm_manage"
    # Strip interface-name:$DEV from the unmanaged-devices list (handles
    # leading-comma, trailing-comma, and only-element cases).
    if [[ -f "$NM_UNMANAGED_CONF" ]]; then
      /bin/sed -i \
        -e "s/,interface-name:${DEV}\b//g" \
        -e "s/interface-name:${DEV},//g" \
        -e "s/^unmanaged-devices=interface-name:${DEV}\$/unmanaged-devices=/" \
        "$NM_UNMANAGED_CONF" \
        && log "[$DEV] ROLLBACK stripped $DEV from $NM_UNMANAGED_CONF" \
        || log "[$DEV] ROLLBACK sed failed on $NM_UNMANAGED_CONF"
      /bin/systemctl reload NetworkManager 2>/dev/null || true
    fi
    /usr/bin/nmcli device set "$DEV" managed yes 2>/dev/null \
      && log "[$DEV] ROLLBACK nmcli set managed=yes" \
      || log "[$DEV] ROLLBACK nmcli set managed=yes failed"
    /usr/bin/nmcli device connect "$DEV" 2>/dev/null \
      && log "[$DEV] ROLLBACK nmcli connect" \
      || log "[$DEV] ROLLBACK nmcli connect failed (may need to wait for NM)"
  }

  log "[$DEV] ROLLBACK complete"
}

configure_transit_interface() {
  local DEV="$1" BAUD="$2"
  
  log "=== Configuring $DEV (baud=$BAUD) ==="
  
  # Double-check exclude list before configuring
  if is_excluded "$DEV"; then
    log "[$DEV] SKIP: Interface is in exclude list"
    return 1
  fi
  
  [[ -d "/sys/class/net/$DEV" ]] || { log "SKIP $DEV: not found"; return 1; }
  
  # Step 1: Bring up and unmanage
  [[ -x "$NM_UNMANAGE" ]] && "$NM_UNMANAGE" "$DEV" 2>&1 | while read -r line; do log "[$DEV] nm: $line"; done
  $IP link set "$DEV" up || { log "[$DEV] FAIL: Cannot bring up"; return 1; }
  
  for i in 1 2 3 4 5; do
    state="$(cat /sys/class/net/$DEV/operstate 2>/dev/null)"
    log "[$DEV] Link state check $i: $state"
    [[ "$state" == "up" || "$state" == "unknown" ]] && break
    $SLEEP 1
  done
  
  state="$(cat /sys/class/net/$DEV/operstate 2>/dev/null)"
  [[ "$state" != "up" && "$state" != "unknown" ]] && { log "[$DEV] SKIP: operstate=$state"; return 1; }
  
  # Step 2: Temp link-local IP
  OUR_MAC="$(cat /sys/class/net/$DEV/address 2>/dev/null)"
  MAC_LAST2="${OUR_MAC: -5}"
  OCTET3=$(( 16#${MAC_LAST2:0:2} % 254 + 1 ))
  OCTET4=$(( 16#${MAC_LAST2:3:2} % 254 + 1 ))
  TEMP_IP="169.254.${OCTET3}.${OCTET4}"
  log "[$DEV] Temporary IP: $TEMP_IP"
  $IP addr add "${TEMP_IP}/16" dev "$DEV" 2>/dev/null || true
  
  # Step 3: ARP discovery
  log "[$DEV] ARP discovery..."
  PEER_MAC=""
  for i in $(seq 1 10); do
    [[ -x "$ARPING" ]] && {
      $ARPING -c 1 -w 1 -I "$DEV" 169.254.255.255 >/dev/null 2>&1 || true
      $ARPING -c 1 -w 1 -I "$DEV" -B 255.255.255.255 >/dev/null 2>&1 || true
    }
    $PING -c 1 -W 1 -b -I "$DEV" 169.254.255.255 >/dev/null 2>&1 || true
    $PING -6 -c 1 -W 1 -I "$DEV" ff02::1 >/dev/null 2>&1 || true
    
    PEER_MAC="$($IP neigh show dev "$DEV" 2>/dev/null | $GREP -v FAILED | $GREP lladdr | head -1 | $AWK '{for(i=1;i<=NF;i++) if($i=="lladdr") print $(i+1)}')"
    [[ -n "$PEER_MAC" ]] && { log "[$DEV] Peer MAC: $PEER_MAC"; break; }
    $SLEEP 1
  done
  
  $IP addr del "${TEMP_IP}/16" dev "$DEV" 2>/dev/null || true
  [[ -z "$PEER_MAC" ]] && log "[$DEV] WARN: No peer MAC discovered"
  
  # Step 4: Link autoconfig
  if [[ -x "$LINK_AUTOCONFIG" ]]; then
    log "[$DEV] Running autoconfig..."
    local autoconfig_out
    autoconfig_out="$("$LINK_AUTOCONFIG" "$DEV" 2>&1)"
    local ac_rc=$?

    # Log all output (stderr + stdout)
    while IFS= read -r line; do log "[$DEV] autoconfig: $line"; done <<<"$autoconfig_out"

    if [[ $ac_rc -ne 0 ]]; then
      log "[$DEV] autoconfig exited rc=$ac_rc"
    fi

    # Eval the exports from stdout
    eval "$(echo "$autoconfig_out" | $GREP '^export ')" || true

    if [[ -n "${FROGNET_TRANSIT_LOCAL:-}" ]]; then
      log "[$DEV] Transit: local=$FROGNET_TRANSIT_LOCAL peer=${FROGNET_TRANSIT_PEER:-}"
    fi

    # Step 4b: Install route to peer's FrogNet subnet via the transit peer.
    # [TRANSIT_NO_SHADOW_V1] Skip the install if $DEV is already on
    # $peer_subnet via a kernel-connected route — installing a via-route
    # over the kernel route creates a duplicate ("shadow") entry that
    # confuses conntrack/path-selection and is exactly the class of bug
    # frognet_up_clean.sh's prune_shadow_connected_routes() exists to
    # clean up. Don't create the mess in the first place.
    if [[ -n "${FROGNET_TRANSIT_PEER_FROGNET:-}" && -n "${FROGNET_TRANSIT_PEER:-}" ]]; then
      local peer_subnet="${FROGNET_TRANSIT_PEER_FROGNET%.*}.0/24"
      if $IP -4 route show "$peer_subnet" dev "$DEV" proto kernel 2>/dev/null | $GREP -q .; then
        log "[$DEV] ROUTE_SKIP_SHADOW net=$peer_subnet — already kernel-connected on $DEV"
      else
        log "[$DEV] ROUTE_INSTALL net=$peer_subnet via=$FROGNET_TRANSIT_PEER dev=$DEV metric=22"
        $IP route replace "$peer_subnet" via "$FROGNET_TRANSIT_PEER" dev "$DEV" metric 22 onlink || {
          log "[$DEV] ROUTE_FAIL net=$peer_subnet via=$FROGNET_TRANSIT_PEER dev=$DEV"
        }
      fi
    else
      log "[$DEV] NO_PEER_FROGNET: peer echo did not return a FrogNet subnet — rolling back"
      rollback_transit_interface "$DEV" "no_frognet_peer"
      return 1
    fi
  fi
  
  # Step 5: Clear any existing traffic shaping (always start fresh)
  log "[$DEV] Clearing any existing traffic shaping..."
  /sbin/tc qdisc del dev "$DEV" root 2>/dev/null || true
  /sbin/tc qdisc del dev "$DEV" ingress 2>/dev/null || true
  $IP link set dev "$DEV" mtu 1500 2>/dev/null || true
  $IP link set dev "$DEV" txqueuelen 1000 2>/dev/null || true
  
  # Step 6: Apply baud shaping only if explicitly requested
  if [[ "$BAUD" != "full" && "$BAUD" != "0" && "$BAUD" != "" ]]; then
    if [[ -x "$SET_BAUD" ]]; then
      log "[$DEV] Applying baud rate shaping: $BAUD"
      "$SET_BAUD" "$DEV" "$BAUD" 2>&1 | while read -r line; do log "[$DEV] baud: $line"; done
    else
      log "[$DEV] WARN: $SET_BAUD not found, cannot apply baud shaping"
    fi
  else
    log "[$DEV] No baud shaping requested (full speed)"
  fi
  
  # Step 7: Verify — real success requires both a transit /30 bound AND
  # a confirmed FrogNet peer on the other end.  Mere presence of a
  # 10.253.253.x address isn't enough — the autoconfig binds it
  # tentatively during discovery, so finding it here just means "we
  # tried," not "we succeeded."  If we don't have a FrogNet peer
  # confirmed (FROGNET_TRANSIT_PEER_FROGNET unset), the NO_PEER_FROGNET
  # branch above already rolled back, but cover the case where
  # autoconfig exits without setting it for any other reason.
  TRANSIT_IP="$($IP -4 -o addr show dev "$DEV" 2>/dev/null | $GREP '10\.253\.253\.' | head -1 | $AWK '{print $4}')"
  if [[ -n "$TRANSIT_IP" && -n "${FROGNET_TRANSIT_PEER_FROGNET:-}" ]]; then
    log "[$DEV] SUCCESS: Transit IP=$TRANSIT_IP peer_frognet=$FROGNET_TRANSIT_PEER_FROGNET"
    return 0
  fi
  if [[ -n "$TRANSIT_IP" ]]; then
    log "[$DEV] INCOMPLETE: transit IP $TRANSIT_IP bound but no FrogNet peer confirmed — rolling back"
    rollback_transit_interface "$DEV" "verify_no_peer_frognet"
  else
    log "[$DEV] INCOMPLETE: No transit IP yet"
  fi
  return 1
}

# ============================================================
# MAIN
# ============================================================

# ARG1="${1:-}"
BAUD="${2:-full}"

[[ -z "$ARG1" ]] && { echo "Usage: $0 <interface|auto> [baud]"; exit 1; }

# Show exclude list at startup
if [[ -f "$EXCLUDE_FILE" ]]; then
  log "Exclude list: $(cat "$EXCLUDE_FILE" | tr '\n' ' ')"
else
  log "No exclude file at $EXCLUDE_FILE"
fi

INTERFACES_TO_CONFIGURE=()
if [[ "$ARG1" == "auto" ]]; then
  log "AUTO mode: discovering interfaces"
  while read -r dev; do [[ -n "$dev" ]] && INTERFACES_TO_CONFIGURE+=("$dev"); done < <(list_potential_transit_interfaces)
  log "Found ${#INTERFACES_TO_CONFIGURE[@]}: ${INTERFACES_TO_CONFIGURE[*]}"
else
  # Even for explicit interface, check exclude list
  if is_excluded "$ARG1"; then
    log "FATAL: Interface $ARG1 is in exclude list $EXCLUDE_FILE"
    exit 1
  fi
  [[ -d "/sys/class/net/$ARG1" ]] || { log "FATAL: Interface not found: $ARG1"; exit 1; }
  INTERFACES_TO_CONFIGURE=("$ARG1")
fi

[[ ${#INTERFACES_TO_CONFIGURE[@]} -eq 0 ]] && { log "No interfaces to configure"; exit 0; }

SUCCESS_COUNT=0
FAIL_COUNT=0
for DEV in "${INTERFACES_TO_CONFIGURE[@]}"; do
  configure_transit_interface "$DEV" "$BAUD" && ((SUCCESS_COUNT++)) || ((FAIL_COUNT++))
done

log "DONE: $SUCCESS_COUNT succeeded, $FAIL_COUNT incomplete"
exit 0
