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
netns_backend.py (M2) - run the SAME live_engine converge against a real kernel.

live_engine.converge_real(topo, ipr_factory=...) is backend-agnostic. This module
provides the real backend:

  - A NetnsProvisioner turns a frognet_sim Topology into a real testbed: one
    network namespace per node, veth pairs for each L2 LAN segment, and a
    WireGuard interface pair for each tunnel link, addressed exactly as the
    topology says. It also writes per-node `ip`/`wg` wrapper scripts so the
    stock RealIPRoute (which shells out to `ip`/`wg`) operates INSIDE each node's
    namespace with no code change.
  - make_ipr_factory("real", prov) hands live_engine a RealIPRoute bound to each
    node's namespace; make_ipr_factory("fake") returns FakeIPRoute (default).

Fake-here / real-on-box (per the standing rule): provisioning is driven through a
Runner. DryRunner (default, used in the container - no privileges, no kernel)
records the exact argv it WOULD execute, so the provisioning logic is validated
here without touching a kernel. ShellRunner executes for real and is used only on
a Linux box as root. Selection: env FROGNET_SIM_BACKEND=fake|real, and for real,
FROGNET_SIM_EXECUTE=1 to use ShellRunner instead of DryRunner.

Feedback loop: a real run's measured RTTs / failures can be captured and fed back
to calibrate frognet_sim's EDGE_RTT_BY_KIND and to seed failure scenarios - the
offline model gets more faithful each time it meets hardware.
"""
import os
import shlex
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from frognet_log import get_logger
from frognet_route.iproute import FakeIPRoute, RealIPRoute

log = get_logger("simulation.netns_backend")


class DryRunner:
    """Records commands instead of executing. Used in the container."""
    def __init__(self):
        self.commands = []
    def run(self, argv):
        self.commands.append(list(argv))
        return 0, "", ""
    def is_real(self):
        return False


class ShellRunner:
    """Executes for real. BOX-ONLY: needs Linux + root (ip netns / wg)."""
    def __init__(self):
        import platform
        if platform.system() != "Linux":
            raise RuntimeError("ShellRunner requires Linux (ip netns / wg)")
        if os.geteuid() != 0:
            raise RuntimeError("ShellRunner requires root (ip netns / wg)")
    def run(self, argv):
        import subprocess
        r = subprocess.run(argv, capture_output=True, text=True, check=False)
        return r.returncode, r.stdout, r.stderr
    def is_real(self):
        return True


class NetnsProvisioner:
    """Build a real netns testbed from a frognet_sim Topology.

    The plan is deterministic and emitted command-by-command so it can be
    inspected (DryRunner) or executed (ShellRunner) by the same code.
    """
    def __init__(self, topo, runner=None, wrapper_dir=None):
        self.topo = topo
        self.runner = runner or DryRunner()
        # [SIM_TMP_MUST_NOT_MATCH_THE_MERGE_GLOB_V1] This was
        # /tmp/frognet_netns, which runMerge.bash deletes: `rm -rf /tmp/frognet*`.
        # On a live node a merge fires on any route or /etc/hosts change, so a
        # merge landing mid-run removes the per-node ip/wg wrappers the real
        # backend is executing, and the topology dies with
        #
        #   FileNotFoundError: [Errno 2] No such file or directory:
        #                      '/tmp/frognet_netns/Lb3/ip'
        #
        # Node-random and topology-random, because it depends on when the merge
        # fires -- which is why it reads like a harness bug and is not one. The
        # merge glob is deliberate; the harness has no business inside it.
        # FROGNET_SIM_NETNS_DIR overrides.
        self.wrapper_dir = (wrapper_dir
                            or os.environ.get("FROGNET_SIM_NETNS_DIR")
                            or os.path.join("/tmp", "frogsim_netns"))
        self._wg_port = 51820

    def _ns(self, node):
        return f"fns_{node}"

    def _r(self, *argv, quiet=False):
        rc, out, err = self.runner.run(list(argv))
        if rc != 0 and self.runner.is_real() and not quiet:
            log.warning("cmd failed rc=%d: %s :: %s", rc, " ".join(argv), err.strip())
        return rc, out, err

    def provision(self):
        """Create namespaces, addresses, LAN veths, and WG tunnels."""
        t = self.topo
        # 1. namespace per node, loopback up
        for name in t.nodes:
            self._r("ip", "netns", "add", self._ns(name))
            self._r("ip", "netns", "exec", self._ns(name), "ip", "link", "set", "lo", "up")
        # 2. LAN segments -> a veth per (node,iface) into a shared bridge per segment
        for si, seg in enumerate(t.segments):
            # only model multi-access LAN segments (eth/wlan); tunnels handled below
            if any(iface.startswith("wg") for (_n, iface) in seg):
                continue
            br = f"fbr{si}"
            self._r("ip", "link", "add", br, "type", "bridge")
            self._r("ip", "link", "set", br, "up")
            for mi, (node, iface) in enumerate(seg):
                ip = t.nodes[node].ifaces[iface].addr_cidr
                # [IFNAME_LEN_V1] Linux IFNAMSIZ caps interface names at 15 chars.
                # Deriving veth names from node names (`v{si}_{node}_h` /
                # `tmp_{node}_{iface}`) overflowed for names like BlackBox or
                # Seattle-W1 (>=7 chars) -> `ip link add` rejected -> the LAN iface
                # never existed -> every route over it failed ENETUNREACH (the
                # committer errors were downstream symptoms). Use short, unique,
                # index-based transient names; the in-netns end is renamed to the
                # real iface (eth0/wlan0/ham0/...), which is itself always short.
                veth_h = f"fv{si}_{mi}h"   # host side, stays in root netns on br
                veth_t = f"fv{si}_{mi}t"   # transient peer, renamed inside the ns
                # [VETH_RECLAIM_V1] The host side lives in the root ns and is
                # normally reaped only when `ip netns del` destroys its in-ns peer
                # (veth pairs die together). That reap is on the kernel's ASYNC
                # namespace-teardown path and can lag the next topology reusing the
                # same fv{si}_{mi}h name -> `ip link add ... :: File exists`, the
                # add aborts, no fv..t peer is created, and every following step
                # fails (the committer ENETUNREACH errors are the downstream
                # symptom). Don't trust cascade timing: synchronously reclaim the
                # name here. `ip link del` on a root-ns device is synchronous, so
                # the add below always sees a free name. Quiet: "Cannot find" is
                # the normal (no-leak) case and must not spam the log.
                self._r("ip", "link", "del", veth_h, quiet=True)
                self._r("ip", "link", "add", veth_h, "type", "veth", "peer", "name",
                        veth_t)
                self._r("ip", "link", "set", veth_h, "master", br, "up")
                self._r("ip", "link", "set", veth_t, "netns", self._ns(node))
                self._r("ip", "netns", "exec", self._ns(node), "ip", "link", "set",
                        veth_t, "name", iface)
                self._r("ip", "netns", "exec", self._ns(node), "ip", "addr", "add", ip,
                        "dev", iface)
                self._r("ip", "netns", "exec", self._ns(node), "ip", "link", "set",
                        iface, "up")
        # 3. WG tunnels: each wg-bearing segment is a point-to-point link.
        #    Generate a keypair per endpoint, set up the iface, and peer the two
        #    ends with AllowedIPs=10.0.0.0/8 (the standing FrogNet rule).
        for seg in t.segments:
            wg_members = [(n, i) for (n, i) in seg if i.startswith("wg")]
            if len(wg_members) != 2:
                continue
            (na, ia), (nb, ib) = wg_members
            ipa = t.nodes[na].ifaces[ia].addr_cidr
            ipb = t.nodes[nb].ifaces[ib].addr_cidr
            pa, pb = self._wg_port, self._wg_port + 1
            self._wg_port += 2
            ka_priv, ka_pub = self._genkey(na, ia)
            kb_priv, kb_pub = self._genkey(nb, ib)
            ends = ((na, ia, ipa, pa, ka_priv, kb_pub, pb),
                    (nb, ib, ipb, pb, kb_priv, ka_pub, pa))
            for (node, iface, ip, port, priv, peer_pub, peer_port) in ends:
                ns = self._ns(node)
                self._r("ip", "netns", "exec", ns, "ip", "link", "add", iface,
                        "type", "wireguard", quiet=True)
                if not self._iface_exists(ns, iface):
                    # [WG_USERSPACE_FALLBACK_V1] `ip link add ... type
                    # wireguard` needs wireguard.ko on the host. Without it the
                    # only symptom downstream is a wall of ROUTE_INSTALL_FAILED
                    # ... ENETUNREACH, because the iface the routes point at was
                    # never created. wireguard-go is the reference USERSPACE
                    # implementation: same protocol, same keys, same `wg set`.
                    # Tunnels here are simulated either way -- endpoints are
                    # 127.0.0.1 loopback ports, not a real internet path -- so
                    # this costs no fidelity the tier ever claimed. Kernel first,
                    # always; userspace only when the kernel refuses.
                    #
                    # THREE things this has to get right, each found the hard way:
                    #
                    # 1. CREATE IN ROOT, THEN MOVE. A WireGuard device keeps its
                    #    UDP socket in the namespace it was CREATED in. Created
                    #    inside the node's namespace, its 127.0.0.1 endpoint is
                    #    that namespace's own loopback and the two ends can never
                    #    reach each other: the device exists, no handshake ever
                    #    happens, the planner has no next hop, and `via` comes
                    #    out EMPTY.
                    # 2. A UNIQUE ROOT-SIDE NAME, renamed inside the namespace --
                    #    the same trick this file already uses for veth peers.
                    #    Node iface names REPEAT across nodes (every node has a
                    #    "wg0") and wireguard-go's UAPI socket at
                    #    /var/run/wireguard/<name>.sock is ROOT-namespace-wide,
                    #    so a shared name makes one node's `wg set` land on
                    #    another's still-running device: "Unable to modify
                    #    interface: Protocol not supported".
                    # 3. wireguard-go REFUSES to start when the kernel
                    #    advertises first-class support -- which this kernel
                    #    does, even where the module will not load -- so the
                    #    documented override is required.
                    self._wg_uniq = getattr(self, "_wg_uniq", 0) + 1
                    tmpif = f"wgu{self._wg_uniq}"
                    self._r("rm", "-f", f"/var/run/wireguard/{tmpif}.sock",
                            quiet=True)
                    self._r("env",
                            "WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1",
                            "wireguard-go", tmpif)
                    if not self._iface_exists(None, tmpif, tries=25):
                        raise RuntimeError(
                            f"{node}: could not create {iface} -- no wireguard "
                            f"kernel module and wireguard-go did not start. "
                            f"Install wireguard-go, or run on a host with "
                            f"wireguard.ko.")
                    self._r("wg", "set", tmpif, "private-key",
                            self._keyfile(node, iface), "listen-port", str(port))
                    self._r("wg", "set", tmpif, "peer", peer_pub,
                            "allowed-ips", "10.0.0.0/8",
                            "endpoint", f"127.0.0.1:{peer_port}")
                    self._r("ip", "link", "set", tmpif, "netns", ns)
                    self._r("ip", "netns", "exec", ns, "ip", "link", "set",
                            tmpif, "name", iface)
                else:
                    self._r("ip", "netns", "exec", ns, "wg", "set", iface,
                            "private-key", self._keyfile(node, iface),
                            "listen-port", str(port))
                    self._r("ip", "netns", "exec", ns, "wg", "set", iface,
                            "peer", peer_pub, "allowed-ips", "10.0.0.0/8",
                            "endpoint", f"127.0.0.1:{peer_port}")
                self._r("ip", "netns", "exec", ns, "ip", "addr", "add", ip,
                        "dev", iface)
                self._r("ip", "netns", "exec", ns, "ip", "link", "set", iface,
                        "up")
        return self.runner

    def _iface_exists(self, ns, iface, tries=1):
        """True once `iface` exists (in `ns`, or in root when ns is None).
        wireguard-go daemonises, so the device appears a moment AFTER the
        command returns -- hence the retry."""
        import time as _t
        for _ in range(max(1, tries)):
            pre = ["ip", "netns", "exec", ns] if ns else []
            rc, _o, _e = self._r(*pre, "ip", "link", "show", iface, quiet=True)
            if rc == 0:
                return True
            _t.sleep(0.1)
        return False

    def _genkey(self, node, iface):
        """Generate a wg keypair for (node,iface). On a real runner shells out to
        `wg genkey|wg pubkey` and writes the private key to a file the `wg set`
        above references; on a dry runner returns deterministic placeholders so
        the plan is inspectable."""
        if self.runner.is_real():
            import subprocess
            priv = subprocess.run(["wg", "genkey"], capture_output=True, text=True).stdout.strip()
            pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True).stdout.strip()
            kf = self._keyfile(node, iface)
            os.makedirs(os.path.dirname(kf), exist_ok=True)
            with open(kf, "w") as f:
                f.write(priv + "\n")
            os.chmod(kf, 0o600)
            return priv, pub
        return (f"<priv:{node}/{iface}>", f"<pub:{node}/{iface}>")

    def _keyfile(self, node, iface):
        return os.path.join(self.wrapper_dir, node, f"{iface}.key")

    def teardown(self):
        """Remove all namespaces, host-side veths, and bridges this provisioner
        created. Idempotent; safe to call on cleanup. Host-side veths are deleted
        explicitly rather than left to `ip netns del` cascade - see
        [VETH_RECLAIM_V1] in provision: the cascade reap is async and unreliable
        as a between-topology guarantee."""
        for name in self.topo.nodes:
            self._r("ip", "netns", "del", self._ns(name))
        for si, seg in enumerate(self.topo.segments):
            if any(i.startswith("wg") for (_n, i) in seg):
                continue
            for mi in range(len(seg)):
                self._r("ip", "link", "del", f"fv{si}_{mi}h", quiet=True)
            self._r("ip", "link", "del", f"fbr{si}")
        return self.runner

    def write_wrappers(self):
        """Emit per-node `ip`/`wg` wrappers that exec inside the namespace, so the
        stock RealIPRoute runs unmodified against each node's kernel view."""
        cmds = []
        for name in self.topo.nodes:
            d = os.path.join(self.wrapper_dir, name)
            for tool in ("ip", "wg", "wg-quick"):
                path = os.path.join(d, tool)
                body = f'#!/bin/sh\nexec ip netns exec {self._ns(name)} {tool} "$@"\n'
                cmds.append((path, body))
        if self.runner.is_real():
            for path, body in cmds:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(body)
                os.chmod(path, 0o755)
        return cmds


def make_ipr_factory(mode="fake", prov=None):
    """Return an ipr_factory(node_name) for live_engine.converge_real.

    mode='fake' (default, container): FakeIPRoute, no kernel.
    mode='real' (box): RealIPRoute whose ip/wg binaries are the per-node netns
    wrappers, so each node's commits land in its own namespace.
    """
    if mode == "fake":
        return lambda name: FakeIPRoute()
    if mode == "real":
        if prov is None:
            raise ValueError("real backend needs a NetnsProvisioner")
        base = prov.wrapper_dir
        def factory(name):
            d = os.path.join(base, name)
            return RealIPRoute(ip_bin=os.path.join(d, "ip"),
                               wg_bin=os.path.join(d, "wg"),
                               wg_quick_bin=os.path.join(d, "wg-quick"))
        return factory
    raise ValueError(f"unknown backend mode: {mode}")


def main():
    """Dry-run: build a testbed plan for one topology and print the argv. Proves
    the provisioning logic without a kernel. On a box: FROGNET_SIM_EXECUTE=1."""
    import frognet_sim as H
    topo = H.topo_pond_full_mesh()
    H.assign_identities(topo)
    execute = os.environ.get("FROGNET_SIM_EXECUTE", "") in ("1", "true", "yes")
    runner = ShellRunner() if execute else DryRunner()
    prov = NetnsProvisioner(topo, runner=runner)
    prov.provision()
    wrappers = prov.write_wrappers()
    if not runner.is_real():
        print(f"=== M2 netns provisioning PLAN for: {topo.label} "
              f"({len(topo.nodes)} nodes) ===")
        print(f"  {len(runner.commands)} kernel commands, "
              f"{len(wrappers)} wrapper scripts (dry-run; set FROGNET_SIM_EXECUTE=1 "
              f"on a Linux box as root to apply)\n")
        for c in runner.commands[:24]:
            print("   " + " ".join(shlex.quote(x) for x in c))
        if len(runner.commands) > 24:
            print(f"   ... (+{len(runner.commands) - 24} more)")
    else:
        print(f"provisioned {len(topo.nodes)} namespaces for {topo.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
