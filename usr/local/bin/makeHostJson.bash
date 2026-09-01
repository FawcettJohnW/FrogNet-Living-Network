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
##############################################################
# /usr/local/bin/makeHostJson.bash
#
# PURPOSE (authoritative for getHosts.php and discovery recursion):
#   Emit a VALID JSON ARRAY of host rows.
#
# CONTRACT:
#   - Output MUST be a top-level JSON array.
#   - Each element MUST have ".ip" (sync_interfaces relies on this).
#   - "hostname" prefers canonical FrogNetHost.<Domain> alias when present.
#   - "echo" MUST be present for each host when possible.
#
# ECHO RULE (per your requirement):
#   - If echo is missing from cache for an IP, recreate it (best-effort).
#   - Network calls to recreate echo happen ONLY during a forced rebuild
#     (merge calls makeHostJson.bash Rebuild). Non-forced calls are cache-only
#     to keep the link quiet and avoid repeated probing.
#
# SOVEREIGNTY:
#   - /etc/hosts is canonical for the converged host universe on this node.
#   - No central authority.
##############################################################

SCRIPT_VERSION="3"
. /usr/local/bin/mapInterfaces 2>&1 > /dev/null

forceRebuild="${1:-}"

SENT_DIR="/etc/sentinels"
ETC_HOSTS="/etc/hosts"
OLD_JSON="${SENT_DIR}/old_host_json"
OLD_META="${SENT_DIR}/old_hosts_meta"   # stores "version sha"

mkdir -p "$SENT_DIR"
# touch "$ETC_HOSTS"

canon_hosts() {
  while IFS= read -r line; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs || true)"
    [[ -z "$line" ]] && continue
    echo "$line"
  done < "$ETC_HOSTS"
}

sha_now="$(canon_hosts | sha256sum | awk '{print $1}')"

# Fast path: unchanged and not forced
if [[ -z "$forceRebuild" && -f "$OLD_META" && -f "$OLD_JSON" ]]; then
  read -r ver_old sha_old < "$OLD_META" || true
  if [[ "$ver_old" == "$SCRIPT_VERSION" && -n "$sha_old" && "$sha_old" == "$sha_now" ]]; then
    cat "$OLD_JSON"
    exit 0
  fi
fi

touch "$OLD_META"
echo "$SCRIPT_VERSION $sha_now" > "$OLD_META"

# ------------------------------------------------------------
# Helper: validate echo contract "HOST,HOST_PATH,GW0,GW1"
# HOST_PATH must be 10.*
# ------------------------------------------------------------
is_valid_echo() {
  local s="${1:-}"
  [[ -z "$s" ]] && return 1
  s="$(echo "$s" | tr -d '\r' | tr -d '\n')"
  local commas
  commas="$(echo "$s" | awk -F',' '{print NF-1}')"
  [[ "$commas" -lt 3 ]] && return 1
  local f1 f2 f3 f4
  IFS=',' read -r f1 f2 f3 f4 <<< "$s"
  f1="$(echo "$f1" | xargs)"
  f2="$(echo "$f2" | xargs)"
  [[ -z "$f1" || -z "$f2" ]] && return 1
  [[ "$f2" == 10.* ]] || return 1
  return 0
}

# ------------------------------------------------------------
# Load echo cache from OLD_JSON: ip -> echo
# ------------------------------------------------------------
declare -A ECHO_BY_IP
if [[ -f "$OLD_JSON" ]] && command -v jq >/dev/null 2>&1; then
  while IFS=$'\t' read -r ip echo; do
    [[ -n "$ip" && -n "$echo" ]] && ECHO_BY_IP["$ip"]="$echo"
  done < <(jq -r '.[] | select(.ip!=null) | "\(.ip)\t\(.echo // "")"' "$OLD_JSON" || true)
fi

# ------------------------------------------------------------
# Load fresh echoes written by sync_interfaces (overrides stale cache)
# ------------------------------------------------------------
ECHO_CACHE="${SENT_DIR}/echo_cache"
if [[ -f "$ECHO_CACHE" ]]; then
  while IFS=$'\t' read -r ip echo; do
    [[ -n "$ip" && -n "$echo" ]] && ECHO_BY_IP["$ip"]="$echo"
  done < "$ECHO_CACHE"
fi

# ------------------------------------------------------------
# Our local echo string is authoritative for our own IP
# ------------------------------------------------------------
ourIP=$(/usr/local/bin/getEth0Address || echo "")
hostShort=$(hostname -s || echo "FrogNetHost")
domain=$(/usr/local/bin/getOurDomain || echo "")
if [[ -n "$domain" ]]; then
  fqdn="${hostShort}.${domain}"
else
  fqdn="${hostShort}"
fi
w0=$(/usr/local/bin/getWlan0IP || echo "")
w1=$(/usr/local/bin/getWlan1IP || echo "")
localEcho="${fqdn},${ourIP},${w0},${w1}"
if [[ -n "$ourIP" ]]; then
  ECHO_BY_IP["$ourIP"]="$localEcho"
fi

# ------------------------------------------------------------
# Parse /etc/hosts into:
#   - set of 10.x IPs
#   - preferred hostname per IP (favor FrogNetHost.<Domain>)
# ------------------------------------------------------------
declare -A IP_HOST
declare -A IP_SEEN

while IFS= read -r line; do
  line="${line%%#*}"
  line="$(echo "$line" | xargs || true)"
  [[ -z "$line" ]] && continue

  read -ra parts <<< "$line"
  ip="${parts[0]}"
  [[ "$ip" != 10.* ]] && continue

  # .2 admin alias is a probe-only phantom and MUST NOT appear in the
  # host JSON.  Drop the entry outright if the IP ends in .2 or if the
  # line carries a FrogNetAdmin.* label (defense against stale
  # /etc/hosts from older builds).
  [[ "$ip" == *.2 ]] && continue
  _skip_admin=0
  for hn in "${parts[@]:1}"; do
    if [[ "$hn" == FrogNetAdmin.* ]]; then _skip_admin=1; break; fi
  done
  (( _skip_admin )) && continue

  IP_SEEN["$ip"]=1

  chosen=""
  for hn in "${parts[@]:1}"; do
    [[ -z "$hn" ]] && continue
    [[ "$hn" == "databasehost.frognet" ]] && continue
    if [[ "$hn" == FrogNetHost.* ]]; then
      chosen="$hn"
      break
    fi
  done
  if [[ -z "$chosen" ]]; then
    for hn in "${parts[@]:1}"; do
      [[ -z "$hn" ]] && continue
      [[ "$hn" == "databasehost.frognet" ]] && continue
      chosen="$hn"
      break
    done
  fi
  [[ -z "$chosen" ]] && chosen="$ip"

  if [[ -z "${IP_HOST[$ip]:-}" ]]; then
    IP_HOST["$ip"]="$chosen"
  fi
done < "$ETC_HOSTS"

# ------------------------------------------------------------
# Recreate missing echo entries (best-effort)
# ONLY when forced (merge calls Rebuild)
# ------------------------------------------------------------
fetch_echo_for_ip() {
  local ip="$1"
  local out=""

  # Try direct app port first (avoids gateway OUTPUT redirect of 10/8:80)
  # out="$(curl -fsS --connect-timeout 10 --max-time 45 "http://${ip}/frognet_echo.php" || true)"
  # out="$(echo "$out" | tr -d '\r' | tr -d '\n')"
  # if is_valid_echo "$out"; then
    # echo "$out"
    # return 0
  # fi

  # Fallback: standard port 80 (may be proxied/semantic on that remote)
  out="$(curl -fsS --connect-timeout 10 --max-time 45 "http://${ip}/frognet_echo.php" || true)"
  out="$(echo "$out" | tr -d '\r' | tr -d '\n')"
  if is_valid_echo "$out"; then
    echo "$out"
    return 0
  fi

  return 1
}

if [[ -n "$forceRebuild" ]]; then
  # Collect IPs that need echo recreation
  declare -a _rebuild_ips=()
  for ip in "${!IP_SEEN[@]}"; do
    if [[ -n "${ECHO_BY_IP[$ip]:-}" ]] && is_valid_echo "${ECHO_BY_IP[$ip]}"; then
      continue
    fi
    _rebuild_ips+=("$ip")
  done

  if ((${#_rebuild_ips[@]})); then
    # Launch all echo fetches in parallel — each writes to a temp file
    _rebuild_dir="$(mktemp -d /tmp/makeHostJson_rebuild.XXXXXX)"
    _rebuild_pids=()

    for ip in "${_rebuild_ips[@]}"; do
      _sip="${ip//\./_}"
      (
        _out="$(curl -fsS --connect-timeout 10 --max-time 45 "http://${ip}/frognet_echo.php" 2>/dev/null || true)"
        _out="$(echo "$_out" | tr -d '\r' | tr -d '\n')"
        echo "$_out" > "${_rebuild_dir}/echo_${_sip}"
      ) &
      _rebuild_pids+=($!)
    done

    # Wait for ALL parallel fetches
    for _pid in "${_rebuild_pids[@]}"; do
      wait "$_pid" 2>/dev/null || true
    done

    # Collect results
    for ip in "${_rebuild_ips[@]}"; do
      _sip="${ip//\./_}"
      _rfile="${_rebuild_dir}/echo_${_sip}"
      [[ -f "$_rfile" ]] || continue
      echoVal="$(cat "$_rfile")"
      if is_valid_echo "$echoVal"; then
        ECHO_BY_IP["$ip"]="$echoVal"
      fi
    done

    rm -rf "$_rebuild_dir"
  fi
fi

# ------------------------------------------------------------
# Emit JSON array (ALWAYS include all 10.x IPs found)
# ------------------------------------------------------------
tmp="${OLD_JSON}.tmp"
{
  echo "["
  first=1
  for ip in "${!IP_SEEN[@]}"; do
    hn="${IP_HOST[$ip]:-$ip}"
    echoVal="${ECHO_BY_IP[$ip]:-}"

    # Ensure echo is a string (may be empty)
    echoVal="$(echo "${echoVal}" | tr -d '\r' | tr -d '\n')"

    if [[ $first -eq 0 ]]; then
      echo ","
    fi
    first=0

    if command -v jq >/dev/null 2>&1 ; then
      jq -n --arg ip "$ip" --arg hostname "$hn" --arg echo "$echoVal" \
        '{ip:$ip, hostname:$hostname, echo:$echo}'
    else
      esc() { echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
      echo "{\"ip\":\"$(esc "$ip")\",\"hostname\":\"$(esc "$hn")\",\"echo\":\"$(esc "$echoVal")\"}"
    fi
  done
  echo
  echo "]"
} > "$tmp"

mv -f "$tmp" "$OLD_JSON"
chmod 777 "$OLD_JSON"
cat "$OLD_JSON"
exit 0
