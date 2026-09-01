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
Global configuration for frognet-tunnel-daemon v3 (pond/chorus).

Reads /etc/frognet/tunnel.conf which now contains:
  BROKER_URL=https://streamingfrog.com:8443/frognet-broker
  PUBKEY=<wireguard public key>

Node identity (name, subnet) is discovered from the local system,
same as before. Pond/chorus membership is broker-side only - the
daemon doesn't know or care about them.

[LAN_ONLY_RUNTIME_V1] Broker reachability is a per-merge runtime
property, not a config-time decision. On every load_config() call we
do a cheap probe (DNS + TCP-connect with 2s timeout). Any failure
flips BROKER_DISABLED=True and the daemon runs LAN-only for this
cycle. We NEVER sys.exit() from config - the committer must run
regardless so LAN observations get committed to the routing table.
"""

import glob
import logging
import os
import signal
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION = "3.0.0"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [v" + VERSION + "] %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("frognet.tunnel-daemon")


def dbg(msg):
    log.info("DBG %s", msg)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONF_FILE  = Path("/etc/frognet/tunnel.conf")
CONF_DIR   = Path("/etc/wireguard")
STATE_DIR  = Path("/var/lib/frognet-tunnel")
ACTIVE_DIR = STATE_DIR / "active"
PID_FILE   = Path("/run/frognet/tunnel-daemon.pid")

# ---------------------------------------------------------------------------
# Tunable constants (env-overridable)
# ---------------------------------------------------------------------------

POLL_INTERVAL_SEC          = int(os.environ.get("FROGNET_TUNNEL_POLL_SEC",           "300"))
# [STALE_HANDSHAKE_THRESHOLD_V2 2026-05-25] Raised default 180 -> 600.
#
# Why: STALE_HANDSHAKE_PURGE_V1 in poll.py tears down a tunnel whose
# last successful WireGuard handshake exceeds this threshold.  On a
# forwarder node every wg interface carries multiple `dev wgN scope
# link` /24 routes (one per remote subnet reachable through that
# tunnel).  When wg-quick down runs, the kernel removes EVERY route
# with `dev wgN` in the same syscall - frognet_monitor goes blank for
# the rest of the merge cycle until the committer's commit-only phase
# at the end of mergeHostsAndResolv.bash reinstalls them.
#
# WireGuard's PersistentKeepalive (peer.py:620) is 25s; rekey interval
# is 120s.  Under normal traffic handshake age stays well under 180s.
# But a quiet tunnel with no probe traffic between merges, or any
# transient broker/NAT hiccup that costs two keepalive intervals,
# crosses 180s easily and triggers a purge of a tunnel that is in
# fact fine and only needs a re-handshake.
#
# 600s = 10 min gives ample headroom on quiet tunnels while still
# catching tunnels that are actually dead (peer rebooted / rotated
# keys / went offline).  Pair with REFRESH_HANDSHAKE_IN_PLACE_V1 in
# poll.py: when handshake IS stale, we first attempt to wake the
# tunnel via `wg set persistent-keepalive` toggle on the existing
# iface - no route disturbance.  Only if that fails to produce a
# fresh handshake within REFRESH_HANDSHAKE_WAIT_SEC do we fall back
# to the destructive teardown.
HANDSHAKE_DEAD_SEC         = int(os.environ.get("FROGNET_TUNNEL_HANDSHAKE_DEAD_SEC", "600"))
# How long to wait for a fresh handshake after kicking persistent-
# keepalive on a stale tunnel.  Short enough that a dead tunnel
# doesn't stall the merge cycle; long enough that a real peer
# responding through a relay has time to answer.
REFRESH_HANDSHAKE_WAIT_SEC = int(os.environ.get("FROGNET_TUNNEL_REFRESH_WAIT_SEC", "12"))
# [HANDSHAKE_GRACE_V3 2026-06-06] A stale handshake whose in-place refresh
# fails is NOT torn down on the first failing cycle.  The peer is given this
# many CONSECUTIVE daemon health cycles (each cycle = one reconcile pass that
# tried refresh and failed) to restore itself before the destructive teardown
# fires.  The counter resets the instant the handshake is fresh again or a
# refresh succeeds.  Set to 1 to restore the pre-V3 tear-down-on-first-failure
# behaviour.  Rationale: a WG tunnel can be L3-alive while its handshake is
# briefly stale (peer rekeying, endpoint roam); one failed refresh is not proof
# of death, and a needless teardown costs a full rebuild + route churn.
HANDSHAKE_GRACE_CYCLES     = int(os.environ.get("FROGNET_TUNNEL_HANDSHAKE_GRACE_CYCLES", "2"))
GRACE_STATE_PATH           = str(STATE_DIR / "handshake_grace.json")
GATEWAY_GRACE_SEC          = int(os.environ.get("FROGNET_TUNNEL_GW_GRACE_SEC",       "120"))
GATEWAY_CHECK_INTERVAL_SEC = int(os.environ.get("FROGNET_TUNNEL_GW_CHECK_SEC",        "60"))
GATEWAY_FAIL_THRESHOLD     = int(os.environ.get("FROGNET_TUNNEL_GW_FAIL_THRESH",       "3"))
ECHO_TIMEOUT_SEC           = float(os.environ.get("FROGNET_ECHO_TIMEOUT_SEC",        "60.0"))
ECHO_SETTLE_SEC            = int(os.environ.get("FROGNET_ECHO_SETTLE_SEC",            "5"))
RECONNECT_BASE_SEC         = 5
RECONNECT_MAX_SEC          = 120
MERGE_SETTLE_SEC           = float(os.environ.get("FROGNET_MERGE_SETTLE_SEC",        "10.0"))
METRICS_INTERVAL_SEC       = int(os.environ.get("FROGNET_METRICS_INTERVAL_SEC",      "60"))
BROKER_PROBE_TIMEOUT_SEC   = float(os.environ.get("FROGNET_BROKER_PROBE_TIMEOUT_SEC", "2.0"))

# ---------------------------------------------------------------------------
# Node identity - populated by load_config()
# ---------------------------------------------------------------------------

running       = True

BROKER_URL    = ""
HOSTNAME      = ""
NODE_NAME     = ""
LOCAL_SUBNET  = ""
LOCAL_GW      = ""
SUBNET_PREFIX = ""
# SUBNET_OCTET removed: a single octet isn't an identity for a FrogNet.
# The served /24 is identified by SUBNET_PREFIX ('10.x.y'); the (x,y) pair
# is the identity, neither octet alone is meaningful.  Comparators live
# in names.py and operate on the full prefix tuple.
CHANNEL_NAME  = ""
GROUP_TOKEN   = ""
NODE_GUID     = ""
POND          = ""
PUBKEY        = ""
PRIVKEY       = ""

# Last known digest from the broker - used for quick change detection
last_digest   = ""

# [LAN_ONLY_RUNTIME_V1] True when broker is empty, unreachable, or no
# keypair on disk.  Consumers (bringup, poll) must skip every broker
# call when this is True and treat the node as LAN-only.
BROKER_DISABLED = False

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
# Transit subnet discovery
# ---------------------------------------------------------------------------

# [LAN_DEV_DENYLIST_V1] Bearer classification is a DENY-list, not an
# allow-list, and it matches mapInterfaces._list_physical_interfaces exactly.
#
# The prior allow-list ("eth","en","wlan","wlp","ham","br","bond") silently
# misclassified working uplinks. systemd predictable naming produces wlx<mac>
# for USB WiFi and enx<mac> for USB ethernet; "wlx90de80b193db" matches none of
# wlan/wlp, so _is_lan_dev() returned False, _broker_reachable() declared the
# node LAN-only, broker_disabled went True, and tunnel bring-up no-opped on
# every merge forever while the node had a perfectly good non-10.x default
# route. "enx0050b6246629" passed only by accident, because it starts with "en".
#
# The set of names a bearer MIGHT have is open and unknowable (wlx, enx, wwan,
# usb, ww, altnames, user-renamed devices, ham radio bearers). The set of names
# that are NOT bearers is small, closed, and ours: our own overlay interfaces
# plus loopback and container/virtual devices. Deny that set; admit the rest.
#
# Keep in sync with mapInterfaces._list_physical_interfaces (lo, veth*,
# docker*, br-*, virbr*) plus this module's overlay devices (wg*, frognet*).
_NON_BEARER_PREFIXES = ("wg", "frognet", "lo", "docker", "veth",
                        "virbr", "br-", "tun", "tap")

# Retained for callers/tests that still import it. NOT used for classification:
# an allow-list cannot be complete. Do not reintroduce it as the gate.
_LAN_DEV_PREFIXES = ("eth", "en", "wlan", "wlp", "wlx", "wl", "ww",
                     "ham", "br", "bond", "usb")


def _is_lan_dev(dev: str) -> bool:
    """True if dev is a directly-attached bearer.

    A bearer is anything that is not one of OUR overlay interfaces (wg*,
    frognet*) and not a loopback/container/virtual device. Name prefixes are
    NOT consulted: see _NON_BEARER_PREFIXES above for why.

    Note "br-" is denied (docker bridges) while a plain "br0" is admitted --
    an operator-created bridge is a legitimate bearer. This matches
    mapInterfaces, which skips br-* and keeps br*.
    """
    if not dev:
        return False
    return not any(dev == p or dev.startswith(p)
                   for p in _NON_BEARER_PREFIXES)


def _broker_reachable(broker_url: str,
                      timeout: float = BROKER_PROBE_TIMEOUT_SEC) -> bool:
    """[LAN_DEV_PATH_V1] Reachability = the route to the broker IP exits
    via a directly-attached non-FrogNet interface (eth*/en*/wlan*/wlp*/
    ham/br/bond).  A node whose path to the broker goes through wg* or
    frognet* is riding another FrogNet's tunnels; that node must NOT
    register, must NOT make broker calls, must NOT bring up its own
    tunnels.  TCP-connect alone can't tell - it succeeds via the
    upstream's tunnels.  The kernel routing table can.

    Fail-closed on every uncertainty: no host, no DNS, no route, or
    a route through wg*/frognet*/lo/anything-not-a-LAN-prefix -> False.

    Cheap: one DNS lookup, one `ip route get`.  Runs every poll cycle.
    """
    try:
        parsed = urllib.parse.urlparse(broker_url)
        host = parsed.hostname
    except Exception as e:
        log.info("Broker URL %r parse failed (%s) - LAN-only", broker_url, e)
        return False
    if not host:
        log.info("Broker URL %r has no host - LAN-only", broker_url)
        return False
    # [HOSTS_ONLY_V1] does NOT apply here, deliberately. The rule is that /etc/hosts is
    # the only source of name->IP for FROGNET names. The broker is reached over the
    # public internet and its hostname is not a FrogNet name -- it is not in
    # /etc/hosts and never will be, so DNS is the correct and only source for it.
    # Guarded: a 10. answer would mean a FrogNet name reached this path, which is a
    # classification bug, and it is refused rather than dialled.
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        log.info("Broker host %s not resolvable (%s) - LAN-only", host, e)
        return False
    if str(ip).startswith("10."):
        log.info("Broker host %s resolved to a FrogNet address %s via DNS - refusing; "
                 "FrogNet names come from /etc/hosts only [HOSTS_ONLY_V1]", host, ip)
        return False
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "get", ip],
            text=True, timeout=timeout)
    except Exception as e:
        log.info("Broker route lookup for %s (%s) failed (%s) - LAN-only",
                 host, ip, e)
        return False
    # Parse "<ip> via <gw> dev <dev> ..." or "<ip> dev <dev> ..."
    parts = out.split()
    dev = ""
    for i, tok in enumerate(parts):
        if tok == "dev" and i + 1 < len(parts):
            dev = parts[i + 1]
            break
    if not dev:
        log.info("Broker route to %s (%s) has no dev (%r) - LAN-only",
                 host, ip, out.strip())
        return False
    if not _is_lan_dev(dev):
        log.info("Broker route to %s (%s) goes via dev=%s - upstream is "
                 "another FrogNet, this node is LAN-only", host, ip, dev)
        return False
    log.info("Broker %s (%s) reachable via dev=%s - online", host, ip, dev)
    return True


# [TRANSIT_DISCOVER_V2_RESTORED 2026-06-06] Reverted v25's TRANSIT_FROM_ROUTES_V1
# route-scan (which reintroduced the peer-LAN-claiming bug the working build
# deliberately abandoned) back to the proven all5 lease-based downstream +
# echo-probed upstream method. DHCP leases are the authoritative gateway signal.
def discover_transit_subnets(local_subnet: str) -> list:
    """[TRANSIT_DISCOVER_V2] Return /24s for FrogNet nodes that THIS host
    serves DHCP to.

    Algorithm:
      1. Enumerate unexpired leases in /var/lib/misc/dnsmasq.leases.
      2. For each leased IP, GET http://<ip>/frognet_echo.php (1s timeout).
         Non-FrogNet devices (printers, laptops, '*' hostnames) fail the
         probe and are skipped.
      3. Parse CSV response; field 1 is the peer's frognet IP; mask /24.
      4. Skip own /24, 10.253/8 transit, 10.254/8 chorus.

    Hosts that aren't running DHCP have no leases file -> [].

    The lease file is the gateway signal: a node only serves DHCP for
    LANs it's the gateway of, so leases scoped to those LANs identify
    that node's downstream FrogNet peers.  This replaces the symmetric
    route-scan that incorrectly claimed peer-LANs and learned transit
    routes as own transit subnets.
    """
    import time
    import urllib.request

    LEASES = "/var/lib/misc/dnsmasq.leases"
    if not os.path.isfile(LEASES):
        log.info("discover_transit_subnets [V2]: no %s -> []", LEASES)
        return []
    try:
        with open(LEASES) as f:
            raw_lines = f.readlines()
    except OSError as e:
        log.warning("discover_transit_subnets [V2]: read %s: %s", LEASES, e)
        return []

    now    = time.time()
    found  = set()
    probed = 0
    skipped_expired = 0
    failed_probe    = 0

    for raw in raw_lines:
        parts = raw.strip().split()
        if len(parts) < 3:
            continue
        try:
            expiry = int(parts[0])
        except ValueError:
            continue
        if expiry <= now:
            skipped_expired += 1
            continue
        ip = parts[2]

        url = "http://%s/frognet_echo.php" % ip
        probed += 1
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if getattr(r, "status", 200) != 200:
                    failed_probe += 1
                    continue
                body = r.read(256).decode("utf-8", "replace").strip()
        except Exception:
            failed_probe += 1
            continue

        fields = body.split(",")
        if len(fields) < 2:
            failed_probe += 1
            continue
        frognet_ip = fields[1].strip()
        if not frognet_ip.startswith("10."):
            failed_probe += 1
            continue
        octets = frognet_ip.split(".")
        if len(octets) != 4:
            failed_probe += 1
            continue
        try:
            if not all(0 <= int(o) <= 255 for o in octets):
                failed_probe += 1
                continue
        except ValueError:
            failed_probe += 1
            continue
        if frognet_ip.startswith("10.253.") or frognet_ip.startswith("10.254."):
            continue
        cidr = "%s.%s.%s.0/24" % (octets[0], octets[1], octets[2])
        if cidr == local_subnet:
            continue
        found.add(cidr)

    # [TRANSIT_DISCOVER_V3] Upstream gateway discovery. The lease loop
    # above finds DOWNSTREAM peers (nodes we serve DHCP to). It misses
    # UPSTREAM peers - APs whose network this host is a DHCP CLIENT of.
    # Without this, when we transit traffic FROM the upstream's /24
    # through one of our wg tunnels, the peer on the far end has no
    # return route and replies are dropped.
    #
    # Method: enumerate every /24 connected on this host. For each /24
    # where our IP is NOT the .1, probe .1 with frognet_echo. If .1
    # answers as FrogNet, that's an upstream FrogNet host and we can
    # transit its /24.
    try:
        addr_out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"], text=True)
    except subprocess.CalledProcessError as e:
        log.warning("discover_transit_subnets [V3]: ip addr failed: %s", e)
        addr_out = ""

    local_ips = set()
    candidate_subnets = []
    for line in addr_out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        ip_cidr = parts[3]
        if "/" not in ip_cidr:
            continue
        ip_str, mask_str = ip_cidr.split("/", 1)
        if not ip_str.startswith("10."):
            continue
        if ip_str.startswith("10.253.") or ip_str.startswith("10.254."):
            continue
        try:
            if int(mask_str) != 24:
                continue
        except ValueError:
            continue
        local_ips.add(ip_str)
        octets = ip_str.split(".")
        if len(octets) != 4:
            continue
        prefix = "%s.%s.%s" % (octets[0], octets[1], octets[2])
        candidate_subnets.append(("%s.0/24" % prefix, "%s.1" % prefix, ip_str))

    upstream_probed = 0
    upstream_failed = 0
    upstream_found = set()
    for subnet_cidr, gw_ip, my_ip in candidate_subnets:
        if subnet_cidr == local_subnet:
            continue
        if subnet_cidr in found:
            continue
        if gw_ip == my_ip or gw_ip in local_ips:
            continue
        upstream_probed += 1
        try:
            with urllib.request.urlopen(
                    "http://%s/frognet_echo.php" % gw_ip, timeout=1.0) as r:
                if getattr(r, "status", 200) != 200:
                    upstream_failed += 1
                    continue
                body = r.read(256).decode("utf-8", "replace").strip()
        except Exception:
            upstream_failed += 1
            continue
        fields = body.split(",")
        if len(fields) < 2 or not fields[1].strip().startswith("10."):
            upstream_failed += 1
            continue
        upstream_found.add(subnet_cidr)
        found.add(subnet_cidr)

    log.info("discover_transit_subnets [V3-upstream]: candidates=%d "
             "probed=%d failed=%d found=%s",
             len(candidate_subnets), upstream_probed,
             upstream_failed, sorted(upstream_found))

    result = sorted(found)
    log.info("discover_transit_subnets [V2]: local=%s leases=%d "
             "probed=%d expired=%d failed=%d found=%s",
             local_subnet, len(raw_lines), probed,
             skipped_expired, failed_probe, result)
    return result

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def load_config():
    """[LAN_ONLY_RUNTIME_V1] Load tunnel.conf and probe broker reachability.

    Never sys.exit() on broker/keypair issues - the committer must run
    on LAN-only nodes so LAN observations get committed.  Sets
    BROKER_DISABLED=True for any failure mode (empty URL, unreachable
    broker, missing keypair when otherwise reachable).
    """
    global BROKER_URL, GROUP_TOKEN, BROKER_DISABLED
    global HOSTNAME, NODE_NAME, LOCAL_SUBNET, LOCAL_GW, SUBNET_PREFIX
    global CHANNEL_NAME, PUBKEY, PRIVKEY, NODE_GUID, POND

    if not CONF_FILE.exists():
        log.error("Config file %s not found - run frognet_tunnel_setup_v3.sh first", CONF_FILE)
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
    if not BROKER_URL:
        log.info("No BROKER_URL in %s - LAN-only mode", CONF_FILE)
        BROKER_DISABLED = True
    elif not _broker_reachable(BROKER_URL):
        # Logged inside _broker_reachable; no need to repeat.
        BROKER_DISABLED = True
    else:
        BROKER_DISABLED = False

    GROUP_TOKEN = conf.get("GROUP_TOKEN", "")
    NODE_GUID = ""
    try:
        with open("/etc/fnid") as _gf:
            NODE_GUID = _gf.read().strip()
    except OSError:
        pass
    if not NODE_GUID and not BROKER_DISABLED:
        log.error("CRITICAL: No GUID at /etc/fnid - this node has NO FrogNet "
                  "identity and the broker will reject its registration. Run "
                  "`frognet-node-guid.sh --ensure` and restart "
                  "frognet-tunnel-daemon-v3.")
    if not GROUP_TOKEN and not BROKER_DISABLED:
        log.error("CRITICAL: No GROUP_TOKEN in %s - broker auth WILL fail. "
                  "Channel registration and transit-subnet updates will be "
                  "rejected (401). Set GROUP_TOKEN=<value> in %s and restart "
                  "frognet-tunnel-daemon-v3.", CONF_FILE, CONF_FILE)

    HOSTNAME = subprocess.check_output(["hostname", "-s"], text=True).strip()

    # Discover node name from dnsmasq domain=
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
        log.error("No domain= found in /etc/dnsmasq.d/*.conf")
        sys.exit(1)

    # Discover local subnet via the pure parser in names.py - same code
    # path the sim uses, so any drift between parser and what the live
    # daemon actually does shows up immediately.
    from .names import parse_local_subnet
    out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True)
    _subnet = parse_local_subnet(out)
    if _subnet:
        LOCAL_SUBNET  = _subnet["LOCAL_SUBNET"]
        LOCAL_GW      = _subnet["LOCAL_GW"]
        SUBNET_PREFIX = _subnet["SUBNET_PREFIX"]

    if not LOCAL_SUBNET:
        log.error("No 10.x.x.x address found (excluding 10.253.x.x transit and 10.254.x.x chorus ranges)")
        sys.exit(1)

    CHANNEL_NAME = f"{NODE_NAME}-{SUBNET_PREFIX}"

    # [GUID_IDENTITY_V1] Pond name for broker register/retire calls. The
    # daemon historically did not carry the pond (membership was broker-side
    # only), but GUID re-registration needs it. It is already on disk: install
    # writes NETWORK_NAME=<pond> into /etc/frognet/gateways.conf. Read it here
    # so the daemon can re-register on a my-channels 404 instead of giving up.
    POND = ""
    try:
        with open("/etc/frognet/gateways.conf") as _gf:
            for _gl in _gf:
                _gl = _gl.strip()
                if _gl.startswith("NETWORK_NAME="):
                    POND = _gl.split("=", 1)[1].strip()
                    break
    except OSError:
        pass
    if not POND and not BROKER_DISABLED:
        log.warning("No NETWORK_NAME in /etc/frognet/gateways.conf - cannot "
                    "self-register with the broker on a 404 until the pond is "
                    "known.")

    # WireGuard keys.  Required only when we intend to talk to the
    # broker (i.e. not BROKER_DISABLED).  On a permanent LAN-only node
    # the keys never exist, which is fine.  On a node that's just lost
    # its broker, the keys exist but we won't use them this cycle.
    privkey_file = STATE_DIR / "node_private.key"
    pubkey_file  = STATE_DIR / "node_public.key"
    if privkey_file.exists() and pubkey_file.exists():
        PRIVKEY = privkey_file.read_text().strip()
        PUBKEY  = pubkey_file.read_text().strip()
    elif not BROKER_DISABLED:
        # Broker reachable, configured, keypair missing.  This is the
        # "operator hasn't run setup_v3 yet" case - log it loudly but
        # don't sys.exit.  Downgrade to LAN-only so the committer
        # still runs; setup_v3 will populate the keypair and a normal
        # systemctl restart picks up from there.
        log.error("WireGuard keypair missing - run frognet_tunnel_setup_v3.sh "
                  "to register with broker; running in LAN-only mode this cycle")
        PRIVKEY = ""
        PUBKEY  = ""
        BROKER_DISABLED = True
    else:
        log.info("WireGuard keypair not present - LAN-only mode, no tunnels")
        PRIVKEY = ""
        PUBKEY  = ""

    log.info("Config: broker=%s broker_disabled=%s", BROKER_URL, BROKER_DISABLED)
    log.info("Identity: node=%s host=%s subnet=%s channel=%s",
             NODE_NAME, HOSTNAME, LOCAL_SUBNET, CHANNEL_NAME)
