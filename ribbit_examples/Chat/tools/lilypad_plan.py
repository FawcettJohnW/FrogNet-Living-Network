#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""lilypad_plan.py WORKLOAD.json --memory ENVELOPE.json [--persistent ENVELOPE.json] [--path SIM_PROFILE.json]

"I intend to run THIS. Will THIS LilyPad carry it, and if not, what do I change?"

Arithmetic over measured envelopes (bench_envelope.py) and a measured path
(bench_to_sim.py). Each state class in the workload is sent where its semantics
say it belongs and its demand is compared with the limit that was MEASURED for
that host, headroom already applied. Nothing is extrapolated: demand beyond the
range a host was tested over is reported as untested, with the bench command
that would test it -- not as a pass and not as a fail.

WORKLOAD.json
  {"name": "...", "devices": 300,
   "per_device": {"latest_per_s": 20,            current truth; replacing is fine
                  "every_generation": false,     true = a reader must see every one of those
                  "distinct_per_s": 0,           every value matters (its own cell)
                  "persistent_per_s": 2,         must survive a restart -> the persistent target
                  "bulk_bytes_per_s": 51200,     large / streaming
                  "largest_value_bytes": 4096,
                  "blocking_reads": 1}}
"""
import json, sys


def load(p): return json.load(open(p))


def main(argv):
    if not argv: sys.exit(__doc__)
    wl = load(argv[0]); opt = dict(zip(argv[1::2], argv[2::2]))
    if "--memory" not in opt: sys.exit("--memory ENVELOPE.json is required")
    mem = load(opt["--memory"]); per = load(opt["--persistent"]) if "--persistent" in opt else None; path = load(opt["--path"]) if "--path" in opt else None
    n, d = int(wl["devices"]), wl["per_device"]; E, H = mem["envelope"], mem["policy"]["headroom_fraction"]
    out, changes, untested, ok, unknown = [], [], [], True, False
    def line(verdict, what, detail): out.append("  %-9s %s\n            %s" % (verdict, what, detail))
    def host(e): return e["profile"]["name"] or "(unnamed)"

    # -- where each class goes --------------------------------------------------
    latest, distinct, persistent, bulk = n * d.get("latest_per_s", 0), n * d.get("distinct_per_s", 0), n * d.get("persistent_per_s", 0), n * d.get("bulk_bytes_per_s", 0)
    mem_load = latest + distinct + (persistent if per is None else 0)
    ds = E.get("distinct_state")
    if ds and "recommended_sustained_writes_per_s" in ds:
        lim = ds["recommended_sustained_writes_per_s"]; good = mem_load <= lim; ok &= good
        line("OK" if good else "EXCEEDS", "Memory host '%s': %.0f writes/s offered, limit %.0f" % (host(mem), mem_load, lim),
             "latest %.0f + distinct %.0f%s; limit is %.0f%% of the measured boundary %.0f/s%s  [%s]" % (latest, distinct, "" if per else " + persistent %.0f" % persistent, 100 * H,
              ds["boundary_writes_per_s"], "" if ds["boundary_reached"] else " (boundary not reached: the host can do more than was tested)", ds["runs"][-1]))
        if not good:
            if latest and latest >= mem_load - lim: changes.append("Move the latest-value state (%.0f writes/s) to the Plane: it is current truth, replacing is fine, and it is %.0f%% of this host's write load." % (latest, 100 * latest / mem_load))
            else: changes.append("Put the transient Memory on a host whose distinct-state limit is at least %.0f writes/s, or split the devices across %d hosts like this one." % (mem_load, -(-int(mem_load) // int(lim))))
    else:
        unknown = True; line("UNKNOWN", "Memory host '%s': no distinct-state limit in its envelope" % host(mem), "; ".join(mem.get("not_measured", [])) or str(ds))
    if d.get("latest_per_s"):
        key = "latest_every_generation_seen" if d.get("every_generation") else "latest_freshness"; L = E.get(key)
        if L:
            lim = L["recommended_per_pair_per_s"]; good = d["latest_per_s"] <= lim; ok &= good
            line("OK" if good else "EXCEEDS", "Latest-value rate per publisher: %.0f/s, limit %.0f/s (%s)" % (d["latest_per_s"], lim, "every generation must be seen" if d.get("every_generation") else "freshness target %.0f ms p99" % mem["policy"]["freshness_p99_ms_max"]),
                 "measured boundary %.0f/s per pair%s" % (L["boundary_per_pair_per_s"], "" if L["boundary_reached"] else " (not reached)"))
            if not good: changes.append("Readers cannot see every generation at %.0f/s per publisher on this host. Give each value its own cell (distinct state), or accept skipping, or lower the rate to %.0f/s." % (d["latest_per_s"], lim))
    readers = n * d.get("blocking_reads", 0); B = E.get("blocking_readers")
    if readers and B:
        tested = B["highest_tested_inside_policy"]
        if readers <= tested: line("OK", "Blocking readers: %d, and %d were inside policy with every one writing flat out" % (readers, tested), "[%s]" % B["runs"][-1])
        elif B["boundary_reached"]:
            unknown = True; line("UNPROVEN", "Blocking readers: %d needed; with EVERY client writing flat out this host left policy above %d" % (readers, tested), "that is a worst case, not this workload (your writers are paced): it neither passes nor fails until it is measured at your concurrency and rate")
            untested.append("./mesh HOST PORT --every --clients %d --rate %.0f --seconds 10    # your concurrency at your rate" % (min(readers, 500), max(1.0, (d.get("latest_per_s", 0) + d.get("distinct_per_s", 0)) / max(1, n - 1))))
        else:
            unknown = True; line("UNTESTED", "Blocking readers: %d needed; this host was profiled to %d and was still inside policy" % (readers, tested), "not a pass and not a fail: nothing was measured at this concurrency")
            untested.append("./mesh HOST PORT --every --ack --clients %d --seconds 10          # profile the concurrency you intend to run" % min(readers, 500))
    if per is not None and persistent:
        P = per["envelope"].get("distinct_state")
        if P and "recommended_sustained_writes_per_s" in P:
            lim = P["recommended_sustained_writes_per_s"]; good = persistent <= lim; ok &= good
            line("OK" if good else "EXCEEDS", "Persistent target '%s': %.0f writes/s offered, limit %.0f" % (host(per), persistent, lim), "boundary %.0f/s%s" % (P["boundary_writes_per_s"], "" if P["boundary_reached"] else " (not reached)"))
            if not good: changes.append("Limit database-backed updates to %.0f/s in total (%.2f/s per device), or batch them: only what must survive a restart belongs there." % (lim, lim / n))
        else:
            unknown = True; line("UNKNOWN", "Persistent target '%s' has no distinct-state limit in its envelope" % host(per), "; ".join(per.get("not_measured", [])))
            untested.append("./run_bench.sh PERSISTENT_HOST PORT && ./bench_envelope.py ...             # profile it with this kit's bench (paced ladder)")
    elif persistent and per is None:
        line("NOTE", "%.0f persistent writes/s have no persistent target: counted against the Memory host" % persistent, "a restart of a transient host is an empty memory; pass --persistent ENVELOPE.json to plan a database-backed target")
    pay = E.get("payload")
    if pay and d.get("largest_value_bytes", 0) > pay["use_plane_above_bytes"]:
        changes.append("Values of %d bytes are above this host's %.0f-byte threshold: carry them on the Plane and put only the descriptor in Memory." % (d["largest_value_bytes"], pay["use_plane_above_bytes"]))
    if bulk:
        bw = (path or {}).get("network", {}).get("bandwidth_bytes_per_s_up")
        if bw:
            lim = H * bw; good = bulk <= lim; ok &= good
            line("OK" if good else "EXCEEDS", "Bulk data: %.1f MB/s offered, path limit %.1f MB/s" % (bulk / 1e6, lim / 1e6), "%.0f%% of the measured %.1f MB/s (%s)" % (100 * H, bw / 1e6, path["network"].get("bandwidth_source", "")[:90]))
            if not good: changes.append("The path carries %.1f MB/s with headroom and %.1f MB/s is wanted: place a Plane on the devices' side of that link, or reduce bulk to %.0f bytes/s per device." % (lim / 1e6, bulk / 1e6, lim / n))
        else:
            unknown = True; line("UNKNOWN", "Bulk data: %.1f MB/s offered, and no measured path was given" % (bulk / 1e6), "pass --path SIM_PROFILE.json from bench_to_sim.py for the link the devices are behind")
    print("LILYPAD PLAN: %s  --  %d devices\n" % (wl.get("name", ""), n)); print("\n".join(out))
    print("\n  VERDICT: %s" % ("EXCEEDS the measured envelope." if not ok else
                               "NOT ESTABLISHED. Inside every limit that was measured, but part of this workload lies outside what was measured (below). Measure it; do not assume it." if unknown else
                               "inside the measured operating envelope, with the configured headroom."))
    if changes: print("\n  Recommended changes:\n" + "\n".join("    - " + c for c in changes))
    if untested: print("\n  Measure before relying on this:\n" + "\n".join("    " + u for u in untested))
    print("\n  Envelopes: memory measured %s%s. Limits already include %.0f%% headroom." % (mem["profile"]["measured_utc"], ("; persistent measured %s" % per["profile"]["measured_utc"]) if per else "", 100 * H))
    return 1 if not ok else (2 if unknown else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
