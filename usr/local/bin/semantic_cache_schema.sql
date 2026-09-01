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
-- Semantic Cache Schema for FrogNet
-- Deploy to all nodes: mysql -u FrogUser -p'<set at install>' FrogNet < semantic_cache_schema.sql

-- Proxy-side cache (stores reconstructed responses for SAME lookups)
CREATE TABLE IF NOT EXISTS SemCacheProxy (
    SameID BINARY(16) NOT NULL PRIMARY KEY,
    ReqHash BINARY(32) NOT NULL,
    RawHash BINARY(32),
    SemHash BINARY(32),
    ReplyBytes LONGBLOB,
    ContentType VARCHAR(128),
    UpdatedUTC DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    IsRaw TINYINT DEFAULT 0,
    HttpStatus SMALLINT DEFAULT 200,
    HttpHeaders BLOB,
    INDEX idx_reqhash (ReqHash),
    INDEX idx_updated (UpdatedUTC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Daemon-side cache (stores semantic blobs for SAME lookups)
CREATE TABLE IF NOT EXISTS SemCacheDaemon (
    ReqHash BINARY(32) NOT NULL PRIMARY KEY,
    RawHash BINARY(32) NOT NULL,
    SameID BINARY(16) NOT NULL,
    SemHash BINARY(32),
    SemBlob LONGBLOB,
    Opcode INT UNSIGNED,
    IsRaw TINYINT DEFAULT 0,
    HttpStatus SMALLINT,
    HttpHeaders BLOB,
    HttpBody LONGBLOB,
    UpdatedUTC DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_sameid (SameID),
    INDEX idx_updated (UpdatedUTC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Cache performance metrics (for health monitor display)
CREATE TABLE IF NOT EXISTS SemanticCacheMetrics (
    ID BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    CreatedUTC DATETIME DEFAULT CURRENT_TIMESTAMP,
    PeerIP VARCHAR(45),
    Direction ENUM('proxy', 'daemon') NOT NULL,
    OpType VARCHAR(32) NOT NULL,  -- 'same', 'diff', 'raw_same', 'raw_diff', 'miss'
    Endpoint VARCHAR(255),
    BytesWould INT UNSIGNED DEFAULT 0,  -- bytes that would have been sent without cache
    BytesActual INT UNSIGNED DEFAULT 0, -- bytes actually sent
    RttMs FLOAT DEFAULT 0,
    INDEX idx_created (CreatedUTC),
    INDEX idx_peer (PeerIP),
    INDEX idx_optype (OpType)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Aggregated cache stats (rolled up periodically for dashboard)
CREATE TABLE IF NOT EXISTS SemanticCacheStats (
    ID BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    PeriodStart DATETIME NOT NULL,
    PeriodEnd DATETIME NOT NULL,
    PeerIP VARCHAR(45),
    Direction ENUM('proxy', 'daemon') NOT NULL,
    SameCount INT UNSIGNED DEFAULT 0,
    DiffCount INT UNSIGNED DEFAULT 0,
    MissCount INT UNSIGNED DEFAULT 0,
    BytesSaved BIGINT UNSIGNED DEFAULT 0,
    BytesSent BIGINT UNSIGNED DEFAULT 0,
    AvgRttMs FLOAT DEFAULT 0,
    UNIQUE INDEX idx_period_peer (PeriodStart, PeerIP, Direction),
    INDEX idx_period (PeriodStart)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- View for quick cache hit ratio
CREATE OR REPLACE VIEW v_cache_hit_ratio AS
SELECT 
    DATE(CreatedUTC) as day,
    PeerIP,
    Direction,
    SUM(CASE WHEN OpType IN ('same', 'raw_same') THEN 1 ELSE 0 END) as hits,
    SUM(CASE WHEN OpType IN ('diff', 'raw_diff') THEN 1 ELSE 0 END) as misses,
    ROUND(100.0 * SUM(CASE WHEN OpType IN ('same', 'raw_same') THEN 1 ELSE 0 END) / COUNT(*), 1) as hit_pct,
    SUM(BytesWould - BytesActual) as bytes_saved
FROM SemanticCacheMetrics
GROUP BY DATE(CreatedUTC), PeerIP, Direction;

-- View for recent cache activity (last hour)
CREATE OR REPLACE VIEW v_cache_recent AS
SELECT 
    PeerIP,
    Direction,
    OpType,
    COUNT(*) as cnt,
    SUM(BytesWould) as total_would,
    SUM(BytesActual) as total_actual,
    ROUND(AVG(RttMs), 1) as avg_rtt
FROM SemanticCacheMetrics
WHERE CreatedUTC > DATE_SUB(NOW(), INTERVAL 1 HOUR)
GROUP BY PeerIP, Direction, OpType;
