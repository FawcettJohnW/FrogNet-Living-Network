#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_liveness_oracle.py -- [LIVENESS_IS_A_HELD_READ_V1] a peer's liveness is a held read on its capability tuple.

core/homa_liveness.PeerLiveness against a stand-in for the SystemRam's store and the hosts block (put, hold and the
block; the rule that reads the rows is the real one) with real held-read behaviour: a hold returns when the family is
not what the holder last saw, or on the hosts event. With the Homa simulator underneath (per-RPC timeout REMOVED,
waits held): requests wait on a peer for many times timeout_ticks and none is abandoned; the peer's machine leaves the
hosts block and every one of them ends at once; an end stops, starts, is replaced; the RAM is rebuilt at a network
event and each living end writes its own tuple again while the departed one is simply absent.
The same rule on the real memory (api.php, core/frognet_held, MariaDB): core/test_homa_liveness_ram_oracle.py.
"""
import errno
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.update(FROGNET_HOMASIM_RPC_TIMEOUT="0", FROGNET_HOMASIM_HELD_WAITS="1", FROGNET_HOMASIM_ADAPTIVE="1")
from core import homa_liveness as L, homa_sim as HS, homa_socket as H   # noqa: E402

ok = True


def check(cond, msg):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)


class Memory:
    """The (homa, can) family of one SystemRam and the network's hosts block, with real held-read behaviour: a hold
    returns when the family is not what the holder last saw, or when the block is rewritten (the hosts event)."""
    def __init__(self, block):
        self.rows, self.cv, self.puts, self.block, self.moves = {}, threading.Condition(), [], set(block), 0

    def sig(self):
        return json.dumps(sorted(self.rows.items()))

    def view(self, ip):
        mem = self
        from core import frognet_tuples

        class View(L._Ram):                     # ends() and the name rule are the real ones; the store is this file's
            def __init__(self):
                self._t, self.moves, self.watched = frognet_tuples, mem.moves, mem.moves
            def my_ip(self): return ip
            def put(self, scope, value):
                with mem.cv:
                    mem.rows[self._t.var_name(L.VAR, scope)] = json.dumps(value, sort_keys=True); mem.puts.append(scope); mem.cv.notify_all()
                return True
            def hold(self, seen):
                with mem.cv:
                    mem.cv.wait_for(lambda: mem.sig() != seen or mem.moves != self.moves, timeout=30)
                    self.moves = mem.moves
                    return [{"SensorName": k, "data": json.loads(v)} for k, v in mem.rows.items()], mem.sig()
            def live_block(self): return set(mem.block)
            def wait_network_event(self):                      # the file watcher: returns when the block is rewritten
                with mem.cv:
                    mem.cv.wait_for(lambda: mem.moves != self.watched)
                    self.watched = mem.moves
        return View()

    def remove(self, scope):                  # the tuple is taken out of the memory
        with self.cv:
            self.rows.pop("SD:can." + scope, None); self.cv.notify_all()

    def merge(self, block, rebuilt=False):    # the network event: the hosts block is rewritten; a new RAM starts empty
        with self.cv:
            self.block = set(block); self.moves += 1
            if rebuilt:
                self.rows.clear()
            self.cv.notify_all()


def main():
    A, B, C = "10.1.1.1", "10.2.2.1", "10.3.3.1"
    mem = Memory([A, B, C])
    srv = HS.HomaSimSocket("127.0.0.1", 19160)                      # node B's daemon: takes requests, never answers
    cli = HS.HomaSimSocket("127.0.0.1")                             # node A's proxy
    gone_at_a, events_at_a, gone_at_b = [], [], []

    def a_gone(ip, why, left):
        gone_at_a.append((ip, why))
        if ip == B:
            cli.peer_gone(("127.0.0.1", 19160))

    def start(ip, role, port, on_gone, on_ev=None):
        L._ram = lambda dbhost="x": mem.view(ip)
        return L.PeerLiveness(role, port, on_gone, "node %s %s" % (ip, role), on_network_event=on_ev)
    la = start(A, "proxy", 0, a_gone, lambda: events_at_a.append(1))
    lb = start(B, "daemon", 19160, lambda ip, why, left: gone_at_b.append((ip, why)))
    lc = start(C, "daemon", 19161, lambda ip, why, left: None)
    la.watch(B); la.watch(C)
    time.sleep(0.3)
    check(set(la.seen) == {B, C} and set(lb.seen) == {A} and set(lc.seen) == {A}, f"each end wrote its own tuple; the proxy reads both daemons, each daemon reads the proxy ({sorted(la.seen)}, {sorted(lb.seen)})")
    check(sorted(mem.puts) == [A + ":proxy", B + ":daemon", C + ":daemon"], f"one tuple per end, each written once, by its owner ({sorted(mem.puts)})")

    for i in range(20):
        cli.send_request(("127.0.0.1", 19160), b"hold this %d" % i, i)
    tick, tt = cli.p["tick_ms"] / 1000.0, cli.p["timeout_ticks"]
    time.sleep(3 * tt * tick)
    k = cli.counters()
    check(cli._inbox.empty() and k["timeouts"] == 0 and not gone_at_a, f"20 requests waited 3 x timeout_ticks on a peer that is there: none abandoned, nothing asked of it ({k['tx_resend']} RESENDs, {srv.counters()['tx_busy']} BUSYs)")

    # B's machine goes down. Its process writes nothing more; its tuple STAYS in the memory (nothing on Homa removes it).
    lb._stop.set()
    time.sleep(0.3)
    check(not gone_at_a and cli._inbox.empty(), "B's machine went down: until the network says so nothing is ended and nothing is guessed")
    t0 = time.time(); mem.merge([A, C]); errs = []
    for _ in range(20):
        try:
            cli.recv()
        except H.RpcFailed as e:
            errs.append(e.errno)
    dt = time.time() - t0
    check(errs == [errno.EHOSTUNREACH] * 20 and dt < 0.5 and [g[0] for g in gone_at_a] == [B], f"the merge rewrote the hosts block without B: all 20 ended in {dt * 1000:.0f} ms from ONE event at A ({gone_at_a})")
    check(set(la.seen) == {C} and la.n_gone == 1, "and C, still in the block, was not touched")

    # C's daemon stops cleanly, starts again; then is replaced without a stop (killed and started: a new inst)
    lc.withdraw(); time.sleep(0.3)
    check([g[0] for g in gone_at_a] == [B, C] and "no longer in the memory" in gone_at_a[-1][1], f"C's daemon stopped: it said so in the memory and A knows from that write ({gone_at_a[-1][1]})")
    mem.remove(C + ":daemon")
    lc = start(C, "daemon", 19161, lambda ip, why, left: None); time.sleep(0.3)
    check(set(la.seen) == {C} and len(gone_at_a) == 2, f"C's daemon started again: A reads it there, nobody called gone ({sorted(la.seen)}, {len(gone_at_a)})")
    lc._stop.set(); lc2 = start(C, "daemon", 19161, lambda ip, why, left: None); time.sleep(0.3)
    check(len(gone_at_a) == 3 and "another started" in gone_at_a[-1][1] and la.seen.get(C) == lc2.inst, f"C's daemon was replaced without stopping: A calls the old one gone once, by its inst, and reads the new one ({gone_at_a[-1][1]})")

    # the RAM is rebuilt at a network event
    n0 = len(mem.puts); mem.merge([A, C], rebuilt=True); time.sleep(0.5)
    again = sorted(mem.puts[n0:])
    check(again == [A + ":proxy", C + ":daemon"] and len(mem.rows) == 2, f"the RAM was rebuilt at a network event: each living end wrote its own truth again ({again}); the departed one is absent, not stale")
    check(len(gone_at_a) == 3 and events_at_a == [1], f"A was told of ONE network event and called no living peer gone on it (departures {len(gone_at_a)}, events {len(events_at_a)})")
    time.sleep(0.3)
    check(set(la.seen) == {C}, f"and it learned who is there again from the memory ({sorted(la.seen)})")

    # a second live process writing this end's tuple: this process does not fight it
    n0 = len(mem.puts)
    with mem.cv:
        v = json.loads(mem.rows["SD:can." + A + ":proxy"]); v["inst"] = "someoneelse"
    for _ in range(3):
        with mem.cv:
            mem.rows["SD:can." + A + ":proxy"] = json.dumps(v, sort_keys=True); mem.cv.notify_all()
        time.sleep(0.2)
    check(mem.puts[n0:].count(A + ":proxy") == 1, f"another running proxy writes A's tuple three times: this one wrote over it once and then says so, not a write for a write ({mem.puts[n0:].count(A + ':proxy')})")
    srv.close(); cli.close()
    print()
    print("ALL HOMA-LIVENESS ORACLE CHECKPOINTS PASS" if ok else "HOMA-LIVENESS ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main(); sys.stdout.flush(); os._exit(rc)
