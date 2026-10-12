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
# /usr/local/bin, /usr/local/sbin and /usr/local/lib are SHARED with the machine:
# only the files the installer recorded laying down (/var/lib/frognet-install/files)
# are removed from them, nothing else there. The machine's name resolution, hostname
# and the services the install enabled are put back from what the installer recorded
# before it changed them (/var/lib/frognet-install/os-before). A region membership
# is left (fdf-leave). Run it from OUTSIDE /usr/local/bin (e.g. /root or /tmp): it
# removes itself from /usr/local/bin/installer.
#
# Usage:
#   sudo bash frognet_reset.sh [--dry-run] [--keep-db] [--keep-identity] [--yes]
#   sudo bash frognet_reset.sh --uninstall [--keep-db] [--keep-identity] [--yes]
#
#   --uninstall      Everything above, and also what only an install adds: the
#                    crontab lines, froguser (if the install made it), /opt/fdf
#                    (unless this machine holds a region: hub.env), and the install
#                    record itself. The apt packages stay installed.
#   --dry-run        Print every action, change nothing.
#   --keep-db        Do NOT drop the FrogNet database + FrogUser.
#   --keep-identity  Keep /etc/fnid (node GUID), /etc/wireguard and /var/lib/frognet-tunnel/node_*.key (WG key).
#                    Use this to REINSTALL the same node; omit for a true clone-
#                    safe wipe (fresh GUID on next install).
#   --yes, -y        Skip the confirmation prompt.
#
# No `set -x` (never trace, in case creds are ever passed) and no `set -e`
# (a reset must keep going even when a path is already absent).
###############################################################################
set -uo pipefail
# [UMASK_IS_OURS_V1 - T4a] what this lays down under /etc, /opt and /usr must not take its modes from the caller's
# umask (a shell with umask 0000 made /opt/fdf and its bin/ world-writable, in the T4a container).
umask 022

DRY=0; KEEP_DB=0; KEEP_ID=0; ASSUME_YES=0; UNINSTALL=0
RESET_BUILD="frognet-reset-2026-10-12-t4a"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)     DRY=1 ;;
        --keep-db)        KEEP_DB=1 ;;
        --keep-identity)  KEEP_ID=1 ;;
        --yes|-y)         ASSUME_YES=1 ;;
        --uninstall)      UNINSTALL=1 ;;
        -h|--help)        sed -n '/^# frognet_reset.sh - /,/^# (a reset must/p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1 (see --help)" >&2; exit 1 ;;
    esac
    shift
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: must run as root" >&2; exit 1; }

LOG="/var/tmp/frognet_reset.log"   # /var/log/frognet is one of the things we remove
exec > >(tee -a "$LOG") 2>&1
phase() { echo; echo "------------------------------------------------------"; echo "  $*"; echo "------------------------------------------------------"; }
log()   { echo "$(date '+%H:%M:%S') [reset] $*"; }

# _do: run a command, or just print it under --dry-run. A failure is logged and
# recorded (FAILED), and the reset goes on; the exit status is 1 if anything failed.
_do() {
    if [[ $DRY -eq 1 ]]; then echo "  DRY  $*"; return 0; fi
    echo "  ->   $*"
    "$@" || { echo "  FAILED ($?): $*"; FAILED+=("$*"); return 1; }
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

echo "FrogNet $( ((UNINSTALL)) && echo uninstall || echo reset ) (build ${RESET_BUILD}) on $(hostname) - $(date)"
echo "  dry-run=$DRY  keep-db=$KEEP_DB  keep-identity=$KEEP_ID  uninstall=$UNINSTALL"
REC=/var/lib/frognet-install
FAILED=()      # each step that failed, named; the exit status says so
if [[ $DRY -eq 0 && $ASSUME_YES -eq 0 ]]; then
    echo
    echo "  This DESTROYS the FrogNet install, config, node identity$([[ $KEEP_ID -eq 1 ]] && echo ' (KEPT)'), and databases$([[ $KEEP_DB -eq 1 ]] && echo ' (KEPT)')."
    read -rp "  Type RESET to proceed: " ans
    [[ "$ans" == "RESET" ]] || { echo "Aborted."; exit 0; }
fi

###############################################################################
phase "0. Leave the region (if this node is in one)"
###############################################################################
# [RESET_LEAVES_THE_REGION_V1 - T4a] A region membership is FrogNet state: left in place,
# /opt/fdf/var/join.env would put a reinstalled "LAN-only" node straight back into region
# mode. fdf-leave is the one thing that undoes a join.
if [[ -x /opt/fdf/bin/fdf-leave && ( -e /opt/fdf/var/join.env || -e /etc/systemd/system/fdf-client.service ) ]]; then
    _do /opt/fdf/bin/fdf-leave
else
    log "not in a region (no /opt/fdf/var/join.env, no fdf-client.service)"
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
    if systemctl cat frognet-tunnel-daemon-v3 >/dev/null 2>&1; then
        _do systemctl stop frognet-tunnel-daemon-v3
        _do systemctl disable frognet-tunnel-daemon-v3
    fi
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
    log "keep-db set - leaving FrogNet / FrogUser in place"
else
    if command -v mysql >/dev/null 2>&1; then
        _do systemctl start mariadb    # (one unit: FrogNet installs MariaDB; it was "start mariadb || start mysql || true")
        # [DB_DROP_PLAIN_V1] Plain DROP, nothing clever. A previous revision wrapped
        # this in `SET GLOBAL innodb_adaptive_hash_index=OFF` to avoid the AHI purge
        # cost per table - but disabling AHI itself has to latch the whole buffer pool
        # and tear down every AHI entry before it returns, so with a 1024M pool it
        # stalls BEFORE the drop even begins. Run by hand the same DROP completes
        # fine, because by hand nobody touches AHI. The tuning was the hang, so it is
        # gone. If the drop is slow it is slow honestly, and the timing line says so.
        _t0=$(date +%s)
        _do mysql -u root -e "DROP DATABASE IF EXISTS FrogNet; DROP USER IF EXISTS 'FrogUser'@'localhost'; DROP USER IF EXISTS 'FrogUser'@'%'; FLUSH PRIVILEGES;"
        [[ $DRY -eq 1 ]] || log "databases dropped in $(( $(date +%s) - _t0 ))s"
    else
        log "mysql client not present - skipping DB drop"
    fi
fi

###############################################################################
phase "3. Remove FrogNet-owned directories (whole)"
###############################################################################
nuke /opt/frognet_semantic
# [INSTALL_RECORD_V1 - T4a] /usr/local/bin, /usr/local/sbin and /usr/local/lib are the
# machine's, shared with whatever else is installed there: remove FrogNet's files from
# them, by the installer's record, and the directories that are then empty and were
# FrogNet's (four levels down or deeper: /usr/local/bin/installer, .../frognet_monitor_py,
# /usr/local/lib/frognet). It used to remove /usr/local/bin and /usr/local/sbin WHOLE.
if [[ -f "$REC/files" ]]; then
    _n=0
    while IFS= read -r f; do
        case "$f" in usr/local/bin/*|usr/local/sbin/*|usr/local/lib/*|usr/local/share/*) ;; *) continue ;; esac
        [[ -e "/$f" || -L "/$f" ]] || continue
        _do rm -f -- "/$f" && _n=$((_n+1))
    done < "$REC/files"
    while IFS= read -r d; do
        while [[ "$d" == */*/*/* ]]; do
            [[ -d "/$d" ]] && [[ -z "$(ls -A "/$d" 2>/dev/null)" ]] || break
            _do rmdir -- "/$d"; d="${d%/*}"
        done
    done < <(sed -n 's|/[^/]*$||p' "$REC/files" | grep -E '^usr/local/(bin|sbin|lib|share)/' | sort -u | sort -r)
    log "removed $_n recorded FrogNet file(s) from /usr/local/{bin,sbin,lib,share}"
    nuke "$REC/files"
else
    log "NO INSTALL RECORD ($REC/files): this install predates T4a, so FrogNet's files in /usr/local/bin, /usr/local/sbin and /usr/local/lib are not known file by file. They are LEFT (the next install writes over them and removes the retired ones, C1c). Nothing else in those directories is touched."
fi
nuke /etc/frognet_bundles
nuke /etc/frognet
nuke /etc/sentinels
# [KEY_IS_IDENTITY_V1 - 2026-10-12] the tunnel daemon's WireGuard key lives here (node_private.key /
# node_public.key; the daemon makes it, T3a). With --keep-identity it stays, so a reinstalled node announces the
# same key; everything else in the directory is daemon state and goes.
if [[ $KEEP_ID -eq 1 && -d /var/lib/frognet-tunnel ]]; then
    for p in /var/lib/frognet-tunnel/* /var/lib/frognet-tunnel/.[!.]*; do
        [[ -e "$p" ]] || continue
        case "${p##*/}" in node_private.key|node_public.key) continue ;; esac
        nuke "$p"
    done
else
    nuke /var/lib/frognet-tunnel
fi
nuke /run/frognet
nuke /var/log/frognet
nuke /etc/setup_iptables /etc/database_ip
nuke /var/lib/misc/dnsmasq.leases

###############################################################################
phase "4. Node identity"
###############################################################################
if [[ $KEEP_ID -eq 1 ]]; then
    log "keep-identity set - keeping /etc/fnid (GUID), /etc/wireguard and the daemon's key in /var/lib/frognet-tunnel"
else
    nuke /etc/fnid            # the node GUID - lives OUTSIDE /etc/frognet
    nuke /etc/wireguard       # WireGuard private keys = identity
fi

###############################################################################
phase "5. systemd unit files + dangling enable-symlinks"
###############################################################################
nuke_glob /etc/systemd/system 'frognet-*.service' 'frognet-*.timer' 'frognet-*.path' 'frognet-*.mount' 'frognet-*.target' 'frognet-*.service.d'
nuke /etc/systemd/system/dnsmasq.service.d/override.conf
# dangling *.wants/frognet-* symlinks left behind by disable
if [[ -d /etc/systemd/system ]]; then
    while IFS= read -r -d '' l; do _do rm -f -- "$l"; done \
        < <(find /etc/systemd/system -type l -path '*/*.wants/frognet-*' -print0 2>/dev/null)
fi
_do systemctl daemon-reload
# a unit that failed before its file went is still listed (not-found, failed) until this
_do systemctl reset-failed

###############################################################################
phase "6. FrogNet files inside SHARED OS directories (files only, not the dirs)"
###############################################################################
nuke_glob /etc/dnsmasq.d '*frognet*' 'opts_only.conf' 'forward_to_unbound.conf' 'upstream_fallback.conf'
nuke_glob /etc/NetworkManager/conf.d '*frognet*' 'no-resolved.conf' '99-wifi-powersave.conf'
nuke_glob /etc/NetworkManager/dispatcher.d '90-frognet-merge' '99-ifup'
nuke_glob /etc/apache2/sites-available '000-default.conf' 'admin-site.conf' 'databasehost.conf' 'frognet-ssl.conf'
nuke_glob /etc/apache2/sites-enabled '000-default.conf' 'admin-site.conf' 'databasehost.conf' 'frognet-ssl.conf'
nuke_glob /etc/sysctl.d '*frognet*'
nuke_glob /etc/mysql/mariadb.conf.d '99-frognet*.cnf' 'provider_*.cnf'
nuke_glob /etc/ssl 'frognet-universal.*'
nuke /etc/ssl/localCA
nuke /etc/security/limits.d/99-frognet.conf
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
phase "8. Put back the machine's own name resolution, hostname and services"
###############################################################################
# [OS_BEFORE_FROGNET_V1 - T4a] The installer recorded these before it changed them, the
# first time FrogNet was installed here (/var/lib/frognet-install/os-before). Put back
# exactly that. This used to write 8.8.8.8 / 8.8.4.4 into resolv.conf - somebody else's
# resolver, on a machine that may have no route to it, and not the way it was found.
OSB="$REC/os-before"
if [[ -d "$OSB" ]]; then
    if [[ -e "$OSB/resolv.conf" || -L "$OSB/resolv.conf" ]]; then
        _do rm -f /etc/resolv.conf && _do cp -P "$OSB/resolv.conf" /etc/resolv.conf
    fi
    [[ -f "$OSB/NetworkManager.conf" ]] && _do cp -p "$OSB/NetworkManager.conf" /etc/NetworkManager/NetworkManager.conf
    # the packet-filter rules as they were (a resolver can be behind one: a container's 127.0.0.11, ufw)
    # iptables-save lists only the tables that were in use, and iptables-restore replaces
    # only the tables it is given: a table the machine had empty (filter, often) would keep
    # FrogNet's rules. Every table absent from the record is given as empty, so the whole
    # ruleset is the machine's again.
    _whole_ruleset() {   # $1 = record file -> the record plus an empty section for each table it lacks
        cat "$1"
        grep -q '^\*filter' "$1" || printf '*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
        grep -q '^\*nat'    "$1" || printf '*nat\n:PREROUTING ACCEPT [0:0]\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\n:POSTROUTING ACCEPT [0:0]\nCOMMIT\n'
        grep -q '^\*mangle' "$1" || printf '*mangle\n:PREROUTING ACCEPT [0:0]\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\n:POSTROUTING ACCEPT [0:0]\nCOMMIT\n'
        grep -q '^\*raw'    "$1" || printf '*raw\n:PREROUTING ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n'
    }
    for _v in 4 6; do
        _f="$OSB/iptables.v$_v"; [[ -f "$_f" ]] || continue
        _r=iptables-restore; [[ $_v == 6 ]] && _r=ip6tables-restore
        if [[ $DRY -eq 1 ]]; then echo "  DRY  $_r < (the record $_f + empty sections for the tables it lacks)"
        elif _whole_ruleset "$_f" | $_r; then log "packet-filter rules (IPv$_v): the machine's again (from $_f)"
        else FAILED+=("$_r from $_f"); log "packet-filter rules (IPv$_v): $_r FAILED"; fi
    done
    if (( UNINSTALL )); then
        for f in hostname hosts fstab; do [[ -f "$OSB/$f" ]] && _do cp -p "$OSB/$f" "/etc/$f"; done
        [[ -f "$OSB/hostname" && $DRY -eq 0 ]] && hostname "$(cat "$OSB/hostname")" 2>/dev/null
    fi
    # The units the install enables, starts or masks, back to their state before FrogNet:
    # on a reset (before a reinstall) only systemd-resolved, which name resolution needs;
    # on --uninstall all of them. NetworkManager is never stopped here: it may be what
    # carries this machine's connection (and the operator's SSH); its FrogNet conf.d
    # files are gone and NetworkManager.conf is the machine's again.
    _units=(systemd-resolved)
    (( UNINSTALL )) && _units+=(dnsmasq apache2 mariadb hostapd ifplugd unbound netfilter-persistent)
    for u in "${_units[@]}"; do
        st="$OSB/$u.enabled"; [[ -f "$st" ]] || continue
        was="$(cat "$st")"; act="$(cat "$OSB/$u.active" 2>/dev/null)"
        # a mask the install made goes first (D2 masks systemd-resolved even where it is not installed)
        [[ "$(systemctl is-enabled "$u" 2>/dev/null)" == masked && "$was" != masked* ]] && _do systemctl unmask "$u"
        if ! systemctl cat "$u" >/dev/null 2>&1; then log "$u: not on this machine (was: ${was:-not installed}) - nothing more to put back"; continue; fi
        case "$was" in
            masked*)                                     _do systemctl mask --now "$u" ;;
            enabled*)                                    _do systemctl enable "$u" ;;
            static|alias|indirect|generated|linked*)     : ;;
            *)                                           _do systemctl disable "$u" ;;
        esac
        if [[ "$was" != masked* ]]; then
            if [[ "$act" == active ]]; then _do systemctl restart "$u"; else _do systemctl stop "$u"; fi
        fi
        log "$u: put back to ${was:-not installed}/${act:-inactive}"
    done
    log "name resolution: /etc/resolv.conf is the machine's own again ($(readlink /etc/resolv.conf 2>/dev/null || head -c 60 /etc/resolv.conf | tr '\n' ' '))"
else
    log "NO RECORD of this machine's name resolution before FrogNet ($OSB; the install predates T4a). /etc/resolv.conf is left as it is (FrogNet's: nameserver 127.0.0.1, served by dnsmasq). Set it to this machine's resolver by hand; nothing here guesses one."
    # before a reinstall (the installer's A0) this is not a failure: the install writes it again
    (( UNINSTALL )) && FAILED+=("name resolution not restored: no record of it before FrogNet")
fi

###############################################################################
if (( UNINSTALL )); then
phase "9. Uninstall: what only an install adds"
###############################################################################
    # the crontab lines F3 writes
    if crontab -l >/dev/null 2>&1; then
        _keep="$(crontab -l | grep -v 'startFrog\|registerFrogNetHost\|runMerge\|runBoth\|frognet-netstart' || true)"
        if [[ $DRY -eq 1 ]]; then echo "  DRY  remove FrogNet crontab lines"
        elif [[ -z "$_keep" ]]; then _do crontab -r && log "crontab: only FrogNet lines were there - removed"
        else printf '%s\n' "$_keep" | crontab - && log "crontab: FrogNet lines removed" || FAILED+=("crontab"); fi
    fi
    # froguser, only if the install made it (F4 records that)
    if [[ -f "$REC/froguser-created" ]] && id froguser >/dev/null 2>&1; then _do userdel -r froguser; fi
    # /opt/fdf: the node's FD&F (the .run's), unless this machine holds a region
    if [[ -f /opt/fdf/hub/hub.env ]]; then
        log "/opt/fdf kept: this machine holds a region (/opt/fdf/hub/hub.env, fdf-region-create); remove it with the region"
    else
        for u in $(systemctl list-unit-files --no-legend 'fdf-*' 2>/dev/null | awk '{print $1}'); do _do systemctl disable --now "$u"; done
        nuke_glob /etc/systemd/system 'fdf-*.service'
        nuke /opt/fdf
        _do systemctl daemon-reload
    fi
    nuke /etc/systemd/system/dnsmasq.service.d /etc/sudoers.d/frognet-setup /etc/frognet /var/lib/frognet
    nuke "$REC"
fi

echo
echo "======================================================"
if [[ $DRY -eq 1 ]]; then
    echo "  DRY RUN complete - nothing was changed. Log: $LOG"
else
    echo "  FrogNet $( ((UNINSTALL)) && echo uninstall || echo reset ) complete. Log: $LOG"
    if (( ${#FAILED[@]} )); then echo "  ${#FAILED[@]} step(s) FAILED:"; printf '    %s\n' "${FAILED[@]}"; fi
    (( UNINSTALL )) || echo "  Recommend a reboot before installing the RC."
fi
echo "======================================================"
(( ${#FAILED[@]} == 0 ))
