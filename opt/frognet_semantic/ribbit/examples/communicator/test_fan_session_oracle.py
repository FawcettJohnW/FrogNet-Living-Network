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
test_fan_session_oracle.py -- [FAN_IS_PER_SESSION_V1]

The relay fans a frame only to peers in the SAME CALL SESSION.

Measured 2026-08-10, both ends captured over the same six minutes: Dave
published session d7cf50bad296, John had joined a stale call row carrying
91b1b64561fa, both dialled the shared port :9000, and video flowed normally in
both directions. Meanwhile every session-scoped control tuple was addressed to
a session the other end was not in --

    [Dave] no viewer cap for session d7cf50bad296 -- 1 MediaSpeed row(s) exist
           but carry session(s) 91b1b64561fa.

-- so no cap and no commanded resolution ever crossed, while the pictures were
fine. The session id was decoration on the media plane and load-bearing on the
control plane, and that gap is where the instructions fell through.

  N1  the declaration carries plane, teardown tag, and call session, in that
      order, with the teardown tag fixed width so the two cannot be confused
  N2  a frame reaches a peer in the same session
  N3  a frame does NOT reach a peer in a different session
  N4  a connection that declared no call session receives nothing
  N5  ... and sends nothing: empty matches nothing, including another empty
  N6  the plane rule and the same-name rule still hold inside a session
  N7  teardown drops the call-session record with the rest
"""
import sys, threading

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

R = fnav.Relay if hasattr(fnav, "Relay") else None
if R is None:
    for _n in dir(fnav):
        _o = getattr(fnav, _n)
        if isinstance(_o, type) and hasattr(_o, "_fan") and hasattr(_o, "serve"):
            R = _o
            break
if R is None or not hasattr(R, "_fan"):
    print("  FAIL  no relay class with _fan()")
    sys.exit(1)


class Conn:
    """Stands in for a socket: records what the fan wrote to it."""
    def __init__(self, tag):
        self.tag = tag
        self.got = []
    def sendall(self, b):
        self.got.append(b)
    def send(self, b):
        self.got.append(b); return len(b)
    def fileno(self):
        return 1
    def setblocking(self, *_a):
        pass
    def __repr__(self):
        return "<Conn %s>" % self.tag


def relay(peers):
    """peers: list of (tag, name, plane, call_session)."""
    r = R.__new__(R)
    r.lock = threading.Lock()
    r.peers, r._plane, r._name_of, r._call_of = {}, {}, {}, {}
    r._session_of, r._planes_of = {}, {}
    # [FAN_IS_DRIVEN_NOT_RESTATED_V1] Mirror the real constructor rather than
    # guessing which fields _fan touches: an oracle that only fills in what the
    # current code happens to read breaks on the next edit for the wrong reason.
    # Every dict-valued attribute Relay.__init__ sets, empty. No caps and no
    # pending keyframes means _fan takes the plain forward path, so the only
    # thing selecting targets is the session/plane/name rule under test.
    import re as _re, inspect as _inspect
    _src = _inspect.getsource(R.__init__)
    for _a, _rhs in _re.findall(r"self\.(_[a-z_0-9]+)\s*=\s*([^\n]+)", _src):
        if hasattr(r, _a):
            continue
        _v = {}
        if _rhs.startswith(("time.time", "0.0", "0", "None", "False", "True")):
            _v = 0.0 if "time" in _rhs or "." in _rhs.split("#")[0] else 0
            if _rhs.startswith("None"):
                _v = None
            elif _rhs.startswith("False"):
                _v = False
            elif _rhs.startswith("True"):
                _v = True
        setattr(r, _a, _v)
    r._stop = threading.Event()
    conns = {}
    for tag, name, plane, cs in peers:
        c = Conn(tag)
        conns[tag] = c
        r.peers[c] = tag
        r._plane[c] = plane
        r._name_of[c] = name
        r._call_of[c] = cs
    return r, conns


def targets_of(r, sender):
    """Who ACTUALLY received, by running the shipped _fan().

    Deliberately not a re-implementation of the target rule: an oracle that
    restates the logic it is checking passes whether or not the code does.
    """
    for c in r.peers:
        c.got = []
    frame = fnav.pack_typed(fnav.KIND_VIDEO, r._name_of.get(sender, "x"),
                            fnav.pack_video(7, b"\x00" * 64, 0, is_key=True))
    r._fan(sender, frame)
    return sorted(r.peers[c] for c in r.peers if c.got)


V = fnav.PLANE_VIDEO
A = fnav.PLANE_AUDIO
S1, S2 = "d7cf50bad296", "91b1b64561fa"

# ---- N1: the declaration's shape ------------------------------------------
src = open("fnav.py", encoding="utf-8").read()
ck("N1 the client sends plane + teardown tag + call session",
   "tag + self.session_tag" in src and 'self.session or ""' in src)
ck("N1 the relay slices the teardown tag at a FIXED width",
   "self._session_of[conn] = bytes(_pl[1:9])" in src)
ck("N1 and takes the call session as the remainder",
   "self._call_of[conn] = bytes(_pl[9:])" in src)
ck("N1 the teardown tag is 8 bytes, so the slice is right",
   'uuid.uuid4().hex[:8].encode("ascii")' in src)

# ---- N2/N3 -----------------------------------------------------------------
r, c = relay([("dave", "Dave", V, S1), ("john", "John", V, S1),
              ("stranger", "Stranger", V, S2)])
t = targets_of(r, c["dave"])
ck("N2 a frame reaches a peer in the same session", "john" in t, t)
ck("N3 and does NOT reach a peer in a different session",
   "stranger" not in t, t)

t = targets_of(r, c["stranger"])
ck("N3 the other session is isolated in both directions", t == [], t)

# ---- N4/N5: no declared session -------------------------------------------
r, c = relay([("dave", "Dave", V, S1), ("mute", "Mute", V, "")])
ck("N4 an undeclared connection receives nothing",
   "mute" not in targets_of(r, c["dave"]), targets_of(r, c["dave"]))
ck("N5 and sends nothing", targets_of(r, c["mute"]) == [],
   targets_of(r, c["mute"]))

r, c = relay([("a", "A", V, ""), ("b", "B", V, "")])
ck("N5 two undeclared connections do NOT find each other",
   targets_of(r, c["a"]) == [] and targets_of(r, c["b"]) == [],
   (targets_of(r, c["a"]), targets_of(r, c["b"])))

# ---- N6: the existing rules survive ---------------------------------------
r, c = relay([("dave-v", "Dave", V, S1), ("john-v", "John", V, S1),
              ("john-a", "John", A, S1)])
t = targets_of(r, c["dave-v"])
ck("N6 the plane rule still holds inside a session",
   t == ["john-v"], t)

r, c = relay([("dave-v", "Dave", V, S1), ("dave-v2", "Dave", V, S1),
              ("john-v", "John", V, S1)])
t = targets_of(r, c["dave-v"])
ck("N6 a caller's own name is still excluded", "dave-v2" not in t, t)

# ---- N7 --------------------------------------------------------------------
ck("N7 teardown drops the call-session record",
   "self._call_of.pop(conn, None)" in src)

# ---- N8: [ALONE_IS_NOT_SILENT_V1] -----------------------------------------
# Discarding a lone sender's frames is correct. Discarding them quietly is not:
# TCP never pushes back because the relay drains the socket, so the ladder sees
# no drops, climbs to the top rung and sits there. Measured 2026-08-10, 374 KB/s
# of 1080p into a black hole with frames=0 coming back.
src_f = open("fnav.py", encoding="utf-8").read()
ck("N8 the relay names a sender with no peers in its session",
   "is ALONE in session" in src_f and "being DISCARDED" in src_f)
ck("N8 and reports the rate it is throwing away",
   "frame/s" in src_f and "nobody to fan to" in src_f)

# the client cannot tell a black hole from a healthy call except by the silence
ck("N8 the client warns when nothing has EVER arrived",
   "receiving NOTHING" in src_f and "_rx_ever" in src_f)
# The message spans two source lines, so a substring search across the raw text
# misses it. Join the literals before looking -- an assertion about what a
# program SAYS has to survive the program being formatted.
_joined = src_f.replace('" \n', "").replace('"\n', "")
_flat = " ".join(src_f.split())
ck("N8 and names the session so the mismatch is findable",
   "is anyone " in _flat and "else in it?" in _flat
   and "self.session or" in src_f)
ck("N8 the warning repeats on an interval rather than once",
   "RX_NONE_WARN_S" in src_f)

# a call that has received even one frame must never warn again: the interesting
# state is "never", not "not right now" -- a quiet moment is not a black hole.
# Anchored on the per-source loop rather than on the old aggregate check: the
# watcher now walks sources individually, and "any(counts.values())" no longer
# exists. What matters is unchanged -- one arrived frame silences it for good.
_i = src_f.index("if total > 0:")
ck("N8 one arrived frame silences it permanently",
   "self._rx_ever = True" in src_f[_i:_i + 200], None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
