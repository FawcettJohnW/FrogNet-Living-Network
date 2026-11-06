#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# analyze.py [RESULTS] [--train 1-6] [--test 7-10] [--json OUT] -- the attackers and the metrics, from what every run's
# agents sent back through the control region (RESULTS/<run>/<agent>/*.json, controller.json). The ground truth (the
# plan's follow graph; the participants' truth logs) labels pairs and measures delivery; no attacker reads it.
#
# Observers and what each sees:
#   O1  p1's link (p1's capture): only p1's own packets -- scored on the 14 pairs that involve p1
#   O2  srv's link (srv's capture): every participant's packets to and from the server -- 56 pairs
#   O3  the server operator: the operations the host decodes (C, D, D-all: logged at the client library boundary;
#       B: Mosquitto's own log, log_type all) -- 56 pairs
#   O4  p1 as an insider: what it can read in the service beyond its own deliveries -- the 42 pairs without p1
# Per pair (i, j) -- "j follows i" -- features: timing (the share of i's upstream events followed within w by a
# downstream event to j, and that share's expectation were they independent; w in 10 ms, 50 ms, 200 ms, 1 s, 2 s),
# packet-size bins (lagged co-occurrence, 200 ms), and the keys an observer can read (O3, O4: puts/publishes by i
# addressed to j, held reads by j on i's variable; O1: packets between p1 and another participant's address).
# Attackers: timing correlation alone (w chosen on the training seeds), and a logistic regression on every feature
# (trained on the training seeds, scored on the held-out ones). AUC with a 95% bootstrap CI (1000 draws: seeds, then
# pairs within them, resampled).
import argparse, glob, json, os, re, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bed, workload  # noqa: E402

WS = [0.01, 0.05, 0.2, 1.0, 2.0]
BINS = [0, 100, 200, 400, 800, 1 << 30]
PARTS = workload.PARTS
PAIRS = [(i, j) for i in PARTS for j in PARTS if i != j]
SCOPE = {"O1": [(i, j) for (i, j) in PAIRS if "p1" in (i, j)], "O2": PAIRS, "O3": PAIRS,
         "O4": [(i, j) for (i, j) in PAIRS if "p1" not in (i, j)]}


def name_of():
    m = {bed.ADDR("srv"): "srv", bed.WG("srv"): "srv"}
    for p in PARTS:
        m[bed.ADDR(p)] = m[bed.ADDR(p, 3)] = m[bed.WG(p)] = p
    return m


def load(run):
    def j(*a):
        p = os.path.join(run, *a)
        return json.load(open(p)) if os.path.exists(p) else []
    return {"ctl": json.load(open(os.path.join(run, "controller.json"))),
            "truth": sum((j(p, "truth.json") for p in PARTS), []),
            "o3": sum((j(p, "o3.json") for p in PARTS), []), "o4": j("p1", "o4.json"),
            "cap1": j("p1", "packets.json"), "cap2": j("srv", "packets.json"), "mosq": j("srv", "mosquitto.json")}


def lagged(up, down, w):
    """share of up-times followed within w by a down-time, and its expectation for independent streams"""
    if len(up) == 0 or len(down) == 0:
        return 0.0, 0.0
    d = np.sort(np.asarray(down)); u = np.asarray(up)
    k = np.searchsorted(d, u, side="right")
    hit = (k < len(d)) & (d[np.minimum(k, len(d) - 1)] - u <= w)
    span = max(d[-1] - d[0], 1e-9)
    return float(hit.mean()), float(1 - np.exp(-len(d) / span * w))


def timing_feats(up, down, pairs):
    """up[i]: [(t, size)] from i; down[j]: [(t, size)] to j"""
    F = {}
    for (i, j) in pairs:
        U, D = up.get(i, []), down.get(j, [])
        ut, dt = [t for t, _ in U], [t for t, _ in D]
        f = []
        for w in WS:
            a, b = lagged(ut, dt, w); f += [a, b, a - b]
        for bi in range(len(BINS) - 1):              # size bins: i's packets of bin a followed within 200 ms by j's of bin b
            ua = [t for t, s in U if BINS[bi] <= s < BINS[bi + 1]]
            for bj in range(len(BINS) - 1):
                db = [t for t, s in D if BINS[bj] <= s < BINS[bj + 1]]
                a, b = lagged(ua, db, 0.2); f.append(a - b)
        F[(i, j)] = f
    return F


def packets_view(pk, nm):
    up, down = {}, {}
    for t, s, d, proto, sp, dp, ln in pk:
        a, b = nm.get(s), nm.get(d)
        if a in PARTS and b == "srv": up.setdefault(a, []).append((t, ln))
        elif a == "srv" and b in PARTS: down.setdefault(b, []).append((t, ln))
    return up, down


def features(R, cond):
    nm = name_of(); out = {}
    # O2: the server's link
    up, down = packets_view(R["cap2"], nm)
    out["O2"] = timing_feats(up, down, SCOPE["O2"])
    # O1: p1's link -- its packets to and from each address it talks to
    direct = {}
    for t, s, d, proto, sp, dp, ln in R["cap1"]:
        a, b = nm.get(s), nm.get(d)
        if a == "p1" and b in PARTS: direct.setdefault(("p1", b), []).append(t)
        if b == "p1" and a in PARTS: direct.setdefault((a, "p1"), []).append(t)
    tot = max(1, len(R["cap1"]))
    up1, down1 = packets_view(R["cap1"], nm)
    O1 = {}
    for (i, j) in SCOPE["O1"]:
        O1[(i, j)] = [len(direct.get((i, j), [])) / tot, float(len(direct.get((i, j), [])) > 0),
                      len(up1.get(i, [])) / tot, len(down1.get(j, [])) / tot]
    out["O1"] = O1
    # O3: the operator's log
    o3u, o3d, key = {}, {}, {}
    if cond == "B":
        for l in R["mosq"]:
            m = re.match(r"(\d+): (Received|Sending) PUBLISH (?:from|to) (\S+) \(.*?'chat/(\S+?)'.*?\((\d+) bytes\)\)", l)
            if not m: continue
            t, how, who, topic, n = float(m.group(1)), m.group(2), m.group(3), m.group(4), int(m.group(5))
            if how == "Received" and who in PARTS:
                o3u.setdefault(who, []).append((t, n))
                if topic in PARTS: key[(who, topic)] = key.get((who, topic), 0) + 1
            elif how == "Sending" and who in PARTS:
                o3d.setdefault(who, []).append((t, n))
    else:
        for e in R["o3"]:
            who = nm.get(e["conn"])
            if e["op"] == "put":
                o3u.setdefault(who, []).append((e["t"], e["bytes"]))
                if cond == "C" and e["var"] in PARTS: key[(who, e["var"])] = key.get((who, e["var"]), 0) + 1
            elif e["op"] == "release" and e.get("rows"):
                o3d.setdefault(who, []).append((e["t"], e["bytes"]))
            elif e["op"] == "park" and cond == "D" and e["var"] in PARTS:
                key[(e["var"], who)] = key.get((e["var"], who), 0) + 1
    T3 = timing_feats(o3u, o3d, SCOPE["O3"])
    kt = max(1, sum(key.values()))
    out["O3"] = {p: [key.get(p, 0) / kt, float(key.get(p, 0) > 0)] + T3[p] for p in SCOPE["O3"]}
    # O4: the insider's reads
    k4 = {}
    for e in R["o4"]:
        if e.get("src") == "mqtt":
            j = e["topic"].split("/", 1)[1] if "/" in e["topic"] else ""
            i = e.get("f")
        elif e.get("svc") == "ChatServer":
            j, i = e.get("var"), e.get("scope")
        else:
            continue                                    # Chat.<producer>.out / Chat.Out.<producer>: no consumer in it
        if i in PARTS and j in PARTS: k4[(i, j)] = k4.get((i, j), 0) + 1
    t4 = max(1, sum(k4.values()))
    out["O4"] = {p: [k4.get(p, 0) / t4, float(k4.get(p, 0) > 0)] for p in SCOPE["O4"]}
    return out


def costs(R):
    tr, ctl = R["truth"], R["ctl"]
    sends = {(e["m"], e["f"]): e["t"] for e in tr if e["ev"] == "send"}   # an unchanged telemetry write: its first send
    fol = {}
    for e in tr:
        if e["ev"] == "send": fol[(e["m"], e["f"])] = e["to"]
    want = sum(len(v) for v in fol.values())
    lat = [e["t"] - sends[(e["m"], e["f"])] for e in tr if e["ev"] == "recv" and (e["m"], e["f"]) in sends]
    got = len(lat)
    links = ctl.get("links") or {}
    wire = sum(v["to_router_bytes"] for v in links.values())
    use = [u for u in (ctl.get("server_usage") or []) if u]
    return {"deliveries_expected": want, "delivered": got, "delivery_ratio": got / want if want else None,
            "wire_bytes": wire, "wire_bytes_per_delivery": wire / got if got else None,
            "latency_p50_ms": float(np.percentile(lat, 50) * 1000) if lat else None,
            "latency_p95_ms": float(np.percentile(lat, 95) * 1000) if lat else None,
            "server_cpu_s": sum(u.get("cpu_s", 0) for u in use) if use else None,
            "server_rss_peak_kb": max((u.get("rss_peak_kb") or 0) for u in use) if use else None,
            "errors": {k: v.get("n_err") for k, v in (ctl.get("results") or {}).items()}}


def auc(y, s):
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y); s = np.asarray(s)
    return float(roc_auc_score(y, s)) if 0 < y.sum() < len(y) else float("nan")


def boot(groups, rng, n=1000):
    """groups: per held-out seed, (labels, scores); resample seeds, then pairs within each"""
    vals = []
    for _ in range(n):
        ys, ss = [], []
        for g in rng.integers(0, len(groups), len(groups)):
            y, s = groups[g]; k = rng.integers(0, len(y), len(y)); ys += list(np.asarray(y)[k]); ss += list(np.asarray(s)[k])
        a = auc(ys, ss)
        if a == a: vals.append(a)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else (float("nan"), float("nan"))


def attack(train, test, rng):
    """train / test: [(seed, {pair: feats}, follows)]; returns the LR and timing-only attackers' held-out results"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    def xy(runs):
        X, Y, G = [], [], []
        for seed, F, fol in runs:
            for (i, j), f in F.items():
                X.append(f); Y.append(int(i in fol[j])); G.append(seed)
        return np.asarray(X, float), np.asarray(Y), np.asarray(G)
    Xa, Ya, _ = xy(train); Xb, Yb, Gb = xy(test)
    res = {}
    if len(Xa) == 0 or len(Xb) == 0:
        return {"lr": None, "timing": None}
    keep = Xa.std(0) > 1e-12
    if keep.any() and 0 < Ya.sum() < len(Ya):
        sc = StandardScaler().fit(Xa[:, keep])
        m = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(sc.transform(Xa[:, keep]), Ya)
        sb = m.predict_proba(sc.transform(Xb[:, keep]))[:, 1]
    else:
        sb = np.zeros(len(Xb))                           # nothing an attacker can tell apart: every pair alike
    res["lr"] = summarize(Yb, sb, Gb, rng)
    # timing alone: the lift column at each w (columns 2, 5, 8, 11, 14 of a timing block), w chosen on training seeds
    nf = Xa.shape[1]; best = None
    for k, w in enumerate(WS):
        col = (nf - (3 * len(WS) + 25)) + 3 * k + 2 if nf >= 3 * len(WS) + 25 else None
        if col is None: break
        a = auc(Ya, Xa[:, col])
        if a == a and (best is None or a > best[0]): best = (a, w, col)
    res["timing"] = dict(summarize(Yb, Xb[:, best[2]], Gb, rng), w_s=best[1], train_auc=best[0]) if best else None
    return res


def summarize(Y, S, G, rng):
    groups = [(Y[G == g], S[G == g]) for g in sorted(set(G))]
    a = auc(Y, S); lo, hi = boot(groups, rng)
    prec = []
    for y, s in groups:                                 # precision and recall at top-k, k = true edges in scope
        k = int(y.sum())
        if k == 0: continue
        top = np.argsort(-s, kind="stable")[:k]; prec.append(y[top].sum() / k)
    return {"auc": a, "ci95": [lo, hi], "precision_at_k": float(np.mean(prec)) if prec else None,
            "recall_at_k": float(np.mean(prec)) if prec else None, "n_pairs": int(len(Y)), "n_true": int(Y.sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?", default=os.path.join(HERE, "results"))
    ap.add_argument("--train", default="1-6"); ap.add_argument("--test", default="7-10"); ap.add_argument("--json")
    a = ap.parse_args()
    rg = lambda s: set(range(int(s.split("-")[0]), int(s.split("-")[1]) + 1))
    TR, TE = rg(a.train), rg(a.test)
    runs = {}
    for d in sorted(glob.glob(os.path.join(a.results, "*", "controller.json"))):
        run = os.path.dirname(d); c = json.load(open(d))
        p = c["plan"]
        runs.setdefault((p["cond"], p["traffic"], p.get("stress_name")), []).append((int(p["seed"]), run, c.get("ok")))
    rng = np.random.default_rng(20261106)
    report = {"build": "obsvis-analyze-2026-11-06-r2", "train": sorted(TR), "test": sorted(TE), "cells": []}
    for (cond, traffic, stress), rs in sorted(runs.items(), key=lambda x: (x[0][2] or "", x[0][1], x[0][0])):
        tr, te, cost, failed = {o: [] for o in SCOPE}, {o: [] for o in SCOPE}, [], []
        for seed, run, ok in sorted(rs):
            if not ok: failed.append(seed)
            R = load(run); F = features(R, cond); fol = R["ctl"]["plan"]["follows"]
            for o in SCOPE:
                (tr if seed in TR else te if seed in TE else {o: []})[o].append((seed, F[o], fol))
            if seed in TE: cost.append(costs(R))
        cell = {"cond": cond, "traffic": traffic, "stress": stress, "seeds": sorted(s for s, _, _ in rs), "failed_runs": failed}
        for o in SCOPE:
            cell[o] = attack(tr[o] if stress is None else (tr[o] or base_train(runs, cond, traffic, o, TR)), te[o], rng)
        cell["cost"] = {k: (float(np.mean([c[k] for c in cost if c[k] is not None])) if any(c[k] is not None for c in cost) else None)
                        for k in ("delivery_ratio", "wire_bytes_per_delivery", "latency_p50_ms", "latency_p95_ms", "server_cpu_s", "server_rss_peak_kb")}
        report["cells"].append(cell)
        print("%-5s %-9s %-11s " % (cond, traffic, stress or "baseline") + "  ".join(
            "%s %s" % (o, ("%.2f [%.2f,%.2f]" % (cell[o]["lr"]["auc"], *cell[o]["lr"]["ci95"])) if cell[o]["lr"] else "n/a") for o in SCOPE), flush=True)
    if a.json:
        json.dump(report, open(a.json, "w"), indent=1)
    return report


def base_train(runs, cond, traffic, o, TR):
    """a stress phase runs held-out seeds only: its attackers are the baseline's, trained on the baseline's seeds"""
    out = []
    for seed, run, ok in runs.get((cond, traffic, None), []):
        if seed in TR:
            R = load(run); out.append((seed, features(R, cond)[o], R["ctl"]["plan"]["follows"]))
    return out


if __name__ == "__main__":
    main()
