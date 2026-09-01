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
# frognet_reset.sh - FrogNet System Cleanup / Full Reset
#
# Wipes a machine's FrogNet install and state so a fresh RC/release can be
# installed cleanly. Removes what an install OVERWRITES (so stale, differently-
# named files can't survive) plus identity, runtime state, and the databases.
#
# What it does NOT do: it never nukes shared OS directories wholesale
# (/etc/apache2, /etc/mysql, /etc/NetworkManager, /etc/ssl, /etc/sysctl.d) -
# only the FrogNet-owned files inside them. FrogNet-OWNED directories
# (/opt/frognet_semantic, /etc/frognet, /etc/frognet_bundles, ...) are removed
# whole.
#
# Run it from OUTSIDE /usr/local/bin (e.g. /root or /tmp): this script removes
# /usr/local/bin, and running it from there would delete it mid-run.
#
# Usage:
#   sudo bash frognet_reset.sh [--dry-run] [--keep-db] [--keep-identity] [--yes]
#
#   --dry-run        Print every action, change nothing.
#   --keep-db        Do NOT drop the FrogNet / FrogNetFamily databases + FrogUser.
#   --keep-identity  Keep /etc/fnid (node GUID) and /etc/wireguard (WG keys).
#                    Use this to REINSTALL the same node; omit for a true clone-
#                    safe wipe (fresh GUID on next install).
#   --yes, -y        Skip the confirmation prompt.
#
# No `set -x` (never trace, in case creds are ever passed) and no `set -e`
# (a reset must keep going even when a path is already absent).
###############################################################################
set -uo pipefail

DRY=0; KEEP_DB=0; KEEP_ID=0; ASSUME_YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)     DRY=1 ;;
        --keep-db)        KEEP_DB=1 ;;
        --keep-identity)  KEEP_ID=1 ;;
        --yes|-y)         ASSUME_YES=1 ;;
        -h|--help)        sed -n '2,33p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; sed -n '30,33p' "$0"; exit 1 ;;
    esac
    shift
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: must run as root" >&2; exit 1; }

LOG="/var/tmp/frognet_reset.log"   # /var/log/frognet is one of the things we remove
exec > >(tee -a "$LOG") 2>&1
phase() { echo; echo "------------------------------------------------------"; echo "  $*"; echo "------------------------------------------------------"; }
log()   { echo "$(date '+%H:%M:%S') [reset] $*"; }

# _do: run a command, or just print it under --dry-run.
_do() {
    if [[ $DRY -eq 1 ]]; then echo "  DRY  $*"; else echo "  ->   $*"; "$@"; fi
}
# nuke: remove fixed paths (dirs or files); skip absent ones silently.
nuke() {
    local p
    for p in "$@"; do
        [[ -e "$p" || -L "$p" ]] || continue
        _do rm -rf -- "$p"
    done
}
# nuke_glob <dir> <name-pattern>...: remove matching entries in ONE dir only.
nuke_glob() {
    local dir="$1"; shift
    [[ -d "$dir" ]] || return 0
    local pat f
    for pat in "$@"; do
        while IFS= read -r -d '' f; do _do rm -rf -- "$f"; done \
            < <(find "$dir" -maxdepth 1 \( -name "$pat" \) -print0 2>/dev/null)
    done
}

echo "FrogNet reset on $(hostname) - $(date)"
echo "  dry-run=$DRY  keep-db=$KEEP_DB  keep-identity=$KEEP_ID"
if [[ $DRY -eq 0 && $ASSUME_YES -eq 0 ]]; then
    echo
    echo "  This DESTROYS the FrogNet install, config, node identity$([[ $KEEP_ID -eq 1 ]] && echo ' (KEPT)'), and databases$([[ $KEEP_DB -eq 1 ]] && echo ' (KEPT)')."
    read -rp "  Type RESET to proceed: " ans
    [[ "$ans" == "RESET" ]] || { echo "Aborted."; exit 0; }
fi

###############################################################################
phase "1. Stop + disable FrogNet services"
###############################################################################
# Stop every loaded frognet unit (services, timers, paths), then disable so no
# enable-symlink lingers. mariadb is intentionally LEFT UP for the DB drop below.
if command -v systemctl >/dev/null 2>&1; then
    for glob in 'frognet-*.service' 'frognet-*.timer' 'frognet-*.path'; do
        # shellcheck disable=SC2046
        units=$(systemctl list-units --all --plain --no-legend "$glob" 2>/dev/null | awk '{print $1}')
        for u in $units; do _do systemctl stop "$u"; done
    done
    ufiles=$(systemctl list-unit-files --no-legend 'frognet-*' 2>/dev/null | awk '{print $1}')
    for u in $ufiles; do _do systemctl disable "$u"; done
    # [TUNNEL_DAEMON_IS_V3_V1] The live tunnel daemon is frognet-tunnel-daemon-v3.
    # Name it explicitly rather than relying on the frognet-* globs above: a unit that
    # is masked, not-loaded, or listed under a different state can slip through
    # list-units, and leaving the tunnel daemon running through a reset means it keeps
    # writing state and holding wg interfaces while everything under it is deleted.
    _do systemctl stop frognet-tunnel-daemon-v3
    _do systemctl disable frognet-tunnel-daemon-v3
    # The v2 unit (frognet-tunnel-daemon, no suffix) is RETIRED. Only touch it if the
    # unit file is actually present, so this is not a call against nothing.
    if [[ -f /etc/systemd/system/frognet-tunnel-daemon.service ]]; then
        _do systemctl stop frognet-tunnel-daemon
        _do systemctl disable frognet-tunnel-daemon
    fi
else
    log "systemctl not present - skipping service stop"
fi

###############################################################################
phase "2. Drop FrogNet databases"
###############################################################################
if [[ $KEEP_DB -eq 1 ]]; then
    log "keep-db set - leaving FrogNet / FrogNetFamily / FrogUser in place"
else
    if command -v mysql >/dev/null 2>&1; then
        [[ $DRY -eq 1 ]] || systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || true
        # [DB_DROP_PLAIN_V1] Plain DROP, nothing clever. A previous revision wrapped
        # this in `SET GLOBAL innodb_adaptive_hash_index=OFF` to avoid the AHI purge
        # cost per table - but disabling AHI itself has to latch the whole buffer pool
        # and tear down every AHI entry before it returns, so with a 1024M pool it
        # stalls BEFORE the drop even begins. Run by hand the same DROP completes
        # fine, because by hand nobody touches AHI. The tuning was the hang, so it is
        # gone. If the drop is slow it is slow honestly, and the timing line says so.
        _t0=$(date +%s)
        _do mysql -u root -e "DROP DATABASE IF EXISTS FrogNet; DROP DATABASE IF EXISTS FrogNetFamily; DROP USER IF EXISTS 'FrogUser'@'localhost'; DROP USER IF EXISTS 'FrogUser'@'%'; FLUSH PRIVILEGES;"
        [[ $DRY -eq 1 ]] || log "databases dropped in $(( $(date +%s) - _t0 ))s"
    else
        log "mysql client not present - skipping DB drop"
    fi
fi

###############################################################################
phase "3. Remove FrogNet-owned directories (whole)"
###############################################################################
nuke /opt/frognet_semantic
nuke /usr/local/bin /usr/local/sbin
nuke /usr/local/lib/frognet_trace.sh /usr/local/lib/frognet_log.sh
nuke /etc/frognet_bundles
nuke /etc/frognet
nuke /etc/sentinels
nuke /var/lib/frognet-tunnel
nuke /run/frognet
nuke /var/log/frognet
nuke /etc/setup_iptables /etc/database_ip
nuke /var/lib/misc/dnsmasq.leases

###############################################################################
phase "4. Node identity"
###############################################################################
if [[ $KEEP_ID -eq 1 ]]; then
    log "keep-identity set - keeping /etc/fnid (GUID) and /etc/wireguard (WG keys)"
else
    nuke /etc/fnid            # the node GUID - lives OUTSIDE /etc/frognet
    nuke /etc/wireguard       # WireGuard private keys = identity
fi

###############################################################################
phase "5. systemd unit files + dangling enable-symlinks"
###############################################################################
nuke_glob /etc/systemd/system 'frognet-*.service' 'frognet-*.timer' 'frognet-*.path' 'frognet-*.mount'
nuke /etc/systemd/system/dnsmasq.service.d/override.conf
# dangling *.wants/frognet-* symlinks left behind by disable
if [[ -d /etc/systemd/system ]]; then
    while IFS= read -r -d '' l; do _do rm -f -- "$l"; done \
        < <(find /etc/systemd/system -type l -path '*/*.wants/frognet-*' -print0 2>/dev/null)
fi
_do systemctl daemon-reload

###############################################################################
phase "6. FrogNet files inside SHARED OS directories (files only, not the dirs)"
###############################################################################
nuke_glob /etc/dnsmasq.d '*frognet*' 'opts_only.conf' 'forward_to_unbound.conf' 'upstream_fallback.conf'
nuke_glob /etc/NetworkManager/conf.d '*frognet*' 'no-resolved.conf' '99-wifi-powersave.conf'
nuke_glob /etc/NetworkManager/dispatcher.d '90-frognet-merge' '99-ifup'
nuke_glob /etc/apache2/sites-available '000-default.conf' 'admin-site.conf' 'databasehost.conf' 'frognet-ssl.conf'
nuke_glob /etc/apache2/sites-enabled '000-default.conf' 'admin-site.conf' 'databasehost.conf' 'frognet-ssl.conf'
nuke_glob /etc/sysctl.d '*frognet*'
nuke_glob /etc/mysql/mariadb.conf.d '99-frognet.cnf' 'provider_*.cnf'
nuke /etc/ssl/frognet-universal.crt /etc/ssl/frognet-universal.key /etc/ssl/localCA
nuke /etc/sudoers.d/frognet-setup
nuke /etc/iptables/rules.v4
nuke_glob /etc/logrotate.d 'frognet'
nuke /etc/ifplugd/action.d/frognet
nuke /etc/hostapd/hostapd.conf.template
nuke /etc/systemd/journald.conf.d/frognet.conf

###############################################################################
phase "7. Webroot"
###############################################################################
nuke /var/www/html /var/www/www_admin

###############################################################################
phase "8. Restore sane DNS (FrogNet had pointed resolv.conf at 127.0.0.1)"
###############################################################################
if [[ $DRY -eq 0 ]]; then
    rm -f /etc/resolv.conf
    printf 'nameserver 8.8.8.8\nnameserver 8.8.4.4\n' > /etc/resolv.conf
    log "wrote public DNS to /etc/resolv.conf so the box resolves before reinstall"
else
    echo "  DRY  reset /etc/resolv.conf -> 8.8.8.8 / 8.8.4.4"
fi

echo
echo "======================================================"
if [[ $DRY -eq 1 ]]; then
    echo "  DRY RUN complete - nothing was changed. Log: $LOG"
else
    echo "  FrogNet reset complete. Log: $LOG"
    echo "  Recommend a reboot before installing the RC."
fi
echo "======================================================"
