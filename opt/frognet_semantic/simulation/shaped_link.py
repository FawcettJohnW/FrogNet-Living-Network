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
shaped_link.py - [SHAPED_LINKS_BETWEEN_MACHINES_V1] a machine's uplink, shaped, for wire_between_machines_tier.py.

John, 2026-11-17: "Wire in the shaped links."

The simulator's links between machines were perfect: an in-memory cable with no delay, no loss and no ceiling. A
shaped link is one given a bearer's delay, jitter, bandwidth and loss, so that what runs over it behaves as it
would over wifi, a cellular modem, a HaLow radio or a path across a continent. The kernel would do this with
tc-netem; the container's kernel has no qdiscs, so the simulator already has its own shaper, slowlink.py
([SLOW_LINKS_V1]), which shapes the real bytes of a real TCP session in user space. This file puts that shaper on
every TCP connection a machine makes to another machine, without either end being told:

  * it runs ON the machine (in its network namespace), listening on 127.0.0.1:<port>;
  * the tier adds three rules to that machine's nat OUTPUT chain, ahead of the node's own:
        mark 2                         -> RETURN      (this relay's own connections out: not shaped twice)
        tcp to this machine's own .1   -> RETURN      (talking to itself is not a link), except port 80
        tcp to 10.0.0.0/8, not port 80 -> REDIRECT here
    (port 80 to another machine is the node's own rule: it goes to this machine's proxy, which is not a link);
  * for each connection it reads where the connection was really going (SO_ORIGINAL_DST), connects there from the
    machine's own .1 with mark 2, and relays through slowlink's two directions: `up` for what this machine sends,
    `down` for what comes back on that connection.

So every byte of both drivers that leaves a machine for another crosses a shaped link: the TCP driver's send and
return connections, and soft Homa's session with its memory (the Homa driver's messages ride that session). What
is shaped is each machine's uplink, at the machine. A machine that only forwards another's packets does not shape
them again: a path of three machines has the two ends' links, not three.

usage (run by the tier, on the machine):
  python3 simulation/shaped_link.py --port P --src <this machine's .1> --profile 4g [--seed N]
"""
import argparse
import os
import random
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slowlink  # noqa: E402

SO_ORIGINAL_DST = 80
MARK = 2
# what "mixed" draws from, one per machine by the seed: the bearers a FrogNet node is put on, one of them lossy.
# geo and jammed are named profiles and can be asked for by name; they are not in the draw because one machine on
# 256 kb/s or 2 kb/s makes a battery of thousands of requests a test of patience and not of the wire.
MIXED = ("lan", "wifi", "4g", "halow", "wan-100ms", "4g-lossy")


def profile_for(name, index, seed=7):
    """The profile's name for machine number `index` under the tier's --links word."""
    if name != "mixed":
        slowlink.profile(name)                            # raises with the list of names if it is not one
        return name
    return random.Random(seed * 1000 + index).choice(MIXED)


class Uplink(slowlink.Relay):
    """slowlink.Relay with two differences: it listens where it is told, and each connection goes where that
    connection was going before the machine's rule sent it here."""

    def __init__(self, port, src, up, down=None, seed=0, name="uplink"):
        self.target, self.up, self.down, self.name = None, up, down or up, name
        self.src = src
        self.rng = random.Random(seed)
        self.stats = {"connections": 0}
        self.ls = socket.socket()
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("0.0.0.0", port))                   # REDIRECT delivers to the address the packet left by
        self.ls.listen(256)
        self.port = port
        self.dark = threading.Event()
        self.conns = []
        threading.Thread(target=self._accept, daemon=True, name="uplink").start()

    def _accept(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            if self.dark.is_set():
                c.close()
                continue
            try:
                raw = c.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
                port, a, b, c4, d = struct.unpack("!2xH4B8x", raw)
                target = ("%d.%d.%d.%d" % (a, b, c4, d), port)
                t = socket.socket()
                t.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, MARK)       # the machine's first rule lets it out
                t.bind((self.src, 0))                                       # from this machine's identity, as a node's
                t.settimeout(10)
                t.connect(target)
                t.settimeout(None)
            except OSError as e:
                # where it was going refused or is not there: the caller sees its connection end, as it would
                c.close()
                self.stats["refused"] = self.stats.get("refused", 0) + 1
                self.stats["last_refusal"] = repr(e)
                continue
            for s in (c, t):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.stats["connections"] += 1
            self.conns.append((c, t))
            rtt = 2 * self.up.latency_ms
            for src, dst, L, k in ((c, t, self.up, "up"), (t, c, self.down, "down")):
                d_ = slowlink._Direction(src, dst, L, self.rng, self.stats, k, rtt)
                threading.Thread(target=d_.reader, daemon=True).start()
                threading.Thread(target=d_.writer, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    link = slowlink.profile(a.profile)
    u = Uplink(a.port, a.src, link, seed=a.seed)
    print("[SHAPED-LINK] [SHAPED_LINKS_BETWEEN_MACHINES_V1] %s's uplink is %s (%s), every TCP connection to another "
          "machine except port 80, listening on %d" % (a.src, a.profile, link.label(), a.port), flush=True)
    threading.Event().wait()                               # until the tier ends this process; `u` relays meanwhile


if __name__ == "__main__":
    main()
