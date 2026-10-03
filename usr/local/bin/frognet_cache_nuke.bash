#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# frognet_cache_nuke.bash — Total semantic cache reset for a FrogNet node
#
# Clears ALL cached state:
#   1. Proxy-side MySQL table (SemCacheProxy)
#   2. Daemon-side MySQL table (SemCacheDaemon)
#   3. In-memory references (by restarting both services)
#   4. Diagnostic TSV files
#
# Run as root on the target box.
# Usage: bash frognet_cache_nuke.bash

HOSTNAME=$(hostname)
echo "=== FrogNet Cache Nuke on ${HOSTNAME} ==="
echo "$(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ---- 1. Stop services so no new writes happen ----
echo "[1/5] Stopping frognet-proxy and frognet-daemon..."
systemctl stop frognet-proxy  2>&1 || echo "  frognet-proxy was not running"
systemctl stop frognet-daemon 2>&1 || echo "  frognet-daemon was not running"
sleep 1
echo "  Done."
echo ""

# ---- 2. Nuke proxy-side cache table ----
echo "[2/5] Truncating SemCacheProxy table..."
mysql -u root FrogNet -e "TRUNCATE TABLE SemCacheProxy;" 2>&1 || echo "  SemCacheProxy table does not exist (OK)"
PROXY_COUNT=$(mysql -u root FrogNet -N -e "SELECT COUNT(*) FROM SemCacheProxy;" 2>/dev/null || echo "N/A")
echo "  SemCacheProxy rows after truncate: ${PROXY_COUNT}"
echo ""

# ---- 3. Nuke daemon-side cache table ----
echo "[3/5] Truncating SemCacheDaemon table..."
mysql -u root FrogNet -e "TRUNCATE TABLE SemCacheDaemon;" 2>&1 || echo "  SemCacheDaemon table does not exist (OK)"
DAEMON_COUNT=$(mysql -u root FrogNet -N -e "SELECT COUNT(*) FROM SemCacheDaemon;" 2>/dev/null || echo "N/A")
echo "  SemCacheDaemon rows after truncate: ${DAEMON_COUNT}"
echo ""

# ---- 4. Remove diag/tmp files ----
echo "[4/5] Cleaning temp and diag files..."
rm -f /tmp/frognet_proxy_diag.tsv  && echo "  Removed /tmp/frognet_proxy_diag.tsv" || true
rm -f /tmp/frognet_daemon_diag.tsv && echo "  Removed /tmp/frognet_daemon_diag.tsv" || true
rm -f /tmp/frognet_*.log           && echo "  Removed /tmp/frognet_*.log" || true
echo "  Done."
echo ""

# ---- 5. Restart services (clears all in-memory state) ----
#   In-memory caches cleared by restart:
#     - _request_references   (transport_semantic.py)
#     - _response_references  (transport_semantic.py)
#     - _last_req_hashes      (transport_semantic.py)
#     - _seen_cache           (semcache_db.py)
#     - _DaemonPool connections
echo "[5/5] Restarting frognet-daemon and frognet-proxy..."
systemctl start frognet-daemon 2>&1
sleep 1
systemctl start frognet-proxy 2>&1
sleep 1

# Verify they're running
DAEMON_STATUS=$(systemctl is-active frognet-daemon 2>/dev/null || echo "dead")
PROXY_STATUS=$(systemctl is-active frognet-proxy 2>/dev/null || echo "dead")
echo "  frognet-daemon: ${DAEMON_STATUS}"
echo "  frognet-proxy:  ${PROXY_STATUS}"
echo ""

echo "=== Cache nuke complete on ${HOSTNAME} ==="
echo "All requests will now be REQ_FULL until caches rebuild."
echo ""
