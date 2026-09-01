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
# /usr/local/bin/frognet_metrics_emit.sh
#
# Emit FrogNet SensorData via existing metric_upsert.sh.
#
# CONTRACT:
# - ALWAYS emits presence + basic link state (no sensitive details).
# - Emits detailed link attributes + counters ONLY when debug enabled.
# - NEVER emits until databasehost is assigned and local API path is usable.
#
# Usage:
#   frognet_metrics_emit.sh emit_presence
#   frognet_metrics_emit.sh emit_link <dev> [tag]
#   frognet_metrics_emit.sh emit_all_links [tag]
#
# Debug enable:
#   FROGNET_DEBUG=1 or FROGNET_DEBUG_METRICS=1

set -u

IP=/usr/sbin/ip
AWK=/usr/bin/awk
CUT=/usr/bin/cut
HEAD=/usr/bin/head
DATE=/bin/date
CAT=/bin/cat
GETENT=/usr/bin/getent

METRIC_UPSERT=/usr/local/bin/metric_upsert.sh
GET_FROGNET=/usr/local/bin/getFrogNet.bash

log(){ echo "[frognet-metrics] $($DATE '+%H:%M:%S') $*"; }

die(){ echo "[frognet-metrics][FATAL] $*" >&2; exit 2; }

debug_enabled(){
  [[ "${FROGNET_DEBUG:-0}" == "1" || "${FROGNET_DEBUG_METRICS:-0}" == "1" ]]
}

require_tools(){
  [[ -x "$METRIC_UPSERT" ]] || die "missing $METRIC_UPSERT"
  [[ -x "$GET_FROGNET" ]] || die "missing $GET_FROGNET"
  command -v python3 >/dev/null 2>&1 || die "missing python3"
}

dbhost_assigned(){
  # /etc/hosts must contain databasehost.frognet
  grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+[[:space:]]+databasehost\.frognet(\s|$)' /etc/hosts 2>/dev/null
}

dbhost_ip(){
  # resolve via getent; fall back to hosts file parse
  local ip
  ip="$($GETENT hosts databasehost.frognet 2>/dev/null | $AWK '{print $1}' | $HEAD -n1)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
    return 0
  fi
  ip="$(awk '$0 ~ /(^|[[:space:]])databasehost\.frognet([[:space:]]|$)/ {print $1; exit}' /etc/hosts 2>/dev/null)"
  [[ -n "$ip" ]] && echo "$ip"
}

local_api_ready(){
  # Your metric_upsert.sh uses: http://127.0.0.1:80/api.php ... Host: databasehost.frognet
  # Probe that exact path lightly.
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --connect-timeout 10 --max-time 10 \
    -H 'Host: databasehost.frognet' \
    'http://127.0.0.1:80/api.php?entity=ping&action=ping' >/dev/null 2>&1 && return 0

  # If ping endpoint doesn't exist, fall back to generic "is api.php reachable"
  curl -fsS --connect-timeout 10 --max-time 10 \
    -H 'Host: databasehost.frognet' \
    'http://127.0.0.1:80/api.php' >/dev/null 2>&1
}

wait_for_dbhost_ready(){
  local timeout="${1:-30}"
  local start now
  start="$($DATE +%s)"

  while true; do
    if dbhost_assigned; then
      local ip
      ip="$(dbhost_ip)"
      if [[ -n "$ip" ]]; then
        if local_api_ready; then
          return 0
        fi
      fi
    fi
    now="$($DATE +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 1
  done
}

fqdn(){
  # getFrogNet.bash returns: FQDN,HOSTPATH,wlan0,wlan1-ish (your echo format)
  # We want the canonical FQDN.
  local s
  s="$("$GET_FROGNET" 2>/dev/null | tr -d '\r\n')"
  echo "$s" | $AWK -F, '{print $1}' | xargs
}

hostpath(){
  local s
  s="$("$GET_FROGNET" 2>/dev/null | tr -d '\r\n')"
  echo "$s" | $AWK -F, '{print $2}' | xargs
}

hostnet24(){
  local hp
  hp="$(hostpath)"
  [[ -n "$hp" ]] || return 1
  /usr/local/bin/convertToSubnetRange "$hp"
}

json_escape_py(){
  # stdin -> JSON string value content-escaped (no surrounding quotes)
  python3 - <<'PY'
import json,sys
s=sys.stdin.read()
print(json.dumps(s)[1:-1])
PY
}

emit_metric(){
  local sensor_type="$1"
  local metric_name="$2"
  local json="$3"
  "$METRIC_UPSERT" "$sensor_type" "$metric_name" "$json"
}

presence_json(){
  local fq hp net ts
  fq="$(fqdn)"
  hp="$(hostpath)"
  net="$(hostnet24)"
  ts="$($DATE +%s)"

  # Gather defaults and active uplinks summary
  local defaults
  defaults="$($IP route show default 2>/dev/null | awk NF | sed 's/"/'\''/g')"

  # IPv4 addresses (non-loopback)
  local ip4s
  ip4s="$($IP -4 -o addr show 2>/dev/null | awk '$2!="lo"{print $2" "$4}' )"

  python3 - <<PY
import json, time
data = {
  "ts": int($ts),
  "fqdn": ${fq!r},
  "hostpath": ${hp!r},
  "hostnet": ${net!r},
  "defaults": ${defaults!r},
  "ip4": ${ip4s!r},
}
print(json.dumps(data, separators=(",",":")))
PY
}

uplink_json(){
  # This node's single uplink / next hop, for the dashboard topology map.
  # Resolve the actual FIB decision toward off-mesh (the default route that
  # wins), not just `show default`, so a node with two defaults reports the
  # one the kernel uses. next_hop classification:
  #   10.x  -> frognet_parent  (the .1 of next_hop's /24 is the parent node;
  #                             dashboard maps it by subnet per the standing
  #                             subnet-identity rule)
  #   other -> external        (this node egresses to the Internet directly)
  #   none  -> none            (no default route; no uplink)
  local fq hp ts rt
  fq="$(fqdn)"
  hp="$(hostpath)"
  ts="$($DATE +%s)"
  rt="$($IP -4 -j route get 8.8.8.8 2>/dev/null || echo '[]')"

  python3 - "$ts" "$fq" "$hp" "$rt" <<'PY'
import json, sys
ts, fq, hp, raw = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    rt = json.loads(raw or "[]")
except Exception:
    rt = []
hop = dev = src = ""
if isinstance(rt, list) and rt and isinstance(rt[0], dict):
    e = rt[0]
    hop = e.get("gateway", "") or ""
    dev = e.get("dev", "") or ""
    src = e.get("prefsrc", "") or ""
if hop.startswith("10."):
    kind = "frognet_parent"
    parent_net = hop.rsplit(".", 1)[0]
elif hop:
    kind = "external"
    parent_net = ""
else:
    kind = "none"
    parent_net = ""
data = {
  "ts": int(ts),
  "fqdn": fq,
  "hostpath": hp,
  "next_hop": hop,
  "dev": dev,
  "src": src,
  "uplink_kind": kind,
  "parent_net": parent_net,
}
print(json.dumps(data, separators=(",", ":")))
PY
}

dev_stat(){
  local dev="$1" key="$2"
  local f="/sys/class/net/$dev/statistics/$key"
  [[ -f "$f" ]] && cat "$f" || echo ""
}

dev_attr(){
  local dev="$1" key="$2"
  local f="/sys/class/net/$dev/$key"
  [[ -f "$f" ]] && cat "$f" || echo ""
}

link_basic_json(){
  local dev="$1" tag="${2:-}"
  local ts
  ts="$($DATE +%s)"

  local state oper mtu
  oper="$(dev_attr "$dev" operstate)"
  mtu="$(dev_attr "$dev" mtu)"

  # Basic counters are considered debug per your rule, so do NOT include here.
  python3 - <<PY
import json
data = {
  "ts": int($ts),
  "dev": ${dev!r},
  "tag": ${tag!r},
  "operstate": ${oper!r},
  "mtu": ${mtu!r},
}
print(json.dumps(data, separators=(",",":")))
PY
}

link_debug_json(){
  local dev="$1" tag="${2:-}"
  local ts
  ts="$($DATE +%s)"

  local mac ifindex speed carrier txqlen
  mac="$(dev_attr "$dev" address)"
  ifindex="$(dev_attr "$dev" ifindex)"
  speed="$(dev_attr "$dev" speed)"
  carrier="$(dev_attr "$dev" carrier)"
  txqlen="$(dev_attr "$dev" tx_queue_len)"

  # IPv4/IPv6 addresses on dev
  local ip4 ip6
  ip4="$($IP -4 -o addr show dev "$dev" 2>/dev/null | awk '{print $4}' | tr '\n' ' ' | sed 's/[[:space:]]\+$//')"
  ip6="$($IP -6 -o addr show dev "$dev" 2>/dev/null | awk '{print $4}' | tr '\n' ' ' | sed 's/[[:space:]]\+$//')"

  # Stats
  local rx_bytes tx_bytes rx_packets tx_packets rx_errors tx_errors rx_dropped tx_dropped
  rx_bytes="$(dev_stat "$dev" rx_bytes)"
  tx_bytes="$(dev_stat "$dev" tx_bytes)"
  rx_packets="$(dev_stat "$dev" rx_packets)"
  tx_packets="$(dev_stat "$dev" tx_packets)"
  rx_errors="$(dev_stat "$dev" rx_errors)"
  tx_errors="$(dev_stat "$dev" tx_errors)"
  rx_dropped="$(dev_stat "$dev" rx_dropped)"
  tx_dropped="$(dev_stat "$dev" tx_dropped)"

  # Neigh tables (v4 + v6)
  local neigh4 neigh6
  neigh4="$($IP neigh show dev "$dev" 2>/dev/null | sed 's/"/'\''/g')"
  neigh6="$($IP -6 neigh show dev "$dev" 2>/dev/null | sed 's/"/'\''/g')"

  python3 - <<PY
import json
data = {
  "ts": int($ts),
  "dev": ${dev!r},
  "tag": ${tag!r},
  "mac": ${mac!r},
  "ifindex": ${ifindex!r},
  "speed": ${speed!r},
  "carrier": ${carrier!r},
  "tx_queue_len": ${txqlen!r},
  "ip4": ${ip4!r},
  "ip6": ${ip6!r},
  "stats": {
    "rx_bytes": ${rx_bytes!r},
    "tx_bytes": ${tx_bytes!r},
    "rx_packets": ${rx_packets!r},
    "tx_packets": ${tx_packets!r},
    "rx_errors": ${rx_errors!r},
    "tx_errors": ${tx_errors!r},
    "rx_dropped": ${rx_dropped!r},
    "tx_dropped": ${tx_dropped!r},
  },
  "neigh4": ${neigh4!r},
  "neigh6": ${neigh6!r},
}
print(json.dumps(data, separators=(",",":")))
PY
}

emit_presence(){
  require_tools
  if ! wait_for_dbhost_ready 30; then
    log "skip presence: databasehost not ready"
    return 1
  fi
  emit_metric "Presence" "Base" "$(presence_json)"
  return 0
}

emit_uplink(){
  require_tools
  if ! wait_for_dbhost_ready 30; then
    log "skip uplink: databasehost not ready"
    return 1
  fi
  emit_metric "Topology" "Uplink" "$(uplink_json)"
  return 0
}

emit_link(){
  local dev="${1:-}"
  local tag="${2:-}"
  [[ -n "$dev" ]] || die "emit_link missing dev"

  require_tools
  if ! wait_for_dbhost_ready 30; then
    log "skip link dev=$dev: databasehost not ready"
    return 1
  fi

  emit_metric "LinkState" "Base" "$(link_basic_json "$dev" "$tag")"

  if debug_enabled; then
    emit_metric "LinkState" "Debug" "$(link_debug_json "$dev" "$tag")"
  fi

  return 0
}

emit_all_links(){
  local tag="${1:-}"
  require_tools
  if ! wait_for_dbhost_ready 30; then
    log "skip all_links: databasehost not ready"
    return 1
  fi

  emit_metric "Presence" "Base" "$(presence_json)"

  for devpath in /sys/class/net/*; do
    dev="$(basename "$devpath")"
    [[ "$dev" == "lo" ]] && continue
    emit_metric "LinkState" "Base" "$(link_basic_json "$dev" "$tag")"
    if debug_enabled; then
      emit_metric "LinkState" "Debug" "$(link_debug_json "$dev" "$tag")"
    fi
  done
  return 0
}

cmd="${1:-}"
shift || true

case "$cmd" in
  emit_presence) emit_presence ;;
  emit_uplink) emit_uplink ;;
  emit_link) emit_link "$@" ;;
  emit_all_links) emit_all_links "$@" ;;
  *)
    echo "usage:"
    echo "  $0 emit_presence"
    echo "  $0 emit_uplink"
    echo "  $0 emit_link <dev> [tag]"
    echo "  $0 emit_all_links [tag]"
    exit 2
    ;;
esac
