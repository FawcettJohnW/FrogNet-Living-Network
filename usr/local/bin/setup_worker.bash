#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC              #
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
# set -x
#
#  setup_worker.bash - FrogNet Worker Node Setup
#
#  Worker nodes are non-routing nodes that:
#    - Get DHCP from upstream FrogNet
#    - Run Apache + MySQL for local apps
#    - Run semantic proxy/daemon for compression
#    - Know only about upstream FrogNet + databasehost.frognet
#    - Do NOT participate in routing/discovery
#
#  Arguments:
#    $1 = Worker hostname (e.g., "SensorHost1")
#    $2 = Upstream FrogNet gateway IP (e.g., "10.101.50.1")
#    $3 = (Optional) Semantic cache secret (hex string)
#         If not provided, will fetch from upstream or generate
#
#  Example:
#    ./setup_worker.bash SensorHost1 10.101.50.1
#    ./setup_worker.bash SensorHost1 10.101.50.1 abc123...def456
#

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <WorkerHostname> <UpstreamGatewayIP> [secret]"
    echo ""
    echo "  WorkerHostname:    Name for this worker (e.g., SensorHost1)"
    echo "  UpstreamGatewayIP: Gateway IP of upstream FrogNet (e.g., 10.101.50.1)"
    echo "  secret:            (Optional) Shared secret in hex format"
    echo ""
    echo "Example:"
    echo "  $0 SensorHost1 10.101.50.1"
    exit 1
fi

WORKER_NAME="$1"
UPSTREAM_GW="$2"
SECRET_HEX="${3:-}"

log() { echo "[setup_worker] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

log "Setting up FrogNet Worker: $WORKER_NAME"
log "Upstream gateway: $UPSTREAM_GW"

# Validate upstream IP
if ! [[ "$UPSTREAM_GW" =~ ^10\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Upstream gateway must be 10.x.x.x format"
    exit 1
fi

# ============================================================
# Create directories
# ============================================================
mkdir -p /etc/frognet /etc/sentinels

# ============================================================
# Configure semantic cache secret
# ============================================================
SECRET_FILE="/etc/frognet/sem_cache_secret"

if [[ -n "$SECRET_HEX" ]]; then
    # Use provided secret
    log "Using provided secret"
    echo "$SECRET_HEX" > "$SECRET_FILE"
    chmod 0600 "$SECRET_FILE"
elif [[ ! -f "$SECRET_FILE" ]]; then
    # Try to fetch from upstream
    log "Attempting to fetch secret from upstream..."
    
    # This requires the upstream to have an endpoint for secret distribution
    # For security, this should be done via a secure channel in production
    # For now, we generate a local secret (will be incompatible with upstream cache)
    
    log "WARNING: Generating local secret - cache tokens won't match upstream"
    log "         For full cache compatibility, provide the shared secret as argument 3"
    head -c 32 /dev/urandom | xxd -p -c 64 > "$SECRET_FILE"
    chmod 0600 "$SECRET_FILE"
else
    log "Using existing secret: $SECRET_FILE"
fi

# ============================================================
# Set hostname
# ============================================================
log "Setting hostname: $WORKER_NAME"
hostname "$WORKER_NAME"
echo "$WORKER_NAME" > /etc/hostname

# ============================================================
# Configure /etc/hosts (minimal - just upstream and databasehost)
# ============================================================
log "Configuring /etc/hosts..."

# Determine upstream hostname by querying frognet_echo
UPSTREAM_ECHO=""
UPSTREAM_HOSTNAME=""

if command -v curl >/dev/null 2>&1; then
    UPSTREAM_ECHO=$(curl -fsS --connect-timeout 5 --max-time 5 \
        -H "Host: $UPSTREAM_GW" \
        "http://$UPSTREAM_GW/frognet_echo.php" 2>/dev/null || true)
fi

if [[ -n "$UPSTREAM_ECHO" ]]; then
    UPSTREAM_HOSTNAME=$(echo "$UPSTREAM_ECHO" | cut -d',' -f1)
    log "Discovered upstream hostname: $UPSTREAM_HOSTNAME"
else
    UPSTREAM_HOSTNAME="upstream.frognet"
    log "Could not discover upstream hostname, using: $UPSTREAM_HOSTNAME"
fi

# Get our IP (from DHCP)
OUR_IP=""
for iface in eth0 wlan0 ens33 enp0s3; do
    if ip link show "$iface" >/dev/null 2>&1; then
        OUR_IP=$(ip -4 addr show "$iface" 2>/dev/null | grep 'inet ' | head -1 | awk '{print $2}' | cut -d/ -f1)
        [[ -n "$OUR_IP" ]] && break
    fi
done

log "Our IP: ${OUR_IP:-unknown}"

# Write minimal /etc/hosts
cat > /etc/hosts <<EOF
# FrogNet Worker Node: $WORKER_NAME
# Generated by setup_worker.bash on $(date)
# 
# This is a WORKER node - minimal hosts configuration
# Only knows about: localhost, self, upstream, databasehost

127.0.0.1       localhost
${OUR_IP:-127.0.1.1}       $WORKER_NAME

# Upstream FrogNet gateway
$UPSTREAM_GW    $UPSTREAM_HOSTNAME upstream.frognet

# Database host (routes via upstream to actual database)
$UPSTREAM_GW    databasehost.frognet
EOF

log "Wrote /etc/hosts"

# ============================================================
# Configure /etc/resolv.conf
# ============================================================
log "Configuring DNS..."

cat > /etc/resolv.conf <<EOF
# FrogNet Worker DNS
# Generated by setup_worker.bash
nameserver $UPSTREAM_GW
EOF

# Also write to sentinels for compatibility
echo "nameserver $UPSTREAM_GW" > /etc/sentinels/frog_resolv.conf

# ============================================================
# Configure gateway info for frognet_echo (if this worker serves it)
# ============================================================
cat > /etc/frognet/gateways.conf <<EOF
# FrogNet Worker gateway configuration
# Generated by setup_worker.bash on $(date)
# Workers report upstream as their gateway
GW1=$UPSTREAM_GW
GW2=0.0.0.0
EOF

# Store upstream IP
echo "$UPSTREAM_GW" > /etc/database_ip

# ============================================================
# Mark this as a worker node (not a full FrogNet)
# ============================================================
touch /etc/frognet/worker_node
cat > /etc/frognet/node_info <<EOF
NODE_TYPE=worker
WORKER_NAME=$WORKER_NAME
UPSTREAM_GW=$UPSTREAM_GW
UPSTREAM_HOSTNAME=$UPSTREAM_HOSTNAME
SETUP_DATE=$(date -Iseconds)
EOF

# ============================================================
# Disable discovery scripts (workers don't route)
# ============================================================
log "Disabling discovery services (workers don't route)..."

# Disable the sync timer if it exists
if systemctl is-enabled frognet-sync.timer 2>/dev/null; then
    systemctl disable frognet-sync.timer || true
    systemctl stop frognet-sync.timer || true
fi

# Disable transit watch
if systemctl is-enabled frognet-transit-watch 2>/dev/null; then
    systemctl disable frognet-transit-watch || true
    systemctl stop frognet-transit-watch || true
fi

# ============================================================
# Start/restart semantic services (workers DO use semantic compression)
# ============================================================
log "Starting semantic services..."

if systemctl is-enabled frognet-daemon 2>/dev/null; then
    systemctl restart frognet-daemon || true
    log "Started frognet-daemon"
fi

if systemctl is-enabled frognet-proxy 2>/dev/null; then
    systemctl restart frognet-proxy || true
    log "Started frognet-proxy"
fi

#
# ============================================================
# Test connectivity to upstream
# ============================================================
log "Testing connectivity..."

# frognet_echo IS the aliveness test — no separate ping.
if curl -fsS --connect-timeout 5 --max-time 5 \
    -H "Host: $UPSTREAM_GW" \
    "http://$UPSTREAM_GW/frognet_echo.php" >/dev/null 2>&1; then
    log "✓ FrogNet echo to upstream ($UPSTREAM_GW): OK"
else
    log "✗ FrogNet echo to upstream ($UPSTREAM_GW): FAILED"
fi

if curl -fsS --connect-timeout 5 --max-time 5 \
    -H "Host: databasehost.frognet" \
    "http://databasehost.frognet/frognet_echo.php" >/dev/null 2>&1; then
    log "✓ FrogNet echo to databasehost.frognet: OK"
else
    log "✗ FrogNet echo to databasehost.frognet: FAILED"
fi

# ============================================================
# Done
# ============================================================
log "============================================"
log "FrogNet Worker setup complete!"
log "  Worker Name:    $WORKER_NAME"
log "  Our IP:         ${OUR_IP:-unknown (DHCP pending)}"
log "  Upstream:       $UPSTREAM_GW ($UPSTREAM_HOSTNAME)"
log "  Database:       databasehost.frognet → $UPSTREAM_GW"
log "  Secret:         $SECRET_FILE"
log "  Node Type:      worker (non-routing)"
log ""
log "This node will:"
log "  ✓ Use semantic compression for slow links"
log "  ✓ Route API calls via upstream to databasehost"
log "  ✗ NOT participate in FrogNet discovery"
log "  ✗ NOT route traffic for other nodes"
log "============================================"

exit 0
