#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_dist.py - [S4_DIST_V1] the layered run (s4_layer: real IS-IS below, shared RAM above) across machines -- Seattle
and New York. Nothing connects the machines but the bed itself (its hub and ring links: VXLAN between the machines)
and the experiment's CONTROL memory, a RAM host of its own (`day-claims host`), which carries every instruction and
every result:

  agent      on every machine: advertises itself in "agents" (its address toward the control host, its arch); holds
             a read on "plan"; does each step for the namespaces placed on it and acknowledges it in "acks"; publishes
             its nodes' kernel tables in "tables" whenever a table changes (ip monitor: route, nexthop, link), and its
             applications' views in "views" whenever a reader says something. No polling anywhere.
  controller on one machine: holds a read on "agents" until N machines are there; plans the bed over them; writes each
             step as its own record in "plan"; holds reads on "acks", "tables" and "views" for the outcome. The only
             route-following is its verification walk over the tables the agents published.

Timing is on ONE clock, the controller's: from writing a step to reading the record that satisfies it. Each figure
therefore includes one trip through the control memory (Seattle - New York: tens of ms); that is said with the results.

usage: s4_dist.py agent CTL_HOST[:PORT] [NAME]
       s4_dist.py run CTL_HOST[:PORT] --machines N [--profile tuned|default] [--S 20] [--E 2] [--ring] [--steady 30]
                  [--settle 15] [--timeout 240] [--out DIR]
env:   S4_DAF_AGENT (daf-agent), FRR_BIN, S4_RAM_BIN, S4_FDF (as s4_layer)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_bed as B          # noqa: E402

C = "s4ctl#1"
NAMES = ("agents", "plan", "acks", "tables", "views")
ARCH = os.uname().machine
PKG = "/opt/fdf/experiments/day-claims"


def where(s):
    return s if ":" in s else s + ":52500"


class Daf:
    """One FNW1 session to the control memory (a daf-agent process): one call at a time; a held read blocks it."""
    def __init__(self, ctl, apn):
        exe = os.environ.get("S4_DAF_AGENT", f"{PKG}/bin/{ARCH}/daf-agent")
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                  env=dict(os.environ, RINA_RAM=ctl))
        self.mu = threading.Lock()
        self.call(fn="create_daf", dan=C.split("#")[0], daii="1", owner="s4")        # the first claimant creates it
        self.must(fn="enroll", daf=C, apn=apn)
        for n in NAMES:
            self.call(fn="define_name", daf=C, name=n, rule="all")

    def call(self, **kw):
        with self.mu:
            self.p.stdin.write(json.dumps(kw, separators=(",", ":")) + "\n")   # daf-agent spots a held read by "fn":"wait"
            self.p.stdin.flush()
            line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("the session to the control memory ended (daf-agent exited)")
        return json.loads(line)

    def must(self, **kw):
        r = self.call(**kw)
        if not r.get("ok"):
            raise RuntimeError(f"{kw.get('fn')} refused: {r.get('error')} {r.get('why')}")
        return r

    def join(self, name, facts):
        return self.must(fn="join", daf=C, name=name, facts=facts)

    def subscribe(self, *names):
        return self.must(fn="subscribe", daf=C, names=list(names))["sub"]

    def wait(self, sub, after, now=False):
        """Held: the subscription's data once something in it is past `after`. -> (rev, {name: [members]})."""
        kw = dict(fn="wait", daf=C, what="sub", key=sub, after=after)
        if now:
            kw["now"] = True
        r = self.must(**kw)
        return int(r["rev"]), r["rows"][0]["v"]["data"]

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(5)
        except Exception:
            self.p.kill()


# ================================================================ agent
def my_addr_toward(host):
    r = subprocess.run(["ip", "-4", "route", "get", host.split(":")[0]], capture_output=True, text=True).stdout.split()
    return r[r.index("src") + 1] if "src" in r else ""


def diag(st):
    """[DIAG-S4-RAM] every node here, as the region sees it from inside the node: the route to the RAM manager, a TCP
    connect to it, the last lines of its reader, fdf-client and fdf-facts; on the hub, rina-host's state and log."""
    import s4_layer as L
    out = {}
    bed, work = st["bed"], st["work"]
    for n in bed.mine:
        d = os.path.join(work, n)
        tail = lambda f, k=3: (open(os.path.join(d, f)).read().strip().splitlines()[-k:] if os.path.exists(os.path.join(d, f)) else ["(no %s)" % f])
        rt = B.sh("ip", "-4", "route", "get", L.RAM_IP, ns=n, check=False)
        cn = B.sh("timeout", "4", "bash", "-c", f"exec 3<>/dev/tcp/{L.RAM_IP}/{L.RAM_PORT} && echo connected", ns=n, check=False)
        out[n] = {"route": (rt.stdout or rt.stderr).strip().splitlines()[:1], "connect": (cn.stdout + cn.stderr).strip()[-160:] or "rc=%d" % cn.returncode,
                  "reader": tail("reader.log"), "client": tail("fdf-client.log"), "facts": tail("fdf-facts.log", 2)}
        if n == "hub":
            pids = B.sh("ip", "netns", "pids", B.NS + n, check=False).stdout.split()
            comms = sorted({open(f"/proc/{p}/comm").read().strip() for p in pids if os.path.exists(f"/proc/{p}/comm")})
            out[n].update(processes=comms, host=tail("rina-host.log", 6),
                          listen=B.sh("ss", "-H", "-lnt", ns=n, check=False).stdout.strip().splitlines())
    return out


def agent(ctl, name):
    import s4_isis
    import s4_layer as L
    me = "a-" + (name or os.uname().nodename)
    w, r = Daf(ctl, me), Daf(ctl, me + ".r")
    ip = os.environ.get("S4_IP") or my_addr_toward(ctl)      # the address the bed's VXLAN links use here
    w.join("agents", {"ip": ip, "arch": ARCH, "cores": os.cpu_count(), "host": os.uname().nodename})
    print(f"s4-dist agent {me} ({ip}, {ARCH}) on {ctl}: advertised; holding a read on the plan", flush=True)
    sub = r.subscribe("plan")
    rev, done = 0, set()
    st = {}                                                  # this run's bed, watcher, arms

    def publish_tables():
        bed, wt = st["bed"], st["watcher"]
        while not st.get("ending"):
            with wt.cv:
                while not wt.dirty and not st.get("ending"):
                    wt.cv.wait(1.0)                          # bounded only so an ended run is noticed; no table is read here
                d, wt.dirty = wt.dirty, set()
            if st.get("ending"):
                return
            time.sleep(0.02)                                 # a burst of changes lands as one record
            with wt.cv:
                d |= wt.dirty
                wt.dirty = set()
            for n in d:
                st["tables"][n] = {k: f"{v[0]}|{v[1]}" for k, v in bed.routes(n).items()}
            w.join("tables", {"run": st["run"], "t": dict(st["tables"])})

    def publish_views():
        reg = st["reg"]
        seen = 0
        while not st.get("ending"):
            with reg.cv:
                while len(reg.lines) == seen and len(reg.restarts) == st.get("restarts_seen", 0) and not st.get("ending"):
                    reg.cv.wait(1.0)
                seen = len(reg.lines)
                st["restarts_seen"] = len(reg.restarts)
                view = {n: {k: d.get(k) for k in ("ev", "alive", "with_facts", "why", "error") if k in d} for n, d in reg.view.items()}
                restarts = [[n, nm] for _t, n, nm, _rc in reg.restarts]
            if st.get("ending"):
                return
            w.join("views", {"run": st["run"], "v": view, "restarts": restarts, "lines": seen})

    while True:
        rev, data = r.wait(sub, rev, now=(rev == 0))
        for m in data.get("plan", []):
            f = m.get("facts") or {}
            key = f"{f.get('run')}/{f.get('step')}"
            if not m.get("live") or not f or key in done:
                continue
            done.add(key)
            act, out, err = f["action"], {}, ""
            try:
                if "roster" in f and me not in f["roster"]:
                    # [S4_NOT_IN_THIS_RUN_V1] the roster is the authority on EVERY step, not only build: an agent that
                    # advertised after build was issued still knows at once it is not one of this run's machines, so it
                    # never builds, counts, or acks an error into a run it has no bed for.
                    if not st.get("idle") or st.get("run") != f["run"]:
                        st.clear(); st.update(run=f["run"], idle=True)
                        print(f"s4-dist agent {me}: run {f['run']} is over other machines; idle for it", flush=True)
                    out = {"nodes": [], "note": "not one of this run's machines"}
                elif act == "build":
                    for k in ("reg", "isis"):
                        if st.get(k):
                            st[k].stop()
                    if st.get("bed"):
                        st["ending"] = True
                        st["watcher"].close()
                        st["bed"].teardown()
                    p = f["plan"]
                    work = tempfile.mkdtemp(prefix=f"s4-dist-{f['run']}-", dir=f.get("workdir") or "/tmp")
                    bed = B.Bed(p, me).build()
                    st.clear()
                    st.update(run=f["run"], bed=bed, work=work, tables={}, watcher=B.Watcher(bed))
                    L.count_ram(bed)
                    threading.Thread(target=publish_tables, daemon=True).start()
                    out = {"nodes": bed.mine, "work": work}
                elif act == "isis":
                    st["isis"] = s4_isis.Isis(st["bed"], os.path.join(st["work"], "isis"), f["profile"],
                                              os.environ.get("FRR_BIN", f"{PKG}/isis/bin/{ARCH}"))
                    st["isis"].start()
                elif act == "region":
                    st["reg"] = L.Region(st["bed"], st["work"], os.environ.get("S4_RAM_BIN", f"{PKG}/scale/bin/{ARCH}"),
                                         os.environ.get("S4_FDF", f"{PKG}/s4/fdf"))
                    if "hub" in st["bed"].mine:
                        st["reg"].start_host()
                    threading.Thread(target=publish_views, daemon=True).start()
                elif act == "members":
                    st["reg"].start_members()
                elif act == "silence":
                    for n, dev, on in f["links"]:
                        if n in st["bed"].mine:
                            st["bed"].silence(n, dev, on)
                elif act == "count":
                    out = {"wire": B.wire_bytes(st["bed"])[0], "ram": L.ram_bytes(st["bed"])}
                elif act == "diag":
                    out = diag(st)
                elif act == "usage":
                    out = {"isis": st["isis"].usage() if st.get("isis") else {}}
                elif act == "end":
                    st["ending"] = True
                    for k in ("reg", "isis"):
                        if st.get(k):
                            st[k].stop()
                    if st.get("watcher"):
                        st["watcher"].close()
                    if st.get("bed"):
                        st["bed"].teardown()
                    st.clear()
                    print(f"s4-dist agent {me}: run {f['run']} ended", flush=True)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                print(f"s4-dist agent {me}: step {key} {act} failed: {err}", flush=True)
            w.join("acks", {"run": f["run"], "step": f["step"], "ok": not err, "error": err, "out": out})


# ================================================================ controller
class Controller:
    def __init__(self, ctl, n_machines, timeout):
        self.run = "%x" % int(time.time())
        self.timeout = timeout
        self.w = Daf(ctl, "controller-" + self.run)
        self.ra = Daf(ctl, "controller-" + self.run + ".acks")
        self.rt = Daf(ctl, "controller-" + self.run + ".tables")
        self.rv = Daf(ctl, "controller-" + self.run + ".views")
        self.k = 0
        # who is there: a held read on agents until N
        sub = self.ra.subscribe("agents")
        rev, data = self.ra.wait(sub, 0, now=True)
        while len([m for m in data.get("agents", []) if m.get("live")]) < n_machines:
            print(f"s4-dist: {len([m for m in data.get('agents', []) if m.get('live')])} of {n_machines} agents; "
                  "holding a read on agents", flush=True)
            rev, data = self.ra.wait(sub, rev)
        self.agents = sorted((m["apn"], m["facts"]) for m in data["agents"] if m.get("live"))[:n_machines]
        self.roster = {a for a, _ in self.agents}           # the run's machines; a stray agent's records are ignored
        self.asub = self.ra.subscribe("acks")
        self.arev = 0
        self.tsub, self.trev, self.tables = self.rt.subscribe("tables"), 0, {}
        self.vsub, self.vrev, self.views = self.rv.subscribe("views"), 0, {}
        self.vlog = []                                      # (controller time, node, view) for every view change seen
        self.restarts = {}                                  # agent -> [[node, name], ...] so far
        self.tcv = threading.Condition()
        threading.Thread(target=self._tables, daemon=True).start()
        threading.Thread(target=self._views, daemon=True).start()

    def _tables(self):
        while True:
            rev, data = self.rt.wait(self.tsub, self.trev, now=(self.trev == 0))
            merged = {}
            for m in data.get("tables", []):
                f = m.get("facts") or {}
                if m.get("live") and f.get("run") == self.run and m["apn"] in self.roster:
                    for n, t in f["t"].items():
                        merged[n] = {k: tuple(v.split("|", 1)) for k, v in t.items()}
            with self.tcv:
                self.trev, self.tables = rev, merged
                self.tcv.notify_all()

    def _views(self):
        while True:
            rev, data = self.rv.wait(self.vsub, self.vrev, now=(self.vrev == 0))
            now = time.time()
            with self.tcv:
                self.vrev = rev
                for m in data.get("views", []):
                    f = m.get("facts") or {}
                    if not (m.get("live") and f.get("run") == self.run and m["apn"] in self.roster):
                        continue
                    self.restarts[m["apn"]] = f.get("restarts", [])
                    for n, v in f["v"].items():
                        if self.views.get(n) != v:
                            self.views[n] = v
                            self.vlog.append((now, n, v))
                self.tcv.notify_all()

    def step(self, action, **kw):
        """Write the step into the plan; held read on acks until every agent has acknowledged it."""
        self.k += 1
        t0 = time.time()
        self.w.join("plan", dict(run=self.run, step=self.k, action=action, roster=sorted(self.roster), **kw))
        want = self.roster
        while True:
            self.arev, data = self.ra.wait(self.asub, self.arev, now=(self.arev == 0))
            got = {}
            for m in data.get("acks", []):
                f = m.get("facts") or {}
                if m.get("live") and f.get("run") == self.run and f.get("step") == self.k and m["apn"] in self.roster:
                    got[m["apn"]] = f
            bad = [f"{a}: {f['error']}" for a, f in got.items() if not f["ok"]]
            if bad:
                raise RuntimeError(f"step {self.k} {action}: " + "; ".join(bad))
            if want <= set(got):
                return {a: got[a]["out"] for a in want}, time.time() - t0
            if time.time() - t0 > self.timeout:
                raise RuntimeError(f"step {self.k} {action}: no ack from {sorted(want - set(got))} in {self.timeout} s")

    def wait(self, test, timeout):
        t_end = time.time() + timeout
        with self.tcv:
            while not test():
                left = t_end - time.time()
                if left <= 0:
                    return False
                self.tcv.wait(left)
            return True


def run(a):
    ctl = Controller(where(a.ctl), a.machines, a.timeout)
    machines = [{"name": apn, "ip": f["ip"]} for apn, f in ctl.agents]
    p = B.plan(a.S, a.E, machines, ring=a.ring, mtu=a.mtu)
    own = B.owners(p)
    names = sorted(p["nodes"])
    cut = {n for n, d in p["nodes"].items() if n == "e1" or d.get("egress") == "e1"}
    res = {"exp": "S4-layered-dist", "run": ctl.run, "profile": a.profile, "S": a.S, "E": a.E, "ring": a.ring,
           "mtu": a.mtu, "machines": machines, "placement": {m["name"]: sorted(n for n, x in p["placement"].items()
                                                                              if x == m["name"]) for m in machines}}
    print(f"s4-dist: run {ctl.run} over {[m['name'] for m in machines]}; hub and e1 on {p['placement']['hub']}",
          flush=True)
    silenced = set()

    def walks(cutset=frozenset()):
        ok = tot = 0
        bad = []
        served = {d["served"]: n for n, d in p["nodes"].items() if d.get("served")}
        for src in p["nodes"]:
            for net, dst in served.items():
                if dst == src:
                    continue
                tot += 1
                path, v = B.walk(p, ctl.tables, src, net, own, frozenset(silenced))
                good = (v == "NO ROUTE") if (src in cutset) != (dst in cutset) else (v == "ok")
                ok += good
                if not good and len(bad) < 5:
                    bad.append(f"{src}->{net}: {v} {'>'.join(path)}")
        return ok, tot, bad

    def counts():
        out, _ = ctl.step("count")
        return sum(v["wire"] for v in out.values()), sum(v["ram"] for v in out.values())

    full = lambda n, d: d.get("with_facts") == names
    try:
        _, dt = ctl.step("build", plan=p, workdir=a.workdir)
        res["build_s"] = round(dt, 2)
        w0, _ = counts()
        t0 = time.time()
        ctl.step("isis", profile=a.profile)
        ok = ctl.wait(lambda: walks()[0] == walks()[1], a.timeout)
        res["isis_cold_s"] = round(time.time() - t0, 2) if ok else None
        res["isis_cold_bad"] = walks()[2]
        w1, _ = counts()
        res["isis_cold_bytes"] = w1 - w0
        time.sleep(a.steady)
        w2, r2 = counts()
        res["isis_steady_bytes"] = w2 - w1
        t1 = time.time()
        ctl.step("region")
        ctl.step("members")
        ok = ctl.wait(lambda: all(full(n, ctl.views.get(n, {})) for n in names), a.timeout)
        res["ram_cold_s"] = round(time.time() - t1, 2) if ok else None
        if not ok:
            # [DIAG-S4-RAM] the applications never all saw the region: why, from inside every node, in the evidence
            dg, _ = ctl.step("diag")
            res["ram_cold_diag"] = dg
            print("[DIAG-S4-RAM] the region was not ready on every node in %d s; views now: %s" % (a.timeout, {
                n: (v.get("ev"), len(v.get("with_facts") or []), v.get("why") or v.get("error")) for n, v in sorted(ctl.views.items())}), flush=True)
            for ag, nodes in dg.items():
                for n, d in sorted(nodes.items()):
                    print("[DIAG-S4-RAM] %s %s: route %s | connect %s | reader %s | client %s" % (ag, n, d["route"], d["connect"],
                          d["reader"][-1:], d["client"][-1:]), flush=True)
                    if n == "hub":
                        print("[DIAG-S4-RAM] %s hub processes %s listen %s" % (ag, d.get("processes"), d.get("listen")), flush=True)
                        for l in d.get("host", []):
                            print("[DIAG-S4-RAM] %s hub rina-host: %s" % (ag, l[:200]), flush=True)
        w3, r3 = counts()
        res["ram_cold_bytes"] = r3 - r2
        time.sleep(a.steady)
        w4, r4 = counts()
        res.update(steady_s=a.steady, steady_bytes_all=w4 - w3, steady_bytes_ram=r4 - r3)

        def phase(tag, off, on, cutset, want):
            wb, rb = counts()
            with ctl.tcv:
                before = {k: len(v) for k, v in ctl.restarts.items()}
            ts = time.time()
            ctl.step("silence", links=[[n, d, True] for n, d in off] + [[n, d, False] for n, d in on])
            silenced.update(off)
            silenced.difference_update(on)
            ok = ctl.wait(lambda: walks(cutset)[0] == walks(cutset)[1], a.timeout)
            res[f"{tag}_isis_s"] = round(time.time() - ts, 2) if ok else None
            okn, tot, bad = walks(cutset)
            res[f"{tag}_isis_ok"] = f"{okn}/{tot}"
            if bad:
                res[f"{tag}_isis_bad"] = bad
            ctl.wait(lambda: all(want(n, ctl.views.get(n, {})) for n in names), a.timeout)
            time.sleep(a.settle)
            with ctl.tcv:
                log = [(t, n, v) for t, n, v in ctl.vlog if t >= ts]
                ended = [x for ag, v in ctl.restarts.items() for x in v[before.get(ag, 0):]]
                final_ok = all(want(n, ctl.views.get(n, {})) for n in names)
            disrupted, settled = set(), {}
            for t, n, v in log:
                if want(n, v):
                    settled.setdefault(n, t)
                else:
                    disrupted.add(n)
                    settled.pop(n, None)
            res[f"{tag}_apps_disrupted"] = sorted(disrupted)
            res[f"{tag}_apps_settled_s"] = round(max([settled.get(n, ts) for n in names]) - ts, 2) if final_ok else None
            res[f"{tag}_apps_ok_at_end"] = final_ok
            res[f"{tag}_client_sessions_ended"] = sorted(n for n, nm in ended if nm == "fdf-client")
            res[f"{tag}_views_ended"] = sorted(n for n, nm in ended if nm == "reader")
            we, re_ = counts()
            res[f"{tag}_bytes_all"], res[f"{tag}_bytes_ram"] = we - wb, re_ - rb

        hub_link = [("e1", "hl0"), ("hub", "hl1")]
        ring = [(l[s][0], l[s][1]) for l in p["links"] if l["kind"] == "ring" and "e1" in (l["a"][0], l["b"][0])
                for s in ("a", "b")]
        if a.ring:
            phase("reroute", hub_link, [], frozenset(), full)
            phase("restore", [], hub_link, frozenset(), full)
        far, near = sorted(set(names) - cut), sorted(cut)

        def partitioned(n, d):
            if n in far:
                return d.get("alive") == far
            return d.get("ev") == "gone" or (bool(d) and set(d.get("alive", [])) <= set(near))
        phase("partition", hub_link + ring, [], frozenset(cut), partitioned)
        phase("heal", [], hub_link + ring, frozenset(), full)
        u, _ = ctl.step("usage")
        rss = sorted(v[1] for x in u.values() for v in x["isis"].values())
        res["isis_rss_kb_p50"] = rss[len(rss) // 2] if rss else None
    finally:
        try:
            ctl.step("end")
        except Exception as e:
            print(f"s4-dist: end step: {e}", flush=True)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"s4-dist-{a.profile}-S{a.S}-E{a.E}{'-ring' if a.ring else ''}-{ctl.run}.json")
    json.dump(res, open(path, "w"), indent=1)
    print(json.dumps(res, indent=1))
    print(f"s4-dist: evidence {path}", flush=True)
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "agent":
        return agent(where(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("ctl")
    ap.add_argument("--machines", type=int, required=True)
    ap.add_argument("--profile", default="tuned")
    ap.add_argument("--S", type=int, default=20)
    ap.add_argument("--E", type=int, default=2)
    ap.add_argument("--ring", action="store_true")
    ap.add_argument("--mtu", type=int, default=1350)
    ap.add_argument("--steady", type=float, default=30)
    ap.add_argument("--settle", type=float, default=15)
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--out", default=f"{PKG}/runs")
    ap.add_argument("--workdir", default=None, help="where each agent keeps its run's files (default /tmp)")
    a = ap.parse_args()
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
