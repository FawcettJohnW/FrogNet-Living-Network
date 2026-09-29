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
test_communicator.py - oracles for the Communicator spine. Pure stdlib; run:
    python3 test_communicator.py

Every claim the Communicator makes is asserted here against the running code.
"""
from __future__ import annotations

import argparse
import sys

from working_memory import InMemoryTransient, InMemoryPerm
from bringup import (default_bringup, State, BringUp, FakeBle, FakeSetupHelper,
                     FakeIdentity, FakePondAdmin, FakeLillypad)
from communicator import Communicator, converge_presence, converge_room
from media_codex import MediaReplica

PASS, FAIL = "PASS", "FAIL"
results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(f"  [{PASS if cond else FAIL}] {name}")


def t_presence_convergence():
    print("T1 presence: FULL -> DIFF -> SAME and reconstruction")
    a = Communicator("nodeA", "Alice"); a.start()
    b = Communicator("nodeB", "Bob");   b.start()
    k1 = converge_presence(a, b)                     # first ever -> FULL
    check("first emit is FULL", k1 == ["full"])
    view = b.presence.view_of("nodeA")
    check("Bob reconstructs Alice name", view["name"] == "Alice")
    check("Bob reconstructs Alice status online", view["status"] == "online")
    k2 = converge_presence(a, b)                     # nothing changed -> SAME
    check("unchanged emit is SAME (nothing on wire)", k2 == ["same"])
    a.go_away()
    k3 = converge_presence(a, b)                     # status changed -> DIFF
    check("status change is DIFF", k3 == ["diff"])
    check("Bob converges to away", b.presence.view_of("nodeA")["status"] == "away")


def t_beacon():
    print("T2 beacon: the one byte (alive -> need help)")
    a = Communicator("nodeA", "Alice"); a.start()
    b = Communicator("nodeB", "Bob");   b.start()
    converge_presence(a, b)                           # FULL, beacon=0 (OK)
    check("peer sees OK beacon", b.presence.view_of("nodeA")["beacon"] == 0)
    quiet = converge_presence(a, b)                  # still alive, no change
    check("steady alive is SAME (no wire)", quiet == ["same"])
    a.raise_beacon()                                 # the one-byte flip
    kinds = converge_presence(a, b)
    check("need-help flip is a DIFF", kinds == ["diff"])
    check("peer sees NEED_HELP", b.presence.view_of("nodeA")["beacon"] == 1)


def t_text_lossless():
    print("T3 text: lossless-eventual (every message lands, in order)")
    a = Communicator("nodeA", "Alice"); a.start()
    b = Communicator("nodeB", "Bob");   b.start()
    a.room("family").channel("nodeB")                # open channel before posting
    a.say("family", "one"); a.say("family", "two"); a.say("family", "three")
    a.room("family").set_typing("Alice")             # latest-only rides along
    vals = converge_room(a, b, "family")
    msgs = [v["msg"]["text"] for v in vals if v.get("msg")]
    check("all three messages delivered", msgs == ["one", "two", "three"])
    check("delivered in order", msgs == sorted(["one", "two", "three"], key=["one","two","three"].index))
    check("sender log is resumable (perm)", [m["text"] for m in a.room("family").log()] == ["one", "two", "three"])


def t_bringup():
    print("T4 bring-up: install stands the node up (idempotent)")
    bu = default_bringup("Alice")
    st = bu.bring_up()
    check("reaches READY", st == State.READY)
    check("is_ready (all services up)", bu.is_ready())
    check("got a 10/8 address", str(bu.ctx.get("addr", "")).startswith("10."))
    order = bu.trace[:]
    bu.bring_up()                                    # re-run
    check("re-run is a no-op (idempotent)", bu.trace == order)
    idn = FakeIdentity()
    check("keypair idempotent", idn.ensure_keypair() == idn.ensure_keypair())
    pa = FakePondAdmin()
    check("rejoin returns same lease", pa.join("K", "n") == pa.join("K", "n"))

    # grounded bring-up contracts (after reading the setup-admin + pond_admin surfaces)
    pa2 = FakePondAdmin(node_base="10.111.11.0", node_increment=256)
    r0, r1 = pa2.join("A", "alice"), pa2.join("B", "bob")
    check("node record has the real nodes-action shape",
          all(k in r0 for k in ("pond", "name", "label", "subnet", "pubkey", "active", "blocked")))
    check("node addr is the subnet .1 (FrogNetHost)", r0["addr"].endswith(".1"))
    check("node_base+node_increment gives distinct subnets", r0["subnet"] != r1["subnet"])
    sh = FakeSetupHelper()
    BringUp(FakeBle(name="Carol"), sh, FakeIdentity(), FakePondAdmin(), FakeLillypad()).bring_up()
    check("apply_identity bound (name, ip) per <name> <ip>, not the pubkey",
          sh.identity_applied is not None and sh.identity_applied[0] == "Carol"
          and str(sh.identity_applied[1]).startswith("10."))


def t_float():
    print("T5 working memory: perm-first commit, refault-from-perm (the float)")
    a = Communicator("nodeA", "Alice"); a.start()
    a.presence.set_beacon(1)
    key = a.presence._key()
    check("perm holds authority", a.p.load(key)["beacon"] == 1)
    check("transient holds live copy", a.t.get(key)["beacon"] == 1)
    a.t.drop(key)                                    # cold / relocated host
    check("transient now cold", a.t.get(key) is None)
    refaulted = a.presence._live(key)               # the float in miniature
    check("refaults from perm", refaulted["beacon"] == 1)
    check("transient repopulated", a.t.get(key)["beacon"] == 1)


def t_av_call():
    print("T7 A/V call (the first codex): envelope once, audio continuous, silence SAME")
    a = Communicator("10.179.179.1", "Alice"); a.start()
    call = a.call("call-1", codec="opus", sr=48000)
    rep = MediaReplica(call.codex)
    # frame 1: FULL carries the envelope + first audio
    call.frame(1, b"AUDIO-1", video=b"VIDEO-1")
    f1 = call.emit_to("peer")[0]; out1 = rep.apply(*f1)
    check("frame 1 is FULL", out1["kind"] == "full")
    check("envelope rode the FULL (session_id present)", out1["session_id"] == "call-1")
    check("audio reconstructs to bytes", out1["audio"] == b"AUDIO-1")
    check("video reconstructs to bytes", out1["video"] == b"VIDEO-1")
    # frame 2: audio changes -> DIFF, envelope does NOT re-ship (resident-once)
    call.frame(2, b"AUDIO-2", video=b"VIDEO-2")
    f2 = call.emit_to("peer")[0]; out2 = rep.apply(*f2)
    check("frame 2 is DIFF (envelope not re-shipped)", out2["kind"] == "diff")
    check("audio converged to new frame", out2["audio"] == b"AUDIO-2")
    # frame 3: identical audio (Opus DTX silence), new seq -> DIFF carries ONLY seq;
    # the unchanged audio (continuous) does NOT re-cross the wire - that's the saving.
    import base64 as _b64m
    call.frame(3, b"AUDIO-2", video=b"VIDEO-2")
    f3, k3 = call.emit_to("peer")[0]
    check("repeat-audio frame is a seq-only DIFF, not a re-ship", k3 == "diff")
    check("unchanged audio did NOT re-cross the wire",
          _b64m.b64encode(b"AUDIO-2") not in f3)
    out3 = rep.apply(f3, k3)
    check("far side still holds the audio", out3["audio"] == b"AUDIO-2")
    # a fully idle tick (nothing changes) -> SAME, zero payload
    f4, k4 = call.emit_to("peer")[0]
    check("idle tick encodes SAME (zero wire)", k4 == "same")


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    t_presence_convergence(); t_beacon(); t_text_lossless()
    t_bringup(); t_float()
    t_av_call()
    n = len(results); ok = sum(1 for _, v in results if v)
    print(f"\n{ok}/{n} checks passed")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
