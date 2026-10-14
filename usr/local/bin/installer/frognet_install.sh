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
# frognet_install.sh - FrogNet node installer (LAN-only or region; SSID projection)
#
# Handles fresh installs AND unclean/cloned boxes. All configuration
# happens AFTER the world tar is extracted, so nothing gets overwritten.
#
# Two modes. Without --region the node is LAN-only. With --region it is also a
# member of an FD&F region: at the end (after phase F, before the reboot) the
# installer runs the FD&F node installer for this machine's architecture from
# the payload, opt/fdf/dist/fdf-install-$(uname -m).run, with the site key, the
# RAM manager's address and the area (the tunnel daemon then makes its own
# WireGuard key). A region is made with fdf-region-create on the machine that
# holds it.
#
# Usage:
#   sudo bash frognet_install.sh                    # interactive, from a release
#   sudo bash frognet_install.sh --from-repo        # from a repository checkout
#   sudo bash frognet_install.sh \
#        --name <NODE> \
#        --ip   <10.X.Y.1> \
#        [--lan-iface <IFACE>]                      # the interface this node serves
#        [--region <RAM_HOST:PORT> --site-key <KEY> --area <NAME>]
#        [--no-ssid-projection]                     # disable AP fallback
#   sudo bash frognet_install.sh --uninstall [--keep-identity] [--keep-db]
#
# Args (all optional; missing values fall through to interactive prompt):
#   --name <NODE>            Node name. Letters, numbers, _, -.
#   --ip   <10.X.Y.1>        Gateway IP. Must end in .1.
#   --lan-iface <IFACE>      The wired interface this node SERVES (its 10.X.Y.1/24,
#                            dnsmasq, DHCP). Omitted -> the first wired interface
#                            that is not the uplink. The uplink (the interface the
#                            default route uses) is never accepted: serving it
#                            would delete its connection profile and cut the
#                            machine off (and an SSH session with it). With no
#                            other wired interface the install stops and says so.
#   --region <HOST:PORT>     The region's RAM manager, an IPv4 address and port as
#                            this node reaches it (e.g. 192.0.2.10:51888). Omitted:
#                            the node is LAN-only.
#   --site-key <KEY>         The region's site key (fdf-region-create printed it).
#                            Required with --region; omitted -> prompted (hidden).
#   --area <NAME>            The region's area name (fdf-region-create's AREA).
#                            Required with --region; omitted -> prompted.
#   --uninstall              Remove FrogNet from this machine: everything the reset
#                            removes (frognet_reset.sh), and also the region
#                            membership and /opt/fdf, the crontab lines, froguser,
#                            and this installer. Puts back the name resolution the
#                            install replaced. Packages installed by apt stay.
#   --keep-identity          With --uninstall: keep /etc/fnid and the tunnel
#                            daemon's WireGuard key.
#   --keep-db                With --uninstall: keep the FrogNet database.
#   --no-reboot              Do everything, then stop instead of rebooting (the
#                            node starts its services on the next boot).
#   --db-pass <PASS>         FrogUser DB password. SHARED pond-wide constant:
#                            every node must use the SAME value. Omit to be
#                            prompted (hidden, entered twice). A value on the
#                            command line is visible in `ps` to local users
#                            while the installer runs; prefer the prompt.
#   --wifi-psk <PSK>         WPA2 passphrase for the projected SSID (8-63 chars).
#                            Pond-shared: every node in a pond uses the SAME one.
#                            Omitted -> taken from an existing /etc/frognet/wifi.env,
#                            else prompted for. Never defaulted.
#   --rebuild-venv           Force a full venv rebuild. Default is to CARRY the
#                            existing venv across a reset - it is arch-specific
#                            build output, not state, and rebuilding it costs
#                            many minutes on ARM for no gain.
#   --from-repo              Install from a REPOSITORY CHECKOUT instead of a
#                            release tarball. Run it from the repo root, where
#                            etc/ opt/ usr/ var/ are. What is copied to / is the
#                            node file set (frognet_node_files in
#                            usr/local/lib/frognet_world_manifest.sh, the same
#                            rule the release's world tar is built by); the rest
#                            of the checkout (.git, docs/, site/, opt/fdf, the
#                            book) stays behind. This is the path a stranger who just
#                            cloned the repo takes to a first node: no release
#                            tarball exists yet, and the only thing that builds
#                            one snapshots a node that is already running.
#   --preserve               UPGRADE IN PLACE. Writes the new files over the existing
#                            install, removes what the new tree retired (C1c),
#                            migrates the schema if it changed, and keeps the
#                            existing databases, identity (/etc/fnid, name, IP),
#                            the tunnel daemon's WireGuard key and config.
#                            DEFAULT (no flag) on a machine that already has a
#                            FrogNet install is a FULL RESET: frognet_reset.sh
#                            wipes install, state, identity and databases first,
#                            so the box comes up exactly like a clean machine.
#   --no-ssid-projection     Disable WiFi AP fallback when eth0 has no link.
#                            Default: ON - when eth0 has no cable and the device
#                            has a wlan0, hostapd projects the node SSID.
#   --ssid-projection        Force the WiFi AP fallback on (the default; given
#                            explicitly, E7 does not ask).
#   --country <CC>           WiFi regulatory country (two letters, e.g. US).
#                            Omitted -> asked in E1 (default US).
#   --ap-iface <IFACE>       The wireless interface hostapd projects the SSID on.
#                            Omitted -> detected (wlan0 when present).
#   -h, --help               Print this text.
#
# Phase A: Install packages (apt; the WireGuard implementation recorded; the venv's package list)
# Phase B: Pre-extraction setup (dirs, sysctl, iptables scaffold, sudoers)
# Phase C: Extract world tar + the venv the services run on
# Phase D: Clean stale state + configure everything (post-tar)
# Phase E: Node identity + provision (setup_lillypad_v4, SSL, tuning)
# Phase F: Finalize (enable services, logs, cron, verify, reboot)
###############################################################################
set -eu
# [UMASK_IS_OURS_V1 - T4a] what this lays down under /etc, /opt and /usr must not take its modes from the caller's
# umask (a shell with umask 0000 made /opt/fdf and its bin/ world-writable, in the T4a container).
umask 022

# [SELF_RELOCATE_V1] frognet_reset.sh removes FrogNet's files from /usr/local/bin,
# and the installer is one of them (/usr/local/bin/installer/frognet_install.sh) -
# so running it from there means the reset deletes the script that is running it, mid-run.
# Copy ourselves to /tmp and re-exec BEFORE anything can remove us, remembering the
# original directory so frognet_world.tgz is still found beside the original.
if [[ -z "${FROGNET_INSTALL_RELOCATED:-}" ]]; then
    _self="$(readlink -f "$0")"
    case "$_self" in
        /usr/local/bin/*)
            _safe="$(mktemp /tmp/frognet_install.XXXXXX)"
            cp "$_self" "$_safe"
            chmod +x "$_safe"
            FROGNET_INSTALL_ORIGIN="$(cd "$(dirname "$_self")" && pwd)"
            export FROGNET_INSTALL_ORIGIN FROGNET_INSTALL_RELOCATED=1
            echo "Relocating installer to $_safe (the reset removes FrogNet's files from /usr/local/bin, this one among them)"
            exec bash "$_safe" "$@"
            ;;
    esac
fi
SCRIPT_DIR="${FROGNET_INSTALL_ORIGIN:-$(cd "$(dirname "$0")" && pwd)}"
WORLD_TGZ="${SCRIPT_DIR}/frognet_world.tgz"

###############################################################################
# Argument parsing
###############################################################################
ARG_NAME=""
ARG_IP=""
ARG_DB_PASS=""
ARG_DB_PASS_SET=0
SSID_PROJECTION=1
SSID_PROJECTION_EXPLICIT=0
PRESERVE=0
FROM_REPO=0
REBUILD_VENV_FORCE=0
WIFI_COUNTRY=""
WIFI_PSK=""
AP_IFACE=""
LAN_IFACE=""
REGION_RAM=""
REGION_KEY=""
REGION_AREA=""
UNINSTALL=0
KEEP_IDENTITY=0
KEEP_DB=0
NO_REBOOT=0

usage() {
    # The whole header, from the title line to the Phase list: every option.
    sed -n '/^# frognet_install.sh - /,/^# Phase F:/p' "$0"
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)              ARG_NAME="$2";        shift 2 ;;
        --name=*)            ARG_NAME="${1#*=}";   shift   ;;
        --ip)                ARG_IP="$2";          shift 2 ;;
        --ip=*)              ARG_IP="${1#*=}";     shift   ;;
        --db-pass)           ARG_DB_PASS="$2"; ARG_DB_PASS_SET=1; shift 2 ;;
        --db-pass=*)         ARG_DB_PASS="${1#*=}"; ARG_DB_PASS_SET=1; shift ;;
        --preserve)          PRESERVE=1; shift ;;
        --from-repo)         FROM_REPO=1; shift ;;
        --wifi-psk)          WIFI_PSK="$2"; shift 2 ;;
        --wifi-psk=*)        WIFI_PSK="${1#*=}"; shift ;;
        --rebuild-venv)      REBUILD_VENV_FORCE=1; shift ;;
        --no-ssid-projection) SSID_PROJECTION=0; SSID_PROJECTION_EXPLICIT=1; shift ;;
        --ssid-projection)   SSID_PROJECTION=1; SSID_PROJECTION_EXPLICIT=1; shift ;;
        --country)           WIFI_COUNTRY="$2"; shift 2 ;;
        --country=*)         WIFI_COUNTRY="${1#*=}"; shift ;;
        --ap-iface)          AP_IFACE="$2"; shift 2 ;;
        --ap-iface=*)        AP_IFACE="${1#*=}"; shift ;;
        --lan-iface)         LAN_IFACE="$2"; shift 2 ;;
        --lan-iface=*)       LAN_IFACE="${1#*=}"; shift ;;
        --region)            REGION_RAM="$2"; shift 2 ;;
        --region=*)          REGION_RAM="${1#*=}"; shift ;;
        --site-key)          REGION_KEY="$2"; shift 2 ;;
        --site-key=*)        REGION_KEY="${1#*=}"; shift ;;
        --area)              REGION_AREA="$2"; shift 2 ;;
        --area=*)            REGION_AREA="${1#*=}"; shift ;;
        --uninstall)         UNINSTALL=1; shift ;;
        --keep-identity)     KEEP_IDENTITY=1; shift ;;
        --keep-db)           KEEP_DB=1; shift ;;
        --no-reboot)         NO_REBOOT=1; shift ;;
        -h|--help)           usage 0 ;;
        *)                   echo "Unknown arg: $1" >&2; usage 1 ;;
    esac
done

# [UNINSTALL_V1] --uninstall is the reset plus what only an install adds (cron,
# froguser, the region membership, /opt/fdf, this installer). frognet_reset.sh
# owns all of it (its --uninstall), so there is one list of what FrogNet put on
# the machine. It sits beside this script in a release and in a checkout, and at
# /usr/local/bin/installer/ on a node (the self-relocation above kept the origin).
if (( UNINSTALL )); then
    (( ${EUID:-$(id -u)} == 0 )) || { echo "ERROR: Must run as root" >&2; exit 1; }
    _R="${SCRIPT_DIR}/frognet_reset.sh"
    [[ -f "$_R" ]] || _R="${SCRIPT_DIR}/usr/local/bin/installer/frognet_reset.sh"
    [[ -f "$_R" ]] || { echo "ERROR: --uninstall: frognet_reset.sh not found beside $0 (looked in ${SCRIPT_DIR}/ and ${SCRIPT_DIR}/usr/local/bin/installer/)" >&2; exit 1; }
    _RA=(--uninstall --yes)
    (( KEEP_IDENTITY )) && _RA+=(--keep-identity)
    (( KEEP_DB )) && _RA+=(--keep-db)
    cd / && exec bash "$_R" "${_RA[@]}"
fi
(( KEEP_IDENTITY || KEEP_DB )) && { echo "ERROR: --keep-identity / --keep-db go with --uninstall (an install keeps identity with --preserve)" >&2; exit 1; }

# Validate args we did receive (defer interactive collection to phase E1).
if [[ -n "$ARG_IP" ]]; then
    [[ "$ARG_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || {
        echo "ERROR: --ip must look like 10.X.Y.1 (got: $ARG_IP)" >&2; exit 1; }
fi
if [[ -n "$ARG_NAME" ]]; then
    [[ "$ARG_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || {
        echo "ERROR: --name must be letters/numbers/_/- (got: $ARG_NAME)" >&2; exit 1; }
fi
if [[ -n "$LAN_IFACE" ]]; then
    [[ -e "/sys/class/net/${LAN_IFACE}" ]] || {
        echo "ERROR: --lan-iface ${LAN_IFACE}: no such interface (this machine has: $(ls /sys/class/net | grep -v '^lo$' | tr '\n' ' '))" >&2; exit 1; }
fi
# [REGION_FROM_THE_INSTALLER_V1] --region: the FD&F node installer for this
# architecture must be in the payload, and the key and area must be known, BEFORE
# phase A - a missing piece found after ten minutes of apt is ten minutes lost.
# The .run is the one way a node joins a region (the same file John's existing
# nodes take); there is no second path.
FDF_RUN=""
if [[ -n "$REGION_RAM" ]]; then
    [[ "$REGION_RAM" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$ ]] || {
        echo "ERROR: --region must be IPV4:PORT, the RAM manager's address as this node reaches it (got: $REGION_RAM)" >&2; exit 1; }
    if [[ -z "$REGION_AREA" || -z "$REGION_KEY" ]]; then
        (: <>/dev/tty) 2>/dev/null || {
            echo "ERROR: --region needs --site-key and --area (no terminal to ask for them on)" >&2; exit 1; }
        exec 3<>/dev/tty
        while [[ -z "$REGION_AREA" ]]; do printf '  Region area name (fdf-region-create AREA): ' >&3; IFS= read -r REGION_AREA <&3 || exit 1; done
        while [[ -z "$REGION_KEY" ]]; do printf '  Region site key (hidden): ' >&3; IFS= read -rs REGION_KEY <&3 || exit 1; printf '\n' >&3; done
        exec 3>&-
    fi
    [[ "$REGION_AREA" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: --area must be letters/numbers/._- (got: $REGION_AREA)" >&2; exit 1; }
elif [[ -n "$REGION_KEY" || -n "$REGION_AREA" ]]; then
    echo "ERROR: --site-key / --area go with --region RAM_HOST:PORT" >&2; exit 1
fi

die_early() { echo "ERROR: $*" >&2; exit 1; }
[[ "${EUID:-$(id -u)}" -eq 0 ]] || die_early "Must run as root"
# [CHECKOUT_IS_A_FIRST_NODE_V1] Without --from-repo the ONLY input this installer
# accepts is frognet_world.tgz, and the only thing that builds one is
# frognet_build_release.sh, which snapshots a node that is ALREADY RUNNING. So the
# graph was working node -> tarball -> new node, with NO EDGE from a repository
# checkout to a first node: a stranger who cloned the repo could not reach one.
#
# --from-repo makes the checkout itself the payload. The repo IS the filesystem
# (etc/ opt/ usr/ var/ laid out as they land on the box), so C1 COPIES instead of
# unpacking. Every other phase is byte-identical -- this is one branch in one
# phase, not a second installer.
if (( FROM_REPO )); then
    # [FIND_THE_ROOT_DO_NOT_ASSUME_IT_V1] SCRIPT_DIR is where THIS FILE lives, not
    # where the operator is standing. In a release the installer sits at the
    # tarball root beside frognet_world.tgz, so SCRIPT_DIR is the root. In a
    # CHECKOUT it sits at usr/local/bin/installer/ - four levels down - because
    # that is where it installs to. Treating SCRIPT_DIR as the repo root therefore
    # failed on the ordinary invocation:
    #
    #   /repo# ./usr/local/bin/installer/frognet_install.sh --from-repo
    #   ERROR: /repo/usr/local/bin/installer/etc not found
    #
    # which told an operator standing in the repo root that they were not in the
    # repo root. WALK UP from the script until a directory holds all four payload
    # roots. That works whether the installer was run in place, copied to the root,
    # or invoked by absolute path from anywhere, and it does not depend on $PWD.
    REPO_ROOT=""
    _c="$SCRIPT_DIR"
    # "/" is NEVER a candidate. Every Linux box has /etc /opt /usr /var, so an
    # unbounded walk finds the filesystem root and calls it a checkout - the
    # not-a-repo case then "succeeds" and C1 copies / onto /. Verified: running
    # this from /tmp/notrepo accepted "/" before this line existed.
    for _ in 1 2 3 4 5 6; do
        [[ "$_c" == "/" ]] && break
        if [[ -d "$_c/etc" && -d "$_c/opt" && -d "$_c/usr" && -d "$_c/var" ]]; then
            REPO_ROOT="$_c"; break
        fi
        _c="$(dirname "$_c")"
    done
    # $PWD is checked last, and only as a distinct candidate - never merged with
    # the walk, so the error can say exactly which places were looked in.
    if [[ -z "$REPO_ROOT" && "$PWD" != "/" && -d "$PWD/etc" && -d "$PWD/opt" && -d "$PWD/usr" && -d "$PWD/var" ]]; then
        REPO_ROOT="$PWD"
    fi
    [[ -n "$REPO_ROOT" ]] || die_early "--from-repo: no directory holding all of etc/ opt/ usr/ var/ found, searching upward from ${SCRIPT_DIR} and in ${PWD}. Extract the repo tarball and run the installer from inside it."
    [[ -d "${REPO_ROOT}/etc/systemd/system" ]] || die_early "--from-repo: ${REPO_ROOT}/etc/systemd/system not found - the checkout has no systemd units and would install a node that boots with nothing running."
    # Everything downstream uses SCRIPT_DIR as "where the payload is". Point it at
    # the root we actually found, so C1 and the self-install both follow.
    SCRIPT_DIR="$REPO_ROOT"
else
    [[ -f "$WORLD_TGZ" ]] || { echo "ERROR: frognet_world.tgz not found in $SCRIPT_DIR (pass --from-repo to install from a repository checkout instead)" >&2; exit 1; }
fi
# The FD&F node installer is found in the payload root (the checkout, or the
# release root), at the path it has in the repository.
if [[ -n "$REGION_RAM" ]]; then
    FDF_RUN="${SCRIPT_DIR}/opt/fdf/dist/fdf-install-$(uname -m).run"
    [[ -f "$FDF_RUN" ]] || die_early "--region: $FDF_RUN not found. This machine is $(uname -m); a region member needs the FD&F node installer built for it (opt/fdf/src/fdf-source/rinab/tools/make_deliverables.sh puts it in opt/fdf/dist)."
fi

LOG=/var/log/frognet_install.log
mkdir -p /var/log
exec > >(tee -a "$LOG") 2>&1

phase()     { echo; echo "========================================================"; echo "  STEP $*"; echo "========================================================"; }
log()       { echo "$(date '+%Y-%m-%d %H:%M:%S') [install] $*"; }
warn()      { echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN]    $*" >&2; }
die()       { echo "$(date '+%Y-%m-%d %H:%M:%S') [FATAL]   $*" >&2; exit 1; }

INSTALL_BUILD="frognet-install-2026-10-14-r24"   # [EVERY_COMPONENT_LOGS_ITS_BUILD_V1]
log "frognet_install.sh build ${INSTALL_BUILD} starting on $(hostname) - $(uname -m) - $(date) - $( [[ -n "$REGION_RAM" ]] && echo "region ${REGION_AREA} at ${REGION_RAM}" || echo "LAN-only" )"

# [INSTALL_QUIESCE_MERGE_V1] Take runMerge's own lock and hold it for the whole
# install. The NetworkManager dispatcher hooks written in D7 fire runMerge on any
# interface event, and runMerge rewrites the default route and resolv.conf -- so an
# NM event during the install (a DHCP renew, a carrier blip, or the interactive E1
# prompt simply sitting idle) would tear up the route the operator's SSH rides on.
#
# runMerge does `flock -n 9` on this file and bails cleanly (touch runAgain, exit 0)
# when the lock is held. We open fd 9 on the same file and flock it here; the
# installer is one long-lived process, so the lock is held until the installer
# exits -- i.e. through the final `systemctl reboot`. No cleanup path can leave it
# stuck: the fd releases on ANY exit (success, die, kill), and /var/run is tmpfs so
# the file itself is gone after the reboot regardless. This is the same guard
# runMerge already honors -- no new mechanism, nothing for the hooks to check.
mkdir -p /var/run /etc/sentinels
exec 9>/var/run/runMerge.lock || die "cannot open runMerge lock"
if flock -n 9; then
    log "merge lock held for the duration of the install (runMerge will defer)"
else
    warn "runMerge lock already held - a merge may be in flight; continuing"
fi

###############################################################################
# DB secret: collect ONCE, up front, and treat it as authoritative
#
# [INSTALL_DB_PASS_PROMPT_V1] The FrogUser password is a SHARED pond-wide
# constant. It used to come from whatever literal was baked into the shipped
# config.php, and D8 pushed only THAT value into MySQL -- nothing reconciled
# DB_CONFIG.json or the cache modules. When those disagreed (a release built
# with --scramble leaves the tokenized __FROGNET_DB_PASS__ behind), MySQL held
# one value while core/store.py read another, producing
#   1045 (28000): Access denied for user 'FrogUser'@'localhost' (using password: YES)
# on a node that looked cleanly installed.
#
# Collected BEFORE phase A so a mistyped secret costs seconds, not a full apt
# run. Never echoed, never written to the log.
###############################################################################
DB_PASS=""

collect_db_pass() {
    if [[ $ARG_DB_PASS_SET -eq 1 ]]; then
        [[ -n "$ARG_DB_PASS" ]] || die "--db-pass was given but empty"
        DB_PASS="$ARG_DB_PASS"
        log "DB password taken from --db-pass (${#DB_PASS} chars)"
        return
    fi
    # Probe by OPENING /dev/tty: the node exists even with no controlling
    # terminal (systemd, ssh -T, CI), where -r/-w pass but the open fails ENXIO.
    if ! (: <>/dev/tty) 2>/dev/null; then
        die "No DB password: no controlling terminal and --db-pass was not given. Re-run with --db-pass '<password>'."
    fi
    exec 3<>/dev/tty
    local p1 p2
    for _try in 1 2 3; do
        printf '\n  FrogUser DB password (shared across the whole pond).\n' >&3
        printf '  Input is hidden.\n' >&3
        printf '  Password: ' >&3
        IFS= read -rs p1 <&3 || die "Could not read password from terminal"
        printf '\n  Confirm : ' >&3
        IFS= read -rs p2 <&3 || die "Could not read password from terminal"
        printf '\n' >&3
        if [[ -z "$p1" ]]; then printf '  Empty password is not allowed.\n' >&3; continue; fi
        if [[ "$p1" != "$p2" ]]; then printf '  Entries did not match.\n' >&3; continue; fi
        DB_PASS="$p1"; unset p1 p2; exec 3>&-
        log "DB password collected interactively (${#DB_PASS} chars)"
        return
    done
    exec 3>&-
    die "DB password not confirmed after 3 attempts"
}

collect_db_pass
[[ -n "$DB_PASS" ]] || die "internal: DB_PASS empty after collection"


phase "D1: Detect interfaces (before anything is installed: a wrong LAN choice stops here)"
# [LAN_IS_NEVER_THE_UPLINK_V1 - T4a] The served LAN interface (eth0Name: its
# 10.X.Y.1/24, dnsmasq, DHCP) must never be the uplink - the interface the default
# route uses. setup_lillypad step 2 deletes every NetworkManager profile bound to the
# served interface, so serving the uplink deletes the uplink's profile and the
# machine (and the operator's SSH) is cut off at the next reboot. It used to default
# to "eth0" when nothing else was found, which on a one-NIC machine IS the uplink.
# Now: --lan-iface if given, else the first wired interface that is not the uplink;
# the uplink is refused either way, and no candidate stops the install, naming the
# interfaces and the option to give.
_UPLINK_DEV="$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
_wired_ifaces() {
    for dev in $(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == lo || "$dev" == veth* || "$dev" == docker* || "$dev" == br-* || "$dev" == virbr* || "$dev" == wl* || "$dev" == wg* || "$dev" == frognet* ]] && continue
        echo "$dev"
    done
}
_detect_eth0() {
    local dev
    for dev in $(_wired_ifaces); do
        [[ -n "$_UPLINK_DEV" && "$dev" == "$_UPLINK_DEV" ]] && continue
        echo "$dev"; return
    done
}
_detect_wlan() {
    local idx=0
    for dev in $(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == wl* ]] || continue
        [[ $idx -eq $1 ]] && { echo "$dev"; return; }
        idx=$((idx + 1))
    done
}
_PRESERVED_LAN=""
[[ "$PRESERVE" -eq 1 && -f /etc/frognet/interfaces_override.conf ]] && \
    _PRESERVED_LAN="$(sed -n 's/^eth0Name="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/frognet/interfaces_override.conf | head -1)"
if [[ -n "$LAN_IFACE" ]]; then
    ETH0_IF="$LAN_IFACE"
    log "LAN interface from --lan-iface: $ETH0_IF"
elif [[ -n "$_PRESERVED_LAN" ]]; then
    # [PRESERVE_KNOWS_ITSELF_V1] an upgrade keeps the interface the node already serves.
    ETH0_IF="$_PRESERVED_LAN"
    log "--preserve: LAN interface $ETH0_IF from /etc/frognet/interfaces_override.conf"
else
    ETH0_IF="$(_detect_eth0 || true)"
fi
_ifs_line="interfaces: $(_wired_ifaces | tr '\n' ' ')(uplink = the default route's device: ${_UPLINK_DEV:-none})"
[[ -n "$ETH0_IF" ]] || die "D1: no wired interface to serve the LAN on besides the uplink. ${_ifs_line}. Give the interface this node serves with --lan-iface IFACE (it must not be ${_UPLINK_DEV:-the uplink})."
[[ -z "$_UPLINK_DEV" || "$ETH0_IF" != "$_UPLINK_DEV" ]] || die "D1: ${ETH0_IF} is the uplink (the default route uses it); serving it would delete its connection profile and cut this machine off. ${_ifs_line}. Give another interface with --lan-iface IFACE."
WLAN0_IF="$(_detect_wlan 0 || true)"; WLAN0_IF="${WLAN0_IF:-wlan0}"
WLAN1_IF="$(_detect_wlan 1 || true)"; WLAN1_IF="${WLAN1_IF:-wlan1}"
log "Detected: eth0=$ETH0_IF (served LAN) wlan0=$WLAN0_IF wlan1=$WLAN1_IF uplink=${_UPLINK_DEV:-none}"

###############################################################################
#                      PHASE A: INSTALL PACKAGES
###############################################################################

# [PRESERVE_KNOWS_ITSELF_V1] --preserve is an UPGRADE of a node that already has
# an identity, so do not ask for it again.  Both values are already on disk:
#   node name  -> `domain=` in /etc/dnsmasq.d/opts_only.conf, which
#                 setup_lillypad_v4 (:485) declares the SINGLE SOURCE OF TRUTH
#                 for the node name -- every other dnsmasq conf has its domain=
#                 stripped precisely so this one cannot be contradicted.
#   gateway IP -> `dhcp-option=option:router,<ip>` in the same file, which is by
#                 definition this node's .1.
#
# Prompting here is not merely redundant, it is dangerous: a typo at this prompt
# re-IPs or renames a live node, which changes its identity and its whole
# /24, on what the operator asked for as an in-place upgrade.
#
# An explicit --name / --ip still wins: this only fills in what was not given.
# If the file is missing or malformed we fall through to the normal prompt rather
# than guessing -- a wrong identity is worse than a question.
#
# [PRESERVE_READS_BEFORE_D2_V1 - T4a] Read HERE, before anything is changed: D2 deletes
# opts_only.conf (stale state for setup_lillypad to rewrite), and this used to be read in
# E1, after D2 - so --preserve never found it, fell to the name prompt, and a
# non-interactive upgrade died there with no message (seen upgrading a T0 node, G4).
if [[ "$PRESERVE" -eq 1 && -f /etc/dnsmasq.d/opts_only.conf ]]; then
    _PN="$(awk -F= '/^domain=/{print $2; exit}' /etc/dnsmasq.d/opts_only.conf | xargs)"
    _PI="$(awk -F, '/^dhcp-option=option:router,/{print $2; exit}' /etc/dnsmasq.d/opts_only.conf | xargs)"
    if [[ -n "$_PN" && "$_PI" =~ ^([0-9]{1,3}\.){3}1$ ]]; then
        if [[ -z "$ARG_NAME" ]]; then
            ARG_NAME="$_PN"
            log "--preserve: node name '$_PN' recovered from opts_only.conf (not prompting)"
        fi
        if [[ -z "$ARG_IP" ]]; then
            ARG_IP="$_PI"
            log "--preserve: gateway IP $_PI recovered from opts_only.conf (not prompting)"
        fi
    else
        warn "--preserve: could not recover identity from opts_only.conf"
        warn "            (domain='$_PN' router='$_PI') - falling through to prompt"
    fi
fi


phase "A0: Reinstall reset"
# [REINSTALL_RESET_V1] A reinstall must land on a CLEAN machine, not on top of the
# last one. Stale schema, an old GUID, orphaned WireGuard keys and half-written
# config are exactly what makes "it worked on a fresh box but not this one" - so the
# DEFAULT for a machine that already carries an install is a full wipe. The wipe is
# NOT reimplemented here: frognet_reset.sh is the proven tool that owns it (it knows
# which FrogNet-owned files live inside shared OS dirs and never nukes those dirs
# wholesale). Pass --preserve to upgrade in place and keep DBs + identity.
# frognet_reset.sh SHIPS IN THE RELEASE ROOT beside this script, so it is already
# outside /usr/local/bin (which it deletes) and already present BEFORE the world tar
# is laid down. Run it from where it sits - no copying, no dependency on what happens
# to be installed on the box.
# [VENV_SURVIVES_RESET_V1] frognet_reset.sh removes /opt/frognet_semantic, and the
# venv lives inside it - so a reinstall used to destroy a perfectly good venv, then
# C2 rebuilt it from zero: `pip install --upgrade pip` (the 26 -> 29 churn you see
# every run) plus ~24 wheels, several of which COMPILE on ARM. The venv is not state
# and it is not config; it is arch-specific build output that the next install can
# reuse verbatim. Carry it across the wipe and put it back after extraction. C2 still
# arch-checks what we restore, so a wrong-arch venv is still rebuilt. --rebuild-venv
# forces the old behaviour.
_VENV_STASH="/var/tmp/frognet_venv_stash"
rm -rf "$_VENV_STASH"
# [CHECKOUT_IS_A_FIRST_NODE_V1] In a RELEASE, frognet_reset.sh sits beside
# frognet_world.tgz in the release root. In a CHECKOUT it sits where it installs
# to, usr/local/bin/installer/. Looking only in SCRIPT_DIR made --from-repo die
# at A0 on any box with a prior install, telling the operator the release was
# incomplete when it was not. Measured on BrokerHost 2026-08-29.
_RESET_SRC=""
for _c in "${SCRIPT_DIR}/frognet_reset.sh" \
          "${SCRIPT_DIR}/usr/local/bin/installer/frognet_reset.sh"; do
    [[ -f "$_c" ]] && { _RESET_SRC="$_c"; break; }
done
[[ -n "$_RESET_SRC" ]] || _RESET_SRC="${SCRIPT_DIR}/frognet_reset.sh"
# [OS_BEFORE_FROGNET_V1] C1 records the machine's name resolution only while it is
# the machine's own: on a clean machine, or after a reset that put it back from an
# earlier record. On --preserve, or after a reset of an install that had no record,
# what is on the machine is FrogNet's, and recording it would be recording a lie.
OSB_RECORDABLE=0
if [[ ! -e /opt/frognet_semantic && ! -e /etc/frognet ]]; then
    log "No prior FrogNet install detected - already clean"
    OSB_RECORDABLE=1
elif [[ "$PRESERVE" -eq 1 ]]; then
    # UPGRADE IN PLACE: nothing is removed. New files are written over the old ones
    # by C1, the schema is migrated in D8, identity and databases are untouched.
    log "--preserve: UPGRADE IN PLACE - no files removed, no databases dropped"
    log "  new code is written over the existing install; schema migrations run in D8"
elif [[ -f "$_RESET_SRC" ]]; then
    log "Prior install detected - FULL RESET (pass --preserve to keep it)"
    if [[ "$REBUILD_VENV_FORCE" -eq 0 && -x /opt/frognet_semantic/venv/bin/python3 ]]; then
        mv /opt/frognet_semantic/venv "$_VENV_STASH"
        log "  Stashed existing venv - it will be restored after extraction"
    fi
    [[ -d /var/lib/frognet-install/os-before ]] && OSB_RECORDABLE=1
    ( cd / && bash "$_RESET_SRC" --yes ) || die "frognet_reset.sh failed - refusing to install over a half-wiped machine"
    log "Reset complete - installing onto a clean machine"
else
    die "Prior install detected but frognet_reset.sh was not found (looked in ${SCRIPT_DIR}/ and ${SCRIPT_DIR}/usr/local/bin/installer/) - the release or checkout is incomplete. Pass --preserve to upgrade in place."
fi

mkdir -p /var/lib/frognet-install
phase "A0b: Record the machine before FrogNet"
# [OS_BEFORE_FROGNET_V1 - T4a] What the install REPLACES in the machine's own name
# resolution (and the units it enables), recorded once - the first time FrogNet is
# installed on this machine, and BEFORE phase A (apt enables what it installs) -
# so a reset or an uninstall puts it back the way it was (D2 masks systemd-resolved
# and points resolv.conf at dnsmasq; D7 rewrites NetworkManager.conf). A reinstall
# finds the record and leaves it alone: it describes the machine before FrogNet.
_OSB=/var/lib/frognet-install/os-before
OSB_NEW=0
if [[ -d "$_OSB" ]]; then
    log "  the machine's name resolution before FrogNet is recorded in $_OSB"
elif (( OSB_RECORDABLE )); then
    mkdir -p "$_OSB.tmp"
    if [[ -e /etc/resolv.conf || -L /etc/resolv.conf ]]; then cp -P /etc/resolv.conf "$_OSB.tmp/resolv.conf"; fi
    [[ -f /etc/NetworkManager/NetworkManager.conf ]] && cp -p /etc/NetworkManager/NetworkManager.conf "$_OSB.tmp/NetworkManager.conf"
    # hostname and hosts (E2 rewrites both), fstab (frognet-system-tune.sh adds tmpfs lines)
    for _f in hostname hosts fstab; do [[ -f "/etc/$_f" ]] && cp -p "/etc/$_f" "$_OSB.tmp/$_f"; done
    # the units this install enables, starts or masks (frognet_reset.sh puts them back)
    for _u in systemd-resolved dnsmasq apache2 mariadb hostapd ifplugd unbound netfilter-persistent; do
        printf '%s\n' "$(systemctl is-enabled "$_u" 2>/dev/null || true)" > "$_OSB.tmp/$_u.enabled"
        printf '%s\n' "$(systemctl is-active "$_u" 2>/dev/null || true)" > "$_OSB.tmp/$_u.active"
    done
    mv "$_OSB.tmp" "$_OSB"; OSB_NEW=1
    log "  recorded the machine's name resolution before FrogNet in $_OSB (resolv.conf, NetworkManager.conf, systemd-resolved: $(cat "$_OSB/systemd-resolved.enabled")/$(cat "$_OSB/systemd-resolved.active"))"
else
    warn "  no record of this machine's name resolution before FrogNet (it was installed before T4a): what is here now is FrogNet's, so it is not recorded; a later reset or uninstall will say it cannot put it back"
fi

phase "A1: Package index"
# [FROGNET_DOES_NOT_OWN_THE_OS_V1] This used to run a full
# `apt-get upgrade -y` -- every package on the box. On BrokerHost 2026-08-29 that
# was 352 packages and 270 MB, including chromium, vlc, hplip and the kernel, and
# it FAILED: packages.sury.org served EXPKEYSIG on its index and 404 on twenty
# php8.4 debs. `set -eu` then killed the install at A1, before a single FrogNet
# file was laid down, for a reason that has nothing to do with FrogNet.
#
# Upgrading the operating system is not part of installing a node. It makes every
# third-party repo on the box a hard dependency of the install, takes minutes to
# tens of minutes, and can reboot-require a kernel the operator did not ask for.
# What FrogNet actually needs is A2, which installs the packages it names.
#
# The index refresh stays: A2 cannot resolve without it, and its failure IS ours.
apt-get update
log "Package index refreshed"

phase "A2: APT packages"
APT_PACKAGES=(
    mariadb-server mariadb-client
    apache2 apache2-utils libapache2-mod-php
    php php-mysql php-curl php-xml php-mbstring php-gd php-zip php-cli
    wireguard wireguard-tools
    # (wireguard-go: A2b, only where it is this machine's WireGuard [WG_IMPL_RECORDED_V1])
    dnsmasq
    network-manager
    iptables iptables-persistent netfilter-persistent
    python3 python3-pip python3-dev python3-venv
    # [SIM_NEEDS_YAML_V1] netdef topology definitions are YAML. Without this the
    # simulator's TIER 4 reports "[netdef] SKIPPED <dir>: ImportError" and
    # silently gates only the twenty built-in topologies.
    python3-yaml
    build-essential gcc g++ make pkg-config libffi-dev libssl-dev
    # [PIP_BUILD_DEPS_V1] mysqlclient and NetfilterQueue publish no wheel for
    # Linux: pip compiles them, and the compile needs these headers (found on a
    # fresh Ubuntu 24.04, T4a). Without them A3 and C2 fail on those two.
    libmariadb-dev libnetfilter-queue-dev
    iproute2 iputils-ping net-tools curl wget nmap traceroute dnsutils netcat-openbsd
    gpsd gpsd-clients
    # [HAM_OUT_V1] ax25-tools / ax25-apps are not installed (the ham subsystem
    # does not ship). file: C2's venv arch check. uuid-runtime: uuidgen
    # (frognet-node-guid.sh, registerFrogNetHost.bash, the reporters).
    file uuid-runtime
    # [WATCHER_HAS_INOTIFY_V1 - T4c] inotifywait: the merge watcher waits on its request
    # directory with it (the hook's and the neighbours' merge requests). It was never
    # installed: every wait failed at once and the watcher fell into a 1 s poll that
    # fired each request with no 30 s window (seen in the T4c G1 container).
    inotify-tools
    jq qrencode sqlite3 cron logrotate rsync git htop tmux vim less unzip
    ca-certificates gnupg lsb-release openssl libssl-dev
    apt-transport-https software-properties-common
    hostapd
    ifplugd iw
)
# [SKIP_SATISFIED_V1] Only hand apt the packages that are actually MISSING. The
# list was previously filtered for "exists in the repo" and then passed wholesale
# every run, so a reinstall re-resolved every package for nothing.
INSTALL_LIST=(); _already=0
for pkg in "${APT_PACKAGES[@]}"; do
    apt-cache show "$pkg" >/dev/null 2>&1 || { warn "Not in repo: $pkg"; continue; }
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        _already=$((_already+1)); continue
    fi
    INSTALL_LIST+=("$pkg")
done
log "APT: ${_already} already installed, ${#INSTALL_LIST[@]} to install"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends mariadb-server mariadb-client
INSTALL_NO_MARIA=()
for pkg in "${INSTALL_LIST[@]}"; do
    [[ "$pkg" == "mariadb-server" || "$pkg" == "mariadb-client" ]] && continue
    INSTALL_NO_MARIA+=("$pkg")
done
if (( ${#INSTALL_NO_MARIA[@]} > 0 )); then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${INSTALL_NO_MARIA[@]}"
else
    log "  All APT packages already present - nothing to do"
fi
systemctl unmask hostapd 2>/dev/null || true
log "APT packages installed"

phase "A2b: WireGuard implementation (chosen once, recorded)"
# [WG_IMPL_RECORDED_V1 - T4b] One implementation per machine, chosen here by platform and recorded in
# /etc/frognet/wg.impl; the tunnel daemon (through wg-quick), fdf-hub and the simulator's netns backend use only it.
# The rule is fdf-wg-impl's (opt/fdf/bin/fdf-wg-impl, its twin for machines without FrogNet): kernel when
# /sys/module/wireguard exists or `modprobe -n wireguard` finds the module, else wireguard-go. A record already
# there is kept (an upgrade does not re-decide). wireguard-go is installed only where it is the choice.
WG_IMPL_FILE=/etc/frognet/wg.impl
if [[ -s "$WG_IMPL_FILE" ]]; then
    WG_IMPL="$(cat "$WG_IMPL_FILE")"
    log "WireGuard: $WG_IMPL (recorded in $WG_IMPL_FILE; kept)"
else
    if [[ -e /sys/module/wireguard ]]; then WG_IMPL=kernel; _why="/sys/module/wireguard exists"
    elif modprobe -n wireguard >/dev/null 2>&1; then WG_IMPL=kernel; _why="modprobe -n wireguard: the module is there for $(uname -r)"
    else WG_IMPL=wireguard-go; _why="no wireguard module for $(uname -r)"; fi
    mkdir -p /etc/frognet
    printf '%s\n' "$WG_IMPL" > "$WG_IMPL_FILE" || die "cannot write $WG_IMPL_FILE"
    chmod 644 "$WG_IMPL_FILE"
    log "WireGuard: $WG_IMPL ($_why), recorded in $WG_IMPL_FILE"
fi
case "$WG_IMPL" in
    kernel) ;;
    wireguard-go)
        if ! command -v wireguard-go >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard-go \
                || die "A2b: wireguard-go is this machine's WireGuard and apt-get install wireguard-go failed"
        fi ;;
    *) die "A2b: $WG_IMPL_FILE says '$WG_IMPL': it must be kernel or wireguard-go" ;;
esac

phase "A3: Python packages (the list C2 puts in the venv)"
# [SERVICES_ON_THE_VENV_V1 - T4b] FrogNet's Python runs on ONE interpreter: the venv C2 builds,
# /opt/frognet_semantic/venv/bin/python3 (frognet-proxy, -daemon, -tunnel-daemon-v3, the safeboot,
# the merge, C3). Nothing is pip-installed into the system python any more: that put eight console scripts in
# /usr/local/bin (cbor2, dotenv, gpsdclient, gunicorn, ...) and Ubuntu 24.04 refuses some packages there
# (Flask/pyOpenSSL over Debian's blinker/cryptography, T4a). The same list was installed twice.
# lxml: core/html_handler.py and xml_handler.py (the proxy's and the daemon's format registry) import it; the
# system python had it only because an apt package pulled python3-lxml in.
PIP_PACKAGES=(
    mysql-connector-python mysqlclient NetfilterQueue
    aiohttp lz4 msgpack orjson cbor2 psutil
    gevent gunicorn
    gpsdclient paho-mqtt requests
    python-dotenv simplejson ujson PyYAML
    cryptography httpx lxml
)
# [ONLY_WHAT_A_NODE_IMPORTS_V1 - T4a] Flask, Werkzeug and pyOpenSSL are not in this list: nothing a node runs
# imports them (the only `import flask` is simulation/sotf_browser_server.py, a development tool).
log "  ${#PIP_PACKAGES[@]} packages, installed into the venv in C2 (nothing into the system python)"

###############################################################################
#                 PHASE B: PRE-EXTRACTION SETUP
###############################################################################

# B1 (sysctl) is applied in C1a, from the tree's own etc/sysctl.d/90-frognet-forwarding.conf, once C1 has laid it
# down. [TREE_FILES_ARE_THE_FILES_V1] It used to write its own older copy here, which C1 then overwrote.

phase "B2: iptables scaffold"
# [OS_BEFORE_FROGNET_V1 - T4a] The machine's own packet-filter rules, recorded before
# FrogNet's replace them (B2, E6), in the same record - here, after A2, because a minimal
# machine has no iptables-save before A2 installs it. A resolver can depend on them (a
# container's 127.0.0.11 is a NAT rule; ufw is rules), so the reset puts them back.
if (( OSB_NEW )); then
    iptables-save  > "$_OSB/iptables.v4" || die "iptables-save failed - cannot record the machine's rules before replacing them"
    ip6tables-save > "$_OSB/iptables.v6" || die "ip6tables-save failed - cannot record the machine's rules before replacing them"
    log "  recorded the machine's packet-filter rules before FrogNet ($(grep -c '^-A' "$_OSB/iptables.v4") v4 / $(grep -c '^-A' "$_OSB/iptables.v6") v6 rules)"
fi
mkdir -p /etc/iptables
cat > /etc/iptables/rules.v4 <<'IPTR'
*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
COMMIT
IPTR
systemctl enable netfilter-persistent 2>/dev/null || true

phase "B3: Directories"
mkdir -p \
    /var/lib/frognet-tunnel/active /var/lib/misc \
    /etc/wireguard /etc/sentinels /etc/frognet /etc/ssl/localCA \
    /opt/frognet_semantic/{blob_cache,logs,cache} \
    /run/frognet /var/log/{frognet,mysql,apache2} \
    /var/www/{html,cgi-bin} \
    /etc/dnsmasq.d /etc/NetworkManager/{conf.d,dispatcher.d} \
    /etc/systemd/system/dnsmasq.service.d \
    /etc/sudoers.d
chmod 700 /etc/wireguard /opt/frognet_semantic/blob_cache
# [APACHE_LOGDIR_V1] apache2 refuses to start without its log directory, and the
# install never created it - so a box whose /var/log/apache2 was missing (minimal
# image, or a wipe that took it) came up with a dead web server, which takes api.php,
# getHosts.php and frognet_echo.php down with it. 777 because the FrogNet web stack
# writes here as more than one uid (apache child + php + the metrics writers).
# Kept for the case where something else still writes here; harmless once the
# directives above point at the journal. mkdir first so a tmpfs-wiped /var/log
# cannot abort the install under 'set -e'.
mkdir -p /var/log/apache2 && chmod 777 /var/log/apache2 || true
log "Directories created"

phase "B4: Sudoers"
# www-data needs to invoke the FrogNet setup helpers as root so
# setup_frognet.html can apply identity, WiFi and SSID settings.  Without
# this fragment, every action in the setup page returns a 401.
cat > /etc/sudoers.d/frognet-setup <<'SUDOERS'
www-data ALL=(root) NOPASSWD: /usr/local/bin/frognet_setup_helper.bash
www-data ALL=(root) NOPASSWD: /usr/local/bin/frognet_setup_v4_helper.bash
Defaults!/usr/local/bin/frognet_setup_helper.bash    !requiretty
Defaults!/usr/local/bin/frognet_setup_v4_helper.bash !requiretty
SUDOERS
chmod 0440 /etc/sudoers.d/frognet-setup
visudo -cf /etc/sudoers.d/frognet-setup >/dev/null || die "sudoers fragment is invalid"
log "Sudoers fragment installed"

###############################################################################
#                    PHASE C: EXTRACT WORLD TAR
###############################################################################

phase "C1: Lay down the world (tar, or --from-repo checkout)"
if (( FROM_REPO )); then
    # [CHECKOUT_IS_A_FIRST_NODE_V1] The repo IS the filesystem, so copy it; there is
    # nothing to unpack. This is the ONLY phase that differs between a release
    # install and a checkout install.
    #
    # [NODE_FILE_SET_V1 - T4a] Copy the NODE FILE SET, not the four roots. A repository is
    # not a filesystem image, it merely contains one, and it also contains what no node
    # runs: opt/fdf (the FD&F sources, Android tree, rlite tarball, the dist files, the
    # Windows bundle: ~250 MB), the book, the ham scripts the release never ships. The
    # rule is frognet_node_files in usr/local/lib/frognet_world_manifest.sh (taken from
    # THIS checkout), the same paths and excludes frognet_build_release.sh builds the
    # world tar with. A node in a region gets /opt/fdf from the FD&F node installer
    # (phase R), which is the one way /opt/fdf reaches a node.
    log "Installing from repository checkout $SCRIPT_DIR -> / (the node file set)"
    [[ "$(readlink -f "$SCRIPT_DIR")" != "/" ]] || die "--from-repo: the checkout is / itself"
    _MAN="${SCRIPT_DIR}/usr/local/lib/frognet_world_manifest.sh"
    [[ -r "$_MAN" ]] || die "--from-repo: $_MAN not found - the checkout has no node file set"
    _NODE_LIST="$(mktemp /tmp/frognet_node_files.XXXXXX)"
    ( . "$_MAN"; frognet_check_manifest "$SCRIPT_DIR" 2>/dev/null; frognet_node_files "$SCRIPT_DIR" ) > "$_NODE_LIST" \
        || die "--from-repo: could not list the node file set from $SCRIPT_DIR"
    [[ -s "$_NODE_LIST" ]] || die "--from-repo: the node file set under $SCRIPT_DIR is empty"
    # Files only (no directory entries: an existing /etc or /usr/local/bin keeps its own
    # mode and owner), owned by root whoever cloned the repository.
    tar -C "$SCRIPT_DIR" --owner=0 --group=0 --no-recursion -cf - -T "$_NODE_LIST" | tar -C / -xpf - \
        || die "--from-repo: copying the node file set failed"
    log "  copied $(wc -l < "$_NODE_LIST") files"
else
    log "Extracting $WORLD_TGZ -> / ($(du -h "$WORLD_TGZ" | cut -f1))"
    # [EXTRACT_PROGRESS_V1] tar without -v prints NOTHING, so a multi-minute extraction
    # on SD looked like a hang - `ps` showed tar running while the screen sat dead. -v
    # would flood thousands of filenames; a checkpoint every 2000 records gives a live
    # heartbeat with a bounded number of lines.
    tar --ignore-zeros -xzf "$WORLD_TGZ" -C / \
        --checkpoint=2000 --checkpoint-action=echo="  ... extracted %u records"
    _NODE_LIST="$(mktemp /tmp/frognet_node_files.XXXXXX)"
    tar --ignore-zeros -tzf "$WORLD_TGZ" | grep -v '/$' | sed 's|^\./||' > "$_NODE_LIST"
fi
# [INSTALL_RECORD_V1 - T4a] What this install laid down, file by file, so frognet_reset.sh
# removes exactly FrogNet's files from the directories FrogNet shares with the machine
# (/usr/local/bin, /usr/local/sbin, /usr/local/lib) and nothing else there. Kept in
# /var/lib/frognet-install/ (FrogNet's; --uninstall removes it).
mkdir -p /var/lib/frognet-install
sort -u "$_NODE_LIST" > /var/lib/frognet-install/files
rm -f "$_NODE_LIST"
log "  install record: $(wc -l < /var/lib/frognet-install/files) files in /var/lib/frognet-install/files"
chmod +x /usr/local/bin/* 2>/dev/null || true
chmod +x /usr/local/sbin/* 2>/dev/null || true

# [INSTALLER_DIR_LANDS_V1] Put the release tooling onto the target.
#
# frognet_build_release.sh:509 excludes usr/local/bin/installer from the world
# tar ON PURPOSE -- bundling the installer inside the tar it extracts doubles the
# release size.  Instead frognet_install.sh and frognet_reset.sh ride in the
# RELEASE ROOT beside frognet_world.tgz (build :631).  But nothing ever copied
# them back out, so an installed node had no /usr/local/bin/installer at all:
#   * the node could not re-run its own installer or reset;
#   * _RESET_SRC (:279) resolves against SCRIPT_DIR, so a reinstall only worked
#     while the release directory still existed on the box;
#   * test_clean_install_oracle's "frognet_reset.sh ships" / "found" checks fail,
#     and [RESET_RELEASE_SYMMETRY_V1] cannot diff reset against the release.
#
# They are copied from SCRIPT_DIR (the release root we are running out of), AFTER
# the world tar extracts -- the tar does not contain this directory, so there is
# nothing to overwrite and nothing to be overwritten by.
_INST_DIR="/usr/local/bin/installer"
mkdir -p "$_INST_DIR"
for _rel in frognet_install.sh frognet_reset.sh; do
    # [NO_SELF_INSTALL_V1] Under --from-repo, SCRIPT_DIR can already BE the
    # destination (the release layout puts these at usr/local/bin/installer/, which
    # is exactly where they are being installed to). `install` from a file onto
    # itself truncates it.
    if [[ "$(readlink -f "${SCRIPT_DIR}/${_rel}")" == "$(readlink -f "${_INST_DIR}/${_rel}")" ]]; then
        log "  ${_INST_DIR}/${_rel} is the source - already in place"
    elif [[ -f "${SCRIPT_DIR}/${_rel}" ]]; then
        install -m 0755 "${SCRIPT_DIR}/${_rel}" "${_INST_DIR}/${_rel}"
        log "  installed ${_INST_DIR}/${_rel}"
    elif [[ -f "${_INST_DIR}/${_rel}" ]]; then
        # [CHECKOUT_IS_A_FIRST_NODE_V1] Under --from-repo, C1 copied usr/ wholesale
        # a few lines above, so this file is ALREADY at its destination even though
        # it is not in the checkout root. The old branch warned "not installed"
        # about a file that was installed - observed on BrokerHost 2026-08-30 for
        # frognet_reset.sh. Check the destination before reporting a miss; a log
        # that cries wolf is worse than a silent one.
        log "  ${_INST_DIR}/${_rel} already in place (laid down by C1)"
    else
        die "${_rel} is in neither ${SCRIPT_DIR}/ nor ${_INST_DIR}/ - this node would have no way to reset or reinstall itself"
    fi
done
# [PYCACHE_PURGE_V1] tar restores .py with their OLD mtimes; a leftover .pyc from a
# prior install can be newer and get used instead of the fresh source (stale bytecode
# -> import errors, e.g. the proxy.cache circular import). Purge so Python recompiles.
log "Clearing stale Python bytecode under /opt/frognet_semantic"
find /opt/frognet_semantic -type d -name __pycache__ -prune -exec rm -rf {} +
find /opt/frognet_semantic -type f -name '*.pyc' -delete
# [VENV_SURVIVES_RESET_V1] put the carried venv back before C2 looks for it.
if [[ -d "${_VENV_STASH:-/nonexistent}" ]]; then
    mkdir -p /opt/frognet_semantic
    mv "$_VENV_STASH" /opt/frognet_semantic/venv
    log "Restored carried venv (C2 will arch-check it)"
fi
log "Extraction complete"
# [BASE_BUILD_V1 - T4b] The FrogNet base's release id: the tree's opt/frognet_semantic/BUILD (build=, the tree's one
# tag), plus, on this machine, the git commit it was installed from (when it was a git checkout) and when. The
# proxy, the data daemon and the tunnel daemon say it in their first log line.
BASE_BUILD_FILE=/opt/frognet_semantic/BUILD
BASE_TAG="$(sed -n 's/^build=//p' "$BASE_BUILD_FILE" 2>/dev/null | head -1)"
[[ -n "$BASE_TAG" ]] || die "C1: $BASE_BUILD_FILE has no build= line (the tree carries it): this is not a release tree"
BASE_COMMIT=""
if (( FROM_REPO )) && git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    BASE_COMMIT="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse --short=12 HEAD)"
    [[ -n "$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]] \
        && BASE_COMMIT="${BASE_COMMIT}+changes"
fi
{ grep -vE '^(commit|installed)=' "$BASE_BUILD_FILE"
  [[ -n "$BASE_COMMIT" ]] && echo "commit=$BASE_COMMIT"
  echo "installed=$(date -u +%Y-%m-%dT%H:%MZ)"; } > "$BASE_BUILD_FILE.tmp" && mv -f "$BASE_BUILD_FILE.tmp" "$BASE_BUILD_FILE" \
    || die "C1: cannot write $BASE_BUILD_FILE"
chmod 644 "$BASE_BUILD_FILE"
log "FrogNet base: build=$BASE_TAG ${BASE_COMMIT:+commit=$BASE_COMMIT}${BASE_COMMIT:-(no git checkout: no commit recorded)}"

phase "C1a: sysctl (the tree's 90-frognet-forwarding.conf)"
# [TREE_FILES_ARE_THE_FILES_V1 - T4a] Forwarding and rp_filter live in the tree's own
# etc/sysctl.d/90-frognet-forwarding.conf (a separate file, so frognet-system-tune.sh can
# regenerate 99-frognet.conf without losing them); C1 laid it down. Apply THAT file.
_SYSCTL=/etc/sysctl.d/90-frognet-forwarding.conf
[[ -f "$_SYSCTL" ]] || die "$_SYSCTL missing after C1 - the payload has no forwarding settings"
sysctl -p "$_SYSCTL" || die "sysctl -p $_SYSCTL failed"
log "sysctl applied from $_SYSCTL (ip_forward=$(sysctl -n net.ipv4.ip_forward))"

# [NODE_GUID_V1] Allocate the node's permanent identity GUID once, at install.
# /etc/fnid is outside /etc/frognet so a reinstall that wipes /etc/frognet does
# NOT whack it. Generate-if-absent; never regenerated; mode 0444. The tunnel
# daemon announces it in the region (facts.guid); rinab's machine id reads it.
# Identity is essential: fail LOUDLY if it can't be allocated (no masking).
[[ -x /usr/local/bin/frognet-node-guid.sh ]] || die "frognet-node-guid.sh missing - cannot allocate node identity"
/usr/local/bin/frognet-node-guid.sh --ensure
NODE_GUID="$(/usr/local/bin/frognet-node-guid.sh)"
log "Node GUID at /etc/fnid: $NODE_GUID"

phase "C1b: DB secret injection"
###############################################################################
# [INSTALL_DB_PASS_PROMPT_V1] Write the ONE collected password into every
# secret-bearing file, so MySQL (set from it in D8) and every reader agree.
#
# This array MUST stay byte-identical to SECRET_INJECT_FILES in
# frognet_build_release.sh — that script names itself the single source of truth
# for which files carry the password, and its post-build scanner keys off the
# same list. If a file is added there, add it here in the same commit.
#
# Handles both shapes a shipped file can arrive in:
#   - tokenized  __FROGNET_DB_PASS__   (release built with --scramble)
#   - a literal  previous password     (unscrambled build, or an older secret)
# Replacement is format-aware and value-agnostic: we rewrite the field, so we do
# not need to know what the old value was.
###############################################################################
# [ONE_CREDENTIAL_ONE_SOURCE_V1 - John 2026-09-12] Three of the five entries
# here were Python modules carrying their own copy of the password as a
# module-level default, kept in agreement with the other two ONLY by this
# injection running over all five in the same pass. It works exactly once. A
# module restored from a build tar, a partial reinstall, or a unit started
# without FROGNET_DB_PASS leaves a stale literal, and the node then sprays
#   [Warning] Access denied for user 'FrogUser'@'localhost' (using password: YES)
# at its own auth log for as long as the service retries. Two of them shipped
# a real, working pond password as their shipped default.
#
# Those three now read core/db_credentials, which reads DB_CONFIG.json at call
# time. They hold no secret, so they are not injection sites and must NOT be
# listed: a listed file that yields zero matches is a FATAL injection failure.
# Two files carry the credential, and they are the two a human edits.
SECRET_INJECT_FILES=(
    var/www/html/config.php
    opt/frognet_semantic/DB_CONFIG.json
)

_inject_rc=0
for _rel in "${SECRET_INJECT_FILES[@]}"; do
    if [[ ! -f "/${_rel}" ]]; then
        warn "  secret file not present, skipping: /${_rel}"
        continue
    fi
    # Secret passed by ENV, never argv (argv is world-readable via ps).
    FN_SECRET="$DB_PASS" python3 - "/${_rel}" <<'PY' || _inject_rc=1
import json, os, re, sys

path = sys.argv[1]
secret = os.environ["FN_SECRET"]
src = open(path, encoding="utf-8", errors="surrogateescape").read()
out, hits = src, 0

def php_lit(s):      # single-quoted PHP: only \ and ' are special
    return s.replace("\\", "\\\\").replace("'", "\\'")

# 1) PHP:  define('DB_PASS', '<value>')
out, n = re.subn(r"(define\(\s*'DB_PASS'\s*,\s*')(?:[^'\\]|\\.)*('\s*\))",
                 lambda m: m.group(1) + php_lit(secret) + m.group(2), out)
hits += n

# 2) JSON / python dict:  "password": "<value>"
out, n = re.subn(r'("password"\s*:\s*)"(?:[^"\\]|\\.)*"',
                 lambda m: m.group(1) + json.dumps(secret), out)
hits += n

# 3) python env-default:  os.environ.get("FROGNET_DB_PASS", "<value>")
out, n = re.subn(r'(os\.environ\.get\(\s*["\']FROGNET_DB_PASS["\']\s*,\s*)'
                 r'(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
                 lambda m: m.group(1) + json.dumps(secret), out)
hits += n

# 4) catch-all: any surviving build token
if "__FROGNET_DB_PASS__" in out:
    out = out.replace("__FROGNET_DB_PASS__", secret)
    hits += 1

if hits == 0:
    print(f"    NO-SECRET-FIELD {path}", file=sys.stderr)
    sys.exit(2)

if out != src:
    open(path, "w", encoding="utf-8", errors="surrogateescape").write(out)

# Validate the two structured formats so a bad rewrite fails HERE, not at runtime.
if path.endswith(".json"):
    json.load(open(path, encoding="utf-8", errors="surrogateescape"))
print(f"    injected ({hits} site(s)) {path}")
PY
done
[[ $_inject_rc -eq 0 ]] || die "DB secret injection failed — refusing to continue with mismatched credentials"

# Leak check: no shipped secret file may still hold the build token.
#
# [LEAK_SCAN_NO_TREE_WALK_V1] Scan EXACTLY the files we injected -- not a
# recursive grep over /opt, /var/www, /usr/local/bin. `grep -r` over live
# directories blocks FOREVER the moment it meets a FIFO, socket, or device node
# (a running service's named pipe, a stray unix socket), which presents exactly
# as "the installer printed the last inject line and then hung with no error and
# no prompt." SECRET_INJECT_FILES is the authoritative set of files that ever
# carry the secret, so checking those by name is both correct and bounded.
_leaks=""
for _rel in "${SECRET_INJECT_FILES[@]}"; do
    _f="/${_rel}"
    [[ -f "$_f" ]] || continue
    if grep -q "__FROGNET_DB_PASS__" "$_f" 2>/dev/null; then
        _leaks+="    $_f"$'\n'
    fi
done
if [[ -n "$_leaks" ]]; then
    warn "files still holding the token after injection:"
    printf '%s' "$_leaks" >&2
    die "__FROGNET_DB_PASS__ still present after injection - injection failed for the above"
fi
log "DB secret injected into ${#SECRET_INJECT_FILES[@]} declared file(s)"

phase "C1c: Upgrade (what the tree no longer ships)"
###############################################################################
# Runs on EVERY install, --preserve included, after C1 laid the tree down (it
# brings frognet_prune_removed.sh and conf.sh). [PRUNE_ALL_ON_UPGRADE_V1 - T4c]
# The WHOLE list, every section: the old tunnel.conf keys (RINA_RAM / RINA_AREA
# too: join.env is the one switch), the retired config files and their backups,
# the retired units, their sentinels and state, every file T1..T4b deleted from
# usr/local/bin, usr/local/sbin, etc/systemd/system, var/www/html and
# opt/frognet_semantic, and the old system-pip console scripts whose module is
# not also an OS package. No path in it is in the tree. Each removal is logged.
# --sweep-code: also every .py/.sh/.bash/.php under /opt/frognet_semantic that C1's install record does not
# list -- code from older tarballs that no list can name [CODE_IS_THE_TREE_V1].
# A failure is logged and the install goes on; the next install (or a manual run
# of the same command) retries it.
###############################################################################
if /usr/local/bin/frognet_prune_removed.sh --apply --sweep-code; then
    log "C1c: retired state removed"
else
    warn "C1c: some retired state could not be removed (listed above); the next install retries it"
fi

phase "C2: Python venv"
VENV_DIR="/opt/frognet_semantic/venv"
VENV_PYTHON="${VENV_DIR}/bin/python3"
SYS_ARCH="$(uname -m)"
REBUILD_VENV=0
if [[ -f "$VENV_PYTHON" ]]; then
    VENV_ARCH="$(file "$VENV_PYTHON" | grep -oE 'ARM|x86-64|aarch64|ARM aarch64' | head -1 || true)"
    SYS_IS_ARM=0; SYS_IS_X86=0; VEN_IS_ARM=0; VEN_IS_X86=0
    [[ "$SYS_ARCH" =~ aarch64|arm ]] && SYS_IS_ARM=1 || SYS_IS_X86=1
    [[ "${VENV_ARCH,,}" =~ arm|aarch64 ]] && VEN_IS_ARM=1 || VEN_IS_X86=1
    [[ $SYS_IS_ARM -ne $VEN_IS_ARM ]] && REBUILD_VENV=1
else
    REBUILD_VENV=1
fi
_ntot=${#PIP_PACKAGES[@]}
if [[ $REBUILD_VENV -eq 1 ]]; then
    log "Rebuilding venv for $SYS_ARCH ($_ntot packages; ARM wheels may compile, minutes each)"
    log "  Removing old venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
    log "  Creating venv (python3 -m venv)..."
    python3 -m venv "$VENV_DIR" || die "python3 -m venv $VENV_DIR failed (python3-venv is in A2)"
    log "  Upgrading pip in venv..."
    "${VENV_DIR}/bin/pip3" install --upgrade pip || die "pip upgrade in the venv failed"
else
    log "Venv arch matches ($VENV_DIR); checking its $_ntot packages"
fi
# [SERVICES_ON_THE_VENV_V1] Every install (an upgrade too) makes sure each listed package is in the venv: a package
# added to the list reaches an existing venv, which "arch matches - using as-is" used to skip.
VP="${VENV_DIR}/bin/pip3"
VFAIL=0
VFAILED=()
_i=0
for pkg in "${PIP_PACKAGES[@]}"; do
    _i=$((_i+1))
    if "$VP" show "$pkg" >/dev/null 2>&1; then
        log "  [$_i/$_ntot] $pkg already present - skipping"; continue
    fi
    log "  [$_i/$_ntot] pip install $pkg ..."
    "$VP" install --progress-bar off "$pkg" && log "  [$_i/$_ntot] OK: $pkg" \
        || { warn "  [$_i/$_ntot] FAILED: $pkg"; VFAIL=$((VFAIL+1)); VFAILED+=( "$pkg" ); }
done
# [A_BROKEN_VENV_IS_NOT_A_WARNING_V1] This warned and walked on. Observed
# 2026-08-29: "Venv: 7 of 23 package(s) failed" scrolled past and the install
# continued into C3 and the whole D block. Every package here is imported by
# something that runs on the node -- lz4 and msgpack by the wire codec,
# mysql-connector by the cache, NetfilterQueue by the proxy data plane,
# cryptography by the tunnel daemon. A venv missing seven of them is a node
# whose services cannot import, and the operator has no reason to think so:
# the phase banner for C3 prints two lines later and says PASSED.
#
# Names the failures, because "7 failed" does not tell you whether it was
# PyYAML or NetfilterQueue.
if (( VFAIL > 0 )); then
    die "venv build failed for $VFAIL of $_ntot package(s): ${VFAILED[*]} - these are imported by the proxy, daemon and tunnel code; a node with them missing cannot start its services. Fix the build failure (usually a missing -dev header or no ARM wheel) and re-run."
fi
log "Venv complete ($_ntot packages)"
systemctl daemon-reload

phase "C3: Pre-activation simulator gate (real code on real hardware)"
# RC-1 GATE. Before any FrogNet merge/proxy/daemon service is enabled (phase F1),
# run the simulator against the REAL code just installed on THIS box. The
# simulator drives the actual ported discovery/orchestration - same code
# that will run live - through byte-exact oracle proofs and the proof plane's
# shapes and churn. This is the last gate before
# the real code goes active.
#
# DEFAULT: ON. The ONLY way to skip is an explicit FROGNET_INSTALL_SKIP_SIM=1,
# and skipping is logged loudly. If the simulator does not pass ALL tests, the
# installation TERMINATES here (die) and nothing is activated.
DISC_PY="${VENV_DIR}/bin/python3"   # [SERVICES_ON_THE_VENV_V1] the interpreter the services run on, and only it
[[ -x "$DISC_PY" ]] || die "C3: $DISC_PY missing (C2 builds it)"
SIM_LOG="/var/log/frognet_preactivation_sim.log"
if [[ "${FROGNET_INSTALL_SKIP_SIM:-0}" == "1" ]]; then
    warn "  FROGNET_INSTALL_SKIP_SIM=1 - SKIPPING the pre-activation simulator gate."
    warn "  The real code will be activated WITHOUT proof on this box. Not recommended."
else
    log "  Running pre-activation simulator on real code (log: $SIM_LOG) ..."
    : > "$SIM_LOG"
    _sim_ok=1
    # (1) full proof suite (oracle byte-exact + proof_plane shapes/churn)
    # PYTHONUTF8/PYTHONIOENCODING: the sim writes to a redirected file, so Python
    # derives stdout encoding from the install-time locale. Under C/POSIX that is
    # latin-1/ascii and a single non-ASCII char (e.g. an em-dash in a diagnostic)
    # raises UnicodeEncodeError, kills the sim, and this gate dies - a false
    # activation-block that has nothing to do with the code under test. Force UTF-8.
    if ( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic \
            PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
            "$DISC_PY" -m discovery selfcheck ) >>"$SIM_LOG" 2>&1; then
        log "  [PASS] discovery selfcheck (all suites)"
    else
        log "  [FAIL] discovery selfcheck (see $SIM_LOG)"; _sim_ok=0
    fi
    # (2) [NO_LIFECYCLE_AT_INSTALL_V1] end-to-end lifecycle: NOT RUN HERE.
    #
    # The lifecycle harness drives the REAL merge, and the real merge reads the
    # REAL box -- /etc/hosts, /etc/sentinels, /etc/frognet.  At install time that
    # state belongs to the PREVIOUS install: it is neither the old network nor
    # the new one, and the installer is in the middle of replacing it.  The
    # harness picks those up and validates a 3-node fabric against next hops
    # from a network that no longer exists, so it fails on the box's history
    # rather than on the code under test.  Verified: the identical scenario
    # passes on a machine with no prior FrogNet and fails on one that has one.
    #
    # This is a DEVELOPMENT gate.  Run it pre-build, on a clean tree, where the
    # fabric is the only state there is:
    #     cd /opt/frognet_semantic && PYTHONPATH=. python3 -m discovery.sim.lifecycle
    #
    # Do not reinstate it here without making the harness hermetic first, which
    # an installer cannot guarantee -- the environment is dynamic by definition.
    log "  [SKIP] discovery lifecycle E2E (development gate; not valid mid-install)"
    if (( _sim_ok != 1 )); then
        die "Pre-activation simulator FAILED - refusing to activate the FrogNet merge \
on this box. The installed code did not pass all tests. Inspect $SIM_LOG, fix, and \
re-run the installer. (Override only with FROGNET_INSTALL_SKIP_SIM=1, at your own risk.)"
    fi
    log "  Pre-activation simulator gate PASSED - real code cleared for activation."
fi
chmod +x /usr/local/bin/runMerge.bash 2>/dev/null || true

###############################################################################
#       PHASE D: CLEAN + CONFIGURE (post-tar - tar may have overwritten)
###############################################################################

phase "D2: Clean stale state"
systemctl stop dnsmasq 2>/dev/null || true
kill -9 $(pgrep dnsmasq) 2>/dev/null || true
systemctl stop frognet-proxy frognet-daemon frognet-merge-watcher 2>/dev/null || true
systemctl stop apache2 2>/dev/null || true
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true
systemctl mask systemd-resolved 2>/dev/null || true
rm -f /etc/dnsmasq.d/opts_only.conf /etc/dnsmasq.d/frognet_forwarders_auto.conf /etc/dnsmasq.d/frognet_databasehost.conf
rm -f /etc/sentinels/{frognet_hosts,frognet_hosts_out,frognet_resolv,frog_resolv.conf}
rm -f /etc/sentinels/{expected_routes,transit_upstream_seeds,mergePending,runAgain,forwarded,new_resolv.conf,getFrogNet.cache}
rm -f /etc/frognet/gateways.conf /etc/database_ip /var/lib/misc/dnsmasq.leases
rm -f /etc/resolv.conf; echo "nameserver 127.0.0.1" > /etc/resolv.conf
log "Stale state cleaned"

phase "D3: Interface overrides"
cat > /etc/frognet/interfaces_override.conf <<IFCONF
# AUTO-GENERATED by frognet_install.sh on $(date)
eth0Name="${ETH0_IF}"
wlan0Name="${WLAN0_IF}"
wlan1Name="${WLAN1_IF}"
IFCONF
echo "$ETH0_IF" > /etc/frognet/transit_exclude_interfaces
[[ -f /etc/frognet/transit.conf ]] || printf 'TRANSIT_MODE=auto\nTRANSIT_BAUD=full\n' > /etc/frognet/transit.conf
[[ -f /etc/frognet/semantic_hosts ]] || touch /etc/frognet/semantic_hosts
[[ -f /etc/frognet/simulator_underlay ]] || touch /etc/frognet/simulator_underlay
[[ -f /etc/frognet/sem_cache_secret ]] || { openssl rand -hex 32 > /etc/frognet/sem_cache_secret; chmod 600 /etc/frognet/sem_cache_secret; }
# [WIFI_PSK_REQUIRED_V1] wifi.env used to be seeded with FROGNET_WIFI_PSK=CHANGEME.
# frognet-netstart then died at FIRST BOOT with "FROGNET_WIFI_PSK not set ... cannot
# build WPA2 hostapd.conf" - the AP never came up, so the identity interface never
# got carrier, so identity preflight failed and runMerge aborted. A placeholder that
# guarantees a later fatal is worse than no file. The real value is obtained in E1
# (existing wifi.env, --wifi-psk, or prompt); here we only ensure the non-secret
# defaults exist.
if [[ ! -f /etc/frognet/wifi.env ]]; then
    printf 'SIGNAL_FLOOR=50\nUSE_BOTH_INTERFACES=1\n' > /etc/frognet/wifi.env
    chmod 600 /etc/frognet/wifi.env
fi
log "Interface overrides written"

phase "D4: MariaDB"
for cnf in /etc/mysql/mariadb.conf.d/provider_*.cnf; do
    [[ -f "$cnf" ]] || continue
    pn=$(basename "$cnf" .cnf)
    [[ ! -f "/usr/lib/mysql/plugin/${pn}.so" ]] && { log "Removing stale: $cnf"; rm -f "$cnf"; }
done
touch /var/log/mysql/slow_query.log
chown mysql:mysql /var/log/mysql /var/log/mysql/slow_query.log 2>/dev/null || true
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
POOL_MB=$((TOTAL_MB / 2))
(( POOL_MB > 1024 )) && POOL_MB=1024
(( POOL_MB < 128 )) && POOL_MB=128
# [TREE_FILES_ARE_THE_FILES_V1 - T4a] The FrogNet MariaDB settings are the tree's own
# etc/mysql/mariadb.conf.d/99-frognet.cnf and 99-frognet-perf.cnf (C1 laid them down);
# this phase used to write its own copy of 99-frognet.cnf over the first. It only sizes
# the buffer pool for this machine's RAM, in place, in every file that sets it.
[[ -f /etc/mysql/mariadb.conf.d/99-frognet.cnf ]] || die "/etc/mysql/mariadb.conf.d/99-frognet.cnf missing after C1 - the payload has no MariaDB settings"
for f in $(grep -rl 'innodb_buffer_pool_size' /etc/mysql/ 2>/dev/null); do
    sed -i "s/innodb_buffer_pool_size\s*=.*/innodb_buffer_pool_size = ${POOL_MB}M/" "$f"
done
for f in $(grep -rl 'innodb_buffer_pool_size_max' /etc/mysql/ 2>/dev/null); do
    sed -i "s/innodb_buffer_pool_size_max\s*=.*/innodb_buffer_pool_size_max = ${POOL_MB}M/" "$f"
done
systemctl enable mariadb; systemctl start mariadb
for i in $(seq 1 15); do mysql -u root -e "SELECT 1" >/dev/null 2>&1 && break || sleep 1; done
mysql -u root <<'SQL'
CREATE DATABASE IF NOT EXISTS `FrogNet` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
SQL
log "MariaDB configured (pool=${POOL_MB}M, RAM=${TOTAL_MB}M)"

phase "D5: Apache"
INSTALLED_PHP=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || true)
for mod in rewrite proxy proxy_http proxy_fcgi setenvif headers ssl deflate status; do
    a2enmod "$mod" 2>/dev/null || true
done
if [[ -n "$INSTALLED_PHP" ]]; then
    for mod in /etc/apache2/mods-enabled/php*.load; do
        [[ -f "$mod" ]] || continue
        mn=$(basename "$mod" .load)
        [[ "$mn" != "php${INSTALLED_PHP}" ]] && a2dismod "$mn" 2>/dev/null || true
    done
    a2enmod "php${INSTALLED_PHP}" 2>/dev/null || true
    for fc in /etc/apache2/conf-enabled/php*-fpm.conf; do
        [[ -f "$fc" ]] || continue
        a2disconf "$(basename "$fc" .conf)" 2>/dev/null || true
    done
    log "PHP $INSTALLED_PHP (mod_php, all FPM disabled)"
fi
cat > /etc/apache2/ports.conf <<'PORTS'
Listen 8080
<IfModule ssl_module>
    Listen 8443
</IfModule>
<IfModule mod_gnutls.c>
    Listen 8443
</IfModule>
PORTS
# [APACHE_LOG_TO_JOURNAL_V1] Stop apache writing under /var/log/apache2.
#
# /var/log is tmpfs on these nodes, nothing ships a tmpfiles.d entry to recreate
# the directory, and the world tar carries no /var/log at all -- so the only
# thing that ever creates /var/log/apache2 is B3's mkdir in this script, and a
# reboot wipes it. apache2 then refuses to start, which takes api.php,
# getHosts.php and frognet_echo.php down with it.
#
# Logging to the journal removes the dependency on any directory existing.
# Fixing the vhosts alone is not enough: Debian's apache2.conf carries its own
# global ErrorLog, and other-vhosts-access-log.conf a global CustomLog, and
# both reference ${APACHE_LOG_DIR} independently of anything written here.
if [[ -f /etc/apache2/apache2.conf ]]; then
    cp -p /etc/apache2/apache2.conf "/etc/apache2/apache2.conf.bak.$(date +%Y%m%d-%H%M%S)"
    sed -i 's|^ErrorLog .*|ErrorLog syslog:local1|' /etc/apache2/apache2.conf
    grep -q '^ErrorLog syslog:local1' /etc/apache2/apache2.conf \
        || echo 'ErrorLog syslog:local1' >> /etc/apache2/apache2.conf
fi
a2disconf other-vhosts-access-log 2>/dev/null || true

a2dissite 000-default 2>/dev/null || true
rm -f /etc/apache2/sites-enabled/000-default.conf
# [TREE_FILES_ARE_THE_FILES_V1 - T4a] The four vhosts are the tree's own files in
# etc/apache2/sites-available (C1 laid them down); this phase used to write its own copies
# over them. (They log to the journal, [APACHE_LOG_TO_JOURNAL_V1].) admin-site went in T5: it served
# /var/www/www_admin on *:80, a port Apache does not listen on (the proxy owns 80); an upgraded node loses it in C1c.
for _v in 000-default databasehost frognet-ssl; do
    [[ -f "/etc/apache2/sites-available/${_v}.conf" ]] || die "/etc/apache2/sites-available/${_v}.conf missing after C1"
done
a2ensite 000-default databasehost frognet-ssl
# [ONE_MONITOR_TRUTH_V1 - T5] the tree's sudoers fragment (C1 laid it down): www-data reads the kernel's WireGuard
# handshake times for getSnapshot.php's hub link. sudo wants it root-owned and not group/other-writable; 0440 as B4's.
chmod 0440 /etc/sudoers.d/frognet-snapshot || die "/etc/sudoers.d/frognet-snapshot missing after C1"
visudo -cf /etc/sudoers.d/frognet-snapshot >/dev/null || die "/etc/sudoers.d/frognet-snapshot is invalid"
systemctl enable apache2
log "Apache configured (not started - SSL certs come later)"

phase "D6: dnsmasq skeleton"
# [DNS_NEXTHOP_ONLY_V1] No hardcoded upstream. dnsmasq forwards to the next hop
# on the default route and nobody else -- that upstream file is maintained by
# manageResolv (next hop = field 2 of exit_host.tsv) and re-read on SIGHUP.
#
# resolv-file points dnsmasq at the manageResolv-maintained upstream file and
# nothing else. no-resolv is NOT set AND must not be -- with resolv-file present,
# no-resolv would disable file reading entirely and leave dnsmasq with zero
# upstreams. The file may be empty (no upstream -> no external DNS, FrogNet names
# still resolve locally). Belkin sink and local domain stay; strict-order keeps
# file order.
touch /etc/sentinels/dnsmasq_upstream.conf
cat > /etc/dnsmasq.d/upstream_fallback.conf <<'DNSCONF'
resolv-file=/etc/sentinels/dnsmasq_upstream.conf
strict-order
address=/heartbeat.belkin.com/0.0.0.0
DNSCONF
# [DNSMASQ_USE_EXISTING_TRACKER_V1] Point dnsmasq at the dhcp_tracking.bash that
# already ships in the world tar, instead of writing a lesser trigger by heredoc.
# dhcp_tracking.bash fires on add AND del, filters to the FrogNet plane (10/8
# less the reserved 10.253.* transit and 10.254.* chorus planes) so non-FrogNet
# DHCP churn does not spuriously merge, and logs via debugTag -- all
# of which the old inline "add-only, no filter" script dropped. It is a shipped
# node tool, so it stays in step with the rest of the tree; the heredoc drifted.
# dhcp_tracking.sh is only a one-line exec shim onto the .bash, so either path
# works; the .bash is canonical.
cat > /etc/dnsmasq.d/frognet_dhcp_event.conf <<'DNSCONF'
dhcp-script=/usr/local/bin/dhcp_tracking.bash
DNSCONF
# Clean up the heredoc trigger from any prior install so two scripts don't linger.
rm -f /usr/local/sbin/dnsmasq_merge_trigger.sh 2>/dev/null || true
# [DNSMASQ_NO_UNBOUND_V1] Do NOT write forward_to_unbound.conf.
#
# It pointed dnsmasq at a local unbound on 127.0.0.1#5353, but nothing in the
# install ever brings unbound UP (it is not installed; one already on the machine is
# stopped below). So this file forwarded DNS to a dead
# resolver. On the normal path it was merely overridden by upstream_fallback.conf
# through alphabetical read order (both set server=, dnsmasq merges them); on any
# node where the ordering or the fallback differed it would black-hole DNS.
# Remove it here and sweep any copy a prior install left behind.
rm -f /etc/dnsmasq.d/forward_to_unbound.conf 2>/dev/null || true

cat > /etc/systemd/system/dnsmasq.service.d/override.conf <<'UNIT'
[Unit]
# [UNIT_AFTER_IS_ONE_LINE_V1] After= takes its whole list on ONE line. Split
# across two, systemd rejects the orphan line with "Missing '=', ignoring line"
# on every daemon-reload, and the dependency silently does not exist.
Wants=network-online.target
After=network-online.target NetworkManager-wait-online.service

[Service]
Restart=on-failure
RestartSec=2
UNIT
# [UNBOUND_ONE_STEP_V1] FrogNet does not install unbound (A2 used to, only for this phase to stop it). One that
# is already on the machine would hold :53 against dnsmasq: it is stopped and disabled, here, once (the record A0b
# made puts it back on --uninstall). (This was `fix_unbound_bypass.sh || systemctl stop unbound`; the script also
# restarted dnsmasq, which this phase must not do.)
if systemctl cat unbound.service >/dev/null 2>&1; then
    systemctl disable --now unbound && log "unbound was on this machine: stopped and disabled (dnsmasq serves :53)" \
        || warn "systemctl disable --now unbound failed: dnsmasq will not get :53 while it runs"
fi
systemctl enable dnsmasq
# DO NOT start dnsmasq - no IP on interface yet. setup_lillypad handles it.
log "dnsmasq skeleton configured"

phase "D7: NetworkManager"
cat > /etc/NetworkManager/NetworkManager.conf <<NMCONF
[main]
plugins=ifupdown,keyfile
dns=none

[ifupdown]
managed=false

[device]
wifi.scan-rand-mac-address=no
NMCONF
cat > /etc/NetworkManager/conf.d/no-resolved.conf <<'EOF'
[main]
dns=none
EOF
# Mark the served LAN interface unmanaged (D1 has refused the uplink, so this can
# never be the default-route device).
cat > /etc/NetworkManager/conf.d/10-frognet-unmanaged.conf <<NMCONF
[keyfile]
unmanaged-devices=interface-name:${ETH0_IF}
NMCONF
cat > /etc/NetworkManager/conf.d/99-frognet-unmanaged.conf <<NMCONF
[keyfile]
unmanaged-devices=interface-name:${ETH0_IF}
NMCONF
cat > /etc/NetworkManager/conf.d/99-wifi-powersave.conf <<'NMCONF'
[connection]
wifi.powersave = 2
NMCONF
# [TREE_FILES_ARE_THE_FILES_V1 - T4a] The merge hook is the tree's own
# etc/NetworkManager/dispatcher.d/90-frognet-merge (C1 laid it down): it merges only on a
# real routing delta and goes through the merge watcher's accumulator. This phase used to
# write an older copy over it (the unconditional `runMerge.bash &`), plus a second hook,
# 99-ifup, that ran runMerge again on the same event - two merges per interface event.
# One hook now; any 99-ifup a prior install left is removed.
_HOOK=/etc/NetworkManager/dispatcher.d/90-frognet-merge
[[ -f "$_HOOK" ]] || die "$_HOOK missing after C1 - the payload has no merge hook"
chmod 755 "$_HOOK"
rm -f /etc/NetworkManager/dispatcher.d/99-ifup
log "  merge hook: $_HOOK (the tree's); 99-ifup removed"
# [NO_RESTART_BEFORE_REBOOT_V1] Do NOT restart NetworkManager here. The install
# ends in an unconditional reboot (systemctl reboot), so a restart buys nothing -
# and restarting NM drops the operator's SSH session mid-install, which is how a
# remote run gets truncated. Enable it; the dispatcher hooks and config written
# above take effect cleanly on the reboot.
systemctl enable NetworkManager 2>/dev/null || true
log "NetworkManager configured (applies on reboot; not restarted to preserve SSH)"

phase "D8: Schema + data + FrogUser"
# [PRESERVE_IS_AN_UPGRADE_V1] Three paths, and they differ in exactly what they are
# allowed to destroy:
#
#   --preserve  UPGRADE IN PLACE. Preserve means preserve. Create_Database.sql DROPs
#               tables, so it does NOT run; the shipped dump is NOT restored over the
#               live rows. Only schema_fixups.sql runs - that is how a schema change
#               reaches an existing database without touching its data.
#   reset/clean The databases are empty. Create_Database.sql builds the structure,
#               schema_fixups.sql migrates it, then the dump restores the production
#               data - which is DESIRABLE: it primes the template store on the new
#               node instead of making it learn every template from cold.
#
# Order matters: structure, then migrations, then data.
_SCHEMA="/var/www/html/Create_Database.sql"
_FIXUPS="/var/www/html/schema_fixups.sql"

if [[ "$PRESERVE" -eq 1 ]]; then
    log "--preserve: keeping existing data - no schema rebuild, no dump restore"
else
    [[ -f "$_SCHEMA" ]] || die "$_SCHEMA missing - cannot build the FrogNet schema"
    log "Loading schema ($_SCHEMA)..."
    mysql -u root FrogNet < "$_SCHEMA"
    log "Schema loaded"
fi

[[ -f "$_FIXUPS" ]] || die "$_FIXUPS missing - cannot migrate the schema"
log "Applying schema migrations ($_FIXUPS)..."
mysql -u root FrogNet < "$_FIXUPS"
log "Schema migrations applied"

if [[ "$PRESERVE" -eq 1 ]]; then
    log "--preserve: live data left in place"
elif (( FROM_REPO )); then
    # [A_CHECKOUT_MAKES_A_NEW_NODE_V1] /frognet_db.sql is `mysqldump FrogNet` from
    # the build host - the whole live database. It exists so a RELEASE can clone a
    # working node. A CHECKOUT makes a NEW node, which correctly starts with the
    # ten empty tables Create_Database.sql just created (User, Team, TeamMember,
    # Message, KnownFrogNet, Sensor, IAHost, Actuator, WellKnownSite, SensorData -
    # all operational, no reference rows; the schema contains zero INSERTs).
    #
    # So there is no dump to restore and there must not be one: a public
    # repository cannot carry a pond's users, messages and host table. This is not
    # a fallback for a missing file - it is the other input's correct behaviour,
    # the same way C1 copies instead of unpacking.
    log "--from-repo: new node, tables start empty (no database dump - a checkout is not a node clone)"
else
    [[ -f /frognet_db.sql ]] || die "/frognet_db.sql missing - the release is incomplete"
    log "Restoring FrogNet data..."
    # [DB_RESTORE_BULK_V1] Streaming a dump straight into mysql runs EVERY INSERT as
    # its own transaction, and each commit fsyncs (innodb_flush_log_at_trx_commit=1
    # is the default and 99-frognet.cnf does not change it). These nodes measure
    # fsync_ms 9.6 / 138 / 1245 in their own capability probes - at 1245ms that is
    # under one row per second. So for the duration of the load only: one transaction
    # instead of N, no per-row unique/FK checks (the dump is internally consistent),
    # and log flushing relaxed. Durability is restored immediately afterward; a failed
    # restore is simply re-run. Pairs with [DUMP_BULK_V1] on the build side.
    _FLUSH_PREV="$(mysql -u root -N -B -e 'SELECT @@innodb_flush_log_at_trx_commit')"
    mysql -u root -e "SET GLOBAL innodb_flush_log_at_trx_commit=2"
    _t0=$(date +%s)
    {
        echo "SET autocommit=0;"
        echo "SET unique_checks=0;"
        echo "SET foreign_key_checks=0;"
        cat /frognet_db.sql
        echo "COMMIT;"
    } | mysql -u root FrogNet
    _rc=$?
    mysql -u root -e "SET GLOBAL innodb_flush_log_at_trx_commit=${_FLUSH_PREV}"
    (( _rc == 0 )) || die "Database restore FAILED (rc=$_rc) - durability restored, re-run the install"
    log "Data restored in $(( $(date +%s) - _t0 ))s"
fi

CONFIG_PHP="/var/www/html/config.php"
[[ -f "$CONFIG_PHP" ]] || die "config.php not found"
# [INSTALL_DB_PASS_PROMPT_V1] DB_PASS is the operator-supplied value collected at
# startup and already written into config.php (and every other secret-bearing
# file) by phase C1b -- we no longer scrape it back out of config.php, which is
# what let MySQL and DB_CONFIG.json drift apart. Cross-check that C1b landed.
[[ -n "$DB_PASS" ]] || die "internal: DB_PASS unset at D8"
grep -Fq "define('DB_PASS'" "$CONFIG_PHP" \
    || die "config.php has no DB_PASS define - C1b could not have injected it"
# Escape for a single-quoted SQL literal: backslash first, then quote.
DB_PASS_SQL="${DB_PASS//\\/\\\\}"
DB_PASS_SQL="${DB_PASS_SQL//\'/\'\'}"
[[ -n "$DB_PASS" ]] || die "Could not extract DB_PASS from $CONFIG_PHP"
log "Setting FrogUser password..."
mysql -u root <<SQL
DROP USER IF EXISTS 'FrogUser'@'localhost';
DROP USER IF EXISTS 'FrogUser'@'%';
CREATE USER 'FrogUser'@'localhost' IDENTIFIED BY '${DB_PASS_SQL}';
CREATE USER 'FrogUser'@'%'         IDENTIFIED BY '${DB_PASS_SQL}';
GRANT ALL PRIVILEGES ON \`FrogNet\`.* TO 'FrogUser'@'localhost';
GRANT ALL PRIVILEGES ON \`FrogNet\`.* TO 'FrogUser'@'%';
FLUSH PRIVILEGES;
SQL
# MYSQL_PWD keeps the secret out of argv (visible in ps) on the verify.
MYSQL_PWD="$DB_PASS" mysql -u FrogUser FrogNet -e "SELECT 1" >/dev/null 2>&1 \
    && log "FrogUser auth verified" || die "FrogUser auth failed"

# Prove the OTHER readers agree, not just config.php -- this is the exact pairing
# that failed in production: MySQL accepted config.php's value while
# core/store.py read a different one out of DB_CONFIG.json.
if [[ -f /opt/frognet_semantic/DB_CONFIG.json ]]; then
    FN_SECRET="$DB_PASS" python3 -c 'import json,os,sys; sys.exit(0 if json.load(open("/opt/frognet_semantic/DB_CONFIG.json")).get("password")==os.environ["FN_SECRET"] else 1)' \
        || die "DB_CONFIG.json password does not match the installed grant"
    log "DB_CONFIG.json password matches the installed grant"
else
    warn "DB_CONFIG.json absent - core/store.py will fail to start"
fi

###############################################################################
#                  PHASE E: IDENTITY + PROVISION
###############################################################################

phase "E1: Node identity"

# Use args if supplied; otherwise prompt.
if [[ -n "$ARG_NAME" ]]; then
    NODE_NAME="$ARG_NAME"
    log "Using --name: $NODE_NAME"
else
    [[ -t 0 ]] || die "E1: no node name (--name not given$( ((PRESERVE)) && echo ', and --preserve found none in /etc/dnsmasq.d/opts_only.conf' )) and no terminal to ask on"
    while true; do
        read -rp "  Node name: " NODE_NAME; NODE_NAME="$(echo "$NODE_NAME" | xargs)"
        [[ -n "$NODE_NAME" ]] && break; echo "  Name cannot be empty."
    done
fi

if [[ -n "$ARG_IP" ]]; then
    NODE_IP="$ARG_IP"
    log "Using --ip: $NODE_IP"
else
    [[ -t 0 ]] || die "E1: no gateway IP (--ip not given) and no terminal to ask on"
    while true; do
        read -rp "  Gateway IP (10.X.Y.1, this node's /24): " NODE_IP; NODE_IP="$(echo "$NODE_IP" | xargs)"
        [[ "$NODE_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || { echo "  Must look like 10.X.Y.1"; continue; }
        break
    done
fi

# Conflict check only meaningful when we have a default route - i.e. when
# something else might already be answering on this IP.
if ip route show default | grep -q via 2>/dev/null; then
    if ping -c 1 -W 2 "$NODE_IP" >/dev/null 2>&1; then
        if [[ -n "$ARG_IP" ]]; then
            warn "  $NODE_IP responded to ping - proceeding anyway (--ip was given)"
        else
            echo; echo "  WARNING: $NODE_IP responded to ping."
            read -rp "  Continue? [y/N] " PO; [[ "$PO" =~ ^[Yy]$ ]] || die "Aborted"
        fi
    fi
fi

# ----------------------------------------------------------------------------
# SSID projection decision.
#
# If the user did not pass --ssid-projection / --no-ssid-projection on the
# command line, decide based on hardware:
#   - eth0 has a carrier (cable plugged in)  -> leave default (1); netstart
#     will pick wired mode regardless.  No prompt.
#   - eth0 has no carrier                    -> scan every wl* interface for
#     AP-mode capability.  No AP-capable WiFi -> force off.  One candidate ->
#     prompt Y/N to project.  Multiple -> prompt Y/N, then prompt which
#     interface (external antenna on wlan1 vs onboard wlan0 etc.).
#
# Then, if projection is on, prompt for the regulatory country code (or
# accept --country=).  Country is required: the radio refuses to TX, or
# TX's at reduced power, until reg domain is set.
#
# AP-mode capability check works on any Linux box, not just Pi: it reads
# the driver's advertised nl80211 interface modes via `iw phy info`.
# ----------------------------------------------------------------------------
_eth0_candidate() {
    for dev in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == lo || "$dev" == veth* || "$dev" == docker* || "$dev" == br-* || "$dev" == virbr* || "$dev" == wl* || "$dev" == wg* ]] && continue
        echo "$dev"; return 0
    done
    echo "eth0"
}
_wlan_list() {
    ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1 | grep -E '^wl' | sort
}
_wlan_supports_ap() {
    local wl="$1"
    [[ -z "$wl" ]] && return 1
    command -v iw >/dev/null 2>&1 || return 2
    local phy
    phy="$(iw dev "$wl" info 2>/dev/null | awk '/wiphy/{print "phy"$2; exit}')"
    [[ -z "$phy" ]] && return 1
    iw phy "$phy" info 2>/dev/null \
        | awk '/Supported interface modes/{f=1;next} /^\s*$/{f=0} f && /^\s*\* AP\s*$/{found=1} END{exit !found}'
}

if [[ "$SSID_PROJECTION_EXPLICIT" -ne 1 ]]; then
    PROBE_ETH0="$(_eth0_candidate)"
    CARRIER=0
    [[ -r "/sys/class/net/${PROBE_ETH0}/carrier" ]] \
        && CARRIER="$(cat "/sys/class/net/${PROBE_ETH0}/carrier" 2>/dev/null || echo 0)"
    if [[ "$CARRIER" == "1" ]]; then
        log "eth0 ($PROBE_ETH0) has carrier - SSID projection stays on (netstart picks wired)"
    else
        # Build list of AP-capable wl* interfaces
        AP_CANDIDATES=()
        for wl in $(_wlan_list); do
            if _wlan_supports_ap "$wl"; then
                AP_CANDIDATES+=("$wl")
            fi
        done
        if [[ ${#AP_CANDIDATES[@]} -eq 0 ]]; then
            log "No AP-capable WiFi device found - disabling SSID projection"
            warn "  Realtek USB dongles and some laptop chipsets cannot run in AP mode."
            warn "  Pi onboard chips and ath9k/Intel chips do support AP."
            SSID_PROJECTION=0
        else
            echo
            echo "  No ethernet cable detected on $PROBE_ETH0."
            echo "  AP-capable WiFi interface(s): ${AP_CANDIDATES[*]}"
            echo "  Project the node SSID '$NODE_NAME' as a WiFi AP?"
            echo "  (Clients can then join wirelessly and route through this node.)"
            read -rp "  Enable WiFi SSID projection? [Y/n] " WANT_AP
            if [[ "$WANT_AP" =~ ^[Nn]$ ]]; then
                SSID_PROJECTION=0
            else
                SSID_PROJECTION=1
                # If only one AP-capable iface, use it.  If multiple and
                # --ap-iface wasn't given, prompt.
                if [[ -z "$AP_IFACE" ]]; then
                    if [[ ${#AP_CANDIDATES[@]} -eq 1 ]]; then
                        AP_IFACE="${AP_CANDIDATES[0]}"
                    else
                        echo
                        echo "  Multiple AP-capable WiFi interfaces:"
                        idx=1
                        for c in "${AP_CANDIDATES[@]}"; do
                            printf "    %d) %s\n" "$idx" "$c"
                            idx=$((idx+1))
                        done
                        read -rp "  Choose AP interface [1-${#AP_CANDIDATES[@]}, default 1]: " AP_CHOICE
                        AP_CHOICE="${AP_CHOICE:-1}"
                        if [[ "$AP_CHOICE" =~ ^[0-9]+$ ]] && \
                           [[ $AP_CHOICE -ge 1 && $AP_CHOICE -le ${#AP_CANDIDATES[@]} ]]; then
                            AP_IFACE="${AP_CANDIDATES[$((AP_CHOICE-1))]}"
                        else
                            AP_IFACE="${AP_CANDIDATES[0]}"
                        fi
                    fi
                fi
                # Country code
                if [[ -z "$WIFI_COUNTRY" ]]; then
                    read -rp "  WiFi country code (ISO 2-letter) [US]: " WIFI_COUNTRY
                    WIFI_COUNTRY="${WIFI_COUNTRY:-US}"
                fi
                WIFI_COUNTRY="${WIFI_COUNTRY^^}"
                [[ "$WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]] || die "Invalid country code: $WIFI_COUNTRY"
            fi
        fi
    fi
fi

# Even if SSID_PROJECTION came from --ssid-projection / explicit, provide
# defaults so the conf and template substitution don't see empty values.
[[ -z "$AP_IFACE" ]] && AP_IFACE="wlan0"
[[ -z "$WIFI_COUNTRY" ]] && WIFI_COUNTRY="US"
log "SSID_PROJECTION=$SSID_PROJECTION  AP_IFACE=$AP_IFACE  WIFI_COUNTRY=$WIFI_COUNTRY"
# [WIFI_PSK_REQUIRED_V1] Obtain the WPA2 passphrase NOW, while a human is here to
# answer, instead of letting frognet-netstart discover it is missing at first boot.
# Order: what the machine already has, then what was passed, then ask. No default,
# no placeholder - if the AP is going to be projected it needs a real key, and a node
# that cannot raise its AP cannot bear its identity and cannot merge.
# It is POND-SHARED: every node in a pond must use the SAME passphrase or clients
# roaming between them re-authenticate against a different key.
if [[ "$SSID_PROJECTION" -eq 1 ]]; then
    if [[ -z "$WIFI_PSK" && -f /etc/frognet/wifi.env ]]; then
        _existing="$(sed -n 's/^FROGNET_WIFI_PSK=//p' /etc/frognet/wifi.env | head -1)"
        if [[ -n "$_existing" && "$_existing" != "CHANGEME" ]]; then
            WIFI_PSK="$_existing"
            log "  WiFi PSK: taken from the existing /etc/frognet/wifi.env"
        fi
    fi
    while [[ ${#WIFI_PSK} -lt 8 || ${#WIFI_PSK} -gt 63 ]]; do
        [[ -n "$WIFI_PSK" ]] && warn "  WPA2 passphrase must be 8-63 characters (got ${#WIFI_PSK})"
        echo
        echo "  The node projects SSID '$NODE_NAME' as a WiFi AP."
        echo "  Every node in this pond must share ONE passphrase."
        read -rsp "  WiFi passphrase (8-63 chars): " WIFI_PSK; echo
    done
    sed -i '/^FROGNET_WIFI_PSK=/d' /etc/frognet/wifi.env
    printf 'FROGNET_WIFI_PSK=%s\n' "$WIFI_PSK" >> /etc/frognet/wifi.env
    chmod 600 /etc/frognet/wifi.env
    log "  WiFi PSK written to /etc/frognet/wifi.env (mode 600)"
fi


echo
echo "  +------------------------------------+"
printf "  |  Name:      %-23s|\n" "$NODE_NAME"
printf "  |  IP:        %-23s|\n" "$NODE_IP"
printf "  |  Interface: %-23s|\n" "$ETH0_IF"
printf "  |  SSID proj: %-23s|\n" "$([[ $SSID_PROJECTION -eq 1 ]] && echo enabled || echo disabled)"
echo  "  +------------------------------------+"
echo
# Skip confirmation if everything came from args (non-interactive).
if [[ -n "$ARG_NAME" && -n "$ARG_IP" ]]; then
    log "Non-interactive identity: skipping confirm prompt"
else
    read -rp "  Confirm? [y/N] " CONFIRM; [[ "$CONFIRM" =~ ^[Yy]$ ]] || die "Aborted"
fi
log "Identity: $NODE_NAME $NODE_IP $ETH0_IF"

phase "E2: Hostname"
FQDN="FrogNetHost.${NODE_NAME}"
# [NO_RESTART_BEFORE_REBOOT_V1] Write /etc/hostname; do NOT `hostnamectl
# set-hostname`. hostnamectl sets the hostname live over D-Bus, which emits a
# hostname-changed signal that NetworkManager reacts to by re-evaluating the
# primary connection -- dropping the operator's SSH mid-install. The install
# reboots at the end, and /etc/hostname is read on boot, so the file write is all
# that is needed. This is the second SSH-drop the operator hit (the first being
# the NetworkManager restart), and it lands right after the interactive E1 prompt
# -- which is why it looks like the "Node Identity step" disconnects.
echo "$FQDN" > /etc/hostname
# Set the transient (live) hostname WITHOUT touching the static/D-Bus path, so
# tooling that reads `hostname` in this session still works and NM is not poked.
hostname "$FQDN" 2>/dev/null || true
if grep -q "127.0.1.1" /etc/hosts; then sed -i "s/^127\.0\.1\.1.*/127.0.1.1   $FQDN/" /etc/hosts
else echo "127.0.1.1   $FQDN" >> /etc/hosts; fi
log "Hostname: $FQDN"

phase "E3: setup_lillypad (v4)"
SETUP="/usr/local/bin/setup_lillypad_v4.bash"
[[ -x "$SETUP" ]] || die "$SETUP not found"

# Gateway IP from E1 is what setup_lillypad_v4.bash expects as its
# 2nd positional arg.  Don't re-ask.
MACHINE_IP="$NODE_IP"
log "Using machine IP: $MACHINE_IP"

# [SETUP_LILLYPAD_NORESTART_V1] --norestart: setup_lillypad writes all config
# and the persistent NM connection profile but does not bounce services or
# re-address the live interface. The installer reboots at the end, so live
# activation is unnecessary -- and on a LAN-only gateway the operator's SSH
# rides the very interface setup_lillypad reconfigures, so activating it live
# drops the install. Everything applies cleanly on the reboot.
bash "$SETUP" "$NODE_NAME" "$MACHINE_IP" --norestart
log "setup_lillypad (v4) complete (config applied; interface + services come up on reboot)"
# No WireGuard key is made here: the tunnel daemon makes and owns
# /var/lib/frognet-tunnel/node_private.key when the node is on a region
# [DAEMON_OWNS_ITS_KEY_V1]. A LAN-only node needs none.

phase "E5: SSL certificates"
mkdir -p /etc/ssl/localCA
# [EACH_NODE_MAKES_ITS_CERT_V1 - T4a] The pair is made when the KEY is missing, not the
# certificate: a certificate without its key is of no use (Apache refuses to start on
# it). --from-repo used to ship etc/ssl/frognet-universal.crt without a key, so this
# check (on the .crt) skipped, and Apache had a cert and no key. Nothing ships either
# half now (the manifest's NEVER_SHIP); every node makes its own pair, here, once.
if [[ ! -f /etc/ssl/frognet-universal.key ]]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/ssl/frognet-universal.key -out /etc/ssl/frognet-universal.crt \
        -subj "/CN=frognethost.frognet/O=FrogNet/C=US" \
        -addext "subjectAltName=DNS:frognethost.frognet,DNS:*.frognet,DNS:FrogNetHost.${NODE_NAME}" 2>/dev/null
    chmod 600 /etc/ssl/frognet-universal.key
fi
if [[ ! -f /etc/ssl/localCA/databasehost.frognet.key ]]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/ssl/localCA/databasehost.frognet.key -out /etc/ssl/localCA/databasehost.frognet.crt \
        -subj "/CN=databasehost.frognet/O=FrogNet/C=US" 2>/dev/null
    chmod 600 /etc/ssl/localCA/databasehost.frognet.key
fi
# [NO_RESTART_BEFORE_REBOOT_V1] Apache comes up with the new SSL + vhosts on the
# reboot; no need to bounce it now. (It is enabled in the F phase.)
log "SSL + Apache configured (applies on reboot)"

phase "E6: iptables"
SETUP_IPT="/etc/setup_iptables"
[[ -f "$SETUP_IPT" ]] && { bash "$SETUP_IPT"; iptables-save > /etc/iptables/rules.v4; } || warn "setup_iptables not found"

phase "E6a: WiFi AP prerequisites"
# This phase prepares everything hostapd needs to bind on $AP_IFACE.
# Runs even when SSID_PROJECTION=0 - the artifacts are harmless when not
# in use, and if the user toggles projection on later via
# frognet-ssid-projection, they're already in place.

# 1) hostapd.conf.template - required by frognet-netstart:configure_hostapd.
TEMPLATE_SRC="/opt/frognet_semantic/internet_tunnels_v3/hostapd.conf.template"
TEMPLATE_DST="/etc/hostapd/hostapd.conf.template"
mkdir -p /etc/hostapd
# [ONE_TEMPLATE_V1] This used to fall through to an inline heredoc when
# TEMPLATE_SRC was absent -- and TEMPLATE_SRC WAS absent from the release, so the
# inline copy is what every install actually got. Two texts answering one
# question, and the one nobody maintains is the one that ran: a generic 2.4GHz
# hw_mode=g channel=6 template written under a log line that reads like success.
# The template now SHIPS at TEMPLATE_SRC and its absence is a broken release, not
# a branch. NOTE 2026-08-31: the file there is the AUTHORED template that had been
# sitting unused at /usr/local/bin/hostapd.conf.template - same four placeholders
# and same radio settings as the inline copy, plus the documentation of what each
# placeholder means and where it is read from. Extracting the heredoc had created a
# THIRD copy and made the least informative one canonical; there is now one.
if [[ -f "$TEMPLATE_DST" ]]; then
    log "  hostapd template already present at $TEMPLATE_DST"
else
    [[ -f "$TEMPLATE_SRC" ]] || die "hostapd.conf.template missing from the release at $TEMPLATE_SRC - frognet-netstart:configure_hostapd() has nothing to substitute into and SSID projection cannot come up"
    cp "$TEMPLATE_SRC" "$TEMPLATE_DST"
    log "  Installed $TEMPLATE_DST from release"
fi
chmod 644 "$TEMPLATE_DST"

# 2) Unblock rfkill - Pi images often ship with WiFi soft-blocked until
#    country code is set.
if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock wifi 2>/dev/null || true
    rfkill unblock all 2>/dev/null || true
    log "  rfkill unblocked"
fi

# 3) Regulatory domain - set both at kernel level (iw reg) and persistently
#    via wireless-regdb config.  Without this, the chip refuses many
#    channels and may TX at reduced power.
if command -v iw >/dev/null 2>&1; then
    iw reg set "$WIFI_COUNTRY" 2>&1 | sed 's/^/  iw: /' || true
    log "  iw reg set $WIFI_COUNTRY"
fi
# Pi: /boot/firmware/config.txt drives the boot-time country.  Don't
# touch the file if it's not Pi.
for CONFIG_TXT in /boot/firmware/config.txt /boot/config.txt; do
    [[ -f "$CONFIG_TXT" ]] || continue
    if grep -qE '^[[:space:]]*country=' "$CONFIG_TXT"; then
        sed -i "s|^[[:space:]]*country=.*|country=${WIFI_COUNTRY}|" "$CONFIG_TXT"
    else
        echo "country=${WIFI_COUNTRY}" >> "$CONFIG_TXT"
    fi
    log "  Recorded country=$WIFI_COUNTRY in $CONFIG_TXT"
    break
done
# wpa_supplicant.conf country (for station mode, separate from AP)
WPA_SUP=/etc/wpa_supplicant/wpa_supplicant.conf
if [[ -f "$WPA_SUP" ]] && ! grep -qE '^country=' "$WPA_SUP"; then
    sed -i "1i country=${WIFI_COUNTRY}" "$WPA_SUP" 2>/dev/null || true
fi

# 4) [AP_MANAGED_BY_NETSTART_V1] Whether NetworkManager manages $AP_IFACE is frognet-netstart's decision, made at
#    every start from the mode: wireless (projecting) -> its NM connections down, `managed no`, and its own
#    conf.d/99-frognet-wireless-ap.conf so the decision holds when NM starts after it ([AP_UNMANAGED_PERSISTS_V1]);
#    wired -> that file removed, `managed yes` (the radio may be this node's WiFi link into another LAN). The installer used to also write
#    conf.d/frognet-unmanaged-ap.conf (unmanaged for good, "belt-and-suspenders"), which contradicted netstart in
#    wired mode on every NetworkManager start. An upgrade removes that file (frognet_prune_removed.sh).

phase "E7: SSID projection (WiFi AP fallback)"
# When eth0 has no carrier, frognet-netstart + frognet-netmode normally bring
# wlan0 up as an AP and project the node SSID via hostapd.  This phase records
# the user's preference so the netstart logic can honor it on next boot.
#
# Switch is ON by default.  To disable: pass --no-ssid-projection to the
# installer, or set SSID_PROJECTION=0 in /etc/frognet/ssid_projection.conf
# at any time.
cat > /etc/frognet/ssid_projection.conf <<SSIDCONF
# /etc/frognet/ssid_projection.conf
#
# When SSID_PROJECTION=1 (default) and eth0 has no link, this node will
# project its node name as a WiFi SSID via hostapd on AP_IFACE.  Clients
# associating to the SSID get DHCP from dnsmasq and route through the
# node as if connected by ethernet.
#
# When SSID_PROJECTION=0, the node never starts hostapd, regardless of
# eth0 carrier state.  AP_IFACE (if present) is left under NetworkManager
# control for upstream/station use.
#
# AP_IFACE selects which WiFi device runs the AP - e.g. wlan0 for onboard
# Pi chip, wlan1 for an external USB adapter with a directional antenna.
# WIFI_COUNTRY is the ISO 3166-1 alpha-2 code used for the regulatory
# domain (and substituted into hostapd.conf at run time).
#
# Read by /usr/local/bin/frognet-netstart at boot and on link events.
SSID_PROJECTION=${SSID_PROJECTION}
AP_IFACE=${AP_IFACE}
WIFI_COUNTRY=${WIFI_COUNTRY}
SSIDCONF
chmod 644 /etc/frognet/ssid_projection.conf
log "SSID projection: $([[ $SSID_PROJECTION -eq 1 ]] && echo ENABLED || echo DISABLED)  iface=$AP_IFACE  country=$WIFI_COUNTRY"

# When disabled, mask hostapd so it cannot start even via the netstart
# fallback path.  When enabled, just unmask + leave it disabled (netstart
# starts/stops it on demand based on eth0 link).
if [[ $SSID_PROJECTION -eq 0 ]]; then
    systemctl mask hostapd 2>/dev/null || true
    log "  hostapd masked"
else
    systemctl unmask hostapd 2>/dev/null || true
    log "  hostapd unmasked (netstart will manage it)"
fi

# Apply the choice via the helper.  Side effect: writes the conf (idempotent
# with the cat above) and runs frognet-netstart so the running system
# reflects the choice immediately.  Non-fatal if it fails; reboot will
# resolve.
HELPER=/usr/local/bin/frognet_setup_v4_helper.bash
if [[ -x "$HELPER" ]]; then
    if [[ $SSID_PROJECTION -eq 1 ]]; then
        "$HELPER" ssid_projection_set on 2>&1 | sed 's/^/  helper: /' || warn "ssid_projection_set on failed"
    else
        "$HELPER" ssid_projection_set off 2>&1 | sed 's/^/  helper: /' || warn "ssid_projection_set off failed"
    fi
fi

phase "E8: Link watcher (ifplugd -> frognet-netstart)"
# ifplugd monitors the eth0 carrier and calls into /etc/ifplugd/action.d/
# on link up/down.  We drop a single action script that re-runs
# frognet-netstart so the box flips between wired (identity on eth0) and
# wireless (identity on wlan0, hostapd AP) modes automatically.
#
# Resolve the actual eth0 name now (D1 already ran) and write it into
# /etc/default/ifplugd so the daemon watches the right device on every
# boot.
IFPLUGD_DEV="${ETH0_IF:-eth0}"
mkdir -p /etc/ifplugd/action.d
cat > /etc/ifplugd/action.d/frognet <<'IFPACT'
#!/bin/bash
# Fired by ifplugd on link up/down for the watched interface.
# $1 = interface name, $2 = "up" or "down".
DEV="${1:-}"
STATE="${2:-}"
logger -t frognet-ifplugd "link event: dev=$DEV state=$STATE - running frognet-netstart"
exec /usr/local/bin/frognet-netstart
IFPACT
chmod +x /etc/ifplugd/action.d/frognet
log "  installed /etc/ifplugd/action.d/frognet"

if [[ -f /etc/default/ifplugd ]]; then
    # Replace INTERFACES= line; preserve everything else
    if grep -qE '^INTERFACES=' /etc/default/ifplugd; then
        sed -i "s|^INTERFACES=.*|INTERFACES=\"${IFPLUGD_DEV}\"|" /etc/default/ifplugd
    else
        echo "INTERFACES=\"${IFPLUGD_DEV}\"" >> /etc/default/ifplugd
    fi
    # Sensible debounce: 3s up-delay (filter PHY negotiation), 10s down-delay
    if grep -qE '^ARGS=' /etc/default/ifplugd; then
        sed -i 's|^ARGS=.*|ARGS="-q -f -u3 -d10 -w -I"|' /etc/default/ifplugd
    else
        echo 'ARGS="-q -f -u3 -d10 -w -I"' >> /etc/default/ifplugd
    fi
    log "  configured /etc/default/ifplugd to watch $IFPLUGD_DEV"
else
    warn "  /etc/default/ifplugd not present - ifplugd package may be missing"
fi
systemctl enable ifplugd 2>/dev/null || true

###############################################################################
#                        PHASE F: FINALIZE
###############################################################################

phase "F0: Cache nuke"
CACHE_NUKE="/usr/local/bin/frognet_cache_nuke.bash"
[[ -x "$CACHE_NUKE" ]] && { bash "$CACHE_NUKE"; log "Cache nuke complete"; } || warn "frognet_cache_nuke.bash not found"

phase "F1: Enable services"
systemctl daemon-reload

# Disable v2 tunnel daemon - keep on disk for rollback but don't run it
if [[ -f /etc/systemd/system/frognet-tunnel-daemon.service ]]; then
    systemctl stop frognet-tunnel-daemon 2>/dev/null || true
    systemctl disable frognet-tunnel-daemon 2>/dev/null || true
    log "  disabled: frognet-tunnel-daemon (v2)"
fi

CORE_SERVICES=(frognet-proxy frognet-daemon frognet-merge-watcher
    frognet-sysperf frognet-tunnel-daemon-v3)   # (frognet-eth0-fixup gone: [NETSTART_OWNS_THE_IDENTITY_V1])
# [NAT64_REMOVED_V1] frognet-nat64 dropped 2026-08-29. Its ExecStart,
# /usr/local/bin/start_nat64_dns64_jool.bash, is not in the tree and the only
# other mentions of nat64/jool anywhere were this line and the matching one in
# frognet_fixup.sh. It was ENABLED on a live node, so every boot enabled a unit
# whose script does not exist -- a permanently failing service that reads as
# FrogNet being broken. The capability is gone, not moved.
# [CONNECTIVITY_UI_REMOVED_V1] frognet-connectivity-ui and
# frognet-connectivity-watchdog dropped 2026-08-29. Neither unit existed --
# not in the tree and not on a live node -- so the installer enabled names that
# resolve to nothing. Their scripts (usr/local/sbin/frognet-connectivity-ui.py,
# a 707-line admin web UI on port 8877, and frognet_connectivity_watchdog.py)
# were removed with them: nothing referenced 8877, and the watchdog's only
# caller was propagateNotification.php, itself a superseded generation of an
# endpoint nothing calls.

OPT_SERVICES=(frognet-gps frognet-transit-boot)
# [NO_TRANSIT_WATCH_V1 - T6a fixes] frognet-transit-watch is gone: its script was a disabled copy of the old bash
# discovery (sync_interfaces.sh, `exit 0` at the top) under Restart=always, so systemd started it every 5 s for ever and
# it did nothing. Discovery is opt/frognet_semantic/discovery, run by the merge; transit links are frognet-transit-boot.
# [A_MISSING_UNIT_MUST_NEVER_LOOK_LIKE_AN_ANSWER_V1] MISSING used to be counted
# here and then never read again -- set to 0, incremented in the loop, and
# examined by nothing. A release built without units warned eight times and
# walked on into thirty more phases of configuration, so the failure surfaced at
# F6 as two unchecked boxes rather than as "this release has no services in it".
# Measured 2026-08-29: the release tarball contained zero FrogNet units and the
# install would have completed to the reboot prompt.
#
# A core unit absent from the release is not a warning. It means the release is
# incomplete, and every phase after this one configures a node that cannot run.
# Die here, naming all of them, before that work happens.
MISSING=()
for svc in "${CORE_SERVICES[@]}"; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && { systemctl enable "$svc" && log "  enabled: $svc"; } \
        || MISSING+=( "${svc}.service" )
done
if (( ${#MISSING[@]} )); then
    die "release is incomplete - ${#MISSING[@]} core unit(s) absent from /etc/systemd/system: ${MISSING[*]}. These ship in the world tar; a release built without them installs a node that boots with no proxy, no daemon and no tunnels. Rebuild the release and reinstall."
fi
for svc in "${OPT_SERVICES[@]}"; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && systemctl enable "$svc" 2>/dev/null || true
done
systemctl enable apache2 mariadb dnsmasq NetworkManager cron

# [ELECTOR_ON_LAN_ONLY_V1 - T6a fixes] databasehost, LAN-only: every merge publishes this node's capability to the
# control (core/role_publish.publish_all; the daemon's publish watcher also does at boot and when the control moves)
# and elects (discovery/live.py, SERVICE_ELECT in /var/log/frognet-merge-watcher.log). A neighbour's row can land in
# the control's store AFTER the control's merge elected without it (boot race, T6a G2). frognet-dbhost-elect.timer
# covers that: on the control only, 1/min for 10 min after each merge (the merge stamps /etc/sentinels/last_merge),
# it runs the same election over the rows now there. This installer never enabled it (only fdf-leave did); it does now,
# on a LAN-only node. A region node does not run it: there the tunnel daemon elects from the members' facts, the
# elector stands down (region_mode()), and fdf-region-join (phase R, or an earlier join kept in
# /opt/fdf/var/join.env) stops it; fdf-leave enables it again. The 60 s advertiser timer is gone: it wrote the same
# databasehost/capability row publish_all writes, and the election never ages a row.
if [[ -z "$REGION_RAM" ]] && ! grep -Eqs '^(export )?RINA_RAM=.+' /opt/fdf/var/join.env; then
    [[ -f /etc/systemd/system/frognet-dbhost-elect.timer ]] \
        || die "release is incomplete - /etc/systemd/system/frognet-dbhost-elect.timer is absent; it ships in the world tar"
    systemctl enable frognet-dbhost-elect.timer && log "  enabled: frognet-dbhost-elect.timer (LAN-only)"
else
    log "  frognet-dbhost-elect.timer: not enabled (region: the tunnel daemon elects)"
fi

phase "F2: Log management"
# [TREE_FILES_ARE_THE_FILES_V1 - T4a] /etc/logrotate.d/frognet is the tree's file (C1).
# The mysql-server rule this phase also wrote is gone: /var/log/mysql/*.log overlapped
# the distribution's own mariadb rule, and logrotate refuses a log named by two rules.
[[ -f /etc/logrotate.d/frognet ]] || die "/etc/logrotate.d/frognet missing after C1"
# (a prior install's copy is removed - only if no package owns that name)
if [[ -f /etc/logrotate.d/mysql-server ]] && ! dpkg -S /etc/logrotate.d/mysql-server >/dev/null 2>&1; then
    rm -f /etc/logrotate.d/mysql-server; log "  removed a prior install's /etc/logrotate.d/mysql-server"
fi
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/frognet.conf <<'J'
[Journal]
SystemMaxUse=200M
SystemKeepFree=500M
MaxFileSec=1week
MaxRetentionSec=2week
Compress=yes
J
# [NO_RESTART_BEFORE_REBOOT_V1] journald picks up this config on the reboot.
:  # systemd-journald not restarted

phase "F3: Crontab"
(
    crontab -l 2>/dev/null | grep -v 'startFrog\|registerFrogNetHost\|runMerge\|runBoth\|frognet-netstart' || true
    echo "@reboot /usr/local/bin/frognet-netstart"
    # [BOOT_MERGE_IS_THE_HOOK_V1] No "@reboot sleep 240 && runMerge.bash": the boot merge is the NetworkManager
    # hook's (90-frognet-merge keeps its route signature in /run, so the first interface event after a boot always
    # merges). The grep above drops the old line from an upgraded machine's crontab.
    echo "*/15 * * * * /usr/local/bin/registerFrogNetHost.bash"
) | sort -u | crontab -

phase "F4: froguser"
# [UNINSTALL_V1] recorded when the install makes it, so --uninstall removes only a froguser FrogNet made
if ! id froguser >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash froguser
    : > /var/lib/frognet-install/froguser-created
fi

phase "F5: Cleanup"
rm -f /frognet_db.sql

phase "F6: Verify"
FAIL=0
ok()  { echo "  +  $*"; }
nok() { echo "  x  $*" >&2; FAIL=$((FAIL+1)); }
chk() { local d="$1"; shift; "$@" >/dev/null 2>&1 && ok "$d" || nok "$d"; }
chk "hostname"               test "$(cat /etc/hostname 2>/dev/null)" = "$FQDN"
# [NO_RESTART_BEFORE_REBOOT_V1] This is an install-time (pre-reboot) verify, so it
# checks that services are ENABLED (will come up on boot), not is-active. Only
# mariadb runs during the install - D8 needs it - so only it is checked active.
# apache/dnsmasq/proxy/daemon are deliberately not started here (dnsmasq waits for
# an IP, per D6; proxy/daemon come up on the reboot); asserting is-active on them
# was only ever passing for apache because of a pre-reboot restart that dropped
# the operator's SSH. They are verified enabled instead.
chk "mariadb running"        systemctl is-active  --quiet mariadb
chk "apache2 enabled"        systemctl is-enabled --quiet apache2
# [F6_STARTS_WHAT_IT_CHECKS_V1 - T4a] The echo check below asks Apache, and D2 stopped
# Apache. It used to curl it stopped (and on port 80, which is frognet-proxy's, also not
# running before the reboot), so it could never pass. Start Apache now - it has its
# vhosts and both certificate pairs (E5) - and ask it on its own port, 8080. Starting a
# web server does not touch the network the operator's SSH rides on.
chk "apache2 started"        systemctl start apache2
chk "apache2 running"        systemctl is-active  --quiet apache2
chk "dnsmasq enabled"        systemctl is-enabled --quiet dnsmasq
chk "frognet-proxy enabled"  systemctl is-enabled --quiet frognet-proxy
chk "frognet-daemon enabled" systemctl is-enabled --quiet frognet-daemon
chk "resolved off"           test "$(systemctl is-active systemd-resolved 2>/dev/null)" != "active"
chk "FrogNet DB"             mysql -u root -e "USE FrogNet"
chk "FrogUser auth"          env MYSQL_PWD="${DB_PASS}" mysql -u FrogUser FrogNet -e "SELECT 1"
chk "interfaces_override"    test -f /etc/frognet/interfaces_override.conf
chk "ip_forward"             test "$(sysctl -n net.ipv4.ip_forward)" = "1"
chk "ssl cert"               test -f /etc/ssl/frognet-universal.crt
chk "sudoers"                test -f /etc/sudoers.d/frognet-setup
chk "sudoers valid"          visudo -cf /etc/sudoers.d/frognet-setup
chk "qrencode"               command -v qrencode
chk "merge controller present"  test -x /usr/local/bin/runMerge.bash
chk "ssid_projection.conf"   test -f /etc/frognet/ssid_projection.conf
chk "gateways.conf"          grep -q "$NODE_NAME" /etc/frognet/gateways.conf
chk "dnsmasq node"           grep -q "$NODE_NAME" /etc/dnsmasq.d/opts_only.conf
chk "proxy enabled"          systemctl is-enabled --quiet frognet-proxy
chk "daemon enabled"         systemctl is-enabled --quiet frognet-daemon
chk "merge hook (one)"       test -x /etc/NetworkManager/dispatcher.d/90-frognet-merge -a ! -e /etc/NetworkManager/dispatcher.d/99-ifup
chk "ssl key"                test -f /etc/ssl/frognet-universal.key
chk "LAN is not the uplink"  test "$ETH0_IF" != "${_UPLINK_DEV:-}"
# the echo is this node's line: "<name>,<.1>,<upstream>,<upstream>" - an empty 200 is a failure.
# [LAN_DEV_ONLY_V1 - T4c] The .1 field is getEth0Address: the LAN dev's own 10.x, nothing else.
# [ECHO_CHECK_ONE_RULE_V1 - 2026-10-14] F6 does not probe the interface to decide what to expect (that probe and
# getEth0Address can see the LAN at different moments: Seattle7 answered its .1 while the probe said "not addressed
# yet", and the check failed a correct echo). The echo is right when it names this node and its .1 is the one this
# install set, or is empty because NetworkManager addresses the LAN at the reboot. Any other .1 is a failure.
ECHO_R="$(curl -fsS --max-time 5 http://127.0.0.1:8080/frognet_echo.php 2>&1)"
_E1="$(printf '%s' "$ECHO_R" | cut -d, -f2)"
if [[ "$ECHO_R" == "${NODE_NAME},"* && ( "$_E1" == "$NODE_IP" || -z "$_E1" ) ]]; then
    if [[ -n "$_E1" ]]; then ok "echo: $ECHO_R"
    else ok "echo: $ECHO_R (LAN not addressed yet: frognet-netstart puts $NODE_IP on the LAN dev at the reboot)"; fi
else
    nok "echo: ${ECHO_R:-<empty>} (want ${NODE_NAME},${NODE_IP},... or ${NODE_NAME},,... before the reboot)"
fi
echo "  FrogNet base: $(grep -vE '^#' /opt/frognet_semantic/BUILD | tr '\n' ' ')"   # [BASE_BUILD_V1]
# [ONE_MONITOR_TRUTH_V1 - T5] the node snapshot (getSnapshot.php: what frognet_monitor and the dashboard draw) answers
SNAP_R="$(curl -fsS --max-time 10 http://127.0.0.1:8080/getSnapshot.php 2>&1)"
SNAP_S="$(printf '%s' "$SNAP_R" | python3 -c 'import json,sys
d=json.load(sys.stdin); assert d.get("snapshot")==1 and d.get("mode") in ("lan-only","region")
print("mode=%s name=%s build=%s members=%d errors=%s" % (d["mode"], d["node"]["name"], d["build"].get("build",""), len(d["members"]), d["errors"]))' 2>&1)" \
    && ok "snapshot: $SNAP_S" || nok "snapshot: ${SNAP_R:0:200} ($SNAP_S)"
echo
[[ $FAIL -eq 0 ]] && echo "  All checks passed." || echo "  $FAIL check(s) failed."

phase "F7: System tuning"
# [ONE_TUNE_SCRIPT_V1 - T4b] one file, the unit's name (frognet-system-tune.service runs the same one); the
# cmp-identical frognet_system_tune.sh is gone (an upgrade removes it: frognet_prune_removed.sh).
TUNE="/usr/local/bin/frognet-system-tune.sh"
[[ -x "$TUNE" ]] || die "F7: $TUNE missing after C1"
bash "$TUNE" || warn "F7: $TUNE failed (above); the node runs untuned until it is run again"

phase "R: Region ($( [[ -n "$REGION_RAM" ]] && echo "join $REGION_AREA at $REGION_RAM" || echo "none: LAN-only" ))"
# [REGION_FROM_THE_INSTALLER_V1 - T4a] With --region, this machine joins the region the
# one way any FrogNet node does: the FD&F node installer for its architecture
# (opt/fdf/dist/fdf-install-$(uname -m).run, checked before phase A), given the site key,
# the RAM manager's address and the area. It lays down /opt/fdf, runs fdf-region-join
# (site.env, join.env, fdf-client + fdf-facts, the tunnel daemon restarted into region
# mode, which makes its WireGuard key) and shows what happened. Without --region the
# node is LAN-only: no /opt/fdf, no WireGuard, FrogNet discovery and the election.
if [[ -z "$REGION_RAM" ]]; then
    log "LAN-only node (no --region): nothing to join"
elif (( FAIL > 0 )); then
    warn "F6 found $FAIL failure(s): NOT joining the region on a node that did not verify. Fix them and re-run the installer (with --preserve) and --region."
else
    log "Joining region: $FDF_RUN <key ${#REGION_KEY} chars> $REGION_RAM $REGION_AREA"
    bash "$FDF_RUN" "$REGION_KEY" "$REGION_RAM" "$REGION_AREA" \
        || die "the FD&F node installer failed (output above): FrogNet is installed, the join of $REGION_AREA did not complete. Re-run it when the cause is fixed (it is re-runnable): sudo bash $FDF_RUN <SITE KEY> $REGION_RAM $REGION_AREA   (or leave: /opt/fdf/bin/fdf-leave)"
    log "Region: joined $REGION_AREA at $REGION_RAM ($(sed -n 's/^build=//p' /opt/fdf/BUILD 2>/dev/null))"
fi

phase "F8: Reboot"
if [[ $FAIL -gt 0 ]]; then
    if (( NO_REBOOT )); then echo "  $FAIL check(s) failed - not rebooting (--no-reboot)."; exit 1; fi
    read -rp "  $FAIL failure(s). Reboot anyway? [y/N] " R
    [[ "$R" =~ ^[Yy]$ ]] || { echo "Reboot skipped."; exit 0; }
elif (( NO_REBOOT )); then
    echo "  $NODE_NAME ($NODE_IP) on $ETH0_IF installed. --no-reboot: reboot to start it."
    log "Install complete (--no-reboot)."; exit 0
else
    echo "  $NODE_NAME ($NODE_IP) on $ETH0_IF ready."
    echo "  Rebooting in 10s - Ctrl-C to cancel."; sleep 10
fi
log "Rebooting."; systemctl reboot
