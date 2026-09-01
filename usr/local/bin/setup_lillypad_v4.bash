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
# setup_lillypad_v4.bash - FrogNet Gateway Setup (v4)
#
# Same LAN setup as v2, plus writes /etc/frognet/tunnel.conf
# so the tunnel daemon knows where the broker is.
#
# Usage:
#   sudo bash setup_lillypad_v4.bash <NetworkName> <GatewayIP> [BROKER_URL] \\
#        [--pond <PondName>]
#
#   --pond <PondName>   The pond this node JOINS - the group of nodes sharing a
#                       broker namespace. REQUIRED whenever BROKER_URL is given.
#                       It is NOT the node/domain name and has no default.
#
# Examples:
#   sudo bash setup_lillypad_v4.bash HardBox 10.101.100.1
#   sudo bash setup_lillypad_v4.bash HardBox 10.101.100.1 \
#        https://streamingfrog.com/frognet-broker-v4
#
# BROKER_URL is the full URL the tunnel daemon will hit.  Path
# prefix is required for v4 (e.g. /frognet-broker-v4).  The
# daemon appends /api/v4/... itself.
#
# If BROKER_URL is omitted, tunnel.conf is created with an empty
# BROKER_URL - broker registration is deferred until tunnel.conf
# is filled in by hand or by a later run of this script.
##############################################################

# [NO_SET_X_V1] `set -x` traced every command, and this script handles the broker
# URL and the pond credentials - a trace is where secrets leak into logs and
# terminals. Same rule frognet_reset.sh states in its header: never trace.

log() { echo "[setup_lillypad] $(date '+%H:%M:%S') $*"; }
die() { log "FATAL: $*"; exit 1; }

# [POND_IS_NOT_THE_DOMAIN_V1] POND_NAME used to default to NETWORK_NAME. A pond is a
# GROUP OF NODES that share a broker namespace; NETWORK_NAME is THIS node's domain.
# Defaulting one to the other put every node in its own single-member pond named
# after itself, so nodes joining "the same" pond never met. There is no sane default
# for a pond you are joining - it must be given, as --pond.
POND_NAME_ARG=""
_POS=()
# [SETUP_LILLYPAD_NORESTART_V1] --norestart: write all config (files + the
# persistent NM connection profile) but do NOT bounce services or re-address the
# live interface. Used when the installer calls this over SSH that rides the very
# interface being configured (a LAN-only gateway reached via its wlan0/AP link):
# restarting NetworkManager or flushing the interface drops that SSH mid-install.
# The install reboots at the end, and the NM profile is autoconnect-priority 999,
# so everything skipped here comes up cleanly on that reboot.
NO_RESTART=0
# [SETUP_LILLYPAD_NORESTART_V1] restart wrapper: a no-op under --norestart, since
# the install reboots at the end and every service comes up then. Keeps SSH alive.
svc_restart() {
    if [[ "${NO_RESTART:-0}" -eq 1 ]]; then
        log "  [--norestart] not restarting: $* (applies on reboot)"
    else
        systemctl restart "$@" 2>/dev/null || true
    fi
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --pond)   POND_NAME_ARG="${2:-}"; shift 2 ;;
        --pond=*) POND_NAME_ARG="${1#*=}"; shift ;;
        --norestart) NO_RESTART=1; shift ;;
        --)       shift; while [[ $# -gt 0 ]]; do _POS+=("$1"); shift; done ;;
        -*)       die "Unknown option: $1" ;;
        *)        _POS+=("$1"); shift ;;
    esac
done
NETWORK_NAME="${_POS[0]:-}"
GATEWAY_IP="${_POS[1]:-}"

[[ $EUID -eq 0 ]] || die "Must run as root"
[[ -n "$NETWORK_NAME" ]] || die "Usage: $0 <NetworkName> <GatewayIP>"
[[ -n "$GATEWAY_IP" ]] || die "Usage: $0 <NetworkName> <GatewayIP>"
[[ "$GATEWAY_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || die "Gateway IP must be 10.x.x.1 format"

# Optional third arg: full URL to the v4 broker. (Was incorrectly read from $4 -
# the caller passes it as $3: setup_lillypad_v4.bash <NetworkName> <GatewayIP> <URL>.
# The $4 bug silently dropped the broker URL, leaving an empty tunnel.conf/broker.conf
# so the tunnel daemon never registered.)
BROKER_URL="${_POS[2]:-}"
if [[ -n "$BROKER_URL" ]]; then
    [[ "$BROKER_URL" =~ ^https?://[^/[:space:]]+(/.*)?$ ]] || \
        die "BROKER_URL must be a full URL (e.g. https://streamingfrog.com/frognet-broker-v4)"
fi

# ============================================================
# Resolve actual interface name via mapInterfaces
# ============================================================
. /usr/local/bin/mapInterfaces
ETH0="${eth0Name:-eth0}"
log "Resolved eth0 interface: $ETH0"

# Sanity check: does the interface exist?
# Sanity check: does the interface exist?
if ip link show "$ETH0" >/dev/null 2>&1; then
    ETH0_PRESENT=true
    log "Interface $ETH0 is present"
else
    ETH0_PRESENT=false
    log "WARNING: Interface '$ETH0' does not exist."
    log "         Continuing without eth0 - dnsmasq and identity will be"
    log "         configured anyway, and frognet-eth0-fixup will claim"
    log "         the interface when it appears."
fi

# Extract subnet info
IP_BASE="${GATEWAY_IP%.*}"
SUBNET="${IP_BASE}.0/24"
ADMIN_IP="${IP_BASE}.2"
DHCP_START="${IP_BASE}.3"
DHCP_END="${IP_BASE}.254"

# ============================================================
# [LILLYPAD_RENAME_TEARDOWN_V1] Capture the PRIOR identity and the SSID that is
# actually on the air NOW, BEFORE any file below overwrites them. On a rename
# (foo -> bar) or an SSID->wired switch we use these to tear down the old
# projected network so we don't leave "foo" broadcasting for a node that no
# longer exists.
#   OLD_NAME  - previous node name (gateways.conf is overwritten in Step 10).
#   OLD_SSID  - SSID hostapd is currently broadcasting (truth of what is live;
#               frognet-netstart writes ssid=<node> into hostapd.conf).
#   PROJ_MODE - on/off from the persisted projection switch; "on" projects an
#               AP when eth0 has no carrier, "off" means wired (no hostapd).
# ============================================================
OLD_NAME=""
[[ -r /etc/frognet/gateways.conf ]] && \
    OLD_NAME="$(grep -E '^NETWORK_NAME=' /etc/frognet/gateways.conf 2>/dev/null \
                | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
OLD_SSID=""
[[ -r /etc/hostapd/hostapd.conf ]] && \
    OLD_SSID="$(grep -E '^ssid=' /etc/hostapd/hostapd.conf 2>/dev/null \
                | tail -1 | cut -d= -f2-)"
PROJ_MODE="on"
if [[ -x /usr/local/bin/frognet-ssid-projection ]]; then
    PROJ_MODE="$(/usr/local/bin/frognet-ssid-projection get 2>/dev/null || echo on)"
fi
log "Identity transition: OLD_NAME='${OLD_NAME:-none}' NEW='${NETWORK_NAME}' OLD_SSID='${OLD_SSID:-none}' projection=${PROJ_MODE}"

log "Configuring FrogNet gateway: $NETWORK_NAME"
log "  Gateway IP: $GATEWAY_IP"
log "  Admin IP:   $ADMIN_IP"
log "  Subnet: $SUBNET"
log "  DHCP range: $DHCP_START - $DHCP_END"

# ============================================================
# Step 1: Stop services that might interfere
# ============================================================
log "Step 1: Stopping interfering services..."
systemctl stop dnsmasq 2>/dev/null || true
kill -9 $(pgrep dnsmasq) 2>/dev/null || true
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true

# Prevent NetworkManager from pushing DNS to (now-dead) systemd-resolved
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/no-resolved.conf << 'NMEOF'
[main]
dns=none
NMEOF
if [[ "${NO_RESTART:-0}" -eq 1 ]]; then
    log "  [--norestart] NOT restarting NetworkManager (would drop the install SSH; applies on reboot)"
else
    systemctl restart NetworkManager 2>/dev/null || true
fi
systemctl stop frognet-transit-boot 2>/dev/null || true
systemctl stop frognet-transit-watch 2>/dev/null || true

# ============================================================
# Step 1b: Purge stale identity from cloned image
# ============================================================
log "Step 1b: Purging stale config from prior identity..."

# dnsmasq configs
rm -f /etc/dnsmasq.d/opts_only.conf
rm -f /etc/dnsmasq.d/frognet_forwarders_auto.conf
rm -f /etc/dnsmasq.d/frognet_databasehost.conf

# sentinels
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
rm -f /etc/sentinels/getFrog*

# frognet identity
rm -f /etc/frognet/gateways.conf
rm -f /etc/database_ip

# DHCP leases from prior subnet
rm -f /var/lib/misc/dnsmasq.leases

# resolv.conf - likely symlinked to systemd-resolved stub
rm -f /etc/resolv.conf
echo "nameserver 127.0.0.1" > /etc/resolv.conf

log "Step 1b: Purge complete"

# ============================================================
# Step 1c: Reconcile services for cloned image
# ============================================================
log "Step 1c: Reconciling cloned image to this machine..."

# -- MariaDB: remove provider plugins the clone had but we don't --
if ls /etc/mysql/mariadb.conf.d/provider_*.cnf >/dev/null 2>&1; then
    log "  Removing stale MariaDB provider plugin configs..."
    rm -f /etc/mysql/mariadb.conf.d/provider_*.cnf
fi

# -- MariaDB: ensure slow_query.log path exists --
mkdir -p /var/log/mysql
touch /var/log/mysql/slow_query.log
chown mysql:mysql /var/log/mysql /var/log/mysql/slow_query.log 2>/dev/null || true

# -- MariaDB: start it so we can fix FrogUser --
systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || true
sleep 2

# -- MariaDB: sync FrogUser password from config.php --
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
    # Verify
    if mysql -u FrogUser -p"${FROG_PASS}" FrogNet -e "SELECT 1" >/dev/null 2>&1; then
        log "  FrogUser password sync OK"
    else
        log "  WARNING: FrogUser still cannot authenticate"
    fi
else
    log "  WARNING: Could not find FrogUser password in config.php or DB_CONFIG.json"
fi

# -- Apache: fix PHP module version mismatch --
INSTALLED_PHP=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null)
if [[ -n "$INSTALLED_PHP" ]]; then
    # Disable any PHP modules that don't match installed version
    for mod in /etc/apache2/mods-enabled/php*.load; do
        [[ -f "$mod" ]] || continue
        mod_name=$(basename "$mod" .load)
        if [[ "$mod_name" != "php${INSTALLED_PHP}" ]]; then
            log "  Disabling mismatched Apache module: $mod_name (have PHP $INSTALLED_PHP)"
            a2dismod "$mod_name" 2>/dev/null || true
        fi
    done
    # Enable the correct one
    if [[ -f "/usr/lib/apache2/modules/libphp${INSTALLED_PHP}.so" ]]; then
        a2enmod "php${INSTALLED_PHP}" 2>/dev/null || true
        log "  Enabled Apache module: php${INSTALLED_PHP}"
    else
        log "  WARNING: libphp${INSTALLED_PHP}.so not found - install libapache2-mod-php"
    fi

    # Disable any PHP-FPM configs that don't match installed version
    for fpm_conf in /etc/apache2/conf-enabled/php*-fpm.conf; do
        [[ -f "$fpm_conf" ]] || continue
        fpm_name=$(basename "$fpm_conf" .conf)
        if [[ "$fpm_name" != "php${INSTALLED_PHP}-fpm" ]]; then
            log "  Disabling mismatched FPM config: $fpm_name (have PHP $INSTALLED_PHP)"
            a2disconf "$fpm_name" 2>/dev/null || true
        fi
    done
else
    log "  WARNING: PHP not installed"
fi

svc_restart apache2
svc_restart frognet-proxy frognet-daemon

log "Step 1c: Reconciliation complete"

# ============================================================
# Step 2: Aggressively clean up NetworkManager connections
# ============================================================
log "Step 2: Cleaning up NetworkManager connections..."

# Delete ALL ethernet connections bound to $ETH0 - match by interface-name AND
# active device, not just active device. [ETH0_REAP_BY_IFNAME_V1] In `nmcli -t`
# an INACTIVE connection's DEVICE field is empty (not "--"), so the old
# ":${ETH0}$|:--$" device grep silently missed inactive profiles pinned to eth0
# by connection.interface-name. That left stale duplicates (e.g. a prior
# "Seattle5" at 10.250.250 alongside a new "SeattleFive" at 10.251.251, both
# autoconnect-priority 999 on eth0) racing for eth0 each boot -> the node's
# served /24 flapped between identities. Matching interface-name reaps them all.
for uuid in $(nmcli -t -f UUID,TYPE,connection.interface-name,DEVICE c show 2>/dev/null \
              | awk -F: -v e="$ETH0" '$2=="ethernet" && ($3==e || $4==e){print $1}'); do
    log "  Deleting eth0-bound connection UUID: $uuid"
    nmcli conn del "$uuid" 2>/dev/null || true
done

# Delete ALL connections named $NETWORK_NAME (loop until none remain)
while nmcli conn show "$NETWORK_NAME" >/dev/null 2>&1; do
    log "  Deleting connection named: $NETWORK_NAME"
    nmcli conn del "$NETWORK_NAME" 2>/dev/null || true
    sleep 0.5
done

# ============================================================
# Step 3: Ensure $ETH0 is managed and clean
# ============================================================
log "Step 3: Preparing $ETH0..."

# Make sure NetworkManager manages $ETH0
nmcli dev set "$ETH0" managed yes 2>/dev/null || true

if [[ "${NO_RESTART:-0}" -eq 1 ]]; then
    # [SETUP_LILLYPAD_NORESTART_V1] Do NOT flush/re-address the live interface.
    # On a LAN-only gateway the operator's SSH rides this very device; flushing it
    # disconnects the install exactly as an NM restart would. The persistent NM
    # connection is still created below (Step 5) and comes up on the reboot, so the
    # address is applied then. If the interface already carries the gateway IP
    # (a --preserve upgrade) nothing is lost; if it does not, it is set on reboot.
    log "  [--norestart] NOT flushing/re-addressing $ETH0 (would drop the install SSH; NM profile applies on reboot)"
    ip link set "$ETH0" up 2>/dev/null || true
    CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | head -1)
    log "  Current $ETH0 IP: ${CURRENT_IP:-<unchanged>}"
else
    # Flush all IPs from $ETH0
    ip addr flush dev "$ETH0" 2>/dev/null || true

    # Bring $ETH0 up
    ip link set "$ETH0" up

    # Wait for device to be ready
    sleep 1

    # ============================================================
    # Step 4: Set IP directly first (immediate effect)
    # ============================================================
    log "Step 4: Setting IP directly on $ETH0..."
    ip addr add "${GATEWAY_IP}/24" dev "$ETH0" 2>/dev/null || true
    ip addr add "${ADMIN_IP}/24" dev "$ETH0" 2>/dev/null || true
    ip route add "$SUBNET" dev "$ETH0" 2>/dev/null || true

    # Verify immediate result
    CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | head -1)
    log "  Current $ETH0 IP: $CURRENT_IP"
fi

# ============================================================
# Step 5: Create NetworkManager connection for persistence
# ============================================================
log "Step 5: Creating NetworkManager connection..."

nmcli conn add con-name "$NETWORK_NAME" \
    ifname "$ETH0" \
    type ethernet \
    ip4 "${GATEWAY_IP}/24,${ADMIN_IP}/24" \
    ipv4.method manual \
    ipv4.dns "$GATEWAY_IP" \
    ipv4.gateway "" \
    connection.autoconnect yes \
    connection.autoconnect-priority 999 \
    connection.autoconnect-retries 0

# ============================================================
# Step 6: Activate the connection
# ============================================================
if [[ "${NO_RESTART:-0}" -eq 1 ]]; then
    # [SETUP_LILLYPAD_NORESTART_V1] The persistent NM connection is created above
    # (Step 5, autoconnect-priority 999). Do NOT activate it live here: nmcli
    # conn up / dev disconnect / a forced ip flush all cut an SSH riding on $ETH0.
    # It activates on the reboot instead.
    log "Step 6/7: [--norestart] connection profile saved; NOT activated live (applies on reboot)"
    CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
    log "  $ETH0 primary IP: ${CURRENT_IP:-<applies on reboot>}"
else
    log "Step 6: Activating connection..."

    # Try multiple activation methods
    if ! nmcli conn up "$NETWORK_NAME" 2>/dev/null; then
        log "  Direct activation failed, trying with ifname..."
        nmcli conn up "$NETWORK_NAME" ifname "$ETH0" 2>/dev/null || true
    fi

    # If still not active, disconnect and reconnect the device
    if ! nmcli -t -f DEVICE c show --active 2>/dev/null | grep -q "^${ETH0}\$"; then
        log "  Reconnecting $ETH0..."
        nmcli dev disconnect "$ETH0" 2>/dev/null || true
        sleep 1
        nmcli dev connect "$ETH0" 2>/dev/null || true
    fi

    # ============================================================
    # Step 7: Verify and fix if needed
    # ============================================================
    log "Step 7: Verifying configuration..."

    have_gw=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$GATEWAY_IP" || true)
    have_admin=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$ADMIN_IP" || true)

    if [[ -z "$have_gw" || -z "$have_admin" ]]; then
        log "  WARNING: address set incomplete (gw=$have_gw admin=$have_admin want gw=$GATEWAY_IP admin=$ADMIN_IP)"
        log "  Forcing IP assignment..."
        ip addr flush dev "$ETH0"
        ip addr add "${GATEWAY_IP}/24" dev "$ETH0"
        ip addr add "${ADMIN_IP}/24" dev "$ETH0"
        ip link set "$ETH0" up
    fi

    CURRENT_IP=$(ip -4 -o addr show dev "$ETH0" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
    log "  $ETH0 primary IP: $CURRENT_IP"
    log "  $ETH0 addresses:"
    ip -4 -o addr show dev "$ETH0" | awk '{print "    "$4}'
fi

# ============================================================
# Step 8: Configure hostname
# ============================================================
log "Step 8: Setting hostname..."
FQDN="FrogNetHost.${NETWORK_NAME}"
# [SETUP_LILLYPAD_NORESTART_V1] hostnamectl emits a D-Bus signal NM reacts to,
# which can drop the install SSH; use the plain syscall under --norestart (the
# installer's E2 has already set the hostname anyway).
echo "$FQDN" > /etc/hostname
if [[ "${NO_RESTART:-0}" -eq 1 ]]; then
    hostname "$FQDN" 2>/dev/null || true
else
    hostnamectl set-hostname "$FQDN" 2>/dev/null || hostname "$FQDN"
fi

# Update /etc/hosts
if ! grep -q "$FQDN" /etc/hosts 2>/dev/null; then
    echo "$GATEWAY_IP $FQDN FrogNetHost" >> /etc/hosts
fi

# ============================================================
# Step 9: Configure dnsmasq
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

# [DHCP_SERVED_IF_ONLY_V3] DHCP is served on exactly ONE interface: the device
# PROJECTING THE LAN.  Every other interface is excluded.
#
# Selector, in strict order of authority:
#
#   1. The interface actually carrying GATEWAY_IP.  This is the node's identity
#      and Steps 4-7 above have just placed it, so it is live by the time this
#      block runs.  It is the ONLY signal that reflects where the LAN really is.
#   2. hostapd.conf interface=, ONLY if (1) came back empty -- i.e. the address
#      is not up yet.
#   3. eth0Name from mapInterfaces, wired mode last resort.
#
# Order matters and was learned the hard way.  Keying on hostapd FIRST is wrong:
# on a wired gateway whose radio is present but NO-CARRIER, hostapd.conf names
# wlan0 while the LAN is on eth0 -- that scopes DHCP to a dead interface and the
# node answers nobody.  Keying on GATEWAY_IP alone is also wrong: it resolves to
# empty whenever the address is not up yet, and the old V2 code then wrote NO
# exclusions at all, leaving dnsmasq offering DHCP on every interface including
# somebody else's LAN segment.  Each source covers the other's blind spot.
#
# Nothing here depends on upstreams or Internet reachability -- a node with no
# uplink at all still serves its own LAN and must still scope DHCP to it.
#
# This MUST be written HERE and not left to frognet_fixup.sh: this script
# rm -f's opts_only.conf and rewrites it, so every enrolment / pond change /
# reinstall silently drops the exclusions until fixup happens to run again.
SERVED_IF="$(ip -4 -o addr show 2>/dev/null | awk -v g="$GATEWAY_IP" '$4 ~ ("^" g "/") {print $2; exit}')"
if [[ -n "$SERVED_IF" ]]; then
    log "  dnsmasq: LAN device $SERVED_IF resolved from GATEWAY_IP=$GATEWAY_IP"
elif [[ -f /etc/hostapd/hostapd.conf ]]; then
    SERVED_IF="$(awk -F= '/^interface=/{print $2; exit}' /etc/hostapd/hostapd.conf)"
    [[ -n "$SERVED_IF" ]] && log "  dnsmasq: GATEWAY_IP not up; LAN device $SERVED_IF from hostapd.conf"
fi
if [[ -z "$SERVED_IF" ]]; then
    . /usr/local/bin/mapInterfaces 2>/dev/null || true
    SERVED_IF="${eth0Name:-}"
    [[ -n "$SERVED_IF" ]] && log "  dnsmasq: falling back to wired LAN device $SERVED_IF"
fi
if [[ -z "$SERVED_IF" || ! -d "/sys/class/net/$SERVED_IF" ]]; then
    log "FATAL: cannot identify the LAN-projecting interface (hostapd.conf"
    log "       interface=, or eth0).  Refusing to write an unscoped DHCP"
    log "       config -- dnsmasq would answer DHCP on every interface."
    exit 1
fi
log "  dnsmasq: DHCP scoped to $SERVED_IF (LAN-projecting device)"
for _IF in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
    [[ "$_IF" == "lo" || "$_IF" == "$SERVED_IF" ]] && continue
    echo "no-dhcp-interface=$_IF" >> /etc/dnsmasq.d/opts_only.conf
done

if [[ "${NO_RESTART:-0}" -eq 1 ]]; then log "  [--norestart] not restarting dnsmasq (applies on reboot)"; else systemctl restart dnsmasq 2>/dev/null || service dnsmasq restart 2>/dev/null || true; fi

# ============================================================
# Step 9b: [LILLYPAD_RENAME_TEARDOWN_V1] Reconcile the projected SSID with the
# (possibly new) identity. frognet-netstart derives the broadcast SSID from the
# dnsmasq `domain=` directive, so:
#   - strip stale `domain=` from every OTHER dnsmasq conf, leaving opts_only.conf
#     (just written with the new name) the single source - otherwise get_node_name
#     can latch an old name and keep projecting it;
#   - on a rename, drop the OLD-name AP and the OLD NM wifi profile, then re-apply
#     projection so hostapd comes up with the NEW SSID;
#   - on a switch to wired (projection off), shut hostapd down so the old AP
#     stops broadcasting. ("If we switch from SSID to LAN mode, shut down hostapd.")
# ============================================================
log "Step 9b: Reconciling projected SSID (mode=${PROJ_MODE}, old='${OLD_NAME:-none}')..."

# Single source of truth for the node name: only opts_only.conf carries domain=.
for f in /etc/dnsmasq.d/*.conf; do
    [[ -e "$f" ]] || continue
    [[ "$f" == /etc/dnsmasq.d/opts_only.conf ]] && continue
    if grep -qE '^[[:space:]]*domain=' "$f" 2>/dev/null; then
        log "  Stripping stale domain= from $f"
        sed -i -E '/^[[:space:]]*domain=/d' "$f"
    fi
done

# Delete a stale NM wifi profile left from the old identity (by connection id or
# by SSID). Scoped to the OLD name/SSID so the current uplink is never touched.
_del_wifi_profile() {
    local want="$1"
    [[ -n "$want" ]] || return 0
    local id
    while IFS=: read -r id ctype; do
        [[ "$ctype" == "802-11-wireless" || "$ctype" == "wifi" ]] || continue
        local ssid
        ssid="$(nmcli -t -g 802-11-wireless.ssid connection show "$id" 2>/dev/null)"
        if [[ "$id" == "$want" || "$ssid" == "$want" ]]; then
            log "  Deleting stale wifi profile id='$id' ssid='${ssid:-?}' (old=$want)"
            nmcli conn del "$id" 2>/dev/null || true
        fi
    done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null)
}

if [[ "$PROJ_MODE" == "off" ]]; then
    # Wired mode: drop the projected AP entirely. frognet-ssid-projection off
    # runs netstart to drop the AP cleanly, then stops+masks hostapd.
    if [[ -x /usr/local/bin/frognet-ssid-projection ]]; then
        log "  Projection OFF -> dropping AP via frognet-ssid-projection off"
        /usr/local/bin/frognet-ssid-projection off >/dev/null 2>&1 || true
    else
        systemctl stop hostapd 2>/dev/null || true
        systemctl mask hostapd 2>/dev/null || true
    fi
    _del_wifi_profile "$OLD_NAME"
    _del_wifi_profile "$OLD_SSID"
elif [[ -n "$OLD_NAME" && "$OLD_NAME" != "$NETWORK_NAME" ]]; then
    # Rename while projecting: stop the old-SSID AP, remove the old profile,
    # then re-apply projection so hostapd rebuilds with the new node name.
    log "  Rename '$OLD_NAME' -> '$NETWORK_NAME' while projecting: cycling AP"
    systemctl stop hostapd 2>/dev/null || true
    _del_wifi_profile "$OLD_NAME"
    [[ "$OLD_SSID" != "$NETWORK_NAME" ]] && _del_wifi_profile "$OLD_SSID"
    if [[ -x /usr/local/bin/frognet-ssid-projection ]]; then
        /usr/local/bin/frognet-ssid-projection on >/dev/null 2>&1 || true
    fi
fi

# ============================================================
# Step 10: Create FrogNet config files
# ============================================================
log "Step 10: Creating config files..."
mkdir -p /etc/frognet /etc/sentinels

cat > /etc/frognet/gateways.conf << EOF
# FrogNet gateway configuration
# Generated by setup_lillypad.bash on $(date)
NETWORK_NAME=$NETWORK_NAME
GATEWAY_IP=$GATEWAY_IP
ADMIN_IP=$ADMIN_IP
GW1=$GATEWAY_IP
GW2=0.0.0.0
EOF

echo "$GATEWAY_IP" > /etc/database_ip
echo "nameserver $GATEWAY_IP" > /etc/sentinels/frog_resolv.conf

# ============================================================
# Step 11: Create boot-time fixup service
# ============================================================
log "Step 11: Creating boot-time fixup service..."

cat > /usr/local/bin/frognet_eth0_fixup.sh << 'FIXUPEOF'
#!/bin/bash
# Ensure FrogNet interface has the correct gateway + admin IPs at boot.
# Runs BEFORE transit boot to claim the interface.

CONF="/etc/frognet/gateways.conf"
[[ -f "$CONF" ]] || exit 0

source "$CONF"
[[ -n "$GATEWAY_IP" ]] || exit 0

# Derive admin alias from gateway IP:  10.x.y.1 -> 10.x.y.2
ADMIN_IP="${GATEWAY_IP%.*}.2"

# Resolve actual interface name
. /usr/local/bin/mapInterfaces
DEV="${eth0Name:-eth0}"

# Check if interface already has BOTH correct IPs
have_gw=$(ip -4 -o addr show dev "$DEV" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$GATEWAY_IP" || true)
have_admin=$(ip -4 -o addr show dev "$DEV" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$ADMIN_IP" || true)
[[ -n "$have_gw" && -n "$have_admin" ]] && exit 0

# Force the correct IPs
ip addr flush dev "$DEV" 2>/dev/null || true
ip addr add "${GATEWAY_IP}/24" dev "$DEV"
ip addr add "${ADMIN_IP}/24" dev "$DEV"
ip link set "$DEV" up

# Try to activate NM connection
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
# Step 12: Update transit boot to skip eth0
# ============================================================
log "Step 12: Configuring transit to skip $ETH0..."

# Create exclusion file
echo "$ETH0" > /etc/frognet/transit_exclude_interfaces

# ============================================================
# Step 13: Restart services
# ============================================================
log "Step 13: Restarting services..."
systemctl restart mariadb 2>/dev/null || systemctl restart mysql 2>/dev/null || true
svc_restart apache2
svc_restart dnsmasq
svc_restart frognet-proxy
svc_restart frognet-daemon
systemctl start frognet-transit-boot 2>/dev/null || true
systemctl start frognet-transit-watch 2>/dev/null || true

# ============================================================
# Final verification
# ============================================================
log ""
log "============================================"
# ============================================================
# Step N: Write /etc/frognet/tunnel.conf for the tunnel daemon
# ============================================================
log "Step N: Writing /etc/frognet/tunnel.conf..."

mkdir -p /etc/frognet

if [[ -f /etc/frognet/tunnel.conf ]]; then
    cp -p /etc/frognet/tunnel.conf "/etc/frognet/tunnel.conf.bak.$(date +%Y%m%d-%H%M%S)"
    if grep -q '^BROKER_URL=' /etc/frognet/tunnel.conf; then
        sed -i "s|^BROKER_URL=.*|BROKER_URL=${BROKER_URL}|" /etc/frognet/tunnel.conf
    else
        echo "BROKER_URL=${BROKER_URL}" >> /etc/frognet/tunnel.conf
    fi
    log "  Updated existing /etc/frognet/tunnel.conf (BROKER_URL set, other fields preserved)"
else
    cat > /etc/frognet/tunnel.conf <<TCONF
BROKER_URL=${BROKER_URL}
PASSCODE=
GROUP_TOKEN=
GROUP_NAME=
MAX_TUNNELS=5000
TCONF
    chmod 600 /etc/frognet/tunnel.conf
    log "  Wrote new /etc/frognet/tunnel.conf"
fi

if [[ -n "$BROKER_URL" ]]; then
    log "  BROKER_URL=$BROKER_URL"
else
    log "  BROKER_URL=<empty> - fill in /etc/frognet/tunnel.conf before starting tunnel daemon"
fi

# ============================================================
# Step N2: Write /etc/frognet/broker.conf - THIS is the file
# frognet-tunnel-setup-v3.sh reads to register with the broker. v4 previously wrote
# only tunnel.conf (read by the tunnel DAEMON), so tunnel-setup found no broker config
# and never registered -> no tunnels. v3 wrote broker.conf; restore that.
# ============================================================
if [[ -n "$BROKER_URL" ]]; then
    # A broker means this node JOINS A POND. Without the pond name we would be
    # guessing which group it belongs to - refuse rather than invent one.
    [[ -n "$POND_NAME_ARG" ]] || \
        die "BROKER_URL given but no POND_NAME (4th arg) - a pond name is required to join a pond"
    # [ONE_CONF_V1] One config file. This `cat >` was safe only while
    # broker.conf held two keys nobody else owned; aimed at the merged file it
    # would truncate GROUP_TOKEN, PASSCODE, MAX_TUNNELS and NODE_GUID -- the
    # node's membership card. Set keys individually instead.
    . "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
    fn_conf_backup >/dev/null
    fn_conf_set BROKER_URL "$BROKER_URL"
    fn_pond_set "$POND_NAME_ARG"
    log "  Wrote $FROGNET_CONF (BROKER_URL + pond=${POND_NAME_ARG}) for tunnel-setup-v3"
else
    log "  No BROKER_URL - broker fields NOT written; tunnel-setup stays offline until configured"
fi

log "Setup complete!"
log ""
log "$ETH0 configuration:"
ip addr show "$ETH0" | grep -E "inet |state"
log ""
log "getEth0Address returns:"
/usr/local/bin/getEth0Address 2>/dev/null || echo "$GATEWAY_IP"
log ""
log "Service health:"
for svc in mariadb apache2 dnsmasq frognet-proxy frognet-daemon; do
    status=$(systemctl is-active "$svc" 2>/dev/null || echo "not found")
    log "  $svc: $status"
done
log ""
log "FrogUser auth:"
# [NO_HARDCODED_CREDENTIAL_V1] This defaulted to a live pond password when
# FROG_PASS was unset. A health check is not a place to keep a credential, and a
# default that happens to work hides the fact that nothing supplied one. Read it
# from the file that owns it, and say so plainly when it is not there.
_FROG_PASS="${FROG_PASS:-}"
if [ -z "$_FROG_PASS" ] && [ -r /var/www/html/config.php ]; then
    _FROG_PASS="$(sed -nE "s/.*define\('DB_PASS', *'([^']*)'.*/\1/p" /var/www/html/config.php | head -1)"
fi
if [ -z "$_FROG_PASS" ] || [ "$_FROG_PASS" = "__FROGNET_DB_PASS__" ]; then
    log "  SKIPPED - no DB password available (set FROG_PASS, or check that phase C1b injected /var/www/html/config.php)"
elif mysql -u FrogUser -p"$_FROG_PASS" FrogNet -e "SELECT 1" >/dev/null 2>&1; then
    log "  OK"
else
    log "  FAILED - check credentials"
fi
log ""
log "frognet_echo:"
ECHO_RESULT=$(curl -fsS --max-time 5 http://127.0.0.1/frognet_echo.php 2>&1) || true
log "  ${ECHO_RESULT:-FAILED}"
log "============================================"
