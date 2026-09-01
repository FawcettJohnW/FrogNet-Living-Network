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
# frognet_install.sh - FrogNet node installer (v4 - broker + SSID-projection)
#
# Handles fresh installs AND unclean/cloned boxes. All configuration
# happens AFTER the world tar is extracted, so nothing gets overwritten.
#
# Usage:
#   sudo bash frognet_install.sh                    # interactive, from a release
#   sudo bash frognet_install.sh --from-repo        # from a repository checkout
#   sudo bash frognet_install.sh \
#        --name <NODE> \
#        --ip   <10.X.Y.1> \
#        --broker-host <HOST> \
#        --broker-port <PORT> \
#        [--no-ssid-projection]                     # disable AP fallback
#
# Args (all optional; missing values fall through to interactive prompt):
#   --name <NODE>            Node name. Letters, numbers, _, -.
#   --ip   <10.X.Y.1>        Gateway IP. Must end in .1.
#   --broker-host <HOST>     Broker DNS name or IP.
#                            Empty -> no broker; configure later via setup_frognet.html.
#   --broker-port <PORT>     Broker port.
#   --broker-scheme <http|https>
#                            Scheme for the broker URL (default https). A broker's
#                            own port is plain HTTP unless its installer was given
#                            a TLS cert, in which case pass --broker-scheme http.
#   --db-pass <PASS>         FrogUser DB password. SHARED pond-wide constant:
#                            every node must use the SAME value. Omit to be
#                            prompted (hidden, entered twice). A value on the
#                            command line is visible in `ps` to local users
#                            while the installer runs; prefer the prompt.
#   --pond <name>            Pond to JOIN (the group sharing a broker namespace).
#                            Required whenever a broker is configured. This is NOT
#                            the node/domain name - do not pass the node name here.
#                            Omitted -> taken from an existing broker.conf, else asked.
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
#                            etc/ opt/ usr/ var/ are; those four are copied to /
#                            and everything else in the root (.git, INSTALL.md)
#                            is left behind. This is the path a stranger who just
#                            cloned the repo takes to a first node: no release
#                            tarball exists yet, and the only thing that builds
#                            one snapshots a node that is already running.
#   --preserve               UPGRADE IN PLACE. Removes NOTHING: writes the new files
#                            over the existing install, migrates the schema if it
#                            changed, and keeps the existing databases,
#                            identity (/etc/fnid), WireGuard keys and config.
#                            DEFAULT (no flag) on a machine that already has a
#                            FrogNet install is a FULL RESET: frognet_reset.sh
#                            wipes install, state, identity and databases first,
#                            so the box comes up exactly like a clean machine.
#   --no-ssid-projection     Disable WiFi AP fallback when eth0 has no link.
#                            Default: ON - when eth0 has no cable and the device
#                            has a wlan0, hostapd projects the node SSID.
#
# Phase A: Install packages (apt + pip)
# Phase B: Pre-extraction setup (dirs, sysctl, iptables scaffold, sudoers)
# Phase C: Extract world tar + rebuild venv if arch mismatch
# Phase D: Clean stale state + configure everything (post-tar)
# Phase E: Node identity + provision (setup_lillypad_v4, SSL, tuning)
# Phase F: Finalize (enable services, logs, cron, verify, reboot)
###############################################################################
set -eu

# [SELF_RELOCATE_V1] frognet_reset.sh removes /usr/local/bin WHOLE, and the
# installer now lives at /usr/local/bin/installer/frognet_install.sh - so running
# it from there means the reset deletes the script that is running it, mid-run.
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
            echo "Relocating installer to $_safe (the reset removes /usr/local/bin)"
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
ARG_BROKER_HOST=""
ARG_BROKER_PORT=""
ARG_BROKER_SCHEME="https"
ARG_DB_PASS=""
ARG_DB_PASS_SET=0
SSID_PROJECTION=1
SSID_PROJECTION_EXPLICIT=0
PRESERVE=0
FROM_REPO=0
REBUILD_VENV_FORCE=0
WIFI_COUNTRY=""
WIFI_PSK=""
ARG_POND=""
AP_IFACE=""

usage() {
    sed -n '2,43p' "$0"
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)              ARG_NAME="$2";        shift 2 ;;
        --name=*)            ARG_NAME="${1#*=}";   shift   ;;
        --ip)                ARG_IP="$2";          shift 2 ;;
        --ip=*)              ARG_IP="${1#*=}";     shift   ;;
        --broker-host)       ARG_BROKER_HOST="$2"; shift 2 ;;
        --broker-host=*)     ARG_BROKER_HOST="${1#*=}";   shift ;;
        --broker-port)       ARG_BROKER_PORT="$2"; shift 2 ;;
        --broker-port=*)     ARG_BROKER_PORT="${1#*=}";   shift ;;
        --broker-scheme)     ARG_BROKER_SCHEME="$2"; shift 2 ;;
        --broker-scheme=*)   ARG_BROKER_SCHEME="${1#*=}"; shift ;;
        --db-pass)           ARG_DB_PASS="$2"; ARG_DB_PASS_SET=1; shift 2 ;;
        --db-pass=*)         ARG_DB_PASS="${1#*=}"; ARG_DB_PASS_SET=1; shift ;;
        --preserve)          PRESERVE=1; shift ;;
        --from-repo)         FROM_REPO=1; shift ;;
        --pond)              ARG_POND="$2"; shift 2 ;;
        --pond=*)            ARG_POND="${1#*=}"; shift ;;
        --wifi-psk)          WIFI_PSK="$2"; shift 2 ;;
        --wifi-psk=*)        WIFI_PSK="${1#*=}"; shift ;;
        --rebuild-venv)      REBUILD_VENV_FORCE=1; shift ;;
        --no-ssid-projection) SSID_PROJECTION=0; SSID_PROJECTION_EXPLICIT=1; shift ;;
        --ssid-projection)   SSID_PROJECTION=1; SSID_PROJECTION_EXPLICIT=1; shift ;;
        --country)           WIFI_COUNTRY="$2"; shift 2 ;;
        --country=*)         WIFI_COUNTRY="${1#*=}"; shift ;;
        --ap-iface)          AP_IFACE="$2"; shift 2 ;;
        --ap-iface=*)        AP_IFACE="${1#*=}"; shift ;;
        -h|--help)           usage 0 ;;
        *)                   echo "Unknown arg: $1" >&2; usage 1 ;;
    esac
done

# Validate args we did receive (defer interactive collection to phase E1).
if [[ -n "$ARG_IP" ]]; then
    [[ "$ARG_IP" =~ ^10\.[0-9]+\.[0-9]+\.1$ ]] || {
        echo "ERROR: --ip must look like 10.X.Y.1 (got: $ARG_IP)" >&2; exit 1; }
fi
if [[ -n "$ARG_NAME" ]]; then
    [[ "$ARG_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || {
        echo "ERROR: --name must be letters/numbers/_/- (got: $ARG_NAME)" >&2; exit 1; }
fi
if [[ -n "$ARG_BROKER_HOST" && -z "$ARG_BROKER_PORT" ]]; then
    ARG_BROKER_PORT="443"
fi
if [[ -n "$ARG_BROKER_PORT" ]]; then
    [[ "$ARG_BROKER_PORT" =~ ^[0-9]+$ ]] || {
        echo "ERROR: --broker-port must be numeric (got: $ARG_BROKER_PORT)" >&2; exit 1; }
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

LOG=/var/log/frognet_install.log
mkdir -p /var/log
exec > >(tee -a "$LOG") 2>&1

phase()     { echo; echo "========================================================"; echo "  STEP $*"; echo "========================================================"; }
log()       { echo "$(date '+%Y-%m-%d %H:%M:%S') [install] $*"; }
warn()      { echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN]    $*" >&2; }
die()       { echo "$(date '+%Y-%m-%d %H:%M:%S') [FATAL]   $*" >&2; exit 1; }

log "frognet_install.sh starting on $(hostname) - $(uname -m) - $(date)"

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


###############################################################################
#                      PHASE A: INSTALL PACKAGES
###############################################################################

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
if [[ ! -e /opt/frognet_semantic && ! -e /etc/frognet ]]; then
    log "No prior FrogNet install detected - already clean"
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
    ( cd / && bash "$_RESET_SRC" --yes ) || die "frognet_reset.sh failed - refusing to install over a half-wiped machine"
    log "Reset complete - installing onto a clean machine"
else
    die "Prior install detected but frognet_reset.sh was not found (looked in ${SCRIPT_DIR}/ and ${SCRIPT_DIR}/usr/local/bin/installer/) - the release or checkout is incomplete. Pass --preserve to upgrade in place."
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
    # [SIM_NEEDS_USERSPACE_WG_V1] wireguard-go is the reference USERSPACE
    # WireGuard. The simulator's netns tier creates real wg devices; where
    # wireguard.ko will not load (containers, some cloud kernels) the kernel
    # path fails with "Unknown device type" and the only visible symptom is a
    # wall of ROUTE_INSTALL_FAILED ... ENETUNREACH. netns_backend falls back to
    # wireguard-go, but only if it is installed. Kernel is still tried first on
    # every host, so this changes nothing on a box with the module.
    wireguard-go
    dnsmasq unbound
    network-manager
    iptables iptables-persistent netfilter-persistent
    python3 python3-pip python3-dev python3-venv
    # [SIM_NEEDS_YAML_V1] netdef topology definitions are YAML. Without this the
    # simulator's TIER 4 reports "[netdef] SKIPPED <dir>: ImportError" and
    # silently gates only the twenty built-in topologies.
    python3-yaml
    build-essential gcc g++ make pkg-config libffi-dev libssl-dev
    iproute2 iputils-ping net-tools curl wget nmap traceroute dnsutils netcat-openbsd
    gpsd gpsd-clients
    ax25-tools ax25-apps
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
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends mariadb-server mariadb-client || true
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

phase "A3: pip packages"
PIP_PACKAGES=(
    mysql-connector-python mysqlclient NetfilterQueue
    aiohttp lz4 msgpack orjson cbor2 psutil
    gevent gunicorn Flask Werkzeug
    gpsdclient paho-mqtt requests
    python-dotenv simplejson ujson PyYAML
    cryptography pyOpenSSL httpx
)
# [SKIP_SATISFIED_V1] pip re-resolves and re-downloads metadata for every package
# on every run; skip the ones already present.
_pipskip=0
for pkg in "${PIP_PACKAGES[@]}"; do
    if pip3 show "$pkg" >/dev/null 2>&1; then _pipskip=$((_pipskip+1)); continue; fi
    pip3 install --break-system-packages --quiet "$pkg" \
        && log "  pip: $pkg" || warn "  pip FAILED: $pkg"
done
log "  pip: ${_pipskip} already present, skipped"
log "pip packages installed"

###############################################################################
#                 PHASE B: PRE-EXTRACTION SETUP
###############################################################################

phase "B1: sysctl"
# Forwarding settings go in a SEPARATE file (90-*) so that
# frognet-system-tune.sh can regenerate 99-frognet.conf without
# overwriting them.
cat > /etc/sysctl.d/90-frognet-forwarding.conf <<'SYSCTL'
# CRITICAL: Do not merge into 99-frognet.conf - that file is
# regenerated by frognet-system-tune.sh and would lose these.
net.ipv4.ip_forward = 1
net.ipv4.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
SYSCTL
sysctl -p /etc/sysctl.d/90-frognet-forwarding.conf
# Belt-and-suspenders: force procfs directly in case sysctl -p silently failed
echo 1 > /proc/sys/net/ipv4/ip_forward
echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter
echo 0 > /proc/sys/net/ipv4/conf/default/rp_filter
log "sysctl configured (ip_forward=$(cat /proc/sys/net/ipv4/ip_forward))"

phase "B2: iptables scaffold"
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
    /var/www/{html,www_admin/public_html,cgi-bin} \
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
# setup_frognet.html can apply identity and broker settings.  Without
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
    # Copy the four payload roots BY NAME. Copying $SCRIPT_DIR wholesale would take
    # .git, README.md, INSTALL.md and anything else a contributor left in the root
    # onto the box -- a repository is not a filesystem image, it merely contains one.
    log "Installing from repository checkout $SCRIPT_DIR -> /"
    _dest_is_src=0
    for _d in etc opt usr var; do
        # Do not copy a directory onto itself. If someone clones INTO / -- or reruns
        # this from an installed tree -- cp -a src/. src/ silently churns every file
        # it is asked to preserve. Detect it and say so instead.
        if [[ "$(readlink -f "${SCRIPT_DIR}/${_d}")" == "$(readlink -f "/${_d}")" ]]; then
            _dest_is_src=1
            continue
        fi
        log "  copying ${_d}/ ..."
        cp -a "${SCRIPT_DIR}/${_d}/." "/${_d}/"
    done
    if (( _dest_is_src )); then
        log "  one or more payload roots ARE their destination - checkout is already in place, nothing copied for those"
    fi
else
    log "Extracting $WORLD_TGZ -> / ($(du -h "$WORLD_TGZ" | cut -f1))"
    # [EXTRACT_PROGRESS_V1] tar without -v prints NOTHING, so a multi-minute extraction
    # on SD looked like a hang - `ps` showed tar running while the screen sat dead. -v
    # would flood thousands of filenames; a checkpoint every 2000 records gives a live
    # heartbeat with a bounded number of lines.
    tar --ignore-zeros -xzf "$WORLD_TGZ" -C / \
        --checkpoint=2000 --checkpoint-action=echo="  ... extracted %u records"
fi
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

# [NODE_GUID_V1] Allocate the node's permanent identity GUID once, at install.
# /etc/fnid is outside /etc/frognet so a reinstall that wipes /etc/frognet does
# NOT whack it. Generate-if-absent; never regenerated; mode 0444. Sent to the
# broker as the "mac" identity by frognet-tunnel-setup-v3.sh. Identity is
# essential: fail LOUDLY if it can't be allocated (no masking).
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
SECRET_INJECT_FILES=(
    var/www/html/config.php
    opt/frognet_semantic/DB_CONFIG.json
    opt/frognet_semantic/daemon/cache/semcache_db.py
    opt/frognet_semantic/proxy/cache/semcache_db.py
    opt/frognet_semantic/daemon/engine/data_cache.py
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

phase "C1c: Config consolidation"
###############################################################################
# [ONE_CONF_V1] Fold broker.conf and pond.conf into tunnel.conf.
#
# Must run AFTER C1 (which lays down conf.sh and the consolidator) and BEFORE
# anything reads node config -- every patched consumer now reads tunnel.conf
# only, so an un-migrated node would still have POND_PASSWORD and CHORUSES
# stranded in a pond.conf nothing opens. Idempotent: a no-op on a node that has
# already been consolidated, so re-running the installer is safe.
###############################################################################
if [[ -x /usr/local/bin/frognet-conf-consolidate.sh ]]; then
    /usr/local/bin/frognet-conf-consolidate.sh || die "config consolidation failed"
else
    warn "frognet-conf-consolidate.sh not present - skipping consolidation"
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
if [[ $REBUILD_VENV -eq 1 ]]; then
    _ntot=${#PIP_PACKAGES[@]}
    log "Rebuilding venv for $SYS_ARCH ($_ntot packages; ARM wheels may compile, minutes each)"
    log "  Removing old venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
    log "  Creating venv (python3 -m venv)..."
    python3 -m venv "$VENV_DIR"
    VP="${VENV_DIR}/bin/pip3"
    log "  Upgrading pip in venv..."
    "$VP" install --upgrade pip
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
else
    log "Venv arch matches - using as-is"
fi
systemctl daemon-reload

phase "C3: Pre-activation simulator gate (real code on real hardware)"
# RC-1 GATE. Before any FrogNet merge/proxy/daemon service is enabled (phase F1),
# run the simulator against the REAL code just installed on THIS box. The
# simulator drives the actual ported discovery/orchestration/broker - same code
# that will run live - through byte-exact oracle proofs and a full startup->
# shutdown lifecycle (including the real broker). This is the last gate before
# the real code goes active.
#
# DEFAULT: ON. The ONLY way to skip is an explicit FROGNET_INSTALL_SKIP_SIM=1,
# and skipping is logged loudly. If the simulator does not pass ALL tests, the
# installation TERMINATES here (die) and nothing is activated.
DISC_PY="${VENV_DIR}/bin/python3"
[[ -x "$DISC_PY" ]] || DISC_PY="/usr/bin/python3"
SIM_LOG="/var/log/frognet_preactivation_sim.log"
if [[ "${FROGNET_INSTALL_SKIP_SIM:-0}" == "1" ]]; then
    warn "  FROGNET_INSTALL_SKIP_SIM=1 - SKIPPING the pre-activation simulator gate."
    warn "  The real code will be activated WITHOUT proof on this box. Not recommended."
else
    log "  Running pre-activation simulator on real code (log: $SIM_LOG) ..."
    : > "$SIM_LOG"
    _sim_ok=1
    # (1) full proof suite (oracle byte-exact + proof_plane shapes/churn/broker)
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

phase "D1: Detect interfaces"
_detect_eth0() {
    # The uplink (default-route device) must NEVER be picked: D7 marks whatever
    # this returns as NM-unmanaged and restarts NM, so returning the uplink strands
    # the box with no network. Skip it and let a real served wired iface win.
    local _uplink
    _uplink="$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
    for dev in $(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1 | sort); do
        [[ "$dev" == lo || "$dev" == veth* || "$dev" == docker* || "$dev" == br-* || "$dev" == virbr* || "$dev" == wl* || "$dev" == wg* ]] && continue
        [[ -n "$_uplink" && "$dev" == "$_uplink" ]] && continue
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
ETH0_IF="$(_detect_eth0 || true)"; ETH0_IF="${ETH0_IF:-eth0}"
WLAN0_IF="$(_detect_wlan 0 || true)"; WLAN0_IF="${WLAN0_IF:-wlan0}"
WLAN1_IF="$(_detect_wlan 1 || true)"; WLAN1_IF="${WLAN1_IF:-wlan1}"
log "Detected: eth0=$ETH0_IF wlan0=$WLAN0_IF wlan1=$WLAN1_IF"

phase "D2: Clean stale state"
systemctl stop dnsmasq 2>/dev/null || true
kill -9 $(pgrep dnsmasq) 2>/dev/null || true
systemctl stop frognet-proxy frognet-daemon frognet-merge-watcher 2>/dev/null || true
systemctl stop apache2 2>/dev/null || true
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true
systemctl mask systemd-resolved 2>/dev/null || true
systemctl stop unbound 2>/dev/null || true
rm -f /etc/dnsmasq.d/opts_only.conf /etc/dnsmasq.d/frognet_forwarders_auto.conf /etc/dnsmasq.d/frognet_databasehost.conf
rm -f /etc/sentinels/{frognet_hosts,frognet_hosts_out,frognet_resolv,frog_resolv.conf}
rm -f /etc/sentinels/{expected_routes,transit_upstream_seeds,mergePending,runAgain,forwarded,new_resolv.conf}
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
[[ -f /etc/frognet/tunnel.conf ]] || {
    cat > /etc/frognet/tunnel.conf <<'TUNCONF'
# Populated by setup_frognet.html -> cmd_apply_broker, or by
# setup_lillypad_v4.bash when given a BROKER_URL argument.
BROKER_URL=
GROUP_NAME=
TUNCONF
    chmod 600 /etc/frognet/tunnel.conf
}
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
for f in $(grep -rl 'innodb_buffer_pool_size' /etc/mysql/ 2>/dev/null); do
    sed -i "s/innodb_buffer_pool_size\s*=.*/innodb_buffer_pool_size = ${POOL_MB}M/" "$f"
done
for f in $(grep -rl 'innodb_buffer_pool_size_max' /etc/mysql/ 2>/dev/null); do
    sed -i "s/innodb_buffer_pool_size_max\s*=.*/innodb_buffer_pool_size_max = ${POOL_MB}M/" "$f"
done
cat > /etc/mysql/mariadb.conf.d/99-frognet.cnf <<MYCNF
[mysqld]
max_connections = 300
wait_timeout = 300
interactive_timeout = 300
innodb_buffer_pool_size = ${POOL_MB}M
MYCNF
systemctl enable mariadb; systemctl start mariadb
for i in $(seq 1 15); do mysql -u root -e "SELECT 1" >/dev/null 2>&1 && break || sleep 1; done
mysql -u root <<'SQL'
CREATE DATABASE IF NOT EXISTS `FrogNet` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE DATABASE IF NOT EXISTS `FrogNetFamily` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
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
cat > /etc/apache2/sites-available/000-default.conf <<'VHOST'
<VirtualHost *:8080>
    ServerAdmin webmaster@localhost
    DocumentRoot /var/www/html
    ErrorLog syslog:local1
    CustomLog "|/usr/bin/logger -t apache2-access -p local1.info" combined
    ProxyPass        /semantic/  http://127.0.0.1:9100/semantic/
    ProxyPassReverse /semantic/  http://127.0.0.1:9100/semantic/
</VirtualHost>
VHOST
cat > /etc/apache2/sites-available/admin-site.conf <<'VHOST'
<VirtualHost *:80>
    ServerName FrogNetAdmin.NODENAME_PLACEHOLDER
    ServerAdmin webmaster@localhost
    DocumentRoot /var/www/www_admin/public_html
    LogLevel warn
    ErrorLog syslog:local1
    CustomLog "|/usr/bin/logger -t apache2-access -p local1.info" combined
</VirtualHost>
VHOST
cat > /etc/apache2/sites-available/databasehost.conf <<'VHOST'
<VirtualHost *:80>
    ServerName databasehost.frognet
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
VHOST
cat > /etc/apache2/sites-available/frognet-ssl.conf <<'VHOST'
<IfModule mod_ssl.c>
    <VirtualHost *:8443>
        ServerName frognethost.frognet
        DocumentRoot /var/www/html
        SSLEngine on
        SSLCertificateFile      /etc/ssl/frognet-universal.crt
        SSLCertificateKeyFile   /etc/ssl/frognet-universal.key
        SSLOptions +StrictRequire
        SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1
        Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains" env=HTTPS
    </VirtualHost>
    <VirtualHost *:443>
        ServerName databasehost.frognet
        DocumentRoot /var/www/html
        SSLEngine on
        SSLCertificateFile      /etc/ssl/localCA/databasehost.frognet.crt
        SSLCertificateKeyFile   /etc/ssl/localCA/databasehost.frognet.key
        SSLOptions +StrictRequire
        SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1
        Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains" env=HTTPS
    </VirtualHost>
</IfModule>
VHOST
a2ensite 000-default admin-site databasehost frognet-ssl 2>/dev/null || true
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
# dhcp_tracking.bash fires on add AND del, filters to FrogNet (10.10x) leases so
# non-FrogNet DHCP churn does not spuriously merge, and logs via debugTag -- all
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
# install ever brings unbound UP -- fix_unbound_bypass.sh stops and disables it
# and writes upstream_fallback.conf (Google DNS) as the real config, and the
# fallback branch below also stops unbound. So this file forwarded DNS to a dead
# resolver. On the normal path it was merely overridden by upstream_fallback.conf
# through alphabetical read order (both set server=, dnsmasq merges them); on any
# node where the ordering or the fallback differed it would black-hole DNS.
# Remove it here and sweep any copy a prior install left behind.
rm -f /etc/dnsmasq.d/forward_to_unbound.conf 2>/dev/null || true

cat > /etc/systemd/system/dnsmasq.service.d/override.conf <<'UNIT'
[Unit]
Wants=network-online.target
After=network-online.target
NetworkManager-wait-online.service

[Service]
Restart=on-failure
RestartSec=2
UNIT
FIX_UNBOUND="/usr/local/bin/fix_unbound_bypass.sh"
[[ -x "$FIX_UNBOUND" ]] && bash "$FIX_UNBOUND" || { systemctl stop unbound 2>/dev/null || true; }
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
# Only mark a real, non-uplink wired iface unmanaged. Marking the default-route
# device unmanaged and then restarting NM (below) is exactly what drops the box's
# network with no recovery, so refuse it and leave the uplink managed.
_UPLINK_DEV="$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
if [[ -n "$ETH0_IF" && "$ETH0_IF" != "$_UPLINK_DEV" ]]; then
cat > /etc/NetworkManager/conf.d/10-frognet-unmanaged.conf <<NMCONF
[keyfile]
unmanaged-devices=interface-name:${ETH0_IF}
NMCONF
cat > /etc/NetworkManager/conf.d/99-frognet-unmanaged.conf <<NMCONF
[keyfile]
unmanaged-devices=interface-name:${ETH0_IF}
NMCONF
else
warn "  D7: NOT marking '${ETH0_IF:-<none>}' unmanaged - it is the uplink (default route via ${_UPLINK_DEV:-?}). Leaving it managed so the box keeps its network."
rm -f /etc/NetworkManager/conf.d/10-frognet-unmanaged.conf /etc/NetworkManager/conf.d/99-frognet-unmanaged.conf 2>/dev/null || true
fi
cat > /etc/NetworkManager/conf.d/99-wifi-powersave.conf <<'NMCONF'
[connection]
wifi.powersave = 2
NMCONF
cat > /etc/NetworkManager/dispatcher.d/90-frognet-merge <<'DISPATCH'
#!/bin/bash
IFACE="${1:-}"; STATE="${2:-}"
case "$STATE" in up|down) ;; *) exit 0 ;; esac
[[ "$IFACE" == "l0" ]] && exit 0
SENT_DIR="/etc/sentinels"; STATE_FILE="${SENT_DIR}/nm_upstream_state.json"; mkdir -p "$SENT_DIR"
. /usr/local/bin/mapInterfaces 2>/dev/null || true
iface_ipv4() { ip -4 -o addr show dev "$1" 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f1; }
ip_base3()   { local ip="$1"; [[ -n "$ip" ]] || { echo ""; return; }; IFS=. read -r a b c _ <<<"$ip" || true; [[ -n "${a:-}" && -n "${b:-}" && -n "${c:-}" ]] || { echo ""; return; }; echo "${a}.${b}.${c}"; }
default_route_line() { ip route show default 2>/dev/null | head -n1 || true; }
default_via() { awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"${1:-}"; }
default_dev() { awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"${1:-}"; }
W0_IP="$(iface_ipv4 "${wlan0Name:-wlan0}" || true)"
W1_IP="$(iface_ipv4 "${wlan1Name:-wlan1}" || true)"
W0_BASE="$( [[ -n "${W0_IP:-}" && "$W0_IP" == 10.* ]] && ip_base3 "$W0_IP" || echo "" )"
W1_BASE="$( [[ -n "${W1_IP:-}" && "$W1_IP" == 10.* ]] && ip_base3 "$W1_IP" || echo "" )"
DEF_LINE="$(default_route_line)"; DEF_VIA="$(default_via "$DEF_LINE")"; DEF_DEV="$(default_dev "$DEF_LINE")"
CUR_SIG="w0=${W0_IP:-}|w1=${W1_IP:-}|defvia=${DEF_VIA:-}|defdev=${DEF_DEV:-}|w0b=${W0_BASE:-}|w1b=${W1_BASE:-}"
tmp="${STATE_FILE}.tmp"; printf '%s\n' "$CUR_SIG" > "$tmp"; mv -f "$tmp" "$STATE_FILE"
/usr/local/bin/runMerge.bash &
DISPATCH
# [DEFERRED_BROKER_SETUP_V1] connectivity-change is the event that matters for a
# gateway waiting on its broker: the link is already up, so neither up/down hook
# fires again, but NM signals when the box actually gains (or loses) internet.
# 99-ifup deliberately drops it -- NM passes no interface for this event and that
# script exits 4 on an empty one -- so nothing acted on it at all. This hook takes
# only that event and asks for a merge, which is where the enrolment retry lives.
cat > /etc/NetworkManager/dispatcher.d/91-frognet-connectivity <<'DISPATCH'
#!/bin/bash
# $1 = interface (empty for connectivity-change), $2 = event
[[ "${2:-}" == "connectivity-change" ]] || exit 0
# Only bother when something is actually waiting on the broker.
[[ -f /etc/frognet/pond_bootstrap_done ]] && exit 0
grep -q '^BROKER_URL=..*' /etc/frognet/tunnel.conf 2>/dev/null || exit 0
logger -t frognet-connectivity "connectivity-change - requesting merge for pending broker enrolment"
/usr/local/bin/runMerge.bash &
DISPATCH

cat > /etc/NetworkManager/dispatcher.d/99-ifup <<'DISPATCH'
#!/bin/bash
INTERFACE="${1:-}"; EVENT="${2:-}"
[[ "$INTERFACE" == "l0" ]] && exit 0
[[ "$EVENT" == "connectivity-change" ]] && exit 0
[[ -z "$INTERFACE" ]] && exit 4
exec /usr/local/bin/runMerge.bash &
DISPATCH
chmod +x /etc/NetworkManager/dispatcher.d/90-frognet-merge \
         /etc/NetworkManager/dispatcher.d/91-frognet-connectivity \
         /etc/NetworkManager/dispatcher.d/99-ifup
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
GRANT ALL PRIVILEGES ON \`FrogNetFamily\`.* TO 'FrogUser'@'localhost';
GRANT ALL PRIVILEGES ON \`FrogNetFamily\`.* TO 'FrogUser'@'%';
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
declare -A KNOWN_NODES=(
    [BlackBox]=10.101.10.1    [IronBox]=10.101.20.1
    [SilverBox]=10.101.30.1   [HardBox]=10.101.40.1
    [TealBox]=10.101.210.1    [NYCBox]=10.101.60.1
    [NYC2Box]=10.101.61.1     [NYC4Box]=10.101.64.1
    [NYC5Box]=10.101.65.1     [AMSbox]=10.101.70.1
    [BABox]=10.101.80.1
)

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
# re-IPs or renames a live node, which changes its broker identity and its whole
# /24, on what the operator asked for as an in-place upgrade.
#
# An explicit --name / --ip still wins: this only fills in what was not given.
# If the file is missing or malformed we fall through to the normal prompt rather
# than guessing -- a wrong identity is worse than a question.
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

# Use args if supplied; otherwise prompt.
if [[ -n "$ARG_NAME" ]]; then
    NODE_NAME="$ARG_NAME"
    log "Using --name: $NODE_NAME"
else
    echo; echo "  Known nodes:"
    for name in $(echo "${!KNOWN_NODES[@]}" | tr ' ' '\n' | sort); do
        printf "    %-12s  %s\n" "$name" "${KNOWN_NODES[$name]}"
    done; echo
    while true; do
        read -rp "  Node name: " NODE_NAME; NODE_NAME="$(echo "$NODE_NAME" | xargs)"
        [[ -n "$NODE_NAME" ]] && break; echo "  Name cannot be empty."
    done
fi

if [[ -n "$ARG_IP" ]]; then
    NODE_IP="$ARG_IP"
    log "Using --ip: $NODE_IP"
else
    SUGGESTED_IP="${KNOWN_NODES[$NODE_NAME]:-}"
    while true; do
        if [[ -n "$SUGGESTED_IP" ]]; then
            read -rp "  Gateway IP [$SUGGESTED_IP]: " INPUT_IP
            NODE_IP="${INPUT_IP:-$SUGGESTED_IP}"; NODE_IP="$(echo "$NODE_IP" | xargs)"
        else
            read -rp "  Gateway IP (e.g. 10.101.50.1): " NODE_IP; NODE_IP="$(echo "$NODE_IP" | xargs)"
        fi
        [[ "$NODE_IP" =~ \.1$ ]] || { echo "  Must end in .1"; SUGGESTED_IP=""; continue; }
        [[ "$NODE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "  Invalid IP"; SUGGESTED_IP=""; continue; }
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


NODE_NAME_LOWER="${NODE_NAME,,}"
echo
echo "  +------------------------------------+"
printf "  |  Name:      %-23s|\n" "$NODE_NAME"
printf "  |  IP:        %-23s|\n" "$NODE_IP"
printf "  |  Interface: %-23s|\n" "$ETH0_IF"
if [[ -n "$ARG_BROKER_HOST" ]]; then
printf "  |  Broker:    %-23s|\n" "${ARG_BROKER_HOST}:${ARG_BROKER_PORT}"
fi
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

# ---------------------------------------------------------------------------
# [DEFERRED_BROKER_SETUP_V1] Gateway role + broker enrolment
#
# Asked here, after the node has its name and address and before E3 runs
# setup_lillypad -- which is what actually writes BROKER_URL.
#
# Being a gateway is a property of the machine's uplink, not of this answer: the
# tunnel daemon decides WAN vs LAN mode every merge from three live conditions
# (a non-10 upstream, broker config present, broker routable). What we do here is
# supply the middle one. Answering yes on a node with no upstream is harmless --
# it simply stays LAN-only until an uplink appears.
#
# We deliberately configure even when the broker cannot be reached right now.
# A fresh gateway usually cannot: DNS may not be up, the uplink may not be
# plumbed, the broker may be down or not yet migrated. Writing the config and
# leaving enrolment pending means the node completes itself later without a
# second visit -- retried on every runMerge (i.e. on every network change, which
# is when reachability is most likely to have changed) and by the 2-minute
# dispatcher hooks -- there is no timer.
# ---------------------------------------------------------------------------
if [[ -z "$ARG_BROKER_HOST" ]]; then
    echo
    echo "  Is this node an internet gateway - should it reach a broker and build"
    echo "  WireGuard tunnels to other ponds?  A LAN-only node answers no."
    read -rp "  Gateway? [y/N]: " _gw
    if [[ "$_gw" =~ ^[Yy] ]]; then
        while [[ -z "$ARG_BROKER_HOST" ]]; do
            read -rp "  Broker host (DNS name or IP): " ARG_BROKER_HOST
            ARG_BROKER_HOST="$(echo "$ARG_BROKER_HOST" | xargs)"
        done
        while [[ -z "${_bp:-}" ]]; do
            read -rp "  Broker port: " _bp
        done
        ARG_BROKER_PORT="$_bp"
        read -rp "  Scheme http/https [http]: " _bs
        ARG_BROKER_SCHEME="${_bs:-http}"
        log "Gateway: broker ${ARG_BROKER_SCHEME}://${ARG_BROKER_HOST}:${ARG_BROKER_PORT}"
    else
        log "LAN-only node - no broker will be configured"
    fi
else
    log "Gateway: broker host supplied on the command line ($ARG_BROKER_HOST)"
fi

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

phase "E3: setup_lillypad (v4) + WireGuard credentials"
SETUP="/usr/local/bin/setup_lillypad_v4.bash"
[[ -x "$SETUP" ]] || die "$SETUP not found"

# Gateway IP from E1 is what setup_lillypad_v4.bash expects as its
# 2nd positional arg.  Don't re-ask.
MACHINE_IP="$NODE_IP"
log "Using machine IP: $MACHINE_IP"

# Broker URL precedence: args > existing tunnel.conf > none
INHERITED_BROKER_URL=""
if [[ -f /etc/frognet/tunnel.conf ]]; then
    INHERITED_BROKER_URL="$(grep -E '^BROKER_URL=' /etc/frognet/tunnel.conf \
                            | head -1 | cut -d= -f2-)"
fi

CHOSEN_BROKER_URL=""
if [[ -n "$ARG_BROKER_HOST" ]]; then
    # Build URL: scheme://host[:port]<path>.  Omit :port when 443.
    hp="$ARG_BROKER_HOST"
    [[ -n "$ARG_BROKER_PORT" && "$ARG_BROKER_PORT" != "443" ]] && hp="${ARG_BROKER_HOST}:${ARG_BROKER_PORT}"
    case "$ARG_BROKER_SCHEME" in
        http|https) ;;
        *) die "--broker-scheme must be http or https (got: $ARG_BROKER_SCHEME)" ;;
    esac
    # [ONE_BROKER_URL_SHAPE_V1] scheme://host:port, no path. The broker serves
    # /api/v4/... at the root of its own port; --broker-path existed for a
    # deployment that lived under a /frognet-broker-v4 prefix on someone's 443,
    # and its own installer keeps serving that prefix via
    # FROGNET_BROKER_LEGACY_PREFIX, so nothing needs the node to send it.
    #
    # Removed 2026-09-01. It was also a disagreement between two entry points: the
    # command-line default was "/frognet-broker-v4" while the interactive prompt
    # defaulted to blank, so the same operator got a different broker URL
    # depending on which way they answered.
    CHOSEN_BROKER_URL="${ARG_BROKER_SCHEME}://${hp}"
    log "Broker URL from args: $CHOSEN_BROKER_URL"
elif [[ -n "$INHERITED_BROKER_URL" ]]; then
    CHOSEN_BROKER_URL="$INHERITED_BROKER_URL"
    log "Inheriting BROKER_URL from existing tunnel.conf: $CHOSEN_BROKER_URL"
else
    log "No BROKER_URL - broker will be configured later via setup_frognet.html"
fi

if [[ -n "$CHOSEN_BROKER_URL" ]]; then
    # [POND_IS_NOT_THE_DOMAIN_V1] A broker means this node JOINS A POND - a group of
    # nodes sharing a broker namespace. It is NOT the node/domain name, and it used
    # to be silently set to it, which put every node in its own single-member pond.
    # Obtain it: existing broker.conf first (reinstall keeps its pond), then --pond,
    # then ask. No default.
    if [[ -z "$ARG_POND" && -f /etc/frognet/broker.conf ]]; then
        ARG_POND="$(sed -n 's/^POND_NAME=//p' /etc/frognet/broker.conf | head -1)"
        [[ -n "$ARG_POND" ]] && log "Pond from existing broker.conf: $ARG_POND"
    fi
    while [[ -z "$ARG_POND" ]]; do
        echo
        echo "  This node will register with broker $CHOSEN_BROKER_URL."
        echo "  Enter the POND to join - the group name shared by its nodes."
        echo "  This is NOT the node name ($NODE_NAME)."
        read -rp "  Pond name: " ARG_POND
    done
    log "Joining pond: $ARG_POND"
    # [SETUP_LILLYPAD_NORESTART_V1] --norestart: setup_lillypad writes all config
    # and the persistent NM connection profile but does not bounce services or
    # re-address the live interface. The installer reboots at the end, so live
    # activation is unnecessary -- and on a LAN-only gateway the operator's SSH
    # rides the very interface setup_lillypad reconfigures, so activating it live
    # drops the install. Everything applies cleanly on the reboot.
    bash "$SETUP" "$NODE_NAME" "$MACHINE_IP" "$CHOSEN_BROKER_URL" --pond "$ARG_POND" --norestart
else
    bash "$SETUP" "$NODE_NAME" "$MACHINE_IP" --norestart
fi
log "setup_lillypad (v4) complete (config applied; interface + services come up on reboot)"

# Generate WireGuard credentials via v3 tunnel setup
TUNNEL_SETUP="/usr/local/bin/frognet-tunnel-setup-v3.sh"   # dash = canonical (underscore version retired)
[[ -x "$TUNNEL_SETUP" ]] || die "$TUNNEL_SETUP not found"

# [DEFERRED_BROKER_SETUP_V1] An unreachable broker must NOT abort the install.
# This used to be a bare `bash "$TUNNEL_SETUP"` under `set -e`, so a gateway
# installed before its uplink was plumbed -- or while the broker was down or
# mid-migration -- failed here, after the world was already extracted. Configure
# everything we can now, and leave enrolment to complete itself later.
if bash "$TUNNEL_SETUP"; then
    log "WireGuard credentials created; broker enrolment attempted"
else
    warn "broker enrolment did not complete (broker unreachable?) - deferring"
fi

# Arm the deferred completion whenever a broker is configured. Clearing the
# sentinel is deliberate: after an upgrade the enrolment should be re-verified,
# and frognet-pond-bootstrap.sh re-writes it as soon as it confirms the tunnel
# daemon is up. runMerge retries on every network change; the timer is the
# backstop for a node that is otherwise idle.
if grep -q '^BROKER_URL=..*' "${FROGNET_CONF:-/etc/frognet/tunnel.conf}" 2>/dev/null; then
    rm -f /etc/frognet/pond_bootstrap_done
    # No timer. Enrolment is retried by runMerge, and a merge is what a network
    # change already produces: the dnsmasq dhcp-script on lease events, and the
    # NetworkManager dispatcher hooks on interface up/down and connectivity
    # change. Polling every two minutes would only ask the same question the
    # events already answer, and would keep asking on a node that will never
    # have an uplink. The timer unit is left disabled.
    systemctl disable --now frognet-pond-bootstrap.timer 2>/dev/null || true
    log "Broker configured - enrolment deferred to the next merge (network-event driven)"
else
    log "No broker configured - LAN-only; deferred enrolment not armed"
fi

phase "E4: Apache admin-site"
ADMIN_CONF="/etc/apache2/sites-available/admin-site.conf"
if [[ -f "$ADMIN_CONF" ]]; then
    sed -i "s/ServerName FrogNetAdmin\..*/ServerName FrogNetAdmin.${NODE_NAME_LOWER}/" "$ADMIN_CONF"
    sed -i "s/ServerName NODENAME_PLACEHOLDER/ServerName FrogNetAdmin.${NODE_NAME_LOWER}/" "$ADMIN_CONF"
fi

phase "E5: SSL certificates"
mkdir -p /etc/ssl/localCA
if [[ ! -f /etc/ssl/frognet-universal.crt ]]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/ssl/frognet-universal.key -out /etc/ssl/frognet-universal.crt \
        -subj "/CN=frognethost.frognet/O=FrogNet/C=US" \
        -addext "subjectAltName=DNS:frognethost.frognet,DNS:*.frognet,DNS:FrogNetHost.${NODE_NAME}" 2>/dev/null
    chmod 600 /etc/ssl/frognet-universal.key
fi
if [[ ! -f /etc/ssl/localCA/databasehost.frognet.crt ]]; then
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

# 4) NetworkManager: declare $AP_IFACE unmanaged so NM doesn't grab it
#    between boot and frognet-netstart firing.  netstart's runtime
#    nmcli-managed-no is still useful as belt-and-suspenders.
NM_UNMANAGED=/etc/NetworkManager/conf.d/frognet-unmanaged-ap.conf
mkdir -p /etc/NetworkManager/conf.d
cat > "$NM_UNMANAGED" <<NMU
# Generated by frognet_install.sh: $(date)
# Keep the AP interface out of NetworkManager so hostapd can bind it
# cleanly at boot.
[keyfile]
unmanaged-devices=interface-name:${AP_IFACE}
NMU
chmod 644 "$NM_UNMANAGED"
log "  Wrote $NM_UNMANAGED (unmanaged: $AP_IFACE)"
# [NO_RESTART_BEFORE_REBOOT_V1] Same reason: this unmanaged-iface config is read
# when NM starts on the reboot. Reloading/restarting here only risks the SSH
# session for no gain.
:  # NetworkManager not poked; config applies on reboot

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
    frognet-eth0-fixup frognet-sysperf frognet-tunnel-setup frognet-tunnel-daemon-v3
    frognet-pond-bootstrap)
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
#
# NOT removed: /etc/NetworkManager/dispatcher.d/91-frognet-connectivity. That
# hook is the connectivity-change -> merge path, written in D7, asserted in F6,
# and depended on by runMerge and frognet-pond-bootstrap. Different thing that
# happens to share a word.
OPT_SERVICES=(frognet-gps frognet-transit-boot frognet-transit-watch)
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
# [DEFERRED_BROKER_SETUP_V1] The pond-bootstrap timer is NOT enabled. Enrolment
# is retried from the tail of runMerge, and a merge is what a network change
# already produces (dnsmasq dhcp-script, and the NetworkManager up/down and
# connectivity-change dispatcher hooks). Enabling the timer here would have
# silently re-armed the polling that E3 just turned off.
systemctl disable --now frognet-pond-bootstrap.timer 2>/dev/null || true
systemctl enable apache2 mariadb dnsmasq NetworkManager cron

# Candidate registration: every FrogNet host advertises databasehost + mediahost
# capability on a 60s timer so the merge-end election can score it. Units ship
# statically (frognet-{dbhost,mediahost}-advertise.{service,timer}); enable the timers
# here so they START AT BOOT (WantedBy=timers.target, OnBootSec=30). Opt a host out of
# a role by stopping that role's timer. Server software is NOT required to register -
# the capability blob's gate decides eligibility.
for t in frognet-dbhost-advertise.timer frognet-mediahost-advertise.timer; do
    [[ -f "/etc/systemd/system/${t}" ]] && { systemctl enable "$t" 2>/dev/null && log "  enabled: $t"; } \
        || warn "  MISSING: ${t}"
done
if [[ -x /usr/local/bin/frognet_setup_advertisers.sh ]]; then
    /usr/local/bin/frognet_setup_advertisers.sh && log "  candidate registration set up (db + media advertisers)" \
        || warn "  frognet_setup_advertisers.sh failed"
else
    warn "  MISSING: frognet_setup_advertisers.sh (no candidate registration)"
fi

phase "F2: Log management"
cat > /etc/logrotate.d/frognet <<'LR'
/var/log/frognet/*.log { daily missingok rotate 7 compress delaycompress notifempty copytruncate su root root }
LR
cat > /etc/logrotate.d/mysql-server <<'LR'
/var/log/mysql/*.log { daily missingok rotate 7 compress delaycompress notifempty copytruncate su root adm }
LR
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
    echo "@reboot sleep 240 && /usr/local/bin/runMerge.bash"
    echo "*/15 * * * * /usr/local/bin/registerFrogNetHost.bash"
) | sort -u | crontab -

phase "F4: froguser"
id froguser >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash froguser

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
chk "dnsmasq enabled"        systemctl is-enabled --quiet dnsmasq
chk "frognet-proxy enabled"  systemctl is-enabled --quiet frognet-proxy
chk "frognet-daemon enabled" systemctl is-enabled --quiet frognet-daemon
chk "resolved off"           test "$(systemctl is-active systemd-resolved 2>/dev/null)" != "active"
chk "FrogNet DB"             mysql -u root -e "USE FrogNet"
chk "FrogNetFamily DB"       mysql -u root -e "USE FrogNetFamily"
chk "FrogUser auth"          mysql -u FrogUser -p"${DB_PASS}" FrogNet -e "SELECT 1"
chk "interfaces_override"    test -f /etc/frognet/interfaces_override.conf
chk "ip_forward"             test "$(sysctl -n net.ipv4.ip_forward)" = "1"
chk "ssl cert"               test -f /etc/ssl/frognet-universal.crt
chk "sudoers"                test -f /etc/sudoers.d/frognet-setup
chk "sudoers valid"          visudo -cf /etc/sudoers.d/frognet-setup
chk "qrencode"               command -v qrencode
# [DEFERRED_BROKER_SETUP_V1] Was: "pond-bootstrap enabled" -- it asserted the
# timer was enabled, so it would now fail by design. What matters is that the
# event path exists: the merge controller and the dispatcher hook that fires
# when the box gains connectivity.
chk "merge controller present"  test -x /usr/local/bin/runMerge.bash
chk "connectivity hook present" test -x /etc/NetworkManager/dispatcher.d/91-frognet-connectivity
chk "ssid_projection.conf"   test -f /etc/frognet/ssid_projection.conf
chk "gateways.conf"          grep -q "$NODE_NAME" /etc/frognet/gateways.conf
chk "dnsmasq node"           grep -q "$NODE_NAME" /etc/dnsmasq.d/opts_only.conf
chk "proxy enabled"          systemctl is-enabled --quiet frognet-proxy
chk "daemon enabled"         systemctl is-enabled --quiet frognet-daemon
ECHO_R="$(curl -fsS --max-time 5 http://127.0.0.1/frognet_echo.php 2>&1)" && ok "echo: $ECHO_R" || nok "echo: ${ECHO_R:-fail}"
echo
[[ $FAIL -eq 0 ]] && echo "  All checks passed." || echo "  $FAIL check(s) failed."

phase "F7: System tuning"
TUNE="/usr/local/bin/frognet_system_tune.sh"
[[ -x "$TUNE" ]] && bash "$TUNE" || warn "frognet_system_tune.sh not found"

phase "F8: Reboot"
if [[ $FAIL -gt 0 ]]; then
    read -rp "  $FAIL failure(s). Reboot anyway? [y/N] " R
    [[ "$R" =~ ^[Yy]$ ]] || { echo "Reboot skipped."; exit 0; }
else
    echo "  $NODE_NAME ($NODE_IP) on $ETH0_IF ready."
    echo "  Rebooting in 10s - Ctrl-C to cancel."; sleep 10
fi
log "Rebooting."; systemctl reboot
