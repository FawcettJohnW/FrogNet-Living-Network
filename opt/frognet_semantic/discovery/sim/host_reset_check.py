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
sim/host_reset_check.py - proves hostReset is the one reconcile path (memory, not
messages) across the REAL modules. Simulates a databasehost float by swapping each
module's transient for a fresh, cold one (the new host) and asserts every module
rebuilds the new transient from its perm authority - the smooth host change - plus
consistency-first ordering, idempotency, channel re-FULL, and the dispatcher's
shared-vector read culminating in UI.
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_ROOT))    # .../work
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles", "communicator"))

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

from working_memory import InMemoryTransient, InMemoryPerm
from bundle_codex import CalendarCodex, BackgammonCodex, CAL_ELEMENT
from substrate import Codex, Channel, Freshness
from frognet_host_reset import host_reset_all

def run():
    perm = InMemoryPerm()
    old_t = InMemoryTransient()
    cal = CalendarCodex(old_t, perm)
    bg = BackgammonCodex(old_t, perm, gid="g1")
    cal.add_event({"uid": "e1", "title": "soccer", "ts": 10})
    cal.add_event({"uid": "e2", "title": "dentist", "ts": 20})
    bg.play_move({"n": 1, "from": 24, "to": 23}, position="p1")

    # --- simulate a databasehost FLOAT: each module now points at a fresh, COLD host ---
    new_t = InMemoryTransient()
    cal.t = new_t
    bg.t = new_t
    probs = []
    if new_t.get(CAL_ELEMENT) is not None:
        probs.append("precondition: new transient should start cold")
    check("[FLOAT] new databasehost transient starts cold (nothing migrated yet)", probs)

    # shared-vector read + UI culmination recorders (the dispatcher's job)
    rendered = {}
    def read_vector():
        # read shared memory BY TUPLE VECTOR from the (now re-asserted) new transient
        return [k for k in (CAL_ELEMENT, bg.element) if new_t.get(k) is not None]
    def render_ui(vec):
        rendered["vec"] = list(vec)

    out = host_reset_all([cal, bg], read_shared_vector=read_vector,
                         render_ui=render_ui)

    # --- 1. SMOOTH MOVE: new transient rebuilt from perm by re-assertion ---
    probs = []
    if new_t.get(CAL_ELEMENT) is None or new_t.get(bg.element) is None:
        probs.append("new transient was NOT repopulated from perm on hostReset")
    if cal.events() != [{"uid": "e1", "title": "soccer", "ts": 10},
                        {"uid": "e2", "title": "dentist", "ts": 20}]:
        probs.append(f"calendar state lost across the float: {cal.events()}")
    if bg.moves() != [{"n": 1, "from": 24, "to": 23}]:
        probs.append(f"backgammon state lost across the float: {bg.moves()}")
    check("[SMOOTH] every module rebuilt the new transient from perm (memory, not migration)", probs)

    # --- 2. UI culmination over the re-asserted shared vector ---
    probs = []
    if rendered.get("vec") != [CAL_ELEMENT, bg.element]:
        probs.append(f"UI not rendered over the full re-asserted vector: {rendered.get('vec')}")
    if not out["ui_rendered"]:
        probs.append("dispatcher did not render UI")
    check("[UI] shared vector read AFTER all re-assert; Communicator UI rendered (living network)", probs)

    # --- 3. CONSISTENCY-FIRST: a dup uid in perm is repaired before re-assertion ---
    probs = []
    st = perm.load(CAL_ELEMENT)
    st["events"] = [{"uid": "e1", "ts": 10}, {"uid": "e1", "ts": 99}, {"uid": "e2", "ts": 20}]
    perm.save(CAL_ELEMENT, st)
    cal.t = InMemoryTransient()
    cal.hostReset()
    ev = cal.events()
    uids = sorted(e["uid"] for e in ev)
    e1 = next((e for e in ev if e["uid"] == "e1"), None)
    if uids != ["e1", "e2"]:
        probs.append(f"consistency did not dedup uid before write: {ev}")
    if not (e1 and e1["ts"] == 99):
        probs.append(f"consistency kept the stale e1 (want ts=99): {e1}")
    check("[CONSISTENCY] my world made coherent (dedup) BEFORE my memory is written", probs)

    # --- 4. IDEMPOTENT: re-running changes nothing (upsert, no version churn) ---
    probs = []
    cal.t = InMemoryTransient()
    r1 = cal.hostReset()
    v1 = cal.t.get(CAL_ELEMENT)["version"]
    r2 = cal.hostReset()
    v2 = cal.t.get(CAL_ELEMENT)["version"]
    if v1 != v2:
        probs.append(f"hostReset bumped version (not idempotent): {v1} -> {v2}")
    if r1["written"] != r2["written"]:
        probs.append("hostReset not idempotent across runs")
    check("[IDEMPOTENT] re-asserting the same authority does not churn version", probs)

    # --- 5. CHANNEL re-FULL: a converged reference is dropped so next emit is FULL ---
    probs = []
    cx = Codex(None, "POST", "p/x", ["a", "b"], {"a": Freshness.LATEST_ONLY,
                                                 "b": Freshness.LATEST_ONLY})
    ch = Channel(cx)
    _f, ref, kind = cx.encode({"a": 1, "b": 2}, ch.reference); ch.reference = ref
    _f2, _r2, kind2 = cx.encode({"a": 1, "b": 2}, ch.reference)   # would be SAME now
    ch.hostReset()
    _f3, _r3, kind3 = cx.encode({"a": 1, "b": 2}, ch.reference)   # reference dropped -> FULL
    if kind != "full" or kind2 != "same" or kind3 != "full":
        probs.append(f"channel reset did not force re-FULL: {kind}/{kind2}/{kind3}")
    check("[REFULL] hostReset drops the stale convergence reference; next emit is FULL", probs)

    # --- 6. SEE 1: trigger-agnostic - any reason takes the identical path ---
    probs = []
    cal.t = InMemoryTransient(); a = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    cal.t = InMemoryTransient(); b = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    if [r["written"] for r in a["modules"]] != [r["written"] for r in b["modules"]]:
        probs.append("two triggers took different paths - must be 'see 1'")
    check("[SEE1] one reconcile path regardless of what triggered it", probs)

    # --- 7. SotF / ffmpeg: the CALL reconciles (envelope survives, droppable frame shed) ---
    from media_codex import MediaCodex, SESSION_FIELDS
    mperm = InMemoryPerm(); mt = InMemoryTransient()
    call = MediaCodex(mt, mperm, session_id="call-1", codec="opus", level_idx=4)
    call.frame(seq=7, audio=b"\xaa\xbb", video=b"\xcc\xdd")   # an in-flight frame
    new_mt = InMemoryTransient(); call.t = new_mt              # mediahost/db float
    # converge a channel reference so we can prove re-FULL
    _f, _k = call.emit_to("peerX")[0][0], call.emit_to("peerX")
    rep = call.hostReset()
    probs = []
    s = new_mt.get("call-1")
    if s is None:
        probs.append("call session did NOT survive the float on the new host")
    else:
        if s.get("codec") != "opus" or s.get("level_idx") != 4:
            probs.append(f"session envelope (scaffold) lost: {s}")
        if s.get("seq") or s.get("audio") is not None or s.get("video") is not None:
            probs.append(f"droppable in-flight frame was REPLAYED on reset: {s}")
    # next emit after reset must be a FULL (reference dropped) carrying the envelope
    frames = call.emit_to("peerX")
    if not frames or frames[0][1] != "full":
        probs.append(f"media channel did not re-FULL after reset: {[k for _,k in frames]}")
    check("[SOTF] call envelope re-asserts, droppable frame shed, channel re-FULLs (ffmpeg returns)", probs)

    # --- 8. HostResetWatcher: a float is a resolved-IP delta; one reconcile per change ---
    from frognet_host_reset import HostResetWatcher
    class _M:
        def __init__(self): self.n = 0
        def hostReset(self): self.n += 1; return {"module": "m", "written": ["k"]}
    m = _M(); ip = ["10.250.250.1"]; ui = []
    w = HostResetWatcher(resolve_ip=lambda: ip[0], get_modules=lambda: [m],
                         read_shared_vector=lambda: ["v"], render_ui=lambda v: ui.append(v))
    r1 = w.tick()                                  # first: prime baseline, no reconcile
    r2 = w.tick()                                  # same IP: no-op
    ip[0] = "10.120.120.1"                          # control floats
    r3 = w.tick()                                  # delta: reconcile
    r4 = w.tick()                                  # settled again: no-op
    probs = []
    if r1 or r2 or r4:
        probs.append(f"watcher reconciled without a float: {(r1, r2, r4)}")
    if not r3 or m.n != 1:
        probs.append(f"watcher did not reconcile exactly once on the float (n={m.n})")
    if ui != [["v"]]:
        probs.append(f"living-network UI not rendered on the float: {ui}")
    check("[WATCH] float = resolved-IP delta -> exactly one reconcile (prime, then on change)", probs)

def main():
    print("=== hostReset: the one reconcile path (memory, not messages) across modules ===")
    run()
    print("\n" + ("ALL HOST-RESET CHECKS PASS" if not FAILS
                  else f"HOST-RESET CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
