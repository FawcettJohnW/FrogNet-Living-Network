#!/usr/bin/env python3
"""covmap: merge per-oracle coverage into one defined-API coverage map.

Inputs:  p0/api-inventory.json            (ToRadio/FromRadio/AdminMessage/PortNum from the pinned protobufs)
         p0/cov/<oracle>.json             (apitap wire tallies, one per oracle run)
         p0/cov/native-unit-static.json   (symbol references in firmware test/ sources)
Output:  p0/coverage-map.json and p0/coverage-map.md

Wire coverage means the item crossed the wire during that oracle's run (observed, counted).
Static coverage means a native suite's source names the item's nanopb tag/enum; it says the
suite touches the item, not that it asserts on it.
"""
import glob
import json
import os
import sys

P0 = "/home/claude/mesh/p0"
inv = json.load(open(f"{P0}/api-inventory.json"))
static = json.load(open(f"{P0}/cov/native-unit-static.json"))
wire = {}
for p in sorted(glob.glob(f"{P0}/cov/*.json")):
    n = os.path.basename(p)[:-5]
    if n != "native-unit-static":
        wire[n] = json.load(open(p))
order = [o for o in (sys.argv[1:] or sorted(wire))]


def seen(w, key, name):
    return w.get(key, {}).get(name, 0)


rows = {}
for sec, keys in [
    ("ToRadio", ["to_radio.variant"]),
    ("FromRadio", ["from_radio.variant"]),
    ("AdminMessage", ["to_radio.admin", "from_radio.admin", "ota.admin"]),
    ("PortNum", ["to_radio.portnum", "from_radio.portnum", "ota.portnum"]),
]:
    rows[sec] = []
    for item in inv[sec]:
        name = item["name"]
        r = {"name": name, "num": item.get("field", item.get("value"))}
        for o in order:
            r[o] = sum(seen(wire[o], k, name) for k in keys)
        r["native-unit(static)"] = static[sec][name]
        rows[sec].append(r)

json.dump({"oracles": order, "rows": rows}, open(f"{P0}/coverage-map.json", "w"), indent=1)

md = ["# Phase 0 coverage map: Meshtastic defined client API vs. existing oracles, stock meshtasticd", ""]
md.append("Cell = frames/packets observed on the wire during that oracle's run against stock. "
          "`native-unit(static)` = native C++ suites whose source references the item (touches, not necessarily asserts).")
md.append("")
summary = ["| section | items | covered by any wire oracle | covered by native-unit (static) | covered by none |", "|---|---|---|---|---|"]
for sec, rs in rows.items():
    anyw = [r for r in rs if any(r[o] for o in order)]
    st = [r for r in rs if r["native-unit(static)"]]
    none = [r for r in rs if not any(r[o] for o in order) and not r["native-unit(static)"]]
    summary.append(f"| {sec} | {len(rs)} | {len(anyw)} | {len(st)} | {len(none)} |")
md += ["## Summary", ""] + summary + [""]
for sec, rs in rows.items():
    md.append(f"## {sec}")
    md.append("")
    md.append("| item | # | " + " | ".join(order) + " | native-unit (static) |")
    md.append("|---|---|" + "---|" * len(order) + "---|")
    for r in rs:
        su = r["native-unit(static)"]
        sus = f"{len(su)}: " + ", ".join(s.replace('test_', '') for s in su[:4]) + (" …" if len(su) > 4 else "") if su else "—"
        md.append(f"| {r['name']} | {r['num']} | " + " | ".join(str(r[o]) if r[o] else "—" for o in order) + f" | {sus} |")
    md.append("")
    none = [r["name"] for r in rs if not any(r[o] for o in order) and not r["native-unit(static)"]]
    md.append(f"**Gaps (no oracle at all): {len(none)}** — " + (", ".join(none) if none else "none"))
    md.append("")
open(f"{P0}/coverage-map.md", "w").write("\n".join(md))
print("\n".join(summary))
