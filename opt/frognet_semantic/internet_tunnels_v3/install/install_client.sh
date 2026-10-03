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
# install_client_v3.sh - Install FrogNet v3 tunnel daemon on a node
#
# Usage:
#   bash install_client_v3.sh --broker <url> --pond <pond> [--pond-password <pw>]
#
# Example:
#   bash install_client_v3.sh \
#       --broker https://streamingfrog.com:18256 \
#       --pond frognet
#
# Prerequisites:
#   - setup_lillypad.bash has already run (node has LAN identity, dnsmasq, Apache)
#   - frognet-client-v3.tar.gz is in the current directory
#   - wg and wg-quick are installed
#
# What this does:
#   1. Extracts package files to correct locations
#   2. Installs PHP files into Apache document root
#   3. Installs systemd service
#   4. Writes /etc/frognet/broker.conf
#   5. Cleans /etc/frognet/tunnel.conf (removes v2 keys, deduplicates)
#   6. Enables and starts frognet-tunnel-daemon-v3
#   7. Runs frognet_tunnel_setup_v3.sh to register with the broker
# =============================================================================

set -e

log()  { echo "[install_v3] $(date -u +%H:%M:%S) $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }
warn() { log "WARNING: $*"; }

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
BROKER_URL=""
POND_NAME=""
POND_PASSWORD=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --broker)        BROKER_URL="$2";   shift 2 ;;
        --pond)          POND_NAME="$2";     shift 2 ;;
        --pond-password) POND_PASSWORD="$2"; shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done

[[ -n "$BROKER_URL" ]] || die "Usage: $0 --broker <url> --pond <pond>"
[[ -n "$POND_NAME"  ]] || die "Usage: $0 --broker <url> --pond <pond>"
[[ $EUID -eq 0      ]] || die "Must run as root"

TARBALL="$(pwd)/frognet-client-v3.tar.gz"
[[ -f "$TARBALL" ]] || die "frognet-client-v3.tar.gz not found in current directory"

log "Installing FrogNet tunnel daemon v3"
log "  Broker: $BROKER_URL"
log "  Pond:   $POND_NAME"

# ---------------------------------------------------------------------------
# Step 1: Extract package
# ---------------------------------------------------------------------------
log "Step 1: Extracting package..."
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
tar -xzf "$TARBALL" -C "$TMPDIR"
PKG="${TMPDIR}/frognet-client-v3"
[[ -d "$PKG" ]] || die "Bad tarball - missing frognet-client-v3/"

# ---------------------------------------------------------------------------
# Step 2: Install Python package
# ---------------------------------------------------------------------------
log "Step 2: Installing Python package..."
mkdir -p /opt/frognet_semantic/internet_tunnels_v3
for f in __main__.py config.py poll.py wg.py names.py __init__.py; do
    src="${PKG}/internet_tunnels_v3/${f}"
    [[ -f "$src" ]] || die "Tarball missing: internet_tunnels_v3/${f}"
    cp "$src" /opt/frognet_semantic/internet_tunnels_v3/
    log "  installed ${f}"
done

# ---------------------------------------------------------------------------
# Step 3: Install setup script
# ---------------------------------------------------------------------------
log "Step 3: Installing setup script..."
cp "${PKG}/bin/frognet_tunnel_setup_v3.sh" /usr/local/bin/frognet_tunnel_setup_v3.sh
chmod +x /usr/local/bin/frognet_tunnel_setup_v3.sh
log "  installed /usr/local/bin/frognet_tunnel_setup_v3.sh"

# ---------------------------------------------------------------------------
# Step 4: Install PHP files
# ---------------------------------------------------------------------------
log "Step 4: Installing PHP files..."
DOCROOT=""
for d in /var/www/html /var/www; do
    if [[ -d "$d" ]]; then
        DOCROOT="$d"
        break
    fi
done

if [[ -z "$DOCROOT" ]]; then
    warn "No Apache document root found - skipping PHP install"
else
    for f in frognet_chorus.php frognet_pond.php; do
        src="${PKG}/www/${f}"
        if [[ -f "$src" ]]; then
            cp "$src" "${DOCROOT}/${f}"
            log "  installed ${DOCROOT}/${f}"
        else
            warn "${f} not in package - skipping"
        fi
    done
fi

# ---------------------------------------------------------------------------
# Step 5: Install systemd service
# ---------------------------------------------------------------------------
log "Step 5: Installing systemd service..."
cp "${PKG}/systemd/frognet-tunnel-daemon-v3.service" \
   /etc/systemd/system/frognet-tunnel-daemon-v3.service
systemctl daemon-reload
systemctl enable frognet-tunnel-daemon-v3
log "  installed and enabled frognet-tunnel-daemon-v3.service"

# ---------------------------------------------------------------------------
# Step 6: Write broker.conf
# ---------------------------------------------------------------------------
log "Step 6: Writing /etc/frognet/broker.conf..."
mkdir -p /etc/frognet
# [ONE_CONF_V1] one config file; set keys rather than rewriting, or this would
# truncate the membership fields it now shares space with.
. "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
fn_conf_set BROKER_URL "$BROKER_URL"
fn_pond_set "$POND_NAME"
log "  written"

# ---------------------------------------------------------------------------
# Step 7: Clean tunnel.conf
# ---------------------------------------------------------------------------
log "Step 7: Cleaning /etc/frognet/tunnel.conf..."
TUNNEL_CONF=/etc/frognet/tunnel.conf
if [[ -f "$TUNNEL_CONF" ]]; then
    python3 - << PYEOF
path = "${TUNNEL_CONF}"
v2_keys = {"PASSCODE", "GROUP_TOKEN", "GROUP_NAME", "MAX_TUNNELS"}
lines = open(path).readlines()
seen_broker = False
out = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        out.append(line)
        continue
    if "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key in v2_keys:
            continue
        if key == "BROKER_URL":
            if seen_broker:
                continue
            seen_broker = True
            # Update to v3 broker URL
            line = "BROKER_URL=${BROKER_URL}\n"
    out.append(line)
# Ensure BROKER_URL is present
if not seen_broker:
    out.append("BROKER_URL=${BROKER_URL}\n")
open(path, "w").writelines(out)
print("  tunnel.conf cleaned and updated")
PYEOF
else
    log "  no tunnel.conf found - creating minimal one"
    cat > "$TUNNEL_CONF" << TEOF
# FrogNet tunnel configuration
BROKER_URL=${BROKER_URL}
TEOF
    chmod 600 "$TUNNEL_CONF"
fi

# ---------------------------------------------------------------------------
# Step 8: Start the daemon
# ---------------------------------------------------------------------------
log "Step 8: Starting frognet-tunnel-daemon-v3..."
systemctl restart frognet-tunnel-daemon-v3
sleep 2
if systemctl is-active --quiet frognet-tunnel-daemon-v3; then
    log "  daemon running"
else
    log "  daemon failed to start:"
    journalctl -u frognet-tunnel-daemon-v3 -n 10 --no-pager
    die "Daemon did not start"
fi

# ---------------------------------------------------------------------------
# Step 9: Register with broker
# ---------------------------------------------------------------------------
log "Step 9: Registering with broker..."
SETUP_ARGS=""
[[ -n "$POND_PASSWORD" ]] && SETUP_ARGS="--pond-password ${POND_PASSWORD}"
/usr/local/bin/frognet_tunnel_setup_v3.sh $SETUP_ARGS

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log ""
log "============================================"
log "Installation complete"
log ""
log "Service status:"
systemctl status frognet-tunnel-daemon-v3 --no-pager | tail -5
log ""
log "To watch the daemon:"
log "  journalctl -u frognet-tunnel-daemon-v3 -f"
log "============================================"
