#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_plane_oracle.py HOST:PORT -- the fast plane, with a kart-race workload.

  P1  a published value is what a reader gets back
  P2  a read asked BEFORE the publish is held, and wakes on it (no polling)
  P3  an expired wait returns [] -- "not yet" -- and does not raise
  P4  nothing ever published and no wait: that is said (KeyError), not an empty success
  P5  an OLDER generation can never replace a newer one
  P6  12 karts publishing 80-byte bodies at 60 Hz for 3 s, 12 readers each
      blocking on the whole field: at every reader, every kart's generations
      only ever increase and no state is
      delivered twice; report states/s and publish->read latency
  P7  a reader that only wants karts near it reads only those (prefix)
  P8  dropping a name withdraws it
"""
import os, struct, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plane_client import Plane

fails = 0


def ck(name, cond, extra=""):
    global fails
    print(("  ok    " if cond else "  FAIL  ") + name + (("   " + str(extra)) if extra != "" else ""))
    fails += 0 if cond else 1


def main(where):
    race = "race%d/" % (int(time.time()) % 100000)
    a, b = Plane(where), Plane(where)
    a.publish(race + "solo/1", 5, b"hello")
    time.sleep(0.05)
    rows, at = b.read(race + "solo/1")
    ck("P1 a published value is what a reader gets", rows == [(race + "solo/1", 5, b"hello")])

    got = {}
    t = threading.Thread(target=lambda: got.update(r=b.read(race + "solo/1", after=at, wait_ms=5000), t=time.time()))
    t0 = time.time(); t.start(); time.sleep(0.5); held = t.is_alive()
    a.publish(race + "solo/1", 6, b"six"); t.join(3)
    ck("P2 a read asked before the publish was held, then woke on it",
       held and got.get("r", [None])[0] == [(race + "solo/1", 6, b"six")] and 0.45 < got["t"] - t0 < 1.0, "%.3f s" % (got.get("t", 0) - t0))

    at = got["r"][1]
    t0 = time.time(); r = b.read(race + "solo/1", after=at, wait_ms=400); dt = time.time() - t0
    ck("P3 an expired wait returns [], no raise", r[0] == [] and 0.35 < dt < 1.0, "%.2f s" % dt)
    try:
        b.read(race + "nobody/"); ck("P4 nothing ever published, no wait: said so", False)
    except KeyError:
        ck("P4 nothing ever published, no wait: said so", True)

    a.publish(race + "solo/1", 3, b"OLD"); time.sleep(0.05)
    ck("P5 an older generation cannot replace a newer one", b.read(race + "solo/1")[0] == [(race + "solo/1", 6, b"six")])

    N, HZ, SECS = 12, 60, 3.0
    stop = threading.Event(); lat = []; regress = [0]; seen = [0]; shed = [0]; sent = [0]

    def kart(i):
        p = Plane(where); tick = 100
        nxt = time.perf_counter()
        while not stop.is_set():
            tick += 1
            body = struct.pack("!d", time.time()) + bytes(72)          # 80 bytes, like STK's kart state
            ok = p.publish("%skart/%02d" % (race, i), tick, body)
            sent[0] += ok; shed[0] += (not ok)
            nxt += 1.0 / HZ
            time.sleep(max(0, nxt - time.perf_counter()))

    def racer(i):
        p = Plane(where); last = {}; after = 0
        while not stop.is_set():
            rows, after = p.read(race + "kart/", after=after, wait_ms=200)
            now = time.time()
            for name, gen, body in rows:
                if gen <= last.get(name, 0):
                    regress[0] += 1
                last[name] = gen
                if len(body) == 80:
                    lat.append(now - struct.unpack("!d", body[:8])[0])
                seen[0] += 1

    ths = [threading.Thread(target=kart, args=(i,)) for i in range(N)] + [threading.Thread(target=racer, args=(i,)) for i in range(N)]
    for x in ths: x.start()
    time.sleep(SECS); stop.set()
    for x in ths: x.join(3)
    lat.sort()
    ck("P6 generations only ever increase, at every reader, for every kart", regress[0] == 0 and seen[0] > 0, "regressions=%d" % regress[0])
    print("        %d karts x %d Hz for %.0f s: published %d, shed %d; readers took %d states (%.0f/s); "
          "publish->read median %.1f ms, p99 %.1f ms"
          % (N, HZ, SECS, sent[0], shed[0], seen[0], seen[0] / SECS,
             1000 * lat[len(lat) // 2], 1000 * lat[int(len(lat) * 0.99)]))

    near = b.read(race + "kart/0")[0]            # karts 00..09: a prefix is the reader's choice of what to read
    ck("P7 a reader reads only what it asks for", 0 < len(near) <= 10 and all(n.startswith(race + "kart/0") for n, _, _ in near), len(near))

    a.drop(race + "solo/1"); time.sleep(0.05)
    try:
        b.read(race + "solo/1"); ck("P8 a dropped name is gone", False)
    except KeyError:
        ck("P8 a dropped name is gone", True)
    print("\n%s  (%d failed)   plane: %s" % ("PASS" if not fails else "FAIL", fails, where))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else "usage: test_plane_oracle.py HOST:PORT")
