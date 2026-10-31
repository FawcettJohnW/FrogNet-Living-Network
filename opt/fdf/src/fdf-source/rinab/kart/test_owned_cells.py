#!/usr/bin/env python3
# test_owned_cells.py RINA_HOST [PORT] -- [OWNED_CELL_GOES_WITH_THE_SESSION_V1] against a rina-host it starts itself:
#   1 a held read on the family fires with {} when the session that wrote a tuple with own=True closes, at once
#   2 a tuple written without own stays when its session closes
#   3 the last session to write a tuple with own owns it: the first one closing leaves it alone
#   4 a plain-HTTP write with own:true is refused, said
#   5 [PERMANENT_CELL_V1] a tuple written permanent=True is in the memory again after the host is killed -9 and started
#     again; one written without it is not
#   6 a permanent write of {} removes the tuple for good: not back after the next restart
#   7 own and permanent together are refused, said
# Prints PASS/FAIL per check with what it saw; exit 0 only if all pass. Needs libkartram.so beside ribbit.py.
import json, os, subprocess, sys, tempfile, threading, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ribbit import Memory, RibbitError

host_bin = sys.argv[1]; port = int(sys.argv[2]) if len(sys.argv) > 2 else 18799
tmp = tempfile.mkdtemp()
env = dict(os.environ, RINA_REGION_FILE=os.path.join(tmp, "t.perm")); env.pop("RINA_MAP_AREA", None)
def start():
    p = subprocess.Popen([host_bin, "--listen", f"127.0.0.1:{port}", "--api", "/ram.php"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0); return p
srv = start()
results = []
def check(name, ok, saw): results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {name}: {saw}", flush=True)

def held_after_close(writer_kw, other=None):
    """reader holds on (t, k, *) after the newest id; writer closes; what does the hold return, and how fast?"""
    w = Memory("127.0.0.1", port); w.write("t", "k", "a", {"x": 1}, **writer_kw)
    if other: other()
    r = Memory("127.0.0.1", port)
    time.sleep(0.3)
    cells = r.read("t", "k", "a"); last = max(c["id"] for c in cells)
    got = {}
    def hold():
        t0 = time.time(); got["cells"] = r.read("t", "k", "a", after=last, wait_s=3.0); got["s"] = time.time() - t0
    th = threading.Thread(target=hold); th.start(); time.sleep(0.3)
    w.close(); th.join()
    now = r.read("t", "k", "a"); r.close()
    return got, now

try:
    got, now = held_after_close({"own": True})
    check("1 own: the hold fires with {} when the writer's session closes",
          bool(got["cells"]) and got["cells"][0]["bag"] == {} and got["s"] < 2.0 and now[0]["bag"] == {},
          f"hold returned {got['cells']} after {got['s']:.2f}s (the writer closed 0.3s into it); the tuple is now {now[0]['bag']}")
    got, now = held_after_close({"own": False})
    check("2 no own: the tuple stays when its session closes", not got["cells"] and now[0]["bag"] == {"x": 1},
          f"hold returned {got['cells']} after {got['s']:.2f}s; the tuple is now {now[0]['bag']}")
    taker = []
    def take(): t = Memory("127.0.0.1", port); t.write("t", "k", "a", {"x": 2}, own=True); taker.append(t)
    got, now = held_after_close({"own": True}, other=take)
    check("3 the last own writer owns it: the first closing leaves it", not got["cells"] and now[0]["bag"] == {"x": 2},
          f"hold returned {got['cells']}; the tuple is now {now[0]['bag']}")
    taker[0].close()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/ram.php?op=write", method="POST",
                                 data=json.dumps({"service": "t", "variable": "k", "instance": "h", "bag": {"x": 1}, "own": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp: body = resp.read().decode()
    except urllib.error.HTTPError as e: body = e.read().decode()
    check("4 plain HTTP with own:true is refused", '"ok":false' in body and "session" in body, body)
    w = Memory("127.0.0.1", port)
    w.write("t", "keep", "a", {"wins": 3}, permanent=True); w.write("t", "keep", "b", {"wins": 1})
    time.sleep(0.3); w.close(); srv.kill(); srv.wait(); srv = start()
    r = Memory("127.0.0.1", port); got = {c["instance"]: c["bag"] for c in r.read("t", "keep")}
    check("5 permanent is back after kill -9 and a restart; the other is not", got == {"a": {"wins": 3}}, f"after the restart: {got}")
    r.write("t", "keep", "a", {}, permanent=True); time.sleep(0.3); r.close(); srv.kill(); srv.wait(); srv = start()
    r = Memory("127.0.0.1", port); got = {c["instance"]: c["bag"] for c in r.read("t", "keep")}; r.close()
    check("6 a permanent {} removes it for good", got == {}, f"after the next restart: {got}")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/ram.php?op=write", method="POST",
                                 data=json.dumps({"service": "t", "variable": "k", "instance": "x", "bag": {"x": 1}, "own": True, "permanent": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp: body = resp.read().decode()
    except urllib.error.HTTPError as e: body = e.read().decode()
    check("7 own and permanent together are refused", '"ok":false' in body and "not both" in body, body)
except RibbitError as e:
    check("the checks ran", False, str(e))
finally:
    srv.terminate(); srv.wait()
print(f"{sum(results)}/{len(results)} passed", flush=True)
sys.exit(0 if all(results) else 1)
