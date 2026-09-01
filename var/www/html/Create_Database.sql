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

DROP DATABASE IF EXISTS `FrogNet`;
CREATE DATABASE `FrogNet` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `FrogNet`;

-- Users
CREATE TABLE `User` (
  `RealName`   VARCHAR(255),
  `Password`   VARCHAR(255),
  `CallSign`   VARCHAR(255),
  PRIMARY KEY (`CallSign`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Teams
CREATE TABLE `Team` (
  `TeamName`    VARCHAR(255) NOT NULL,
  `CreatedDate` DATETIME,
  `TeamOwner`   VARCHAR(255) NOT NULL,
  PRIMARY KEY (`TeamName`),
  CONSTRAINT `fk_team_owner` FOREIGN KEY (`TeamOwner`) REFERENCES `User`(`CallSign`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Team members (composite PK for idempotency)
CREATE TABLE `TeamMember` (
  `TeamName` VARCHAR(255) NOT NULL,
  `TeamUser` VARCHAR(255) NOT NULL,
  PRIMARY KEY (`TeamName`,`TeamUser`),
  CONSTRAINT `fk_tm_team` FOREIGN KEY (`TeamName`) REFERENCES `Team`(`TeamName`) ON DELETE CASCADE,
  CONSTRAINT `fk_tm_user` FOREIGN KEY (`TeamUser`) REFERENCES `User`(`CallSign`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Messages
CREATE TABLE `Message` (
  `messageID` INT NOT NULL AUTO_INCREMENT,
  `FromUser`  VARCHAR(255) NOT NULL,
  `ToUser`    VARCHAR(255) NOT NULL,
  `Message`   VARCHAR(4096),
  `sentDate`  DATETIME,
  `readDate`  DATETIME,
  PRIMARY KEY (`messageID`),
  CONSTRAINT `fk_msg_from` FOREIGN KEY (`FromUser`) REFERENCES `User`(`CallSign`) ON DELETE CASCADE,
  CONSTRAINT `fk_msg_to`   FOREIGN KEY (`ToUser`)   REFERENCES `User`(`CallSign`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Known FrogNet
CREATE TABLE `KnownFrogNet` (
  `NetworkName`   VARCHAR(256) NOT NULL,
  `IPAddress`     VARCHAR(64)  NOT NULL,
  `FrogID`        VARCHAR(64)  NOT NULL,
  `Tags`          VARCHAR(2048),
  `LastHeartbeat` DATETIME,
  PRIMARY KEY (`NetworkName`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Sensors
CREATE TABLE `Sensor` (
  `SensorID`       INT NOT NULL AUTO_INCREMENT,
  `FrogID`         VARCHAR(64)  NOT NULL,
  `SensorAddress`  VARCHAR(191)  NOT NULL,
  `SensorNetwork`  VARCHAR(1024) NOT NULL,
  -- [TUPLE_ADDRESS_V1] Name, Type and Address are the THREE COORDINATES that address
  -- a segment of network shared memory -- a Linda tuple space. The columns are called
  -- Sensor* because sensors were the first application; nothing here is
  -- sensor-specific. Their COMBINATION is the address and is what must be unique; no
  -- single one of them is, and two hosts legitimately write the same Name+Type from
  -- different Addresses (net.frognet.hearts installed on both).
  --
  -- SensorLocation is the SAME COORDINATE under a second name. Nothing distinguishes
  -- them, and having two columns for one coordinate is why they were filled
  -- inconsistently -- SD: tuples set Address and leave Location empty, System rows set
  -- both to the same IP. A template that matched on Location would miss every tuple
  -- that filled Address. Address is the one every writer populates, so Address is the
  -- coordinate; Location is kept only so existing rows and api.php callers do not
  -- break, and is not part of the key.
  --
  -- Sized to fit a three-column utf8mb4 index (191 chars = 764 bytes each).
  `SensorName`     VARCHAR(191)  NOT NULL,
  `SensorType`     VARCHAR(191)  NOT NULL,
  `SensorLocation` VARCHAR(191)  NOT NULL DEFAULT '',
  `Tags`           VARCHAR(2048),
  PRIMARY KEY (`SensorID`),
  -- [TUPLE_ADDRESS_V1] upsert_by_name resolves on THIS key. Without it every write
  -- inserted a new row and readers got the oldest, so a tuple looked frozen at its
  -- first value. With a NAME-ONLY key instead, two hosts writing the same plain name
  -- (net.frognet.hearts) would silently collapse into one row.
  UNIQUE KEY `idx_tuple_address` (`SensorName`, `SensorType`, `SensorAddress`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- IA Hosts
CREATE TABLE `IAHost` (
  `IAHostID`  INT NOT NULL AUTO_INCREMENT,
  `FrogID`    VARCHAR(64)  NOT NULL,
  `IAAddress` VARCHAR(1024) NOT NULL,
  `IANetwork` VARCHAR(1024) NOT NULL,
  `IAName`    VARCHAR(256)  NOT NULL,
  `IAType`    VARCHAR(2048) NOT NULL,
  `Tags`      VARCHAR(2048),
  PRIMARY KEY (`IAHostID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Actuators
CREATE TABLE `Actuator` (
  `ActuatorID`      INT NOT NULL AUTO_INCREMENT,
  `FrogID`          VARCHAR(64)  NOT NULL,
  `ActuatorAddress` VARCHAR(1024) NOT NULL,
  `ActuatorNetwork` VARCHAR(1024) NOT NULL,
  `ActuatorName`    VARCHAR(256)  NOT NULL,
  `ActuatorType`    VARCHAR(2048) NOT NULL,
  `Tags`            VARCHAR(2048),
  PRIMARY KEY (`ActuatorID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Well Known Sites
CREATE TABLE `WellKnownSite` (
  `SiteID`     INT NOT NULL AUTO_INCREMENT,
  `FrogID`     VARCHAR(64)  NOT NULL,
  `SiteAddress` VARCHAR(1024) NOT NULL,
  `SiteName`    VARCHAR(256)  NOT NULL,
  `Tags`        VARCHAR(2048),
  PRIMARY KEY (`SiteID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Sensor Data (1:1 with Sensor)
CREATE TABLE `SensorData` (
  `SensorID` INT NOT NULL,
  `FrogID`   VARCHAR(64) NOT NULL,
  `jsonData` VARCHAR(8192),
  -- [SENSORDATA_UPDATEDAT_V1] api.php's upsert path writes this column; without it
  -- every INSERT ... ON DUPLICATE KEY UPDATE on this table fails 1054 at parse time.
  `UpdatedAt` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`SensorID`),
  CONSTRAINT `fk_sd_sensor` FOREIGN KEY (`SensorID`) REFERENCES `Sensor`(`SensorID`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
