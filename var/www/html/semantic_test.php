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
// semantic_test.php — FrogNet semantic compression test page
// Generates ~800KB of stable content with 6 dynamic elements.
// On each call, a random subset of dynamic elements gets a new random value.
// The rest stay at their baseline, so SAME/DIFF should kick in.

// --- Dynamic elements ---
$fields = ['ts', 'rnd_a', 'rnd_b', 'rnd_c', 'rnd_d', 'rnd_e'];

// Decide which fields change this call (random subset, at least 1)
$num_changing = rand(1, count($fields));
$changing = array_slice(array_values($fields), 0, $num_changing);
shuffle($changing);
$changing = array_slice($changing, 0, $num_changing);

$values = [];
foreach ($fields as $f) {
    if ($f === 'ts') {
        // Always include timestamp in changing set so there's always variation
        $values[$f] = date('Y-m-d H:i:s');
    } elseif (in_array($f, $changing)) {
        $values[$f] = sprintf('%08x', rand(0, 0x7fffffff));
    } else {
        $values[$f] = 'baseline_value_' . $f;
    }
}

// Generate stable bulk content (~800KB of static text)
// We use a seeded deterministic block so the static part is always identical.
$static_block = '';
$line = str_repeat('FrogNet semantic compression test payload. This line is exactly 80 chars long. ', 1);
// 800KB / 80 bytes = 10000 lines
for ($i = 0; $i < 10000; $i++) {
    $static_block .= sprintf("%05d: %s\n", $i, $line);
}

header('Content-Type: text/html; charset=utf-8');
?><!DOCTYPE html>
<html>
<head><title>FrogNet Semantic Test</title></head>
<body>
<h1>FrogNet Semantic Compression Test</h1>
<p>This page has 6 dynamic elements embedded in ~800KB of static content.</p>

<h2>Dynamic Fields</h2>
<table border="1">
<tr><th>Field</th><th>Value</th></tr>
<tr><td>Timestamp</td>     <td id="ts"><?= htmlspecialchars($values['ts']) ?></td></tr>
<tr><td>Random A</td>      <td id="rnd_a"><?= htmlspecialchars($values['rnd_a']) ?></td></tr>
<tr><td>Random B</td>      <td id="rnd_b"><?= htmlspecialchars($values['rnd_b']) ?></td></tr>
<tr><td>Random C</td>      <td id="rnd_c"><?= htmlspecialchars($values['rnd_c']) ?></td></tr>
<tr><td>Random D</td>      <td id="rnd_d"><?= htmlspecialchars($values['rnd_d']) ?></td></tr>
<tr><td>Random E</td>      <td id="rnd_e"><?= htmlspecialchars($values['rnd_e']) ?></td></tr>
</table>

<hr>
<h2>Static Payload (~800KB)</h2>
<pre>
<?= htmlspecialchars($static_block) ?>
</pre>

</body>
</html>
