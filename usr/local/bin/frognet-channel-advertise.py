#!/opt/frognet_semantic/venv/bin/python3
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
"""
frognet-channel-advertise.py — Advertise a FrogNet tunnel channel.

Opens a websocket to the broker, advertises this node's channel,
and waits for tunnel creation events. When a joiner connects,
the broker pushes WG config and this script brings up the tunnel.

Usage:
    frognet-channel-advertise.py <broker_ws_url> <group_token> <channel_name> <subnet1> [subnet2...]

Example:
    frognet-channel-advertise.py \
        wss://streamingfrog.com:8443/frognet-broker/api/v1/advertise \
        "nEWkmqs..." \
        "ironbox" \
        10.101.20.0/24

Runs as a long-lived daemon. Handles tunnel_created and tunnel_destroyed
events from the broker.

NEVER masks errors.
"""

import os
import sys
import json
import signal
import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: 'websockets' package not installed. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONF_DIR = Path("/etc/wireguard")
STATE_DIR = Path("/var/lib/frognet-tunnel")
LOG_LEVEL = os.environ.get("FROGNET_LOG_LEVEL", "INFO")
LABEL = os.environ.get("FROGNET_TUNNEL_LABEL", "")
RECONNECT_DELAY = 5  # seconds between reconnect attempts

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("frognet.advertise")

# Active tunnels: tunnel_name -> {"iface": "wgN", "conf": path}
active_tunnels: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_local_gateway(subnet: str) -> str:
    """Derive .1 from a subnet CIDR. e.g. 10.101.20.0/24 → 10.101.20.1"""
    network = subnet.split("/")[0]
    parts = network.rsplit(".", 1)
    return f"{parts[0]}.1"


def find_next_wg_iface() -> str:
    """Find the next available wgN interface name."""
    for n in range(17):
        result = subprocess.run(
            ["ip", "link", "show", f"wg{n}"],
            capture_output=True, check=False,
        )
        if result.returncode != 0:
            return f"wg{n}"
    raise RuntimeError("Too many WireGuard interfaces (wg0-wg16 all exist)")


def get_wg_pubkey() -> str:
    """Get or generate this node's WireGuard public key."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    priv_file = STATE_DIR / "node_private.key"
    pub_file = STATE_DIR / "node_public.key"

    if priv_file.exists() and pub_file.exists():
        return pub_file.read_text().strip()

    # Generate new keypair
    priv = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True).stdout.strip()
    pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True).stdout.strip()

    priv_file.write_text(priv)
    priv_file.chmod(0o600)
    pub_file.write_text(pub)
    pub_file.chmod(0o644)

    return pub


def get_wg_privkey() -> str:
    """Read this node's WireGuard private key."""
    return (STATE_DIR / "node_private.key").read_text().strip()


def bring_up_tunnel(tunnel_name: str, wg_config: dict, remote_subnets: list, local_gw: str):
    """Bring up a WireGuard tunnel from broker-pushed config."""
    iface = find_next_wg_iface()
    privkey = get_wg_privkey()

    conf_path = CONF_DIR / f"{iface}.conf"
    conf_path.write_text(f"""# FrogNet Tunnel — {tunnel_name}
# Auto-configured by frognet-channel-advertise.py

[Interface]
Address = {wg_config['edge_ip']}/{wg_config['edge_mask']}
PrivateKey = {privkey}

[Peer]
PublicKey = {wg_config['droplet_pubkey']}
Endpoint = {wg_config['droplet_endpoint']}
AllowedIPs = {wg_config['allowed_ips']}
PersistentKeepalive = 25
""")
    conf_path.chmod(0o600)

    # Bring up
    result = subprocess.run(
        ["wg-quick", "up", iface],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.error("Failed to bring up %s: %s", iface, result.stderr.strip())
        raise RuntimeError(f"wg-quick up {iface} failed: {result.stderr.strip()}")

    # Install routes — dev wgN src local_gw, no via
    for subnet in remote_subnets:
        subprocess.run(
            ["ip", "route", "replace", subnet, "dev", iface, "src", local_gw],
            capture_output=True, check=False,
        )
        log.info("Route: %s dev %s src %s", subnet, iface, local_gw)

    active_tunnels[tunnel_name] = {"iface": iface, "conf": str(conf_path)}
    log.info("Tunnel '%s' UP on %s", tunnel_name, iface)


def tear_down_tunnel(tunnel_name: str):
    """Tear down a WireGuard tunnel."""
    info = active_tunnels.pop(tunnel_name, None)
    if not info:
        log.warning("Tunnel '%s' not in active list — nothing to tear down", tunnel_name)
        return

    iface = info["iface"]
    subprocess.run(["wg-quick", "down", iface], capture_output=True, check=False)
    log.info("Tunnel '%s' DOWN (%s)", tunnel_name, iface)


def tear_down_all():
    """Tear down all active tunnels on shutdown."""
    for name in list(active_tunnels.keys()):
        tear_down_tunnel(name)


# ---------------------------------------------------------------------------
# Main websocket loop
# ---------------------------------------------------------------------------

async def run(ws_url: str, group_token: str, channel_name: str, subnets: list[str]):
    pubkey = get_wg_pubkey()
    local_gw = get_local_gateway(subnets[0])
    label = LABEL or channel_name

    log.info("Advertising channel '%s' (pubkey=%s…, subnets=%s, gw=%s)",
             channel_name, pubkey[:12], subnets, local_gw)

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                # Send advertisement
                await ws.send(json.dumps({
                    "group_token": group_token,
                    "channel_name": channel_name,
                    "pubkey": pubkey,
                    "frognet_subnets": subnets,
                    "label": label,
                }))

                # Wait for confirmation
                raw = await ws.recv()
                msg = json.loads(raw)

                if "error" in msg:
                    log.error("Advertisement rejected: %s", msg["error"])
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                log.info("Channel '%s' advertised — %s", channel_name, msg.get("status"))

                # Event loop
                async for raw in ws:
                    msg = json.loads(raw)
                    event = msg.get("event") or msg.get("type", "")

                    if event == "tunnel_created":
                        tunnel_name = msg["tunnel_name"]
                        wg_config = msg["wg_config"]
                        remote_subnets = msg.get("joiner_subnets", wg_config.get("remote_subnets", []))
                        log.info("Tunnel '%s' — bringing up WG", tunnel_name)
                        try:
                            bring_up_tunnel(tunnel_name, wg_config, remote_subnets, local_gw)
                        except Exception as e:
                            log.error("Failed to bring up tunnel '%s': %s", tunnel_name, e)

                    elif event == "tunnel_destroyed":
                        tunnel_name = msg["tunnel_name"]
                        log.info("Tunnel '%s' — tearing down", tunnel_name)
                        tear_down_tunnel(tunnel_name)

                    elif event == "ping" or msg.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))

                    else:
                        log.debug("Unhandled message: %s", msg)

        except websockets.ConnectionClosed as e:
            log.warning("Websocket closed: %s — reconnecting in %ds", e, RECONNECT_DELAY)
        except ConnectionRefusedError:
            log.warning("Connection refused — broker down? Retrying in %ds", RECONNECT_DELAY)
        except Exception as e:
            log.exception("Websocket error: %s — reconnecting in %ds", e, RECONNECT_DELAY)

        # Tear down all tunnels on disconnect — they'll be recreated on reconnect+rejoin
        tear_down_all()
        await asyncio.sleep(RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 5:
        print(f"Usage: {sys.argv[0]} <broker_ws_url> <group_token> <channel_name> <subnet1> [subnet2...]",
              file=sys.stderr)
        print("", file=sys.stderr)
        print("Example:", file=sys.stderr)
        print(f"  {sys.argv[0]} wss://streamingfrog.com:8443/frognet-broker/api/v1/advertise \\", file=sys.stderr)
        print('      "nEWkmqs..." "ironbox" 10.101.20.0/24', file=sys.stderr)
        sys.exit(1)

    ws_url = sys.argv[1]
    group_token = sys.argv[2]
    channel_name = sys.argv[3]
    subnets = sys.argv[4:]

    if os.getuid() != 0:
        print("ERROR: Must run as root (WireGuard configuration requires root)", file=sys.stderr)
        sys.exit(1)

    # Graceful shutdown
    def shutdown(sig, frame):
        log.info("Shutting down (signal %d)…", sig)
        tear_down_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    CONF_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    asyncio.run(run(ws_url, group_token, channel_name, subnets))


if __name__ == "__main__":
    main()
