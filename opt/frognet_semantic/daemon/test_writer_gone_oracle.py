#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""
test_writer_gone_oracle.py -- [WRITER_GONE_V1] the permanent socket closing is the machine leaving.

The REAL proxy transport (transport_semantic) against a REAL DaemonServer on 127.0.0.1/10.77.77.1, as
test_fanout_wire_order_oracle runs them. This host's api.php is a stub that records what it is told
(FROGNET_API_LOCAL). Checks:
  1  a request through the proxy brings up the permanent socket pair: the daemon says the peer joined
  2  nothing is removed while the socket is up
  3  the proxy's socket pair closes (its worker stopped): the daemon tells api.php remove_writer
     {"subnet": <the peer node's /24, from the .1 its HELLO gave>} exactly once
  4  a probe connection that never brings up a RETURN channel removes nothing
Needs root (10.77.77.1 on lo) and MariaDB for the template store, as the fan-out oracle does.
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

os.environ["FROGNET_DATA_CACHE_ENABLED"] = "0"
os.environ.setdefault("FROGNET_DAEMON_PORT", "19019")
os.environ.setdefault("FROGNET_LOCAL_READ_CACHE", "0")
os.environ.setdefault("FROGNET_LOCAL_GW", "10.88.88.1")      # the proxy's node: its HELLO names this .1
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TOLD = []
class _Api(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        TOLD.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
        b = b'{"ok":true,"removed":0}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
threading.Thread(target=api.serve_forever, daemon=True).start()
os.environ["FROGNET_API_LOCAL"] = "http://127.0.0.1:%d/api.php" % api.server_address[1]

from proxy import transport_semantic as TS           # noqa: E402
from proxy.templates import canonical_semantic_key    # noqa: E402
from daemon.engine import server as DSRV              # noqa: E402
from daemon.engine.resolver import DestinationResolver  # noqa: E402
import io                                             # noqa: E402

PORT = int(os.environ["FROGNET_DAEMON_PORT"])
DAEMON_IP = os.environ.get("FROGNET_TEST_DAEMON_IP", "10.77.77.1")
FAILS = []
def check(ok, what):
    print("  [%s] %s" % ("PASS" if ok else "FAIL", what), flush=True)
    if not ok: FAILS.append(what)


class Origin:
    def request(self, peer_ip, upstream_port, method, path, body_text, headers, host_header):
        b = json.dumps({"ok": True, "rows": []})
        return 200, {"Content-Type": "application/json"}, b.encode(), b


class Handler:
    def __init__(self, command):
        self.status = None; self.hdrs = []; self.wfile = io.BytesIO()
        self.headers = {}; self.client_address = ("127.0.0.1", 50000); self.command = command
    def send_response(self, code, msg=None): self.status = code
    def send_header(self, k, v): self.hdrs.append((k, v))
    def end_headers(self): pass


def call(path):
    h = Handler("GET")
    TS.handle_request(h, {"headers": {}, "semantic_path": canonical_semantic_key("GET", path, b"")}, target_ip=DAEMON_IP,
                      method="GET", path=path, headers={}, body=b"")
    return h.status


def wait_for(pred, limit=15.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        if pred(): return True
        time.sleep(0.05)
    return False


def main():
    subprocess.run(["ip", "addr", "add", DAEMON_IP + "/32", "dev", "lo"], capture_output=True)
    TS.get_template_store().ensure_tables()
    from proxy.cache import semcache_db as _psc
    _psc.ensure_table()
    from daemon.cache import semcache_db as _dsc
    _dsc.ensure_table()
    srv = DSRV.DaemonServer(DAEMON_IP, PORT, DestinationResolver())
    srv._http_client = Origin()
    threading.Thread(target=srv.run, daemon=True, name="daemon-server").start()
    wait_for(lambda: socket.create_connection((DAEMON_IP, PORT), timeout=0.2).close() is None, 5)

    st = call("/api.php?entity=sensors&action=values&SensorType=probe&parse=1")
    joined = wait_for(lambda: any(srv._live_by_ip.values()), 10)
    peer = next(iter(srv._live_by_ip), "")
    check(st == 200 and joined, "1 a request brought up the permanent socket pair: the daemon has %s joined (status %s)" % (peer or "nobody", st))
    time.sleep(0.5)
    check(not TOLD, "2 nothing removed while the socket is up (%d call(s))" % len(TOLD))

    with TS._WORKERS_LOCK:
        ws = [TS._WORKERS.pop(k) for k in list(TS._WORKERS) if k[0] == DAEMON_IP]
    for w in ws:
        w.stop()
    told = wait_for(lambda: TOLD, 20)
    check(told and len(TOLD) == 1 and TOLD[0][0].endswith("action=remove_writer") and TOLD[0][1] == {"subnet": "10.88.88.0/24"},
          "3 the socket pair closed: api.php told %s" % TOLD)

    n = len(TOLD)
    s = socket.create_connection((DAEMON_IP, PORT), timeout=2)   # a probe: HELLO-less, no RETURN, then gone
    s.close()
    time.sleep(1.0)
    check(len(TOLD) == n, "4 a probe connection that never brought up a RETURN channel removed nothing (%d call(s))" % (len(TOLD) - n))

    subprocess.run(["ip", "addr", "del", DAEMON_IP + "/32", "dev", "lo"], capture_output=True)
    print("WRITER-GONE %s: %d fail" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
