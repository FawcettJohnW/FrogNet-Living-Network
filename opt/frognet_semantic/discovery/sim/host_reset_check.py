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
messages) across the REAL modules: core.frognet_host_reset (host_reset_all and
HostResetWatcher), core.role_publish (RolePublishReconcile -> publish_all) and the
shipped role handler's advertise (core.database_handler.DatabaseRoleHandler).

A databasehost float is simulated by pointing each module at a fresh, cold transient
(the new host) and asserting every module rebuilds it from its perm authority - the
smooth host change - plus consistency-first ordering, idempotency, one module's error
not sinking the reconcile, the shared-vector read happening once AFTER every module
re-asserted, and the watcher firing exactly once per resolved-IP delta.

[HOST_RESET_REAL_MODULES_V1] This used to import working_memory and substrate (the old
Communicator bundle's WorkingMemory / Codex / Channel) and media_codex, none of which is
in the tree, so it failed at import. The fixtures below are local; the modules under test
are the shipped ones.
"""
from __future__ import annotations
import os, sys
from typing import Any, Dict, List

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
sys.path.insert(0, _ROOT)

from core.frognet_host_reset import host_reset_all, HostResetWatcher   # noqa: E402

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")


# ---------------------------------------------------------------------------
# Fixtures: a transient (the databasehost's store, which floats) and a perm (this
# node's own authority, which does not), and a module that owns one element in each.
# hostReset is the doctrine host_reset_all drives: reset my world -> make MY memory
# consistent -> write MY memory.
# ---------------------------------------------------------------------------
class Store:
    def __init__(self): self.d: Dict[str, Any] = {}
    def get(self, k): return self.d.get(k)
    def put(self, k, v): self.d[k] = v


class EventsModule:
    """A lossless event list. Perm is the authority; the transient is re-asserted from it."""

    def __init__(self, transient: Store, perm: Store, key="fixture.events", log=None):
        self.t, self.p, self.key, self.log = transient, perm, key, log if log is not None else []
        self.p.put(self.key, {"version": 0, "events": []})
        self.t.put(self.key, self.p.get(self.key))

    def add_event(self, ev):
        st = dict(self.p.get(self.key))
        st["events"] = [e for e in st["events"] if e.get("uid") != ev.get("uid")] + [ev]
        st["version"] += 1
        self.p.put(self.key, st); self.t.put(self.key, st)

    def events(self):
        return list((self.t.get(self.key) or {}).get("events", []))

    def hostReset(self) -> Dict[str, Any]:
        st = dict(self.p.get(self.key))
        by_uid = {}                                  # consistency first: one event per uid, latest ts wins
        for e in st.get("events", []):
            u = e.get("uid")
            if u not in by_uid or int(e.get("ts", 0)) >= int(by_uid[u].get("ts", 0)):
                by_uid[u] = e
        consistency = "ok"
        if len(by_uid) != len(st.get("events", [])):
            st["events"] = list(by_uid.values()); self.p.put(self.key, st); consistency = "repaired"
        self.t.put(self.key, st)                     # then write: an upsert, no version bump
        self.log.append(("reset", self.key))
        return {"module": "EventsModule", "reset": [self.key], "written": [self.key],
                "consistency": consistency}


class Broken:
    def hostReset(self):
        raise RuntimeError("this module's store is unreachable")


class _StubTuples:
    """The tuple client the role handler's advertise writes through; records every put."""
    def __init__(self): self.puts: List[tuple] = []
    def role_scope(self, role): return "host:10.250.250.1:%s" % role
    def put(self, svc, var, scope, value, dbhost=None, own=True):
        self.puts.append((svc, var, scope, dbhost)); return True
    def prune_self_stale_capability(self, *a, **k): return 0


def run():
    perm = Store(); old_t = Store(); order = []
    cal = EventsModule(old_t, perm, "fixture.events", order)
    other = EventsModule(old_t, Store(), "fixture.other", order)
    cal.add_event({"uid": "e1", "title": "soccer", "ts": 10})
    cal.add_event({"uid": "e2", "title": "dentist", "ts": 20})
    other.add_event({"uid": "o1", "ts": 5})

    # --- a databasehost FLOAT: each module now points at a fresh, COLD host ---
    new_t = Store(); cal.t = new_t; other.t = new_t
    check("[FLOAT] new databasehost transient starts cold (nothing migrated yet)",
          [] if new_t.get(cal.key) is None else ["precondition: new transient should start cold"])

    rendered = {}
    def read_vector():
        order.append(("read", None))
        return [k for k in (cal.key, other.key) if new_t.get(k) is not None]
    def render_ui(vec):
        order.append(("render", None)); rendered["vec"] = list(vec)

    out = host_reset_all([cal, Broken(), other], read_shared_vector=read_vector, render_ui=render_ui)

    # --- 1. SMOOTH MOVE: new transient rebuilt from perm by re-assertion ---
    probs = []
    if new_t.get(cal.key) is None or new_t.get(other.key) is None:
        probs.append("new transient was NOT repopulated from perm on hostReset")
    if cal.events() != [{"uid": "e1", "title": "soccer", "ts": 10}, {"uid": "e2", "title": "dentist", "ts": 20}]:
        probs.append(f"event list lost across the float: {cal.events()}")
    check("[SMOOTH] every module rebuilt the new transient from perm (memory, not migration)", probs)

    # --- 2. ORDER: every module re-asserts BEFORE the one shared-vector read, then the UI ---
    probs = []
    if order[-4:] != [("reset", cal.key), ("reset", other.key), ("read", None), ("render", None)]:
        probs.append(f"reconcile order wrong: {order[-4:]}")
    if rendered.get("vec") != [cal.key, other.key] or not out["ui_rendered"]:
        probs.append(f"UI not rendered over the full re-asserted vector: {rendered.get('vec')}")
    check("[ORDER] shared vector read once, AFTER all re-assert; UI rendered over it", probs)

    # --- 3. ISOLATION: one module raising does not sink the others ---
    probs = []
    errs = [r for r in out["modules"] if r.get("error")]
    if len(out["modules"]) != 3 or len(errs) != 1 or "unreachable" not in errs[0]["error"]:
        probs.append(f"reports: {out['modules']}")
    check("[ISOLATE] a module that raises is reported; the rest still reconcile", probs)

    # --- 4. CONSISTENCY-FIRST: a dup uid in perm is repaired before re-assertion ---
    probs = []
    st = dict(perm.get(cal.key))
    st["events"] = [{"uid": "e1", "ts": 10}, {"uid": "e1", "ts": 99}, {"uid": "e2", "ts": 20}]
    perm.put(cal.key, st)
    cal.t = Store(); rep = cal.hostReset()
    ev = cal.events(); e1 = next((e for e in ev if e["uid"] == "e1"), None)
    if sorted(e["uid"] for e in ev) != ["e1", "e2"] or not (e1 and e1["ts"] == 99) or rep["consistency"] != "repaired":
        probs.append(f"consistency did not dedup uid (latest ts) before write: {ev} {rep}")
    check("[CONSISTENCY] my world made coherent (dedup) BEFORE my memory is written", probs)

    # --- 5. IDEMPOTENT: re-running changes nothing (upsert, no version churn) ---
    probs = []
    cal.t = Store(); r1 = cal.hostReset(); v1 = cal.t.get(cal.key)["version"]
    r2 = cal.hostReset(); v2 = cal.t.get(cal.key)["version"]
    if v1 != v2 or r1["written"] != r2["written"]:
        probs.append(f"hostReset not idempotent: version {v1} -> {v2}")
    check("[IDEMPOTENT] re-asserting the same authority does not churn version", probs)

    # --- 6. SEE 1: trigger-agnostic - any reason takes the identical path ---
    probs = []
    cal.t = Store(); a = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    cal.t = Store(); b = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    if [r["written"] for r in a["modules"]] != [r["written"] for r in b["modules"]]:
        probs.append("two triggers took different paths - must be 'see 1'")
    check("[SEE1] one reconcile path regardless of what triggered it", probs)

    # --- 7. ROLES: the shipped role handler rides the same path (RolePublishReconcile ->
    #        publish_all -> DatabaseRoleHandler.advertise), control first, data mirror ---
    import core.unrest_handler as UH
    import core.role_publish as RP
    from core.role_registry import ROLE_HANDLERS
    stub = _StubTuples()
    saved = (UH._tuples, RP._region_mode)
    UH._tuples = lambda: stub
    RP._region_mode = lambda: False                  # LAN-only: the store is the election input
    try:
        reps = RP.publish_all(blob={"lan_ip": "10.250.250.1", "cores": 4}, control="10.250.250.1",
                              logger=lambda s: None)
        rec = RP.RolePublishReconcile()
        RP.publish_all, _pa = (lambda logger=None, **k: reps), RP.publish_all
        via = host_reset_all([rec])
        RP.publish_all = _pa
    finally:
        UH._tuples, RP._region_mode = saved
    probs = []
    if sorted(ROLE_HANDLERS) != ["databasehost"]:
        probs.append(f"role registry is not the one-role world: {sorted(ROLE_HANDLERS)}")
    if [(s, v, d) for s, v, _sc, d in stub.puts][:1] != [("databasehost", "capability", "10.250.250.1")]:
        probs.append(f"advertise did not write databasehost/capability to the control first: {stub.puts}")
    if not any(r.get("written") for r in reps):
        probs.append(f"publish_all wrote nothing: {reps}")
    if not via["modules"] or "role_publish" not in via["modules"][0]:
        probs.append(f"RolePublishReconcile did not ride host_reset_all: {via}")
    check("[ROLES] role publish is a hostReset module; databasehost re-advertises to control", probs)

    # --- 8. HostResetWatcher: a float is a resolved-IP delta; one reconcile per change ---
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
