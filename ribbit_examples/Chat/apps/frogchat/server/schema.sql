-- An application's FrogNet RAM. The database it lives in is named by the app's
-- manifest and created by the installer; nothing here names one.
-- A cell is addressed by three coordinates and holds one value. Writing replaces.
CREATE TABLE IF NOT EXISTS `Cell` (
  `id`        BIGINT UNSIGNED NOT NULL,                  -- the memory's write order: every write, to any cell, takes the next one (from WriteOrder)
  `service`   VARCHAR(191) NOT NULL,
  `variable`  VARCHAR(191) NOT NULL,
  `instance`  VARCHAR(191) NOT NULL,
  `bag`       LONGTEXT     NOT NULL,                     -- JSON, stored as written
  `updated`   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `address` (`service`, `variable`, `instance`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- THE ONE SERIALIZATION POINT. Every write takes the next number from this single
-- row, inside its transaction, and holds the row until it commits. So the order of
-- ids IS the order of commits: a reader asking for "everything after N" can never be
-- shown N+1 while N is still on its way, and writers all take the same lock first, so
-- they queue instead of deadlocking. (REPLACE with AUTO_INCREMENT did neither under
-- contention: tools/mesh caught a write refused by an InnoDB deadlock.)
CREATE TABLE IF NOT EXISTS `WriteOrder` (
  `k` TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  `n` BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;
INSERT IGNORE INTO `WriteOrder` (`k`, `n`) SELECT 1, COALESCE(MAX(`id`), 0) FROM `Cell`;
