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
# /usr/local/bin/frognet_alive.bash
#
# FrogNet aliveness probe — sends frognet_echo.php through the proxy.
#
# The proxy owns the persistent daemon-to-daemon connection.
# We never open our own connections.  We ask the proxy to reach the
# target via semantic transport (REQ_REPEAT / RESP_SAME when cached).
#
# One probe, one target: curl http://127.0.0.1:80/frognet_echo.php with Host: <target> (an IP as given, a name as
# <name>.frognet). The address asked is the address probed: .2 is never answered by .1 nor .1 by .2
# [ALIVE_ONE_TARGET_V1]. A failure is said on stderr; the caller's next event (the next merge) asks again.
#
# Usage:
#   frognet_alive.bash <ip_or_hostname> [timeout_sec]
#
# Exit:  0 = alive, 1 = unreachable, 2 = usage error
#
# Stdout (on success):
#   <rtt_ms>|proxy
#   e.g.  "12|proxy"
#
# Stdout (on failure): empty
#
# Environment:
#   FROGNET_ALIVE_TIMEOUT   override default timeout (seconds, default 5)

TARGET="${1:-}"
TIMEOUT="${2:-${FROGNET_ALIVE_TIMEOUT:-10}}"

[[ -z "$TARGET" ]] && { echo "Usage: $0 <ip_or_hostname> [timeout]" >&2; exit 2; }

# Resolve target to a Host header the proxy will route.
# If it looks like an IP, use it directly.
# If it looks like a hostname (no dots or .frognet), append .frognet.
resolve_host() {
  local t="$1"
  if [[ "$t" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$t"
  elif [[ "$t" == *.frognet ]]; then
    echo "$t"
  else
    echo "${t}.frognet"
  fi
}

HOST=$(resolve_host "$TARGET")

# ── Probe through proxy ────────────────────────────────────────
# Single curl to 127.0.0.1:80 with Host header for the target.
# The proxy routes this through the existing daemon connection.
# On a cached endpoint: REQ_REPEAT + RESP_SAME ≈ 40 bytes on the wire.
probe() {
  local host="$1"
  local start_ns end_ns elapsed_ms out

  start_ns=$(date +%s%N)
  out=$(/usr/bin/curl -fsS \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        -H "Host: ${host}" \
        "http://127.0.0.1:80/frognet_echo.php" 2>&1) || { LAST_ERR="$out"; return 1; }

  # Validate: must have at least 3 commas and a 10.x IP in field 2
  local commas
  commas=$(/usr/bin/awk -F',' '{print NF-1}' <<<"$out")
  [[ "$commas" -ge 3 ]] || { LAST_ERR="not an echo: ${out:0:80}"; return 1; }
  IFS=',' read -r _ fld2 _ _ <<<"$out"
  fld2="$(/usr/bin/xargs <<<"$fld2")"
  [[ "$fld2" == 10.* ]] || { LAST_ERR="echo without a 10.x address: ${out:0:80}"; return 1; }

  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  [[ "$elapsed_ms" -lt 1 ]] && elapsed_ms=1
  RESULT="${elapsed_ms}|proxy"
  return 0
}

# ── Main ────────────────────────────────────────────────────────
# The same probe of the same target up to 3 times, 0.2 s apart (the proxy's semantic cache warms across
# REQ_FULL -> REQ_REPEAT -> REP_SAME). Not a second mechanism: one target, one path.
LAST_ERR=""; RESULT=""
for attempt in 1 2 3; do
  if probe "$HOST"; then
    echo "$RESULT"
    exit 0
  fi
  [[ $attempt -lt 3 ]] && sleep 0.2
done
echo "frognet_alive: ${HOST} did not answer through the proxy: ${LAST_ERR}" >&2
exit 1
