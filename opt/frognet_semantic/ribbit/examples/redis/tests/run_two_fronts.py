#!/usr/bin/env python3
"""What one process could not show: one RAM host holding the Redis region, two RESP fronts connected up to it.
Usage: run_two_fronts.py <build-dir> <redis-cli> <base-port>"""
import subprocess, sys, time, socket, os
B, CLI, P = sys.argv[1], sys.argv[2], int(sys.argv[3]); API = "/Fawcett.Redis.ram_interface.php"
def up(port):
    t0 = time.time()
    while time.time() - t0 < 10:
        try: socket.create_connection(("127.0.0.1", port), timeout=1).close(); return True
        except OSError: pass
    return False
env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/ribbit-two-fronts-blobs"); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True)
# RAM_ENDPOINT=host:port runs against a RAM host already running (the deployment, e.g. streamingfrog); otherwise one
# is started here. Keys are prefixed with this run's id so a shared deployment host is never disturbed.
EP = os.environ.get("RAM_ENDPOINT", "")
if EP: host = None
else:
    host = subprocess.Popen([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % P, "--api", API, "--quiet"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not up(P): sys.exit("host did not start")
fenv = dict(env, RIBBIT_RAM=EP or "127.0.0.1:%d" % P)
fa = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P + 1), "--save", ""], env=fenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fb = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P + 2), "--save", ""], env=fenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not (up(P + 1) and up(P + 2)): sys.exit("fronts did not start")
def cli(port, *a): return subprocess.run([CLI, "-p", str(port)] + list(a), capture_output=True, text=True).stdout.strip()
K = "twofronts:%d:" % os.getpid()
fails = 0
def check(n, ok, why=""):
    global fails; print(("PASS " if ok else "FAIL ") + n + (" -- " + why if why else "")); fails += 0 if ok else 1
cli(P + 1, "set", K + "greeting", "written on front A")
check("T1 a write through front A is read through front B (no replication)", cli(P + 2, "get", K + "greeting") == "written on front A")
for _ in range(50): cli(P + 1, "incr", K + "c"); cli(P + 2, "incr", K + "c")
check("T2 100 INCRs split across both fronts: one counter, 100", cli(P + 1, "get", K + "c") == "100" and cli(P + 2, "get", K + "c") == "100")
cli(P + 2, "-n", "3", "set", K + "k3", "v")
check("T3 databases are the region's: db 3 written on B, read on A", cli(P + 1, "-n", "3", "get", K + "k3") == "v")
fa.kill(); fa.wait()
check("T4 front A killed: front B serves everything A wrote, nothing to resync", cli(P + 2, "get", K + "greeting") == "written on front A" and cli(P + 2, "get", K + "c") == "100")
fa = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P + 1), "--save", ""], env=fenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); up(P + 1)
check("T5 front A restarted: serves the same keyspace at once", cli(P + 1, "get", K + "c") == "100")
cli(P + 1, "del", K + "greeting", K + "c"); cli(P + 1, "-n", "3", "del", K + "k3")
for p in (fa, fb, host):
    if p: p.kill()
print("RESULT", "GREEN" if fails == 0 else "RED", fails); sys.exit(1 if fails else 0)
