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
# /usr/local/bin/frognet_discovery_cache.sh
# Discovery cache helper - stores/retrieves peer info from MySQL
#
# v3 - 2026-02-05
# Fixes:
#   - Vouched peers BYPASS cache entirely (was: shorter window, same threshold — useless)
#   - $3 (voucher) is actually passed through the case dispatch
#   - FailCount capped at 50 to prevent runaway accumulation from shared DB
#   - success clears LastFailUTC (was: left it set, so WHERE could still match)
#   - All mysql errors go to stderr, never masked with 2>/dev/null
#   - Added: flush, reset, status commands

MYSQL="/usr/bin/mysql"
DB="FrogNet"

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
SKIP_WINDOW_SEC=300       # ignore failures older than 5 minutes
SKIP_FAIL_THRESHOLD=1    # need 10+ failures in window before skipping

log_cache() {
    echo "[discovery_cache][$(date '+%Y-%m-%d %H:%M:%S.%3N')] $*" >&2
}

# ---------------------------------------------------------------------------
# should_skip <peer_ip> [voucher_ip]
#
# Returns 0 = skip (don't probe), 1 = try (do probe)
#
# A "voucher" is the next-hop / attesting neighbor a caller has for peer_ip.
# Historically a non-empty voucher unconditionally bypassed the failure
# cache — the theory being "a live neighbor reports it, so it must be
# reachable." That theory is wrong: a neighbor's getHosts can list stale
# entries (e.g. peer was renamed/re-IP'd), and the voucher alone is not
# proof of reachability.  Letting it bypass blindly puts us in a loop:
# stale neighbor advertises ghost, we probe ghost, fail, neighbor still
# advertises ghost, repeat forever.
#
# New rule: vouchers bypass the failure cache ONLY when we have our own
# local success record (FailCount=0 and LastSeenUTC populated) for
# peer_ip.  That's the actual evidence that the address is reachable;
# the voucher then earns its rescue role of re-probing a known-good peer
# despite cached failures.  Unverified vouchers fall through to the
# normal failure-skip logic, so a ghost gets at most SKIP_FAIL_THRESHOLD
# wasted probes before the cache shuts it out.
# ---------------------------------------------------------------------------
discovery_cache_should_skip() {
    # [NO_MEMORY_V1] FrogNet proves reachability every pass.  A prior failure is
    # not evidence about this pass, and a prior success is not a licence to skip
    # proving it again.  This function used to consult TransitPeerCache
    # (FailCount/LastFailUTC inside SKIP_WINDOW_SEC) and to honour a voucher
    # shortcut; both made one bad probe silence a peer for the rest of the
    # window.  Always probe.  Kept as a function so callers need no change.
    :
    return 1
}

# ---------------------------------------------------------------------------
# success <peer_ip> <local_ip> <dev> <hostname> <hostpath> [rtt_ms]
# ---------------------------------------------------------------------------
discovery_cache_success() {
    local peer_ip="$1"
    local local_ip="$2"
    local dev="$3"
    local hostname="$4"
    local hostpath="$5"
    local rtt_ms="${6:-NULL}"

    local out
    out=$($MYSQL "$DB" 2>&1 <<EOSQL
INSERT INTO TransitPeerCache (PeerIP, LocalIP, Dev, HostName, HostPath, LastSeenUTC, FailCount, LastRttMs)
VALUES ('$peer_ip', '$local_ip', '$dev', '$hostname', '$hostpath', NOW(), 0, $rtt_ms)
ON DUPLICATE KEY UPDATE
    LocalIP   = VALUES(LocalIP),
    Dev       = VALUES(Dev),
    HostName  = VALUES(HostName),
    HostPath  = VALUES(HostPath),
    LastSeenUTC = NOW(),
    FailCount   = 0,
    LastFailUTC = NULL,
    LastRttMs   = COALESCE(VALUES(LastRttMs), LastRttMs);
EOSQL
    )

    if [[ $? -ne 0 ]]; then
        log_cache "ERROR success mysql failed for $peer_ip: $out"
    fi
}

# ---------------------------------------------------------------------------
# fail <peer_ip> <dev>
# ---------------------------------------------------------------------------
discovery_cache_fail() {
    local peer_ip="$1"
    local dev="$2"

    local out
    out=$($MYSQL "$DB" 2>&1 <<EOSQL
INSERT INTO TransitPeerCache (PeerIP, LocalIP, Dev, LastFailUTC, FailCount)
VALUES ('$peer_ip', '', '$dev', NOW(), 1)
ON DUPLICATE KEY UPDATE
    LastFailUTC = NOW(),
    FailCount   = LEAST(FailCount + 1, 50);
EOSQL
    )

    if [[ $? -ne 0 ]]; then
        log_cache "ERROR fail mysql failed for $peer_ip: $out"
    fi
}

# ---------------------------------------------------------------------------
# get <peer_ip>
# ---------------------------------------------------------------------------
discovery_cache_get() {
    local peer_ip="$1"

    $MYSQL -N -B "$DB" <<EOSQL
SELECT CONCAT(COALESCE(HostName,''), '|', COALESCE(HostPath,''), '|', Dev)
FROM TransitPeerCache
WHERE PeerIP='$peer_ip'
  AND LastSeenUTC > DATE_SUB(NOW(), INTERVAL 5 MINUTE)
  AND HostPath IS NOT NULL
LIMIT 1;
EOSQL
}

# ---------------------------------------------------------------------------
# list [minutes]
# ---------------------------------------------------------------------------
discovery_cache_list_recent() {
    local minutes="${1:-5}"

    $MYSQL -N -B "$DB" <<EOSQL
SELECT PeerIP, HostName, HostPath, Dev
FROM TransitPeerCache
WHERE LastSeenUTC > DATE_SUB(NOW(), INTERVAL $minutes MINUTE)
  AND HostPath IS NOT NULL;
EOSQL
}

# ---------------------------------------------------------------------------
# cleanup [hours]
# ---------------------------------------------------------------------------
discovery_cache_cleanup() {
    local hours="${1:-24}"

    local out
    out=$($MYSQL "$DB" 2>&1 <<EOSQL
DELETE FROM TransitPeerCache
WHERE (LastSeenUTC < DATE_SUB(NOW(), INTERVAL $hours HOUR) OR LastSeenUTC IS NULL)
  AND FailCount > 10;
EOSQL
    )

    if [[ $? -ne 0 ]]; then
        log_cache "ERROR cleanup mysql failed: $out"
    fi
}

# ---------------------------------------------------------------------------
# reset <peer_ip>   — manually clear a single poisoned entry
# ---------------------------------------------------------------------------
discovery_cache_reset() {
    local peer_ip="$1"
    if [[ -z "$peer_ip" ]]; then
        echo "Usage: $0 reset <peer_ip>" >&2
        return 1
    fi

    local out
    out=$($MYSQL "$DB" 2>&1 <<EOSQL
UPDATE TransitPeerCache
SET FailCount = 0, LastFailUTC = NULL
WHERE PeerIP = '$peer_ip';
EOSQL
    )

    if [[ $? -ne 0 ]]; then
        log_cache "ERROR reset mysql failed for $peer_ip: $out"
    else
        log_cache "RESET $peer_ip — FailCount=0, LastFailUTC=NULL"
    fi
}

# ---------------------------------------------------------------------------
# flush   — clear ALL cache entries.  Called by runMerge at the start of
# every merge cycle so each merge starts with a clean slate: every
# destination gets re-probed, no SKIP_CACHED entries carry over.
# Previously only zeroed FailCount/LastFailUTC, leaving rows in place
# which still influenced discovery_cache_should_skip decisions via
# other fields (LastSeenUTC, HostPath).  DELETE is the correct shape
# for "everything new every time."
# ---------------------------------------------------------------------------
discovery_cache_flush() {
    local out
    out=$($MYSQL "$DB" 2>&1 <<EOSQL
DELETE FROM TransitPeerCache;
EOSQL
    )

    if [[ $? -ne 0 ]]; then
        log_cache "ERROR flush mysql failed: $out"
    else
        log_cache "FLUSH — all entries deleted"
    fi
}

# ---------------------------------------------------------------------------
# status   — show current cache state
# ---------------------------------------------------------------------------
discovery_cache_status() {
    $MYSQL "$DB" -e "
SELECT PeerIP,
       HostName,
       FailCount,
       TIMESTAMPDIFF(SECOND, LastFailUTC, NOW()) AS fail_age_sec,
       TIMESTAMPDIFF(SECOND, LastSeenUTC, NOW()) AS seen_age_sec,
       Dev
FROM TransitPeerCache
ORDER BY FailCount DESC, PeerIP;
" 2>&1
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "${1:-}" in
    should_skip) discovery_cache_should_skip "$2" "$3" ;;
    success)     discovery_cache_success "$2" "$3" "$4" "$5" "$6" "${7:-}" ;;
    fail)        discovery_cache_fail "$2" "$3" ;;
    get)         discovery_cache_get "$2" ;;
    list)        discovery_cache_list_recent "${2:-5}" ;;
    cleanup)     discovery_cache_cleanup "${2:-24}" ;;
    reset)       discovery_cache_reset "$2" ;;
    flush)       discovery_cache_flush ;;
    status)      discovery_cache_status ;;
    *)           echo "Usage: $0 {should_skip|success|fail|get|list|cleanup|reset|flush|status} [args...]" >&2 ;;
esac
