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
"""boardgame_panel.py -- Communicator UI module for the board-game bundle (pluggable).

DATA-DRIVEN + PLUGGABLE: the Communicator discovers this module via the bundle manifest's
`ui` block and mounts it under the declared hub/tab path (Games > Board Games > <game>).
The panel is a PARTICIPANT in shared memory: it resolves <service>.frognet, reads the GAME
object to render, and writes the player's USER/MOVE object to act -- NOTHING is installed on
the player's device for the thin path; control comes through the Communicator.

Contract the Communicator calls (kept minimal + framework-agnostic so the host UI can be Tk
or other): build a panel given (mount, service, gid, me) and it manages its own read/write
loop against the tuple space. If the host prefers process isolation, the same module exposes
spawn() to run the bundled bg_play.py as a child -- but control/registration is the UI's.
"""
import os, sys
BUNDLE_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, BUNDLE_APP)

MANIFEST_HINT = {"hub": "Games", "group": "Board Games"}  # tab placement (also in bundle.json)

def list_games(service_beacons):
    """Data-driven: the games shown under 'Board Games' come from the bundle's declared
    games list crossed with what's announced on the net -- the UI renders from data, the
    bundle controls the entries."""
    return [b for b in service_beacons if b.get("group") == "Board Games"]

def make_panel(mount, *, service, gid, me, tk=None):
    """Mount an in-Communicator board panel. Resolves the service, renders the GAME object,
    writes USER/MOVE objects. Implementation reuses the bundled engine's render/space code.
    (Host UI passes its Tk parent as `mount`; a non-Tk host can drive read()/act() directly.)"""
    from bg_space import TupleSpace
    sp = TupleSpace(gid, typ=service)
    mod = f"backgammon.{gid}"
    class Panel:
        def read_state(self): return sp.get(sp.typ, mod, "state")
        def claim(self, seat):
            u = sp.get(sp.typ, mod, f"user.{me}") or {"who": me, "intent_seq": 0}
            u["seat_claim"] = seat; sp.put(sp.typ, mod, f"user.{me}", u)
        def act(self, **intent):
            u = sp.get(sp.typ, mod, f"user.{me}") or {"who": me, "intent_seq": 0}
            u["intent"] = intent; u["intent_seq"] = int(u.get("intent_seq", 0)) + 1
            sp.put(sp.typ, mod, f"user.{me}", u)
    return Panel()

def spawn(service, gid, me, seat="w"):
    """Optional process-isolated UX: run the bundled CLI client as a child. Control still
    originates in the Communicator (it chose to spawn)."""
    import subprocess
    return subprocess.Popen([sys.executable, os.path.join(BUNDLE_APP, "bg_play.py"),
                             "--name", service, "--gid", gid, "--who", me, "--seat", seat])
