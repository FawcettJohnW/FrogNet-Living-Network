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
bg_app.py -- Family Backgammon, standalone desktop app (stdlib only).

Cross-platform: runs anywhere Python + Tk are present (Windows and macOS python.org
builds include Tk; on Linux install the distro's python3-tk). No third-party packages.

Two backends, chosen at launch -- same UI, same codex contract:
  LOCAL  (default): embeds backgammon_codex with in-memory stores. Plays immediately,
                    offline, hot-seat (act for whichever side is on turn).
  NET    (--connect HOST[:PORT] [--table NAME]): a thin client to a codex on the mesh,
                    speaking the UnREST suffix /unrest/family-backgammon/<verb> over HTTP.
                    The fabric does the codec; this app just reads/writes the element.

Run:
  python3 bg_app.py                          # local, offline, instant
  python3 bg_app.py --connect family-backgammon.frognet     # join a table on the mesh
  python3 bg_app.py --connect 10.111.11.1:80 --table kitchen
"""
import sys, os, json, argparse, threading, time
import tkinter as tk
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex"))

WHITE, BLACK = "w", "b"

# ----------------------------------------------------------------- backends
class LocalClient:
    """Embeds the engine; plays offline. Same verbs as the networked client."""
    def __init__(self, table="table-1"):
        from backgammon_codex import BackgammonCodex, InMemoryTransient, InMemoryPerm
        self.c = BackgammonCodex(InMemoryTransient(), InMemoryPerm())
        self.g = table
        if self.c.get(self.g) is None: self.c.new_game(gid=self.g)
    def get(self):            return self.c.get(self.g)
    def new_game(self):       return self.c.new_game(gid=self.g)
    def roll(self):           return self.c.roll(self.g)
    def move(self, frm, die): return self.c.move(self.g, frm, die)
    def offer_double(self):   return self.c.offer_double(self.g)
    def accept_double(self):  return self.c.accept_double(self.g)
    def decline_double(self): return self.c.decline_double(self.g)
    def presence(self, who):  self.c.touch_presence(self.g, who); return self.c.who_is_here(self.g)

class OriginClient:
    """UnREST origin client. Writes a move into the SHARED board -- held in the table
    host's working memory -- and reads the board back. No game logic here and no game
    server process: the proxy routes the `_game` request to the node holding the table,
    and that node's GameOrigin applies the move to memory in place. The write IS the
    move landing in the shared board; the read IS the board. Same put/get rhythm as the
    tuple space, but the authority lives with the memory."""
    def __init__(self, host, table="table-1", who="desktop", game="backgammon"):
        if "://" not in host:
            host = "http://" + host
        self.base = host.rstrip("/"); self.g = table; self.who = who; self.game = game
    def _op(self, op, **args):
        body = json.dumps({"_game": 1, "game": self.game, "table": self.g,
                           "op": op, "who": self.who, "args": args}).encode()
        req = urllib.request.Request(self.base + "/game", data=body,
              headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    def get(self):            return self._op("get").get("state")
    def new_game(self):       return self._op("new_game").get("state")
    def roll(self):           return self._op("roll").get("state")
    def move(self, frm, die): return self._op("move", **{"from": frm, "die": die}).get("state")
    def offer_double(self):   return self._op("offer_double").get("state")
    def accept_double(self):  return self._op("accept_double").get("state")
    def decline_double(self): return self._op("decline_double").get("state")
    def presence(self, who):  return self._op("presence").get("here", [])


class HttpClient:
    """Thin UnREST client to a codex on the mesh."""
    def __init__(self, base, table="table-1", who="desktop"):
        if "://" not in base: base = "http://" + base
        self.base = base.rstrip("/"); self.g = table; self.who = who
        self.suffix = "/unrest/family-backgammon"
    def _call(self, verb, body=None):
        url = f"{self.base}{self.suffix}/{verb}?g={self.g}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data,
              headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    def get(self):            return self._call("get")
    def new_game(self):       return self._call("new_game", {})
    def roll(self):           return self._call("roll", {})
    def move(self, frm, die): return self._call("move", {"from": frm, "die": die})
    def offer_double(self):   return self._call("offer_double", {})
    def accept_double(self):  return self._call("accept_double", {})
    def decline_double(self): return self._call("decline_double", {})
    def presence(self, who):  return self._call("presence", {"who": who}).get("who_is_here", [])

# The UnREST tuple-space backend is optional at import time: a standalone client
# directory may not have frognet_tuples beside it, and LOCAL/offline play must still
# run. Guard the import so its absence only blocks --space, not the whole app.
try:
    from bg_tuple_store import TupleClient
    _TUPLE_ERR = None
except Exception as _e:                 # pragma: no cover - environment dependent
    TupleClient = None
    _TUPLE_ERR = _e

_SHARED = tuple(c for c in (HttpClient, OriginClient,
                            TupleClient if TupleClient else None) if c)

# ----------------------------------------------------------------- palette
WALNUT="#3a2417"; FELT="#1f4a3f"; PT_L="#e8d8b8"; PT_D="#9c3b2e"
BONE="#f3ead6"; BONE_E="#bfae8a"; EBONY="#23211f"; EBONY_E="#000000"
BRASS="#e9d29a"; BRASS_D="#8a6d22"; GLOW="#ffd66e"; INK="#efe7d6"; MUTED="#b8a98c"

# ----------------------------------------------------------------- the app
class App(tk.Tk):
    W, H, M, BAR, TRAY = 940, 600, 24, 60, 66
    R = 19; PT = 232
    def __init__(self, client, who="desktop"):
        super().__init__()
        self.client = client; self.who = who; self.sel = None
        self.state = None; self.watermark = -1; self.connected = True
        self.title("Backgammon")
        self.configure(bg="#120d08")
        self.fieldX = self.M
        self.fieldW = self.W - 2*self.M - self.TRAY
        self.half = (self.fieldW - self.BAR) / 2
        self.colW = self.half / 6
        self.barX = self.fieldX + self.half

        top = tk.Frame(self, bg="#120d08"); top.pack(fill="x", padx=16, pady=(12,4))
        tk.Label(top, text="Backgammon", fg=INK, bg="#120d08",
                 font=("Georgia", 20, "bold")).pack(side="left")
        self.status = tk.Label(top, text="...", fg=MUTED, bg="#120d08", font=("Helvetica", 11))
        self.status.pack(side="left", padx=14)
        self.presence = tk.Label(top, text="", fg=MUTED, bg="#120d08", font=("Helvetica", 11))
        self.presence.pack(side="right")

        self.canvas = tk.Canvas(self, width=self.W, height=self.H,
                                bg="#120d08", highlightthickness=0)
        self.canvas.pack(padx=16)

        bar = tk.Frame(self, bg="#120d08"); bar.pack(fill="x", padx=16, pady=10)
        self.turnlbl = tk.Label(bar, text="", fg=MUTED, bg="#120d08", font=("Helvetica", 11))
        self.turnlbl.pack(side="left")
        self.btns = tk.Frame(bar, bg="#120d08"); self.btns.pack(side="right")

        self.refresh()
        if isinstance(client, HttpClient):
            from host_float import FloatWatch, resolve_base_ip
            self._fw = FloatWatch(lambda: resolve_base_ip(self.client.base),
                                  self._on_float)
        else:
            self._fw = None
        # Poll whenever play is shared: HTTP table OR the UnREST tuple space. Local
        # hot-seat needs no poll (it acts for both sides in-process). The watermark in
        # refresh() uses the state ts the codex stamps on commit, so a peer's move in
        # the shared tuple is picked up the same way a remote host's would be.
        if isinstance(client, _SHARED):
            self.after(1500, self._poll)

    def _on_float(self):
        # table host floated: drop stale watermark (new host's ts is a fresh baseline),
        # clear any prior disconnect, and re-sync against the new host.
        self.watermark = -1
        self.connected = True
        self.refresh()

    # ---- helpers
    def _btn(self, parent, label, cmd, primary=True):
        return tk.Button(parent, text=label, command=cmd, font=("Helvetica", 11, "bold"),
            fg=("#1c130a" if primary else INK), bg=(BRASS if primary else "#2a2018"),
            activebackground=(GLOW if primary else "#3a2c1c"), bd=0,
            padx=18, pady=8, cursor="hand2")

    def point_geom(self, i):
        if i >= 13:
            top = True; lb, slot = (True, i-13) if i <= 18 else (False, i-19)
        else:
            top = False; lb, slot = (True, 12-i) if i >= 7 else (False, 6-i)
        blockX = self.fieldX + (0 if lb else self.half + self.BAR)
        return blockX + self.colW*slot + self.colW/2, top

    def checker_y(self, k, top):
        base = self.M + 8 + self.R if top else self.H - self.M - 8 - self.R
        return base + k*(self.R*1.7) if top else base - k*(self.R*1.7)

    def draw_checker(self, x, y, color):
        c = self.canvas; r = self.R
        c.create_oval(x-r, y-r+2, x+r, y+r+2, fill="#000000", outline="", stipple="gray25")
        fill = BONE if color == WHITE else EBONY
        edge = BONE_E if color == WHITE else EBONY_E
        c.create_oval(x-r, y-r, x+r, y+r, fill=fill, outline=edge, width=2)
        ring = "#d8c9a4" if color == WHITE else "#3a3a3a"
        c.create_oval(x-r+6, y-r+6, x+r-6, y+r-6, fill="", outline=ring, width=1)

    def draw_die(self, x, y, val, dark):
        c = self.canvas; s = 46
        c.create_rectangle(x, y, x+s, y+s, fill=("#1c1a18" if dark else BONE),
                           outline=("#000" if dark else BONE_E), width=2)
        pc = "#e8e2d4" if dark else "#26211a"
        a, mid, b = 12, 23, 34
        P = {1:[(mid,mid)], 2:[(a,a),(b,b)], 3:[(a,a),(mid,mid),(b,b)],
             4:[(a,a),(b,a),(a,b),(b,b)], 5:[(a,a),(b,a),(mid,mid),(a,b),(b,b)],
             6:[(a,a),(b,a),(a,mid),(b,mid),(a,b),(b,b)]}
        for px, py in P.get(val, []):
            c.create_oval(x+px-4, y+py-4, x+px+4, y+py+4, fill=pc, outline="")

    # ---- render
    def render(self):
        s = self.state; c = self.canvas; c.delete("all")
        if not s: return
        W,H,M,BAR,TRAY,PT = self.W,self.H,self.M,self.BAR,self.TRAY,self.PT
        c.create_rectangle(0,0,W,H, fill=WALNUT, outline="")
        c.create_rectangle(self.fieldX,M,self.fieldX+self.fieldW,H-M, fill=FELT, outline="")
        c.create_rectangle(self.barX,M,self.barX+BAR,H-M, fill=WALNUT, outline="")
        c.create_rectangle(W-M-TRAY,M,W-M,H-M, fill=WALNUT, outline="")

        legal_froms = {m["from"] for m in (s.get("legal") or [])}
        sel_dests = ({m["to"] for m in s["legal"] if m["from"] == self.sel}
                     if self.sel is not None else set())
        for i in range(1, 25):
            x, top = self.point_geom(i); light = (i % 2 == 0)
            tipY = M+PT if top else H-M-PT; baseY = M if top else H-M
            c.create_polygon(x-self.colW/2+4, baseY, x+self.colW/2-4, baseY, x, tipY,
                             fill=(PT_L if light else PT_D), outline="#00000033")
            # invisible hot zone for legal sources
            if i in legal_froms:
                z = c.create_rectangle(x-self.colW/2, (M if top else H-M-PT),
                                       x+self.colW/2, (M+PT if top else H-M),
                                       fill="", outline=GLOW, width=2)
                c.tag_bind(z, "<Button-1>", lambda e, p=i: self.pick(p))
            if i in sel_dests:
                d = c.create_oval(x-11, (M+PT-37 if top else H-M-PT+15),
                                  x+11, (M+PT-15 if top else H-M-PT+37),
                                  fill=GLOW, outline="")
                c.tag_bind(d, "<Button-1>", lambda e, p=i: self.choose_dest(p))
        # checkers
        for i in range(1, 25):
            v = s["points"][i]
            if not v: continue
            x, top = self.point_geom(i); color = WHITE if v > 0 else BLACK; n = abs(v)
            for k in range(min(n, 5)):
                self.draw_checker(x, self.checker_y(k, top), color)
            if n > 5:
                ty = self.checker_y(4, top)
                c.create_text(x, ty, text=f"x{n}",
                              fill=("#3a2c10" if color==WHITE else BONE),
                              font=("Helvetica", 12, "bold"))
        # bar
        for k in range(s["bar"][WHITE]): self.draw_checker(self.barX+BAR/2, H/2-30-k*8, WHITE)
        for k in range(s["bar"][BLACK]): self.draw_checker(self.barX+BAR/2, H/2+30+k*8, BLACK)
        if self.sel == 0:
            c.create_oval(self.barX+BAR/2-32, H/2-32, self.barX+BAR/2+32, H/2+32,
                          outline=GLOW, width=3)
        # off tray
        trayX = W-M-TRAY/2
        for k in range(s["off"][WHITE]):
            c.create_rectangle(trayX-22, H-M-12-k*9, trayX+22, H-M-5-k*9, fill=BONE, outline="")
        for k in range(s["off"][BLACK]):
            c.create_rectangle(trayX-22, M+6+k*9, trayX+22, M+13+k*9, fill=EBONY, outline="")
        # dice
        if s.get("dice"):
            dx = self.barX-150 if s["turn"]==WHITE else self.barX+BAR+86
            for idx, d in enumerate(s["dice"]):
                self.draw_die(dx+idx*58, H/2-23, d, s["turn"]==BLACK)
        # cube
        c.create_rectangle(trayX-22, H/2-22, trayX+22, H/2+22, fill=BRASS, outline=BRASS_D, width=2)
        cv = s["cube"]["value"]; c.create_text(trayX, H/2, text=str(64 if cv==1 else cv),
                                               fill="#4a3a14", font=("Helvetica", 18, "bold"))
        # bear-off target
        if self.sel is not None:
            if any(m["from"]==self.sel and m["bear"] for m in s["legal"]):
                ty = H-M-40 if s["turn"]==WHITE else M+40
                t = c.create_rectangle(trayX-30, ty-16, trayX+30, ty+16, fill=GLOW, outline="")
                c.create_text(trayX, ty, text="bear off", fill="#3a2c10",
                              font=("Helvetica", 10, "bold"))
                c.tag_bind(t, "<Button-1>", lambda e: self.choose_bear())
        self.render_controls()

    def render_controls(self):
        s = self.state
        self.turnlbl.config(text=self._turn_text())
        for w in self.btns.winfo_children(): w.destroy()
        ph = s["phase"]
        def add(label, cmd, primary=True):
            self._btn(self.btns, label, cmd, primary).pack(side="left", padx=5)
        if ph == "roll":
            add("Roll", lambda: self.act("roll")); add("Double", lambda: self.act("offer_double"), False)
        elif ph == "double-offered":
            add("Accept double", lambda: self.act("accept_double"))
            add("Decline", lambda: self.act("decline_double"), False)
        elif ph == "move":
            add("Clear selection", lambda: (setattr(self, "sel", None), self.render()), False)
        add("New game", lambda: self.act("new_game"), False)

    def _turn_text(self):
        s = self.state
        if s["phase"] == "gameover":
            return f"{s['players'][s['winner']]} wins"
        who = s["players"][s["turn"]]
        verb = "roll" if s["phase"]=="roll" else "play"
        extra = f"   dice: {' '.join(map(str, s['dice_remaining']))}" if s["phase"]=="move" else ""
        return f"{who} to {verb}{extra}"

    # ---- actions
    def pick(self, frm): self.sel = None if self.sel == frm else frm; self.render()
    def choose_dest(self, to):
        mv = next((m for m in self.state["legal"] if m["from"]==self.sel and m["to"]==to), None)
        if mv: self.sel = None; self._do(lambda: self.client.move(mv["from"], mv["die"]))
    def choose_bear(self):
        mv = next((m for m in self.state["legal"] if m["from"]==self.sel and m["bear"]), None)
        if mv: self.sel = None; self._do(lambda: self.client.move(mv["from"], mv["die"]))
    def act(self, verb):
        self.sel = None
        self._do(lambda: getattr(self.client, verb)())

    def _do(self, fn):
        try:
            fn(); self.refresh()
        except Exception:
            self.connected = False; self.status.config(text="can't reach the table -- contact admin")

    def refresh(self):
        try:
            s = self.client.get()
            ts = int(s.get("ts", 0))
            if self.watermark >= 0 and ts < self.watermark:
                self.connected = False
                self.status.config(text="disconnected (split detected)"); return
            self.watermark = ts; self.connected = True; self.state = s
            self.status.config(text="game over" if s["phase"]=="gameover" else "live")
            try:
                here = self.client.presence(self.who)
                self.presence.config(text=("watching: " + ", ".join(here)) if here else "")
            except Exception: pass
            self.render()
        except Exception:
            self.connected = False; self.status.config(text="can't reach the table -- contact admin")

    def _poll(self):
        if self._fw is not None:
            self._fw.tick()                      # float? re-sync against the new host first
        if self.connected: self.refresh()
        self.after(1500, self._poll)

# ----------------------------------------------------------------- launch
def main():
    ap = argparse.ArgumentParser(description="Family Backgammon (standalone)")
    ap.add_argument("--connect", metavar="HOST[:PORT]",
                    help="join an HTTP table on the mesh (legacy request/response)")
    ap.add_argument("--space", metavar="DBHOST", nargs="?", const="databasehost.frognet",
                    help="join a table over the UnREST tuple space (default holder "
                         "databasehost.frognet); the board is a shared tuple, no server")
    ap.add_argument("--origin", metavar="HOST",
                    help="join a table served by the in-proxy GameOrigin (the move is "
                         "applied in the table host's working memory; no game server)")
    ap.add_argument("--table", default="table-1", help="table name (default table-1)")
    ap.add_argument("--who", default="desktop", help="your identity for presence")
    a = ap.parse_args()
    if a.origin:
        client = OriginClient(a.origin, a.table, a.who)
    elif a.space:
        if TupleClient is None:
            ap.error(f"--space needs frognet_tuples importable beside the bundle: {_TUPLE_ERR}")
        client = TupleClient(a.space, a.table, a.who)
    elif a.connect:
        client = HttpClient(a.connect, a.table, a.who)
    else:
        client = LocalClient(a.table)
    App(client, a.who).mainloop()

if __name__ == "__main__":
    main()
