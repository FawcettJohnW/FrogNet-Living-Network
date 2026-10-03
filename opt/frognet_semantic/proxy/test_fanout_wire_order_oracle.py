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
test_fanout_wire_order_oracle.py - [REFERENCES_MOVE_IN_WIRE_ORDER_V1]

John, 2026-09-26: references move in WIRE ORDER. The proxy commits a request reference as the frame enters its
worker's send order and applies replies in the worker's read order; the daemon merges the request reference on the
socket thread in arrival order and encodes-and-queues a reply under one lock. Under fan-out -- many callers on one
proxy, many workers on one daemon -- that is the only rule that keeps the two ends' references equal.

Drives the REAL transport: proxy.transport_semantic over its own _DaemonWorker (real send socket, real RETURN socket,
real pipelining) against a REAL daemon DaemonServer on 127.0.0.1, with the real template store on MariaDB. The only
substitution is the web server behind the daemon: an api.php stand-in with the same filter rules. data_cache is off.

Workload (one instance = one SensorType, many callers on it at once):
  - R reader threads read entity=sensors&action=values&SensorType=<T>&limit=<k>&parse=1 with k cycling 1..6, on two
    types T in {A, B}; every answer must carry exactly k rows, every row of type T. limit is not a location field, so
    each read is a REQ_DIFF against the instance's previous read; a misordered merge gives a wrong k or a wrong T
    with status 200, and a misapplied RESP_DIFF gives another read's rows.
  - W writer threads each upsert their own tuple (upsert_by_name, one template) with a rising value; each reply must
    be ok, and at the end the origin must hold every writer's last value.
Every request also goes through the origin directly for a reference count of rows.
FAILS on the tree before the fix (wrong row counts, wrong types, or 5xx from a decode/merge error); PASSES after.
"""
import faulthandler
import io
import signal
import json
import os
import socket
import sys
import threading
import time

os.environ["FROGNET_DATA_CACHE_ENABLED"] = "0"
os.environ.setdefault("FROGNET_DAEMON_PORT", "19009")
os.environ.setdefault("FROGNET_LOCAL_READ_CACHE", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from proxy import transport_semantic as TS           # noqa: E402
from proxy.templates import canonical_semantic_key    # noqa: E402
from daemon.engine import server as DSRV              # noqa: E402
from daemon.engine.resolver import DestinationResolver  # noqa: E402

ALLOW = ["SensorID", "FrogID", "SensorAddress", "SensorNetwork", "SensorName", "SensorType", "SensorLocation", "Tags"]
ROWS = {}
ROWS_LOCK = threading.Lock()
PORT = int(os.environ["FROGNET_DAEMON_PORT"])
# the proxy talks only to 10/8 hosts (_is_frognet_ip); the daemon listens on a 10/8 address of this machine
DAEMON_IP = os.environ.get("FROGNET_TEST_DAEMON_IP", "10.77.77.1")


def row(i, name, typ, v):
    return {"SensorID": i, "FrogID": "f1", "SensorAddress": "10.250.250.1", "SensorNetwork": "Seattle5",
            "SensorName": name, "SensorType": typ, "Tags": None, "jsonData": json.dumps({"v": v}),
            "UpdatedAt": "2026-09-26 12:00:%02d" % (i % 60), "UpdatedAtEpoch": 1790424000 + i, "data": {"v": v}}


def origin_answer(method, path, body_text):
    from urllib.parse import urlsplit, parse_qs
    u = urlsplit(path)
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    with ROWS_LOCK:
        if u.path == "/api.php" and q.get("action") == "values":
            filt = {k: v for k, v in q.items() if k in ALLOW}
            rows = [r for r in ROWS.values() if all(str(r.get(k)) == v for k, v in filt.items())]
            rows.sort(key=lambda r: r[q.get("order", "SensorID")])   # api.php: ORDER BY an allow-listed column
            if "limit" in q:
                rows = rows[:int(q["limit"])]
            return json.dumps({"ok": True, "rows": rows}, separators=(",", ":"))
        if u.path == "/api.php" and q.get("action") == "upsert_by_name":
            d = json.loads(body_text)
            n = d["SensorName"]
            i = ROWS[n]["SensorID"] if n in ROWS else len(ROWS) + 1
            ROWS[n] = row(i, n, d["SensorType"], d["jsonData"]["v"])
            return json.dumps({"ok": True, "SensorID": i}, separators=(",", ":"))
    raise RuntimeError("origin: no such call %s %s" % (method, path))


class Origin:
    def request(self, peer_ip, upstream_port, method, path, body_text, headers, host_header):
        b = origin_answer(method, path, body_text)
        return 200, {"Content-Type": "application/json"}, b.encode(), b


class Handler:
    def __init__(self, command):
        self.status = None; self.hdrs = []; self.wfile = io.BytesIO()
        self.headers = {}; self.client_address = ("127.0.0.1", 50000); self.command = command

    def send_response(self, code, msg=None): self.status = code

    def send_header(self, k, v): self.hdrs.append((k, v))

    def end_headers(self): pass


def call(method, path, body=b""):
    h = Handler(method)
    sem = canonical_semantic_key(method, path, body)
    hdr = {"Content-Type": "application/json"} if body else {}
    TS.handle_request(h, {"headers": hdr, "semantic_path": sem}, target_ip=DAEMON_IP, method=method, path=path,
                      headers=hdr, body=body)
    return h.status, h.wfile.getvalue().decode()


def main():
    ok = True
    problems = []

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)

    TS.get_template_store().ensure_tables()
    from proxy.cache import semcache_db as _psc
    _psc.ensure_table()                                  # as proxy_main does at startup
    from daemon.cache import semcache_db as _dsc
    _dsc.ensure_table()                                  # as daemon_main does at startup
    srv = DSRV.DaemonServer(DAEMON_IP, PORT, DestinationResolver())
    srv._http_client = Origin()
    threading.Thread(target=srv.run, daemon=True, name="daemon-server").start()
    for _ in range(50):
        try:
            socket.create_connection((DAEMON_IP, PORT), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)

    # names run opposite to ids, so order=SensorName and order=SensorID give opposite row orders
    types = {"A": ["a%d.Seattle5.cpu.load" % (7 - i) for i in range(8)], "B": ["b%d.Seattle5.mem.load" % (7 - i) for i in range(8)]}
    with ROWS_LOCK:
        i = 0
        for t, names in types.items():
            for n in names:
                i += 1; ROWS[n] = row(i, n, t, i)
    # two non-location fields move independently (limit, order): a caller whose limit equals the reference's omits it
    # from its REQ_DIFF, and a misordered merge fills it from another caller's request
    rpath = lambda t, k, o="SensorID": "/api.php?entity=sensors&action=values&SensorType=%s&limit=%d&order=%s&parse=1" % (t, k, o)

    # warm: one RAW per template, sequentially, so the concurrent phase is all semantic
    for t in types:
        for k in (1, 2):
            st, body = call("GET", rpath(t, k))
            check(st == 200 and len(json.loads(body)["rows"]) == k, f"warm read {t} limit={k}: {st}, {len(json.loads(body)['rows']) if st == 200 else body[:80]} rows")
    wpath = "/api.php?entity=sensor_data&action=upsert_by_name"
    st, body = call("POST", wpath, json.dumps({"SensorName": "w0.Seattle5.cpu.load", "SensorType": "A", "jsonData": {"v": 0}}).encode())
    check(st == 200, f"warm write: {st}")

    R, W, N = int(os.environ.get("FANOUT_R", "8")), int(os.environ.get("FANOUT_W", "4")), int(os.environ.get("FANOUT_N", "150"))
    counts = {"reads": 0, "writes": 0}
    lock = threading.Lock()

    def reader(ix):
        t = "A" if ix % 2 == 0 else "B"
        for j in range(N):
            k = 1 + (ix + j) % 6 if j % 3 else 1 + ix % 6
            o = "SensorID" if (ix + j) % 2 else "SensorName"
            st, body = call("GET", rpath(t, k, o))
            with lock:
                counts["reads"] += 1
            try:
                rows = json.loads(body)["rows"]
            except Exception:
                problems.append(("read", t, k, st, body[:120])); continue
            ordered = [r.get(o) for r in rows] == sorted(r.get(o) for r in rows)
            if st != 200 or len(rows) != k or any(r.get("SensorType") != t for r in rows) or not ordered:
                problems.append(("read", t, k, o, st, "rows=%d types=%s order_ok=%s" % (len(rows), sorted({r.get("SensorType") for r in rows}), ordered)))

    last = {}

    def writer(ix):
        name = "w%d.Seattle5.cpu.load" % ix
        for j in range(N):
            v = ix * 1000 + j
            body = json.dumps({"SensorName": name, "SensorType": "A", "jsonData": {"v": v}}).encode()
            st, got = call("POST", wpath, body)
            with lock:
                counts["writes"] += 1
            if st != 200 or not json.loads(got).get("ok"):
                problems.append(("write", name, v, st, got[:120]))
            last[name] = v

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(R)] + [threading.Thread(target=writer, args=(i,)) for i in range(W)]
    t0 = time.time()
    for th in threads: th.start()
    for th in threads: th.join()
    dt = time.time() - t0
    print(f"    {counts['reads']} reads and {counts['writes']} writes through the wire in {dt:.1f}s "
          f"({(counts['reads'] + counts['writes']) / dt:.0f}/s), {len(problems)} problems", flush=True)
    for p in problems[:8]:
        print("    ", p, flush=True)
    check(not [p for p in problems if p[0] == "read"], f"every concurrent read answered exactly k rows of its own type ({R} readers x {N})")
    check(not [p for p in problems if p[0] == "write"], f"every concurrent write acknowledged ok ({W} writers x {N})")
    with ROWS_LOCK:
        held = {n: ROWS[n]["data"]["v"] for n in last}
    check(held == last, f"the origin holds every writer's last value ({len(last)} tuples)")
    st, body = call("GET", rpath("A", 3))
    check(st == 200 and len(json.loads(body)["rows"]) == 3, "a read after the storm is still exact")
    print()
    print("ALL FANOUT-WIRE-ORDER ORACLE CHECKPOINTS PASS" if ok else "FANOUT-WIRE-ORDER ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    faulthandler.register(signal.SIGUSR1, all_threads=True)   # kill -USR1 <pid>: every thread's stack, for a stall
    rc = main()
    os._exit(rc)
