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
/**
 * /var/www/html/frognet_cache.php
 * 
 * Hash-based HTTP response caching for all FrogNet PHP endpoints.
 * 
 * Usage at TOP of any PHP endpoint:
 *   require_once('/var/www/html/frognet_cache.php');
 *   $cache = new FrogNetCache();
 *   if ($cache->checkClientHash()) {
 *       exit; // 304 already sent
 *   }
 *   // ... compute response ...
 *   $cache->sendResponse($response_body, $content_type, $status_code);
 */

class FrogNetCache {
    private $reqMethod;
    private $reqPath;
    private $reqBody;
    private $reqBodyHash;
    private $clientHash;
    private $db;
    
    private $useDatabase = true;
    private $hashAlgo = 'sha256';
    
    public function __construct() {
        $this->reqMethod = $_SERVER['REQUEST_METHOD'] ?? 'GET';
        $this->reqPath = $_SERVER['REQUEST_URI'] ?? '/';
        $this->reqBody = file_get_contents('php://input') ?: '';
        $this->reqBodyHash = hash($this->hashAlgo, $this->reqBody, true);
        
        $this->clientHash = $this->getClientHash();
        
        if ($this->useDatabase) {
            $this->connectDb();
        }
    }
    
    private function getClientHash(): ?string {
        $headers = [
            'HTTP_X_FROGNET_HASH',
            'HTTP_X_FROGNET_RESP_HASH',
            'HTTP_IF_NONE_MATCH'
        ];
        
        foreach ($headers as $h) {
            if (!empty($_SERVER[$h])) {
                $hash = trim($_SERVER[$h], '"');
                if (strlen($hash) === 64) {
                    return $hash;
                }
            }
        }
        return null;
    }
    
    private function connectDb(): void {
        // Route through config.php's FrogNetDb wrapper.  The wrapper is
        // a request-scoped singleton whose __destruct drains pending
        // result sets before close(), giving mariadb a clean COM_QUIT
        // and eliminating "Aborted connection" warnings from this
        // caller's tear-down.  FrogNetCache holds the raw mysqli (via
        // raw()) but doesn't destruct it itself — the wrapper's
        // destructor runs at request end after this instance is gone.
        //
        // Previous version opened a per-instance raw mysqli from
        // FROGNET_DB_* env vars with defaults of root/empty password.
        // Those defaults wouldn't match the real FrogUser credentials,
        // so the env-var path almost always failed and useDatabase
        // got set to false — no env override actually worked in
        // practice.  If env-var overrides are needed in future, add
        // them to config.php's db() rather than re-introducing a
        // bypass path here.
        try {
            require_once __DIR__ . '/config.php';
            $this->db = db()->raw();
            if ($this->db === null) {
                $this->useDatabase = false;
            }
        } catch (Throwable $e) {
            error_log("FrogNetCache: DB exception: " . $e->getMessage());
            $this->db = null;
            $this->useDatabase = false;
        }
    }
    
    public function checkClientHash(): bool {
        if (!$this->clientHash) {
            return false;
        }
        
        if ($this->useDatabase && $this->db) {
            $cached = $this->getCachedResponse();
            if ($cached && $cached['resp_hash'] === $this->clientHash) {
                $this->send304($cached['resp_hash']);
                return true;
            }
        }
        
        return false;
    }
    
    private function getCachedResponse(): ?array {
        if (!$this->db) return null;
        
        $stmt = $this->db->prepare(
            "SELECT HEX(RespHash) as resp_hash, RespBody, RespContentType, RespStatus 
             FROM HttpResponseCache 
             WHERE ReqMethod = ? AND ReqPath = ? AND ReqBodyHash = ?
             LIMIT 1"
        );
        
        if (!$stmt) return null;
        
        $stmt->bind_param('sss', $this->reqMethod, $this->reqPath, $this->reqBodyHash);
        $stmt->execute();
        $result = $stmt->get_result();
        $row = $result->fetch_assoc();
        $stmt->close();
        
        if ($row) {
            $this->incrementHitCount();
            return [
                'resp_hash' => strtolower($row['resp_hash']),
                'resp_body' => $row['RespBody'],
                'content_type' => $row['RespContentType'],
                'status' => $row['RespStatus']
            ];
        }
        
        return null;
    }
    
    private function incrementHitCount(): void {
        if (!$this->db) return;
        
        $stmt = $this->db->prepare(
            "UPDATE HttpResponseCache SET HitCount = HitCount + 1 
             WHERE ReqMethod = ? AND ReqPath = ? AND ReqBodyHash = ?"
        );
        if ($stmt) {
            $stmt->bind_param('sss', $this->reqMethod, $this->reqPath, $this->reqBodyHash);
            $stmt->execute();
            $stmt->close();
        }
    }
    
    private function send304(string $hash): void {
        http_response_code(304);
        header('X-FrogNet-Hash: ' . $hash);
        header('ETag: "' . $hash . '"');
        header('X-FrogNet-Cache: HIT');
        header('Connection: close');
    }
    
    public function sendResponse(string $body, string $contentType = 'application/json', int $status = 200): void {
        $respHash = hash($this->hashAlgo, $body);
        
        if ($this->clientHash === $respHash) {
            $this->send304($respHash);
            return;
        }
        
        if ($this->useDatabase && $this->db) {
            $this->storeResponse($body, $contentType, $status, $respHash);
        }
        
        http_response_code($status);
        header('Content-Type: ' . $contentType);
        header('Content-Length: ' . strlen($body));
        header('X-FrogNet-Hash: ' . $respHash);
        header('ETag: "' . $respHash . '"');
        header('X-FrogNet-Cache: MISS');
        header('Connection: close');
        
        echo $body;
    }
    
    private function storeResponse(string $body, string $contentType, int $status, string $respHash): void {
        if (!$this->db) return;
        
        $respHashBin = hex2bin($respHash);
        
        $stmt = $this->db->prepare(
            "INSERT INTO HttpResponseCache 
             (ReqMethod, ReqPath, ReqBodyHash, RespHash, RespBody, RespContentType, RespStatus)
             VALUES (?, ?, ?, ?, ?, ?, ?)
             ON DUPLICATE KEY UPDATE 
                RespHash = VALUES(RespHash),
                RespBody = VALUES(RespBody),
                RespContentType = VALUES(RespContentType),
                RespStatus = VALUES(RespStatus),
                HitCount = 0"
        );
        
        if ($stmt) {
            $stmt->bind_param('ssssssi', 
                $this->reqMethod, 
                $this->reqPath, 
                $this->reqBodyHash,
                $respHashBin,
                $body,
                $contentType,
                $status
            );
            $stmt->execute();
            $stmt->close();
        }
    }
    
    public function getRequestBody(): string {
        return $this->reqBody;
    }
    
    public static function cleanup(int $maxAgeHours = 24): int {
        // Route through config.php's FrogNetDb wrapper so the
        // connection used by cron-driven cleanup also gets the
        // clean COM_QUIT path.  Drains and closes happen via the
        // wrapper's __destruct at request end; do NOT call close()
        // here or we'd race the wrapper.
        require_once __DIR__ . '/config.php';
        $db = db()->raw();
        if ($db === null) {
            return -1;
        }

        $stmt = $db->prepare("CALL CleanupSemCache(?)");
        if (!$stmt) {
            return -1;
        }
        $stmt->bind_param('i', $maxAgeHours);
        $stmt->execute();
        $affected = $db->affected_rows;
        $stmt->close();

        // Drain any pending result set from the CALL so the wrapper's
        // __destruct doesn't trip over an unread set at request end.
        while (@$db->more_results() && @$db->next_result()) {
            if ($r = @$db->store_result()) { $r->free(); }
        }

        return $affected;
    }
}
