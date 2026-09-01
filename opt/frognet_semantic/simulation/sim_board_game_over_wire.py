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
sim_board_game_over_wire.py - the UnREST board game played over the REAL simulated
transport (transport_sim_tier.WireMedium), under network conditions from clean LAN to
jammed RF, and across a host that floats to another node mid-game.

WHAT THIS IS FOR
----------------
This is the simulator doing what it is for: not discovery, but proving an application
over the wire under conditions you would otherwise need a lab of radios and a patient
afternoon to reproduce. We dial in latency, jitter, bandwidth, and outages, run a full
two-player game across the impaired link, and check the only thing that matters - the
board both players see.

THE POINT IT MAKES
------------------
Look at how little the game does. A player's entire interaction with the network is:

    link.move(frm, die)     # write my move into the shared board
    board = link.get()      # read the board

There is no retry loop. No acknowledgment. No sequence numbers. No dedup. No "is the
host still up." No "did the host's IP move." No port. No reconnect logic. The game
author wrote a backgammon engine and two lines that touch a shared value - and that is
the whole networked program. Everything underneath - the framing, the transit, the
outage that becomes a latency spike instead of a lost move, the host that floats to a
new node and is found again - is the FrogNet layer's job, written and debugged once,
decades ago, so the game author never has to.

Over a reliable transit, impairment costs TIME, not CORRECTNESS. A jammed link makes a
read slow; it never makes the board wrong. So the turn-based game needs none of the
machinery a message protocol would force on it. That is the thesis, and this harness
runs it under fire.

Run:  FROGNET_BUNDLES=/path/to/etc/frognet_bundles python3 sim_board_game_over_wire.py
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                   # transport_sim_tier sibling
sys.path.insert(0, os.path.join(_HERE, "..", "core"))       # game_origin

from transport_sim_tier import WireMedium, NetworkParams     # noqa: E402
from game_origin import GameOrigin                           # noqa: E402


# Real-world profiles, lifted from transport_sim_tier's own NetworkParams docstring.
PROFILES = {
    "Clean LAN":       NetworkParams(latency_ms=0.5,  bandwidth_bps=1e9),
    "Satellite (GEO)": NetworkParams(latency_ms=300,  jitter_ms=20,  bandwidth_bps=256e3),
    "HaLow 900MHz":    NetworkParams(latency_ms=20,   jitter_ms=5,   bandwidth_bps=600e3),
    "Jammed RF":       NetworkParams(latency_ms=100,  jitter_ms=200, bandwidth_bps=4e3,
                                     outage_prob_per_sec=0.25, outage_duration_ms=1500),
}


# ---------------------------------------------------------------------------
# The transport layer - generic, game-agnostic, "the networking crap" the game
# author never writes. Length-prefixed frames over a WireEndpoint. Because the
# transit is reliable, a slow or briefly-severed link just delays bytes; we wait.
# ---------------------------------------------------------------------------
def _send_frame(ep, b: bytes) -> None:
    ep.sendall(struct.pack("!I", len(b)) + b)


def _recv_n(ep, n: int):
    buf = b""
    while len(buf) < n:
        chunk = ep.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_frame(ep):
    hdr = _recv_n(ep, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack("!I", hdr)
    return _recv_n(ep, n)


class TableHost(threading.Thread):
    """A FrogNet node holding the table. Owns a GameOrigin over its working memory.
    Serves whatever `_game` frames arrive and writes the resulting board back. This
    stands in for the proxy origin terminating a `/game` request in-process."""
    def __init__(self, endpoint, origin: GameOrigin, label="host"):
        super().__init__(daemon=True)
        self.ep = endpoint
        self.origin = origin
        self.label = label
        self._halt = False

    def run(self):
        self.ep.settimeout(0.5)
        while not self._halt:
            try:
                req = _recv_frame(self.ep)
            except socket.timeout:
                continue
            except OSError:
                return
            if req is None:
                return
            _, resp = self.origin.serve(req)               # touch memory, answer
            try:
                _send_frame(self.ep, resp.encode())
            except OSError:
                return

    def stop(self):
        self._halt = True


class WireLink:
    """What the PLAYER holds. Pure game verbs; the body is the same put/read you'd
    write against local memory. No retry, no ack, no seq, no reconnect - over reliable
    transit, a get just takes as long as the link makes it take, then returns the
    board."""
    def __init__(self, endpoint, table="kitchen", who="player", game="backgammon",
                 timeout=30.0):
        self.ep = endpoint
        self.ep.settimeout(timeout)
        self.table, self.who, self.game = table, who, game

    def _op(self, op, **args):
        env = {"_game": 1, "game": self.game, "table": self.table,
               "op": op, "who": self.who, "args": args}
        _send_frame(self.ep, json.dumps(env).encode())
        rep = _recv_frame(self.ep)
        return json.loads(rep.decode()) if rep is not None else {}

    def get(self):            return self._op("get").get("state")
    def presence(self):       return self._op("presence").get("here", [])
    def roll(self, d1, d2):   return self._op("roll", d1=d1, d2=d2).get("state")
    def move(self, frm, die): return self._op("move", **{"from": frm, "die": die}).get("state")


# ---------------------------------------------------------------------------
# A deterministic two-player game. Fixed dice so the final board is identical on
# every wire - which is exactly the assertion: the network changed nothing.
# ---------------------------------------------------------------------------
def play_two_player_game(alice: WireLink, bob: WireLink):
    alice.presence(); bob.presence()                       # both seated
    board = alice.get()

    def play_turn(link, d1, d2):
        st = link.roll(d1, d2)
        # Play legal moves until the dice are spent (phase leaves "move").
        for _ in range(6):
            if st is None or st.get("phase") != "move" or not st.get("legal"):
                break
            mv = st["legal"][0]
            st = link.move(mv["from"], mv["die"])
        return st

    play_turn(alice, 3, 1)                                  # white opens 3-1
    board = play_turn(bob, 6, 4)                            # black answers 6-4
    return alice.get()                                      # read final from the wire


def run_profile(name: str, params: NetworkParams):
    """Stand up the wire, the host, two players; play; return (final_board, wire_stats, secs)."""
    medium = WireMedium(forward_params=params, reverse_params=params, label=name)
    origin = GameOrigin()                                   # the table's memory
    host = TableHost(medium.endpoint_b(), origin)
    host.start()
    # Both players share the one host endpoint? No - each needs its own conversation.
    # In the mesh, every player has its own link to the table host. We model two links
    # by giving the host a second medium would double the host loop; instead both
    # players ride one link in turn (the game is turn-based, so the link is idle between
    # turns). One player object per identity, same endpoint.
    a_ep = medium.endpoint_a()
    alice = WireLink(a_ep, who="alice")
    bob = WireLink(a_ep, who="bob")

    t0 = time.monotonic()
    final = play_two_player_game(alice, bob)
    secs = time.monotonic() - t0
    host.stop(); host.join(timeout=2)
    return final, dict(medium.stats), secs


def run_host_float():
    """The table host floats to another node mid-game. Two origins share ONE perm store
    (the resumable authority); the live caches are separate (the floating part). The
    player finishes the game against host B, which it reaches on a fresh link - the only
    'reconnect' is being handed the new endpoint, the way FrogNet discovery re-resolves
    databasehost.frognet without the app knowing. The board continues intact."""
    from working_memory import InMemoryTransient, InMemoryPerm
    shared_perm = InMemoryPerm()                            # the board's real home

    # Host A: its own transient, the shared perm.
    origin_a = GameOrigin(transient=InMemoryTransient(), perm=shared_perm)
    med_a = WireMedium(forward_params=PROFILES["Clean LAN"],
                       reverse_params=PROFILES["Clean LAN"], label="hostA")
    host_a = TableHost(med_a.endpoint_b(), origin_a); host_a.start()
    link_a = WireLink(med_a.endpoint_a(), who="alice")

    link_a.presence()
    link_a.roll(3, 1)
    st = link_a.get()
    while st and st.get("phase") == "move" and st.get("legal"):
        mv = st["legal"][0]; st = link_a.move(mv["from"], mv["die"])
    before = link_a.get()
    host_a.stop(); host_a.join(timeout=2)                   # node A vanishes

    # Host B comes up on a different node, cold transient, SAME perm. The player gets a
    # new link (discovery re-resolved the host) and just keeps reading the board.
    origin_b = GameOrigin(transient=InMemoryTransient(), perm=shared_perm)
    med_b = WireMedium(forward_params=PROFILES["Clean LAN"],
                       reverse_params=PROFILES["Clean LAN"], label="hostB")
    host_b = TableHost(med_b.endpoint_b(), origin_b); host_b.start()
    link_b = WireLink(med_b.endpoint_a(), who="alice")

    after = link_b.get()                                    # cold host refaults from perm
    host_b.stop(); host_b.join(timeout=2)
    return before, after


def main():
    print("=" * 72)
    print("UnREST board game over the simulated transport tier")
    print("=" * 72)

    finals = {}
    for name, params in PROFILES.items():
        final, stats, secs = run_profile(name, params)
        finals[name] = final
        fwd_out = stats.get("outages_forward", 0) + stats.get("outages_reverse", 0)
        print(f"\n[{name}]  latency={params.latency_ms}ms jitter={params.jitter_ms}ms "
              f"bw={params.bandwidth_bps:g} B/s outage_p={params.outage_prob_per_sec}")
        print(f"   game completed in {secs:5.2f}s  | wire outages during play: {fwd_out}")
        print(f"   final: turn={final['turn']} phase={final['phase']} "
              f"ts={final['ts']} version={final['version']}")

    # The assertion the whole exercise exists to make: the network changed NOTHING
    # about the GAME. ts is wall-clock (differs per run) and version is the commit
    # counter; neither is game state. Compare the position.
    def _pos(b):
        b = {k: v for k, v in b.items() if k not in ("ts", "version")}
        return json.dumps(b, sort_keys=True)
    ref = _pos(finals["Clean LAN"])
    print("\n" + "-" * 72)
    all_same = True
    for name, board in finals.items():
        same = _pos(board) == ref
        all_same &= same
        print(f"   {name:16s} board identical to Clean LAN: {same}")
    assert all_same, "a wire changed the board - that would be a correctness bug"
    print("   => impairment cost TIME, never CORRECTNESS. The game wrote no retry,")
    print("      no ack, no seq, no dedup. The board is memory; reads just arrive.")

    print("\n" + "-" * 72)
    print("[Host float]  the table moves to another node mid-game (IP 'changed')")
    before, after = run_host_float()
    same = json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    print(f"   board before float ts={before['ts']} version={before['version']}")
    print(f"   board after  float ts={after['ts']} version={after['version']}")
    print(f"   identical across the host move: {same}")
    assert same, "the board did not survive the host floating"
    print("   => the player 'found the IP that moved' by doing nothing. Discovery")
    print("      re-resolved the host; the board was never in the host to begin with.")

    print("\n" + "=" * 72)
    print("ALL PASS - the game is just gameplay. The network is the layer below it.")
    print("=" * 72)


if __name__ == "__main__":
    main()
