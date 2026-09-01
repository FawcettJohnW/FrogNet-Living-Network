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
test_call_host_roster_oracle.py - host roster seeded from the call tuple (the gap fix).

The client never writes mediastream/stream or mediastream/watch, so the host's plan was
empty (uplinks arrived, nothing mixed). frognet_call_host seeds the roster from the
originator's `call` tuple participants instead. This gates that.

  1. read_participants pulls the FULL participant set from the call tuple(s) the originator
     wrote (scope session:<sid>:<invitee>, value carries participants + sid).
  2. plan_from_participants makes every participant a SOURCE and a WATCHER (all-feeds).
  3. the resulting plan has all sources -> the host would add_source for each (non-empty).
  4. the TupleBackend.get_one adapter works over the get/get-only tuple surface.
"""
from __future__ import annotations
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok: FAILS.append(label)


class FakeTuples:
    """Mirror of frognet_tuples used by TupleBackend (get/put by service+var, scope kept)."""
    def __init__(self): self.rows = []
    def put(self, service, var, scope, value, dbhost=None, timeout=4.0, own=True):
        self.rows = [r for r in self.rows if not (r[0]==service and r[1]==var and r[2]==scope)]
        self.rows.append((service, var, scope, value)); return True
    def get(self, service, var, dbhost=None, fresh_s=0, timeout=4.0):
        return [{"scope": r[2], "value": r[3]} for r in self.rows if r[0]==service and r[1]==var]


def run():
    # inject the fake tuples as frognet_tuples BEFORE importing the host module
    import types
    ft = FakeTuples()
    mod = types.ModuleType("frognet_tuples")
    mod.put = ft.put; mod.get = ft.get
    mod.session_scope = lambda sid: f"session:{sid}"
    mod.role_scope = lambda x: f"role:{x}"
    sys.modules["frognet_tuples"] = mod

    import frognet_call_host as H

    # originator (alice) wrote one call tuple per invitee, each carrying the full roster
    sid = "alice-grp-123"
    participants = ["alice", "bob", "carol"]
    for p in ["bob", "carol"]:
        ft.put("communicator", "call", f"session:{sid}:{p}",
               {"from": "alice", "to": p, "state": "ringing", "sid": sid,
                "participants": participants, "group": True})

    backend = H.TupleBackend("databasehost_control.frognet")

    # 4. get_one adapter works over get-only surface
    ft.put("mediastream", "conn_info", f"session:{sid}",
           {"addr": "10.250.250.1", "tx": 9101, "rx": 9102})
    one = backend.get_one("mediastream", "conn_info", f"session:{sid}")
    check("get_one adapter resolves a scoped value", one and one.get("tx") == 9101, f"{one}")

    # 1. roster from the call tuple
    roster = H.participants_for(backend, sid)
    check("roster seeded from call tuple = full participant set",
          set(roster) == set(participants), f"roster={roster}")

    # 2+3. plan has every participant as a source (all-feeds)
    plan = H.plan_from_participants(roster)
    check("plan sources = all participants (non-empty)",
          set(plan["sources"]) == set(participants), f"sources={plan['sources']}")
    check("every participant is a recipient (all-feeds, watcher==source)",
          set(plan["recipients"].keys()) == set(participants),
          f"recipients={list(plan['recipients'])}")
    check("plan has a demanded rung (encoder would start)",
          len(plan["demanded_levels"]) >= 1, f"levels={plan['demanded_levels']}")

    # contrast: the OLD path (read stream/watch tuples) would be empty
    import call_host_service as HS
    old_streams, old_watchers = HS._read_streams_watchers(backend, sid, "databasehost_control.frognet")
    check("OLD stream/watch path is empty (confirms the gap the roster fix closes)",
          old_streams == [] and old_watchers == [], f"s={old_streams} w={old_watchers}")


def main():
    print("=== host roster seeded from the call tuple (empty-plan gap fix) ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL HOST-ROSTER CHECKS PASS" if not FAILS
                  else f"HOST-ROSTER CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
