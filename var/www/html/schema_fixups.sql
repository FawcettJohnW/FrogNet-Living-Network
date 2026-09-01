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
USE FrogNet;

-- Fix foreign key targets to the correct table names.
--
-- [FK_SPLIT_DROP_ADD_V1] Each block below used to DROP a foreign key and ADD one
-- with the SAME NAME inside a single ALTER TABLE. That works on a fresh install
-- and fails on an upgrade:
--
--   ERROR 1005 (HY000): Can't create table `FrogNet`.`Team`
--                       (errno: 121 "Duplicate key on write or update")
--
-- InnoDB foreign-key names are unique per SCHEMA, not per table, and they live in
-- the data dictionary. Within one ALTER the old name is still registered when the
-- ADD is evaluated, so the ADD collides with the constraint the same statement is
-- dropping. On a fresh database the constraint does not exist yet, DROP IF EXISTS
-- is a no-op, and the ADD succeeds -- which is why this only shows up on the
-- --preserve upgrade path, over a database that already has the constraints.
--
-- Splitting into separate statements lets the DROP commit and free the name
-- before the ADD claims it. DROP FOREIGN KEY IF EXISTS keeps every block safe to
-- run on a fresh database and safe to re-run after a partial failure.

ALTER TABLE Team DROP FOREIGN KEY IF EXISTS Team_ibfk_1;
ALTER TABLE Team
  ADD CONSTRAINT Team_ibfk_1 FOREIGN KEY (TeamOwner) REFERENCES `User`(CallSign) ON DELETE CASCADE;

ALTER TABLE TeamMember DROP FOREIGN KEY IF EXISTS TeamMember_ibfk_1;
ALTER TABLE TeamMember DROP FOREIGN KEY IF EXISTS TeamMember_ibfk_2;
ALTER TABLE TeamMember
  ADD CONSTRAINT TeamMember_ibfk_1 FOREIGN KEY (TeamName) REFERENCES Team(TeamName) ON DELETE CASCADE;
ALTER TABLE TeamMember
  ADD CONSTRAINT TeamMember_ibfk_2 FOREIGN KEY (TeamUser) REFERENCES `User`(CallSign) ON DELETE CASCADE;

ALTER TABLE TeamMember
  ADD UNIQUE KEY IF NOT EXISTS uniq_team_user (TeamName, TeamUser);

ALTER TABLE Message DROP FOREIGN KEY IF EXISTS Message_ibfk_1;
ALTER TABLE Message DROP FOREIGN KEY IF EXISTS Message_ibfk_2;
ALTER TABLE Message
  ADD CONSTRAINT Message_ibfk_1 FOREIGN KEY (FromUser) REFERENCES `User`(CallSign) ON DELETE CASCADE;
ALTER TABLE Message
  ADD CONSTRAINT Message_ibfk_2 FOREIGN KEY (ToUser) REFERENCES `User`(CallSign) ON DELETE CASCADE;

ALTER TABLE SensorData DROP FOREIGN KEY IF EXISTS SensorData_ibfk_1;
ALTER TABLE SensorData
  ADD CONSTRAINT SensorData_ibfk_1 FOREIGN KEY (SensorID) REFERENCES Sensor(SensorID) ON DELETE CASCADE;

-- [SENSORDATA_UPDATEDAT_V1] api.php has always written
--     ON DUPLICATE KEY UPDATE ..., UpdatedAt = CURRENT_TIMESTAMP
-- (api.php:583, the upsert / upsert_by_name path) against a SensorData table that
-- has only SensorID, FrogID and jsonData. MariaDB validates the whole statement, so
-- this fails with
--     ERROR 1054 (42S22) Unknown column 'UpdatedAt' in 'UPDATE'
-- on EVERY write, first insert included -- not just on updates. T.put catches the
-- non-200 and returns False, and nothing above it logged that branch, so a node
-- published its <role>/capability every merge, every write was rejected, and no log
-- anywhere said so. The row aged past FROGNET_BALLOT_MAX_AGE_S and every other node
-- refused that machine as a candidate for every role.
--
-- The only writes that landed came through upsert_batch (api.php:671), whose clause
-- is just jsonData = VALUES(jsonData) -- which is why a capability row would sit
-- untouched for hours and then jump fresh.
--
-- ON UPDATE CURRENT_TIMESTAMP so the batch path stamps it too without naming it.
-- That makes row age a DATABASE fact -- when it was written -- rather than the `ts`
-- the writer embedded in jsonData, which put() preserves via setdefault and which is
-- therefore probe time, not write time.
ALTER TABLE SensorData
  ADD COLUMN IF NOT EXISTS UpdatedAt TIMESTAMP NOT NULL
      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

-- [TUPLE_ADDRESS_V1] A tuple is addressed by THREE coordinates: Name, Type and
-- Address. This is a Linda tuple space -- a network-wide shared memory. The columns
-- are called Sensor* because sensors were the first application; nothing about the
-- model is sensor-specific. The COMBINATION is the address and is what is unique. No
-- single coordinate is: two hosts both write net.frognet.hearts, distinguished only
-- by Address, and a name-only key would silently collapse them into one row.
--
-- SensorLocation is the SAME COORDINATE as SensorAddress under a second name. Nothing
-- distinguishes them, and one coordinate in two columns is why they were filled
-- inconsistently: SD: tuples set Address and leave Location empty, System rows set
-- both to the same IP, plugin rows set only Address. Address is the one every writer
-- populates, so Address is the coordinate. Location is left in place so existing rows
-- and api.php callers keep working, and is NOT part of the key.
--
-- api.php:191 and :440 already assume a unique key exists and the whole
-- upsert_by_name resolve-or-create depends on it. There was none -- Sensor had only
-- PRIMARY KEY (SensorID) -- so every write INSERTed a new row and every reader got
-- whichever the LEFT JOIN emitted first: the OLDEST. Measured against the real store,
-- a call's member list stayed ['john'] through invite, join and two leaves, with FIVE
-- rows under one address. Same reason one node read a capability row as 11 hours old
-- while its writer saw it fresh, and why two Communicators never saw each other.
--
-- SensorLocation is also ADDED here: the shipped Create_Database.sql never created it
-- though api.php reads and writes it and live tables have it -- the same drift as
-- UpdatedAt.
--
-- Widths: three utf8mb4 columns at 191 chars = 764 bytes each, 2292 total, inside
-- InnoDB's 3072-byte DYNAMIC limit. Longest address we generate is
-- SD:capability.host:255.255.255.255:databasehost at 47 characters, and an address
-- coordinate is an IP. The narrowing is guarded: it aborts rather than truncating.
ALTER TABLE Sensor ADD COLUMN IF NOT EXISTS `SensorLocation` VARCHAR(191) NOT NULL DEFAULT '';

SELECT IF(MAX(CHAR_LENGTH(SensorName)) > 191
       OR MAX(CHAR_LENGTH(SensorType)) > 191
       OR MAX(CHAR_LENGTH(SensorAddress)) > 191,
          (SELECT * FROM (SELECT 'ABORT: a Name/Type/Address exceeds 191 chars -- widen the plan, do not truncate') x),
          'ok') AS width_check FROM Sensor;

ALTER TABLE Sensor MODIFY `SensorName`    VARCHAR(191) NOT NULL;
ALTER TABLE Sensor MODIFY `SensorType`    VARCHAR(191) NOT NULL;
ALTER TABLE Sensor MODIFY `SensorAddress` VARCHAR(191) NOT NULL;

-- Collapse rows already sharing one ADDRESS, keeping the LOWEST SensorID -- the row
-- readers have been seeing all along -- and dropping the shadows' data with them.
DELETE d FROM SensorData d
  JOIN Sensor s ON s.SensorID = d.SensorID
  JOIN (SELECT SensorName, SensorType, SensorAddress, MIN(SensorID) keep
          FROM Sensor GROUP BY SensorName, SensorType, SensorAddress) k
    ON k.SensorName = s.SensorName AND k.SensorType = s.SensorType
   AND k.SensorAddress = s.SensorAddress
 WHERE s.SensorID <> k.keep;

DELETE s FROM Sensor s
  JOIN (SELECT SensorName, SensorType, SensorAddress, MIN(SensorID) keep
          FROM Sensor GROUP BY SensorName, SensorType, SensorAddress) k
    ON k.SensorName = s.SensorName AND k.SensorType = s.SensorType
   AND k.SensorAddress = s.SensorAddress
 WHERE s.SensorID <> k.keep;

ALTER TABLE Sensor DROP INDEX IF EXISTS idx_sensor_name;
ALTER TABLE Sensor ADD UNIQUE KEY IF NOT EXISTS idx_tuple_address
      (`SensorName`, `SensorType`, `SensorAddress`);
