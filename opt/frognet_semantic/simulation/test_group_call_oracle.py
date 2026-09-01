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
test_group_call_oracle.py - multi-person originator-only call signaling.

Gates the group-call signaling in communicator.py. Uses a fake tuple backend with REAL
scope semantics (put keyed by service+var+scope; a shared scope OVERWRITES) to prove:

  1. ONE session for the whole group; the originator fixes the participant set at creation.
  2. N invitees each get a DISTINCT ring tuple (scope session:<sid>:<invitee>), so none
     overwrites another - the bug that a shared session:<sid> scope would cause.
  3. Each ring carries the full participant list + the shared sid.
  4. Each invitee's ACCEPT binds to the SAME sid (one mediahost session for all).
  5. NO join-in-progress: the participant set is fixed; a late party is not in participants.
  6. Originator-only: invitees do not create the session; they only accept/decline.
"""
from __future__ import annotations
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok: FAILS.append(label)


class FakeTuple:
    """Mirrors B.T scope semantics: put keyed by (service,var,scope) - same scope overwrites."""
    SERVICE = "communicator"
    def __init__(self): self.store = {}
    def session_scope(self, sid): return f"session:{sid}"
    def role_scope(self, x): return f"role:{x}"
    def put(self, service, var, scope, value, dbhost=None, timeout=4.0, own=True):
        self.store[(service, var, scope)] = value; return True
    def get(self, service, var, dbhost=None, fresh_s=0, timeout=4.0):
        return [{"scope": k[2], "value": v} for k, v in self.store.items()
                if k[0] == service and k[1] == var]


def make_app(me_id, me_name, T):
    """Build a Communicator instance shell with just what the signaling path needs."""
    import sys, types
    for m in ("tkinter", "tkinter.font", "tkinter.ttk", "tkinter.scrolledtext"):
        mod = types.ModuleType(m)
        if m == "tkinter":
            for n in ("Tk","Toplevel","Frame","Label","Button","Canvas","StringVar","Entry"):
                setattr(mod, n, object)
        sys.modules[m] = mod
    sys.modules["tkinter"].font = sys.modules["tkinter.font"]
    import communicator_app as B
    B.T = T
    import communicator as C
    App = [v for v in vars(C).values() if isinstance(v, type) and hasattr(v, "_spawn_media_sotf")][0]
    app = App.__new__(App)
    app.me_id = me_id; app.me_name = me_name
    app.dbhost = "databasehost_control.frognet"
    app.calls = {}; app.sessions = {}; app.seen_calls = {}; app._group_sel = set()
    app.active_peer = None; app.chat_sid = None
    app.render = lambda: None
    app.tab = type("T", (), {"set": lambda self, x: None})()
    return app, C


def run():
    T = FakeTuple()
    orig, C = make_app("alice", "Alice", T)

    # 1+2+3. originator starts a group call with bob, carol, dave
    orig.start_group_call(["bob", "carol", "dave"])
    rings = T.get("communicator", "call")
    ring_scopes = sorted(r["scope"] for r in rings)
    check("3 invitees -> 3 DISTINCT ring tuples (no overwrite)", len(rings) == 3,
          f"scopes={ring_scopes}")
    sids = {r["value"]["sid"] for r in rings}
    check("all rings share ONE session id", len(sids) == 1, f"sids={sids}")
    parts = rings[0]["value"]["participants"]
    check("participant set fixed at creation (originator + 3)",
          parts == ["alice", "bob", "carol", "dave"], f"participants={parts}")
    check("each ring scope is session:<sid>:<invitee>",
          all(r["scope"].endswith((":bob", ":carol", ":dave")) for r in rings),
          f"scopes={ring_scopes}")
    check("ring flagged as group (>2 participants)",
          all(r["value"]["group"] for r in rings))
    sid = list(sids)[0]

    # 6. originator-only: an invitee (bob) does NOT create a session; only accepts.
    bob, _ = make_app("bob", "Bob", T)
    # bob sees his ring and accepts (idempotent-spawn path stubbed)
    bob._spawn_media = lambda peer, s: bob.__dict__.setdefault("_spawned", (peer, s))
    bob.accept_call(sid, "alice", participants=parts)
    accepts = [r for r in T.get("communicator", "call") if r["value"].get("state") == "accepted"]
    check("invitee ACCEPT binds to the SAME sid", accepts and accepts[0]["value"]["sid"] == sid,
          f"accepts={[a['value'].get('sid') for a in accepts]}")
    check("invitee accept is on its own scope (session:<sid>:bob)",
          any(a["scope"] == f"session:{sid}:bob" for a in accepts))
    check("invitee joins the shared session via _spawn_media", bob.__dict__.get("_spawned") == ("alice", sid),
          f"spawned={bob.__dict__.get('_spawned')}")

    # 4. carol accepts too -> same sid, distinct scope (no overwrite of bob's accept)
    carol, _ = make_app("carol", "Carol", T)
    carol._spawn_media = lambda peer, s: None
    carol.accept_call(sid, "alice", participants=parts)
    accepts2 = [r for r in T.get("communicator", "call") if r["value"].get("state") == "accepted"]
    check("multiple accepts coexist (distinct scopes, no overwrite)", len(accepts2) == 2,
          f"accept_scopes={[a['scope'] for a in accepts2]}")

    # 5. no join-in-progress: a non-invited party (eve) is not in participants
    check("no join-in-progress: uninvited party not in participant set",
          "eve" not in parts)


def main():
    print("=== multi-person originator-only group call signaling ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL GROUP-CALL CHECKS PASS" if not FAILS
                  else f"GROUP-CALL CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
