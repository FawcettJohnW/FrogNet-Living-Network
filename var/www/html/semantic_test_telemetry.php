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
// Telemetry workload: 200-sensor fleet. 5-10 sensors get fresh readings per call.
// Models the actual FrogNet pattern — most sensors quiet, a few report each cycle.
$NUM = 200;
$updates = rand(5, 10);
$update_set = [];
while (count($update_set) < $updates) $update_set[rand(0, $NUM - 1)] = true;

$now = time();
$sensors = [];
for ($i = 0; $i < $NUM; $i++) {
    if (isset($update_set[$i])) {
        $sensors[] = [
            'sensor_id' => sprintf('sensor_%04d', $i),
            'sensor_type' => 'temperature',
            'unit' => 'C',
            'timestamp' => $now,
            'value' => round(20.0 + (rand(0,1000)/100.0), 2),
            'status' => 'ok',
            'battery_pct' => rand(60, 100),
        ];
    } else {
        $sensors[] = [
            'sensor_id' => sprintf('sensor_%04d', $i),
            'sensor_type' => 'temperature',
            'unit' => 'C',
            'timestamp' => 1700000000 + $i,
            'value' => round(20.0 + ($i * 0.01), 2),
            'status' => 'ok',
            'battery_pct' => 90,
        ];
    }
}

header('Content-Type: application/json');
echo json_encode([
    'status' => 'ok',
    'generated_at' => date('Y-m-d H:i:s'),
    'fleet_id' => 'fleet_001',
    'sensor_count' => $NUM,
    'updates_in_window' => $updates,
    'sensors' => $sensors,
]);
