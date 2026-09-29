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
test_call_reap_oracle.py -- [RELAY_REAPS_DEAD_CALLS_V1]

The relay deletes call rows for sessions with nobody connected to it.

A `call` row is an ASSERTION: a client writes it and never withdraws it if that
client dies, is killed, or loses the link. Nothing reaped them. Observed
2026-08-09 in one snapshot: 25 calls in progress, one real, several sessions
duplicated across writers, most with a single member or none. Observed
2026-08-10: a client picked a dead one out of that list, joined it, and got a
working picture with a control plane addressed to a session nobody was in.

[FAN_IS_PER_SESSION_V1] makes joining a dead session harmless. This makes it
rare. Both halves are needed: a row nobody can join is still a row in every
roster.

  R1  the relay knows its live sessions from its own sockets, not from a tuple
  R2  a session with a live connection is NOT reaped
  R3  a session with no connection here IS reaped
  R4  a row belonging to ANOTHER media host is never touched
  R5  a row younger than the grace period is left alone -- it is written before
      the caller dials
  R6  a malformed row (no session) is skipped, not crashed on
  R7  reaping never takes the relay down and never fails silently
"""
import sys, threading, time

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

R = fnav.Relay
if not hasattr(R, "_reap_dead_calls"):
    print("  FAIL  the relay does not reap call rows")
    print("\nFAILED: dead call rows accumulate and clients join them.")
    sys.exit(1)

ME = "10.160.160.1"
OTHER = "10.28.28.1"


class Conn:
    def __init__(self, tag):
        self.tag = tag


def relay(sessions):
    r = R.__new__(R)
    r.lock = threading.Lock()
    r.peers, r._call_of = {}, {}
    r._stop = threading.Event()
    for i, s in enumerate(sessions):
        c = Conn(i)
        r.peers[c] = "peer%d" % i
        r._call_of[c] = s
    return r


# ---- R1 --------------------------------------------------------------------
r = relay(["live1", "live1", "live2", ""])
ck("R1 live sessions come from the sockets", r._live_sessions() == {"live1", "live2"},
   r._live_sessions())
ck("R1 an undeclared connection contributes no session",
   "" not in r._live_sessions(), r._live_sessions())
ck("R1 a relay with no peers has no live sessions",
   relay([])._live_sessions() == set(), relay([])._live_sessions())


# ---- R2..R6: the selection, driven through the real loop -------------------
# A fake tuple module: the reaper reads `call` rows, then walks the raw rows to
# delete by id. Both are recorded so the oracle can see exactly what it chose.
class FakeT:
    SD_PREFIX = "SD:"
    DEFAULT_DBHOST = "db"

    def __init__(self, rows):
        self.rows = rows
        self.deleted = []

    def my_ip(self):
        return ME

    def get(self, service, var, fresh_s=0, **kw):
        return [{"value": v} for v in self.rows]

    def _values_raw(self, service, dbhost, **kw):
        return [{"SensorName": "SD:call.%s" % v.get("session"),
                 "SensorID": i, "data": v} for i, v in enumerate(self.rows)]

    def _delete_by_id(self, dbhost, sid):
        # A real store REMOVES the row. Recording the delete without removing it
        # made the reaper re-select the same rows on every pass, and the oracle
        # measured its own harness -- 24 identical deletions in a quarter second
        # rather than the one the code performs.
        self.deleted.append(sid)
        self.rows[sid] = {}
        return True


def run_once(rows, live_sessions):
    """One pass of the real _reap_dead_calls, then stop it."""
    before = [dict(v) for v in rows]
    fake = FakeT([dict(v) for v in rows])
    sys.modules["frognet_tuples"] = fake
    r = relay(live_sessions)
    r.REAP_EVERY_S = 0.01
    t = threading.Thread(target=r._reap_dead_calls, daemon=True)
    t.start()
    time.sleep(0.25)
    r._stop.set()
    t.join(timeout=2.0)
    # rows[] is mutated by the delete, so read the session off a snapshot taken
    # before the run.
    return fake, [before[i].get("session") for i in fake.deleted]


now = int(time.time())
OLD = now - 3600
NEW = now - 1

rows = [
    {"session": "live", "host": ME, "ts": OLD, "members": ["Dave"]},
    {"session": "dead", "host": ME, "ts": OLD, "members": ["Ghost"]},
    {"session": "theirs", "host": OTHER, "ts": OLD, "members": ["Someone"]},
    {"session": "fresh", "host": ME, "ts": NEW, "members": []},
    {"host": ME, "ts": OLD, "members": []},                  # no session
]
fake, killed = run_once(rows, ["live"])

ck("R2 a session with a live connection is not reaped", "live" not in killed, killed)
ck("R3 a session with no connection here is reaped", "dead" in killed, killed)
ck("R4 another media host's row is never touched", "theirs" not in killed, killed)
ck("R5 a row inside the grace period is left alone", "fresh" not in killed, killed)
ck("R6 a row with no session is skipped", None not in killed, killed)
ck("R3 exactly one row went", killed == ["dead"], killed)

# nothing live at all: every old row of ours goes, nobody else's does
fake, killed = run_once(rows, [])
ck("R3 with no live sessions, all of OUR stale rows go",
   sorted(killed) == ["dead", "live"], killed)
ck("R4 and still none of theirs", "theirs" not in killed, killed)


# ---- R7: failure is loud and survivable ------------------------------------
class Exploding(FakeT):
    def get(self, *a, **kw):
        raise RuntimeError("tuple store down")

sys.modules["frognet_tuples"] = Exploding([])
r = relay(["live"])
r.REAP_EVERY_S = 0.01
t = threading.Thread(target=r._reap_dead_calls, daemon=True)
t.start()
time.sleep(0.2)
alive = t.is_alive()
r._stop.set()
t.join(timeout=2.0)
ck("R7 a failing store does not kill the reaper thread", alive)

src = open("fnav.py", encoding="utf-8").read()
ck("R7 and the failure is printed rather than swallowed",
   "call reap failed" in src)
ck("R7 the grace period is at least two poll intervals",
   R.REAP_GRACE_S >= 2 * R.REAP_EVERY_S, (R.REAP_GRACE_S, R.REAP_EVERY_S))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
