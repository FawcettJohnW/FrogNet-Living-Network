#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# saturation.py -- find the RTT knee for each payload class, and route each to a plane.
#
# The question this answers: how many requests can the origin (Apache) carry at once,
# and how do the template/delta mechanism (SAME/DIFF) and the daemon's fan-out +
# coalescing move that number?  You cannot read it from Apache; you read it from RTT.
# Below the worker ceiling RTT sits on a floor (wire + service time).  Past the ceiling,
# excess requests queue, and each one's RTT grows by the wait.  So RTT is flat, flat,
# then a KNEE, then climbs.  The knee is the concurrency at which the origin saturates.
#
# Five benches, matching pipe_workload's payload taxonomy:
#
#   REST   -- direct to Apache on :8080, no FrogNet.  The baseline the rest is measured
#             against: no template, no coalescing, one worker per request.  Knee at
#             MaxRequestWorkers.
#   SAME   -- identical payload every request (frognet_echo).  Template matches, the
#             reply is RESP_SAME, the delta is empty.  Many concurrent callers coalesce
#             onto ONE in-flight RPC at the daemon; the origin sees ~one.  Knee is late
#             or never -- this is the high number.
#   DIFF-L -- templated, a SMALL delta per request (a few dynamic fields change).  The
#             template coalesces/caches; only the delta crosses the wire.  Knee later
#             than REST (template shared) but earlier than SAME (delta is distinct).
#   DIFF-H -- templated, a LARGE delta per request (many fields change).  Same template
#             win, but the delta is big -- separates the worker-ceiling knee from the
#             bandwidth wall.  DIFF-L vs DIFF-H says whether you hit workers or throughput.
#   RAW    -- no template, opaque payload, sent over the HIGH-SPEED LINK (the reflector),
#             not the memory path.  Nothing to coalesce, so it does not belong on the
#             worker-bound origin at all; the fast plane fans it out send-or-drop.
#
# The verdict per class is the routing rule: a payload whose knee is LATE (SAME, and the
# templated DIFFs to the extent the template dominates) is BOUNDED -- a candidate for the
# reflector / fast plane.  A payload whose knee is at the raw worker ceiling (REST, or a
# DIFF whose delta is most of the bytes) is UNBOUNDED -- slow-plane distinct state.  RAW
# is bounded by definition of where it runs.  That verdict is what the planner consumes.
#
# Usage:
#   saturation.py TARGET [--rest-port 8080] [--reflector HOST:PORT]
#                        [--conc 1,5,10,25,50,100] [--per N] [--json DIR]
#
# TARGET is the templated memory endpoint (the FrogNet path).  --rest-port is the plain
# Apache port for the REST baseline.  --reflector is the fast-plane address for RAW; if
# omitted, RAW is reported as "not measured (no reflector given)".
import argparse, json, os, statistics, sys, threading, time, urllib.request

def now(): return time.time()

def http_rtt(url, body=None, timeout=60):
    t0 = now()
    try:
        req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return (now()-t0)*1000.0, True
    except Exception:
        return (now()-t0)*1000.0, False

def ramp(fetch_one, conc_list, per):
    # For each concurrency level, fire `conc` requests (repeated `per` batches),
    # record p50 RTT.  Return list of (conc, p50_ms, ok, fail).
    rows = []
    for conc in conc_list:
        times, ok, fail = [], 0, 0
        for _ in range(per):
            results = [None]*conc
            def worker(i): results[i] = fetch_one()
            th = [threading.Thread(target=worker, args=(i,)) for i in range(conc)]
            for t in th: t.start()
            for t in th: t.join()
            for ms, good in results:
                if good: times.append(ms); ok += 1
                else: fail += 1
        p50 = statistics.median(times) if times else 0.0
        rows.append((conc, p50, ok, fail))
    return rows

def find_knee(rows):
    # The knee: the first concurrency where p50 rises clearly above the floor.
    # Floor = p50 at the lowest concurrency.  Knee = first level where p50 > 2x floor
    # (and at least +20ms), i.e. queueing has begun.  None if it never lifts.
    if not rows: return None, 0.0
    floor = rows[0][1] or 0.001
    for conc, p50, ok, fail in rows:
        if p50 > max(floor*2.0, floor+20.0):
            return conc, floor
    return None, floor   # never lifted within the tested range

def verdict(name, knee, floor, max_conc):
    if knee is None:
        return f"BOUNDED  -- knee not reached through concurrency {max_conc}: coalesces well; candidate for the fast plane (reflector)."
    if knee <= 5:
        return f"UNBOUNDED-- knee at concurrency {knee}: saturates the origin almost immediately; slow-plane distinct state."
    return f"MIXED    -- knee at concurrency {knee}: the template carries it to {knee} concurrent before the delta saturates the origin."

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--rest-port", type=int, default=8080)
    ap.add_argument("--fn-port", type=int, default=80, help="the FrogNet templated path port")
    ap.add_argument("--reflector", default="", help="HOST:PORT of the fast-plane reflector for RAW")
    ap.add_argument("--conc", default="1,5,10,25,50,100")
    ap.add_argument("--per", type=int, default=4)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    conc_list = [int(x) for x in a.conc.split(",")]
    maxc = max(conc_list)
    host = a.target

    # endpoint map mirrors pipe_workload's payload classes
    EP = {
        "REST":   (f"http://{host}:{a.rest_port}/frognet_echo.php", None),           # baseline, straight to Apache
        "SAME":   (f"http://{host}:{a.fn_port}/frognet_echo.php", None),             # identical -> RESP_SAME
        "DIFF-L": (f"http://{host}:{a.fn_port}/semantic_test_chat.php", None),       # small delta (chat, ~20% new)
        "DIFF-H": (f"http://{host}:{a.fn_port}/semantic_test_telemetry.php", None),  # large delta (200-sensor telem)
    }

    print(f"\nFrogNet saturation -- RTT knee per payload class, and its plane")
    print(f"Target: {host}   REST:{a.rest_port}  FrogNet:{a.fn_port}   concurrency: {conc_list}\n")
    out = {"target": host, "conc": conc_list, "per": a.per, "classes": {}}

    for name,(url,body) in EP.items():
        counter = {"n": 0}
        lock = threading.Lock()
        def fetch_one(url=url, body=body, name=name, counter=counter, lock=lock):
            # DIFF classes send a per-request delta so the template stays shared but the
            # delta differs; SAME/REST send nothing extra so the payload is identical.
            b = body
            if name.startswith("DIFF"):
                with lock: counter["n"] += 1; n = counter["n"]
                pad = "x" * (32 if name=="DIFF-L" else 4096)
                b = json.dumps({"seq": n, "delta": pad}).encode()
            return http_rtt(url, b)
        rows = ramp(fetch_one, conc_list, a.per)
        knee, floor = find_knee(rows)
        print(f"  {name:7s} floor {floor:6.1f} ms")
        for conc,p50,ok,fail in rows:
            mark = "  <- knee" if (knee is not None and conc==knee) else ""
            print(f"      conc {conc:4d}  p50 {p50:8.1f} ms  ok {ok:4d} fail {fail:3d}{mark}")
        v = verdict(name, knee, floor, maxc)
        print(f"      {v}\n")
        out["classes"][name] = {"rows": rows, "knee": knee, "floor_ms": floor, "verdict": v}

    # RAW over the high-speed link
    if a.reflector:
        print(f"  RAW     over the reflector at {a.reflector} (fast plane, send-or-drop)")
        print(f"      RAW rides the high-speed link, off the worker-bound origin entirely.")
        print(f"      (measured by the reflector oracle / fast-plane bench, not the HTTP ramp)")
        out["classes"]["RAW"] = {"transport": "reflector", "reflector": a.reflector,
                                 "verdict": "BOUNDED -- runs on the fast plane by construction; not an Apache request at all."}
    else:
        print(f"  RAW     not measured (pass --reflector HOST:PORT to bench the high-speed link)")
        out["classes"]["RAW"] = {"verdict": "not measured (no reflector given)"}

    print("\nRouting summary (what the planner reads):")
    for name, c in out["classes"].items():
        print(f"  {name:7s} {c.get('verdict','')}")

    # ASSERTION, not a fallback: SAME goes through the daemon and must coalesce, so its
    # knee must be strictly later than REST's (which goes straight to Apache, one worker
    # per request).  If SAME's knee is not later -- same concurrency, or both never bent
    # together -- coalescing did not fire on this path.  That is a failed measurement:
    # report it and exit non-zero.  Do not soften, retry, or assume the daemon is present.
    rest = out["classes"].get("REST", {})
    same = out["classes"].get("SAME", {})
    rk, sk = rest.get("knee"), same.get("knee")
    coalesced = (sk is None and rk is not None) or (sk is not None and rk is not None and sk > rk)
    out["coalescing_observed"] = coalesced
    print()
    if coalesced:
        where = "never bent through the tested range" if sk is None else f"bent at concurrency {sk}"
        print(f"COALESCING OBSERVED: REST bent at concurrency {rk}, SAME {where}. "
              f"The daemon folded concurrent identical requests onto one worker.")
    else:
        print(f"COALESCING NOT OBSERVED: REST knee={rk}, SAME knee={sk}. SAME did not outlast REST, "
              f"so concurrent identical requests were NOT coalesced on this path. Either the daemon "
              f"is not in front of this target or coalescing is not firing. This run does not "
              f"demonstrate coalescing -- treat it as a failed measurement, not a result.")

    if a.json:
        os.makedirs(a.json, exist_ok=True)
        with open(os.path.join(a.json, "saturation.json"), "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {a.json}/saturation.json")

    return 0 if out.get("coalescing_observed") else 1

if __name__ == "__main__":
    sys.exit(main())
