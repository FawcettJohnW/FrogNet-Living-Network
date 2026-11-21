#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/test_channel_frame_oracle.py -- what core/channel_frame.py must do, checked on a real connection between two
ends in one process. Each end has its OWN copy of the settings, as two machines do, so a check can leave one end
behind the other. It is the mechanism's own check; that it works between machines is the Roles exercise tier's.

    python3 core/test_channel_frame_oracle.py
"""
import copy
import hashlib
import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import channel_frame as F  # noqa: E402

FAILED = []


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


def key_dir(roles, size=20, salt=""):
    d = tempfile.mkdtemp(prefix="channel-keys-")
    for r in roles:
        os.makedirs(os.path.join(d, r))
        for i in range(size):
            with open(os.path.join(d, r, "%02d" % i), "wb") as f:
                f.write(hashlib.sha256(("%s%s key %d" % (salt, r, i)).encode()).digest())
    return d


def pair(set_a, set_b, dir_a, dir_b):
    a, b = socket.socketpair()
    tap = Tap(a)
    quiet = lambda text: None
    A = F.Link(tap, ("b", 1), True, lambda r: set_a.get(r), keys=lambda r: F.key_set(r, dir_a), say=quiet)
    B = F.Link(b, ("a", 1), False, lambda r: set_b.get(r), keys=lambda r: F.key_set(r, dir_b), say=quiet)
    return A, B, tap


def main():
    F.register_driver("turned", b"TRN\x01", lambda st, pid, m: bytes(m)[::-1], lambda st, pid, d: bytes(d)[::-1])
    keys = key_dir(["assessment", "units"])
    truth = {"units": {"driver": "tcp"}, "assessment": {"driver": "tcp", "seed": "seed-a", "protection": "aes-256-gcm", "switches": 0}}
    sa, sb = copy.deepcopy(truth), copy.deepcopy(truth)
    A, B, tap = pair(sa, sb, keys, keys)

    # two Roles with different settings on one connection, told apart by the packet ID
    A.submit("units", b"units asks one")
    A.submit("assessment", b"assessment asks one SECRET")
    A.submit("units", b"units asks two")
    got = [B.receive() for _ in range(3)]
    check([(g[0], g[2], g[3], g[4]) for g in got] == [("units", 1, False, b"units asks one"), ("assessment", 1, False, b"assessment asks one SECRET"),
                                                      ("units", 2, False, b"units asks two")],
          "two Roles on one connection, one sealed and one not: each message arrives for its Role, in order, by its packet ID alone")
    check(b"units asks one" in tap.seen and b"SECRET" not in tap.seen, "on the link the unsealed Role's message is readable and the sealed Role's is not")
    B.submit_reply("assessment", got[1][1], 1, b"assessment answers one")
    r = A.receive()
    check((r[0], r[2], r[3], r[4]) == ("assessment", 1, True, b"assessment answers one"), "a reply comes back on its request's sequence, sealed under the same Role's key")

    # a protocol switch: one call at one place in the stream; the encryption does not move
    for i in (3, 4):
        A.submit("assessment", b"before %d" % i)
    sa["assessment"]["driver"] = "turned"
    A.setting_changed("assessment")
    for i in (5, 6):
        A.submit("assessment", b"after %d" % i)
    got = [B.receive()[4] for _ in range(4)]
    check(got == [b"before 3", b"before 4", b"after 5", b"after 6"] and A.calls["protocol"][0] == 1 and B.calls["protocol"][1] == 1,
          "a protocol switch: two messages sent before the call and two after it, read only afterwards, all arrive right; one call sent, one read")
    check(B.running() == {"assessment": {"driver": "tcp", "switches": 0, "protection": "aes-256-gcm", "sealed": True}},
          "each direction is its own stream: the other end still sends the old protocol until its own held read wakes it (%s)" % B.running())
    sb["assessment"]["driver"] = "turned"
    B.setting_changed("assessment")
    B.settle()
    B.submit_reply("assessment", 1, 5, b"answer 5")
    check(A.receive()[4] == b"answer 5" and B.running()["assessment"]["driver"] == "turned", "and then it switches its own sending the same way")

    # a key switch: the receiver is told nothing by the sender; it follows the call
    seen = len(tap.seen)
    for i in (7, 8, 9):
        A.submit("assessment", b"old key %d" % i)
    sa["assessment"]["switches"] = 1
    A.setting_changed("assessment")
    for i in (10, 11, 12):
        A.submit("assessment", b"new key %d" % i)
    got = [B.receive()[4] for _ in range(6)]             # B's own copy of the setting still says 0 switches
    check(got == [b"old key %d" % i for i in (7, 8, 9)] + [b"new key %d" % i for i in (10, 11, 12)] and B.calls["encryption"][1] == 1 and B.found == 0,
          "a key switch: three messages under the old key, the call, three under the next, read only afterwards by an end whose own "
          "copy of the setting has not changed: all six open, by the rule, with no search")
    k0, k1 = F.key_number("seed-a", 0, 20), F.key_number("seed-a", 1, 20)
    check(k0 != k1 and A._out[1].k == k1 and B._in[1].k == k1 and A._out[1].n == 1,
          "both ends are at the same key of the set, by the rule on the seed and the count (key %d, then key %d)" % (k0, k1))
    check(b"seed-a" not in tap.seen[seen:] and all(open(os.path.join(keys, "assessment", "%02d" % k), "rb").read() not in tap.seen for k in range(20)),
          "nothing about the key crossed the link: no key, no seed")

    # an algorithm switch reaches an end before its own setting: the pair that opens is the current one
    newer = copy.deepcopy(sa); newer["assessment"].update(switches=2, protection="chacha20-poly1305")
    s2a, s2b = copy.deepcopy(sa), copy.deepcopy(sa)
    A2, B2, _t = pair(s2a, s2b, keys, keys)
    A2.submit("assessment", b"under aes")
    s2a.update(newer)
    A2.setting_changed("assessment")
    A2.submit("assessment", b"under chacha")
    A2.submit("assessment", b"under chacha again")
    got = [B2.receive()[4] for _ in range(3)]
    check(got == [b"under aes", b"under chacha", b"under chacha again"] and B2._in[0].protection == "chacha20-poly1305"
          and B2._in[0].k == A2._out[0].k and B2.found == 1,
          "an algorithm switch reaches an end whose setting is behind: the next frame does not open under what it held, it runs "
          "through the set under every algorithm, takes the pair that opens, and the frame after needs no search (%d found)" % B2.found)

    # three switches commanded faster than an end's setting can follow, two of them algorithms
    s4a, s4b = copy.deepcopy(sa), copy.deepcopy(sa)
    A7, B7, _t = pair(s4a, s4b, keys, keys)
    A7.submit("assessment", b"one")
    for n, alg in ((2, "chacha20-poly1305"), (3, "aes-256-gcm"), (4, "chacha20-poly1305")):
        s4a["assessment"].update(switches=n, protection=alg)
        A7.setting_changed("assessment")
        A7.submit("assessment", b"after switch %d" % n)
    got = [B7.receive()[4] for _ in range(4)]
    check(got == [b"one"] + [b"after switch %d" % n for n in (2, 3, 4)] and B7._in[0].k == A7._out[0].k and B7._in[0].n == 4,
          "three switches in a row, two of them of algorithm, to an end that holds none of the three settings: every frame opens (%d found by search)" % B7.found)

    # an end that holds the set and no setting at all for the Role
    A8, B8, _t = pair(copy.deepcopy(sa), {}, keys, keys)
    A8.submit("assessment", b"to an end with no setting")
    check(B8.receive()[4] == b"to an end with no setting" and B8.found == 1,
          "an end that holds the Role's set and no setting for it yet: the first frame is opened by the key of the set that opens it")

    # encryption turned on for a Role that had none
    sa["units"].update(seed="seed-u", switches=1); sb["units"].update(seed="seed-u", switches=1)
    seen = len(tap.seen)
    A.setting_changed("units")
    A.submit("units", b"units asks three HIDDEN")
    g = B.receive()
    check(g[4] == b"units asks three HIDDEN" and b"HIDDEN" not in tap.seen[seen:], "encryption turned on: the call crosses unsealed, and every frame after it is sealed")

    # encryption turned off: taken only from a setting this end holds
    sa["units"].update(seed="", switches=2)
    A.setting_changed("units")
    A.submit("units", b"units asks four, unsealed")
    try:
        B.receive(); said = ""
    except F.DoesNotOpen as e:
        said = str(e)
    check("an unsealed frame is taken only when the setting this end holds shows no seed" in said,
          "encryption turned off, at an end whose own setting still shows a seed: the unsealed frame is refused with the reason (a plain frame is what a forgery looks like)")
    s9a = {"units": {"driver": "tcp", "seed": "seed-u", "switches": 1}}; s9b = copy.deepcopy(s9a)
    A9, B9, _t = pair(s9a, s9b, keys, keys)
    A9.submit("units", b"sealed")
    s9a["units"].update(seed="", switches=2); s9b["units"].update(seed="", switches=2)
    A9.setting_changed("units")
    A9.submit("units", b"not sealed")
    check([B9.receive()[4] for _ in range(2)] == [b"sealed", b"not sealed"], "and at an end whose setting shows no seed, it is taken")

    # a connection opened after switches starts from the setting's count
    s3b = {"assessment": dict(truth["assessment"], driver="turned")}
    A3, B3, _t = pair(copy.deepcopy(sa), s3b, keys, keys)
    A3.submit("assessment", b"a later connection")
    check(B3.receive()[4] == b"a later connection" and B3._in[0].n == 1 and B3.found == 0,
          "a connection opened after a switch states its count in its sequence's first frame; the far end starts from the same key, by "
          "the rule, though its own copy of the setting is behind")

    # the rule never lands on the key in use, for any size of set
    ok = True
    for size in range(2, 51):
        ks = [F.key_number("any seed", n, size) for n in range(200)]
        ok = ok and all(x != y for x, y in zip(ks, ks[1:])) and all(0 <= x < size for x in ks)
    check(ok, "the selection rule: over 200 switches, for every set of 2 to 50 keys, no switch lands on the key in use")

    # what does not open ends the connection with the reason
    other = key_dir(["assessment", "units"], salt="another site's ")
    A4, B4, _t = pair(copy.deepcopy(sa), copy.deepcopy(sa), keys, other)
    A4.submit("assessment", b"x")
    try:
        B4.receive(); said = ""
    except F.DoesNotOpen as e:
        said = str(e)
    check("not under any of the 20 keys" in said, "an end holding a different set: no key of its set opens the frame, and the connection ends with the reason")
    A5, B5, _t = pair({"units": {"driver": "tcp"}}, {"units": {"driver": "tcp"}}, keys, keys)
    A5.submit("units", b"x")
    B5.receive()
    A5.settle()
    A5._put(F.packet_id(2, 0, False), b"XYZ\x01 not a protocol")      # (the check writing what no writer would)
    try:
        B5.receive(); said = ""
    except F.ConnectionGone as e:
        said = str(e)
    check("no registered protocol's word" in said, "data that begins with no registered word is refused, with the reason")

    # what the frame costs
    A6, B6, t6 = pair({"units": {"driver": "tcp"}}, {"units": {"driver": "tcp"}}, keys, keys)
    A6.submit("units", b"")
    A6.settle()
    first = len(t6.seen)
    A6.submit("units", b"")
    A6.settle()
    check(len(t6.seen) - first == 2 + 4, "an unsealed frame adds 2 bytes to its data (its length and its packet ID as small numbers) and the 4-byte word (%d)" % (len(t6.seen) - first))

    # one writer: many threads hand one connection their requests, and every one arrives whole, each Role's in order
    import threading
    A10, B10, _t = pair(copy.deepcopy(truth), copy.deepcopy(truth), keys, keys)
    def many(role, k):
        for i in range(200):
            A10.submit(role, b"%s thread %d message %03d" % (role.encode(), k, i))
    ts = [threading.Thread(target=many, args=(r, k)) for k in range(4) for r in ("units", "assessment")]
    [t.start() for t in ts]
    got = [B10.receive() for _ in range(1600)]
    [t.join() for t in ts]
    per = {}
    for g in got:
        role, k, i = g[4].split()[0].decode(), int(g[4].split()[2]), int(g[4].split()[4])
        per.setdefault((role, k), []).append(i)
    counters = {r: [g[2] for g in got if g[0] == r] for r in ("units", "assessment")}
    check(len(per) == 8 and all(v == list(range(200)) for v in per.values()) and all(c == list(range(1, 801)) for c in counters.values())
          and not hasattr(A10, "_send_lock"),
          "eight threads hand one connection 1,600 requests for two Roles, one of them sealed: its one writer puts each on the wire whole, "
          "every thread's in the order it handed them, each Role's counters 1 to 800 in order; there is no lock on sending")
    print("RESULT %s (%s)" % ("FAIL" if FAILED else "PASS", F.BUILD))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
