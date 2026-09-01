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
calendar_app.py - Family Calendar, standalone desktop app (stdlib only).

Cross-platform: anywhere Python + Tk are present (Windows/macOS python.org builds
include Tk; Linux needs the distro's python3-tk). No third-party packages.

Two backends, same UI (the suite's standalone standard):
  LOCAL (default): embeds calendar_codex with in-memory stores - works offline.
  NET   (--connect HOST[:PORT]): a thin UnREST client to a codex on the mesh, over
        /unrest/family-calendar/<verb>. The fabric does the codec; the app reads/writes
        the shared element only.

Run:
  python3 calendar_app.py
  python3 calendar_app.py --connect family-calendar.frognet
  python3 calendar_app.py --connect 10.111.11.1:80
"""
import sys, os, json, argparse
import tkinter as tk
from tkinter import ttk
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex"))


# ----------------------------------------------------------------- backends
class LocalClient:
    def __init__(self):
        from calendar_codex import CalendarCodex, InMemoryTransientStore, InMemoryPermStore, make_event
        self.c = CalendarCodex(InMemoryTransientStore(), InMemoryPermStore())
        self._make = make_event
    def list_events(self):                 return {"events": self.c.list_events(), "ts": "0"}
    def create(self, ev):                  return self.c.create_event(self._make(**ev))
    def delete(self, uid):                 return {"ok": self.c.delete_event(uid)}
    def presence(self, who):               self.c.touch_presence(who); return self.c.who_is_here()

class HttpClient:
    def __init__(self, base, who="desktop"):
        if "://" not in base: base = "http://" + base
        self.base = base.rstrip("/"); self.who = who
        self.suffix = "/unrest/family-calendar"; self.wm = -1
    def _call(self, verb, body=None):
        url = f"{self.base}{self.suffix}/{verb}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data,
              headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    def list_events(self):  return self._call("list")
    def create(self, ev):   return self._call("create", ev)
    def delete(self, uid):  return self._call("delete", {"uid": uid})
    def presence(self, who):return self._call("presence", {"who": who}).get("who_is_here", [])


# ----------------------------------------------------------------- palette
BG="#14181d"; CARD="#1b222b"; INK="#e8edf2"; MUTED="#8a96a3"; ACCENT="#3fb68b"
LINE="#26303a"; BAD="#d96b6b"


class App(tk.Tk):
    def __init__(self, client, who="desktop"):
        super().__init__()
        self.client = client; self.who = who; self.watermark = -1; self.connected = True
        self.title("Family Calendar"); self.configure(bg=BG); self.geometry("560x640")

        top = tk.Frame(self, bg=BG); top.pack(fill="x", padx=18, pady=(14,6))
        tk.Label(top, text="Family Calendar", fg=INK, bg=BG,
                 font=("Georgia", 18, "bold")).pack(side="left")
        self.status = tk.Label(top, text="...", fg=MUTED, bg=BG, font=("Helvetica", 10))
        self.status.pack(side="left", padx=12)
        self.presence = tk.Label(top, text="", fg=MUTED, bg=BG, font=("Helvetica", 10))
        self.presence.pack(side="right")

        form = tk.Frame(self, bg=CARD); form.pack(fill="x", padx=18, pady=8)
        self.e_sum = self._entry(form, "What?");            self.e_sum.pack(fill="x", padx=10, pady=(10,4))
        row = tk.Frame(form, bg=CARD); row.pack(fill="x", padx=10, pady=4)
        self.e_start = self._entry(row, "Start  2026-06-14T10:00"); self.e_start.pack(side="left", expand=True, fill="x", padx=(0,4))
        self.e_end   = self._entry(row, "End  2026-06-14T11:00");   self.e_end.pack(side="left", expand=True, fill="x", padx=(4,0))
        self.e_loc = self._entry(form, "Where? (optional)");  self.e_loc.pack(fill="x", padx=10, pady=4)
        tk.Button(form, text="Add to the family calendar", command=self.add,
                  font=("Helvetica", 11, "bold"), fg="#06231a", bg=ACCENT,
                  activebackground="#5fd0a6", bd=0, padx=14, pady=9, cursor="hand2"
                  ).pack(fill="x", padx=10, pady=(6,12))

        self.listwrap = tk.Frame(self, bg=BG); self.listwrap.pack(fill="both", expand=True, padx=18, pady=(2,16))

        self.refresh()
        if isinstance(client, HttpClient):
            from host_float import FloatWatch, resolve_base_ip
            self._fw = FloatWatch(lambda: resolve_base_ip(self.client.base),
                                  self._on_float)
        else:
            self._fw = None
        self.after(4000, self._poll)

    def _on_float(self):
        # calendar host floated: drop stale watermark, clear disconnect, re-sync.
        self.watermark = -1
        self.connected = True
        self.refresh()

    def _entry(self, parent, placeholder):
        e = tk.Entry(parent, bg=BG, fg=INK, insertbackground=INK, relief="flat",
                     highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                     font=("Helvetica", 11))
        e._ph = placeholder; e.insert(0, placeholder); e.config(fg=MUTED)
        def fin(_):  # clear placeholder
            if e.get() == placeholder: e.delete(0, "end"); e.config(fg=INK)
        def fout(_):
            if not e.get(): e.insert(0, placeholder); e.config(fg=MUTED)
        e.bind("<FocusIn>", fin); e.bind("<FocusOut>", fout)
        return e

    def _val(self, e):
        v = e.get().strip()
        return "" if v == e._ph else v

    def add(self):
        summary = self._val(self.e_sum); start = self._val(self.e_start); end = self._val(self.e_end)
        if not summary or not start or not end: return
        ev = {"summary": summary, "start": start, "end": end, "location": self._val(self.e_loc)}
        try:
            self.client.create(ev)
            for e in (self.e_sum, self.e_start, self.e_end, self.e_loc):
                e.delete(0, "end"); e.event_generate("<FocusOut>")
            self.refresh()
        except Exception:
            self._fail()

    def render(self, events):
        for w in self.listwrap.winfo_children(): w.destroy()
        if not events:
            tk.Label(self.listwrap, text="No events yet.", fg=MUTED, bg=BG,
                     font=("Helvetica", 11)).pack(pady=24); return
        for ev in events:
            card = tk.Frame(self.listwrap, bg=CARD); card.pack(fill="x", pady=4)
            when = "All day" if ev.get("all_day") else ev.get("start", "")
            tk.Label(card, text=when, fg=ACCENT, bg=CARD, font=("Helvetica", 10),
                     width=18, anchor="w").pack(side="left", padx=(10,6), pady=8)
            body = tk.Frame(card, bg=CARD); body.pack(side="left", fill="x", expand=True)
            tk.Label(body, text=ev.get("summary",""), fg=INK, bg=CARD,
                     font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x")
            meta = " . ".join([x for x in (ev.get("location",""), ev.get("notes","")) if x])
            if meta: tk.Label(body, text=meta, fg=MUTED, bg=CARD, font=("Helvetica", 10),
                              anchor="w").pack(fill="x")
            tk.Button(card, text="?", command=lambda u=ev["uid"]: self.remove(u),
                      fg=MUTED, bg=CARD, bd=0, activebackground=CARD, cursor="hand2"
                      ).pack(side="right", padx=10)

    def remove(self, uid):
        try: self.client.delete(uid); self.refresh()
        except Exception: self._fail()

    def _fail(self):
        self.connected = False; self.status.config(text="can't reach calendar - contact admin", fg=BAD)

    def refresh(self):
        try:
            data = self.client.list_events()
            ts = int(data.get("ts", 0))
            if self.watermark >= 0 and ts < self.watermark:
                self.connected = False
                self.status.config(text="disconnected (split detected)", fg=BAD); return
            self.watermark = ts; self.connected = True
            self.status.config(text="live", fg=ACCENT)
            self.render(data.get("events", []))
            try:
                here = self.client.presence(self.who)
                self.presence.config(text=("watching: " + ", ".join(here)) if here else "")
            except Exception: pass
        except Exception:
            self._fail()

    def _poll(self):
        if self._fw is not None:
            self._fw.tick()                      # float? re-sync against the new host first
        if self.connected: self.refresh()
        self.after(4000, self._poll)


def main():
    ap = argparse.ArgumentParser(description="Family Calendar (standalone)")
    ap.add_argument("--connect", metavar="HOST[:PORT]",
                    help="join the family calendar on the mesh (default: local/offline)")
    ap.add_argument("--who", default="desktop")
    a = ap.parse_args()
    client = HttpClient(a.connect, a.who) if a.connect else LocalClient()
    App(client, a.who).mainloop()


if __name__ == "__main__":
    main()
