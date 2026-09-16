#!/usr/bin/env python3
"""plot_proof.py -- graphs from the run JSONs.

    python3 plot_proof.py /tmp/proof plots/

Makes five, each answering one question:

  1_latency_vs_size    what a collective costs, per arm, per world size
  2_slope_vs_world     per-element cost as the fleet grows, with the W/2 line
  3_peer_latency       per-PEER read time -- which link costs what
  4_phases             where a collective's time goes: publish, phase1, phase2
  5_distribution       every sample, not the median, per arm

Graph 3 is the one that separates a transcontinental node from a local one.
Graph 5 is what shows a startup outlier for what it is instead of letting it
move a median.
"""
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("pip install matplotlib")
    raise SystemExit(1)


def load(root):
    out = []
    for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        try:
            j = json.load(open(p))
        except Exception:
            continue
        if "sizes" in j and j.get("sizes"):
            j["_arm"] = "%s%s" % (j["backend"],
                                  "-" + j["reduce"] if j.get("reduce")
                                  and j["backend"] != "gloo" else "")
            out.append(j)
    return out


def fit(pts):
    n = len(pts)
    if n < 2:
        return None, None
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts); sxy = sum(p[0] * p[1] for p in pts)
    den = n * sxx - sx * sx
    if den == 0:
        return None, None
    b = (n * sxy - sx * sy) / den
    return (sy - b * sx) / n, b


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/tmp/proof"
    out = sys.argv[2] if len(sys.argv) > 2 else "plots"
    os.makedirs(out, exist_ok=True)
    runs = load(root)
    if not runs:
        print("no runs under %s" % root)
        return 1
    print("%d run(s)" % len(runs))

    # ---- 1. latency vs size, per arm, one panel per world ---------------
    worlds = sorted({r["world"] for r in runs})
    fig, axes = plt.subplots(1, len(worlds), figsize=(5 * len(worlds), 4),
                             squeeze=False)
    for ax, w in zip(axes[0], worlds):
        for arm in sorted({r["_arm"] for r in runs if r["world"] == w}):
            pts = defaultdict(list)
            for r in runs:
                if r["world"] == w and r["_arm"] == arm:
                    for sz, v in r["sizes"].items():
                        pts[int(sz)].append(v["median_ms"])
            xs = sorted(pts)
            if xs:
                ax.plot(xs, [max(pts[x]) for x in xs], "o-", label=arm)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_title("world %d" % w)
        ax.set_xlabel("elements"); ax.set_ylabel("ms (slowest rank)")
        ax.grid(True, which="both", alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(out, "1_latency_vs_size.png"), dpi=130)

    # ---- 2. slope vs world ----------------------------------------------
    fig, ax = plt.subplots(figsize=(6, 4))
    for arm in sorted({r["_arm"] for r in runs}):
        xs, ys = [], []
        for w in worlds:
            worst = {}
            for r in runs:
                if r["world"] == w and r["_arm"] == arm:
                    for sz, v in r["sizes"].items():
                        worst[int(sz)] = max(worst.get(int(sz), 0), v["median_ms"])
            a, b = fit(sorted(worst.items()))
            if b:
                xs.append(w); ys.append(b * 1e6)
        if xs:
            ax.plot(xs, ys, "o-", label=arm)
    ax.set_xlabel("world size"); ax.set_ylabel("ns per element")
    ax.set_title("per-element cost as the fleet grows")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(out, "2_slope_vs_world.png"), dpi=130)

    # ---- 3. per-peer read latency ---------------------------------------
    # The graph that says which link costs what. One box per (rank -> peer).
    series, labels = [], []
    for r in sorted(runs, key=lambda x: (x["world"], x["rank"])):
        t = r.get("timing") or {}
        for peer, vals in sorted((t.get("peer_ms") or {}).items()):
            if len(vals) >= 3:
                series.append(vals)
                labels.append("w%d r%d<-r%s" % (r["world"], r["rank"], peer))
    if series:
        fig, ax = plt.subplots(figsize=(max(7, len(series) * .55), 4.5))
        ax.boxplot(series, tick_labels=labels, showfliers=False)
        ax.set_yscale("log"); ax.set_ylabel("ms per peer read")
        ax.set_title("read latency per peer -- which link costs what")
        ax.grid(alpha=.3, axis="y")
        plt.xticks(rotation=60, ha="right", fontsize=7)
        fig.tight_layout(); fig.savefig(os.path.join(out, "3_peer_latency.png"), dpi=130)
    else:
        print("no per-peer timing: gloo runs do not produce it, and "
              "psychedelic runs need the build that records it")

    # ---- 4. where the time goes ------------------------------------------
    ph = defaultdict(list)
    for r in runs:
        for k, v in ((r.get("timing") or {}).get("phase_ms") or {}).items():
            ph[k] += v
    if ph:
        fig, ax = plt.subplots(figsize=(6, 4))
        ks = [k for k in ("publish", "phase1", "phase2") if ph.get(k)]
        ax.boxplot([ph[k] for k in ks], tick_labels=ks, showfliers=False)
        ax.set_ylabel("ms"); ax.set_yscale("log")
        ax.set_title("where a collective's time goes")
        ax.grid(alpha=.3, axis="y")
        fig.tight_layout(); fig.savefig(os.path.join(out, "4_phases.png"), dpi=130)

    # ---- 5. every sample --------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labs, data = [], []
    for w in worlds:
        for arm in sorted({r["_arm"] for r in runs if r["world"] == w}):
            biggest = max(int(sz) for r in runs if r["world"] == w
                          and r["_arm"] == arm for sz in r["sizes"])
            vals = []
            for r in runs:
                if r["world"] == w and r["_arm"] == arm:
                    v = r["sizes"].get(str(biggest))
                    if v:
                        vals += v.get("samples_ms") or [v["median_ms"]]
            if vals:
                data.append(vals); labs.append("w%d %s" % (w, arm))
    if data:
        ax.boxplot(data, tick_labels=labs, showfliers=True)
        ax.set_yscale("log"); ax.set_ylabel("ms per collective")
        ax.set_title("every sample at the largest size (outliers shown)")
        ax.grid(alpha=.3, axis="y")
        plt.xticks(rotation=30, ha="right", fontsize=8)
        fig.tight_layout(); fig.savefig(os.path.join(out, "5_distribution.png"), dpi=130)

    print("wrote %s/" % out)
    for f in sorted(os.listdir(out)):
        print("   %s" % f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
