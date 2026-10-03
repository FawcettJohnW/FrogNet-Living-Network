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
# =============================================================================
# deploy_broker_v4.sh — Deploy FrogNet Tunnel Broker v4.0 on the droplet
#
# Run on StreamingFrog (the DO droplet).
#
# Designed to install ALONGSIDE v3 — does NOT stop or modify the v3 broker
# or its data.  After this script:
#   v3 (existing):   /opt/frognet_broker_v3   uvicorn:18427  apache:18256
#   v4 (this):       /opt/frognet_broker_v4   uvicorn:18428  apache:18257
#
# What this does:
#   1. Install Python deps in v4 venv
#   2. Deploy v4 broker code to /opt/frognet_broker_v4
#   3. Install systemd unit frognet-broker-v4
#   4. Configure Apache vhost on 18257 (no edits to v3's vhost)
#   5. Start broker, bootstrap admin token to /etc/frognet/admin_token_v4.conf
#   6. Deploy admin PHP page on the droplet's web docroot
#   7. Optionally run migrate_v3_to_v4.py (with --migrate)
#
# Required files in CWD:
#   frognet_broker_v4.py
#   broker_namespace_v4.py
#   migrate_v3_to_v4.py
#   frognet_broker_admin_v4.php
#   pond_admin.html
#
# Usage:
#   sudo ./deploy_broker_v4.sh                # deploy only
#   sudo ./deploy_broker_v4.sh --migrate      # deploy + import v3 ponds/choruses
# =============================================================================

set -euo pipefail
trap 'echo "FATAL: error on line $LINENO, exit $?" >&2; exit 1' ERR

TS()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(TS) $*"; }

DO_MIGRATE=0
[[ "${1:-}" == "--migrate" ]] && DO_MIGRATE=1

BROKER_DIR="/opt/frognet_broker_v4"
VENV="${BROKER_DIR}/venv"
DB_DIR="/var/lib/frognet_broker_v4"
DB_PATH="${DB_DIR}/broker.db"
ADMIN_TOKEN_FILE="/etc/frognet/admin_token_v4.conf"
APACHE_DOCROOT="${APACHE_DOCROOT:-/var/www/html}"

# Verify required files are in CWD before doing anything destructive.
required=(frognet_broker_v4.py broker_namespace_v4.py migrate_v3_to_v4.py
          frognet_broker_admin_v4.php pond_admin.html)
for f in "${required[@]}"; do
    [[ -f "$f" ]] || { log "FATAL: missing required file: $f"; exit 1; }
done

# ---------------------------------------------------------------------------
# Step 1: Python venv + deps  (does NOT touch v3 venv)
# ---------------------------------------------------------------------------

log "Step 1: Python venv + dependencies in $VENV"
mkdir -p "$BROKER_DIR" "$DB_DIR"
mkdir -p /etc/frognet

if [[ ! -d "$VENV" ]]; then
    python3 -m venv "$VENV"
fi
"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet --upgrade \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "pydantic>=2.0"
"${VENV}/bin/pip" show fastapi uvicorn pydantic | grep -E '^(Name|Version):'

# ---------------------------------------------------------------------------
# Step 2: Copy code
# ---------------------------------------------------------------------------

log "Step 2: Deploying code to $BROKER_DIR"
cp frognet_broker_v4.py    "${BROKER_DIR}/"
cp broker_namespace_v4.py  "${BROKER_DIR}/"
cp migrate_v3_to_v4.py     "${BROKER_DIR}/"

# ---------------------------------------------------------------------------
# Step 3: Systemd unit
# ---------------------------------------------------------------------------

log "Step 3: Installing systemd unit frognet-broker-v4"
cat > /etc/systemd/system/frognet-broker-v4.service <<EOF
[Unit]
Description=FrogNet Tunnel Broker v4.0 (pond/chorus + admin)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BROKER_DIR}
ExecStart=${VENV}/bin/uvicorn frognet_broker_v4:app \\
    --host 127.0.0.1 --port 18428 --log-level info
Restart=on-failure
RestartSec=5
Environment=FROGNET_BROKER_DIR=${DB_DIR}
Environment=FROGNET_BROKER_LOG=INFO
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable frognet-broker-v4

# ---------------------------------------------------------------------------
# Step 4: Apache vhost on 18257
# ---------------------------------------------------------------------------

log "Step 4: Apache reverse proxy on 18257 (v3's 18256 untouched)"

a2enmod proxy proxy_http 2>/dev/null || true

cat > /etc/apache2/sites-available/frognet-broker-v4-proxy.conf <<'APACHE'
# FrogNet Broker v4.0 — HTTPS on 18257 -> uvicorn 127.0.0.1:18428
# Sits next to v3's vhost on 18256 -> 18427.
<VirtualHost *:18257>
    ServerName streamingfrog.com

    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:18428/
    ProxyPassReverse / http://127.0.0.1:18428/

    ErrorLog  ${APACHE_LOG_DIR}/frognet-broker-v4-error.log
    CustomLog ${APACHE_LOG_DIR}/frognet-broker-v4-access.log combined
</VirtualHost>
APACHE

if ! grep -q "^Listen 18257" /etc/apache2/ports.conf; then
    echo "Listen 18257" >> /etc/apache2/ports.conf
fi

a2ensite frognet-broker-v4-proxy 2>/dev/null || true
apachectl configtest

# ---------------------------------------------------------------------------
# Step 5: Start broker + bootstrap admin token
# ---------------------------------------------------------------------------

log "Step 5: Starting frognet-broker-v4"
systemctl start frognet-broker-v4
sleep 3

if ! systemctl is-active --quiet frognet-broker-v4; then
    log "FATAL: broker failed to start"
    journalctl -u frognet-broker-v4 -n 40 --no-pager
    exit 1
fi

log "  Bootstrapping admin token..."
BOOTSTRAP=$(curl -sf -X POST http://127.0.0.1:18428/api/v1/bootstrap \
    -H "Content-Type: application/json" || true)
NEW_TOKEN=$(echo "$BOOTSTRAP" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('admin_token',''))" \
    2>/dev/null || true)

if [[ -n "$NEW_TOKEN" ]]; then
    echo "$NEW_TOKEN" > "$ADMIN_TOKEN_FILE"
    chmod 600 "$ADMIN_TOKEN_FILE"
    chown root:www-data "$ADMIN_TOKEN_FILE" 2>/dev/null || \
        chown root:root "$ADMIN_TOKEN_FILE"
    chmod 640 "$ADMIN_TOKEN_FILE"
    log "  Admin token saved: $ADMIN_TOKEN_FILE"
else
    log "  Token already bootstrapped (broker had one in DB)"
fi

# ---------------------------------------------------------------------------
# Step 6: Admin PHP page
# ---------------------------------------------------------------------------

log "Step 6: Deploying admin web page"

if [[ -d "$APACHE_DOCROOT" ]]; then
    cp frognet_broker_admin_v4.php "${APACHE_DOCROOT}/"
    cp pond_admin.html             "${APACHE_DOCROOT}/"
    chown www-data:www-data "${APACHE_DOCROOT}/frognet_broker_admin_v4.php" \
                            "${APACHE_DOCROOT}/pond_admin.html" 2>/dev/null || true
    chmod 644              "${APACHE_DOCROOT}/frognet_broker_admin_v4.php" \
                           "${APACHE_DOCROOT}/pond_admin.html"
    log "  Deployed to ${APACHE_DOCROOT}/pond_admin.html"
else
    log "  WARNING: APACHE_DOCROOT=${APACHE_DOCROOT} does not exist — "
    log "  copy frognet_broker_admin_v4.php and pond_admin.html manually."
fi

systemctl reload apache2

# ---------------------------------------------------------------------------
# Step 7: Optional migration
# ---------------------------------------------------------------------------

if (( DO_MIGRATE )); then
    log "Step 7: Migrating v3 ponds + choruses into v4"
    "${VENV}/bin/python3" "${BROKER_DIR}/migrate_v3_to_v4.py"
fi

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

echo ""
log "Verification:"
log "  systemctl status frognet-broker-v3: $(systemctl is-active frognet-broker-v3 2>/dev/null || echo 'not-installed')"
log "  systemctl status frognet-broker-v4: $(systemctl is-active frognet-broker-v4)"

curl -sf http://127.0.0.1:18428/api/v1/ponds \
    -H "Authorization: Bearer $(cat "$ADMIN_TOKEN_FILE" 2>/dev/null)" \
    >/dev/null 2>&1 \
    && log "  v4 broker direct (18428): OK" \
    || log "  v4 broker direct (18428): FAILED"

curl -sf http://127.0.0.1:18257/api/v1/ponds \
    -H "Authorization: Bearer $(cat "$ADMIN_TOKEN_FILE" 2>/dev/null)" \
    >/dev/null 2>&1 \
    && log "  v4 Apache proxy (18257): OK" \
    || log "  v4 Apache proxy (18257): FAILED"

echo ""
log "Deployment complete."
echo ""
echo "Admin page:  https://streamingfrog.com:18257/pond_admin.html"
echo "             (page password: Fr0gN3t!)"
echo ""
echo "Node setup:  setup_lillypad.bash <NodeName> --broker https://streamingfrog.com:18257"
echo ""
