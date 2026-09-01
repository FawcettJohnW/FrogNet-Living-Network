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
// Chat workload: 50-message rolling window viewer (matches FrogApp.Chat.{Channel}
// rolling-jsonData architecture). ~20% chance of a new message having arrived
// since last fetch — simulates poll traffic on a quiet channel.
$WINDOW = 50;
$NEW_PROB = 20; // percent

$messages = [];
for ($i = 0; $i < $WINDOW - 1; $i++) {
    $messages[] = [
        'idx' => $i,
        'sender' => sprintf('user_%02d', $i % 8),
        'ts' => sprintf('2026-05-09 %02d:%02d:%02d', ($i / 4) % 24, ($i * 7) % 60, ($i * 13) % 60),
        'body' => sprintf('Stable message %02d: chat history from the rolling window. Body content is fixed for compression baseline.', $i),
    ];
}
if (rand(1, 100) <= $NEW_PROB) {
    $messages[] = [
        'idx' => $WINDOW - 1,
        'sender' => sprintf('user_%02d', rand(0, 7)),
        'ts' => date('Y-m-d H:i:s'),
        'body' => sprintf('NEW arrival %08x', rand(0, 0x7fffffff)),
    ];
} else {
    $messages[] = [
        'idx' => $WINDOW - 1,
        'sender' => 'user_00',
        'ts' => '2026-05-09 12:00:00',
        'body' => 'Stable tail message — no new arrivals this poll.',
    ];
}

header('Content-Type: text/html; charset=UTF-8');
?>
<!DOCTYPE html>
<html><head><title>FrogNet Chat</title></head><body>
<h1 id="channel">FrogApp.Chat.general</h1>
<p id="generated">Generated: <?= date('Y-m-d H:i:s') ?></p>
<p id="participant_count">Participants: 8</p>
<div id="message_list">
<?php foreach ($messages as $m): ?>
  <div class="msg" id="msg_<?= sprintf('%02d', $m['idx']) ?>">
    <span class="sender"><?= htmlspecialchars($m['sender']) ?></span>
    <span class="ts"><?= htmlspecialchars($m['ts']) ?></span>
    <span class="body"><?= htmlspecialchars($m['body']) ?></span>
  </div>
<?php endforeach; ?>
</div>
</body></html>
