#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_fdf_app.py -- [APP_DROPLET_V1] fdf-app against a real network RAM (rina-host) and three real members, each with
# its own session (fdf-client) and its own fdf-app serve, its droplets listening on its own loopback .1 (127.0.N.1).
# One app, rangertrak, with two RAMs (general, hospitals); member c has the most memory, b the next, a the least.
#   usage: tools/test_fdf_app.py [BUILD_STATIC_DIR]   (default: rinab/build/static)
# Checks, each with what it saw:
#   1 every RAM is served by exactly one member, the one the selection names (c), and answers there
#   2 c's session ends: each RAM moves to b, within the time printed; c stops serving
#   3 c's session is back: the RAMs move to c again (the selection is deterministic; a stronger member takes it)
#   4 a member without the app is never chosen
#   6 b is cut off (behind b's address for the network RAM there is now b's own side's RAM): b's droplet writes its
#     configuration and capability there again and serves both RAMs on its side; a and c still map them to c. b
#     rejoins: b maps them to c again and stops serving
#   7 [APP_ACCESS_V1] each droplet's configuration tuple carries the access definition: login, the roles, the users
#     each assigned a role with their password's verifier (the password itself nowhere in the network RAM), and never the app's key
#   5 every member with the app has the same map of where each RAM is served (FDF_VAR/app-where.json), the one the pages use
import json, os, shutil, socket, subprocess, sys, tempfile, time
H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "build", "static")
F = os.path.join(H, "..", "fdf")
sys.path.insert(0, os.path.join(F, "lib", "python"))
import fdfram  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netcut import Via  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="fdf-app-")
os.makedirs(T + "/bin")
for b in ("rina-host", "fdf-client", "fdf-where", "fdf-events"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
NET = "127.0.0.1:%d" % free()
procs.append(subprocess.Popen([T + "/bin/rina-host", "--listen", NET, "--api", fdfram.API],
                              env=dict(os.environ, RINA_REGION_FILE=T + "/net.perm", RINA_NET_CONFIG=T + "/none"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/net.log", "w")))
time.sleep(0.5)
rm = fdfram.Ram(NET)
NET_B = "127.0.0.1:%d" % free()   # b's side of a split: another network RAM
procs.append(subprocess.Popen([T + "/bin/rina-host", "--listen", NET_B, "--api", fdfram.API],
                              env=dict(os.environ, RINA_REGION_FILE=T + "/net-b.perm", RINA_NET_CONFIG=T + "/none"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/net-b.log", "w")))
via = {n: Via(NET) for n in "abcd"}   # each member's one address for the network RAM

MEM = {"a": 1_000_000, "b": 4_000_000, "c": 8_000_000, "d": 16_000_000}   # d is strongest but has no app
members = {}
def member_env(n):
    return dict(os.environ, RINA_RAM=via[n].addr, FDF_FACTS=T + "/%s/facts.json" % n, FDF_VAR=T + "/" + n,
                FDF_APPS=T + "/%s/apps" % n, FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"), FDF_NAME="node-" + n)
import threading
up = {}
def client(n):
    """the member's session as its unit runs it (fdf-join: Restart=always): a session that ends is started again"""
    up[n] = True
    def run():
        while up[n]:
            p = subprocess.Popen([T + "/bin/fdf-client"], env=member_env(n), stdout=open(T + "/%s/client.log" % n, "a"), stderr=subprocess.STDOUT)
            members[n]["session"] = p; p.wait()
            if up[n]: time.sleep(0.5)
    members.setdefault(n, {}); threading.Thread(target=run, daemon=True).start()
def client_stop(n):
    up[n] = False; p = members[n]["session"]; p.terminate(); p.wait()
for i, n in enumerate("abcd", start=1):
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": "node-" + n, "guid": "guid-" + n, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}],
               "capability": {"mem_total_kb": MEM[n], "cores": 4}}, open(d + "/facts.json", "w"))
    client(n)
time.sleep(1.5)
for n in "abc":   # the admin's install on each member it names (d is not named: it has no app)
    subprocess.run([os.path.join(F, "bin", "fdf-app"), "config", "rangertrak", "general,hospitals", "--login", "--key", "ab" * 32,
                    "--role", "hospitals", "--role", "command:default",
                    "--user", "pat:beds-2026=hospitals"], env=member_env(n), check=True)
for n in "abcd":
    members[n]["serve"] = subprocess.Popen([os.path.join(F, "bin", "fdf-app"), "serve"], env=member_env(n),
                                           stdout=open(T + "/%s/app.log" % n, "w"), stderr=subprocess.STDOUT)

def where(r):
    out = subprocess.run([os.path.join(F, "bin", "fdf-app"), "where", "rangertrak", r], env=member_env("a"),
                         capture_output=True, text=True).stdout.strip()
    return out
def answers(hp):
    try: return bool(fdfram.Ram(hp).version(timeout=2))
    except Exception: return False
def serving(n):
    return [l for l in open(T + "/%s/app.log" % n) if "serving rangertrak" in l]
def maps_agree():
    want = {"rangertrak." + r: where(r) for r in ("general", "hospitals")}
    got = {n: (json.load(open(T + "/%s/app-where.json" % n)) if os.path.exists(T + "/%s/app-where.json" % n) else None) for n in "abc"}
    return want if all(g is not None and {k: g.get(k) for k in want} == want and g.get("rangertrak.login", "").split(":")[0] == want["rangertrak.general"].split(":")[0]
                       for g in got.values()) else None
def map_of(n):
    try: return json.load(open(T + "/%s/app-where.json" % n))
    except (OSError, ValueError): return None
def wait_for(pred, limit=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        v = pred()
        if v: return v, time.monotonic() - t0
        time.sleep(0.02)
    return None, limit

try:
    for r in ("general", "hospitals"):
        hp, dt = wait_for(lambda: (lambda w: w if w.startswith("127.0.3.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "1 %s served by c (127.0.3.1), answering there: %s (%.2f s from start)" % (r, hp or where(r) or "nothing", dt))
    check(not serving("d"), "4 d (the strongest, no app) never serves: %d serving lines" % len(serving("d")))
    m, _ = wait_for(maps_agree, 5)
    check(m is not None, "5 a, b and c each map the RAMs to c: %s" % m)
    import hashlib
    cf = [c for c in rm.read("fdf-app", "config")]
    txt = json.dumps([c["bag"] for c in cf])
    v = cf[0]["bag"]["users"]["pat"]["verifier"] if cf else {}
    ok_v = bool(v) and hashlib.pbkdf2_hmac("sha256", b"beds-2026", bytes.fromhex(v["salt"]), int(v["n"])).hex() == v["hash"]
    check(len(cf) == 3 and all(c["bag"]["login"] and set(c["bag"]["roles"]) == {"hospitals", "command"} and c["bag"]["users"]["pat"]["role"] == "hospitals" for c in cf)
          and ok_v and "beds-2026" not in txt and "cmd-2026" not in txt and "ab" * 32 not in txt and '"key"' not in txt,
          "7 the access definition in the three droplets' configuration tuples: roles %s, pat assigned hospitals, the verifier checks "
          "the password, no password and no key in the network RAM" % (sorted(cf[0]["bag"]["roles"]) if cf else None))
    # 8 [APP_ACCESS_V1] login at the droplet, and its RAMs' check
    import urllib.request
    def login_at(body):
        hp = map_of("a").get("rangertrak.login", "")
        rq = urllib.request.Request("http://%s/login" % hp, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(rq, timeout=5) as r: return json.loads(r.read())
        except urllib.error.HTTPError as e: return json.loads(e.read())
    def can(hp, grant, svc="t"):
        try: fdfram.Ram(hp, grant=grant).write(svc, "v", "x", {"n": 1}); return True
        except fdfram.RamError as e: return str(e)
    g_pat = login_at({"dev": "dev-pat", "station": "phone-f", "user": "pat", "password": "beds-2026"})
    g_bad = login_at({"dev": "dev-x", "station": "x", "user": "pat", "password": "wrong"})
    g_cmd = login_at({"dev": "dev-c", "station": "eoc-1", "role": "command"})
    g_hosp = login_at({"dev": "dev-h", "station": "x", "role": "hospitals"})
    gen, hosp = map_of("a")["rangertrak.general"], map_of("a")["rangertrak.hospitals"]
    res = {"pat general": can(gen, g_pat.get("grant")), "pat hospitals": can(hosp, g_pat.get("grant")),
           "command hospitals": can(hosp, g_cmd.get("grant")), "no grant general": can(gen, None), "no grant hospitals": can(hosp, None),
           "forged hospitals": can(hosp, (g_cmd.get("grant") or "x.y").split(".")[0] + "." + "0" * 64)}
    GRANT_PAT = g_pat.get("grant")
    check(g_pat.get("role") == "hospitals" and not g_bad.get("ok") and g_cmd.get("role") == "command" and not g_hosp.get("ok")
          and res["pat general"] is True and res["pat hospitals"] is True and res["command hospitals"] is not True
          and res["no grant general"] is not True and res["no grant hospitals"] is not True and res["forged hospitals"] is not True,
          "8 login at the droplet: pat's password gives hospitals, a wrong one nothing (%s), command is a default role (no password), "
          "hospitals is not (%s); the RAMs: %s" % (g_bad.get("why"), g_hosp.get("why"), {k: (v if v is True else v.split("refused: ")[-1][:60]) for k, v in res.items()}))


    client_stop("c")
    t0 = time.monotonic()
    for r in ("general", "hospitals"):
        hp, _ = wait_for(lambda: (lambda w: w if w.startswith("127.0.2.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "2 c's session ended: %s on b (127.0.2.1) %.0f ms after: %s" % (r, (time.monotonic() - t0) * 1000, hp or where(r) or "nothing"))
    hb = map_of("a").get("rangertrak.hospitals", "")
    check(hb.startswith("127.0.2.1:") and can(hb, GRANT_PAT) is True and can(hb, None) is not True,
          "8 relocated to b: pat's grant from c works on b's hospitals RAM with no new login; none still refused")
    gone, _ = wait_for(lambda: all(not answers("127.0.3.1:%d" % p) for p in (21000 + __import__("zlib").crc32(("rangertrak." + r).encode()) % 8000 for r in ("general", "hospitals"))))
    check(bool(gone), "2 c stopped serving both RAMs")

    client("c")
    t0 = time.monotonic()
    for r in ("general", "hospitals"):
        hp, _ = wait_for(lambda: (lambda w: w if w.startswith("127.0.3.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "3 c is back: %s on c again %.0f ms after its session: %s" % (r, (time.monotonic() - t0) * 1000, hp or where(r) or "nothing"))
    check(not serving("d"), "4 d still never serves")
    m, _ = wait_for(maps_agree, 5)
    check(m is not None, "5 after c's return, a, b and c each map the RAMs to c again: %s" % m)

    on_c = m
    via["b"].to(NET_B); t0 = time.monotonic()
    want_b = {k: "127.0.2.1:" + v.split(":")[1] for k, v in on_c.items()}
    mb, _ = wait_for(lambda: {k: (map_of("b") or {}).get(k) for k in want_b} == want_b and all(answers(v) for v in want_b.values()), 20)
    dt = time.monotonic() - t0
    cfg_b = sorted(c["instance"] for c in fdfram.Ram(NET_B).read("fdf-app", "config"))
    check(bool(mb) and cfg_b == ["rangertrak.guid-b"],
          "6 b cut off: b serves both on its side %.0f ms after (%s); the configuration on its side's RAM: %s"
          % (dt * 1000, map_of("b"), cfg_b))
    check(all({k: (map_of(n) or {}).get(k) for k in on_c} == on_c for n in "ac"), "6 a and c still map them to c: %s, %s" % (map_of("a"), map_of("c")))
    via["b"].to(NET); t0 = time.monotonic()
    mb, _ = wait_for(lambda: {k: (map_of("b") or {}).get(k) for k in on_c} == on_c, 20)
    dt = time.monotonic() - t0
    gone, _ = wait_for(lambda: all(not answers(v) for v in want_b.values()), 10)
    check(bool(mb) and bool(gone), "6 b rejoined: b maps them to c again %.0f ms after, and stopped serving: %s" % (dt * 1000, map_of("b")))
finally:
    for n in list(up): up[n] = False
    for m in members.values():
        for p in m.values():
            if p.poll() is None: p.terminate()
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
