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
broker_namespace.py — Linux namespace + WireGuard provisioning for FrogNet Tunnel Broker.

Each group gets an isolated network namespace with WireGuard interfaces.
All operations use subprocess calls to ip/wg commands — no exotic dependencies.

NEVER masks errors. Every subprocess call is checked.

INSTRUMENTATION POLICY
----------------------
Every subprocess invocation logs: full command, return code, stdout, stderr.
Every interface create/delete logs: ns, iface, ip, port, peer pubkey.
Every route add/del logs: ns, destination, via, dev.
Every namespace create/destroy logs: ns_name, result.
ensure_namespace always logs ip_forward and iptables state after enforcement.
"""

import hashlib
import subprocess
import os
import tempfile
import logging

log = logging.getLogger("broker.namespace")


# ---------------------------------------------------------------------------
# Veth / port-forward helpers
# ---------------------------------------------------------------------------

def _veth_names(ns_name: str):
    """
    Derive stable veth interface names from the namespace name.
    15-char kernel limit: "veth_" + 6hex + "_d" = 13 chars.
    """
    h = hashlib.sha256(ns_name.encode()).hexdigest()[:6]
    return f"veth_{h}_d", f"veth_{h}_n"   # default-ns side, group-ns side


def _veth_ips(group_index: int):
    """
    /30 pair for the veth bridge between default ns and the group ns.
    group 0 → 10.252.254.1/30 (default) ↔ 10.252.254.2/30 (ns)
    group 1 → 10.252.254.5/30           ↔ 10.252.254.6/30
    ...  max 56 groups before the /24 fills.
    """
    base = group_index * 4
    return (
        f"10.252.254.{base + 1}",   # default-ns IP
        f"10.252.254.{base + 2}",   # group-ns IP
        30,                          # prefix length
    )

def setup_wg_masquerade(ns_name: str, iface_name: str):
    """MASQUERADE forwarded traffic leaving a WG interface in the namespace."""
    r = _run(["iptables", "-t", "nat", "-C", "POSTROUTING",
              "-o", iface_name, "-j", "MASQUERADE"], netns=ns_name, check=False)
    if r.returncode == 0:
        log.info("WG MASQUERADE: already present for %s in ns=%s", iface_name, ns_name)
        return
    _run(["iptables", "-t", "nat", "-A", "POSTROUTING",
          "-o", iface_name, "-j", "MASQUERADE"], netns=ns_name)
    log.info("WG MASQUERADE: added for %s in ns=%s", iface_name, ns_name)

def _setup_veth(ns_name: str, group_index: int):
    """
    Ensure a veth pair bridges the default namespace to the group namespace.
    Idempotent — checks before creating.
    Also ensures ip_forward and MASQUERADE are in place in the default ns.
    """
    veth_d, veth_n = _veth_names(ns_name)
    default_ip, ns_ip, pfx = _veth_ips(group_index)
    ns_subnet = f"10.252.254.{group_index * 4}/30"

    # ── Veth pair ────────────────────────────────────────────────────────────
    r = _run(["ip", "link", "show", veth_d], check=False)
    if r.returncode == 0:
        log.info("VETH EXISTS: %s already present — skipping creation", veth_d)
    else:
        log.info("VETH CREATE: %s <-> %s for ns=%s", veth_d, veth_n, ns_name)
        _run(["ip", "link", "add", veth_d, "type", "veth", "peer", "name", veth_n])

        # Move ns-side into the group namespace
        _run(["ip", "link", "set", veth_n, "netns", ns_name])
        log.info("VETH MOVE: %s → ns=%s", veth_n, ns_name)

        # Assign IPs
        _run(["ip", "addr", "add", f"{default_ip}/{pfx}", "dev", veth_d])
        _run(["ip", "addr", "add", f"{ns_ip}/{pfx}", "dev", veth_n], netns=ns_name)

        # Bring up
        _run(["ip", "link", "set", veth_d, "up"])
        _run(["ip", "link", "set", veth_n, "up"], netns=ns_name)

        # Default route in namespace — all return traffic exits via veth
        _run(["ip", "route", "add", "default", "via", default_ip], netns=ns_name)

        r = _run(["iptables", "-C", "FORWARD", "-s", ns_subnet, "-j", "ACCEPT"], check=False)
        if r.returncode != 0:
            _run(["iptables", "-I", "FORWARD", "-s", ns_subnet, "-j", "ACCEPT"])
            log.info("FORWARD: added src=%s ACCEPT", ns_subnet)

        log.info("VETH UP: default=%s/%d  ns=%s/%d  ns=%s",
                 default_ip, pfx, ns_ip, pfx, ns_name)

    # ── ip_forward in DEFAULT namespace ──────────────────────────────────────
    r = _run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)
    log.info("DEFAULT NS ip_forward enforce: %s", r.stdout.strip())

    # ── MASQUERADE for return traffic leaving default ns ──────────────────────
    # -C returns 0 if rule already exists; add only if missing.
    r = _run(["iptables", "-t", "nat", "-C", "POSTROUTING",
              "-s", ns_subnet, "-j", "MASQUERADE"], check=False)
    if r.returncode != 0:
        _run(["iptables", "-t", "nat", "-A", "POSTROUTING",
              "-s", ns_subnet, "-j", "MASQUERADE"])
        log.info("MASQUERADE: added for src=%s", ns_subnet)
    else:
        log.info("MASQUERADE: already present for src=%s", ns_subnet)

    # ── FORWARD rule for DNAT'd WireGuard UDP into the namespace ─────────────
    # DNAT rewrites destination to ns_ip, but ufw's FORWARD policy is DROP.
    # We must insert this rule at position 1 so it is evaluated BEFORE the
    # ufw chains can drop the packet.  Idempotent: -C checks first.
    r = _run(["iptables", "-C", "FORWARD", "-d", ns_ip, "-p", "udp",
              "--dport", "51820:51900", "-j", "ACCEPT"], check=False)
    if r.returncode != 0:
        _run(["iptables", "-I", "FORWARD", "1", "-d", ns_ip, "-p", "udp",
              "--dport", "51820:51900", "-j", "ACCEPT"])
        log.info("FORWARD: inserted dst=%s udp 51820:51900 ACCEPT at top of chain",
                 ns_ip)
    else:
        log.info("FORWARD: dst=%s udp 51820:51900 ACCEPT already present", ns_ip)

    return default_ip, ns_ip


def _flush_dport_rules(port: int):
    """Remove every PREROUTING DNAT and FORWARD ACCEPT rule for udp/<port>,
    regardless of destination.  iptables stops at the first matching rule,
    so a stale DNAT from a prior tunnel — whose destination no longer
    exists — captures packets ahead of any new rule we'd add.  The result
    is silent: tunnel comes up cleanly on the broker side, handshake
    initiates from the joiner, packets get DNAT'd to a dead destination,
    handshake times out at 30s, broker tears down "successfully."
    Flushing by port-only ensures we own the port unambiguously before
    adding the current destination.
    """
    for table, chain in (("nat", "PREROUTING"), ("filter", "FORWARD")):
        r = subprocess.run(["iptables-save", "-t", table],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            log.warning("iptables-save -t %s failed: %s", table, r.stderr)
            continue
        for line in r.stdout.splitlines():
            if not line.startswith(f"-A {chain} "):
                continue
            if " -p udp " not in line:
                continue
            # Match --dport <port> at end-of-line or followed by space
            if (f"--dport {port} " not in line
                    and not line.rstrip().endswith(f"--dport {port}")):
                continue
            del_spec = line.replace(f"-A {chain}", f"-D {chain}", 1)
            args = ["iptables", "-t", table] + del_spec.split()
            _run(args, check=False)
            log.info("PORT_FORWARD: flushed stale %s/%s rule for udp:%d",
                     table, chain, port)


def setup_port_forward(port: int, ns_name: str, group_index: int):
    """
    DNAT external UDP <port> into the group namespace via its veth IP.

    Flushes any prior rules for this port first (regardless of
    destination), so stale rules from previously-destroyed tunnels
    can't shadow the new one.  Must be called after _setup_veth so the
    veth IP exists.
    """
    _flush_dport_rules(port)

    _, ns_ip, _ = _veth_ips(group_index)
    dest = f"{ns_ip}:{port}"

    _run(["iptables", "-t", "nat", "-A", "PREROUTING",
          "-p", "udp", "--dport", str(port),
          "-j", "DNAT", "--to-destination", dest])

    _run(["iptables", "-A", "FORWARD",
          "-d", ns_ip, "-p", "udp", "--dport", str(port), "-j", "ACCEPT"])

    log.info("PORT_FORWARD SETUP: udp:%d → %s (ns=%s)", port, dest, ns_name)


def teardown_port_forward(port: int, group_index: int):
    """
    Remove ALL DNAT + FORWARD rules for a WireGuard port — not just the
    one whose --to-destination matches what we currently compute (which
    can drift across broker restarts as group_index/veth IPs shift).
    """
    _flush_dport_rules(port)
    log.info("PORT_FORWARD TEARDOWN: udp:%d", port)


def _run(cmd, check=True, capture=True, netns=None):
    """Run a command, optionally in a network namespace. Never hides errors."""
    full_cmd = cmd
    if netns:
        full_cmd = ["ip", "netns", "exec", netns] + cmd
    log.debug("CMD: %s", " ".join(full_cmd))
    result = subprocess.run(
        full_cmd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if result.returncode != 0 and check:
        log.error("CMD FAILED [%d]: %s\n  stdout: %s\n  stderr: %s",
                  result.returncode, " ".join(full_cmd),
                  result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(
            f"Command failed [{result.returncode}]: {' '.join(full_cmd)}: "
            f"{result.stderr.strip()}"
        )
    if result.returncode != 0:
        log.debug("CMD OK (check=False) [%d]: %s stderr: %s",
                  result.returncode, " ".join(full_cmd), result.stderr.strip())
    return result


def _wg_genkey():
    """Generate a WireGuard private key, return (private_key, public_key)."""
    log.debug("WG GENKEY: generating new keypair")
    priv = _run(["wg", "genkey"]).stdout.strip()
    pub_result = subprocess.run(
        ["wg", "pubkey"],
        input=priv,
        capture_output=True,
        text=True,
        check=False,
    )
    if pub_result.returncode != 0:
        raise RuntimeError(f"wg pubkey failed: {pub_result.stderr.strip()}")
    pub = pub_result.stdout.strip()
    log.debug("WG GENKEY: pubkey=%s...", pub[:16])
    return priv, pub


def ensure_namespace(ns_name, group_index: int = 0):
    """
    Create network namespace if it doesn't exist, then:
      - enforce ip_forward=1 and iptables ACCEPT inside the namespace
      - create/verify a veth pair bridging the namespace to the default namespace
      - enforce ip_forward=1 and MASQUERADE in the default namespace

    The veth pair is what makes WireGuard ports inside the namespace
    reachable from the internet: external UDP arrives in the default
    namespace, DNAT rewrites the destination to the veth's namespace-side
    IP, the kernel forwards the packet through the veth into the namespace,
    and WireGuard (listening on 0.0.0.0) handles it.

    group_index is used to derive the /30 veth address pair and must be
    stable per group (stored in the groups table).
    """
    log.info("NAMESPACE ENSURE: ns=%s group_index=%d", ns_name, group_index)
    existing = _run(["ip", "netns", "list"], check=False).stdout
    existing_names = existing.split()

    if ns_name not in existing_names:
        log.info("NAMESPACE CREATE: ns=%s (not found in: %s)",
                 ns_name, existing_names)
        _run(["ip", "netns", "add", ns_name])
        _run(["ip", "link", "set", "lo", "up"], netns=ns_name)
        log.info("NAMESPACE CREATE: ns=%s loopback up", ns_name)
        newly_created = True
    else:
        log.info("NAMESPACE EXISTS: ns=%s", ns_name)
        newly_created = False

    # Always enforce — do not rely on prior state or kernel defaults.
    log.info("NAMESPACE ENFORCE: ns=%s setting ip_forward=1", ns_name)
    r = _run(["sysctl", "-w", "net.ipv4.ip_forward=1"], netns=ns_name)
    log.info("NAMESPACE ENFORCE: ns=%s ip_forward sysctl result: %s",
             ns_name, r.stdout.strip())

    for policy in ("FORWARD", "INPUT", "OUTPUT"):
        log.info("NAMESPACE ENFORCE: ns=%s iptables -P %s ACCEPT",
                 ns_name, policy)
        _run(["iptables", "-P", policy, "ACCEPT"], netns=ns_name)

    # Log final state so we can verify in the journal
    fwd = _run(["sysctl", "net.ipv4.ip_forward"], netns=ns_name,
               check=False).stdout.strip()
    ipt = _run(["iptables", "-S"], netns=ns_name, check=False).stdout.strip()
    log.info("NAMESPACE STATE: ns=%s ip_forward='%s' iptables_policies=[\n%s\n]",
             ns_name, fwd, ipt)

    if newly_created:
        log.info("NAMESPACE CREATE COMPLETE: ns=%s", ns_name)
    else:
        log.info("NAMESPACE ENSURE COMPLETE: ns=%s (existing, policies enforced)",
                 ns_name)

    # Always ensure veth bridge and default-namespace rules are in place.
    # Idempotent — safe on every startup/reconnect.
    _setup_veth(ns_name, group_index)

    return newly_created


def delete_namespace(ns_name):
    """Delete a network namespace. All interfaces inside are automatically cleaned up."""
    log.info("NAMESPACE DELETE: ns=%s", ns_name)
    _run(["ip", "netns", "delete", ns_name])
    log.info("NAMESPACE DELETE: ns=%s done", ns_name)


def create_wg_interface(ns_name, iface_name, listen_port, private_key,
                        local_ip, local_mask):
    """
    Create a WireGuard interface inside a namespace.
    IDEMPOTENT: If the interface already exists (in either the default or target
    namespace), it is deleted first.
    """
    log.info("WG CREATE: ns=%s iface=%s ip=%s/%s port=%d",
             ns_name, iface_name, local_ip, local_mask, listen_port)

    # Check default namespace
    result = _run(["ip", "link", "show", iface_name], check=False)
    if result.returncode == 0:
        log.warning("WG CREATE: orphaned %s found in DEFAULT namespace — deleting",
                    iface_name)
        _run(["ip", "link", "delete", iface_name], check=False)
        log.info("WG CREATE: orphan %s deleted from default namespace", iface_name)

    # Check target namespace
    result = _run(["ip", "link", "show", iface_name], netns=ns_name, check=False)
    if result.returncode == 0:
        log.warning("WG CREATE: orphaned %s found in ns=%s — deleting",
                    iface_name, ns_name)
        _run(["ip", "link", "delete", iface_name], netns=ns_name, check=False)
        import time as _time
        for attempt in range(50):
            r = _run(["ip", "link", "show", iface_name], netns=ns_name,
                     check=False)
            if r.returncode != 0:
                log.info("WG CREATE: orphan %s gone from ns=%s after %d polls",
                         iface_name, ns_name, attempt + 1)
                break
            _time.sleep(0.1)
        else:
            raise RuntimeError(
                f"Timed out waiting for {iface_name} to disappear from {ns_name}")

    # Create in default namespace then move
    # Create directly inside the target namespace — socket binds in the right ns
    log.info("WG CREATE: ip link add %s type wireguard directly in ns=%s",
             iface_name, ns_name)
    _run(["ip", "link", "add", iface_name, "type", "wireguard"], netns=ns_name)

    # Configure WireGuard
    with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
        f.write(private_key)
        keyfile = f.name
    try:
        log.info("WG CREATE: wg set %s listen-port=%d private-key=<file>",
                 iface_name, listen_port)
        _run(["wg", "set", iface_name,
              "listen-port", str(listen_port),
              "private-key", keyfile],
             netns=ns_name)
    finally:
        os.unlink(keyfile)

    # Assign IP
    _run(["ip", "addr", "flush", "dev", iface_name], netns=ns_name, check=False)
    log.info("WG CREATE: ip addr add %s/%s dev %s in ns=%s",
             local_ip, local_mask, iface_name, ns_name)
    _run(["ip", "addr", "add", f"{local_ip}/{local_mask}", "dev", iface_name],
         netns=ns_name)

    # Bring up
    _run(["ip", "link", "set", iface_name, "up"], netns=ns_name)

    # Log final interface state
    show = _run(["ip", "addr", "show", "dev", iface_name], netns=ns_name,
                check=False).stdout.strip()
    wg_show = _run(["wg", "show", iface_name], netns=ns_name,
                   check=False).stdout.strip()
    setup_wg_masquerade(ns_name, iface_name)
    log.info("WG CREATE DONE: ns=%s iface=%s ip=%s/%s port=%d\n"
             "  ip addr: %s\n  wg show: %s",
             ns_name, iface_name, local_ip, local_mask, listen_port,
             show, wg_show)


def add_wg_peer(ns_name, iface_name, peer_pubkey, allowed_ips, endpoint=None,
                keepalive=None):
    """Add a WireGuard peer to an interface."""
    log.info("WG PEER ADD: ns=%s iface=%s peer=%s... allowed=%s endpoint=%s "
             "keepalive=%s",
             ns_name, iface_name, peer_pubkey[:16], allowed_ips,
             endpoint, keepalive)
    cmd = ["wg", "set", iface_name,
           "peer", peer_pubkey,
           "allowed-ips", ",".join(allowed_ips)]
    if endpoint:
        cmd.extend(["endpoint", endpoint])
    if keepalive:
        cmd.extend(["persistent-keepalive", str(keepalive)])
    _run(cmd, netns=ns_name)
    log.info("WG PEER ADD DONE: ns=%s iface=%s peer=%s…%s allowed=%s",
             ns_name, iface_name, peer_pubkey[:8], peer_pubkey[-4:], allowed_ips)


def add_route(ns_name, destination, via_ip, dev=None):
    """Add a route inside a namespace.

    If a route to *destination* already exists, leave it alone — the first
    tunnel to claim a destination wins.  This prevents later tunnel creation
    from clobbering a working route with one whose peer hasn't connected yet
    (endpoint=None → Destination Host Unreachable).
    """
    existing = _run(["ip", "route", "show", destination], netns=ns_name,
                    check=False).stdout.strip()
    if existing:
        log.info("ROUTE ADD SKIP: ns=%s %s via %s dev=%s — route already "
                 "exists: '%s'", ns_name, destination, via_ip, dev, existing)
        return

    log.info("ROUTE ADD: ns=%s %s via %s dev=%s", ns_name, destination,
             via_ip, dev)
    cmd = ["ip", "route", "add", destination, "via", via_ip]
    if dev:
        cmd.extend(["dev", dev])
    _run(cmd, netns=ns_name)

    # Verify it landed
    show = _run(["ip", "route", "show", destination], netns=ns_name,
                check=False).stdout.strip()
    log.info("ROUTE ADD DONE: ns=%s %s via %s — kernel: '%s'",
             ns_name, destination, via_ip, show)

# =====================================================================
# RENUMBER SUPPORT — paste into /opt/frognet_broker_v4/broker_namespace_v4.py
#
# Insert the del_route() function below right after add_route() (around
# line 425, before "def remove_wg_peer").
# =====================================================================

def del_route(ns_name, destination):
    """Delete a route inside a namespace, no-op if it doesn't exist.

    Used by the renumber path: when a node changes subnet, the broker
    deletes routes for the *old* subnet across the netns before adding
    new ones for the new subnet.  Tolerates missing routes because the
    same code may be called against tunnels created before the route
    was installed.
    """
    existing = _run(["ip", "route", "show", destination], netns=ns_name,
                    check=False).stdout.strip()
    if not existing:
        log.info("ROUTE DEL SKIP: ns=%s %s — no such route", ns_name, destination)
        return

    log.info("ROUTE DEL: ns=%s %s (was: '%s')", ns_name, destination, existing)
    _run(["ip", "route", "del", destination], netns=ns_name, check=False)

    # Verify it's gone (a /24 may have multiple routes installed; we
    # only delete the first match, so log what's left).
    show = _run(["ip", "route", "show", destination], netns=ns_name,
                check=False).stdout.strip()
    if show:
        log.warning("ROUTE DEL PARTIAL: ns=%s %s — remaining: '%s'",
                    ns_name, destination, show)
    else:
        log.info("ROUTE DEL DONE: ns=%s %s", ns_name, destination)

def remove_wg_peer(ns_name, iface_name, peer_pubkey):
    """Remove a WireGuard peer from an interface."""
    log.info("WG PEER REMOVE: ns=%s iface=%s peer=%s...",
             ns_name, iface_name, peer_pubkey[:16])
    _run(["wg", "set", iface_name, "peer", peer_pubkey, "remove"],
         netns=ns_name)
    log.info("WG PEER REMOVE DONE: ns=%s iface=%s peer=%s…%s",
             ns_name, iface_name, peer_pubkey[:8], peer_pubkey[-4:])


def delete_wg_interface(ns_name, iface_name):
    """Delete a WireGuard interface inside a namespace."""
    log.info("WG DELETE: ns=%s iface=%s", ns_name, iface_name)
    _run(["ip", "link", "delete", iface_name], netns=ns_name)
    log.info("WG DELETE DONE: ns=%s iface=%s", ns_name, iface_name)


def get_public_ip():
    """Get the droplet's public IP. Used for WireGuard endpoint."""
    log.debug("PUBLIC IP: trying DigitalOcean metadata service")
    result = _run(
        ["curl", "-sf", "--max-time", "2",
         "http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address"],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        ip = result.stdout.strip()
        log.info("PUBLIC IP: %s (from DO metadata)", ip)
        return ip

    log.debug("PUBLIC IP: metadata failed, trying ifconfig.me")
    result = _run(["curl", "-sf", "--max-time", "5", "https://ifconfig.me"],
                  check=False)
    if result.returncode == 0 and result.stdout.strip():
        ip = result.stdout.strip()
        log.info("PUBLIC IP: %s (from ifconfig.me)", ip)
        return ip

    raise RuntimeError("Cannot determine public IP")


def compute_transit_ips(group_index, tunnel_index):
    """
    Compute /30 transit IPs for a tunnel.

    Each group gets 10.252.{200+group_index}.0/24
    Each tunnel gets a /30 within that:
      tunnel 0: .0/30  host=.1  edge=.2
      tunnel 1: .4/30  host=.5  edge=.6
      tunnel N: .(N*4)/30

    Returns (droplet_ip, edge_ip, mask)
    """
    if group_index > 55:
        raise ValueError(
            f"group_index {group_index} exceeds max (55)")
    if tunnel_index > 63:
        raise ValueError(
            f"tunnel_index {tunnel_index} exceeds max (63 per /24)")

    third_octet = 200 + group_index
    base = tunnel_index * 4
    droplet_ip = f"10.252.{third_octet}.{base + 1}"
    edge_ip = f"10.252.{third_octet}.{base + 2}"
    log.debug("TRANSIT IPS: group_index=%d tunnel_index=%d "
              "droplet=%s edge=%s /30",
              group_index, tunnel_index, droplet_ip, edge_ip)
    return droplet_ip, edge_ip, 30
