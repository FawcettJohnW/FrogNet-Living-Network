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
#     each assigned a role with their password's verifier (the password itself nowhere in the network RAM)
#   8 [ADMIN_APP_V1] the admin app's configuration, (fdf-app, admin, rangertrak) with the admin key's mac: every member
#     adopts it (a new team role and its RAM, a new user), says so in (fdf-app, applied, ...), and serves the new RAM;
#     the admin key is nowhere in the network RAM, its check value is
#   9 a configuration with a mac from the wrong password: refused by every member, and nothing changes
#  10 [APP_SELECTION_V1] the app's own selection, in the admin's configuration ("select" {"*": rank -mem_total_kb}):
#     every RAM moves to a (the least memory), and a, b and c map them the same
#  11 "need": hospitals needs gpu, which only b's capability has: hospitals on b, the rest stay on a
#  12 the selection itself (fdf_select): a member that does not offer a RAM ("serve") is not a candidate; a member whose
#     tuple lacks a ranked key ranks below every member that has it; the same tuples give the same answer in any order
#  13 [GROUP_PLACE_V1] pat, hospitals' lead, places it "here" on c (the place cell as the door writes it): every member
#     adopts it and hospitals is on c, whatever the selection says; a place written by a user who is not the lead changes nothing
#  14 c's session ends: hospitals is served by nobody (the group went with its lead's device), general and alpha stay on a
#  15 c is back, and pat places hospitals on databasehost: the app's selection again (need gpu: b)
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
               "capability": dict({"mem_total_kb": MEM[n], "cores": 4}, **({"gpu": 1} if n == "b" else {}))}, open(d + "/facts.json", "w"))
    client(n)
time.sleep(1.5)
for n in "abc":   # the admin's install on each member it names (d is not named: it has no app)
    subprocess.run([os.path.join(F, "bin", "fdf-app"), "config", "rangertrak", "general,hospitals", "--login",
                    "--role", "hospitals", "--role", "command:default",
                    "--user", "pat:beds-2026=hospitals", "--admin", "boss:adm-2026"], env=member_env(n), check=True)
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
    return want if all(g is not None and {k: g.get(k) for k in want} == want for g in got.values()) else None   # (check 8 adds a RAM)
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
          and ok_v and "beds-2026" not in txt and "cmd-2026" not in txt,
          "7 the access definition in the three droplets' configuration tuples: roles %s, pat assigned hospitals, the verifier checks "
          "the password, no password in the network RAM" % (sorted(cf[0]["bag"]["roles"]) if cf else None))

    # [ADMIN_APP_V1] what the admin app sends: the configuration as text, a seq, HMAC-SHA256 under the admin key
    import hmac
    def admin_send(pw, cfg, seq):
        salt = hashlib.sha256(b"fdf-app-admin:rangertrak:boss").digest()[:16]
        k = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200000)
        text = json.dumps(cfg)
        rm.write("fdf-app", "admin", "rangertrak", {"config": text, "seq": seq, "user": "boss", "at": int(time.time()),
                                                     "mac": hmac.new(k, ("%s\n%d" % (text, seq)).encode(), hashlib.sha256).hexdigest()})
        return k
    vs = os.urandom(16); kim = {"kdf": "pbkdf2-sha256", "n": 200000, "salt": vs.hex(), "hash": hashlib.pbkdf2_hmac("sha256", b"kim-2026", vs, 200000).hex()}
    new = {"rams": ["general", "hospitals", "alpha"], "partition": "relocate", "login": True,
           "roles": {"hospitals": {"default": False}, "command": {"default": True}, "alpha": {"default": True}},
           "users": dict((u, {"role": b["role"], "verifier": b["verifier"]}) for u, b in cf[0]["bag"]["users"].items())}
    new["users"]["kim"] = {"role": "alpha", "verifier": kim}
    seq = int(time.time() * 1000) + 5000
    k = admin_send("adm-2026", new, seq)
    ap, dt = wait_for(lambda: (lambda a: a if len(a) == 3 and all(x["bag"]["seq"] == seq and x["bag"]["ok"] for x in a) else None)(rm.read("fdf-app", "applied")), 15)
    hp, _ = wait_for(lambda: (lambda w: w if answers(w) else None)(where("alpha")), 15)
    cf2, _ = wait_for(lambda: (lambda c: c if len(c) == 3 and all(x["bag"]["seq"] == seq for x in c) else None)(rm.read("fdf-app", "config")), 10)
    cf2 = cf2 or rm.read("fdf-app", "config")
    chk = hmac.new(k, b"fdf-admin-check", hashlib.sha256).hexdigest()
    alltext = json.dumps([c["bag"] for c in rm.read("fdf-app", "config")] + [c["bag"] for c in rm.read("fdf-app", "admin")])
    check(ap is not None and all(c["bag"]["seq"] == seq and "alpha" in c["bag"]["rams"] and c["bag"]["users"].get("kim", {}).get("role") == "alpha" for c in cf2)
          and hp is not None and k.hex() not in alltext and all(c["bag"]["admin"]["check"] == chk for c in cf2),
          "8 the admin's configuration adopted by a, b and c in %.0f ms (applied: %s); the new team RAM alpha served at %s; the admin key "
          "nowhere in the network RAM, its check value in every configuration tuple" % (dt * 1000, [(x["bag"]["member"], x["bag"]["ok"]) for x in (ap or [])], hp))
    admin_send("not-the-password", dict(new, roles={"hospitals": {"default": True}}, users={}), seq + 1)
    bad, _ = wait_for(lambda: (lambda a: a if len(a) == 3 and all(x["bag"]["seq"] == seq + 1 for x in a) else None)(rm.read("fdf-app", "applied")), 15)
    cf3 = rm.read("fdf-app", "config")
    check(bad is not None and all(not x["bag"]["ok"] and "mac" in x["bag"]["why"] for x in bad) and all(c["bag"]["seq"] == seq and "kim" in c["bag"]["users"] for c in cf3),
          "9 a wrong admin password's configuration refused by every member (%s); the configuration unchanged" % ([x["bag"]["why"][:40] for x in (bad or [])][:1]))

    client_stop("c")
    t0 = time.monotonic()
    for r in ("general", "hospitals"):
        hp, _ = wait_for(lambda: (lambda w: w if w.startswith("127.0.2.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "2 c's session ended: %s on b (127.0.2.1) %.0f ms after: %s" % (r, (time.monotonic() - t0) * 1000, hp or where(r) or "nothing"))
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

    # [APP_SELECTION_V1]
    def on(host, rams):
        return lambda: (lambda ms: ms if all(ms[n] and all(str(ms[n].get("rangertrak." + r, "")).startswith(host[r] + ":") for r in rams) for n in "abc")
                        and all(answers(ms["a"]["rangertrak." + r]) for r in rams) else None)({n: map_of(n) for n in "abc"})
    sel = {"*": {"rank": ["-mem_total_kb"]}}
    t0 = time.monotonic()
    admin_send("adm-2026", dict(new, select=sel), seq + 10)
    rs = ("general", "hospitals", "alpha")
    ms, dt = wait_for(on({r: "127.0.1.1" for r in rs}, rs), 20)
    check(ms is not None, "10 the admin's selection %s: every RAM on a (the least memory) %.0f ms after, the same map on a, b and c: %s"
          % (json.dumps(sel), dt * 1000, map_of("a")))
    sel = {"*": {"rank": ["-mem_total_kb"]}, "hospitals": {"need": ["gpu"], "rank": ["mem_total_kb"]}}
    admin_send("adm-2026", dict(new, select=sel), seq + 11)
    ms, dt = wait_for(on({"general": "127.0.1.1", "alpha": "127.0.1.1", "hospitals": "127.0.2.1"}, rs), 20)
    why = [l.strip() for l in open(T + "/a/app.log") if "RAM hospitals ->" in l][-1:]
    check(ms is not None, "11 hospitals needs gpu: on b %.0f ms after, general and alpha stay on a: %s; a's log: %s" % (dt * 1000, map_of("a"), why))
    import fdf_select, random
    cands = [("g1", {"mem_total_kb": 9, "serve": ["general"]}), ("g2", {"mem_total_kb": 5}), ("g3", {}), ("g4", {"mem_total_kb": "x"})]
    one = fdf_select.select({"*": {"rank": ["mem_total_kb"]}}, "hospitals", cands)
    two = fdf_select.select({"*": {"rank": ["mem_total_kb"]}}, "general", cands)
    low = fdf_select.select({"*": {"rank": ["-mem_total_kb"]}}, "hospitals", cands)
    same = all(fdf_select.select({"*": {"rank": ["-mem_total_kb"]}}, "hospitals", random.sample(cands, len(cands))) == low for _ in range(20))
    bad = []
    for b in ({"*": {"rank": "mem"}}, {"*": {"order": []}}, {"*": {"need": ["-x"]}}, [1]):
        try: fdf_select.check(b)
        except ValueError: bad.append(b)
    check(one == "g2" and two == "g1" and low == "g2" and same and len(bad) == 4,
          "12 the selection: hospitals (g1 offers only general) -> %s; general -> %s; lowest memory -> %s (absent and non-numeric rank last); "
          "the same in any order: %s; %d of 4 bad selections refused" % (one, two, low, same, len(bad)))

    # [GROUP_PLACE_V1]
    lead = dict(new, select=sel, users=dict(new["users"], pat=dict(new["users"]["pat"], lead=True)))
    admin_send("adm-2026", lead, seq + 12)
    wait_for(lambda: (lambda c: c if len(c) == 3 and all(x["bag"]["seq"] == seq + 12 for x in c) else None)(rm.read("fdf-app", "config")), 15)
    def place(user, at, guid=""):
        rm.write("fdf-app", "place", "rangertrak.hospitals", {"at": at, "guid": guid, "user": user, "role": "hospitals",
                                                               "seq": int(time.time() * 1000), "from": "test"})
    place("pat", "here", "guid-c")
    want = {"general": "127.0.1.1", "alpha": "127.0.1.1", "hospitals": "127.0.3.1"}
    ms, dt = wait_for(on(want, rs), 20)
    cfp = [c["bag"].get("places", {}).get("hospitals", {}).get("guid") for c in rm.read("fdf-app", "config")]
    place("kim", "here", "guid-a"); time.sleep(1.5)
    still = (map_of("a") or {}).get("rangertrak.hospitals", "").startswith("127.0.3.1:")
    notlead = any("by 'kim' is not its lead's" in l for l in open(T + "/a/app.log"))
    check(ms is not None and cfp == ["guid-c"] * 3 and still and notlead,
          "13 pat placed hospitals on c: there %.0f ms after (%s); every configuration tuple has it: %s; kim's place (not the lead) changed nothing: %s"
          % (dt * 1000, (map_of("a") or {}).get("rangertrak.hospitals"), cfp, still and notlead))
    client_stop("c")
    gone, dt = wait_for(lambda: all("rangertrak.hospitals" not in (map_of(n) or {"rangertrak.hospitals": 1}) for n in "ab")
                        and all((map_of(n) or {}).get("rangertrak.general", "").startswith("127.0.1.1:") for n in "ab"), 20)
    check(bool(gone), "14 c gone: hospitals served by nobody %.0f ms after (a's map: %s)" % (dt * 1000, map_of("a")))
    client("c")
    wait_for(on(want, rs), 20)
    place("pat", "databasehost")
    ms, dt = wait_for(on({"general": "127.0.1.1", "alpha": "127.0.1.1", "hospitals": "127.0.2.1"}, rs), 20)
    check(ms is not None, "15 pat placed hospitals on databasehost: the selection again, on b (need gpu) %.0f ms after: %s" % (dt * 1000, map_of("a")))
finally:
    for n in list(up): up[n] = False
    for m in members.values():
        for p in m.values():
            if p.poll() is None: p.terminate()
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
