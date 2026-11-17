#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_carrier_oracle.py -- [CARRIER_PER_HOP_V1] which carrier a hop of the semantic wire runs on.

Holds core/homa_transport.py and its one seam in proxy/transport_semantic._get_worker to the rule in that file's
docstring: detected, announced in the ControlRam, a listener per carrier, the lowest measured round trip among the
carriers both ends have, an unmeasured one first in the walk's order, a session never moved and retired only with
nothing outstanding. A node with no Homa is exactly as it was. The two workers are stood in for (this file is about
the choice, not the carriers; proxy/test_homa_ordering_oracle.py holds the carriers to the bus contract).
"""
import os
import queue
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.pop("FROGNET_SEM_TRANSPORT", None)
os.environ["FROGNET_SEM_TRANSPORT_FILE"] = "/nonexistent/semantic_transport"
from core import homa_transport as HT, homa_liveness as L          # noqa: E402

ok = True


def check(cond, msg):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)


def fresh(word=None):
    HT._chosen = HT._here = None
    HT._rtt.clear(); HT._picked.clear()
    if word is None:
        os.environ.pop("FROGNET_SEM_TRANSPORT", None)
    else:
        os.environ["FROGNET_SEM_TRANSPORT"] = word


def main():
    P = "10.2.2.1"

    class Tcp:                                   # stands in for DaemonServer
        def __init__(self, host, port, resolver): pass

    # a node with no Homa: exactly as it was
    fresh()
    check(HT.semantic_transport() == "auto" and HT.carriers_here() == ("tcp",), f"no word and no Homa socket here: auto, carriers {HT.carriers_here()}")
    check(HT.carrier_for(P) == "tcp" and type(HT.daemon_server("h", 1, None, Tcp)) is Tcp and "proxy.transport_homa" not in sys.modules and "daemon.engine.server_homa" not in sys.modules,
          "sessions open on tcp, the daemon's listener is DaemonServer itself, and none of the Homa driver was loaded")
    fresh("tcp"); probed = []
    real = HT._kernel_homa_opens
    HT._kernel_homa_opens = lambda: (probed.append(1), (True, "x"))[1]
    check(HT.carriers_here() == ("tcp",) and HT.carrier_for(P) == "tcp" and not probed, "the word tcp: tcp, and nothing is even detected")
    fresh("bogus")
    try:
        HT.semantic_transport(); stopped = False
    except SystemExit as e:
        stopped = "bogus" in str(e)
    check(stopped, "any other word stops the process with the word in the message")

    # a node with kernel Homa
    fresh(); HT._kernel_homa_opens = lambda: (True, "an IPPROTO_HOMA socket opened (stood in)")
    theirs = {"v": None}
    HT._peer_carriers = lambda node: theirs["v"]
    HT._tcp_rtt = lambda host: None
    check(HT.carriers_here() == ("homa", "tcp"), "a Homa socket opens here: carriers homa, tcp")
    check(HT.carrier_for(P) == "tcp", "a peer whose tuple has not been read is known to have tcp and nothing more: tcp")
    theirs["v"] = ("tcp",)
    check(HT.carrier_for(P) == "tcp", "a peer that says tcp only: tcp")
    theirs["v"] = ("homa", "tcp")
    check(HT.carrier_for(P) == "homa", "a peer that says homa and tcp, nothing measured: the walk's order, homa")
    HT.note_rtt(P, "homa", 0.0004)
    check(HT.carrier_for(P) == "tcp", "homa measured and tcp not: tcp is taken next, so that it is measured by being used")
    HT._tcp_rtt = lambda host: 0.0009
    check(HT.carrier_for(P) == "homa", "both measured, homa 0.4 ms and tcp 0.9 ms: the lowest measured round trip wins (homa)")
    HT.note_rtt(P, "tcp", 0.0002)
    check(HT.carrier_for(P) == "tcp", "tcp measures 0.2 ms: tcp wins. Nothing is preferred by kind")
    HT.note_rtt(P, "homa", 0.0002)
    check(HT.carrier_for(P) == "homa", "equal measurements keep the walk's order")
    HT.note_rtt(P, "homa", 0.5)
    check(HT._rtt[(P, "homa")] == 0.0002, "a measurement only goes down (the least seen is kept), so the choice settles")
    theirs["v"] = ("homa",)
    check(HT.carrier_for(P) == "homa", "a peer told Homa only: homa")
    theirs["v"] = ("carrier-pigeon",)
    try:
        HT.carrier_for(P); none = False
    except ConnectionError as e:
        none = "no carrier in common" in str(e)
    check(none, "no carrier in common is said, and nothing is opened")
    d = HT.daemon_server("0.0.0.0", 9009, None, Tcp)
    from daemon.engine.server_homa import MsgDaemonServer
    check(type(d.tcp) is Tcp and type(d.homa) is MsgDaemonServer, "the daemon's listener is one per carrier it has: DaemonServer and the Homa server")

    # the capability tuple says the carriers
    from core import frognet_tuples

    class R(L._Ram):
        def __init__(self): self._t = frognet_tuples
    r = R()
    r.ends([{"SensorName": "SD:can.10.2.2.1:daemon", "data": {"inst": "a", "carriers": ["homa", "tcp"]}},
            {"SensorName": "SD:can.10.3.3.1:daemon", "data": {"inst": "b"}}])
    check(r._carriers == {("10.2.2.1", "daemon"): ("homa", "tcp"), ("10.3.3.1", "daemon"): ("homa",)}, f"each end's tuple says what it carries the wire on; one that does not say was told Homa only ({r._carriers})")

    # the seam: a session is never moved, and is retired only with nothing outstanding
    from proxy import transport_semantic as TS, transport_homa as TH
    made = []

    class W:
        def __init__(self, host, port, set_id=0):
            self._pending, self.q, self.stopped = {}, queue.Queue(), False; made.append(self)
        def is_stopped(self): return self.stopped
        def stopped_reason(self): return "stopped"
        def stop(self, err=None): self.stopped = True

    class H(W):
        carrier = "homa"
        def __init__(self, host, port, set_id=0):
            W.__init__(self, host, port, set_id); self._out = set()
    TS._DaemonWorker, TH._MsgWorker = W, H
    fresh(); HT._kernel_homa_opens = lambda: (True, "stood in"); HT._tcp_rtt = lambda host: None
    theirs["v"] = None
    w1 = TS._get_worker(P, 9009)
    check(type(w1) is W, "first contact, the peer's tuple not yet read: the session opens on tcp")
    theirs["v"] = ("homa", "tcp"); w1._pending[7] = object()
    w2 = TS._get_worker(P, 9009)
    check(w2 is w1 and not w1.stopped, "its tuple is read and the choice is now homa, but the session has a request outstanding: it is not moved and not stopped")
    w1._pending.clear()
    w3 = TS._get_worker(P, 9009)
    check(type(w3) is H and w1.stopped and len(made) == 2, "the next request finds it with nothing outstanding: it is retired and the session opens on homa")
    w4 = TS._get_worker(P, 9009)
    check(w4 is w3 and len(made) == 2, "and stays there: one session, no spare")
    HT._kernel_homa_opens = real
    print()
    print("ALL HOMA-CARRIER ORACLE CHECKPOINTS PASS" if ok else "HOMA-CARRIER ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main(); sys.stdout.flush(); os._exit(rc)
