<?php
/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
/*
 * propogateNotification.php  [NOTIFY_GUID_DEDUP_V1]
 *
 * Receive a propagation notification from a neighbor and, if it is a wave we
 * have not already accounted for, queue a poke for the merge accumulator
 * (frognet-merge-watcher). No systemctl, no exec, no per-event @.service: this
 * is a straightforward back-end call.
 *
 *   - $event is a GUID minted once by the merge that started the wave and
 *     carried unchanged through the epidemic.
 *   - seen-set  (/run/frognet/seen_notifications/<guid>) is the exact recursion
 *     guard: a GUID we have already scheduled/run is dropped, so a wave cannot
 *     loop back and spawn another merge. It is recorded at SCHEDULE time and
 *     outlives the poke, so a late echo after the merge already ran is still
 *     dropped (reboot clears /run, which is fine).
 *   - poke queue (/run/frognet/merge_requests/<guid>) is the not-yet-merged
 *     work; the accumulator consumes it when its resettable timer expires.
 */
$event = $_GET['event'] ?? '';
if ($event === '') {
    http_response_code(400);
    echo "missing event\n";
    exit;
}

// GUID -> safe filename.
$guid = preg_replace('/[^A-Za-z0-9_.-]/', '_', $event);

$seen_dir = '/run/frognet/seen_notifications';
$req_dir  = '/run/frognet/merge_requests';
@mkdir($seen_dir, 01777, true);
@mkdir($req_dir, 01777, true);

$seen = "$seen_dir/$guid";

// Atomic claim: O_CREAT|O_EXCL. If it already exists, this is an echo of a
// wave we have already accounted for -> drop, no poke, no merge.
$fp = @fopen($seen, 'x');
if ($fp === false) {
    http_response_code(200);
    echo "DUP\n";
    exit;
}
fwrite($fp, gmdate('c') . "\n");
fclose($fp);

// New wave: queue a poke. Creating the file is the accumulator's reset signal.
@touch("$req_dir/$guid");

http_response_code(200);
echo "OK\n";
