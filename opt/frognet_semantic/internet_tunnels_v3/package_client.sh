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
# =============================================================================
# package_client_v3.sh - Build the v3 client tarball from a working node
#
# Run on TealBox (or any node with a working v3 installation).
# Produces: frognet-client-v3.tar.gz in the current directory.
#
# Usage:
#   bash package_client_v3.sh
# =============================================================================

set -e

log() { echo "[package] $*"; }
die() { log "FATAL: $*" >&2; exit 1; }

PKGNAME="frognet-client-v3"
TMPDIR="$(mktemp -d)"
WORK="${TMPDIR}/${PKGNAME}"

mkdir -p "${WORK}/internet_tunnels_v3"
mkdir -p "${WORK}/systemd"
mkdir -p "${WORK}/bin"
mkdir -p "${WORK}/www"

# ---------------------------------------------------------------------------
# Python package
# ---------------------------------------------------------------------------
log "Collecting Python package files..."
for f in __main__.py config.py poll.py wg.py names.py __init__.py; do
    src="/opt/frognet_semantic/internet_tunnels_v3/${f}"
    [[ -f "$src" ]] || die "Missing: $src"
    cp "$src" "${WORK}/internet_tunnels_v3/${f}"
    log "  + internet_tunnels_v3/${f}"
done

# ---------------------------------------------------------------------------
# Setup script
# ---------------------------------------------------------------------------
log "Collecting setup script..."
src="/opt/frognet_semantic/internet_tunnels_v3/frognet_tunnel_setup_v3.sh"
[[ -f "$src" ]] || die "Missing: $src"
cp "$src" "${WORK}/bin/frognet_tunnel_setup_v3.sh"
chmod +x "${WORK}/bin/frognet_tunnel_setup_v3.sh"
log "  + bin/frognet_tunnel_setup_v3.sh"

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------
log "Collecting systemd service..."
src="/etc/systemd/system/frognet-tunnel-daemon-v3.service"
[[ -f "$src" ]] || die "Missing: $src"
cp "$src" "${WORK}/systemd/frognet-tunnel-daemon-v3.service"
log "  + systemd/frognet-tunnel-daemon-v3.service"

# ---------------------------------------------------------------------------
# PHP files - drop into document root on install
# ---------------------------------------------------------------------------
log "Collecting PHP files..."
for f in frognet_chorus.php frognet_pond.php; do
    # Look in common locations
    found=""
    for d in /var/www/html \
              /opt/frognet_semantic/internet_tunnels_v3 \
              /opt/frognet_semantic; do
        if [[ -f "${d}/${f}" ]]; then
            found="${d}/${f}"
            break
        fi
    done
    if [[ -n "$found" ]]; then
        cp "$found" "${WORK}/www/${f}"
        log "  + www/${f}  (from $found)"
    else
        log "  WARNING: ${f} not found - skipping"
    fi
done

# ---------------------------------------------------------------------------
# Build tarball
# ---------------------------------------------------------------------------
OUTFILE="$(pwd)/${PKGNAME}.tar.gz"
tar -czf "$OUTFILE" -C "$TMPDIR" "$PKGNAME"
rm -rf "$TMPDIR"

log ""
log "Package: ${OUTFILE}"
log ""
log "Install on any node:"
log "  bash install_client_v3.sh --broker <url> --pond <pond>"

