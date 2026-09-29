#!/usr/bin/env python3
"""The LISP service, end to end ([LISP_SERVICE_V1]).
usage: tools/test_lisp_service.py RAM_SERVER_BIN PORT UDP_PORT
Two machines from the same package, one RAM host:
  A  map-server + map-resolver: a site (198.40.0.0/16, key 1), the governor, the UDP front
  B  etr + itr: one database mapping 198.40.1.0/24 -> 192.0.2.41, liveness, a resolver view
Checks ([CAPABILITY_REGISTRATION_V1] -- capabilities are registered, not peers identified):
  1. each role is registered as its own capability tuple, addressed by what reaches it (udp:A:P, xtr:<id>, ...)
  2. after the startup wait each role reads the capabilities it uses: the ETR the map-server (with its site), the ITR
     the map-resolver (at A's UDP front), the map-server the ETR's prefix
  3. heartbeats advance in the capability tuples
  3. an ITR's Encapsulated Map-Request to A's UDP front for 198.40.1.9 is answered with B's locator -- B registered
     natively (a cell, governed by A), not by a Map-Register
  4. B stops cleanly on SIGTERM; after its liveness lifetime A's front stops answering with B's locator
  5. B reads its configuration from /etc/lispers.d/lisp-service.config (the default) and runs with --service: it
     returns at once, runs in the background, logs to logs/lisp-service.log beside the program, pid in
     logs/lisp-service.pid. A is run in the foreground with --config. An existing /etc/lispers.d/lisp-service.config
     is put back afterwards.
"""
import json, os, signal, socket, struct, subprocess, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from compare_lispers import ecm, map_request, parse_map_reply
srv_bin, port, udp = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
API = "/lisper-api"                     # lisper-ram answers its own endpoint, compiled in
fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok: fails.append(what)
srv = subprocess.Popen([srv_bin, "--listen", "127.0.0.1:%d" % port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.5)
tmp = tempfile.mkdtemp()
A = {"name": "ms-a", "ram": {"host": "127.0.0.1", "port": port, "api": API}, "roles": ["map-server", "map-resolver"],
     "udp": {"address": "127.0.0.1", "port": udp}, "heartbeat_s": 1, "startup_wait_s": 5,
     "sites": [{"iid": "0", "prefix": "198.40.0.0/16", "group": "", "accept_more_specifics": True, "key_id": 1, "password": "svc-secret"}],
     "governor": {"iid": "0", "group": ""}}
B = {"name": "etr-b", "ram": {"host": "127.0.0.1", "port": port, "api": API}, "roles": ["etr", "itr"], "heartbeat_s": 1, "startup_wait_s": 5,
     "etr": {"xtr_id": "b0b0", "database_mappings": [{"iid": "0", "prefix": "198.40.1.0/24", "group": "", "rloc_set": ["192.0.2.41"]}],
             "liveness": {"interval_s": 1, "lifetime_s": 4}},
     "itr": {"iids": [{"iid": "0", "group": ""}]}}
import shutil
path_a = os.path.join(tmp, "ms-a.json"); json.dump(A, open(path_a, "w"))
alog = open(os.path.join(tmp, "ms-a.log"), "w+")
pa = subprocess.Popen([os.path.join(here, "lisp-service"), "--config", path_a], stdout=alog, stderr=subprocess.STDOUT)
time.sleep(0.3)
bdir = os.path.join(tmp, "b-install"); os.makedirs(bdir)
shutil.copy(os.path.join(here, "lisp-service"), bdir)
ETC = "/etc/lispers.d/lisp-service.config"; os.makedirs("/etc/lispers.d", exist_ok=True)
saved = open(ETC).read() if os.path.exists(ETC) else None
json.dump(B, open(ETC, "w"))
started = subprocess.run([os.path.join(bdir, "lisp-service"), "--service"], capture_output=True, text=True, timeout=10)
check(started.returncode == 0 and "started, pid" in started.stdout, "--service returns at once: %r" % started.stdout.strip())
time.sleep(0.5)
bpid = int(open(os.path.join(bdir, "logs", "lisp-service.pid")).read())
check(os.path.exists("/proc/%d" % bpid), "--service: the service runs in the background (pid %d from logs/lisp-service.pid)" % bpid)
time.sleep(7)
def log(n):
    if n == "ms-a": alog.flush(); return open(alog.name).read()
    return open(os.path.join(bdir, "logs", "lisp-service.log")).read()
logs = {"ms-a": None, "etr-b": None}
tw = subprocess.Popen([os.path.join(here, "tuple-write"), "--ram", "127.0.0.1", str(port), "--api", API], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def tw_line(o): tw.stdin.write(json.dumps(o) + "\n"); tw.stdin.flush(); return json.loads(tw.stdout.readline())
def get(var, inst): return tw_line({"get": {"service": "lisp", "variable": var, "instance": inst}})["bag"]
def insts(var): return tw_line({"read": {"service": "lisp", "variable": var}})["instances"]
U = "udp:127.0.0.1:%d" % udp
check(insts("capability|map-server") == [U] and insts("capability|map-resolver") == [U], "map-server and map-resolver registered as capabilities at %s" % U)
check(insts("capability|etr") == ["xtr:b0b0"] and get("capability|etr", "xtr:b0b0")["prefixes"] == [{"iid": "0", "prefix": "198.40.1.0/24"}], "etr registered as a capability (xtr:b0b0) with its prefix")
check(len(insts("capability|itr")) == 1, "itr registered as a capability")
check(not insts("capability"), "no per-machine tuple: capabilities only")
bl, al = log("etr-b"), log("ms-a")
check("etr: 1 map-server capability" in bl and "map-server at 127.0.0.1:%d site 198.40.0.0/16 (iid 0)" % udp in bl, "the ETR read the map-server capability it uses")
check("itr: 1 map-resolver capability" in bl and "map-resolver at 127.0.0.1:%d" % udp in bl, "the ITR read the map-resolver capability it uses")
check("map-server: 1 etr capability" in al and "etr 198.40.1.0/24 (iid 0)" in al, "the map-server read the ETR capability and its prefix")
b1 = get("capability|map-server", U)["beat"]; time.sleep(2.5); b2 = get("capability|map-server", U)["beat"]
check(b2 >= b1 + 2, "the heartbeat advances in the capability tuples (%s -> %s)" % (b1, b2))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0)); s.settimeout(2)
def ask(eid, nonce):
    s.sendto(ecm(map_request(eid, nonce, "127.0.0.1"), "127.0.0.1", eid, s.getsockname()[1]), ("127.0.0.1", udp))
    while True:
        d, _ = s.recvfrom(4096)
        if d[4:12] == struct.pack("!Q", nonce): return parse_map_reply(d)[2]
got = None
for k in range(20):
    try: got = ask("198.40.1.9", 0x7100 + k)
    except socket.timeout: got = None
    if got == ["192.0.2.41"]: break
    time.sleep(0.5)
check(got == ["192.0.2.41"], "an ITR's Map-Request to ms-a's UDP front answers etr-b's native registration: %s" % got)
os.kill(bpid, signal.SIGTERM)
for k in range(100):
    if not os.path.exists("/proc/%d" % bpid) or open("/proc/%d/stat" % bpid).read().split()[2] == "Z": break
    time.sleep(0.1)
check("stopped after" in log("etr-b"), "etr-b (--service) stops cleanly on SIGTERM, and says so in logs/lisp-service.log")
gone = False
for k in range(24):
    try: got = ask("198.40.1.9", 0x7200 + k)
    except socket.timeout: got = None
    if got == []: gone = True; break
    time.sleep(0.5)
check(gone, "after etr-b's liveness lifetime, ms-a's front no longer answers with its locator")
pa.send_signal(signal.SIGTERM)
try: pa.wait(timeout=10)
except subprocess.TimeoutExpired: pa.kill()
tw.stdin.close(); srv.kill()
if saved is None: os.remove(ETC)
else: open(ETC, "w").write(saved)
if fails:
    for n in logs: print("---- " + n + "\n" + log(n))
print("\n%s: %d fail" % ("LISP-SERVICE PASS" if not fails else "LISP-SERVICE FAIL", len(fails)))
sys.exit(1 if fails else 0)
