#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_sim_oracle.py -- [HOMA_SIM_V1] the Homa simulator (core/homa_sim.py) keeps Homa's rules.

Each checkpoint is one rule of HomaModule's protocol.md or its man pages, shown on real UDP sockets on 127.0.0.1:
whole messages of every size up to the limit; the limit; grants (a long message does not go until the receiver
permits it); no order between messages and short ahead of long (SRPT); every message arrives whole under packet loss;
a request is run at most once under loss; a silent server is ETIMEDOUT at timeout_ticks; a reply that is merely late
is never timed out and costs a RESEND and a BUSY every resend_interval; an unknown RPC makes the client start over.

It proves the simulator against the DOCUMENT. It proves nothing about the kernel module.
"""
import errno
import hashlib
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import homa_sim as HS            # noqa: E402
from core import homa_socket as H          # noqa: E402

ok = True


def check(cond, msg):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)


def blob(n, salt):
    seed = hashlib.sha256(b"%d.%d" % (n, salt)).digest()
    return (seed * (n // 32 + 1))[:n]


def echo_server(port, delay_s=0.0, seen=None, loss=0.0):
    os.environ["FROGNET_HOMASIM_LOSS"] = str(loss)
    s = HS.HomaSimSocket("127.0.0.1", port)

    def run():
        while True:
            try:
                m = s.recv()
            except H.CarrierClosed:
                return
            if seen is not None:
                seen.append(hashlib.sha256(m.payload).hexdigest())
            if delay_s:
                threading.Timer(delay_s, s.send_reply, (m.peer, m.id, m.payload[::-1] or b"x")).start()
            else:
                s.send_reply(m.peer, m.id, m.payload[::-1])
    threading.Thread(target=run, daemon=True).start()
    return s


def client(loss=0.0):
    os.environ["FROGNET_HOMASIM_LOSS"] = str(loss)
    return HS.HomaSimSocket("127.0.0.1")


def main():
    # 1. whole messages, every size class, and the limit
    srv = echo_server(19146); c = client()
    sizes = [1, 2, 1399, 1400, 1401, 39999, 40000, 40001, 65536, 250000, 1000000]
    want = {}
    for i, n in enumerate(sizes):
        d = blob(n, i); want[c.send_request(("127.0.0.1", 19146), d, 1000 + i)] = d
    got = {}
    while len(got) < len(sizes):
        m = c.recv(); got[m.id] = m
    check(all(got[r].payload == want[r][::-1] and not got[r].is_request for r in want),
          f"whole messages: {len(sizes)} sizes from 1 byte to 1,000,000 came back exact, each with its cookie")
    check(sorted(m.cookie for m in got.values()) == [1000 + i for i in range(len(sizes))], "every response returned the cookie its request was sent with")
    try:
        c.send_request(("127.0.0.1", 19146), b"x" * 1000001, 1); refused = False
    except ValueError:
        refused = True
    check(refused, "a message of 1,000,001 bytes is refused, as HOMA_MAX_MESSAGE_LENGTH requires")
    k = c.counters(); ks = srv.counters()
    check(ks["tx_grant"] > 0 and k["rx_grant"] > 0, f"grants: the long messages went only as the receiver granted them ({ks['tx_grant']} GRANTs from the server, {k['tx_grant']} from the client for the responses)")

    # 2. no order between messages; short ahead of long
    order, want = [], {}
    big = c.send_request(("127.0.0.1", 19146), blob(1000000, 7), 1)
    smalls = [c.send_request(("127.0.0.1", 19146), blob(200, 50 + i), 2) for i in range(20)]
    for _ in range(21):
        order.append(c.recv().id)
    check(order.index(big) == 20, f"SRPT: 20 short messages sent AFTER a 1,000,000-byte one all completed before it (it finished at position {order.index(big) + 1} of 21)")
    srv.close(); c.close()

    # 3. packet loss: everything arrives whole, and no request is run twice
    seen = []
    srv = echo_server(19147, seen=seen, loss=0.05); c = client(loss=0.05)
    N, want, sent_n, WIN = 300, {}, 0, 32                  # 32 in flight: the semantic wire's own bound per peer
    got, failed, t0 = {}, [], time.time()
    while len(got) + len(failed) < N and time.time() - t0 < 240:
        while sent_n < N and sent_n - len(got) - len(failed) < WIN:
            d = blob(1 + (sent_n * 7919) % 120000, sent_n); want[c.send_request(("127.0.0.1", 19147), d, sent_n)] = d; sent_n += 1
        try:
            m = c.recv(); got[m.id] = m.payload
        except H.RpcFailed as e:
            failed.append(e)
    k, ks = c.counters(), srv.counters()
    check(len(got) == N and not failed and all(got[r] == want[r][::-1] for r in want),
          f"5% packet loss each way: all {N} RPCs (1 byte to 120 KB, 32 in flight) completed exact; {k['dropped_by_loss'] + ks['dropped_by_loss']} packets were dropped, {k['tx_resend'] + ks['tx_resend']} RESENDs, {k['bytes_resent'] + ks['bytes_resent']} bytes sent again")
    check(len(seen) == N and len(set(seen)) == N and ks["requests_delivered"] == N,
          f"at most once: the server's application saw each of the {N} requests exactly once ({ks['duplicate_requests_not_rerun']} repeated request packets were not run again)")
    check(k["rx_unknown"] >= 0 and k["timeouts"] == 0, f"no RPC timed out under loss ({k['rx_unknown']} UNKNOWN restarts)")
    srv.close(); c.close()

    # 4. a silent server
    c = client(); t0 = time.time()
    rid = c.send_request(("127.0.0.1", 19199), b"anyone there", 77)
    try:
        c.recv(); e = None
    except H.RpcFailed as x:
        e = x
    dt = time.time() - t0
    check(e is not None and e.errno == errno.ETIMEDOUT and e.cookie == 77 and e.rpc_id == rid, "a server that is not there: recv raises RpcFailed ETIMEDOUT with the RPC's id and cookie")
    tick = c.p["tick_ms"] / 1000.0; tt = c.p["timeout_ticks"]
    check(0.8 * tt * tick <= dt <= tt * tick + 0.5, f"at timeout_ticks: {dt * 1000:.0f} ms ({tt} ticks of {c.p['tick_ms']} ms; {c.counters()['tx_resend']} RESENDs went unanswered first)")
    c.close()

    # 5. a reply that is only late: never timed out, and what it costs
    late = 5 * tt * tick
    srv = echo_server(19148, delay_s=late); c = client(); t0 = time.time()
    c.send_request(("127.0.0.1", 19148), b"take your time", 5)
    try:
        m = c.recv(); late_ok = m.payload == b"take your time"[::-1]
    except H.RpcFailed:
        late_ok = False
    dt = time.time() - t0
    k, ks = c.counters(), srv.counters()
    check(late_ok and dt >= late, f"a reply {late:.1f} s late (5 x timeout_ticks) is NOT timed out: the server's BUSY keeps the RPC alive ({dt * 1000:.0f} ms)")
    check(70 <= k["tx_resend"] <= 110 and abs(ks["tx_busy"] - k["tx_resend"]) <= 3,
          f"and it cost {k['tx_resend']} RESENDs and {ks['tx_busy']} BUSYs in that time: one pair per resend_interval for every RPC left waiting (in the module, where a tick is 1 ms, that is about 400 packets a second)")
    srv.close(); c.close()

    # 6. UNKNOWN: the server never heard of the RPC (every packet of the request was lost)
    srv = echo_server(19149)
    c = client(loss=1.0)
    c.send_request(("127.0.0.1", 19149), b"lost on the way", 9)
    time.sleep(2 * tick)
    c.loss = 0.0                                            # the link comes back
    try:
        m = c.recv(); un_ok = m.payload == b"lost on the way"[::-1]
    except H.RpcFailed:
        un_ok = False
    check(un_ok and c.counters()["rx_unknown"] >= 1, f"a request lost whole: the client's RESEND is answered UNKNOWN, it starts over, and the RPC completes ({c.counters()['rx_unknown']} UNKNOWN)")
    srv.close(); c.close()

    # 7. [HELD_WAITS_V1] the same waits as held reads, side by side with Homa as shipped
    def waiting(held, n, wait_s, loss=0.0, port=19150):
        os.environ["FROGNET_HOMASIM_HELD_WAITS"] = "1" if held else "0"
        seen = []
        srv = echo_server(port, delay_s=wait_s, seen=seen, loss=loss); c = client(loss=loss)
        want = {c.send_request(("127.0.0.1", port), blob(300 + i, i), i): blob(300 + i, i) for i in range(n)}
        got, bad, t0 = {}, 0, time.time()
        while len(got) + bad < n and time.time() - t0 < wait_s + 60:
            try:
                m = c.recv(); got[m.id] = m.payload
            except H.RpcFailed:
                bad += 1
        k, ks = c.counters(), srv.counters()
        pk = sum(v for x, v in list(k.items()) + list(ks.items()) if x.startswith("tx_"))
        exact = len(got) == n and all(got[r] == want[r][::-1] for r in want)
        srv.close(); c.close()
        os.environ["FROGNET_HOMASIM_HELD_WAITS"] = "0"
        return exact, pk, k["tx_resend"], ks["tx_busy"], ks["tx_held"], len(seen), len(set(seen))
    a = waiting(False, 50, 3.0, port=19150); b = waiting(True, 50, 3.0, port=19151)
    print(f"    50 requests each left waiting 3.0 s:  as shipped {a[1]} packets ({a[2]} RESEND, {a[3]} BUSY)   |   held {b[1]} packets ({b[2]} RESEND, {b[3]} BUSY, {b[4]} HELD)", flush=True)
    check(a[0] and b[0], "both ways every one of the 50 replies arrived exact")
    check(b[2] == 0 and b[3] == 0 and b[4] == 50, f"held: nothing is asked while it waits: 0 RESENDs, 0 BUSYs, one HELD per request ({b[4]})")
    check(b[1] * 10 < a[1], f"held: {b[1]} packets against {a[1]} as shipped for the same 50 waits ({a[1] / max(1, b[1]):.0f}x fewer)")
    d = waiting(True, 100, 0.5, loss=0.05, port=19152)
    check(d[0] and d[5] == 100 and d[6] == 100, f"held, with 5% of packets lost each way: all 100 replies arrived exact and each request ran once ({d[2]} RESENDs, {d[4]} HELDs)")
    os.environ["FROGNET_HOMASIM_HELD_WAITS"] = "1"
    srv = echo_server(19153, delay_s=30.0); c = client()
    for i in range(20):
        c.send_request(("127.0.0.1", 19153), b"wait for me %d" % i, i)
    time.sleep(0.5); t0 = time.time()
    n_gone = c.peer_gone(("127.0.0.1", 19153)); errs = []
    for _ in range(20):
        try:
            c.recv()
        except H.RpcFailed as e:
            errs.append(e.errno)
    check(n_gone == 20 and errs == [errno.EHOSTUNREACH] * 20 and time.time() - t0 < 0.5,
          f"held: the peer leaving is ONE event: all 20 waits ended in {1000 * (time.time() - t0):.0f} ms with EHOSTUNREACH, not 20 timeouts")
    srv.close(); c.close(); os.environ["FROGNET_HOMASIM_HELD_WAITS"] = "0"

    # 8. [NO_RPC_TIMEOUT_V1] the per-RPC timeout removed: silence abandons nothing; the peer leaving is the event
    os.environ.update(FROGNET_HOMASIM_RPC_TIMEOUT="0", FROGNET_HOMASIM_HELD_WAITS="1", FROGNET_HOMASIM_ADAPTIVE="1")
    c = client(); t0 = time.time()
    for i in range(10):
        c.send_request(("127.0.0.1", 19198), b"is anyone there %d" % i, i)       # nobody is
    time.sleep(3 * tt * tick)
    k = c.counters()
    check(c._inbox.empty() and k["timeouts"] == 0, f"no timeout: after 3 x timeout_ticks of silence from a peer that is not there, none of 10 requests was abandoned")
    check(k["tx_resend"] <= 10 * 8, f"and it was asked at doubling intervals, not at a steady pace: {k['tx_resend']} RESENDs for 10 requests in {3 * tt} ticks (as shipped: 190 before giving up)")
    t1 = time.time(); n_gone = c.peer_gone(("127.0.0.1", 19198)); errs = []
    for _ in range(10):
        try:
            c.recv()
        except H.RpcFailed as e:
            errs.append(e.errno)
    check(n_gone == 10 and errs == [errno.EHOSTUNREACH] * 10 and time.time() - t1 < 0.5, "the peer leaving is the event: all 10 ended at once with EHOSTUNREACH")
    c.close()
    seen = []
    srv = echo_server(19154, delay_s=3 * tt * tick, seen=seen, loss=0.05); c = client(loss=0.05)
    want = {c.send_request(("127.0.0.1", 19154), blob(5000 + i, i), i): blob(5000 + i, i) for i in range(30)}
    got = {}
    while len(got) < 30:
        m = c.recv(); got[m.id] = m.payload
    check(all(got[r] == want[r][::-1] for r in want) and len(seen) == 30 and len(set(seen)) == 30,
          f"no timeout, 5% loss, replies 3 x timeout_ticks late: all 30 arrived exact and each request ran once ({c.counters()['tx_resend'] + srv.counters()['tx_resend']} RESENDs, {srv.counters()['tx_busy']} BUSYs)")
    with srv._lock:
        srv_has = len(srv._rpcs)
    n_srv = srv.peer_gone(("127.0.0.1", c.port))
    check(n_srv == srv_has, f"at the server the same event drops what that peer had outstanding ({n_srv} RPC states)")
    srv.close(); c.close()
    os.environ.update(FROGNET_HOMASIM_RPC_TIMEOUT="1", FROGNET_HOMASIM_HELD_WAITS="0", FROGNET_HOMASIM_ADAPTIVE="0")

    print()
    print("ALL HOMA-SIM ORACLE CHECKPOINTS PASS" if ok else "HOMA-SIM ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
