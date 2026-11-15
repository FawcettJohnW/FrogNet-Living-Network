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
slowlink.py - [SLOW_LINKS_V1] a slow link between a member and what it talks to, for the simulator's tiers.

A member's TCP session (fdf-client to fdf-ram, an xTR to its map-server, a Ribbit front to its RAM host) goes through
a relay on loopback that shapes each direction the way a bearer does. tc-netem would do this in the kernel, but the
container's kernel has no qdiscs, so the shaping is here, in user space, on the real bytes of the real session:

  latency_ms    one-way delay of every chunk                      (order is kept: a chunk never passes the one before)
  jitter_ms     a uniform extra delay in [0, jitter_ms) per chunk
  rate_Bps      a bandwidth ceiling, bytes per second: a chunk occupies the link for len/rate
  loss          the chance a chunk is lost; TCP retransmits it, so the chunk and every chunk behind it wait one
                retransmission timeout (max(200 ms, 2 x RTT), doubling while the loss repeats) -- head-of-line, as TCP
  outage_per_s  the chance per second that the link goes dark; nothing is delivered for outage_ms, then everything
                queued is delivered at the link's rate. An outage longer than a session's keepalive ends the session:
                that is a result, not an error of the relay.
  udp           the same shaping for UDP datagrams (each is its own chunk; a lost one is gone, not retransmitted)

The profiles are the transport tier's NetworkParams examples (transport_sim_tier.py), the same numbers:
  lan 0.5 ms 1 Gb/s; wifi 5 ms +-2, 50 Mb/s; 4g 40 ms +-15, 10 Mb/s; geo 300 ms +-20, 256 kb/s; halow 20 ms +-5,
  600 kb/s; jammed 100 ms +-200, 2 kb/s, 10%/s outages of 2 s. Loss is 0 in those; the named lossy profiles add it.
"""
import random
import socket
import threading
import time
from dataclasses import dataclass, replace


@dataclass
class Link:
    latency_ms: float = 0.5
    jitter_ms: float = 0.0
    rate_Bps: float = 125e6
    loss: float = 0.0
    outage_per_s: float = 0.0
    outage_ms: float = 500.0
    chunk: int = 16384

    def label(self):
        s = f"{self.latency_ms:g} ms"
        if self.jitter_ms:
            s += f" +-{self.jitter_ms:g}"
        r = self.rate_Bps * 8
        s += f", {r/1e6:g} Mb/s" if r >= 1e6 else f", {r/1e3:g} kb/s"
        if self.loss:
            s += f", {self.loss*100:g}% loss"
        if self.outage_per_s:
            s += f", {self.outage_per_s*100:g}%/s outages of {self.outage_ms/1000:g} s"
        return s


PROFILES = {
    "lan":    Link(0.5, 0.0, 125e6),
    "wifi":   Link(5.0, 2.0, 50e6 / 8),
    "4g":     Link(40.0, 15.0, 10e6 / 8),
    "geo":    Link(300.0, 20.0, 256e3 / 8),
    "halow":  Link(20.0, 5.0, 600e3 / 8),
    "jammed": Link(100.0, 200.0, 2e3 / 8, 0.0, 0.10, 2000.0),
    # lossy variants: the same bearers with loss, for the cases where a retransmission is what the member sees
    "wifi-lossy":  Link(5.0, 2.0, 50e6 / 8, 0.02),
    "4g-lossy":    Link(40.0, 15.0, 10e6 / 8, 0.01),
    "halow-lossy": Link(20.0, 5.0, 600e3 / 8, 0.05),
    "wan-100ms":   Link(50.0, 5.0, 100e6 / 8),           # one way 50 ms: the 100 ms round trip of NY2 <-> Seattle7
}


def profile(name):
    if name not in PROFILES:
        raise KeyError(f"no link profile {name!r}: {', '.join(sorted(PROFILES))}")
    return replace(PROFILES[name])


class _Direction:
    """One direction of one connection: read chunks, deliver each at its time, in order."""

    def __init__(self, src, dst, link, rng, stats, key, rtt_ms):
        self.src, self.dst, self.link, self.rng, self.stats, self.key = src, dst, link, rng, stats, key
        self.rtt_ms = rtt_ms
        self.q, self.cv, self.done = [], threading.Condition(), False
        self.busy_until = 0.0          # the link is carrying a chunk until then
        self.last_at = 0.0             # order: nothing is delivered before the chunk before it
        self.dark_until = 0.0
        self.next_outage_check = time.monotonic()
        self.rto_ms = 0.0

    def _outage(self, now):
        L = self.link
        if not L.outage_per_s:
            return
        while self.next_outage_check <= now:
            self.next_outage_check += 1.0
            if self.rng.random() < L.outage_per_s:
                start = max(self.next_outage_check - 1.0 + self.rng.random(), self.dark_until)
                self.dark_until = max(self.dark_until, start + L.outage_ms / 1000.0)
                self.stats[self.key + "_outages"] = self.stats.get(self.key + "_outages", 0) + 1

    def when(self, n, now):
        L = self.link
        self._outage(now)
        t = now + (L.latency_ms + (self.rng.random() * L.jitter_ms if L.jitter_ms else 0.0)) / 1000.0
        start = max(now, self.busy_until)
        if start < self.dark_until <= start + 3600 or now < self.dark_until:
            start = max(start, self.dark_until)
        self.busy_until = start + n / L.rate_Bps
        t = max(t, self.busy_until + L.latency_ms / 1000.0)
        if L.loss and self.rng.random() < L.loss:
            self.rto_ms = max(200.0, 2 * self.rtt_ms) if not self.rto_ms else min(self.rto_ms * 2, 60000.0)
            t += self.rto_ms / 1000.0
            self.stats[self.key + "_lost"] = self.stats.get(self.key + "_lost", 0) + 1
        else:
            self.rto_ms = 0.0
        t = max(t, self.last_at)
        self.last_at = t
        return t

    def reader(self):
        try:
            while True:
                b = self.src.recv(self.link.chunk)
                if not b:
                    break
                with self.cv:
                    self.q.append((self.when(len(b), time.monotonic()), b))
                    self.cv.notify()
        except OSError:
            pass
        with self.cv:
            self.done = True
            self.cv.notify()

    def writer(self):
        try:
            while True:
                with self.cv:
                    while not self.q and not self.done:
                        self.cv.wait()
                    if not self.q:
                        break
                    at, b = self.q[0]
                d = at - time.monotonic()
                if d > 0:
                    time.sleep(d)
                with self.cv:
                    self.q.pop(0)
                self.dst.sendall(b)
                self.stats[self.key + "_bytes"] = self.stats.get(self.key + "_bytes", 0) + len(b)
        except OSError:
            pass
        try:
            self.dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Relay:
    """Listens on 127.0.0.1:<port>; each connection is relayed to `target` through `up` (member -> target) and
    `down` (target -> member). One Relay is one member's link."""

    def __init__(self, target, up, down=None, seed=0, name="link"):
        self.target, self.up, self.down, self.name = target, up, down or up, name
        self.rng = random.Random(seed)
        self.stats = {"connections": 0}
        self.ls = socket.socket()
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(64)
        self.port = self.ls.getsockname()[1]
        self.where = f"127.0.0.1:{self.port}"
        self.dark = threading.Event()          # set: the link is cut (link-down event); no new connections either
        self.conns = []
        threading.Thread(target=self._accept, daemon=True, name=f"relay-{name}").start()

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
                t = socket.create_connection(self.target)
            except OSError:
                c.close()
                continue
            for s in (c, t):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.stats["connections"] += 1
            self.conns.append((c, t))
            rtt = 2 * self.up.latency_ms
            for src, dst, L, k in ((c, t, self.up, "up"), (t, c, self.down, "down")):
                d = _Direction(src, dst, L, self.rng, self.stats, k, rtt)
                threading.Thread(target=d.reader, daemon=True).start()
                threading.Thread(target=d.writer, daemon=True).start()

    def cut(self):
        """The link goes down: every session over it ends, and none is accepted until restore()."""
        self.dark.set()
        for c, t in self.conns:
            for s in (c, t):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()
        self.conns = []

    def restore(self):
        self.dark.clear()

    def close(self):
        self.cut()
        self.ls.close()


class UdpRelay:
    """The same shaping for a UDP service: a member sends to 127.0.0.1:<port>; each datagram reaches `target`
    after the up link's delay (or is lost), and the answers come back through the down link to whoever sent."""

    def __init__(self, target, up, down=None, seed=0, name="udp"):
        self.target, self.up, self.down, self.name = target, up, down or up, name
        self.rng = random.Random(seed)
        self.stats = {"up_lost": 0, "down_lost": 0}
        self.front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.front.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.front.bind(("127.0.0.1", 0))
        self.port = self.front.getsockname()[1]
        self.back = {}                             # client addr -> its socket toward the target
        self.lock = threading.Lock()
        self.heap, self.cv = [], threading.Condition()
        self.busy = {"up": 0.0, "down": 0.0}
        threading.Thread(target=self._front_reader, daemon=True).start()
        threading.Thread(target=self._sender, daemon=True).start()

    def _delay(self, L, n, k):
        if L.loss and self.rng.random() < L.loss:
            self.stats[k + "_lost"] += 1
            return None
        now = time.monotonic()
        start = max(now, self.busy[k])
        self.busy[k] = start + n / L.rate_Bps
        return self.busy[k] + (L.latency_ms + (self.rng.random() * L.jitter_ms if L.jitter_ms else 0.0)) / 1000.0

    def _push(self, at, sock, data, addr):
        import heapq
        with self.cv:
            heapq.heappush(self.heap, (at, id(data), sock, data, addr))
            self.cv.notify()

    def _sender(self):
        import heapq
        while True:
            with self.cv:
                while not self.heap:
                    self.cv.wait()
                at = self.heap[0][0]
                d = at - time.monotonic()
                if d > 0:
                    self.cv.wait(d)
                    continue
                _, _, sock, data, addr = heapq.heappop(self.heap)
            try:
                sock.sendto(data, addr)
            except OSError:
                pass

    def _front_reader(self):
        while True:
            data, cli = self.front.recvfrom(65535)
            with self.lock:
                b = self.back.get(cli)
                if b is None:
                    b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
                    b.bind(("127.0.0.1", 0))
                    self.back[cli] = b
                    threading.Thread(target=self._back_reader, args=(b, cli), daemon=True).start()
            at = self._delay(self.up, len(data), "up")
            if at is not None:
                self._push(at, b, data, self.target)

    def _back_reader(self, b, cli):
        while True:
            try:
                data, _ = b.recvfrom(65535)
            except OSError:
                return
            at = self._delay(self.down, len(data), "down")
            if at is not None:
                self._push(at, self.front, data, cli)


def _selftest():
    """A relay's added round trip and rate are what its profile says (within 20%)."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)

    def echo():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=lambda c=c: [c.sendall(b) for b in iter(lambda: c.recv(65536), b"")], daemon=True).start()
    threading.Thread(target=echo, daemon=True).start()
    ok = True
    for name in ("4g", "geo", "halow"):
        L = profile(name)
        L.jitter_ms = 0
        r = Relay(srv.getsockname(), L, name=name)
        s = socket.create_connection(("127.0.0.1", r.port))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        t0 = time.monotonic(); s.sendall(b"x"); s.recv(1); rtt = (time.monotonic() - t0) * 1000
        n = int(min(L.rate_Bps * 2, 2_000_000)); t0 = time.monotonic(); s.sendall(b"y" * n); got = 0
        while got < n:
            got += len(s.recv(65536))
        took = time.monotonic() - t0 - 2 * L.latency_ms / 1000 - min(L.chunk, n) / L.rate_Bps   # the echo's last chunk crosses twice
        rate = n / max(took, 1e-6)
        good = abs(rtt - 2 * L.latency_ms) <= 0.2 * 2 * L.latency_ms + 5 and abs(rate - L.rate_Bps) <= 0.2 * L.rate_Bps
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {name:6s} round trip {rtt:7.1f} ms (profile {2*L.latency_ms:g})  "
              f"rate {rate*8/1e3:9.1f} kb/s (profile {L.rate_Bps*8/1e3:g})")
        s.close(); r.close()
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
