#!/usr/bin/env python3
"""[SAME_MISS_FROM_CACHE_V1] oracle for the Python reference (frognet_semantic_ref).
Usage: test_same_miss_py_oracle.py <frognet_semantic_ref dir>
Drives the real modules: the wire (core/semcache_wire.py), the daemon's handler and answer cache
(daemon/engine/session.py, daemon/cache/semcache_db.py) and both proxy paths (proxy/transport_semantic.py) with the
daemon worker replaced by a fake that records every frame the proxy sends. mysql is stubbed (never used here)."""
import sys, os, types, io, contextlib, hashlib
root = sys.argv[1]; sys.path.insert(0, root); sys.dont_write_bytecode = True
os.environ.setdefault("FROGNET_BLOB_ROOT", "/tmp/same-miss-py-blobs")
m = types.ModuleType("mysql"); mc = types.ModuleType("mysql.connector"); me = types.ModuleType("mysql.connector.errors")
for n in ("Error", "DatabaseError", "OperationalError", "InterfaceError", "IntegrityError", "ProgrammingError", "PoolError"):
    setattr(me, n, type(n, (Exception,), {}))
mp = types.ModuleType("mysql.connector.pooling"); mp.MySQLConnectionPool = object
mc.errors = me; mc.pooling = mp; m.connector = mc
sys.modules.update({"mysql": m, "mysql.connector": mc, "mysql.connector.errors": me, "mysql.connector.pooling": mp})
errors_sent = []
stub = types.ModuleType("proxy.proxy_main")
def _send_error_reply(handler, status, msg, **k): errors_sent.append((status, msg)); return ("ERROR", status, msg)
stub.send_error_reply = _send_error_reply; sys.modules["proxy.proxy_main"] = stub
with contextlib.redirect_stdout(io.StringIO()):
    import core.semcache_wire as W, daemon.cache.semcache_db as D, daemon.engine.session as S, proxy.transport_semantic as T

fails = 0
def check(name, ok, why=""):
    global fails; print(("PASS " if ok else "FAIL ") + name + (" -- " + why if why else "")); fails += 0 if ok else 1
def attempt(name, f):
    try: f()
    except Exception as e: check(name, False, "%s: %s" % (type(e).__name__, e))
SID = hashlib.sha256(b"answer-1").digest()[:16]
PEER = "10.250.9.9"

def w1():
    fr = W.wrap_req_same_miss(SID); msg = W.try_parse(fr)
    check("W1 REQ_SAME_MISS [0x22][same_id:16] round-trips; op_name names it",
          fr[4] == 0x22 and len(fr) == 5 + 16 and msg.op == 0x22 and msg.same_id == SID and W.op_name(0x22) == "REQ_SAME_MISS")
attempt("W1 REQ_SAME_MISS [0x22][same_id:16] round-trips; op_name names it", w1)

def session():
    s = object.__new__(S.SemanticSession); s.peer_ip = PEER; return s
def d1():
    D.answer_put(SID, ("raw", 201, b"X-A: 1\r\n", b"the body"), 8)
    msg = W.try_parse(session()._handle_req_same_miss(SID))
    check("D1 daemon: a held raw answer comes back as RESP_RAW, exactly (status, headers, body); nothing runs",
          msg.op == W.OP_RESP_RAW and msg.same_id == SID and msg.status == 201 and msg.headers == b"X-A: 1\r\n" and msg.body == b"the body")
attempt("D1 daemon: a held raw answer comes back as RESP_RAW, exactly (status, headers, body); nothing runs", d1)
def d2():
    msg = W.try_parse(session()._handle_req_same_miss(hashlib.sha256(b"never").digest()[:16]))
    check("D2 daemon: an answer no longer held is ERROR 410, not a re-run", msg.op == W.OP_ERROR and msg.status == 410, repr(msg))
attempt("D2 daemon: an answer no longer held is ERROR 410, not a re-run", d2)
def d3():
    op, inst = 7, "i1"; dyn = [("a", 1), ("b", "two")]; sid = hashlib.sha256(b"sem").digest()[:16]
    S._set_response_reference(PEER, op, inst, {"a": 99, "b": "stale"})          # the daemon's reference moved on
    D.answer_put(sid, ("sem", op, inst, dyn, {}), 32)
    msg = W.try_parse(session()._handle_req_same_miss(sid))
    after = S._get_response_reference(PEER, op, inst)
    S._clear_response_reference(PEER, op, inst); expected = S._diff_encode_response(PEER, op, inst, dyn, {})
    check("D3 daemon: a held semantic answer comes back as RESP_DIFF encoded against no reference, and the daemon's own "
          "reference is left exactly as it was (later replies may already be encoded against it)",
          msg.op == W.OP_RESP_DIFF and msg.same_id == sid and msg.payload == expected and after == {"a": 99, "b": "stale"},
          "ref after=%r" % (after,))
attempt("D3 daemon: a held semantic answer comes back as RESP_DIFF encoded against no reference", d3)
def d4():
    src = open(os.path.join(root, "daemon/engine/session.py")).read()
    check("D4 daemon: all four SAME decisions require the answer held; all five answer writers keep it",
          src.count("semcache_db.answer_held(old_same_id)") == 4 and src.count("semcache_db.answer_put(") == 5,
          "held=%d put=%d" % (src.count("semcache_db.answer_held(old_same_id)"), src.count("semcache_db.answer_put(")))
attempt("D4 daemon: all four SAME decisions require the answer held", d4)

class FakeWorker:
    def __init__(self, answer): self.frames = []; self.answer = answer
    def call(self, fr): self.frames.append(fr); return self.answer
def p1():
    """bootstrap path: the request goes once; the SAME it gets back is unknown; the answer is asked for, not re-run"""
    sid = hashlib.sha256(b"boot").digest()[:16]
    sent = []; served = []
    worker = FakeWorker(W.wrap_resp_raw(sid, 200, b"", b"answer"))
    T._get_worker = lambda *a, **k: worker
    T._dispatch_rpc = lambda target_ip, req_hash, w, wire_req, path: (sent.append(wire_req), (W.wrap_resp_same(sid), False))[1]
    T.semcache_db.is_req_seen = lambda *a, **k: False
    T.semcache_db.get_raw_by_sameid = lambda *a, **k: None
    T.semcache_db.clear_req_seen = lambda *a, **k: None
    T._handle_resp_raw_and_learn = lambda handler, ctx, store, same_id, status, headers, body, *a, **k: (served.append((same_id, status, body)), "served")[1]
    del errors_sent[:]
    r = T._handle_raw_and_learn(object(), {}, None, target_ip="10.0.0.2", method="POST", path="/x", headers={}, body=b"{}")
    ops = [f[4] for f in worker.frames]
    check("P1 proxy (raw path): request sent once; unknown SAME -> one REQ_SAME_MISS; the RAW answer is served",
          len(sent) == 1 and ops == [0x22] and served == [(sid, 200, b"answer")] and not errors_sent,
          "request sends=%d, extra frames=%r, served=%r, errors=%r" % (len(sent), [hex(o) for o in ops], served, errors_sent))
attempt("P1 proxy (raw path): request sent once; unknown SAME -> one REQ_SAME_MISS; the RAW answer is served", p1)
def p2():
    """semantic path: an unknown SAME must not send the request again (the old retry went to _handle_raw_and_learn)"""
    sid = hashlib.sha256(b"sem-path").digest()[:16]
    reruns = []; diffs = []
    worker = FakeWorker(W.wrap_resp_diff(sid, b"\x00"))
    T._handle_raw_and_learn = lambda *a, **k: (reruns.append(k), "re-run")[1]
    T._handle_resp_diff = lambda handler, codec, resp_tpl, same_id, sem_blob, req_hash, target_ip, opcode, inst: (diffs.append((same_id, sem_blob)), 1)[1]
    T._same_lru_get = lambda same_id: None
    T.semcache_db.get_by_sameid = lambda *a, **k: None
    del errors_sent[:]
    kw = dict(handler=object(), ctx={}, store=None, codec=None, target_ip="10.0.0.3", method="POST", path="/y", body=b"{}",
              req_row=None, resp_row=None, req_headers={}, http_req_would=0, req_tpl=None, resp_tpl=None, opcode=5, inst="k",
              gate=None, msg=W.try_parse(W.wrap_resp_same(sid)), wire_req=b"x", wire_reply=W.wrap_resp_same(sid), rtt_ms=0.0,
              req_type="REQ_DIFF", new_reference=None, req_hash=b"\x01" * 16, worker=worker)
    T._apply_semantic_reply(**kw)
    ops = [f[4] for f in worker.frames]
    check("P2 proxy (semantic path): unknown SAME -> one REQ_SAME_MISS, applied as the RESP_DIFF it is; the request is not sent again",
          not reruns and ops == [0x22] and diffs == [(sid, b"\x00")] and not errors_sent,
          "re-runs=%d, frames=%r, diffs=%r, errors=%r" % (len(reruns), [hex(o) for o in ops], diffs, errors_sent))
attempt("P2 proxy (semantic path): unknown SAME -> one REQ_SAME_MISS, applied as the RESP_DIFF it is", p2)
print("RESULT", "GREEN" if fails == 0 else "RED", fails); sys.exit(1 if fails else 0)
