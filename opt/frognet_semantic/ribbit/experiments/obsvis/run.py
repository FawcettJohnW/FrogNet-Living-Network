#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run.py COND TRAFFIC SEED [--minutes M] [--stress NAME] [--out DIR] -- one run of the observer-visibility experiment,
# as root. It brings the bed up, starts the control region (a chat-ram of its own in ov-ctl, 10.77.30.2:8899, left out
# of every capture) and one agent in every namespace (agent.py), then moves itself into ov-ctl and is the controller:
# everything from there goes through the control region (ribbit::Space: its own cells written, standing
# follows on the agents' families -- no polling, no waiting on a process).
#   1 a standing follow on (obsvis, adv) until p1..p8, srv and rt have advertised themselves
#   2 (obsvis, ctl, prepare) the plan: condition, traffic, seed, duration, follow graph, who captures; a follow on
#     (obsvis, ready) until every agent has said it is prepared (srv: its server listening; p1, srv: capturing)
#   3 (obsvis, ctl, go) t0; the stress steps at their offsets, each (obsvis, ctl, step-N), answered on (obsvis, ready);
#     after a server change, (obsvis, ctl, server) tells the participants a fresh one is there
#   4 the follow on (obsvis, result) for p2..p8 and (obsvis, ready, p1.run); then (obsvis, ctl, finish) and the follow
#     on (obsvis, result) for p1, srv and rt; each result names its data tuples, read one by one
#   5 (obsvis, ctl, done): the agents exit; the bed comes down
# Everything that came back lands in DIR (default results/<run id>/): <agent>/<kind>.json (truth, o3, o4, packets,
# mosquitto), controller.json (the plan, every result, server CPU and peak RSS, link counters, stress events), run.json
# (versions), and the raw captures cap-p1.pcap (O1) and cap-srv.pcap (O2).
#
#   COND     A | B | C | D | Dall          TRAFFIC  chat | telemetry
#   --stress netem-delay | netem-loss | server | move | revoke
import argparse, json, os, platform, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bed, workload  # noqa: E402

BUILD = "obsvis-2026-11-06-r3"
CONDS = ["A", "B", "C", "D", "Dall"]
AGENTS = workload.PARTS + ["srv", "rt"]
STRESS = {
    "netem-delay": {"netem": "delay 100ms"},
    "netem-loss": {"netem": "loss 2%"},
    "server": {"events": [{"at": 0.5, "do": "server"}]},              # at: a fraction of the duration
    "move": {"events": [{"at": 0.5, "do": "move", "who": ["p6", "p7"]}]},
    "revoke": {"events": [{"at": 0.5, "do": "revoke", "who": ["p8"]}]},
}


def out_of(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.stdout.strip() or r.stderr.strip()).splitlines()[0]
    except Exception as e:
        return "(%s)" % e


def versions():
    return {"harness": BUILD, "bed": bed.BUILD, "kernel": platform.release(), "python": sys.version.split()[0],
            "chat-ram": out_of([os.path.join(HERE, "build", "chat-ram"), "--version"]), "mosquitto": out_of(["mosquitto", "-h"]),
            "wireguard-go": out_of(["wireguard-go", "--version"]), "tcpdump": out_of(["tcpdump", "--version"]),
            "openssl": out_of(["openssl", "version"])}


def certs(d):
    os.makedirs(d, exist_ok=True)
    q = dict(capture_output=True, check=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2",
                    "-subj", "/CN=obsvis-ca", "-keyout", d + "/ca.key", "-out", d + "/ca.crt"], **q)
    for n in workload.PARTS + ["srv"]:
        subprocess.run(["openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=" + n,
                        "-keyout", "%s/%s.key" % (d, n), "-out", "%s/%s.csr" % (d, n)], **q)
        open("%s/%s.ext" % (d, n), "w").write("subjectAltName=DNS:%s,IP:%s\n" % (n, bed.ADDR(n)))
        subprocess.run(["openssl", "x509", "-req", "-in", "%s/%s.csr" % (d, n), "-CA", d + "/ca.crt", "-CAkey", d + "/ca.key",
                        "-CAcreateserial", "-days", "2", "-extfile", "%s/%s.ext" % (d, n), "-out", "%s/%s.crt" % (d, n)], **q)


def start(ns, argv, log, ready=None):
    """a process in a namespace, its output to its log; ready when a line of its output says so"""
    f = open(log, "w"); ev = threading.Event(); tail = []
    p = subprocess.Popen(["ip", "netns", "exec", bed.NS(ns)] + argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    def rd():
        for l in p.stdout:
            f.write(l); f.flush(); tail.append(l)
            if ready and ready in l: ev.set()
        ev.set()
    threading.Thread(target=rd, daemon=True).start()
    if ready and (not ev.wait(30) or p.poll() is not None):
        raise RuntimeError("%s did not say %r: %s" % (argv[0], ready, "".join(tail[-5:])))
    return p


class Ctl:
    """the controller's side of the control region: its own words written, every answer gathered by a standing follow"""
    def __init__(self):
        import ram
        self.ram = ram
        self.s = ram.Space(bed.CTL + ":8899")
        self.fam = {v: ram.Collect(self.s, "obsvis", v) for v in ("adv", "ready", "result")}

    def say(self, word, value):
        self.s.write("obsvis", "ctl", word, value)

    def collect(self, var, names, until, got=None):
        """the family's instances until every name in names is there (or `until` passes); woken only by its follow"""
        g = self.fam[var].wait(names, until)
        if got is not None: got.update(g); return got
        return g

    def fetch(self, chunks):
        data = {}
        for var in chunks:
            rows = [r for r in self.s.read("obsvis", var, "data") if r["bag"]]
            if not rows:
                raise RuntimeError("data tuple %s is not there" % var)
            v = rows[-1]["bag"]; data.setdefault(v["k"], []).extend(v["items"])   # "items": a bag field named "rows" comes back wrapped (semtpl rebuild_json)
        return data


def control(plan, out):
    c = Ctl()
    print("[ctl] controller of run %s, in ov-ctl" % plan["run"], flush=True)
    have = c.collect("adv", AGENTS, time.time() + 120)
    if set(have) != set(AGENTS):
        raise RuntimeError("only %s advertised in 120 s" % sorted(have))
    print("[ctl] advertised: %s" % ", ".join("%s %s" % (k, have[k]["build"]) for k in AGENTS), flush=True)
    c.say("prepare", plan)
    ready = c.collect("ready", AGENTS, time.time() + 120)
    bad = [k for k in AGENTS if not (ready.get(k) or {}).get("ok")]
    if bad:
        raise RuntimeError("not prepared: %s" % bad)
    t0 = time.time() + 3.0
    c.say("go", {"t0": t0})
    print("[ctl] go: t0 %.3f, %.0f s" % (t0, plan["dur"]), flush=True)
    events = []

    def timeline():
        s = c
        for n, ev in enumerate(plan["stress"]):
            d = t0 + ev["at"] - time.time()
            if d > 0: time.sleep(d)                            # the schedule's own time, not a wait on anyone
            w = "step-%d" % n; t = time.time()
            s.say(w, ev)
            who = ["srv"] if ev["do"] in ("server", "revoke") else ev["who"]
            got = s.collect("ready", ["%s.%s" % (x, w) for x in who], time.time() + 60)
            if ev["do"] == "server":
                s.say("server", {"epoch": n + 1})
            events.append(dict(ev, word=w, t=t, answered=got))
            print("[ctl] stress %s at t0+%.1f s: %s" % (ev["do"], t - t0, sorted(got)), flush=True)
    th = threading.Thread(target=timeline, daemon=True); th.start()
    until = t0 + plan["dur"] + plan["drain"] + 180
    res = c.collect("result", [p for p in workload.PARTS if p != "p1"], until)
    c.collect("ready", ["p1.run"], until, ready)
    th.join(max(0.0, until - time.time()))
    c.say("finish", {})
    res = c.collect("result", AGENTS, time.time() + 300, res)
    missing = [k for k in AGENTS if k not in res]
    for k, v in res.items():
        d = os.path.join(out, k); os.makedirs(d, exist_ok=True)
        for kind, rows in c.fetch(v.get("chunks", [])).items():
            json.dump(rows, open(os.path.join(d, kind + ".json"), "w"))
    # a baseline run is ok when every agent reported without an error; under stress the errors ARE what is measured
    # (a revoked participant's reads fail; a server change ends every held read): there, every agent reporting is ok
    ok = not missing and (bool(plan["stress"]) or all(v.get("ok") for v in res.values()))
    json.dump({"plan": plan, "t0": t0, "advertised": have, "results": res, "missing": missing, "stress_events": events, "ok": ok,
               "server_usage": (res.get("srv") or {}).get("server_usage"), "links": (res.get("rt") or {}).get("links")},
              open(os.path.join(out, "controller.json"), "w"), indent=1)
    c.say("done", {"ok": ok})
    print("[ctl] %s: errors %s%s" % ("ok" if ok else "FAILED", {k: v.get("n_err") for k, v in sorted(res.items())},
                                     ", no result from %s" % missing if missing else ""), flush=True)
    return ok


def run(cond, traffic, seed, minutes, stress=None, out=None):
    rid = "%s-%s-s%d%s" % (cond, traffic, seed, "-" + stress if stress else "")
    out = os.path.abspath(out or os.path.join(HERE, "results", rid))
    os.makedirs(out, exist_ok=True)
    st = STRESS.get(stress, {}); dur = minutes * 60.0
    print("[run] %s: %s (%.1f min)%s -> %s" % (BUILD, rid, minutes, ", stress " + stress if stress else "", out), flush=True)
    json.dump(versions(), open(os.path.join(out, "run.json"), "w"), indent=1)
    ok, procs = False, []
    home = os.open("/proc/self/ns/net", os.O_RDONLY)          # this process's own network, to come back to
    try:
        bed.up(wg=cond in ("C", "D", "Dall"), netem=st.get("netem"))
        cdir = os.path.join(out, "certs")
        if cond in ("A", "B"): certs(cdir)
        procs.append(start("ctl", [os.path.join(HERE, "build", "chat-ram"), "--listen", bed.CTL + ":8899"], os.path.join(out, "ctl-chat-ram.log"), "answering"))
        for n in AGENTS:
            procs.append(start(n, ["python3", "-u", os.path.join(HERE, "agent.py"), "--name", n, "--ctl", bed.CTL + ":8899", "--out", out],
                               os.path.join(out, "agent-%s.log" % n)))
        plan = {"run": rid, "cond": cond, "traffic": traffic, "seed": seed, "dur": dur, "drain": 8, "certs": cdir,
                "follows": workload.follows(seed), "capture": ["p1", "srv"], "stress_name": stress, "netem": st.get("netem"),
                "stress": [dict(e, at=e["at"] * dur) for e in st.get("events", [])]}
        fd = os.open("/var/run/netns/" + bed.NS("ctl"), os.O_RDONLY)
        os.setns(fd, os.CLONE_NEWNET); os.close(fd)          # the controller's network is ov-ctl's
        ok = control(plan, out)
    except Exception as e:
        print("[run] FAILED: %s" % e, flush=True)
    finally:
        os.setns(home, os.CLONE_NEWNET); os.close(home)       # out of ov-ctl before the bed comes down with it
        for p in procs:
            if p.poll() is None: p.terminate()
        bed.down()
    print("[run] %s: %s" % (rid, "ok" if ok else "FAILED (see %s)" % out), flush=True)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cond", choices=CONDS); ap.add_argument("traffic", choices=["chat", "telemetry"]); ap.add_argument("seed", type=int)
    ap.add_argument("--minutes", type=float, default=30.0); ap.add_argument("--stress", choices=sorted(STRESS)); ap.add_argument("--out")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run.py: as root (network namespaces)")
    rc = 0 if run(a.cond, a.traffic, a.seed, a.minutes, a.stress, a.out) else 1
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)                  # the control region's follow threads are the library's: nothing to finalize under them
