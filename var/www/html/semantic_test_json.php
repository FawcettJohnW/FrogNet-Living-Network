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
// semantic_test_json.php — FrogNet semantic compression test, JSON variant
// ~800KB of stable JSON with 6 dynamic top-level fields.
// Random subset of dynamic fields changes on each call.

$fields = ['ts', 'rnd_a', 'rnd_b', 'rnd_c', 'rnd_d', 'rnd_e'];

$num_changing = rand(1, count($fields));
$changing_keys = $fields;
shuffle($changing_keys);
$changing_keys = array_slice($changing_keys, 0, $num_changing);

$dynamic = [];
foreach ($fields as $f) {
    if ($f === 'ts') {
        $dynamic[$f] = date('Y-m-d H:i:s');
    } elseif (in_array($f, $changing_keys)) {
        $dynamic[$f] = sprintf('%08x', rand(0, 0x7fffffff));
    } else {
        $dynamic[$f] = 'baseline_value_' . $f;
    }
}

// Generate stable bulk JSON array (~800KB)
// Each entry is a fixed-structure object; content is deterministic.
$entries = [];
for ($i = 0; $i < 6000; $i++) {
    $entries[] = [
        'index'   => $i,
        'key'     => sprintf('node_%05d', $i),
        'value'   => sprintf('FrogNet semantic compression test payload entry %05d', $i),
        'static'  => 'This field is always the same and compresses to near-zero after bootstrap.',
    ];
}

$payload = [
    'status'  => 'ok',
    'dynamic' => $dynamic,
    'entries' => $entries,
];

header('Content-Type: application/json');
echo json_encode($payload);
