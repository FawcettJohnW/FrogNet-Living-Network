"""
test_resp_same_reference_oracle.py - [SAME_MOVES_REFERENCE_V1]

The daemon, answering RESP_SAME, sets its response reference for (peer, opcode) to the answer the SAME names
(daemon/engine/session.py, REQ_REPEAT and REQ_DIFF branches). The proxy's RESP_SAME branch served the cached body
and left its own reference where it was. When the SAME'd answer is not the proxy's last decoded answer for that
opcode, the two references differ, and the next RESP_DIFF is applied to a reference it was not encoded against:
fields the daemon omitted as unchanged are filled by the proxy from the wrong answer. Silent wrong data.

Drives the REAL proxy (proxy.transport_semantic.handle_request) against a REAL daemon SemanticSession, with the
real template store (core/store.py on MariaDB). Substituted: the socket hop (proxy _get_worker/_dispatch_rpc hand
the frame to the session's _process_frame_inner) and the origin web server behind the daemon (a dict of answers).

One template (GET /item.php?id=...), two items:
  A = {"id":"A","n":1,"s":"x"}      B = {"id":"B","n":2,"s":"x"}
  A (learn), A, B, A (-> RESP_SAME), then B changes to n=1 -> the daemon diffs against A and omits n.
The proxy must answer B's real body. FAILS on the old proxy (it answers n=2), PASSES when the proxy moves its
response reference on RESP_SAME exactly as the daemon does.
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from proxy import transport_semantic as TS          # noqa: E402
from daemon.engine import session as DS              # noqa: E402
from proxy.templates import canonical_semantic_key   # noqa: E402

ORIGIN = {}
SEEN = []


class Origin:
    def request(self, peer_ip, upstream_port, method, path, body_text, headers, host_header):
        from urllib.parse import urlsplit, parse_qs       # a PHP origin reads $_GET['id']; other parameters are ignored
        body = ORIGIN["/item.php?id=" + parse_qs(urlsplit(path).query)["id"][-1]]
        return 200, {"Content-Type": "application/json"}, body.encode(), body


class Handler:
    def __init__(self):
        self.status = None; self.hdrs = []; self.wfile = io.BytesIO()
        self.headers = {}; self.client_address = ("127.0.0.1", 50000); self.command = "GET"

    def send_response(self, code, msg=None): self.status = code

    def send_header(self, k, v): self.hdrs.append((k, v))

    def end_headers(self): pass


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    TS.get_template_store().ensure_tables()              # as the daemon does at startup ([TEMPLATE_TABLE_IS_REQUIRED_V1])
    import socket as _socket
    a_sock, _b_sock = _socket.socketpair()             # the session sets socket options on its receive socket; unused
    sess = DS.SemanticSession(recv_sock=a_sock, peer_ip="127.0.0.1", resolver=None, http_client=Origin())

    class W:
        pass

    TS._get_worker = lambda host, port, set_id=0: W()

    def dispatch(target_ip, req_hash, worker, wire_req, path):
        msg = DS.try_parse(wire_req)
        SEEN.append(DS.op_name(msg.op) if msg else "?")
        _seq, reply, _w = sess._process_frame_inner(wire_req, 1, len(wire_req))
        r = DS.try_parse(reply)
        SEEN.append(DS.op_name(r.op) if r else "?")
        return reply, False
    TS._dispatch_rpc = dispatch

    def get(item):
        h = Handler()
        path = f"/item.php?id={item}"
        sem = canonical_semantic_key("GET", path, b"")      # exactly as proxy_main builds ctx["semantic_path"]
        TS.handle_request(h, {"headers": {}, "semantic_path": sem}, target_ip="127.0.0.1", method="GET",
                          path=path, headers={}, body=b"")
        return h.status, h.wfile.getvalue().decode()

    A = {"id": "A", "n": 1, "s": "x"}
    B = {"id": "B", "n": 2, "s": "x"}
    ORIGIN["/item.php?id=A"] = json.dumps(A)
    ORIGIN["/item.php?id=B"] = json.dumps(B)

    steps = []
    for item in ("A", "A", "B", "A"):
        del SEEN[:]
        st, body = get(item)
        steps.append((item, list(SEEN), st, body))
    ORIGIN["/item.php?id=B"] = json.dumps({"id": "B", "n": 1, "s": "x"})    # B now shares n with A
    del SEEN[:]
    st, body = get("B")
    steps.append(("B'", list(SEEN), st, body))
    for item, frames, st, body in steps:
        print(f"    {item:3} {' -> '.join(frames):40} {st} {body}")

    check(any("RESP_SAME" in f for _i, f, _s, _b in steps[3:4]), "the 4th call (A again) is answered RESP_SAME")
    check(any("RESP_DIFF" in f for _i, f, _s, _b in steps[4:5]), "the 5th call (B changed) is answered RESP_DIFF")
    try:
        got = json.loads(steps[4][3])
    except Exception:
        got = None
    want = {"id": "B", "n": 1, "s": "x"}
    check(got == want, f"the proxy answers B's real body {json.dumps(want)} (got {steps[4][3]})")
    for item, _f, _s, body in steps[:4]:
        exp = A if item == "A" else B
        try:
            check(json.loads(body) == exp, f"call for {item} answered exactly {json.dumps(exp)}")
        except Exception:
            check(False, f"call for {item} answered exactly (got {body!r})")
    print()
    print("ALL RESP-SAME-REFERENCE ORACLE CHECKPOINTS PASS" if ok else "RESP-SAME-REFERENCE ORACLE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
