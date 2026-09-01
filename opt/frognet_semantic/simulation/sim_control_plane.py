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
sim_control_plane.py - model the host control plane (WireGuard kernel,
NetworkManager dispatcher, dnsmasq) around the REAL internet_tunnels_v3
reconcile, and reproduce the Seattle5 2026-06-05 21:34 tunnel teardown.

Why this exists: the planner/committer sim (live_engine) never touches
internet_tunnels_v3/poll.py, which is where tunnels are actually born and
killed. The 21:34 incident was a control-plane feedback loop:

    a merge's bring-up phase tears down wg ifaces
      -> kernel RTM_DELLINK
        -> NetworkManager sees the device vanish, logs 'removed'
          -> nm-dispatcher fires runMerge on the device change
            -> that runMerge BAILs 'lock_held' (a merge already holds the lock)

None of that is modeled by the route sim. This module supplies the missing
layers as light models that issue real callbacks, and drives the ACTUAL
poll._reconcile_bringup_phase() through them - only the I/O leaves (ping,
wg show, broker HTTP, ip link del) are faked, the decision ladder and the
teardown functions are the installed code.

Run: PYTHONPATH=<tree>/opt/frognet_semantic python3 -m simulation.sim_control_plane
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
os.environ.setdefault("FROGNET_PROXY_ROOT", _PARENT)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
sys.dont_write_bytecode = True

from internet_tunnels_v3 import poll, wg, config

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


# ------------------------- sim kernel (wg) --------------------------
class SimKernel:
    """The wg interfaces the kernel holds. add/del fire observers - that
    is the netlink RTM_NEWLINK/DELLINK that NetworkManager reacts to."""
    def __init__(self):
        self.ifaces = {}          # iface -> {"pubkey":..., "hs_age":float|None}
        self.observers = []       # callables(event, iface)

    def add(self, iface, pubkey, hs_age=1.0):
        self.ifaces[iface] = {"pubkey": pubkey, "hs_age": hs_age}
        self._fire("added", iface)

    def seed(self, iface, pubkey, hs_age=1.0):
        """Set up pre-existing kernel state WITHOUT firing observers -
        used to establish a baseline before the scenario's events."""
        self.ifaces[iface] = {"pubkey": pubkey, "hs_age": hs_age}

    def delete(self, iface):
        if iface in self.ifaces:
            del self.ifaces[iface]
            self._fire("removed", iface)

    def enum(self):
        return {i: d["pubkey"] for i, d in self.ifaces.items()}

    def handshake_age(self, iface):
        d = self.ifaces.get(iface)
        return None if d is None else d["hs_age"]

    def _fire(self, event, iface):
        for ob in list(self.observers):
            ob(event, iface)


# ------------------------- sim broker -------------------------------
class SimBroker:
    """Returns the channel list this node should hold. Can be made to
    raise (transient unreachable) or to omit a channel (positive
    authority that a tunnel should go away)."""
    def __init__(self):
        self.channels = {}        # channel_name -> droplet_pubkey
        self.unreachable = False

    def my_channels(self):
        if self.unreachable:
            raise OSError(101, "Network is unreachable")
        return {"channels": [
            {"channel_name": cn,
             "wg_config": {"droplet_pubkey": pk, "droplet_endpoint": "203.0.113.1:51820"}}
            for cn, pk in self.channels.items()
        ]}


# --------------------- control plane orchestrator -------------------
class ControlPlane:
    def __init__(self):
        self.kernel = SimKernel()
        self.broker = SimBroker()
        self.have_internet = True
        self.have_uplink = True
        self.lock_held = False
        # observability counters
        self.nm_removed = 0
        self.nm_added = 0
        self.merge_bails = 0
        self.merges_run = 0
        self.dnsmasq_triggers = 0
        self.log = []
        self.kernel.observers.append(self._nm_dispatch)

    def _emit(self, line):
        self.log.append(line)
        if os.environ.get("SCP_DEBUG"):
            print("    " + line)

    # NetworkManager: witnesses every link change and fires nm-dispatcher
    # -> runMerge. This is the feedback edge that produced the lock_held
    # storm in the real journal.
    def _nm_dispatch(self, event, iface):
        if event == "removed":
            self.nm_removed += 1
            self._emit(f"NetworkManager: device ({iface}): activated -> "
                       f"unmanaged (reason 'unmanaged', sys-iface-state: 'removed')")
        else:
            self.nm_added += 1
            self._emit(f"NetworkManager: device ({iface}): managed -> activated")
        # nm-dispatcher fires a merge synchronously on the state change.
        self.run_merge(trigger=f"nm-dispatcher:{iface}-{event}")

    # dnsmasq: a DHCP lease change runs the dhcp-script, which (per the
    # cutover) sets sync_required and triggers a merge / neighbor notify.
    def dnsmasq_lease(self, action, mac, ip):
        self.dnsmasq_triggers += 1
        self._emit(f"dnsmasq: dhcp-script {action} {mac} {ip} -> sync_required, runMerge")
        self.run_merge(trigger=f"dnsmasq:{action}:{ip}")

    def run_merge(self, trigger="manual"):
        if self.lock_held:
            self.merge_bails += 1
            self._emit(f"runMerge WARN BAIL reason=lock_held trigger={trigger}")
            return
        self.lock_held = True
        self.merges_run += 1
        self._emit(f"runMerge ENTER trigger={trigger}")
        try:
            poll._reconcile_bringup_phase()
        finally:
            self.lock_held = False
            self._emit(f"runMerge EXIT trigger={trigger}")


# --------------------- seam patching (real code, fake I/O) ----------
def install_patches(cp):
    saved = {}

    def save(mod, name):
        saved[(mod, name)] = getattr(mod, name)

    for name in ("_have_internet", "_node_has_own_uplink", "broker_get",
                 "_enumerate_kernel_wg_ifaces", "teardown_wg_iface",
                 "wg_handshake_age", "_signal_bringup_ready",
                 "_persist_last_broker_state", "_bring_up_tunnel"):
        save(poll, name)
    save(wg, "refresh_handshake")
    save(config, "BROKER_DISABLED")
    save(config, "PUBKEY")

    poll._have_internet = lambda: cp.have_internet
    poll._node_has_own_uplink = lambda: cp.have_uplink
    poll.broker_get = lambda path, params=None: (
        cp.broker.my_channels() if path.endswith("my-channels") else {})
    poll._enumerate_kernel_wg_ifaces = lambda: cp.kernel.enum()
    poll.teardown_wg_iface = lambda iface: cp.kernel.delete(iface)
    poll.wg_handshake_age = lambda iface: cp.kernel.handshake_age(iface)
    poll._signal_bringup_ready = lambda: None
    poll._persist_last_broker_state = lambda names, chans: None
    # Bring-up is the I/O leaf (allocates wgN, runs wg-quick up). The
    # teardown ladder is what's under test, so bring-up is a sim no-op:
    # a channel the broker authorizes is modeled as already present in
    # the sim kernel, so a "bring up" is a successful no-op.
    poll._bring_up_tunnel = lambda channel: True
    wg.refresh_handshake = lambda iface, wait_sec: 1.0   # always fresh
    config.BROKER_DISABLED = False
    config.PUBKEY = "SIMPUBKEY="

    # _tear_down_orphan_iface calls the module-local teardown_wg_iface
    # (patched above) and touches _active_tunnels; reset that state.
    poll._active_tunnels.clear()

    def restore():
        for (mod, name), val in saved.items():
            setattr(mod, name, val)
    return restore


# ----------------------------- scenarios ----------------------------
def _fresh_three(cp):
    """wg0/wg1/wg2 up, freshly handshaking, all broker-authorized."""
    cp.kernel.ifaces.clear()
    cp.broker.channels.clear()
    cp.broker.unreachable = False
    cp.have_internet = True
    cp.have_uplink = True
    for i, (iface, sub) in enumerate([("wg0", "10.102.60"),
                                      ("wg1", "10.179.179"),
                                      ("wg2", "10.130.130")]):
        pk = f"PUB{i}="
        cp.broker.channels[f"ch-{sub}"] = pk      # broker first...
        cp.kernel.seed(iface, pk, hs_age=3.0)     # ...then seed iface silently (3s = live)
    # clear counters after setup adds
    cp.nm_added = cp.nm_removed = cp.merge_bails = cp.merges_run = 0
    cp.dnsmasq_triggers = 0
    cp.log.clear()


def scenario_baseline_quiet():
    print("baseline: established+handshaking tunnels, healthy broker -> quiet")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        cp.run_merge(trigger="timer")
        check("no ifaces torn down", set(cp.kernel.ifaces) == {"wg0", "wg1", "wg2"},
              f"kernel={sorted(cp.kernel.ifaces)}")
        check("NetworkManager fired no removals", cp.nm_removed == 0)
        check("no lock_held storm", cp.merge_bails == 0)
    finally:
        restore()


def scenario_transient_no_internet():
    print("transient: one dropped 8.8.8.8 ping (no_internet) during a merge")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        cp.have_internet = False           # single failed ping this pass
        cp.run_merge(trigger="timer")
        check("live tunnels SURVIVE transient no_internet",
              set(cp.kernel.ifaces) == {"wg0", "wg1", "wg2"},
              f"kernel={sorted(cp.kernel.ifaces)}")
        check("NetworkManager fired no removals", cp.nm_removed == 0)
        check("no dispatcher lock_held storm", cp.merge_bails == 0)
    finally:
        restore()


def scenario_transient_broker_unreachable():
    print("transient: one broker fetch throws (broker_unreachable) during a merge")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        cp.broker.unreachable = True
        cp.run_merge(trigger="timer")
        check("live tunnels SURVIVE transient broker_unreachable",
              set(cp.kernel.ifaces) == {"wg0", "wg1", "wg2"},
              f"kernel={sorted(cp.kernel.ifaces)}")
        check("NetworkManager fired no removals", cp.nm_removed == 0)
    finally:
        restore()


def scenario_reproduce_2134():
    print("reproduce 21:34: PRE-FIX behavior (transient nukes all) -> NM storm")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        # Restore the pre-fix wiring: transient short-circuit -> tear down ALL,
        # ignoring handshake liveness. This is exactly what shipped before the fix.
        prefix = poll._tear_down_stale_only
        poll._tear_down_stale_only = poll._tear_down_all_kernel_wg_ifaces
        try:
            cp.have_internet = False        # one dropped ping
            cp.run_merge(trigger="timer")
        finally:
            poll._tear_down_stale_only = prefix
        check("PRE-FIX: all three live tunnels torn down (the incident)",
              cp.kernel.ifaces == {}, f"kernel={sorted(cp.kernel.ifaces)}")
        check("PRE-FIX: NetworkManager logged 3 'removed' events", cp.nm_removed == 3,
              f"nm_removed={cp.nm_removed}")
        check("PRE-FIX: each removal fired an nm-dispatcher runMerge that BAILed lock_held",
              cp.merge_bails == 3, f"merge_bails={cp.merge_bails}")
    finally:
        restore()


def scenario_authority_preserved():
    print("authority: broker positively drops wg1 -> wg1 (and only wg1) torn down")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        del cp.broker.channels["ch-10.179.179"]   # broker no longer authorizes wg1
        cp.run_merge(trigger="timer")
        check("broker-dropped wg1 IS torn down (authority honored)",
              "wg1" not in cp.kernel.ifaces, f"kernel={sorted(cp.kernel.ifaces)}")
        check("wg0/wg2 kept", {"wg0", "wg2"} <= set(cp.kernel.ifaces),
              f"kernel={sorted(cp.kernel.ifaces)}")
    finally:
        restore()


def scenario_dnsmasq_callback():
    print("dnsmasq: a DHCP lease add fires the dhcp-script -> runMerge (healthy)")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        cp.dnsmasq_lease("add", "aa:bb:cc:dd:ee:ff", "10.102.60.57")
        check("dnsmasq lease drove exactly one merge", cp.merges_run == 1,
              f"merges_run={cp.merges_run}")
        check("healthy dnsmasq-triggered merge tore nothing down",
              set(cp.kernel.ifaces) == {"wg0", "wg1", "wg2"})
    finally:
        restore()


# ------------- Apache propogateNotification gossip (fleet) ----------
# propogateNotification.php (Apache) -> propogateNotificationInternal:
#   dedup by EVENT_ID -> trigger local merge -> re-propagate to DIRECT
#   neighbors only. The neighbor set is computed by the REAL
#   discovery.neighbors.direct_neighbor_targets (oracle-tested), so the
#   sim's scoping is the installed logic, not a mock. The receive only
#   ever *does* anything by firing a merge - matching "these fire during
#   a merge."
from discovery.neighbors import direct_neighbor_targets


class FleetNode:
    def __init__(self, name, sub3):
        self.name = name
        self.sub3 = sub3
        self.ip1 = f"{sub3}.1"
        self.neighbors = []        # list[FleetNode]
        self.seen = set()          # /run/frognet/seen_notifications dedup
        self.merges = 0

    @property
    def channel_names(self):
        # active WG tunnel to each neighbor: "<PeerName>-<peer sub3>"
        return [f"{nb.name}-{nb.sub3}" for nb in self.neighbors]

    def targets(self):
        # REAL neighbor-scoping. route_text empty -> tunnel branch only.
        return direct_neighbor_targets("", self.channel_names, {self.ip1, "127.0.0.1"})


class Fleet:
    def __init__(self):
        self.nodes = {}            # ip1 -> FleetNode
        self.deliveries = 0        # propogateNotification.php calls (curls)
        self.dedup_skips = 0
        self.trace = []

    def add(self, name, sub3):
        n = FleetNode(name, sub3)
        self.nodes[n.ip1] = n
        return n

    @staticmethod
    def link(a, b):
        a.neighbors.append(b)
        b.neighbors.append(a)

    def origin_propagate(self, origin, event_id):
        """A merge on `origin` changed hosts -> sync_required -> runMerge
        fires propogateNotification to origin's direct neighbors."""
        origin.seen.add(event_id)
        origin.merges += 1                      # the merge that set sync_required
        for tgt in origin.targets():
            self._deliver(tgt, event_id, origin.name)

    def _deliver(self, ip, event_id, from_name):
        """Apache propogateNotification.php on the receiver -> internal."""
        self.deliveries += 1
        node = self.nodes.get(ip)
        if node is None:
            return
        self.trace.append(f"Apache@{ip}: propogateNotification.php?event={event_id} from={from_name}")
        if event_id in node.seen:               # dedup by EVENT_ID
            self.dedup_skips += 1
            return
        node.seen.add(event_id)
        node.merges += 1                         # receive triggers a local merge
        for tgt in node.targets():               # re-propagate to direct neighbors
            self._deliver(tgt, event_id, node.name)


def _build_fleet():
    """6 nodes, max degree 3 < fleet size, so per-node fan-out is provably
    less than O(fleet):  N1-N2-N3-N4, N4-N5, N4-N6."""
    f = Fleet()
    n = {i: f.add(f"N{i}", f"10.{i}0.{i}0") for i in range(1, 7)}
    Fleet.link(n[1], n[2]); Fleet.link(n[2], n[3]); Fleet.link(n[3], n[4])
    Fleet.link(n[4], n[5]); Fleet.link(n[4], n[6])
    return f, n


def scenario_gossip_is_neighbor_scoped():
    print("gossip: origin notifies ONLY direct neighbors (not the fleet)")
    f, n = _build_fleet()
    t1 = n[1].targets()
    check("N1 (degree 1) targets exactly its 1 neighbor, not all 6",
          t1 == [n[2].ip1], f"targets={t1}")
    t4 = n[4].targets()
    check("N4 (degree 3) targets exactly its 3 neighbors",
          set(t4) == {n[3].ip1, n[5].ip1, n[6].ip1}, f"targets={t4}")
    check("no node targets the whole fleet (fan-out = degree, O(neighbors))",
          all(len(node.targets()) <= 3 for node in f.nodes.values()))


def scenario_gossip_reaches_all_and_terminates():
    print("gossip: epidemic from one origin reaches all, dedup terminates it")
    f, n = _build_fleet()
    f.origin_propagate(n[1], event_id="evt-abc123")
    check("every node received/merged on the event (mesh-wide reach)",
          all(node.merges >= 1 for node in f.nodes.values()),
          f"merges={ {nm: nd.merges for nm, nd in [(x.name, x) for x in f.nodes.values()]} }")
    check("each node merged exactly once (dedup prevents re-merge storm)",
          all(node.merges == 1 for node in f.nodes.values()))
    # 5 undirected edges -> at most 2 deliveries per edge = 10; the second
    # direction of each edge dedup-skips. Bounded by O(edges), never O(N^2).
    check("delivery count bounded by O(edges), not O(N^2)",
          f.deliveries <= 2 * 5, f"deliveries={f.deliveries}")
    check("the echo of each edge is absorbed by dedup", f.dedup_skips >= 4,
          f"dedup_skips={f.dedup_skips}")


def scenario_gossip_triggered_merge_is_teardown_safe():
    print("gossip+teardown: a notification-triggered merge with a transient "
          "blip does NOT nuke that node's live tunnels")
    cp = ControlPlane(); restore = install_patches(cp)
    try:
        _fresh_three(cp)
        # A propogateNotification arrived and forked runMerge; during that
        # merge a single 8.8.8.8 ping drops. Same merge path as a timer merge,
        # so the TRANSIENT_GUARD_V1 fix must cover it too.
        cp.have_internet = False
        cp.run_merge(trigger="apache:propogateNotification")
        check("gossip-triggered merge kept live tunnels under transient failure",
              set(cp.kernel.ifaces) == {"wg0", "wg1", "wg2"},
              f"kernel={sorted(cp.kernel.ifaces)}")
        check("no fleet-wide teardown cascade seeded (0 NM removals)",
              cp.nm_removed == 0)
    finally:
        restore()


def main():
    print("=== control-plane sim: NM + dnsmasq + Apache callbacks over REAL bringup ===\n")
    FAILS.clear()
    scenario_baseline_quiet()
    scenario_transient_no_internet()
    scenario_transient_broker_unreachable()
    scenario_reproduce_2134()
    scenario_authority_preserved()
    scenario_dnsmasq_callback()
    scenario_gossip_is_neighbor_scoped()
    scenario_gossip_reaches_all_and_terminates()
    scenario_gossip_triggered_merge_is_teardown_safe()
    print()
    if FAILS:
        print(f"CONTROL-PLANE SIM FAILURES: {FAILS}")
        return 1
    print("ALL CONTROL-PLANE SCENARIOS PASS\n"
          "  - transient no_internet / broker_unreachable no longer nuke live tunnels\n"
          "  - pre-fix path reproduces the 21:34 teardown + NM lock_held storm\n"
          "  - positive broker omission still tears down (authority preserved)\n"
          "  - dnsmasq + NetworkManager callbacks drive the real reconcile\n"
          "  - Apache propogateNotification gossip is neighbor-scoped, reaches the\n"
          "    whole mesh, terminates by dedup, and its merges are teardown-safe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
