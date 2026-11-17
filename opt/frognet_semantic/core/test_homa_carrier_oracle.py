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
    HT._chosen = None; HT._here.clear(); HT._ram_words.clear()
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

    # [SOFT_HOMA_CARRIER_V1] a node in whose process soft Homa is loaded: the same driver, another carrier's name
    real_soft = HT._soft_homa_loaded
    HT._soft_homa_loaded = lambda: "softhoma-stood-in"
    fresh(); HT._kernel_homa_opens = lambda: (True, "an IPPROTO_HOMA socket opened (stood in)"); HT._tcp_rtt = lambda host: None
    check(HT.homa_name() == "softhoma" and HT.carriers_here() == ("softhoma", "tcp"), f"soft Homa is loaded into this process: carriers {HT.carriers_here()}")
    theirs["v"] = ("homa", "tcp")
    check(HT.carrier_for(P) == "tcp", "a peer with kernel Homa and tcp: soft Homa and kernel Homa are not one wire, tcp is what both have")
    theirs["v"] = ("homa",)
    try:
        HT.carrier_for(P); none = False
    except ConnectionError as e:
        none = "no carrier in common" in str(e) and "softhoma" in str(e)
    check(none, "a peer told kernel Homa only: no carrier in common, said with both ends' carriers")
    theirs["v"] = ("softhoma", "tcp")
    check(HT.carrier_for(P) == "homa", "a peer with soft Homa and tcp, nothing measured: the Homa driver (on soft Homa)")
    HT.note_rtt(P, "softhoma", 0.0003); HT._tcp_rtt = lambda host: 0.0002
    check(HT.carrier_for(P) == "tcp" and (P, "homa") not in HT._rtt, "soft Homa 0.3 ms and tcp 0.2 ms: tcp. Its round trips are kept under its own name, not kernel Homa's")
    HT.note_rtt(P, "softhoma", 0.0001)
    check(HT.carrier_for(P) == "homa", "soft Homa measures 0.1 ms: the Homa driver again")
    fresh("homa")
    try:
        HT.carriers_here(); stopped = False
    except SystemExit as e:
        stopped = "softhoma" in str(e)
    check(stopped, "told homa with soft Homa loaded: the process stops and says the word is softhoma")
    fresh("softhoma")
    check(HT.carriers_here() == ("softhoma",) and HT.carrier_for(P) == "homa" and HT.on_homa_driver(), "told softhoma with soft Homa loaded: Homa only, on soft Homa")
    HT._soft_homa_loaded = lambda: None
    fresh("softhoma")
    try:
        HT.carrier_for(P); stopped = False
    except SystemExit as e:
        stopped = "not told where the soft Homa library is" in str(e)
    check(stopped, "told softhoma without it: the process stops and says so; nothing else is opened")
    # [A_WORD_PER_RAM_CONNECTION_V1] John 2026-11-17: "It is a Setting per ram connection, which may be on different machines"
    SYS, CTL = "databasehost.frognet", "databasehost_control.frognet"
    where = {SYS: "10.2.2.1", CTL: "10.3.3.1"}
    real_at = HT._rams_at
    HT._rams_at = lambda host: tuple(sorted(n for n in HT._ram_words if where.get(n) == host))
    HT._soft_homa_loaded = lambda: "softhoma-test"
    fresh("tcp,%s softhoma" % SYS)
    check(HT.semantic_transport() == "tcp" and HT._ram_words == {SYS: "softhoma"}, "the first line is the node's word; each line after it is a RAM's role name and the word for the connection to it")
    check(HT.carrier_for("10.2.2.1") == "homa" and HT.carrier_for("10.3.3.1") == "tcp" and HT.carrier_for("10.4.4.1") == "tcp",
          "node's word tcp, the SystemRam's softhoma: the session to the SystemRam's machine is on soft Homa, every other machine's on tcp")
    where[SYS] = "10.4.4.1"
    check(HT.carrier_for("10.4.4.1") == "homa" and HT.carrier_for("10.2.2.1") == "tcp", "the role moves: the word follows the RAM to its new machine, and the old machine is the node's word again")
    fresh("softhoma,%s tcp" % CTL)
    check(HT.carrier_for("10.3.3.1") == "tcp" and HT.carrier_for("10.4.4.1") == "homa", "node's word softhoma, the ControlRam's tcp: the ControlRam is reached on tcp and everything else on soft Homa")
    check(HT.carriers_listened() == ("softhoma", "tcp") and type(HT.daemon_server("h", 1, None, Tcp).tcp) is Tcp,
          "and its daemon listens on every carrier its setting names: soft Homa and tcp")
    fresh("tcp,%s softhoma,%s tcp" % (SYS, CTL))
    where[CTL] = "10.4.4.1"
    try:
        HT.carrier_for("10.4.4.1"); refused = False
    except ConnectionError as e:
        refused = SYS in str(e) and CTL in str(e) and "softhoma and tcp" in str(e)
    check(refused, "two named RAMs on one machine with different words: that session is not opened and the error names both; nothing is preferred")
    fresh("tcp,%s softhoma,%s softhoma" % (SYS, CTL))
    check(HT.carrier_for("10.4.4.1") == "homa", "two named RAMs on one machine with the same word: that word")
    for bad, what in (("tcp,%s bogus" % SYS, "a RAM's word that is not a transport"), ("tcp,%s tcp,%s homa" % (SYS, SYS), "a RAM named twice"), ("tcp,%s" % SYS, "a RAM with no word"), ("tcp,%s homasim" % SYS, "a RAM told the harness's word homasim")):
        fresh(bad)
        try:
            HT.semantic_transport(); stopped = False
        except SystemExit as e:
            stopped = "after its first line" in str(e)
        check(stopped, "%s stops the process with the line in the message" % what)
    HT._soft_homa_loaded = lambda: None
    fresh("tcp,%s softhoma" % SYS)
    try:
        HT.semantic_transport(); stopped = False
    except SystemExit as e:
        stopped = "not told where the soft Homa library is" in str(e)
    check(stopped, "a RAM's word this process cannot keep (softhoma, no library) stops it at the start, not at the first session")
    HT._rams_at = real_at
    import tempfile, frognet_held
    hf = tempfile.NamedTemporaryFile("w", suffix=".hosts", delete=False)
    hf.write("127.0.0.1 localhost\n10.2.2.1 a.frognet %s # merge\n10.3.3.1 %s\n" % (SYS, CTL)); hf.close()
    real_hosts = frognet_held.HOSTS; frognet_held.HOSTS = hf.name
    HT._soft_homa_loaded = lambda: "softhoma-test"
    fresh("tcp,%s softhoma" % SYS)
    HT.semantic_transport()
    check(HT._rams_at("10.2.2.1") == (SYS,) and HT._rams_at("10.3.3.1") == (), "where a RAM is comes from this node's hosts file, by the role's name")
    open(hf.name, "w").write("10.2.2.1 a.frognet\n10.3.3.1 %s %s\n" % (CTL, SYS))
    check(HT._rams_at("10.2.2.1") == () and HT._rams_at("10.3.3.1") == (SYS,), "and is read each time: the file changes and the answer changes with it")
    open(hf.name, "w").write("10.2.2.1 %s\n10.3.3.1 %s\n" % (SYS, SYS))
    check(HT._rams_at("10.2.2.1") == () and HT._rams_at("10.3.3.1") == (), "a role the file gives two addresses is at neither")
    frognet_held.HOSTS = real_hosts; os.unlink(hf.name)
    HT._soft_homa_loaded = real_soft
    HT._kernel_homa_opens = real
    print()
    print("ALL HOMA-CARRIER ORACLE CHECKPOINTS PASS" if ok else "HOMA-CARRIER ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main(); sys.stdout.flush(); os._exit(rc)
