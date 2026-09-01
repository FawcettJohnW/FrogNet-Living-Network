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
test_calendar_codex.py - unit + integration tests for the Family Calendar codex.
Runnable here (in-memory stores) and on the box (swap in api.php/Radicale stores).
Integration test drives the REAL SemanticCodec when the tree is importable.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex"))

from calendar_codex import (
    CalendarCodex, InMemoryTransientStore, InMemoryPermStore,
    make_event, wire_codec, ELEMENT_EVENTS,
)

FAILS = []
def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok: FAILS.append(name)

def fresh():
    return CalendarCodex(InMemoryTransientStore(), InMemoryPermStore())

# --- unit -----------------------------------------------------------------
def t_crud():
    print("\nU1: create / list / update / delete")
    c = fresh()
    e = c.create_event(make_event("Dinner", "2026-06-10T18:00", "2026-06-10T19:00",
                                   uid="ev1", location="Mom's"))
    check("create returns the event", e["uid"] == "ev1")
    check("list shows one event", len(c.list_events()) == 1)
    c.update_event("ev1", location="Our place", summary="Family Dinner")
    got = c.list_events()[0]
    check("update mutates fields", got["location"] == "Our place" and got["summary"] == "Family Dinner")
    check("delete removes it", c.delete_event("ev1") and len(c.list_events()) == 0)
    check("delete of missing uid is False", c.delete_event("nope") is False)

def t_perm_authority():
    print("\nU2: perm is the authority - writes go through to perm")
    t, p = InMemoryTransientStore(), InMemoryPermStore()
    c = CalendarCodex(t, p)
    c.create_event(make_event("Trip", "2026-07-01T00:00", "2026-07-05T00:00", uid="ev2", all_day=True))
    check("event persisted to perm store", any(e["uid"] == "ev2" for e in p.load_all()))
    check("event present in transient element", any(e["uid"] == "ev2" for e in c.list_events()))

def t_refault():
    print("\nU3: relocatable brain - cold transient host refaults from perm")
    t, p = InMemoryTransientStore(), InMemoryPermStore()
    c = CalendarCodex(t, p)
    c.create_event(make_event("Recital", "2026-06-20T17:00", "2026-06-20T18:30", uid="ev3"))
    v_before = t.get(ELEMENT_EVENTS)["version"]
    t.drop(ELEMENT_EVENTS)                       # host re-elected -> element gone
    check("transient element is gone after reelection", t.get(ELEMENT_EVENTS) is None)
    events = c.list_events()                      # triggers refault from perm
    check("calendar refaults from perm (data NOT lost)", any(e["uid"] == "ev3" for e in events))
    check("refaulted element has a fresh version", t.get(ELEMENT_EVENTS)["version"] > 0)

def t_presence():
    print("\nU4: presence sibling element")
    c = fresh()
    c.touch_presence("dad"); c.touch_presence("mom")
    here = c.who_is_here()
    check("presence shows both viewers", here == ["dad", "mom"], str(here))

def t_stale():
    print("\nU5: client split detection - backwards-time read terminates")
    check("forward read is not stale", CalendarCodex.is_stale_read(1200, 1100) is False)
    check("backwards read is stale (terminate, do not fork)",
          CalendarCodex.is_stale_read(1100, 1200) is True)

# --- integration: real codec, per-reader DIFF -----------------------------
def t_wire_per_reader():
    print("\nI1: real codec - adding one event ships a DIFF; per-reader deltas")
    codec, handler = wire_codec()
    if codec is None:
        check("real codec available", False, "tree not importable - skipped on box only")
        return
    c = fresh()
    c.create_event(make_event("E1", "2026-06-10T09:00", "2026-06-10T10:00", uid="a"))
    s_v1 = json.dumps(c.t.get(ELEMENT_EVENTS), sort_keys=True)
    c.create_event(make_event("E2", "2026-06-11T09:00", "2026-06-11T10:00", uid="b"))
    s_v2 = json.dumps(c.t.get(ELEMENT_EVENTS), sort_keys=True)

    frag = handler.learn_request_template(s_v1)

    def encode(reference, body):
        dyn = handler.extract_request_dynamic(body, frag)
        diff, new_ref, ident = codec.encode_request_diff(
            opcode=0xCA1, url_vals=[], json_vals=dyn, type_map=frag["type_map"],
            reference=reference, tokens=frag["tokens"], compress=True)
        full = codec.encode_request(opcode=0xCA1, url_vals=[], json_vals=dyn,
            type_map=frag["type_map"], tokens=frag["tokens"], compress=True)
        return diff, new_ref, full

    # caught-up reader holds v1; fresh reader holds nothing
    _, refA, fullA = encode(None, s_v1)             # A learns v1
    diffA, _, _ = encode(refA, s_v2)                # A's delta to v2
    diffB_full, _, fullB = encode(None, s_v2)       # B (fresh) gets a full
    check("caught-up reader's delta < a fresh reader's full",
          len(diffA) < len(fullB), f"A_diff={len(diffA)}B  B_full={len(fullB)}B")
    check("the new event travels as a DIFF, not a whole calendar",
          len(diffA) < len(fullA), f"diff={len(diffA)}B < full={len(fullA)}B")

def main():
    print("=" * 70)
    print("Family Calendar codex - unit + integration")
    print("=" * 70)
    for t in (t_crud, t_perm_authority, t_refault, t_presence, t_stale, t_wire_per_reader):
        t()
    print("\n" + "=" * 70)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAIL - {FAILS}"); return 1
    print("RESULT: ALL PASS"); return 0

if __name__ == "__main__":
    raise SystemExit(main())
