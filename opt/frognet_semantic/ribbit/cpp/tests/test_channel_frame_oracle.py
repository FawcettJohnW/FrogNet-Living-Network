#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_channel_frame_oracle.py -- libchannel_frame against the frame the nodes run.

The oracle is core/channel_frame.py's Link, the code on every node. One end of a real connection is a Link; the other
is the library's Codec (through its C interface and python/channel_codec.py) with nothing but a socket under it.
Whatever one end seals, packs, switches or asks for again, the other must open, unpack and follow. Then the same
between two Codecs.

  1  the check a setting must carry: the library's is the node's (core/channel_setting.py check_of)
  2  not sealed, tcp: messages and replies, both ways
  3  sealed (the network's secret): both ways; a new key; the other algorithm
  4  the protocol switched, tcp -> chorus -> openqasm -> tcp, under a running stream, sealed
  5  the sender switches its key before the receiver's setting arrives: nothing is lost, nothing is out of order
  6  a Role with its own secret, moved forward two steps: the check verifies at each step and the file moves
  7  undo: a unit that did not go on the connection leaves no trace, sealed, on a protocol that keeps state
  8  200 rounds of messages both ways with a switch of key, algorithm or protocol at random between them
  9  the far end holds another secret: nothing opens, nothing is delivered, and the connection says so
 10  Codec to Codec, the library at both ends: sealed, switched, both ways
 11  the library's one rule of its own: a sealed Role takes no unsealed message
A FAIL line says what differed. Exit 0 only if every line is PASS.

    python3 tests/test_channel_frame_oracle.py            (from ribbit/cpp, after bin/libchannel_frame.so is built)
"""
import os, queue, random, shutil, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("FROGNET_SEMANTIC_ROOT") or os.path.normpath(os.path.join(HERE, "..", "..", ".."))
T = tempfile.mkdtemp(prefix="channel-frame-oracle-")
SECRETS = os.path.join(T, "secrets"); os.mkdir(SECRETS)
os.environ["FROGNET_CHANNEL_SECRETS"] = SECRETS
os.environ.setdefault("CHANNEL_FRAME_LIB", os.path.join(HERE, "..", "bin"))
sys.path[:0] = [ROOT, os.path.join(HERE, "..", "python")]
from core import channel_frame as F, channel_setting as S, chorus, openqasm   # noqa: E402  (chorus registers itself)
import channel_codec as C   # noqa: E402
openqasm.register()

RNG = random.Random(20261124)
open(os.path.join(SECRETS, "default"), "wb").write(bytes(RNG.getrandbits(8) for _ in range(48)))
FAILS = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def wait_for(cond, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class LinkEnd:
    """The node's own code at one end: core/channel_frame.py's Link."""
    def __init__(self, sock, opener, fnid, secrets=SECRETS):
        self.settings, self.got, self.said, self.secrets = {}, [], [], secrets
        self.link = F.Link(sock, ("far", 0), opener, lambda role: self.settings.get(role), deliver=self._deliver,
                           secret=lambda role: F.secret_at(role.lower(), (self.settings.get(role) or {}).get("step"), self.secrets)[0],
                           say=self.said.append, fnid=fnid)
        self.ended = None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            self.link.pump()
        except Exception as e:
            self.ended = str(e)

    def _deliver(self, role, sequence, counter, reply, message):
        self.got.append((role, reply, message))
        if message[:5] == b"ECHO ":
            self.link.submit_reply(role, sequence, counter, b"echo " + message[5:])

    def setting(self, role, value):
        if value is None:
            self.settings.pop(role, None)
        else:
            self.settings[role] = value
        self.link.setting_changed(role)

    def send(self, role, message):
        self.link.submit(role, message)

    def counts(self):
        return {"calls": {k: list(v) for k, v in self.link.calls.items()}, "not_opened": self.link.not_opened, "sent_again": self.link.sent_again}

    def close(self):
        self.link.close()


class CodecEnd:
    """The library at one end, with only a socket under it: one thread writes, one reads."""
    def __init__(self, sock, opener, fnid, secrets=SECRETS):
        self.sock, self.got, self.ended = sock, [], None
        self.codec = C.Codec(opener, "far", fnid, secrets)
        self.todo = queue.SimpleQueue()
        threading.Thread(target=self._write, daemon=True).start()
        threading.Thread(target=self._read, daemon=True).start()

    def _owed(self):
        while (unit := self.codec.next()) is not None:
            self.sock.sendall(unit)

    def _write(self):
        try:
            while True:
                item = self.todo.get()
                if item[0] == "stop":
                    return
                if item[0] == "mark":
                    self._owed(); item[1].set(); continue
                self._owed()
                if item[0] == "owed":
                    continue
                while True:
                    unit = self.codec.pack(item[1], item[2]) if item[0] == "send" else self.codec.pack_reply(*item[1:])
                    if unit is not None:
                        break
                    self._owed()                       # messages are being sent again: they go first
                if item[0] == "send" and item[3]:      # this unit does not go on the connection
                    self.codec.undo(); continue
                self.sock.sendall(unit)
        except Exception as e:
            self.ended = "%s: %s" % (type(e).__name__, e)

    def _read(self):
        try:
            while True:
                data = self.sock.recv(RNG.choice((1, 7, 100, 65536)))     # in any pieces
                if not data:
                    self.ended = self.ended or "closed"; return
                for role, sequence, counter, reply, message in self.codec.feed(data):
                    self.got.append((role, reply, message))
                    if message[:5] == b"ECHO ":
                        self.todo.put(("reply", role, sequence, counter, b"echo " + message[5:]))
                self.todo.put(("owed",))
        except Exception as e:
            self.ended = self.ended or "%s: %s" % (type(e).__name__, e)

    def setting(self, role, value):
        self.codec.setting(role, value)
        self.todo.put(("owed",))

    def send(self, role, message, undone=False):
        self.todo.put(("send", role, message, undone))

    def settle(self):
        done = threading.Event(); self.todo.put(("mark", done)); done.wait(10)

    def counts(self):
        return self.codec.counts()

    def close(self):
        self.todo.put(("stop",))
        try:
            self.sock.shutdown(2)
        except OSError:
            pass


def pair(left, right, **kw):
    a, b = socket.socketpair()
    return left(a, True, "node-opener", **kw.get("l", {})), right(b, False, "node-acceptor", **kw.get("r", {}))


def setting(role, **fields):
    """A setting as its owner writes it: with the check made under the Role's secret (the node's own code makes it)."""
    v = dict(fields)
    v["check"] = S.check_of(role, v)
    return v


def body(i, tag):
    """A message like the session's: a prefix that mostly repeats, then data."""
    return b"FNW1" + bytes([7, 0, 0, i & 3]) + b"seq=%06d" % (i // 4) + b" %s %d " % (tag, i) + bytes(RNG.getrandbits(8) for _ in range(RNG.choice((0, 3, 40, 900, 9000))))


def both(a, b, role, value):
    a.setting(role, value); b.setting(role, value)


def exchange(a, b, role, n, tag, note=""):
    """n messages each way, interleaved, each echoed back. Returns whether both ends got exactly them, in order."""
    a0, b0 = len(a.got), len(b.got)
    sent_a = [b"ECHO " + body(i, tag + b"a") for i in range(n)]
    sent_b = [b"ECHO " + body(i, tag + b"b") for i in range(n)]
    for x, y in zip(sent_a, sent_b):
        a.send(role, x); b.send(role, y)
    want_at_b = [(role, False, m) for m in sent_a]
    want_at_a = [(role, False, m) for m in sent_b]
    echo_at_a = [(role, True, b"echo " + m[5:]) for m in sent_a]
    echo_at_b = [(role, True, b"echo " + m[5:]) for m in sent_b]
    wait_for(lambda: len(a.got) - a0 >= 2 * n and len(b.got) - b0 >= 2 * n)
    ga, gb = a.got[a0:], b.got[b0:]
    ok = ([g for g in gb if not g[1]] == want_at_b and [g for g in ga if not g[1]] == want_at_a
          and [g for g in ga if g[1]] == echo_at_a and [g for g in gb if g[1]] == echo_at_b)
    if not ok:
        print("      %s: at a %d of %d, at b %d of %d; a ended %r, b ended %r" % (note, len(ga), 2 * n, len(gb), 2 * n, a.ended, b.ended))
    return ok


def run(left, right, who):
    R = "units"
    a, b = pair(left, right)
    # The end that opened the connection names a Role's sequence; the other end sends for that Role once it has read
    # the name (core/channel_frame.py: "The end that opened the connection gives each Role a sequence of its own").
    a.send(R, b"hello"); wait_for(lambda: len(b.got) == 1)
    ok = b.got == [(R, False, b"hello")] and exchange(a, b, R, 30, b"plain")
    check(ok, "2 %s: not sealed, tcp: 30 messages and 30 replies each way" % who)

    both(a, b, R, setting(R, driver="tcp", number=RNG.getrandbits(62)))
    ok = exchange(a, b, R, 30, b"sealed")
    both(a, b, R, setting(R, driver="tcp", number=RNG.getrandbits(62)))
    ok = exchange(a, b, R, 30, b"newkey") and ok
    both(a, b, R, setting(R, driver="tcp", number=RNG.getrandbits(62), protection="chacha20-poly1305"))
    ok = exchange(a, b, R, 30, b"chacha") and ok
    ca, cb = a.counts(), b.counts()
    check(ok and ca["calls"]["encryption"][0] >= 3 and cb["calls"]["encryption"][0] >= 3 and not a.ended and not b.ended,
          "3 %s: sealed under the network's secret, then a new key, then chacha20-poly1305: 90 messages and replies each way; "
          "SWITCH ENCRYPTION sent %d and %d times" % (who, ca["calls"]["encryption"][0], cb["calls"]["encryption"][0]))

    ok, number = True, RNG.getrandbits(62)
    for driver in ("chorus", "openqasm", "tcp", "openqasm", "chorus"):
        both(a, b, R, setting(R, driver=driver, number=number, protection="aes-256-gcm"))
        ok = exchange(a, b, R, 25, driver.encode(), driver) and ok
    ca, cb = a.counts(), b.counts()
    check(ok and ca["calls"]["protocol"][0] >= 5 and cb["calls"]["protocol"][0] >= 5,
          "4 %s: the protocol switched tcp, chorus, openqasm, tcp, openqasm, chorus under a sealed stream: 125 messages and replies each way; "
          "SWITCH PROTOCOL sent %d and %d times" % (who, ca["calls"]["protocol"][0], cb["calls"]["protocol"][0]))

    # the sender's setting changes first; the receiver's arrives after 40 messages are already on the connection
    ok = True
    for first, second in ((a, b), (b, a)):
        new = setting(R, driver="chorus", number=RNG.getrandbits(62))
        g0 = len(second.got)
        first.setting(R, new)
        sent = [body(i, b"early") for i in range(40)]
        for m in sent:
            first.send(R, m)
        time.sleep(0.3)
        held_back = len(second.got) - g0
        second.setting(R, new)
        wait_for(lambda: len(second.got) - g0 >= 40)
        ok = ok and held_back == 0 and second.got[g0:] == [(R, False, m) for m in sent]
        ok = exchange(a, b, R, 10, b"after", "after the late setting") and ok
    ca, cb = a.counts(), b.counts()
    check(ok and ca["sent_again"] >= 40 and cb["sent_again"] >= 40,
          "5 %s: each end in turn switched its key 40 messages before the other's setting arrived: none taken until it did, then all 40 "
          "in order, sent again (%d and %d messages sent again; frames that did not open: %d and %d)"
          % (who, ca["sent_again"], cb["sent_again"], ca["not_opened"], cb["not_opened"]))

    # a Role with its own secret, moved forward
    own = "vault"
    open(os.path.join(SECRETS, own), "wb").write(bytes(RNG.getrandbits(8) for _ in range(40)))
    ok, moved = True, []
    for step in (0, 1, 2):
        v = {"driver": "tcp", "number": RNG.getrandbits(62), "step": step}
        secret_s = F.secret_at(own, step, SECRETS)[0]
        import hashlib, hmac
        v["check"] = hmac.new(secret_s, S.check_text(own, v), hashlib.sha384).hexdigest()        # the owner's page, at that step
        ok = ok and C.check_of(own, v, SECRETS) == v["check"]
        took = C.take_setting(own, v, SECRETS)
        moved.append(F.read_secret(own, SECRETS)[1])
        ok = ok and took["taken"] and took["step"] == step and took["fingerprint"] == F.secret_fingerprint(secret_s)
        both(a, b, own, {k: took[k] for k in ("driver", "number", "protection", "step")})
        if step == 0:
            a.send(own, b"hello"); wait_for(lambda: b.got and b.got[-1] == (own, False, b"hello"))
        ok = exchange(a, b, own, 10, b"own%d" % step, "own secret step %d" % step) and ok
    forged = dict(v, number=0); refused = C.take_setting(own, forged, SECRETS)
    back = {"driver": "tcp", "number": 5, "step": 1}; back["check"] = "0" * 96
    check(ok and moved == [0, 1, 2] and not refused["taken"] and "does not verify" in refused["why"] and not C.take_setting(own, back, SECRETS)["taken"],
          "6 %s: a Role with its own secret at steps 0, 1, 2: each setting's check verifies, the file moves to steps %s, and traffic "
          "opens at each; a setting with the number changed and the old check is ignored (%s)" % (who, moved, refused["why"][:40]))

    # undo: only the library's end can take a unit back
    ends = [e for e in (a, b) if isinstance(e, CodecEnd)]
    ok = True
    for e in ends:
        other = b if e is a else a
        g0 = len(other.got)
        sent = []
        for i in range(60):
            m = body(i, b"undo")
            if i % 3 == 1:
                e.send(R, body(i, b"never"), undone=True)      # packed, then taken back: it never goes
            e.send(R, m); sent.append(m)
        wait_for(lambda: len(other.got) - g0 >= 60)
        time.sleep(0.1)
        ok = ok and other.got[g0:] == [(R, False, m) for m in sent]
    check(ok and not a.ended and not b.ended, "7 %s: 20 units packed and taken back among 60 sent (sealed, chorus): the far end got the 60, in order, and "
          "nothing else" % who)

    ok, switches = True, 0
    number, driver, protection = RNG.getrandbits(62), "chorus", "aes-256-gcm"
    for i in range(200):
        r = RNG.random()
        if r < 0.5:
            what = RNG.choice(("key", "algorithm", "protocol", "off"))
            if what == "key": number = RNG.getrandbits(62)
            elif what == "algorithm": protection = "chacha20-poly1305" if protection == "aes-256-gcm" else "aes-256-gcm"
            elif what == "protocol": driver = RNG.choice([d for d in ("tcp", "chorus", "openqasm") if d != driver])
            else: number = 0 if number else RNG.getrandbits(62)
            new = setting(R, driver=driver, number=number, protection=protection)
            first, second = (a, b) if RNG.random() < 0.5 else (b, a)
            first.setting(R, new)
            between = RNG.random() < 0.5                       # a message under the new setting before the far end has it
            g0 = len(second.got)
            if between:
                first.send(R, b"between %d" % i); time.sleep(RNG.choice((0, 0.002, 0.02)))
            second.setting(R, new)
            if between:
                ok = wait_for(lambda: len(second.got) > g0) and second.got[g0:] == [(R, False, b"between %d" % i)] and ok
            switches += 1
        ok = exchange(a, b, R, RNG.choice((1, 2, 5)), b"r%d" % i, "round %d" % i) and ok
        if not ok:
            break
    check(ok and not a.ended and not b.ended, "8 %s: 200 rounds both ways with %d switches of key, algorithm, protocol or sealing at random, the two "
          "ends' settings arriving in either order: every message and reply arrived, in order" % (who, switches))
    for e in (a, b):
        e.close()

    # the far end holds another secret
    other_dir = os.path.join(T, "other-" + who.replace(" ", "")); os.mkdir(other_dir)
    open(os.path.join(other_dir, "default"), "wb").write(bytes(RNG.getrandbits(8) for _ in range(48)))
    for kw, name in (({"r": {"secrets": other_dir}}, "the acceptor"), ({"l": {"secrets": other_dir}}, "the opener")):
        a, b = pair(left, right, **kw)
        v = {"driver": "tcp", "number": RNG.getrandbits(62)}
        both(a, b, R, v)
        a.send(R, body(0, b"x")); time.sleep(0.2)          # names the sequence; it does not open at b
        for i in range(1, 5):
            a.send(R, body(i, b"x")); b.send(R, body(i, b"y"))
        time.sleep(0.4)
        na, nb = a.counts()["not_opened"], b.counts()["not_opened"]
        check(not a.got and not b.got and na >= 1 and nb >= 1,
              "9 %s: %s holds another secret: of 5 sealed messages each way none is delivered (frames that did not open: %d and %d)" % (who, name, na, nb))
        for e in (a, b):
            e.close()


def unsealed_into_sealed(left, right, who):
    """The opener holds no setting (it names its sequence unsealed); the acceptor's setting for the Role is sealed."""
    a, b = pair(left, right)
    b.setting("units", {"driver": "tcp", "number": RNG.getrandbits(62)})
    for i in range(5):
        a.send("units", body(i, b"plain"))
    time.sleep(0.4)
    taken = len(b.got)
    for e in (a, b):
        e.close()
    return taken


def a_repeat_is_sent_whole():
    """[A_REPEAT_ONCE_ANNOUNCED_IS_SENT_WHOLE_V1] The receiver asks twice for the same loss, and its RECEIVED for the
    first repeat reaches the sender only after the second repeat has begun. Every message must arrive exactly once."""
    def units(c):
        out = []
        while True:
            u = c.next()
            if not u:
                return out
            out.append(u)
    def sealed():
        return {"driver": "tcp", "number": RNG.getrandbits(62), "protection": "aes-256-gcm"}
    a, b = C.Codec(True, "b", "node-a", SECRETS), C.Codec(False, "a", "node-b", SECRETS)
    got = []
    first = sealed()
    a.setting("units", first); b.setting("units", first)
    got += [m[4] for m in b.feed(a.pack("units", b"hello"))]
    for u in units(b):
        a.feed(u)
    sent = [b"hello"]
    asks = []
    for _ in range(2):                                      # twice the sender's setting changes before the receiver's
        later = sealed()
        a.setting("units", later)
        for i in range(100):
            sent.append(body(len(sent), b"again"))
            got += [m[4] for m in b.feed(a.pack("units", sent[-1]))]
        b.setting("units", later)
        asks += units(b)                                    # NOT OPENED: it asks each time its own setting catches up
    for u in asks:
        a.feed(u)
    late, began, repeats = [], 0, 0
    for _ in range(5000):
        u = a.next()
        if not u:
            if not late:
                break
            for x in late:                                  # nothing more owed: what the receiver said comes in now
                a.feed(x)
            late = []
            continue
        got += [m[4] for m in b.feed(u)]
        late += units(b)                                    # the receiver's RECEIVED calls, held back
        repeats += sum("to send again" in line for line in a.said())
        if repeats >= 2:
            began += 1
            if began == 10:                                 # ten messages into the second repeat, they arrive
                for x in late:
                    a.feed(x)
                late = []
    for i in range(50):
        sent.append(body(len(sent), b"after"))
        got += [m[4] for m in b.feed(a.pack("units", sent[-1]))]
        for x in units(b):
            a.feed(x)
    a.close(); b.close()
    return got == sent, len(sent), len(got), repeats


try:
    v = {"driver": "chorus", "number": 123456789012345678, "protection": "aes-256-gcm", "sealed": True}
    check(C.check_of("Units", v, SECRETS) == S.check_of("Units", v) and len(S.check_of("Units", v)) == 96,
          "1 the check a setting must carry is the node's: %s..." % C.check_of("Units", v, SECRETS)[:16])
    run(LinkEnd, CodecEnd, "node's Link opens, the library accepts")
    run(CodecEnd, LinkEnd, "the library opens, node's Link accepts")
    run(CodecEnd, CodecEnd, "10 the library at both ends")
    lib, node = unsealed_into_sealed(LinkEnd, CodecEnd, "lib"), unsealed_into_sealed(CodecEnd, LinkEnd, "node")
    check(lib == 0, "11 a sender that names itself unsealed, into an end whose setting for the Role is sealed: the library takes %d of its 5 "
          "messages (the node's Link, in the same place, takes %d: it has no such rule)" % (lib, node))
    ok, n_sent, n_got, repeats = a_repeat_is_sent_whole()
    check(ok and repeats >= 2, "12 the receiver asks twice for one loss and says RECEIVED for the first repeat after the second has begun: "
          "%d of %d messages arrived, each once and in order (%d repeats)" % (n_got, n_sent, repeats))
finally:
    shutil.rmtree(T, ignore_errors=True)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS), "(%s; oracle %s)" % (C.BUILD, F.BUILD))
os._exit(1 if FAILS else 0)
