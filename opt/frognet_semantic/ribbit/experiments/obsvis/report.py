#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# report.py [RESULTS] [--out REPORT.md] -- the results report, from analyze.py's RESULTS/analysis.json and the runs
# themselves (versions, stress events, ground truth for the stress measures). It states what ran, the AUCs with CIs,
# the costs, each hypothesis and whether it held, and the stress phases.
import argparse, glob, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze, bed, workload  # noqa: E402

CONDS = ["A", "B", "C", "D", "Dall"]
OBS = ["O1", "O2", "O3", "O4"]


def cell(an, cond, traffic, stress=None):
    for c in an["cells"]:
        if c["cond"] == cond and c["traffic"] == traffic and c["stress"] == stress:
            return c


def fmt(r):
    if not r or r.get("auc") is None or r["auc"] != r["auc"]:
        return "n/a"
    return "%.2f [%.2f, %.2f]" % (r["auc"], r["ci95"][0], r["ci95"][1])


NOTES = [
    "**Run length.** Each run here lasted the minutes given above; the brief specifies 30. `./matrix.sh 30` runs the "
    "brief's matrix unchanged.",
    "**netem delay and loss: not run.** The kernel these runs used has no `sch_netem`, so those stress phases fail at "
    "their first step. They run unchanged on a kernel that has it.",
    "**H4: not run.** The Ribbit wire has no padding flag.",
    "**D and D-all are built on ribbit::Space** (`space_c.cpp`, a C ABI over `ribbit_space.hpp`), the one "
    "publish/subscribe library the platform's MQTT and Redis conversions use, in place of the brief's small shim.",
    "**O3 for C, D and D-all is logged at the client library boundary.** chat-ram has no operation trace hook; the log "
    "records exactly the operations the host decodes, and nothing the host would not see.",
    "**O1 in the relayed builds (0.57 everywhere).** p1's link carries only p1's tunnel or broker connection, so the "
    "attacker's only usable feature is the pair's direction (p1 producing or p1 consuming). 0.57 is that base rate: it "
    "says nothing about which peer. Precision at top-k is the same 0.57.",
    "**B's p95 latency (about 40 ms) is Nagle's algorithm in the MQTT clients.** 6.5% of B's deliveries take 39-43 ms, "
    "Linux's delayed-ACK minimum; the rest take under 2 ms. Measured on chat seed 7 (1 minute): Mosquitto's "
    "`set_tcp_nodelay true` alone leaves p95 at 41.8 ms; with TCP_NODELAY also on the paho clients' sockets, p95 is 1.4 "
    "ms. B is reported as stock (both defaults); `OBSVIS_B_NODELAY=1` reproduces the comparison.",
    "**Revocation.** The others' delivery after p8 is revoked is 100% in every run; p8 receives nothing after the "
    "revocation, in C, D and D-all alike.",
    "**Platform bug found: a bag field named `rows` comes back wrapped.** semtpl.hpp's `rebuild_json` special-cases a "
    "top-level `rows` (a port of Python's template_utils). The harness's result chunks use `items` instead. Not fixed "
    "here: it is wire behavior shared with the Python side.",
    "**Earlier held-read build (before the rebuild on ribbit::Space).** In a D-all smoke run built on plain held reads, "
    "every consumer stalled after p8 was revoked. Identical held reads may be coalesced and tied to the revoked session. "
    "Not confirmed, and not present with Space follows (the others received everything).",
]


def below(x, y):
    """x's AUC below y's with non-overlapping CIs"""
    return bool(x and y and x["auc"] == x["auc"] and y["auc"] == y["auc"] and x["auc"] < y["auc"] and x["ci95"][1] < y["ci95"][0])


def stress_measures(results, cond, stress):
    out = []
    for d in sorted(glob.glob(os.path.join(results, "%s-chat-s*-%s" % (cond, stress)))):
        if not os.path.exists(os.path.join(d, "controller.json")): continue
        R = analyze.load(d); ctl = R["ctl"]; ev = ctl.get("stress_events") or []
        if not ev: continue
        t = ev[0]["t"]; tr = R["truth"]
        sends = [e for e in tr if e["ev"] == "send"]; recv = {(e["m"], e["f"], e["c"]): e["t"] for e in tr if e["ev"] == "recv"}
        m = {"seed": ctl["plan"]["seed"]}
        after = [(e["m"], e["f"], c) for e in sends if e["t"] >= t for c in e["to"]]
        m["delivered_after"] = "%d/%d" % (sum(1 for x in after if x in recv), len(after))
        if stress == "server":
            puts = [e for e in R["o3"] if e.get("op") == "put" and e.get("again")]
            m["rewrite_burst_puts"] = len(puts)
            m["rewrite_burst_span_ms"] = round((max(e["t"] for e in puts) - min(e["t"] for e in puts)) * 1000, 1) if puts else None
        if stress == "move":
            nm = analyze.name_of(); gaps = {}
            for p in ev[0]["who"]:
                old = [r[0] for r in R["cap2"] if r[1] == bed.ADDR(p)]; new = [r[0] for r in R["cap2"] if r[1] == bed.ADDR(p, 3)]
                gaps[p] = round((min(new) - max(old)) * 1000, 1) if old and new else None
            m["old_to_new_gap_ms_at_O2"] = gaps
        if stress == "revoke":
            p8 = [e for e in tr if e["ev"] == "recv" and e["c"] == "p8" and e["t"] >= t]
            m["p8_receipts_after_revoke"] = len(p8)
            m["p8_last_receipt_after_s"] = round(max(e["t"] for e in p8) - t, 2) if p8 else None
            m["p8_errors"] = (ctl["results"].get("p8") or {}).get("n_err")
            others = [x for x in after if x[1] != "p8" and x[2] != "p8"]
            m["others_delivered_after"] = "%d/%d" % (sum(1 for x in others if x in recv), len(others))
        out.append(m)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?", default=os.path.join(HERE, "results")); ap.add_argument("--out")
    a = ap.parse_args()
    an = json.load(open(os.path.join(a.results, "analysis.json")))
    runs = sorted(glob.glob(os.path.join(a.results, "*", "run.json")))
    vers = json.load(open(runs[0])) if runs else {}
    minutes = sorted({round(json.load(open(os.path.join(os.path.dirname(r), "controller.json")))["plan"]["dur"] / 60, 2)
                      for r in runs if os.path.exists(os.path.join(os.path.dirname(r), "controller.json"))})
    L = []
    w = L.append
    w("# Observer visibility: direct, server-relayed and FrogNet shared-RAM communication: results\n")
    kernels = sorted({json.load(open(r)).get("kernel") for r in runs} - {None})
    vers["kernel"] = ", ".join(kernels)
    w("Build %s. Analysis %s. Run on kernel(s) %s.\n" % (vers.get("harness"), an.get("build"), vers["kernel"]))
    w("## What ran\n")
    w("Eight participants (p1..p8), a server (srv), a controller (ctl) and a router (rt), each in its own Linux network "
      "namespace with its own stack, joined in a star through rt. Each participant follows 3 of the other 7, drawn from the "
      "seed: 24 true edges among 56 ordered pairs. The attackers try to recover that graph.\n")
    w("Every step of a run was coordinated through a separate control region, a chat-ram of its own in ctl, left out of "
      "every capture. Each namespace's agent advertised itself and took every instruction on one held read. Every result "
      "came back as tuples: ground truth, the operation logs and the capture headers.\n")
    w("The five conditions used the same machines and the same seeded schedule:\n")
    w("- **A:** direct TLS 1.3, producer to each follower.")
    w("- **B:** Mosquitto %s, topic `chat/<recipient>`, TLS 1.3." % vers.get("mosquitto", "").replace("mosquitto version ", ""))
    w("- **C:** chat-ram (%s), the chat example's own schema and client, `ChatServer.<recipient>.<producer>`." % vers.get("chat-ram"))
    w("- **D:** chat-ram on ribbit::Space (the publish/subscribe library the MQTT and Redis conversions use), own truth: "
      "each producer writes its own ring cells, `Chat.<producer>.<producer>.<n mod 64>`; each consumer holds one standing "
      "follow per producer it follows.")
    w("- **D-all:** the same library, read-all: `Chat.Out.<producer>.<n mod 64>`; each consumer holds one standing follow "
      "on `Chat.Out` and filters locally.\n")
    w("C, D and D-all ran over %s tunnels from each participant to srv.\n" % vers.get("wireguard-go"))
    w("Seeds %s trained the attackers; seeds %s were held out. Each run lasted %s minute(s).\n" % (
        "%d-%d" % (an["train"][0], an["train"][-1]), "%d-%d" % (an["test"][0], an["test"][-1]),
        ", ".join(map(str, minutes))))
    w("Versions:\n")
    for k, v in sorted(vers.items()):
        w("- %s: `%s`" % (k, v))
    w("")
    w("## Observers\n")
    w("- **O1:** p1's link, from a tcpdump capture of headers only (`-s 96`). Scored on the 14 pairs that involve p1.")
    w("- **O2:** srv's link, captured the same way. Scored on all 56 pairs. A has no server, so O2 sees nothing for A.")
    w("- **O3:** the server operator. For C, D and D-all, this is the operations the host decodes, logged at the client "
      "library boundary: chat-ram has no operation trace hook. For B it is Mosquitto's own log (`log_type all`). Scored on "
      "all 56 pairs.")
    w("- **O4:** p1 as an authorized insider, reading the whole service. Scored on the 42 pairs without p1.\n")
    w("**Attacker:** a logistic regression on lagged per-pair features. The features are timing at w of 10 ms, 50 ms, "
      "200 ms, 1 s and 2 s, packet-size bins, and the keys each observer can read. It was trained on the training seeds "
      "and scored on the held-out ones.\n")
    w("**Metric:** AUC, with a 95% bootstrap CI over 1000 draws that resample seeds, then pairs within each seed. Timing "
      "correlation alone, with w chosen on the training seeds, is reported beside it.\n")
    for traffic in ("chat", "telemetry"):
        w("## Edge recovery: %s, held-out seeds\n" % traffic)
        w("| condition | O1 | O2 | O3 | O4 | O2 timing alone (w) | O3 timing alone (w) |")
        w("|---|---|---|---|---|---|---|")
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x: continue
            t2, t3 = (x["O2"] or {}).get("timing"), (x["O3"] or {}).get("timing")
            w("| %s | %s | %s | %s | %s | %s | %s |" % (c, *[fmt((x[o] or {}).get("lr")) for o in OBS],
              fmt(t2) + (" (%g s)" % t2["w_s"] if t2 else ""), fmt(t3) + (" (%g s)" % t3["w_s"] if t3 else "")))
        w("")
        w("Precision at top-k (k = the true edges in scope):\n")
        w("| condition | O1 | O2 | O3 | O4 |"); w("|---|---|---|---|---|")
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x: continue
            w("| %s | %s |" % (c, " | ".join(("%.2f" % x[o]["lr"]["precision_at_k"]) if x[o] and x[o]["lr"] and x[o]["lr"]["precision_at_k"] is not None else "n/a" for o in OBS)))
        w("")
        w("Decision rule: a condition reduces linkability for an observer only if its AUC is below B's for that observer "
          "and the two CIs do not overlap.\n")
        w("| condition | O1 | O2 | O3 | O4 |"); w("|---|---|---|---|---|")
        b = cell(an, "B", traffic)
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x or c == "B": continue
            w("| %s | %s |" % (c, " | ".join("yes" if below((x[o] or {}).get("lr"), (b[o] or {}).get("lr") if b else None) else "no" for o in OBS)))
        w("")
        w("Cost (means over held-out runs):\n")
        w("| condition | delivered | wire bytes per delivery | p50 ms | p95 ms | server CPU s | server peak RSS KB |")
        w("|---|---|---|---|---|---|---|")
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x: continue
            k = x["cost"]
            f = lambda v, p="%.0f": (p % v) if v is not None else "n/a"
            w("| %s | %s | %s | %s | %s | %s | %s |" % (c, f(k["delivery_ratio"] and k["delivery_ratio"] * 100, "%.1f%%"), f(k["wire_bytes_per_delivery"]),
              f(k["latency_p50_ms"], "%.1f"), f(k["latency_p95_ms"], "%.1f"), f(k["server_cpu_s"], "%.2f"), f(k["server_rss_peak_kb"])))
        w("")
        failed = [(c["cond"], c["failed_runs"]) for c in an["cells"] if c["traffic"] == traffic and not c["stress"] and c["failed_runs"]]
        if failed:
            w("Runs that failed and were left in as they came: %s\n" % failed)
    w("## Hypotheses (pre-registered in HYPOTHESES.md before seeds 7-10 ran)\n")
    for traffic in ("chat", "telemetry"):
        A, B = cell(an, "A", traffic), cell(an, "B", traffic)
        if not (A and B): continue
        g = lambda c, o: ((cell(an, c, traffic) or {}).get(o) or {}).get("lr")
        h1 = {c: below(g(c, "O1"), g("A", "O1")) for c in ("C", "D", "Dall")}
        h2 = {c: not below(g(c, "O2"), g("B", "O2")) for c in ("C", "D", "Dall")}
        h3 = (below(g("Dall", "O3"), g("D", "O3")), below(g("D", "O3"), g("C", "O3")))
        w("### %s\n" % traffic)
        w("- **H1** (at O1, C, D and D-all lower than A): %s. %s" % ("held" if all(h1.values()) else "did not hold",
          "; ".join("%s %s vs A %s" % (c, fmt(g(c, "O1")), fmt(g("A", "O1"))) for c in h1)))
        w("- **H2** (at O2, C, D and D-all no lower than B): %s. %s" % ("held" if all(h2.values()) else "did not hold",
          "; ".join("%s %s vs B %s" % (c, fmt(g(c, "O2")), fmt(g("B", "O2"))) for c in h2)))
        w("- **H3** (at O3, D-all < D < C): %s. D-all %s, D %s, C %s" % ("held" if all(h3) else "did not hold",
          fmt(g("Dall", "O3")), fmt(g("D", "O3")), fmt(g("C", "O3"))))
        w("- **H4** (the semantic wire's size pattern raises AUC relative to padded sizes): **not run.** The Ribbit wire has "
          "no padding flag.\n")
    w("## Stress phases (chat, held-out seeds)\n")
    w("The attackers are the baseline's, trained on the baseline's training seeds.\n")
    for st in ("netem-delay", "netem-loss", "server", "move", "revoke"):
        rows = [c for c in an["cells"] if c["stress"] == st]
        w("### %s\n" % st)
        if not rows:
            w("Not run.\n"); continue
        w("| condition | O1 | O2 | O3 | O4 | failed runs |"); w("|---|---|---|---|---|---|")
        for c in sorted(rows, key=lambda x: CONDS.index(x["cond"])):
            w("| %s | %s | %s |" % (c["cond"], " | ".join(fmt((c[o] or {}).get("lr")) for o in OBS), c["failed_runs"] or ""))
        w("")
        for c in CONDS:
            ms = stress_measures(a.results, c, st)
            if ms: w("- %s: %s" % (c, "; ".join(json.dumps(m) for m in ms)))
        w("")
    w("## Deviations from the brief, and notes\n")
    for t in NOTES:
        w("- " + t)
    w("")
    txt = "\n".join(L) + "\n"
    if a.out: open(a.out, "w").write(txt)
    else: print(txt)


if __name__ == "__main__":
    main()
