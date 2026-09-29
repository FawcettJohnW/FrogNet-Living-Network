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
test_perf_publisher_oracle.py - guards [CAPABILITY_DUAL_WRITE_V1].

Fails on the OLD behavior (capability written to ONE host) and passes on the NEW
(written to BOTH databasehost_control.frognet and databasehost.frognet), for every role,
at boot (publish) and on every float (hostReset). Self-contained: a fake frognet_tuples
records writes, the probe is stubbed, no network.

Exit 0 on PASS, nonzero on any FAIL.
"""
import sys
import types

CONTROL = "databasehost_control.frognet"
DATA = "databasehost.frognet"

FAKE_BLOB = {
    "capability": {"mysql_running": True, "ram_mb": 8192, "cores": 4,
                   "disk_free_mb": 50000, "lan_ip": "10.20.30.1"},
    "loadavg": {"1m": 0.3, "5m": 0.2, "15m": 0.1},   # perf rides INSIDE the blob
    "temps_c": [41.0],
    "ts": 111,
}


def _install_fake_tuples():
    """Inject a recording frognet_tuples into sys.modules so the publisher's late import
    binds to it. Returns the list it records (dbhost, role, var, scope, value) into."""
    rec = []
    m = types.ModuleType("frognet_tuples")

    def role_scope(role):
        return f"host:10.20.30.1:{role}"

    def put(service, var, scope, value, dbhost="databasehost_control.frognet",
            timeout=4.0, own=True):
        rec.append({"dbhost": dbhost, "role": service, "var": var,
                    "scope": scope, "value": value, "own": own})
        return True

    def prune_self_stale_capability(role, var="capability",
                                    dbhost="databasehost_control.frognet"):
        return 0

    m.role_scope = role_scope
    m.put = put
    m.prune_self_stale_capability = prune_self_stale_capability
    m.DEFAULT_DBHOST = CONTROL
    sys.modules["frognet_tuples"] = m
    return rec


def _check(cond, msg, fails):
    if not cond:
        fails.append(msg)


def main():
    rec = _install_fake_tuples()
    import frognet_perf_publisher as P

    roles = ("databasehost", "mediahost")
    pub = P.PerfPublisher(roles=roles)
    pub._probe_blob = lambda: dict(FAKE_BLOB)   # stub the real probe

    fails = []

    # --- 1. boot publish() dual-writes capability for EVERY role to BOTH DBs ---------
    rep = pub.publish()
    cap_writes = [r for r in rec if r["var"] == "capability"]
    _check(len(cap_writes) == len(roles) * 2,
           f"publish: expected {len(roles)*2} capability writes, got {len(cap_writes)}", fails)
    for role in roles:
        dbhosts = {r["dbhost"] for r in cap_writes if r["role"] == role}
        # THE regression guard: old single-host code yields {CONTROL} only -> this FAILS.
        _check(dbhosts == {CONTROL, DATA},
               f"publish: role {role} written to {dbhosts}, expected both control+data", fails)
    # perf must ride inside the published blob (where the election reads it)
    _check(all("loadavg" in r["value"] for r in cap_writes),
           "publish: capability blob is missing perf (loadavg) inside it", fails)
    # refresh writes must be own=False (must outlive the call, age by ts)
    _check(all(r["own"] is False for r in cap_writes),
           "publish: capability writes must be own=False (refresh, not atexit-reaped)", fails)
    _check(rep.get("consistency") == "ok" and len(rep.get("written", [])) == len(roles) * 2,
           f"publish: report not ok/complete: {rep}", fails)

    # --- 2. hostReset() RE-PUBLISHES (immediate write on a float) --------------------
    rec.clear()
    pub.hostReset()
    cap2 = [r for r in rec if r["var"] == "capability"]
    for role in roles:
        dbhosts = {r["dbhost"] for r in cap2 if r["role"] == role}
        _check(dbhosts == {CONTROL, DATA},
               f"hostReset: role {role} re-published to {dbhosts}, expected both", fails)

    # --- 3. probe failure degrades cleanly, never raises -----------------------------
    rec.clear()
    pub._probe_blob = lambda: None
    rep3 = pub.hostReset()
    _check(rep3.get("consistency") == "probe_unavailable" and not rec,
           f"probe-fail: should write nothing and flag probe_unavailable, got {rep3}", fails)

    if fails:
        print("FAIL test_perf_publisher_oracle")
        for f in fails:
            print("  -", f)
        return 1
    print(f"PASS test_perf_publisher_oracle "
          f"(roles={roles}, both DBs on publish + hostReset, perf-inside-blob, own=False)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
