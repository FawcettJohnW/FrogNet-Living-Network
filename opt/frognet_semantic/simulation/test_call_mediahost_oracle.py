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
test_call_mediahost_oracle.py - the call mediahost: receive, MIX all-feeds per rung,
fan per-destination over independent threads.

Gates call_mediahost.py. Drives the REAL frognet_mediahost.compute_plan with injected
fake receivers/senders so it runs in-container without ffmpeg or real sockets. Asserts
the architecture John specified (>2-party directive):

  1. ALL-FEEDS mix: every recipient's program contains EVERY source (no minus-self
     filtering). The host does not exclude anyone - self-view is the client's job.
  2. ONE ENCODE PER RUNG (shared): recipients at the same rung receive the SAME encoded
     bytes; the host encodes once per distinct demanded rung, not once per recipient.
  3. PER-DESTINATION INDEPENDENCE: a destination whose sender is blocked/slow does NOT
     reduce what the other destinations receive - each has its own sender thread/queue.
  4. PER-DESTINATION BACKPRESSURE: the slow client's bearer steps down (it then reads a
     lower shared rung program); healthy clients' bearers/rungs are untouched.
  5. AUDIO+VIDEO carried up, demuxed at the host.
"""
from __future__ import annotations
import os, sys, struct, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok:
        FAILS.append(label)


class FakeReceiver:
    """Stands in for MediaSocketReceiver: we capture on_frame and push frames by hand."""
    def __init__(self, host, port, on_frame):
        self.on_frame = on_frame
    def start(self): pass
    def close(self): pass


class FakeSender:
    """Stands in for MediaSocketSender. `block=True` simulates a slow/stuck client: its
    queue fills and it sheds, but - critically - calls into it never touch any OTHER
    sender. Records what actually got through."""
    def __init__(self, host, port, maxq=120, on_drop=None):
        self.delivered = []
        self.on_drop = on_drop
        self.block = False
        self._q = []
        self.maxq = maxq
    def send(self, frame, is_key=False):
        if self.block:
            # stuck client: the writer thread can't drain, so the bounded queue fills
            # and sheds the oldest non-key to make room (mirrors MediaSocketSender).
            if len(self._q) >= self.maxq:
                if is_key:
                    # keep the keyframe: drop the oldest non-key if present, else this stalls
                    self._q.pop(0)
                    if self.on_drop: self.on_drop()
                    self._q.append(frame)
                else:
                    if self.on_drop: self.on_drop()      # shed the new non-key
                return
            self._q.append(frame)
            return
        self.delivered.append((frame, is_key))
    def depth(self): return len(self._q)
    def close(self): pass


def _pack(seq, ts, key, audio, video):
    hdr = struct.Struct("!IQBII")
    return hdr.pack(seq, ts, 1 if key else 0, len(audio), len(video)) + audio + video


def run():
    import call_mediahost as MH
    from frognet_mediahost import compute_plan

    # --- a 3-party call: alice, bob, carol all stream and all watch ---
    who = ["alice", "bob", "carol"]
    streams = [{"type": "stream", "session": "s", "who": w} for w in who]
    watchers = [{"type": "watch", "session": "s", "who": w, "ladder_level": 6} for w in who]
    plan = compute_plan(streams, watchers)

    # the host uses demanded_levels (distinct rungs) - all three want rung 6 -> ONE rung
    check("compute_plan yields the demanded rung set",
          plan["demanded_levels"] == [6], f"{plan['demanded_levels']}")

    # build the host with fake receivers + fake senders
    senders = {}
    def sender_cls(host, port, maxq=120, on_drop=None):
        s = FakeSender(host, port, maxq, on_drop); senders[(host, port)] = s; return s

    host = MH.CallMediaHost("s", _receiver_cls=FakeReceiver, _sender_cls=sender_cls)

    rx = {}
    for w in who:
        host.add_source(w, "10.0.0.1", 9100 + hash(w) % 50)
        rx[w] = host.sources[w].on_frame      # grab the demux callback

    # each recipient dials its own (addr, port) -> its own _Destination/sender
    def dest_addr_port(rec):
        return ("10.0.0.1", 9200 + who.index(rec))
    host.set_plan(plan, dest_addr_port)

    # one shared program per demanded rung, NOT one per recipient
    check("host builds ONE program per demanded rung (not per recipient)",
          len(host.programs) == 1 and 6 in host.programs,
          f"programs={sorted(host.programs)}")

    # --- 5. sources push opaque AV up; host demuxes both tracks ---
    for i, w in enumerate(who):
        rx[w](_pack(i, 1000, True, audio=f"A{w}".encode(), video=f"V{w}".encode()))
    got_audio = all(host.latest[w][0] == f"A{w}".encode() for w in who)
    got_video = all(host.latest[w][1] == f"V{w}".encode() for w in who)
    check("host demuxes audio from opaque uplink", got_audio)
    check("host demuxes video from opaque uplink", got_video)

    # --- 1 & 2. pump once: every recipient gets ALL feeds, and the SAME bytes ---
    host.pump()
    def program_of(frame):
        lvl, n = struct.unpack_from("!BB", frame, 0); off = 2
        names = []
        for _ in range(n):
            wl, al, vl = struct.unpack_from("!BII", frame, off); off += 9
            nm = frame[off:off+wl].decode(); off += wl + al + vl
            names.append(nm)
        return set(names)
    delivered = {w: host.dests[w].sender.delivered[-1][0] for w in who}
    for w in who:
        prog = program_of(delivered[w])
        check(f"{w} receives ALL feeds (incl. self) ({sorted(prog)})",
              prog == set(who), f"got {sorted(prog)}")
    # shared encode: identical bytes to every recipient at the same rung
    check("recipients at the same rung get IDENTICAL bytes (one shared encode)",
          delivered["alice"] == delivered["bob"] == delivered["carol"],
          "bytes differ across recipients at same rung")

    # --- 2 & 3. make ONE client slow; others must be unaffected ---
    host.dests["bob"].sender.block = True          # bob is the stuck client
    host.dests["bob"].sender.maxq = 1
    before = {w: len(host.dests[w].sender.delivered) for w in who}
    for t in range(50):
        # refresh sources so there's always new media to fan
        for i, w in enumerate(who):
            rx[w](_pack(100 + t, 2000 + t, t % 10 == 0,
                        audio=f"a{t}".encode(), video=f"v{t}".encode()))
        host.pump()
    after = {w: len(host.dests[w].sender.delivered) for w in who}
    alice_through = after["alice"] - before["alice"]
    carol_through = after["carol"] - before["carol"]
    bob_through = after["bob"] - before["bob"]      # blocked: nothing in `delivered`
    check("healthy clients keep receiving while one is stuck (alice)", alice_through == 50,
          f"alice got {alice_through}/50")
    check("healthy clients keep receiving while one is stuck (carol)", carol_through == 50,
          f"carol got {carol_through}/50")
    check("stuck client does NOT pull frames into delivered", bob_through == 0,
          f"bob delivered {bob_through}")

    # 3. per-destination backpressure: bob's bearer steps down; alice/carol untouched
    published = {}
    host.apply_backpressure(lambda rec, idx: published.__setitem__(rec, idx),
                            drop_thresh=0.0)        # any drop on bob trips it
    check("stuck client's bearer steps DOWN", published.get("bob", 6) < 6,
          f"bob bearer={published.get('bob')}")
    check("healthy clients' bearers untouched", "alice" not in published and "carol" not in published,
          f"published={published}")


def main():
    print("=== call mediahost: receive, mix all-feeds per rung, fan per-destination ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL MEDIAHOST CHECKS PASS" if not FAILS
                  else f"MEDIAHOST CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
