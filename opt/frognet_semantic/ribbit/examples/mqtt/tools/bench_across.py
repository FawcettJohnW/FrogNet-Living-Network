#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""bench_across.py -- two brokers on two machines, messages crossing between them, stock against Ribbit.

The machines are FrogNet's simulator's (opt/frognet_semantic/simulation): netns_backend.NetnsProvisioner builds them
from a frognet_sim Topology -- one network namespace per node, a veth per interface into a bridge per segment -- and
transport_factories.NetemShaper shapes the link between them with the simulator's NetworkParams. Nothing here builds a
network of its own.

    node A  eth0 10.77.1.1 ---- link (shaped) ---- eth0 10.77.1.2  node B
            eth1 10.77.2.1 ------ clients LAN ----- eth1 10.77.2.2
                                       |
                              node C eth0 10.77.2.3  (the load driver)

Publishers connect to A, subscribers to B: every message crosses the link.
  stock   Mosquitto 2.1.2 on A and on B, B bridged to A over the link (`topic # both <qos>`), the standard way to span
          two machines.
  ribbit  a box on A and a box on B, one memory (mqtt-ram) on A, B reaching it over the link.

For each link and load: messages per second delivered, lost, latency, the bytes that crossed the link (both
directions, from the link's own interface counters), and each broker's CPU per 1k messages.

Run as root from examples/mqtt:  tools/bench_across.py
  FROGNET_SEMANTIC=/opt/frognet_semantic (where the simulator is)   LINKS=lan,halow,4g   SECS=8   REPEAT=1
"""
import json, os, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FN = os.environ.get("FROGNET_SEMANTIC", "/opt/frognet_semantic")
SIM = os.path.join(FN, "simulation")
for p in (FN, SIM):
    if p not in sys.path: sys.path.insert(0, p)
try:
    import frognet_sim as fs
    from netns_backend import NetnsProvisioner, ShellRunner
    from transport_factories import NetemShaper
    from transport_sim_tier import NetworkParams
except Exception as e:
    sys.exit(f"bench_across: FrogNet's simulator is not at {SIM} ({e}); set FROGNET_SEMANTIC to the tree that has it")

# the simulator's own presets (transport_sim_tier.NetworkParams examples); bandwidth in bytes per second
LINKS = {
    "veth":  None,                                         # the plain veth, not shaped: said as such in every row
    "lan":   NetworkParams(latency_ms=0.5, bandwidth_bps=125e6),
    "4g":    NetworkParams(latency_ms=40, jitter_ms=15, bandwidth_bps=10e6),
    "halow": NetworkParams(latency_ms=20, jitter_ms=5, bandwidth_bps=600e3),
}
LOADS = [  # name, mqtt-load options: rate is per publisher (0 = as fast as it goes)
    ("qos1-4x4", dict(pubs=4, subs=4, topics=4, qos=1)),
    ("qos0-4x4", dict(pubs=4, subs=4, topics=4, qos=0)),
    ("qos1-4x4-500/s", dict(pubs=4, subs=4, topics=4, qos=1, rate=500)),
    # [ASK_WHEN_CONSUMED_V1] MQTT 5: every QoS 1 PUBACK carries "no matching subscribers" -- the one case that asks
    ("qos1-4x4-v5", dict(pubs=4, subs=4, topics=4, qos=1, proto=5)),
]
import glob
PERF_BIN = (sorted(glob.glob("/usr/lib/linux-tools*/perf")) + sorted(glob.glob("/usr/lib/linux-tools/*/perf")) + ["perf"])[0]
SECS = float(os.environ.get("SECS", "8")); REPEAT = int(os.environ.get("REPEAT", "1"))
ARMS = os.environ.get("ARMS", "stock,ribbit").split(",")
ONLY = os.environ.get("LOADS")  # comma list of load names; default all
PCAP = os.environ.get("PCAP")   # set: capture the link per run into <logs>/<arm>-<link>-<load>.pcap
STOCK = os.path.join(HERE, "stock/src/mosquitto"); RIBBIT = os.path.join(HERE, "front/src/mosquitto")
RAM = os.path.join(HERE, "front/src/mqtt-ram"); LOAD = os.path.join(HERE, "harness/mqtt-load")
A1, B1, A2, B2, C2 = "10.77.1.1", "10.77.1.2", "10.77.2.1", "10.77.2.2", "10.77.2.3"
PORT, MEM = 1883, 8886


def topology():
    t = fs.Topology("bench-across")
    a = t.add(fs.FrogNode("A")); a.add_iface("eth0", A1 + "/24"); a.add_iface("eth1", A2 + "/24")
    b = t.add(fs.FrogNode("B")); b.add_iface("eth0", B1 + "/24"); b.add_iface("eth1", B2 + "/24")
    c = t.add(fs.FrogNode("C")); c.add_iface("eth0", C2 + "/24")
    t.link(("A", "eth0"), ("B", "eth0"))                      # segment 0: the link between the machines
    t.link(("A", "eth1"), ("B", "eth1"), ("C", "eth0"))       # segment 1: the clients
    return t


def ns(node): return f"fns_{node}"
def in_ns(node, argv): return ["ip", "netns", "exec", ns(node)] + argv
LINK_IFACES = ("fv0_0h", "fv0_1h")                             # the link's two host-side ends (NetnsProvisioner names)


def link_bytes():
    n = 0
    for i in LINK_IFACES:
        for k in ("tx_bytes", "rx_bytes"):
            n += int(open(f"/sys/class/net/{i}/statistics/{k}").read())
    return n // 2                                              # each byte crossing passes both ends once


def cpu(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    except OSError:
        return 0.0


def tree(pid):
    out = [pid]
    try:
        for c in open(f"/proc/{pid}/task/{pid}/children").read().split(): out += tree(int(c))
    except OSError: pass
    return out


def wait_port(node, host, port, t=20):
    t0 = time.time()
    while time.time() - t0 < t:
        if subprocess.run(in_ns(node, ["python3", "-c", f"import socket; socket.create_connection(('{host}',{port}),timeout=0.3)"]),
                          capture_output=True).returncode == 0: return True
        time.sleep(0.1)
    return False


def conf(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, dir="/tmp"); f.write(text); f.close(); os.chmod(f.name, 0o644); return f.name


def start(arm, qos, logs):
    procs = {}
    base = f"listener {PORT} 0.0.0.0\nallow_anonymous true\n"
    if arm == "stock":
        procs["A"] = subprocess.Popen(in_ns("A", [STOCK, "-c", conf(base)]), stdout=open(f"{logs}/stock-A.log", "w"), stderr=subprocess.STDOUT)
        assert wait_port("B", A1, PORT), "stock A never listened"
        bridge = base + f"connection a\naddress {A1}:{PORT}\ntopic # both {qos}\ncleansession true\n"
        procs["B"] = subprocess.Popen(in_ns("B", [STOCK, "-c", conf(bridge)]), stdout=open(f"{logs}/stock-B.log", "w"), stderr=subprocess.STDOUT)
    else:
        procs["mem"] = subprocess.Popen(in_ns("A", [RAM, "--listen", f"{A1}:{MEM}"]), stdout=open(f"{logs}/ribbit-mem.log", "w"), stderr=subprocess.STDOUT)
        assert wait_port("B", A1, MEM), "the memory never listened"
        env = dict(os.environ, MQTT_REGION=f"{A1}:{MEM}")
        if arm[len("ribbit"):].isdigit(): env["MQTT_THREADS"] = arm[len("ribbit"):]   # ribbitN: N fronts per box (the region's own threads)
        procs["A"] = subprocess.Popen(in_ns("A", [RIBBIT, "-c", conf(base)]), env=env, stdout=open(f"{logs}/ribbit-A.log", "w"), stderr=subprocess.STDOUT)
        procs["B"] = subprocess.Popen(in_ns("B", [RIBBIT, "-c", conf(base)]), env=env, stdout=open(f"{logs}/ribbit-B.log", "w"), stderr=subprocess.STDOUT)
    assert wait_port("C", A2, PORT) and wait_port("C", B2, PORT), f"{arm}: a broker never listened"
    time.sleep(1.5)                                            # the bridge connects / the boxes find each other
    return procs


def stop(procs):
    for p in procs.values():
        try: p.send_signal(signal.SIGTERM)
        except OSError: pass
    for p in procs.values():
        try: p.wait(10)
        except subprocess.TimeoutExpired: p.kill(); p.wait()


def run(arm, link, load, opts, logs):
    procs = start(arm, opts.get("qos", 1), logs)
    pids = {k: tree(p.pid) for k, p in procs.items()}
    cap = None
    if PCAP:
        cap = subprocess.Popen(["tcpdump", "-i", LINK_IFACES[0], "-s", "96", "-w", f"{logs}/{arm}-{link}-{load.replace('/','')}.pcap"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(0.5)
    c0 = {k: sum(cpu(x) for x in v) for k, v in pids.items()}; b0 = link_bytes()
    prf = None
    if os.environ.get("PERF"):                                 # each process, sampled through the measured window
        prf = {k: subprocess.Popen([PERF_BIN, "record", "-F", "999", "-g", "-p", str(p.pid),
                                    "-o", f"{logs}/perf-{arm}-{k}-{load.replace('/','')}.data", "--", "sleep", str(SECS + 2)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for k, p in procs.items()}
    args = [LOAD, "--host", B2, "--port", str(PORT), "--pub-host", A2, "--pub-port", str(PORT), "--seconds", str(SECS),
            "--warmup", "2", "--threads", "2", "--label", f"{arm}:{link}:{load}"]
    for k, v in opts.items(): args += [f"--{k}", str(v)]
    out = subprocess.run(in_ns("C", args), capture_output=True, text=True, timeout=SECS + 240).stdout
    b1 = link_bytes(); c1 = {k: sum(cpu(x) for x in v) for k, v in pids.items()}
    if cap: cap.send_signal(signal.SIGINT); cap.wait(5)
    for x in (prf or {}).values(): x.wait(30)
    stop(procs)
    row = json.loads(out.strip().splitlines()[-1])
    n = max(1, row.get("received", 0))
    row.update(arm=arm, link=link, load=load, link_bytes=b1 - b0, link_bytes_per_msg=round((b1 - b0) / n, 1),
               cpu_s={k: round(c1[k] - c0[k], 3) for k in c0},
               cpu_per_1k_ms={k: round(1e6 * (c1[k] - c0[k]) / n, 3) for k in c0})
    return row


def main():
    if os.geteuid() != 0: sys.exit("bench_across: needs root (network namespaces, tc)")
    links = os.environ.get("LINKS", "lan,4g,halow").split(",")
    logs = os.path.join(HERE, "results", "across-" + time.strftime("%Y%m%d-%H%M%S")); os.makedirs(logs, exist_ok=True)
    out = logs + ".jsonl"
    prov = NetnsProvisioner(topology(), runner=ShellRunner())
    prov.teardown(); prov.provision()
    shapers = []
    try:
        for link in links:
            for s in shapers: s.remove()
            shapers = [NetemShaper(i, LINKS[link], runner=ShellRunner()) for i in LINK_IFACES] if LINKS[link] else []
            for s in shapers: s.apply()
            for i in (LINK_IFACES if LINKS[link] else ()):  # the shaper says nothing when tc refuses it: the link is checked, not assumed
                q = subprocess.run(["tc", "qdisc", "show", "dev", i], capture_output=True, text=True).stdout
                if "netem" not in q:
                    sys.exit(f"bench_across: the {link} link is not shaped on {i} (tc shows: {q.strip() or 'nothing'}); this kernel has no "
                             f"sch_netem -- `tc qdisc add dev {i} root netem delay 1ms` says why. No numbers from an unshaped link.")
            for load, opts in LOADS:
                if ONLY and load not in ONLY.split(","): continue
                for rep in range(REPEAT):
                    for arm in ARMS:
                        try: row = run(arm, link, load, opts, logs)
                        except Exception as e: row = dict(arm=arm, link=link, load=load, error=str(e))
                        row["repeat"] = rep
                        print(json.dumps(row), flush=True)
                        with open(out, "a") as f: f.write(json.dumps(row) + "\n")
    finally:
        for s in shapers:
            try: s.remove()
            except Exception: pass
        prov.teardown()
    rows = [json.loads(l) for l in open(out)]
    print(f"\n== two brokers on two machines (FrogNet's simulator: netns + netem), publishers on A, subscribers on B, {SECS:g} s")
    print(f"{'link':6} {'load':16} {'arm':7} {'msgs/s':>9} {'lost':>8} {'p50 us':>9} {'p99 us':>9} {'link B/msg':>10} {'cpu ms/1k A':>11} {'B':>7} {'mem':>7}")
    for r in rows:
        if "error" in r: print(f"{r['link']:6} {r['load']:16} {r['arm']:7} ERROR {r['error']}"); continue
        c = r["cpu_per_1k_ms"]
        print(f"{r['link']:6} {r['load']:16} {r['arm']:7} {r['received_per_s']:>9} {r['lost']:>8} {r['lat_us_p50']:>9} {r['lat_us_p99']:>9} "
              f"{r['link_bytes_per_msg']:>10} {c.get('A', 0):>11} {c.get('B', 0):>7} {c.get('mem', 0):>7}")
    print(f"raw rows: {out}")


if __name__ == "__main__":
    main()
