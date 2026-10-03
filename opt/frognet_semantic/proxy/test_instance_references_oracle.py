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
test_instance_references_oracle.py - [INSTANCE_REFERENCES_V1] on the Python proxy and daemon

John, 2026-09-26: the template is the generally recognized format; each side's LOCAL cache recognizes multiple
individual instances of it. The key for a specific message is the template plus the location in its data ("read tuple
at a,b,c in an instance"). Templates are NOT keyed per tuple. Tuple (databasehost) calls only; every other call keeps
its per-endpoint template, so discovery is unaffected. The tuple key is ONE definition: core/tuple_key.py.

Drives the REAL proxy (proxy.transport_semantic.handle_request) against a REAL daemon SemanticSession with the real
template store (core/store.py on MariaDB). Substituted: the socket hop (the proxy's _get_worker/_dispatch_rpc hand
each frame to the session's _process_frame_inner) and the web server behind the daemon (an api.php stand-in that
filters exactly as api.php does: build_where_and_params over its $allow list and the __like forms, everything else
ignored). The daemon's data_cache is off: this is the wire, not the databasehost's RAM.

Checks:
  reads   six tuples of one format (GET api.php sensors values, SensorName=<n>, parse=1):
          ONE request template; ONE REQ_RAW (the first read ever); a re-read of an unchanged tuple after reads of
          other tuples is REQ_REPEAT -> RESP_SAME; a changed tuple is RESP_DIFF with the right body; a REQ_DIFF on a
          tuple template carries the location (SensorName) explicitly; every answer is exactly the origin's.
  writes  six tuples of one format (POST api.php sensor_data upsert_by_name): ONE request template, ONE REQ_RAW,
          every later write's frame carries its SensorName explicitly, every answer exact.
  others  getHosts.php, frognet_echo.php, api.php entity=frogs: the template key is exactly today's, and each is
          answered exactly.
FAILS on the tree before [INSTANCE_REFERENCES_V1] (a template per SensorName: six templates, six RAWs, no location on
the wire). PASSES when the proxy and daemon key templates by format and references by instance.
"""
import io
import json
import os
import struct
import sys

os.environ["FROGNET_DATA_CACHE_ENABLED"] = "0"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import lz4.frame                                      # noqa: E402
from proxy import transport_semantic as TS           # noqa: E402
from daemon.engine import session as DS               # noqa: E402
from daemon.engine.packet import extract_origin_dest_and_normalize_packet   # noqa: E402
from proxy.templates import canonical_semantic_key    # noqa: E402
from core.codec import SemanticCodec, FLAG_COMPRESSED  # noqa: E402

ALLOW = ["SensorID", "FrogID", "SensorAddress", "SensorNetwork", "SensorName", "SensorType", "SensorLocation", "Tags"]
ROWS = {}                 # SensorName -> row, the Sensor/SensorData join as api.php selects it
FRAMES = []               # (op name of request, op name of reply, request frame)
STATIC = {
    "/getHosts.php": json.dumps([{"ip": "10.250.250.1", "hostname": "FrogNetHost.Seattle5"},
                                 {"ip": "10.199.199.1", "hostname": "FrogNetHost.BAMacBook"}]),
    "/frognet_echo.php": "Seattle5,10.250.250.1,192.168.0.27,192.168.0.19",
}


def row(i, name, v):
    return {"SensorID": i, "FrogID": "f1", "SensorAddress": "10.250.250.1", "SensorNetwork": "Seattle5",
            "SensorName": name, "SensorType": "cpu", "Tags": None, "jsonData": json.dumps({"v": v}),
            "UpdatedAt": "2026-09-26 12:00:%02d" % i, "UpdatedAtEpoch": 1790424000 + i, "data": {"v": v}}


class Origin:
    """api.php as far as these calls go: GET sensors values filtered by $allow (and __like), POST upsert_by_name."""

    def request(self, peer_ip, upstream_port, method, path, body_text, headers, host_header):
        from urllib.parse import urlsplit, parse_qs
        u = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        if u.path in STATIC:
            b = STATIC[u.path]
        elif u.path == "/api.php" and q.get("entity") == "frogs":
            b = json.dumps({"ok": True, "rows": [{"FrogID": q.get("FrogID"), "Name": "frog" + q.get("FrogID", "")}]})
        elif u.path == "/api.php" and q.get("action") == "values":
            filt = {k: v for k, v in q.items() if k in ALLOW}
            rows = [r for r in ROWS.values() if all(str(r.get(k)) == v for k, v in filt.items())]
            if "limit" in q:
                rows = rows[:int(q["limit"])]
            b = json.dumps({"ok": True, "rows": rows})
        elif u.path == "/api.php" and q.get("action") == "upsert_by_name":
            d = json.loads(body_text)
            n = d["SensorName"]
            i = ROWS[n]["SensorID"] if n in ROWS else len(ROWS) + 1
            ROWS[n] = row(i, n, d["jsonData"]["v"])
            b = json.dumps({"ok": True, "SensorID": i})
        else:
            raise RuntimeError("origin: no such call %s %s" % (method, path))
        return 200, {"Content-Type": "application/json"}, b.encode(), b


class Handler:
    def __init__(self, command):
        self.status = None; self.hdrs = []; self.wfile = io.BytesIO()
        self.headers = {}; self.client_address = ("127.0.0.1", 50000); self.command = command

    def send_response(self, code, msg=None): self.status = code

    def send_header(self, k, v): self.hdrs.append((k, v))

    def end_headers(self): pass


def explicit_fields(frame):
    """The field names a REQ_FULL/REQ_DIFF frame carries explicitly, by its template's field order."""
    msg = DS.try_parse(frame)
    _o, _d, sem = extract_origin_dest_and_normalize_packet(packet=msg.payload, peer_ip="127.0.0.1")
    _v, flags, opcode, n = struct.unpack("<BBIH", sem[:8])
    fb = sem[8:]
    if flags & FLAG_COMPRESSED:
        fb = lz4.frame.decompress(fb)
    idx = SemanticCodec()._decode_fieldblock(fb, n)
    req_row, _resp = TS.get_template_store().lookup_by_opcode(opcode)
    tpl = TS.get_template_store().build_request_template(req_row)
    names = list(tpl.url_query_keys or []) + list((tpl.fragment or {}).get("field_order") or [])
    return {names[i] for i in idx}


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    store = TS.get_template_store()
    store.ensure_tables()
    import socket as _socket
    a_sock, _b_sock = _socket.socketpair()
    sess = DS.SemanticSession(recv_sock=a_sock, peer_ip="127.0.0.1", resolver=None, http_client=Origin())

    class _Latency:
        def retry_budget(self): return 1.0

    class W:
        """The socket hop, substituted: submit() hands the frame to the daemon session and holds the reply; wait()
        returns it. The proxy's real _dispatch_begin/_dispatch_end run around it ([REFERENCES_MOVE_IN_WIRE_ORDER_V1])."""
        _peer_latency = _Latency()

        def submit(self, wire_req, gate=None):
            import types
            rpc = types.SimpleNamespace(gate=gate, ticket=0, reply=None)
            _seq, reply, _w = sess._process_frame_inner(wire_req, 1, len(wire_req))
            q, r = DS.try_parse(wire_req), DS.try_parse(reply)
            FRAMES.append((DS.op_name(q.op), DS.op_name(r.op) if r else "?", wire_req))

            rpc.reply = reply
            return rpc

        def wait(self, rpc):
            return rpc.reply

        def call(self, wire_req):                       # the RAW bootstrap path still uses call()
            return self.wait(self.submit(wire_req))

    TS._get_worker = lambda host, port, set_id=0: W()

    def call(method, path, body=b""):
        h = Handler(method)
        sem = canonical_semantic_key(method, path, body)    # exactly as proxy_main builds ctx["semantic_path"]
        mark = len(FRAMES)
        TS.handle_request(h, {"headers": {"Content-Type": "application/json"} if body else {}, "semantic_path": sem},
                          target_ip="127.0.0.1", method=method, path=path,
                          headers={"Content-Type": "application/json"} if body else {}, body=body)
        return h.status, h.wfile.getvalue().decode(), FRAMES[mark:]

    def origin_now(method, path, body=b""):
        return Origin().request("", 0, method, path, body.decode(), {}, "")[3]

    names = ["n%d.Seattle5.cpu.load" % i for i in range(6)]
    for i, n in enumerate(names):
        ROWS[n] = row(i + 1, n, 10 + i)
    rpath = lambda n, extra="": "/api.php?entity=sensors&action=values&SensorName=%s&parse=1%s" % (n, extra)

    # ---- reads -------------------------------------------------------------------------------------------------
    bad, raws, trace = [], 0, []
    for rnd in range(3):
        for n in names:
            want = origin_now("GET", rpath(n))
            st, body, fr = call("GET", rpath(n))
            raws += sum(1 for q, _r, _f in fr if q == "REQ_RAW")
            trace.append((rnd, n, [(q, r) for q, r, _f in fr]))
            if st != 200 or json.loads(body) != json.loads(want):
                bad.append((rnd, n, st, body[:120]))
    check(not bad, "every read answered exactly the origin's body (18 reads, 3 rounds x 6 tuples)%s"
          % ("" if not bad else ": " + repr(bad[:2])))
    check(raws == 1, f"one REQ_RAW for six tuples of one format (got {raws})")
    # An instance is held once it has had a semantic (non-RAW) exchange; the RAW bootstrap learns the template only.
    held, later = set(), []
    for rnd, n, ops in trace:
        if n in held:
            later.append((rnd, n, ops))
        if any(q != "REQ_RAW" for q, _r in ops):
            held.add(n)
    same = [t for t in later if t[2] == [("REQ_REPEAT", "RESP_SAME")]]
    check(len(later) == 11 and len(same) == len(later), "every re-read of an unchanged tuple whose instance is held, "
          f"after reads of the other tuples, is REQ_REPEAT -> RESP_SAME ({len(same)} of {len(later)}; 11 expected: "
          "18 reads less the 6 first sends less n0's first semantic send after its RAW)")
    nread = [r for r in store_rows(store) if r.startswith("GET /api.php?entity=sensors&action=values")]
    check(len(nread) == 1, f"one request template for the read format (store holds {len(nread)}: {nread[:3]})")

    for n, v in ((names[2], 77), (names[4], 88)):
        ROWS[n] = row(ROWS[n]["SensorID"], n, v)
    chg = []
    for n in names:
        want = origin_now("GET", rpath(n))
        st, body, fr = call("GET", rpath(n))
        chg.append((n, [(q, r) for q, r, _f in fr], json.loads(body) == json.loads(want)))
    check(all(c[2] for c in chg), "after two tuples change, every read is exact")
    check([c[1] for c in chg if c[0] in (names[2], names[4])] == [[("REQ_REPEAT", "RESP_DIFF")]] * 2,
          "the two changed tuples are RESP_DIFF")
    check(all(c[1] == [("REQ_REPEAT", "RESP_SAME")] for c in chg if c[0] not in (names[2], names[4])),
          "the four unchanged tuples are still RESP_SAME")

    st, body, fr = call("GET", rpath(names[1], "&limit=5"))    # a new shape: its own template, RAW once
    st, body, fr = call("GET", rpath(names[1], "&limit=5"))    # first semantic send of this instance: REQ_FULL
    st, body, fr = call("GET", rpath(names[1], "&limit=4"))    # same instance, a non-location field changed: REQ_DIFF
    diffs = [f for q, _r, f in fr if q == "REQ_DIFF"]
    check(bool(diffs) and all("SensorName" in explicit_fields(f) for f in diffs),
          "a REQ_DIFF on a tuple template carries its location (SensorName) explicitly")
    check(json.loads(body) == json.loads(origin_now("GET", rpath(names[1], "&limit=4"))), "and is answered exactly")
    st, body, fr = call("GET", rpath(names[3], "&limit=4"))    # another instance of that template
    check(json.loads(body) == json.loads(origin_now("GET", rpath(names[3], "&limit=4"))) and
          not any(q == "REQ_RAW" for q, _r, _f in fr), "another tuple on that template: no RAW, answered exactly")

    # ---- writes ------------------------------------------------------------------------------------------------
    wpath = "/api.php?entity=sensor_data&action=upsert_by_name"
    wbad, wraws, wdiff = [], 0, []
    for rnd in range(2):
        for i, n in enumerate(names):
            body = json.dumps({"SensorName": n, "SensorType": "cpu", "jsonData": {"v": 100 * rnd + i}}).encode()
            st, got, fr = call("POST", wpath, body)
            wraws += sum(1 for q, _r, _f in fr if q == "REQ_RAW")
            wdiff += [f for q, _r, f in fr if q == "REQ_DIFF"]
            if st != 200 or json.loads(got) != {"ok": True, "SensorID": ROWS[n]["SensorID"]}:
                wbad.append((n, st, got[:100]))
            if ROWS[n]["data"] != {"v": 100 * rnd + i}:
                wbad.append((n, "origin holds", ROWS[n]["data"]))
    check(not wbad, "every write reached the origin with its own values and was answered exactly%s"
          % ("" if not wbad else ": " + repr(wbad[:2])))
    check(wraws == 1, f"one REQ_RAW for twelve writes to six tuples of one format (got {wraws})")
    nwrite = [r for r in store_rows(store) if r.startswith("POST /api.php?entity=sensor_data&action=upsert_by_name")]
    check(len(nwrite) == 1, f"one request template for the write format (store holds {len(nwrite)})")
    check(bool(wdiff) and all("SensorName" in explicit_fields(f) for f in wdiff),
          f"every write REQ_DIFF carries its SensorName explicitly ({len(wdiff)} frames)")

    # ---- per-endpoint calls: unchanged --------------------------------------------------------------------------
    keys = {("GET", "/getHosts.php"): "/getHosts.php",
            ("GET", "/frognet_echo.php"): "/frognet_echo.php",
            ("GET", "/api.php?entity=frogs&action=list&FrogID=7"): "/api.php?entity=frogs&action=list&FrogID=7",
            ("GET", "/propogateNotification.php?event=abc"): "/propogateNotification.php?__dyn=event"}
    for (m, p), k in keys.items():
        check(canonical_semantic_key(m, p, b"") == k, f"template key for {p} is today's: {k}")
    for p in ("/getHosts.php", "/frognet_echo.php", "/api.php?entity=frogs&action=list&FrogID=7"):
        outs = [call("GET", p) for _ in range(3)]
        check(all(o[0] == 200 and same_answer(o[1], origin_now("GET", p)) for o in outs),
              f"{p} answered exactly, 3 times: {[o[1][:80] for o in outs]}")

    print()
    print("ALL INSTANCE-REFERENCES ORACLE CHECKPOINTS PASS" if ok else "INSTANCE-REFERENCES ORACLE FAILED")
    return 0 if ok else 1


def same_answer(got, want):
    """JSON answers compare as JSON (the engine rebuilds JSON; spacing is not the answer); anything else byte-exact."""
    try:
        return json.loads(got) == json.loads(want)
    except ValueError:
        return got == want


def store_rows(store):
    def _do(conn):
        cur = conn.cursor()
        cur.execute("SELECT method, actionUrl FROM frognet_request_templates")
        rows = ["%s %s" % tuple(r) for r in cur.fetchall()]
        cur.close()
        return rows
    return store._with_conn(_do)


if __name__ == "__main__":
    sys.exit(main())
