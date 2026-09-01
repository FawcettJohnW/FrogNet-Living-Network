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
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event
"""
WireGuard interface helpers and frognet echo probe.
"""

import glob
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from typing import Optional

from . import config

_wg_up_lock = threading.RLock()


def find_next_wg_iface() -> str:
    trace_enter('wg.find_next_wg_iface')
    for n in range(32):
        r = subprocess.run(["ip", "link", "show", f"wg{n}"],
                           capture_output=True, check=False)
        if r.returncode != 0:
            return f"wg{n}"
    raise RuntimeError("Too many WireGuard interfaces (32+)")


def teardown_wg_iface(iface: str):
    trace_enter('wg.teardown_wg_iface', iface=repr(iface))
    config.dbg(f"_teardown_wg_iface: {iface}")
    conf_path = config.CONF_DIR / f"{iface}.conf"
    try:
        subprocess.run(["ip", "link", "show", iface],
                       capture_output=True, check=True)
    except subprocess.CalledProcessError:
        config.log.info("_teardown_wg_iface: %s not in kernel", iface)
        conf_path.unlink(missing_ok=True)
        return

    if conf_path.exists():
        r = subprocess.run(["wg-quick", "down", iface],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            config.log.warning("_teardown_wg_iface: wg-quick down %s failed: %s",
                               iface, r.stderr.strip())
    else:
        subprocess.run(["ip", "link", "set", iface, "down"],
                       capture_output=True, check=False)
        subprocess.run(["ip", "link", "delete", iface],
                       capture_output=True, check=False)

    conf_path.unlink(missing_ok=True)
    config.log.info("Torn down %s", iface)


def refresh_handshake(iface: str, wait_sec: int) -> Optional[float]:
    """[REFRESH_HANDSHAKE_IN_PLACE_V1 2026-05-25] Force WireGuard to
    initiate a new handshake on `iface` WITHOUT bringing the iface
    down.  Routes installed against this iface (most of the route
    table on a forwarder node) are preserved.

    Mechanism: read the current PersistentKeepalive, set it to 0
    momentarily, then set it back.  On the keepalive=0 -> keepalive=N
    transition the kernel queues an immediate handshake initiation
    packet - exactly the same path as the periodic keepalive timer
    firing, only forced.

    Returns the post-refresh handshake age in seconds on success,
    None if no fresh handshake arrived within `wait_sec`.

    Why this exists: STALE_HANDSHAKE_PURGE used to tear down any
    tunnel with handshake_age >= HANDSHAKE_DEAD_SEC, which removed
    every `dev wgN` route in one syscall.  On a forwarder with 4-9
    routes per wg iface, the route table emptied mid-merge and the
    monitor screen went blank for the duration.  This helper keeps
    the iface up, preserving every route, and only fails if the peer
    is genuinely unresponsive - in which case the caller's fallback
    to destructive teardown is the right action.

    Failure modes that DON'T justify teardown (returns the existing
    age, caller treats as success):
      - wg has no peers configured (shouldn't happen on a managed
        FrogNet tunnel; if it does, teardown won't help)
      - PersistentKeepalive line absent (legacy conf; not us)

    Failure modes that DO justify teardown (returns None):
      - Subprocess errors talking to the wg binary
      - No fresh handshake within wait_sec - peer is actually dead
    """
    # Capture peer pubkey + current keepalive so we can restore.
    trace_enter('wg.refresh_handshake', iface=repr(iface), wait_sec=repr(wait_sec))
    try:
        r = subprocess.run(["wg", "show", iface, "dump"],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            config.log.warning(
                "refresh_handshake: wg show %s dump rc=%d stderr=%s",
                iface, r.returncode, (r.stderr or "").strip())
            return None
    except Exception as e:
        config.log.warning("refresh_handshake: wg show %s: %s", iface, e)
        return None

    # `wg show dump` format:
    #   line 0: interface line (private_key, public_key, listen_port, fwmark)
    #   line 1+: one per peer:
    #       public_key  preshared_key  endpoint  allowed_ips  latest_handshake  rx  tx  persistent_keepalive
    lines = (r.stdout or "").splitlines()
    if len(lines) < 2:
        config.log.warning(
            "refresh_handshake: %s no peers in dump", iface)
        return None

    peer_fields = lines[1].split("\t")
    if len(peer_fields) < 8:
        config.log.warning(
            "refresh_handshake: %s peer line malformed (%d fields)",
            iface, len(peer_fields))
        return None
    peer_pubkey = peer_fields[0]
    current_keepalive = peer_fields[7]   # string, "off" or seconds

    # Default keepalive value to restore.  conf file uses 25; if the
    # current value is something else (operator override), preserve it.
    restore_to = current_keepalive
    if restore_to in ("0", "off", ""):
        restore_to = "25"   # matches conf template default

    # Note the handshake age before we kick, so we can detect "fresh
    # handshake arrived" by age decreasing.  None means never-handshook.
    pre_age = wg_handshake_age(iface)

    # Kick: keepalive 0 then keepalive N - kernel queues an immediate
    # handshake on the 0 -> N transition.
    try:
        subprocess.run(
            ["wg", "set", iface, "peer", peer_pubkey,
             "persistent-keepalive", "0"],
            capture_output=True, text=True, check=False)
        subprocess.run(
            ["wg", "set", iface, "peer", peer_pubkey,
             "persistent-keepalive", restore_to],
            capture_output=True, text=True, check=False)
    except Exception as e:
        config.log.warning(
            "refresh_handshake: wg set %s keepalive failed: %s", iface, e)
        return None

    # Wait for handshake_age to drop (i.e. a NEW handshake landed).
    # Poll every 500ms; bound by wait_sec.
    deadline = time.monotonic() + max(1, int(wait_sec))
    while time.monotonic() < deadline:
        new_age = wg_handshake_age(iface)
        if new_age is not None and (pre_age is None or new_age < pre_age):
            # Fresh handshake - age dropped below pre-kick value (or we
            # had no handshake before and have one now).
            config.log.info(
                "refresh_handshake: %s OK pre_age=%s new_age=%ds peer=%s...",
                iface,
                f"{int(pre_age)}s" if pre_age is not None else "never",
                int(new_age), peer_pubkey[:16])
            return float(new_age)
        time.sleep(0.5)

    final_age = wg_handshake_age(iface)
    config.log.warning(
        "refresh_handshake: %s NO_FRESH_HANDSHAKE waited=%ds "
        "pre_age=%s post_age=%s peer=%s... - caller should fall back "
        "to teardown",
        iface, wait_sec,
        f"{int(pre_age)}s" if pre_age is not None else "never",
        f"{int(final_age)}s" if final_age is not None else "never",
        peer_pubkey[:16])
    return None


def wg_handshake_age(iface: str) -> Optional[float]:
    trace_enter('wg.wg_handshake_age', iface=repr(iface))
    try:
        wg = subprocess.run(["wg", "show", iface],
                            capture_output=True, text=True, check=False)
        for ln in (wg.stdout or "").splitlines():
            ln = ln.strip()
            if not ln.startswith("latest handshake:"):
                continue
            age_str = ln.split(":", 1)[1].strip()
            total_sec = 0
            for val, unit in re.findall(r'(\d+)\s+(second|minute|hour|day)', age_str):
                val = int(val)
                if "second" in unit:   total_sec += val
                elif "minute" in unit: total_sec += val * 60
                elif "hour" in unit:   total_sec += val * 3600
                elif "day" in unit:    total_sec += val * 86400
            return float(total_sec)
    except Exception as e:
        config.log.warning("_wg_handshake_age: %s: %s", iface, e)
    return None


def frognet_echo(ip: str, timeout: float = None) -> Optional[float]:
    """HTTP echo through local proxy semantic layer to remote node.
    Returns RTT in seconds, or None on failure."""
    trace_enter('wg.frognet_echo', ip=repr(ip), timeout=repr(timeout))
    if timeout is None:
        timeout = config.ECHO_TIMEOUT_SEC
    url = f"http://{ip}:8080/frognet_echo.php"
    try:
        t0 = time.monotonic()
        with urllib.request.urlopen(
                urllib.request.Request(url), timeout=timeout) as resp:
            body = resp.read()
        if not body:
            config.log.warning("_frognet_echo: %s returned empty body", ip)
            return None
        return time.monotonic() - t0
    except Exception as e:
        config.log.warning("_frognet_echo: %s %s: %s", ip, type(e).__name__, e)
        return None


def startup_reconcile():
    """
    At startup:
    1. Find all live kernel WireGuard interfaces.
    2. Tear down any that have a dead or missing handshake.
    3. Remove any /etc/wireguard/wgN.conf files that don't correspond
       to a live, healthy interface - these are stale leftovers from
       previous daemon runs and would cause wg-quick to fail or reuse
       wrong keys.
    4. Remove any active/*.json state files whose interface is gone.
    """
    trace_enter('wg.startup_reconcile')
    config.log.info("Startup reconciliation...")

    # -- 1. Find live kernel interfaces ------------------------------------
    try:
        result = subprocess.run(["wg", "show", "interfaces"],
                                capture_output=True, text=True, check=False)
        live_ifaces = set(result.stdout.strip().split()) \
            if result.stdout.strip() else set()
    except Exception as e:
        config.log.error("startup_reconcile: wg show interfaces failed: %s", e)
        live_ifaces = set()

    config.log.info("startup_reconcile: live wg ifaces=%s", sorted(live_ifaces))

    # -- 2. Tear down dead interfaces ---------------------------------------
    dead = set()
    for iface in sorted(live_ifaces):
        age = wg_handshake_age(iface)
        if age is None or age >= config.HANDSHAKE_DEAD_SEC:
            config.log.warning(
                "startup_reconcile: %s dead (handshake=%s) - tearing down",
                iface, f"{age}s" if age is not None else "never")
            dead.add(iface)
        else:
            config.log.info("startup_reconcile: %s alive (handshake=%ds)",
                            iface, age)

    for iface in dead:
        teardown_wg_iface(iface)

    live_ifaces -= dead

    # -- 3. Remove orphaned conf files -------------------------------------
    # Any wgN.conf that doesn't belong to a currently live interface
    # is stale. Delete it so find_next_wg_iface() can safely reuse the slot.
    for conf in config.CONF_DIR.glob("wg*.conf"):
        iface = conf.stem          # e.g. "wg3"
        if iface not in live_ifaces:
            config.log.info(
                "startup_reconcile: removing orphaned conf %s (iface not live)",
                conf)
            conf.unlink(missing_ok=True)

    # -- 4. Remove orphaned state files ------------------------------------
    for sf_path in config.ACTIVE_DIR.glob("*.json"):
        try:
            with open(sf_path) as f:
                sf = json.load(f)
            if sf.get("interface", "") not in live_ifaces:
                config.log.info(
                    "startup_reconcile: removing stale state %s (iface gone)",
                    sf_path.name)
                sf_path.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            sf_path.unlink(missing_ok=True)

    config.log.info("startup_reconcile: done - live=%s dead=%s",
                    sorted(live_ifaces), sorted(dead))
