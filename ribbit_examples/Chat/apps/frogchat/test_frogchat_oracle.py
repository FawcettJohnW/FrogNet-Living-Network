#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_frogchat_oracle.py HOST:PORT -- frogchat against a real memory.

The production path only: the service's FNW1 listener in front of ram.php, as
setup_ram8788.sh builds it. There is no local stand-in. Run it against
loopback on the server, or from another machine across the Internet.

  G1  what Dave writes to Bob's buffer is what Bob's blocking read returns
  G2  Bob's read, asked BEFORE Dave writes, is held open and wakes on the write
  G3  a held read that expires returns nothing, without raising, after ~wait_s
  G4  a write goes out and comes back WHILE a read is held on the same session
      (replies are matched by sequence, not by order)
  G5  both directions at once, 10 lines each way, none lost, order kept
  G6  a message already in my buffer when I arrive is not shown as new
  G7  two people writing to me at the same instant: neither line is lost, and
      my buffer holds one cell per sender, read back in the memory's write order
  G8  no listener: StoreUnreachable.   G9  a refusal: StoreRefused.
  G10 an idle wait costs REQ_REPEAT out and RESP_SAME back: 21 bytes each way
  G11 /Name /Other message: one line into several buffers; a plain line follows
  G12 /list: presence is a fresh cell, leaving removes it
"""
import os, sys, threading, time, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frogchat as F

_fail = 0


def ck(name, cond, extra=""):
    global _fail
    print(("  ok    " if cond else "  FAIL  ") + name + (("   " + str(extra)) if extra != "" else ""))
    if not cond:
        _fail += 1


def main(store):
    tag = uuid.uuid4().hex[:6]
    made = []

    def mk(*a):
        c = F.Chat(*a); made.append(c); return c

    dave_n, bob_n = "Dave-" + tag, "Bob-" + tag
    dave = mk(F.Memory(F.Session(store)), dave_n, [bob_n])
    bob = mk(F.Memory(F.Session(store)), bob_n, [dave_n])

    got = {}

    def wait_bob():
        t0 = time.time(); got["m"] = (bob.next_messages(10.0) or [None])[0]; got["dt"] = time.time() - t0

    t = threading.Thread(target=wait_bob); t.start()
    time.sleep(0.7)
    held = t.is_alive()
    dave.say("hello bob")
    t.join(5.0)
    m = got.get("m") or {}
    ck("G1 Bob's read returned what Dave wrote", m.get("text") == "hello bob" and m.get("from") == dave_n, m)
    ck("G1 the JSON says who and when", isinstance(m.get("ts"), float))
    ck("G2 the read was still held 0.7 s after it was asked", held)
    ck("G2 it woke on the write", 0.6 < got.get("dt", 0) < 2.0, "%.2f s" % got.get("dt", 0))

    t0 = time.time(); m = bob.next_messages(1.0); dt = time.time() - t0
    ck("G3 expiry returns nothing, no raise, after ~wait_s", m == [] and 0.9 <= dt < 3.0, "%.2f s" % dt)

    t = threading.Thread(target=lambda: got.update(h=(bob.next_messages(6.0) or [None])[0])); t.start()
    time.sleep(0.5)
    t0 = time.time(); bob.say("said while my own read is held"); dt = time.time() - t0
    ck("G4 a write completed while a read was held on the same session", t.is_alive() and dt < 1.5, "%.3f s" % dt)
    dave.say("release"); t.join(8.0)
    ck("G4 and the held read then woke", (got.get("h") or {}).get("text") == "release")
    dave.next_messages(2.0)                   # take Bob's line out of Dave's view

    seen_b, seen_d = [], []

    def pump(chat, out, n):
        while len(out) < n:
            ms = chat.next_messages(8.0)
            if not ms:
                return
            out.extend(m["text"] for m in ms)

    tb = threading.Thread(target=pump, args=(bob, seen_b, 10)); td = threading.Thread(target=pump, args=(dave, seen_d, 10))
    tb.start(); td.start()
    for i in range(10):                       # a person types; the reader is back in its read between lines
        dave.say("d%d" % i); bob.say("b%d" % i); time.sleep(0.15)
    tb.join(12); td.join(12)
    ck("G5 Bob got all of Dave's lines in order", seen_b == ["d%d" % i for i in range(10)], seen_b)
    ck("G5 Dave got all of Bob's lines in order", seen_d == ["b%d" % i for i in range(10)], seen_d)

    late = mk(F.Memory(F.Session(store)), bob_n, [dave_n])    # Bob restarts
    m = late.next_messages(1.0)
    ck("G6 a message already in my buffer when I arrive is not shown as new", m == [], m)

    # G7 -- the reason the address is ChatServer.<to>.<from>: two people write to
    # Bob at the same instant, while Bob is NOT in his read. Neither line is lost.
    carol_n = "Carol-" + tag
    carol = mk(F.Memory(F.Session(store)), carol_n, [bob_n])
    go = threading.Event()
    def fire(chat, text):
        go.wait(); chat.say(text)
    ts = [threading.Thread(target=fire, args=(dave, "from dave")), threading.Thread(target=fire, args=(carol, "from carol"))]
    for x in ts: x.start()
    go.set()
    for x in ts: x.join()
    time.sleep(0.3)                           # both landed before Bob looks
    ms = bob.next_messages(3.0)
    ck("G7 two senders at once: both lines delivered in one read",
       sorted(m["text"] for m in ms) == ["from carol", "from dave"], [m["text"] for m in ms])
    rows = dave.mem.read(F.SERVICE, bob_n)
    ck("G7 Bob's buffer is one cell per sender", sorted(r["instance"] for r in rows) == sorted([dave_n, carol_n]),
       [r["instance"] for r in rows])
    ck("G7 rows come back in the memory's write order", [r["id"] for r in rows] == sorted(r["id"] for r in rows))

    # G11 -- /Name /Other message: one line, several buffers
    ck("G11 parse: '/Dave /Alice hi there'", F.parse_line("/Dave /Alice hi there") == (["Dave", "Alice"], "hi there"))
    ck("G11 parse: '/list' is a command, '/list hello' is a line to someone called list",
       F.parse_line("/list") == ("list", "") and F.parse_line("/list hello") == (["list"], "hello"))
    ck("G11 parse: a plain line addresses nobody new", F.parse_line("just text") == ([], "just text"))
    bob.next_messages(0.5); carol.next_messages(0.5)
    went = dave.say("to both of you", [bob_n, carol_n])
    mb, mc = bob.next_messages(3.0), carol.next_messages(3.0)
    ck("G11 one line reached both buffers", went == [bob_n, carol_n]
       and [m["text"] for m in mb] == ["to both of you"] and [m["text"] for m in mc] == ["to both of you"])
    ck("G11 each copy says who else it went to", mb and mb[0].get("to") == [bob_n, carol_n])
    dave.say("and again")                     # plain line: same people as last time
    ck("G11 a plain line goes to whoever was last addressed",
       [m["text"] for m in bob.next_messages(3.0)] == ["and again"] and [m["text"] for m in carol.next_messages(3.0)] == ["and again"])
    try:
        mk(F.Memory(F.Session(store)), "Nobody-" + tag).say("x"); ck("G11 a plain line with nobody addressed is refused", False)
    except ValueError:
        ck("G11 a plain line with nobody addressed is refused", True)
    try:
        mk(F.Memory(F.Session(store)), "list"); ck("G11 a command word cannot be a name", False)
    except ValueError:
        ck("G11 a command word cannot be a name", True)

    # G12 -- /list: present because my cell is fresh; gone because I removed it
    here = dave.who()
    ck("G12 /list shows everyone who is here", all(n in here for n in (dave_n, bob_n, carol_n)), here)
    carol.leave()
    here = dave.who()
    ck("G12 leaving removes exactly my own presence cell", carol_n not in here and dave_n in here and bob_n in here, here)

    # G10 -- nothing happening costs 21 bytes each way. Bob's read expires empty and
    # is asked again unchanged: REQ_REPEAT out, RESP_SAME back.
    st = bob.mem.s.stats
    bob.next_messages(1.0)
    before = dict(st)
    bob.next_messages(1.0)
    ck("G10 an unchanged question went as REQ_REPEAT and came back RESP_SAME",
       st["repeat"] - before["repeat"] == 1 and st["same"] - before["same"] == 1 and st["raw"] == before["raw"])
    ck("G10 21 bytes out, 21 bytes (+4 seq) back",
       st["bytes_out"] - before["bytes_out"] == 21 and st["bytes_in"] - before["bytes_in"] == 21,
       "out=%d in=%d" % (st["bytes_out"] - before["bytes_out"], st["bytes_in"] - before["bytes_in"]))

    try:
        F.Session("127.0.0.1:1"); ck("G8 no listener raises StoreUnreachable", False)
    except F.StoreUnreachable:
        ck("G8 no listener raises StoreUnreachable", True)
    try:
        dave.mem.s.call("GET", "/ram.php?op=nonsense"); ck("G9 a refusal raises StoreRefused", False)
    except F.StoreRefused:
        ck("G9 a refusal raises StoreRefused", True)

    for c in made:                            # leave the memory as we found it
        try:
            c.leave()
        except (F.StoreUnreachable, F.StoreRefused):
            pass
    print("\n%s  (%d failed)   memory: %s" % ("PASS" if _fail == 0 else "FAIL", _fail, store))
    return 1 if _fail else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: test_frogchat_oracle.py HOST:PORT")
    sys.exit(main(sys.argv[1]))
