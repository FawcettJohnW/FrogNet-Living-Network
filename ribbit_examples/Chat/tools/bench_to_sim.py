#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""bench_to_sim.py CLIENT_DIR [SERVER_DIR] -- measurements in, simulator profile out.

Reads what run_bench.sh (and, if given, server_monitor.sh) recorded on physical
machines and writes, into CLIENT_DIR:

  sim_profile.env    the simulator's OWN knobs, as it already reads them:
                       FROGNET_SIM_LATENCY_MS  FROGNET_SIM_JITTER_MS  FROGNET_SIM_BANDWIDTH_BPS
                       FROGNET_SIM_OUTAGE_PROB FROGNET_SIM_OUTAGE_MS
                     (simulation/transport_sim_tier.py, NetworkParams.from_env). Load with
                         set -a; . sim_profile.env; set +a
                     and the shaped wire behaves like the path that was measured.
  sim_profile.json   the same, plus the SERVER MODEL the measurements support -- service
                     time per write, the aggregate ceiling, the reader's wake quantum, the
                     highest rate at which nothing was skipped, CPU per write, threads/workers
                     per client -- and, for every figure, which run it came from.

Every number says where it came from. A figure that was not measured is left out,
not guessed: the profile lists what is missing under "not_measured".
"""
import csv, glob, json, os, statistics, sys


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cdir = argv[0]; sdir = argv[1] if len(argv) > 1 else None
    runs = {}
    for p in sorted(glob.glob(os.path.join(cdir, "runs", "*.json"))):
        try:
            d = json.load(open(p)); runs[d.get("label") or os.path.basename(p)] = d
        except ValueError:
            pass
    if not runs:
        sys.exit("no runs/*.json in " + cdir)
    path = json.load(open(os.path.join(cdir, "path.json")))
    env = json.load(open(os.path.join(cdir, "env.json")))
    prof = {"source": {"client": env, "client_dir": os.path.abspath(cdir), "server_dir": os.path.abspath(sdir) if sdir else None},
            "network": {}, "server_model": {}, "integrity": {}, "not_measured": []}
    net, srv = prof["network"], prof["server_model"]

    # ---- the path -----------------------------------------------------------
    if path.get("ping"):
        rtt, jit, src = path["ping"]["avg"], path["ping"]["mdev"], "ping, 20 samples"
    else:
        rtt, jit, src = path["tcp_connect"]["median"], path["tcp_connect"]["stdev"], "30 timed TCP connects (ping not available)"
    net["rtt_ms"] = rtt; net["rtt_source"] = src
    net["latency_ms_one_way"] = rtt / 2.0; net["jitter_ms_one_way"] = jit / 2.0
    small, big = runs.get("rtt_small"), runs.get("rtt_64k")
    if small and big and big["ack_ms"]["p50"] > small["ack_ms"]["p50"]:
        extra_s = (big["ack_ms"]["p50"] - small["ack_ms"]["p50"]) / 1000.0
        net["bandwidth_bytes_per_s_up"] = big["payload_bytes"] / extra_s
        net["bandwidth_source"] = "extra acknowledgement time for a %d-byte value over a small one (runs rtt_64k, rtt_small); an estimate that includes the server storing it" % big["payload_bytes"]
    else:
        prof["not_measured"].append("bandwidth (rtt_64k / rtt_small runs missing or inconclusive)")
    failed = [k for k, d in runs.items() if not d.get("pass")]
    net["outage_prob_per_sec"] = 0.0
    net["outage_source"] = "no run lost its connection" if not failed else "runs failed: %s -- inspect before trusting this" % ", ".join(failed)

    # ---- the server ----------------------------------------------------------
    if small:
        srv["service_ms_per_acknowledged_write_light_load"] = max(0.0, small["ack_ms"]["p50"] - rtt)
        srv["ack_ms_light_load"] = small["ack_ms"]
    c = runs.get("contention_ack")
    if c:
        srv["contention_ack"] = {k: c[k] for k in ("clients", "writes_per_s", "reads_per_s", "cells_per_read", "dropped", "delivery_ms", "ack_ms", "drain_seconds")}
        if c["server"]["pid"]:
            srv["cpu_us_per_write_under_contention"] = c["server"]["cpu_us_per_write"]; srv["threads_under_contention"] = c["server"]["threads"]; srv["rss_mib"] = c["server"]["rss_mib"]
    n = runs.get("contention_noack")
    if n:
        srv["aggregate_ceiling_writes_per_s"] = n["writes_per_s"]; srv["unacknowledged_backlog_drain_s"] = n["drain_seconds"]
    scale = sorted((d["clients"], d["writes_per_s"]) for k, d in runs.items() if k.startswith("contention_ack"))
    if len(scale) > 1:
        srv["acknowledged_aggregate_by_clients"] = [{"clients": a, "writes_per_s": b} for a, b in scale]
    paced = sorted((d["rate"], d["skipped"], d["writes"]) for k, d in runs.items() if k.startswith("one_pair_paced_"))
    clean = [r for r, sk, w in paced if sk == 0]
    if paced:
        srv["one_pair_highest_rate_with_nothing_skipped"] = max(clean) if clean else 0
        dirty = [r for r, sk, w in paced if sk > 0]
        if clean and dirty and min(dirty) > max(clean):
            srv["reader_wake_quantum_ms_estimate"] = 1000.0 / min(dirty)
            srv["reader_wake_quantum_source"] = "first paced rate that skipped was %g/s; a reader that re-checks every T loses nothing up to about 1/T" % min(dirty)
        elif not dirty:
            srv["reader_wake_quantum_ms_estimate"] = 0.0
            srv["reader_wake_quantum_source"] = "nothing skipped at any paced rate up to %g/s: the read is woken by the write" % max(clean)
    mp = sorted((d["rate_per_pair"], d["dropped"], d["writes"]) for k, d in runs.items() if k.startswith("latest_paced_"))
    if mp:
        srv["mesh_latest_highest_rate_per_pair_with_nothing_skipped"] = max([r for r, dr, w in mp if dr == 0] or [0])
    f, fa = runs.get("one_pair_noack_flat"), runs.get("one_pair_ack_flat")
    if f and fa:
        srv["one_pair"] = {"ack_writes_per_s": fa["writes_per_s"], "ack_reads_per_s": fa["reads_per_s"], "noack_writes_per_s": f["writes_per_s"], "noack_reads_per_s": f["reads_per_s"],
                           "noack_skipped_fraction": f["skipped"] / max(1, f["writes"]), "predicted_by_1_minus_reads_over_writes": max(0.0, 1 - f["reads_per_s"] / max(1e-9, f["writes_per_s"]))}

    # ---- integrity: the part that is not a number ----------------------------
    prof["integrity"] = {"runs": len(runs), "failed": failed,
                         "out_of_order_anywhere": sum(d.get("out_of_order", 0) for d in runs.values()),
                         "delivered_twice_anywhere": sum(d.get("delivered_twice", 0) for d in runs.values()),
                         "dropped_in_contention_tests": sum(d.get("dropped", 0) for k, d in runs.items() if k.startswith("contention_"))}

    # ---- the server's own view, if it was monitored ---------------------------
    if sdir and os.path.isfile(os.path.join(sdir, "server_metrics.csv")):
        rows = list(csv.DictReader(open(os.path.join(sdir, "server_metrics.csv"))))
        def window(d):
            a, b = d["started_epoch"], d["started_epoch"] + d["seconds"] + d.get("drain_seconds", 0)
            return [r for r in rows if a <= float(r["epoch"]) <= b + 1]
        per = {}
        for k, d in runs.items():
            w = window(d)
            if not w:
                continue
            g = lambda col: [float(r[col]) for r in w if r.get(col) not in (None, "")]
            per[k] = {"samples": len(w), "cpu_busy_pct_mean": statistics.mean(g("cpu_busy_pct")), "cpu_busy_pct_max": max(g("cpu_busy_pct")),
                      "port_recvq_bytes_max": max(g("port_recvq_bytes") or [0]), "port_established_max": max(g("port_established") or [0]),
                      "apache_procs_max": max(g("apache2_procs") or [0]), "db_questions_per_s_mean": statistics.mean(g("db_questions_per_s")) if g("db_questions_per_s") else None,
                      "tcp_retrans_delta": (g("tcp_retrans_segs")[-1] - g("tcp_retrans_segs")[0]) if len(g("tcp_retrans_segs")) > 1 else 0}
            if d.get("writes_per_s") and per[k]["cpu_busy_pct_mean"]:
                per[k]["machine_cpu_ms_per_write"] = 10.0 * per[k]["cpu_busy_pct_mean"] * (json.load(open(os.path.join(sdir, "server_env.json")))["cores"]) / d["writes_per_s"]
        prof["server_observed_per_run"] = per
        prof["source"]["server"] = json.load(open(os.path.join(sdir, "server_env.json")))
    else:
        prof["not_measured"].append("server-side CPU, queues, workers, queries (no server_monitor.sh directory given)")

    json.dump(prof, open(os.path.join(cdir, "sim_profile.json"), "w"), indent=1)
    with open(os.path.join(cdir, "sim_profile.env"), "w") as e:
        e.write("# measured %s, %s -> %s:%s   (%s)\n" % (env["started_utc"], env["client_host"], env["target"]["host"], env["target"]["port"], src))
        e.write("FROGNET_SIM_LATENCY_MS=%.3f\nFROGNET_SIM_JITTER_MS=%.3f\n" % (net["latency_ms_one_way"], net["jitter_ms_one_way"]))
        if "bandwidth_bytes_per_s_up" in net:
            e.write("FROGNET_SIM_BANDWIDTH_BPS=%.0f\n" % net["bandwidth_bytes_per_s_up"])
        e.write("FROGNET_SIM_OUTAGE_PROB=%.4f\nFROGNET_SIM_OUTAGE_MS=500.0\n" % net["outage_prob_per_sec"])
    print(open(os.path.join(cdir, "sim_profile.env")).read().strip())
    print("server model: " + ", ".join("%s=%s" % (k, ("%.3g" % v) if isinstance(v, float) else v) for k, v in srv.items() if not isinstance(v, (dict, list)) and not k.endswith("_source")))
    print("integrity: %s" % prof["integrity"])
    if prof["not_measured"]:
        print("not measured: " + "; ".join(prof["not_measured"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
