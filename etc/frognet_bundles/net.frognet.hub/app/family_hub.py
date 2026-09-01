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
family_hub.py - the FrogNet Family app launcher (standalone, stdlib Tk).

Makes every installed module available through one app. Discovery is by **beacon**:
each bundle ships a `bundle.json` (module/title/hub/app); the hub scans the bundles
root, groups by hub, and launches a module's standalone app on tap. This file-based
discovery is the local-install stand-in for the network beacon (an
`installed_family_plugin` sensor in the transient DB) - same shape (module/title/hub),
so moving to mesh-wide discovery later swaps the source, not this launcher.

Run:
  python3 family_hub.py                         # scans /etc/frognet_bundles
  python3 family_hub.py --bundles /path/to/etc/frognet_bundles   # dev / custom root
"""
import os, sys, json, argparse, subprocess
import tkinter as tk

HUBS = [   # display order + labels; unknown hubs fall through to "More"
    ("communications",   "Communications"),
    ("family-management","Family"),
    ("games",            "Games"),
]
BG="#12161b"; CARD="#1b222b"; INK="#e8edf2"; MUTED="#8a96a3"; ACCENT="#3fb68b"; LINE="#26303a"


def default_root():
    for c in (os.environ.get("FROGNET_BUNDLES"), "/etc/frognet_bundles"):
        if c and os.path.isdir(c):
            return c
    return "/etc/frognet_bundles"


def discover(root):
    """Read every bundle.json beacon under root -> list of dicts (+ resolved paths)."""
    found = []
    if not os.path.isdir(root):
        return found
    for name in sorted(os.listdir(root)):
        bdir = os.path.join(root, name)
        man = os.path.join(bdir, "bundle.json")
        if not os.path.isfile(man):
            continue
        try:
            with open(man) as f:
                b = json.load(f)
        except Exception:
            continue
        b["_dir"] = bdir
        b["_app"] = os.path.join(bdir, b.get("app", ""))
        b["_runnable"] = bool(b.get("app")) and os.path.isfile(b["_app"])
        found.append(b)
    return found


class Hub(tk.Tk):
    def __init__(self, root_dir):
        super().__init__()
        self.root_dir = root_dir
        self.title("FrogNet Family"); self.configure(bg=BG); self.geometry("520x620")
        head = tk.Frame(self, bg=BG); head.pack(fill="x", padx=20, pady=(16,4))
        tk.Label(head, text="FrogNet Family", fg=INK, bg=BG,
                 font=("Georgia", 20, "bold")).pack(side="left")
        self.status = tk.Label(head, text="", fg=MUTED, bg=BG, font=("Helvetica", 10))
        self.status.pack(side="right")
        self.body = tk.Frame(self, bg=BG); self.body.pack(fill="both", expand=True, padx=20, pady=8)
        tk.Button(self, text="Refresh", command=self.render, font=("Helvetica", 10),
                  fg=INK, bg=CARD, bd=0, padx=12, pady=6, cursor="hand2").pack(pady=(0,12))
        self.render()

    def launch(self, bundle):
        if not bundle.get("_runnable"):
            self.status.config(text=f"{bundle.get('title','?')}: no app to launch"); return
        try:
            subprocess.Popen([sys.executable, bundle["_app"]], cwd=bundle["_dir"])
            self.status.config(text=f"opened {bundle['title']}")
        except Exception as e:
            self.status.config(text=f"launch failed: {e}")

    def render(self):
        for w in self.body.winfo_children(): w.destroy()
        bundles = discover(self.root_dir)
        if not bundles:
            tk.Label(self.body, text=f"No modules found under\n{self.root_dir}",
                     fg=MUTED, bg=BG, font=("Helvetica", 11), justify="center").pack(pady=40)
            return
        by_hub = {}
        for b in bundles:
            by_hub.setdefault(b.get("hub", "more"), []).append(b)
        order = [h for h, _ in HUBS] + [h for h in by_hub if h not in {x for x, _ in HUBS}]
        labels = dict(HUBS)
        for hub in order:
            items = by_hub.get(hub)
            if not items: continue
            tk.Label(self.body, text=labels.get(hub, hub.title()), fg=ACCENT, bg=BG,
                     font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x", pady=(12,4))
            for b in items:
                self._tile(b)

    def _tile(self, b):
        card = tk.Frame(self.body, bg=CARD); card.pack(fill="x", pady=4)
        txt = tk.Frame(card, bg=CARD); txt.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        tk.Label(txt, text=b.get("title", b.get("module", "?")), fg=INK, bg=CARD,
                 font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x")
        if b.get("summary"):
            tk.Label(txt, text=b["summary"], fg=MUTED, bg=CARD, font=("Helvetica", 10),
                     anchor="w", wraplength=360, justify="left").pack(fill="x")
        state = "Open" if b.get("_runnable") else "-"
        btn = tk.Button(card, text=state, command=lambda bb=b: self.launch(bb),
                        font=("Helvetica", 11, "bold"),
                        fg=("#06231a" if b.get("_runnable") else MUTED),
                        bg=(ACCENT if b.get("_runnable") else CARD), bd=0, padx=16, pady=8,
                        cursor=("hand2" if b.get("_runnable") else "arrow"))
        btn.pack(side="right", padx=12)


def main():
    ap = argparse.ArgumentParser(description="FrogNet Family hub launcher")
    ap.add_argument("--bundles", help="bundles root (default $FROGNET_BUNDLES or /etc/frognet_bundles)")
    a = ap.parse_args()
    Hub(a.bundles or default_root()).mainloop()


if __name__ == "__main__":
    main()
