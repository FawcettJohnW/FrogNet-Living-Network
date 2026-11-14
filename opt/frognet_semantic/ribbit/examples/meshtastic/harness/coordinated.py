#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""coordinated.py -- the distributed side-by-side: one meshtasticd per machine, a client on each machine talking only
to its own node, every machine's part coordinated through the shared memory the nodes use (space mtx, via bin/mtx).
Nothing connects into any machine. Held reads are the scheduling: a participant waits on its go cell, the coordinator
waits on the member and result families; nobody polls.

  on every machine (its own node, already running on the memory -- or stock, linked however stock links sites):
      coordinated.py participate --run RUN --name NAME --ram HOST:PORT [--node 127.0.0.1:4403] [--mtx bin/mtx]
  anywhere (before or after the participants start; it waits for them):
      coordinated.py coordinate  --run RUN --expect N --ram HOST:PORT [--victim NAME]

The run, all under (mtx, <topic>|RUN, <instance>):
  1. register  participant: (member|RUN, NAME) = {node number, machine}; then a held read on (go|RUN, NAME)
  2. go        coordinator: N members seen -> (go|RUN, NAME) = {t0, members: [{name, num}], victim} for each
  3. scenarios each participant, from t0, against its own node:
       A  every other member's node is in this node's list            seconds from t0
       B  broadcast "CO-NAME"; every other member's token arrives      seconds from t0 per token
       C  DM "CO-NAME-dm" to the next member, want_ack                 ACK seconds, error
       D  the victim stops its node at t0+Td; the others: it leaves    seconds from t0+Td (never -> null)
          the list; the victim comes back at t0+Td+Tb; it is back     seconds from t0+Td+Tb
     result: (result|RUN, NAME) = {A, B, C, D}
  4. judge     coordinator: N results -> every A, B, C passed on every machine; D on every non-victim; the table.
Exit status 0 only if every check passes.
"""
import argparse, json, os, subprocess, sys, threading, time
from pubsub import pub
from meshtastic.tcp_interface import TCPInterface

T0 = time.time()
def log(*a):
    print(f"[{time.time() - T0:8.3f}] [COORD]", *a, flush=True)

def mtx(args, binary, ram, *rest, timeout=None):
    cmd = [binary, args, ram, *rest]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout

def put(binary, ram, topic, instance, obj):
    rc, out = mtx("put", binary, ram, topic, instance, json.dumps(obj, separators=(",", ":")))
    if rc != 0:
        raise RuntimeError(f"mtx put failed: {out}")

def wait_rows(binary, ram, topic, want, seconds):
    """A held read on (mtx, topic): rows as they land, until `want` (a count or an instance) is reached."""
    rc, out = mtx("wait", binary, ram, topic, str(want), str(seconds), timeout=seconds + 10)
    rows = {}
    for line in out.splitlines():
        if line.startswith("ROW "):
            _, _t, inst, js = line.split(" ", 3)
            try:
                rows[inst] = json.loads(js)
            except json.JSONDecodeError:
                rows[inst] = {}
    return rc == 0, rows

def connect(hostport, tries=60):
    host, port = hostport.rsplit(":", 1)
    last = None
    for _ in range(tries):
        try:
            return TCPInterface(hostname=host, portNumber=int(port))
        except Exception as e:  # the node is rebooting
            last = e
            time.sleep(1)
    raise RuntimeError(f"cannot connect {hostport}: {last!r}")

def nodes_seen(hostport):
    i = connect(hostport)
    try:
        return {n["num"] for n in i.nodes.values()} - {i.myInfo.my_node_num}
    finally:
        i.close()

def wait_until(fn, deadline, step=0.5):
    while time.time() < deadline:
        r = fn()
        if r:
            return r, time.time()
        time.sleep(step)
    return None, time.time()

# ------------------------------------------------------------------------------------------------ participant
def participate(a):
    i = connect(a.node)
    me = i.myInfo.my_node_num
    i.close()
    put(a.mtx, a.ram, f"member|{a.run}", a.name, {"num": me, "machine": os.uname().nodename, "node": a.node})
    log(f"registered {a.name} as !{me:08x}; waiting on (mtx, go|{a.run}, {a.name})")
    ok, rows = wait_rows(a.mtx, a.ram, f"go|{a.run}", a.name, a.wait)
    if not ok:
        log("no go; giving up"); return 1
    go = rows[a.name]
    t0, members, victim = go["t0"], go["members"], go.get("victim")
    others = {m["name"]: m["num"] for m in members if m["name"] != a.name}
    order = [m["name"] for m in members]
    nxt = order[(order.index(a.name) + 1) % len(order)]
    res = {"name": a.name, "num": me, "machine": os.uname().nodename}
    log(f"go: t0 in {t0 - time.time():.1f} s, {len(others)} others, victim={victim}")
    while time.time() < t0:
        time.sleep(0.05)

    # A  every other member's node is in this node's list
    want = set(others.values())
    ok, t1 = wait_until(lambda: nodes_seen(a.node) >= want, t0 + go["A_s"], step=1)
    res["A"] = {"pass": bool(ok), "seconds": round(t1 - t0, 2), "missing": sorted(f"!{n:08x}" for n in want - nodes_seen(a.node))}
    log("A", res["A"])

    # B  broadcast a token; collect every other member's token on this node's client
    got, lock = {}, threading.Lock()
    def on_text(packet=None, interface=None, **_):
        txt = packet.get("decoded", {}).get("text", "")
        if txt.startswith("CO-") and not txt.endswith("-dm"):
            with lock:
                got.setdefault(txt, time.time())
    pub.subscribe(on_text, "meshtastic.receive.text")
    i = connect(a.node)
    tb = time.time()
    i.sendText(f"CO-{a.name}", wantAck=False)
    want_tok = {f"CO-{n}" for n in others}
    ok, t2 = wait_until(lambda: want_tok <= set(got), t0 + go["B_s"], step=0.5)
    with lock:
        res["B"] = {"pass": bool(ok), "seconds": round(t2 - tb, 2),
                    "per_token": {k: round(v - tb, 2) for k, v in got.items() if k in want_tok},
                    "missing": sorted(want_tok - set(got))}
    pub.unsubscribe(on_text, "meshtastic.receive.text")
    log("B", res["B"])

    # C  DM to the next member with want_ack
    r, e = {}, threading.Event()
    def cb(p):
        r["p"] = p; r["t"] = time.time(); e.set()
    tc = time.time()
    i.sendData(f"CO-{a.name}-dm".encode(), destinationId=others.get(nxt, me), portNum=1, wantAck=True,
               onResponse=cb, onResponseAckPermitted=True)
    e.wait(go["C_s"])
    i.close()
    if r:
        err = r["p"].get("decoded", {}).get("routing", {}).get("errorReason", "NONE")
        res["C"] = {"pass": err == "NONE", "to": nxt, "error": err, "seconds": round(r["t"] - tc, 2)}
    else:
        res["C"] = {"pass": False, "to": nxt, "error": "no response", "seconds": round(time.time() - tc, 2)}
    log("C", res["C"])

    # D  the victim stops its node; the others see it leave, then come back
    td = t0 + go["D_at"]
    while time.time() < td:
        time.sleep(0.05)
    if a.name == victim:
        if not a.stop_cmd or not a.start_cmd:
            res["D"] = {"pass": False, "role": "victim", "error": "no --stop-cmd/--start-cmd"}
        else:
            subprocess.run(a.stop_cmd, shell=True)
            log("victim: node stopped")
            time.sleep(go["D_back"])
            subprocess.run(a.start_cmd, shell=True)
            log("victim: node started")
            res["D"] = {"pass": True, "role": "victim"}
    elif victim:
        vnum = others[victim]
        gone, t3 = wait_until(lambda: vnum not in nodes_seen(a.node), td + go["D_back"], step=1)
        back, t4 = wait_until(lambda: vnum in nodes_seen(a.node), td + go["D_back"] + go["D_s"], step=1)
        res["D"] = {"pass": bool(gone) and bool(back), "left_seconds": round(t3 - td, 2) if gone else None,
                    "back_seconds": round(t4 - (td + go["D_back"]), 2) if back else None}
    else:
        res["D"] = {"pass": True, "role": "no victim"}
    log("D", res["D"])
    put(a.mtx, a.ram, f"result|{a.run}", a.name, res)
    log("result written")
    return 0

# ------------------------------------------------------------------------------------------------ coordinator
def coordinate(a):
    log(f"waiting for {a.expect} members on (mtx, member|{a.run})")
    ok, members = wait_rows(a.mtx, a.ram, f"member|{a.run}", a.expect, a.wait)
    if not ok:
        log(f"only {len(members)} members"); return 1
    names = sorted(members)
    t0 = time.time() + a.lead
    plan = {"t0": t0, "members": [{"name": n, "num": members[n]["num"]} for n in names], "victim": a.victim,
            "A_s": a.A, "B_s": a.B, "C_s": a.C, "D_at": a.D_at, "D_back": a.D_back, "D_s": a.D}
    for n in names:
        put(a.mtx, a.ram, f"go|{a.run}", n, plan)
    log(f"go to {names}, t0 in {a.lead} s")
    total = a.lead + a.D_at + a.D_back + a.D + 30
    ok, results = wait_rows(a.mtx, a.ram, f"result|{a.run}", a.expect, int(total))
    verdict = ok and all(r.get(k, {}).get("pass") for r in results.values() for k in "ABCD")
    print()
    print(f"| machine | node | A list | B broadcast | C DM ack | D victim gone / back |")
    print(f"|---|---|---|---|---|---|")
    for n in names:
        r = results.get(n, {})
        A, B, C, D = r.get("A", {}), r.get("B", {}), r.get("C", {}), r.get("D", {})
        d = (f"{D.get('left_seconds')} / {D.get('back_seconds')} s" if "left_seconds" in D else D.get("role", "-"))
        print(f"| {n} ({r.get('machine','?')}) | !{r.get('num',0):08x} | {'ok' if A.get('pass') else 'FAIL'} {A.get('seconds')} s | "
              f"{'ok' if B.get('pass') else 'FAIL'} {B.get('seconds')} s | {C.get('error')} {C.get('seconds')} s | {d} |")
    print()
    log("VERDICT", "PASS" if verdict else "FAIL")
    json.dump({"run": a.run, "members": members, "results": results, "pass": verdict},
              open(a.out, "w"), indent=1)
    return 0 if verdict else 1

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("participate")
    p.add_argument("--run", required=True); p.add_argument("--name", required=True); p.add_argument("--ram", required=True)
    p.add_argument("--node", default="127.0.0.1:4403"); p.add_argument("--mtx", default=os.path.join(here, "..", "bin", "mtx"))
    p.add_argument("--wait", type=int, default=3600, help="seconds to wait for go")
    p.add_argument("--stop-cmd", default="", help="victim only: how to stop this machine's node")
    p.add_argument("--start-cmd", default="", help="victim only: how to start it again")
    c = sub.add_parser("coordinate")
    c.add_argument("--run", required=True); c.add_argument("--expect", type=int, required=True); c.add_argument("--ram", required=True)
    c.add_argument("--mtx", default=os.path.join(here, "..", "bin", "mtx")); c.add_argument("--victim", default=None)
    c.add_argument("--wait", type=int, default=3600, help="seconds to wait for members")
    c.add_argument("--lead", type=int, default=5, help="seconds between go and t0")
    c.add_argument("--A", type=int, default=60); c.add_argument("--B", type=int, default=60); c.add_argument("--C", type=int, default=60)
    c.add_argument("--D-at", dest="D_at", type=int, default=150, help="seconds after t0 the victim stops")
    c.add_argument("--D-back", dest="D_back", type=int, default=60, help="seconds the victim stays down")
    c.add_argument("--D", type=int, default=120, help="seconds allowed for it to be back in every list")
    c.add_argument("--out", default="coordinated-result.json")
    a = ap.parse_args()
    sys.exit(participate(a) if a.cmd == "participate" else coordinate(a))

if __name__ == "__main__":
    main()
