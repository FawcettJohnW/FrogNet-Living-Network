#!/usr/bin/env python3
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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
door_semantic_tier.py [CODE_ROOT] -- [DOOR_THROUGH_THE_SEMANTIC_PATH_V1] an app's calls to a FrogNet door, through the
REAL proxy and the REAL daemon, each node with its OWN template store, and every answer compared with what the door
said and every request with what the app sent.

Found on hardware 2026-11-02 (OpenEOC on a phone on Seattle5's Wi-Fi, the door on Seattle7): every call to
/dc/ram.php goes Seattle5's proxy -> Seattle7's daemon -> the door, and came back wrong --
  op=admin answered in op=roles' shape, every value null;
  a refused login (HTTP 403 {"ok":false,"why":...}) answered HTTP 200 {"ok":[],"role":null,"station":null,"user":null};
  op=rams answered in op=roles' shape;
  enrollment refused for every user after the first, with the right passwords;
while the same calls made on Seattle7 itself were right. No test could see any of it: they all spoke to the door
directly, or ran both ends on one template store.

The topology, as the nodes run it (one template store per node, DB_CONFIG.json -> 127.0.0.1):
  the REQUESTING node's proxy (Seattle5)  proxy.transport_semantic.handle_request; it learns from its own RAW
                                           bootstraps into ITS store;
  the ORIGIN node's daemon (Seattle7)      daemon SemanticSession; it reads templates by opcode from ITS node's store;
  the ORIGIN node's proxy (Seattle7)       a RAW bootstrap is replayed to port 80, so the origin's proxy serves it and
                                           learns (proxy_main LOCAL path: train on a miss, status < 400) -- as it also
                                           learns from the origin node's OWN calls to its door (curl, the admin app);
  the door                                 OpenEOC's door: its real operations and answer shapes (dc-door r4: roles,
                                           rams, admin, login, enroll), checking the user and password IT RECEIVED,
                                           answering with its own statuses (403 for a refusal).
Both ends learn independently and keep their first sample; a frame carries the opcode and values by position. So the
two stores must agree, and this tier is where they are made to disagree the ways the nodes do.

Substituted: only the socket hop (the frame handed to the session's _process_frame_inner) and the door.
Every answer must equal the door's (status, and the JSON as values); the door must be given exactly what the app sent.

  CODE_ROOT  run that frognet_semantic tree only. Without it: the tree as the nodes ran it on 2026-11-02 (before
             eebacd6 [OP_SELECTS_THE_TEMPLATE_V1] and 68bb965 [REPLY_EXTRACTION_PROVEN_V1]) must FAIL, this tree PASS.
"""
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import types
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
DB_CONFIG = "/opt/frognet_semantic/DB_CONFIG.json"   # core/store.py's own path; the origin's store is its database + "7"

USERS = {"AlphaAdmin": ("ALPHA", "alpha", True), "BravoAdmin": ("bravo-2026", "bravo", True), "EOCAdmin": ("eoc pw", "eoc", False)}
DOOR = "dc-door-sim"
CONFIG0 = {"app": "openeoc", "rams": ["general", "alpha", "bravo"], "partition": "relocate", "login": False,
           "roles": {"eoc": {"default": False}, "alpha": {"default": True}, "bravo": {"default": True}},
           "users": {u: {"role": r, "verifier": {"kdf": "pbkdf2-sha256", "n": 1, "salt": "00", "hash": "x"}, "lead": l} for u, (_, r, l) in USERS.items()},
           "admin": {"user": "RTAdmin", "salt": "aa", "n": 200000, "check": "bb"}, "seq": 1793593812176, "member": "Seattle7", "addr": "10.170.170.1"}
SERVED = {"alpha": "10.170.170.1:23011", "bravo": "10.170.170.1:26240", "general": "10.170.170.1:23827"}
CONFIG = copy.deepcopy(CONFIG0)
RECEIVED = []          # what the door was given by the phone's path: (op, user, password) as it arrived


def door(method, path, body):
    """OpenEOC's door, as ram.php r4 answers: (status, body text)."""
    q = {k: v[-1] for k, v in parse_qs(urlsplit(path).query).items()}
    op = q.get("op", "")
    fail = lambda code, why: (code, json.dumps({"ok": False, "why": why, "output": "", "door": DOOR}))
    if op == "roles":
        roles = {r: {"default": x["default"], "ram": r in CONFIG["rams"]} for r, x in CONFIG["roles"].items()}
        return 200, json.dumps({"ok": True, "door": DOOR, "roles": roles, "rams": CONFIG["rams"], "served": SERVED, "login": False, "seq": CONFIG["seq"]})
    if op == "rams":
        return 200, json.dumps({"ok": True, "door": DOOR, "rams": SERVED, "app": q.get("app", "")})
    if op == "admin" and method == "GET":
        return 200, json.dumps({"ok": True, "door": DOOR, "configs": [CONFIG], "applied": [], "served": SERVED, "last": 15})
    if op in ("login", "enroll") and method == "POST":
        try:
            b = json.loads(body or "")
        except ValueError:
            return fail(400, "app and a JSON body needed")
        u, pw = b.get("user"), b.get("password")
        if u not in USERS or pw != USERS[u][0]:
            return fail(403, "that user and password are not this app's")
        _, role, lead = USERS[u]
        if op == "login":
            return 200, json.dumps({"ok": True, "user": u, "role": role, "station": ""})
        return 200, json.dumps({"ok": True, "user": u, "role": role, "lead": lead, "invite": "fdfinv1.sim-" + u.lower(), "door": DOOR})
    return fail(400, "op is read, write, watch, announce, rams, roles, admin, whoami, login, device, enroll or identity")


E = lambda u, pw: ("POST", "op=enroll&app=openeoc", {"user": u, "password": pw})
L = lambda u, pw: ("POST", "op=login&app=openeoc", {"user": u, "password": pw})
G = lambda op: ("GET", "op=%s&app=openeoc" % op, None)
LOCAL = lambda c: ("LOCAL",) + c            # a call made ON the origin node (Seattle7's curl, the admin app there)
def add_role(name):                         # the admin app changes the configuration
    def f(): CONFIG["roles"][name] = {"default": True}; CONFIG["rams"].append(name)
    return ("CONFIG", f, "role %s added" % name)


def phone_and_admin():
    c = [G("roles"), G("rams"), E("AlphaAdmin", "ALPHA"), E("AlphaAdmin", "wrong"), E("BravoAdmin", "bravo-2026"), G("rams"),
         L("nobody", "x"), L("EOCAdmin", "eoc pw"), G("admin"), G("roles"), E("EOCAdmin", "eoc pw")]
    return c + c


def refused_first():
    """the order seen on hardware: the first of each op a refusal (a mistyped user), then every user"""
    c = [E("RAADmin", "RAAdmin"), L("RAADmin", "RAAdmin"), G("roles")]
    for u, (pw, _r, _l) in USERS.items():
        c += [E(u, pw), G("rams"), L(u, pw)]
    return c + c


def origin_learned_first():
    """Seattle7's own calls taught its store first (what the user did with curl on Seattle7), then the phone"""
    return [LOCAL(G("roles")), LOCAL(L("EOCAdmin", "eoc pw")), LOCAL(G("admin")), LOCAL(G("rams"))] + phone_and_admin()


def config_changed_between():
    """the origin learned op=roles, the admin added a role, then the phone's node learned it: field names that are
    data (roles.<name>.default) differ between the two stores under one opcode"""
    return [LOCAL(G("roles")), LOCAL(E("AlphaAdmin", "ALPHA")), add_role("delta"), G("roles"), G("rams"),
            E("BravoAdmin", "bravo-2026"), G("roles"), add_role("echo"), G("roles"), G("roles"), E("EOCAdmin", "eoc pw"), G("roles")]


SCENARIOS = (("the phone's and the admin app's calls", phone_and_admin), ("a refusal first, then every user", refused_first),
             ("Seattle7's own calls first, then the phone's", origin_learned_first),
             ("the configuration changed between the two nodes' learning", config_changed_between))


def run(root):
    fails = []
    for name, seq in SCENARIOS:
        print(" -- " + name)
        fails += ["%s: %s" % (name, f) for f in run_one(root, seq)]
    return fails


def run_one(root, seq):
    """SEQ through the proxy and daemon of the frognet_semantic tree at ROOT, fresh stores; the list of failures."""
    CONFIG.clear(); CONFIG.update(copy.deepcopy(CONFIG0)); del RECEIVED[:]
    sys.path.insert(0, root)
    for m in [k for k in sys.modules if k.split(".")[0] in ("proxy", "daemon", "core")]:
        del sys.modules[m]
    from proxy import transport_semantic as TS
    from daemon.engine import session as DS
    from proxy.templates import canonical_semantic_key, learn_templates_from_real
    import core.store as CS

    cfg = json.load(open(DB_CONFIG))
    for db in (cfg["database"], cfg["database"] + "7"):                          # fresh stores: this run's templates only
        subprocess.check_call(["mysql", "-u" + cfg["user"], "-p" + cfg["password"], "-h" + cfg["host"], db, "-e",
                               "DROP TABLE IF EXISTS frognet_request_templates; DROP TABLE IF EXISTS frognet_response_templates"])

    class OriginStore(CS.TemplateStore):                                         # Seattle7's store: its own database,
        _pool = None; _pool_lock = threading.RLock(); _tables_ensured = False   # pool and cache
        _tpl_cache = {}; _tpl_cache_lock = threading.RLock()
        def __init__(self):
            with self._pool_lock:
                if self.__class__._pool is None:
                    self.__class__._pool = CS._MySQLPool(dict(cfg, database=cfg["database"] + "7"), getattr(CS, "_POOL_SIZE", 8))
    s7 = OriginStore(); s7.ensure_tables()
    s5 = TS.get_template_store(); s5.ensure_tables()

    def origin_proxy_serves(method, path, body_text, headers):
        """Seattle7's proxy serving a request to its door (proxy_main LOCAL path): learn on a miss, status < 400"""
        st, text = door(method, path, body_text)
        raw = (body_text or "").encode()
        sem = canonical_semantic_key(method, path, raw)
        req_row, resp_row = s7.lookup_by_path(method, sem)
        if not (req_row and resp_row) and st < 400:
            learn_templates_from_real(method=method, semantic_path=sem, req_headers=dict(headers or {}), req_body=raw,
                                      upstream={"status": st, "body": text.encode(), "headers": {"Content-Type": "application/json"}},
                                      store=s7, raw_path=path)
        return st, text

    class Origin:                    # the daemon's HTTP client on Seattle7: port 80 is Seattle7's proxy, else the door
        def request(self, peer_ip, upstream_port, method, path, body_text, headers, host_header):
            if int(upstream_port or 0) == 80:
                st, text = origin_proxy_serves(method, path, body_text, headers)
            else:
                st, text = door(method, path, body_text)
            if method == "POST":
                try:
                    b = json.loads(body_text or "")
                    RECEIVED.append((dict(parse_qs(urlsplit(path).query)).get("op", ["?"])[-1], b.get("user"), b.get("password")))
                except (ValueError, AttributeError):
                    RECEIVED.append(("?", None, body_text))
            return st, {"Content-Type": "application/json"}, text.encode(), text

    class Handler:
        def __init__(self):
            self.status = None; self.hdrs = []; self.wfile = io.BytesIO()
            self.headers = {}; self.client_address = ("127.0.0.1", 50000); self.command = "GET"
        def send_response(self, code, msg=None): self.status = code
        def send_header(self, k, v): self.hdrs.append((k, v))
        def end_headers(self): pass

    a_sock, _b = socket.socketpair()
    sess = DS.SemanticSession(recv_sock=a_sock, peer_ip="127.0.0.1", resolver=None, http_client=Origin())
    sess.engine.loader._store = s7                                              # Seattle7's daemon reads Seattle7's store

    class _Latency:
        def retry_budget(self): return 1.0

    class W:
        _peer_latency = _Latency()
        def submit(self, wire_req, gate=None):
            rpc = types.SimpleNamespace(gate=gate, ticket=0, reply=None)
            _seq, reply, _w = sess._process_frame_inner(wire_req, 1, len(wire_req))
            rpc.reply = reply; return rpc
        def wait(self, rpc): return rpc.reply
        def call(self, wire_req): return self.wait(self.submit(wire_req))
    TS._get_worker = lambda host, port, set_id=0: W()

    fails, sent = [], []
    for i, c in enumerate(seq(), 1):
        if c[0] == "CONFIG":
            c[1](); print("   %2d %s" % (i, c[2])); continue
        local = c[0] == "LOCAL"
        method, q, body = c[1:] if local else c
        path = "/dc/ram.php?" + q
        raw = json.dumps(body).encode() if body is not None else b""
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        if local:
            st, text = origin_proxy_serves(method, path, raw.decode(), hdrs)
            print("   %2d %-4s %-26s on Seattle7 itself: %s" % (i, method, q.split("&")[0], st)); continue
        want_st, want = door(method, path, raw.decode())
        if body is not None: sent.append((q.split("&")[0][3:], body["user"], body["password"]))
        h = Handler()
        TS.handle_request(h, {"headers": hdrs, "semantic_path": canonical_semantic_key(method, path, raw)}, target_ip="127.0.0.1",
                          method=method, path=path, headers=hdrs, body=raw)
        got = h.wfile.getvalue().decode(errors="replace")
        cache = dict(h.hdrs).get("X-FrogNet-Cache", "")
        try: same = json.loads(got) == json.loads(want)
        except ValueError: same = False
        ok = same and h.status == want_st
        line = "%2d %-4s %-26s %-9s %s" % (i, method, q.split("&")[0], cache, "ok" if ok else "WRONG: HTTP %s %s (the door said %s %s)" % (h.status, got[:200], want_st, want[:200]))
        print("   " + line)
        if not ok: fails.append(line)
    if RECEIVED != sent:
        bad = [(s, r) for s, r in zip(sent, RECEIVED) if s != r] or [(sent, RECEIVED)]
        fails.append("the door was not given what the app sent: first (sent, given) %s" % (bad[0],))
        print("   the door was not given what the app sent: (sent, given) %s" % bad[:3])
    sys.path.remove(root)
    return fails


def export(rev, into):
    """the frognet_semantic tree at git REV, into a directory of its own"""
    top = subprocess.check_output(["git", "-C", HERE, "rev-parse", "--show-toplevel"], text=True).strip()
    os.makedirs(into, exist_ok=True)
    tar = subprocess.Popen(["git", "-C", top, "archive", rev] + ["opt/frognet_semantic/" + p for p in
                           ("proxy", "daemon", "core", "frognet_trace.py", "frognet_log.py", "frognet_route")], stdout=subprocess.PIPE)
    subprocess.check_call(["tar", "x", "--strip-components=2", "-C", into], stdin=tar.stdout); tar.wait()
    return into


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    here = os.path.dirname(HERE)
    if argv:
        f = run(os.path.abspath(argv[0]))
        print("RESULT", "PASS" if not f else "FAIL: %d wrong" % len(f)); return 1 if f else 0
    tmp = tempfile.mkdtemp(prefix="door-semantic-")
    print("== the nodes' tree on 2026-11-02 (before eebacd6 and 68bb965): must FAIL")
    before = run(export("eebacd6~1", os.path.join(tmp, "before")))
    print("== this tree: must PASS")
    now = run(here)
    good = bool(before) and not now
    print("RESULT", "PASS" if good else "FAIL: the tree before %s; this tree %s" % (
        "caught" if before else "NOT caught (the tier misses the fault)", "%d wrong" % len(now) if now else "right"))
    for f in now: print("   " + f[:300])
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
