#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""probe_ram.py HOST PORT -- is a FrogNet RAM reachable the production way?

Speaks FNW1 to the service's listener and nothing else. There is no HTTP port to try
instead: ram.php and MariaDB are on the far machine's loopback, behind the
listener. Pure standard library, so it runs from any machine, across the
Internet, with no tree installed.

Does exactly this, and raises on the first thing that is not as expected:
  1. request socket: HELLO <token>
  2. return socket:  HELLO RETURN:<token>, read the listener's HELLO back
  3. REQ_RAW  POST   ram.php?op=write   -> reply tagged seq 0, RESP_RAW 200
  4. REQ_RAW  GET    ram.php?op=read    -> reply tagged seq 1, the cell just written
  5. REQ_RAW  DELETE ram.php?op=remove  -> reply tagged seq 2

The memory is addressed by (service, variable, instance). Nothing in it, or in
this file, knows about sensors.

Frame layouts are core/semcache_wire.py's: 4-byte big-endian length, then
FNW1 + op. A reply is [seq:4][frame].
"""
import hashlib, json, socket, struct, sys, time, uuid

MAGIC = b"FNW1"
OP_REQ_RAW, OP_RESP_RAW, OP_HELLO = 0x03, 0x14, 0x50
ORIGIN_PORT = 8080           # ram.php on the far machine's loopback


def send(s, frame): s.sendall(struct.pack("!I", len(frame)) + frame)


def rx(s, n):
    d = b""
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c:
            raise ConnectionError("the far end closed the connection")
        d += c
    return d


def recv(s): return rx(s, struct.unpack("!I", rx(s, 4))[0])


def hello(text):
    b = text.encode("ascii")
    return MAGIC + bytes([OP_HELLO, len(b)]) + b


def main(host, port, prefix="/ram.php"):
    tok = "probe-" + uuid.uuid4().hex[:12]
    req = socket.create_connection((host, port), 10); send(req, hello(tok))
    ret = socket.create_connection((host, port), 10); send(ret, hello("RETURN:" + tok))
    ret.settimeout(20)
    f = recv(ret)
    if f[:4] != MAGIC or f[4] != OP_HELLO:
        raise RuntimeError("expected the far end's HELLO, got %r" % f[:16])
    print("handshake ok (token %s)" % tok)
    sent = [0]

    def raw(method, path, body=""):
        http = json.dumps({"method": method, "path": path,
                           "headers": {"Content-Type": "application/json"} if body else {},
                           "body": body, "host": "127.0.0.1", "port": ORIGIN_PORT},
                          separators=(",", ":")).encode()
        h = hashlib.sha256(http).digest()[:16]
        t0 = time.time()
        send(req, MAGIC + bytes([OP_REQ_RAW]) + h + struct.pack("!I", len(http)) + http)
        f = recv(ret)
        seq = struct.unpack("!I", f[:4])[0]; f = f[4:]
        if seq != sent[0]:
            raise RuntimeError("reply tagged seq %d, expected %d" % (seq, sent[0]))
        sent[0] += 1
        if f[:4] != MAGIC or f[4] != OP_RESP_RAW:
            raise RuntimeError("expected RESP_RAW, got op %#x: %r" % (f[4], f[5:120]))
        plen = struct.unpack("!I", f[21:25])[0]; p = f[25:25 + plen]
        status, hl = struct.unpack("!HH", p[:4])
        body_out = p[4 + hl:]
        if status != 200:
            raise RuntimeError("%s %s -> HTTP %d: %r" % (method, path, status, body_out[:200]))
        return json.loads(body_out), (time.time() - t0) * 1000.0

    o, ms = raw("POST", prefix + "?op=write",
                json.dumps({"service": "ram.probe", "variable": "probe", "instance": tok,
                            "bag": {"n": 1}}))
    if not o.get("ok"):
        raise RuntimeError("write refused: %r" % o)
    print("write  ok   %.0f ms" % ms)
    o, ms = raw("GET", prefix + "?op=read&service=ram.probe&variable=probe&instance=" + tok)
    mine = o.get("rows", [])
    if len(mine) != 1 or mine[0].get("bag") != {"n": 1}:
        raise RuntimeError("read did not return what was written: %r" % o)
    print("read   ok   %.0f ms" % ms)
    o, ms = raw("DELETE", prefix + "?op=remove&id=%d" % int(mine[0]["id"]))
    if o.get("removed") != 1:
        raise RuntimeError("remove did not remove exactly one cell: %r" % o)
    print("remove ok   %.0f ms" % ms)
    print("PASS  %s:%d answers FNW1 and reaches its memory" % (host, port))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    prefix = "/ram.php"
    if "--path-prefix" in a:
        i = a.index("--path-prefix"); prefix = a[i + 1]; del a[i:i + 2]
    if len(a) != 2:
        sys.exit("usage: probe_ram.py HOST PORT [--path-prefix /ram.php]")
    sys.exit(main(a[0], int(a[1]), prefix))
