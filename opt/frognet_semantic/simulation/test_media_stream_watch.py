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
test_media_stream_watch.py - the server reacts autonomously (watch thread), and the
FrognetTuplesBackend adapter round-trips against the REAL frognet_tuples row shape.

Covers the two "box-ready" gaps:
  W1  server watch thread honors a create-stream tuple with NO manual maybe_create call.
  W2  server watch thread applies a control intent on its own.
  A1  FrognetTuplesBackend.get_one parses the real frognet_tuples get_all() row shape
      (rows are {var,scope,name,addr,value}) - verified against a faithful stub of that
      contract so the adapter is proven without a live api.php.
"""
import sys, os, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_space import MockSpace
import media_stream as M

PASS = 0; FAIL = 0
def check(n, c, e=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  [PASS] {n}")
    else: FAIL += 1; print(f"  [FAIL] {n}  {e}")

print("=== server watch loop + frognet_tuples adapter ===\n")

# ---------------------------------------------------------------- W1/W2 watch loop
print("W1/W2 server watch thread reacts to create + intent with no manual driving")
space = MockSpace(); sid = "sotf-watch"
sctl = M.TupleControl(space, sid, addr="10.250.250.1")
server = M.MediaStreamServer(sctl, ladder=None)
stop = server.start_watch(interval_s=0.02)
try:
    # an endpoint writes create-stream; the watching server must publish conn-info itself
    M.TupleControl(space, sid, addr="10.0.0.5").request_create(session_id=sid, codec="vp8")
    deadline = time.time() + 3
    while time.time() < deadline and not server.created:
        time.sleep(0.02)
    check("W1 watch thread created the stream from the tuple", server.created is True)
    ci = M.TupleControl(space, sid, addr="10.0.0.9").conn_info()
    check("W1 watch thread published conn-info", ci is not None and ci.get("tx"), ci)

    # an endpoint writes a supported intent; the watcher applies it
    M.TupleControl(space, sid, addr="10.0.0.5").write_intent("pause")
    deadline = time.time() + 3
    while time.time() < deadline and not server.applied_intents:
        time.sleep(0.02)
    check("W2 watch thread applied the control intent", bool(server.applied_intents)
          and server.applied_intents[-1]["verb"] == "pause")
finally:
    stop.set()

# ---------------------------------------------------------------- A1 adapter shape
print("\nA1 FrognetTuplesBackend parses the real frognet_tuples get_all row shape")
class FakeFrognetTuples:
    """Faithful stub of the frognet_tuples contract that matters to the adapter:
    put(service,var,scope,value,dbhost,own,timeout) and get_all(service,dbhost,
    fresh_s,timeout) returning rows {var,scope,name,addr,value} with name='SD:<var>.<scope>'."""
    SD = "SD:"
    def __init__(self): self.rows = {}
    def put(self, service, var, scope, value, dbhost="databasehost.frognet", own=True, timeout=4.0):
        name = f"{self.SD}{var}.{scope}"
        self.rows[(service, name)] = {"value": dict(value), "addr": "10.0.0.1"}
        return True
    def get_all(self, service, dbhost="databasehost.frognet", fresh_s=0, timeout=4.0):
        out = []
        for (svc, name), r in self.rows.items():
            if svc != service: continue
            bare = name[len(self.SD):] if name.startswith(self.SD) else name
            var, _, scope = bare.partition(".")
            out.append({"var": var, "scope": scope, "name": name,
                        "addr": r["addr"], "value": r["value"]})
        return out
    def get(self, service, var, dbhost="databasehost.frognet", fresh_s=0, timeout=4.0):
        return [r for r in self.get_all(service) if r["var"] == var]

fake = FakeFrognetTuples()
backend = M.FrognetTuplesBackend(fake, dbhost="databasehost.frognet")
ctl = M.TupleControl(backend, "sotf-adapter", addr="10.250.250.1")
# server writes conn-info through the adapter; a reader pulls it back
ctl.publish_conn_info("mediahost.frognet", 9101, 9102, ["pause", "seek"])
ci = ctl.conn_info()
check("A1 adapter put->get_one round-trips conn-info", ci and ci["tx"] == 9101 and "seek" in ci["supports"], ci)
ctl.publish_bearer(3)
check("A1 adapter reads bearer back", ctl.bearer() == 3)
ctl.set_state("live", note="adapter")
check("A1 adapter reads server status", (ctl.state() or {}).get("note") == "adapter")
# and a full server runs on the adapter backend
srv = M.MediaStreamServer(ctl, addr="mediahost.frognet")
M.TupleControl(backend, "sotf-adapter").request_create(session_id="sotf-adapter", codec="vp8")
check("A1 server.maybe_create works on the real-tuples adapter", srv.maybe_create_from_tuple() is True)

print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
