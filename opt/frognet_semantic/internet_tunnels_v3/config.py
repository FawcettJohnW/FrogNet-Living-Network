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
Global configuration for frognet-tunnel-daemon v3.

Two modes, and only two [NO_BROKER_V1]:

  region    /opt/fdf/var/join.env names RINA_RAM (written by fdf-region-join / fdf-join, removed by fdf-leave: the ONE
            switch [REGION_SWITCH_ONE_FILE_V1]; the area is site.env's RINA_AREA). The daemon holds an FD&F region session, applies FD&F's plan and keeps one WireGuard link
            per hub. It owns its WireGuard key: /var/lib/frognet-tunnel/node_private.key is made by the daemon itself
            the first time it is needed (ensure_node_key) [DAEMON_OWNS_ITS_KEY_V1].
  LAN-only  no RINA_RAM. FrogNet discovery and the databasehost election over the LAN; no WireGuard, no key.

/etc/frognet/tunnel.conf is not a source of anything. If one is left on the machine, every key in it (an old
enrolment's, or an old RINA_RAM / RINA_AREA) is named in one log line and ignored [NO_BROKER_V1]; the installer's
upgrade removes them. Neither tunnel.conf nor the environment switches the mode.

Node identity (name, subnet) is discovered from the local system.
"""

import glob
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION = "3.0.0"
# [BUILD_IN_THE_LOG_V1] said by the daemon at start, in both modes, so its log names what is deployed
BUILD = "frognet-tunnel-2026-10-12-t4c"

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
WG_IMPL_FILE = Path("/etc/frognet/wg.impl")


def wg_impl():
    """[WG_IMPL_RECORDED_V1 - T4b] The ONE WireGuard implementation of this machine: FDF_WG_IMPL when a test harness
    sets it explicitly, else /etc/frognet/wg.impl (written once at install: the installer's A2b, the node .run, by
    fdf-wg-impl's rule). Returns "kernel" | "wireguard-go", or None with the reason logged (a bring-up then fails and
    the next poll retries it)."""
    v, src = os.environ.get("FDF_WG_IMPL", "").strip(), "FDF_WG_IMPL"
    if not v:
        src = str(WG_IMPL_FILE)
        try:
            v = WG_IMPL_FILE.read_text().strip()
        except OSError as e:
            log.error("WG_IMPL: none recorded (%s: %s) - run the installer (or /opt/fdf/bin/fdf-wg-impl --record)",
                      WG_IMPL_FILE, e.strerror)
            return None
    if v not in ("kernel", "wireguard-go"):
        log.error("WG_IMPL: %s says %r - it must be kernel or wireguard-go", src, v)
        return None
    return v


def wg_quick_env():
    """The environment wg-quick runs in, so it makes the interface with the recorded implementation only.
    wg-quick tries `ip link add ... type wireguard` and, when that fails, starts $WG_QUICK_USERSPACE_IMPLEMENTATION
    if that command exists. kernel: it is set to a name no command has, so a kernel failure is the failure (said in
    wg-quick's stderr, which the bring-up logs). wireguard-go: the record says this kernel has no WireGuard, so the
    kernel attempt fails and wireguard-go makes the interface. None when there is no record."""
    impl = wg_impl()
    if impl is None:
        return None
    env = dict(os.environ)
    env["WG_QUICK_USERSPACE_IMPLEMENTATION"] = (
        "frognet-wg-impl-is-kernel-no-userspace" if impl == "kernel" else "wireguard-go")
    if impl == "wireguard-go":
        env["WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD"] = "1"
    return env

# ---------------------------------------------------------------------------
# Tunable constants (env-overridable)
# ---------------------------------------------------------------------------

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
# transient NAT hiccup that costs two keepalive intervals,
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
ECHO_TIMEOUT_SEC           = float(os.environ.get("FROGNET_ECHO_TIMEOUT_SEC",        "60.0"))

# ---------------------------------------------------------------------------
# Node identity - populated by load_config()
# ---------------------------------------------------------------------------

running       = True

MODE          = ""      # "region" or "lan-only", set by load_config()
REGION_RAM    = ""      # [REGION_V1] RINA_RAM=host:8840: region mode on
REGION_AREA   = ""      # [REGION_V1] RINA_AREA=<area name>: the LISP site whose mappings count
HOSTNAME      = ""
NODE_NAME     = ""
LOCAL_SUBNET  = ""
LOCAL_GW      = ""
SUBNET_PREFIX = ""
# SUBNET_OCTET removed: a single octet isn't an identity for a FrogNet.
# The served /24 is identified by SUBNET_PREFIX ('10.x.y'); the (x,y) pair
# is the identity, neither octet alone is meaningful.  Comparators live
# in names.py and operate on the full prefix tuple.
NODE_GUID     = ""
PUBKEY        = ""
PRIVKEY       = ""

# [REGION_SWITCH_ONE_FILE_V1 - T4c] the region settings have one file each: RINA_RAM in join.env (the switch), RINA_AREA
# in site.env (fdf-region-join writes both, fdf-leave removes both). tunnel.conf is read only to name what is left in it.
JOIN_ENV      = Path("/opt/fdf/var/join.env")
SITE_ENV      = Path("/opt/fdf/var/site.env")

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
# load_config
# ---------------------------------------------------------------------------

def _fdf_var(name, path):
    """[REGION_SWITCH_ONE_FILE_V1] NAME's value in one of FD&F's own files (`KEY=VALUE` or `export KEY=VALUE`).
    Returns (value, why): value "" with why saying what was missing (no file, or no NAME in it)."""
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("export "):
                    ln = ln[7:].strip()
                if ln.startswith(name + "="):
                    return ln.split("=", 1)[1].strip().strip('"'), ""
    except OSError as e:
        return "", "%s: %s" % (path, e.strerror)
    return "", "%s has no %s" % (path, name)



PRIVKEY_FILE = STATE_DIR / "node_private.key"
PUBKEY_FILE  = STATE_DIR / "node_public.key"


def _name_leftover_conf() -> None:
    """[REGION_SWITCH_ONE_FILE_V1] tunnel.conf is no source: if one is on the machine, its keys are named in one line and
    ignored (an old enrolment's, or an old RINA_RAM / RINA_AREA; the installer's upgrade strips them)."""
    keys = []
    try:
        with open(CONF_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" in line:
                    keys.append(line.split("=", 1)[0].strip())
    except FileNotFoundError:
        return
    if keys:
        log.warning("%s: old broker keys ignored: %s (nothing is read from this file; the region switch is %s) [NO_BROKER_V1 "
                    "REGION_SWITCH_ONE_FILE_V1]", CONF_FILE, ", ".join(sorted(set(keys))), JOIN_ENV)


def _pubkey_of(privkey: str) -> str:
    r = subprocess.run(["wg", "pubkey"], input=privkey + "\n", capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("wg pubkey failed rc=%d: %s" % (r.returncode, (r.stderr or "").strip()))
    return r.stdout.strip()


def _load_node_key() -> None:
    """PRIVKEY/PUBKEY from node_private.key (the public half is derived from it, one source). Absent: both empty."""
    global PRIVKEY, PUBKEY
    PRIVKEY, PUBKEY = "", ""
    try:
        priv = PRIVKEY_FILE.read_text().strip()
    except FileNotFoundError:
        return
    if not priv:
        log.error("WG_KEY: %s is empty; treated as absent", PRIVKEY_FILE)
        return
    PRIVKEY = priv
    PUBKEY = _pubkey_of(priv)


def ensure_node_key() -> bool:
    """[DAEMON_OWNS_ITS_KEY_V1] Region mode: the daemon's WireGuard key. If node_private.key is absent the daemon makes
    it (umask 077, wg genkey; node_public.key = wg pubkey of it) and says so with the public half. This is the only
    creator of the key. Returns True when a key is in hand. A failure is logged and the caller tries again on its next
    event; there is no second way to get a key."""
    global PRIVKEY, PUBKEY
    if PRIVKEY:
        return True
    _load_node_key()
    if PRIVKEY:
        return True
    old = os.umask(0o077)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["wg", "genkey"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("wg genkey failed rc=%d: %s" % (r.returncode, (r.stderr or "").strip()))
        priv = r.stdout.strip()
        pub = _pubkey_of(priv)
        tmp = PRIVKEY_FILE.with_name(PRIVKEY_FILE.name + ".tmp.%d" % os.getpid())
        tmp.write_text(priv + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, PRIVKEY_FILE)
        tmp = PUBKEY_FILE.with_name(PUBKEY_FILE.name + ".tmp.%d" % os.getpid())
        tmp.write_text(pub + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, PUBKEY_FILE)
    except Exception as e:
        log.error("WG_KEY: could not make %s: %s (tried again on the next event) [DAEMON_OWNS_ITS_KEY_V1]", PRIVKEY_FILE, e)
        return False
    finally:
        os.umask(old)
    PRIVKEY, PUBKEY = priv, pub
    log.warning("WG_KEY: made %s (none on disk); public key %s [DAEMON_OWNS_ITS_KEY_V1]", PRIVKEY_FILE, pub)
    return True


def load_config():
    """[NO_BROKER_V1] The mode (region or LAN-only), the node's identity and, in region mode, its WireGuard key if it
    has one yet. Never exits for a missing tunnel.conf or key: LAN-only needs neither, and in region mode the daemon
    makes the key (ensure_node_key) before its first bring-up."""
    global MODE, REGION_RAM, REGION_AREA
    global HOSTNAME, NODE_NAME, LOCAL_SUBNET, LOCAL_GW, SUBNET_PREFIX, NODE_GUID, PRIVKEY, PUBKEY

    _name_leftover_conf()
    # [REGION_SWITCH_ONE_FILE_V1 - T4c] one file is the switch: join.env's RINA_RAM. The area: site.env's RINA_AREA.
    # tunnel.conf and the environment are not read for either.
    REGION_RAM, why_ram = _fdf_var("RINA_RAM", JOIN_ENV)
    REGION_AREA, why_area = "", ""
    if REGION_RAM:
        REGION_AREA, why_area = _fdf_var("RINA_AREA", SITE_ENV)
    MODE = "region" if REGION_RAM else "lan-only"
    if REGION_RAM:
        log.warning("REGION switch: %s RINA_RAM=%s; %s RINA_AREA=%s -> region mode [REGION_SWITCH_ONE_FILE_V1]",
                    JOIN_ENV, REGION_RAM, SITE_ENV, REGION_AREA or "<absent: %s>" % why_area)
        if not REGION_AREA:
            log.error("REGION: no area (%s): this node's mappings name no site until fdf-region-join writes it", why_area)
    else:
        log.info("LAN-only mode: no region switch (%s); no WireGuard [LAN_ONLY_NO_REGION_V1 REGION_SWITCH_ONE_FILE_V1]",
                 why_ram)

    NODE_GUID = ""
    try:
        with open("/etc/fnid") as _gf:
            NODE_GUID = _gf.read().strip()
    except OSError:
        pass
    if not NODE_GUID and REGION_RAM:
        log.error("CRITICAL: No GUID at /etc/fnid - this node has NO FrogNet identity to announce in the region. Run "
                  "`frognet-node-guid.sh --ensure` and restart frognet-tunnel-daemon-v3.")

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

    # WireGuard key: region mode only. If there is none yet the daemon makes it before its first bring-up
    # (ensure_node_key); LAN-only never needs one.
    if REGION_RAM:
        _load_node_key()
        if not PRIVKEY:
            log.info("WireGuard key: none at %s yet; the daemon makes it before its first bring-up [DAEMON_OWNS_ITS_KEY_V1]",
                     PRIVKEY_FILE)
    else:
        PRIVKEY, PUBKEY = "", ""

    log.info("Config: mode=%s build=%s", MODE, BUILD)
    log.info("Identity: node=%s host=%s subnet=%s", NODE_NAME, HOSTNAME, LOCAL_SUBNET)
