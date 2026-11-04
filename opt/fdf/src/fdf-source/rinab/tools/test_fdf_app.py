#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_fdf_app.py -- [APP_SERVICES_V1] fdf-app as the design has it (the doc "FrogNet SystemRam, FrogNetHost Admin and
App Services -- Design", 2026-11-04, sections 5, 7 and 9), on real RAM hosts (fdf-ram) and four machines a, b, c, d,
each with its own fdf-app serve and its own .1 (127.0.N.1). Two RAM hosts on one port, 127.0.0.2 and 127.0.0.3: the
hosts file (as every node's election writes it) names databasehost-control and the SystemRam; the AppHost lines are
the real election's (core/app_hosts.py), run here as the tunnel daemon runs it: on a held read on each fdf-app family
and on a membership change. One app, rangertrak, RAMs general and hospitals; c has the most memory, b the next, a the
least, d the most and no app.

  1  the first install (a, the Admin's line): ONE configuration tuple, (fdf-app, config, rangertrak), in
     databasehost-control, signed, with a port in 20000..65535 for every RAM; nothing else of the app there
  2  b and c install it from that tuple (the admin password checked against its check value); d never offers it.
     databasehost-control still holds exactly the one tuple: no machine writes a copy of its own
  3  each machine's capability tuple per AppRam in the SystemRam; the password nowhere in a tuple
  4  the election: rangertrakHost.frognet and hospitalsHost.frognet name c; c serves each on its configured port
  5  c's service gone (its machine still there): its capability tuples gone with its session; both on b
  6  c back: both on c again
  7  the Admin's configuration (a new Team RAM alpha with its own port, a new user): adopted by a, b and c (their
     answers in the SystemRam); alpha served on its port
  8  a configuration under the wrong admin password: refused by every machine; nothing changes
  9  the port of general changed in the configuration: its AppHost restarts it on the new port; the old one closed
 10  the app's selection (-mem_total_kb): every AppRam on a
 11  hospitals' lead places it here on b (its own configuration tuple): hospitalsHost.frognet names b; a place by a
     user who is not its lead is no place; placed on databasehost: the selection again
 12  databasehost-control and the SystemRam move (the hosts file names the other RAM host): the merge writes the
     configuration tuples into the new databasehost-control as signed; the capability tuples are held there; the
     AppHosts the same
 13  install with no databasehost-control named: nothing installed
usage: tools/test_fdf_app.py [BUILD_STATIC_DIR]
"""
import hashlib, hmac, json, os, shutil, socket, subprocess, sys, tempfile, threading, time

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "build", "static")
F = os.path.join(H, "..", "fdf")
FN = os.path.join(H, "..", "..", "..", "..", "frognet_semantic")
sys.path.insert(0, os.path.join(F, "lib", "python"))
sys.path.insert(0, FN)
import fdfram  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="fdf-app-")
os.makedirs(T + "/bin")
for b in ("fdf-ram", "ram"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
PORT = free()
os.environ["FDF_LAN_RAM_PORT"] = str(PORT); os.environ["FDF_PY"] = os.path.join(F, "lib", "python")
from core import app_hosts  # noqa: E402  (reads FDF_LAN_RAM_PORT)
def ram_at(ip, name):
    procs.append(subprocess.Popen([T + "/bin/fdf-ram", "--listen", "%s:%d" % (ip, PORT), "--api", fdfram.API],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % name, RINA_NET_CONFIG=T + "/none", FDF_VAR=T + "/v-" + name),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/%s.log" % name, "w")))
    for _ in range(100):
        try: socket.create_connection((ip, PORT), 0.2).close(); return
        except OSError: time.sleep(0.05)
ram_at("127.0.0.2", "ram2"); ram_at("127.0.0.3", "ram3")
R = {ip: fdfram.Ram("%s:%d" % (ip, PORT)) for ip in ("127.0.0.2", "127.0.0.3")}

# ---- the hosts file every node's election writes, and the election (the tunnel daemon's two app-tuple reads)
HOSTS = T + "/etc/hosts"; os.makedirs(T + "/etc")
MEM = {"a": 1_000_000, "b": 4_000_000, "c": 8_000_000, "d": 16_000_000}
IDX = {"a": 1, "b": 2, "c": 3, "d": 4}
world = {"control": "127.0.0.2", "system": "127.0.0.2", "nodes": set("abcd"), "apps": {}}
lock = threading.Lock()
def elect(why=""):
    with lock:
        base = ["127.0.0.1 localhost"] + ["127.0.%d.1 FrogNetHost.node-%s" % (IDX[n], n) for n in sorted(world["nodes"])] + \
               ["%s databasehost_control.frognet" % world["control"], "%s databasehost.frognet" % world["system"]]
        try:
            was = [l.rstrip("\n") for l in open(HOSTS)]
        except OSError:
            was = []
        if app_hosts.ip_of(was, "databasehost_control.frognet") not in ("", world["control"]):
            for n, d in world["apps"].items():          # [CONFIG_WRITTEN_AGAIN_BY_THE_MERGE_V1] each node's merge, from its copies
                app_hosts.APPS = d; app_hosts.write_configs_again(world["control"], logger=lambda m: open(T + "/elect.log", "a").write(m + "\n"))
        etc = app_hosts.commit(base, hosts_path=HOSTS, logger=lambda m: open(T + "/elect.log", "a").write("%s: %s\n" % (why, m)))
        text = "\n".join(etc) + "\n"
        if text != "\n".join(was) + "\n":
            open(HOSTS + ".tmp", "w").write(text); os.replace(HOSTS + ".tmp", HOSTS)
def watch(name):
    last, at = -1, None
    while True:
        ip = world["control"] if name == "control" else world["system"]
        if ip != at: last, at = -1, ip
        try:
            cs = R[ip].read("fdf-app", "", "", after=last, wait_s=5)
            if cs:
                last = max(int(c["id"]) for c in cs); elect("app tuples (%s)" % name)
        except fdfram.RamError:
            time.sleep(0.2)
elect("start")
for n in ("control", "system"): threading.Thread(target=watch, args=(n,), daemon=True).start()

members = {}
def env_of(n):
    return dict(os.environ, FDF_HOSTS=HOSTS, FDF_FACTS=T + "/%s/facts.json" % n, FDF_VAR=T + "/" + n, FDF_APPS=T + "/%s/apps" % n,
                FDF_BIN=T + "/bin", FDF_APP_RESTART="true")
for n in "abcd":
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": "node-" + n, "guid": "guid-" + n, "role": "node", "addrs": [{"ip": "127.0.%d.1" % IDX[n], "kind": "own"}],
               "capability": dict({"mem_total_kb": MEM[n], "cores": 4}, **({"gpu": 1} if n == "b" else {}))}, open(d + "/facts.json", "w"))
    world["apps"][n] = d + "/apps"
fa = os.path.join(F, "bin", "fdf-app")
def run(n, *a):
    p = subprocess.run([sys.executable, fa] + list(a), env=env_of(n), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, (p.stdout + p.stderr).strip()
def serve(n):
    members[n] = subprocess.Popen([sys.executable, fa, "serve"], env=env_of(n), stdout=open(T + "/%s/app.log" % n, "a"), stderr=subprocess.STDOUT)
def stop(n):
    p = members.pop(n); p.terminate(); p.wait()
def live(rm, v, pre=""):
    return [c for c in rm.read("fdf-app", v) if not c.get("gone") and c.get("bag") and c["instance"].startswith(pre)]
def hosts():
    try: return {l.split()[1]: l.split()[0] for l in open(HOSTS) if len(l.split()) >= 2}
    except OSError: return {}
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
def ctl(): return R[world["control"]]
def sysr(): return R[world["system"]]
def cfg_now():
    b = [c["bag"] for c in live(ctl(), "config") if c["instance"] == "rangertrak"]
    return (json.loads(b[0]["config"]), b[0]) if b else (None, None)
def on(want):
    """every AppRam named on the node it should be, answering on its configured port"""
    def f():
        c, _ = cfg_now(); h = hosts()
        if not c: return None
        got = {r: "%s:%s" % (h.get(app_hosts.host_name("rangertrak", r), ""), c["ports"][r]) for r in want}
        return got if all(got[r].startswith("127.0.%d.1:" % IDX[want[r]]) and answers(got[r]) for r in want) else None
    return f
def admin_send(pw, cfg, seq):
    salt = hashlib.sha256(b"fdf-app-admin:rangertrak:boss").digest()[:16]
    k = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200000)
    text = json.dumps(cfg, sort_keys=True)
    _, bag = cfg_now()
    ctl().write("fdf-app", "config", "rangertrak", {"config": text, "seq": seq, "user": "boss", "admin": bag["admin"], "at": int(time.time()),
                                                    "mac": hmac.new(k, ("%s\n%d" % (text, seq)).encode(), hashlib.sha256).hexdigest()})
    return k
def applied(seq, ok=True):
    a = [c["bag"] for c in live(sysr(), "applied", "rangertrak.")]
    return a if len(a) == 3 and all(x["seq"] == seq and bool(x["ok"]) == ok for x in a) else None

try:
    rc, out = run("a", "config", "rangertrak", "general,hospitals", "--login", "--role", "hospitals", "--role", "command:default",
                  "--user", "pat:beds-2026=hospitals", "--user", "kim:kim-2026=command", "--admin", "boss:adm-2026")
    cf = live(ctl(), "config")
    c0, b0 = cfg_now()
    check(rc == 0 and [x["instance"] for x in cf] == ["rangertrak"] and c0 and all(20000 <= c0["ports"][r] <= 65535 for r in ("general", "hospitals"))
          and len(b0["mac"]) == 64 and "key" not in json.dumps(b0["admin"]) and not live(ctl(), "can") and not live(ctl(), "applied"),
          "1 the first install: one configuration tuple in databasehost-control %s, signed, ports %s; nothing else of the app there: %s"
          % ([x["instance"] for x in cf], c0 and c0["ports"], out.splitlines()[-1:]))
    rcs = [run(n, "install", "rangertrak", "--admin", "boss:adm-2026") for n in "bc"]
    bad = run("d", "install", "rangertrak", "--admin", "boss:wrong")
    check(all(r[0] == 0 for r in rcs) and bad[0] != 0 and not os.listdir(T + "/d/apps") and [x["instance"] for x in live(ctl(), "config")] == ["rangertrak"]
          and all(json.load(open(T + "/%s/apps/rangertrak.json" % n))["ports"] == c0["ports"] for n in "bc"),
          "2 b and c installed from the tuple; a wrong admin password on d installs nothing (%s); databasehost-control still holds one tuple"
          % bad[1].splitlines()[-1:])
    for n in "abcd": serve(n)
    cans, _ = wait_for(lambda: (lambda x: x if len(x) == 6 else None)(sorted(c["instance"] for c in live(sysr(), "can", "rangertrak."))))
    alltext = json.dumps([c["bag"] for c in live(ctl(), "config") + live(sysr(), "can")])
    check(bool(cans) and "beds-2026" not in alltext and "adm-2026" not in alltext,
          "3 the capability tuples in the SystemRam, one per machine per AppRam: %s; no password in any tuple" % cans)
    m, dt = wait_for(on({"general": "c", "hospitals": "c"}))
    check(m is not None and not [l for l in open(T + "/d/app.log") if "serving" in l],
          "4 the election: rangertrakHost.frognet and hospitalsHost.frognet on c, served on their ports (%.0f ms): %s" % (dt * 1000, m))

    stop("c"); t0 = time.monotonic()
    m, dt = wait_for(on({"general": "b", "hospitals": "b"}))
    gone = not [c for c in live(sysr(), "can") if c["instance"].endswith(".guid-c")]
    check(m is not None and gone, "5 c's service gone: its capability tuples gone with its session (%s); both on b %.0f ms after: %s" % (gone, dt * 1000, m))
    serve("c")
    m, dt = wait_for(on({"general": "c", "hospitals": "c"}))
    check(m is not None, "6 c back: both on c %.0f ms after: %s" % (dt * 1000, m))

    c1, _ = cfg_now()
    vs = os.urandom(16)
    new = dict(c1, rams=["general", "hospitals", "alpha"], ports=dict(c1["ports"], alpha=c1["ports"]["general"] + 1 if c1["ports"]["general"] < 65535 else 20001),
               roles=dict(c1["roles"], alpha={"default": True, "category": "Teams"}),
               users=dict(c1["users"], ann={"role": "alpha", "verifier": {"kdf": "pbkdf2-sha256", "n": 200000, "salt": vs.hex(),
                                                                          "hash": hashlib.pbkdf2_hmac("sha256", b"ann-2026", vs, 200000).hex()}, "lead": False}))
    seq = int(time.time() * 1000) + 1000
    k = admin_send("adm-2026", new, seq)
    ap, dt = wait_for(lambda: applied(seq), 15)
    m, _ = wait_for(on({"general": "c", "hospitals": "c", "alpha": "c"}))
    check(ap is not None and m is not None and all("ann" in json.load(open(T + "/%s/apps/rangertrak.json" % n))["users"] for n in "abc")
          and k.hex() not in json.dumps([c["bag"] for c in live(ctl(), "config")]),
          "7 the Admin's configuration adopted by a, b and c in %.0f ms (%s); alpha served on its port %s" % (dt * 1000, [(x["member"], x["ok"]) for x in ap or []], m and m.get("alpha")))
    admin_send("not-the-password", dict(new, users={}), seq + 1)
    bad, _ = wait_for(lambda: applied(seq + 1, ok=False), 15)
    check(bad is not None and all("mac" in x["why"] for x in bad) and all(json.load(open(T + "/%s/apps/rangertrak.json" % n))["seq"] == seq for n in "abc"),
          "8 a configuration under the wrong admin password refused by every machine (%s); nothing changed" % ([x["why"][:40] for x in bad or []][:1]))

    oldp = new["ports"]["general"]; newp = 20000 + (oldp - 20000 + 77) % 45000
    new2 = dict(new, ports=dict(new["ports"], general=newp))
    admin_send("adm-2026", new2, seq + 2)
    m, dt = wait_for(lambda: (lambda w: w if w and w.get("general", "").endswith(":%d" % newp) else None)(on({"general": "c", "hospitals": "c", "alpha": "c"})()), 15)
    check(m is not None and not answers("127.0.3.1:%d" % oldp), "9 general's port changed %d -> %d: c serves it on the new one %.0f ms after, the old one closed" % (oldp, newp, dt * 1000))

    new3 = dict(new2, select={"*": {"need": [], "rank": ["-mem_total_kb"]}})
    admin_send("adm-2026", new3, seq + 3)
    m, dt = wait_for(on({"general": "a", "hospitals": "a", "alpha": "a"}), 20)
    check(m is not None, "10 the app's selection -mem_total_kb: every AppRam on a %.0f ms after: %s" % (dt * 1000, m))

    lead = dict(new3, users=dict(new3["users"], pat=dict(new3["users"]["pat"], lead=True)))
    admin_send("adm-2026", lead, seq + 4); wait_for(lambda: applied(seq + 4), 15)
    def place(user, at, guid=""):
        ctl().write("fdf-app", "config", "rangertrak.hospitals", {"at": at, "guid": guid, "user": user, "role": "hospitals", "seq": int(time.time() * 1000), "from": "test"})
    place("pat", "here", "guid-b")
    m, dt = wait_for(on({"general": "a", "hospitals": "b"}), 15)
    place("kim", "here", "guid-c")
    m1, _ = wait_for(on({"general": "a", "hospitals": "a"}), 15)
    place("pat", "here", "guid-b"); wait_for(on({"general": "a", "hospitals": "b"}), 15)
    place("pat", "databasehost")
    m2, dt2 = wait_for(on({"general": "a", "hospitals": "a"}), 15)
    check(m is not None and m1 is not None and m2 is not None,
          "11 pat placed hospitals here on b: hospitalsHost.frognet on b %.0f ms after; a place by kim (not its lead) is no place: the selection (a), not c: %s; "
          "pat placed it on databasehost: the selection again (a) %.0f ms after" % (dt * 1000, bool(m1), dt2 * 1000))

    _, before = cfg_now()
    world["control"] = world["system"] = "127.0.0.3"; t0 = time.monotonic(); elect("split")
    got, dt = wait_for(lambda: (lambda b: b if b and b[0]["bag"]["seq"] == before["seq"] and b[0]["bag"]["mac"] == before["mac"] else None)
                       ([c for c in live(R["127.0.0.3"], "config") if c["instance"] == "rangertrak"]), 15)
    cn, _ = wait_for(lambda: (lambda x: x if len(x) == 9 else None)(sorted(c["instance"] for c in live(R["127.0.0.3"], "can", "rangertrak."))), 15)
    m, _ = wait_for(on({"general": "a", "hospitals": "a", "alpha": "a"}), 15)
    check(bool(got) and bool(cn) and m is not None,
          "12 databasehost-control and the SystemRam moved: the configuration tuple written into the new one by the merge, as signed (%.0f ms); "
          "%d capability tuples held there; the AppHosts the same: %s" % (dt * 1000, len(cn or []), m))

    os.makedirs(T + "/e/apps")
    json.dump({"name": "node-e", "guid": "guid-e", "addrs": [{"ip": "127.0.5.1", "kind": "own"}]}, open(T + "/e/facts.json", "w"))
    open(T + "/nohosts", "w").write("127.0.0.1 localhost\n")
    p = subprocess.run([sys.executable, fa, "install", "rangertrak", "--admin", "boss:adm-2026"], env=dict(env_of("e"), FDF_HOSTS=T + "/nohosts"),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    check(p.returncode != 0 and not os.listdir(T + "/e/apps") and "databasehost-control" in p.stdout,
          "13 install with no databasehost-control named: nothing installed (%s)" % p.stdout.strip().splitlines()[-1:])
finally:
    for p in list(members.values()) + procs:
        if p.poll() is None: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
