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
# /usr/local/bin/frognet_system_tune.sh
# FrogNet system auto-tuning — discovers hardware capabilities and configures
# MariaDB, Apache, kernel, and systemd for optimal FrogNet performance.
#
# Called once during setup_lillypad.bash, safe to re-run at any time.
# All decisions logged.  Nothing masked.  No silent failures.
#
# Usage: frognet_system_tune.sh [--dry-run]
#
# 2026-03-23  John Fawcett / FrogNet Living Network

set -eu

DRYRUN=0
[[ "${1:-}" == "--dry-run" ]] && DRYRUN=1

log() { echo "[frognet_system_tune][$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
warn() { log "WARNING: $*"; }
die() { log "FATAL: $*"; exit 1; }

apply_file() {
    local path="$1" content="$2"
    if [[ $DRYRUN -eq 1 ]]; then
        log "DRY-RUN: would write $path"
        echo "$content"
        echo "---"
        return
    fi
    local dir
    dir="$(dirname "$path")"
    mkdir -p "$dir"
    echo "$content" > "$path"
    log "Wrote $path"
}

# ─────────────────────────────────────────────────────────────────
# DISCOVER HARDWARE
# ─────────────────────────────────────────────────────────────────

log "Discovering hardware..."

TOTAL_RAM_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
CPU_CORES=$(nproc)

# Classify: 2GB / 4GB / 8GB+
if [[ $TOTAL_RAM_MB -lt 2500 ]]; then
    RAM_TIER="2G"
elif [[ $TOTAL_RAM_MB -lt 5000 ]]; then
    RAM_TIER="4G"
else
    RAM_TIER="8G"
fi

# Detect SD card vs USB/SSD root
ROOT_DEV=$(findmnt -n -o SOURCE / 2>/dev/null | head -1)
IS_SD=0
if echo "$ROOT_DEV" | grep -q 'mmcblk'; then
    IS_SD=1
fi

log "RAM: ${TOTAL_RAM_MB}MB (tier=$RAM_TIER) CPUs: $CPU_CORES SD_CARD: $IS_SD"

# ─────────────────────────────────────────────────────────────────
# COMPUTE TUNING PARAMETERS
# ─────────────────────────────────────────────────────────────────

case "$RAM_TIER" in
    2G)
        # Reserve ~800MB for OS + proxy + daemon + tunnel daemon
        MYSQL_MAX_CONN=150
        MYSQL_THREAD_CACHE=20
        MYSQL_INNODB_BP="384M"
        MYSQL_INNODB_LOG="64M"
        MYSQL_TMP_TABLE="16M"
        APACHE_START=2
        APACHE_MIN_SPARE=2
        APACHE_MAX_SPARE=5
        APACHE_MAX_WORKERS=64
        APACHE_MAX_CHILD=500
        SYSCTL_CONNTRACK=32768
        TMPFS_TMP_SIZE="128m"
        TMPFS_LOG=1     # aggressive — logs on tmpfs to save RAM
        FD_LIMIT=32768
        ;;
    4G)
        # Reserve ~1GB for OS + proxy + daemon + tunnel daemon
        MYSQL_MAX_CONN=300
        MYSQL_THREAD_CACHE=30
        MYSQL_INNODB_BP="1G"
        MYSQL_INNODB_LOG="128M"
        MYSQL_TMP_TABLE="32M"
        APACHE_START=3
        APACHE_MIN_SPARE=3
        APACHE_MAX_SPARE=10
        APACHE_MAX_WORKERS=128
        APACHE_MAX_CHILD=1000
        SYSCTL_CONNTRACK=65536
        TMPFS_TMP_SIZE="256m"
        TMPFS_LOG=0
        FD_LIMIT=65536
        ;;
    8G)
        # Reserve ~1GB for OS + proxy + daemon + tunnel daemon
        MYSQL_MAX_CONN=500
        MYSQL_THREAD_CACHE=50
        MYSQL_INNODB_BP="2G"
        MYSQL_INNODB_LOG="256M"
        MYSQL_TMP_TABLE="64M"
        APACHE_START=5
        APACHE_MIN_SPARE=5
        APACHE_MAX_SPARE=20
        APACHE_MAX_WORKERS=256
        APACHE_MAX_CHILD=1000
        SYSCTL_CONNTRACK=65536
        TMPFS_TMP_SIZE="256m"
        TMPFS_LOG=0
        FD_LIMIT=65536
        ;;
esac

# ─────────────────────────────────────────────────────────────────
# DAEMON WORKER POOLS — derived from the Apache/MySQL sizing above so the
# daemon never dispatches more concurrent work than the backend can serve.
# Without this the daemon falls back to its compiled defaults (256 fast,
# cpu*4 db) regardless of tier, which oversubscribes Apache on every tier
# below 8G and produces the 10s loopback timeouts / 502 storms.
#   fast pool : echo/discovery still hit Apache, so cap at its worker count.
#   db  pool  : leave MySQL connections for Apache's own PHP workers (up to
#               APACHE_MAX_WORKERS) plus a 16-conn admin/headroom reserve,
#               so the daemon can never exhaust max_connections.
# Consumed by the daemon as FROGNET_DAEMON_POOL_SIZE /
# FROGNET_DAEMON_DB_POOL_SIZE (session.py); injected via the systemd
# override below.  Tune the reserve here if you want a different split.
# ─────────────────────────────────────────────────────────────────
DAEMON_POOL_SIZE=$APACHE_MAX_WORKERS
DAEMON_DB_POOL_SIZE=$(( MYSQL_MAX_CONN - APACHE_MAX_WORKERS - 16 ))
[[ $DAEMON_DB_POOL_SIZE -lt 8 ]] && DAEMON_DB_POOL_SIZE=8

log "Tuning: MySQL max_conn=$MYSQL_MAX_CONN innodb_bp=$MYSQL_INNODB_BP Apache max_workers=$APACHE_MAX_WORKERS daemon_pool=$DAEMON_POOL_SIZE daemon_db_pool=$DAEMON_DB_POOL_SIZE"

# ─────────────────────────────────────────────────────────────────
# MARIADB
# ─────────────────────────────────────────────────────────────────

MYSQL_CONF="/etc/mysql/mariadb.conf.d/99-frognet-tune.cnf"

apply_file "$MYSQL_CONF" "[mysqld]
# FrogNet auto-tuned for ${TOTAL_RAM_MB}MB RAM, tier=$RAM_TIER
# Any node may become databasehost.frognet — all get full settings
# Generated by frognet_system_tune.sh on $(date)

# --- Connections ---
max_connections         = $MYSQL_MAX_CONN
thread_cache_size       = $MYSQL_THREAD_CACHE
wait_timeout            = 300
interactive_timeout     = 300

# --- InnoDB ---
innodb_buffer_pool_size = $MYSQL_INNODB_BP
innodb_log_file_size    = $MYSQL_INNODB_LOG
innodb_flush_log_at_trx_commit = 2
innodb_flush_method     = O_DIRECT

# --- Query cache (off — FrogNet writes are frequent) ---
query_cache_type        = 0
query_cache_size        = 0

# --- Temp tables ---
tmp_table_size          = $MYSQL_TMP_TABLE
max_heap_table_size     = $MYSQL_TMP_TABLE
tmpdir                  = /tmp

# --- Logging (general log OFF for production — kills SD cards) ---
general_log             = 0
slow_query_log          = 1
slow_query_log_file     = /var/log/mysql/slow_query.log
long_query_time         = 2

# --- Network ---
max_allowed_packet      = 16M
"

# Disable the general_log if it's enabled in the stock config
STOCK_CONF="/etc/mysql/mariadb.conf.d/50-server.cnf"
if [[ -f "$STOCK_CONF" ]] && grep -q '^general_log' "$STOCK_CONF"; then
    if [[ $DRYRUN -eq 0 ]]; then
        sed -i 's/^general_log\b/#general_log/' "$STOCK_CONF"
        log "Disabled general_log in $STOCK_CONF"
    else
        log "DRY-RUN: would disable general_log in $STOCK_CONF"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# APACHE
# ─────────────────────────────────────────────────────────────────

APACHE_CONF="/etc/apache2/mods-available/mpm_prefork.conf"

apply_file "$APACHE_CONF" "<IfModule mpm_prefork_module>
    # FrogNet auto-tuned for ${TOTAL_RAM_MB}MB RAM, tier=$RAM_TIER
    # Generated by frognet_system_tune.sh on $(date)
    StartServers          $APACHE_START
    MinSpareServers       $APACHE_MIN_SPARE
    MaxSpareServers       $APACHE_MAX_SPARE
    MaxRequestWorkers     $APACHE_MAX_WORKERS
    MaxConnectionsPerChild $APACHE_MAX_CHILD
</IfModule>
"

# ─────────────────────────────────────────────────────────────────
# KERNEL (sysctl)
# ─────────────────────────────────────────────────────────────────

SYSCTL_CONF="/etc/sysctl.d/99-frognet.conf"
# NOTE: ip_forward, rp_filter, and forwarding settings live in
# /etc/sysctl.d/90-frognet-forwarding.conf — DO NOT add them here.
# This file is regenerated by this script and would overwrite them.

apply_file "$SYSCTL_CONF" "# FrogNet auto-tuned for ${TOTAL_RAM_MB}MB RAM, tier=$RAM_TIER
# Generated by frognet_system_tune.sh on $(date)

# --- Connection tracking ---
net.netfilter.nf_conntrack_max = $SYSCTL_CONNTRACK
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_established = 300

# --- TCP tuning for many concurrent proxy connections ---
net.core.somaxconn = 1024
net.core.netdev_max_backlog = 2000
net.ipv4.tcp_max_syn_backlog = 1024
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1

# --- Outbound port range (proxy opens many connections) ---
net.ipv4.ip_local_port_range = 10000 60999

# --- TCP keepalive (detect dead WG tunnel connections faster) ---
net.ipv4.tcp_keepalive_time = 60
net.ipv4.tcp_keepalive_intvl = 10
net.ipv4.tcp_keepalive_probes = 5

# --- File descriptors ---
fs.file-max = 100000

# --- WireGuard buffer tuning ---
net.core.rmem_max = 2621440
net.core.wmem_max = 2621440
net.core.rmem_default = 262144
net.core.wmem_default = 262144
"

if [[ $DRYRUN -eq 0 ]]; then
    sysctl -p "$SYSCTL_CONF" || warn "sysctl apply had errors (conntrack module may not be loaded yet)"
    # Always re-apply forwarding settings (separate file, never overwritten)
    [[ -f /etc/sysctl.d/90-frognet-forwarding.conf ]] && sysctl -p /etc/sysctl.d/90-frognet-forwarding.conf || true
    log "Applied sysctl settings"
fi

# ─────────────────────────────────────────────────────────────────
# FILE DESCRIPTOR LIMITS
# ─────────────────────────────────────────────────────────────────

LIMITS_CONF="/etc/security/limits.d/99-frognet.conf"

apply_file "$LIMITS_CONF" "# FrogNet auto-tuned
root     soft nofile $FD_LIMIT
root     hard nofile $FD_LIMIT
froguser soft nofile $FD_LIMIT
froguser hard nofile $FD_LIMIT
*        soft nofile $FD_LIMIT
*        hard nofile $FD_LIMIT
"

# ─────────────────────────────────────────────────────────────────
# SYSTEMD SERVICE OVERRIDES
# ─────────────────────────────────────────────────────────────────

for SVC in frognet-proxy frognet-daemon frognet-tunnel-daemon; do
    OVERRIDE_DIR="/etc/systemd/system/${SVC}.service.d"
    OVERRIDE_FILE="${OVERRIDE_DIR}/99-frognet-tune.conf"
    # Only the daemon carries the worker-pool env; proxy/tunnel-daemon get
    # the FD/proc limits alone.
    DAEMON_ENV=""
    if [[ "$SVC" == "frognet-daemon" ]]; then
        DAEMON_ENV="Environment=FROGNET_DAEMON_POOL_SIZE=$DAEMON_POOL_SIZE
Environment=FROGNET_DAEMON_DB_POOL_SIZE=$DAEMON_DB_POOL_SIZE
"
    fi
    apply_file "$OVERRIDE_FILE" "[Service]
# FrogNet auto-tuned
LimitNOFILE=$FD_LIMIT
LimitNPROC=4096
${DAEMON_ENV}"
done

if [[ $DRYRUN -eq 0 ]]; then
    systemctl daemon-reload
    log "Reloaded systemd"
fi

# ─────────────────────────────────────────────────────────────────
# TMPFS MOUNTS
# ─────────────────────────────────────────────────────────────────

FSTAB="/etc/fstab"

# /tmp on tmpfs
if ! grep -q 'tmpfs.*/tmp.*tmpfs' "$FSTAB" 2>/dev/null; then
    if [[ $DRYRUN -eq 0 ]]; then
        echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,size=$TMPFS_TMP_SIZE 0 0" >> "$FSTAB"
        log "Added /tmp tmpfs to fstab (size=$TMPFS_TMP_SIZE)"
    else
        log "DRY-RUN: would add /tmp tmpfs (size=$TMPFS_TMP_SIZE)"
    fi
fi

# /var/log on tmpfs for 2GB Pis (aggressive — logs lost on reboot)
if [[ $TMPFS_LOG -eq 1 ]]; then
    if ! grep -q 'tmpfs.*/var/log.*tmpfs' "$FSTAB" 2>/dev/null; then
        if [[ $DRYRUN -eq 0 ]]; then
            echo "tmpfs /var/log tmpfs defaults,noatime,nosuid,size=64m 0 0" >> "$FSTAB"
            log "Added /var/log tmpfs to fstab (2GB tier — saves RAM and SD wear)"
        else
            log "DRY-RUN: would add /var/log tmpfs"
        fi
    fi
fi

# Add noatime to root filesystem if not already present
if grep -qE '^\S+\s+/\s' "$FSTAB"; then
    if ! grep -E '^\S+\s+/\s' "$FSTAB" | grep -q 'noatime'; then
        if [[ $DRYRUN -eq 0 ]]; then
            sed -i -E '/^\S+\s+\/\s/ s/defaults/defaults,noatime/' "$FSTAB"
            log "Added noatime to root filesystem"
        else
            log "DRY-RUN: would add noatime to root filesystem"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────

log "════════════════════════════════════════════════════════"
log "FrogNet System Tune Complete"
log "  Hardware:  ${TOTAL_RAM_MB}MB RAM, ${CPU_CORES} cores, tier=$RAM_TIER"
log "  SD Card:   $IS_SD"
log "  MariaDB:   max_conn=$MYSQL_MAX_CONN innodb_bp=$MYSQL_INNODB_BP log_flush=2"
log "  Apache:    max_workers=$APACHE_MAX_WORKERS max_child=$APACHE_MAX_CHILD"
log "  Daemon:    pool=$DAEMON_POOL_SIZE db_pool=$DAEMON_DB_POOL_SIZE (env via systemd override)"
log "  Kernel:    conntrack=$SYSCTL_CONNTRACK fd=$FD_LIMIT"
log "  tmpfs:     /tmp=${TMPFS_TMP_SIZE} /var/log=$(if [[ $TMPFS_LOG -eq 1 ]]; then echo '64m'; else echo 'no'; fi)"
log "════════════════════════════════════════════════════════"

if [[ $DRYRUN -eq 1 ]]; then
    log "DRY-RUN mode — no changes applied.  Run without --dry-run to apply."
else
    log "Changes applied.  Restart services to take effect:"
    log "  systemctl restart mariadb apache2 frognet-proxy frognet-daemon frognet-tunnel-daemon"
fi
