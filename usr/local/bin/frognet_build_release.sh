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
# frognet_build_release.sh - Build a FrogNet release tarball for distribution
#
# Run this on a healthy source node (e.g. TealBox).
#
# Produces:  frognet_release_YYYYMMDD_HHMMSS.tgz
#
# That tarball contains:
#   frognet_install.sh   - the one script testers run
#   frognet_world.tgz    - the full FrogNet filesystem snapshot
#
# Tester workflow:
#   1. Download frognet_release_*.tgz from Google Drive
#   2. tar -xzf frognet_release_*.tgz
#   3. sudo bash frognet_install.sh
#   Done.
#
# Usage:
#   sudo bash frognet_build_release.sh [--output /path/to/dir]
#
###############################################################################
set -eu

###############################################################################
# Args / config
###############################################################################
OUTPUT_DIR="/var/tmp"
for arg in "$@"; do
    case "$arg" in
        --output=*) OUTPUT_DIR="${arg#--output=}" ;;
        --output)   shift; OUTPUT_DIR="$1" ;;
    esac
done

TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
RELEASE_NAME="frognet_release_${TIMESTAMP}"
RELEASE_TGZ="${OUTPUT_DIR}/${RELEASE_NAME}.tgz"
WORLD_TGZ_NAME="frognet_world.tgz"

# [INSTALLER_LIVES_IN_INSTALLER_DIR_V1] frognet_install.sh lives in
# /usr/local/bin/installer/ - it is release tooling, kept out of the flat
# /usr/local/bin namespace where node tools live, while still being ON the build
# host so it stays current. frognet_build_release.sh itself stays in
# /usr/local/bin. The legacy locations are still searched so an older build host
# keeps working; the installer/ dir wins.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_SCRIPT=""
# ONE location. No fallback: if it is not where it belongs, that is an error to
# fix, not a condition to paper over by silently building from a stale copy.
INSTALL_SCRIPT="/usr/local/bin/installer/frognet_install.sh"
# [RESET_SHIPS_IN_RELEASE_ROOT_V1] frognet_reset.sh must ride in the RELEASE ROOT
# beside frognet_install.sh, not in the world tar. The installer runs it BEFORE the
# world tar is laid down and from OUTSIDE /usr/local/bin - which the reset itself
# deletes. Shipping it only inside the world tar made the reinstall path depend on
# whatever happened to already be on the box.
RESET_SCRIPT="/usr/local/bin/installer/frognet_reset.sh"

###############################################################################
# Root check
###############################################################################
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "ERROR: Must run as root" >&2
    exit 1
fi

[[ -f "$RESET_SCRIPT" ]] || {
    echo "ERROR: frognet_reset.sh not found at $RESET_SCRIPT - the reinstall path needs it" >&2
    exit 1
}
[[ -n "$INSTALL_SCRIPT" ]] || {
    echo "ERROR: frognet_install.sh not found in /usr/local/bin/installer/ (nor beside this script)" >&2
    exit 1
}

###############################################################################
# Logging
###############################################################################
LOG="/var/log/frognet_build_release.log"
exec > >(tee -a "$LOG") 2>&1

phase() { echo; echo "========================================================"; echo "  $*"; echo "========================================================"; }
log()   { echo "$(date '+%Y-%m-%d %H:%M:%S') [build_release] $*"; }
warn()  { echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] $*" >&2; }

log "frognet_build_release.sh starting on $(hostname) - output: $RELEASE_TGZ"

WORK_DIR="$(mktemp -d /var/tmp/frognet_release_build_XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

###############################################################################
# DB SECRET AT REST  [DB_SECRET_SCRAMBLE_V1]
#
# The FrogUser DB password is a SHARED constant across the pond. It must not
# ship in cleartext. Phase "D3" below reads the real value from this build
# host's live config.php, encrypts it to db_pass.enc (shipped in the world
# tar), and packs TOKENIZED copies of every file that carried the value. The
# installer's C1b phase descrambles db_pass.enc and injects the value back.
#
# FROGNET_DB_WRAP_KEY wraps the secret. Default is an embedded constant, so a
# holder of the full release CAN reverse it - this is obfuscation (defeats
# grep/secret-scanners/casual copy), not protection against someone who has the
# whole bundle. For real protection, set FROGNET_DB_WRAP_KEY in the environment
# of BOTH the build and every install (and keep it out of the bundle).
#
# SECRET_INJECT_FILES is the SINGLE SOURCE OF TRUTH for which files carry the
# password. It MUST stay byte-identical to the same array in frognet_install.sh.
# The post-build scanner (end of phase E) fails the build if the cleartext
# survives anywhere in the tar, so a missed file cannot silently ship.
###############################################################################
FROGNET_DB_WRAP_KEY="${FROGNET_DB_WRAP_KEY:-fn0-dbwrap-v1-do-not-rely-on-secrecy}"
SECRET_INJECT_FILES=(
    var/www/html/config.php
   
    opt/frognet_semantic/DB_CONFIG.json
    opt/frognet_semantic/daemon/cache/semcache_db.py
    opt/frognet_semantic/proxy/cache/semcache_db.py
    opt/frognet_semantic/daemon/engine/data_cache.py
)

# [DB_SECRET_SCRUB_V1] Files that may historically contain the password but are
# NOT runtime config - docs and a structural fallback. These get the password
# replaced with a harmless placeholder in the shipped copy (SCRUBBED, not
# tokenized/injected - they must never carry the secret, on-node or in-tar).
# Listing them here makes the build immune to a stale build host: whether the
# on-disk copy is already scrubbed (no-op) or still cleartext (scrubbed now),
# the shipped copy is clean and the leak scanner passes.
SCRUB_FILES=(
    usr/local/bin/install_databasehost.sh
    usr/local/bin/semantic_cache_schema.sql
)

###############################################################################
# A. Quiesce FrogNet services
###############################################################################
phase "A: Quiesce services"

# [DEAD_UNITS_REMOVED_V1] frognet-dashboard, frognet-post-merge-metrics and the v2
# frognet-tunnel-daemon dropped 2026-08-29 - all three disabled on a live node, all
# three with an ExecStart or a precondition that cannot be satisfied. Quiescing a
# unit that does not exist is a no-op, but listing it here says it does.
for svc in frognet-proxy frognet-daemon frognet-sysperf frognet-merge-watcher \
           frognet-tunnel-daemon-v3; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" && log "Stopped $svc" || warn "Could not stop $svc"
    fi
done

###############################################################################
# B. Purge all logs
###############################################################################
phase "B: Purge logs"

log "Apache logs..."
find /var/log/apache2/ -type f \( -name "*.log" -o -name "*.log.*" -o -name "*.gz" \) \
    -exec truncate -s 0 {} \; 2>/dev/null || true
find /var/log/apache2/ -type f -name "*.gz"  -delete 2>/dev/null || true
find /var/log/apache2/ -type f ! -name "*.log" -delete 2>/dev/null || true

log "MariaDB logs..."
find /var/log/mysql/ -type f -exec truncate -s 0 {} \; 2>/dev/null || true
find /var/log/mysql/ -type f ! -name "*.log" -delete 2>/dev/null || true
mysql -u root -e "RESET MASTER;" 2>/dev/null || true

log "FrogNet application logs..."
find /var/log/frognet/ -type f -exec truncate -s 0 {} \; 2>/dev/null || true
find /opt/frognet_semantic/logs/ -type f -exec truncate -s 0 {} \; 2>/dev/null || true

log "System logs..."
journalctl --vacuum-time=1s 2>/dev/null || true
for f in /var/log/syslog /var/log/auth.log /var/log/kern.log \
          /var/log/daemon.log /var/log/messages /var/log/debug \
          /var/log/user.log /var/log/wtmp /var/log/lastlog; do
    [[ -f "$f" ]] && truncate -s 0 "$f" || true
done
find /var/log/ -name "*.gz" -delete 2>/dev/null || true
find /var/log/ -name "*.1"  -delete 2>/dev/null || true
find /var/log/ -name "*.old" -delete 2>/dev/null || true

log "Blob cache..."
find /opt/frognet_semantic/blob_cache/ -type f -delete 2>/dev/null || true

log "WireGuard tunnel state..."
find /var/lib/frognet-tunnel/active/ -type f -delete 2>/dev/null || true

log "Sentinels..."
find /etc/sentinels/ -type f -delete 2>/dev/null || true

log "Python bytecode..."
find /opt/frognet_semantic/ -name "*.pyc" -delete 2>/dev/null || true
find /opt/frognet_semantic/ -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find /usr/local/bin/ -name "*.pyc" -delete 2>/dev/null || true

log "pip cache..."
pip3 cache purge 2>/dev/null || true

log "dnsmasq leases..."
truncate -s 0 /var/lib/misc/dnsmasq.leases 2>/dev/null || true

log "NetworkManager leases..."
find /var/lib/NetworkManager/ -name '*.lease' -delete 2>/dev/null || true

log "Purge complete"

###############################################################################
# C. Enforce INFO log level in unit files
###############################################################################
phase "C: Set INFO log level"

for unit in /etc/systemd/system/frognet-*.service; do
    [[ -f "$unit" ]] || continue
    sed -i \
        -e 's/FROGNET_LOG_LEVEL=DEBUG/FROGNET_LOG_LEVEL=INFO/g' \
        -e 's/FROGNET_DEBUG=1/FROGNET_DEBUG=0/g' \
        -e 's/FROGNET_SEM_RPC_DIAG=1/FROGNET_SEM_RPC_DIAG=0/g' \
        -e 's/FROGNET_DIAG=1/FROGNET_DIAG=0/g' \
        "$unit"
    log "  INFO: $(basename "$unit")"
done

systemctl daemon-reload 2>/dev/null || true

###############################################################################
# D. Database dump
###############################################################################
phase "D: Database dump"

DB_DUMP="${WORK_DIR}/frognet_db.sql"
# [DUMP_BULK_V1] The production data SHIPS - it primes the template store on a new
# node, which is the point. What was slow was the SHAPE of the dump, not its size:
#   --extended-insert    batch many rows per INSERT instead of one statement per row
#   --net-buffer-length  make those batches actually large (default caps them small)
#   --quick              stream rows out instead of buffering whole tables in RAM
#   --disable-keys       wrap each table so the restore rebuilds indexes ONCE at the
#                        end rather than maintaining them row by row
# Paired with [DB_RESTORE_BULK_V1] on the install side (single transaction, checks
# off, log flush relaxed), that turns a per-row fsync-bound replay into a bulk load.
log "Dumping FrogNet database (bulk-shaped for fast restore)..."
mysqldump \
    --single-transaction \
    --routines \
    --triggers \
    --events \
    --add-drop-table \
    --create-options \
    --extended-insert \
    --net-buffer-length=1M \
    --quick \
    --disable-keys \
    FrogNet > "$DB_DUMP"
log "  $(wc -l < "$DB_DUMP") lines, $(du -h "$DB_DUMP" | cut -f1)"

###############################################################################
# D2. Deploy v3 scripts to /usr/local/bin
###############################################################################
phase "D2: Deploy v3 edge scripts"

V3_PKG="/opt/frognet_semantic/internet_tunnels_v3"
if [[ -d "$V3_PKG" ]]; then
    # setup_lillypad_v3.bash deployment RETIRED - only setup_lillypad_v4.bash is
    # supported now (it ships via the world-tar glob of usr/local/bin). Do NOT
    # re-add a v3 deploy here. frognet-tunnel-setup-v3.sh likewise ships via the
    # glob (underscore version retired).
    if [[ -f "${V3_PKG}/frognet_chorus.php" ]]; then
        cp "${V3_PKG}/frognet_chorus.php" "/var/www/html/frognet_chorus.php"
        log "  Deployed frognet_chorus.php -> /var/www/html/"
    fi
else
    warn "internet_tunnels_v3 not found - v3 edge assets not deployed"
fi

# Disable v2 tunnel daemon in the image (keep unit file for rollback)
if [[ -f /etc/systemd/system/frognet-tunnel-daemon.service ]]; then
    systemctl disable frognet-tunnel-daemon 2>/dev/null || true
    log "  Disabled v2 frognet-tunnel-daemon"
fi
if [[ -f /etc/systemd/system/frognet-tunnel-daemon-v3.service ]]; then
    systemctl enable frognet-tunnel-daemon-v3 2>/dev/null || true
    log "  Enabled v3 frognet-tunnel-daemon-v3"
fi

###############################################################################
# D3. Scramble DB secret at rest  [DB_SECRET_SCRAMBLE_V1]
###############################################################################
phase "D3: Scramble DB secret"

# Read the real shared password from THIS build host's live config.php.
_REAL_DB_PASS="$(grep -oP "define\('DB_PASS',\s*'\K[^']+" /var/www/html/config.php 2>/dev/null || true)"
if [[ -z "$_REAL_DB_PASS" ]]; then
    echo "FATAL: no DB_PASS in /var/www/html/config.php on the build host - cannot \
scramble the DB secret. (If this host was itself installed from a scrambled \
release, config.php should have been injected at install; investigate.)" >&2
    exit 1
fi

# Encrypt -> db_pass.enc (base64, single line). Value is fed via stdin, never argv.
printf '%s' "$_REAL_DB_PASS" \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:${FROGNET_DB_WRAP_KEY}" -base64 -A \
  > "${WORK_DIR}/db_pass.enc"
log "  Wrote db_pass.enc ($(wc -c < "${WORK_DIR}/db_pass.enc") bytes ciphertext)"

# Stage TOKENIZED copies of every secret-bearing file (literal replace via
# python; secret passed by env, not argv). These staged copies REPLACE the live
# ones in the world tar (see excludes + second -C below).
SANI_DIR="${WORK_DIR}/sanitized"
STAGED_REL=()
_missing_secret=0
for rel in "${SECRET_INJECT_FILES[@]}"; do
    if [[ ! -f "/$rel" ]]; then
        warn "  secret file not present on build host: /$rel"
        _missing_secret=$((_missing_secret+1))
        continue
    fi
    mkdir -p "${SANI_DIR}/$(dirname "$rel")"
    FN_SECRET="$_REAL_DB_PASS" python3 - "/$rel" "${SANI_DIR}/$rel" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
secret = os.environ["FN_SECRET"]
data = open(src, "r", encoding="utf-8", errors="surrogateescape").read()
open(dst, "w", encoding="utf-8", errors="surrogateescape").write(
    data.replace(secret, "__FROGNET_DB_PASS__"))
PY
    STAGED_REL+=("$rel")
    log "  tokenized: $rel"
done
if (( _missing_secret )); then
    warn "  $_missing_secret secret file(s) missing on build host - that's OK if this node lacks those roles"
fi

# Scrub the doc/structural files: replace the password with a placeholder in a
# staged copy (no token, no injection). Immune to a stale build host.
STAGED_SCRUB_REL=()
for rel in "${SCRUB_FILES[@]}"; do
    [[ -f "/$rel" ]] || continue
    mkdir -p "${SANI_DIR}/$(dirname "$rel")"
    FN_SECRET="$_REAL_DB_PASS" python3 - "/$rel" "${SANI_DIR}/$rel" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
secret = os.environ["FN_SECRET"]
data = open(src, "r", encoding="utf-8", errors="surrogateescape").read()
open(dst, "w", encoding="utf-8", errors="surrogateescape").write(
    data.replace(secret, "<set at install>"))
PY
    STAGED_SCRUB_REL+=("$rel")
    log "  scrubbed: $rel"
done

###############################################################################
# E. Build world tarball
###############################################################################
phase "E: Build world tarball"

# [BUILD_GATE_MIRRORS_INSTALL_V1] Pre-package gate. It runs exactly what the
# installer's C3 gate runs, on the same code, so "build succeeded" implies "the
# install-time gate will pass." That mirror property is the whole point, and it
# is why this gate is FATAL with no skip at build time: a release that would
# `die` at C3 must never reach a tester.
#
# [NO_LIFECYCLE_AT_INSTALL_V1] That is ONE check, not two. discovery.sim.lifecycle
# came out of the installer's C3 gate and therefore comes out of here as well --
# leaving it would break the mirror in the direction that matters, refusing to
# build releases that install perfectly well.
#
# It was removed because it is not gate material anywhere: the harness drives the
# REAL merge, and the real merge reads the REAL box -- /etc/hosts, /etc/sentinels,
# /etc/frognet.  A build host is itself a live FrogNet node, so the fabric under
# test gets next hops from the builder's own mesh and the suite fails on the
# machine's state rather than on the code.  Verified: the identical scenario
# passes on a box with no prior FrogNet and fails on one that has one.
#
# It is still a real suite.  Run it deliberately, on a clean tree:
#     cd /opt/frognet_semantic && PYTHONPATH=. python3 -m discovery.sim.lifecycle
#
# discovery selfcheck stays, and it is not weakened: it covers the oracle suite
# byte-exact plus the proof_plane shapes/churn/broker scenarios.
_DISC_PY="/opt/frognet_semantic/venv/bin/python3"
[[ -x "$_DISC_PY" ]] || _DISC_PY="/usr/bin/python3"
_GATE_LOG="/var/tmp/frognet_release_build_gate.log"
: > "$_GATE_LOG"
_gate_ok=1
if ( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic \
        "$_DISC_PY" -m discovery selfcheck ) >>"$_GATE_LOG" 2>&1; then
    log "  [PASS] discovery selfcheck"
else
    log "  [FAIL] discovery selfcheck - see $_GATE_LOG"; _gate_ok=0
fi
log "  [SKIP] discovery lifecycle E2E (development gate; see NO_LIFECYCLE_AT_INSTALL_V1)"
if (( _gate_ok != 1 )); then
    echo "FATAL: build gate failed - the installed code would 'die' at the C3 \
install gate on every tester's box. Refusing to build a release that cannot \
install. Inspect $_GATE_LOG, fix, and re-run. (No skip at build time - this is \
the whole point of the gate.)" >&2
    exit 1
fi
log "  Build gate PASSED - release will clear the install-time C3 gate."

WORLD_TGZ="${WORK_DIR}/${WORLD_TGZ_NAME}"
log "Building $WORLD_TGZ_NAME..."

# Paths included in the world tar (relative to /)
# [ONE_MANIFEST_V1] The path list is NOT held here. It lives in
# /usr/local/lib/frognet_world_manifest.sh, which full_tar.bash also sources, so
# the public repo and the node clone cannot disagree about what a node is made
# of. Three lists that disagreed is how a published tarball came to ship 5 of 23
# paths. A local copy here, however carefully kept in sync, is a fourth.
_MANIFEST="/usr/local/lib/frognet_world_manifest.sh"
[ -r "$_MANIFEST" ] || { log "ERROR: manifest not readable: $_MANIFEST"; exit 1; }
# shellcheck source=/dev/null
. "$_MANIFEST"
frognet_check_manifest / || exit 1
WORLD_PATHS=( "${FROGNET_WORLD_PATHS[@]}" )

# Expand and skip missing paths
SAFE_WORLD_PATHS=()
# [RELEASE_MANIFEST_FAILFAST_V1] The manifest IS the contract. A path listed
# in WORLD_PATHS but absent on the build host means the build host is broken
# or the manifest is stale - either way the release must not ship with a
# silent hole. (Proven cost 2026-07-06: an all_5 built past a missing
# /etc/setup_iptables shipped without it, and the absence was only noticed
# during a field root-cause days later.)
_MISSING=()
for p in "${WORLD_PATHS[@]}"; do
    if [[ -e "/$p" ]]; then
        SAFE_WORLD_PATHS+=("$p")
    else
        _MISSING+=("/$p")
    fi
done
if (( ${#_MISSING[@]} )); then
    for m in "${_MISSING[@]}"; do
        echo "FATAL: manifest path missing on build host: $m" >&2
    done
    echo "FATAL: ${#_MISSING[@]} manifest path(s) missing - refusing to build an incomplete world" >&2
    exit 1
fi

# [LIB_MANIFEST_AUTODISCOVER_V1] /usr/local/bin and /usr/local/sbin ship WHOLE, but
# /usr/local/lib is enumerated file-by-file (python3.11 lives there and is not ours).
# Enumeration DRIFTS: frognet_log.sh existed on every node, was deleted by
# frognet_reset.sh, and was never in this manifest - so a reset+reinstall left every
# shell tool without the flog_* API and runMerge aborted. frognet_trace (no .sh) is
# in the same position today. Enumerating by hand loses this race every time a lib
# file is added, so DISCOVER them: anything matching /usr/local/lib/frognet* on the
# build host ships, whether or not somebody remembered to list it.
for _lib in /usr/local/lib/frognet*; do
    [[ -e "$_lib" ]] || continue
    _rel="${_lib#/}"
    _seen=0
    for _s in "${SAFE_WORLD_PATHS[@]}"; do [[ "$_s" == "$_rel" ]] && { _seen=1; break; }; done
    (( _seen )) && continue
    SAFE_WORLD_PATHS+=("$_rel")
    log "  world: auto-added FrogNet lib $_rel (not in the hand-written manifest)"
done

# Add frognet systemd unit files individually (services + timers + paths + targets)
#
# [BOOT_GATE_V1] '.target' was missing from this list. frognet-discovered.target
# is the stable name frognet-dashboard.service and frognet-gps.service order
# behind (After=/Wants=), so a release built without it shipped two consumers
# pointed at a unit that did not exist. systemd treats Wants= on a missing unit
# as a soft failure -- it logs and continues -- so both services started at an
# arbitrary point instead of after discovery converged, and nothing ever
# complained. Caught by discovery/sim/boot_gate_check.py, which could not read
# the target file.
while IFS= read -r -d '' f; do
    rel="${f#/}"
    SAFE_WORLD_PATHS+=("$rel")
done < <(find /etc/systemd/system -maxdepth 1 \
    \( -name 'frognet-*.service' \
    -o -name 'frognet-*.timer' \
    -o -name 'frognet-*.path' \
    -o -name 'frognet-*.mount' \
    -o -name 'frognet-*.target' \
    -o -name 'frognet-*@.service' \) \
    -print0 2>/dev/null)

tar -czf "$WORLD_TGZ" \
    `# [NO_NESTED_WORLD_TAR_V1] A prior frognet_world.tgz / frognet_release_*.tgz` \
    `# left inside any swept WORLD_PATH (usr/local/bin, opt/frognet_semantic,` \
    `# var/www/html, ...) would otherwise be packed INTO the world tar, nesting a` \
    `# full copy of a previous release. These two globs (tar '*' spans '/') drop` \
    `# any such artifact at any depth. Real files are unaffected - no legitimate` \
    `# shipped file matches these names.` \
    --exclude='*frognet_world*.tgz' \
    --exclude='*frognet_release_*.tgz' \
    `# [TOOLING_NOT_IN_WORLD_V1] frognet_install.sh and frognet_build_release.sh` \
    `# are build/release TOOLING, not node-runtime files. They live in` \
    `# /usr/local/bin on the build host (that's where this builder finds the` \
    `# installer to bundle), but must NOT be swept into the world tar: doing so` \
    `# lands frognet_install.sh at /usr/local/bin on every node, where - run` \
    `# later - it looks for frognet_world.tgz beside itself (/usr/local/bin). If` \
    `# the tar is then placed there, the next build sweeps that 14MB tar into` \
    `# itself (the 2x-size bug). The installer belongs ONLY in the release tar,` \
    `# beside the world tar, where its SCRIPT_DIR resolves correctly.` \
    --exclude='usr/local/bin/installer' \
    --exclude='usr/local/bin/frognet_install.sh' \
    --exclude='usr/local/bin/frognet_build_release.sh' \
    `# [HAM_OUT_V1] Amateur/AX.25 subsystem must not ship (FCC Part 97.113(a)(4)` \
    `# bars BLDC-1 on amateur bands). All ham_*.sh drive the ham0 interface;` \
    `# frognet_radio_init.sh is an orphan ham0 driver (nothing invokes it).` \
    `# radio_link.sh is a GENERIC tc/ifb shaper, NOT ham - it stays. ax25-tools/` \
    `# ax25-apps removed from the installer apt list separately.` \
    --exclude='usr/local/bin/ham_*.sh' \
    --exclude='usr/local/bin/frognet_radio_init.sh' \
    `# [RETIRED_VERSIONS_V1] Keep only setup_lillypad_v4.bash and` \
    `# frognet-tunnel-setup-v3.sh. Drop the older versions so a build host that` \
    `# still has them on disk cannot re-ship them.` \
    --exclude='usr/local/bin/setup_lillypad.bash' \
    --exclude='usr/local/bin/setup_lillypad_v3.bash' \
    --exclude='usr/local/bin/frognet-tunnel-setup.sh' \
    `# [NO_STAGING_DIRS_UNDER_OPT_V1] /opt/frognet_semantic is Python source only.` \
    `# etc/bin/usr/var have no business there - they are stray staging mirrors` \
    `# (e.g. a full duplicate of etc/frognet_bundles). Nothing references them.` \
    `# Guard belt-and-suspenders even after they are deleted on the build host.` \
    --exclude='opt/frognet_semantic/etc' \
    --exclude='opt/frognet_semantic/bin' \
    --exclude='opt/frognet_semantic/usr' \
    --exclude='opt/frognet_semantic/var' \
    --exclude='opt/frognet_semantic/blob_cache/*' \
    --exclude='opt/frognet_semantic/logs/*' \
    `# [SHIP_NO_BYTECODE] '**/' does not match a TOP-LEVEL __pycache__, i.e.` \
    `# opt/frognet_semantic/__pycache__ itself. The pre-clean above deletes it,` \
    `# so this is belt-and-braces -- but a tarball built from a tree the clean` \
    `# did not reach would otherwise ship bytecode.` \
    --exclude='opt/frognet_semantic/__pycache__' \
    --exclude='opt/frognet_semantic/**/__pycache__' \
    --exclude='opt/frognet_semantic/**/*.pyc' \
    --exclude='opt/frognet_semantic/**.old' \
    --exclude='opt/frognet_semantic/daemon.old' \
    --exclude='opt/frognet_semantic/proxy.old' \
    --exclude='opt/frognet_semantic/core.old' \
    --exclude='opt/frognet_semantic/_v1' \
    --exclude='usr/local/bin/__pycache__' \
    --exclude='usr/local/bin/*.pyc' \
    --exclude='usr/local/sbin/__pycache__' \
    --exclude='usr/local/sbin/*.pyc' \
    --exclude='usr/local/lib/python3.11/**/__pycache__' \
    --exclude='usr/local/lib/python3.11/**/*.pyc' \
    --exclude='etc/frognet_bundles/**/__pycache__' \
    --exclude='etc/frognet_bundles/**/*.pyc' \
    --exclude='etc/wireguard/*.conf' \
    --exclude='etc/NetworkManager/system-connections' \
    --exclude='etc/frognet/tunnel.conf' \
    --exclude='etc/frognet/tunnel.conf.bak.*' \
    --exclude='etc/frognet/wifi.env' \
    --exclude='etc/frognet/sem_cache_secret' \
    --exclude='etc/frognet/broker.conf' \
    `# [DNSMASQ_IDENTITY_OUT_V1] opts_only.conf is per-node: setup_lillypad` \
    `# writes domain=<node> and this node's dhcp-range into it. Building from a` \
    `# live node would otherwise ship one node's DHCP scope and domain to every` \
    `# node. It is regenerated on each install, so dropping it is safe.` \
    --exclude='etc/dnsmasq.d/opts_only.conf' \
    `# [DNSMASQ_NO_UNBOUND_V1] never ship the dead unbound-forward config; the` \
    `# installer no longer writes it, but a live build host may still carry one.` \
    --exclude='etc/dnsmasq.d/forward_to_unbound.conf' \
    --exclude='etc/frognet/gateways.conf' \
    --exclude='etc/frognet/pond.conf' \
    --exclude='etc/frognet/active_interface' \
    --exclude='etc/frognet/interfaces_override.conf' \
    --exclude='etc/frognet/transit_exclude_interfaces' \
    --exclude='etc/frognet/ssid_projection.conf' \
    `# [DB_SECRET_SCRAMBLE_V1] Drop the CLEARTEXT live copies of every secret-` \
    `# bearing file; the TOKENIZED copies are appended as a second gzip member` \
    `# below. Also drop the misnamed webroot-snapshot tarballs (junk + leak) and` \
    `# the install-written env seed, neither of which must ever ship.` \
    --exclude='var/www/html/config.php' \
    --exclude='opt/frognet_semantic/DB_CONFIG.json' \
    --exclude='opt/frognet_semantic/daemon/cache/semcache_db.py' \
    --exclude='opt/frognet_semantic/proxy/cache/semcache_db.py' \
    --exclude='opt/frognet_semantic/daemon/engine/data_cache.py' \
    --exclude='usr/local/bin/install_databasehost.sh' \
    --exclude='usr/local/bin/semantic_cache_schema.sql' \
    --exclude='*addExternalNameserver.php' \
    --exclude='etc/frognet/db.env' \
    -C / \
    "${SAFE_WORLD_PATHS[@]}" \
    -C "$WORK_DIR" \
    frognet_db.sql \
    db_pass.enc

# Append the TOKENIZED secret files as a second gzip member. (A single gzipped
# tar can't be --appended to, but concatenated gzip members decompress as one
# stream; the installer extracts with --ignore-zeros so both members apply.)
if (( ${#STAGED_REL[@]} )); then
    tar -czf - -C "$SANI_DIR" "${STAGED_REL[@]}" >> "$WORLD_TGZ"
    log "  Appended ${#STAGED_REL[@]} tokenized secret file(s) as a second gzip member"
fi
if (( ${#STAGED_SCRUB_REL[@]} )); then
    tar -czf - -C "$SANI_DIR" "${STAGED_SCRUB_REL[@]}" >> "$WORLD_TGZ"
    log "  Appended ${#STAGED_SCRUB_REL[@]} scrubbed doc/structural file(s)"
fi

# [DB_SECRET_SCAN_V1] Enforcement: no cleartext password may survive anywhere in
# the world tar. Extract to a scratch dir and grep for the real value. If found,
# the sanitize list drifted or a new file introduced the secret - FAIL the build.
_SCAN_DIR="$(mktemp -d "${WORK_DIR}/scan_XXXXXX")"
tar -xizf "$WORLD_TGZ" -C "$_SCAN_DIR" 2>/dev/null || true
if grep -rlF "$_REAL_DB_PASS" "$_SCAN_DIR" >/dev/null 2>&1; then
    echo "FATAL: cleartext DB password survived in the world tar:" >&2
    grep -rlF "$_REAL_DB_PASS" "$_SCAN_DIR" | sed "s|${_SCAN_DIR}||; s/^/  LEAK: /" >&2
    echo "Add the offending file(s) to SECRET_INJECT_FILES (and the same array in \
frognet_install.sh), then re-run." >&2
    rm -rf "$_SCAN_DIR"; unset _REAL_DB_PASS
    exit 1
fi
rm -rf "$_SCAN_DIR"
unset _REAL_DB_PASS
log "  Scan clean - no cleartext DB password in the world tar"

log "  World tar: $(du -sh "$WORLD_TGZ" | cut -f1)"

###############################################################################
# F. Bundle into release tarball
###############################################################################
phase "F: Bundle release tarball"

log "Bundling installer + reset + world tar -> $RELEASE_TGZ"

tar -czf "$RELEASE_TGZ" \
    -C "$WORK_DIR" \
    "$WORLD_TGZ_NAME" \
    -C "$(dirname "$INSTALL_SCRIPT")" \
    "$(basename "$INSTALL_SCRIPT")" \
    "$(basename "$RESET_SCRIPT")"

log "  Release tar: $(du -sh "$RELEASE_TGZ" | cut -f1)"

###############################################################################
# G. Restart source node
###############################################################################
phase "G: Restart source node"

for svc in frognet-proxy frognet-daemon frognet-sysperf frognet-merge-watcher \
           frognet-tunnel-daemon-v3; do
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl start "$svc" && log "Started $svc" || warn "Could not start $svc"
    fi
done

###############################################################################
# Summary
###############################################################################
echo
echo "========================================================"
echo "  BUILD COMPLETE"
echo "========================================================"
echo
echo "  Release: $RELEASE_TGZ"
echo "  Size:    $(du -sh "$RELEASE_TGZ" | cut -f1)"
echo
echo "  Upload to Google Drive, then testers run:"
echo
echo "    tar -xzf $(basename "$RELEASE_TGZ")"
echo "    sudo bash frognet_install.sh"
echo
