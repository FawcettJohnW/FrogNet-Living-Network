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
# oracle_avcap_cache.sh — prove the capability probe won't republish a stale cache.
# Seeds the cache path with the incident poison (mem_available_kb as a DICT), runs
# the probe, and checks the emitted numeric fields are scalars.
#   old probe: returns the cache verbatim -> dict -> FAIL
#   new probe: rejects stale schema, recomputes -> scalar -> PASS
# Backs up/restores any pre-existing cache. Usage: oracle_avcap_cache.sh <probe.sh>
set -u
PROBE="${1:?usage: oracle_avcap_cache.sh <probe.sh>}"
CACHE="${FROGNET_AVCAP_CACHE:-/run/frognet_avcap.json}"
HERE="$(cd "$(dirname "$0")" && pwd)"

BACKUP=""
if [ -f "$CACHE" ]; then BACKUP="$(mktemp)"; cp "$CACHE" "$BACKUP"; fi
restore() { if [ -n "$BACKUP" ]; then cp "$BACKUP" "$CACHE"; rm -f "$BACKUP"; else rm -f "$CACHE"; fi; }
trap restore EXIT

mkdir -p "$(dirname "$CACHE")" 2>/dev/null || true
printf '%s' '{"cores":4,"cpu_mhz":1800,"cpu_bench_total":1234.5,"mem_total_kb":8000000,"mem_available_kb":{"MemAvailable":4000000},"mysql_innodb_pool_bytes":1073741824,"disk_write_mbps":120.0,"disk_fsync_ms":2.0,"disk_free_gb":50.0,"lan_ip":"10.102.60.1","av_port":9000,"arch":"x86_64"}' > "$CACHE"

OUT="$(FROGNET_AVCAP_CACHE="$CACHE" bash "$PROBE" 2>/dev/null)"
if [ -z "$OUT" ]; then echo "RESULT: ERROR — empty probe output"; exit 2; fi
printf '%s' "$OUT" | python3 "$HERE/check_blob.py"
