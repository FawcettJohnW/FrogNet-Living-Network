#!/usr/bin/env python3
"""topo-rig.py TOPOLOGIES.json INDEX [--keep] -- [EXP3_TOPOLOGY_RIG_V1] FD&F experiment 3: build ONE of FrogNet's discovery
topologies (tools/export_frognet_topos.py) as real machines on real shared Ethernet, run Mode B over it, and check FrogNet's
own criterion: every node reaches every other node.

- each node      a network namespace with its own agent (told only where the area's host is)
- each segment   a Linux bridge in this namespace: a real shared L2 (LAN or WireGuard segment alike)
- DIFs           one shim-eth DIF per segment; one normal DIF ("nd") over all of them
- joining        node 0 is the first member; the others enroll in breadth-first order, each through a segment it
                 shares with a member already in (broadcast enrollment, as rlite 0025), then take an adjacency to
                 EVERY member it shares a segment with (ipcp-lower-flow-alloc, as rlite 0028) -- so paths are the
                 topology's, not a tree's
- the oracle     every ordered pair (i, j): node i pings node j's server by name, once
Prints one JSON line: pairs, reached, routes derived per node, seconds to build and to check. Exit 0 only if every
pair is reached. Needs build/ (tools/build.sh, tools/build_rlite.sh). Run as root, on its own (not under run.sh)."""
import json, os, subprocess, sys, tempfile, time
from collections import deque

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); B = os.path.join(H, "build/bin")
API = "/FawcettInnovations.RINA.ram_interface.php"; PORT = 8861
topos = json.load(open(sys.argv[1])); T = topos[int(sys.argv[2])]; keep = "--keep" in sys.argv
D = tempfile.mkdtemp(prefix="exp3-%02d-" % int(sys.argv[2]))
idx = {n: i for i, n in enumerate(T["nodes"])}; N = len(T["nodes"])
segs = [[(idx[n], i) for n, i in s] for s in T["segments"]]
ENV = dict(os.environ, PATH=B + ":" + os.environ["PATH"], RINA_RAM="127.0.0.1:%d" % PORT, RINA_RAM_NETNS="/proc/%d/ns/net" % os.getpid(),
           RINA_AGENT_LOG=D, RINA_RMT_STATS=os.path.join(D, "rmt.txt"), RINA_REGION_FILE=os.path.join(D, "region.perm"))
def sh(cmd, check=True, ns=None, quiet=False):
    full = (["ip", "netns", "exec", ns] if ns else []) + cmd
    r = subprocess.run(full, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with open(os.path.join(D, "commands.log"), "a") as f: f.write("%s$ %s\n%s" % (ns or "root", " ".join(cmd), r.stdout))
    if check and r.returncode != 0: raise RuntimeError("%s: %s" % (" ".join(full), r.stdout.strip()))
    return r
def ns(i): return "t%d" % i
procs = []
def teardown():
    for i in range(N): sh(["rlite-ctl", "terminate"], check=False, ns=ns(i))
    time.sleep(0.3)
    for p in procs: p.kill()
    for i in range(N):
        pids = subprocess.run(["ip", "netns", "pids", ns(i)], stdout=subprocess.PIPE, text=True).stdout.split()
        for p in pids: subprocess.run(["kill", "-9", p])
        subprocess.run(["ip", "netns", "delete", ns(i)], stderr=subprocess.DEVNULL)
    for k in range(len(segs)): subprocess.run(["ip", "link", "delete", "fb%d" % k], stderr=subprocess.DEVNULL)
result = {"builder": T["builder"], "label": T["label"], "nodes": N, "segments": len(segs), "diag": D}
try:
    t0 = time.time()
    host = subprocess.Popen([os.path.join(B, "rina-host"), "--listen", "127.0.0.1:%d" % PORT, "--api", API], env=ENV,
                            stdout=open(os.path.join(D, "host.log"), "w"), stderr=subprocess.STDOUT); procs.append(host)
    for _ in range(100):
        if subprocess.run(["bash", "-c", "(exec 3<>/dev/tcp/127.0.0.1/%d)" % PORT], stderr=subprocess.DEVNULL).returncode == 0: break
        time.sleep(0.05)
    for i in range(N):
        subprocess.run(["ip", "netns", "add", ns(i)], check=True); sh(["ip", "link", "set", "lo", "up"], ns=ns(i))
    for k, seg in enumerate(segs):                                   # a segment is a bridge; each member a veth onto it
        sh(["ip", "link", "add", "fb%d" % k, "type", "bridge"]); sh(["ip", "link", "set", "fb%d" % k, "up"])
        for (i, _) in seg:
            v, b = "v%ds%d" % (i, k), "b%ds%d" % (i, k)
            sh(["ip", "link", "add", v, "type", "veth", "peer", "name", b]); sh(["ip", "link", "set", b, "master", "fb%d" % k])
            sh(["ip", "link", "set", b, "up"]); sh(["ip", "link", "set", v, "netns", ns(i)]); sh(["ip", "link", "set", v, "up"], ns=ns(i))
    for i in range(N): sh(["rlite-uipcps", "-d"], ns=ns(i))
    mine = {i: [k for k, seg in enumerate(segs) if any(m == i for m, _ in seg)] for i in range(N)}
    for i in range(N):
        for k in mine[i]:
            sh(["rlite-ctl", "ipcp-create", "n%de%d" % (i, k), "shim-eth", "s%d" % k], ns=ns(i))
            sh(["rlite-ctl", "ipcp-config", "n%de%d" % (i, k), "netdev", "v%ds%d" % (i, k)], ns=ns(i))
        sh(["rlite-ctl", "ipcp-create", "n%dn" % i, "normal", "nd"], ns=ns(i))
        for k in mine[i]: sh(["rlite-ctl", "ipcp-register", "n%dn" % i, "s%d" % k], ns=ns(i))
    sh(["rlite-ctl", "ipcp-enroller-enable", "n0n"], ns=ns(0))
    inside, order, q = {0}, [0], deque([0]); adj = 0
    while q:                                                          # breadth-first from node 0, over shared segments
        j = q.popleft()
        for k in mine[j]:
            for (i, _) in segs[k]:
                if i in inside: continue
                sh(["rlite-ctl", "ipcp-enroll", "n%dn" % i, "nd", "s%d" % k, "n%dn" % j], ns=ns(i))
                sh(["rlite-ctl", "ipcp-enroller-enable", "n%dn" % i], ns=ns(i))
                inside.add(i); order.append(i); q.append(i); adj += 1
                for k2 in mine[i]:                                    # an adjacency to every member already in, on every segment
                    for (m, _) in segs[k2]:
                        if m in inside and m != i and not (k2 == k and m == j):
                            sh(["rlite-ctl", "ipcp-lower-flow-alloc", "n%dn" % i, "nd", "s%d" % k2, "n%dn" % m], ns=ns(i)); adj += 1
    result["joined"] = len(inside); result["adjacencies"] = adj; result["build_s"] = round(time.time() - t0, 2)
    for i in range(N):
        procs.append(subprocess.Popen(["ip", "netns", "exec", ns(i), "rinaperf", "-l", "-z", "p%d" % i], env=ENV,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(0.5)
    t1 = time.time(); pairs = reached = 0; missed = []
    for i in range(N):
        for j in range(N):
            if i == j: continue
            pairs += 1
            r = sh(["rinaperf", "-z", "p%d" % j, "-p", "1", "-c", "1", "-g", "0"], check=False, ns=ns(i))
            if r.returncode == 0 and "1 packets transmitted, 1 received" in r.stdout: reached += 1
            else: missed.append("%s->%s" % (T["nodes"][i], T["nodes"][j]))
    result.update(pairs=pairs, reached=reached, missed=missed[:10], check_s=round(time.time() - t1, 2))
    t_end = time.time()                                               # routes as they stood while the pairs were checked
    for i in range(N): sh(["rlite-ctl", "terminate"], check=False, ns=ns(i))
    time.sleep(0.5)
    routes = {}
    for line in open(ENV["RINA_RMT_STATS"]) if os.path.exists(ENV["RINA_RMT_STATS"]) else []:
        if "derived" in line and " routes" in line:
            w = line.split(); who = w[0]                     # "<machine> derived N routes at T": the last per machine wins
            at = float(w[w.index("at") + 1]) if "at" in w else 0.0
            if at > t_end: continue                                   # teardown re-derives: not the routes that were checked
            for x, y in zip(w, w[1:]):
                if x == "derived": routes[who] = int(y)
    result["routes_per_node"] = sorted(routes.values())
except Exception as e:
    result["error"] = str(e)[:300]
finally:
    if not keep: teardown()
print(json.dumps(result), flush=True)
sys.exit(0 if result.get("pairs") and result.get("reached") == result.get("pairs") else 1)
