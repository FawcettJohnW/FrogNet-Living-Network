----------------------------------------------------------------
--  Copyright (C) 2016-2026 Fawcett Innovations LLC           --
--                                                            --
--  SPDX-License-Identifier: GPL-2.0-only                     --
--                                                            --
--  This program is free software; you can redistribute it    --
--  and/or modify it under the terms of the GNU General Public--
--  License as published by the Free Software Foundation;     --
--  version 2 of the License, and no other version.           --
--                                                            --
--  This program is distributed in the hope that it will be   --
--  useful, but WITHOUT ANY WARRANTY; without even the implied--
--  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR   --
--  PURPOSE.  See the GNU General Public License for details. --
--                                                            --
--  See COPYRIGHT and LICENSE at the root of this tree.       --
----------------------------------------------------------------
-- data_cache_gen.sql   [DATA_CACHE_GEN_V3]
--
-- The proof that the daemon's RAM copy of Sensor + SensorData equals disk.
--
-- V3 SPLITS THE COUNTER PER TABLE. V2 kept ONE row, 'SensorTables', bumped by
-- all six triggers, so a single SensorData write invalidated the Sensor half
-- too and the next read rebuilt BOTH tables whole.
--
-- Measured on Seattle5, one hour as the elected databasehost: 1713 upserts
-- arrived (1363 upsert_by_name, 459 upsert_batch) from eight nodes' 60s role
-- advertisers, metrics flushers and per-merge discovery. Those produced 830
-- distinct generations and 831 full two-table reloads -- one complete rebuild
-- of 258 sensors + 258 sensor_data rows every 4.3 seconds, competing for the
-- same MySQL as the writes that caused it. The write rate is normal; the
-- invalidation granularity was not.
--
-- Sensor is node identity: it changes when a node joins or leaves, near never.
-- SensorData is all the churn. Separating them stops the churn flushing the
-- identity table, which is most of the waste.
--
-- THE CASCADE. V2's header records the reason the single counter was safe:
--
--   "InnoDB does not fire row triggers for ON DELETE CASCADE, so deleting a
--    Sensor does not fire the SensorData delete trigger. That is safe: the
--    parent DELETE fires trg_sensor_del, and any change to this single counter
--    reloads BOTH tables."
--
-- That safety came FROM the sharing. Split naively and a DELETE FROM Sensor
-- cascades its SensorData rows away without bumping the SensorData generation,
-- and the cache serves deleted rows until something else happens to write.
--
-- So trg_sensor_del bumps BOTH counters, explicitly. A Sensor delete IS a
-- SensorData change -- the cascade is a known, documented consequence of it,
-- and the trigger now says so instead of relying on a shared row to cover it.
-- Sensor INSERT and UPDATE cascade nothing and bump only 'Sensor'.
--
-- Triggers fire for EVERY writer -- api.php, another node's proxy, a human at
-- the mysql prompt, a restore -- which is precisely the property the old
-- data_cache lacked: it observed only its own writes and went stale on
-- everyone else's.
--
-- Idempotent. Safe to re-run. Safe to run while the daemon is live: it reads
-- both new rows and falls through to Apache for any table whose row is missing.
--
--   mysql FrogNet < var/www/html/data_cache_gen.sql
--
-- Until this is loaded, the daemon logs "[DATA-CACHE] DISABLED: generation
-- wiring incomplete" once and sends every read to Apache. Slow, never wrong.
--
-- [DATA_CACHE_GEN_V3_RESTORED] 2026-09-25: written in the 2026-08-11 NY2 session with the V3 data_cache.py; the
-- tree carried V3 data_cache.py but still this file's V2, so data_cache ran DISABLED ("this database is on
-- [DATA_CACHE_GEN_V2]") and every tuple read went to Apache. Restored verbatim.

USE FrogNet;

CREATE TABLE IF NOT EXISTS `FrogNetTableGen` (
  `TableName` VARCHAR(64)      NOT NULL,
  `Gen`       BIGINT UNSIGNED  NOT NULL DEFAULT 0,
  PRIMARY KEY (`TableName`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed the per-table rows AT THE CURRENT SHARED VALUE, not at 0.
--
-- A daemon still holding a V2 snapshot has _gen = <the shared number>. Seeding
-- at 0 would make every live daemon believe its cache is NEWER than disk and
-- skip the reload that this migration should force. Seeding at the current
-- value, which only ever increases, guarantees the next write moves both past
-- whatever any daemon is holding.
INSERT INTO `FrogNetTableGen` (`TableName`, `Gen`)
  SELECT 'Sensor', COALESCE(MAX(`Gen`), 0) FROM `FrogNetTableGen`
  ON DUPLICATE KEY UPDATE `Gen` = `Gen`;
INSERT INTO `FrogNetTableGen` (`TableName`, `Gen`)
  SELECT 'SensorData', COALESCE(MAX(`Gen`), 0) FROM `FrogNetTableGen`
  ON DUPLICATE KEY UPDATE `Gen` = `Gen`;

-- 'SensorTables' is left in place and left bumped by nothing. A daemon still on
-- V2 reads a row that stops moving and therefore serves a cache it believes is
-- current but is not -- so DO NOT load this until every node runs the V3
-- data_cache.py, which reads the per-table rows. The row is not dropped so a
-- rollback to V2 is a matter of re-running the V2 file.

DROP TRIGGER IF EXISTS `trg_sensor_ins`;
DROP TRIGGER IF EXISTS `trg_sensor_upd`;
DROP TRIGGER IF EXISTS `trg_sensor_del`;
DROP TRIGGER IF EXISTS `trg_sensordata_ins`;
DROP TRIGGER IF EXISTS `trg_sensordata_upd`;
DROP TRIGGER IF EXISTS `trg_sensordata_del`;

CREATE TRIGGER `trg_sensor_ins` AFTER INSERT ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'Sensor';

CREATE TRIGGER `trg_sensor_upd` AFTER UPDATE ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'Sensor';

-- BOTH. ON DELETE CASCADE removes SensorData rows without firing that table's
-- own delete trigger; this is the only place that fact can be accounted for.
CREATE TRIGGER `trg_sensor_del` AFTER DELETE ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1
    WHERE `TableName` IN ('Sensor', 'SensorData');

CREATE TRIGGER `trg_sensordata_ins` AFTER INSERT ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorData';

CREATE TRIGGER `trg_sensordata_upd` AFTER UPDATE ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorData';

CREATE TRIGGER `trg_sensordata_del` AFTER DELETE ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorData';
