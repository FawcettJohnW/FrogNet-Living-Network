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
sim_whole_call.py -- the whole thing, as one program with threads.

Not a unit test. A publisher, a viewer and a relay, all real code, all sharing
one real tuple store over HTTP, running concurrently the way they do on the
pond. The only things faked are the database (a dict behind an api.php-shaped
server) and the camera (a frame generator).

It exists because every failure of the last two days was a SYSTEM failure that
every unit test passed through: two controllers moving one knob, a role stamp
that erased a fact, an announce that never happened. None of those are visible
in a test of one function. All of them are visible here.

What success looks like, and what this asserts:

  W1  the publisher's call appears in the viewer's lobby
  W2  the viewer can join it
  W3  both ends carry frames, both directions
  W4  every end derives the SAME rate from the same rows
  W5  that rate is stable on a healthy link -- it does not walk to the floor
  W6  a genuinely slow viewer moves it down, and only as far as it needs
  W7  nobody waits on anybody: each end's loop runs at its own pace
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
sys.path.insert(0, HERE)
sys.path.insert(0, "/home/claude/src2/opt/frognet_semantic")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


# ---- the store -------------------------------------------------------------
ROWS, NEXT_ID, LOCK = {}, [1], threading.Lock()


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, o):
        b = json.dumps(o).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        q = json.loads(self.rfile.read(n).decode() or "{}")
        with LOCK:
            name = q.get("SensorName")
            row = ROWS.get(name)
            if row is None:
                row = ROWS[name] = {"SensorID": NEXT_ID[0], "SensorName": name}
                NEXT_ID[0] += 1
            row.update(SensorType=q.get("SensorType"),
                       SensorAddress=q.get("SensorAddress"),
                       jsonData=json.dumps(q.get("jsonData")),
                       UpdatedAtEpoch=int(time.time()))
        self._send({"ok": True})

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        p = parse_qs(urlparse(self.path).query)
        svc = (p.get("SensorType") or [None])[0]
        fresh = int((p.get("fresh_s") or [0])[0])
        now = int(time.time())
        with LOCK:
            out = [dict(r) for r in ROWS.values()
                   if (not svc or r.get("SensorType") == svc)
                   and not (fresh and (now - r["UpdatedAtEpoch"]) > fresh)]
        self._send({"rows": out})


srv = HTTPServer(("127.0.0.1", 0), Api)
threading.Thread(target=srv.serve_forever, daemon=True).start()
DB = "127.0.0.1:%d" % srv.server_port

from core import frognet_tuples as T          # noqa: E402
import comms_control as CC                    # noqa: E402

CC.T = T
T.my_ip = lambda: "10.0.0.1"
RELAY = ("10.160.160.1", 9000)


def plane(name):
    cp = CC.ControlPlane(name.lower() + "-x", name, dbhost=DB)
    cp._resolve_media = lambda: RELAY
    return cp


# ---- W1/W2: the lobby ------------------------------------------------------
print("-- the lobby")
dave = plane("Dave")
john = plane("John")

call = dave.start_call()
sid = call["session"]

seen = john.list_calls()
ck("W1 the publisher's call appears in the viewer's lobby",
   [c["session"] for c in seen] == [sid], seen)
ck("W1 with somewhere to connect",
   seen and seen[0]["host"] == RELAY[0], seen)
ck("W1 and the publisher listed on it",
   seen and seen[0]["members"] == ["Dave"], seen and seen[0]["members"])

joined = john.join_call(sid)
ck("W2 the viewer can join it", joined and joined["session"] == sid, joined)
ck("W2 and both are then on it",
   sorted(dave.list_calls()[0]["members"]) == ["Dave", "John"],
   dave.list_calls()[0]["members"])

# the heartbeat keeps it there -- a call that vanishes between polls is not
# joinable in practice however good the first read was
for _ in range(3):
    dave.refresh_calls()
    john.refresh_calls()
ck("W2 and it survives the heartbeat",
   [c["session"] for c in john.list_calls()] == [sid], john.list_calls())


# ---- W4..W7: the rate, derived concurrently --------------------------------
print("\n-- the rate, three ends deriving concurrently")
LADDER = [{"w": 1920, "h": 1080}, {"w": 1280, "h": 720}, {"w": 854, "h": 480},
          {"w": 640, "h": 360}, {"w": 480, "h": 360}, {"w": 320, "h": 240},
          {"w": 160, "h": 120}]


def idx_of(g):
    px = g["w"] * g["h"]
    for n, x in enumerate(LADDER):
        if x["w"] * x["h"] <= px:
            return n
    return len(LADDER) - 1


def derive(cp, session, cur_idx):
    """The rate, from the rows alone.

    [THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1] cur_idx is not consulted. It was,
    and that made the answer a function of each end's HISTORY -- two ends at
    854x480 and one at 640x360, all reading the same rows.
    """
    g = cp.call_rate(session, LADDER)
    return idx_of(g) if g else cur_idx


class End(threading.Thread):
    """One participant, running its own loop at its own pace."""

    def __init__(self, name, healthy=True, sends=True, capacity=None):
        super().__init__(daemon=True)
        # pixels per second this end's link can actually deliver
        self.capacity = capacity or (854 * 480 * 24.0)
        self.cp = plane(name)
        self.name = name
        self.healthy = healthy
        self.sends = sends
        self.idx = 0
        self.history = []
        self.stop = threading.Event()
        self.cp.join_call_at(sid, *RELAY)

    def run(self):
        good = 0
        while not self.stop.is_set():
            try:
                if self.sends:
                    g = LADDER[self.idx]
                    self.cp.report_sending(sid, g["w"], g["h"], 24.0)
                # What this end is getting, modelled PHYSICALLY: a link carries
                # so many pixels a second, so a smaller picture arrives faster.
                #
                # The first version of this fixture gave the slow end 7 fps
                # whatever the size, which is not how a link behaves -- and
                # under that model stepping down can never help, so of course it
                # walked to the floor. A fixture that cannot be satisfied proves
                # nothing about the loop.
                g = LADDER[self.idx]
                _px = g["w"] * g["h"]
                if self.healthy:
                    fps = 23.5
                else:
                    # enough for about 854x480 at 24, no more
                    fps = min(23.5, self.capacity / float(_px))
                good = good + 1 if fps >= 24.0 * 0.8 else 0
                self.cp.report_receiving(sid, g["w"], g["h"], fps,
                                         good >= 3, fps < 24.0 * 0.8)
                self.idx = derive(self.cp, sid, self.idx)
                self.history.append(self.idx)
            except Exception as e:
                print("   %s loop error: %r" % (self.name, e))
            self.stop.wait(0.12)


# -- healthy pond: two good ends ---------------------------------------------
ends = [End("Dave"), End("John")]
for e in ends:
    e.start()
time.sleep(2.5)
for e in ends:
    e.stop.set()
for e in ends:
    e.join(timeout=2)

settled = [e.idx for e in ends]
ck("W4 every end derives the SAME rate", len(set(settled)) == 1, settled)
ck("W5 a healthy pond does not walk to the floor",
   all(i < len(LADDER) - 1 for i in settled),
   [LADDER[i] for i in settled])
ck("W5 and it settles rather than oscillating",
   all(len(set(e.history[-6:])) == 1 for e in ends),
   [e.history[-6:] for e in ends])
ck("W7 each end ran its own loop", all(len(e.history) > 5 for e in ends),
   [len(e.history) for e in ends])

# -- one genuinely slow viewer -----------------------------------------------
print("\n-- one slow viewer joins")
for k in list(ROWS):
    if "CallsJoined" in k:
        with LOCK:
            ROWS.pop(k, None)

ends = [End("Dave"), End("John"),
        End("Sue", healthy=False, sends=False)]   # her link tops out at 854x480
for e in ends:
    e.start()
time.sleep(3.0)
for e in ends:
    e.stop.set()
for e in ends:
    e.join(timeout=2)

settled = [e.idx for e in ends]
ck("W6 a slow viewer moves the rate down",
   all(i > 0 for i in settled), [LADDER[i] for i in settled])
ck("W6 and stops where that viewer can actually keep up",
   all(LADDER[i]["w"] <= 854 for i in settled), [LADDER[i] for i in settled])
ck("W6 every end still agrees on where", len(set(settled)) == 1, settled)
ck("W6 and it does not collapse to the floor",
   all(i < len(LADDER) - 1 for i in settled),
   [LADDER[i] for i in settled])


# ---- W8: equilibrium, both ends together -----------------------------------
# A high-speed pond runs at the top; a constrained one runs at the bottom; and
# BOTH ENDS move at the same time, because both compute the same number from
# the same rows rather than telling each other anything.
print("\n-- equilibrium at each link speed")

for k in list(ROWS):
    if "CallsJoined" in k:
        with LOCK:
            ROWS.pop(k, None)


def settle(capacity, label, seconds=3.0):
    for k in list(ROWS):
        if "CallsJoined" in k:
            with LOCK:
                ROWS.pop(k, None)
    es = [End("Dave", healthy=False, capacity=capacity),
          End("John", healthy=False, capacity=capacity)]
    for e in es:
        e.start()
    time.sleep(seconds)
    for e in es:
        e.stop.set()
    for e in es:
        e.join(timeout=2)
    got = [LADDER[e.idx] for e in es]
    same = len({(g["w"], g["h"]) for g in got}) == 1
    print("   %-14s -> %s%s" % (label,
                                ", ".join("%dx%d" % (g["w"], g["h"]) for g in got),
                                "" if same else "   <- DISAGREE"))
    return got, same


_fast, _same_fast = settle(1920 * 1080 * 30.0, "fast link")
ck("W8 a fast link settles at the top", _fast[0]["w"] == 1920, _fast)
ck("W8 with both ends together", _same_fast, _fast)

_slow, _same_slow = settle(320 * 240 * 24.0, "constrained link")
ck("W8 a constrained link settles at the bottom", _slow[0]["w"] <= 320, _slow)
ck("W8 with both ends together", _same_slow, _slow)

_mid, _same_mid = settle(854 * 480 * 24.0, "middling link")
ck("W8 a middling link settles in the middle",
   320 < _mid[0]["w"] <= 854, _mid)
ck("W8 with both ends together", _same_mid, _mid)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
