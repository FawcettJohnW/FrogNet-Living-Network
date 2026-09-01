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
# /usr/local/bin/getFrogNet.bash
#
# Returns one CSV line: fqdn,eth0IP,wlan0IP,wlan1IP
#
# Behavior: answer is cached in /etc/sentinels/getFrogNet.cache and
# reused across invocations. Identity doesn't change during normal
# operation — computing it on every call (from frognet_echo.php, which
# runs thousands of times per hour) was the bottleneck that wedged
# Apache workers in anon_pipe_read waiting on slow sub-probes.
#
# Fast path (cache present): single cat, no forks, no subprocess,
# sub-millisecond.
#
# Slow path (cache absent, first call after reboot / cache cleared):
# one caller wins a flock, computes, writes atomically. All other
# concurrent callers block on the lock briefly, then hit the fast path.
#
# Each compute sub-probe is bounded by `timeout 2` so a single stuck
# helper (bad DHCP, stuck wlan, broken script) can't wedge this script
# indefinitely — an empty field is returned on timeout.
#
# Cache invalidation: delete /etc/sentinels/getFrogNet.cache when
# network identity could have changed. Reasonable trigger points:
#   - boot (add `rm -f /etc/sentinels/getFrogNet.cache` to a tmpfiles.d
#     entry or a oneshot systemd unit before apache2.service)
#   - post-up / post-down hooks on eth0/wlan0/wlan1
#   - frognet-tunnel-register or any DHCP-renewal hook

set -u

CACHE_FILE="/etc/sentinels/getFrogNet.cache"
LOCK_FILE="/etc/sentinels/getFrogNet.lock"
SUBCALL_TIMEOUT=2   # seconds; bound each helper invocation

# [NO_CACHE_GARBAGE_V1] The name field falls back to `hostname -s` — the generic
# "FrogNetHost" every node carries — whenever getOurDomain times out. A cached
# line whose name equals that generic is GARBAGE: it poisons every echo of this
# node (peers read the generic name and treat it as no real identity -> blank
# authoritative -> merge livelock), and the only cure has been deleting the cache
# by hand. Capture the generic once so both read and write can guard against it.
SELF_GENERIC="$(hostname -s)"

# ---- fast path -----------------------------------------------------------
if [[ -s "$CACHE_FILE" ]]; then
    cached_line="$(cat "$CACHE_FILE")"
    cached_name="${cached_line%%,*}"
    if [[ -n "$cached_name" && "$cached_name" != "$SELF_GENERIC" ]]; then
        printf '%s\n' "$cached_line"
        exit 0
    fi
    # garbage (name == generic fallback): drop it and recompute below (self-heal)
    rm -f "$CACHE_FILE" 2>/dev/null
fi

# ---- slow path: serialize computation -----------------------------------
mkdir -p /etc/sentinels 2>/dev/null

# flock -w 3: if we can't get the lock in 3s, don't pile up behind a
# stuck compute — fall through and attempt our own (worst case, two
# callers compute; last write wins atomically).
exec 9>"$LOCK_FILE"
flock -w 3 9 || true

# Re-check cache: another caller may have just populated it.
if [[ -s "$CACHE_FILE" ]]; then
    cat "$CACHE_FILE"
    exit 0
fi

# Compute. Each helper bounded by SUBCALL_TIMEOUT. On timeout / failure
# the field becomes empty string — matches the original script's `|| echo ""`.
. /usr/local/bin/mapInterfaces

fqdn="$(timeout "$SUBCALL_TIMEOUT" /usr/local/bin/getOurDomain 2>/dev/null)"
domain_ok=1
if [[ -z "$fqdn" ]]; then
    fqdn="$SELF_GENERIC"      # getOurDomain failed -> generic fallback
    domain_ok=0               # degraded: return it for THIS call, but never cache it
fi

eth0IP="$(timeout  "$SUBCALL_TIMEOUT" /usr/local/bin/getEth0Address 2>/dev/null)"
wlan0IP="$(timeout "$SUBCALL_TIMEOUT" /usr/local/bin/getWlan0IP     2>/dev/null)"
wlan1IP="$(timeout "$SUBCALL_TIMEOUT" /usr/local/bin/getWlan1IP     2>/dev/null)"

line="${fqdn},${eth0IP},${wlan0IP},${wlan1IP}"

# Atomic publish: write to a temp file in the same directory, then rename.
# Any concurrent reader either sees the old file (possibly empty/absent)
# or the fully-written new one — never a half-written line.
tmp="$(mktemp /etc/sentinels/.getFrogNet.cache.XXXXXX 2>/dev/null)" || tmp=""
if [[ -n "$tmp" && "$domain_ok" -eq 1 ]]; then
    printf '%s\n' "$line" > "$tmp"
    mv -f "$tmp" "$CACHE_FILE" 2>/dev/null
elif [[ -n "$tmp" ]]; then
    rm -f "$tmp" 2>/dev/null   # [NO_CACHE_GARBAGE_V1] degraded name: don't persist garbage
fi

printf '%s\n' "$line"
