#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""plane_listener.py -- the FAST PLANE for one service: current values, nothing else.

    python3 plane_listener.py --listen 0.0.0.0:8789

State that changes every tick (a kart's position and velocity) does not belong
in the database. It belongs here: a name holds ONE current value with a
generation, in memory. Publishing replaces. Reading returns what is there now.
The memory (ram.php) holds the slow, structured state and the descriptor saying
where this plane is; this plane holds the bytes. Same split as the Communicator
(tuples + media planes) and Psychedelic (tuples + tensor plane), and the same
read tensor_plane.py has: block until the generation advances; expiry is its
own answer; never a substitute.

One permanent TCP connection per client, which the CLIENT dials. Frames are
[len:4 BE][op:1]... ; names are UTF-8; gen is an unsigned 64-bit the publisher
supplies (a race tick). A publish whose gen is not newer than the one held is
dropped: the newest state wins, and an older one can never replace a newer.

Every accepted publish also takes the next `seq` -- THE PLANE'S OWN arrival
order, across all names. Generations belong to publishers and are not
comparable between names; seq is, because it is assigned at the one place
everything arrives. A reader's `after` is a seq, so one number is exact for a
whole prefix: each state is delivered at most once, in arrival order. (The
memory does the same thing with its write order, for the same reason.)

  PUB   0x01 [gen:8][nlen:2][name][bytes]                 no reply (send and forget)
  READ  0x02 [rid:4][after:8][wait_ms:4][nlen:2][prefix]  every name under prefix with seq > after
  DATA  0x11 [rid:4][count:2] count * ([seq:8][gen:8][nlen:2][name][blen:4][bytes])
  NONE  0x12 [rid:4][reason:1]    1 = WAIT_EXPIRED   2 = NEVER_PUBLISHED (nothing under prefix at all)
  DROP  0x03 [nlen:2][name]                               the publisher withdraws its value

A READ with wait_ms parks on a condition and wakes on the publish that
satisfies it -- no polling on either side. Replies carry the request's rid, so
a parked read does not hold up anything else on the connection.
Standard library only.
"""
import argparse, socket, struct, sys, threading, time

OP_PUB, OP_READ, OP_DROP, OP_DATA, OP_NONE = 0x01, 0x02, 0x03, 0x11, 0x12
WAIT_EXPIRED, NEVER_PUBLISHED = 1, 2
MAX_FRAME, MAX_WAIT_MS = 1 << 20, 30000


def log(m): print("[PLANE] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


class Plane:
    def __init__(self):
        self.cells = {}                       # name -> (gen, bytes, seq)
        self.seq = 0
        self.cv = threading.Condition()
        self.pubs = self.superseded = 0

    def publish(self, name, gen, data):
        with self.cv:
            held = self.cells.get(name)
            if held is not None and gen <= held[0]:
                self.superseded += 1
                return
            self.seq += 1
            self.cells[name] = (gen, data, self.seq)
            self.pubs += 1
            self.cv.notify_all()

    def drop(self, name):
        with self.cv:
            self.cells.pop(name, None)

    def read(self, prefix, after, wait_ms):
        deadline = time.monotonic() + min(wait_ms, MAX_WAIT_MS) / 1000.0
        with self.cv:
            while True:
                under = [(q, g, n, d) for n, (g, d, q) in self.cells.items() if n.startswith(prefix)]
                new = sorted(x for x in under if x[0] > after)
                if new:
                    return new, 0
                left = deadline - time.monotonic()
                if left <= 0:
                    return [], (WAIT_EXPIRED if under or wait_ms else NEVER_PUBLISHED)
                self.cv.wait(left)


def rx(s, n):
    d = b""
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c:
            raise ConnectionError("closed")
        d += c
    return d


def serve(conn, addr, plane):
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    wlock = threading.Lock()

    def reply(frame):
        try:
            with wlock:
                conn.sendall(struct.pack("!I", len(frame)) + frame)
        except OSError:
            pass

    def do_read(rid, after, wait_ms, prefix):
        rows, why = plane.read(prefix, after, wait_ms)
        if not rows:
            return reply(bytes([OP_NONE]) + struct.pack("!IB", rid, why))
        out = bytes([OP_DATA]) + struct.pack("!IH", rid, len(rows))
        for q, g, n, d in rows:
            nb = n.encode()
            out += struct.pack("!QQH", q, g, len(nb)) + nb + struct.pack("!I", len(d)) + d
        reply(out)

    try:
        while True:
            n = struct.unpack("!I", rx(conn, 4))[0]
            if not 1 <= n <= MAX_FRAME:
                raise ValueError("frame length %d" % n)
            f = rx(conn, n)
            op = f[0]
            if op == OP_PUB:
                gen, nl = struct.unpack("!QH", f[1:11])
                plane.publish(f[11:11 + nl].decode(), gen, f[11 + nl:])
            elif op == OP_READ:
                rid, after, wait_ms, nl = struct.unpack("!IQIH", f[1:19])
                threading.Thread(target=do_read, args=(rid, after, wait_ms, f[19:19 + nl].decode()), daemon=True).start()
            elif op == OP_DROP:
                nl = struct.unpack("!H", f[1:3])[0]
                plane.drop(f[3:3 + nl].decode())
            else:
                raise ValueError("op %#x" % op)
    except Exception as e:
        log("connection from %s ended (%s)" % (addr[0], type(e).__name__))
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0:8789")
    a = ap.parse_args()
    h, p = a.listen.rsplit(":", 1)
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((h, int(p))); srv.listen(1024)
    plane = Plane()
    log("fast plane listening on %s" % a.listen)
    while True:
        c, addr = srv.accept()
        threading.Thread(target=serve, args=(c, addr, plane), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
