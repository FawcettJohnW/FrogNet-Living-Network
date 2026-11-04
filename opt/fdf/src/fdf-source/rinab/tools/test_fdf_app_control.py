#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_fdf_app_control.py -- [APP_TUPLES_IN_CONTROL_V1] fdf-app with its tuples in databasehost-control, apart from the
WAN conduit: the members' sessions (fdf-client) are on one RAM host (the conduit), and databasehost-control is one of
two other RAM hosts on one port (127.0.0.2, 127.0.0.3), named by a hosts file the test rewrites as the election does.
Members a (least memory) and c (most) have the app; d has none.

  1  the app's tuples -- configuration, capability, presence, where each RAM is served -- are in databasehost-control;
     none of them is on the conduit
  2  each RAM served by c, mapped the same on a and c (presence held in databasehost-control with the member's session
     on the conduit: fdf-where --hold needs no member session there [HOLD_NEEDS_NO_MEMBER_SESSION_V1])
  3  the hosts file names the other RAM host databasehost-control: every member's configuration, capability and
     presence written there at once, and the RAMs served there by c again
  4  c's fdf-app gone (its session on the conduit still up): its presence in databasehost-control goes with it, and a
     serves every RAM
  5  install on d: no databasehost-control named, nothing installed (the bootstrap rule); named, the configuration
     adopted from it
usage: tools/test_fdf_app_control.py [BUILD_STATIC_DIR]
"""
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "build", "static")
F = os.path.join(H, "..", "fdf")
sys.path.insert(0, os.path.join(F, "lib", "python"))
import fdfram  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="fdf-app-control-")
os.makedirs(T + "/bin")
for b in ("fdf-ram", "fdf-client", "fdf-where"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
def ram_at(listen, name):
    procs.append(subprocess.Popen([T + "/bin/fdf-ram", "--listen", listen, "--api", fdfram.API],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % name, RINA_NET_CONFIG=T + "/none", FDF_VAR=T + "/v-" + name),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/%s.log" % name, "w")))
    h, p = listen.rsplit(":", 1)
    for _ in range(100):
        try: socket.create_connection((h, int(p)), 0.2).close(); return
        except OSError: time.sleep(0.05)
CONDUIT = "127.0.0.1:%d" % free()
PORT = free()
ram_at(CONDUIT, "conduit"); ram_at("127.0.0.2:%d" % PORT, "ctl2"); ram_at("127.0.0.3:%d" % PORT, "ctl3")
conduit = fdfram.Ram(CONDUIT)
ctl = {ip: fdfram.Ram("%s:%d" % (ip, PORT)) for ip in ("127.0.0.2", "127.0.0.3")}

hosts = T + "/etc/hosts"
os.makedirs(T + "/etc")
def name_control(ip):
    with open(hosts + ".tmp", "w") as f:
        f.write("127.0.0.1 localhost\n" + ("%s databasehost_control.frognet\n" % ip if ip else ""))
    os.replace(hosts + ".tmp", hosts)
name_control("127.0.0.2")

MEM = {"a": 1_000_000, "c": 8_000_000, "d": 16_000_000}
members = {}
def member_env(n):
    return dict(os.environ, RINA_RAM=CONDUIT, FDF_HOSTS=hosts, FDF_LAN_RAM_PORT=str(PORT), FDF_FACTS=T + "/%s/facts.json" % n,
                FDF_VAR=T + "/" + n, FDF_APPS=T + "/%s/apps" % n, FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"))
for i, n in ((1, "a"), (3, "c"), (4, "d")):
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": "node-" + n, "guid": "guid-" + n, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}], "capability": {"mem_total_kb": MEM[n], "cores": 4}},
              open(d + "/facts.json", "w"))
    members[n] = {"session": subprocess.Popen([T + "/bin/fdf-client"], env=member_env(n), stdout=open(d + "/client.log", "w"), stderr=subprocess.STDOUT)}
def serve(n, mode="w"):
    members[n]["serve"] = subprocess.Popen([os.path.join(F, "bin", "fdf-app"), "serve"], env=member_env(n),
                                           stdout=open(T + "/%s/app.log" % n, mode), stderr=subprocess.STDOUT)
time.sleep(1.0)
for n in "ac":
    subprocess.run([os.path.join(F, "bin", "fdf-app"), "config", "rangertrak", "general,hospitals", "--role", "hospitals",
                    "--admin", "boss:adm-2026"], env=member_env(n), check=True, stdout=subprocess.DEVNULL)
for n in "acd":
    serve(n)

def map_of(n):
    try: return json.load(open(T + "/%s/app-where.json" % n))
    except (OSError, ValueError): return None
def answers(hp):
    try: return bool(fdfram.Ram(hp).version(timeout=2))
    except Exception: return False
def wait_for(pred, limit=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        v = pred()
        if v: return v, time.monotonic() - t0
        time.sleep(0.02)
    return None, limit
def live(cells):
    return [c for c in cells if not c.get("gone") and c.get("bag")]
def on(host, who="ac"):
    def f():
        ms = {n: map_of(n) for n in who}
        ok = all(ms[n] and len(ms[n]) == 2 and all(v.startswith(host + ":") for v in ms[n].values()) for n in who)
        return ms if ok and all(ms[n] == ms["a"] for n in who) and all(answers(v) for v in ms["a"].values()) else None
    return f
def family(rm):
    return sorted("%s/%s" % (c["variable"], c["instance"]) for c in live(rm.read("fdf-app")) if c["variable"] in ("config", "can"))
def presence(rm):
    return sorted(c["instance"] for c in live(rm.read("pond", "where")) if c["instance"].endswith("/fdf-app"))

try:
    ms, dt = wait_for(on("127.0.3.1"))
    wait_for(lambda: len([c for c in live(ctl["127.0.0.2"].read("pond", "where")) if not c["instance"].endswith("/fdf-app")]) == 2, 5)
    fam, pres = family(ctl["127.0.0.2"]), presence(ctl["127.0.0.2"])
    wh = sorted(c["instance"] for c in live(ctl["127.0.0.2"].read("pond", "where")) if not c["instance"].endswith("/fdf-app"))
    on_conduit = [c["instance"] for c in live(conduit.read("fdf-app"))] + [c["instance"] for c in live(conduit.read("pond", "where"))]
    check(fam == ["can/rangertrak.guid-a", "can/rangertrak.guid-c", "config/rangertrak.guid-a", "config/rangertrak.guid-c"]
          and pres == ["w:guid-a/fdf-app", "w:guid-c/fdf-app", "w:guid-d/fdf-app"] and len(wh) == 2 and not on_conduit,
          "1 in databasehost-control: %s, presence %s, where %s; on the conduit: %s" % (fam, pres, wh, on_conduit or "nothing"))
    check(ms is not None, "2 every RAM served by c, the same map on a and c, %.0f ms from start: %s" % (dt * 1000, ms and ms["a"]))

    t0 = time.monotonic(); name_control("127.0.0.3")
    got, dt = wait_for(lambda: family(ctl["127.0.0.3"]) == fam and presence(ctl["127.0.0.3"]) == pres
                       and len([c for c in live(ctl["127.0.0.3"].read("pond", "where")) if not c["instance"].endswith("/fdf-app")]) == 2)
    ms, _ = wait_for(on("127.0.3.1"))
    check(bool(got) and ms is not None, "3 databasehost-control moved to 127.0.0.3: every member's configuration, capability and presence "
          "there %.0f ms after the hosts file named it, and the RAMs still on c: %s" % (dt * 1000, ms and ms["a"]))

    p = members["c"]["serve"]; p.terminate(); p.wait(); t0 = time.monotonic()
    ms, dt = wait_for(on("127.0.1.1", "a"))
    sess = any(x.get("ns") == "w:guid-c" and x.get("alive") for x in conduit.participants())
    check(ms is not None and sess and "w:guid-c/fdf-app" not in presence(ctl["127.0.0.3"]),
          "4 c's fdf-app gone (its session on the conduit still up: %s): its presence gone, a serves every RAM %.0f ms after: %s"
          % (sess, dt * 1000, ms and ms["a"]))

    envd = dict(member_env("d"), FDF_APP_RESTART="true")
    name_control("")
    no = subprocess.run([os.path.join(F, "bin", "fdf-app"), "install", "rangertrak", "--admin", "boss:adm-2026"], env=envd,
                        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    nofile = not os.path.exists(T + "/d/apps/rangertrak.json")
    name_control("127.0.0.3")
    ok = subprocess.run([os.path.join(F, "bin", "fdf-app"), "install", "rangertrak", "--admin", "boss:adm-2026"], env=envd,
                        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    mine = json.load(open(T + "/d/apps/rangertrak.json")) if os.path.exists(T + "/d/apps/rangertrak.json") else {}
    check(no.returncode != 0 and nofile and "databasehost-control" in no.stdout and ok.returncode == 0 and mine.get("rams") == ["general", "hospitals"]
          and mine.get("admin", {}).get("key"),
          "5 install on d with no databasehost-control named: refused, nothing saved (%s); named: adopted from it (rams %s)"
          % ((no.stdout.strip().splitlines() or [""])[-1][:90], mine.get("rams")))
finally:
    for m in members.values():
        for p in m.values():
            if p.poll() is None: p.terminate()
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
