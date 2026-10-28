#!/usr/bin/env bash
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
# full_tar.bash -- snapshot this node's FrogNet source into one tarball, paths relative to /, so it untars over the
# repository's root (or over another node's /). Self-contained: everything it takes and leaves is named below.
# [NO_MANIFEST_V1]
#
#   full_tar.bash [--output DIR]          writes DIR/frognet-source-<host>-<date>.tgz (DIR: your home)
#
# Taken: what FrogNet owns on a node. Left out: keys and secrets, this node's identity and per-board tuning, caches,
# logs and build output, and anything a .gitignore in the tree names. The database password in config.php and
# DB_CONFIG.json is replaced by __FROGNET_DB_PASS__ in the tarball (the node's own files are not touched).
set -eu
OUTPUT="$HOME"
while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT="$2"; shift 2 ;;
        --output=*) OUTPUT="${1#*=}"; shift ;;
        *) echo "unknown argument: $1 (full_tar.bash [--output DIR])" >&2; exit 2 ;;
    esac
done
ROOT="${FULL_TAR_ROOT:-/}"                        # / on a node; another tree only to test this script

# ---- what FrogNet owns -------------------------------------------------------------------------------------------
TAKE=(
    opt/frognet_semantic
    opt/fdf
    usr/local/bin
    usr/local/sbin
    usr/local/lib/frognet
    usr/local/lib/frognet_log.sh
    usr/local/lib/frognet_trace.sh
    usr/local/share/frognet
    var/www/html
    etc/apache2/sites-available/000-default.conf
    etc/apache2/sites-available/default-ssl.conf
    etc/apache2/sites-available/databasehost.conf
    etc/apache2/sites-available/databasehost-ssl.conf
    etc/apache2/sites-available/frognet-ssl.conf
    etc/dnsmasq.d/frognet_dhcp_event.conf
    etc/dnsmasq.d/upstream_fallback.conf
    etc/frognet/transit.conf
    etc/logrotate.d/frognet
    etc/mysql/mariadb.conf.d/99-frognet.cnf
    etc/mysql/mariadb.conf.d/99-frognet-perf.cnf
    etc/NetworkManager/dispatcher.d/90-frognet-merge
    etc/setup_iptables
    etc/ssl/frognet-universal.cnf
    etc/sudoers.d/frognet-snapshot
    etc/sysctl.d/90-frognet-forwarding.conf
    etc/systemd/system/dnsmasq.service.d
)
# every FrogNet unit and its drop-ins, whatever they are called today
for p in "$ROOT"/etc/systemd/system/frognet*; do [ -e "$p" ] && TAKE+=( "${p#"$ROOT"/}" ); done

# ---- what never leaves the node ----------------------------------------------------------------------------------
LEAVE=(
    # keys, secrets, certificates
    'opt/fdf/var'                                 # wg.key, site.env (site key), area.env (area password), facts
    'etc/wireguard' 'etc/ssl/private' 'etc/ssl/localCA' 'etc/ssl/certs'
    'etc/ssl/*.key' 'etc/ssl/*.pem' 'etc/ssl/*.crt' 'etc/ssl/*.csr'
    'etc/frognet/*secret*' 'etc/frognet/*.pem' 'etc/frognet/*.key'
    'opt/frognet_semantic/ribbit/examples/communicator/shell.json'
    # this node's identity and its per-board tuning (rewritten at every boot for its RAM and cores)
    'etc/frognet/node-id' 'etc/frognet/tunnel.conf' 'etc/frognet/db.env'
    'etc/mysql/mariadb.conf.d/99-frognet-tune.cnf'
    'etc/systemd/system/*.service.d/99-frognet-tune.conf'
    'var/www/html/index.html'
    # node-local state, caches, logs, build output
    'opt/frognet_semantic/etc' 'opt/frognet_semantic/bin' 'opt/frognet_semantic/usr' 'opt/frognet_semantic/var'
    'opt/frognet_semantic/blob_cache' 'opt/frognet_semantic/logs' 'opt/frognet_semantic/venv'
    'opt/frognet_semantic/codex_fullsuite/corpora' 'opt/frognet_semantic/_v1'
    'usr/local/bin/frognet_monitor_cpp/build' 'usr/local/bin/frognet_monitor_cpp/linux'
    '*/__pycache__' '*.pyc' '*.pyo' '*/venv' '*/.venv' '*/site-packages' '*/node_modules' '*/.git'
    '*/build/obj' '*.o'
    # pip's launchers in usr/local/bin: other projects' programs, installed by pip on each node
    'usr/local/bin/bottle' 'usr/local/bin/bottle.py' 'usr/local/bin/cbor2' 'usr/local/bin/cheroot' 'usr/local/bin/dotenv'
    'usr/local/bin/f2py' 'usr/local/bin/flask' 'usr/local/bin/futurize' 'usr/local/bin/gpsdclient' 'usr/local/bin/gunicorn'
    'usr/local/bin/gunicornc' 'usr/local/bin/httpx' 'usr/local/bin/isympy' 'usr/local/bin/markdown-it'
    'usr/local/bin/numpy-config' 'usr/local/bin/pasteurize' 'usr/local/bin/proton' 'usr/local/bin/proton-viewer'
    'usr/local/bin/pyav' 'usr/local/bin/torchfrtrace' 'usr/local/bin/torchrun'
    # somebody else's code and media
    'opt/frognet_semantic/ribbit/examples/lispers.net/third_party' 'var/www/html/php/php/PHPMailer/docs' 'var/www/html/media'
    '*.mp4' '*.webm' '*.mov' '*.mkv' '*.avi' '*.wav' '*.mp3' '*.flac' '*.iso' '*.img'
    # leftovers
    '*.log' '*.bak' '*.bak.*' '*.orig' '*.old' '*~' '*.rej' '*.swp' '*.lanonly-removed.*' '*.conf.20*'
)
TOKENIZE=( var/www/html/config.php opt/frognet_semantic/DB_CONFIG.json )

# ---- the tarball -------------------------------------------------------------------------------------------------
have=(); missing=()
for p in "${TAKE[@]}"; do if [ -e "$ROOT/$p" ]; then have+=( "$p" ); else missing+=( "$p" ); fi; done
EX=( --exclude-vcs-ignores ); for p in "${LEAVE[@]}"; do EX+=( "--exclude=$p" ); done
STAGE="$(mktemp -d /tmp/.full_tar.XXXXXX)"; trap 'rm -rf "$STAGE"' EXIT
tok=()
for t in "${TOKENIZE[@]}"; do
    [ -f "$ROOT/$t" ] || continue
    mkdir -p "$STAGE/$(dirname "$t")"
    sed -E "s/(define\('DB_PASS', *')[^']*(')/\1__FROGNET_DB_PASS__\2/; s/(\"password\" *: *\")[^\"]*(\")/\1__FROGNET_DB_PASS__\2/" "$ROOT/$t" > "$STAGE/$t"
    grep -q '__FROGNET_DB_PASS__' "$STAGE/$t" || { echo "ERROR: $t: its password field did not match; not shipping a live credential" >&2; exit 1; }
    EX+=( "--exclude=$t" ); tok+=( "$t" )
done
for l in LICENSE COPYRIGHT README.md CLAIMS.md AI_READ_FIRST.md; do   # the repository's root copies
    [ -f "$ROOT/usr/local/share/frognet/$l" ] && cp "$ROOT/usr/local/share/frognet/$l" "$STAGE/$l" && tok+=( "$l" )
done
OUT="$OUTPUT/frognet-source-$(hostname -s 2>/dev/null || echo node)-$(date +%Y%m%d-%H%M).tgz"
TAR="${OUT%.tgz}.tar"
tar -cf "$TAR" "${EX[@]}" -C "$ROOT" "${have[@]}"
[ ${#tok[@]} -eq 0 ] || tar -rf "$TAR" -C "$STAGE" "${tok[@]}"
gzip -f "$TAR"; mv "$TAR.gz" "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1), $(tar tzf "$OUT" | grep -vc '/$') files)"
for p in opt/frognet_semantic opt/fdf usr/local/bin var/www/html etc; do
    printf '  %6d  %s\n' "$(tar tzf "$OUT" | grep -v '/$' | grep -c "^$p/")" "$p"
done
[ ${#missing[@]} -eq 0 ] || echo "not on this node (left out): ${missing[*]}"
# [FULL_TAR_SAYS_ITS_RELEASE_V1] the tarball carries THIS node's FrogNet: say which release that is, and say it loudly
# when this node has no release stamp (a node installed from such a tarball has no /opt/frognet_semantic/BUILD either,
# and its monitor says "/opt/frognet_semantic/BUILD: cannot be read"). Reported, not refused.
if [ -f "$ROOT/opt/frognet_semantic/BUILD" ]; then
    echo "release: $(grep -vE '^#' "$ROOT/opt/frognet_semantic/BUILD" | tr '\n' ' ')"
else
    echo "WARNING: this node has no /opt/frognet_semantic/BUILD: the tarball carries no release stamp. Make it on a node" >&2
    echo "         whose FrogNet was installed by the current installer (it writes BUILD), or the target's monitor says so." >&2
fi
