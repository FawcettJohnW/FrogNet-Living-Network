#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""bench_lib.py -- MQTT applications on two machines, through libmosquitto's API, stock against ours.

FrogNet's simulator's machines (bench_across.py's topology: A and B joined by the link, C unused). The same application
binary (harness/mqtt-load-lib) runs its publishers on A and its subscribers on B.
  stock   the stock libmosquitto; publishers talk to stock Mosquitto on A, subscribers to stock Mosquitto on B, B bridged
          to A over the link -- the standard way across two machines
  ribbit  our libmosquitto ([CLIENT_ON_THE_MEMORY_V1]): no broker anywhere; the memory (mqtt-ram) on A, the publishers
          write to it, the subscribers read from it over the link
  here    our libmosquitto, A as its own ram ([NODE_IS_ITS_OWN_RAM_V1]): the publishers' process holds the memory and
          writes it directly (MQTT_MEMORY=here:A1:port); the subscribers on B read it over the link
Per link and load: delivered on time per second, late, lost (sent x subscribers per topic - delivered), latency, the
bytes on the link per message, and the CPU of every process of the arm (applications included) per 1k messages.
Run as root from examples/mqtt:  tools/bench_lib.py      LINKS=veth,lan,4g,halow  SECS=6  ARMS=stock,ribbit  LOADS=...
"""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_across as X

HERE = X.HERE
LIB_STOCK = os.path.join(HERE, "stock/lib"); LIB_OURS = os.path.join(HERE, "libram"); DRIVER = os.path.join(HERE, "harness/mqtt-load-lib")
LOADS = [("qos1-4x4", dict(pubs=4, subs=4, topics=4, qos=1)), ("qos0-4x4", dict(pubs=4, subs=4, topics=4, qos=0)),
         ("qos1-4x4-500/s", dict(pubs=4, subs=4, topics=4, qos=1, rate=500))]
SECS = float(os.environ.get("SECS", "6")); WARM = 2.0
ARMS = os.environ.get("ARMS", "stock,ribbit,here").split(","); ONLY = os.environ.get("LOADS")


def start(arm, qos, logs):
    procs = {}
    base = f"listener {X.PORT} 0.0.0.0\nallow_anonymous true\n"
    if arm == "stock":
        procs["brokerA"] = subprocess.Popen(X.in_ns("A", [X.STOCK, "-c", X.conf(base)]), stdout=open(f"{logs}/stock-A.log", "w"), stderr=subprocess.STDOUT)
        assert X.wait_port("B", X.A1, X.PORT), "stock A never listened"
        bridge = base + f"connection a\naddress {X.A1}:{X.PORT}\ntopic # both {qos}\ncleansession true\n"
        procs["brokerB"] = subprocess.Popen(X.in_ns("B", [X.STOCK, "-c", X.conf(bridge)]), stdout=open(f"{logs}/stock-B.log", "w"), stderr=subprocess.STDOUT)
        assert X.wait_port("A", X.B1, X.PORT), "stock B never listened"
        time.sleep(1.5)
    elif arm == "ribbit":
        procs["mem"] = subprocess.Popen(X.in_ns("A", [X.RAM, "--listen", f"{X.A1}:{X.MEM}"]), stdout=open(f"{logs}/ribbit-mem.log", "w"), stderr=subprocess.STDOUT)
        assert X.wait_port("B", X.A1, X.MEM), "the memory never listened"
    return procs


def run(arm, link, load, opts, logs):
    procs = start(arm, opts.get("qos", 1), logs)
    lib = LIB_STOCK if arm == "stock" else LIB_OURS
    env = dict(os.environ, LD_LIBRARY_PATH=lib, MQTT_MEMORY=f"{X.A1}:{X.MEM}")
    t0 = int(time.time() * 1000) + 2500
    common = ["--topics", str(opts["topics"]), "--qos", str(opts["qos"]), "--start", str(t0), "--warmup", str(WARM), "--seconds", str(SECS)]
    pubargs = [DRIVER, "pub", "--host", "127.0.0.1", "--port", str(X.PORT), "--pubs", str(opts["pubs"])] + common + (["--rate", str(opts["rate"])] if opts.get("rate") else [])
    def start_sub():
        return subprocess.Popen(X.in_ns("B", [DRIVER, "sub", "--host", "127.0.0.1", "--port", str(X.PORT), "--subs", str(opts["subs"])] + common),
                                env=env, stdout=subprocess.PIPE, stderr=open(f"{logs}/{arm}-sub.err", "w"), text=True)
    def start_pub(e):
        return subprocess.Popen(X.in_ns("A", pubargs), env=e, stdout=subprocess.PIPE, stderr=open(f"{logs}/{arm}-pub.err", "w"), text=True)
    if arm == "here":                                             # the publishers' process is the memory: it first
        pubargs += ["--linger", "5"]                              # the memory stays up while the subscribers finish reading
        pub = start_pub(dict(env, MQTT_MEMORY=f"here:{X.A1}:{X.MEM}"))
        assert X.wait_port("B", X.A1, X.MEM), "the publishers' memory never listened"
        sub = start_sub()
    else:
        sub = start_sub(); pub = start_pub(env)
    procs_all = dict(procs, pub=pub, sub=sub)
    time.sleep(max(0.0, t0 / 1000 - time.time()) + WARM)         # measure from the window's start
    pids = {k: X.tree(p.pid) for k, p in procs_all.items()}
    c0 = {k: sum(X.cpu(x) for x in v) for k, v in pids.items()}; b0 = X.link_bytes()
    time.sleep(SECS)
    b1 = X.link_bytes(); c1 = {k: sum(X.cpu(x) for x in v) for k, v in pids.items()}
    so = json.loads(sub.communicate(timeout=120)[0].strip().splitlines()[-1])
    po = json.loads(pub.communicate(timeout=120)[0].strip().splitlines()[-1])
    X.stop(procs)
    per_topic = max(1, opts["subs"] // opts["topics"])
    delivered = so["on_time"] + so["late"]; expected = po["sent"] * per_topic
    n = max(1, so["on_time"])
    return dict(arm=arm, link=link, load=load, sent=po["sent"], sent_per_s=po["sent_per_s"], on_time=so["on_time"], on_time_per_s=so["on_time_per_s"],
                late=so["late"], lost=max(0, expected - delivered), lat_us_p50=so["lat_us_p50"], lat_us_p99=so["lat_us_p99"],
                link_bytes_per_msg=round((b1 - b0) / n, 1), cpu_per_1k_ms={k: round(1e6 * (c1[k] - c0[k]) / n, 2) for k in c0},
                cpu_total_per_1k_ms=round(1e6 * sum(c1[k] - c0[k] for k in c0) / n, 2))


def main():
    if os.geteuid() != 0: sys.exit("bench_lib: needs root (network namespaces, tc)")
    links = os.environ.get("LINKS", "veth").split(",")
    logs = os.path.join(HERE, "results", "lib-" + time.strftime("%Y%m%d-%H%M%S")); os.makedirs(logs, exist_ok=True)
    out = logs + ".jsonl"
    prov = X.NetnsProvisioner(X.topology(), runner=X.ShellRunner()); prov.teardown(); prov.provision()
    shapers = []
    try:
        for link in links:
            for s in shapers: s.remove()
            shapers = [X.NetemShaper(i, X.LINKS[link], runner=X.ShellRunner()) for i in X.LINK_IFACES] if X.LINKS[link] else []
            for s in shapers: s.apply()
            for i in (X.LINK_IFACES if X.LINKS[link] else ()):
                q = subprocess.run(["tc", "qdisc", "show", "dev", i], capture_output=True, text=True).stdout
                if "netem" not in q: sys.exit(f"bench_lib: the {link} link is not shaped on {i}; no numbers from an unshaped link")
            for load, opts in LOADS:
                if ONLY and load not in ONLY.split(","): continue
                for arm in ARMS:
                    try: row = run(arm, link, load, opts, logs)
                    except Exception as e: row = dict(arm=arm, link=link, load=load, error=str(e))
                    print(json.dumps(row), flush=True)
                    with open(out, "a") as f: f.write(json.dumps(row) + "\n")
    finally:
        for s in shapers:
            try: s.remove()
            except Exception: pass
        prov.teardown()
    print(f"\n== MQTT applications on two machines through libmosquitto (publishers on A, subscribers on B), {SECS:g} s")
    print(f"{'link':6} {'load':16} {'arm':7} {'on-time/s':>9} {'late':>8} {'lost':>9} {'p50 ms':>8} {'p99 ms':>8} {'link B/msg':>10} {'CPU ms/1k':>9}")
    for r in (json.loads(l) for l in open(out)):
        if "error" in r: print(f"{r['link']:6} {r['load']:16} {r['arm']:7} ERROR {r['error']}"); continue
        print(f"{r['link']:6} {r['load']:16} {r['arm']:7} {r['on_time_per_s']:>9.0f} {r['late']:>8} {r['lost']:>9} {r['lat_us_p50']/1000:>8.1f} {r['lat_us_p99']/1000:>8.1f} "
              f"{r['link_bytes_per_msg']:>10} {r['cpu_total_per_1k_ms']:>9}")
    print(f"raw rows: {out}")


if __name__ == "__main__":
    main()
