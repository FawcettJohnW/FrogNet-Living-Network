#!/usr/bin/env python3
# fdf-browser [--listen ADDR:PORT] [--no-open] [HOST:PORT] -- [FDF_BROWSER_V1] the browser terminal for an FD&F network.
# Serves the page (/opt/fdf/www/term, xterm.js vendored) and does the page's RAM get/send ("PHP does RAM get/send"):
# the RAM manager's HTTP front sends no CORS headers, so the page talks only to this server, which talks only to the
# RAM manager. Then it opens the page in this machine's browser.
#   --listen  default 127.0.0.1:8091. Anything other than 127.0.0.1 lets any browser that reaches it use it (no auth).
#   --no-open print the address; don't start a browser
# The network is HOST:PORT, else RINA_RAM, else what fdf-join wrote.
#
# The page's API (all JSON):
#   GET  /api/targets?after=G   held until the target list changes after generation G (a participant joins, leaves,
#                               or a machine starts/stops advertising terminals); G=-1 answers at once
#   POST /api/open {target,cols,rows} -> {sid}
#   POST /api/in   {sid, d?|r?|x?}     one input row for the session (seq assigned here, written in seq order)
#   GET  /api/out?sid=S&after=ID       held read on termout/S after ID -> {cells}
#   POST /api/done {sid}               the page saw the end: remove the session's output rows
import json, os, shutil, subprocess, sys, threading, time, uuid, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
BIN = os.path.dirname(os.path.realpath(__file__))
# Linux: /opt/fdf/bin with ../lib/python and ../www/term.  Windows folder: fdf-browser.py, fdfram.py, term\, fdf-watch.exe
LIB = os.path.join(os.path.dirname(BIN), "lib", "python")
sys.path.insert(0, LIB if os.path.isdir(LIB) else BIN)
import fdfram
WWW = os.path.join(os.path.dirname(BIN), "www", "term")
if not os.path.isdir(WWW):
    WWW = os.path.join(BIN, "term")
RING_IN = 256
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}


def log(*a):
    print("[FDF_BROWSER] %s %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


class State:
    def __init__(self, ram):
        self.ram, self.cv = ram, threading.Condition()
        self.gen, self.participants, self.terms, self.error = 0, [], set(), None
        self.sessions = {}                     # sid -> {"target", "seq", "lock"}

    def bump(self, **kw):
        with self.cv:
            for k, v in kw.items():
                setattr(self, k, v)
            self.gen += 1
            self.cv.notify_all()

    def watch_participants(self):              # one session, one watch: fdf-watch
        exe = os.path.join(BIN, "fdf-watch" + (".exe" if os.name == "nt" else ""))
        p = subprocess.Popen([exe, self.ram.hostport], stdout=subprocess.PIPE, text=True)
        for line in p.stdout:
            self.bump(participants=json.loads(line)["participants"])
        rc = p.wait()
        self.bump(error="the participant watch ended (fdf-watch exit %s); restart fdf-browser" % rc)
        log("participant watch ended, exit", rc)

    def watch_terms(self):                     # held read on term.hosts: a machine starts or stops advertising
        last = -1
        while True:
            try:
                cells = self.ram.read("term.hosts")
                terms = {c["variable"] for c in cells}
                if terms != self.terms or last < 0:
                    self.bump(terms=terms)
                last = max([c["id"] for c in cells] + [last, 0])
                self.ram.read("term.hosts", after=last, wait_s=fdfram.HOLD_S)  # returns on a write; a remove shows
            except fdfram.RamError as e:                                        # at the next one (or the hold's end)
                self.bump(error=str(e))
                log("term.hosts read failed:", e)
                return

    def targets(self):
        out = []
        for p in sorted(self.participants, key=lambda p: p["hostname"].lower()):
            out.append({"name": p["hostname"], "alive": p["alive"], "term": p["hostname"] in self.terms,
                        "ips": p.get("ips", []), "os": p.get("os", "")})
        return {"gen": self.gen, "targets": out, "error": self.error, "ram": self.ram.hostport}

    def write_in(self, sid, bag):
        s = self.sessions.get(sid)
        if s is None:
            raise fdfram.RamError("no session " + sid)
        with s["lock"]:                         # seq order == write order == id order
            bag["s"] = s["seq"]
            self.ram.write("term", s["target"], "%s.%d" % (sid, s["seq"] % RING_IN), bag)
            s["seq"] += 1


class H(BaseHTTPRequestHandler):
    st = None

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        st = self.st
        try:
            if u.path == "/api/targets":
                after, end = int(q.get("after", -1)), time.time() + fdfram.HOLD_S
                with st.cv:
                    while st.gen <= after and time.time() < end:
                        st.cv.wait(end - time.time())
                    return self.reply(200, st.targets())
            if u.path == "/api/out":
                return self.reply(200, {"cells": st.ram.read("termout", q["sid"], after=int(q.get("after", 0)),
                                                             wait_s=fdfram.HOLD_S)})
            name = "term.html" if u.path in ("/", "/index.html") else u.path.lstrip("/")
            f = os.path.realpath(os.path.join(WWW, name))
            if not f.startswith(os.path.realpath(WWW) + os.sep) or not os.path.isfile(f):
                return self.reply(404, {"error": "not found"})
            return self.reply(200, open(f, "rb").read(), TYPES.get(os.path.splitext(f)[1], "application/octet-stream"))
        except (fdfram.RamError, KeyError, ValueError) as e:
            return self.reply(502, {"error": str(e)})

    def do_POST(self):
        st = self.st
        try:
            b = self.body()
            if self.path == "/api/open":
                sid = uuid.uuid4().hex[:12]
                st.sessions[sid] = {"target": b["target"], "seq": 0, "lock": threading.Lock()}
                st.write_in(sid, {"open": 1, "cols": int(b.get("cols", 80)), "rows": int(b.get("rows", 24))})
                log("session", sid, "->", b["target"])
                return self.reply(200, {"sid": sid})
            if self.path == "/api/in":
                bag = {k: b[k] for k in ("d", "r", "x") if k in b}
                st.write_in(b["sid"], bag)
                if "x" in bag:
                    log("session", b["sid"], "closed by the page")
                return self.reply(200, {"ok": True})
            if self.path == "/api/done":
                st.sessions.pop(b["sid"], None)
                n = sum(st.ram.remove(c["id"]) for c in st.ram.read("termout", b["sid"]))
                log("session", b["sid"], "ended; removed", n, "output row(s)")
                return self.reply(200, {"ok": True, "removed": n})
            return self.reply(404, {"error": "not found"})
        except (fdfram.RamError, KeyError, ValueError) as e:
            return self.reply(502, {"error": str(e)})


def main():
    a = sys.argv[1:]
    listen, opn = "127.0.0.1:8091", True
    if "--listen" in a:
        i = a.index("--listen"); listen = a[i + 1]; del a[i:i + 2]
    if "--no-open" in a:
        a.remove("--no-open"); opn = False
    try:
        ram = fdfram.Ram(a[0] if a else None)
    except fdfram.RamError as e:
        sys.exit("fdf-browser: %s" % e)
    st = H.st = State(ram)
    threading.Thread(target=st.watch_participants, daemon=True).start()
    threading.Thread(target=st.watch_terms, daemon=True).start()
    host, port = listen.rsplit(":", 1)
    srv = ThreadingHTTPServer((host, int(port)), H)
    srv.daemon_threads = True
    url = "http://%s:%s/" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, port)
    log("network", ram.hostport, "| page at", url, "(Ctrl-C stops; open sessions are closed)")
    if host not in ("127.0.0.1", "localhost"):
        log("listening on", listen, "-- any browser that reaches it can use it; there is no authentication")
    if opn and (os.name == "nt" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform == "darwin"):
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        for sid in list(st.sessions):
            try:
                st.write_in(sid, {"x": 1})
                log("session", sid, "closed (fdf-browser stopped)")
            except fdfram.RamError as e:
                log("session", sid, "close failed:", e)


main()
