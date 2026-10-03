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
# /usr/local/bin/discovery_slow_worker.sh
#
# Slow-link discovery worker (standalone).
# v2: No ICMP ping — frognet_echo is the aliveness gate,
#     frognet_alive.bash for via-hop reachability check.

log(){ echo "discovery_slow_worker: $*"; }

TARGET="${1:-}"
[[ -n "$TARGET" ]] || { log "BAIL reason=missing_target"; exit 2; }

log "ENTER pid=$$ target=$TARGET"
trap 'rc=$?; log "EXIT pid=$$ target=$TARGET rc=$rc"; exit $rc' EXIT

. /usr/local/bin/mapInterfaces

IP="/usr/sbin/ip"
CURL="/usr/bin/curl"
AWK="/usr/bin/awk"
CUT="/usr/bin/cut"
HEAD="/usr/bin/head"
TR="/usr/bin/tr"
XARGS="/usr/bin/xargs"
JQ="/usr/bin/jq"
GREP="/usr/bin/grep"
ALIVE="/usr/local/bin/frognet_alive.bash"

SENT_DIR="/etc/sentinels"
SIM_UNDERLAY_FILE="/etc/frognet/simulator_underlay"
PENDING_HELPER="/usr/local/bin/discovery_pending.sh"

MAX_DEPTH=3
CURL_TIMEOUT="${FROGNET_DISCOVERY_CURL_TIMEOUT:-30}"

MET_NET=22
MET_BOOT=23

is_local_ip() {
  local ip="$1"
  $IP -4 -o addr show | $AWK '{print $4}' | $CUT -d/ -f1 | $GREP -Fxq "$ip"
}
is_ip() { [[ "${1:-}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
is_10x() { [[ "${1:-}" == 10.* ]]; }

subnet24() { /usr/local/bin/convertToSubnetRange "${1:-}" 2>/dev/null || echo ""; }

cidr_to_24() {
  local cidr="$1"
  local ip="${cidr%%/*}"
  [[ -n "$ip" ]] || { echo ""; return; }
  echo "$ip" | $AWK -F. '{print $1"."$2"."$3".0/24"}'
}

dev_is_sim_underlay() {
  local dev="$1"
  [[ -f "$SIM_UNDERLAY_FILE" ]] || return 1
  local dev_cidr dev_24
  dev_cidr="$($IP -o -4 addr show dev "$dev" 2>/dev/null | $AWK '{print $4}' | $HEAD -n1 || true)"
  [[ -n "$dev_cidr" ]] || return 1
  dev_24="$(cidr_to_24 "$dev_cidr")"
  [[ -n "$dev_24" ]] || return 1
  while read -r cidr; do
    cidr="$(echo "${cidr:-}" | $AWK '{$1=$1;print}')"
    [[ -z "$cidr" || "$cidr" =~ ^# ]] && continue
    [[ "$(cidr_to_24 "$cidr")" == "$dev_24" ]] && return 0
  done < "$SIM_UNDERLAY_FILE"
  return 1
}

route_get() {
  local dst="$1"
  local out dev via
  out="$($IP route get "$dst" 2>/dev/null | $HEAD -n1 || true)"
  [[ -n "$out" ]] || return 1
  dev="$(echo "$out" | $AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
  via="$(echo "$out" | $AWK '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')"
  [[ -n "$dev" ]] || return 1
  echo "${dev}|${via}|${out}"
}

# ── Aliveness: frognet_echo replaces ping ──
# If echo succeeds, host is alive AND we have identity.
frognet_echo_8080_or_80() {
  local ip="$1"
  local out=""
  out="$($CURL -fsS --connect-timeout 10 --max-time 15 -H "Host: $ip" "http://$ip:8080/frognet_echo.php" 2>/dev/null || true)"
  out="$(echo "$out" | $TR -d '\r\n')"
  if [[ -n "$out" ]]; then
    echo "$out"; return 0
  fi
  out="$($CURL -fsS --connect-timeout 10 --max-time "$CURL_TIMEOUT" -H "Host: $ip" "http://$ip/frognet_echo.php" 2>/dev/null || true)"
  echo "$out" | $TR -d '\r\n'
}

# ── Via-hop reachability: frognet_alive replaces ping ──
# Used only at line 174 equivalent to check if the via hop is still up.
via_is_reachable() {
  local via="$1"
  "$ALIVE" "$via" 5 >/dev/null 2>&1
}

gethosts_80() {
  local ip="$1"
  $CURL -fsS --connect-timeout 10 --max-time "$CURL_TIMEOUT" -H "Host: $ip" "http://$ip/getHosts.php" 2>/dev/null || echo "[]"
}

is_valid_echo() {
  local s="${1:-}"
  [[ -n "$s" ]] || return 1
  local n
  n="$($AWK -F',' '{print NF-1}' <<<"$s" 2>/dev/null || echo 0)"
  [[ "$n" -ge 3 ]] || return 1
  local host path
  IFS=',' read -r host path _ _ <<<"$s"
  host="$(echo "$host" | $XARGS)"
  path="$(echo "$path" | $XARGS)"
  [[ -n "$host" && -n "$path" ]] || return 1
  is_ip "$path" || return 1
  is_10x "$path" || return 1
  return 0
}

delete_routes_for_prefix_not_dev() {
  local net="$1" keep_dev="$2"
  if dev_is_sim_underlay "$keep_dev"; then
    log "ROUTE delete_skip net=$net keep_dev=$keep_dev reason=sim_underlay_keep_dev"
    return 0
  fi
  while read -r line; do
    [[ -n "$line" ]] || continue
    echo "$line" | $GREP -q 'proto kernel' && continue
    local dev
    dev="$(echo "$line" | $AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
    [[ -n "$dev" ]] || continue
    [[ "$dev" == "$keep_dev" ]] && continue
    log "ROUTE delete net=$net line=[$line] reason=competing_dev"
    $IP route del $line 2>/dev/null || true
  done < <($IP route show "$net" 2>/dev/null || true)
}

delete_bootstrap32_if_present() {
  local ip="$1"
  $IP route del "$ip/32" 2>/dev/null || true
}

install_bootstrap32() {
  local ip="$1" dev="$2" via="$3"
  log "ROUTE bootstrap32 ip=$ip dev=$dev via=$via metric=$MET_BOOT"
  $IP route replace "$ip/32" dev "$dev" via "$via" metric "$MET_BOOT" onlink
}

commit_net24_route() {
  local net="$1" dev="$2" via="$3"
  if dev_is_sim_underlay "$dev"; then
    log "ROUTE commit_skip net=$net dev=$dev reason=sim_underlay_dev"
    return 0
  fi
  log "ROUTE commit net=$net dev=$dev via=$via metric=$MET_NET"
  $IP route replace "$net" dev "$dev" via "$via" metric "$MET_NET" onlink 2>/dev/null || true
  delete_routes_for_prefix_not_dev "$net" "$dev"
}

validate_and_commit_for_target() {
  local target="$1" dev="$2" via="$3"

  is_ip "$target" || { log "ROUTE validate_skip target=$target reason=not_ip"; return 0; }
  is_10x "$target" || { log "ROUTE validate_skip target=$target reason=not_10x"; return 0; }
  is_local_ip "$target" && { log "ROUTE validate_skip target=$target reason=local_ip"; return 0; }
  is_ip "$via" || { log "ROUTE validate_skip target=$target via=$via reason=via_not_ip"; return 0; }

  if dev_is_sim_underlay "$dev"; then
    log "ROUTE validate_skip target=$target dev=$dev reason=sim_underlay_dev"
    return 0
  fi

  # Check via-hop reachability using frognet_alive (not ping)
  if is_10x "$via"; then
    if ! via_is_reachable "$via"; then
      log "ROUTE validate_skip target=$target dev=$dev via=$via reason=via_unreachable"
      return 0
    fi
  fi

  local net
  net="$(subnet24 "$target")"
  [[ -n "$net" ]] || { log "ROUTE validate_skip target=$target reason=no_subnet24"; return 0; }

  commit_net24_route "$net" "$dev" "$via"
  delete_bootstrap32_if_present "$target"
  return 0
}

pending_mark() { [[ -x "$PENDING_HELPER" ]] && "$PENDING_HELPER" mark "$1" || true; }
pending_clear(){ [[ -x "$PENDING_HELPER" ]] && "$PENDING_HELPER" clear "$1" || true; }

rg="$(route_get "$TARGET" || true)"
if [[ -z "$rg" ]]; then
  log "BAIL target=$TARGET reason=route_get_failed -> pending"
  pending_mark "$TARGET"
  exit 0
fi
dev="${rg%%|*}"
via="$(echo "$rg" | $CUT -d'|' -f2)"
log "route_get target=$TARGET dev=$dev via=$via raw=[$(echo "$rg" | cut -d'|' -f3-)]"

# ── Gate: frognet_echo IS the aliveness test ──
# Try echo directly.  If it fails, try bootstrap32 and retry echo.
echo="$(frognet_echo_8080_or_80 "$TARGET" | $TR -d '\r\n')"
if ! is_valid_echo "$echo"; then
  log "echo_fail target=$TARGET dev=$dev via=$via (try bootstrap32 if via)"
  if [[ -n "$via" && "$via" != "$TARGET" ]]; then
    install_bootstrap32 "$TARGET" "$dev" "$via" || true
  fi
  # Retry echo after bootstrap32 route fix
  echo="$(frognet_echo_8080_or_80 "$TARGET" | $TR -d '\r\n')"
  if ! is_valid_echo "$echo"; then
    log "BAIL target=$TARGET reason=echo_fail_after_bootstrap -> pending"
    pending_mark "$TARGET"
    exit 0
  fi
fi

pending_clear "$TARGET"
log "echo_ok target=$TARGET echo=[$echo]"

host="$(echo "$echo" | $CUT -d',' -f1 | $XARGS)"
host_path="$(echo "$echo" | $CUT -d',' -f2 | $XARGS)"
log "parsed host=$host host_path=$host_path"

is_ip "$host_path" || { log "BAIL reason=host_path_not_ip host_path=$host_path"; exit 0; }
is_10x "$host_path" || { log "BAIL reason=host_path_not_10x host_path=$host_path"; exit 0; }

anchor_via="${via:-$TARGET}"
validate_and_commit_for_target "$host_path" "$dev" "$anchor_via" || true

log "CALL addHostAndPropogate host=$host host_path=$host_path seed=$TARGET dev=$dev"
/usr/local/bin/addHostAndPropogate.bash "$host" "$host_path" "" "$TARGET" "$dev" || true

declare -A SEEN
QUEUE=()
enqueue() {
  local ip="$1" depth="$2"
  is_ip "$ip" || return 0
  is_10x "$ip" || return 0
  is_local_ip "$ip" && return 0
  [[ -n "${SEEN[$ip]:-}" ]] && return 0
  SEEN["$ip"]=1
  QUEUE+=("$ip|$depth")
  log "QUEUE add ip=$ip depth=$depth qsize=${#QUEUE[@]}"
}

enqueue "$host_path" 1

# ======================================================================
# Parallel wavefront BFS — all gethosts calls within a depth wave
# run as parallel background jobs for maximum wire utilization.
# Route commits and child enqueue are serial (shared kernel state).
# ======================================================================
_probe_dir="$(/usr/bin/mktemp -d /tmp/dsw_probe.XXXXXX)"
trap 'rc=$?; rm -rf "$_probe_dir"; log "EXIT pid=$$ target=$TARGET rc=$rc"; exit $rc' EXIT

_sanitize() { echo "${1//\./_}"; }

_wave=0
while ((${#QUEUE[@]})); do
  _wave=$((_wave + 1))
  WAVE=("${QUEUE[@]}")
  QUEUE=()

  log "WAVE${_wave}_BEGIN count=${#WAVE[@]}"

  # Phase 1: Launch all gethosts in parallel
  _gh_pids=()
  for item in "${WAVE[@]}"; do
    IFS='|' read -r ip depth <<<"$item"
    _key="$(_sanitize "$ip")"
    (
      _kids=$(gethosts_80 "$ip" 2>/dev/null) || _kids="[]"
      echo "$_kids" > "$_probe_dir/hosts_${_key}"
    ) &
    _gh_pids+=($!)
  done

  for _pid in "${_gh_pids[@]}"; do
    wait "$_pid" 2>/dev/null || true
  done
  log "WAVE${_wave}_GETHOSTS_DONE"

  # Phase 2: Process results — enqueue children, validate routes
  for item in "${WAVE[@]}"; do
    IFS='|' read -r ip depth <<<"$item"
    _key="$(_sanitize "$ip")"
    _hosts_file="$_probe_dir/hosts_${_key}"
    [[ -f "$_hosts_file" ]] || continue

    kids="$(cat "$_hosts_file")"
    kid_count="$(echo "$kids" | $JQ -r '. | length' 2>/dev/null || echo 0)"
    log "getHosts ip=$ip depth=$depth kids_count=$kid_count"

    # Enqueue children for next wave
    if (( depth < MAX_DEPTH )); then
      while read -r child; do
        [[ -n "$child" ]] || continue
        enqueue "$child" "$((depth+1))"
      done < <(echo "$kids" | $JQ -r '.[]?.ip // empty')
    fi

    # Validate and commit routes for children (serial — kernel state)
    while read -r child; do
      [[ -n "$child" ]] || continue
      rg2="$(route_get "$child" || true)"
      [[ -n "$rg2" ]] || { log "child_route_get_fail child=$child"; continue; }
      dev2="${rg2%%|*}"
      via2="$(echo "$rg2" | $CUT -d'|' -f2)"
      [[ -n "$via2" ]] || { log "child_skip child=$child reason=no_via dev=$dev2"; continue; }
      validate_and_commit_for_target "$child" "$dev2" "$via2" || true
    done < <(echo "$kids" | $JQ -r '.[]?.ip // empty')
  done

  # Clean up probe files for this wave
  rm -f "$_probe_dir"/hosts_*
  log "WAVE${_wave}_END next_qsize=${#QUEUE[@]}"
done

# Signal merge needed — fire runMerge directly instead of touching
# runAgain (which would cause infinite merge loops)
    # [SLOW_WORKER_MERGE_V1] was a passive marker; call runMerge directly (it
    # self-queues) so discovery actually re-runs rather than waiting for the next pass.
    /usr/local/bin/runMerge.bash >/dev/null 2>&1 &
log "SUCCESS — forking runMerge"
/usr/local/bin/runMerge.bash &

exit 0
