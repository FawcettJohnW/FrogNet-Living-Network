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
-- data_cache_gen.sql   [DATA_CACHE_GEN_V2]
--
-- The proof that the daemon's RAM copy of Sensor + SensorData equals disk.
--
-- One counter, bumped by triggers on every INSERT / UPDATE / DELETE on either
-- table. Triggers fire for EVERY writer -- api.php, another node's proxy, a
-- human at the mysql prompt, a restore -- which is precisely the property the
-- old data_cache lacked: it observed only its own writes and went stale on
-- everyone else's.
--
-- The daemon reads this one row before answering any cached read. Same value as
-- the loaded snapshot means RAM is disk. Different means reload first.
--
-- InnoDB does not fire row triggers for ON DELETE CASCADE, so deleting a Sensor
-- does not fire the SensorData delete trigger. That is safe: the parent DELETE
-- fires trg_sensor_del, and any change to this single counter reloads BOTH
-- tables.
--
-- Idempotent. Safe to re-run.
--
--   mysql FrogNet < var/www/html/data_cache_gen.sql
--
-- Until this is loaded, the daemon logs "[DATA-CACHE] DISABLED: generation
-- wiring incomplete" once and sends every read to Apache. Slow, never wrong.

USE FrogNet;

CREATE TABLE IF NOT EXISTS `FrogNetTableGen` (
  `TableName` VARCHAR(64)      NOT NULL,
  `Gen`       BIGINT UNSIGNED  NOT NULL DEFAULT 0,
  PRIMARY KEY (`TableName`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO `FrogNetTableGen` (`TableName`, `Gen`) VALUES ('SensorTables', 0);

DROP TRIGGER IF EXISTS `trg_sensor_ins`;
DROP TRIGGER IF EXISTS `trg_sensor_upd`;
DROP TRIGGER IF EXISTS `trg_sensor_del`;
DROP TRIGGER IF EXISTS `trg_sensordata_ins`;
DROP TRIGGER IF EXISTS `trg_sensordata_upd`;
DROP TRIGGER IF EXISTS `trg_sensordata_del`;

CREATE TRIGGER `trg_sensor_ins` AFTER INSERT ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';

CREATE TRIGGER `trg_sensor_upd` AFTER UPDATE ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';

CREATE TRIGGER `trg_sensor_del` AFTER DELETE ON `Sensor` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';

CREATE TRIGGER `trg_sensordata_ins` AFTER INSERT ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';

CREATE TRIGGER `trg_sensordata_upd` AFTER UPDATE ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';

CREATE TRIGGER `trg_sensordata_del` AFTER DELETE ON `SensorData` FOR EACH ROW
  UPDATE `FrogNetTableGen` SET `Gen` = `Gen` + 1 WHERE `TableName` = 'SensorTables';
