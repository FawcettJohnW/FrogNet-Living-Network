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
###############################################################################
# frognet_fixup.sh — Fix a FrogNet node installed from a cloned image
#
# Handles every issue found during the April 2026 deployment:
#   - Stale identity from cloned parent (configs, dnsmasq, sentinels)
#   - MariaDB: provider plugins, slow_query.log, buffer pool, FrogUser auth
#   - Apache: PHP version mismatch, FPM socket mismatch
#   - dnsmasq: wrong listen-address, crash-loop
#   - systemd-resolved conflict on port 53
#   - NetworkManager dns=none, interface name mismatches
#   - Interface auto-detection for non-Pi hardware
#   - Service enable/start
#
# Usage:
#   sudo bash frognet_fixup.sh <NodeName> <GatewayIP>
#   sudo bash frognet_fixup.sh NYC5Box 10.101.65.1
#
# Safe to run multiple times (idempotent).
###############################################################################
set -eu

log() { echo "[fixup] $(date '+%H:%M:%S') $*"; }
die() { log "FATAL: $*"; exit 1; }

NODE_NAME="${1:-}"
GATEWAY_IP="${2:-}"

[[ $EUID -eq 0 ]] || die "Must run as root"
[[ -n "$NODE_NAME" ]] || die "Usage: $0 <NodeName> <GatewayIP>"
[[ -n "$GATEWAY_IP" ]] || die "Usage: $0 <NodeName> <GatewayIP>"
[[ "$GATEWAY_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || die "Gateway IP must be 10.x.x.1"

IP_BASE="${GATEWAY_IP%.*}"

###############################################################################
# 1. Auto-detect interfaces
###############################################################################
log "=== Step 1: Detecting interfaces ==="

_detect_eth0() {
    for dev in $(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == lo || "$dev" == veth* || "$dev" == docker* || "$dev" == br-* || "$dev" == virbr* || "$dev" == wl* || "$dev" == wg* ]] && continue
        echo "$dev"
        return
    done
}
_detect_wlan() {
    local idx=0
    for dev in $(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == wl* ]] || continue
        if [[ $idx -eq $1 ]]; then echo "$dev"; return; fi
        idx=$((idx + 1))
    done
}

ETH0="$(_detect_eth0 || true)"
ETH0="${ETH0:-eth0}"
WLAN0="$(_detect_wlan 0 || true)"
WLAN0="${WLAN0:-wlan0}"
WLAN1="$(_detect_wlan 1 || true)"
WLAN1="${WLAN1:-wlan1}"

if ! ip link show "$ETH0" >/dev/null 2>&1; then
    die "Detected interface '$ETH0' does not exist"
fi

log "eth0=$ETH0  wlan0=$WLAN0  wlan1=$WLAN1"

###############################################################################
# 2. Stop everything
###############################################################################
log "=== Step 2: Stopping services ==="

systemctl stop dnsmasq 2>/dev/null || true
kill -9 $(pgrep dnsmasq) 2>/dev/null || true
systemctl stop frognet-proxy frognet-daemon frognet-merge-watcher 2>/dev/null || true
systemctl stop apache2 2>/dev/null || true
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true

log "Services stopped"

###############################################################################
# 3. NetworkManager: dns=none + correct interface names
###############################################################################
log "=== Step 3: NetworkManager ==="

mkdir -p /etc/NetworkManager/conf.d

# dns=none — prevent NM from pushing to dead systemd-resolved
cat > /etc/NetworkManager/conf.d/no-resolved.conf <<'EOF'
[main]
dns=none
EOF

# Also fix the main config if it has dns= set wrong
if [[ -f /etc/NetworkManager/NetworkManager.conf ]]; then
    if ! grep -q '^dns=none' /etc/NetworkManager/NetworkManager.conf; then
        sed -i '/^\[main\]/a dns=none' /etc/NetworkManager/NetworkManager.conf 2>/dev/null || true
    fi
fi

# Fix unmanaged interface name
for f in /etc/NetworkManager/conf.d/10-frognet-unmanaged.conf /etc/NetworkManager/conf.d/99-frognet-unmanaged.conf; do
    cat > "$f" <<NMEOF
[keyfile]
unmanaged-devices=interface-name:${ETH0}
NMEOF
done

systemctl restart NetworkManager 2>/dev/null || true

# Fix 90-frognet-merge dispatcher if it has hardcoded wlan0/wlan1
MERGE_DISPATCH="/etc/NetworkManager/dispatcher.d/90-frognet-merge"
if [[ -f "$MERGE_DISPATCH" ]] && grep -q 'iface_ipv4 wlan0' "$MERGE_DISPATCH"; then
    log "Patching 90-frognet-merge dispatcher (hardcoded wlan0/wlan1)"
    sed -i '/^mkdir -p "$SENT_DIR"/a\
\
. /usr/local/bin/mapInterfaces 2>/dev/null || true' "$MERGE_DISPATCH" 2>/dev/null || true
    sed -i 's/iface_ipv4 wlan0/iface_ipv4 "${wlan0Name:-wlan0}"/g' "$MERGE_DISPATCH" 2>/dev/null || true
    sed -i 's/iface_ipv4 wlan1/iface_ipv4 "${wlan1Name:-wlan1}"/g' "$MERGE_DISPATCH" 2>/dev/null || true
fi

log "NetworkManager fixed"

###############################################################################
# 4. resolv.conf
###############################################################################
log "=== Step 4: resolv.conf ==="

rm -f /etc/resolv.conf
echo "nameserver 127.0.0.1" > /etc/resolv.conf
log "resolv.conf fixed"

###############################################################################
# 5. Interface override file
###############################################################################
log "=== Step 5: Interface overrides ==="

mkdir -p /etc/frognet
cat > /etc/frognet/interfaces_override.conf <<IFEOF
# AUTO-GENERATED by frognet_fixup.sh on $(date)
eth0Name="${ETH0}"
wlan0Name="${WLAN0}"
wlan1Name="${WLAN1}"
IFEOF

echo "$ETH0" > /etc/frognet/transit_exclude_interfaces
log "interfaces_override.conf written"

###############################################################################
# 6. Purge stale identity
###############################################################################
log "=== Step 6: Purging stale identity ==="

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
rm -f /etc/sentinels/forwarded
rm -f /etc/sentinels/new_resolv.conf
rm -f /etc/frognet/gateways.conf
rm -f /etc/database_ip
rm -f /var/lib/misc/dnsmasq.leases

log "Stale identity purged"

###############################################################################
# 7. MariaDB
###############################################################################
log "=== Step 7: MariaDB ==="

# Remove provider plugins that don't exist on this machine
for cnf in /etc/mysql/mariadb.conf.d/provider_*.cnf; do
    [[ -f "$cnf" ]] || continue
    plugin_name=$(basename "$cnf" .cnf)
    if [[ ! -f "/usr/lib/mysql/plugin/${plugin_name}.so" ]]; then
        log "Removing stale MariaDB plugin: $cnf"
        rm -f "$cnf"
    fi
done

# Ensure slow_query.log directory exists
mkdir -p /var/log/mysql
touch /var/log/mysql/slow_query.log
chown mysql:mysql /var/log/mysql /var/log/mysql/slow_query.log 2>/dev/null || true

# Check buffer pool size vs available RAM
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
CURRENT_POOL=$(grep -rh 'innodb_buffer_pool_size' /etc/mysql/ 2>/dev/null | grep -oP '\d+' | head -1)
if [[ -n "$CURRENT_POOL" && -n "$TOTAL_MB" ]]; then
    # If buffer pool (in MB) > 50% of RAM, scale it down
    POOL_LIMIT=$((TOTAL_MB / 2))
    if [[ "$CURRENT_POOL" -gt "$POOL_LIMIT" ]]; then
        log "Buffer pool ${CURRENT_POOL}M exceeds 50% of RAM (${TOTAL_MB}M), setting to ${POOL_LIMIT}M"
        for f in $(grep -rl 'innodb_buffer_pool_size' /etc/mysql/ 2>/dev/null); do
            sed -i "s/innodb_buffer_pool_size\s*=.*/innodb_buffer_pool_size = ${POOL_LIMIT}M/" "$f"
        done
        for f in $(grep -rl 'innodb_buffer_pool_size_max' /etc/mysql/ 2>/dev/null); do
            sed -i "s/innodb_buffer_pool_size_max\s*=.*/innodb_buffer_pool_size_max = ${POOL_LIMIT}M/" "$f"
        done
    fi
fi

systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || true
sleep 2

if ! systemctl is-active --quiet mariadb 2>/dev/null; then
    log "WARNING: MariaDB failed to start — check journalctl -u mariadb"
else
    log "MariaDB running"
fi

# Sync FrogUser password
FROG_PASS=""
if [[ -f /var/www/html/config.php ]]; then
    FROG_PASS=$(grep -oP "define\('DB_PASS',\s*'\\K[^']+" /var/www/html/config.php 2>/dev/null || true)
fi
if [[ -z "$FROG_PASS" ]] && [[ -f /opt/frognet_semantic/DB_CONFIG.json ]]; then
    FROG_PASS=$(python3 -c "import json; print(json.load(open('/opt/frognet_semantic/DB_CONFIG.json'))['password'])" 2>/dev/null || true)
fi

if [[ -n "$FROG_PASS" ]]; then
    log "Syncing FrogUser credentials..."
    mysql -u root <<SQLEOF || log "WARNING: FrogUser sync failed"
DROP USER IF EXISTS 'FrogUser'@'localhost';
DROP USER IF EXISTS 'FrogUser'@'%';
CREATE USER 'FrogUser'@'localhost' IDENTIFIED BY '${FROG_PASS}';
CREATE USER 'FrogUser'@'%' IDENTIFIED BY '${FROG_PASS}';
CREATE DATABASE IF NOT EXISTS FrogNet;
CREATE DATABASE IF NOT EXISTS FrogNetFamily;
GRANT ALL ON FrogNet.* TO 'FrogUser'@'localhost';
GRANT ALL ON FrogNet.* TO 'FrogUser'@'%';
GRANT ALL ON FrogNetFamily.* TO 'FrogUser'@'localhost';
GRANT ALL ON FrogNetFamily.* TO 'FrogUser'@'%';
FLUSH PRIVILEGES;
SQLEOF

    if mysql -u FrogUser -p"${FROG_PASS}" FrogNet -e "SELECT 1" >/dev/null 2>&1; then
        log "FrogUser auth OK"
    else
        log "WARNING: FrogUser auth FAILED"
    fi
else
    log "WARNING: Could not find DB password in config.php or DB_CONFIG.json"
fi

###############################################################################
# 8. Apache: PHP version + FPM
###############################################################################
log "=== Step 8: Apache/PHP ==="

INSTALLED_PHP=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || true)
if [[ -n "$INSTALLED_PHP" ]]; then
    # Disable mismatched PHP modules
    for mod in /etc/apache2/mods-enabled/php*.load; do
        [[ -f "$mod" ]] || continue
        mod_name=$(basename "$mod" .load)
        if [[ "$mod_name" != "php${INSTALLED_PHP}" ]]; then
            log "Disabling mismatched module: $mod_name (have $INSTALLED_PHP)"
            a2dismod "$mod_name" 2>/dev/null || true
        fi
    done
    a2enmod "php${INSTALLED_PHP}" 2>/dev/null || true

    # Disable mismatched FPM configs
    for fpm_conf in /etc/apache2/conf-enabled/php*-fpm.conf; do
        [[ -f "$fpm_conf" ]] || continue
        fpm_name=$(basename "$fpm_conf" .conf)
        if [[ "$fpm_name" != "php${INSTALLED_PHP}-fpm" ]]; then
            log "Disabling mismatched FPM: $fpm_name"
            a2disconf "$fpm_name" 2>/dev/null || true
        fi
    done
    log "PHP $INSTALLED_PHP configured"
else
    log "WARNING: PHP not installed"
fi

systemctl restart apache2 2>/dev/null || true

###############################################################################
# 9. Set network identity (IP on interface)
###############################################################################
log "=== Step 9: Network identity ==="

# Flush and set IP
ip addr flush dev "$ETH0" 2>/dev/null || true
ip addr add "${GATEWAY_IP}/24" dev "$ETH0" 2>/dev/null || true
ip link set "$ETH0" up

# NM connection
nmcli conn del "$NODE_NAME" 2>/dev/null || true
nmcli conn add con-name "$NODE_NAME" \
    ifname "$ETH0" \
    type ethernet \
    ip4 "${GATEWAY_IP}/24" \
    ipv4.method manual \
    ipv4.dns "$GATEWAY_IP" \
    ipv4.gateway "" \
    connection.autoconnect yes \
    connection.autoconnect-priority 999 \
    connection.autoconnect-retries 0 2>/dev/null || true
nmcli conn up "$NODE_NAME" 2>/dev/null || true

CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
log "$ETH0 IP: $CURRENT_IP"

###############################################################################
# 10. Hostname
###############################################################################
log "=== Step 10: Hostname ==="

FQDN="FrogNetHost.${NODE_NAME}"
hostnamectl set-hostname "$FQDN" 2>/dev/null || hostname "$FQDN"
echo "$FQDN" > /etc/hostname
log "Hostname: $FQDN"

###############################################################################
# 11. dnsmasq config
###############################################################################
log "=== Step 11: dnsmasq ==="

mkdir -p /etc/dnsmasq.d

cat > /etc/dnsmasq.d/opts_only.conf <<EOF
# Generated by frognet_fixup.sh on $(date)
domain=$NODE_NAME
local=/$NODE_NAME/
expand-hosts
dhcp-range=${IP_BASE}.2,${IP_BASE}.254,24h
dhcp-option=option:router,$GATEWAY_IP
dhcp-option=option:dns-server,$GATEWAY_IP
dhcp-authoritative
# [DHCP_IGNORE_NAMES_V1] Every FrogNetHost reports the hostname "FrogNetHost", so a
# DHCP client (e.g. a downstream node) always collides with this node's own /etc/hosts
# entry -> "not giving name FrogNetHost ... because the name exists in /etc/hosts".
# /etc/hosts is authoritative for FrogNet names; never register a DHCP-supplied one.
dhcp-ignore-names
EOF

# [DHCP_SERVED_IF_ONLY_V1] A FrogNetHost serves DHCP on exactly ONE interface:
# the one holding GATEWAY_IP (its .1 identity) — eth0 standalone, or wlan0/wlan1
# under hostapd. Every OTHER interface (the internet uplink with its dynamic
# lease, an unused eth0, the frognet0 tunnel) must be excluded, or dnsmasq offers
# DHCP on the uplink and logs "no address range available for <uplink>".
# [DHCP_SERVED_IF_ONLY_V3] Selector, in strict order of authority:
#   1. the interface actually carrying GATEWAY_IP
#   2. hostapd.conf interface=, ONLY if (1) is empty (address not up yet)
#   3. eth0Name from mapInterfaces, wired-mode last resort
#
# Order matters and was learned the hard way.  hostapd FIRST is wrong: on a
# wired gateway whose radio is present but NO-CARRIER, hostapd.conf names wlan0
# while the LAN is on eth0, which scopes DHCP to a dead interface and the node
# answers nobody.  GATEWAY_IP alone is also wrong: this script runs at BOOT,
# frequently before the LAN address is up, and the old V2 code then took the
# "leaving DHCP unrestricted" branch and wrote NO exclusions at all -- dnsmasq
# offering DHCP on every interface, including somebody else's LAN.  That branch
# silently undid the scoping setup_lillypad_v4 had written correctly.
#
# There is no fail-open any more.  Unscoped is worse than absent: a node that
# cannot identify its own LAN device must not serve DHCP at all.
SERVED_IF="$(ip -4 -o addr show 2>/dev/null | awk -v g="$GATEWAY_IP" '$4 ~ ("^" g "/") {print $2; exit}')"
if [ -n "$SERVED_IF" ]; then
    log "dnsmasq: LAN device $SERVED_IF resolved from GATEWAY_IP=$GATEWAY_IP"
elif [ -f /etc/hostapd/hostapd.conf ]; then
    SERVED_IF="$(awk -F= '/^interface=/{print $2; exit}' /etc/hostapd/hostapd.conf)"
    [ -n "$SERVED_IF" ] && log "dnsmasq: GATEWAY_IP not up; LAN device $SERVED_IF from hostapd.conf"
fi
if [ -z "$SERVED_IF" ]; then
    . /usr/local/bin/mapInterfaces 2>/dev/null || true
    SERVED_IF="${eth0Name:-}"
    [ -n "$SERVED_IF" ] && log "dnsmasq: falling back to wired LAN device $SERVED_IF"
fi
if [ -n "$SERVED_IF" ] && [ -d "/sys/class/net/$SERVED_IF" ]; then
    log "dnsmasq: DHCP scoped to served interface $SERVED_IF"
    # [DHCP_SERVED_IF_ONLY_V2] Exclude DHCP on EVERY interface except the served one --
    # enumerate the real link list, not just $ETH0/$WLAN0/$WLAN1/frognet0. The old set
    # missed the internet uplink (eth1 on NY-1) and anything else, so dnsmasq kept
    # offering DHCP on it: "no address range available for DHCP request via eth1".
    for _IF in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
        [ "$_IF" = "lo" ] && continue
        [ -n "$_IF" ] && [ "$_IF" != "$SERVED_IF" ] && \
            echo "no-dhcp-interface=$_IF" >> /etc/dnsmasq.d/opts_only.conf
    done
else
    log "dnsmasq: FATAL cannot identify the LAN-projecting interface"
    log "dnsmasq: (GATEWAY_IP=$GATEWAY_IP not up, no hostapd interface=, no eth0)"
    log "dnsmasq: refusing to leave DHCP unscoped - disabling DHCP on ALL interfaces"
    for _IF in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
        [ "$_IF" = "lo" ] && continue
        [ -n "$_IF" ] && echo "no-dhcp-interface=$_IF" >> /etc/dnsmasq.d/opts_only.conf
    done
fi

# Write forwarders with correct listen-address
OUR_IP="$GATEWAY_IP"
cat > /etc/dnsmasq.d/frognet_forwarders_auto.conf <<EOF
# Generated by frognet_fixup.sh
listen-address=127.0.0.1,${OUR_IP}
bind-interfaces
EOF

cat > /etc/dnsmasq.d/frognet_databasehost.conf <<EOF
address=/databasehost.frognet/${GATEWAY_IP}
EOF

systemctl restart dnsmasq 2>/dev/null || true

if systemctl is-active --quiet dnsmasq; then
    log "dnsmasq running"
else
    log "WARNING: dnsmasq failed to start"
fi

###############################################################################
# 12. FrogNet config files
###############################################################################
log "=== Step 12: FrogNet configs ==="

mkdir -p /etc/frognet /etc/sentinels

cat > /etc/frognet/gateways.conf <<EOF
NETWORK_NAME=$NODE_NAME
GATEWAY_IP=$GATEWAY_IP
GW1=$GATEWAY_IP
GW2=0.0.0.0
EOF

echo "$GATEWAY_IP" > /etc/database_ip
echo "nameserver $GATEWAY_IP" > /etc/sentinels/frog_resolv.conf

log "Config files written"

# Fix boot-time fixup script to use mapInterfaces
cat > /usr/local/bin/frognet_eth0_fixup.sh <<'FIXUPEOF'
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

# Fix systemd unit for boot fixup
cat > /etc/systemd/system/frognet-eth0-fixup.service <<UNITEOF
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
UNITEOF
systemctl daemon-reload
systemctl enable frognet-eth0-fixup 2>/dev/null || true

# /etc/hosts
grep -q "$FQDN" /etc/hosts 2>/dev/null || echo "$GATEWAY_IP $FQDN FrogNetHost" >> /etc/hosts

###############################################################################
# 13. Enable and start services
###############################################################################
log "=== Step 13: Services ==="

systemctl daemon-reload

for svc in frognet-proxy frognet-daemon frognet-merge-watcher; do
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
        systemctl enable "$svc" 2>/dev/null || true
        systemctl restart "$svc" 2>/dev/null || true
        log "  $svc: $(systemctl is-active "$svc" 2>/dev/null)"
    else
        log "  $svc: unit file missing"
    fi
done

# Optional services — enable if present
# [NAT64_REMOVED_V1] frognet-nat64 dropped 2026-08-29 - its script is not in
# the tree; see frognet_install.sh OPT_SERVICES.
# [CONNECTIVITY_UI_REMOVED_V1] see frognet_install.sh OPT_SERVICES.
for svc in frognet-eth0-fixup frognet-sysperf frognet-tunnel-daemon \
           frognet-gps \
           frognet-transit-boot frognet-transit-watch; do
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
        systemctl enable "$svc" 2>/dev/null || true
    fi
done


###############################################################################
# 14. Verify
###############################################################################
log "=== Step 14: Verification ==="

FAIL=0
ok()  { echo "  ✓  $*"; }
nok() { echo "  ✗  $*"; FAIL=$((FAIL+1)); }
chk() { local d="$1"; shift; "$@" >/dev/null 2>&1 && ok "$d" || nok "$d"; }

chk "hostname"               test "$(hostname)" = "$FQDN"
chk "interface IP"           test "$CURRENT_IP" = "$GATEWAY_IP"
chk "mariadb"                systemctl is-active --quiet mariadb
chk "apache2"                systemctl is-active --quiet apache2
chk "dnsmasq"                systemctl is-active --quiet dnsmasq
chk "frognet-proxy"          systemctl is-active --quiet frognet-proxy
chk "frognet-daemon"         systemctl is-active --quiet frognet-daemon
chk "systemd-resolved off"   test "$(systemctl is-active systemd-resolved 2>/dev/null)" != "active"
chk "interfaces_override"    test -f /etc/frognet/interfaces_override.conf
chk "gateways.conf"          grep -q "$NODE_NAME" /etc/frognet/gateways.conf

if [[ -n "${FROG_PASS:-}" ]]; then
    chk "FrogUser auth"      mysql -u FrogUser -p"${FROG_PASS}" FrogNet -e "SELECT 1"
fi

# ECHO_RESULT=$(curl --max-time 10 http://$(hostname)/frognet_echo.php) && ok "frognet_echo: $ECHO_RESULT" || nok "frognet_echo: ${ECHO_RESULT:-no response}"

# echo
# if [[ $FAIL -eq 0 ]]; then
    log "All checks passed. $NODE_NAME ($GATEWAY_IP) is ready."
# else
#   log "$FAIL check(s) failed — review output above."
# fi
