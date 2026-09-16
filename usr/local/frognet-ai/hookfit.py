#!/usr/bin/env python3
"""hookfit.py -- read a DDP hook campaign and say what it means.

    python3 hookfit.py /tmp/nb3 ddp800 ddp809

Reports, per arm: step cost and what the model learned.

THE SECOND COLUMN IS NOT A FORMALITY

gloo, no-hook and `faithful` all compute the same sum. Their held-out
losses and parameter hashes MUST agree; if they do not, something is wrong
that no amount of speed excuses, and the run is a bug report rather than a
measurement.

`fresh_only` and `every_k` deliberately incorporate less. They are ALLOWED
to differ -- that is the experiment. The question they answer is not "how
fast" but "did it still learn", and the held-out loss is the answer. A 40%
saving that trains a worse model is not a saving; a 40% saving that trains
an equally good one is the whole argument.
"""
import glob
import json
import os
import statistics
import sys


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nb3"
    lo = sys.argv[2].replace("ddp", "") if len(sys.argv) > 2 else ""
    hi = sys.argv[3].replace("ddp", "") if len(sys.argv) > 3 else ""
    lo = int(lo) if lo else 0
    hi = int(hi) if hi else 10 ** 9

    runs = {}
    for f in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        b = os.path.basename(f)
        if not b.startswith("ddp") or ".r" not in b:
            continue
        try:
            n = int(b.split(".r")[0].replace("ddp", ""))
        except ValueError:
            continue
        if not (lo <= n <= hi):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if "ms_step_min" in d or d.get("status") != "ok":
            runs.setdefault(n, []).append(d)

    if not runs:
        print("no DDP runs in %s between ddp%d and ddp%d" % (root, lo, hi))
        return 1

    print("%-8s %-26s %-3s %10s %10s %12s %s"
          % ("run", "arm", "bv", "ms/step", "slowest", "heldout", "params"))
    rows = []
    for n in sorted(runs):
        ok = [d for d in runs[n] if d.get("status") == "ok"]
        if not ok:
            e = (runs[n][0].get("error") or "").splitlines()
            print("ddp%-5d FAILED: %s" % (n, e[0][:60] if e else "?"))
            continue
        W = ok[0]["world"]
        if len(ok) != W:
            print("ddp%-5d only %d of %d ranks -- dropped" % (n, len(ok), W))
            continue
        arm = "%s%s%s" % (ok[0]["backend"],
                          "/" + ok[0]["reduce"] if ok[0].get("reduce") else "",
                          " +" + ok[0]["hook"] if ok[0].get("hook") else "")
        fastest = min(d["ms_step_min"] for d in ok)
        slowest = max(d["ms_step_min"] for d in ok)
        ho = statistics.median(d["heldout"] for d in ok)
        shas = {d["param_sha"] for d in ok}
        sha = list(shas)[0] if len(shas) == 1 else "DIFFER(%d)" % len(shas)
        print("ddp%-5d %-26s %-3s %10.2f %10.2f %12.5f %s"
              % (n, arm, int(ok[0]["bucket_view"]), fastest, slowest, ho, sha))
        rows.append((n, arm, bool(ok[0]["bucket_view"]), slowest, ho, sha,
                     ok[0].get("grad_mb")))

    if not rows:
        return 1

    print()
    print("=" * 74)
    print("WHAT bucket_view COSTS OR SAVES")
    print("PyTorch copies each gradient into the bucket and back out again.")
    print("The flag makes them views instead. It helps every backend alike.")
    for arm in sorted({r[1] for r in rows}):
        off = [r[3] for r in rows if r[1] == arm and not r[2]]
        on = [r[3] for r in rows if r[1] == arm and r[2]]
        if off and on:
            a, b = statistics.median(off), statistics.median(on)
            print("  %-26s %8.2f -> %8.2f ms   %.2fx" % (arm, a, b, a / b))

    print()
    print("=" * 74)
    print("THE SUM-PRESERVING ARMS MUST AGREE")
    exact = [r for r in rows
             if r[1].startswith("gloo") or r[1].endswith("+faithful")
             or (r[1].startswith("psychedelic") and "+" not in r[1])]
    shas = {r[5] for r in exact}
    if len(exact) < 2:
        # One arm agrees with itself. Saying "they agree" here would be
        # vacuously true and read as a passed check.
        print("  only %d exact-sum arm in this range -- nothing to compare."
              % len(exact))
    elif len(shas) <= 1:
        print("  gloo, no-hook and faithful agree on the trained model"
              " (%d arms, one parameter hash)." % len(exact))
    else:
        print("  THEY DO NOT AGREE -- this is a correctness failure, not a")
        print("  performance result:")
        for r in exact:
            print("    ddp%-5d %-26s heldout %.5f  %s" % (r[0], r[1], r[4], r[5]))

    base = [r[4] for r in exact]
    if base:
        ref = statistics.median(base)
        print()
        print("=" * 74)
        print("THE ARMS THAT INCORPORATE LESS")
        print("Allowed to differ. The question is whether it mattered.")
        print("  reference held-out loss (exact-sum arms): %.5f" % ref)
        for r in rows:
            if "+fresh_only" in r[1] or "+every_k" in r[1]:
                d = (r[4] - ref) / ref * 100 if ref else 0
                verdict = ("no worse" if d <= 2 else
                           "%.0f%% worse -- weigh against the saving" % d)
                print("  ddp%-5d %-26s bv=%d  %8.2f ms  heldout %.5f  (%s)"
                      % (r[0], r[1], r[2], r[3], r[4], verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
