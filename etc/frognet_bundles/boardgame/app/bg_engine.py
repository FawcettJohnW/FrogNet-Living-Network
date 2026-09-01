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
"""bg_engine.py -- resident UnRESTful backgammon engine. Comes up on the gameserver,
governs the shared tuple space, gets ready for customers. It does NOT serve requests;
it reads the space, runs the governor (authority: dice, turn gate, seating), writes the
Game object back -- current state only. Players join by writing their own User objects.

  run:  bg_engine.py --gid <table> [--dbhost <ip> | --http <base>] [--hz 5]
"""
import argparse, time, random
import bg_governor as G
from bg_space import TupleSpace, HttpSpace

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gid", required=True)
    ap.add_argument("--name", default="gameserver", help="service name = SensorType region")
    ap.add_argument("--dbhost"); ap.add_argument("--http")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    space = HttpSpace(a.gid, a.http, typ=a.name) if a.http else TupleSpace(a.gid, a.dbhost, typ=a.name)
    rng = random.SystemRandom()
    print(f"[engine] backgammon governor up: gid={a.gid} table region <{a.name}>/backgammon.{a.gid}/*")
    period = 1.0 / max(0.5, a.hz)
    idle_period = max(period, 3.0)   # empty table: heartbeat, not a 5Hz poll storm
    while True:
        try:
            # [ENGINE_IDLE_WHEN_EMPTY_V1] Don't run the governor (3 tuple reads per
            # step) at full rate for a table nobody is at. If no User objects are
            # seated, this is an idle table -- drop to a slow heartbeat that just
            # notices someone joining. Full cadence resumes the moment a player
            # writes their User object. Kills the boardgame poll storm at source.
            seated = G._seat_users(space, a.gid)
            if not seated:
                if a.once: break
                time.sleep(idle_period)
                continue
            g = G.governor_step(space, a.gid, rng)
        except Exception as e:
            print("[engine] step error:", e)
        if a.once: break
        time.sleep(period)

if __name__ == "__main__":
    main()
