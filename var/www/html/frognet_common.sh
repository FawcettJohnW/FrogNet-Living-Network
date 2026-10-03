#!/usr/bin/env bash
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
# frognet_common.sh - shared helpers for FrogNet curl scripts
set -euo pipefail
: "${BASE_URL:=http://databasehost.frognet/api.php}"

urlenc() {
  python3 - <<'PY' <<<"$1"
import sys, urllib.parse
print(urllib.parse.quote(sys.stdin.read(), safe=''))
PY
}

build_qs() {
  local qs=""
  local arg key val enc
  for arg in "$@"; do
    key="${arg%%=*}"; val="${arg#*=}"
    enc="$(urlenc "$val")"
    if [[ -z "$qs" ]]; then qs="${key}=${enc}"; else qs="${qs}&${key}=${enc}"; fi
  done
  echo "$qs"
}

json_payload() {
  python3 - "$@" <<'PY'
import sys, json
args=sys.argv[1:]
it=iter(args)
obj={}
for k,v in zip(it,it):
    obj[k]=v
print(json.dumps(obj))
PY
}

api_get()    { local entity="$1"; local action="$2"; shift 2; local qs="$(build_qs "$@")"; curl -sS -X GET    "$BASE_URL?entity=${entity}&action=${action}${qs:+&${qs}}" | jq .; }
api_post()   { local entity="$1"; local action="$2"; local json="$3"; curl -sS -X POST "$BASE_URL?entity=${entity}&action=${action}" -H "Content-Type: application/json" --data "$json" | jq .; }
api_put()    { local entity="$1"; local action="$2"; local json="$3"; curl -sS -X PUT  "$BASE_URL?entity=${entity}&action=${action}" -H "Content-Type: application/json" --data "$json" | jq .; }
api_delete() { local entity="$1"; local action="$2"; shift 2; local qs="$(build_qs "$@")"; curl -sS -X DELETE "$BASE_URL?entity=${entity}&action=${action}${qs:+&${qs}}" | jq .; }

api_list() { local entity="$1"; shift; api_get "$entity" "list" "$@"; }
