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
frognet_call_host.py - the FrogNet call mediahost SERVICE.

Start it ONCE on the mediahost box. No --session. It watches the tuple space for ANY
call's create-stream, spins up a per-session worker to mix+fan that call, and reaps
workers when the call ends. This is the always-on service; the per-session shim is gone.

    # run once on the box mediahost.frognet resolves to:
    python3 frognet_call_host.py
    # or via systemd (see frognet-call-host.service)

Discovery (no SID needed): clients write CTL_CREATE under service 'mediastream' at scope
session:<sid>. get('mediastream','create', fresh_s=N) returns EVERY live session's create
tuple; the scope yields the sid, and the matching 'communicator'/'call' tuples give the
participant roster. A session whose create tuple goes stale (not re-asserted) or whose call
tuple reads state=='ended' is reaped.

Each session gets its own port pair (base + 2*slot) so concurrent calls don't collide.

STILL BOX-SIDE (not sim-provable): real socket bind/listen, the live FIFO mix feed, the
per-destination dial-back. The DISCOVERY + lifecycle (start worker on create, reap on end,
roster from participants) is sim-proven (test_call_host_service_loop_oracle.py).
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Dict, List, Optional, Tuple

import frognet_tuples as T
from media_stream import TupleControl
import call_host_service as HS

try:
    from frognet_mediahost import compute_plan
except Exception:
    compute_plan = None

MEDIASTREAM = "mediastream"     # where clients write create tuples
CREATE_VAR = "create"
CALL_SERVICE = "communicator"   # where the originator writes call/participant tuples
CALL_VAR = "call"

PORT_BASE = 9101                # session slot 0 -> tx 9101 / rx 9102, slot 1 -> 9103/9104 ...
FRESH_S = 30                    # a create tuple older than this (no re-assert) = call gone


class TupleBackend:
    """Adapt frognet_tuples (put/get, no get_one) to the TupleControl contract."""
    def __init__(self, dbhost: str):
        self.dbhost = dbhost
    def put(self, service, var, scope, value, addr=None):
        return T.put(service, var, scope, value, dbhost=self.dbhost)
    def get(self, service, var, dbhost=None, fresh_s=0):
        return T.get(service, var, dbhost=self.dbhost, fresh_s=fresh_s)
    def get_one(self, service, var, scope, fresh_s=0):
        for r in T.get(service, var, dbhost=self.dbhost, fresh_s=fresh_s):
            if r.get("scope") == scope:
                return r.get("value")
        return None


def discover_sessions(backend: "TupleBackend", fresh_s: int = FRESH_S) -> List[str]:
    """Every live call session = scope of a fresh create tuple under mediastream."""
    sids = []
    for r in backend.get(MEDIASTREAM, CREATE_VAR, fresh_s=fresh_s):
        scope = r.get("scope", "")
        if scope.startswith("session:"):
            sid = scope.split(":", 1)[1]
            if sid and sid not in sids:
                sids.append(sid)
    return sids


def participants_for(backend: "TupleBackend", sid: str) -> List[str]:
    """Roster the host mixes: the participant set the originator published in the call
    tuple(s) for this session. (Falls back to empty -> worker waits.)"""
    seen: List[str] = []
    ended = False
    for r in backend.get(CALL_SERVICE, CALL_VAR):
        v = r.get("value", {})
        scope = r.get("scope", "")
        if v.get("sid") != sid and not scope.startswith(f"session:{sid}"):
            continue
        if v.get("state") == "ended":
            ended = True
        for p in (v.get("participants") or []):
            if p not in seen:
                seen.append(p)
    return [] if ended else seen


def plan_from_participants(participants: List[str], level_idx: int = 6) -> dict:
    if compute_plan is not None:
        streams = [{"type": "stream", "session": "_", "who": p} for p in participants]
        watchers = [{"type": "watch", "session": "_", "who": p, "ladder_level": level_idx}
                    for p in participants]
        return compute_plan(streams, watchers)
    return {"recipients": {p: {"recipient": p, "level": level_idx} for p in participants},
            "demanded_levels": [level_idx], "sources": list(participants)}


class SessionWorker:
    """One running call. Owns a CallHostService bound to this session's port pair."""
    def __init__(self, sid: str, backend: "TupleBackend", addr: str, slot: int):
        self.sid = sid
        self.tx = PORT_BASE + 2 * slot
        self.rx = self.tx + 1
        self.control = TupleControl(backend, sid, addr=addr)
        self.svc = HS.CallHostService(sid, self.control, addr=addr,
                                      tx_port=self.tx, rx_port=self.rx)
        self.backend = backend
        self.created = False

    def tick(self):
        if self.svc.maybe_create():
            if not self.created:
                print(f"[call_host] session={self.sid} conn_info published "
                      f"tx={self.tx} rx={self.rx}", flush=True)
                self.created = True
            roster = participants_for(self.backend, self.sid)
            if roster:
                plan = plan_from_participants(roster)
                for who in plan.get("sources", []):
                    if who not in self.svc.sources:
                        self.svc.add_source(who, "0.0.0.0", self.tx)
                        print(f"[call_host] session={self.sid} +source {who}", flush=True)
                self.svc.set_plan(plan)
                self.svc.apply_backpressure()

    def close(self):
        self.svc.close()
        print(f"[call_host] session={self.sid} reaped", flush=True)


def run(addr: str, dbhost: str, interval: float = 1.0, fresh_s: int = FRESH_S):
    backend = TupleBackend(dbhost)
    workers: Dict[str, SessionWorker] = {}
    slots: Dict[str, int] = {}
    print(f"[call_host] SERVICE up addr={addr} dbhost={dbhost} - watching for calls",
          flush=True)
    while True:
        try:
            live = set(discover_sessions(backend, fresh_s))
            # start workers for new sessions
            for sid in live:
                if sid not in workers:
                    slot = next(i for i in range(256) if i not in slots.values())
                    slots[sid] = slot
                    workers[sid] = SessionWorker(sid, backend, addr, slot)
                    print(f"[call_host] session={sid} NEW -> worker slot {slot}", flush=True)
            # drive live workers; reap ended/stale ones
            for sid in list(workers):
                if sid in live and participants_for(backend, sid) != []:
                    workers[sid].tick()
                elif sid not in live or participants_for(backend, sid) == []:
                    workers.pop(sid).close()
                    slots.pop(sid, None)
        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write(f"[call_host] loop error: {e!r}\n")
        time.sleep(interval)
    for w in workers.values():
        w.close()
    print("[call_host] SERVICE stopped", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="FrogNet call mediahost SERVICE (watches all calls)")
    ap.add_argument("--addr", default="mediahost.frognet",
                    help="address to publish in conn_info (default mediahost.frognet)")
    ap.add_argument("--dbhost", default="databasehost_control.frognet")
    args = ap.parse_args(argv)
    run(args.addr, args.dbhost)


if __name__ == "__main__":
    main()
