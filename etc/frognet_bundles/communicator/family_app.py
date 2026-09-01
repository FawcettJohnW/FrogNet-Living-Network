#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
family_app.py - the FrogNet Family app (standalone, stdlib Tk).

Supersedes family_hub.py's bundle-only launcher. This is the multi-network family
home screen: one app that shows the whole family across every pond and chorus it spans,
with the one-byte NEED_HELP beacon surfaced at the top, a strip of which networks are
lit, the family roster with presence, and the bundle grid underneath. The mesh stays
invisible; the user sees their people.

It renders family_view.home_model() over the live Communicator spine. The view-model is
the headless core (tested in test_communicator.py); this file is the rendering shell.

Run (on a box with a display):
  python3 family_app.py --demo                      # stands up a sample multi-network family
  python3 family_app.py --bundles /path/to/etc/frognet_bundles --demo

Box wiring: replace _demo_world() with the real Communicator + the roster the node
learns from the mesh (the federated pond/chorus membership), and the same render runs.
"""
import argparse
import tkinter as tk

from communicator import Communicator, converge_presence
from family_view import home_model
from presence_codex import NEED_HELP
from launcher import LocalFileBeacons

BG = "#12161b"; CARD = "#1b222b"; INK = "#e8edf2"; MUTED = "#8a96a3"
ACCENT = "#3fb68b"; LINE = "#26303a"; ALERT = "#e0533d"; DARK = "#404a55"


# --------------------------------------------------------------------------
# Demo world: a family across two ponds and two choruses, one calling for help.
# On the box this is replaced by the real Communicator + mesh-learned roster.
# --------------------------------------------------------------------------
def _demo_world():
    me = Communicator("10.179.179.1", "You"); me.start(); me.go_online()
    roster = [
        {"peer_id": "p-grandma", "person": "Grandma", "pond": "home-pond",   "chorus": "Seattle"},
        {"peer_id": "p-dad",     "person": "Dad",     "pond": "home-pond",   "chorus": "Seattle"},
        {"peer_id": "p-sister",  "person": "Sister",  "pond": "nyc-pond",    "chorus": "New York"},
        {"peer_id": "p-cousin",  "person": "Cousin",  "pond": "nyc-pond",    "chorus": "New York"},
        {"peer_id": "p-uncle",   "person": "Uncle",   "pond": "cabin-pond",  "chorus": "Cabin"},
    ]
    peers = {}
    for e in roster:
        c = Communicator(f"10.0.0.{abs(hash(e['peer_id'])) % 200 + 2}", e["person"])
        c.start()
        peers[e["peer_id"]] = c
    # converge presence from the reachable ones; Uncle (cabin) stays dark (no egress yet)
    peers["p-grandma"].go_online(); peers["p-grandma"].raise_beacon()   # the byte that matters
    peers["p-dad"].go_online()
    peers["p-sister"].go_online()
    peers["p-cousin"].go_away()
    for pid in ("p-grandma", "p-dad", "p-sister", "p-cousin"):
        src = peers[pid]
        for frame, _ in src.presence.emit_to(me.node_id):
            me.presence.ingest(pid, frame)
    return me, roster


class FamilyApp(tk.Tk):
    def __init__(self, comm, roster, bundle_source):
        super().__init__()
        self.comm, self.roster, self.bundles = comm, roster, bundle_source
        self.title("FrogNet Family"); self.configure(bg=BG); self.geometry("540x760")
        head = tk.Frame(self, bg=BG); head.pack(fill="x", padx=20, pady=(16, 6))
        tk.Label(head, text="FrogNet Family", fg=INK, bg=BG,
                 font=("Georgia", 20, "bold")).pack(side="left")
        self.me = tk.Label(head, text="", fg=MUTED, bg=BG, font=("Helvetica", 10))
        self.me.pack(side="right")
        self.body = tk.Frame(self, bg=BG); self.body.pack(fill="both", expand=True, padx=18, pady=6)
        tk.Button(self, text="Refresh", command=self.render, font=("Helvetica", 10),
                  fg=INK, bg=CARD, bd=0, padx=12, pady=6, cursor="hand2").pack(pady=(0, 12))
        self.render()

    # -- rendering -----------------------------------------------------------
    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        m = home_model(self.comm, self.roster, self.bundles)
        s = m["self"]
        self.me.config(text=f"{s.get('name','?')} . {s.get('status','')}")

        if m["alerts"]:                                  # the one byte, surfaced first
            self._alerts(m["alerts"])
        self._networks(m["networks"])
        for group in m["family"]:
            self._family_group(group)
        if m["bundles"]:
            self._bundles(m["bundles"])

    def _alerts(self, alerts):
        bar = tk.Frame(self.body, bg=ALERT); bar.pack(fill="x", pady=(0, 10))
        who = ", ".join(a["name"] for a in alerts)
        tk.Label(bar, text=f"\u26a0  Needs help: {who}", fg="#fff", bg=ALERT,
                 font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x", padx=12, pady=10)

    def _networks(self, networks):
        strip = tk.Frame(self.body, bg=BG); strip.pack(fill="x", pady=(0, 8))
        tk.Label(strip, text="Networks", fg=MUTED, bg=BG,
                 font=("Helvetica", 10, "bold"), anchor="w").pack(fill="x")
        row = tk.Frame(strip, bg=BG); row.pack(fill="x", pady=(2, 0))
        for n in networks:
            lit = n["reachable"]
            chip = tk.Frame(row, bg=CARD); chip.pack(side="left", padx=(0, 6))
            dot = ACCENT if lit else DARK
            tk.Label(chip, text="\u25cf", fg=dot, bg=CARD, font=("Helvetica", 10)).pack(side="left", padx=(8, 2), pady=4)
            label = f"{n['chorus']} ({n['pond']})" if False else n["chorus"]
            tk.Label(chip, text=f"{label} . {n['online']}/{n['members']}",
                     fg=(INK if lit else MUTED), bg=CARD, font=("Helvetica", 10)).pack(side="left", padx=(0, 8))

    def _family_group(self, group):
        title = group["chorus"]
        if len(group["ponds"]) > 1:
            title += "  \u2014 across " + ", ".join(group["ponds"])
        tk.Label(self.body, text=title, fg=ACCENT, bg=BG,
                 font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x", pady=(12, 4))
        for mem in group["members"]:
            self._member(mem)

    def _member(self, mem):
        card = tk.Frame(self.body, bg=CARD); card.pack(fill="x", pady=3)
        if mem["beacon"] == NEED_HELP:
            dot, sub = ALERT, "needs help"
        elif not mem["reachable"]:
            dot, sub = DARK, "offline"
        elif mem["status"] == "online":
            dot, sub = ACCENT, "online"
        else:
            dot, sub = MUTED, mem["status"]
        tk.Label(card, text="\u25cf", fg=dot, bg=CARD, font=("Helvetica", 14)).pack(side="left", padx=(12, 6), pady=10)
        txt = tk.Frame(card, bg=CARD); txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=mem["name"], fg=INK, bg=CARD,
                 font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(txt, text=f"{sub} . {mem['pond']}", fg=MUTED, bg=CARD,
                 font=("Helvetica", 9), anchor="w").pack(fill="x")
        callable_ = mem["reachable"]
        tk.Button(card, text="Call", command=lambda mm=mem: self._call(mm),
                  font=("Helvetica", 10, "bold"),
                  fg=("#06231a" if callable_ else MUTED),
                  bg=(ACCENT if callable_ else CARD), bd=0, padx=14, pady=6,
                  cursor=("hand2" if callable_ else "arrow")).pack(side="right", padx=(4, 12))

    def _call(self, mem):
        # the A/V codex is the first codex; opening a call is one substrate session
        self.comm.call(f"call-{mem['peer_id']}")
        self.me.config(text=f"calling {mem['name']}\u2026")

    def _bundles(self, by_hub):
        tk.Label(self.body, text="Apps", fg=MUTED, bg=BG,
                 font=("Helvetica", 10, "bold"), anchor="w").pack(fill="x", pady=(14, 2))
        for hub, items in by_hub.items():
            for b in items:
                card = tk.Frame(self.body, bg=CARD); card.pack(fill="x", pady=3)
                tk.Label(card, text=b.get("title", b.get("module", "?")), fg=INK, bg=CARD,
                         font=("Helvetica", 12, "bold"), anchor="w").pack(side="left", padx=12, pady=8)
                tk.Label(card, text=hub, fg=MUTED, bg=CARD,
                         font=("Helvetica", 9)).pack(side="right", padx=12)


def main():
    ap = argparse.ArgumentParser(description="FrogNet Family app (multi-network)")
    ap.add_argument("--bundles", help="bundles root for the app grid")
    ap.add_argument("--demo", action="store_true", help="stand up a sample multi-network family")
    a = ap.parse_args()
    src = LocalFileBeacons(a.bundles) if a.bundles else None
    if a.demo:
        comm, roster = _demo_world()
    else:
        comm = Communicator("10.179.179.1", "You"); comm.start(); comm.go_online()
        roster = []                                   # box wires the mesh-learned roster here
    FamilyApp(comm, roster, src).mainloop()


if __name__ == "__main__":
    main()
