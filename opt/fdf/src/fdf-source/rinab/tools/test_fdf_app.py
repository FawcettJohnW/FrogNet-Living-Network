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
import json, os, shutil, socket, subprocess, sys, tempfile, time
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

MEM = {"a": 1_000_000, "b": 4_000_000, "c": 8_000_000, "d": 16_000_000}   # d is strongest but has no app
members = {}
def member_env(n):
    return dict(os.environ, RINA_RAM=NET, FDF_FACTS=T + "/%s/facts.json" % n, FDF_VAR=T + "/" + n,
                FDF_APPS=T + "/%s/apps" % n, FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"), FDF_NAME="node-" + n)
def client(n):
    return subprocess.Popen([T + "/bin/fdf-client"], env=member_env(n), stdout=open(T + "/%s/client.log" % n, "a"), stderr=subprocess.STDOUT)
for i, n in enumerate("abcd", start=1):
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": "node-" + n, "guid": "guid-" + n, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}],
               "capability": {"mem_total_kb": MEM[n], "cores": 4}}, open(d + "/facts.json", "w"))
    if n != "d": json.dump({"app": "rangertrak"}, open(d + "/apps/rangertrak.json", "w"))
    members[n] = {"client": client(n)}
time.sleep(1.5)
subprocess.run([os.path.join(F, "bin", "fdf-app"), "config", "rangertrak", "general,hospitals"], env=member_env("a"), check=True)
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

    members["c"]["client"].terminate(); members["c"]["client"].wait()
    t0 = time.monotonic()
    for r in ("general", "hospitals"):
        hp, _ = wait_for(lambda: (lambda w: w if w.startswith("127.0.2.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "2 c's session ended: %s on b (127.0.2.1) %.0f ms after: %s" % (r, (time.monotonic() - t0) * 1000, hp or where(r) or "nothing"))
    gone, _ = wait_for(lambda: all(not answers("127.0.3.1:%d" % p) for p in (21000 + __import__("zlib").crc32(("rangertrak." + r).encode()) % 8000 for r in ("general", "hospitals"))))
    check(bool(gone), "2 c stopped serving both RAMs")

    members["c"]["client"] = client("c")
    t0 = time.monotonic()
    for r in ("general", "hospitals"):
        hp, _ = wait_for(lambda: (lambda w: w if w.startswith("127.0.3.1:") and answers(w) else None)(where(r)))
        check(hp is not None, "3 c is back: %s on c again %.0f ms after its session: %s" % (r, (time.monotonic() - t0) * 1000, hp or where(r) or "nothing"))
    check(not serving("d"), "4 d still never serves")
finally:
    for m in members.values():
        for p in m.values():
            if p.poll() is None: p.terminate()
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
