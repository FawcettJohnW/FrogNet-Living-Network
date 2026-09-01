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
NOTE: the CANONICAL convergence substrate is simulation/unrest_substrate.py in the
FrogNet tree (Codex+Channel+InMemoryTemplateStore, per-field Freshness, proven TIER U).
This file is the simpler app-level illustration of the same property for the bundle.
Also: with codec build 2026-06-10-typeraw-bytes a binary field crosses as native TYPE_RAW
bytes (no base64 tax) -- relevant when a codex carries media/blobs.

convergence_sim.py -- SIM proof of the doctrine data plane for backgammon.

Shows the SERVED path moving MEMORY, not whole structures:
  - the resident scaffold (the learned template) crosses ONCE per fresh client,
  - thereafter a caught-up client's poll is a small DIFF (only the hot fields) or a
    0-byte SAME ("your copy is correct"),
  - the client holds the structure and RECONSTRUCTS it from deltas; reconstruction
    is asserted equal to the authoritative state every step.

Uses the REAL SemanticCodec + JsonFormatHandler and the REAL BackgammonCodex engine.
This is the sim-rung reference; the shipping Tk/Android/web clients adopt the same
ClientReplica pattern (hold reference, decode deltas) to become doctrine-complete.

Run:  FROGNET_SEMANTIC=/path/to/frognet_semantic python3 dev/convergence_sim.py
"""
import os, sys, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codex"))
from backgammon_codex import (BackgammonCodex, InMemoryTransient, InMemoryPerm, wire_codec)

OP = 0xB6  # backgammon opcode (semantic_path identity in the real store)


def state_json(codex, gid):
    """The authoritative structure as canonical JSON (field order stable)."""
    return json.dumps(codex.get(gid), sort_keys=True)


class _Tpl:
    """Minimal req-template view decode_request expects (url keys + fragment)."""
    url_query_keys = []
    def __init__(self, frag): self.fragment = frag


class ConvergenceChannel:
    """FrogNet-side: learns the template once, holds a per-client reference, and
    emits SAME / DIFF / FULL -- never the whole structure to a caught-up client."""
    def __init__(self, codec, handler, first_state_json):
        self.codec = codec; self.h = handler
        self.frag = handler.learn_request_template(first_state_json)
        self.tpl = _Tpl(self.frag)
        self.template_bytes = len(json.dumps(self.frag.get("schema", self.frag)).encode())
        self.ref = {}            # client_id -> reference dict
        self.sent_template = set()

    def serve(self, client_id, body_json):
        """Return (kind, wire_bytes, template_or_None). template crosses once."""
        dyn = self.h.extract_request_dynamic(body_json, self.frag)
        prior = self.ref.get(client_id)
        diff, newref, identical = self.codec.encode_request_diff(
            opcode=OP, url_vals=[], json_vals=dyn, type_map=self.frag["type_map"],
            reference=prior, tokens=self.frag["tokens"], compress=True)
        self.ref[client_id] = newref
        tpl = None
        if client_id not in self.sent_template:        # resident scaffold, once
            tpl = self.frag; self.sent_template.add(client_id)
        if identical:
            return ("SAME", b"", tpl)
        if prior is None:
            full = self.codec.encode_request(opcode=OP, url_vals=[], json_vals=dyn,
                type_map=self.frag["type_map"], tokens=self.frag["tokens"], compress=True)
            return ("FULL", full, tpl)
        return ("DIFF", diff, tpl)


class ClientReplica:
    """App-side: holds the template + reference + reconstructed structure, and
    rebuilds full state from deltas. Never re-fetches the whole structure."""
    def __init__(self, codec):
        self.codec = codec; self.frag = None; self.tpl = None
        self.ref = None; self.fields = {}

    def apply(self, kind, wire, tokens, template):
        if template is not None and self.frag is None:
            self.frag = template; self.tpl = _Tpl(template)
        if kind == "SAME":
            return self.fields                      # copy already correct
        _, jv = self.codec.decode_request(wire, self.tpl, tokens=tokens, reference=self.ref)
        self.fields = dict(jv)
        self.ref = dict(self.fields)                # reference advances
        return self.fields


def main():
    codec, handler = wire_codec()
    if codec is None:
        print("real codec not importable -- set FROGNET_SEMANTIC to the tree"); return 1

    auth = BackgammonCodex(InMemoryTransient(), InMemoryPerm())
    auth.new_game(gid="t1", white="Julie", black="John")
    auth.roll("t1", 3, 1)                            # deterministic first roll

    ch = ConvergenceChannel(codec, handler, state_json(auth, "t1"))
    tokens = ch.frag["tokens"]
    A = ClientReplica(codec)

    def authoritative_fields():
        return dict(handler.extract_request_dynamic(state_json(auth, "t1"), ch.frag))
    def poll(client_id, replica):
        kind, wire, tpl = ch.serve(client_id, state_json(auth, "t1"))
        fields = replica.apply(kind, wire, tokens, tpl)
        ok = (fields == authoritative_fields())
        scaffold = 0 if tpl is None else ch.template_bytes
        return kind, len(wire), scaffold, ok

    full_state_bytes = len(state_json(auth, "t1").encode())
    print("=" * 70)
    print("Backgammon -- convergence on the served path (real codec)")
    print("=" * 70)
    print(f"full authoritative state JSON = {full_state_bytes} bytes\n")
    rows = []

    # 1) fresh client A: scaffold once + FULL
    k, w, sc, ok = poll("A", A)
    rows.append(("A joins (fresh)", k, w, sc, ok)); FAIL = (not ok)

    # 2) A polls again, nothing changed -> SAME (0 bytes)
    k, w, sc, ok = poll("A", A)
    rows.append(("A re-polls, no change", k, w, sc, ok)); FAIL |= (not ok) or (k != "SAME")

    # 3) make a move on the authoritative table, A polls -> small DIFF
    mv = auth.get("t1")["legal"][0]; auth.move("t1", mv["from"], mv["die"])
    k, w, sc, ok = poll("A", A)
    rows.append(("A polls after a move", k, w, sc, ok)); FAIL |= (not ok) or (k != "DIFF")
    move_diff = w

    # 4) fresh client B joins mid-game -> pays a FULL (no shortcut for the uncaught-up)
    B = ClientReplica(codec)
    k, w, sc, ok = poll("B", B)
    rows.append(("B joins mid-game (fresh)", k, w, sc, ok)); FAIL |= (not ok)

    # 5) another move, both poll -> both get a DIFF; A's reconstruct still matches
    mv = auth.get("t1")["legal"][0]; auth.move("t1", mv["from"], mv["die"])
    kA, wA, _, okA = poll("A", A)
    kB, wB, _, okB = poll("B", B)
    rows.append(("A polls after move 2", kA, wA, 0, okA))
    rows.append(("B polls after move 2", kB, wB, 0, okB)); FAIL |= (not okA) or (not okB)

    print(f"{'step':<26}{'kind':<6}{'wire B':>8}{'scaffold':>10}{'reconstruct ok':>16}")
    for name, kind, wb, sc, ok in rows:
        print(f"{name:<26}{kind:<6}{wb:>8}{sc:>10}{'  yes' if ok else '  NO':>16}")

    print("\nReadout:")
    print(f"  * scaffold (resident template) crosses ONCE per client (~{ch.template_bytes} B), never again")
    print(f"  * a move on the wire to a caught-up client = {move_diff} B DIFF vs {full_state_bytes} B full state")
    print(f"  * no change = 0-byte SAME ('your copy is correct')")
    print(f"  * every client reconstruction matched the authoritative state")
    print("\n" + "=" * 70)
    if FAIL:
        print("RESULT: FAIL -- convergence or reconstruction did not hold"); return 1
    print("RESULT: ALL PASS -- the served path moves memory, not whole structures"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
