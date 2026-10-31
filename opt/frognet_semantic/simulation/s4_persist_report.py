#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_persist_report.py - [S4_PERSIST_REPORT_V1] the tables of the loop-persistence report, from the result files:
  tier 4 (MEASURED, real FRR): s4_persist.py result dirs
  model  (MODELED):            s4_persist_model.py run outputs (*.json), and its `compare` output

usage: s4_persist_report.py --tier4 DIR... [--model FILE...] [--compare FILE] > tables.md
"""
import argparse
import json
import os
import statistics
from collections import defaultdict


def survived(r, cls):
    h = r.get(f"{cls}_survived_hist") or {}
    return sum(v for k, v in h.items() if int(k) >= 1), max([int(k) for k, v in h.items() if v] + [0])


def tier4_table(dirs):
    rows = []
    for w in dirs:
        r = json.load(open(f"{w}/result.json"))
        a = r["args"]
        ls, lmax = survived(r, "live")
        gs, gmax = survived(r, "gone")
        rows.append((a["profile"], a["topo"], a["n"], a["event"], r["live_loops_n"], r["live_max_loop_ms"], ls, lmax,
                     r["gone_loops_n"], r["gone_max_loop_ms"], gs, gmax, r["window_max_ms"], r.get("loadavg_1m", 0)))
    order = {"silent": 0, "nodefail": 1, "partition": 2, "cut": 3}
    rows.sort(key=lambda x: (x[0] != "tuned", x[1], x[2], order[x[3]]))
    out = ["| timers | topo | N | event | live loops | live longest ms | live loops past >=1 SPF | most SPFs survived "
           "| gone loops | gone longest ms | gone past >=1 SPF | most SPFs survived | reconverged ms | load |",
           "|" + "---|" * 14]
    for x in rows:
        out.append("| " + " | ".join(f"{v:.0f}" if isinstance(v, float) and i in (5, 9, 12) else
                                     (f"{v:.2f}" if isinstance(v, float) else str(v)) for i, v in enumerate(x)) + " |")
    return "\n".join(out)


def model_table(files):
    groups = defaultdict(list)
    for f in files:
        r = json.load(open(f))
        groups[(r["profile"], r["topo"], r["n"], r["event"])].append(r)
    order = {"silent": 0, "nodefail": 1, "partition": 2, "cut": 3}
    e1 = ["| timers | topo | N | event | seeds | live loops /64 dests | live longest ms | live past >=1 SPF "
          "| most SPFs survived | gone loops | gone longest ms | reconverged ms | SPF ms (modeled) |", "|" + "---|" * 13]
    e2 = ["| timers | topo | N | event | views/bridge p50 (max) | bridges detecting | mismatches per detecting bridge "
          "p50 (max) | msgs per port max | ports agreed by ms | held ms per port |", "|" + "---|" * 10]
    for k in sorted(groups, key=lambda k: (k[0] != "tuned", k[1], k[2], order[k[3]])):
        rs = groups[k]
        med = lambda f: statistics.median(f(r) for r in rs)
        mx = lambda f: max(f(r) for r in rs)
        e1.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {len(rs)} | {med(lambda r: r['live_loops_n']):.0f} "
                  f"| {mx(lambda r: r['live_max_loop_ms']):.0f} | {med(lambda r: survived(r, 'live')[0]):.0f} "
                  f"| {mx(lambda r: survived(r, 'live')[1])} | {med(lambda r: r['gone_loops_n']):.0f} "
                  f"| {mx(lambda r: r['gone_max_loop_ms']):.0f} | {mx(lambda r: r['window_max_ms']):.0f} "
                  f"| {rs[0]['spf_ms_modeled']:.0f} |")
        s = lambda r: r["spb"]["spf"]
        ports = lambda r: max(1, s(r)["ports_reagreeing"])
        e2.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {med(lambda r: s(r)['views_per_bridge']['p50']):.0f} "
                  f"({mx(lambda r: s(r)['views_per_bridge']['max'])}) | {med(lambda r: s(r)['frac_detecting']) * 100:.0f}% "
                  f"| {med(lambda r: s(r)['mismatches_per_detecting']['p50']):.0f} "
                  f"({mx(lambda r: s(r)['mismatches_per_detecting']['max'])}) | {mx(lambda r: s(r)['msgs_per_port']['max'])} "
                  f"| {mx(lambda r: s(r)['agree_ms']['max']):.0f} "
                  f"| {med(lambda r: s(r)['restricted_port_s'] * 1000 / ports(r)):.0f} |")
    return "\n".join(e1), "\n".join(e2)


def compare_table(path):
    rows = json.load(open(path))
    out = ["| run | live loops bed / model | live longest ms bed / model | gone loops bed / model "
           "| gone longest ms bed / model | reconverged ms bed / model | SPF runs per node bed / model |",
           "|" + "---|" * 7]
    for r in sorted(rows, key=lambda r: r["run"]):
        t, m = r["t4"], r["model_med"]
        out.append(f"| {r['run']} | {t['live_loops_n']} / {m['live_loops_n']:.0f} | {t['live_max_loop_ms']:.0f} / "
                   f"{m['live_max_loop_ms']:.0f} | {t['gone_loops_n']} / {m['gone_loops_n']:.0f} | "
                   f"{t['gone_max_loop_ms']:.0f} / {m['gone_max_loop_ms']:.0f} | {t['window_max_ms']:.0f} / "
                   f"{m['window_max_ms']:.0f} | {r['t4_spf_mean']} / {r['model_spf_mean']} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier4", nargs="*", default=[])
    ap.add_argument("--model", nargs="*", default=[])
    ap.add_argument("--compare")
    a = ap.parse_args()
    if a.tier4:
        print("## Tier 4 (MEASURED, real FRR)\n\n" + tier4_table(a.tier4) + "\n")
    if a.compare and os.path.exists(a.compare):
        print("## Model beside the bed (median of 5 model seeds, every destination)\n\n" + compare_table(a.compare) + "\n")
    if a.model:
        e1, e2 = model_table(a.model)
        print("## E1 IS-IS (MODELED)\n\n" + e1 + "\n\n## E2 SPB agreement (MODELED, digest per SPF run)\n\n" + e2 + "\n")


if __name__ == "__main__":
    main()
