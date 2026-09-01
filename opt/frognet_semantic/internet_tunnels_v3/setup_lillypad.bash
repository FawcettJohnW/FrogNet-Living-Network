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
##############################################################
# setup_lillypad.bash - FrogNet Gateway Setup
#
# Usage:
#   sudo bash setup_lillypad.bash <NetworkName> --broker <url> [options]
#   sudo bash setup_lillypad.bash <NetworkName> --offline [--ip 10.x.x.1]
#
# Options:
#   --broker <url>   URL of the FrogNet broker (e.g. https://broker.example.com:18427)
#   --offline        Skip broker contact entirely
#   --ip <addr>      Specific gateway IP to use (offline mode only; must end in .1)
#   --pond-password  Pond password if required
#
# Online:  broker allocates IP, setup_lillypad configures LAN,
#          then chains to frognet_tunnel_setup.sh automatically.
# Offline: IP is chosen (arg, or random ping-checked), LAN is configured,
#          frognet_tunnel_setup.sh is NOT run (no broker available).
##############################################################

set -euo pipefail

log()  { echo "[setup_lillypad] $(date '+%H:%M:%S') $*"; }
die()  { log "FATAL: $*"; exit 1; }
warn() { log "WARNING: $*"; }

# ------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------

NETWORK_NAME="${1:-}"
[[ $EUID -eq 0 ]]    || die "Must run as root"
[[ -n "$NETWORK_NAME" ]] || die "Usage: $0 <NetworkName> --broker <url> | --offline [--ip 10.x.x.1]"
shift

BROKER_URL=""
OFFLINE=0
REQUESTED_IP=""
POND_PASSWORD=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --broker)         BROKER_URL="$2"; shift 2 ;;
        --offline)        OFFLINE=1;       shift   ;;
        --ip)             REQUESTED_IP="$2"; shift 2 ;;
        --pond-password)  POND_PASSWORD="$2"; shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done

[[ $OFFLINE -eq 0 || -n "$BROKER_URL" || $OFFLINE -eq 1 ]] || true   # offline needs no broker

if [[ $OFFLINE -eq 0 && -z "$BROKER_URL" ]]; then
    die "Either --broker <url> or --offline is required"
fi

if [[ -n "$REQUESTED_IP" && $OFFLINE -eq 0 ]]; then
    die "--ip is only valid with --offline (online mode gets IP from broker)"
fi

if [[ -n "$REQUESTED_IP" ]]; then
    [[ "$REQUESTED_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || \
        die "--ip must be in 10.x.x.1 format"
    # Reject reserved ranges
    SECOND=$(echo "$REQUESTED_IP" | cut -d. -f2)
    THIRD=$(echo "$REQUESTED_IP"  | cut -d. -f3)
    [[ "$SECOND" -eq 253 || "$SECOND" -eq 254 ]] && \
        die "10.253.x.x and 10.254.x.x are reserved"
fi

# ------------------------------------------------------------
# Resolve actual interface name via mapInterfaces
# ------------------------------------------------------------
. /usr/local/bin/mapInterfaces
ETH0="${eth0Name:-eth0}"
log "Resolved eth0 interface: $ETH0"

ip link show "$ETH0" >/dev/null 2>&1 || \
    die "Interface '$ETH0' does not exist. Check /etc/frognet/interfaces_override.conf"

# ------------------------------------------------------------
# Determine GATEWAY_IP
# ------------------------------------------------------------

_ping_check() {
    local ip="$1"
    # -W 1: 1 second timeout, -c 2: two packets
    if ping -c 2 -W 1 "$ip" >/dev/null 2>&1; then
        return 0   # something answered
    fi
    return 1       # silent
}

_random_ip() {
    # Generate 10.<rand_second>.<rand_third>.1
    # Exclude reserved: 253 (transit) and 254 (chorus)
    local second third
    while true; do
        second=$(( RANDOM % 254 + 1 ))
        [[ "$second" -eq 253 || "$second" -eq 254 ]] && continue
        third=$(( RANDOM % 254 + 1 ))
        echo "10.${second}.${third}.1"
        return
    done
}

if [[ $OFFLINE -eq 1 ]]; then
    # ── Offline path ──────────────────────────────────────────
    if [[ -n "$REQUESTED_IP" ]]; then
        log "Offline mode: using requested IP $REQUESTED_IP"
        if _ping_check "$REQUESTED_IP"; then
            die "Something already answers at $REQUESTED_IP. Choose a different address."
        fi
        GATEWAY_IP="$REQUESTED_IP"
    else
        log "Offline mode: selecting random IP (ping-checked)..."
        ATTEMPTS=0
        while true; do
            CANDIDATE=$(_random_ip)
            ATTEMPTS=$(( ATTEMPTS + 1 ))
            if [[ $ATTEMPTS -gt 10 ]]; then
                die "Could not find a free address after 10 attempts. " \
                    "Your LAN may be very crowded, or specify --ip manually."
            fi
            if ! _ping_check "$CANDIDATE"; then
                GATEWAY_IP="$CANDIDATE"
                log "Selected IP: $GATEWAY_IP (attempt $ATTEMPTS)"
                break
            fi
            log "  $CANDIDATE is in use, trying another..."
        done
    fi

    # Store broker URL if provided alongside --offline (for later use)
    if [[ -n "$BROKER_URL" ]]; then
        mkdir -p /etc/frognet
        echo "BROKER_URL=${BROKER_URL}" > /etc/frognet/broker.conf
        chmod 600 /etc/frognet/broker.conf
        log "Broker URL stored for later use by frognet_tunnel_setup.sh"
    else
        log "No broker URL provided. Run frognet_tunnel_setup.sh manually once broker is reachable."
    fi

else
    # ── Online path ───────────────────────────────────────────
    log "Online mode: requesting IP from broker at $BROKER_URL ..."

    ALLOC_BODY="{\"pond_password\":\"${POND_PASSWORD}\"}"
    ALLOC_URL="${BROKER_URL%/}/api/v4/ponds/${NETWORK_NAME}/allocate-ip"

    RESPONSE=$(curl -sk -X POST "$ALLOC_URL" \
        -H "Content-Type: application/json" \
        -d "$ALLOC_BODY" 2>&1) || true

    GATEWAY_IP=$(echo "$RESPONSE" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d['ip'])" 2>/dev/null || true)

    if [[ -z "$GATEWAY_IP" ]]; then
        log "Broker unreachable or returned an error."
        log "Response was: $RESPONSE"
        log ""
        log "To allocate an IP manually from another machine, run:"
        log "  curl -sk -X POST ${ALLOC_URL} \\"
        log "    -H 'Content-Type: application/json' \\"
        log "    -d '{\"pond_password\":\"${POND_PASSWORD}\"}'"
        log ""
        read -r -p "[setup_lillypad] Enter IP to use (10.x.x.1 format): " GATEWAY_IP
        [[ "$GATEWAY_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || \
            die "Invalid IP format. Must be 10.x.x.1"
    fi

    # Store broker URL
    mkdir -p /etc/frognet
    cat > /etc/frognet/broker.conf << EOF
BROKER_URL=${BROKER_URL}
EOF
    chmod 600 /etc/frognet/broker.conf
    log "Broker URL stored: /etc/frognet/broker.conf"
fi

log "Gateway IP: $GATEWAY_IP"

# ============================================================
# All remaining steps are identical to the original script.
# Extract subnet info and proceed.
# ============================================================

IP_BASE="${GATEWAY_IP%.*}"
SUBNET="${IP_BASE}.0/24"
DHCP_START="${IP_BASE}.2"
DHCP_END="${IP_BASE}.254"

log "Configuring FrogNet gateway: $NETWORK_NAME"
log "  Gateway IP:  $GATEWAY_IP"
log "  Subnet:      $SUBNET"
log "  DHCP range:  $DHCP_START - $DHCP_END"

# ============================================================
# Step 1: Stop services that might interfere
# ============================================================
log "Step 1: Stopping interfering services..."
systemctl stop dnsmasq 2>/dev/null || true
kill -9 $(pgrep dnsmasq) 2>/dev/null || true
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true

mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/no-resolved.conf << 'NMEOF'
[main]
dns=none
NMEOF
systemctl restart NetworkManager 2>/dev/null || true
systemctl stop frognet-transit-boot 2>/dev/null || true
systemctl stop frognet-transit-watch 2>/dev/null || true

# ============================================================
# Step 1b: Purge stale identity from cloned image
# ============================================================
log "Step 1b: Purging stale config from prior identity..."

rm -f /etc/dnsmasq.d/opts_only.conf
rm -f /etc/dnsmasq.d/frognet_forwarders_auto.conf
rm -f /etc/dnsmasq.d/frognet_databasehost.conf
rm -f /etc/sentinels/frognet_hosts
rm -f /etc/sentinels/frognet_hosts_out
rm -f /etc/sentinels/frognet_resolv
rm -f /etc/sentinels/frog_resolv.conf
rm -f /etc/sentinels/expected_routes
rm -f /etc/sentinels/transit_upstream_seeds
rm -f /etc/sentinels/mergePending
rm -f /etc/sentinels/runAgain
rm -f /etc/sentinels/sync_required
rm -f /etc/sentinels/forwarded
rm -f /etc/sentinels/new_resolv.conf
rm -f /etc/frognet/gateways.conf
rm -f /etc/database_ip
rm -f /var/lib/misc/dnsmasq.leases
rm -f /etc/resolv.conf
echo "nameserver 127.0.0.1" > /etc/resolv.conf

log "Step 1b: Purge complete"

# ============================================================
# Step 1c: Reconcile services for cloned image
# ============================================================
log "Step 1c: Reconciling cloned image to this machine..."

if ls /etc/mysql/mariadb.conf.d/provider_*.cnf >/dev/null 2>&1; then
    log "  Removing stale MariaDB provider plugin configs..."
    rm -f /etc/mysql/mariadb.conf.d/provider_*.cnf
fi

mkdir -p /var/log/mysql
touch /var/log/mysql/slow_query.log
chown mysql:mysql /var/log/mysql /var/log/mysql/slow_query.log 2>/dev/null || true

systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || true
sleep 2

FROG_PASS=""
if [[ -f /var/www/html/config.php ]]; then
    FROG_PASS=$(grep -oP "define\('DB_PASS',\s*'\\K[^']+" /var/www/html/config.php 2>/dev/null)
fi
if [[ -z "$FROG_PASS" ]] && [[ -f /opt/frognet_semantic/DB_CONFIG.json ]]; then
    FROG_PASS=$(python3 -c "import json; print(json.load(open('/opt/frognet_semantic/DB_CONFIG.json'))['password'])" 2>/dev/null)
fi
if [[ -n "$FROG_PASS" ]]; then
    log "  Syncing FrogUser password from config..."
    mysql -u root <<SQLEOF || log "  WARNING: FrogUser password sync failed"
DROP USER IF EXISTS 'FrogUser'@'localhost';
DROP USER IF EXISTS 'FrogUser'@'%';
CREATE USER 'FrogUser'@'localhost' IDENTIFIED BY '${FROG_PASS}';
CREATE USER 'FrogUser'@'%' IDENTIFIED BY '${FROG_PASS}';
GRANT ALL ON FrogNet.* TO 'FrogUser'@'localhost';
GRANT ALL ON FrogNet.* TO 'FrogUser'@'%';
GRANT ALL ON FrogNetFamily.* TO 'FrogUser'@'localhost';
GRANT ALL ON FrogNetFamily.* TO 'FrogUser'@'%';
FLUSH PRIVILEGES;
SQLEOF
    if mysql -u FrogUser -p"${FROG_PASS}" FrogNet -e "SELECT 1" >/dev/null 2>&1; then
        log "  FrogUser password sync OK"
    else
        log "  WARNING: FrogUser still cannot authenticate"
    fi
else
    log "  WARNING: Could not find FrogUser password"
fi

INSTALLED_PHP=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null)
if [[ -n "$INSTALLED_PHP" ]]; then
    for mod in /etc/apache2/mods-enabled/php*.load; do
        [[ -f "$mod" ]] || continue
        mod_name=$(basename "$mod" .load)
        if [[ "$mod_name" != "php${INSTALLED_PHP}" ]]; then
            log "  Disabling mismatched Apache module: $mod_name"
            a2dismod "$mod_name" 2>/dev/null || true
        fi
    done
    if [[ -f "/usr/lib/apache2/modules/libphp${INSTALLED_PHP}.so" ]]; then
        a2enmod "php${INSTALLED_PHP}" 2>/dev/null || true
    fi
    for fpm_conf in /etc/apache2/conf-enabled/php*-fpm.conf; do
        [[ -f "$fpm_conf" ]] || continue
        fpm_name=$(basename "$fpm_conf" .conf)
        if [[ "$fpm_name" != "php${INSTALLED_PHP}-fpm" ]]; then
            a2disconf "$fpm_name" 2>/dev/null || true
        fi
    done
fi

systemctl restart apache2 2>/dev/null || true
systemctl restart frognet-proxy frognet-daemon 2>/dev/null || true

log "Step 1c: Reconciliation complete"

# ============================================================
# Step 2: Clean up NetworkManager connections
# ============================================================
log "Step 2: Cleaning up NetworkManager connections..."

for uuid in $(nmcli -t -f UUID,TYPE,DEVICE c show 2>/dev/null | grep ':ethernet:' | grep -E ":${ETH0}\$|:--\$" | cut -d: -f1); do
    nmcli conn del "$uuid" 2>/dev/null || true
done
while nmcli conn show "$NETWORK_NAME" >/dev/null 2>&1; do
    nmcli conn del "$NETWORK_NAME" 2>/dev/null || true
    sleep 0.5
done

# ============================================================
# Step 3: Prepare interface
# ============================================================
log "Step 3: Preparing $ETH0..."
nmcli dev set "$ETH0" managed yes 2>/dev/null || true
ip addr flush dev "$ETH0" 2>/dev/null || true
ip link set "$ETH0" up
sleep 1

# ============================================================
# Step 4: Set IP directly
# ============================================================
log "Step 4: Setting IP directly on $ETH0..."
ip addr add "${GATEWAY_IP}/24" dev "$ETH0" 2>/dev/null || true
ip route add "$SUBNET" dev "$ETH0" 2>/dev/null || true

# ============================================================
# Step 5: Create NetworkManager connection
# ============================================================
log "Step 5: Creating NetworkManager connection..."
nmcli conn add con-name "$NETWORK_NAME" \
    ifname "$ETH0" \
    type ethernet \
    ip4 "${GATEWAY_IP}/24" \
    ipv4.method manual \
    ipv4.dns "$GATEWAY_IP" \
    ipv4.gateway "" \
    connection.autoconnect yes \
    connection.autoconnect-priority 999 \
    connection.autoconnect-retries 0

# ============================================================
# Step 6: Activate connection
# ============================================================
log "Step 6: Activating connection..."
if ! nmcli conn up "$NETWORK_NAME" 2>/dev/null; then
    nmcli conn up "$NETWORK_NAME" ifname "$ETH0" 2>/dev/null || true
fi
if ! nmcli -t -f DEVICE c show --active 2>/dev/null | grep -q "^${ETH0}\$"; then
    nmcli dev disconnect "$ETH0" 2>/dev/null || true
    sleep 1
    nmcli dev connect "$ETH0" 2>/dev/null || true
fi

# ============================================================
# Step 7: Verify
# ============================================================
log "Step 7: Verifying configuration..."
CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
if [[ "$CURRENT_IP" != "$GATEWAY_IP" ]]; then
    log "  IP mismatch (got $CURRENT_IP, want $GATEWAY_IP) — forcing..."
    ip addr flush dev "$ETH0"
    ip addr add "${GATEWAY_IP}/24" dev "$ETH0"
    ip link set "$ETH0" up
fi
log "  $ETH0 IP: $(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"

# ============================================================
# Step 8: Hostname
# ============================================================
log "Step 8: Setting hostname..."
FQDN="FrogNetHost.${NETWORK_NAME}"
hostnamectl set-hostname "$FQDN" 2>/dev/null || hostname "$FQDN"
echo "$FQDN" > /etc/hostname
grep -q "$FQDN" /etc/hosts 2>/dev/null || \
    echo "$GATEWAY_IP $FQDN FrogNetHost" >> /etc/hosts

# ============================================================
# Step 9: dnsmasq
# ============================================================
log "Step 9: Configuring dnsmasq..."
mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/opts_only.conf << EOF
# FrogNet dnsmasq configuration
# Generated by setup_lillypad.bash on $(date)
domain=$NETWORK_NAME
local=/$NETWORK_NAME/
expand-hosts
dhcp-range=${DHCP_START},${DHCP_END},24h
dhcp-option=option:router,$GATEWAY_IP
dhcp-option=option:dns-server,$GATEWAY_IP
dhcp-authoritative
EOF
systemctl restart dnsmasq 2>/dev/null || service dnsmasq restart 2>/dev/null || true

# ============================================================
# Step 10: Config files
# ============================================================
log "Step 10: Creating config files..."
mkdir -p /etc/frognet /etc/sentinels

cat > /etc/frognet/gateways.conf << EOF
# FrogNet gateway configuration
# Generated by setup_lillypad.bash on $(date)
NETWORK_NAME=$NETWORK_NAME
GATEWAY_IP=$GATEWAY_IP
GW1=$GATEWAY_IP
GW2=0.0.0.0
EOF

echo "$GATEWAY_IP" > /etc/database_ip
echo "nameserver $GATEWAY_IP" > /etc/sentinels/frog_resolv.conf

# ============================================================
# Step 11: Boot-time fixup service
# ============================================================
log "Step 11: Creating boot-time fixup service..."

cat > /usr/local/bin/frognet_eth0_fixup.sh << 'FIXUPEOF'
#!/bin/bash
CONF="/etc/frognet/gateways.conf"
[[ -f "$CONF" ]] || exit 0
source "$CONF"
[[ -n "$GATEWAY_IP" ]] || exit 0
. /usr/local/bin/mapInterfaces
DEV="${eth0Name:-eth0}"
CURRENT=$(ip -4 -o addr show dev "$DEV" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
[[ "$CURRENT" == "$GATEWAY_IP" ]] && exit 0
ip addr flush dev "$DEV" 2>/dev/null || true
ip addr add "${GATEWAY_IP}/24" dev "$DEV"
ip link set "$DEV" up
nmcli conn up "$NETWORK_NAME" 2>/dev/null || true
exit 0
FIXUPEOF
chmod +x /usr/local/bin/frognet_eth0_fixup.sh

cat > /etc/systemd/system/frognet-eth0-fixup.service << EOF
[Unit]
Description=FrogNet Gateway IP Fixup ($ETH0)
Before=network.target frognet-transit-boot.service
After=sys-subsystem-net-devices-${ETH0}.device
Wants=sys-subsystem-net-devices-${ETH0}.device

[Service]
Type=oneshot
ExecStart=/usr/local/bin/frognet_eth0_fixup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable frognet-eth0-fixup.service

# ============================================================
# Step 12: Configure transit to skip eth0
# ============================================================
log "Step 12: Configuring transit to skip $ETH0..."
echo "$ETH0" > /etc/frognet/transit_exclude_interfaces

# ============================================================
# Step 13: Restart services
# ============================================================
log "Step 13: Restarting services..."
systemctl restart mariadb 2>/dev/null || systemctl restart mysql 2>/dev/null || true
systemctl restart apache2 2>/dev/null || true
systemctl restart dnsmasq 2>/dev/null || true
systemctl restart frognet-proxy 2>/dev/null || true
systemctl restart frognet-daemon 2>/dev/null || true
systemctl start frognet-transit-boot 2>/dev/null || true
systemctl start frognet-transit-watch 2>/dev/null || true

# ============================================================
# Online only: chain to frognet_tunnel_setup.sh
# ============================================================

if [[ $OFFLINE -eq 0 ]]; then
    log ""
    log "LAN setup complete. Registering with broker..."
    TUNNEL_ARGS="--pond-password ${POND_PASSWORD}"
    if /usr/local/bin/frognet_tunnel_setup.sh ${TUNNEL_ARGS}; then
        log "Tunnel setup complete."
    else
        warn "frognet_tunnel_setup.sh failed — you may need to run it manually."
    fi
else
    log ""
    log "Offline setup complete. To register with the broker once it is reachable:"
    log "  frognet_tunnel_setup.sh"
fi

# ============================================================
# Final verification
# ============================================================
log ""
log "============================================"
log "Setup complete!"
log ""
log "$ETH0 configuration:"
ip addr show "$ETH0" | grep -E "inet |state"
log ""
log "Service health:"
for svc in mariadb apache2 dnsmasq frognet-proxy frognet-daemon; do
    status=$(systemctl is-active "$svc" 2>/dev/null || echo "not found")
    log "  $svc: $status"
done
log ""
log "frognet_echo:"
ECHO_RESULT=$(curl -fsS --max-time 5 http://127.0.0.1/frognet_echo.php 2>&1) || true
log "  ${ECHO_RESULT:-FAILED}"
log "============================================"
