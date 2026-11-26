#!/usr/bin/env python3
"""test_the_apphost_moves.py -- [THE_RAM_IS_A_SERVICE_V1] WorldShepherd's memory as a FrogNet app service, and what its
SARAs do when the service moves.

The app "worldshepherd" is configured with its one RAM (fdf-app config worldshepherd general), installed on three
machines and served by fdf-app on each; the election names the AppHost in the hosts file and fdf-app serves the RAM
there with the app's own RAM host, worldshepherd-ram (fdf-app config --host). Three SARAs (an issuer, a winner, a loser) are RibbitClaimStore() with no address: they find the
RAM as any program on a node does. Then the machine serving it stops, the election names another machine, and that
machine's memory is empty.

 1  the service: one configuration tuple, the AppHost elected, worldshepherd-ram answering on the configured port
 2  before: two tickets issued, one claimed once, the second claim of it refused
 3  the AppHost moves to another machine; every SARA is on the new memory, having written its own cells into it
 4  after: the consumed ticket is refused as consumed, by every SARA; the ticket issued before is claimable, once
 5  claims made while the AppHost moves: each returns an answer, none raises, each ticket has one winner
 6  the serving machine's worldshepherd-ram is killed: fdf-app starts it again on the socket it holds; the same holds

The scaffolding (role hosts on api.php, the hosts file, the election, the machines) is rinab/tools/test_fdf_app.py's.
Needs: root, MariaDB, php with mysqli and curl, fdf-ram and ram in BUILD_STATIC_DIR, Brandon's tree in --sara.
usage: harness/test_the_apphost_moves.py --sara DIR [BUILD_STATIC_DIR]
"""
import argparse, hashlib, hmac, json, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time

ap = argparse.ArgumentParser(); ap.add_argument("--sara", required=True); ap.add_argument("static", nargs="?")
A = ap.parse_args()
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "fdf", "src", "fdf-source", "rinab"))
B = A.static or os.path.join(H, "build", "static")
F = os.path.join(H, "..", "fdf")
FN = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(F, "lib", "python"))
sys.path.insert(0, FN)
os.environ["FROGNET_CORE"] = os.path.normpath(os.path.join(FN, "core"))
sys.path.insert(0, os.environ["FROGNET_CORE"])
import fdfram  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="ws-app-")
os.makedirs(T + "/bin")
for b in ("fdf-ram", "ram"): shutil.copy(os.path.join(B, b), T + "/bin/")
shutil.copy(os.path.join(HERE, "bin", "worldshepherd-ram"), T + "/bin/")   # [THE_APPS_OWN_RAM_HOST_V1] the app's own RAM host, on every machine
procs = []
os.environ["FDF_PY"] = os.path.join(F, "lib", "python")
from core import app_hosts  # noqa: E402
WWW = os.path.normpath(os.path.join(FN, "..", "..", "var", "www", "html"))
PW = "wstest" + str(os.getpid())
def serve_www(ip, files, db=None):
    """php -S on ip:80 serving `files` from this tree's var/www/html; api.php on its own database `db`"""
    d = os.path.join(T, "www-" + ip)
    os.makedirs(d)
    for f in files:
        shutil.copy(os.path.join(WWW, f), d)
    if db:
        cfg = (open(os.path.join(WWW, "config.php")).read().replace("__FROGNET_DB_PASS__", PW)
               .replace("define('DB_NAME', 'FrogNet');", "define('DB_NAME', '%s');" % db)
               .replace("define('DB_USER', 'FrogUser');", "define('DB_USER', 'FaTestUser');"))
        open(os.path.join(d, "config.php"), "w").write(cfg)
        sql = ("DROP DATABASE IF EXISTS %s; CREATE DATABASE %s;" % (db, db) +
               "".join("CREATE USER IF NOT EXISTS 'FaTestUser'@'%s' IDENTIFIED BY '%s'; ALTER USER 'FaTestUser'@'%s' IDENTIFIED BY '%s'; "
                       "GRANT ALL ON %s.* TO 'FaTestUser'@'%s';" % (h, PW, h, PW, db, h) for h in ("localhost", "127.0.0.1")))
        subprocess.run(["mysql", "-e", sql], check=True)
        for f in ("Create_Database.sql", "schema_fixups.sql"):
            subprocess.run("sed 's/`FrogNet`/`%s`/g; s/^USE FrogNet;/USE %s;/' %s | mysql" % (db, db, os.path.join(WWW, f)), shell=True,
                           check=True, stdout=subprocess.DEVNULL)
    procs.append(subprocess.Popen(["php", "-S", "%s:80" % ip, "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/php-%s.log" % ip, "w")))
    for _ in range(100):
        try: socket.create_connection((ip, 80), 0.2).close(); return
        except OSError: time.sleep(0.05)
for ip in ("127.0.0.2", "127.0.0.3"):
    subprocess.run(["ip", "addr", "add", ip + "/8", "dev", "lo"], capture_output=True)
# this node's own .1 on its LAN (a veth pair: its FrogNet address, as frognet_tuples.my_ip finds it -- not lo)
if subprocess.run(["ip", "-o", "addr", "show", "fnt0"], capture_output=True, text=True).stdout.find("10.99.99.1/") < 0:
    subprocess.run(["ip", "link", "add", "fnt0", "type", "veth", "peer", "name", "fnt0p"], capture_output=True)
    subprocess.run(["ip", "addr", "add", "10.99.99.1/24", "dev", "fnt0"], capture_output=True)
    for d in ("fnt0p", "fnt0"): subprocess.run(["ip", "link", "set", d, "up"], capture_output=True)
serve_www("127.0.0.2", ["api.php", "config.php"], db="wstest_a")       # the role hosts
serve_www("127.0.0.3", ["api.php", "config.php"], db="wstest_b")
import frognet_tuples  # noqa: E402
serve_www(frognet_tuples.my_ip(), ["held_fire.php"])                   # this node's own .1: where its holds fire
R = {ip: fdfram.RoleRam(ip) for ip in ("127.0.0.2", "127.0.0.3")}

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
    """the tunnel daemon's two app-tuple reads: held at the role's api.php; the election on every change"""
    at, had = None, None
    while True:
        ip = world["control"] if name == "control" else world["system"]
        rm = R[ip]
        try:
            cs = rm.read("fdf-app", "", "", after=-1 if ip != at else 0, wait_s=0 if ip != at else 5)
            now = sorted((c["id"], c["writer"], json.dumps(c["bag"], sort_keys=True)) for c in cs)
            if ip != at or now != had:
                elect("app tuples (%s)" % name)
            at, had = ip, now
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
    members[n] = subprocess.Popen([sys.executable, fa, "serve", "worldshepherd"], env=env_of(n), stdout=open(T + "/%s/app.log" % n, "a"), stderr=subprocess.STDOUT)
def stop(n):
    p = members.pop(n); p.terminate(); p.wait()
def live(rm, v, pre=""):
    return [c for c in rm.read("fdf-app", v) if not c.get("gone") and c.get("bag") and c["instance"].startswith(pre)]
def hosts():
    try: return {l.split()[1]: l.split()[0] for l in open(HOSTS) if len(l.split()) >= 2}
    except OSError: return {}
def answers(hp):
    try: fdfram.Ram(hp).read("worldshepherd", "sara"); return True      # a read: what every RAM host answers (op=rina is fdf-ram's region's)
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

APP = "worldshepherd"
def cfg():
    b = [c["bag"] for c in live(ctl(), "config") if c["instance"] == APP]
    return json.loads(b[0]["config"]) if b else None
def served_on(n):
    def f():
        c, h = cfg(), hosts().get(app_hosts.host_name(APP, "general"), "")
        w = "%s:%s" % (h, c["ports"]["general"]) if c else ""
        return w if h == "127.0.%d.1" % IDX[n] and answers(w) else None
    return f

try:
    rc, out = run("a", "config", APP, "general", "--host", "general=worldshepherd-ram", "--admin", "boss:adm-2026")
    for n in "bc": run(n, "install", APP, "--admin", "boss:adm-2026")
    for n in "abc": serve(n)
    w0, dt = wait_for(served_on("c"))
    check(rc == 0 and w0 is not None and [x["instance"] for x in live(ctl(), "config")] == [APP],
          "1 the service: one configuration tuple (%s); %sHost.frognet elected on c and worldshepherd-ram answering at %s (%.0f ms)" % (APP, APP, w0, dt * 1000))

    # ---- three SARAs on node a: they find the RAM as fdf_appram does (this hosts file, a's copy of the configuration)
    os.environ.update(FDF_HOSTS=HOSTS, FDF_APPS=T + "/a/apps", FDF_VAR=T + "/a")
    sys.path[:0] = [HERE, os.path.join(HERE, "lib"), A.sara]
    import ribbit_claim_store as S  # noqa: E402
    issuer, winner, loser = (S.RibbitClaimStore(name=n) for n in ("issuer", "winner", "loser"))
    saras = (issuer, winner, loser)
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
    far = time.time() + 250
    def issue(t): issuer.register(ticket_id=t, ticket_sha256=sha(t), expires_at=far)
    def claim(s, t): return s.claim(ticket_id=t, ticket_sha256=sha(t), now=time.time())
    issue("T1"); issue("T2")
    c1, c2 = claim(winner, "T1"), claim(loser, "T1")
    check(c1.ok and not c2.ok and c2.reason == "ticket already consumed",
          "2 before: T1 and T2 issued; T1 claimed by the winner (%s); the loser's claim of it refused: %s" % (c1.reason, c2.reason))

    def all_on(where):
        return lambda: all(s.memory.where.startswith(where) for s in saras) or None

    # ---- claims in flight while the AppHost moves
    K = 40
    for i in range(K): issue("M%d" % i)
    got, raised = {}, []
    def claimer(s, tag):
        for i in range(K):
            try: got[(tag, i)] = claim(s, "M%d" % i)
            except Exception as e: raised.append("%s M%d: %r" % (tag, i, e))
    th = [threading.Thread(target=claimer, args=(s, t)) for s, t in ((winner, "w"), (loser, "l"))]
    for t in th: t.start()
    stop("c"); t0 = time.monotonic()
    w1, dt = wait_for(served_on("b"))
    ok, dt2 = wait_for(all_on(w1 or "-"), 20)
    for t in th: t.join(60)
    wrote = {s.who.split("-")[0]: len(s._mine) for s in saras}
    check(w1 is not None and bool(ok) and w1 != w0,
          "3 c stopped serving: %sHost.frognet on b, worldshepherd-ram at %s %.0f ms after; all three SARAs on that memory %.0f ms after the stop, "
          "each having written its own cells into it (%s)" % (APP, w1, dt * 1000, (time.monotonic() - t0) * 1000, wrote))
    r1 = [claim(s, "T1") for s in saras]
    r2 = [claim(s, "T2") for s in (loser, winner)]
    st = issuer.status("T1")
    check(all(not r.ok and r.reason == "ticket already consumed" for r in r1) and st.get("known") and st.get("consumed")
          and r2[0].ok and not r2[1].ok,
          "4 after: T1, consumed before, is refused as consumed by every SARA (%s); T2, issued before, is claimed once (%s / %s)"
          % (sorted({r.reason for r in r1}), r2[0].reason, r2[1].reason))
    wins = [sum(1 for tag in "wl" if (tag, i) in got and got[(tag, i)].ok) for i in range(K)]
    check(not raised and len(got) == 2 * K and all(w == 1 for w in wins),
          "5 %d claims made while the AppHost moved: %d answered, %d raised; tickets with one winner %d, two %d, none %d %s"
          % (2 * K, len(got), len(raised), wins.count(1), wins.count(2), wins.count(0), raised[:2]))

    # ---- the serving machine's worldshepherd-ram is killed: fdf-app starts it again on the socket it holds
    port = w1.rsplit(":", 1)[1]
    pids = subprocess.run(["pgrep", "-f", "worldshepherd-ram.*%s" % port], capture_output=True, text=True).stdout.split()
    for i in range(K): issue("N%d" % i)
    got2, raised2 = {}, []
    def claimer2(s, tag):
        for i in range(K):
            try: got2[(tag, i)] = claim(s, "N%d" % i)
            except Exception as e: raised2.append("%s N%d: %r" % (tag, i, e))
    th = [threading.Thread(target=claimer2, args=(s, t)) for s, t in ((winner, "w"), (loser, "l"))]
    was = [s.memory for s in saras]
    for t in th: t.start()
    for p in pids: os.kill(int(p), signal.SIGKILL)
    t0 = time.monotonic()
    ok, dt = wait_for(lambda: all(s.memory is not m for s, m in zip(saras, was)) or None, 20)
    for t in th: t.join(60)
    wins = [sum(1 for tag in "wl" if (tag, i) in got2 and got2[(tag, i)].ok) for i in range(K)]
    r1 = [claim(s, "T1") for s in saras] + [claim(s, "T2") for s in saras]
    check(bool(pids) and bool(ok) and not raised2 and len(got2) == 2 * K and all(w == 1 for w in wins)
          and all(not r.ok and r.reason == "ticket already consumed" for r in r1),
          "6 worldshepherd-ram on b killed (pid %s): all three SARAs on the memory fdf-app started %.0f ms after; %d claims across it: %d answered, "
          "%d raised, one winner %d, two %d, none %d; T1 and T2 still consumed (%s) %s"
          % (",".join(pids), dt * 1000, 2 * K, len(got2), len(raised2), wins.count(1), wins.count(2), wins.count(0), sorted({r.reason for r in r1}), raised2[:2]))
finally:
    for p in list(members.values()) + procs:
        if p.poll() is None: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.stdout.flush()
os._exit(1 if FAILS else 0)
