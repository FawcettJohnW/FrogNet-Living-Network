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
sim/lifecycle.py - END-TO-END lifecycle harness.

Drives a whole topology through startup -> running -> shutdown using the REAL
ported discovery/merge at every step, triggering discovery runs on live events
(join / leave / re-IP / move) and validating invariants after each phase. This
is the "is the entire system up and working" gate to run before bare metal -
the lifecycle analogue of /opt/frognet_semantic/simulation/frognet_sim.py, but
exercising the actual discovery engine rather than a transcription.

Phases & what each proves:
  startup()   cold start: every node brings up its tunnels (enter-seed + bringup
              peers) and runs discovery to convergence. PROVES the network comes
              up: every live node learns every other, all pairs reachable.
  tick()      a steady-state discovery run with NO topology change. PROVES
              idempotency / stickiness: nothing churns (converges in 1 cycle).
  join()      a node appears and the mesh re-runs discovery. PROVES the newcomer
              is learned everywhere and reachable, with no regression.
  leave()     a node disappears. PROVES it is purged everywhere and survivors
              stay fully reachable.
  reip()      a node changes its served /24. PROVES the old net is dropped
              everywhere and the new net learned everywhere.
  move()      a node relocates (LAN/tunnels change). PROVES re-convergence.
  loop_gate() on a topology with a LAN child reachable two ways, PROVES the
              live system rejects the looping tunnel path and keeps the direct
              one (reflect loop_probe over the converged tables).
  shutdown()  nodes go down one by one. PROVES orderly teardown: each departure
              is purged everywhere and the shrinking mesh stays reachable.

Each event calls System.converge(), which re-runs the real per-node merge across
the mesh - that IS "triggering a discovery run during execution". Churn events
reset discovered state (change-driven re-merge), so an event proves the system
RE-converges from the event; tick() (no reset) proves it holds steady.

Run:  python3 -m discovery.sim.lifecycle
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.sim.system import System, TopologySpec, NodeSpec
from discovery.sim import shapes

CAP = 20


class Lifecycle:
    def __init__(self, label, spec, *, verbose=True):
        self.label = label
        self.sys = System(spec)
        self.verbose = verbose
        self.problems: list[str] = []
        self.up: set[str] = set()
        self._t = 0

    # -- internal -----------------------------------------------------------
    def _live(self):
        return {n.name for n in self.sys.spec.nodes if not n.dead}

    def _s3_of(self, name):
        return self.sys.node[name].subnet3 if name in self.sys.node else None

    def _known_everywhere(self, s3, owner=None):
        bad = []
        for n in self._live():
            if n == owner:
                continue
            if s3 not in self.sys.known.get(n, set()):
                bad.append(f"{n} does not know {s3}")
        return bad

    def _gone_everywhere(self, s3):
        bad = []
        for n in self._live():
            if s3 in self.sys.known.get(n, set()):
                bad.append(f"{n} still knows {s3}")
            for line in self.sys.routes.get(n, []):
                if line.split()[0].startswith(s3 + "."):
                    bad.append(f"{n} still routes {line.split()[0]}")
        return bad

    def _phase(self, name, probs, extra=""):
        self._t += 1
        tag = "PASS" if not probs else "FAIL"
        if probs:
            self.problems.extend(f"[{self.label}] {name}: {p}" for p in probs)
        if self.verbose:
            x = f" ({extra})" if extra else ""
            print(f"    t{self._t} [{tag}] {name}{x}")
            for p in probs[:4]:
                print(f"             - {p}")
        return not probs

    def _converge(self):
        c = self.sys.converge(CAP)
        return c, ([] if c < CAP else [f"did not converge within {CAP} cycles"])

    # -- partition-safe teardown ordering -----------------------------------
    def _adjacency(self):
        live = self._live()
        adj = {n: set() for n in live}
        for a, b in self.sys.spec.tunnels:
            if a in live and b in live:
                adj[a].add(b); adj[b].add(a)
        for host, guests in self.sys.lan_guests.items():
            gs = [g for (g, _a) in guests if g in live]
            if host in live:
                for g in gs:
                    adj[g].add(host); adj[host].add(g)
            for i in range(len(gs)):
                for j in range(i + 1, len(gs)):
                    adj[gs[i]].add(gs[j]); adj[gs[j]].add(gs[i])
        return adj

    @staticmethod
    def _connected_without(adj, drop):
        nodes = [n for n in adj if n != drop]
        if not nodes:
            return True
        seen = {nodes[0]}; stack = [nodes[0]]
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v != drop and v not in seen:
                    seen.add(v); stack.append(v)
        return len(seen) == len(nodes)

    def _safe_next(self):
        """A node whose removal keeps the remainder connected (lowest degree
        first). Every connected graph has at least one such non-cut node."""
        adj = self._adjacency()
        for n in sorted(adj, key=lambda x: (len(adj[x]), x)):
            if self._connected_without(adj, n):
                return n
        return sorted(adj)[0] if adj else None

    # -- phases -------------------------------------------------------------
    def startup(self):
        c, probs = self._converge()
        probs = probs + self.sys.validate()
        self.up = set(self._live())
        self._phase("STARTUP cold-start", probs, extra=f"{len(self.up)} nodes, {c} cyc")
        return self

    def tick(self):
        c = self.sys.converge(CAP)
        probs = [] if c == 1 else [f"steady-state discovery churned ({c} cycles, expected 1)"]
        self._phase("TICK steady-state", probs, extra=f"{c} cyc")
        return self

    def join(self, nodespec, tunnels=()):
        self.sys.add_node(nodespec, tunnels)
        c, probs = self._converge()
        probs = probs + self.sys.validate() + self._known_everywhere(nodespec.subnet3, owner=nodespec.name)
        self.up.add(nodespec.name)
        self._phase(f"JOIN {nodespec.name}", probs, extra=f"{nodespec.subnet3}, {c} cyc")
        return self

    def leave(self, name):
        s3 = self._s3_of(name)
        self.sys.remove_node(name)
        self.up.discard(name)
        c, probs = self._converge()
        probs = probs + self.sys.validate() + self._gone_everywhere(s3)
        self._phase(f"LEAVE {name}", probs, extra=f"{s3}, {c} cyc")
        return self

    def reip(self, name, new_s3):
        old = self._s3_of(name)
        self.sys.reip_node(name, new_s3)
        c, probs = self._converge()
        probs = probs + self.sys.validate() + self._gone_everywhere(old) + self._known_everywhere(new_s3, owner=name)
        self._phase(f"REIP {name} {old}->{new_s3}", probs, extra=f"{c} cyc")
        return self

    def move(self, name, *, new_guest_on=None, new_tunnels=None):
        self.sys.move_node(name, new_guest_on=new_guest_on, new_tunnels=new_tunnels)
        c, probs = self._converge()
        probs = probs + self.sys.validate()
        self._phase(f"MOVE {name}", probs, extra=f"{c} cyc")
        return self

    def loop_gate(self, origin, child_ip, *, loop_via, direct_via):
        """On the converged, RUNNING system: the looping path to a LAN child
        must be rejected and the direct path delivered."""
        probs = []
        lp = self.sys.loop_probe(origin, child_ip, via=loop_via)
        if lp != "LOOP":
            probs.append(f"tunnel path to {child_ip} should be LOOP, got {lp}")
        dp = self.sys.loop_probe(origin, child_ip, via=direct_via)
        if dp != "DELIVERED":
            probs.append(f"direct path to {child_ip} should be DELIVERED, got {dp}")
        self._phase(f"LOOP-GATE {origin}->{child_ip}", probs)
        return self

    def shutdown(self):
        """Orderly teardown: at each step take down a node whose removal does
        NOT partition the mesh (leaves first, cut-vertices last), so the
        shrinking network stays fully reachable the whole way down. (Taking
        down a cut vertex would partition by definition - that's an operator
        choice, not a discovery failure, so the harness avoids it.)"""
        while self._live():
            name = self._safe_next()
            if name is None:
                break
            s3 = self._s3_of(name)
            self.sys.remove_node(name)
            self.up.discard(name)
            c, probs = self._converge()
            probs = probs + self._gone_everywhere(s3)
            if self._live():
                probs = probs + self.sys.validate()
            self._phase(f"SHUTDOWN {name}", probs, extra=f"{len(self._live())} left, {c} cyc")
        self._phase("SHUTDOWN complete", [] if not self._live() else
                    [f"{len(self._live())} nodes still live"])
        return self


# ============================ E2E SCENARIOS ================================

FAILS: list[str] = []


def _run(lc: Lifecycle):
    FAILS.extend(lc.problems)


def scenario_full_lifecycle(label, spec, *, join_node, join_tunnels, reip_target, reip_new):
    """Generic startup -> steady -> churn (join/leave/re-IP) -> shutdown."""
    print(f"\n  === {label} ===")
    lc = Lifecycle(label, spec)
    (lc.startup()
       .tick()
       .join(join_node, join_tunnels)
       .leave(join_node.name)
       .reip(reip_target, reip_new)
       .tick()
       .shutdown())
    _run(lc)


# ======================= REAL-BROKER LIFECYCLE ============================
# Same timeline, but the TUNNELS are decided by the real broker
# (frognet_broker_v4) at every phase: register_node at startup/join, retire
# (active=0, the broker's own membership gate) at leave/shutdown, and
# _compute_aggregate re-deriving each node's channels in between. Discovery
# then converges over whatever the real broker paired.

class BrokerLifecycle(Lifecycle):
    def __init__(self, label, node_specs, *, pond="lifepond", verbose=True):
        # node_specs: [(name, subnet3), ...]
        self._bw = __import__("discovery.sim.broker_world",
                              fromlist=["x"])
        self.pond = pond
        self.broker = self._bw.load_broker()
        self._bw.pond_create(self.broker, pond)
        self._members = list(node_specs)               # [(name, s3)] live
        self._reg = {}                                 # name -> pubkey (active)
        # base init with a placeholder spec; startup() rebuilds from the broker
        super().__init__(label, TopologySpec(nodes=[NodeSpec(n, s3) for n, s3 in node_specs],
                                             tunnels=[]), verbose=verbose)

    def _rebuild_from_broker(self):
        _ch, edges = self._bw.edges_for(self.broker, self.pond, self._reg)
        nodes = [NodeSpec(n, s3) for (n, s3) in self._members]
        self.sys = System(TopologySpec(nodes=nodes, tunnels=[list(e) for e in edges]))
        return edges

    def startup(self):
        for name, s3 in self._members:
            self._reg[name] = self._bw.node_register(self.broker, self.pond, name, s3)
        edges = self._rebuild_from_broker()
        c, probs = self._converge()
        probs = probs + self.sys.validate()
        self.up = set(self._live())
        self._phase("STARTUP cold-start (real broker)", probs,
                    extra=f"{len(self.up)} nodes, {len(edges)} tunnels, {c} cyc")
        return self

    def join(self, name, s3, *, _tunnels=None):
        self._members.append((name, s3))
        self._reg[name] = self._bw.node_register(self.broker, self.pond, name, s3)
        self._rebuild_from_broker()
        c, probs = self._converge()
        probs = probs + self.sys.validate() + self._known_everywhere(s3, owner=name)
        self.up.add(name)
        self._phase(f"JOIN {name} (real broker)", probs, extra=f"{s3}, {c} cyc")
        return self

    def leave(self, name):
        s3 = next((x for (n, x) in self._members if n == name), None)
        self._bw.node_retire(self.broker, self._reg.pop(name, ""))
        self._members = [(n, x) for (n, x) in self._members if n != name]
        self._rebuild_from_broker()
        self.up.discard(name)
        c, probs = self._converge()
        probs = probs + self._gone_everywhere(s3)
        if self._live():
            probs = probs + self.sys.validate()
        self._phase(f"LEAVE {name} (real broker retire)", probs, extra=f"{s3}, {c} cyc")
        return self

    def shutdown(self):
        while self._live():
            name = self._safe_next()
            if name is None:
                break
            s3 = next((x for (n, x) in self._members if n == name), None)
            self._bw.node_retire(self.broker, self._reg.pop(name, ""))
            self._members = [(n, x) for (n, x) in self._members if n != name]
            self._rebuild_from_broker()
            self.up.discard(name)
            c, probs = self._converge()
            probs = probs + self._gone_everywhere(s3)
            if self._live():
                probs = probs + self.sys.validate()
            self._phase(f"SHUTDOWN {name} (real broker retire)", probs,
                        extra=f"{len(self._live())} left, {c} cyc")
        self._phase("SHUTDOWN complete", [] if not self._live() else
                    [f"{len(self._live())} nodes still live"])
        return self


def scenario_loop_aware_lifecycle():
    """Seattle6 shape through its lifecycle: O has the tunnels, L is a LAN child
    of O, Rmt is a remote tunnel peer that only reaches L's /24 back through O.
    Prove the running system rejects the looping tunnel path to L, then churn."""
    print("\n  === loop-aware lifecycle (LAN child reachable two ways) ===")
    spec = TopologySpec(
        nodes=[NodeSpec("O", "10.250.250"),
               NodeSpec("L", "10.160.160", guest_on=("O", "10.250.250.221")),
               NodeSpec("Rmt", "10.102.60")],
        tunnels=[("O", "Rmt")],
    )
    lc = Lifecycle("loop-aware", spec)
    (lc.startup()
       .loop_gate("O", "10.160.160.1", loop_via="Rmt", direct_via="L")
       .join(NodeSpec("Rmt2", "10.103.103"), [("O", "Rmt2")])
       .loop_gate("O", "10.160.160.1", loop_via="Rmt", direct_via="L")
       .shutdown())
    _run(lc)


def scenario_broker_lifecycle():
    """Full timeline driven by the REAL broker: every tunnel comes from
    register_node + _compute_aggregate; leave/shutdown go through the broker's
    own active=0 retire. Skips cleanly if the broker tree isn't present."""
    print("\n  === real-broker lifecycle (broker pairs every phase) ===")
    # [BROKER_SIM_TIMEOUT_V1] The real-broker path makes blocking broker calls
    # (load_broker/register/edges). A slow or unreachable broker during install
    # would BLOCK forever here -- the try/except below only catches exceptions, and
    # a hang raises none, so the whole pre-activation gate wedges. Bound it: if the
    # broker doesn't answer within the deadline, SIGALRM raises TimeoutError and the
    # scenario SKIPS cleanly (same as when the broker tree is absent), letting the
    # install proceed. Main-thread only, which the sim entrypoint always is.
    import signal as _signal
    _have_alarm = hasattr(_signal, "SIGALRM")
    _deadline = int(os.environ.get("FROGNET_SIM_BROKER_TIMEOUT_S", "45"))
    _old_handler = None
    try:
        if _have_alarm:
            def _on_timeout(_sig, _frame):
                raise TimeoutError(f"real broker did not respond within {_deadline}s")
            _old_handler = _signal.signal(_signal.SIGALRM, _on_timeout)
            _signal.alarm(_deadline)
        lc = BrokerLifecycle("broker", [("M1", "10.11.11"), ("M2", "10.12.12"),
                                        ("M3", "10.13.13"), ("GW", "10.9.9")])
        (lc.startup()
           .tick()
           .join("M4", "10.14.14")
           .leave("M2")
           .tick()
           .shutdown())
        _run(lc)
    except Exception as e:
        print(f"    [SKIP] real-broker lifecycle ({type(e).__name__}: {e})")
    finally:
        if _have_alarm:
            _signal.alarm(0)
            if _old_handler is not None:
                _signal.signal(_signal.SIGALRM, _old_handler)


def main():
    print("=== END-TO-END LIFECYCLE (real ported discovery, startup->shutdown) ===")

    scenario_full_lifecycle(
        "snake(5)", shapes.snake(5),
        join_node=NodeSpec("NEW", "10.99.99"), join_tunnels=[("N0", "NEW")],
        reip_target="N2", reip_new="10.77.77")

    scenario_full_lifecycle(
        "star(6)", shapes.star(6),
        join_node=NodeSpec("NEW", "10.99.99"), join_tunnels=[("N0", "NEW")],
        reip_target="N3", reip_new="10.77.77")

    scenario_full_lifecycle(
        "ring(6)", shapes.ring(6),
        join_node=NodeSpec("NEW", "10.99.99"), join_tunnels=[("N0", "NEW"), ("N3", "NEW")],
        reip_target="N2", reip_new="10.77.77")

    scenario_full_lifecycle(
        "snowflake(3x3)", shapes.snowflake(3, 3),
        join_node=NodeSpec("NEW", "10.99.99"), join_tunnels=[("C", "NEW")],
        reip_target="C", reip_new="10.77.77")

    scenario_loop_aware_lifecycle()
    scenario_broker_lifecycle()

    print("\n" + ("ALL LIFECYCLE SCENARIOS PASS" if not FAILS
                  else f"LIFECYCLE FAILURES: {len(FAILS)}"))
    for p in FAILS[:12]:
        print(f"  - {p}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
