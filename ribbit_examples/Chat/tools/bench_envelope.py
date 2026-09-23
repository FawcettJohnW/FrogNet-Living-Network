#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""bench_envelope.py CLIENT_DIR [SERVER_DIR] [--policy policy.json] [--name TEXT]

The recommended operating envelope for ONE profiled RAM host, derived by
explicit rules from what run_bench.sh measured. No interpretation: a policy
says what is acceptable, each rule finds the highest tested load that stays
inside the policy, and headroom is applied on top. Every line names the runs
and the policy values that produced it, so the conclusion can be checked and
reproduced rather than trusted.

A boundary that the tests never reached is reported as "not reached (>= X)".
A quantity that was not measured is reported as not measured. Nothing is
extrapolated and nothing is filled in.

Writes CLIENT_DIR/envelope.json and CLIENT_DIR/envelope.txt.

Default policy (override any of it with --policy FILE):
  headroom_fraction      0.5     recommend this fraction of each measured boundary
  p99_delivery_ms_max    50      "every value matters": written -> in the reader's hands, 99th percentile
  freshness_p99_ms_max   100     "latest value matters": same measure, its own target
  skip_fraction_max      0.0     when every generation must be seen by a latest-value reader
  cpu_busy_pct_max       60      sustained whole-machine CPU the operator is willing to run at
  recvq_bytes_max        65536   bytes waiting in the port's receive queues: above this a backlog is forming
  payload_budget_ms      20      a value that takes longer than this to move belongs on the Plane
"""
import csv, glob, json, os, statistics, sys, time

DEFAULT = {"headroom_fraction": 0.5, "p99_delivery_ms_max": 50.0, "freshness_p99_ms_max": 100.0, "skip_fraction_max": 0.0,
           "cpu_busy_pct_max": 60.0, "recvq_bytes_max": 65536, "payload_budget_ms": 20.0}


def main(argv):
    pos, policy, name = [], dict(DEFAULT), None
    it = iter(argv)
    for a in it:
        if a == "--policy": policy.update(json.load(open(next(it))))
        elif a == "--name": name = next(it)
        else: pos.append(a)
    if not pos:
        sys.exit(__doc__)
    cdir, sdir = pos[0], (pos[1] if len(pos) > 1 else None)
    runs = {}
    for p in sorted(glob.glob(os.path.join(cdir, "runs", "*.json"))):
        try:
            d = json.load(open(p)); d["_id"] = os.path.basename(p)[:-5]; runs[d.get("label") or d["_id"]] = d
        except ValueError:
            pass
    if not runs:
        sys.exit("no runs/*.json in " + cdir)
    env = json.load(open(os.path.join(cdir, "env.json")))
    srows = list(csv.DictReader(open(os.path.join(sdir, "server_metrics.csv")))) if sdir and os.path.isfile(os.path.join(sdir, "server_metrics.csv")) else []
    senv = json.load(open(os.path.join(sdir, "server_env.json"))) if srows else None

    def server_view(d):
        if not srows: return None
        a, b = d["started_epoch"], d["started_epoch"] + d["seconds"] + d.get("drain_seconds", 0) + 1
        w = [r for r in srows if a <= float(r["epoch"]) <= b]
        if not w: return None
        f = lambda c: [float(r[c]) for r in w if r.get(c) not in (None, "")]
        return {"cpu_busy_pct_mean": statistics.mean(f("cpu_busy_pct")) if f("cpu_busy_pct") else None, "recvq_bytes_max": max(f("port_recvq_bytes") or [0])}

    H = policy["headroom_fraction"]; lines, out, missing = [], {}, []

    def ladder(prefix, key, ok):
        """rows sorted by load; the boundary is the highest load such that it and every lower tested load are inside the policy."""
        rows = sorted(((d[key], d) for k, d in runs.items() if k.startswith(prefix)), key=lambda x: x[0])
        best, first_bad = None, None
        for load, d in rows:
            good, why = ok(d)
            if good and first_bad is None: best = d
            elif not good and first_bad is None: first_bad = (d, why)
        return rows, best, first_bad

    # ---- RULE 1: every value matters (distinct state) -------------------------
    def ok_every(d):
        if d.get("dropped", 0) or d.get("out_of_order", 0) or d.get("delivered_twice", 0) or not d.get("pass"): return False, "integrity: dropped=%s out_of_order=%s" % (d.get("dropped"), d.get("out_of_order"))
        if d["delivery_ms"]["p99"] > policy["p99_delivery_ms_max"]: return False, "p99 delivery %.1f ms > %.0f ms" % (d["delivery_ms"]["p99"], policy["p99_delivery_ms_max"])
        sv = server_view(d)
        if sv and sv["cpu_busy_pct_mean"] is not None and sv["cpu_busy_pct_mean"] > policy["cpu_busy_pct_max"]: return False, "server CPU %.0f%% > %.0f%%" % (sv["cpu_busy_pct_mean"], policy["cpu_busy_pct_max"])
        if sv and sv["recvq_bytes_max"] > policy["recvq_bytes_max"]: return False, "receive queue %d B > %d B (a backlog is forming)" % (sv["recvq_bytes_max"], policy["recvq_bytes_max"])
        return True, ""
    rows, best, bad = ladder("contention_paced_", "writes_per_s", ok_every)
    if not rows:
        missing.append("distinct-state limit: no contention_paced_* runs (re-run run_bench.sh from this kit)")
    elif best is None:
        out["distinct_state"] = {"recommendation": "NOT RECOMMENDED at any tested rate", "reason": bad[1], "runs": [bad[0]["_id"]]}
        lines.append("Distinct state (every value matters):  NOT RECOMMENDED on this configuration -- even the lowest tested rate, %.0f writes/s, is outside policy: %s  [%s]" % (bad[0]["writes_per_s"], bad[1], bad[0]["_id"]))
    else:
        reached = bad is not None; lim = best["writes_per_s"]
        out["distinct_state"] = {"boundary_writes_per_s": lim, "boundary_reached": reached, "recommended_sustained_writes_per_s": H * lim, "p99_ms_at_boundary": best["delivery_ms"]["p99"],
                                 "first_outside_policy": ({"writes_per_s": bad[0]["writes_per_s"], "why": bad[1], "run": bad[0]["_id"]} if reached else None), "runs": [d["_id"] for _, d in rows]}
        lines.append("Distinct state (every value matters):  <= %.0f writes/s sustained   [boundary %s%.0f/s at p99 %.1f ms, run %s%s]" % (
            H * lim, "" if reached else "not reached, >= ", lim, best["delivery_ms"]["p99"], best["_id"], ("; next rung %.0f/s is outside policy: %s" % (bad[0]["writes_per_s"], bad[1])) if reached else ""))
    c = runs.get("contention_noack")
    if c: out["ceiling_writes_per_s"] = {"value": c["writes_per_s"], "run": c["_id"], "note": "writers not waiting; delivery p99 %.0f ms -- a ceiling, not an operating point" % c["delivery_ms"]["p99"]}

    # ---- RULE 2: latest value matters -------------------------------------------
    def ok_skip(d): f = d["skipped"] / max(1, d["writes"]); return (f <= policy["skip_fraction_max"], "skipped %.1f%%" % (100 * f))
    rows, best, bad = ladder("one_pair_paced_", "rate", ok_skip)
    if rows and best:
        out["latest_every_generation_seen"] = {"boundary_per_pair_per_s": best["rate"], "boundary_reached": bad is not None, "recommended_per_pair_per_s": H * best["rate"], "runs": [d["_id"] for _, d in rows]}
        lines.append("Latest value, every generation seen:   <= %.0f writes/s per pair   [no generation skipped through %s%.0f/s, run %s%s]. Above that, generations are skipped by design; use distinct cells if every one matters." % (
            H * best["rate"], "" if bad else ">= ", best["rate"], best["_id"], ("; at %.0f/s %s" % (bad[0]["rate"], bad[1])) if bad else ""))
    elif rows:
        lines.append("Latest value, every generation seen:   NOT AVAILABLE -- generations are skipped even at %.0f/s per pair (%s)  [%s]" % (bad[0]["rate"], bad[1], bad[0]["_id"]))
    def ok_fresh(d): return (d.get("pass") and d["delivery_ms"]["p99"] <= policy["freshness_p99_ms_max"], "p99 freshness %.1f ms > %.0f ms" % (d["delivery_ms"]["p99"], policy["freshness_p99_ms_max"]))
    rows, best, bad = ladder("latest_paced_", "rate_per_pair", ok_fresh)
    if rows and best:
        out["latest_freshness"] = {"boundary_per_pair_per_s": best["rate_per_pair"], "clients": best["clients"], "p99_ms": best["delivery_ms"]["p99"], "boundary_reached": bad is not None, "recommended_per_pair_per_s": H * best["rate_per_pair"]}
        lines.append("Latest value, freshness:               <= %.0f writes/s per pair across %d clients at <= %.0f ms p99   [%s%.0f/s per pair measured at %.1f ms, run %s]" % (
            H * best["rate_per_pair"], best["clients"], policy["freshness_p99_ms_max"], "" if bad else ">= ", best["rate_per_pair"], best["delivery_ms"]["p99"], best["_id"]))
    elif rows:
        lines.append("Latest value, freshness:               NOT RECOMMENDED -- %s at %.0f/s per pair  [%s]" % (bad[1], bad[0]["rate_per_pair"], bad[0]["_id"]))

    # ---- RULE 3: blocking readers --------------------------------------------------
    rows, best, bad = ladder("contention_ack", "clients", ok_every)
    rows = [(n, d) for n, d in rows if not d["_id"].split("_", 1)[1].startswith("contention_paced")]
    if rows and best:
        out["blocking_readers"] = {"highest_tested_inside_policy": best["clients"], "boundary_reached": bad is not None, "runs": [d["_id"] for _, d in rows]}
        lines.append("Blocking readers, all writing flat out: %s%d inside policy (p99 %.1f ms)   [run %s%s]" % ("" if bad else ">= ", best["clients"], best["delivery_ms"]["p99"], best["_id"], ("; %d readers: %s" % (bad[0]["clients"], bad[1])) if bad else "; more were not tested"))
    elif rows:
        lines.append("Blocking readers, all writing flat out: NOT RECOMMENDED -- %d readers: %s  [%s]" % (bad[0]["clients"], bad[1], bad[0]["_id"]))

    # ---- RULE 4: queue grows before the CPU does -------------------------------------
    sweep = sorted(((d["clients"], d, server_view(d)) for k, d in runs.items() if k.startswith("contention_ack")), key=lambda x: x[0])
    sweep = [x for x in sweep if x[2]]
    if len(sweep) >= 2:
        q0, q1 = sweep[0][2]["recvq_bytes_max"], sweep[-1][2]["recvq_bytes_max"]; cpu1 = sweep[-1][2]["cpu_busy_pct_mean"] or 0
        if q1 > policy["recvq_bytes_max"] and q1 > 2 * max(q0, 1) and cpu1 < policy["cpu_busy_pct_max"]:
            out["pattern_warning"] = {"pattern": "receive queue grows with client count before CPU saturates", "recvq_bytes": [q0, q1], "cpu_pct_at_most_clients": cpu1, "runs": [sweep[0][1]["_id"], sweep[-1][1]["_id"]]}
            lines.append("WORKLOAD PATTERN NOT RECOMMENDED:       adding clients grows the receive queue (%d -> %d B from %d to %d clients) while CPU is only %.0f%%: the limit is not compute. Reduce concurrency or use a different RAM host.  [%s, %s]" % (
                q0, q1, sweep[0][0], sweep[-1][0], cpu1, sweep[0][1]["_id"], sweep[-1][1]["_id"]))
    else:
        missing.append("queue-versus-CPU pattern check: needs server_monitor.sh data for the client-count runs")

    # ---- RULE 5: payloads ---------------------------------------------------------------
    s_, b_ = runs.get("rtt_small"), runs.get("rtt_64k")
    if s_ and b_ and b_["ack_ms"]["p50"] > s_["ack_ms"]["p50"]:
        bps = b_["payload_bytes"] / ((b_["ack_ms"]["p50"] - s_["ack_ms"]["p50"]) / 1000.0); S = bps * policy["payload_budget_ms"] / 1000.0
        out["payload"] = {"memory_path_bytes_per_s": bps, "use_plane_above_bytes": S, "runs": [s_["_id"], b_["_id"]]}
        lines.append("Payloads above %s: use the Plane         [the memory path moved %.1f MB/s; %.0f ms budget; runs %s, %s]" % (("%.0f KiB" % (S / 1024)) if S < 1 << 20 else ("%.1f MiB" % (S / (1 << 20))), bps / 1e6, policy["payload_budget_ms"], s_["_id"], b_["_id"]))
    else:
        missing.append("payload threshold: rtt_small / rtt_64k missing or inconclusive")
    lines.append("Recommended sustained CPU ceiling:      %.0f%%   [policy]" % policy["cpu_busy_pct_max"])
    missing.append("maximum recommended cell population: not measured by this bench (no test fills the memory)")
    integ = {"out_of_order": sum(d.get("out_of_order", 0) for d in runs.values()), "delivered_twice": sum(d.get("delivered_twice", 0) for d in runs.values()),
             "dropped_in_distinct_state_runs": sum(d.get("dropped", 0) for k, d in runs.items() if k.startswith("contention_"))}

    prof = {"profile": {"name": name or env.get("label") or "", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "measured_utc": env["started_utc"],
                        "client": env, "server": senv, "runs": len(runs), "server_monitored": bool(srows)},
            "policy": policy, "envelope": out, "integrity": integ, "not_measured": missing}
    json.dump(prof, open(os.path.join(cdir, "envelope.json"), "w"), indent=1)
    txt = ["RECOMMENDED OPERATING ENVELOPE", "", "%s   measured %s from %s against %s:%s%s" % (prof["profile"]["name"] or "(unnamed host)", env["started_utc"], env["client_host"], env["target"]["host"], env["target"]["port"],
           ("; server %s, %s cores" % (senv["cpu_model"], senv["cores"])) if senv else "; server not monitored"), ""] + ["  " + l for l in lines] + ["",
           "  These limits are %.0f%% of each measured boundary (policy headroom_fraction = %s)." % (100 * H, H),
           "  Integrity across all %d runs: out of order %d, delivered twice %d, dropped in distinct-state runs %d." % (len(runs), integ["out_of_order"], integ["delivered_twice"], integ["dropped_in_distinct_state_runs"]), ""]
    if missing: txt += ["  Not measured:"] + ["    - " + m for m in missing] + [""]
    txt += ["  Policy: " + ", ".join("%s=%s" % kv for kv in sorted(policy.items())), "  Every figure above names the run it came from; the runs are in %s/runs/." % os.path.abspath(cdir)]
    open(os.path.join(cdir, "envelope.txt"), "w").write("\n".join(txt) + "\n"); print("\n".join(txt)); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
