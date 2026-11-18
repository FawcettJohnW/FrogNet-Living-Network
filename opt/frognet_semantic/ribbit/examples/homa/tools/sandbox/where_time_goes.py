#!/usr/bin/env python3
"""where_time_goes.py <dir of *.metrics.txt>: one table per end and frame size from the [WIRE-METRICS] tables the
drivers print ([WHERE_THE_TIME_GOES_V1]), and one per size from the Homa API interface's [SOFTHOMA-METRICS] lines.
The last table each process printed for a kind and size is the one counted. Shares are of all the time the requests
of that kind took, so the largest share is where the time goes."""
import collections, glob, re, statistics as S, sys
d = sys.argv[1]
agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, [], 0.0])); tot = collections.defaultdict(lambda: [0, 0.0])
soft = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0])); softn = collections.defaultdict(lambda: [0, 0])
for p in sorted(glob.glob(d + "/*.metrics.txt")):
    last, cur, lastsoft = {}, None, {}
    for l in open(p, errors="replace"):
        src = l.split(":", 1)[0]
        if "[WIRE-METRICS] " in l:
            txt = l.split("[WIRE-METRICS] ", 1)[1]
            m = re.match(r"\S+ (\S+) (\S+) frames<=(\S+) n=(\d+) mean total ([\d.]+)ms", txt)
            if m:
                cur = (src, m.group(1), m.group(3)); last[cur] = {"n": int(m.group(4)), "mean": float(m.group(5)), "rows": []}; continue
            m = re.match(r"\s+([\d.]+)% (\S+)\s+n=(\d+)\s+([\d.]+) / ([\d.]+) / ([\d.]+)", txt)
            if m and cur:
                last[cur]["rows"].append((m.group(2), float(m.group(1)), float(m.group(4)), float(m.group(6))))
        elif "[SOFTHOMA-METRICS] " in l:
            m = re.search(r"pid=(\d+) \([^)]*\) messages (\S+): (\d+), (\d+) bytes:(.*)", l)
            if m:
                lastsoft[(src, m.group(1), m.group(2))] = (int(m.group(3)), int(m.group(4)), re.findall(r" (\S+) n=(\d+) mean=([\d.]+)ms max=([\d.]+)ms sum=([\d.]+)ms;", m.group(5)))
    for (src, who, cls), t in last.items():
        k = (who, cls); tot[k][0] += t["n"]; tot[k][1] += t["n"] * t["mean"]
        for name, share, p50, mx in t["rows"]:
            a = agg[k][name]; a[0] += share / 100 * t["n"] * t["mean"]; a[1].append(p50); a[2] = max(a[2], mx)
    for (src, pid, cls), (n, b, rows) in lastsoft.items():
        softn[cls][0] += n; softn[cls][1] += b
        for name, cnt, mean, mx, sm in rows:
            a = soft[cls][name]; a[0] += int(cnt); a[1] += float(sm); a[2] = max(a[2], float(mx))
order = {"1000": 0, "10000": 1, "100000": 2, "1000000": 3, "any": 4}
for k in sorted(tot, key=lambda k: (k[0], order.get(k[1], 9))):
    n, ms = tot[k]
    print("\n%s end, frames up to %s bytes: %d requests, mean %.2f ms inside the driver" % (k[0], k[1] if k[1] != "any" else "any size (over 1,000,000)", n, ms / n))
    for name, a in sorted(agg[k].items(), key=lambda kv: -kv[1][0])[:10]:
        print("  %5.1f%%  %-42s typical %.3f ms  worst %.1f ms" % (100 * a[0] / ms, name, S.median(a[1]), a[2]))
for cls in ("<=10,000", "<=240,000", "larger"):
    if cls not in softn: continue
    print("\nHoma API interface, messages %s bytes: %d messages, %d bytes" % (cls, softn[cls][0], softn[cls][1]))
    for name, a in sorted(soft[cls].items(), key=lambda kv: -kv[1][1]):
        print("  %-34s n=%-8d mean %.3f ms  worst %.1f ms  all together %.0f ms" % (name, a[0], a[1] / max(1, a[0]), a[2], a[1]))
