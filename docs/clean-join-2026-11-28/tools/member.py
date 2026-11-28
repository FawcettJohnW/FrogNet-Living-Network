#!/usr/bin/env python3
# member.py -- run ON a node with the node's venv (PYTHONPATH=/opt/frognet_semantic). What a full member can do:
#   state            names, roles, routes, the capability rows the ControlRam holds
#   write TAG        write this node's own tuple into the SystemRam
#   hold N SECS TAG  hold a read in the SystemRam until N nodes' TAG tuples are there; says how long the hold waited
import json, subprocess, sys, time
sys.path.insert(0, "/opt/frognet_semantic/core")   # core on the path, bare names, as the node's own programs import it
import frognet_tuples as T
SVC = "membercheck"
def hosts():
    names, roles = {}, {}
    for ln in open("/etc/hosts"):
        f = ln.split()
        if len(f) < 2 or not f[0].startswith("10."): continue
        for t in f[1:]:
            if t.startswith("FrogNetHost."): names[f[0]] = t.split(".", 1)[1]
            if t.endswith(".frognet"): roles[t] = f[0]
    return names, roles
def state():
    names, roles = hosts()
    rt = subprocess.run(["ip", "-4", "route", "show"], capture_output=True, text=True).stdout
    r24 = sorted({l.split()[0] + (" via " + l.split()[l.split().index("via") + 1] if "via" in l.split() else " direct")
                  for l in rt.splitlines() if l.startswith("10.") and "/24" in l.split()[0]})
    dflt = [l for l in rt.splitlines() if l.startswith("default")]
    caps = {}
    try:
        for r in T._values_raw("", "databasehost_control.frognet", timeout=5.0):
            sn = r.get("SensorName", "")
            if sn.startswith("SD:capability.host:"):
                caps.setdefault(sn[len("SD:capability.host:"):].split(":", 1)[0], []).append(r.get("SensorType", ""))
    except Exception as e:
        caps = {"ERROR": "%s: %s" % (type(e).__name__, e)}
    print(json.dumps({"me": T.my_ip(), "names": names, "roles": roles, "routes24": r24, "default": dflt,
                      "capability_at_control": {k: sorted(v) for k, v in caps.items()}}, sort_keys=True))
if sys.argv[1] == "state":
    state()
elif sys.argv[1] == "write":
    ok = T.put(SVC, "hello-" + sys.argv[2], T.node_scope(), {"tag": sys.argv[2], "from": T.my_ip()}, own=False)
    print("write ok=%s from=%s" % (ok, T.my_ip()))
elif sys.argv[1] == "hold":
    import frognet_held as H
    n, secs, tag = int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]; t0 = time.time()
    rows = H.wait_rows(SVC, "databasehost.frognet", name_like="SD:hello-" + tag + ".%", min_rows=n, wait_s=secs)
    print("hold me=%s rows=%d wanted=%d waited=%.2fs from=%s" % (T.my_ip(), len(rows), n, time.time() - t0,
          sorted({(r.get("SensorAddress") or r.get("addr") or "?") for r in rows})))
    sys.exit(0 if len(rows) >= n else 1)
