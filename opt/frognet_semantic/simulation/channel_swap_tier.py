#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
channel_swap_tier.py -- [CHANNEL_SWAP_TIER_V1] the channel's setting changed under a running workload, between
machines, and what that cost.

John, 2026-11-19: the channel's setting "is a tuple. A control tuple and part of the system capability tuple", and
"you can absolutely run this test under the simulator".

The machines, their daemons and proxies, their databases and their api.php are wire_between_machines_tier's, unchanged:
the real proxy and the real daemon on each machine, in its own network namespace. This tier adds two things. Each
machine is given two key files, as an owner would place them by hand. And while one machine's programs read and write
through the wire to another machine, the owner writes the channel's tuple (core/channel_setting.py) in the ControlRam.

For each change it reports, side by side:
  learned       from the owner's write returning to the asking machine's proxy reading the change (its held read)
  failed        requests that were not answered 200
  wrong         requests answered 200 with an answer that is not right
  gap before    the longest time with no answer arriving, in the two seconds before the change
  gap after     the same in the two seconds after it
  median        a request's time, before and after
  restarted     daemons or proxies that are not the process they were (must be none)

  python3 simulation/channel_swap_tier.py [--topology trial3] [--links none] [--seconds 8] [--from New-York-1 --to Seattle5]
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wire_between_machines_tier as W  # noqa: E402

START = "auto,databasehost_control.frognet softhoma"       # every machine is told of soft Homa and listens on both
PY, SEM = W.PY, W.SEM
KEYS = {"A": bytes(range(32)), "B": bytes(range(100, 132))}


def owner_writes(m, value):
    """The owner sets the channel, from machine m. Returns (when the write was made, when it had returned)."""
    code = ("import sys, time, json; sys.path.insert(0, %r); from core import channel_setting as C; a = time.time(); "
            "ok = C.write(json.loads(%r)); print('@@', a, time.time(), ok)" % (SEM, json.dumps(value)))
    r = m.exec([PY, "-c", code], env=m.env)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    if not line or not line[0].endswith("True"):
        raise RuntimeError("the owner's write from %s failed: %s %s" % (m.name, r.stdout[-300:], r.stderr[-300:]))
    _, a, b, _ok = line[0].split()
    return float(a), float(b)


def owner_waits(m, value, ends, wait_s=20):
    """The owner's held read on the ends' own tuples (channel, at, *): returns True when `ends` ends say they run
    `value` and none says anything else."""
    code = ("import sys, time, json; sys.path.insert(0, %r); from core import channel_setting as C; T, H = C._tuples(); "
            "want = json.dumps(json.loads(%r), sort_keys=True); pre = T.var_name('at', '')\n"
            "def done(rows):\n"
            "    at = [json.dumps((r.get('data') or {}).get('is'), sort_keys=True) for r in rows if str(r.get('SensorName') or '').startswith(pre)]\n"
            "    return len(at) >= %d and all(a == want for a in at)\n"
            "rows = H.holder().wait_until(C.SERVICE, C.CONTROL_RAM, done, name_like=T.var_name('at', '%%'), wait_s=%f)\n"
            "print('@@', done(rows), time.time(), len(rows))\n" % (SEM, json.dumps(value), ends, wait_s))
    r = m.exec([PY, "-c", code], env=m.env)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    if not line:
        raise RuntimeError("the owner's held read from %s failed: %s %s" % (m.name, r.stdout[-300:], r.stderr[-400:]))
    return line[0].split()[1] == "True", float(line[0].split()[2])


def learned(m, value, since, wait_s=5):
    """When m's proxy and daemon each read this setting (their own log lines), or None."""
    want = json.dumps(value, sort_keys=True)
    t_end, got = time.time() + wait_s, {}
    while time.time() < t_end:
        for who, path in (("proxy", m.proxy_log), ("daemon", m.daemon_log)):
            # anywhere in the text: two threads' lines are sometimes written on one line of the log
            for t in re.findall(r"\[CHANNEL-SETTING\] ([0-9.]+) \S+ [^\n\[]*?: the channel is now " + re.escape(want), open(path, errors="replace").read()):
                if float(t) >= since - 0.5:
                    got.setdefault(who, float(t))
        if len(got) == 2:
            break
        time.sleep(0.05)
    return got


def pids(machines):
    return {m.name: (m.daemon.pid, m.daemon.poll(), m.proxy_main.pid, m.proxy_main.poll()) for m in machines}


LIGHT = False


def workload(a, b, readers=6, writers=3):
    if LIGHT:
        readers, writers = 2, 1
    lists = [[{"target": b.ip, "method": "GET", "path": W.RPATH % (("WA", "WB")[r % 2], 1 + (i + r) % 3)} for i in range(30)]
             for r in range(readers)]
    lists += [[{"target": b.ip, "method": "POST", "path": W.WPATH, "body": json.dumps(
        {"SensorName": "swap%d.%s.from.%s" % (w, b.name, a.name), "SensorType": "WS", "SensorAddress": a.ip,
         "SensorNetwork": a.name, "jsonData": {"v": i}})} for i in range(30)] for w in range(writers)]
    return lists


def gap(done, lo, hi):
    """The longest time with no answer arriving in [lo, hi]."""
    ts = sorted(t for t in done if lo <= t <= hi)
    edges = [lo] + ts + [hi]
    return max(y - x for x, y in zip(edges, edges[1:])) * 1000


def one_change(machines, a, b, owner, label, new, seconds, check):
    before = pids(machines)
    for q in [l[0] for l in workload(a, b) if l[0]["method"] == "POST"]:      # each writer's row is there before they all write at once
        w = a.ask("req", target=b.ip, method=q["method"], path=q["path"], body=q["body"])
        if w.get("status") != 200:
            check(False, "%s: %s could not write its first row on %s: %s" % (label, a.name, b.name, str(w)[:200])); return None
    a.proxy.stdin.write(json.dumps({"op": "timed", "lists": workload(a, b), "seconds": seconds}) + "\n"); a.proxy.stdin.flush()
    time.sleep(seconds / 2.0)
    t_w0, t_w1 = owner_writes(owner, new)
    ans = a.ask(None, wait_s=seconds + 300)
    if not isinstance(ans, list):
        check(False, "%s: the workload on %s did not come back: %s" % (label, a.name, ans)); return None
    got = [g for l in ans for g in l]
    all_run_it, t_all = owner_waits(owner, new, 2 * len(machines))
    heard = {m.name: learned(m, new, t_w0, wait_s=1) for m in machines}
    t_a = heard[a.name].get("proxy")
    failed = [g for g in got if g[2] != 200]
    wrong = [g for g in got if g[2] == 200 and not g[3]]
    done = [g[1] for g in got if g[2] == 200]
    ms_b = sorted((g[1] - g[0]) * 1000 for g in got if g[2] == 200 and g[1] < t_w1)
    ms_a = sorted((g[1] - g[0]) * 1000 for g in got if g[2] == 200 and g[0] >= t_w1)
    after = pids(machines)
    res = {
        "change": label, "requests": len(got), "failed": len(failed), "wrong": len(wrong),
        "learned_ms": None if t_a is None else round((t_a - t_w1) * 1000, 1),
        "learned_by_all_ms": None if any(len(h) < 2 for h in heard.values()) else round((max(max(h.values()) for h in heard.values()) - t_w1) * 1000, 1),
        "gap_before_ms": round(gap(done, t_w1 - 2, t_w1), 1), "gap_after_ms": round(gap(done, t_w1, t_w1 + 2), 1),
        "median_before_ms": round(ms_b[len(ms_b) // 2], 1) if ms_b else None,
        "median_after_ms": round(ms_a[len(ms_a) // 2], 1) if ms_a else None,
        "sent_after": len(ms_a), "restarted": [n for n in before if before[n] != after[n]], "every_end_says_it_runs_it": all_run_it,
        "first_failures": [g[4] for g in failed[:3]],
    }
    fine = not failed and not wrong and not res["restarted"] and t_a is not None and ms_a and all_run_it
    check(fine, "%s, %s -> %s: %d requests, %d failed, %d wrong; %s's proxy read the change %s ms after the owner's write "
                "returned (every daemon and proxy by %s ms); longest time with no answer %s ms in the 2 s before and %s ms "
                "in the 2 s after; a request's median %s ms before and %s ms after (%d sent after); restarted: %s%s"
          % (label, a.name, b.name, res["requests"], res["failed"], res["wrong"], a.name, res["learned_ms"], res["learned_by_all_ms"],
             res["gap_before_ms"], res["gap_after_ms"], res["median_before_ms"], res["median_after_ms"], res["sent_after"],
             res["restarted"] or "none", "" if not failed else " -- " + "; ".join(res["first_failures"]))
          + ("" if all_run_it else " -- NOT every end says it runs the new setting: the owner goes no further"))
    return res if all_run_it else None


def said(m, pattern, since):
    return [float(t) for t in re.findall(pattern, open(m.proxy_log, errors="replace").read()) if float(t) >= since]


def main():
    if "--agent" in sys.argv:
        return W.agent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="trial3")
    ap.add_argument("--links", default="none")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--from", dest="src", default="New-York-1")
    ap.add_argument("--to", dest="dst", default="Seattle5")
    ap.add_argument("--out", default="")
    ap.add_argument("--light", action="store_true",
                    help="for a very slow link: two readers and one writer, and the all-pairs battery is not run")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    global LIGHT
    LIGHT = a.light
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg), flush=True)

    need = W.missing()
    try:
        import cryptography  # noqa: F401
    except ImportError:
        need.append("the Python module cryptography in this Python (AES-256-GCM for the keyed channel)")
    if need:
        print("RESULT FAIL: this tier needs " + "; ".join(need)); return 1
    dbc = json.load(open(os.environ["FROGNET_DB_CONFIG"]))
    tmp = tempfile.mkdtemp(prefix="channel-swap-")
    net = W.net_of(a.topology)
    machines = [W.Machine(i, name, ip, roles, tmp, ifaces) for i, (name, ip, roles, ifaces) in enumerate(net["machines"])]
    W.tear_down(machines)
    results = []
    print("== %s, links %s: %s" % (a.topology, a.links, ", ".join("%s %s" % (m.name, m.ip) for m in machines)), flush=True)
    try:
        W.bring_up(machines, dbc, net)
        for m in machines:                                 # the keys, placed on each machine as an owner places them
            kd = os.path.join(m.dir, "channel_keys"); os.makedirs(kd, exist_ok=True)
            for n, k in KEYS.items():
                with open(os.path.join(kd, n), "wb") as f:
                    f.write(k)
            m.env["FROGNET_CHANNEL_KEYS"] = kd
            # [A_FIRE_REACHES_ITS_HOLDER_V1] held_fire.php looks for a holder's socket in /run/frognet/held, a constant.
            # wire_between_machines_tier gives each machine a held directory of its own (FROGNET_HELD_DIR), so on a
            # machine of that tier every fire is answered 410 "no holder here" and no held read ever returns on a
            # fire. This machine's copy of the file is told its own directory, as its config.php is told its database.
            hf = os.path.join(m.dir, "html", "held_fire.php")
            c = open(hf).read()
            if "const HELD_DIR = '/run/frognet/held';" not in c:
                raise RuntimeError("held_fire.php no longer names its directory as this tier expects")
            open(hf, "w").write(c.replace("const HELD_DIR = '/run/frognet/held';", "const HELD_DIR = '%s';" % m.env["FROGNET_HELD_DIR"]))
        if a.links != "none":
            W.shape_links(machines, a.links, check)
        W.the_merge(net, machines, machines, "the machines found each other; no election yet", roles=False, pause=0)
        time.sleep(1.5)
        for m in machines:
            m.start_wire(START)
        up = [m.ask(None, wait_s=60) for m in machines]

        def answering():
            return [m.ask("req", target=m.ip, method="GET", path=W.RPATH % ("none", 1)).get("status") == 200 for m in machines]
        t_end, ready = time.time() + 60, answering()
        while not all(ready) and time.time() < t_end:
            time.sleep(0.5); ready = answering()
        heard = W.wait_daemons(machines, START)
        check(all(u.get("ready") for u in up) and all(ready) and all(heard.values()),
              "every machine's daemon and proxy are up, told %r" % START)
        W.the_merge(net, machines, machines, "the election ran")
        there = W.wait_for_listeners(machines)
        check(all(there.values()), "every machine's daemon is listening on soft Homa as well as TCP (%s)" % there)

        by = {m.name: m for m in machines}
        src, dst, owner = by[a.src], by[a.dst], machines[0].control
        # ---- the channel's tuple, first written: tcp, no key
        s0 = {"driver": "tcp"}
        t0, _ = owner_writes(owner, s0)
        ran, _t = owner_waits(owner, s0, 2 * len(machines), wait_s=40)
        h = {m.name: learned(m, s0, t0) for m in machines}
        check(ran and all(len(x) == 2 for x in h.values()),
              "the owner (on %s, the ControlRam) wrote the channel's tuple %s and every machine's daemon and proxy read it by "
              "their held reads" % (owner.name, json.dumps(s0)))
        if a.light:
            for t in ("WA", "WB"):                        # what the readers read: 3 rows of each type, written on the far machine
                for i in range(3):
                    r = dst.ask("local", method="POST", path=W.WPATH, body=json.dumps(
                        {"SensorName": "%s.%s.s%d" % (t, dst.name, i), "SensorType": t, "SensorAddress": dst.ip,
                         "SensorNetwork": dst.name, "jsonData": {"v": i, "at": dst.name}}))
                    check(r.get("status") == 200, "seeding %s row %s.%d" % (dst.name, t, i)) if r.get("status") != 200 else None
        else:
            ok = W.battery_fanout(machines, check, tag="under the tuple's tcp, ") and ok

        K = {"driver": "softhoma"}
        steps = [
            ("the driver, tcp to softhoma", dict(K)),
            ("(preparing) key A accepted as well as no key", dict(K, accept=["", "A"])),
            ("a key where there was none: requests now go under key A", dict(K, key="A", accept=["", "A"])),
            ("no key is no longer accepted", dict(K, key="A", accept=["A"])),
            ("(preparing) key B accepted as well as key A", dict(K, key="A", accept=["A", "B"])),
            ("the key changed: requests now go under key B", dict(K, key="B", accept=["A", "B"])),
            ("key A is no longer accepted", dict(K, key="B", accept=["B"])),
            ("(preparing) no key accepted as well as key B", dict(K, key="B", accept=["B", ""])),
            ("the driver, softhoma under key B back to tcp", {"driver": "tcp", "accept": ["B", ""]}),
            ("key B is no longer accepted", {"driver": "tcp"}),
        ]
        for label, new in steps:
            t_mark = time.time()
            r = one_change(machines, src, dst, owner, label, new, a.seconds, check)
            if r is None:
                break
            keyed = said(src, r"\[CHANNEL-KEY\] ([0-9.]+) requests to %s:\d+ now go under" % re.escape(dst.ip), t_mark)
            drained = said(src, r"\[CHANNEL-SETTING\] ([0-9.]+) the session to %s:\d+ set=\d+ on \w+ has nothing outstanding: ended" % re.escape(dst.ip), t_mark)
            r["links"] = a.links
            r["key_lines"] = len(keyed); r["sessions_drained"] = len(drained)
            results.append(r)
        # ---- what the far end holds is what was written, read through the wire and from its own api.php
        path = W.RPATH % ("WS", 1000)
        wire, own = src.ask("req", target=dst.ip, method="GET", path=path), dst.ask("local", method="GET", path=path)
        same = wire.get("status") == 200 and own.get("status") == 200 and json.loads(wire["body"]) == json.loads(own["body"])
        check(same, "after every change, a read through the wire of what %s's writers wrote equals %s's own api.php" % (src.name, dst.name))
        if not a.light:
            ok = W.battery_fanout(machines, check, pairs=[(src, dst), (dst, src)], tag="after every change, ") and ok
    finally:
        if a.keep:
            print("kept: %s; namespaces %s" % (tmp, ", ".join(m.ns for m in machines)))
        else:
            W.tear_down(machines)
            shutil.rmtree(tmp, ignore_errors=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=1)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
