#!/usr/bin/env python3
"""perf.py -- lispers.net vs Ribbit-LISP performance qualification.

A separate program from the acceptance suite; it drives the same systems, configured from the same fixture, and adds
nothing to the package it runs from (drop this one file into an installed v0.60 directory; no rebuild).

Workloads, each at 1, 4, 16 and 64 closed-loop clients, fixed duration after a warmup:
  reg    Map-Register -> Map-Notify, every register a real state change (the locator set alternates)
  req    ECM Map-Request -> Map-Reply for registered EIDs (state preloaded)
  mixed  80% requests / 20% registers
and a scale sweep: the req workload at 16 clients with 1, 100, 1K, 10K registrations preloaded, plus the same with
nested prefixes (/20, /24, /28, /32) so the lookup is a real longest-prefix match.

Reported for every run, from all three components at once -- an integrated view:
  load generator  successful ops/s, p50/p95/p99 latency, errors, unanswered; its own CPU (a client-bound run shows)
  control         the systems' machine: CPU and memory of each system's own processes (lispers.net: every lisp-*
                  process; Ribbit: its front), the machine's CPU utilization, network bytes and packets
  shared memory   the RAM server's machine: the RAM server's CPU and memory, machine CPU, network
with CPU-seconds per million successful operations per component, network bytes per operation per machine, and a
timeline (one sample a second from every machine, run boundaries marked) in PERF-TIMELINE.html.

How the numbers line up without trusting clocks: the harness writes each run's boundaries (after warmup, at the end)
as cells in the shared memory; every machine's monitor holds a read on them and snapshots its counters the moment a
boundary appears, so a run's deltas on every machine cover the same interval.

Machines (connections go up only, to the RAM server):
  RAM machine:   the RAM server; and   python3 tools/perf.py --monitor ram --ram HOST:PORT
  systems:       python3 tools/perf.py --agent --ram HOST:PORT --lispers-dir DIR     (as root; monitors itself too)
  load:          python3 tools/perf.py --ram HOST:PORT --out perf-out [--quick]
"""
import argparse, json, os, random, socket, struct, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import acceptance as T

PERF_VERSION = "perf v1 (package v0.60-acceptance)"
CLK = os.sysconf("SC_CLK_TCK")

# ------------------------------------------------------------------------------------------------ CPU accounting
def proc_cpu(pids):
    """user+system seconds of the given processes (and their reaped children), from /proc."""
    t = 0.0
    for pid in pids:
        try: f = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()
        except OSError: continue
        t += (int(f[11]) + int(f[12]) + int(f[13]) + int(f[14])) / CLK
    return t

def pids_named(*names):
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    return [int(l.split()[0]) for l in out.splitlines() if any(n in l for n in names)]

def lispers_cpu(): return proc_cpu(pids_named(*[p + ".py" for p in T.LISP_PROCS]))
def ribbit_front_cpu(): return proc_cpu(pids_named("lisp-boundary"))
def ram_server_cpu(): return proc_cpu(pids_named("lisper-ram --listen"))

# ------------------------------------------------------------------------------------------------ the monitor (every machine)
GROUPS = {   # what each component's processes are, by command line
    "lispers.net": [p + ".py" for p in T.LISP_PROCS],
    "ribbit-front": ["lisp-boundary"],
    "ram-server": ["lisper-ram --listen"],
}
def proc_rss_mb(pids):
    t = 0
    for pid in pids:
        try:
            for l in open("/proc/%d/status" % pid):
                if l.startswith("VmRSS:"): t += int(l.split()[1])
        except OSError: pass
    return t / 1024.0

def host_counters():
    f = open("/proc/stat").readline().split()[1:]; v = [int(x) for x in f]
    total = sum(v[:8]) / CLK; idle = (v[3] + v[4]) / CLK
    rx = tx = rxp = txp = 0
    for l in open("/proc/net/dev").read().splitlines()[2:]:
        name, d = l.split(":", 1); d = d.split()
        if name.strip() == "lo": continue
        rx += int(d[0]); rxp += int(d[1]); tx += int(d[8]); txp += int(d[9])
    return {"cpu_total_s": total, "cpu_busy_s": total - idle, "ncpu": os.cpu_count(), "net_rx_B": rx, "net_tx_B": tx,
            "net_rx_pk": rxp, "net_tx_pk": txp, "load1": float(open("/proc/loadavg").read().split()[0])}

def snapshot(groups, extra=None):
    procs = {}
    for g in groups:
        pids = pids_named(*GROUPS[g]); procs[g] = {"cpu_s": proc_cpu(pids), "rss_mb": proc_rss_mb(pids), "n": len(pids)}
    if extra: procs.update(extra())
    return {"t": time.time(), "procs": procs, "host": host_counters()}

class Monitor(threading.Thread):
    """Publishes this machine's counters: a snapshot at every run boundary the harness writes, and one sample a second."""
    def __init__(self, ram, name, groups, extra=None):
        super().__init__(daemon=True); self.sp = T.Space(ram); self.name, self.groups, self.extra = name, groups, extra
    def run(self):
        sp = self.sp; after = max([0] + [c["id"] for c in sp.wait("perf|phase", 0, 0)]); seq = 0
        sp.write("perf|monitors", self.name, {"groups": self.groups, "host": socket.gethostname(), "t": time.time()})
        print("perf monitor '%s' (%s) publishing through the RAM server" % (self.name, ", ".join(self.groups) or "machine only"), flush=True)
        while True:
            for c in sp.wait("perf|phase", after, 1.0):
                after = max(after, c["id"])
                sp.write("perf|snap", "%s|%s" % (c["instance"], self.name), snapshot(self.groups, self.extra))
            seq += 1
            sp.write("perf|sample", "%s|%09d" % (self.name, seq), snapshot(self.groups, self.extra))

def run_monitor(a):
    groups = [g for g in (a.groups or "ram-server").split(",") if g]
    m = Monitor(a.ram, a.monitor, groups); m.start(); m.join()

# ------------------------------------------------------------------------------------------------ the systems' agent
def run_agent(a):
    """The acceptance agent, with this machine's monitor ("control") beside it."""
    os.makedirs(a.out, exist_ok=True)                 # the systems' own logs (Ribbit's front) go here
    Monitor(a.ram, "control", ["lispers.net", "ribbit-front"]).start()
    T.run_agent(a)

# ------------------------------------------------------------------------------------------------ load generation
def reg_packet(eid, mask, rlocs, nonce, ttl_min=1440):
    return T.register([(eid, mask, ttl_min, rlocs)], nonce)

def many_records_packet(recs, nonce):
    return T.register(recs, nonce)

class Worker(threading.Thread):
    """One closed-loop client: its own socket, one outstanding request, latency per answered operation."""
    def __init__(self, idx, local_ip, target, kind, pick, stop_at, measure_from, mix_req=0.8):
        super().__init__(daemon=True)
        self.idx, self.target, self.kind, self.pick = idx, target, kind, pick
        self.stop_at, self.measure_from, self.mix_req = stop_at, measure_from, mix_req
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.s.bind((local_ip, 0)); self.s.settimeout(1.0)
        self.local_ip = local_ip; self.lat = []; self.errors = self.unanswered = 0; self.flip = False
        self.nonce = (idx + 1) << 40
    def one(self):
        self.nonce += 1; n = self.nonce
        kind = self.kind if self.kind != "mixed" else ("req" if random.random() < self.mix_req else "reg")
        if kind == "reg":
            eid = "198.18.%d.%d" % (200 + self.idx // 250, self.idx % 250 + 1)        # this client's own EID
            self.flip = not self.flip
            pkt = reg_packet(eid, 32, [self.local_ip] if self.flip else [self.local_ip, "198.51.100.%d" % (self.idx % 250 + 1)], n)
            want = 4
        else:
            eid = self.pick()
            pkt = T.ecm(T.map_request(eid, n, self.local_ip), self.local_ip, eid, self.s.getsockname()[1]); want = 2
        t0 = time.perf_counter(); self.s.sendto(pkt, self.target); nb = struct.pack("=Q", n) if want == 2 else struct.pack("!Q", n)
        while True:
            try: d, _ = self.s.recvfrom(65535)
            except socket.timeout: return None, kind
            if len(d) >= 12 and (d[4:12] == struct.pack("!Q", n) or d[4:12] == struct.pack("=Q", n)):
                if d[0] >> 4 != want: return False, kind
                return time.perf_counter() - t0, kind
    def run(self):
        while time.time() < self.stop_at:
            r, kind = self.one()
            if time.time() < self.measure_from: continue
            if r is None: self.unanswered += 1
            elif r is False: self.errors += 1
            else: self.lat.append(r)

def pct(v, p):
    if not v: return None
    v = sorted(v); return v[min(len(v) - 1, int(p / 100.0 * len(v)))]

# ------------------------------------------------------------------------------------------------ the harness
class Harness:
    def __init__(self, a):
        self.a = a
        self.A, self.B = T.remote_systems(a)
        self.local_ip = a.local_ip
        self.sp = T.Space(a.ram)
        self.start_id = max([0] + [c["id"] for c in self.sp.wait("perf|sample", 0, 0)] + [c["id"] for c in self.sp.wait("perf|snap", 0, 0)])
        self.monitors = sorted(c["instance"] for c in self.sp.wait("perf|monitors", 0, 0))
        self.run_n = 0; self.run_tag = "%s.%d" % (socket.gethostname(), os.getpid()); self.snap_after = self.start_id
        self.phase_t = {}                                        # (run, phase) -> this machine's time
        self.load_snaps = {}
    def phase(self, run, ph):
        inst = "%s|%s" % (run, ph); self.phase_t[(run, ph)] = time.time()
        self.load_snaps[(run, ph)] = {"t": time.time(), "procs": {"load-generator": {"cpu_s": time.process_time(), "rss_mb": proc_rss_mb([os.getpid()]), "n": 1}}, "host": host_counters()}
        self.sp.write("perf|phase", inst, {"run": run, "phase": ph})
    def collect(self, run):
        """Every monitor's snapshots at this run's two boundaries."""
        want = {(m, ph) for m in self.monitors for ph in ("start", "end")}; got = {}; t0 = time.time()
        while want - set(got) and time.time() - t0 < 30:
            for c in self.sp.wait("perf|snap", self.snap_after, 5):
                self.snap_after = max(self.snap_after, c["id"])
                r, ph, m = c["instance"].split("|")
                if r == run: got[(m, ph)] = c["bag"]
        missing = sorted(want - set(got))
        if missing: print("  !! no snapshot from %s -- that machine's numbers are missing for this run" % missing, flush=True)
        return got
    def preload(self, s, eids):
        """Register the given (eid, mask) set, 40 records per Map-Register, and wait for every Map-Notify."""
        c = T.Client(self.local_ip, s.target)
        for i in range(0, len(eids), 40):
            recs = [(e, m, 1440, [self.local_ip]) for e, m in eids[i:i + 40]]
            n = c.next(); c.send(many_records_packet(recs, n)); d, _ = c.wait_for(n, 3.0)
            if d is None: raise RuntimeError("%s: preload Map-Register %d (of %d) not acknowledged" % (s.name, i // 40 + 1, (len(eids) + 39) // 40))
    def withdraw(self, s, eids):
        c = T.Client(self.local_ip, s.target)
        for i in range(0, len(eids), 40):
            n = c.next(); c.send(many_records_packet([(e, m, 0, []) for e, m in eids[i:i + 40]], n)); c.wait_for(n, 2.0)
    def run(self, s, kind, clients, pick, warm, dur, state):
        self.run_n += 1; run = "%s.%03d" % (self.run_tag, self.run_n); t0 = time.time()
        ws = [Worker(i, self.local_ip, s.target, kind, pick, t0 + warm + dur, t0 + warm) for i in range(clients)]
        for w in ws: w.start()
        time.sleep(max(0, t0 + warm - time.time())); self.phase(run, "start")
        for w in ws: w.join()
        self.phase(run, "end")
        snaps = self.collect(run)
        for ph in ("start", "end"): snaps[("load", ph)] = self.load_snaps[(run, ph)]
        lat = [x for w in ws for x in w.lat]; ok = len(lat)
        r = {"run": run, "system": s.name, "workload": kind, "clients": clients, "state": state, "ops": ok, "ops_s": ok / dur,
             "p50_ms": (pct(lat, 50) or 0) * 1e3, "p95_ms": (pct(lat, 95) or 0) * 1e3, "p99_ms": (pct(lat, 99) or 0) * 1e3,
             "errors": sum(w.errors for w in ws), "unanswered": sum(w.unanswered for w in ws), "duration_s": dur, "components": {}}
        for m in sorted({k[0] for k in snaps}):
            if (m, "start") not in snaps or (m, "end") not in snaps: continue
            a0, a1 = snaps[(m, "start")], snaps[(m, "end")]
            comp = {"host_busy_pct": 100.0 * (a1["host"]["cpu_busy_s"] - a0["host"]["cpu_busy_s"]) / max(1e-9, a1["host"]["cpu_total_s"] - a0["host"]["cpu_total_s"]),
                    "net_B_per_op": ((a1["host"]["net_rx_B"] - a0["host"]["net_rx_B"]) + (a1["host"]["net_tx_B"] - a0["host"]["net_tx_B"])) / ok if ok else None,
                    "net_pk_per_op": ((a1["host"]["net_rx_pk"] - a0["host"]["net_rx_pk"]) + (a1["host"]["net_tx_pk"] - a0["host"]["net_tx_pk"])) / ok if ok else None,
                    "procs": {}}
            for g, v in a1["procs"].items():
                if g not in a0["procs"]: continue
                d = v["cpu_s"] - a0["procs"][g]["cpu_s"]
                comp["procs"][g] = {"cpu_s": d, "cpu_s_per_M": d / ok * 1e6 if ok else None, "rss_mb": v["rss_mb"], "n": v["n"]}
            r["components"][m] = comp
        return r

def host_eids(n, base=(198, 18)):
    """n distinct host EIDs inside the accept-more-specifics site 198.18.0.0/16 (avoiding the reg clients' 198.18.200+)."""
    out = []
    for k in range(n):
        third, fourth = divmod(k, 254); out.append(("%d.%d.%d.%d" % (base[0], base[1], third % 200, fourth + 1), 32))
    return out

def nested_eids(n):
    """n registrations as nested prefixes: /20 over /24 over /28 over /32, so lookups exercise longest-prefix match."""
    out, k = [], 0
    for a20 in range(0, 200, 16):
        out.append(("198.18.%d.0" % a20, 20)); k += 1
        for a24 in range(a20, a20 + 16):
            out.append(("198.18.%d.0" % a24, 24)); k += 1
            for a28 in range(0, 256, 16):
                out.append(("198.18.%d.%d" % (a24, a28), 28)); k += 1
                out.append(("198.18.%d.%d" % (a24, a28 + 1), 32)); k += 1
                if k >= n: return out[:n]
    return out[:n]

def cost_cell(r, comp, group):
    c = r["components"].get(comp, {}).get("procs", {}).get(group)
    return "%.1f" % c["cpu_s_per_M"] if c and c["cpu_s_per_M"] is not None else "-"

def write_report(a, h, rows):
    json.dump(rows, open(os.path.join(a.out, "perf.json"), "w"), indent=1)
    with open(os.path.join(a.out, "PERF-REPORT.md"), "w") as f:
        f.write("# Performance -- lispers.net %s vs Ribbit-LISP %s\n\n%s. Load generator %s; monitors: %s; warmup %ss, measured %ss per run.\n\n"
                % (h.A.version, h.B.version, PERF_VERSION, h.local_ip, ", ".join(h.monitors) or "none", a.warmup, a.duration))
        f.write("## Throughput and latency (load generator)\n\n| system | workload | clients | state | ops/s | p50 ms | p95 ms | p99 ms | errors | unanswered |\n|---|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write("| %s | %s | %d | %d | %.0f | %.2f | %.2f | %.2f | %d | %d |\n" % (r["system"], r["workload"], r["clients"], r["state"], r["ops_s"], r["p50_ms"], r["p95_ms"], r["p99_ms"], r["errors"], r["unanswered"]))
        f.write("\n## Work per successful operation -- CPU-seconds per 1M operations, by component\n\n"
                "control = the systems' machine (lispers.net's lisp-* processes, or Ribbit's front); shared memory = the RAM server's machine; load = the generator.\n\n"
                "| system | workload | clients | state | control: lispers.net | control: ribbit-front | shared memory: ram-server | system total | load-gen |\n|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            tot = 0.0; parts = []
            for comp, g in (("control", "lispers.net"), ("control", "ribbit-front"), ("ram", "ram-server")):
                c = r["components"].get(comp, {}).get("procs", {}).get(g)
                if c and c["cpu_s_per_M"] is not None and (g != "lispers.net" or r["system"] == "lispers.net") and (g == "lispers.net" or r["system"] == "ribbit"): tot += c["cpu_s_per_M"]
            f.write("| %s | %s | %d | %d | %s | %s | %s | %.1f | %s |\n" % (r["system"], r["workload"], r["clients"], r["state"],
                    cost_cell(r, "control", "lispers.net"), cost_cell(r, "control", "ribbit-front"), cost_cell(r, "ram", "ram-server"), tot, cost_cell(r, "load", "load-generator")))
        f.write("\n## Machines -- CPU utilization and network per operation\n\n| system | workload | clients | state | control busy % | shared-memory busy % | load busy % | control B/op | shared-memory B/op | control pk/op | shared-memory pk/op | ram-server RSS MB |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        g = lambda r, m, k, fmt="%.1f": (fmt % r["components"][m][k]) if m in r["components"] and r["components"][m].get(k) is not None else "-"
        for r in rows:
            rss = r["components"].get("ram", {}).get("procs", {}).get("ram-server", {}).get("rss_mb")
            f.write("| %s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %s |\n" % (r["system"], r["workload"], r["clients"], r["state"],
                    g(r, "control", "host_busy_pct"), g(r, "ram", "host_busy_pct"), g(r, "load", "host_busy_pct"),
                    g(r, "control", "net_B_per_op", "%.0f"), g(r, "ram", "net_B_per_op", "%.0f"), g(r, "control", "net_pk_per_op", "%.2f"), g(r, "ram", "net_pk_per_op", "%.2f"),
                    "%.0f" % rss if rss is not None else "-"))
    timeline(a, h, rows)

def timeline(a, h, rows):
    """PERF-TIMELINE.html: CPU busy % per machine, one point a second, the measured runs shaded; each machine's clock
    lined up with the harness's by the run boundaries (both sides stamped the same boundary)."""
    samples = {}
    for c in h.sp.wait("perf|sample", h.start_id, 0):
        m, _ = c["instance"].split("|"); samples.setdefault(m, []).append(c["bag"])
    snaps = {}
    for c in h.sp.wait("perf|snap", h.start_id, 0):
        r, ph, m = c["instance"].split("|"); snaps[(r, ph, m)] = c["bag"]["t"]
    offs = {}
    for m in samples:
        d = sorted(h.phase_t[(r, ph)] - t for (r, ph, mm), t in snaps.items() if mm == m and (r, ph) in h.phase_t)
        offs[m] = d[len(d) // 2] if d else 0.0
    t0 = min([t for t in h.phase_t.values()] or [time.time()]) - 30
    W, H, PAD = 1400, 160, 40
    tmax = max([t for t in h.phase_t.values()] or [t0 + 60]) - t0 + 30
    X = lambda t: PAD + (t - t0) / tmax * (W - 2 * PAD)
    out = ['<!doctype html><meta charset="utf-8"><title>perf timeline</title><body style="font-family:sans-serif">',
           "<h2>Timeline -- CPU busy %% per machine (lispers.net %s vs Ribbit-LISP %s)</h2>" % (h.A.version, h.B.version)]
    for m in sorted(samples):
        pts = sorted(samples[m], key=lambda b: b["t"]); poly = []
        for p0, p1 in zip(pts, pts[1:]):
            dt = p1["host"]["cpu_total_s"] - p0["host"]["cpu_total_s"]
            if dt <= 0: continue
            busy = 100.0 * (p1["host"]["cpu_busy_s"] - p0["host"]["cpu_busy_s"]) / dt
            poly.append("%.1f,%.1f" % (X(p1["t"] + offs[m]), H - 10 - busy / 100.0 * (H - 30)))
        svg = ['<svg width="%d" height="%d" style="border:1px solid #ccc">' % (W, H)]
        for r in rows:
            s0, s1 = h.phase_t.get((r["run"], "start")), h.phase_t.get((r["run"], "end"))
            if s0 and s1:
                col = "#e8eefc" if r["system"] == "ribbit" else "#fcebe8"
                svg.append('<rect x="%.1f" y="0" width="%.1f" height="%d" fill="%s"><title>%s %s %d clients state %d: %.0f ops/s</title></rect>'
                           % (X(s0), max(1, X(s1) - X(s0)), H, col, r["system"], r["workload"], r["clients"], r["state"], r["ops_s"]))
        svg.append('<polyline fill="none" stroke="#333" stroke-width="1" points="%s"/>' % " ".join(poly))
        svg.append('<text x="5" y="14" font-size="12">%s (busy %%, 0-100; red runs lispers.net, blue Ribbit)</text></svg>' % m)
        out.append("<h3>%s</h3>%s" % (m, "".join(svg)))
    open(os.path.join(a.out, "PERF-TIMELINE.html"), "w").write("\n".join(out))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ram", required=True, help="HOST:PORT of the RAM server -- Ribbit's memory, and where every component meets")
    ap.add_argument("--agent", action="store_true"); ap.add_argument("--monitor", help="run as this machine's monitor, under this name (e.g. ram)")
    ap.add_argument("--groups", help="with --monitor: which process groups this machine has (default ram-server)")
    ap.add_argument("--local-ip"); ap.add_argument("--lispers-dir"); ap.add_argument("--lispers-api-port", type=int, default=8080)
    ap.add_argument("--lispers-env", action="append", default=[]); ap.add_argument("--ribbit-udp", type=int, default=14342)
    ap.add_argument("--ram-server"); ap.add_argument("--ram-port", type=int); ap.add_argument("--ribbit-front")
    ap.add_argument("--agent-name", default="perf"); ap.add_argument("--out", default="perf-out")
    ap.add_argument("--clients", default="1,4,16,64"); ap.add_argument("--warmup", type=float, default=5); ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--scale", default="1,100,1000,10000"); ap.add_argument("--quick", action="store_true", help="short runs, to check the setup")
    a = ap.parse_args()
    print("#" * 80 + "\n##  " + PERF_VERSION + "\n" + "#" * 80, flush=True)
    a.agent_space = a.ram; a.ram_external = a.ram; a.record = None; a.wire = None
    T.LOCAL_IP = a.local_ip
    if a.monitor: return run_monitor(a)
    if a.agent:
        if not a.lispers_dir: ap.error("--agent needs --lispers-dir")
        return run_agent(a)
    if a.quick: a.warmup, a.duration, a.clients, a.scale = 1, 3, "1,4", "1,100"
    os.makedirs(a.out, exist_ok=True)
    h = Harness(a); h.local_ip = a.local_ip
    print("load generator %s -> lispers.net %s:%d, Ribbit %s:%d; monitors publishing: %s" % (h.local_ip, h.A.target[0], h.A.target[1],
          h.B.target[0], h.B.target[1], ", ".join(h.monitors) or "NONE"), flush=True)
    for need in ("control", "ram"):
        if need not in h.monitors: print("  !! no '%s' monitor is publishing: its numbers will be missing (see the usage)" % need, flush=True)
    clients = [int(x) for x in a.clients.split(",")]; scale = [int(x) for x in a.scale.split(",")]
    rows = []
    def show(r):
        c = r["components"]
        print("  %-11s %-11s %3d cl %6d st %9.0f ops/s  p50 %6.2f p95 %6.2f p99 %6.2f ms  err %d unans %d | CPU s/1M: control %s ram %s load %s"
              % (r["system"], r["workload"], r["clients"], r["state"], r["ops_s"], r["p50_ms"], r["p95_ms"], r["p99_ms"], r["errors"], r["unanswered"],
                 cost_cell(r, "control", "lispers.net" if r["system"] == "lispers.net" else "ribbit-front"), cost_cell(r, "ram", "ram-server"),
                 cost_cell(r, "load", "load-generator")), flush=True)
        rows.append(r)
    for s in (h.A, h.B):
        print("== %s: starting fresh" % s.name, flush=True); s.start()
        base = host_eids(1000); h.preload(s, base); pick = lambda: random.choice(base)[0]
        for kind in ("reg", "req", "mixed"):
            for n in clients:
                print("  .. %s %s, %d clients (%gs warmup, %gs measured)" % (s.name, kind, n, a.warmup, a.duration), flush=True)
                show(h.run(s, kind, n, pick, a.warmup, a.duration, len(base)))
        h.withdraw(s, base)
        for label, gen in (("host", host_eids), ("nested", nested_eids)):
            for n in scale:
                eids = gen(n); print("  .. %s scale (%s prefixes): preloading %d registrations" % (s.name, label, n), flush=True)
                h.preload(s, eids)
                if label == "host": pick = (lambda e=eids: random.choice(e)[0])
                else:
                    def pick(e=eids):
                        p, m = random.choice(e); o = [int(x) for x in p.split(".")]
                        if m < 32:
                            span = 1 << (32 - m); v = ((o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]) + random.randrange(span)
                            o = [(v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255]
                        return "%d.%d.%d.%d" % tuple(o)
                r = h.run(s, "req", 16, pick, a.warmup, a.duration, n); r["workload"] = "req-" + label; show(r)
                h.withdraw(s, eids)
        s.stop()
    write_report(a, h, rows)
    print("report: %s  (numbers: perf.json; timeline: PERF-TIMELINE.html)" % os.path.join(a.out, "PERF-REPORT.md"), flush=True)

if __name__ == "__main__":
    sys.exit(main())
