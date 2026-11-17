#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_ordering_oracle.py -- [HOMA_TRANSPORT_V1] [DRIVER_KEEPS_WIRE_ORDER_V1]

The gate for the FNW1 semantic wire's Homa driver (proxy/transport_homa.py, daemon/engine/server_homa.py): it must
give the semantic engine the bus the engine was written against, while Homa does as it likes with the wire. Homa has
no order between messages and sends short ones ahead of long ones on purpose; the engine moves its references in wire
order ([REFERENCES_MOVE_IN_WIRE_ORDER_V1]) and detects nothing when that order is broken.

This runs the fan-out oracle's own workload (proxy/test_fanout_wire_order_oracle.py: 8 readers on two instances,
4 writers, every answer checked against the origin's rule) through the REAL engine on both ends -- the real
proxy.transport_semantic over _MsgWorker, MsgDaemonServer over the real SemanticSession, the real template store on
MariaDB -- with ONE substitution: the socket. In place of the Homa socket stands one with the same calls that
delivers every request and every reply late by a random time plus a time per byte, so messages overtake each other
and short ones overtake long ones.

Three runs, each its own process (the engine's references are process-wide):

  free      both drivers hand frames up as they arrive. MUST FAIL: wrong rows, wrong counts or decode errors, with
            status 200. This is what the engine does on a wire nobody keeps in order.
  ordered   the driver as shipped. MUST PASS every checkpoint of the fan-out oracle while messages overtake each
            other; the daemon's engine is handed every session's frames in exactly the order the proxy's engine
            submitted them; frames of one instance are in flight together (nothing is held back from the wire);
            Then contract 5 by liveness ([LIVENESS_IS_A_HELD_READ_V1]), with a carrier that never reports a failure:
            the daemon's driver is told the proxy end stopped (its sessions end, what they were owed is let go, the
            callers are told, [WRITER_GONE_V1] is NOT told, and the next request opens a new session); then the
            proxy's driver is told the daemon's machine left the network with requests outstanding (every one fails
            at once with that reason and is aborted in Homa); and the daemon's driver is told a machine left
            ([WRITER_GONE_V1] is told, once, as the TCP driver tells it).
  gone      a peer that was never there, on a carrier that reports it (Homa as shipped): the caller gets its error
            once, and nothing is tried in Homa's place.
  sim       the driver as shipped, with NO substitution in this file: the socket is core/homa_sim.py, the Homa
            simulator (Homa's protocol over real UDP on this machine: grants, SRPT, RESEND/BUSY, timeouts), with 2% of
            packets dropped each way. Same checks as ordered, and contract 5 both ways: by liveness, and by Homa's
            own timeout when the peer goes silent. This is the configuration the simulator tiers run.
  simheld   the same on the simulator with Homa's per-RPC timeout REMOVED, waits held and timers measured
            ([NO_RPC_TIMEOUT_V1] [HELD_WAITS_V1] [TIMERS_FROM_THE_MEASURED_PATH_V1]). Requests outstanding to a
            machine that has died wait three times timeout_ticks and none is abandoned; liveness says it left and
            every one ends at once.

In every mode core/homa_liveness.PeerLiveness is replaced, at each end, by an object the oracle uses to say who is
gone; the rule that decides it is held to the real memory by core/test_homa_liveness_ram_oracle.py.

What this does not prove: anything about the Homa kernel module. The stand-in keeps Homa's contract as its man pages
state it; core/test_homa_layout_oracle.py checks the system-call layouts; the first real sendmsg is on a node.
"""
import errno
import heapq
import itertools
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
SIM = MODE in ("sim", "simheld")
os.environ["FROGNET_LOCAL_GW"] = "10.88.88.1"     # the .1 each end names itself by in its HELLO (one process, both ends)
os.environ["FROGNET_SEM_TRANSPORT"] = "homasim" if SIM else "homa"
if SIM:
    os.environ.setdefault("FROGNET_HOMASIM_LOSS", "0.02")
if MODE == "simheld":
    os.environ.update(FROGNET_HOMASIM_RPC_TIMEOUT="0", FROGNET_HOMASIM_HELD_WAITS="1", FROGNET_HOMASIM_ADAPTIVE="1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


# ------------------------------------------------------------------------------------------- the reordering carrier
class Hub:
    """Every endpoint of one run, and the one thread that delivers. A message is delivered after uniform(0, jitter_s)
    + bytes * per_byte_s: late by a random time, later the longer it is."""

    def __init__(self, seed=20261116, jitter_s=0.004, per_byte_s=2e-6, dead_after_s=0.3):
        self.rng = random.Random(seed)
        self.jitter_s, self.per_byte_s, self.dead_after_s = jitter_s, per_byte_s, dead_after_s
        self.ends = {}
        self.heap, self.n = [], itertools.count()
        self.cv = threading.Condition()
        self.posted = itertools.count()
        self.overtakes = 0
        self.last_delivered = {}                         # (src, dst) -> highest post number delivered
        self.msgs = self.bytes = self.not_fnw1 = 0
        self.to_dead = 0
        self.aborts, self.peer_gone_calls = 0, []
        threading.Thread(target=self._run, daemon=True, name="hub").start()

    def post(self, src, dst, payload, deliver):
        if payload[:4] != b"FNH1" or b"FNW1" not in payload[12:24]:
            self.not_fnw1 += 1
        with self.cv:
            due = time.monotonic() + self.rng.uniform(0, self.jitter_s) + len(payload) * self.per_byte_s
            heapq.heappush(self.heap, (due, next(self.n), next(self.posted), src, dst, len(payload), deliver))
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while not self.heap or self.heap[0][0] > time.monotonic():
                    self.cv.wait(None if not self.heap else max(0.0, self.heap[0][0] - time.monotonic()))
                _due, _n, pn, src, dst, nbytes, deliver = heapq.heappop(self.heap)
                k = (src, dst)
                if pn < self.last_delivered.get(k, -1):
                    self.overtakes += 1                  # something posted after this one was delivered before it
                else:
                    self.last_delivered[k] = pn
                self.msgs += 1; self.bytes += nbytes
            deliver()


class Endpoint:
    """The carrier contract of core/homa_socket.HomaSocket, kept by hand: ids even at the client and odd at the
    server, a response returns the cookie of its request, a request to nobody fails with ETIMEDOUT."""

    def __init__(self, hub, addr):
        from core import homa_socket as H
        self.H, self.hub, self.addr = H, hub, addr
        self.inbox = queue.Queue()
        self.ids = itertools.count(2, 2)
        self.cookies = {}
        self.lock = threading.Lock()
        hub.ends[addr] = self

    def send_request(self, dest, payload, cookie):
        if not 0 < len(payload) <= self.H.HOMA_MAX_MESSAGE_LENGTH:
            raise ValueError("homa: a message of %d bytes cannot be sent" % len(payload))
        rid = next(self.ids)
        with self.lock:
            self.cookies[rid] = cookie
        far = self.hub.ends.get(tuple(dest))
        if far is None:
            self.hub.to_dead += 1
            threading.Timer(self.hub.dead_after_s, lambda: self._fail(rid, errno.ETIMEDOUT)).start()
        else:
            self.hub.post(self.addr, tuple(dest), payload,
                          lambda: far.inbox.put(self.H.Msg(rid | 1, 0, self.addr, payload, True)))
        return rid

    def _fail(self, rid, err):
        with self.lock:
            cookie = self.cookies.pop(rid, None)
        if cookie is not None:
            self.inbox.put(self.H.RpcFailed(rid, cookie, err))

    def send_reply(self, dest, rpc_id, payload):
        if not 0 < len(payload) <= self.H.HOMA_MAX_MESSAGE_LENGTH:
            raise ValueError("homa: a message of %d bytes cannot be sent" % len(payload))
        far, rid = self.hub.ends[tuple(dest)], rpc_id & ~1

        def deliver():
            with far.lock:
                cookie = far.cookies.pop(rid, None)
            if cookie is not None:                       # None: its client aborted it
                far.inbox.put(self.H.Msg(rid, cookie, self.addr, payload, False))
        self.hub.post(self.addr, tuple(dest), payload, deliver)

    def abort(self, rpc_id):
        with self.lock:
            if self.cookies.pop(rpc_id, None) is not None:
                self.hub.aborts += 1

    def peer_gone(self, peer):
        self.hub.peer_gone_calls.append((self.addr, tuple(peer)))
        return 0

    def recv(self):
        m = self.inbox.get()
        if isinstance(m, BaseException):
            raise m
        return m

    def close(self):
        self.inbox.put(self.H.CarrierClosed("closed"))


class Liveness:
    """In place of core/homa_liveness.PeerLiveness at one end of the driver: the oracle says who is gone."""

    def __init__(self, on_gone):
        self.on_gone, self.watched = on_gone, []

    def watch(self, ip):
        self.watched.append(ip)

    def gone(self, ip, why, machine_left):
        self.on_gone(ip, why, machine_left)


# ---------------------------------------------------------------------------------------------------- one run
def run(mode):
    import proxy.test_fanout_wire_order_oracle as F          # the workload, the origin stand-in and the checks
    from proxy import transport_semantic as TS
    from proxy import transport_homa as TH
    from daemon.engine import server_homa as SH
    from daemon.engine.resolver import DestinationResolver

    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)

    hub = Hub(seed=int(os.environ.get("HOMA_ORACLE_SEED", "20261116")), jitter_s=float(os.environ.get("HOMA_ORACLE_JITTER_MS", "4")) / 1000.0)
    sim = mode in ("sim", "simheld")
    if not sim:
        proxy_end = Endpoint(hub, ("10.77.77.2", 40001))
        TH._client_carrier = lambda: proxy_end
    lv, told = {}, []
    TH._peer_liveness = lambda on_gone, on_ev: lv.setdefault("proxy", Liveness(on_gone))
    tcp_workers = []
    _orig_init = TS._DaemonWorker.__init__
    TS._DaemonWorker.__init__ = lambda self, *a, **k: (tcp_workers.append(a), _orig_init(self, *a, **k))[1]
    TH._MsgWorker.KEEP_ORDER = SH._MsgSession.KEEP_ORDER = (mode != "free")
    SH._MsgSession.keep_order_record = True

    # watched from outside the driver: frames of one instance in flight at once
    inflight, worst = {}, {"n": 0}
    watch = threading.Lock()
    _send, _hand_up = TH._ClientMux.send, TH._MsgWorker._hand_up

    def send(self, worker, rpc, wire):
        if rpc.gate is not None:
            with watch:
                inflight[rpc.gate] = inflight.get(rpc.gate, 0) + 1
                worst["n"] = max(worst["n"], inflight[rpc.gate])
        return _send(self, worker, rpc, wire)

    def hand_up(self, rpc, frame):
        if rpc.gate is not None:
            with watch:
                inflight[rpc.gate] -= 1
        return _hand_up(self, rpc, frame)
    TH._ClientMux.send, TH._MsgWorker._hand_up = send, hand_up

    if mode == "gone":
        TS.get_template_store().ensure_tables()
        from proxy.cache import semcache_db as _psc
        _psc.ensure_table()
        t0 = time.time()
        st, body = F.call("GET", "/api.php?entity=sensors&action=values&SensorType=A&limit=1&order=SensorID&parse=1")
        dt = time.time() - t0
        check(st == 503 and "timed out" in body.lower(), f"a peer that is not there: the caller is told once, 503 with the carrier's error ({st}, {body[:90]!r})")
        check(dt < 5.0, f"told in {dt:.2f}s, the carrier's own bound (the stand-in's is {hub.dead_after_s}s), not a wait of this file's")
        check(hub.to_dead == 2, f"exactly two messages were sent to it, the session's HELLO and the request: nothing was sent again ({hub.to_dead})")
        check(not tcp_workers, "no TCP worker was made: nothing was tried in Homa's place")
        check(all(w.is_stopped() for w in TS._WORKERS.values()), "the session to it is ended, with the reason: " + "; ".join(w.stopped_reason() for w in TS._WORKERS.values())[:90])
        return ok

    TS.get_template_store().ensure_tables()
    from proxy.cache import semcache_db as _psc
    _psc.ensure_table()
    from daemon.cache import semcache_db as _dsc
    _dsc.ensure_table()
    srv = SH.MsgDaemonServer(F.DAEMON_IP, F.PORT, DestinationResolver())
    hold = threading.Event(); hold.set()

    class Origin(F.Origin):                      # the origin, which the oracle can make slow to answer
        def request(self, *a, **k):
            hold.wait(30)
            return F.Origin.request(self, *a, **k)
    srv._http_client = Origin()
    srv._peer_liveness = lambda: lv.setdefault("daemon", Liveness(srv._peer_gone))
    srv._writer_gone = told.append
    if not sim:
        srv._server_carrier = lambda: Endpoint(hub, (F.DAEMON_IP, F.PORT))
    threading.Thread(target=srv.run, daemon=True, name="daemon-server").start()
    while srv._carrier is None:
        time.sleep(0.01)

    types = {"A": ["a%d.Seattle5.cpu.load" % (7 - i) for i in range(8)], "B": ["b%d.Seattle5.mem.load" % (7 - i) for i in range(8)]}
    with F.ROWS_LOCK:
        i = 0
        for t, names in types.items():
            for n in names:
                i += 1; F.ROWS[n] = F.row(i, n, t, i)
    rpath = lambda t, k, o="SensorID": "/api.php?entity=sensors&action=values&SensorType=%s&limit=%d&order=%s&parse=1" % (t, k, o)
    for t in types:
        for k in (1, 2):
            st, body = F.call("GET", rpath(t, k))
            check(st == 200 and len(json.loads(body)["rows"]) == k, f"warm read {t} limit={k}: {st}")
    wpath = "/api.php?entity=sensor_data&action=upsert_by_name"
    st, body = F.call("POST", wpath, json.dumps({"SensorName": "w0.Seattle5.cpu.load", "SensorType": "A", "jsonData": {"v": 0}}).encode())
    check(st == 200, f"warm write: {st}")

    R, W, N = int(os.environ.get("FANOUT_R", "8")), int(os.environ.get("FANOUT_W", "4")), int(os.environ.get("FANOUT_N", "150"))
    problems, counts, last, lock = [], {"reads": 0, "writes": 0}, {}, threading.Lock()

    def reader(ix):
        t = "A" if ix % 2 == 0 else "B"
        for j in range(N):
            k = 1 + (ix + j) % 6 if j % 3 else 1 + ix % 6
            o = "SensorID" if (ix + j) % 2 else "SensorName"
            st, body = F.call("GET", rpath(t, k, o))
            with lock:
                counts["reads"] += 1
            try:
                rows = json.loads(body)["rows"]
            except Exception:
                problems.append(("read", t, k, st, body[:120])); continue
            ordered = [r.get(o) for r in rows] == sorted(r.get(o) for r in rows)
            if st != 200 or len(rows) != k or any(r.get("SensorType") != t for r in rows) or not ordered:
                problems.append(("read", t, k, o, st, "rows=%d types=%s order_ok=%s" % (len(rows), sorted({r.get("SensorType") for r in rows}), ordered)))

    def writer(ix):
        name = "w%d.Seattle5.cpu.load" % ix
        for j in range(N):
            v = ix * 1000 + j
            st, got = F.call("POST", wpath, json.dumps({"SensorName": name, "SensorType": "A", "jsonData": {"v": v}}).encode())
            with lock:
                counts["writes"] += 1
            try:
                good = st == 200 and json.loads(got).get("ok")
            except Exception:
                good = False
            if not good:
                problems.append(("write", name, v, st, got[:120]))
            last[name] = v

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(R)] + [threading.Thread(target=writer, args=(i,)) for i in range(W)]
    t0 = time.time()
    for th in threads: th.start()
    for th in threads: th.join()
    dt = time.time() - t0
    total = counts["reads"] + counts["writes"]
    ws = [w.stats() for w in TS._WORKERS.values()]
    print(f"    mode={mode}: {counts['reads']} reads and {counts['writes']} writes in {dt:.1f}s ({total / dt:.0f}/s), "
          f"{len(problems)} problems; carrier: {hub.msgs} messages, {hub.bytes} bytes, {hub.overtakes} delivered after a "
          f"later one; driver: {ws}", flush=True)
    for p in problems[:6]:
        print("    ", p, flush=True)
    print("RESULT " + json.dumps({"mode": mode, "ops": total, "seconds": round(dt, 2), "per_s": round(total / dt), "problems": len(problems),
                                  "messages": hub.msgs, "bytes": hub.bytes, "overtakes": hub.overtakes,
                                  "worst_in_flight_per_instance": worst["n"], "driver": ws}), flush=True)
    if sim:
        cc, sc = TH._get_mux().carrier.counters(), srv._carrier.counters()
        print("    homasim proxy socket: %s" % {k: v for k, v in cc.items() if v}, flush=True)
        print("    homasim daemon socket: %s" % {k: v for k, v in sc.items() if v}, flush=True)
        check(cc["dropped_by_loss"] + sc["dropped_by_loss"] > 20 and cc["tx_resend"] + sc["tx_resend"] > 0,
              f"the simulator really lost packets and Homa's recovery ran: {cc['dropped_by_loss'] + sc['dropped_by_loss']} dropped, {cc['tx_resend'] + sc['tx_resend']} RESENDs, {cc['bytes_resent'] + sc['bytes_resent']} bytes sent again, {cc['timeouts']} RPC timeouts")
        check(sc["requests_delivered"] == sum(len(x.order) for x in srv._sessions.values()),
              f"at most once: the daemon's driver was given each request exactly once ({sc['requests_delivered']}; {sc['duplicate_requests_not_rerun']} repeated packets not run again)")
    else:
        check(hub.overtakes > 100, f"the carrier really reordered: {hub.overtakes} messages were delivered after one posted later")
    if mode == "free":
        check(len(problems) > 0, f"on a wire nobody keeps in order the engine returns wrong answers or errors: {len(problems)} of {total} (this checkpoint passing is the hazard shown)")
        check(worst["n"] > 1, f"frames of one instance were in flight together (up to {worst['n']})")
        return ok
    check(not [p for p in problems if p[0] == "read"], f"every concurrent read answered exactly k rows of its own type ({R} readers x {N})")
    check(not [p for p in problems if p[0] == "write"], f"every concurrent write acknowledged ok ({W} writers x {N})")
    with F.ROWS_LOCK:
        held = {n: F.ROWS[n]["data"]["v"] for n in last}
    check(held == last, f"the origin holds every writer's last value ({len(last)} tuples)")
    st, body = F.call("GET", rpath("A", 3))
    check(st == 200 and len(json.loads(body)["rows"]) == 3, "a read after the storm is still exact")
    sess = list(srv._sessions.values())
    check(sess and all(x.order == list(range(len(x.order))) for x in sess),
          f"contract 2: the daemon's engine was handed every frame in the order the proxy's engine submitted it ({sum(len(x.order) for x in sess)} frames, {len(sess)} session)")
    early_d, early_p = sum(x.n_early for x in sess), sum(w["replies_arrived_early"] for w in ws)
    check(early_d > 0 and early_p > 0, f"the driver did the work: {early_d} requests and {early_p} replies arrived ahead of their turn and were held for it")
    check(worst["n"] > 1, f"contract 3: nothing was held back from the wire: up to {worst['n']} frames of one instance in flight together")
    if not sim:
        check(hub.not_fnw1 == 0, "every message was the driver's header followed by one whole FNW1 frame")
    check(not tcp_workers, "no TCP worker was made")
    # ---- contract 5 by liveness. From here on the carrier reports nothing unless the mode is Homa as shipped.
    hub.dead_after_s = 3600.0
    mux = TH._get_mux()
    w0 = [w for w in TS._WORKERS.values() if not w.is_stopped()]
    s0 = list(srv._sessions.values())
    check(len(w0) == 1 and len(s0) == 1 and w0[0].node == s0[0].node == "10.88.88.1" and "10.88.88.1" in lv["proxy"].watched and "10.88.88.1" in lv["daemon"].watched,
          f"each end named its peer by the HELLO and gave it to liveness to watch (proxy watches {sorted(set(lv['proxy'].watched))}, daemon watches {sorted(set(lv['daemon'].watched))})")

    def burst(n=6):
        got = []
        ths = [threading.Thread(target=lambda i=i: got.append(F.call("GET", rpath("A" if i % 2 else "B", 1 + i % 5)))) for i in range(n)]
        for th in ths: th.start()
        return got, ths

    # (a) the daemon's driver is told the proxy END stopped, with requests in the engine
    hold.clear()
    got, ths = burst()
    t_end = time.time() + 10
    while sum(len(x._dest) for x in srv._sessions.values()) < 6 and time.time() < t_end:
        time.sleep(0.01)
    owed = sum(len(x._dest) for x in srv._sessions.values())
    t0 = time.time(); lv["daemon"].gone(s0[0].node, "its capability tuple is no longer in the memory", False)
    for th in ths: th.join(20)
    dt = time.time() - t0
    hold.set()
    check(owed == 6 and [g[0] for g in got] == [503] * 6 and all("ended this session" in g[1] for g in got) and dt < 3.0,
          f"the daemon's driver was told the proxy end stopped with {owed} requests in the engine: the session ended, each was let go, and every caller was told in {dt:.2f}s ({sorted(set(g[0] for g in got))}, {got[0][1][:80]!r})")
    check(not srv._sessions and w0[0].is_stopped() and "ended this session" in w0[0].stopped_reason(), f"the session is gone at both ends, with the reason ({w0[0].stopped_reason()[:100]})")
    time.sleep(0.3)
    check(told == [], "a proxy that stopped has not left the network: [WRITER_GONE_V1] was not told")
    st, body = F.call("GET", rpath("A", 3))
    s1 = list(srv._sessions.values())
    check(st == 200 and len(json.loads(body)["rows"]) == 3 and len(s1) == 1 and s1[0].sid != s0[0].sid, "the next request opened a new session and its answer is exact")
    check(not tcp_workers, "no TCP worker was made")

    # (b) the proxy's driver is told the daemon's MACHINE left, with requests outstanding on a wire that says nothing
    w1 = [w for w in TS._WORKERS.values() if not w.is_stopped()][0]
    if sim:
        srv._carrier.close()                     # the machine died: nothing answers and nothing says so
    else:
        del hub.ends[(F.DAEMON_IP, F.PORT)]
    got, ths = burst()
    if mode == "sim":
        # Homa as shipped: its own timeout reports the silent peer, and that ends the session too
        for th in ths: th.join(20)
        dt = time.time() - t0
        check([g[0] for g in got] == [503] * 6, f"contract 5 on Homa as shipped: the peer went silent with 6 requests outstanding; every one was failed (503) by Homa's own timeout, none re-sent ({[g[0] for g in got]}, {got[0][1][:70]!r})")
        check(not tcp_workers, "and still no TCP worker")
        return ok
    wait_s = 1.0
    if mode == "simheld":
        p = mux.carrier.p
        wait_s = 3 * p["timeout_ticks"] * p["tick_ms"] / 1000.0
    time.sleep(wait_s)
    check(not got and len(w1._out) == 6 and not w1.is_stopped(), f"6 requests outstanding to a machine that has died waited {wait_s:.1f}s" + (" (3 x timeout_ticks)" if mode == "simheld" else "") + f": none abandoned, none re-sent, the session stands ({len(got)} returned)")
    a0 = hub.aborts
    t0 = time.time(); lv["proxy"].gone(w1.node, "its machine is no longer in the hosts block (it left the network)", True)
    for th in ths: th.join(20)
    dt = time.time() - t0
    check([g[0] for g in got] == [503] * 6 and all("is gone" in g[1] and "left the network" in g[1] for g in got) and dt < 1.0,
          f"contract 5: liveness said the machine left: every one of the 6 failed (503) in {dt * 1000:.0f} ms with that reason ({got[0][1][:90]!r})")
    if sim:
        cc = mux.carrier.counters()
        check(cc["timeouts"] == 0 and not [k for k in mux.carrier._rpcs], f"Homa abandoned nothing on its own ({cc['timeouts']} timeouts) and holds nothing for that peer now")
    else:
        check(hub.aborts - a0 == 6 and [c for c in hub.peer_gone_calls if c[0] == ("10.77.77.2", 40001)] == [(("10.77.77.2", 40001), (F.DAEMON_IP, F.PORT))], f"the 6 requests were aborted in the carrier and it was told the peer is gone, once ({hub.aborts - a0} aborts)")
    check(w1.is_stopped() and not tcp_workers, f"the session is ended with the reason and no TCP worker was made ({w1.stopped_reason()[:80]})")

    # (c) the daemon's driver is told a MACHINE left
    n0 = srv.n_peers_gone
    lv["daemon"].gone(s1[0].node, "its machine is no longer in the hosts block (it left the network)", True)
    time.sleep(0.3)
    check(told == [s1[0].node] and not srv._sessions and srv.n_peers_gone == n0 + 1, f"the daemon's driver was told the machine left: its sessions ended and [WRITER_GONE_V1] was told, once, for {told}")
    if not sim:
        dcalls = [c for c in hub.peer_gone_calls if c[0] == (F.DAEMON_IP, F.PORT)]
        check(dcalls == [((F.DAEMON_IP, F.PORT), ("10.77.77.2", 40001))], f"and its carrier was told that address is gone, only then ({len(dcalls)} call; none when the proxy end merely stopped)")
    return ok


def main():
    if MODE != "all":
        ok = run(MODE)
        print()
        print(f"HOMA-ORDERING ORACLE mode={MODE}: " + ("ALL CHECKPOINTS PASS" if ok else "FAILED"), flush=True)
        return 0 if ok else 1
    rcs = {}
    for m in ("free", "ordered", "gone", "sim", "simheld"):
        print(f"=== {m} ===", flush=True)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), m], capture_output=True, text=True, timeout=900)
        for line in r.stdout.split("\n"):
            if line.startswith(("  [", "    mode=", "    homasim", "HOMA-ORDERING", "RESULT")):
                print(line, flush=True)
        if r.returncode != 0:
            print(r.stdout[-3000:]); print(r.stderr[-3000:])
        rcs[m] = r.returncode
    ok = all(v == 0 for v in rcs.values())
    print()
    print("ALL HOMA-ORDERING ORACLE CHECKPOINTS PASS" if ok else f"HOMA-ORDERING ORACLE FAILED {rcs}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
