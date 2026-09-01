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
sim/malformed_capability_guard_check.py - one corrupt peer blob must not zero a role.

Reproduces the live Seattle5 failure: SD:capability...:mediahost for 10.160.160.1 had a
clobbered blob (ts held a temps_c array instead of an int). get_all's int(ts) raised, the
eager T.get unwound, and gather's bare except returned ([],[]) -> SERVICE_ELECT mediahost
hosts=0 while three valid, fresh mediahost rows were discarded.

Guard proven here:
  1. get_all drops ONLY the malformed-ts row (logs [TUPLE] drop_malformed_ts) and never raises.
  2. T.get returns the valid rows.
  3. gather_candidates returns the valid hosts (NOT empty), so the role still elects.
"""
from __future__ import annotations
import os, sys, time

_HERE = os.path.abspath(__file__)
_FS = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))     # opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_FS))                      # work
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles", "communicator"))

import frognet_tuples as T
import frognet_role_elect as E

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

NOW = int(time.time())

def _blob(ip, ts):
    # a realistic capability blob; ffmpeg/libvpx/mysql true so it's role-eligible
    return {"lan_ip": ip, "ffmpeg": True, "libvpx": True, "mysql_running": True,
            "av_port": 9000, "loadavg": {"1": 0.1}, "temps_c": [{"temp_c": 50.6}], "ts": ts}

def _row(ip, ts, envelope=None):
    """[ENVELOPE_TS_V1] Rows carry an UpdatedAtEpoch. get_all no longer reads the
    payload's own ts -- freshness is envelope information, stamped by the store
    on its own clock -- so a row without one is dropped outright when fresh_s is
    asked for, and this fixture returned nothing at all until the envelope was
    added. `ts` stays in the payload because the POISON lives there and the
    point below is that it is now inert.

    envelope=None means "no UpdatedAtEpoch at all", used to exercise the drop.
    """
    b = _blob(ip, ts)
    r = {"SensorName": f"SD:capability.host:{ip}:mediahost",
         "SensorType": "mediahost", "SensorAddress": ip, "data": b, "jsonData": ""}
    if envelope is not None:
        r["UpdatedAtEpoch"] = envelope
    return r

# The poison: ts is a temps_c-shaped LIST, exactly like the live 10.160.160.1 blob.
# It is kept deliberately. [ENVELOPE_TS_V1] moved freshness off the payload, so
# get_all never calls int() on data["ts"] any more and this blob can no longer
# raise -- which is the property checked below. The row that CAN still be
# unreadable is one whose `data` is not a dict at all, so one of those is added.
ROWS = [
    _row("10.250.250.1", NOW - 55, envelope=NOW - 55),
    _row("10.130.130.1", NOW - 27, envelope=NOW - 27),
    _row("10.160.160.1", [{"name": "thermal_zone0", "temp_c": 57.3}],
         envelope=NOW - 31),                                          # corrupt payload ts
    _row("10.120.120.1", NOW - 34, envelope=NOW - 34),
]
# jsonData that will not parse -> data is None -> get_all must skip it silently.
_UNREADABLE = {"SensorName": "SD:capability.host:10.99.99.1:mediahost",
               "SensorType": "mediahost", "SensorAddress": "10.99.99.1",
               "data": None, "jsonData": "{not json", "UpdatedAtEpoch": NOW - 12}

def _patched_values_raw(service, dbhost="x", name_like=None, timeout=4.0, **kw):
    """**kw on purpose. _values_raw grew fresh_s and the stub did not, so every
    call through T.get raised TypeError -- which this check then read as "the
    guard raised on a malformed row", blaming the code under test for the
    fixture's own signature drift. Nothing here varies with the extra keywords;
    the corrupt set is returned whatever the caller asks for."""
    return [dict(r) for r in ROWS] + [dict(_UNREADABLE)]

def run():
    T._values_raw = _patched_values_raw            # feed the corrupt set
    # [LAN_IS_UNTUNNELLED_V1] island: every real host is LAN. This used to stub
    # E._wan_subnets, which no longer decides the split -- gather_candidates asks
    # is_on_lan() per candidate, which traceroutes. Stubbing the retired function
    # left every candidate off the LAN list and read as a guard failure.
    E._wan_subnets = lambda dbhost: set()
    E.is_on_lan = lambda ip, local_ips=None, hops=None: True
    E._is_real_host = lambda ip: bool(ip) and not ip.endswith(".253")

    # 1. get_all: the corrupt PAYLOAD ts is now inert, and an unreadable row is
    #    skipped without raising.
    #
    #    The original poison -- data["ts"] holding a temps_c list -- killed
    #    get_all when freshness was computed locally from the payload. It cannot
    #    now: [ENVELOPE_TS_V1] reads UpdatedAtEpoch and states that a payload's
    #    own ts "is NOT considered here". So the corrupt row comes back with the
    #    others rather than being dropped, and the check is that it does no harm.
    #    The row that can still be unreadable is one whose data is not a dict.
    probs = []
    try:
        got = T.get("mediahost", "capability", dbhost="x", fresh_s=180)
        ips = sorted((r["value"].get("lan_ip") for r in got))
        want = ["10.120.120.1", "10.130.130.1", "10.160.160.1", "10.250.250.1"]
        if ips != want:
            probs.append(f"T.get returned {ips}, want {want} "
                         "(corrupt payload ts is inert; unreadable row skipped)")
        if any(r["value"].get("lan_ip") == "10.99.99.1" for r in got):
            probs.append("the unparseable row was returned; data is not a dict")
    except Exception as e:
        probs.append(f"T.get RAISED on a malformed row (must not): {e!r}")
    check("[GUARD] get_all survives a corrupt blob: bad payload ts is inert, "
          "unreadable row skipped, nothing raises", probs)

    # 2. gather_candidates: NOT zero - the role still has candidates
    #
    #    [CAPABILITY_DOES_NOT_AGE_V1]: "A missing envelope no longer disqualifies
    #    anything -- refusing a ballot for having no timestamp is an age
    #    judgement by another name." So the corrupt-ts row is a candidate too,
    #    and the count is 4. What this check exists for is unchanged and is the
    #    part that matters: one bad blob must not return ([],[]).
    probs = []
    class _H:  ROLE_NAME = "mediahost"
    try:
        hosts, lan = E.gather_candidates(_H(), dbhost="x")
        if len(hosts) != 4:
            probs.append(f"hosts_list={len(hosts)} (want 4); corrupt row must not zero the role")
        if len(lan) != 4:
            probs.append(f"lan_list={len(lan)} (want 4)")
    except Exception as e:
        probs.append(f"gather_candidates RAISED: {e!r}")
    check("[ELECT] gather yields every valid candidate (was 0 before the guard)", probs)

    # 3. regression: an ALL-valid set is unaffected
    probs = []
    good = [_row("10.1.1.1", NOW - 5, envelope=NOW - 5),
            _row("10.2.2.2", NOW - 9, envelope=NOW - 9)]
    T._values_raw = lambda *a, **k: [dict(r) for r in good]
    try:
        got = T.get("mediahost", "capability", dbhost="x", fresh_s=180)
        if len(got) != 2:
            probs.append(f"clean set returned {len(got)} (want 2)")
    except Exception as e:
        probs.append(f"clean set RAISED: {e!r}")
    check("[REGRESS] a clean capability set is unchanged by the guard", probs)

def main():
    print("=== malformed capability blob must not zero a role ===")
    run()
    print("\n" + ("ALL MALFORMED-GUARD CHECKS PASS" if not FAILS
                  else f"MALFORMED-GUARD CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
