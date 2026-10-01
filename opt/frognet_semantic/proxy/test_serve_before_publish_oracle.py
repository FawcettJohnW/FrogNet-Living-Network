#!/usr/bin/env python3
"""proxy/test_serve_before_publish_oracle.py  [SERVE_BEFORE_PUBLISH_V1]

On the databasehost the proxy's boot publish writes to its OWN :80. Publishing before serve_forever() left every one
of those writes (and every other caller) in the listen backlog until it timed out -- Seattle5, 2026-09-25, a node that
answered nothing for many minutes after a restart. The real FrogNetProxyServer and the real _serve_then are used; the
boot step makes an HTTP call to the proxy's own port, exactly what publish_all does there.
  1. the self-call completes, promptly
  2. a boot step that raises stops the listener and the error reaches the caller (the process exits loudly)
  3. fail-on-old: the pre-fix order (boot step, THEN serve) does not complete the self-call
"""
import http.client, os, sys, threading, time
from http.server import BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    import proxy.proxy_main as PM
fails = []
def ck(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "  -- " + detail)); ok or fails.append(name)
class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
def self_call(port, timeout):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout); c.request("GET", "/frognet_echo.php"); return c.getresponse().read()
# 1
srv = PM.FrogNetProxyServer(("127.0.0.1", 0), Echo); port = srv.server_address[1]; got = {}
def publish():
    t = time.time(); got["body"] = self_call(port, 5); got["s"] = time.time() - t
PM._serve_then(srv, publish)
ck("boot step's call to the proxy's own port completes (%.3fs)" % got.get("s", -1), got.get("body") == b"ok" and got["s"] < 2)
ck("the proxy keeps serving after the boot step", self_call(port, 5) == b"ok")
srv.shutdown(); PM._SERVE_THREAD.join(5)
# 2
srv2 = PM.FrogNetProxyServer(("127.0.0.1", 0), Echo); p2 = srv2.server_address[1]
def bad(): raise RuntimeError("publish failed: store unreachable")
try:
    with contextlib.redirect_stdout(io.StringIO()): PM._serve_then(srv2, bad)
    ck("a failing boot step reaches the caller", False, "no exception")
except RuntimeError as e:
    ck("a failing boot step reaches the caller with its own error", "store unreachable" in str(e))
ck("and the listener is stopped", not PM._SERVE_THREAD.is_alive())
srv2.server_close()
# 3 fail-on-old
srv3 = PM.FrogNetProxyServer(("127.0.0.1", 0), Echo); p3 = srv3.server_address[1]
try: self_call(p3, 2); old_ok = True
except Exception: old_ok = False
ck("fail-on-old: publishing BEFORE serving does not complete the self-call", not old_ok)
srv3.server_close()
print("\n%s: %d fail" % ("ALL SERVE-BEFORE-PUBLISH CHECKS PASS" if not fails else "SERVE-BEFORE-PUBLISH ORACLE FAILED", len(fails)))
sys.exit(1 if fails else 0)
