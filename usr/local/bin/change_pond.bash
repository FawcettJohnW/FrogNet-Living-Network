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
# change_pond.sh — move this node into a different pond.
#
# What it does, in order:
#   1. Stops frognet-tunnel-daemon-v3 so it can't race the config rewrite.
#   2. Backs up broker.conf, tunnel.conf, pond.conf, the WG keypair.
#   3. Rewrites broker.conf (POND_NAME, optionally BROKER_URL).
#   4. Rewrites tunnel.conf (GROUP_NAME, optionally BROKER_URL).
#   5. Rewrites pond.conf (POND_NAME) if it exists.
#   6. Deletes the WG keypair so setup_v3 generates a fresh one
#      (avoids carrying an old-pond pubkey into the new pond).
#   7. Deletes cached broker state so the daemon doesn't try to
#      reconcile against ghosts from the old pond.
#   8. Runs setup_lillypad_v4 ONLY if the node hasn't been through
#      basic setup yet (no pre-existing broker.conf).
#   9. Runs /usr/local/bin/frognet-tunnel-setup-v3.sh to register
#      in the new pond and start the daemon.
#  10. Runs runMerge.bash to propagate the change locally.
#
# The broker side handles the cross-pond migration automatically
# (RECONFIG_ARCHIVE_V1 retires the old-pond row by MAC, V1.1 frees
# the pubkey UNIQUE slot).  No manual broker cleanup required.
#
# Usage:
#   sudo change_pond.sh <new-pond-name>
#   sudo change_pond.sh <new-pond-name> --broker https://other.example.com/frognet-broker-v4
#
# Examples:
#   sudo change_pond.sh batpond
#   sudo change_pond.sh ratpond --broker https://streamingfrog.com/frognet-broker-v4

set -eu

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

log()  { echo "[change_pond] $(date '+%H:%M:%S') $*"; }
die()  { log "FATAL: $*" >&2; exit 1; }
warn() { log "WARNING: $*" >&2; }

[[ $EUID -eq 0 ]] || die "Must run as root"

NEW_POND="${1:-}"
[[ -n "$NEW_POND" ]] || die "Usage: $0 <new-pond-name> [--broker <url>]"
shift

NEW_BROKER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --broker) NEW_BROKER="$2"; shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# [ONE_CONF_V1] one config file; the other names remain only so the backup
# sweep still collects any pre-consolidation leftovers.
TUNNEL_CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"
BROKER_CONF="/etc/frognet/broker.conf"
POND_CONF="/etc/frognet/pond.conf"
PRIVKEY_FILE="/var/lib/frognet-tunnel/node_private.key"
PUBKEY_FILE="/var/lib/frognet-tunnel/node_public.key"
LAST_BROKER_STATE="/var/lib/frognet-tunnel/last_broker_state.json"
BROKER_CHANNELS="/var/lib/frognet-tunnel/broker_channels.json"
CHANNEL_MAP="/var/lib/frognet-tunnel/channel_map.json"
HANDSHAKE_RTTS="/var/lib/frognet-tunnel/handshake_rtts.json"
ACTIVE_DIR="/var/lib/frognet-tunnel/active"
SETUP_V3="/usr/local/bin/frognet-tunnel-setup-v3.sh"
SETUP_LILLYPAD="/usr/local/bin/setup_lillypad_v4.bash"
RUN_MERGE="/usr/local/bin/runMerge.bash"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/backups/frognet_pond_change_${TS}"

# ---------------------------------------------------------------------------
# Existing-state detection
# ---------------------------------------------------------------------------

HAD_BROKER_CONF=0
[[ -f "$TUNNEL_CONF" ]] && HAD_BROKER_CONF=1

OLD_POND=""
OLD_BROKER=""
if [[ $HAD_BROKER_CONF -eq 1 ]]; then
    OLD_POND=$(grep -m1 -E '^(POND_NAME|GROUP_NAME)=' "$TUNNEL_CONF" | cut -d= -f2- | tr -d '[:space:]' || true)
    OLD_BROKER=$(grep -m1 '^BROKER_URL=' "$TUNNEL_CONF" | cut -d= -f2- | tr -d '[:space:]' || true)
fi

# Broker URL resolution: --broker wins, else keep current, else require.
if [[ -z "$NEW_BROKER" ]]; then
    if [[ -n "$OLD_BROKER" ]]; then
        NEW_BROKER="$OLD_BROKER"
        log "Keeping existing BROKER_URL: $NEW_BROKER"
    else
        die "No --broker given and no existing BROKER_URL in $BROKER_CONF"
    fi
fi

if [[ "$NEW_POND" == "$OLD_POND" && "$NEW_BROKER" == "$OLD_BROKER" ]]; then
    log "Already in pond '$NEW_POND' on broker '$NEW_BROKER' — nothing to do"
    exit 0
fi

log "Pond change: ${OLD_POND:-<none>} → $NEW_POND"
[[ "$NEW_BROKER" != "$OLD_BROKER" ]] && log "Broker change: ${OLD_BROKER:-<none>} → $NEW_BROKER"

# ---------------------------------------------------------------------------
# 1. Stop the services that own / observe tunnel state
# ---------------------------------------------------------------------------
# All three must be down: the tunnel daemon owns wg interface lifecycle,
# the semantic proxy and the per-node daemon both have routes / sockets
# bound to old-pond tunnels.  Stopping them in this order minimizes the
# window where any service holds a reference to an interface we're about
# to delete.

for unit in frognet-tunnel-daemon-v3 frognet-daemon frognet-proxy; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        log "Stopping $unit"
        systemctl stop "$unit"
    fi
done

# ---------------------------------------------------------------------------
# 2. Backups
# ---------------------------------------------------------------------------

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
for f in "$BROKER_CONF" "$TUNNEL_CONF" "$POND_CONF" "$PRIVKEY_FILE" "$PUBKEY_FILE" \
         "$LAST_BROKER_STATE" "$BROKER_CHANNELS" "$CHANNEL_MAP" "$HANDSHAKE_RTTS"; do
    [[ -f "$f" ]] && cp -a "$f" "$BACKUP_DIR/$(basename "$f")"
done
log "Backed up existing config to $BACKUP_DIR"

# ---------------------------------------------------------------------------
# 2b. Deregister old pubkey from the OLD broker / pond
# ---------------------------------------------------------------------------
# Best-effort POST /api/v4/deregister against the OLD broker URL with the
# OLD pubkey.  The broker:
#   - destroys all tunnels involving this node in its old pond
#   - removes chorus memberships (auto-deletes choruses that go empty)
#   - frees the node's IP allocation
#   - deactivates the node row
# Endpoint is idempotent (returns status=not_registered if the pubkey is
# already gone), so failure here is not fatal — we log and proceed.  The
# broker's MAC-identity-change path on the new pond's register would
# eventually retire the old row anyway; explicit deregister is cleaner
# and avoids leaving the old pond temporarily showing this node still
# active.
#
# Skipped when:
#   - no prior pubkey on disk (fresh node, nothing to deregister)
#   - no prior broker URL (shouldn't happen if HAD_BROKER_CONF=1, but
#     guard anyway)

if [[ -f "$PUBKEY_FILE" && -n "$OLD_BROKER" ]]; then
    OLD_PUBKEY="$(/usr/bin/tr -d '[:space:]' < "$PUBKEY_FILE" || true)"
    if [[ -n "$OLD_PUBKEY" ]]; then
        OLD_BROKER_STRIPPED="${OLD_BROKER%/}"
        DEREG_URL="${OLD_BROKER_STRIPPED}/api/v4/deregister"
        log "Deregistering old pubkey from $OLD_BROKER (old pond: ${OLD_POND:-<unknown>})"
        DEREG_PAYLOAD="$(/usr/bin/printf '{"pubkey":"%s"}' "$OLD_PUBKEY")"
        DEREG_RESP="$(/usr/bin/curl -sk --max-time 15 \
            -H "Content-Type: application/json" \
            -X POST -d "$DEREG_PAYLOAD" \
            "$DEREG_URL" 2>&1)" || true
        # Log the raw response on a single line, capped, so we always
        # know what the broker said.  Don't fail the script on a bad
        # response — the broker may be unreachable from this LAN-side
        # node and we still want the pond change to proceed.
        DEREG_RESP_TRIMMED="$(printf '%s' "$DEREG_RESP" | /usr/bin/tr -d '\n' | /usr/bin/cut -c1-200)"
        log "Deregister response: $DEREG_RESP_TRIMMED"
    else
        log "Old pubkey file present but empty — skipping deregister"
    fi
else
    log "No prior pubkey or broker URL — skipping deregister"
fi

# ---------------------------------------------------------------------------
# 3. Rewrite broker.conf
# ---------------------------------------------------------------------------

mkdir -p /etc/frognet
chmod 0755 /etc/frognet
umask 0177

# ---------------------------------------------------------------------------
# 3-5. Rewrite the config
#
# [ONE_CONF_V1] This wrote three files - broker.conf, tunnel.conf and pond.conf -
# because three consumers each read only one of them. They are one file now, so
# this is one key-wise update. Membership fields (PASSCODE, GROUP_TOKEN,
# MAX_TUNNELS, NODE_GUID) are deliberately left alone: step 6 below deletes the
# WG keypair and cached broker state so the node re-registers into the new pond.
# ---------------------------------------------------------------------------
. "${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"

fn_conf_backup >/dev/null
fn_conf_set BROKER_URL "$NEW_BROKER"
fn_pond_set "$NEW_POND"
log "Updated $FROGNET_CONF (BROKER_URL + pond=${NEW_POND})"

# ---------------------------------------------------------------------------
# 6. Tear down every WireGuard interface
# ---------------------------------------------------------------------------
# The tunnel daemon creates wg0/wg2/wg3/wg4/... as it brings up channels.
# After a pond change, none of those interfaces belong to the new pond.
# Leaving them up means the bringup phase sees `Loaded N active tunnel(s)
# from disk` and treats them as already-converged — old-pond tunnels
# stay live and old-pond peers stay reachable inside the new pond.
#
# We delete every wireguard-type interface unconditionally; the daemon
# will recreate exactly the ones the new pond needs from broker state.

mapfile -t WG_IFACES < <(/usr/sbin/ip -o link show type wireguard 2>/dev/null | /usr/bin/awk -F': ' '{print $2}' | /usr/bin/awk '{print $1}')
if (( ${#WG_IFACES[@]} > 0 )); then
    log "Tearing down WireGuard interfaces: ${WG_IFACES[*]}"
    for iface in "${WG_IFACES[@]}"; do
        /usr/sbin/ip link delete "$iface" 2>/dev/null || warn "ip link delete $iface failed (continuing)"
    done
else
    log "No WireGuard interfaces present — nothing to tear down"
fi

# ---------------------------------------------------------------------------
# 7. Force fresh WG keypair
# ---------------------------------------------------------------------------

if [[ -f "$PRIVKEY_FILE" || -f "$PUBKEY_FILE" ]]; then
    rm -f "$PRIVKEY_FILE" "$PUBKEY_FILE"
    log "Removed old WG keypair (setup_v3 will generate a new one)"
fi

# ---------------------------------------------------------------------------
# 8. Drop cached broker state — it references old-pond channels
# ---------------------------------------------------------------------------
# last_broker_state.json drives the bringup phase ("Loaded N active
# tunnel(s) from disk").  broker_channels.json and channel_map.json hold
# channel→config mappings.  handshake_rtts.json holds per-channel timing
# data.  Everything under active/ is per-channel runtime state.  All of
# this is old-pond data; the daemon will rebuild it from the broker
# after registration.

for f in "$LAST_BROKER_STATE" "$BROKER_CHANNELS" "$CHANNEL_MAP" "$HANDSHAKE_RTTS"; do
    if [[ -f "$f" ]]; then
        rm -f "$f"
        log "Cleared $(basename "$f")"
    fi
done

if [[ -d "$ACTIVE_DIR" ]]; then
    # Don't remove the dir itself — the daemon expects it to exist.
    # Just empty its contents.
    if find "$ACTIVE_DIR" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
        rm -rf "${ACTIVE_DIR:?}"/* "${ACTIVE_DIR:?}"/.[!.]* 2>/dev/null || true
        log "Cleared $ACTIVE_DIR contents"
    fi
fi

# ---------------------------------------------------------------------------
# 9. setup_lillypad_v4 if this looks like a never-set-up node
# ---------------------------------------------------------------------------

if [[ $HAD_BROKER_CONF -eq 0 ]]; then
    if [[ ! -x "$SETUP_LILLYPAD" ]]; then
        die "No prior broker.conf and $SETUP_LILLYPAD missing — cannot bootstrap"
    fi
    log "No prior broker.conf — running $SETUP_LILLYPAD"
    # setup_lillypad expects a network name positional arg.  We pass
    # the new pond name as a reasonable default; the operator can re-run
    # lillypad later with different LAN options if needed.
    "$SETUP_LILLYPAD" "$NEW_POND" --broker "$NEW_BROKER" --pond "$NEW_POND" \
        || die "setup_lillypad_v4 failed"
fi

# ---------------------------------------------------------------------------
# 10. Register with broker in the new pond
# ---------------------------------------------------------------------------
# setup_v3 generates a fresh keypair, POSTs /api/v4/register with the
# new pond name and fresh pubkey, then starts frognet-tunnel-daemon-v3.
# It does NOT start frognet-proxy or frognet-daemon — we restart those
# explicitly below so we're certain they reload against the new state.

[[ -x "$SETUP_V3" ]] || die "$SETUP_V3 not found or not executable"
log "Running $SETUP_V3"
"$SETUP_V3" || die "frognet-tunnel-setup-v3.sh failed"

# ---------------------------------------------------------------------------
# 11. Bring the rest of the services back
# ---------------------------------------------------------------------------
# setup_v3 already started frognet-tunnel-daemon-v3.  Start the proxy
# and per-node daemon if they're not running, or restart them if they
# are (a restart picks up the new tunnel set cleanly).

for unit in frognet-proxy frognet-daemon; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null || systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q .; then
        log "Restarting $unit"
        systemctl restart "$unit" || warn "$unit restart returned non-zero"
    fi
done

# ---------------------------------------------------------------------------
# 12. runMerge
# ---------------------------------------------------------------------------

if [[ -x "$RUN_MERGE" ]]; then
    log "Running $RUN_MERGE"
    "$RUN_MERGE" || warn "runMerge exited non-zero (review the trace above)"
else
    warn "$RUN_MERGE not found — skipping; you may want to run it by hand"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
log "Pond change complete: ${OLD_POND:-<none>} → $NEW_POND"
log "Broker:               $NEW_BROKER"
log "Backup:               $BACKUP_DIR"
echo
log "To revert: stop the daemon, restore files from the backup dir, re-run setup_v3."
