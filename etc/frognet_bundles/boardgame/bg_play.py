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
"""bg_play.py -- a backgammon participant. You write YOUR user object (claim a seat, then
intents) into the shared space and read the Game object. No call/response: you change
memory; the resident engine governs it.

  bg_play.py --gid <table> --who <name> [--seat w|b] [--dbhost <ip> | --http <base>]
Interactive: shows the board + your legal moves; you pick. 'r'=roll, number=play that
legal move, 'd'=double, 'a'=accept, 'q'=quit.
"""
import argparse, time, sys
from bg_space import TupleSpace, HttpSpace

W, B = "w", "b"
def render(g, me_seat):
    pts = g["points"]
    top = " ".join(f"{pts[p]:+d}" for p in range(13,25))
    bot = " ".join(f"{pts[p]:+d}" for p in range(12,0,-1))
    print(f"\n  13..24: {top}")
    print(f"  12.. 1: {bot}")
    print(f"  bar w{g['bar']['w']} b{g['bar']['b']}  off w{g['off']['w']} b{g['off']['b']}"
          f"  cube {g['cube']['value']}")
    print(f"  turn={g['turn']} phase={g['phase']} dice={g['dice']} rem={g['dice_remaining']}"
          + (f"  winner={g['winner']}" if g['winner'] else ""))
    print(f"  you are seat '{me_seat}'")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gid", required=True)
    ap.add_argument("--name", default="gameserver", help="service name = SensorType region"); ap.add_argument("--who", required=True)
    ap.add_argument("--seat", choices=[W,B], default=None)
    ap.add_argument("--dbhost"); ap.add_argument("--http")
    a = ap.parse_args()
    space = HttpSpace(a.gid, a.http, typ=a.name) if a.http else TupleSpace(a.gid, a.dbhost, typ=a.name)
    me = a.who; mod = f"backgammon.{a.gid}"

    def read_user():
        return space.get(space.typ, mod, f"user.{me}") or {"who": me, "joined_ts": str(int(time.time()*1000)), "intent_seq": 0}
    def write_user(u): space.put(space.typ, mod, f"user.{me}", u)
    def set_intent(**kw):
        u = read_user(); u["intent"] = kw; u["intent_seq"] = int(u.get("intent_seq",0))+1; write_user(u)

    # claim a seat by writing my user object (engine grants + records in Game.seats)
    u = read_user(); u["seat_claim"] = a.seat or "any"; u["present"] = True; write_user(u)
    print(f"[{me}] joined table {a.gid}; waiting for the engine to seat me...")

    last = None
    while True:
        g = space.get(space.typ, mod, "state")
        if g is None: time.sleep(0.4); continue
        my_seat = g.get("seats", {}).get(me)
        sig = (g["version"], g["turn"], g["phase"], tuple(g["dice_remaining"]))
        if sig != last:
            render(g, my_seat); last = sig
        if g["phase"] == "gameover":
            print(f"\n*** game over -- winner {g['winner']} ***"); return
        my_turn = my_seat is not None and g["turn"] == my_seat
        if not my_turn:
            time.sleep(0.4); continue
        # my move: prompt
        if g["phase"] == "roll":
            cmd = input("[roll] r=roll d=double q=quit > ").strip().lower()
            if cmd == "r": set_intent(kind="roll")
            elif cmd == "d": set_intent(kind="double")
            elif cmd == "q": return
        elif g["phase"] == "move":
            legal = g["legal"]
            for i, m in enumerate(legal):
                tag = "bear-off" if m["bear"] else f"->{m['to']}" + ("*" if m["hit"] else "")
                print(f"   [{i}] from {m['from']} die {m['die']} {tag}")
            if not legal:
                print("   (no legal moves -- engine will pass)"); time.sleep(0.6); continue
            cmd = input("pick #, q=quit > ").strip().lower()
            if cmd == "q": return
            if cmd.isdigit() and int(cmd) < len(legal):
                m = legal[int(cmd)]; set_intent(kind="move", frm=m["from"], die=m["die"])
        elif g["phase"] == "double-offered":
            cmd = input("opponent doubled. a=accept q=quit > ").strip().lower()
            if cmd == "a": set_intent(kind="accept")
        time.sleep(0.2)

if __name__ == "__main__":
    main()
