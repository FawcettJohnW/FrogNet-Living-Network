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

CONDS = ["A", "B", "Bt", "C", "D", "Dall"]
OBS = ["O1", "O2", "O3", "O4"]
NA = "n/a (no server)"


def absent(c, o):
    """A has no server, operator or service: O2, O3 and O4 do not exist for it (not a privacy result)"""
    return c == "A" and o in ("O2", "O3", "O4")


def cell(an, cond, traffic, stress=None):
    for c in an["cells"]:
        if c["cond"] == cond and c["traffic"] == traffic and c["stress"] == stress:
            return c


def verdict(r):
    """a plain word for an AUC and its CI"""
    if not r or r.get("auc") is None or r["auc"] != r["auc"]: return "not measured"
    lo, hi = r["ci95"]
    if lo <= 0.5 <= hi: return "at chance"
    if lo >= 0.9: return "recovered"
    return "partly recovered"


def summary(an, w):
    w("## Glossary\n")
    w("- **Edge:** \"participant j follows participant i\". There are 24 true edges among 56 ordered pairs.")
    w("- **AUC:** how well an observer separates true edges from false ones. 1.00 means every edge is found. 0.50 means "
      "the observer is guessing.")
    w("- **CI:** the 95% confidence interval of the AUC, in brackets.")
    w("- **Held-out seeds:** runs that the observer's model never saw during training (seeds 7-10).")
    w("- **O1:** an observer on one participant's network link. **O2:** an observer on the server's network link. "
      "**O3:** the server's operator. **O4:** a member who is allowed to read the whole service.")
    w("- **Cell:** one value in the shared memory. **Family:** a group of cells. **Follow:** a standing read. The "
      "server sends each new write in the family to the reader.")
    w("")
    w("## Finding\n")
    w("The words below follow the AUC. \"Found\" means the whole CI is at or above 0.90. \"At chance\" means the CI "
      "includes 0.50. \"Partly found\" is everything between.\n")
    word = {"recovered": "found", "at chance": "at chance", "partly recovered": "partly found", "not measured": "not measured"}
    names = (("A", "Direct TLS (A)"), ("B", "MQTT broker (B)"), ("Bt", "MQTT broker, Nagle off (B tuned)"),
             ("C", "Recipient keys on shared memory (C)"),
             ("D", "Own cells, selective follows (D)"), ("Dall", "Own cells, read all (D-all)"))
    for traffic in ("chat", "telemetry"):
        w("**%s workload** (edges found, held-out seeds):\n" % traffic.capitalize())
        w("| build | O1 | O2 | O3 | O4 |")
        w("|---|---|---|---|---|")
        for cond, label in names:
            c = cell(an, cond, traffic)
            if not c: continue
            if cond == "A":
                w("| %s | %s | no server | no server | no server |" % (label, word[verdict(c["O1"]["lr"])]))
            else:
                w("| %s | %s | %s | %s | %s |" % (label, *(word[verdict(c[o]["lr"])] for o in ("O1", "O2", "O3", "O4"))))
        w("")
    w("What this means:\n")
    w("- The schema decides what the server and the members learn.")
    w("- With selective follows (D), each follow names a producer. So the server learns the edges from the follows.")
    w("- With recipient keys (B and C), each key names the recipient. So the operator and any full reader learn the "
      "edges.")
    w("- With read-all (D-all), no key and no follow names a reader. Every reader receives every write. So the server "
      "cannot separate the readers. The cost is more traffic.")
    w("- Encryption on the server's link hides the content and the keys. It does not hide the timing.")
    w("- The exact AUC values are in the tables further down.\n")

    w("## What each observer can see directly\n")
    w("This follows from how each build works (the code in this harness). It is what each observer sees before any "
      "analysis.\n")
    w("| condition | the server's link (O2) | the server operator (O3) | an authorized insider (O4) |")
    w("|---|---|---|---|")
    w("| A | (no server) a participant's link shows its direct peers' addresses | (none) | (none) |")
    w("| B | each client's TLS connection to the broker: address, timing, sizes | client identities; topics `chat/<recipient>` "
      "on every publish (sender and recipient); each client's subscription `chat/<self>`; timing and sizes | with `chat/#`: "
      "every topic (the recipient) and payload (the sender) |")
    w("| C | each participant's tunnel to srv: address, timing, sizes | the connection (one participant per tunnel); keys "
      "`ChatServer.<recipient>.<producer>` on every write and read (both ends); timing and sizes | the whole service's "
      "keys: both ends of every message |")
    w("| D | as C | each producer's writes to its own family; each consumer's standing follow on each producer's family it "
      "reads (the graph, by name); the batch each follow releases; timing and sizes | producers' cells and their bags "
      "(the sender); nothing about consumers |")
    w("| B tuned | as B | as B | as B |")
    w("| D-all | as C | each producer's writes to `Chat.Out`; one follow on `Chat.Out` per consumer (no producer named); "
      "every write released to every consumer; timing and sizes | as D |")
    w("")
    w("Identities: in B, C, D and D-all every connection is one participant (its broker client id or its tunnel address), "
      "so the operator always knows who is connected and who writes. What differs is whether keys, follows or delivery "
      "carry who reads whom.\n")


def compare(an, prev, w):
    """the earlier build's results beside these: what the harness corrections changed"""
    w("## Effect of the harness corrections\n")
    w("The earlier build (%s analysis) had the errors listed in the notes. This table shows its held-out results beside "
      "the corrected build's. AUC is shown as earlier -> corrected. Wire bytes are per delivery.\n" % prev.get("build"))
    w("| build | workload | O1 | O2 | O3 | O4 | wire bytes |")
    w("|---|---|---|---|---|---|---|")
    a = lambda x, o: ("%.2f" % x[o]["lr"]["auc"]) if x and x.get(o) and x[o].get("lr") else "n/a"
    for traffic in ("chat", "telemetry"):
        for c in CONDS:
            x, y = cell(prev, c, traffic), cell(an, c, traffic)
            if not (x and y): continue
            obs = [NA if absent(c, o) else "%s -> %s" % (a(x, o), a(y, o)) for o in OBS]
            bx, by = x["cost"]["wire_bytes_per_delivery"], y["cost"]["wire_bytes_per_delivery"]
            w("| %s | %s | %s | %s |" % (c, traffic, " | ".join(obs), "%.0f -> %.0f" % (bx, by) if bx and by else "n/a"))
    w("")


def scope_sections(w):
    w("## What D-all protects, and what it does not\n")
    w("Three properties are different, and this test measures only the first.\n")
    w("- **Linkability** (who reads whom). Measured here. In D-all, no key and no follow names a reader, and every reader "
      "receives every write. So O2, O3 and O4, as modeled here, cannot separate the readers.")
    w("- **Confidentiality of the content.** Not provided by the schema. WireGuard encrypts each link. The server decodes "
      "every write in order to serve it, so the server's operator can read all content.")
    w("- **Authorization** (who may read). Not tested. This harness sets no access rule per family. In D-all, every "
      "authorized reader receives every write. So every reader can read all content, including each write's producer.")
    w("")
    w("In short, D-all hides who follows whom. It does not hide what is written, or who wrote it, from the server or "
      "from any reader. If the follow graph itself is sensitive (for example, who follows whose research), D-all "
      "addresses that. If the content is sensitive, it must be protected separately.\n")
    w("## Limitations\n")
    w("- The test has 8 participants and 4 held-out seeds. The results are informative but limited.")
    w("- Pairs within one run are not independent. The bootstrap resamples seeds first, then pairs, but the CIs may "
      "still be too narrow.")
    w("- Each observer uses one model: a logistic regression on the features listed above. An AUC near 0.50 shows that "
      "this model fails. It does not show that every traffic-analysis method fails.")
    w("- Each run lasted the minutes given above, not the 30 minutes in the brief.")
    w("- The network delay and loss tests, the padding test (H4) and a relay-broker observer were not run. The notes "
      "give the reasons.")
    w("")
    w("## Possible later studies\n")
    w("These are outside this test.\n")
    w("- How D-all's traffic and delivery cost grow with more participants and with different follow graphs.")
    w("- Stronger or different observer models.")
    w("- Persistence of shared records and independent verification, for uses such as a research network. This "
      "harness tests neither.")
    w("")


def fmt(r):
    if not r or r.get("auc") is None or r["auc"] != r["auc"]:
        return "n/a"
    return "%.2f [%.2f, %.2f]" % (r["auc"], r["ci95"][0], r["ci95"][1])


KNOWN_DEFECTS = {   # harness builds whose runs differ from the design this report describes
    "obsvis-2026-11-06-r3": [
        "**Telemetry in D and D-all was written as messages.** Each reading went into a new cell of a ring, including "
        "unchanged readings. The correct design is one cell per producer, overwritten on each reading. Effect: D and "
        "D-all telemetry wire bytes are too high, and the O1 and O2 telemetry numbers for D and D-all measure the wrong "
        "design.",
        "**Wire bytes included the control region's traffic.** This traffic is the harness's own reporting. Effect: the "
        "cost of C, D and D-all is too high, because their participants report more.",
        "**O3 timing for C, D and D-all used the client's clock, not the server's.** Effect: O3 timing is weaker than "
        "the real operator's. This makes C, D and D-all look better at O3. D-all is affected most, because it has no "
        "keys and depends on timing.",
        "**B's operator log had whole-second timestamps.** Effect: O3 timing for B is too weak. This makes B look "
        "better at O3. B's O3 result (1.00) comes from its topic keys, so it does not change.",
        "**After a server change, chat messages were sent again.** The correct design re-sends only a latest value, "
        "never a message. Effect: extra traffic in the server-change stress runs.",
        "**No linger time was set.** A producer's cells disappeared at once when its session ended. The correct design "
        "keeps them 5 seconds. Effect: the move stress runs used an unplanned setting.",
        "**Follows started from \"now\".** The correct design reads what a family holds, then waits. Effect: none "
        "measured; the start margin hid it.",
    ],
}

NOTES = [
    "**Run length.** Each run here lasted the minutes given above. The brief specifies 30 minutes. The command "
    "`./matrix.sh 30` runs the brief's matrix with no other change.",
    "**Network delay and loss tests: not run.** These tests need the Linux `sch_netem` module. The kernel used here does "
    "not have it, so these tests stop at their first step. They run with no change on a kernel that has the module.",
    "**H4: not run.** H4 needs messages padded to equal sizes. The Ribbit wire has no padding option.",
    "**D and D-all use ribbit::Space.** ribbit::Space is the publish/subscribe library that the platform's MQTT and "
    "Redis conversions use. The harness reaches it through a small C interface (`space_c.cpp`). The brief proposed a "
    "different small interface.",
    "**How O3 is measured for C, D and D-all.** The server program has no trace output for operations. So each "
    "operation is recorded in the client library. Then it is moved to the time of the server's own packet for it. That "
    "packet comes from a capture on the server, inside the tunnels. A write is placed at the first packet from its "
    "connection. A delivery is placed at the last packet to its connection. So the operations are the ones the server "
    "sees, and the times are the server's times.",
    "**Telemetry is one current value. Chat is a series of separate messages.** In D and D-all, each producer's "
    "telemetry is one cell, `<producer>`. The producer overwrites it on each reading. An unchanged reading writes the "
    "same bytes to the same cell. The wire then sends only a short hash, not the data. Chat uses a ring of 64 cells per "
    "producer, `<producer>.<n mod 64>`. C keeps the chat example's own design for both workloads: one send to each "
    "recipient. C is the comparison case: messaging built on shared memory.",
    "**How long cells live, and how reads start.** In D and D-all, a producer's cells stay 5 seconds after its session "
    "ends. The MQTT conversion uses the same value. A reader first receives what the family already holds, then each "
    "new write. After a new server or a move, a producer writes its current telemetry value again. It does not send "
    "chat messages again.",
    "**Wire bytes** are the bytes that each participant and the server sent into the router. nftables counts them. The "
    "control traffic of the test harness is not counted.",
    "**B's operator log** records the time each line arrives. Mosquitto's own timestamp has only whole seconds.",
    "**O1 for B, C, D and D-all is 0.57 in every case.** p1's link carries only p1's own connection to the server. So "
    "the only usable information is the direction of the pair: p1 is the producer, or p1 is the reader. 0.57 comes from "
    "that direction alone. It says nothing about which other participant is involved. Precision at top-k is also 0.57.",
    "**B's 95th-percentile latency (about 40 ms) comes from Nagle's algorithm.** About 6.5% of B's deliveries take "
    "39-43 ms. That is the minimum delay of Linux's delayed acknowledgement. All other deliveries take less than 2 ms. "
    "B tuned (Bt) is B with Nagle off in Mosquitto (`set_tcp_nodelay true`) and in the clients (`TCP_NODELAY`), and "
    "nothing else changed. Its 95th percentile is about 1.5 ms (the cost tables). Its wire bytes are the same as B's. "
    "Its edges are found as B's are: the same verdict for every observer, in both workloads. So Nagle changes B's "
    "latency, not what B reveals. B with default settings stays the baseline of the decision rule.",
    "**Not tested: a rendezvous or relay broker.** In this test, each participant's tunnel goes directly to the server. "
    "FrogNet can also send tunnels through a relay broker. Such a broker would see the end points, timing and sizes of "
    "the tunnels it relays. It would not see the keys. This observer is not measured here.",
    "**Revocation.** After p8's access is removed, the other participants receive 100% of their messages in every run. "
    "p8 receives nothing after the removal. This is the same in C, D and D-all.",
    "**Platform bug found.** A data field named `rows` comes back changed. The cause is in semtpl.hpp, function "
    "`rebuild_json`. The harness uses the name `items` instead. The bug is not fixed here, because the Python side "
    "shares the same wire behavior.",
    "**Possible platform issue, not confirmed.** An earlier version of D-all used plain held reads, not ribbit::Space. "
    "In one short run of that version, all readers stopped receiving after p8's access was removed. A possible cause: "
    "identical held reads are combined and tied to the removed session. This did not happen with ribbit::Space; the "
    "other readers received everything.",
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
    ap.add_argument("--compare", help="an earlier results directory: show its results beside these")
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
    if vers.get("harness") in KNOWN_DEFECTS:
        w("## Preliminary: these numbers are not fully accurate\n")
        w("The runs behind this report used harness build %s. An audit after the runs found errors in that build. They are "
          "fixed in the next build, and the full matrix is running again. A final report will replace this one.\n"
          % vers.get("harness"))
        w("The text of this report describes the corrected design. These runs differ from it as follows:\n")
        for t in KNOWN_DEFECTS[vers["harness"]]:
            w("- " + t)
        w("")
    summary(an, w)
    if a.compare:
        compare(an, json.load(open(os.path.join(a.compare, "analysis.json"))), w)
    scope_sections(w)
    w("## What ran\n")
    w("Eight participants (p1..p8), a server (srv), a controller (ctl) and a router (rt), each in its own Linux network "
      "namespace with its own stack, joined in a star through rt. Each participant follows 3 of the other 7, drawn from the "
      "seed: 24 true edges among 56 ordered pairs. The attackers try to recover that graph.\n")
    w("Every step of a run was coordinated through a separate control region, a chat-ram of its own in ctl, left out of "
      "every capture. Each namespace's agent advertised itself and took every instruction on one held read. Every result "
      "came back as tuples: ground truth, the operation logs and the capture headers.\n")
    w("The six conditions used the same machines and the same seeded schedule:\n")
    w("- **A:** direct TLS 1.3, producer to each follower.")
    w("- **B:** Mosquitto %s, topic `chat/<recipient>`, TLS 1.3." % vers.get("mosquitto", "").replace("mosquitto version ", ""))
    w("- **B tuned (Bt):** B with Nagle off in Mosquitto and in the clients. Baseline runs only.")
    w("- **C:** chat-ram (%s), the chat example's own schema and client, `ChatServer.<recipient>.<producer>`." % vers.get("chat-ram"))
    w("- **D:** chat-ram on ribbit::Space (the publish/subscribe library the MQTT and Redis conversions use), own truth: "
      "each producer writes its own cells, a ring `Chat.<producer>.<producer>.<n mod 64>` for chat and one cell "
      "`Chat.<producer>.<producer>` for telemetry; each consumer holds one standing follow per producer it follows.")
    w("- **D-all:** the same library, read-all: `Chat.Out.<producer>.<n mod 64>` for chat, `Chat.Out.<producer>` for telemetry; each consumer holds one standing follow "
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
    w("- **O3:** the server operator. For C, D and D-all, this is the operations the host decodes, on the host's clock "
      "(see the notes). For B it is Mosquitto's own log (`log_type all`). Scored on all 56 pairs.")
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
            tf = lambda t, o: NA if absent(c, o) else fmt(t) + (" (%g s)" % t["w_s"] if t else "")
            w("| %s | %s | %s | %s | %s | %s | %s |" % (c, *[NA if absent(c, o) else fmt((x[o] or {}).get("lr")) for o in OBS],
              tf(t2, "O2"), tf(t3, "O3")))
        w("")
        w("Precision at top-k (k = the true edges in scope):\n")
        w("| condition | O1 | O2 | O3 | O4 |"); w("|---|---|---|---|---|")
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x: continue
            w("| %s | %s |" % (c, " | ".join(NA if absent(c, o) else ("%.2f" % x[o]["lr"]["precision_at_k"]) if x[o] and x[o]["lr"] and x[o]["lr"]["precision_at_k"] is not None else "n/a" for o in OBS)))
        w("")
        w("Decision rule: a condition reduces linkability for an observer only if its AUC is below B's for that observer "
          "and the two CIs do not overlap.\n")
        w("| condition | O1 | O2 | O3 | O4 |"); w("|---|---|---|---|---|")
        b = cell(an, "B", traffic)
        for c in CONDS:
            x = cell(an, c, traffic)
            if not x or c == "B": continue
            w("| %s | %s |" % (c, " | ".join(NA if absent(c, o) else "yes" if below((x[o] or {}).get("lr"), (b[o] or {}).get("lr") if b else None) else "no" for o in OBS)))
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
