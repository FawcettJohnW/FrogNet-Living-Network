#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_android_frame.py RIBBIT_CPP JVM_CLASSES -- the phone's frame (core/ChannelFrame.java) against the C++ library.

RIBBIT_CPP is ribbit/cpp with bin/libchannel_frame.so built (its README says how); JVM_CLASSES holds the compiled
core and FrameProbe. One end is the Java Codec, the other the C++ Codec, in both directions, then Java at both ends.
No socket is between them: each check hands one end's bytes to the other, so the order of everything is the test's.
The C++ library is the oracle; it was proven against core/channel_frame.py's Link (test_channel_frame_oracle.py).
Prints one PASS or FAIL line per check and RESULT PASS|FAIL."""
import os, random, shutil, subprocess, sys, tempfile

CPP, JVM = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
os.environ.setdefault("CHANNEL_FRAME_LIB", os.path.join(CPP, "bin"))
sys.path.insert(0, os.path.join(CPP, "python"))
import channel_codec as C  # noqa: E402

RNG = random.Random(20261125)
FAILS = []


def check(ok, text):
    print(("PASS  " if ok else "FAIL  ") + text, flush=True)
    if not ok:
        FAILS.append(text)


class Gone(Exception):
    pass


class Java:
    """The Java process: every Java end is one of its Codecs."""
    def __init__(self):
        self.p = subprocess.Popen(["java", "-cp", JVM, "FrameProbe"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.build = self.p.stdout.readline().strip()[6:]
        self.n = 0

    def ask(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        a = self.p.stdout.readline().rstrip("\n")
        if a.startswith("gone "):
            raise Gone(a[5:])
        return a

    def lines(self, n):
        return [self.p.stdout.readline().rstrip("\n") for _ in range(n)]


def hx(b):
    return b.hex() if b else "-"


class JEnd:
    kind = "Java"

    def __init__(self, java, opener, far, sender, secrets):
        self.j = java
        java.n += 1
        self.id = "e%d" % java.n
        java.ask("open %s %d %s %s %s" % (self.id, 1 if opener else 0, far, sender or "-", secrets))

    def setting(self, role, v):
        self.j.ask("setting %s %s %s" % (self.id, role, "none" if v is None else "%s %d %s %d" % (
            v.get("driver") or "tcp", v.get("number") or 0, v.get("protection") or "aes-256-gcm", v.get("step") if v.get("step") is not None else -1)))

    def _unit(self, a):
        return None if a == "unit -" else bytes.fromhex(a[5:])

    def pack(self, role, m):
        return self._unit(self.j.ask("pack %s %s %s" % (self.id, role, hx(m))))

    def next(self):
        return self._unit(self.j.ask("next %s" % self.id))

    def undo(self):
        self.j.ask("undo %s" % self.id)

    def feed(self, b):
        n = int(self.j.ask("feed %s %s" % (self.id, hx(b)))[5:])
        return [bytes.fromhex(x.split(" ")[2]) if x.split(" ")[2] != "-" else b"" for x in self.j.lines(n)]

    def said(self):
        return self.j.lines(int(self.j.ask("said %s" % self.id)[5:]))

    def repeats(self):
        return int(self.j.ask("counts %s" % self.id).split()[5])

    def close(self):
        pass


class CEnd:
    kind = "C++"

    def __init__(self, _java, opener, far, sender, secrets):
        self.c = C.Codec(opener, far, sender, secrets)

    def setting(self, role, v):
        self.c.setting(role, v)

    def pack(self, role, m):
        try:
            return self.c.pack(role, m)
        except C.Gone as e:
            raise Gone(str(e))

    def next(self):
        return self.c.next()

    def undo(self):
        self.c.undo()

    def feed(self, b):
        try:
            return [m[4] for m in self.c.feed(b)]
        except C.Gone as e:
            raise Gone(str(e))

    def said(self):
        return self.c.said()

    def repeats(self):
        return self.c.counts()["calls"]["again"][0]

    def close(self):
        self.c.close()


def sealed(driver="tcp", protection="aes-256-gcm", step=None):
    return {"driver": driver, "number": RNG.getrandbits(62) | 1, "protection": protection, "step": step}


def body(i, tag=b"m"):
    """Bodies like a call's: a header that mostly repeats, then bytes that mostly do not."""
    n = RNG.choice((0, 1, 7, 23, 24, 25, 160, 900, 4097, 9000))
    head = b"\x01\x05Chapel" + bytes([i & 0xff, (i >> 8) & 0xff]) + tag
    return (head + bytes(RNG.getrandbits(8) if RNG.random() < 0.3 else 65 + (k % 7) for k in range(n)))[:max(n, 0) + len(head)]


class Pair:
    """Two ends and what is in flight between them. settle() carries everything until neither owes anything."""
    def __init__(self, java, make_a, make_b, secrets_a, secrets_b=None, role="units"):
        self.a = make_a(java, True, "b", "node-a", secrets_a)
        self.b = make_b(java, False, "a", "node-b", secrets_b or secrets_a)
        self.role = role
        self.at_a, self.at_b = [], []                      # what each has taken

    def set(self, v, a=True, b=True):
        if a:
            self.a.setting(self.role, v)
        if b:
            self.b.setting(self.role, v)

    def send(self, m, back=False):
        src, dst, got = (self.b, self.a, self.at_a) if back else (self.a, self.b, self.at_b)
        for _ in range(4000):                              # a repeat under way goes first, a unit at a time
            u = src.pack(self.role, m)
            if u is not None:
                got += dst.feed(u)
                return
            self.settle()
        raise Gone("pack never came back with a unit")

    def settle(self):
        for _ in range(20000):
            moved = False
            for src, dst, got in ((self.a, self.b, self.at_b), (self.b, self.a, self.at_a)):
                u = src.next()
                if u is not None:
                    got += dst.feed(u)
                    moved = True
            if not moved:
                return
        raise Gone("the two ends never stopped owing each other")

    def close(self):
        self.a.close()
        self.b.close()


def run(java, name, make_a, make_b, T):
    S = os.path.join(T, "secrets")
    said = "%s opens, %s accepts" % (make_a.kind, make_b.kind)

    def roundtrip(p, n, tag):
        sa, sb = [], []
        for i in range(n):
            m = body(i, tag)
            if i % 3 == 2:
                sb.append(m)
                p.send(m, back=True)
            else:
                sa.append(m)
                p.send(m)
        return sa, sb

    # 1 not sealed, then sealed, new keys, both algorithms, the three protocols: every body arrives, in order, intact
    p = Pair(java, make_a, make_b, S)
    p.set(None)
    sent_a, sent_b = [], []
    steps = [None, sealed(), sealed(), sealed(protection="chacha20-poly1305"), sealed("chorus"), sealed("openqasm"), {"driver": "openqasm", "number": 0},
             {"driver": "chorus", "number": 0}, sealed("chorus", "chacha20-poly1305"), sealed("tcp")]
    for k, v in enumerate(steps):
        p.set(v)
        sa, sb = roundtrip(p, 30, b"s%d" % k)
        sent_a += sa
        sent_b += sb
    p.settle()
    check(p.at_b == sent_a and p.at_a == sent_b, "1 %s: not sealed, sealed, 2 new keys, both algorithms, tcp, chorus and openqasm sealed and not: %d of %d and %d of %d bodies, in order"
          % (said, len(p.at_b), len(sent_a), len(p.at_a), len(sent_b)))
    p.close()

    # 2 the sender switches 40 messages before the receiver's setting arrives: none taken until it does, then all 40
    p = Pair(java, make_a, make_b, S)
    first = sealed()
    p.set(first)
    p.send(b"hello"); p.send(b"hello back", back=True); p.settle()
    later = sealed("chorus")
    p.set(later, b=False)
    sent = [body(i, b"late") for i in range(40)]
    for m in sent:
        p.at_b += p.b.feed(p.a.pack(p.role, m))
    before = len(p.at_b) - 1
    p.set(later, a=False)
    p.settle()
    check(before == 0 and p.at_b[1:] == sent, "2 %s: the sender switches key and protocol 40 messages before the receiver's setting arrives: %d taken before it, %d of 40 after, in order"
          % (said, before, len(p.at_b) - 1))
    p.close()

    # 3 undo: a unit the plane did not send is taken back, and the far end never knows
    p = Pair(java, make_a, make_b, S)
    p.set(sealed("openqasm"))
    want = []
    for i in range(120):
        m = body(i, b"u")
        u = p.a.pack(p.role, m)
        if i % 3 == 1:
            p.a.undo()
            continue
        want.append(m)
        p.at_b += p.b.feed(u)
        if i % 17 == 5:
            p.set(sealed(RNG.choice(("tcp", "chorus", "openqasm"))))
    p.settle()
    check(p.at_b == want, "3 %s: 40 of 120 units taken back with undo, under 7 switches: exactly the other %d arrive, in order (%d did)" % (said, len(want), len(p.at_b)))
    p.close()

    # 4 random: 200 rounds, a switch in about one of four, settings arriving at the two ends in either order or late
    p = Pair(java, make_a, make_b, S)
    p.set(None)
    sent_a, sent_b, switches, pending = [b"hello"], [], 0, None
    p.send(b"hello")                                       # the end that opened the connection names the sequence
    for i in range(200):
        r = RNG.random()
        if pending and r < 0.5:
            p.set(pending[1], a=not pending[0], b=pending[0])
            pending = None
        elif r < 0.25:
            v = RNG.choice((None, sealed(RNG.choice(("tcp", "chorus", "openqasm")), RNG.choice(("aes-256-gcm", "chacha20-poly1305"))),
                            {"driver": RNG.choice(("tcp", "chorus", "openqasm")), "number": 0}))
            switches += 1
            if pending:
                p.set(pending[1], a=not pending[0], b=pending[0])
            first_a = RNG.random() < 0.5
            p.set(v, a=first_a, b=not first_a)
            pending = (first_a, v)
        for _ in range(RNG.randint(1, 4)):
            m = body(i, b"r")
            if RNG.random() < 0.5:
                sent_a.append(m); p.send(m)
            else:
                sent_b.append(m); p.send(m, back=True)
    if pending:
        p.set(pending[1], a=not pending[0], b=pending[0])
    p.settle()
    check(p.at_b == sent_a and p.at_a == sent_b, "4 %s: 200 random rounds, %d switches, the two ends' settings arriving in either order: %d of %d and %d of %d bodies, in order"
          % (said, switches, len(p.at_b), len(sent_a), len(p.at_a), len(sent_b)))
    p.close()

    # 5 another secret: nothing is delivered either way
    p = Pair(java, make_a, make_b, S, os.path.join(T, "other"))
    p.set(sealed())
    try:
        for i in range(20):
            p.at_b += p.b.feed(p.a.pack(p.role, body(i)))
        p.settle()
        for i in range(20):
            u = p.b.pack(p.role, body(i))
            if u is not None:
                p.at_a += p.a.feed(u)
    except Gone:
        pass
    check(not p.at_a and not p.at_b, "5 %s: the two ends hold different secrets: %d and %d bodies delivered" % (said, len(p.at_b), len(p.at_a)))
    p.close()

    # 6 an unsealed sender into an end whose setting is sealed: nothing taken; once the sender's setting arrives, everything
    p = Pair(java, make_a, make_b, S)
    v = sealed()
    p.set(None, b=False)
    p.set(v, a=False)
    sent = [body(i, b"x") for i in range(5)]
    for m in sent:
        p.at_b += p.b.feed(p.a.pack(p.role, m))
    before = len(p.at_b)
    p.set(v, b=False)
    p.settle()
    check(before == 0 and p.at_b == sent, "6 %s: 5 unsealed messages into an end whose setting is sealed: %d taken; after the sender's setting arrives, %d of 5, sealed" % (said, before, len(p.at_b)))
    p.close()

    # 7 a loss asked for twice, RECEIVED for the first repeat arriving after the second has begun: each message once
    p = Pair(java, make_a, make_b, S)
    a, b, got, sent, asks = p.a, p.b, [], [b"hello"], []
    v = sealed()
    p.set(v)
    got += b.feed(a.pack(p.role, b"hello"))
    while True:
        u = b.next()
        if u is None:
            break
        a.feed(u)
    for _ in range(2):
        later = sealed()
        a.setting(p.role, later)
        for i in range(100):
            sent.append(body(len(sent), b"again"))
            got += b.feed(a.pack(p.role, sent[-1]))
        b.setting(p.role, later)
        while True:
            u = b.next()
            if u is None:
                break
            asks.append(u)
    for u in asks:
        a.feed(u)
    late, began = [], 0
    for _ in range(5000):
        u = a.next()
        if u is None:
            if not late:
                break
            for x in late:
                a.feed(x)
            late = []
            continue
        got += b.feed(u)
        while True:
            x = b.next()
            if x is None:
                break
            late.append(x)
        if a.repeats() >= 4:                               # one AGAIN for each of the two key switches, then one for each repeat
            began += 1
            if began == 10:
                for x in late:
                    a.feed(x)
                late = []
    for i in range(50):
        sent.append(body(len(sent), b"after"))
        got += b.feed(a.pack(p.role, sent[-1]))
        while True:
            x = b.next()
            if x is None:
                break
            a.feed(x)
    check(got == sent, "7 %s: the receiver asks twice for one loss and says RECEIVED for the first repeat after the second has begun: %d of %d messages, each once and in order"
          % (said, len(got), len(sent)))
    p.close()


def settings_and_secrets(java, T):
    S = os.path.join(T, "secrets")
    # the check a setting carries: the Java text and HMAC are the C++ library's
    same = 0
    for k in range(50):
        f = {"driver": RNG.choice(("tcp", "chorus", "openqasm")), "number": str(RNG.getrandbits(62)), "protection": RNG.choice(("aes-256-gcm", "chacha20-poly1305")),
             "step": str(RNG.randint(0, 9))}
        if k % 5 == 0:
            f["host"] = "10.0.0.%d" % k; f["port"] = "8800"
        c = C.check_of("mediahost", f, S)
        j = java.ask("check mediahost %s %s" % (S, " ".join("%s=%s" % kv for kv in f.items())))[6:]
        same += c == j
    check(same == 50, "8 the check of a setting: the Java check equals the C++ library's on %d of 50 settings" % same)

    # a setting is taken only with its check; without the secret's check it is ignored and says why
    f = {"driver": "chorus", "number": "77", "protection": "aes-256-gcm", "step": "0"}
    good = dict(f, check=C.check_of("mediahost", f, S))
    bad = dict(f, check="0" * 96)
    t1 = java.ask("take mediahost %s %s" % (S, " ".join("%s=%s" % kv for kv in good.items()))).split(" ", 8)
    t2 = java.ask("take mediahost %s %s" % (S, " ".join("%s=%s" % kv for kv in bad.items()))).split(" ", 8)
    none = os.path.join(T, "none"); os.makedirs(none, exist_ok=True)
    t3 = java.ask("take mediahost %s %s" % (none, " ".join("%s=%s" % kv for kv in bad.items()))).split(" ", 8)
    c1 = C.take_setting("mediahost", good, S)
    check(t1[1] == "1" and t1[7] == c1["fingerprint"] and t2[1] == "0" and "does not verify" in t2[8] and t3[1] == "1" and t3[7] == "-",
          "9 a setting with its check is taken (the secret's 8 characters %s, the C++ library's %s); with a wrong check it is ignored (\"%s\"); a device with no secret takes it"
          % (t1[7], c1["fingerprint"], t2[8][:60]))

    # a Role's own secret moves forward: Java and C++ each move their own copy of the same file to the same bytes
    import hashlib, hmac
    own_j, own_c = os.path.join(T, "own_j"), os.path.join(T, "own_c")
    for d in (own_j, own_c):
        os.makedirs(d)
        shutil.copy(os.path.join(S, "default"), os.path.join(d, "default"))
        shutil.copy(os.path.join(T, "role_secret"), os.path.join(d, "vault"))
    ok = True
    for step in (0, 1, 2, 5):
        f = {"driver": "tcp", "number": str(1000 + step), "protection": "aes-256-gcm", "step": str(step)}
        sec = open(os.path.join(T, "role_secret"), "rb").read()          # the owner's check, with the secret at that step
        for k in range(1, step + 1):
            sec = hmac.new(sec, ("next\nvault\n%d" % k).encode(), hashlib.sha384).digest()
        f["check"] = hmac.new(sec, ("vault" + "".join("\n%s=%s" % (k, f[k]) for k in sorted(f))).encode(), hashlib.sha384).hexdigest()
        tj = java.ask("take vault %s %s" % (own_j, " ".join("%s=%s" % kv for kv in f.items()))).split(" ", 8)
        tc = C.take_setting("vault", f, own_c)
        same_file = open(os.path.join(own_j, "vault"), "rb").read() == open(os.path.join(own_c, "vault"), "rb").read()
        ok = ok and tj[1] == "1" and tc["taken"] and same_file and (tj[2] == "1") == tc["moved"]
    f = {"driver": "tcp", "number": "5", "protection": "aes-256-gcm", "step": "1"}
    back = java.ask("take vault %s %s" % (own_j, " ".join("%s=%s" % kv for kv in dict(f, check="0" * 96).items()))).split(" ", 8)
    check(ok and back[1] == "0" and "does not take a Role's secret back" in back[8],
          "10 a Role's own secret moves forward at steps 0, 1, 2 and 5: after each, the Java file and the C++ file are the same bytes; a setting at a lower step is refused")
    works = java.ask("works chacha20-poly1305")
    check(works == "works 1", "11 this Java has ChaCha20-Poly1305 (on a phone: Android 9 and later)")


def main():
    T = tempfile.mkdtemp(prefix="android-frame-")
    try:
        os.makedirs(os.path.join(T, "secrets")); os.makedirs(os.path.join(T, "other"))
        open(os.path.join(T, "secrets", "default"), "wb").write(os.urandom(48))
        open(os.path.join(T, "other", "default"), "wb").write(os.urandom(48))
        open(os.path.join(T, "role_secret"), "wb").write(os.urandom(40))
        java = Java()
        print("Java: %s\nC++:  %s" % (java.build, C.BUILD), flush=True)
        run(java, "jc", JEnd, CEnd, T)
        run(java, "cj", CEnd, JEnd, T)
        run(java, "jj", JEnd, JEnd, T)
        settings_and_secrets(java, T)
        java.p.stdin.close()
    finally:
        shutil.rmtree(T, ignore_errors=True)
    print("RESULT %s the phone's frame against the C++ library: %d fail" % ("FAIL" if FAILS else "PASS", len(FAILS)), flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
