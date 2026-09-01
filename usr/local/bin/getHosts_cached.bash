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
# /usr/local/bin/getHosts_cached.bash
#!/bin/bash
# getHosts_cached.bash
#
# Usage:
#   getHosts_cached.bash <host_ip>
#
# Behavior:
#   - Cache by host_ip with a sha/ttl file in /etc/sentinels/gethosts_sha_cache.tsv
#   - Fetch getHosts from :8080 first (direct app), fallback to :80.
#   - Output must be a JSON array (possibly empty array "[]").

HOST_IP="${1:-}"
[[ -z "$HOST_IP" ]] && exit 2

SENT_DIR="/etc/sentinels"
CACHE_JSON="${SENT_DIR}/gethosts_${HOST_IP}.json"
META="${SENT_DIR}/gethosts_sha_cache.tsv"
mkdir -p "$SENT_DIR"
touch "$META"

TTL_SEC="${FROGNET_GETHOSTS_TTL_SEC:-30}"
now="$(date +%s)"

is_json_array() {
  local s="${1:-}"
  [[ -z "$s" ]] && return 1
  echo "$s" | jq -e 'if type=="array" then true else false end' >/dev/null 2>&1
}

# Meta format: host_ip \t epoch \t sha256
get_meta() {
  awk -F'\t' -v k="$HOST_IP" '{if($1==k){print $2 "\t" $3; exit}}' "$META" 2>/dev/null || true
}

meta="$(get_meta || true)"
meta_ts="$(echo "$meta" | awk -F'\t' '{print $1}' || true)"
meta_sha="$(echo "$meta" | awk -F'\t' '{print $2}' || true)"

if [[ -n "$meta_ts" && -n "$meta_sha" && -f "$CACHE_JSON" ]]; then
  age=$(( now - meta_ts ))
  if (( age <= TTL_SEC )); then
    cat "$CACHE_JSON"
    exit 0
  fi
fi

fetch() {
  local url="$1"
  curl -fsS --connect-timeout 10 --max-time 20 "$url" 2>/dev/null || true
}

out="$(fetch "http://${HOST_IP}/getHosts.php")"
out="$(echo "$out" | tr -d '\r')"
if ! is_json_array "$out"; then
  out="$(fetch "http://${HOST_IP}:8080/getHosts.php")"
  out="$(echo "$out" | tr -d '\r')"
fi

if is_json_array "$out"; then
  sha="$(echo -n "$out" | sha256sum | awk '{print $1}')"
  tmp="$(mktemp)"
  awk -F'\t' -v k="$HOST_IP" '$1!=k {print}' "$META" > "$tmp" 2>/dev/null || true
  printf "%s\t%s\t%s\n" "$HOST_IP" "$now" "$sha" >> "$tmp"
  mv "$tmp" "$META"
  echo "$out" > "$CACHE_JSON"
  cat "$CACHE_JSON"
  exit 0
fi

# If fetch failed, return empty array (caller treats as no children)
echo "[]"
exit 0
