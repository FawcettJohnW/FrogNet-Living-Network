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
# frognet-merge-watcher.sh
#
# [MERGE_ACCUMULATOR_V1] Resettable-timer merge accumulator.
#
# Two distinct jobs, kept separate:
#
#   1. Recursion guard (exact, GUID-keyed) lives in propogateNotification.php:
#      every notification carries a GUID minted once by the merge that started
#      the wave. The .php drops a poke ONLY for a GUID it has not seen
#      (/run/frognet/seen_notifications/<guid>); an echo of a GUID already
#      seen is dropped, so a wave cannot loop back and re-trigger merges.
#
#   2. Coalescing (this service): genuinely distinct waves -- several real
#      changes landing close together, or a LAN device storm with many
#      distinct GUIDs -- are batched. Every new poke RESETS a 30s timer; the
#      merge fires only once the timer expires with no new poke. Many valid
#      GUIDs in one window => exactly one merge.
#
# At expiry we decide ONCE, against the same lock runMerge uses:
#   - lock free  -> no merge running: consume the batch and start one.
#   - lock held  -> a merge is already running, and it may have read the world
#                   before this batch's events happened. The batch STAYS in
#                   $REQ_DIR; we wait on the lock (flock: the kernel returns the
#                   moment that merge exits, no polling), then consume the batch
#                   and start a merge. [REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1]
#
# $REQ_DIR holds nothing but pokes, so unrelated /run/frognet writes
# (mergePending, runAgain, sentinels, the daemon) can't reset the timer.
##############################################################
set -u

# [SIM_ROOT_REDIRECT_V1] the four paths and the window can be pointed elsewhere by an oracle that runs THIS script
# (discovery/test_merge_request_kept_oracle.py). Unset, which is every node: the shipped values.
REQ_DIR="${FROGNET_MERGE_REQ_DIR:-/run/frognet/merge_requests}"
SEEN_DIR="${FROGNET_MERGE_SEEN_DIR:-/run/frognet/seen_notifications}"
LOCKFILE="${FROGNET_MERGE_LOCKFILE:-/var/run/runMerge.lock}"
RUN_MERGE="${FROGNET_MERGE_CONTROLLER:-/usr/local/bin/runMerge.bash}"
DEBOUNCE_SEC="${FROGNET_MERGE_DEBOUNCE_SEC:-30}"

mkdir -p "$REQ_DIR"
chmod 1777 "$REQ_DIR" 2>/dev/null || true   # sticky world-write: www-data (.php) drops pokes; only owner/root removes

# [WATCHER_HAS_INOTIFY_V1 - T4c] inotifywait is how this waits (the installer's A2 installs inotify-tools). Without it
# every wait failed at once and the loop below became a 1 s poll firing each request with no window: that second
# mechanism is gone. Missing: said, exit 1; systemd starts it again (Restart=on-failure) and the requests wait in
# $REQ_DIR until it can run.
command -v inotifywait >/dev/null 2>&1 \
    || { echo "[MERGE-ACCUM] inotifywait not found (install inotify-tools): not watching $REQ_DIR" >&2; exit 1; }

_pending() { compgen -G "$REQ_DIR/*" >/dev/null 2>&1; }

# [REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1] A request leaves $REQ_DIR only when a merge is started for it, and that
# merge starts after every event in the batch: it reads the world as those events left it.
#
# What stood here consumed the batch first and, when the lock was held, wrote /etc/sentinels/runAgain for the running
# merge and was done. runMerge.bash reads runAgain ONCE, straight after its discovery stage; makeHostJson, the
# propagation and the exit come after that read with the lock still held (measured on 16 merges, 2026-11-28: 0.8 to
# 2.2 s each). A batch whose window expired in that stretch found the lock held, wrote a flag nothing would read
# again (the next merge clears it at its top), and was gone: the event got no merge. And when the lock's holder was a
# merge that would never finish (dhcp_tracking.bash [LEASE_ASKS_THE_WATCHER_V1]: systemd killing it 90 s later), the
# same - which is how H3 never learned that H5 had joined (10:11:58 `fire decision=set_runAgain reason=merge_running`,
# the holder SIGKILLed at 10:12:25, no merge after).
#
# The wait is `flock` with no -n: the kernel wakes it when the holder's descriptor closes - when that merge exits,
# however it exits. No polling, no timer. Requests that arrive during the wait land in $REQ_DIR and go with this batch.
# A runMerge.bash re-invoking itself (its own runAgain) closes the lock before it execs; if this takes the lock in
# that gap, that pass bails as any second runMerge does and the merge started below is the pass it would have been.
_fire() {
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        echo "[MERGE-ACCUM] fire decision=wait reason=merge_running requests=$(ls "$REQ_DIR" 2>/dev/null | tr '\n' ' ')(kept; their merge starts when the running one ends) [REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1]" >&2
        flock 9
    fi
    # the lock is ours: no merge is running. Consume the batch, release, start its merge.
    _batch="$(ls "$REQ_DIR" 2>/dev/null | tr '\n' ' ')"
    rm -f "$REQ_DIR"/* 2>/dev/null || true
    flock -u 9; exec 9>&-
    echo "[MERGE-ACCUM] fire decision=fork reason=lock_free requests=${_batch}" >&2
    FROGNET_MERGE_WHY=watcher:poke "$RUN_MERGE" &        # background: keep coalescing the next window
}

while true; do
    # [QUEUE_SURVIVES_THE_WIPE_V1 - T4b] the watcher owns its queue directory: make it again if anything removed it
    # and the seen-set beside it: propogateNotification.php runs as www-data and must create files in both (a seen
    # directory it could not write made every notification "DUP": T4b G2, h2 told h1 and h1 never merged).
    for _d in "$REQ_DIR" "$SEEN_DIR"; do
        [[ -d "$_d" && "$(stat -c %a "$_d")" == 1777 ]] && continue
        mkdir -p "$_d" && chmod 1777 "$_d" && echo "[MERGE-ACCUM] $_d made (1777)" >&2
    done
    # Idle until the first poke (skip the block if pokes are already queued,
    # e.g. ones that arrived while a previous merge was running).
    if ! _pending; then
        inotifywait -qq -e create -e moved_to -e close_write "$REQ_DIR" \
            || { echo "[MERGE-ACCUM] inotifywait on $REQ_DIR failed rc=$? (above); waiting again" >&2; sleep 1; continue; }
    fi

    # Resettable timer: each poke returns rc=0 and re-arms the wait; a timeout
    # (rc!=0) with no poke for the full window falls through to fire.
    # rc 0: a poke inside the window -> reset; rc 2: the window passed quiet -> fire; anything else is said
    while :; do
        inotifywait -qq -t "$DEBOUNCE_SEC" -e create -e moved_to -e close_write "$REQ_DIR"; _rc=$?
        [[ $_rc -eq 0 ]] || break   # poke arrived inside the window -> reset (loop again)
    done
    [[ $_rc -eq 2 ]] || echo "[MERGE-ACCUM] inotifywait window ended rc=$_rc (not the quiet timeout, 2)" >&2

    _pending && _fire
done
