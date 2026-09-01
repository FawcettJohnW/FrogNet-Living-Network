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
test_propagate_reach_oracle.py - [PROPAGATE_REACH_V1]

The thing that was never tested. sim/system.converge() re-merges EVERY node
every cycle, so it proves discovery DATA convergence but never the
propogateNotification TRIGGER graph - i.e. whether the node that actually needs
to re-run gets told to. On the box a node only re-runs when triggered (boot
timer, runAgain self-loop, or a received propogateNotification); the deepest LAN
leaf (Seattle2, 3 hops from the gateway) was finishing once and never running
again.

This oracle reproduces the trigger graph faithfully:
  - real converged route tables from the Seattle chain (sim/lan_chain)
  - each node's notify targets from the REAL discovery.neighbors.direct_neighbor_targets
  - the receiver's UNCONDITIONAL re-propagation (matches propogateNotificationInternal:
    on receive it forks runMerge AND forwards the same event to its own neighbors,
    regardless of whether its local merge changed anything), dedup by event id.

Proves:
  A. neighbor edges form the connected chain gw->...->leaf (Seattle3 DOES target
     Seattle2 at its on-segment client addr, not its .1).
  B. an epidemic from the gateway reaches EVERY live LAN-chain node, incl. the
     deepest leaf.
  C. FRAGILITY: if ONE intermediate receiver-hop fails to re-propagate (the
     backend propogateNotification.php -> propogateNotificationInternal not
     firing on that hop), everything below it is orphaned. This pins the
     invariant that reach depends on every hop forwarding - and is exactly the
     production failure mode for Seattle2.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.lan_chain import build_seattle_chain
from discovery.sim.system import System
from discovery.neighbors import direct_neighbor_targets

CHAIN = ["Seattle5", "Seattle6", "Seattle3", "Seattle2"]   # gw -> deepest leaf

ok = True
def check(m, c, detail=""):
    global ok; ok = ok and bool(c)
    print(f"  [{'PASS' if c else 'FAIL'}] {m}" + (f"  ({detail})" if detail and not c else ""))


def _model():
    """Converge the real chain, then derive the notify graph from real tables."""
    sysm = System(build_seattle_chain())
    sysm.converge()

    def node_ips(n):
        ips = {n.one, f"{n.subnet3}.2"}
        if n.guest_on:
            ips.add(n.guest_on[1])               # on-segment client addr (e.g. .47)
        return ips

    def chans(name):
        return [f"{sysm.node[p].name}-{sysm.node[p].subnet3}"
                for p in sysm.tunnels.get(name, [])]

    def owner_of(ip):
        for n in sysm.node.values():
            if ip in node_ips(n):
                return n.name
        return None

    targets = {}
    for name in sysm.node:
        rt = "\n".join(sysm.routes[name])
        targets[name] = direct_neighbor_targets(rt, chans(name), node_ips(sysm.node[name]))
    return sysm, targets, owner_of


def _epidemic(targets, owner_of, origin, *, no_forward=frozenset()):
    """Receiver re-propagates unconditionally (propogateNotificationInternal),
    dedup by seen-set. no_forward = hops that receive but FAIL to re-propagate
    (models a broken receiver handler on those nodes)."""
    merged = {origin}
    def deliver(ip):
        who = owner_of(ip)
        if who is None or who in merged:
            return
        merged.add(who)
        if who in no_forward:
            return                                # received, but handler didn't forward
        for t in targets[who]:
            deliver(t)
    for t in targets[origin]:
        deliver(t)
    return merged


def main():
    sysm, targets, owner_of = _model()
    live = [n for n in CHAIN if not sysm.node[n].dead]

    # A. chain edge: Seattle3 must target Seattle2 at its on-segment addr
    s3_targets_nodes = {owner_of(t) for t in targets["Seattle3"]}
    check("Seattle3 notify set includes Seattle2 (its on-segment client addr)",
          "Seattle2" in s3_targets_nodes,
          f"Seattle3 targets={targets['Seattle3']} -> {sorted(s3_targets_nodes)}")

    # B. epidemic from the gateway reaches every live LAN-chain node
    reached = _epidemic(targets, owner_of, "Seattle5")
    for n in live:
        check(f"epidemic reaches {n}", n in reached)
    check("deepest leaf Seattle2 re-runs on a gateway-originated change",
          "Seattle2" in reached)

    # C. fragility: one broken receiver-hop orphans everything below it
    orphaned = _epidemic(targets, owner_of, "Seattle5", no_forward={"Seattle3"})
    check("if Seattle3's receiver-hop does not re-propagate, Seattle2 is orphaned "
          "(reach depends on every hop forwarding)",
          "Seattle2" not in orphaned and "Seattle3" in orphaned)

    print("\nALL PROPAGATE-REACH CHECKPOINTS PASS" if ok
          else "\nPROPAGATE-REACH ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
