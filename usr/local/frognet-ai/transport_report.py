#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""transport_report.py -- transportrun.sh's results, through the FrogTorch RAM.

Each rank writes its own result of each arm into the RAM (its own cell, once); every node then holds one read on the
family until every rank's cell of every arm is there, and prints the same report. Nothing is copied between nodes
and no node asks another.

    transport_report.py ram     --tree T                       what the one RAM is and what it listens with
    transport_report.py put     --base B --arm N --rank R --world W --label L --word WORD --kind ddp|mesh
                                --whole-s S --out DIR --runid ID --tree T
    transport_report.py matrix  --base B --arms N --world W --wait S --tree T

The report (John, 15 September 2026): three matrices of one shape, ranks down and variants across (whole-run
seconds, held-out loss, param_sha), then the variants down the left with their run id, word, best step, spread,
whole-run time and its ratio against this session's control (arm 0) only.
"""
import argparse
import glob
import json
import os
import re
import sys


def _T(tree):
    if tree not in sys.path:
        sys.path.insert(0, tree)
    os.environ.setdefault("FROGNET_DIAG", "0")
    from agent_workload.tuplespace import frogtorch_ram_client as T
    return T


def cmd_ram(a):
    T = _T(a.tree)
    hp = T.ram().control()
    s = T._Session(*hp)
    v, st = s.call({"op": "version"}), s.call({"op": "stats"})
    s.close()
    print("  the FrogTorch RAM for this run: %s:%d" % hp)
    print("    build      %s" % v.get("build"))
    print("    drivers    %s" % v.get("drivers"))
    print("    listening  %s" % ", ".join("%s on port %s" % kv for kv in sorted((st.get("listening") or {}).items())))
    print("    this node's sessions with it are on the %s driver" % v.get("driver"))
    return 0


def cmd_put(a):
    T = _T(a.tree)
    v = {"arm": a.arm, "rank": a.rank, "label": a.label, "word": a.word, "kind": a.kind, "runid": a.runid,
         "whole_s": round(a.whole_s, 2), "status": "FAIL", "sha": None, "heldout": None, "ms_step_min": None,
         "node": os.uname().nodename}
    if a.kind == "ddp":
        fs = sorted(glob.glob(os.path.join(a.out, "%s.r%d.*.json" % (a.runid, a.rank))), key=os.path.getmtime)
        if fs:
            d = json.load(open(fs[-1]))
            v.update(status=d.get("status"), sha=d.get("param_sha"), heldout=d.get("heldout"),
                     ms_step_min=d.get("ms_step_min"), error=(d.get("error") or "")[:300] or None)
        else:
            v["error"] = "no result file %s/%s.r%d.*.json: the rank ended before it wrote one" % (a.out, a.runid, a.rank)
    else:
        log = os.path.join(a.out, "%s.rank%d.log" % (a.runid, a.rank))
        line = ""
        if os.path.exists(log):
            for l in open(log, errors="replace"):
                if "meshfeed    " in l:
                    line = l
        m = re.search(r"([0-9.]+) ms/step \(min\).*?([0-9.]+) heldout\s+([0-9a-f]{16})", line)
        if m:
            v.update(status="ok", ms_step_min=float(m.group(1)), heldout=float(m.group(2)), sha=m.group(3))
        else:
            v["error"] = "no result line in %s" % log
    T.put("transportrun.%d" % a.base, "a%d.r%d" % (a.arm, a.rank), "r%d" % a.rank, v, own=False)
    print("    result written to the RAM: %s  whole run %.1f s  held-out %s  %s"
          % (v["status"], v["whole_s"], v["heldout"], v["sha"]))
    return 0


def cmd_matrix(a):
    T = _T(a.tree)
    want = a.arms * a.world
    rows = T.get_all("transportrun.%d" % a.base, wait_s=a.wait, min_rows=want)
    cells = {}
    for r in rows:
        v = r.get("value")
        if isinstance(v, dict) and "arm" in v:
            cells[(int(v["arm"]), int(v["rank"]))] = v
    arms = sorted({k[0] for k in cells})
    if len(cells) < want:
        print("  %d of %d results are in the RAM after %.0f s; missing: %s" % (
            len(cells), want, a.wait, ", ".join("arm %d rank %d" % (n, r) for n in range(a.arms)
                                                for r in range(a.world) if (n, r) not in cells)))
    if not cells:
        return 1
    names = {r: next((cells[(n, r)]["node"] for n in arms if (n, r) in cells), "?") for r in range(a.world)}
    w = max(18, max(len(x) for x in names.values()) + 6)

    def matrix(title, fmt):
        print("\n  %s" % title)
        print("  %-*s" % (w, "") + "".join("%18s" % ("arm %d" % n) for n in arms))
        for r in range(a.world):
            print("  %-*s" % (w, "r%d %s" % (r, names[r])) + "".join(
                "%18s" % (fmt(cells[(n, r)]) if (n, r) in cells else "-") for n in arms))
    f = lambda k, p: (lambda c: (p % c[k]) if c.get(k) is not None else str(c.get("status")))
    matrix("whole-run seconds (from the arm's barrier to the rank's exit)", f("whole_s", "%.1f"))
    matrix("held-out loss", f("heldout", "%.6f"))
    matrix("param_sha", f("sha", "%s"))

    def whole(n):                                  # an arm's whole-run time: its slowest rank's
        xs = [cells[(n, r)]["whole_s"] for r in range(a.world) if (n, r) in cells]
        return max(xs) if xs else None
    ctl = whole(0) if 0 in arms else None       # the control is arm 0 of this session and nothing else
    print("\n  %-4s %-10s %-34s %-9s %10s %10s %9s %8s  %s" % ("arm", "run id", "variant", "word", "best step", "spread",
                                                              "whole s", "ratio", "param_sha (ranks agree?)"))
    for n in arms:
        cs = [cells[(n, r)] for r in range(a.world) if (n, r) in cells]
        ms = [c["ms_step_min"] for c in cs if c.get("ms_step_min") is not None]
        shas = sorted({str(c.get("sha")) for c in cs})
        bad = [c for c in cs if c.get("status") != "ok"]
        print("  %-4d %-10s %-34s %-9s %10s %10s %9s %8s  %s%s" % (
            n, cs[0]["runid"], cs[0]["label"][:34], cs[0]["word"],
            ("%.0f ms" % min(ms)) if ms else "-", ("%.0f ms" % (max(ms) - min(ms))) if ms else "-",
            "%.1f" % whole(n), ("%.2fx" % (ctl / whole(n))) if ctl and whole(n) else "-",
            shas[0] if len(shas) == 1 else "%d DIFFERENT: %s" % (len(shas), " ".join(shas)),
            "" if not bad else "   FAILED on rank(s) %s: %s" % (
                ",".join(str(c["rank"]) for c in bad), (bad[0].get("error") or "")[:160])))
    print("\n  ratio: arm 0's whole-run time over the arm's (this session's control only; above 1 is faster than it).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["ram", "put", "matrix"])
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--base", type=int, default=0)
    ap.add_argument("--arm", type=int, default=0)
    ap.add_argument("--arms", type=int, default=0)
    ap.add_argument("--rank", type=int, default=0)
    ap.add_argument("--world", type=int, default=0)
    ap.add_argument("--label", default="")
    ap.add_argument("--word", default="")
    ap.add_argument("--kind", default="ddp", choices=["ddp", "mesh"])
    ap.add_argument("--whole-s", type=float, default=0.0)
    ap.add_argument("--out", default="/tmp/nb3")
    ap.add_argument("--runid", default="")
    ap.add_argument("--wait", type=float, default=600.0)
    a = ap.parse_args()
    return {"ram": cmd_ram, "put": cmd_put, "matrix": cmd_matrix}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
