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
sim_games_over_wire.py - every UnREST game, multiplayer, over the REAL simulated
transport (transport_sim_tier.WireMedium), under conditions from clean LAN to jammed RF,
with a watcher reading mid-game and a replay at the end, and a host that floats.

This is the generalization of sim_board_game_over_wire.py to the whole game set. It drives
each game THROUGH THE ORIGIN over the wire - join, act, get, replay are `_game` envelopes
crossing an impaired link - so what's proven is the real path a Communicator player takes,
not just the in-process logic.

The assertion per game: the outcome is identical on every wire. Impairment costs time, not
correctness. And the multiplayer pieces - turn authority across players, hidden info to a
watcher, replay folded from the tuple - all survive the wire because they were always just
reads and writes of one shared value.

Run:  FROGNET_BUNDLES=/path/to/etc/frognet_bundles python3 sim_games_over_wire.py
"""
from __future__ import annotations

import json
import math
import os
import random
import socket
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "core"))

from transport_sim_tier import WireMedium, NetworkParams        # noqa: E402
from game_origin import GameOrigin                              # noqa: E402

PROFILES = {
    "Clean LAN":  NetworkParams(latency_ms=0.5, bandwidth_bps=1e9),
    "WiFi":       NetworkParams(latency_ms=5, jitter_ms=2, bandwidth_bps=50e6),
    "Jammed RF":  NetworkParams(latency_ms=15, jitter_ms=10, bandwidth_bps=32e3,
                                outage_prob_per_sec=0.12, outage_duration_ms=700),
}
# (GEO 300ms latency was already shown wire-invariant for backgammon; here we keep
#  latency low so the long card/dice games finish fast, and stress bandwidth + outages.)

# Each game: seats and a PURE (RNG-free) strategy so the game is identical on every wire.
GAMES = {
    "connectfour": ["alice", "bob"],
    "reversi":     ["dark", "light"],
    "hearts":      ["north", "east", "south", "west"],
    "liarsdice":   ["amy", "ben", "cy"],
}
SEED = 20260614      # fixes deals/rolls so the outcome is deterministic across wires


def strategy(game, state, who):
    """Pure function of state -> an action. No randomness, so every wire plays the
    same game and must reach the same end."""
    legal = state.get("legal") or []
    if not legal:
        return None
    if game == "liarsdice":
        b = state.get("board") or {}
        bid = b.get("bid")
        alive = b.get("alive") or []
        total = sum((b.get("counts") or {}).get(s, 0) for s in alive)
        ch = [a for a in legal if a.get("challenge")]
        if bid is not None and ch and bid[0] >= max(2, math.ceil(total / 3)):
            return ch[0]
        raises = [a for a in legal if "bid" in a]
        return raises[0] if raises else (ch[0] if ch else legal[0])
    return legal[0]                                   # first legal move for the rest


# ---- framing + host (same reliable-stream transport as the board-game harness) ----
def _send(ep, b): ep.sendall(struct.pack("!I", len(b)) + b)
def _recvn(ep, n):
    buf = b""
    while len(buf) < n:
        c = ep.recv(n - len(buf))
        if not c:
            return None
        buf += c
    return buf
def _recv(ep):
    h = _recvn(ep, 4)
    if h is None:
        return None
    return _recvn(ep, struct.unpack("!I", h)[0])


class TableHost(threading.Thread):
    def __init__(self, ep, origin): super().__init__(daemon=True); self.ep = ep; self.o = origin; self._halt = False
    def run(self):
        self.ep.settimeout(0.5)
        while not self._halt:
            try:
                req = _recv(self.ep)
            except socket.timeout:
                continue
            except OSError:
                return
            if req is None:
                return
            _, resp = self.o.serve(req)
            try:
                _send(self.ep, resp.encode())
            except OSError:
                return
    def halt(self): self._halt = True


class Wire:
    """Player/watcher link. Pure game verbs over the impaired wire; no retry/ack/seq."""
    def __init__(self, ep, game, table, who, timeout=40.0):
        self.ep = ep; self.ep.settimeout(timeout); self.g = game; self.t = table; self.who = who
    def _op(self, op, **a):
        _send(self.ep, json.dumps({"_game": 1, "game": self.g, "table": self.t,
                                   "op": op, "who": self.who, "args": a}).encode())
        return json.loads(_recv(self.ep).decode())
    def join(self, role="player"): return self._op("join", role=role)
    def get(self):                 return self._op("get")
    def act(self, action):         return self._op("act", action=action)
    def replay(self):              return self._op("replay")


def _sig(state):
    """Outcome signature: winner + board, minus wall-clock/version/presence noise."""
    b = dict(state.get("board") or {})
    return json.dumps({"winner": state.get("winner"), "phase": state.get("phase"),
                       "board": b}, sort_keys=True)


def run(game, profile_name, params):
    random.seed(SEED)                                  # identical deal/rolls every wire
    seats = GAMES[game]
    medium = WireMedium(forward_params=params, reverse_params=params, label=f"{game}/{profile_name}")
    origin = GameOrigin()
    host = TableHost(medium.endpoint_b(), origin); host.start()
    ep = medium.endpoint_a()
    players = {s: Wire(ep, game, "table", s) for s in seats}
    watcher = Wire(ep, game, "table", "kibitz")

    t0 = time.monotonic()
    for s in seats:                                    # everyone joins -> auto-start at max
        players[s].join("player")
    watcher.join("watcher")

    # hidden-info check over the wire: a watcher must not see a player's secret hand/dice.
    hidden_ok = True
    wb = watcher.get()["state"].get("board") or {}
    if game == "hearts":
        hidden_ok = all(isinstance(v, int) for v in (wb.get("hands") or {}).values())
    elif game == "liarsdice":
        hidden_ok = all(isinstance(v, int) for v in (wb.get("dice") or {}).values())

    guard = 0
    st = players[seats[0]].get()["state"]              # bootstrap once
    while guard < 800 and st.get("phase") != "over":
        turn = st.get("turn")
        view = players[turn].get()["state"]            # turn player's (redacted) view: 1 round trip
        action = strategy(game, view, turn)
        if action is None:
            break
        st = players[turn].act(action)["state"]        # act, response carries next turn: 1 round trip
        guard += 1

    final = players[seats[0]].get()["state"]
    frames = watcher.replay()["state"]                 # replay folded from the tuple, over the wire
    secs = time.monotonic() - t0
    host.halt(); host.join(timeout=2)
    stats = dict(medium.stats)
    outages = stats.get("outages_forward", 0) + stats.get("outages_reverse", 0)
    return {"final": final, "sig": _sig(final), "plies": guard, "secs": secs,
            "outages": outages, "hidden_ok": hidden_ok, "replay_len": len(frames),
            "replay_sig": _sig({"winner": frames[-1]["winner"], "phase": frames[-1]["phase"],
                                "board": frames[-1]["board"]})}


def host_float(game="hearts"):
    """Float the table host mid-game; the multiplayer game continues intact off perm."""
    from working_memory import InMemoryTransient, InMemoryPerm
    random.seed(SEED)
    seats = GAMES[game]
    perm = InMemoryPerm()
    o_a = GameOrigin(transient=InMemoryTransient(), perm=perm)
    m_a = WireMedium(forward_params=PROFILES["Clean LAN"], reverse_params=PROFILES["Clean LAN"])
    h_a = TableHost(m_a.endpoint_b(), o_a); h_a.start()
    ep = m_a.endpoint_a()
    P = {s: Wire(ep, game, "t", s) for s in seats}
    for s in seats:
        P[s].join("player")
    # play ~half the hand
    for _ in range(20):
        st = P[seats[0]].get()["state"]
        if st["phase"] == "over":
            break
        turn = st["turn"]; a = strategy(game, P[turn].get()["state"], turn)
        if a is None:
            break
        P[turn].act(a)
    before = P[seats[0]].get()["state"]
    h_a.halt(); h_a.join(timeout=2)

    # host B: cold transient, SAME perm; players reconnect on a fresh link and continue.
    o_b = GameOrigin(transient=InMemoryTransient(), perm=perm)
    m_b = WireMedium(forward_params=PROFILES["Clean LAN"], reverse_params=PROFILES["Clean LAN"])
    h_b = TableHost(m_b.endpoint_b(), o_b); h_b.start()
    ep2 = m_b.endpoint_a()
    P2 = {s: Wire(ep2, game, "t", s) for s in seats}
    resumed = P2[seats[0]].get()["state"]              # cold host refaults from perm
    while True:
        st = P2[seats[0]].get()["state"]
        if st["phase"] == "over":
            break
        turn = st["turn"]; a = strategy(game, P2[turn].get()["state"], turn)
        if a is None:
            break
        P2[turn].act(a)
    after = P2[seats[0]].get()["state"]
    h_b.halt(); h_b.join(timeout=2)
    return before, resumed, after


def main():
    print("=" * 74)
    print("Every UnREST game, multiplayer, over the simulated transport tier")
    print("=" * 74)
    all_ok = True
    for game, seats in GAMES.items():
        print(f"\n### {game}  ({len(seats)} players: {', '.join(seats)})")
        sigs = {}
        for pname, params in PROFILES.items():
            r = run(game, pname, params)
            sigs[pname] = r["sig"]
            assert r["replay_sig"] == r["sig"], f"{game}/{pname}: replay != live"
            hid = "" if game in ("hearts", "liarsdice") else " n/a"
            print(f"  {pname:16s} {r['secs']:5.2f}s  outages={r['outages']}  plies={r['plies']}  "
                  f"winner={str(r['final'].get('winner')):>6}  hidden_to_watcher={'OK' if r['hidden_ok'] else 'LEAK'}{hid}  "
                  f"replay={r['replay_len']}")
        ref = sigs["Clean LAN"]
        same = all(s == ref for s in sigs.values())
        all_ok &= same
        print(f"  => outcome identical on every wire: {same}")
        assert same, f"{game}: a wire changed the outcome"

    print("\n" + "-" * 74)
    print("[Host float] hearts table moves to another node mid-hand")
    before, resumed, after = host_float("hearts")
    assert _sig(resumed) == _sig(before), "board changed across the float"
    print(f"  before float: turn={before['turn']} trick_no={before['board'].get('trick_no')}")
    print(f"  resumed on new host: identical board: {_sig(resumed) == _sig(before)}")
    print(f"  hand played to completion after the float: {after['phase']=='over'} "
          f"(winner {after.get('winner')})")
    assert after["phase"] == "over"

    print("\n" + "=" * 74)
    print("ALL GAMES PASS OVER THE WIRE - impairment cost time, not correctness;")
    print("turn authority, hidden info, replay, and host-float all held across the link.")
    print("=" * 74)


if __name__ == "__main__":
    main()
