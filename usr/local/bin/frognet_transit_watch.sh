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
# /usr/local/bin/sync_interfaces.sh
# FrogNet Discovery (v6 - no ICMP ping; frognet_echo is the aliveness gate,
#                         frognet_alive.bash for RTT measurement)

set -x
exit 0

log(){ echo "[sync_interfaces][$(date '+%Y-%m-%d %H:%M:%S.%3N')][pid=$$] $*"; }
log "ENTER"
trap 'rc=$?; log "EXIT rc=$rc"; exit $rc' EXIT

IP="/usr/sbin/ip"
CURL="/usr/bin/curl"
AWK="/usr/bin/awk"
CUT="/usr/bin/cut"
HEAD="/usr/bin/head"
SORT="/usr/bin/sort"
TR="/usr/bin/tr"
XARGS="/usr/bin/xargs"
JQ="/usr/bin/jq"
GREP="/usr/bin/grep"
ALIVE="/usr/local/bin/frognet_alive.bash"

ADD_HOST="/usr/local/bin/addHostAndPropogate.bash"
SUBNET="/usr/local/bin/convertToSubnetRange"
MERGE="/usr/local/bin/mergeHostsAndResolv.bash"
DISCOVERY_CACHE="/usr/local/bin/frognet_discovery_cache.sh"
LEASES="/var/lib/misc/dnsmasq.leases"

declare -A DISCOVERED

SENT_DIR="/etc/sentinels"
EXPECTED="${SENT_DIR}/expected_routes"
mkdir -p "$SENT_DIR"
: > "$EXPECTED"

MAX_DEPTH=2
ECHO_TIMEOUT="${FROGNET_DISCOVERY_ECHO_TIMEOUT:-15}"
GETHOSTS_TIMEOUT=15
MET_TMP=7  # metric for the transient /32 probe route.  Distinct from
           # frognet_route's ADMIN_ALIAS_METRIC=5 (committer-owned .2
           # admin aliases) and from sync_interfaces.sh's MET_TMP=6 so
           # each subsystem can sweep its own /32s without touching
           # another subsystem's routes.
MET_NET=22

# Don't use common roots by default
DEFAULT_COMMON_ROOTS=""
COMMON_ROOTS="${FROGNET_COMMON_ROOTS:-$DEFAULT_COMMON_ROOTS}"

# Build list of local IPs and local subnets
mapfile -t LOCAL_IPS < <($IP -4 -o addr show | $AWK '{print $4}' | $CUT -d/ -f1 | $SORT -u)
declare -A LOCAL_SUBNETS
for lip in "${LOCAL_IPS[@]}"; do
  prefix="${lip%.*}"
  LOCAL_SUBNETS["$prefix"]=1
done

is_local_ip(){ for l in "${LOCAL_IPS[@]}"; do [[ "$1" == "$l" ]] && return 0; done; return 1; }
is_ip(){ [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
is_10x(){ [[ "$1" == 10.* ]]; }
is_on_local_subnet(){ local prefix="${1%.*}"; [[ -n "${LOCAL_SUBNETS[$prefix]}" ]]; }

# ── Aliveness + echo: replaces ping_ok_dev_fast + frognet_echo_80 ──
# If echo succeeds, the host is alive AND we have identity. No ping needed.
# Try 8080 FIRST: port 80 is intercepted by iptables OUTPUT DNAT
# (10/8:80 → 127.0.0.1) which returns LOCAL identity for remote IPs.
frognet_echo_8080() {
  $CURL -fsS --connect-timeout 2 --max-time "$ECHO_TIMEOUT" -H "Host: $1" "http://$1:8080/frognet_echo.php" | $TR -d '\r\n'
}

frognet_echo_80() {
  $CURL -fsS --connect-timeout 2 --max-time "$ECHO_TIMEOUT" -H "Host: $1" "http://$1/frognet_echo.php" | $TR -d '\r\n'
}

gethosts_8080() {
  $CURL -fsS --connect-timeout 2 --max-time "$GETHOSTS_TIMEOUT" -H "Host: $1" "http://$1:8080/getHosts.php"
}

gethosts_80() {
  $CURL -fsS --connect-timeout 2 --max-time "$GETHOSTS_TIMEOUT" -H "Host: $1" "http://$1/getHosts.php"
}

is_valid_echo() {
  [[ -n "$1" ]] || return 1
  [[ "$($AWK -F',' '{print NF-1}' <<<"$1")" -ge 3 ]] || return 1
  IFS=',' read -r _ path _ _ <<<"$1"
  path="$($XARGS <<<"$path")"
  is_ip "$path" && is_10x "$path"
}

net_of_ip_or_path() { "$SUBNET" "$1"; }

list_active_devs() {
  $IP -o link show | $AWK -F': ' '{print $2}' | $CUT -d'@' -f1 | while read -r d; do
    [[ "$d" == "lo" ]] && continue
    [[ "$d" == veth* ]] && continue
    [[ "$d" == docker* ]] && continue
    [[ "$d" == br-* ]] && continue
    [[ "$d" == virbr* ]] && continue
    $IP link show dev "$d" | $GREP -q "state UP" || continue
    echo "$d"
  done | $SORT -u
}

seed_from_dev_ip() {
  local dev="$1" cidr ip4
  cidr="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1)"
  [[ -n "$cidr" ]] || return 0
  [[ "$cidr" == 10.*/* ]] || return 0
  ip4="${cidr%/*}"
  [[ "$ip4" == 10.253.253.* ]] && return 0
  echo "$ip4" | $AWK -F. '{print $1"."$2"."$3".1"}'
}

get_transit30_ip() {
  local dev="$1"
  $IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $GREP '^10\.253\.253\..*/30$' | $HEAD -n1 | $CUT -d/ -f1
}

infer_peer_from_local30() {
  local local_ip="$1" last base peer
  last="$(echo "$local_ip" | $AWK -F. '{print $4}')"
  base="$(( (last / 4) * 4 ))"
  [[ "$last" == "$((base+1))" ]] && peer="$((base+2))" || peer="$((base+1))"
  echo "10.253.253.${peer}"
}

ensure_tmp_route_to_ip() {
  local dev="$1" via_ip="$2" ip="$3" anchor_ip="$4"
  [[ -n "$dev" ]] || return 1
  is_ip "$ip" || return 1
  if [[ -n "$via_ip" ]]; then
    local gw="${anchor_ip:-$via_ip}"
    log "ROUTE_TMP ip=$ip/32 via=$via_ip gw=$gw dev=$dev"
    /usr/sbin/ip route replace "$ip/32" dev "$dev" via "$gw" metric "$MET_TMP"
  else
    log "ROUTE_TMP ip=$ip/32 dev=$dev"
    $IP route replace "$ip/32" dev "$dev" metric "$MET_TMP" || true
  fi
}

delete_tmp_route_to_ip() {
  local ip="$1"
  is_ip "$ip" || return 0
  $IP route del "$ip/32" || true
}

# ── RTT measurement via frognet_alive (FNW1 → HTTP fallback) ──
# Replaces the old "3 pings" RTT measurement.
# Returns integer ms on stdout, or 9999 on failure.
measure_rtt() {
  local target="$1"
  local result ms
  result=$("$ALIVE" "$target" 3 2>/dev/null) || true
  if [[ -n "$result" ]]; then
    ms="${result%%|*}"
    # Truncate to integer
    ms="${ms%%.*}"
    [[ -n "$ms" && "$ms" != "0" ]] && { echo "$ms"; return; }
  fi
  echo "9999"
}

install_route_for_hostpath() {
  local host_path="$1" dev="$2" next_hop="$3" net
  
  # Validate next_hop
  if ! is_ip "$next_hop"; then
    log "ROUTE_SKIP host_path=$host_path reason=invalid_next_hop($next_hop)"
    return 1
  fi
  
  net="$(net_of_ip_or_path "$host_path" || true)"
  [[ -n "$net" ]] || return 0
  
  # Duplicate check: same via+dev already installed?
  if $IP route show "$net" | $GREP -q "via ${next_hop} dev ${dev} "; then
    log "ROUTE_DUPLICATE net=$net via=$next_hop dev=$dev (skipping)"
    return 0
  fi
  
  # Measure RTT through this path via frognet_alive
  local rtt_ms
  rtt_ms=$(measure_rtt "$host_path")
  
  # Find best existing FrogNet route for this net (metric < 600)
  local best_metric="" best_via="" best_dev=""
  local eline emetric
  while IFS= read -r eline; do
    [[ -n "$eline" ]] || continue
    emetric="$($AWK '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}' <<<"$eline")"
    [[ -n "$emetric" ]] || continue
    (( emetric >= 600 )) && continue
    if [[ -z "$best_metric" ]] || (( emetric < best_metric )); then
      best_metric="$emetric"
      best_via="$($AWK '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$eline")"
      best_dev="$($AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$eline")"
    fi
  done < <( $IP route show "$net" )
  
  # Decide metric for new route
  # RULE: non-transit next_hops always beat transit next_hops
  # (10.253.253.x or anything on a FROGNET_TRANSIT_DEVS interface),
  # because the transit link has TC shaping applied.
  # RTT is only used to break ties between paths of the same class.
  local new_metric
  local new_is_transit=0
  [[ "$next_hop" == 10.253.253.* ]] && new_is_transit=1
  [[ " $FROGNET_TRANSIT_DEVS " == *" $dev "* ]] && new_is_transit=1
  
  if [[ -z "$best_metric" ]]; then
    # No existing FrogNet route — install as primary
    new_metric=$MET_NET
    log "ROUTE_COMMIT net=$net via=$next_hop dev=$dev metric=$new_metric rtt=${rtt_ms}ms transit=$new_is_transit (primary)"
  else
    # Determine if existing primary is transit
    local existing_is_transit=0
    [[ "$best_via" == 10.253.253.* ]] && existing_is_transit=1
    [[ " $FROGNET_TRANSIT_DEVS " == *" $best_dev "* ]] && existing_is_transit=1
    
    # Measure existing primary's RTT via frognet_alive
    local primary_rtt_ms=9999
    if [[ -n "$best_dev" ]]; then
      primary_rtt_ms=$(measure_rtt "$host_path")
    fi
    
    # Decide: should new path become primary?
    local promote=0
    if (( new_is_transit == 0 && existing_is_transit == 1 )); then
      # New is non-transit, existing is transit — always promote
      promote=1
      log "ROUTE_PROMOTE net=$net new_via=$next_hop (non-transit) beats via=$best_via (transit)"
    elif (( new_is_transit == 1 && existing_is_transit == 0 )); then
      # New is transit, existing is non-transit — always backup
      promote=0
    elif (( rtt_ms < primary_rtt_ms )); then
      # Same class — use RTT to decide
      promote=1
      log "ROUTE_PROMOTE net=$net new_via=$next_hop rtt=${rtt_ms}ms beats via=$best_via rtt=${primary_rtt_ms}ms"
    fi
    
    if (( promote )); then
      _demote_frognet_routes "$net"
      new_metric=$MET_NET
    else
      # Add as backup
      local max_metric=$best_metric
      while IFS= read -r eline; do
        [[ -n "$eline" ]] || continue
        emetric="$($AWK '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}' <<<"$eline")"
        [[ -n "$emetric" ]] || continue
        (( emetric >= 600 )) && continue
        (( emetric > max_metric )) && max_metric=$emetric
      done < <( $IP route show "$net" )
      new_metric=$(( max_metric + 1 ))
      log "ROUTE_BACKUP net=$net via=$next_hop dev=$dev metric=$new_metric rtt=${rtt_ms}ms transit=$new_is_transit (primary via=$best_via transit=$existing_is_transit)"
    fi
  fi
  
  # [SMART_ONLINK_V1] Onlink is needed only when the kernel can't
  # resolve $next_hop through an existing route on $dev.  If a
  # scope-link / proto-kernel route on $dev already covers
  # $next_hop, the kernel does recursive lookup naturally and
  # onlink would just hide misconfiguration if that covering
  # route ever disappears.
  #
  # Check: scan routes on $dev for a non-via entry whose
  # destination contains $next_hop.
  local onlink_flag="onlink"
  local nh_int
  nh_int="$(_ip_to_int "$next_hop" 2>/dev/null || true)"
  if [[ -n "$nh_int" ]]; then
      while IFS= read -r _r; do
          [[ -n "$_r" ]] || continue
          # Skip routes with their own via — they're not connected.
          [[ "$_r" == *" via "* ]] && continue
          # First field is dest (cidr or bare ip).
          local _dest="${_r%% *}"
          local _net="${_dest%/*}"
          local _plen="${_dest#*/}"
          [[ "$_plen" == "$_dest" ]] && _plen=32
          local _net_int
          _net_int="$(_ip_to_int "$_net" 2>/dev/null || true)"
          [[ -n "$_net_int" ]] || continue
          local _mask=$(( 0xFFFFFFFF & (0xFFFFFFFF << (32 - _plen)) ))
          if (( (nh_int & _mask) == (_net_int & _mask) )); then
              onlink_flag=""
              break
          fi
      done < <($IP -o -4 route show dev "$dev" 2>/dev/null)
  fi

  # Install — use "add" to coexist with other metrics; fall back to "replace"
  # if the exact metric already exists (shouldn't happen due to duplicate check)
  if [[ -n "$onlink_flag" ]]; then
      $IP route add "$net" dev "$dev" via "$next_hop" metric "$new_metric" $onlink_flag \
        || $IP route replace "$net" dev "$dev" via "$next_hop" metric "$new_metric" $onlink_flag || true
      echo "$net via $next_hop dev $dev metric $new_metric $onlink_flag" >> "$EXPECTED"
  else
      $IP route add "$net" dev "$dev" via "$next_hop" metric "$new_metric" \
        || $IP route replace "$net" dev "$dev" via "$next_hop" metric "$new_metric" || true
      echo "$net via $next_hop dev $dev metric $new_metric" >> "$EXPECTED"
  fi
}

# Convert a dotted-quad to a 32-bit integer.  Returns empty on
# malformed input.  Used by [SMART_ONLINK_V1].
_ip_to_int() {
    local ip="$1" a b c d
    [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    a="${BASH_REMATCH[1]}"; b="${BASH_REMATCH[2]}"
    c="${BASH_REMATCH[3]}"; d="${BASH_REMATCH[4]}"
    (( a < 256 && b < 256 && c < 256 && d < 256 )) || return 1
    echo $(( (a << 24) | (b << 16) | (c << 8) | d ))
}

# Bump every FrogNet route (metric < 600) for $net up by 1.
# Processes highest metric first to avoid collisions during renumbering.
_demote_frognet_routes() {
  local net="$1"
  local -a tuples=()
  local line m v d onk
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    m="$($AWK '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}' <<<"$line")"
    [[ -n "$m" ]] || continue
    (( m >= 600 )) && continue
    v="$($AWK '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$line")"
    d="$($AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$line")"
    onk=""; $GREP -q 'onlink' <<<"$line" && onk="onlink"
    tuples+=( "$m|$v|$d|$onk" )
  done < <( $IP route show "$net" )

  local sorted
  sorted="$( printf '%s\n' "${tuples[@]}" | $SORT -t'|' -k1 -rn )"

  while IFS='|' read -r m v d onk; do
    [[ -n "$m" ]] || continue
    local new_m=$(( m + 1 ))
    $IP route del "$net" via "$v" dev "$d" metric "$m" || true
    $IP route add "$net" via "$v" dev "$d" metric "$new_m" ${onk} || true
    log "ROUTE_DEMOTE net=$net via=$v dev=$d metric=${m}->${new_m}"
  done <<< "$sorted"
}

restore_connected_10x_24_route_for_dev() {
  local dev="$1" cidr ip4 net
  cidr="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1)"
  [[ -n "$cidr" ]] || return 0
  [[ "$cidr" == */24 ]] || return 0
  ip4="${cidr%/*}"
  is_10x "$ip4" || return 0
  [[ "$ip4" == 10.253.253.* ]] && return 0
  net="$(net_of_ip_or_path "$ip4" || true)"
  [[ -n "$net" ]] || return 0
  if ! $IP route show "$net" dev "$dev" | $GREP -q .; then
    log "RESTORE_CONNECTED net=$net dev=$dev src=$ip4"
    $IP route replace "$net" dev "$dev" proto kernel scope link src "$ip4" metric 100 || true
  fi
}

declare -A SEEN
declare -A TRIED
QUEUE=()

enqueue() {
  local dev="$1" ip="$2" depth="$3" via_ip="$4" anchor_ip="$5"
  [[ -n "$dev" ]] || return
  is_ip "$ip" || return
  is_10x "$ip" || return
  is_local_ip "$ip" && return
  
  [[ -n "${TRIED[${dev}|${via_ip}|${ip}]}" ]] && return
  
  # Pass via_ip as voucher — if a live host reported this peer in its
  # getHosts, that's a vouch that the peer is reachable through it.
  if [[ "$ip" == 10.253.253.* ]]; then
    : # don't check cache for transit IPs
  elif [[ -x "$DISCOVERY_CACHE" ]] && "$DISCOVERY_CACHE" should_skip "$ip" "$via_ip"; then
    log "SKIP_CACHED ip=$ip via=$via_ip (recently failed)"
    return
  fi
  
  local key="${dev}|${ip}|${via_ip}"
  [[ -n "${SEEN[$key]}" ]] && return
  SEEN["$key"]=1
  QUEUE+=("$dev|$ip|$depth|$via_ip|$anchor_ip")
  log "QUEUE_ADD dev=$dev ip=$ip depth=$depth via=$via_ip qsize=${#QUEUE[@]}"
}

process_one() {
  local dev="$1" ip="$2" depth="$3" via_ip="$4" anchor_ip="$5"
  log "PROCESS dev=$dev ip=$ip depth=$depth via=$via_ip"

  # Already found on another interface — skip
  if [[ -n "${DISCOVERED[$ip]+x}" ]]; then
      log "SKIP_DISCOVERED ip=$ip dev=$dev (already found)"
      return 0
  fi
  
  local tried_key="${dev}|${via_ip}|${ip}"
  if [[ -n "${TRIED[$tried_key]}" ]]; then
    log "SKIP_TRIED dev=$dev ip=$ip via=$via_ip"
    return
  fi
  
  restore_connected_10x_24_route_for_dev "$dev" || true
  ensure_tmp_route_to_ip "$dev" "$via_ip" "$ip" "$anchor_ip"
  
  # ── Gate: frognet_echo IS the aliveness test ──
  # No separate ping.  If echo succeeds, host is alive AND we have identity.
  # If echo fails, host is unreachable or not a FrogNet.
  local echo_line
  echo_line="$(frognet_echo_8080 "$ip")" || true
  [[ -z "$echo_line" ]] && { echo_line="$(frognet_echo_80 "$ip")" || true; }
  
  if ! is_valid_echo "$echo_line"; then
    log "FAIL_ECHO dev=$dev ip=$ip via=$via_ip (no ping — echo is the gate)"
    TRIED[$tried_key]=1
    [[ -x "$DISCOVERY_CACHE" ]] && "$DISCOVERY_CACHE" fail "$ip" "$dev" || true
    delete_tmp_route_to_ip "$ip"
    return
  fi
  
  IFS=',' read -r host host_path _ _ <<<"$echo_line"
  host="$($XARGS <<<"$host")"
  host_path="$($XARGS <<<"$host_path")"

  # ── DNAT loopback guard ──
  # If iptables OUTPUT DNAT redirects 10/8:80 → 127.0.0.1:80, the local
  # web server answers with OUR identity.  Detect this: if echo returned
  # a host_path that is one of our own IPs, but the target IP is NOT us,
  # the response is poisoned.  Reject it and retry on 8080.
  if ! is_local_ip "$ip" && is_local_ip "$host_path"; then
    log "DNAT_LOOPBACK dev=$dev ip=$ip echo_returned_local=$host_path — rejecting port-80 echo"
    echo_line="$(frognet_echo_8080 "$ip")" || true
    if ! is_valid_echo "$echo_line"; then
      log "FAIL_ECHO dev=$dev ip=$ip via=$via_ip (port-80 DNAT loopback, port-8080 also failed)"
      TRIED[$tried_key]=1
      [[ -x "$DISCOVERY_CACHE" ]] && "$DISCOVERY_CACHE" fail "$ip" "$dev" || true
      delete_tmp_route_to_ip "$ip"
      return
    fi
    IFS=',' read -r host host_path _ _ <<<"$echo_line"
    host="$($XARGS <<<"$host")"
    host_path="$($XARGS <<<"$host_path")"
  fi
  
  # CRITICAL: next_hop must be the IP we actually contacted (or via_ip if routed)
  # It must NEVER be the host_path (which is the remote gateway's own subnet IP)
  local next_hop
  if [[ -n "$via_ip" ]]; then
    next_hop="$via_ip"
  else
    next_hop="$ip"
  fi
  
  local anchor_use="${anchor_ip:-$next_hop}"
  local local_ip
  local_ip="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1 | $CUT -d/ -f1)"
  
  log "OK dev=$dev ip=$ip host=$host host_path=$host_path next_hop=$next_hop"
  
  # Only cache if we contacted this IP directly (not via routing)
  # The cache stores contactable IPs, not host_paths
  if [[ -z "$via_ip" ]] && is_on_local_subnet "$ip"; then
    [[ -x "$DISCOVERY_CACHE" ]] && "$DISCOVERY_CACHE" success "$ip" "$local_ip" "$dev" "$host" "$host_path" || true
  fi
  
  "$ADD_HOST" "$host" "$host_path" "" "$anchor_use" "$dev" || true
  DISCOVERED[$ip]=1
  install_route_for_hostpath "$host_path" "$dev" "$next_hop" || true
  delete_tmp_route_to_ip "$ip"
  
  (( depth > MAX_DEPTH )) && return
  
  # Get children via the host's FrogNet address (host_path), not the
  # contactable/DHCP address ($ip).  The route to host_path was just
  # installed above, so it is reachable.
  local kids
  kids="$(gethosts_8080 "$host_path")" || true
  [[ -z "$kids" ]] && { kids="$(gethosts_80 "$host_path")" || true; }
  [[ -z "$kids" ]] && return
  
  while read -r child; do
    [[ -n "$child" ]] || continue
    # Skip if child is the same as what we just discovered
    [[ "$child" == "$host_path" ]] && continue
    # Children route via the same wire-level next_hop we used to reach
    # the parent.  From this node, everything behind the transit goes
    # via the transit-anchor peer — intermediate hops are that peer's
    # problem.
    enqueue "$dev" "$child" "$((depth+1))" "$next_hop" "$anchor_use"
  done < <(echo "$kids" | $JQ -r '.[]?.ip // empty')
}

log "SEEDS_BEGIN"
mapfile -t ALL_DEVS < <(list_active_devs)
log "Active interfaces: ${ALL_DEVS[*]}"

for dev in "${ALL_DEVS[@]}"; do restore_connected_10x_24_route_for_dev "$dev" || true; done

# Seed 1: DHCP leases - on the interface that serves that subnet
if [[ -f "$LEASES" && -s "$LEASES" ]]; then
  while read -r ip; do
    [[ -n "$ip" ]] || continue
    ip_prefix="${ip%.*}"
    for dev in "${ALL_DEVS[@]}"; do
      dev_ip="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1 | $CUT -d/ -f1)"
      [[ -n "$dev_ip" ]] || continue
      dev_prefix="${dev_ip%.*}"
      if [[ "$ip_prefix" == "$dev_prefix" ]]; then
        enqueue "$dev" "$ip" 1 "" "$ip"
        break
      fi
    done
  done < <($AWK '{print $3}' "$LEASES" | $SORT -u)
fi

# Seed 2: Common roots (disabled by default)
if [[ -n "$COMMON_ROOTS" ]]; then
  for dev in "${ALL_DEVS[@]}"; do
    for ip in $COMMON_ROOTS; do enqueue "$dev" "$ip" 1 "" "$ip"; done
  done
fi

# Seed 3: Per-interface seeds (gateway .1 from each interface's IP)
for dev in "${ALL_DEVS[@]}"; do
  s="$(seed_from_dev_ip "$dev")"
  [[ -n "$s" ]] && enqueue "$dev" "$s" 1 "" "$s"
done

# Seed 4: Transit peers
for dev in "${ALL_DEVS[@]}"; do
  local30="$(get_transit30_ip "$dev")"
  [[ -n "$local30" ]] || continue
  peer30="$(infer_peer_from_local30 "$local30")"
  log "TRANSIT_SEED dev=$dev local30=$local30 peer30=$peer30"
  enqueue "$dev" "$peer30" 1 "" "$peer30"
done

# Seed 5: Previously discovered transit seeds
TRANSIT_SEEDS_FILE="/etc/sentinels/transit_upstream_seeds"
if [[ -f "$TRANSIT_SEEDS_FILE" ]]; then
  while read -r seed; do
    [[ -n "$seed" && "$seed" == 10.* && "$seed" != 10.253.253.* ]] || continue
    for dev in "${ALL_DEVS[@]}"; do
      local30="$(get_transit30_ip "$dev")"
      [[ -n "$local30" ]] || continue
      peer30="$(infer_peer_from_local30 "$local30")"
      # anchor_ip must be the transit peer, not the destination
      enqueue "$dev" "$seed" 1 "$peer30" "$peer30"
    done
  done < "$TRANSIT_SEEDS_FILE"
fi

# Seed 6: Load cached peers - ONLY contactable IPs on our subnets
# The cache stores the actual IP we contacted, not the host_path
if [[ -x "$DISCOVERY_CACHE" ]]; then
  while IFS=$'\t' read -r peer_ip hostname hostpath cached_dev; do
    [[ -n "$peer_ip" && -n "$hostpath" ]] || continue
    # Only seed if peer_ip is on one of our local subnets (directly reachable)
    # Skip if peer_ip is a host_path (not on our subnet)
    if is_on_local_subnet "$peer_ip" && [[ "$peer_ip" != 10.253.253.* ]]; then
      log "SEED_FROM_CACHE peer=$peer_ip host=$hostname path=$hostpath dev=$cached_dev"
      enqueue "$cached_dev" "$peer_ip" 1 "" "$peer_ip"
    else
      log "SKIP_CACHE_SEED peer=$peer_ip reason=not_on_local_subnet"
    fi
  done < <("$DISCOVERY_CACHE" list 10)
fi

log "SEEDS_END qsize=${#QUEUE[@]}"

while ((${#QUEUE[@]})); do
  IFS='|' read -r dev ip depth via_ip anchor_ip <<<"${QUEUE[0]}"
  QUEUE=("${QUEUE[@]:1}")
  process_one "$dev" "$ip" "$depth" "$via_ip" "$anchor_ip"
done

log "DISCOVERY_DONE"
exit 0
