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
test_unrest_handler_interface_oracle.py - guards the one-interface refactor.

PROVES:
  1. Every semantic handler (format + role) exposes the COMPLETE interface - no missing
     method, no AttributeError, no special-casing needed at the call site.
  2. FORMAT handlers (JSON/XML/HTML/text) do only compress/decompress: their codec slot is
     real, and everything else is the empty-return default (advertise writes nothing,
     score==-1.0, evaluate==None).
  3. ROLE handlers (databasehost/mediahost/boardgame) advertise their <role>/capability to
     BOTH databasehost_control.frognet and databasehost.frognet, perf inside the blob.
  4. publish_all() fans one probe across every role -> the all-contexts publish.

Self-contained: a fake frognet_tuples records writes; the probe is stubbed; no network.
Exit 0 on PASS, nonzero on any FAIL.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # /opt/frognet_semantic

CONTROL = "databasehost_control.frognet"
DATA = "databasehost.frognet"
FAKE_BLOB = {"capability": {"mysql_running": True, "cores": 4, "lan_ip": "10.20.30.1"},
             "loadavg": {"1": 0.2}, "ts": 1}

# full interface every handler must expose
IFACE = ["learn_request_template", "learn_reply_template", "extract_request_dynamic",
         "extract_reply_dynamic", "rebuild_reply", "decode_payload",
         "score", "evaluate", "advertise", "hostReset"]


def _install_fake_tuples():
    rec = []
    m = types.ModuleType("frognet_tuples")
    m.role_scope = lambda role: f"host:10.20.30.1:{role}"
    def put(service, var, scope, value, dbhost=CONTROL, timeout=4.0, own=True):
        rec.append({"dbhost": dbhost, "role": service, "var": var, "value": value, "own": own})
        return True
    m.put = put
    m.prune_self_stale_capability = lambda role, var="capability", dbhost=CONTROL: 0
    m.DEFAULT_DBHOST = CONTROL
    sys.modules["frognet_tuples"] = m
    import core.unrest_handler as _uh
    _uh._tuples = lambda: m            # code now prefers core.frognet_tuples; force the fake
    return rec


def main():
    rec = _install_fake_tuples()
    from core.unrest_handler import UnRESTHandler
    UnRESTHandler._probe_capability = staticmethod(lambda probe, logger: dict(FAKE_BLOB))

    from core.role_registry import ROLE_HANDLERS
    from core.format_registry import FORMAT_HANDLERS
    from core import role_publish

    fails = []
    def ck(c, m):
        if not c:
            fails.append(m)

    # 1. EVERY handler exposes the complete interface
    all_handlers = dict(FORMAT_HANDLERS)
    for r, h in ROLE_HANDLERS.items():
        all_handlers[f"role:{r}"] = h
    for name, h in all_handlers.items():
        for meth in IFACE:
            ck(callable(getattr(h, meth, None)), f"{name}: missing/!callable {meth}")
        ck(hasattr(h, "mode"), f"{name}: missing mode")

    # 2. FORMAT handlers: codec real, everything else return()s
    for fmt in ("json", "xml", "html", "text", "raw"):
        h = FORMAT_HANDLERS[fmt]
        rec.clear()
        rep = h.advertise()
        ck(rep.get("role") is None and not rec,
           f"format {fmt}: advertise() must write nothing (role=None), got {rep} writes={rec}")
        ck(h.score({"lan_ip": "10.0.0.1"}) == -1.0, f"format {fmt}: score must be -1.0 (ineligible)")
        ck(h.evaluate([{"lan_ip": "10.0.0.1"}], []) is None, f"format {fmt}: evaluate must be None")
        ck(isinstance(h.learn_request_template("{}"), dict),
           f"format {fmt}: codec learn_request_template must still work")

    # 3. ROLE handlers: advertise() dual-writes capability to BOTH DBs
    for role, h in ROLE_HANDLERS.items():
        rec.clear()
        h.advertise()
        dbhosts = {r["dbhost"] for r in rec if r["var"] == "capability" and r["role"] == role}
        ck(dbhosts == {CONTROL, DATA},
           f"role {role}: advertise wrote to {dbhosts}, expected both control+data")
        ck(all("loadavg" in r["value"] for r in rec if r["var"] == "capability"),
           f"role {role}: perf must ride inside the capability blob")

    # 4. publish_all() fans one probe across every role -> both DBs each
    rec.clear()
    role_publish.publish_all()
    for role in ROLE_HANDLERS:
        dbhosts = {r["dbhost"] for r in rec if r["role"] == role and r["var"] == "capability"}
        ck(dbhosts == {CONTROL, DATA}, f"publish_all: role {role} -> {dbhosts}, expected both")

    if fails:
        print("FAIL test_unrest_handler_interface_oracle")
        for f in fails:
            print("  -", f)
        return 1
    print(f"PASS test_unrest_handler_interface_oracle "
          f"(handlers={len(all_handlers)}: uniform interface; format=codec-only return(); "
          f"roles dual-write both DBs; publish_all fans all roles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
