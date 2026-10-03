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
# /usr/local/bin/frognet_link_autoconfig.sh
#
# Autoconfigure a deterministic point-to-point transit /30 out of 10.253.253.0/24
# on a wired interface, by discovering the peer MAC at L2.
#
# v2: Honors /etc/frognet/transit_exclude_interfaces
#
# CONTROL-FLOW LOGGING ONLY (your requirement):
#   - Every exit path logs an explicit reason to STDERR.
#   - No silent returns.
#   - STDOUT contains ONLY `export ...` lines (for watcher eval).
#

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

DEV="${1:-}"

IP="/usr/sbin/ip"
AWK="/usr/bin/awk"
CUT="/usr/bin/cut"
HEAD="/usr/bin/head"
TR="/usr/bin/tr"
XARGS="/usr/bin/xargs"
CURL="/usr/bin/curl"
PING="/bin/ping"
DATE="/bin/date"
GREP="/usr/bin/grep"

# Exclude file - interfaces listed here will NEVER get transit IPs
EXCLUDE_FILE="/etc/frognet/transit_exclude_interfaces"

EXIT_REASON="(unset)"

ts(){ $DATE '+%Y-%m-%d %H:%M:%S.%3N'; }
log(){ echo "[frognet-link-autoconfig][$(ts)][pid=$$] $*" >&2; }

# Always log control-flow termination (even success paths set EXIT_REASON)
trap 'rc=$?; log "EXIT dev=${DEV:-<none>} rc=$rc reason=${EXIT_REASON}"; exit $rc' EXIT

_out_empty() {
  # STDOUT: exports ONLY
  echo "export FROGNET_TRANSIT_DEV=''"
  echo "export FROGNET_TRANSIT_LOCAL=''"
  echo "export FROGNET_TRANSIT_PEER=''"
  echo "export FROGNET_TRANSIT_CIDR=''"
  echo "export FROGNET_TRANSIT_PEER_FROGNET=''"
  echo "export FROGNET_TRANSIT_SEED=''"
}

exit_with_reason() {
  # Control-flow exit point for all early exits
  EXIT_REASON="$1"
  _out_empty
  exit 0
}

is_excluded() {
  local dev="$1"
  [[ -f "$EXCLUDE_FILE" ]] || return 1
  $GREP -qxF "$dev" "$EXCLUDE_FILE" 2>/dev/null
}

_is_wireless() { [[ -d "/sys/class/net/$1/wireless" ]]; }

_operstate_up() {
  local dev="$1"
  [[ -f "/sys/class/net/$dev/operstate" ]] || return 1
  local st
  st="$(cat "/sys/class/net/$dev/operstate" 2>/dev/null || true)"
  [[ "$st" == "up" || "$st" == "unknown" ]]
}

_dev_mac() { cat "/sys/class/net/$1/address" 2>/dev/null; }

_dev_ip4_any() {
  $IP -4 addr show dev "$1" 2>/dev/null | $AWK '/inet /{print $2}' | $HEAD -n1 | $CUT -d/ -f1
}

_dev_ip4_transit() {
  $IP -4 -o addr show dev "$1" \
    | $AWK '/inet 10\.253\.253\./{print $4}' \
    | $GREP -E '/30$' \
    | $HEAD -n1 \
    | $CUT -d/ -f1
}


_hash_pair_to_block() {
  python3 - "$1" <<'PY'
import hashlib, sys
s = sys.argv[1].encode()
h = int(hashlib.sha1(s).hexdigest(), 16)
block = (h % 64) * 4
print(block)
PY
}

_infer_peer_from_local30() {
  local local_ip="$1"
  local last base peer
  last="$(echo "$local_ip" | $AWK -F. '{print $4}')"
  base="$(( (last / 4) * 4 ))"
  if [[ "$last" == "$((base+1))" ]]; then peer="$((base+2))"; else peer="$((base+1))"; fi
  echo "10.253.253.${peer}"
}

_peer_mac_from_neigh_v4() {
  local dev="$1"
  $IP neigh show dev "$dev" 2>/dev/null \
    | $AWK '
        $0 ~ /lladdr/ && $0 !~ /FAILED/ {
          for(i=1;i<=NF;i++) if($i=="lladdr"){print $(i+1); exit}
        }'
}

_peer_mac_from_neigh_v6() {
  local dev="$1"
  $IP -6 neigh show dev "$dev" 2>/dev/null \
    | $AWK '
        $0 ~ /lladdr/ && $0 !~ /FAILED/ {
          for(i=1;i<=NF;i++) if($i=="lladdr"){print $(i+1); exit}
        }'
}

_nudge_ipv6_neighbors() {
  local dev="$1"
  # Best-effort nudge; no output to STDOUT
  if [[ -x "$PING" ]]; then
    $PING -6 -I "$dev" -c1 -W1 ff02::1 >/dev/null 2>&1 || true
    $PING -6 -I "$dev" -c1 -W1 ff02::1 >/dev/null 2>&1 || true
  fi
}

_assign_transit_from_macs() {
  local dev="$1" peer_mac="$2"
  local local_mac lo hi
  local_mac="$(_dev_mac "$dev")"

  [[ -n "$local_mac" && -n "$peer_mac" ]] || return 1

  if [[ "$local_mac" < "$peer_mac" ]]; then lo="$local_mac"; hi="$peer_mac"; else lo="$peer_mac"; hi="$local_mac"; fi

  local block host_local host_peer
  block="$(_hash_pair_to_block "${lo}|${hi}")" || return 1

  if [[ "$local_mac" == "$lo" ]]; then
    host_local="$((block+1))"; host_peer="$((block+2))"
  else
    host_local="$((block+2))"; host_peer="$((block+1))"
  fi

  local local_ip peer_ip cidr
  local_ip="10.253.253.${host_local}"
  peer_ip="10.253.253.${host_peer}"
  cidr="${local_ip}/30"

  # Apply /30
  $IP link set "$dev" up 2>/dev/null || true
  $IP addr replace "$cidr" dev "$dev" 2>/dev/null || return 1

  # VERIFY kernel accepted it (control-flow critical)
  $IP -4 addr show dev "$dev" 2>/dev/null | $GREP -q " ${cidr}\b" || return 2

  echo "${local_ip}|${peer_ip}|${cidr}"
}

_probe_peer_frognet_echo() {
  local peer_ip="$1"
  local out commas host_path

  out="$($CURL -fsS --connect-timeout 10 --max-time 15 -H "Host: $peer_ip" "http://$peer_ip:8080/frognet_echo.php" 2>/dev/null | $TR -d '\r\n')"
  if [[ -z "$out" ]]; then
    out="$($CURL -fsS --connect-timeout 10 --max-time 15 -H "Host: $peer_ip" "http://$peer_ip/frognet_echo.php" 2>/dev/null | $TR -d '\r\n')"
  fi

  commas="$(echo "$out" | $AWK -F, '{print NF-1}')"
  [[ "$commas" -ge 3 ]] || { echo ""; return 0; }

  host_path="$(echo "$out" | $AWK -F, '{print $2}' | $XARGS)"
  [[ "$host_path" == 10.* ]] || { echo ""; return 0; }

  echo "$host_path"
}

# ------------------------------------------------------------
# main (control-flow logged via EXIT_REASON + EXIT trap)
# ------------------------------------------------------------
EXIT_REASON="start"

if [[ -z "$DEV" ]]; then
  exit_with_reason "no_dev_arg"
fi

# CRITICAL: Check exclude file FIRST, before anything else
if is_excluded "$DEV"; then
  exit_with_reason "interface_excluded"
fi

$IP link show "$DEV" >/dev/null 2>&1 || exit_with_reason "no_such_dev"
if _is_wireless "$DEV"; then exit_with_reason "wireless_dev"; fi

$IP link set "$DEV" up 2>/dev/null || true
if ! _operstate_up "$DEV"; then
  exit_with_reason "operstate_not_up"
fi

# If transit already present, check if this interface should have it
local_transit="$(_dev_ip4_transit "$DEV")"
if [[ -n "$local_transit" ]]; then
  # IMPORTANT: Even if transit IP exists, check if interface is excluded
  # If excluded, we should NOT have a transit IP - log warning but don't remove
  # (removal should be done manually or by a cleanup script)
  if is_excluded "$DEV"; then
    log "WARNING: $DEV has transit IP $local_transit but is in exclude list!"
    exit_with_reason "excluded_but_has_transit"
  fi
  
  peer_transit="$(_infer_peer_from_local30 "$local_transit")"
  cidr="${local_transit}/30"
  peer_frognet="$(_probe_peer_frognet_echo "$peer_transit")"
  seed=""
  [[ -n "$peer_frognet" ]] && seed="$peer_frognet"

  EXIT_REASON="success_reuse_transit"
  echo "export FROGNET_TRANSIT_DEV='$DEV'"
  echo "export FROGNET_TRANSIT_LOCAL='${local_transit:-}'"
  echo "export FROGNET_TRANSIT_PEER='${peer_transit:-}'"
  echo "export FROGNET_TRANSIT_CIDR='${cidr:-}'"
  echo "export FROGNET_TRANSIT_PEER_FROGNET='${peer_frognet:-}'"
  echo "export FROGNET_TRANSIT_SEED='${seed:-}'"
  exit 0
fi

# Need a peer MAC
peer_mac="$(_peer_mac_from_neigh_v4 "$DEV" | $TR -d '\r\n')"
if [[ -z "$peer_mac" ]]; then
  _nudge_ipv6_neighbors "$DEV"
  peer_mac="$(_peer_mac_from_neigh_v6 "$DEV" | $TR -d '\r\n')"
fi
if [[ -z "$peer_mac" ]]; then
  exit_with_reason "no_peer_mac_in_neigh"
fi

info="$(_assign_transit_from_macs "$DEV" "$peer_mac")"
rc=$?
if [[ $rc -ne 0 || -z "$info" ]]; then
  if [[ $rc -eq 2 ]]; then
    exit_with_reason "kernel_rejected_cidr"
  fi
  exit_with_reason "assign_transit_failed"
fi

local_transit="$(echo "$info" | $CUT -d'|' -f1)"
peer_transit="$(echo "$info" | $CUT -d'|' -f2)"
cidr="$(echo "$info" | $CUT -d'|' -f3)"

peer_frognet="$(_probe_peer_frognet_echo "$peer_transit")"
seed=""
[[ -n "$peer_frognet" ]] && seed="$peer_frognet"

EXIT_REASON="success_assigned_transit"
echo "export FROGNET_TRANSIT_DEV='$DEV'"
echo "export FROGNET_TRANSIT_LOCAL='${local_transit:-}'"
echo "export FROGNET_TRANSIT_PEER='${peer_transit:-}'"
echo "export FROGNET_TRANSIT_CIDR='${cidr:-}'"
echo "export FROGNET_TRANSIT_PEER_FROGNET='${peer_frognet:-}'"
echo "export FROGNET_TRANSIT_SEED='${seed:-}'"
exit 0
