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
#   it follows  ONE family, (obsvis, ctl): the controller's words, each an instance (a standing follow, ribbit::Space):
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
# chat example's own schema and client (ChatServer.<recipient>.<producer>, its held reads); D and D-all on ribbit::Space,
# the one publish/subscribe library the conversions (MQTT, Redis) are built on: each write is the producer's own cell in
# its ring (as the MQTT conversion's publishes: <writer>.<n mod RING>) and each consumer holds standing follows --
#   D      own truth: (Chat, <producer>, <producer>.<n mod RING>) for chat, (Chat, <producer>, <producer>) for telemetry;
#          a follow on each producer followed
#   Dall   read-all:  (Chat, Out, <producer>.<n mod RING>) for chat, (Chat, Out, <producer>) for telemetry; one follow on
#          (Chat, Out), filtered here
import argparse, json, os, signal, socket, ssl, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "build"))
import bed, ram, workload  # noqa: E402

BUILD = "obsvis-agent-2026-11-07-r5"
HOLD_S = 30.0
RING = 64                  # a producer's chat cells: write n is <producer>.<n mod RING> (telemetry: one cell, <producer>)
LINGER_S = 5               # a session's writes outlive it by this long (the MQTT conversion's LINGER_S, [LINGER_BY_FAMILY_V1])
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
    def __init__(self, argv, ready, log, stamp=False):
        self.f = open(log, "a"); self.ev = threading.Event(); self.lines = []
        self.p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        def rd():
            for l in self.p.stdout:
                if stamp: l = "%.6f %s" % (time.time(), l)       # its arrival time: the line's own timestamp is whole seconds
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
    """the headers of an Ethernet or raw-IP (a WireGuard interface's) pcap: [t, src, dst, proto, sport, dport, ip_len]"""
    out = []
    with open(path, "rb") as f:
        g = f.read(24)
        if len(g) < 24: return out
        e = "<" if g[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
        nano = g[:4] in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
        raw = struct.unpack(e + "I", g[20:24])[0] in (12, 14, 101)
        while True:
            h = f.read(16)
            if len(h) < 16: break
            s, us, incl, _ = struct.unpack(e + "IIII", h)
            d = f.read(incl)
            if raw:
                if len(d) < 20 or d[0] >> 4 != 4: continue
                ip = d
            else:
                if len(d) < 34 or d[12:14] != b"\x08\x00": continue
                ip = d[14:]
            ihl = (ip[0] & 15) * 4; proto = ip[9]
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
        self.ctl_at = a.ctl
        self.ctl = ram.Space(a.ctl)
        self.ctl_after, self.done = 0, threading.Event()
        self.data = {"truth": Rec(), "o3": Rec(), "o4": Rec()}
        self.truth, self.o3, self.o4 = self.data["truth"], self.data["o3"], (self.data["o4"] if self.name == "p1" else None)
        self.errors, self.seen, self.mine, self.fol = [], set(), set(), []
        self.stop, self.rearm, self.epoch = threading.Event(), threading.Condition(), 0
        self.moving, self.moved = threading.Event(), threading.Event()
        self.cap = self.capw = self.srv = None; self.used = []; self.extra = {}

    def say(self, m):
        print("[%s] %s" % (self.name, m), flush=True)

    def err(self, m):
        self.say(m); self.errors.append(m)

    # ---- the control region
    def main(self):
        self.say("%s; ribbit lib %s; python %s" % (BUILD, ram.LIB, sys.version.split()[0]))
        self.ctl.write("obsvis", "adv", self.name, {"build": BUILD, "pid": os.getpid(), "addr": bed.ADDR(self.name) if self.name in bed.K else "router"})
        self.follow_ctl()
        self.done.wait()
        self.say("done"); os._exit(0)

    def follow_ctl(self):
        """ONE standing follow on the controller's words; each word is handled on a thread of its own"""
        def on(rows):
            for r in rows:
                self.ctl_after = max(self.ctl_after, int(r["id"]))
                if r["gone"] or r["bag"] is None: continue
                if r["instance"] == "done": self.done.set(); return
                threading.Thread(target=self.word, args=(r["instance"], r["bag"]), daemon=True).start()
        def ended(why):
            if self.moving.is_set():                           # [stress 3] this agent is moving: its new session follows on
                return
            self.say("the control region ended: %s" % why); os._exit(3)
        self.ctl.follow("obsvis", "ctl", on, after=self.ctl_after, ended=ended)

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
                    var = "data.%s.%s.%d" % (self.name, k, n); self.ctl.write("obsvis", var, "data", {"k": k, "items": buf}); idx.append(var)
                    buf, n = [], n + 1
            if buf:
                var = "data.%s.%s.%d" % (self.name, k, n); self.ctl.write("obsvis", var, "data", {"k": k, "items": buf}); idx.append(var)
        self.ctl.write("obsvis", "result", self.name, dict({"ok": not self.errors, "errors": self.errors[:20], "n_err": len(self.errors),
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
        if self.name == "srv" and self.cond in ("C", "D", "Dall"):   # the host's side of the tunnels: O3's clock
            pw = os.path.join(self.outdir, "cap-srv-wg.pcap")
            self.capw = Child(["tcpdump", "-i", bed.WGIF("srv"), "-s", "96", "-U", "-n", "-w", pw], "listening on",
                              os.path.join(self.outdir, "tcpdump-srv-wg.log"))
            self.pcapw = pw
        if self.name == "rt":                                        # the links' bytes, the control region's left out
            sh_nft = ["nft", "-f", "-"]
            rules = "table inet obsvis {\n" + "".join("  counter c%d {}\n" % bed.K[n] for n in workload.PARTS + ["srv"]) + \
                    "  chain links { type filter hook forward priority 0; policy accept;\n" + \
                    "".join('    iifname "r%d" ip daddr != %s ip saddr != %s counter name c%d\n' % (bed.K[n], bed.CTL, bed.CTL, bed.K[n])
                            for n in workload.PARTS + ["srv"]) + "  }\n}\n"
            subprocess.run(["nft", "delete", "table", "inet", "obsvis"], capture_output=True)
            r = subprocess.run(sh_nft, input=rules, capture_output=True, text=True)
            if r.returncode: self.err("nft: " + r.stderr.strip())
        if self.name == "srv":
            self.srv = self.start_server(0)
        if self.part:
            self.mine = set(plan["follows"][self.name])
            self.fol = sorted(c for c, ps in plan["follows"].items() if self.name in ps)
            if self.cond == "A": self.listen_A()
            self.say("run %s: condition %s, %s, seed %d; follows %s; followed by %s" % (plan["run"], self.cond, self.kind, self.seed,
                     sorted(self.mine), self.fol))
        self.ctl.write("obsvis", "ready", self.name, {"ok": True, "what": "prepare"})

    # ---- the server under test (srv)
    def start_server(self, n):
        c, d = self.plan["cond"], self.plan["certs"]
        if c == "A":
            return None
        if c in ("B", "Bt"):                                 # [OBSVIS_BT_V1] Bt: B with Nagle off in the broker and the clients
            conf = os.path.join(self.outdir, "mosquitto.conf")
            open(conf, "w").write("user root\nper_listener_settings false\nallow_anonymous true\npersistence false\nlog_dest stdout\nlog_type all\n"
                                  "connection_messages true\nlog_timestamp true\nlog_timestamp_format %%s\nlistener 8883 %s\n"
                                  "cafile %s/ca.crt\ncertfile %s/srv.crt\nkeyfile %s/srv.key\ntls_version tlsv1.3\n" % (bed.ADDR("srv"), d, d, d))
            if c == "Bt":                                    # Mosquitto's default is Nagle on (set_tcp_nodelay false)
                open(conf, "a").write("set_tcp_nodelay true\n")
            s = Child(["stdbuf", "-oL", "mosquitto", "-c", conf], " running", os.path.join(self.outdir, "o3-mosquitto.log"), stamp=True)
        else:
            s = Child([os.path.join(HERE, "build", "chat-ram"), "--listen", "0.0.0.0:8811"], "answering",
                      os.path.join(self.outdir, "srv-chat-ram-%d.log" % n))
        self.say("server %d: %s" % (n, s.lines[0].strip() if s.lines else c))
        return s

    def step(self, w, v):
        me = self.name in v.get("who", ["srv"] if v["do"] in ("server", "revoke") else [])
        if v["do"] == "server" and self.name == "srv":
            self.used.append(self.srv.stop()); self.srv = self.start_server(len(self.used))
            self.ctl.write("obsvis", "ready", "srv." + w, {"ok": True, "what": w, "t": time.time()})
        elif v["do"] == "revoke" and self.name == "srv":
            for p in v["who"]: bed.revoke(p)
            self.ctl.write("obsvis", "ready", "srv." + w, {"ok": True, "what": w, "t": time.time()})
        elif v["do"] == "move" and me:
            self.moving.set()
            bed.move(self.name)
            self.ctl = ram.Space(self.ctl_at)                # its sessions from the old address are gone with it
            self.follow_ctl()
            self.moved.set()
            if self.cond in ("C", "D", "Dall"):              # its own sessions to the server again, its reads re-armed
                getattr(self, "reconnect_" + self.cond)()
                with self.rearm:
                    self.epoch += 1000; self.rearm.notify_all()
            self.moving.clear()
            self.truth(ev="moved", c=self.name, to=bed.ADDR(self.name, 3))
            self.ctl.write("obsvis", "ready", self.name + "." + w, {"ok": True, "what": w, "t": time.time()})

    def finish_side(self, v):
        if self.part and self.name != "p1":
            return                                           # a participant reports when its run ends (go)
        if self.cap:
            self.cap.stop(signal.SIGINT)
            self.extra["packets"] = pcap_records(self.pcap)
        if getattr(self, "capw", None):
            self.capw.stop(signal.SIGINT)
            self.extra["packets_wg"] = pcap_records(self.pcapw)
        extra = {}
        if self.name == "srv":
            if self.srv: self.used.append(self.srv.stop())
            extra["server_usage"] = self.used
            mlog = os.path.join(self.outdir, "o3-mosquitto.log")
            if self.plan["cond"] in ("B", "Bt") and os.path.exists(mlog):     # its own server's log: this agent's own output
                self.extra["mosquitto"] = [l.rstrip("\n") for l in open(mlog)]
        if self.name == "rt":
            links = {}
            for n in workload.PARTS + ["srv"]:
                c = json.loads(subprocess.run(["nft", "-j", "list", "counter", "inet", "obsvis", "c%d" % bed.K[n]],
                                              capture_output=True, text=True).stdout)["nftables"]
                c = [x["counter"] for x in c if "counter" in x][0]
                links[n] = {"to_router_bytes": c["bytes"], "to_router_packets": c["packets"]}   # not to or from the control region
            subprocess.run(["nft", "delete", "table", "inet", "obsvis"], capture_output=True)
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
                self.ctl.write("obsvis", "ready", "p1.run", {"ok": True, "what": "run"}); return
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
        self.mq.connect(bed.ADDR("srv"), 8883, keepalive=60)
        if self.cond == "Bt":                                 # [OBSVIS_BT_V1] paho leaves Nagle on its socket
            import socket as _s; self.mq.socket().setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1)
        self.mq.loop_start()
        if not ready.wait(20): raise RuntimeError("Mosquitto did not answer in 20 s")

    def send_B(self, body):
        for j in self.fol:
            self.mq.publish("chat/" + j, body, qos=0)

    reconnect_A = reconnect_B = reconnect_Bt = lambda self: None
    up_Bt, send_Bt = up_B, send_B                              # [OBSVIS_BT_V1] the same client; Nagle off by cond

    # ================================================================== C: chat-ram, the chat example
    def up_C(self):
        import chat
        self.chat = chat; self.cl = chat.Client(bed.WG("srv"), 8811)
        threading.Thread(target=self.read_C, daemon=True).start()
        if self.o4 is not None: self.insider("ChatServer")   # the insider's read: ribbit::Space, the whole service

    def reconnect_C(self):
        self.cl = self.chat.Client(bed.WG("srv"), 8811)
        if self.kind == "telemetry" and getattr(self, "last", None): self.send_C(self.last, again=True)   # a latest value, re-asserted

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

    # ================================================================== D: own truth, on ribbit::Space
    def families(self):
        return [("Chat", p) for p in sorted(self.mine)] if self.cond == "D" else [("Chat", "Out")]

    def own(self):
        """this participant's own family: D its own topic, D-all the shared Out"""
        return ("Chat", self.name) if self.cond == "D" else ("Chat", "Out")

    def up_D(self):
        self.r = ram.Space("%s:8811" % bed.WG("srv")); self.n = 0
        self.r.linger(*self.own(), LINGER_S)              # its writes outlive a session by 5 s: the MQTT conversion's choice
        for sp, topic in self.families():                  # standing follows: what the family holds, then each batch as it lands
            self.follow_D(sp, topic)
        if self.o4 is not None: self.insider("Chat" if self.cond != "C" else "ChatServer")

    up_Dall = up_D

    def follow_D(self, sp, topic):
        me = bed.WG(self.name)
        self.o3(conn=me, op="follow", svc=sp, var=topic, scope="*", bytes=0)
        def on(rows):
            live = [r for r in rows if not r["gone"] and r["bag"]]
            if not live: return
            self.o3(conn=me, op="release", svc=sp, var=topic, scope="*", bytes=sum(len(json.dumps(r["bag"])) for r in live), rows=len(live))
            for r in live:
                if "m" in r["bag"]: self.recv(r["bag"])
        def ended(why):
            if not self.stop.is_set(): self.err("follow %s/%s ended: %s" % (sp, topic, why))
        self.r.follow(sp, topic, on, after=0, ended=ended)

    def reconnect_D(self):
        """a fresh server (or this participant moved): its session again, its follows again, its own state again"""
        self.r = ram.Space("%s:8811" % bed.WG("srv"))
        self.r.linger(*self.own(), LINGER_S)
        for sp, topic in self.families():
            self.follow_D(sp, topic)
        if self.kind == "telemetry" and getattr(self, "last", None):   # a latest value is re-asserted; a message is not re-sent
            getattr(self, "send_" + self.cond)(self.last, again=True)

    reconnect_Dall = reconnect_D

    def slot(self):
        """chat: distinct events, a ring of this producer's cells; telemetry: a latest value, this producer's one cell,
        rewritten every reading (an unchanged reading is the same bytes in the same cell: the wire's hash-only repeat)"""
        if self.kind == "telemetry":
            return self.name
        n = self.n; self.n += 1
        return "%s.%d" % (self.name, n % RING)

    def send_D(self, body, again=False):
        self.last = body
        sp, topic = self.own(); slot = self.slot()
        self.r.write(sp, topic, slot, json.loads(body))
        self.o3(conn=bed.WG(self.name), op="put", svc=sp, var=topic, scope=slot, bytes=len(body), again=again)

    send_Dall = send_D

    # ================================================================== O4: the insider follows the whole service
    def insider(self, svc):
        ins = ram.Space("%s:8811" % bed.WG("srv")); self.ins = ins
        def on(rows):
            for r in rows:
                v = r["bag"]
                if r["gone"] or not v: continue
                f = v.get("f") or v.get("from")
                if svc == "ChatServer":
                    try: f = json.loads(v.get("text", "{}")).get("f", f)
                    except ValueError: pass
                self.o4(src="ram", svc=svc, var=r["topic"], scope=r["instance"], f=f, bytes=len(json.dumps(v)))
        ins.follow(svc, "", on, after=-1, ended=lambda why: self.o4(src="ram", error=why))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True); ap.add_argument("--ctl", required=True); ap.add_argument("--out", required=True)
    Agent(ap.parse_args()).main()
