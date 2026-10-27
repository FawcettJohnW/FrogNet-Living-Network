#!/usr/bin/env python3
"""s_plot.py PB_DIR DAY_JSONL MEM_SERIES... OUT.png -- [S_PLOT_V1] experiment S, one figure: Day's method's discovery
(rlite's policies, s_day_model.py) across five physical shapes of the egress level, against memory (the FD&F region,
measured by tools/netns-scale-test.sh), at S subnets, against the number of egress points E.
Delay panel assumption (stated on the figure, the same for both): 10 ms one way per egress-level (WAN) link, 0.1 ms per
LAN link, 1 ms of processing per member that handles the change; memory's two steps are the writer's leg to the RAM
host and the RAM host's leg to the reader, 10 ms each, plus 1 ms each."""
import json, re, sys, collections
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, __import__("os").path.dirname(__file__))
import s_day_model as M

day = [json.loads(l) for l in open(sys.argv[2])]
out = sys.argv[-1]
mem = {}
for f in sys.argv[3:-1]:
    for l in open(f):
        m = re.search(r"\[E=(\d+)\] RESULT size S=(\d+) E=\d+ participants_bytes=(\d+) mappings_bytes=(\d+)", l)
        if m and int(m.group(3)) > 0: mem[int(m.group(1))] = (int(m.group(3)) + int(m.group(4))) / 1024   # a run that died (E=100: the bed ran out of memory) reports 0: not a measurement
S = day[0]["S"]
FAM = ["chain", "tree", "star", "partial", "mesh"]
LBL = {"chain": "chain", "tree": "binary tree", "star": "star (hub)", "partial": "partial mesh (deg 4)", "mesh": "full mesh"}
COL = {"chain": "#2a78d6", "tree": "#eb6834", "star": "#1baf7a", "partial": "#eda100", "mesh": "#e87ba4", "memory": "#008300"}
MRK = {"chain": "o", "tree": "s", "star": "^", "partial": "D", "mesh": "v", "memory": "*"}
by = collections.defaultdict(dict)
for r in day: by[r["layout"]][r["E"]] = r
Es = sorted(by["star"])

# per-member replicated RIB (LowerFlow 2L, DFT N, AA N objects, at their full-message size per object)
lf = (M.lf_msg([("a", "b")] * 10) - M.WIRE_HDR) / 10; df = (M.dft_msg(["x"] * 10) - M.WIRE_HDR) / 10; aa = (M.aa_msg(["x"] * 10) - M.WIRE_HDR) / 10
def rib_kb(r): return r["members"] * (2 * r["links"] * lf + r["members"] * (df + aa)) / 1024   # every member holds the whole RIB
def delay_ms(L, E):
    """the flood's worst path from the changed child: LAN hop to its egress node, the egress level's eccentricity in WAN
    hops, a LAN hop down to the farthest child; 1 ms per member handling it"""
    adj = M.graph(S, E, L); src = "c%d" % S if S > E else "e%d" % E
    d = M.bfs(adj, src); far = max(d.values()); wan = max(0, far - 2) if E > 1 else 0
    return wan * 10 + (far - wan) * 0.1 + far * 1.0

plt.rcParams.update({"font.size": 9, "axes.edgecolor": "#b9b8b1", "axes.labelcolor": "#3d3c36", "xtick.color": "#5c5b54",
                     "ytick.color": "#5c5b54", "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.color": "#ebeae4", "grid.linewidth": 0.8, "lines.linewidth": 2, "lines.markersize": 6})
fig, ax = plt.subplots(2, 3, figsize=(15, 8.6), facecolor="#fcfcfb")
fig.suptitle(f"Discovery by Day's method (rlite link-state) across physical topologies vs memory (FD&F region)  --  {S} subnets, E egress points",
             fontsize=12, color="#1a1a19", x=0.01, ha="left")

ENDS = collections.defaultdict(list)
def series(a, ys, key, label_at_end=True):
    xs = [e for e in Es if ys.get(e) is not None]; v = [ys[e] for e in xs]
    a.plot(xs, v, color=COL[key], marker=MRK[key], label=LBL.get(key, key), clip_on=False)
    if label_at_end and xs: ENDS[a].append((xs[-1], v[-1], LBL.get(key, key)))
def place_labels():
    """direct labels at line ends; lines ending within 4% of the axis height of each other share one label"""
    for a, ends in ENDS.items():
        fig.canvas.draw(); h = a.get_window_extent().height
        pts = sorted((a.transData.transform((x, y))[1], x, y, t) for x, y, t in ends)
        groups = []
        for py, x, y, t in pts:
            if groups and py - groups[-1][-1][0] < 0.04 * h: groups[-1].append((py, x, y, t))
            else: groups.append([(py, x, y, t)])
        for g in groups:
            x = max(p[1] for p in g); y = g[len(g) // 2][2]
            a.annotate(" / ".join(p[3] for p in g), (x, y), xytext=(5, 0), textcoords="offset points", fontsize=7.5, color="#3d3c36", va="center")

def panel(a, title, ylabel, log=False):
    a.set_title(title, fontsize=10, loc="left", color="#1a1a19"); a.set_xscale("log"); a.set_xticks(Es); a.set_xticklabels([str(e) for e in Es])
    a.set_xlabel("egress points E"); a.set_ylabel(ylabel)
    if log: a.set_yscale("log")
    a.set_facecolor("#fcfcfb")

a = ax[0][0]; panel(a, "Worst-case hops a change must cross", "member hops (members that relay it)")
for f in FAM: series(a, {e: by[f][e]["eccentricity_hops"] for e in Es}, f)
series(a, {e: 2 for e in Es}, "memory"); a.lines[-1].set_label("memory: 2 steps (writer -> RAM host -> reader)")
a = ax[0][1]; panel(a, "Discovery delay after one change (model, see note)", "ms to the farthest member")
for f in FAM: series(a, {e: delay_ms(f, e) for e in Es}, f)
series(a, {e: 22 for e in Es}, "memory")
a = ax[0][2]; panel(a, "Transfers of one change (Day: every link it floods)", "transfers", log=True)
for f in FAM: series(a, {e: by[f][e]["change"]["transfers"] for e in Es}, f)
a = ax[1][0]; panel(a, "Refresh traffic, every 30 s, forever", "KB per 30 s", log=True)
for f in FAM: series(a, {e: by[f][e]["refresh_per_30s"]["bytes"] / 1024 for e in Es}, f)
a.text(0.02, 0.93, "memory: 0 -- nothing moves at rest", transform=a.transAxes, fontsize=9, color="#008300")
a = ax[1][1]; panel(a, "Cold start: objects moved to build the network", "object transfers", log=True)
for f in FAM: series(a, {e: by[f][e]["cold"]["object_transfers"] for e in Es}, f)
a = ax[1][2]; panel(a, "State the network holds: Day's RIB in every member vs memory's one copy", "KB", log=True)
for f in FAM: series(a, {e: rib_kb(by[f][e]) for e in Es}, f)
series(a, mem, "memory"); a.lines[-1].set_label("memory: the region, one copy (measured)")
place_labels()
h, l = ax[0][0].get_legend_handles_labels()
fig.legend(h, l, loc="lower center", ncol=6, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.035))
fig.text(0.01, 0.005, "Day's side: rlite user/uipcps as written (flood to every neighbour but the sender; whole RIB on enrollment, 10 objects per CDAP "
         "message; own objects re-sent every 30 s), sizes from rlite's own .proto encodings, per message +62 B of PCI/UDP/IP/Ethernet. "
         "Delay panel: 10 ms per WAN link, 0.1 ms per LAN link, 1 ms per member handling the change, the same for memory's two steps. "
         "Memory's state measured on the real daemon (netns, r27).", fontsize=7.5, color="#5c5b54", wrap=True)
fig.tight_layout(rect=(0, 0.08, 0.97, 0.95)); fig.savefig(out, dpi=140, facecolor=fig.get_facecolor())
print(out)
