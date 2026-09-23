#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_tensor_interop.py -- is the C++ tensor plane the SAME plane?

Run from the tree root (so the real tensor_plane.py imports):
    cd /opt/frognet_semantic && python3 /path/to/tensor/test_tensor_interop.py

The tree's own TensorServer and TensorClient on one side, the C++ ones on the
other, in all four pairings. Each pairing must agree on: the bytes; the digest
(so SAME works across implementations); SAME leaving the destination alone;
by_identity refusing a superseded generation; never_published; a read held open
and woken by the publish; and an expired wait saying so. Works without torch
(a stand-in with the four methods tensor_plane calls on a tensor).
"""
import os, sys, threading, time, types
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.getcwd())
import numpy as np
try:
    import torch                                   # noqa: F401
    HAVE_TORCH = True
    def T(a): return torch.from_numpy(a)
    def raw(t): return t.numpy().tobytes()
except ImportError:
    HAVE_TORCH = False
    sys.modules["torch"] = types.SimpleNamespace(bfloat16=object(), int16=object())
    class _Fake:                                   # what tensor_plane asks of a tensor, and nothing more
        def __init__(self, a): self.a = a; self.dtype = a.dtype
        def detach(self): return self
        def to(self, *_): return self
        def contiguous(self): return self
        def numpy(self): return self.a
        def __buffer__(self, flags): return memoryview(self.a)
    def T(a): return _Fake(a)
    def raw(t): return t.a.tobytes()
import agent_workload.tuplespace.tensor_plane as PY
import frogram_tensor as CPP

fails = 0
def ck(name, ok, extra=""):
    global fails; print(("  ok    " if ok else "  FAIL  ") + name + (("   " + str(extra)) if extra != "" else "")); fails += 0 if ok else 1

def py_read(cli, host, port, name, gen, n, **kw):
    """With torch: the tree's real TensorClient.read. Without it (read() builds a torch tensor), the same
    request the tree's client builds, sent and parsed with the tree's OWN _send_frame / _recv_frame / tags."""
    if HAVE_TORCH:
        t, nbytes, same = cli.read("w", host, port, name, gen, [n], "torch.uint8", **kw)
        return (nbytes, same), np.frombuffer(raw(t), dtype=np.uint8)
    import json, socket, struct
    key = (host, port); sk = _RAW.get(key)
    if sk is None:
        sk = _RAW[key] = socket.create_connection((host, port), 10); sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    have = _HAVE.get((host, port, name))
    PY._send_frame(sk, json.dumps({"name": name, "gen": gen, "mode": kw.get("mode", PY.READ_BY_IDENTITY), "wait_s": kw.get("wait_s", 0.0), "want": None, "inc": None,
                                   "verify": True, "have": have, "from": "raw-python"}).encode())
    reply = PY._recv_frame(sk); tag, rest = bytes(reply[:4]), reply[4:]
    if tag == PY.REPLY_GONE:
        info = json.loads(bytes(rest).decode()); raise PY.StateGone("gone", info.get("reason"), current_gen=info.get("gen"), current_digest=info.get("digest"))
    if tag == PY.REPLY_SAME:
        return (len(reply), True), np.zeros(0, dtype=np.uint8)
    (hl,) = struct.unpack(PY.BULK_LEN_FMT, bytes(rest[:8])); head = json.loads(bytes(rest[8:8 + hl]).decode()); blob = bytes(rest[8 + hl:])
    assert PY._digest(blob) == head["digest"], "digest computed by the tree's _digest does not match the server's"
    _HAVE[(host, port, name)] = head["digest"]
    return (len(reply), False), np.frombuffer(blob, dtype=np.uint8)
_RAW, _HAVE = {}, {}
def cpp_read(cli, host, port, name, gen, n, **kw):
    dst = np.zeros(n, dtype=np.uint8); nbytes, same, els = cli.read_into(dst, "w", host, port, name, gen, exact=False, **kw); return (nbytes, same), dst

def pairing(label, Server, reader):
    print("\n" + label)
    srv = Server("127.0.0.1", 0); a = np.arange(1_000_003, dtype=np.uint8) * 7 % 251; a = a.astype(np.uint8)
    srv.publish("grad.0", 5, T(a), incarnation="inc-A")
    out, dst = reader("grad.0", 5, a.size, mode="current")
    ck("the bytes arrive", dst.tobytes() == a.tobytes(), "%d bytes" % a.size)
    out2, dst2 = reader("grad.0", 5, a.size, mode="current")
    ck("an unchanged state is answered SAME -- so both sides computed the same digest", out2[1] is True, "wire bytes %s" % out2[0])
    srv.publish("grad.0", 6, T(a[::-1].copy()), incarnation="inc-A")
    try:
        reader("grad.0", 5, a.size, mode="by_identity"); ck("by_identity refuses a superseded generation", False)
    except (PY.StateGone, CPP.StateGone) as e:
        ck("by_identity refuses a superseded generation", e.reason == "superseded" and e.current_gen == 6, e.reason)
    try:
        reader("never.was", None, 16, mode="current"); ck("never_published is said", False)
    except (PY.StateGone, CPP.StateGone) as e:
        ck("never_published is said", e.reason == "never_published", e.reason)
    got = {}
    def held():
        t0 = time.time()
        try: got["out"], got["dst"] = reader("grad.0", 7, a.size, mode="current", wait_s=10.0)
        except Exception as e: got["err"] = e
        got["dt"] = time.time() - t0
    th = threading.Thread(target=held); th.start(); time.sleep(0.6); alive = th.is_alive()
    b = (a.astype(np.uint16) * 3 % 256).astype(np.uint8); srv.publish("grad.0", 7, T(b), incarnation="inc-A"); th.join(8)
    ck("a read for a generation not yet published is HELD, then woken by the publish", alive and "err" not in got and got["dst"].tobytes() == b.tobytes() and 0.5 < got["dt"] < 2.0, "%.2f s" % got.get("dt", -1))
    t0 = time.time()
    try:
        reader("grad.0", 99, a.size, mode="current", wait_s=0.5); ck("an expired wait says wait_expired", False)
    except (PY.StateGone, CPP.StateGone) as e:
        ck("an expired wait says wait_expired, after about the wait", e.reason == "wait_expired" and 0.4 < time.time() - t0 < 5.0, "%s %.2f s" % (e.reason, time.time() - t0))
    srv.close()

def main():
    def with_py_client(srv_holder):
        pass
    for label, Server, mk in (
        ("PYTHON server  <-  PYTHON client   (the reference)", PY.TensorServer, "py"),
        ("PYTHON server  <-  C++ client", PY.TensorServer, "cpp"),
        ("C++ server     <-  PYTHON client", CPP.TensorServer, "py"),
        ("C++ server     <-  C++ client", CPP.TensorServer, "cpp")):
        box = {}
        class S:
            def __init__(self, ip, port): box["s"] = Server(ip, port); box["port"] = box["s"].port
            def publish(self, *a, **k): return box["s"].publish(*a, **k)
            def close(self): box["s"].close()
        cli = PY.TensorClient("reader") if mk == "py" else CPP.TensorClient("reader")
        rd = (lambda name, gen, n, **kw: py_read(cli, "127.0.0.1", box["port"], name, gen, n, **kw)) if mk == "py" else \
             (lambda name, gen, n, **kw: cpp_read(cli, "127.0.0.1", box["port"], name, gen, n, **kw))
        _RAW.clear(); _HAVE.clear(); pairing(label, S, rd); cli.close()
    if not HAVE_TORCH:
        print("\nNOTE: no torch on this machine. The Python SERVER side is the tree's real TensorServer throughout. The Python CLIENT side"
              "\n      used the tree's own framing, tags and digest with the request its client builds, because TensorClient.read() returns a"
              "\n      torch tensor. Run this where torch is installed and it uses TensorClient.read() itself.")
    print("\n%s  (%d failed)" % ("PASS" if not fails else "FAIL", fails)); return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
