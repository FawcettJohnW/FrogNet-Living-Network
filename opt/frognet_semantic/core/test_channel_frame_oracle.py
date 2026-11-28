#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/test_channel_frame_oracle.py -- what core/channel_frame.py must do, checked on a real connection between two
ends in one process. Each end has its OWN copy of the settings, as two machines do, so a check can leave one end
behind the other. It is the mechanism's own check; that it works between machines is the Roles exercise tier's.

Real: channel_frame.Link at both ends (its writer thread, its reading, its calls), a socket pair, the algorithms.
Substituted: the settings (a dict for each end, changed by this file as a held read would change it), the secrets (a
directory for each end) and each end's FNID.

Nothing here waits by asking again: each end's reader hands what it opens to a queue, and a check takes from the queue.

    python3 core/test_channel_frame_oracle.py
"""
import copy
import hashlib
import os
import queue
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import channel_frame as F  # noqa: E402
from core import chorus as _chorus  # noqa: E402,F401  (registers the protocol "chorus")
from core import openqasm as _openqasm  # noqa: E402

_openqasm.register()

FAILED = []
NOTHING_S = 0.4          # how long "nothing arrives" is given, where a check says nothing must


def check(ok, what):
    print("  [%s] %s" % ("PASS" if ok else "FAIL", what), flush=True)
    if not ok:
        FAILED.append(what)


class Tap:
    """A socket that keeps what one end put on the connection: what somebody on the link would have."""
    def __init__(self, sock):
        self._s, self.seen = sock, bytearray()

    def sendall(self, b):
        self.seen += b
        return self._s.sendall(b)

    def __getattr__(self, n):
        return getattr(self._s, n)


def secrets(roles, salt=""):
    d = tempfile.mkdtemp(prefix="channel-secrets-")
    for r in roles:
        with open(os.path.join(d, r), "wb") as f:
            f.write(hashlib.sha256(("%sthe secret of %s" % (salt, r)).encode()).digest())
    return d


class End:
    """One end: its Link, its reader thread, what it has taken, what it said."""
    def __init__(self, sock, opener, settings, secret_dir, fnid, far):
        self.settings, self.taken, self.said, self.why = settings, queue.Queue(), [], None
        self.secret_dir = secret_dir            # a check sets this to stand for the secret being set again by hand

        def step(r):
            held = F.read_secret(r, self.secret_dir)
            return held[1] if held[2] else None
        self.link = F.Link(sock, (far, 1), opener, lambda r: self.settings.get(r),
                           deliver=lambda *m: self.taken.put(m), secret=lambda r: F.secret_of(r, self.secret_dir),
                           say=self.said.append, fnid=fnid, step=step)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            self.link.pump()
        except Exception as e:
            self.why = str(e)

    def take(self, n=1, wait_s=5.0):
        """The next n messages this end took, in order ([] short if they do not come)."""
        out = []
        for _ in range(n):
            try:
                out.append(self.taken.get(timeout=wait_s))
            except queue.Empty:
                break
        return out

    def nothing(self):
        try:
            return [self.taken.get(timeout=NOTHING_S)]
        except queue.Empty:
            return []

    def set(self, role, **changes):
        """This end's held read on the Role's setting woke with a new value."""
        self.settings[role] = dict(self.settings.get(role) or {}, **changes)
        self.link.setting_changed(role)


def pair(set_a, set_b, dir_a, dir_b):
    a, b = socket.socketpair()
    tap = Tap(a)
    A = End(tap, True, set_a, dir_a, "fnid-of-node-a", "b")
    B = End(b, False, set_b, dir_b, "fnid-of-node-b", "a")
    return A, B, tap


def texts(msgs):
    return [m[4] for m in msgs]


def main():
    print("ORACLE channel frame (%s)" % F.BUILD)
    S = secrets(["assessment", "units", "warning"])
    sec = hashlib.sha256(b"the secret of assessment").digest()

    # ---- the key
    k = F.key_for(sec, "fnid-of-node-a", "assessment", 7001)
    check(len(k) == 32 and k == F.key_for(sec, "fnid-of-node-a", "assessment", 7001),
          "the key is computed, and the same four values give the same 32 bytes every time")
    others = {F.key_for(sec, "fnid-of-node-b", "assessment", 7001), F.key_for(sec, "fnid-of-node-a", "units", 7001),
              F.key_for(sec, "fnid-of-node-a", "assessment", 7002), F.key_for(hashlib.sha256(b"x").digest(), "fnid-of-node-a", "assessment", 7001),
              }
    check(k not in others and len(others) == 4,
          "another node, another Role, another number or another secret gives another key")
    # [KEY_AND_CHECK_USE_SHA_384_V1] the computation, written out here a second time, independently of core/channel_frame.py
    import hmac as _hmac
    want = _hmac.new(sec, b"frognet channel key\nfnid-of-node-a\nassessment\n7001", hashlib.sha384).digest()[:32]
    check(k == want, "the key is the first 32 bytes of HMAC-SHA384(secret, the sender's FNID, the Role's name, the number)")

    # ---- not sealed, and sealed
    base = {"warning": {"driver": "tcp"}, "assessment": {"driver": "tcp", "number": 7001}, "units": {"driver": "chorus", "number": 9001}}
    A, B, tap = pair(copy.deepcopy(base), copy.deepcopy(base), S, S)
    A.link.submit("warning", b"plain words"); A.link.settle()
    got = B.take()
    named = len(tap.seen)
    A.link.submit("warning", b"plain words"); A.link.settle()
    check(texts(got) == [b"plain words"] and len(tap.seen) - named == 2 + 4 + len(b"plain words") and b"plain words" in tap.seen,
          "a Role with no number is not sealed: a message costs 2 bytes and the protocol's word")
    B.take()
    was = len(tap.seen)
    A.link.submit("assessment", b"secret words"); A.link.settle()
    first = len(tap.seen)
    A.link.submit("assessment", b"secret words"); A.link.settle()
    got = B.take(2)
    check(texts(got) == [b"secret words"] * 2 and b"secret words" not in tap.seen[was:]
          and len(tap.seen) - first == 2 + 12 + 16 + 4 + len(b"secret words"),
          "a Role with a number is sealed under the key computed from it: 28 bytes more, and the words are not on the connection")
    check(b"fnid-of-node-a" in tap.seen[was:first] and b"assessment" in tap.seen[was:first],
          "the first frame of the Role's sequence states the sending node's FNID and the number, once")

    # ---- replies come back under the replying node's own key
    B.link.submit_reply("assessment", got[0][1], got[0][2], b"an answer")
    r = A.take()
    kb = F.key_for(sec, "fnid-of-node-b", "assessment", 7001)
    check(texts(r) == [b"an answer"] and r[0][3] is True and A.link._in[got[0][1]].key == kb != B.link._in[got[0][1]].key,
          "each direction is under its sender's key: node b's replies under the key of node b's FNID")

    # ---- two Roles, one connection
    A.link.submit("units", b"for units"); A.link.submit("assessment", b"for assessment")
    got = B.take(2)
    check([(m[0], m[4]) for m in got] == [("units", b"for units"), ("assessment", b"for assessment")]
          and B.link._in[got[0][1]].key != B.link._in[got[1][1]].key,
          "two Roles with different keys share the connection, told apart by the packet ID")

    # ---- a switch the receiver already knows of
    B.set("assessment", number=7002)
    A.link.submit("assessment", b"before"); A.link.settle()
    A.set("assessment", number=7002)
    A.link.submit("assessment", b"after"); A.link.settle()
    got = B.take(2)
    check(texts(got) == [b"before", b"after"] and B.link.not_opened == 0 and A.link.calls["encryption"][0] == 1,
          "a key switch: the number changed, one call in the stream; the receiver's setting had it already and every frame opens")
    check(B.link._in[got[0][1]].key == F.key_for(sec, "fnid-of-node-a", "assessment", 7002),
          "after it the receiver holds the key for the new number and nothing of the old one")

    # ---- "did it change", not "one more": any number, and many changes are one switch
    for n in (5, 123456789012345, 42):
        B.settings["assessment"]["number"] = n
        A.settings["assessment"]["number"] = n
    B.link.setting_changed("assessment"); A.link.setting_changed("assessment")
    A.link.submit("assessment", b"three changes later"); A.link.settle()
    check(texts(B.take()) == [b"three changes later"] and A.link.calls["encryption"][0] == 2 and B.link.not_opened == 0,
          "the number went through three values, lower and higher: one more call, and the frame opens under the last")

    # ---- the receiver's setting is behind: the sender states its number at the switch and the frames open
    # [A_SWITCH_STATES_THE_SENDERS_NUMBER_V1] (until v14: nothing opened until the receiver's own setting arrived)
    A.set("assessment", number=8001)
    was = B.link.not_opened
    for i in range(5):
        A.link.submit("assessment", b"while b is behind %d" % i)
    A.link.settle()
    got = B.take(5)
    check(texts(got) == [b"while b is behind %d" % i for i in range(5)] and B.link.not_opened == was and B.why is None
          and B.settings["assessment"].get("number") != 8001,
          "the receiver's setting is behind: the sender stated its number when it switched, the receiver computed the key, and all five are taken")
    check(B.link.calls["not opened"][0] == 0 and A.link.sent_again == 0, "nothing was asked for again and nothing sent twice")
    B.set("assessment", number=8001)
    check(B.nothing() == [] and B.link.calls["not opened"][0] == 0, "its setting arrives later: nothing more happens")
    A.link.submit("assessment", b"and on"); A.link.settle()
    check(texts(B.take()) == [b"and on"], "and the sequence goes on")

    # ---- the receiver is ahead (it took a number the sender has not reached yet)
    A.set("assessment", number=8002)
    A.link.submit("assessment", b"at 8002"); A.link.settle()
    B.settings["assessment"]["number"] = 8003            # b's held read brought both changes at once; a's has one
    B.link.setting_changed("assessment")
    opened_before = B.take()                              # (b had not been told of 8002 before the call: see below)
    A.link.submit("assessment", b"still at 8002"); A.link.settle()
    lost = B.nothing()
    A.set("assessment", number=8003)
    got = opened_before + lost + B.take(2 - len(opened_before) - len(lost))
    check(texts(got) == [b"at 8002", b"still at 8002"] and B.nothing() == [],
          "the receiver went to a number the sender had not reached: what the sender sent under the number between goes back, "
          "and is taken once the sender reaches it")

    # ---- the algorithm
    A.set("assessment", number=8100, protection="chacha20-poly1305")
    A.link.submit("assessment", b"under the other algorithm"); A.link.settle()
    got = B.take()
    check(texts(got) == [b"under the other algorithm"] and B.link._in[got[0][1]].protection == "chacha20-poly1305"
          and B.settings["assessment"].get("protection") != "chacha20-poly1305" and B.link.calls["not opened"][0] == 0,
          "an algorithm switch reaches a receiver whose setting is behind: the algorithm that opens is the sender's, and the frame is taken")
    B.set("assessment", number=8100, protection="chacha20-poly1305")

    # ---- encryption turned on
    A.set("warning", number=31337)
    A.link.submit("warning", b"sealed from here"); A.link.settle()
    got = B.take()
    check(texts(got) == [b"sealed from here"] and B.why is None and B.link._in[got[0][1]].key is not None,
          "encryption turned on for a Role: a receiver whose setting has not heard computes the key from the stated number and takes it")
    B.set("warning", number=31337)

    # ---- the protocol
    for e in (B, A):
        e.set("units", driver="openqasm")
    A.link.submit("units", b"x" * 200); A.link.submit("units", b"x" * 199 + b"y"); A.link.settle()
    was = len(tap.seen)
    A.link.submit("units", b"x" * 199 + b"z"); A.link.settle()
    got = B.take(3)
    check(texts(got) == [b"x" * 200, b"x" * 199 + b"y", b"x" * 199 + b"z"] and A.link.calls["protocol"][0] == 1
          and len(tap.seen) - was < 60,
          "a protocol switch is one call; the key did not change; the new protocol's differences cross in place of the message")

    # ---- replies go back for a retry the same way
    A.link.submit("assessment", b"a question"); A.link.settle()
    q = B.take()[0]
    B.set("assessment", number=8200)
    B.link.submit_reply("assessment", q[1], q[2], b"the answer")
    B.link.settle()
    r = A.take()
    check(texts(r) == [b"the answer"] and r[0][2] == q[2] and r[0][3] is True and A.link.calls["not opened"][0] == 0,
          "a reply sealed under a number the asking end's setting has not heard of opens there: the replying end stated its number")
    A.set("assessment", number=8200)

    # ---- a sender keeps a message only until it is said to be received
    for i in range(F.SAY_RECEIVED_EVERY * 2 + 3):
        A.link.submit("units", b"m%d" % i)
    A.link.settle()
    got = B.take(F.SAY_RECEIVED_EVERY * 2 + 3)
    B.link.settle(); A.link.settle()
    A.link.submit("units", b"last"); A.link.settle(); B.take()
    side = A.link._out[A.link._stream["units"]]
    check(len(got) == F.SAY_RECEIVED_EVERY * 2 + 3 and side.base >= F.SAY_RECEIVED_EVERY * 2 and len(side.kept) <= F.SAY_RECEIVED_EVERY + 4,
          "the receiver says RECEIVED every %d messages and the sender lets those go (%d kept, %d let go)"
          % (F.SAY_RECEIVED_EVERY, len(side.kept), side.base))

    # ---- frames that do not open go back for a retry: the secret is wrong, then set again
    # (the retry itself is as it was; until v14 these checks made frames not open with a setting that was behind)
    wrong = secrets(["assessment", "units", "warning"], salt="not this network's ")
    B.secret_dir = wrong
    A.set("assessment", number=8300)
    for i in range(3):
        A.link.submit("assessment", b"once %d" % i)
    A.link.settle()
    check(B.nothing() == [] and B.link.not_opened >= 3 and B.why is None and any("NOTHING OPENS" in t and "QR code" in t for t in B.said),
          "a receiver holding the wrong secret: nothing opens at its secret or the %d steps after it, none is taken, the connection stays, "
          "and it says the secret must be taken again from the QR code" % F.Link.MAX_STEPS_AHEAD)
    B.secret_dir = S
    B.settings["assessment"]["number"] = 8300
    B.link.setting_changed("assessment"); B.link.setting_changed("assessment")
    got = B.take(3)
    check(texts(got) == [b"once %d" % i for i in range(3)] and B.nothing() == [] and B.link.calls["not opened"][0] >= 2,
          "the secret is set again and the receiver's held read woke twice: it asked twice, and what was sent again twice is taken once")
    check(A.link.calls["again"][0] >= 1 and A.link.sent_again >= 3, "NOT OPENED was answered: the sender started again and sent the three a second time")

    # ---- [THE_SECRET_IS_ADVANCED_UNTIL_ITS_KEY_OPENS_V1] a Role's own secret that has moved forward at the sender
    def stepped(steps):
        d = secrets(["assessment", "units", "warning"])
        sec0 = F.secret_of("assessment", d)
        for k in range(1, steps + 1):
            sec0 = F.next_secret(sec0, "assessment", k)
        if steps:
            F.keep_secret("assessment", sec0, steps, d)
        return d
    for ahead, opens in ((3, True), (F.Link.MAX_STEPS_AHEAD, True), (F.Link.MAX_STEPS_AHEAD + 1, False)):
        now = copy.deepcopy(base); now["assessment"] = dict(now["assessment"], number=9100 + ahead)
        A7, B7, _t = pair(now, copy.deepcopy(base), stepped(ahead), stepped(0))
        A7.link.submit("assessment", b"from %d steps on" % ahead); A7.link.settle()
        got = B7.take(1, 5.0 if opens else NOTHING_S)
        if opens:
            check(texts(got) == [b"from %d steps on" % ahead] and F.read_secret("assessment", B7.secret_dir)[1] == 0
                  and any("moved forward %d step" % ahead in t for t in B7.said),
                  "the sender's secret is %d step(s) on: the receiver advanced its own in memory until the key opened, took the message, "
                  "and wrote nothing to its secret's file" % ahead)
        else:
            check(got == [] and B7.why is None and any("NOTHING OPENS" in t and "QR code" in t for t in B7.said),
                  "the sender's secret is %d steps on, one past the limit: nothing opens, and the receiver says the secret must be taken again from the QR code" % ahead)
        A7.link.close(); B7.link.close()

    # ---- a connection opened later states the number
    behind = copy.deepcopy(A.settings)
    behind["assessment"] = dict(behind["assessment"], number=1)
    A3, B3, _t = pair(copy.deepcopy(A.settings), behind, S, S)
    A3.link.submit("assessment", b"a later connection"); A3.link.settle()
    check(texts(B3.take()) == [b"a later connection"] and B3.link.not_opened == 0,
          "a connection opened after switches states its number in the sequence's first frame: the far end computes the same key "
          "though its own copy of the setting is behind")

    # ---- an end with another secret, and an end with none
    A4, B4, _t = pair(copy.deepcopy(base), copy.deepcopy(base), S, secrets(["assessment", "units"], salt="another site's "))
    A4.link.submit("assessment", b"x"); A4.link.settle()
    check(B4.nothing() == [] and B4.link.not_opened == 1 and any("did not open" in t for t in B4.said),
          "an end holding another secret: the frame does not open, is not taken, and the end says so")
    A5, B5, _t = pair(copy.deepcopy(base), copy.deepcopy(base), S, secrets(["units"]))
    A5.link.submit("assessment", b"x"); A5.link.settle()
    check(B5.nothing() == [] and any("holds no secret" in t for t in B5.said),
          "an end with no secret for the Role does not carry it: nothing is taken, and it says why")

    # ---- an unsealed Role takes nothing that is not a protocol's
    A6, B6, _t = pair(copy.deepcopy(base), copy.deepcopy(base), S, S)
    A6.link.submit("warning", b"x"); A6.link.settle(); B6.take()
    A6.link._todo.put(("mark", threading.Event()))
    A6.link.sock.sendall(bytes([9, 5]) + b"not a word")          # a frame on warning's sequence whose data is no protocol's
    B6.nothing()
    check(B6.why is not None and "no registered protocol's word" in B6.why,
          "data for an unsealed Role that begins with no protocol's word ends the connection with the reason")

    # ---- every build says what it is
    check("v14" in F.BUILD, "the build is named (%s)" % F.BUILD)

    print("RESULT %s (%s)" % ("FAIL" if FAILED else "PASS", F.BUILD))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
