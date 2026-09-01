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
# frognet_make_build_tar.sh — produce a CLEAN, self-contained FrogNet build tar
# you can copy off this host and drop onto a target.
#
# The problem this solves: frognet_build_release.sh reads the LIVE filesystem,
# so build-host cruft (stale files, editor .bak's, bytecode, strays) leaks into
# the release. This script instead:
#   1. stages the FrogNet trees from a source root (default /, or --root DIR of a
#      CLEAN extracted tree) into a scratch dir,
#   2. strips everything that must not ship (bytecode, backups, ham, boardgame,
#      the misnamed webroot tarballs, retired script versions, package-module
#      shadows, per-node identity, runtime caches, nested release tarballs),
#   3. optionally scrambles the DB secret at rest (--scramble),
#   4. packages it, then EXTRACTS THE RESULT AND VERIFIES it is clean before
#      telling you it's done.
#
# Take-out flow on the target:
#   cd / && tar xzf frognet_build_<ts>.tgz
#   sudo bash /usr/local/bin/frognet_install.sh --name <N> --ip 10.x.y.1 [--broker-host H]
#   (the installer auto-detects the already-extracted tree; no world tar needed)
#
# Usage:
#   sudo bash frognet_make_build_tar.sh [--root DIR] [--output DIR] [--scramble] [--tag TAG]
#     --root DIR     Source tree to package (default /). Point at a CLEAN extracted
#                    RC to sidestep build-host cruft entirely.
#     --output DIR   Where to write the tarball (default /var/tmp).
#     --scramble     Tokenize the DB password + ship db_pass.enc (for distribution).
#                    Default: keep real values (directly installable by you).
#     --tag TAG      Extra name tag: frognet_build_<TAG>_<ts>.tgz
###############################################################################
set -uo pipefail          # not -e: staging continues past absent paths. no -x: never trace.

ROOT="/"; OUTPUT="/var/tmp"; SCRAMBLE=0; TAG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root)     ROOT="$2"; shift 2 ;;
        --root=*)   ROOT="${1#*=}"; shift ;;
        --output)   OUTPUT="$2"; shift 2 ;;
        --output=*) OUTPUT="${1#*=}"; shift ;;
        --scramble) SCRAMBLE=1; shift ;;
        --tag)      TAG="$2"; shift 2 ;;
        --tag=*)    TAG="${1#*=}"; shift ;;
        -h|--help)  sed -n '2,38p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done
[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
ROOT="${ROOT%/}"; [[ -z "$ROOT" ]] && ROOT=""    # allow ROOT="/"

TS="$(date '+%Y%m%d_%H%M%S')"
NAME="frognet_build${TAG:+_$TAG}_${TS}"
OUT_TGZ="${OUTPUT}/${NAME}.tgz"
STAGE="$(mktemp -d /var/tmp/frognet_build_stage_XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
log() { echo "$(date '+%H:%M:%S') [make-build] $*"; }

WRAP_KEY="${FROGNET_DB_WRAP_KEY:-fn0-dbwrap-v1-do-not-rely-on-secrecy}"

# FrogNet trees to include (relative to ROOT). Installer regenerates /etc config,
# so we ship code + bundles + webroot + the frognet systemd units only.
SRC_PATHS=(
    opt/frognet_semantic
    usr/local/bin usr/local/sbin usr/local/lib
    etc/frognet_bundles
    var/www/html var/www/www_admin
)

# Names that must NEVER ship. Matched as basenames anywhere in the staged tree
# (find -name), which is precise and avoids tar/rsync path-anchoring quirks.
# Broad names that also match LEGIT files (discovery.py, cache, logs) are handled
# by targeted removal AFTER this, not here.
PRUNE_NAMES=(
    '__pycache__' '*.pyc'
    '*.bak' '*.old' '*.pre-*.bak' '*.orig' '*~'
    'ham_*.sh' 'frognet_radio_init.sh'
    'addExternalNameserver.php'
    'setup_lillypad.bash' 'setup_lillypad_v3.bash' 'frognet-tunnel-setup.sh'
    'frognet_build_release.sh' 'frognet_make_build_tar.sh'
    '*frognet_world*.tgz' '*frognet_release*.tgz'
    'boardgame' 'venv' 'blob_cache'
    '*.log'
)

log "Staging FrogNet trees from root='${ROOT:-/}' ..."
missing=0
for p in "${SRC_PATHS[@]}"; do
    src="${ROOT}/${p}"
    if [[ ! -e "$src" ]]; then
        log "  (absent, skipped) $p"; missing=$((missing+1)); continue
    fi
    mkdir -p "${STAGE}/$(dirname "$p")"
    cp -a "$src" "${STAGE}/$(dirname "$p")/"
    log "  staged $p"
done

# Prune the never-ship names from the staged copy.
_find_expr=(); first=1
for n in "${PRUNE_NAMES[@]}"; do
    if [[ $first -eq 1 ]]; then _find_expr+=( -name "$n" ); first=0
    else _find_expr+=( -o -name "$n" ); fi
done
find "$STAGE" \( "${_find_expr[@]}" \) -prune -exec rm -rf {} + 2>/dev/null || true
log "  pruned bytecode/backups/ham/boardgame/retired/tooling/nested-tars"

# frognet systemd units (services/timers/paths), individually — never non-frognet units
mkdir -p "${STAGE}/etc/systemd/system"
if [[ -d "${ROOT}/etc/systemd/system" ]]; then
    while IFS= read -r -d '' u; do
        cp -a "$u" "${STAGE}/etc/systemd/system/"
    done < <(find "${ROOT}/etc/systemd/system" -maxdepth 1 \
        \( -name 'frognet-*.service' -o -name 'frognet-*.timer' -o -name 'frognet-*.path' \
        -o -name 'frognet-*@.service' \) -print0 2>/dev/null)
    log "  staged $(ls "${STAGE}/etc/systemd/system" | wc -l) frognet systemd unit(s)"
fi

# Targeted post-stage removals for strays that SHARE NAMES with legit files, so a
# blanket rsync exclude would have clobbered the real ones. Done by exact path.
rm -f  "${STAGE}/usr/local/bin/discovery.py" "${STAGE}/usr/local/bin/trace.py" 2>/dev/null || true   # package-module shadows
rm -rf "${STAGE}/opt/frognet_semantic/etc" "${STAGE}/opt/frognet_semantic/bin" \
       "${STAGE}/opt/frognet_semantic/usr" "${STAGE}/opt/frognet_semantic/var" 2>/dev/null || true    # staging cruft
for d in cache logs; do   # clear runtime cache/log CONTENTS, keep the dir; never touch daemon/proxy code caches
    [[ -d "${STAGE}/opt/frognet_semantic/$d" ]] && find "${STAGE}/opt/frognet_semantic/$d" -type f -delete 2>/dev/null || true
done
# Identity files (belt-and-suspenders; not in SRC_PATHS, so normally absent anyway)
rm -f "${STAGE}/etc/fnid" 2>/dev/null || true
rm -rf "${STAGE}/etc/wireguard" 2>/dev/null || true
# [STAGE_MATCHES_GATE_V1] The "no node identity" gate below checks for fnid,
# wireguard, broker.conf, tunnel.conf AND db.env, but staging only removed the
# first two -- so the gate would fail the build on files nothing had cleaned.
# frognet_build_release.sh already excludes all of them (its --exclude list);
# this brings the ad-hoc tar path in line.
#
# These are per-node identity and credentials, not software. tunnel.conf in
# particular now carries BROKER_URL, POND_NAME, GROUP_TOKEN, PASSCODE,
# POND_PASSWORD and NODE_GUID after [ONE_CONF_V1] -- shipping it would give every
# node installed from the artifact the build host's identity and pond password.
rm -f "${STAGE}/etc/frognet/broker.conf" 2>/dev/null || true
rm -f "${STAGE}/etc/frognet/tunnel.conf" 2>/dev/null || true
rm -f "${STAGE}/etc/frognet/pond.conf" 2>/dev/null || true
rm -f "${STAGE}"/etc/frognet/broker.conf.* 2>/dev/null || true
rm -f "${STAGE}"/etc/frognet/tunnel.conf.* 2>/dev/null || true
rm -f "${STAGE}"/etc/frognet/pond.conf.* 2>/dev/null || true
rm -f "${STAGE}/etc/frognet/db.env" 2>/dev/null || true
# [DNSMASQ_IDENTITY_OUT_V1] opts_only.conf carries this node's domain and DHCP
# scope; forward_to_unbound.conf is the dead unbound path. Neither belongs in a
# release built off a live node.
rm -f "${STAGE}/etc/dnsmasq.d/opts_only.conf" 2>/dev/null || true
rm -f "${STAGE}/etc/dnsmasq.d/forward_to_unbound.conf" 2>/dev/null || true

###############################################################################
# Optional: scramble the DB secret at rest (mirror of build D3 / install C1b)
###############################################################################
SECRET_INJECT_FILES=(
    var/www/html/config.php
    opt/frognet_semantic/DB_CONFIG.json
    opt/frognet_semantic/daemon/cache/semcache_db.py
    opt/frognet_semantic/proxy/cache/semcache_db.py
    opt/frognet_semantic/daemon/engine/data_cache.py
)
REAL_PASS=""
if [[ -f "${STAGE}/var/www/html/config.php" ]]; then
    REAL_PASS="$(grep -oP "define\('DB_PASS',\s*'\K[^']+" "${STAGE}/var/www/html/config.php" 2>/dev/null || true)"
fi
if [[ $SCRAMBLE -eq 1 ]]; then
    [[ -n "$REAL_PASS" ]] || { echo "ERROR: --scramble but no DB_PASS in staged config.php" >&2; exit 1; }
    printf '%s' "$REAL_PASS" | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:${WRAP_KEY}" -base64 -A > "${STAGE}/db_pass.enc"
    for rel in "${SECRET_INJECT_FILES[@]}"; do
        [[ -f "${STAGE}/${rel}" ]] || continue
        FN_SECRET="$REAL_PASS" python3 - "${STAGE}/${rel}" <<'PY'
import os,sys
p=sys.argv[1]; s=os.environ["FN_SECRET"]
d=open(p,encoding="utf-8",errors="surrogateescape").read()
open(p,"w",encoding="utf-8",errors="surrogateescape").write(d.replace(s,"__FROGNET_DB_PASS__"))
PY
    done
    log "Scrambled DB secret (db_pass.enc + tokenized ${#SECRET_INJECT_FILES[@]} files)"
fi

###############################################################################
# Package
###############################################################################
log "Packaging → $OUT_TGZ"
mkdir -p "$OUTPUT"
tar czf "$OUT_TGZ" -C "$STAGE" .
log "  size: $(du -sh "$OUT_TGZ" | cut -f1)"

###############################################################################
# Verify the OUTPUT (extract to scratch, assert clean)
###############################################################################
log "Verifying output ..."
V="$(mktemp -d /var/tmp/frognet_build_verify_XXXXXX)"
tar xzf "$OUT_TGZ" -C "$V"
fail=0
chk() { local n; n=$(eval "$2" 2>/dev/null | wc -l); if [[ "$n" -eq 0 ]]; then echo "  ok   $1"; else echo "  FAIL $1 ($n)"; eval "$2" 2>/dev/null | sed 's/^/       /' | head -5; fail=1; fi; }
chk "no bytecode"          "find '$V' \( -name '*.pyc' -o -name __pycache__ \)"
chk "no backups"           "find '$V' \( -name '*.bak' -o -name '*.old' -o -name '*.pre-*.bak' \)"
chk "no ham"               "find '$V' -name 'ham_*.sh' -o -name 'frognet_radio_init.sh'"
chk "no boardgame"         "find '$V' -type d -name boardgame"
chk "no webroot tar junk"  "find '$V' -name 'addExternalNameserver.php'"
chk "no retired versions"  "find '$V' \( -name 'setup_lillypad.bash' -o -name 'setup_lillypad_v3.bash' -o -name 'frognet-tunnel-setup.sh' \)"
chk "no module shadows"    "find '$V/usr/local/bin' -maxdepth 1 \( -name discovery.py -o -name trace.py \) 2>/dev/null"
chk "no node identity"     "find '$V' \( -name fnid -o -path '*wireguard/*' -o -name broker.conf -o -name tunnel.conf -o -name db.env -o -name opts_only.conf \)"
chk "no nested build tars" "find '$V' \( -name '*frognet_world*.tgz' -o -name '*frognet_release*.tgz' \)"
chk "sim harness present"  "[[ -f '$V/opt/frognet_semantic/discovery/sim/topology.py' ]] || echo MISSING_SIM"
# [STAGE_MATCHES_GATE_V1] the installer lives in usr/local/bin/installer/ now;
# this gate still pointed at the old top-level path and would report MISSING.
chk "installer present"    "[[ -f '$V/usr/local/bin/installer/frognet_install.sh' ]] || echo MISSING_INSTALLER"
if [[ -n "$REAL_PASS" && $SCRAMBLE -eq 1 ]]; then
    chk "no cleartext DB pw" "grep -rlF '$REAL_PASS' '$V'"
fi
rm -rf "$V"

echo
if [[ $fail -eq 0 ]]; then
    echo "════════════════════════════════════════════════════════"
    echo "  BUILD TAR READY (verified clean):  $OUT_TGZ"
    echo "════════════════════════════════════════════════════════"
    echo "  Take it out, then on the target:"
    echo "    cd / && tar xzf $(basename "$OUT_TGZ")"
    echo "    sudo bash /usr/local/bin/frognet_install.sh --name <N> --ip 10.x.y.1 [--broker-host H]"
    [[ $SCRAMBLE -eq 1 ]] && echo "    (scrambled: the installer's C1b descrambles db_pass.enc automatically)"
    exit 0
else
    echo "FATAL: output failed verification (see FAIL lines above). Tarball left at $OUT_TGZ for inspection." >&2
    exit 1
fi
