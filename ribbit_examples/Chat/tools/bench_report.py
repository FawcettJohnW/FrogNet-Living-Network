#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""bench_report.py [--out report.html] NAME=CLIENT_DIR[:SERVER_DIR] [NAME=DIR ...]

Turns what run_bench.sh recorded into one page of graphs an administrator can
read. Give it one directory per server (or per machine, or per day) and it draws
them side by side; THE FIRST ONE IS THE BASELINE, and every throughput bar says
how many times the baseline it is. Those ratios are the guidance; the absolute
numbers belong to the hardware they were measured on.

    ./bench_report.py "PHP + MariaDB"=bench_php "C++ server"=bench_cpp:server_cpp

One self-contained HTML file: inline SVG, no scripts, no network, light and dark.
"""
import csv, glob, html, json, math, os, sys

COLORS = ["#c2571a", "#1f7a8c", "#6b4fa0", "#3d8b37", "#b0344d"]


def load(spec):
    name, _, dirs = spec.partition("=")
    cdir, _, sdir = dirs.partition(":")
    runs = {}
    for p in sorted(glob.glob(os.path.join(cdir, "runs", "*.json"))):
        try:
            d = json.load(open(p)); runs[d.get("label") or os.path.basename(p)] = d
        except ValueError:
            pass
    if not runs:
        sys.exit("no runs/*.json under %s" % cdir)
    srv = []
    if sdir and os.path.isfile(os.path.join(sdir, "server_metrics.csv")):
        srv = list(csv.DictReader(open(os.path.join(sdir, "server_metrics.csv"))))
    env = json.load(open(os.path.join(cdir, "env.json"))); path = json.load(open(os.path.join(cdir, "path.json")))
    return {"name": name, "runs": runs, "srv": srv, "env": env, "path": path}


def fmt(v):
    if v >= 1e6: return "%.1fM" % (v / 1e6)
    if v >= 1e4: return "%.0fk" % (v / 1e3)
    if v >= 1e3: return "%.1fk" % (v / 1e3)
    if v >= 10: return "%.0f" % v
    return "%.2g" % v


def bars(title, sub, groups, sets, unit, log=False, ratio=True):
    """groups: [label]; sets: [(name, color, [value or None per group])]"""
    W, H, L, R, T, B = 720, 300, 60, 16, 16, 58
    vals = [v for _, _, vs in sets for v in vs if v]
    if not vals:
        return ""
    top = max(vals); lo = min(vals)
    def y(v):
        if log:
            a, b = math.log10(max(lo / 3, 1e-3)), math.log10(top * 1.6)
            return T + (H - T - B) * (1 - (math.log10(max(v, 10 ** a)) - a) / (b - a))
        return T + (H - T - B) * (1 - v / (top * 1.18))
    gw = (W - L - R) / len(groups); bw = min(64, gw * 0.8 / len(sets))
    o = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (W, H, html.escape(title))]
    o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" class="ax"/>' % (L, H - B, W - R, H - B))
    ticks = [10 ** k for k in range(int(math.floor(math.log10(max(lo / 3, 1e-3)))), int(math.ceil(math.log10(top * 1.6))) + 1)] if log else [top * f for f in (0.25, 0.5, 0.75, 1.0)]
    for t in ticks:
        yy = y(t)
        if T <= yy <= H - B:
            o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick" text-anchor="end">%s</text>' % (L, yy, W - R, yy, L - 6, yy + 4, fmt(t)))
    for gi, g in enumerate(groups):
        cx = L + gw * (gi + 0.5); base = sets[0][2][gi]
        for si, (nm, col, vs) in enumerate(sets):
            v = vs[gi]
            if not v:
                continue
            x = cx - bw * len(sets) / 2 + si * bw
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" rx="3"><title>%s: %s %s</title></rect>' % (x + 2, y(v), bw - 4, H - B - y(v), col, html.escape(nm), fmt(v), unit))
            lab = fmt(v) + ((" · %s×" % fmt(v / base)) if ratio and si and base else "")
            o.append('<text x="%.1f" y="%.1f" class="val" text-anchor="middle">%s</text>' % (x + bw / 2, y(v) - 5, lab))
        for li, line in enumerate(g.split("|")):
            o.append('<text x="%.1f" y="%d" class="lab" text-anchor="middle">%s</text>' % (cx, H - B + 16 + 13 * li, html.escape(line)))
    o.append("</svg>")
    return '<figure><h3>%s</h3><p class="sub">%s</p>%s</figure>' % (html.escape(title), sub, "".join(o))


def lines(title, sub, series, xlabel, ylabel, ymax=100.0):
    """series: [(name, color, [(x, y)])], x on a log scale"""
    W, H, L, R, T, B = 720, 300, 60, 16, 16, 48
    xs = [x for _, _, pts in series for x, _ in pts]
    if not xs:
        return ""
    a, b = math.log10(min(xs)), math.log10(max(xs)); b = b if b > a else a + 1
    X = lambda x: L + (W - L - R) * (math.log10(x) - a) / (b - a)
    Y = lambda v: T + (H - T - B) * (1 - v / ymax)
    o = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (W, H, html.escape(title))]
    for v in (0, 25, 50, 75, 100):
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick" text-anchor="end">%d%%</text>' % (L, Y(v), W - R, Y(v), L - 6, Y(v) + 4, v))
    for x in sorted(set(xs)):
        o.append('<text x="%.1f" y="%d" class="tick" text-anchor="middle">%s</text>' % (X(x), H - B + 16, fmt(x)))
    o.append('<text x="%.1f" y="%d" class="lab" text-anchor="middle">%s</text>' % ((L + W - R) / 2, H - 6, html.escape(xlabel)))
    for nm, col, pts in series:
        pts = sorted(pts)
        o.append('<polyline fill="none" stroke="%s" stroke-width="3" stroke-linejoin="round" points="%s"/>' % (col, " ".join("%.1f,%.1f" % (X(x), Y(min(v, ymax))) for x, v in pts)))
        for x, v in pts:
            o.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"><title>%s at %s/s: %.1f%%</title></circle>' % (X(x), Y(min(v, ymax)), col, html.escape(nm), fmt(x), v))
    o.append("</svg>")
    return '<figure><h3>%s</h3><p class="sub">%s</p>%s</figure>' % (html.escape(title), sub, "".join(o))


def main(argv):
    out = "report.html"
    if argv[:1] == ["--out"]:
        out = argv[1]; argv = argv[2:]
    if not argv:
        sys.exit(__doc__)
    S = [load(a) for a in argv]
    for i, s in enumerate(S):
        s["color"] = COLORS[i % len(COLORS)]
    g = lambda s, k, f, d=None: (s["runs"].get(k) or {}).get(f, d)
    legend = "".join('<span class="key"><i style="background:%s"></i>%s</span>' % (s["color"], html.escape(s["name"])) for s in S)
    body = []

    # 0 -- the thing that is not a number
    tot = lambda f: sum(d.get(f, 0) for s in S for d in s["runs"].values())
    dropped = sum(d.get("dropped", 0) for s in S for k, d in s["runs"].items() if k.startswith("contention_"))
    msgs = sum(d.get("writes", 0) for s in S for k, d in s["runs"].items() if k.startswith("contention_"))
    body.append('<section class="cards"><div class="card %s"><b>%s</b><span>sequence numbers dropped in the contention tests<br>out of %s written</span></div>'
                '<div class="card %s"><b>%s</b><span>values delivered out of order,<br>in any test</span></div><div class="card %s"><b>%s</b><span>values delivered twice,<br>in any test</span></div></section>'
                % ("ok" if not dropped else "bad", fmt(dropped) if dropped else "0", fmt(msgs), "ok" if not tot("out_of_order") else "bad", tot("out_of_order"), "ok" if not tot("delivered_twice") else "bad", tot("delivered_twice")))

    # 1 -- max throughput, by ratio
    groups = ["one writer|waits for each ack", "10 clients, all to all|each waits for its ack", "10 clients, all to all|nobody waits (the ceiling)"]
    keys = [("one_pair_ack_flat", "writes_per_s"), ("contention_ack", "writes_per_s"), ("contention_noack", "writes_per_s")]
    body.append(bars("Maximum throughput", "Writes per second the memory accepted. The label on each bar is how many times the first server it is &mdash; <b>that ratio is the guidance</b>; the numbers belong to this hardware. Log scale.",
                     groups, [(s["name"], s["color"], [g(s, k, f) for k, f in keys]) for s in S], "writes/s", log=True))

    # 2 -- when does a reader start to skip
    ser = []
    for s in S:
        pts = [(d["rate"], 100.0 * d["skipped"] / max(1, d["writes"])) for k, d in s["runs"].items() if k.startswith("one_pair_paced_")]
        if pts: ser.append((s["name"], s["color"], pts))
    body.append(lines("How fast can one sender go before its reader starts skipping?", "One sender writing the same cell at a steady rate; the share of values its reader never saw. A cell holds one value, so a reader that is slower than the writer skips &mdash; by design. Stay left of where the line lifts off if every value matters.",
                      ser, "writes per second from one sender (log scale)", "skipped"))

    # 3 -- how long a message waits
    groups = ["each writer waits|median", "each writer waits|99th percentile", "nobody waits|median", "nobody waits|99th percentile"]
    k4 = [("contention_ack", "p50"), ("contention_ack", "p99"), ("contention_noack", "p50"), ("contention_noack", "p99")]
    body.append(bars("How long a message waits under contention", "10 clients all writing to each other, every message kept. Milliseconds from written to in the reader's hands. Nothing is lost either way; <b>not waiting for acknowledgements buys throughput with delay</b>. Log scale.",
                     groups, [(s["name"], s["color"], [(g(s, k, "delivery_ms") or {}).get(f) for k, f in k4]) for s in S], "ms", log=True, ratio=False))

    # 4 -- does adding clients add throughput
    cl = sorted({d["clients"] for s in S for k, d in s["runs"].items() if k.startswith("contention_ack")})
    sets = []
    for s in S:
        by = {d["clients"]: d["writes_per_s"] for k, d in s["runs"].items() if k.startswith("contention_ack")}
        sets.append((s["name"], s["color"], [by.get(c) for c in cl]))
    body.append(bars("Does adding clients add throughput?", "Total acknowledged writes per second as clients are added. Where the bars stop growing, the server is the limit: each client's share is the total divided by the clients.",
                     ["%d clients" % c for c in cl], sets, "writes/s", log=False, ratio=False))

    # 5 -- what it costs the server
    rows = []
    for s in S:
        c = s["runs"].get("contention_ack") or {}; sv = c.get("server") or {}
        win = [r for r in s["srv"] if c and c["started_epoch"] <= float(r["epoch"]) <= c["started_epoch"] + c["seconds"] + 1]
        num = lambda col: [float(r[col]) for r in win if r.get(col) not in (None, "")]
        rows.append("<tr><th style='color:%s'>%s</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            s["color"], html.escape(s["name"]), fmt(c.get("writes_per_s", 0)) if c else "&ndash;",
            ("%.0f%%" % (sum(num("cpu_busy_pct")) / len(num("cpu_busy_pct")))) if num("cpu_busy_pct") else "&ndash;",
            ("%.0f µs" % sv["cpu_us_per_write"]) if sv.get("pid") else "&ndash;",
            ("%.0f MiB" % sv["rss_mib"]) if sv.get("pid") else "&ndash;",
            ("%d" % max(num("apache2_procs"))) if num("apache2_procs") and max(num("apache2_procs")) else ("%d threads" % sv["threads"] if sv.get("pid") else "&ndash;"),
            (fmt(max(num("port_recvq_bytes"))) + " B") if num("port_recvq_bytes") else "&ndash;"))
    body.append("<figure><h3>What the contention test cost the server</h3><p class='sub'>&ndash; means it was not measured: run <code>server_monitor.sh</code> on the server, and pass <code>--server-pid</code> when client and server share a machine.</p>"
                "<div class='scroll'><table><tr><th></th><th>writes/s</th><th>machine CPU busy</th><th>server CPU per write</th><th>server memory</th><th>Apache workers / threads</th><th>largest receive backlog</th></tr>%s</table></div></figure>" % "".join(rows))

    where = "".join("<li><b style='color:%s'>%s</b> &mdash; %s &rarr; %s:%s, %s; path %s; client %s, %s cores</li>" % (
        s["color"], html.escape(s["name"]), html.escape(s["env"]["client_host"]), html.escape(s["env"]["target"]["host"]), s["env"]["target"]["port"], s["env"]["started_utc"],
        ("ping %.2f ms" % s["path"]["ping"]["avg"]) if s["path"].get("ping") else ("TCP connect %.2f ms" % s["path"]["tcp_connect"]["median"]), html.escape(str(s["env"]["cpu_model"])), s["env"]["cores"]) for s in S)
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrogNet RAM — throughput guidance</title><style>
:root{--bg:#f6f4ee;--fg:#1d2326;--mut:#5d676b;--line:#d9d4c6;--card:#fffdf8;--ok:#2f7d4f;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14181a;--fg:#e9e6dd;--mut:#9aa4a8;--line:#2c3437;--card:#1b2124;--ok:#6cc08a;--bad:#ff8a80}}
:root[data-theme="dark"]{--bg:#14181a;--fg:#e9e6dd;--mut:#9aa4a8;--line:#2c3437;--card:#1b2124;--ok:#6cc08a;--bad:#ff8a80}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,'Iowan Old Style',serif}main{max-width:780px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:1.7rem;line-height:1.2;margin:.2em 0}h3{font-size:1.12rem;margin:0 0 2px}.sub,.lede{color:var(--mut);margin:.2em 0 .7em;font-size:.95rem}.lede{font-size:1.02rem}
figure{margin:26px 0;padding:16px 14px 10px;background:var(--card);border:1px solid var(--line);border-radius:10px}svg{width:100%;height:auto;display:block}
.ax{stroke:var(--fg);stroke-width:1}.grid{stroke:var(--line);stroke-width:1}.tick,.lab,.val{fill:var(--mut);font:12px system-ui,sans-serif}.val{fill:var(--fg);font-weight:600;font-size:11.5px}
.legend{position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:2;border-bottom:1px solid var(--line)}.key{margin-right:16px;font:600 .9rem system-ui,sans-serif;white-space:nowrap}.key i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:18px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.card b{display:block;font:700 2.2rem/1 system-ui,sans-serif}.card.ok b{color:var(--ok)}.card.bad b{color:var(--bad)}.card span{color:var(--mut);font-size:.9rem}
.scroll{overflow-x:auto}table{border-collapse:collapse;font:14px system-ui,sans-serif;min-width:620px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right}th:first-child{text-align:left}code{font-size:.88em}ul{padding-left:1.1em;color:var(--mut);font-size:.9rem}
</style></head><body><main><h1>FrogNet RAM &mdash; throughput guidance</h1>
<p class="lede">What the memory does at speed and under load, measured with <code>run_bench.sh</code>. Read the <b>ratios</b> between servers; the absolute numbers belong to the machines named at the bottom.</p>
<div class="legend">@@LEGEND@@</div>@@BODY@@<h3>Where these numbers came from</h3><ul>@@WHERE@@</ul></main></body></html>""".replace("@@LEGEND@@", legend).replace("@@BODY@@", "".join(body)).replace("@@WHERE@@", where)
    open(out, "w").write(page)
    print("wrote %s (%d bytes) from %d data set(s)" % (out, len(page), len(S)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
