#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# agent.py --name NAME --ctl HOST:PORT --out DIR -- the experiment's agent in one namespace (p1..p8, srv, rt). Every
# step of a run reaches it through the control region (a chat-ram of its own at 10.77.30.2:8899) and every answer goes
# back the same way; nothing is read from a file another process wrote and no process waits on another's exit.
#
#   it writes   (obsvis, adv, NAME)         own: here, its build, its role
#               (obsvis, ready, NAME[.N])   a step done: prepared, server (re)started
#               (obsvis, result, NAME)      its data's index: {chunks: [var ...], ok, errors}
#               (obsvis, <var>, data)       its data, ~200 KB a tuple (a reply frame is at most 1 MB)
#   it holds    ONE read on (obsvis, ctl), the controller's words, each a scope:
#               prepare  the plan: srv starts the server under test; p1 and srv start their captures; pN set up
#               go       t0: participants run the seeded workload until t0 + dur, then report
#               step-N   a stress step: server (srv restarts chat-ram), move (p6, p7 move themselves), revoke (srv)
#               server   [stress 2] a fresh server is there: participants re-arm their reads and re-write their state
#               finish   srv and p1 stop their captures, srv its server; srv, p1 and rt report
#               done     the controller has everything: exit
#
# Participant data: truth (ground truth: send / recv, never given to an attacker), o3 (C, D, D-all: the operations as
# the RAM host decodes them, logged at the client library boundary), o4 (p1, the insider: what an authorized member
# reads beyond its own deliveries). srv: o3 for B (Mosquitto's own log, log_type all), the server's CPU and peak RSS.
# Captures (p1: O1, srv: O2): tcpdump -s 96 on eth0, the control region's address left out; the pcap is kept in DIR and
# its packet headers come back as records [t, src, dst, proto, sport, dport, ip_len].
# Conditions: A direct TLS 1.3 to each follower; B Mosquitto, topic chat/<recipient>, TLS 1.3 on 8883; C chat-ram, the
# chat example's own schema and client (ChatServer.<recipient>.<producer>); D chat-ram, own truth (Chat.<producer>.out,
# one held read per producer followed); Dall chat-ram, read-all (Chat.Out.<producer>, one held read, filtered here).
import argparse, json, os, signal, socket, ssl, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "build"))
import bed, ram, workload  # noqa: E402

BUILD = "obsvis-agent-2026-11-06-r2"
HOLD_S = 30.0
CHUNK = 200000
HZ = os.sysconf("SC_CLK_TCK")


class Rec(list):
    """records kept in memory, sent to the controller through the control region"""
    def __init__(self):
        super().__init__(); self.lock = threading.Lock()

    def __call__(self, **o):
        o.setdefault("t", time.time())
        with self.lock:
            self.append(o)


class Child:
    """a process of this agent's (a server, tcpdump), ready when its output says so -- its output read as it comes"""
    def __init__(self, argv, ready, log):
        self.f = open(log, "a"); self.ev = threading.Event(); self.lines = []
        self.p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        def rd():
            for l in self.p.stdout:
                self.f.write(l); self.f.flush(); self.lines.append(l)
                if ready in l: self.ev.set()
            self.ev.set()
        threading.Thread(target=rd, daemon=True).start()
        if not self.ev.wait(30) or self.p.poll() is not None:
            raise RuntimeError("%s did not say %r: %s" % (argv[0], ready, "".join(self.lines[-5:])))

    def usage(self):
        st = open("/proc/%d/stat" % self.p.pid).read().rsplit(")", 1)[1].split()
        hwm = [l for l in open("/proc/%d/status" % self.p.pid) if l.startswith("VmHWM")]
        return {"cpu_s": (int(st[11]) + int(st[12])) / HZ, "rss_peak_kb": int(hwm[0].split()[1]) if hwm else None}

    def stop(self, sig=signal.SIGTERM):
        u = self.usage() if self.p.poll() is None else {}
        if self.p.poll() is None:
            self.p.send_signal(sig)
            try: self.p.wait(15)
            except subprocess.TimeoutExpired: self.p.kill(); self.p.wait()
        return u


def pcap_records(path):
    """the headers of an Ethernet pcap: [t, src, dst, proto, sport, dport, ip_len]"""
    out = []
    with open(path, "rb") as f:
        g = f.read(24)
        if len(g) < 24: return out
        e = "<" if g[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
        nano = g[:4] in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
        while True:
            h = f.read(16)
            if len(h) < 16: break
            s, us, incl, _ = struct.unpack(e + "IIII", h)
            d = f.read(incl)
            if len(d) < 34 or d[12:14] != b"\x08\x00": continue
            ip = d[14:]; ihl = (ip[0] & 15) * 4; proto = ip[9]
            sp = dp = 0
            if proto in (6, 17) and len(ip) >= ihl + 4:
                sp, dp = struct.unpack(">HH", ip[ihl:ihl + 4])
            out.append([s + us / (1e9 if nano else 1e6), socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20]), proto, sp, dp,
                        struct.unpack(">H", ip[2:4])[0]])
    return out


class Agent:
    def __init__(self, a):
        self.name, self.outdir = a.name, a.out
        self.part = self.name in workload.PARTS
        h, p = a.ctl.rsplit(":", 1)
        self.ctl = ram.Ram(h, int(p))
        self.data = {"truth": Rec(), "o3": Rec(), "o4": Rec()}
        self.truth, self.o3, self.o4 = self.data["truth"], self.data["o3"], (self.data["o4"] if self.name == "p1" else None)
        self.errors, self.seen, self.mine, self.fol = [], set(), set(), []
        self.stop, self.rearm, self.epoch = threading.Event(), threading.Condition(), 0
        self.moving, self.moved = threading.Event(), threading.Event()
        self.cap = self.srv = None; self.used = []; self.extra = {}

    def say(self, m):
        print("[%s] %s" % (self.name, m), flush=True)

    def err(self, m):
        self.say(m); self.errors.append(m)

    # ---- the control region
    def main(self):
        self.say("%s; ribbit lib %s; python %s" % (BUILD, ram.LIB, sys.version.split()[0]))
        self.ctl.put("obsvis", "adv", self.name, {"build": BUILD, "pid": os.getpid(), "addr": bed.ADDR(self.name) if self.name in bed.K else "router"}, own=True)
        after = -1
        while True:                                          # ONE held read: every word of the controller's
            ctl = self.ctl
            try:
                rows = ctl.get_after("obsvis", "ctl", after, HOLD_S)
            except ram.RamError as e:
                if self.moving.is_set():                     # [stress 3] this agent is moving: its new session, same place
                    self.moved.wait(); continue
                self.say("the control region ended: %s" % e); os._exit(3)
            for r in rows:
                after = max(after, int(r["id"]))
                w, v = r["scope"], r["value"]
                if w == "done":
                    self.say("done"); os._exit(0)
                threading.Thread(target=self.word, args=(w, v), daemon=True).start()

    def word(self, w, v):
        try:
            if w == "prepare": self.prepare(v)
            elif w == "go" and self.part: self.go(v)
            elif w.startswith("step-"): self.step(w, v)
            elif w == "server" and self.part: self.server_changed(v)
            elif w == "finish": self.finish_side(v)
        except Exception as e:
            self.err("%s: %s" % (w, e))

    def report(self, extra=None):
        idx = []
        for k, rec in list(self.data.items()) + list(self.extra.items()):
            items = list(rec)
            if not items: continue
            buf, n = [], 0
            for it in items:
                buf.append(it)
                if len(json.dumps(buf)) > CHUNK:
                    var = "data.%s.%s.%d" % (self.name, k, n); self.ctl.put("obsvis", var, "data", {"k": k, "rows": buf}); idx.append(var)
                    buf, n = [], n + 1
            if buf:
                var = "data.%s.%s.%d" % (self.name, k, n); self.ctl.put("obsvis", var, "data", {"k": k, "rows": buf}); idx.append(var)
        self.ctl.put("obsvis", "result", self.name, dict({"ok": not self.errors, "errors": self.errors[:20], "n_err": len(self.errors),
                                                          "build": BUILD, "chunks": idx}, **(extra or {})))
        self.say("reported: %d chunk(s), %d error(s)" % (len(idx), len(self.errors)))

    # ---- prepare: the plan
    def prepare(self, plan):
        self.plan = plan
        self.cond, self.kind, self.seed = plan["cond"], plan["traffic"], int(plan["seed"])
        if self.name in plan.get("capture", []):
            pc = os.path.join(self.outdir, "cap-%s.pcap" % self.name)
            self.cap = Child(["tcpdump", "-i", "eth0", "-s", "96", "-U", "-n", "-w", pc, "not host %s" % bed.CTL], "listening on",
                             os.path.join(self.outdir, "tcpdump-%s.log" % self.name))
            self.pcap = pc
        if self.name == "srv":
            self.srv = self.start_server(0)
        if self.part:
            self.mine = set(plan["follows"][self.name])
            self.fol = sorted(c for c, ps in plan["follows"].items() if self.name in ps)
            if self.cond == "A": self.listen_A()
            self.say("run %s: condition %s, %s, seed %d; follows %s; followed by %s" % (plan["run"], self.cond, self.kind, self.seed,
                     sorted(self.mine), self.fol))
        self.ctl.put("obsvis", "ready", self.name, {"ok": True, "what": "prepare"})

    # ---- the server under test (srv)
    def start_server(self, n):
        c, d = self.plan["cond"], self.plan["certs"]
        if c == "A":
            return None
        if c == "B":
            conf = os.path.join(self.outdir, "mosquitto.conf")
            open(conf, "w").write("user root\nper_listener_settings false\nallow_anonymous true\npersistence false\nlog_dest stdout\nlog_type all\n"
                                  "connection_messages true\nlog_timestamp true\nlog_timestamp_format %%s\nlistener 8883 %s\n"
                                  "cafile %s/ca.crt\ncertfile %s/srv.crt\nkeyfile %s/srv.key\ntls_version tlsv1.3\n" % (bed.ADDR("srv"), d, d, d))
            s = Child(["mosquitto", "-c", conf], " running", os.path.join(self.outdir, "o3-mosquitto.log"))
        else:
            s = Child([os.path.join(HERE, "build", "chat-ram"), "--listen", "0.0.0.0:8811"], "answering",
                      os.path.join(self.outdir, "srv-chat-ram-%d.log" % n))
        self.say("server %d: %s" % (n, s.lines[0].strip() if s.lines else c))
        return s

    def step(self, w, v):
        me = self.name in v.get("who", ["srv"] if v["do"] in ("server", "revoke") else [])
        if v["do"] == "server" and self.name == "srv":
            self.used.append(self.srv.stop()); self.srv = self.start_server(len(self.used))
            self.ctl.put("obsvis", "ready", "srv." + w, {"ok": True, "what": w, "t": time.time()})
        elif v["do"] == "revoke" and self.name == "srv":
            for p in v["who"]: bed.revoke(p)
            self.ctl.put("obsvis", "ready", "srv." + w, {"ok": True, "what": w, "t": time.time()})
        elif v["do"] == "move" and me:
            self.moving.set()
            bed.move(self.name)
            self.ctl = ram.Ram(bed.CTL, 8899)                # its sessions from the old address are gone with it
            self.moved.set()
            if self.cond in ("C", "D", "Dall"):              # its own sessions to the server again, its reads re-armed
                getattr(self, "reconnect_" + self.cond)()
                with self.rearm:
                    self.epoch += 1000; self.rearm.notify_all()
            self.truth(ev="moved", c=self.name, to=bed.ADDR(self.name, 3))
            self.ctl.put("obsvis", "ready", self.name + "." + w, {"ok": True, "what": w, "t": time.time()})

    def finish_side(self, v):
        if self.part and self.name != "p1":
            return                                           # a participant reports when its run ends (go)
        if self.cap:
            self.cap.stop(signal.SIGINT)
            self.extra["packets"] = pcap_records(self.pcap)
        extra = {}
        if self.name == "srv":
            if self.srv: self.used.append(self.srv.stop())
            extra["server_usage"] = self.used
            mlog = os.path.join(self.outdir, "o3-mosquitto.log")
            if self.plan["cond"] == "B" and os.path.exists(mlog):     # its own server's log: this agent's own output
                self.extra["mosquitto"] = [l.rstrip("\n") for l in open(mlog)]
        if self.name == "rt":
            links = {}
            for n in workload.PARTS + ["srv"]:
                j = json.loads(subprocess.run(["ip", "-s", "-j", "link", "show", "r%d" % bed.K[n]], capture_output=True, text=True).stdout)[0]["stats64"]
                links[n] = {"to_router_bytes": j["rx"]["bytes"], "from_router_bytes": j["tx"]["bytes"],
                            "to_router_packets": j["rx"]["packets"], "from_router_packets": j["tx"]["packets"]}
            extra["links"] = links
        if self.name == "p1":
            self.p1_cap_done = True
            if not getattr(self, "p1_run_done", False): return     # p1 reports once: its run and its capture
        self.report(extra)

    # ---- go: the run (participants)
    def go(self, v):
        t0, dur = v["t0"], self.plan["dur"]
        try:
            getattr(self, "up_" + self.cond)()
        except Exception as e:
            self.err("condition %s did not come up: %s" % (self.cond, e))
        else:
            for t, mid, text in workload.schedule(self.seed, self.name, self.kind, t0, dur):
                d = t - time.time()
                if d > 0: self.stop.wait(d)
                body = json.dumps({"m": mid, "f": self.name, "t": text}, separators=(",", ":"))
                self.truth(ev="send", m=mid, f=self.name, to=self.fol)
                try:
                    getattr(self, "send_" + self.cond)(body)
                except Exception as e:
                    self.err("send %s: %s" % (mid, e))
            self.stop.wait(max(0.0, t0 + dur + self.plan.get("drain", 8) - time.time()))
        self.stop.set()
        if self.name == "p1":
            self.p1_run_done = True
            if not getattr(self, "p1_cap_done", False):
                self.ctl.put("obsvis", "ready", "p1.run", {"ok": True, "what": "run"}); return
        self.report()

    def recv(self, m, how="recv"):
        if m.get("f") not in self.mine:
            return
        k = (m["m"], m["f"])
        if k in self.seen:
            self.truth(ev="dup", m=m["m"], f=m["f"], c=self.name); return
        self.seen.add(k)
        self.truth(ev=how, m=m["m"], f=m["f"], c=self.name)

    # ---- [stress 2] a fresh server: re-arm and re-write own state
    def server_changed(self, v):
        getattr(self, "reconnect_" + self.cond)()          # the new session first: a reader woken below uses it
        with self.rearm:
            self.epoch = int(v["epoch"]); self.rearm.notify_all()
        self.truth(ev="server", epoch=self.epoch, c=self.name)

    def wait_epoch(self, seen):
        with self.rearm:
            while self.epoch == seen and not self.stop.is_set():
                self.rearm.wait(HOLD_S)
            return self.epoch

    # ================================================================== A: direct TLS
    def listen_A(self):
        d = self.plan["certs"]
        sc = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sc.minimum_version = ssl.TLSVersion.TLSv1_3
        sc.load_cert_chain(os.path.join(d, self.name + ".crt"), os.path.join(d, self.name + ".key"))
        ls = socket.socket(); ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); ls.bind(("0.0.0.0", 9443)); ls.listen(16)

        def conn(c):
            try:
                c = sc.wrap_socket(c, server_side=True)
                while True:
                    h = self._exact(c, 4)
                    if not h: return
                    self.recv(json.loads(self._exact(c, struct.unpack(">I", h)[0])))
            except Exception as e:
                if not self.stop.is_set(): self.err("A receive: %s" % e)
        def acc():
            while True:
                c, _ = ls.accept(); threading.Thread(target=conn, args=(c,), daemon=True).start()
        threading.Thread(target=acc, daemon=True).start()

    @staticmethod
    def _exact(c, n):
        b = b""
        while len(b) < n:
            x = c.recv(n - len(b))
            if not x: return b""
            b += x
        return b

    def up_A(self):
        cc = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); cc.minimum_version = ssl.TLSVersion.TLSv1_3
        cc.load_verify_locations(os.path.join(self.plan["certs"], "ca.crt"))
        self.socks = {}
        for j in self.fol:                          # every listener was up before its agent said ready (prepare)
            s = socket.create_connection((bed.ADDR(j), 9443), timeout=10)
            self.socks[j] = cc.wrap_socket(s, server_hostname=j); self.socks[j].settimeout(None)

    def send_A(self, body):
        b = body.encode()
        for j in self.fol:
            self.socks[j].sendall(struct.pack(">I", len(b)) + b)

    # ================================================================== B: Mosquitto
    def up_B(self):
        import paho.mqtt.client as mqtt
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.name)
        self.mq.tls_set(ca_certs=os.path.join(self.plan["certs"], "ca.crt"), tls_version=ssl.PROTOCOL_TLS_CLIENT)
        ready = threading.Event()
        def on_connect(c, u, f, rc, props=None):
            c.subscribe("chat/" + self.name, qos=0)
            if self.o4 is not None: c.subscribe("chat/#", qos=0)
            ready.set()
        def on_message(c, u, msg):
            try: m = json.loads(msg.payload)
            except ValueError: return
            if msg.topic == "chat/" + self.name: self.recv(m)
            elif self.o4 is not None: self.o4(src="mqtt", topic=msg.topic, f=m.get("f"), bytes=len(msg.payload))
        self.mq.on_connect, self.mq.on_message = on_connect, on_message
        self.mq.connect(bed.ADDR("srv"), 8883, keepalive=60); self.mq.loop_start()
        if not ready.wait(20): raise RuntimeError("Mosquitto did not answer in 20 s")

    def send_B(self, body):
        for j in self.fol:
            self.mq.publish("chat/" + j, body, qos=0)

    reconnect_A = reconnect_B = lambda self: None

    # ================================================================== C: chat-ram, the chat example
    def up_C(self):
        import chat
        self.chat = chat; self.cl = chat.Client(bed.WG("srv"), 8811)
        threading.Thread(target=self.read_C, daemon=True).start()
        if self.o4 is not None: self.insider("ChatServer")

    def reconnect_C(self):
        self.cl = self.chat.Client(bed.WG("srv"), 8811)
        if getattr(self, "last", None): self.send_C(self.last, again=True)

    def read_C(self):
        after, ep = 0, self.epoch
        while not self.stop.is_set():
            self.o3(conn=bed.WG(self.name), op="park", svc="ChatServer", var=self.name, scope="*", bytes=0)
            try:
                msgs, after = self.cl.receive(self.name, after, HOLD_S)
            except Exception as e:
                if self.stop.is_set(): return
                self.err("C held read ended: %s" % e); ep = self.wait_epoch(ep); after = 0; continue
            self.o3(conn=bed.WG(self.name), op="release", svc="ChatServer", var=self.name, scope="*",
                    bytes=sum(len(m["text"]) for m in msgs), rows=len(msgs))
            for m in msgs:
                try: self.recv(json.loads(m["text"]))
                except ValueError: pass

    def send_C(self, body, again=False):
        self.last = body
        for j in self.fol:
            self.cl.send(j, self.name, body)
            self.o3(conn=bed.WG(self.name), op="put", svc="ChatServer", var=j, scope=self.name, bytes=len(body), again=again)

    # ================================================================== D: chat-ram, own truth
    def up_D(self):
        self.r = ram.Ram(bed.WG("srv"), 8811)
        for p in sorted(self.mine):               # one held read per producer followed
            threading.Thread(target=self.read_D, args=(p,), daemon=True).start()
        if self.o4 is not None: self.insider("Chat")

    def reconnect_D(self):
        self.r = ram.Ram(bed.WG("srv"), 8811)
        if getattr(self, "last", None): getattr(self, "send_" + self.cond)(self.last, again=True)

    def read_D(self, var):
        after, ep = -1, self.epoch
        while not self.stop.is_set():
            self.o3(conn=bed.WG(self.name), op="park", svc="Chat", var=var, scope="*", bytes=0)
            try:
                rows = self.r.get_after("Chat", var, after, HOLD_S)
            except ram.RamError as e:
                if self.stop.is_set(): return
                self.err("held read Chat.%s ended: %s" % (var, e)); ep = self.wait_epoch(ep); after = -1; continue
            self.o3(conn=bed.WG(self.name), op="release", svc="Chat", var=var, scope="*",
                    bytes=sum(len(json.dumps(r["value"])) for r in rows), rows=len(rows))
            for r in rows:
                after = max(after, int(r["id"]))
                if "m" in r["value"]: self.recv(r["value"])

    def send_D(self, body, again=False):
        self.last = body
        self.r.put("Chat", self.name, "out", json.loads(body))
        self.o3(conn=bed.WG(self.name), op="put", svc="Chat", var=self.name, scope="out", bytes=len(body), again=again)

    # ================================================================== D-all: chat-ram, read-all
    def up_Dall(self):
        self.r = ram.Ram(bed.WG("srv"), 8811)
        threading.Thread(target=self.read_D, args=("Out",), daemon=True).start()
        if self.o4 is not None: self.insider("Chat")

    reconnect_Dall = reconnect_D

    def send_Dall(self, body, again=False):
        self.last = body
        self.r.put("Chat", "Out", self.name, json.loads(body))
        self.o3(conn=bed.WG(self.name), op="put", svc="Chat", var="Out", scope=self.name, bytes=len(body), again=again)

    # ================================================================== O4: the insider reads the whole service
    def insider(self, svc):
        ins = ram.Ram(bed.WG("srv"), 8811)
        def rd():
            after = -1
            while not self.stop.is_set():
                try:
                    rows = ins.get_after(svc, "", after, HOLD_S)
                except ram.RamError as e:
                    self.o4(src="ram", error=str(e)); return
                for r in rows:
                    after = max(after, int(r["id"])); v = r["value"]; f = v.get("f") or v.get("from")
                    if svc == "ChatServer":
                        try: f = json.loads(v.get("text", "{}")).get("f", f)
                        except ValueError: pass
                    self.o4(src="ram", svc=svc, var=r["var"], scope=r["scope"], f=f, bytes=len(json.dumps(v)))
        threading.Thread(target=rd, daemon=True).start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True); ap.add_argument("--ctl", required=True); ap.add_argument("--out", required=True)
    Agent(ap.parse_args()).main()
