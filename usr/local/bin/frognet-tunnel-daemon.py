#!/usr/bin/env python3
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
frognet-tunnel-daemon — Persistent tunnel lifecycle manager.

Handles ALL tunnel lifecycle automatically:
  1. ADVERTISE — websocket to broker, advertises this node's channel/subnets.
     When a remote node joins, broker pushes tunnel_created; we bring up our side.
  2. POLL & AUTO-JOIN — periodically queries broker for other channels in the
     group, auto-joins any channel whose subnets we can't already reach.
  3. IDLE TEARDOWN — monitors traffic on each wg interface, tears down tunnels
     that go idle for IDLE_TIMEOUT_SEC seconds.
  4. CLEANUP — on startup, reconciles live wg interfaces against state files,
     removes orphans.

Channel name format: {hostname}-{subnet_prefix}
  e.g. "BlackBox-10.101.10"

Reads config from /etc/frognet/tunnel.conf (written by frognet-tunnel-setup.sh).
State files in /var/lib/frognet-tunnel/active/*.json (same format as before —
sync_interfaces.sh Seed 7/8 reads these unchanged).

Runs as root.  Intended for systemd:
  [Service]
  ExecStart=/usr/local/bin/frognet-tunnel-daemon
  Restart=always

NEVER masks errors. All failures visible.
"""

import json
import logging
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import base64
import glob
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("frognet.tunnel-daemon")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONF_FILE = Path("/etc/frognet/tunnel.conf")
CONF_DIR = Path("/etc/wireguard")
STATE_DIR = Path("/var/lib/frognet-tunnel")
ACTIVE_DIR = STATE_DIR / "active"
PID_FILE = Path("/run/frognet/tunnel-daemon.pid")

IDLE_TIMEOUT_SEC = int(os.environ.get("FROGNET_TUNNEL_IDLE_SEC", "600"))  # 10 min
POLL_INTERVAL_SEC = int(os.environ.get("FROGNET_TUNNEL_POLL_SEC", "30"))
METRICS_INTERVAL_SEC = int(os.environ.get("FROGNET_TUNNEL_METRICS_SEC", "30"))
RECONNECT_BASE_SEC = 5
RECONNECT_MAX_SEC = 60

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

running = True

# Tunnels WE created by joining a remote channel (outbound)
joined_tunnels: Dict[str, dict] = {}
# Tunnels created by the broker when someone joins OUR channel (inbound)
hosted_tunnels: Dict[str, dict] = {}
# Combined view for traffic monitoring
all_tunnels: Dict[str, dict] = {}  # tunnel_name -> {"iface": "wgN", "direction": "in|out", ...}

# Previous traffic counters for idle detection
traffic_prev: Dict[str, int] = {}  # iface -> previous (rx_bytes + tx_bytes)
idle_since: Dict[str, float] = {}  # iface -> timestamp when traffic last seen

# Config loaded from tunnel.conf
BROKER_URL = ""
GROUP_TOKEN = ""
PASSCODE = ""
GROUP_NAME = ""
MAX_TUNNELS = 4

# Node identity
HOSTNAME = ""
NODE_NAME = ""         # e.g. "BlackBox" — from dnsmasq domain= directive
LOCAL_SUBNET = ""      # e.g. "10.101.10.0/24"
LOCAL_GW = ""          # e.g. "10.101.10.1"
SUBNET_PREFIX = ""     # e.g. "10.101.10"
SUBNET_OCTET = 0       # e.g. 10 — third octet, used for directionality
CHANNEL_NAME = ""      # e.g. "BlackBox-10.101.10"
PUBKEY = ""
PRIVKEY = ""

# Lock for tunnel state modifications
tunnel_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _shutdown(sig, frame):
    global running
    log.info("Shutting down (signal %d)", sig)
    running = False

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def load_config():
    global BROKER_URL, GROUP_TOKEN, PASSCODE, GROUP_NAME, MAX_TUNNELS
    global HOSTNAME, NODE_NAME, LOCAL_SUBNET, LOCAL_GW, SUBNET_PREFIX
    global SUBNET_OCTET, CHANNEL_NAME
    global PUBKEY, PRIVKEY

    if not CONF_FILE.exists():
        log.error("Config file %s not found. Run frognet-tunnel-setup first.", CONF_FILE)
        sys.exit(1)

    conf = {}
    with open(CONF_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()

    BROKER_URL = conf.get("BROKER_URL", "")
    GROUP_TOKEN = conf.get("GROUP_TOKEN", "")
    PASSCODE = conf.get("PASSCODE", "")
    GROUP_NAME = conf.get("GROUP_NAME", "")
    MAX_TUNNELS = int(conf.get("MAX_TUNNELS", "4"))

    if not BROKER_URL or not GROUP_TOKEN:
        log.error("BROKER_URL or GROUP_TOKEN missing in %s", CONF_FILE)
        sys.exit(1)

    # Node identity — node name from dnsmasq domain= directive
    HOSTNAME = subprocess.check_output(["hostname", "-s"], text=True).strip()

    NODE_NAME = ""
    for dnsmasq_conf in glob.glob("/etc/dnsmasq.d/*.conf"):
        try:
            with open(dnsmasq_conf) as f:
                for dline in f:
                    dline = dline.strip()
                    if dline.startswith("domain="):
                        NODE_NAME = dline.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
        if NODE_NAME:
            break

    if not NODE_NAME:
        log.error("No domain= found in /etc/dnsmasq.d/*.conf — cannot determine node name")
        sys.exit(1)

    # Detect local subnet
    out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True)
    for line in out.splitlines():
        line = line.strip()
        if "inet 10.101." in line:
            # e.g. "inet 10.101.10.1/24 ..."
            parts = line.split()
            idx = next((i for i, p in enumerate(parts) if p == "inet"), -1)
            if idx >= 0 and idx + 1 < len(parts):
                addr_cidr = parts[idx + 1]  # "10.101.10.1/24"
                ip = addr_cidr.split("/")[0]
                LOCAL_SUBNET = ip.rsplit(".", 1)[0] + ".0/24"
                LOCAL_GW = ip.rsplit(".", 1)[0] + ".1"
                SUBNET_PREFIX = ip.rsplit(".", 1)[0]
                SUBNET_OCTET = int(ip.split(".")[2])
                break

    if not LOCAL_SUBNET:
        log.error("No 10.101.x.x address found. Is this a FrogNet node?")
        sys.exit(1)

    CHANNEL_NAME = f"{NODE_NAME}-{SUBNET_PREFIX}"

    # WireGuard keys
    privkey_file = STATE_DIR / "node_private.key"
    pubkey_file = STATE_DIR / "node_public.key"
    if not privkey_file.exists() or not pubkey_file.exists():
        log.error("WireGuard keypair missing. Run frognet-tunnel-setup first.")
        sys.exit(1)

    PRIVKEY = privkey_file.read_text().strip()
    PUBKEY = pubkey_file.read_text().strip()

    log.info("Config: broker=%s group=%s", BROKER_URL, GROUP_NAME)
    log.info("Identity: node=%s host=%s subnet=%s channel=%s octet=%d",
             NODE_NAME, HOSTNAME, LOCAL_SUBNET, CHANNEL_NAME, SUBNET_OCTET)


# ---------------------------------------------------------------------------
# WireGuard interface helpers
# ---------------------------------------------------------------------------

def find_next_wg_iface() -> str:
    """Find next free wgN interface name."""
    for n in range(32):
        r = subprocess.run(["ip", "link", "show", f"wg{n}"],
                           capture_output=True, check=False)
        if r.returncode != 0:
            return f"wg{n}"
    raise RuntimeError("Too many WireGuard interfaces (32+)")


def get_iface_bytes(iface: str) -> int:
    """Read total rx+tx bytes from /sys/class/net/<iface>/statistics."""
    total = 0
    base = f"/sys/class/net/{iface}/statistics"
    for counter in ("rx_bytes", "tx_bytes"):
        path = f"{base}/{counter}"
        try:
            with open(path) as f:
                total += int(f.read().strip())
        except (OSError, ValueError):
            pass
    return total


def local_subnets_from_routes() -> Set[str]:
    """Return set of 10.101.x.0/24 subnets we currently have kernel routes to."""
    subnets = set()
    try:
        out = subprocess.check_output(["ip", "route", "show"], text=True)
        for line in out.splitlines():
            parts = line.split()
            if not parts:
                continue
            cidr = parts[0]
            if cidr.startswith("10.101.") and cidr.endswith("/24"):
                subnets.add(cidr)
    except Exception as e:
        log.error("Failed to read routes: %s", e)
    return subnets


def _parse_channel_name(ch_name: str) -> Tuple[str, int]:
    """
    Parse channel name like 'BlackBox-10.101.10' into (node_name, subnet_octet).
    Returns ("", 0) if unparseable.
    """
    idx = ch_name.rfind("-10.101.")
    if idx < 0:
        return ("", 0)
    node_name = ch_name[:idx]
    prefix = ch_name[idx + 1:]  # "10.101.10"
    parts = prefix.split(".")
    if len(parts) != 3:
        return ("", 0)
    try:
        octet = int(parts[2])
    except ValueError:
        return ("", 0)
    return (node_name, octet)


def _deterministic_tunnel_name(our_name: str, our_octet: int,
                                remote_name: str, remote_octet: int) -> str:
    """
    Compute deterministic tunnel name: {lower_name}-to-{higher_name}.
    Lower subnet octet is always first.
    """
    if our_octet < remote_octet:
        return f"{our_name}-to-{remote_name}"
    else:
        return f"{remote_name}-to-{our_name}"


def _we_are_joiner(remote_octet: int) -> bool:
    """
    Return True if WE should join THEM (we are the higher subnet).
    Lower hosts, higher joins.
    """
    return SUBNET_OCTET > remote_octet


def channels_we_are_connected_to() -> Set[str]:
    """Return set of channel names we have active tunnels to (joined or hosted)."""
    names = set()
    with tunnel_lock:
        for info in all_tunnels.values():
            ch = info.get("channel_name", "")
            if ch:
                names.add(ch)
    return names


# ---------------------------------------------------------------------------
# Stale tunnel cleanup (runs at startup and before new tunnel creation)
# ---------------------------------------------------------------------------

def cleanup_stale_tunnels_for_subnets(target_subnets: List[str]):
    """Remove existing tunnels whose remote_subnets overlap with target_subnets."""
    if not target_subnets:
        return

    log.info("CLEANUP: checking for stale tunnels to %s", target_subnets)
    target_set = set(target_subnets)

    # Pass 1: state-file cleanup — only tear down tunnels that are
    # NOT the canonical tunnel for these subnets (stale/duplicate)
    for sf_path in glob.glob(str(ACTIVE_DIR / "*.json")):
        try:
            with open(sf_path) as f:
                sf_data = json.load(f)
            sf_subnets = set(sf_data.get("remote_subnets", []))
            sf_iface = sf_data.get("interface", "")
            sf_tname = sf_data.get("tunnel_name", "")
            sf_channel = sf_data.get("channel_name", "")
            overlap = sf_subnets & target_set
            if not overlap:
                continue

            # Check if this is the canonical tunnel — if so, keep it
            remote_name, remote_octet = _parse_channel_name(sf_channel)
            if remote_name:
                canonical_name = _deterministic_tunnel_name(
                    NODE_NAME, SUBNET_OCTET, remote_name, remote_octet)
                if sf_tname == canonical_name:
                    log.info("CLEANUP: %s is canonical tunnel '%s', keeping",
                             sf_path, sf_tname)
                    continue

            log.info("CLEANUP: %s (%s on %s) overlaps %s — stale, removing",
                     sf_path, sf_tname, sf_iface, overlap)

            # Tear down
            if sf_iface:
                _teardown_wg_iface(sf_iface)

            # Remove state
            try:
                os.unlink(sf_path)
            except OSError:
                pass

            # Remove from our tracking
            with tunnel_lock:
                all_tunnels.pop(sf_tname, None)
                joined_tunnels.pop(sf_tname, None)
                hosted_tunnels.pop(sf_tname, None)

        except (json.JSONDecodeError, OSError) as e:
            log.warning("CLEANUP: bad state file %s: %s — removing", sf_path, e)
            try:
                os.unlink(sf_path)
            except OSError:
                pass

    # Pass 2: orphan route cleanup
    for sn in target_subnets:
        try:
            result = subprocess.run(["ip", "route", "show", sn],
                                    capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                if "scope link" not in line or "dev wg" not in line:
                    continue
                import re
                m = re.search(r'dev (wg\d+)', line)
                if not m:
                    continue
                old_iface = m.group(1)
                log.info("CLEANUP: orphan route %s on %s", sn, old_iface)
                subprocess.run(["ip", "route", "del", sn, "dev", old_iface, "scope", "link"],
                               check=False, capture_output=True)
        except Exception as e:
            log.debug("CLEANUP: route check error: %s", e)


def _teardown_wg_iface(iface: str):
    """Bring down a single WireGuard interface."""
    conf_path = CONF_DIR / f"{iface}.conf"
    try:
        subprocess.run(["ip", "link", "show", iface],
                       capture_output=True, check=True)
    except subprocess.CalledProcessError:
        # Interface doesn't exist, just clean up conf
        conf_path.unlink(missing_ok=True)
        return

    if conf_path.exists():
        subprocess.run(["wg-quick", "down", iface], capture_output=True, check=False)
    else:
        subprocess.run(["ip", "link", "set", iface, "down"], capture_output=True, check=False)
        subprocess.run(["ip", "link", "delete", iface], capture_output=True, check=False)
    conf_path.unlink(missing_ok=True)
    log.info("Torn down %s", iface)


def startup_reconcile():
    """Reconcile live wg interfaces against state files at startup."""
    log.info("Startup reconciliation...")

    # Pass 1: live wg interfaces with no state file → orphans
    try:
        result = subprocess.run(["wg", "show", "interfaces"],
                                capture_output=True, text=True, check=False)
        live_ifaces = set(result.stdout.strip().split()) if result.stdout.strip() else set()
    except Exception:
        live_ifaces = set()

    claimed_ifaces = set()
    for sf_path in glob.glob(str(ACTIVE_DIR / "*.json")):
        try:
            with open(sf_path) as f:
                sf_data = json.load(f)
            iface = sf_data.get("interface", "")
            if iface:
                claimed_ifaces.add(iface)
        except (json.JSONDecodeError, OSError):
            log.warning("Bad state file %s — removing", sf_path)
            try:
                os.unlink(sf_path)
            except OSError:
                pass

    orphans = live_ifaces - claimed_ifaces
    for iface in orphans:
        log.warning("Orphan wg interface %s (no state file) — tearing down", iface)
        _teardown_wg_iface(iface)

    # Pass 2: state files whose interface is dead → remove state
    for sf_path in glob.glob(str(ACTIVE_DIR / "*.json")):
        try:
            with open(sf_path) as f:
                sf_data = json.load(f)
            iface = sf_data.get("interface", "")
            if iface and iface not in live_ifaces:
                log.warning("Ghost state file %s (iface %s is dead) — removing", sf_path, iface)
                os.unlink(sf_path)
        except (json.JSONDecodeError, OSError):
            try:
                os.unlink(sf_path)
            except OSError:
                pass

    log.info("Reconciliation done. Live: %s, Claimed: %s, Orphans removed: %s",
             live_ifaces, claimed_ifaces, orphans)


# ---------------------------------------------------------------------------
# Tunnel bring-up / tear-down
# ---------------------------------------------------------------------------

def bring_up_tunnel(tunnel_name: str, wg_config: dict,
                    remote_subnets: List[str], channel_name: str,
                    direction: str) -> str:
    """
    Create WireGuard interface, install routes, write state file.
    Returns the interface name.
    """
    with tunnel_lock:
        # Cleanup any stale tunnels to these subnets first
        cleanup_stale_tunnels_for_subnets(remote_subnets)

        iface = find_next_wg_iface()
        conf_path = CONF_DIR / f"{iface}.conf"

        conf_path.write_text(
            f"# FrogNet Tunnel — {tunnel_name}\n"
            f"[Interface]\n"
            f"Address = {wg_config['edge_ip']}/{wg_config['edge_mask']}\n"
            f"PrivateKey = {PRIVKEY}\n"
            f"Table = off\n"
            f"\n"
            f"[Peer]\n"
            f"PublicKey = {wg_config['droplet_pubkey']}\n"
            f"Endpoint = {wg_config['droplet_endpoint']}\n"
            f"AllowedIPs = {wg_config['allowed_ips']}\n"
            f"PersistentKeepalive = 25\n"
        )
        conf_path.chmod(0o600)

        r = subprocess.run(["wg-quick", "up", iface],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            log.error("wg-quick up %s failed: %s", iface, r.stderr.strip())
            conf_path.unlink(missing_ok=True)
            raise RuntimeError(f"wg-quick up failed: {r.stderr.strip()}")

        # Install routes to remote subnets
        for sn in remote_subnets:
            subprocess.run(["ip", "route", "replace", sn, "dev", iface, "src", LOCAL_GW],
                           capture_output=True, check=False)
            log.info("Route: %s dev %s src %s", sn, iface, LOCAL_GW)

        # Write state file (same format sync_interfaces Seed 7 expects)
        state = {
            "interface": iface,
            "tunnel_name": tunnel_name,
            "channel_name": channel_name,
            "remote_subnets": remote_subnets,
            "edge_ip": wg_config.get("edge_ip", ""),
            "droplet_endpoint": wg_config.get("droplet_endpoint", ""),
            "local_gateway": LOCAL_GW,
            "direction": direction,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        state_path = ACTIVE_DIR / f"{tunnel_name}.json"
        state_path.write_text(json.dumps(state, indent=4))

        # Track internally
        info = {"iface": iface, "channel_name": channel_name,
                "direction": direction, "remote_subnets": remote_subnets}
        all_tunnels[tunnel_name] = info
        if direction == "out":
            joined_tunnels[tunnel_name] = info
        else:
            hosted_tunnels[tunnel_name] = info

        # Initialize traffic tracking
        traffic_prev[iface] = get_iface_bytes(iface)
        idle_since[iface] = time.time()

    log.info("Tunnel '%s' UP on %s (direction=%s, subnets=%s)",
             tunnel_name, iface, direction, remote_subnets)

    # Flush discovery cache and trigger merge in background
    def _post_tunnel():
        for sn in remote_subnets:
            gw = sn.replace(".0/24", ".1")
            subprocess.run(["/usr/local/bin/frognet_discovery_cache.sh", "reset", gw],
                           capture_output=True, check=False)
        subprocess.run(["/usr/local/bin/runMerge.bash"],
                       capture_output=True, text=True, timeout=120, check=False)
        log.info("Post-tunnel merge completed for '%s'", tunnel_name)

    threading.Thread(target=_post_tunnel, daemon=True, name=f"merge-{tunnel_name}").start()
    return iface


def tear_down_tunnel(tunnel_name: str):
    """Tear down a tunnel by name."""
    with tunnel_lock:
        info = all_tunnels.pop(tunnel_name, None)
        joined_tunnels.pop(tunnel_name, None)
        hosted_tunnels.pop(tunnel_name, None)

    if not info:
        log.warning("tear_down_tunnel: '%s' not in tracking", tunnel_name)
        return

    iface = info.get("iface", "")
    if iface:
        _teardown_wg_iface(iface)
        traffic_prev.pop(iface, None)
        idle_since.pop(iface, None)

    state_path = ACTIVE_DIR / f"{tunnel_name}.json"
    state_path.unlink(missing_ok=True)

    log.info("Tunnel '%s' DOWN (%s)", tunnel_name, iface)


def tear_down_all():
    """Tear down all tunnels we manage."""
    with tunnel_lock:
        names = list(all_tunnels.keys())
    for name in names:
        tear_down_tunnel(name)


# ---------------------------------------------------------------------------
# Minimal WebSocket client (from frognet-tunnel-host, proven working)
# ---------------------------------------------------------------------------

class SimpleWebSocket:
    """Minimal RFC 6455 websocket client. No external dependencies."""

    def __init__(self, url: str):
        self.url = url
        parsed = urlparse(url)
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = parsed.path or "/"
        self.use_ssl = parsed.scheme == "wss"
        self.sock: Optional[socket.socket] = None

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=15)
        if self.use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self.sock.settimeout(90)

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self.sock.sendall(handshake.encode())

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed during handshake")
            response += chunk

        if b"101" not in response.split(b"\r\n")[0]:
            raise ConnectionError(f"Handshake failed: {response.decode(errors='replace')[:200]}")

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        frame = bytearray()
        frame.append(0x81)
        mask_key = os.urandom(4)
        length = len(data)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(mask_key)
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(data))
        frame.extend(masked)
        self.sock.sendall(frame)

    def recv(self) -> str:
        def _read_exact(n):
            buf = b""
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("Connection closed")
                buf += chunk
            return buf

        header = _read_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", _read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _read_exact(8))[0]

        mask_key = _read_exact(4) if masked else None
        payload = _read_exact(length)

        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x8:  # Close
            raise ConnectionError("Server sent close frame")
        if opcode == 0x9:  # Ping → pong
            pong = bytearray([0x8A, 0x80 | len(payload)])
            mk = os.urandom(4)
            pong.extend(mk)
            pong.extend(bytes(b ^ mk[i % 4] for i, b in enumerate(payload)))
            self.sock.sendall(pong)
            return self.recv()
        if opcode == 0xA:  # Pong
            return self.recv()

        return payload.decode()

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(b"\x88\x80" + os.urandom(4))
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_json(self, obj):
        self.send(json.dumps(obj))

    def recv_json(self):
        return json.loads(self.recv())


# ---------------------------------------------------------------------------
# Broker HTTP helpers (for poll / join)
# ---------------------------------------------------------------------------

def broker_get(path: str) -> dict:
    """GET request to broker, returns parsed JSON."""
    import urllib.request
    url = f"{BROKER_URL}{path}"
    sep = "&" if "?" in url else "?"
    url += f"{sep}group_token={GROUP_TOKEN}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())


def broker_post(path: str, body: dict) -> dict:
    """POST request to broker, returns parsed JSON."""
    import urllib.request
    url = f"{BROKER_URL}{path}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Thread 1: ADVERTISE — websocket to broker
# ---------------------------------------------------------------------------

def advertise_thread():
    """
    Maintain a persistent websocket to the broker advertising our channel.
    When a remote node joins our channel, the broker pushes tunnel_created.
    """
    reconnect_delay = RECONNECT_BASE_SEC

    while running:
        ws = None
        try:
            ws_url = BROKER_URL.replace("https://", "wss://").replace("http://", "ws://")
            ws_url += "/api/v1/advertise"

            log.info("ADVERTISE: connecting to %s", ws_url)
            ws = SimpleWebSocket(ws_url)
            ws.connect()
            log.info("ADVERTISE: connected — sending advertisement")

            ws.send_json({
                "group_token": GROUP_TOKEN,
                "channel_name": CHANNEL_NAME,
                "pubkey": PUBKEY,
                "frognet_subnets": [LOCAL_SUBNET],
                "label": NODE_NAME,
            })

            resp = ws.recv_json()
            if "error" in resp:
                log.error("ADVERTISE: rejected: %s", resp["error"])
                time.sleep(reconnect_delay)
                continue

            log.info("ADVERTISE: channel '%s' live", CHANNEL_NAME)
            reconnect_delay = RECONNECT_BASE_SEC

            # Event loop — handle inbound tunnel_created / tunnel_destroyed
            while running:
                try:
                    msg = ws.recv_json()
                except socket.timeout:
                    try:
                        ws.send_json({"type": "ping"})
                    except Exception:
                        break
                    continue

                event = msg.get("event") or msg.get("type", "")

                if event == "tunnel_created":
                    broker_tunnel_name = msg["tunnel_name"]
                    wg_config = msg["wg_config"]
                    remote_subnets = msg.get("joiner_subnets",
                                             wg_config.get("remote_subnets", []))
                    joiner_channel = msg.get("joiner_channel", broker_tunnel_name)

                    # Validate directionality: we (host) must be lower,
                    # joiner must be higher
                    remote_name, remote_octet = _parse_channel_name(joiner_channel)
                    if remote_name and remote_octet <= SUBNET_OCTET:
                        log.warning("ADVERTISE: rejecting tunnel from '%s' "
                                     "(octet %d <= our %d) — wrong direction",
                                     joiner_channel, remote_octet, SUBNET_OCTET)
                        continue

                    # Compute deterministic tunnel name
                    if remote_name:
                        tunnel_name = _deterministic_tunnel_name(
                            NODE_NAME, SUBNET_OCTET,
                            remote_name, remote_octet)
                    else:
                        tunnel_name = broker_tunnel_name

                    # Skip if already active
                    with tunnel_lock:
                        if tunnel_name in all_tunnels:
                            log.debug("ADVERTISE: tunnel '%s' already active, "
                                       "skipping", tunnel_name)
                            continue

                    try:
                        bring_up_tunnel(tunnel_name, wg_config, remote_subnets,
                                        joiner_channel, direction="in")
                    except Exception as e:
                        log.error("ADVERTISE: failed to bring up '%s': %s",
                                   tunnel_name, e)

                elif event == "tunnel_destroyed":
                    tear_down_tunnel(msg["tunnel_name"])

                elif event in ("ping", "pong") or msg.get("type") in ("ping", "pong"):
                    if event == "ping" or msg.get("type") == "ping":
                        ws.send_json({"type": "pong"})

                else:
                    log.debug("ADVERTISE: unhandled: %s", msg)

        except ConnectionError as e:
            log.warning("ADVERTISE: connection lost: %s", e)
        except socket.timeout:
            log.warning("ADVERTISE: connection timed out")
        except Exception as e:
            log.exception("ADVERTISE: error: %s", e)
        finally:
            if ws:
                ws.close()
            # Tear down inbound tunnels — they'll be re-created on reconnect
            # when the remote side re-joins
            with tunnel_lock:
                inbound_names = list(hosted_tunnels.keys())
            for name in inbound_names:
                tear_down_tunnel(name)

        if running:
            log.info("ADVERTISE: reconnecting in %ds...", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, RECONNECT_MAX_SEC)


# ---------------------------------------------------------------------------
# Thread 2: POLL & AUTO-JOIN
# ---------------------------------------------------------------------------

def poll_and_join_thread():
    """
    Periodically query broker for available channels.
    Auto-join any channel whose subnets we don't have routes to.
    """
    # Wait a few seconds for the advertise thread to connect first
    time.sleep(10)

    while running:
        try:
            _poll_once()
        except Exception as e:
            log.error("POLL: error: %s", e)

        # Sleep in small increments so we can exit promptly
        for _ in range(POLL_INTERVAL_SEC):
            if not running:
                return
            time.sleep(1)


def _poll_once():
    """Single poll cycle: check broker, join missing channels."""
    try:
        resp = broker_get("/api/v1/channels")
    except Exception as e:
        log.debug("POLL: broker unreachable: %s", e)
        return

    channels = resp.get("channels", [])
    if not channels:
        log.debug("POLL: no channels found")
        return

    # What subnets do we already have routes to?
    routed_subnets = local_subnets_from_routes()
    # What channels are we already connected to?
    connected_channels = channels_we_are_connected_to()

    for ch in channels:
        ch_name = ch.get("name", "")
        ch_subnets = ch.get("subnets", [])
        ch_connected = ch.get("connected", False)

        # Skip our own channel
        if ch_name == CHANNEL_NAME:
            continue

        # Skip if not currently advertised (host not online)
        if not ch_connected:
            log.debug("POLL: channel '%s' offline, skipping", ch_name)
            continue

        # Parse remote channel to get directionality
        remote_name, remote_octet = _parse_channel_name(ch_name)
        if not remote_name:
            log.debug("POLL: channel '%s' unparseable, skipping", ch_name)
            continue

        # Directionality: we only JOIN channels with LOWER subnet octet.
        # If remote is higher, THEY join US — skip.
        if not _we_are_joiner(remote_octet):
            log.debug("POLL: channel '%s' (octet %d) is higher than us (%d), "
                       "they join us — skipping",
                       ch_name, remote_octet, SUBNET_OCTET)
            continue

        # Compute deterministic tunnel name
        tunnel_name = _deterministic_tunnel_name(NODE_NAME, SUBNET_OCTET,
                                                  remote_name, remote_octet)

        # Skip if we already have this tunnel active
        with tunnel_lock:
            if tunnel_name in all_tunnels:
                log.debug("POLL: tunnel '%s' already active, skipping", tunnel_name)
                continue

        # Skip if already connected to this channel
        if ch_name in connected_channels:
            continue

        # Check if any of this channel's subnets are missing from our routes
        missing = [sn for sn in ch_subnets if sn not in routed_subnets]
        if not missing:
            log.debug("POLL: channel '%s' subnets %s already routed", ch_name, ch_subnets)
            continue

        # We need this tunnel — join the channel
        log.info("POLL: joining channel '%s' for subnets %s (tunnel=%s)",
                 ch_name, missing, tunnel_name)
        try:
            _join_channel(ch_name, tunnel_name)
        except Exception as e:
            log.error("POLL: failed to join '%s': %s", ch_name, e)


def _join_channel(channel_name: str, tunnel_name: str):
    """Join a remote channel via the broker API, requesting specific tunnel name."""
    resp = broker_post("/api/v1/join", {
        "group_token": GROUP_TOKEN,
        "channel_name": channel_name,
        "pubkey": PUBKEY,
        "frognet_subnets": [LOCAL_SUBNET],
        "label": NODE_NAME,
        "tunnel_name": tunnel_name,
    })

    err = resp.get("detail", "")
    if err:
        raise RuntimeError(f"Join failed: {err}")

    # Use our requested name, not the broker's
    broker_name = resp.get("tunnel_name", tunnel_name)
    if broker_name != tunnel_name:
        log.warning("POLL: broker returned tunnel_name '%s', using our name '%s'",
                     broker_name, tunnel_name)

    wg_config = resp["wg_config"]
    host_subnets = resp.get("host_subnets", [])

    bring_up_tunnel(tunnel_name, wg_config, host_subnets,
                    channel_name, direction="out")

    log.info("POLL: joined '%s' as tunnel '%s', subnets=%s",
             channel_name, tunnel_name, host_subnets)


# ---------------------------------------------------------------------------
# Thread 3: IDLE MONITOR & TEARDOWN
# ---------------------------------------------------------------------------

def idle_monitor_thread():
    """
    Monitor traffic on all tunnel interfaces.
    Tear down tunnels with no traffic for IDLE_TIMEOUT_SEC.
    """
    while running:
        try:
            _idle_check_once()
        except Exception as e:
            log.error("IDLE: error: %s", e)

        # Check every 30 seconds
        for _ in range(30):
            if not running:
                return
            time.sleep(1)


def _idle_check_once():
    """Single idle check pass."""
    now = time.time()

    with tunnel_lock:
        snapshot = dict(all_tunnels)

    teardown_list = []

    for tname, info in snapshot.items():
        iface = info.get("iface", "")
        if not iface:
            continue

        # Read current traffic
        try:
            current_bytes = get_iface_bytes(iface)
        except Exception:
            continue

        prev_bytes = traffic_prev.get(iface, 0)

        if current_bytes != prev_bytes:
            # Traffic flowing — reset idle timer
            traffic_prev[iface] = current_bytes
            idle_since[iface] = now
        else:
            # No new traffic — check how long idle
            idle_start = idle_since.get(iface, now)
            idle_seconds = now - idle_start
            if idle_seconds >= IDLE_TIMEOUT_SEC:
                log.info("IDLE: tunnel '%s' on %s idle for %ds (threshold %ds) — tearing down",
                         tname, iface, int(idle_seconds), IDLE_TIMEOUT_SEC)
                teardown_list.append(tname)

    for tname in teardown_list:
        tear_down_tunnel(tname)


# ---------------------------------------------------------------------------
# Thread 4: METRICS EMITTER
# ---------------------------------------------------------------------------

def metrics_thread():
    """Emit per-tunnel metrics to the transient database."""
    while running:
        for _ in range(METRICS_INTERVAL_SEC):
            if not running:
                return
            time.sleep(1)

        with tunnel_lock:
            snapshot = dict(all_tunnels)

        for tname, info in snapshot.items():
            iface = info.get("iface", "")
            if not iface:
                continue

            try:
                # Read interface stats
                stats = {}
                base = f"/sys/class/net/{iface}/statistics"
                for counter in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                                "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
                    try:
                        with open(f"{base}/{counter}") as f:
                            stats[counter] = int(f.read().strip())
                    except (OSError, ValueError):
                        stats[counter] = 0

                # Compute deltas from previous
                prev = traffic_prev.get(f"{iface}_metrics", {})
                deltas = {}
                for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets"):
                    cur = stats.get(k, 0)
                    prv = prev.get(k, cur)
                    deltas[f"{k}_delta"] = max(0, cur - prv)
                traffic_prev[f"{iface}_metrics"] = dict(stats)

                payload = json.dumps({
                    "tunnel_name": tname,
                    "interface": iface,
                    "channel": info.get("channel_name", ""),
                    "direction": info.get("direction", ""),
                    "interval_sec": METRICS_INTERVAL_SEC,
                    "counters": stats,
                    "deltas": deltas,
                    "idle_sec": int(time.time() - idle_since.get(iface, time.time())),
                    "ts": int(time.time()),
                }, separators=(",", ":"))

                subprocess.run(
                    ["/usr/local/bin/metric_upsert.sh",
                     "TunnelChannel", tname, payload],
                    capture_output=True, timeout=15, check=False,
                )
            except Exception as e:
                log.debug("METRICS: %s: %s", tname, e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Preflight
    if os.geteuid() != 0:
        log.error("Must run as root")
        sys.exit(1)

    for cmd in ("wg", "wg-quick", "ip", "curl", "jq"):
        if not any(os.access(os.path.join(p, cmd), os.X_OK)
                   for p in os.environ.get("PATH", "/usr/bin:/usr/sbin").split(":")):
            log.error("Required command '%s' not found", cmd)
            sys.exit(1)

    # Load config and identity
    load_config()

    # Ensure directories exist
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Write PID
    PID_FILE.write_text(str(os.getpid()))

    # Startup cleanup
    startup_reconcile()

    log.info("Starting frognet-tunnel-daemon (idle_timeout=%ds, poll_interval=%ds)",
             IDLE_TIMEOUT_SEC, POLL_INTERVAL_SEC)

    # Launch threads
    threads = [
        threading.Thread(target=advertise_thread, daemon=True, name="advertise"),
        threading.Thread(target=poll_and_join_thread, daemon=True, name="poll-join"),
        threading.Thread(target=idle_monitor_thread, daemon=True, name="idle-monitor"),
        threading.Thread(target=metrics_thread, daemon=True, name="metrics"),
    ]
    for t in threads:
        t.start()

    # Main thread just waits for shutdown signal
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Shutting down — tearing down all tunnels...")
    tear_down_all()
    PID_FILE.unlink(missing_ok=True)
    log.info("Goodbye.")


if __name__ == "__main__":
    main()
