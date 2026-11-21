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
"""collective_tier.py -- the tier that should have caught 2026-09-14.

Everything under agent_workload/ -- the DDP comm hooks, the psychedelic
backend's collectives, the c10d store over the tuple space -- had NO simulator
coverage at all. Every defect in it was therefore found by John running a
benchmark on four machines across a continent and reading py-spy dumps by hand.
That is not a test procedure, it is an outage with a stopwatch.

Three defects found that day, all of which this tier fails on the old code:

  1. REDUCTION ORDER. faithful_hook built its stack as [self, then every OTHER
     rank ascending], so rank 1 summed [1,0,2,3] and rank 2 summed [2,0,1,3].
     Float addition is not associative, so the four ranks produced different
     weights and diverged over a run. Observed as gloo reporting param_sha
     3622a2291d8b592a on all four ranks while the hook arms reported
     73a583f7ab59285d and 46a94a32156cfc45. The hook's own docstring promises
     arithmetic identity with all_reduce, which makes it a defect.

  2. A BLOCKING READ THAT DOES NOT BLOCK. broadcast() and _await_consumers()
     called _read_all() with no wait_s, so they spun at a 2 ms ceiling. On
     Seattle5: 649 requests in 11.5 s for one key, max 1 in flight -- so the
     proxy's request coalescing had nothing to join, because serial polls
     cannot coalesce.

  3. AN UNSATISFIABLE WAIT CONDITION. min_rows must match the number of
     writers, and they differ per operation: a broadcast has ONE producer (the
     root), _await_consumers wants W-1 consumed-markers, a collective wants
     all W. Asking for W on a broadcast is a condition no store can ever
     satisfy, so the read would hold for the full wait and return empty --
     slower than the spin it replaced. A test that only checks "wait_s is
     present" would have passed that and shipped it.

The tier runs the REAL hooks against real torch tensors, and the real call
sites are checked structurally. It does not simulate the hooks; simulating the
thing under test is how a reduction-order bug survives a green suite.

[NO_FALLBACK_V1] Missing torch is a hard fail, not a skip. This tier exists
because this code was untested; a tier that quietly passes when it cannot run
is the same hole with a green tick on it.
"""
from __future__ import annotations

import ast
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEM = os.path.dirname(_HERE)
if _SEM not in sys.path:
    sys.path.insert(0, _SEM)

FAILS = []
PASSES = 0


def check(ok, what):
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {what}")
    else:
        FAILS.append(what)
        print(f"  [FAIL] {what}")


def _tuplespace():
    return os.path.join(_SEM, "agent_workload", "tuplespace")


# ---------------------------------------------------------------------------
# PLANE 1 -- reduction order, against real tensors
# ---------------------------------------------------------------------------
def plane_reduction_order():
    print("=== PLANE 1: every rank reduces in the same order ===")
    import torch

    W = 4
    # A contribution large enough that the small ones are lost unless they are
    # summed together first. Real gradients have this dynamic range; a fixture
    # of similar magnitudes would pass under either ordering and prove nothing.
    vals = [torch.full((8,), 0.4), torch.full((8,), 0.4),
            torch.full((8,), 0.4), torch.full((8,), 1e7)]

    def reduce_in(order):
        return torch.stack([vals[r] for r in order]).mean(0)

    old = [reduce_in([r] + [q for q in range(W) if q != r]) for r in range(W)]
    new = [reduce_in(list(range(W))) for _ in range(W)]

    def distinct(ts):
        seen = []
        for t in ts:
            if not any(torch.equal(t, u) for u in seen):
                seen.append(t)
        return len(seen)

    check(distinct(old) > 1,
          f"fail-on-old: self-first ordering gives {distinct(old)} distinct "
          f"results across ranks")
    check(distinct(new) == 1,
          "rank-ordered reduction is bit-identical on every rank")

    # and the property that matters downstream: identical weights => identical
    # param hash, which is the check the benchmark actually reports.
    import hashlib

    def sha(t):
        return hashlib.sha256(t.numpy().tobytes()).hexdigest()[:16]

    check(len({sha(t) for t in new}) == 1,
          f"param_sha agrees across ranks ({sha(new[0])})")
    check(len({sha(t) for t in old}) > 1,
          "fail-on-old: param_sha disagreed, which is exactly what ddp17xx "
          "reported")


# ---------------------------------------------------------------------------
# PLANE 2 -- the hooks as written, not a model of them
# ---------------------------------------------------------------------------
def plane_hook_sources():
    print("\n=== PLANE 2: the shipped hooks index by rank ===")
    path = os.path.join(_tuplespace(), "ddp_hook.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    for name in ("faithful_hook", "fresh_only_hook", "every_k_hook"):
        fns = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == name]
        if not fns:
            check(False, f"{name} exists")
            continue
        body = ast.get_source_segment(src, fns[0])
        check("parts = [t.clone()]" not in body,
              f"{name}: no self-first accumulator")
        # [REDUCTION_ORDER_IS_RANK_ORDER_V1] Since 12 Nov (the September hooks revived, 09388fa5) the rank order is
        # in each hook itself: a slot per rank, self at its own rank, a skipped peer's slot left empty, the stack
        # built over the slots in ascending order. (From 11 Nov until then it was in one _gather; these checks
        # still named it and had failed since, unseen: the tier needs torch and the sandbox had none.)
        check("slots[state.rank] = t.clone()" in body, f"{name}: self placed at its own rank index")
        check("parts = [x for x in slots if x is not None]" in body and "torch.stack(parts).mean(0)" in body,
              f"{name}: summed over the slots in rank order, a skipped peer leaving a hole")


# ---------------------------------------------------------------------------
# PLANE 3 -- every waiting read blocks, with a satisfiable condition
# ---------------------------------------------------------------------------
def plane_blocking_reads():
    print("\n=== PLANE 3: every wait is one held read in the memory ===")
    # [ALL_IN_THE_REGION_V1] A rank waits by holding one read on the cell it needs, in FrogTorch's own RAM. The
    # loops this plane used to check -- reads re-asked until rows appeared, consumers acknowledging, a root
    # waiting for the acknowledgements, BLOCK_CAP re-arming every 5 s -- are what was removed. fail-on-old: none of it
    # may come back.
    # (Until 19 Nov this plane also required that _await_consumers, _consumed, _read_fresh, _PLANES, _shared_plane
    # and BLOCK_CAP be absent: the 11 Nov design had removed them. John's ruling of 12 Nov, "Go back and revive the
    # code that worked before", brought the September backends back with them (09388fa5), and of 17 Nov, "I thought
    # what we had in there now was working performant. Why would I give that up?", keeps them. Those six checks
    # asserted a design that was set aside and are gone. The checks on sleeping below are his standing rule and stay;
    # they failed on nine waits from 12 Nov until [A_WAIT_IS_ONE_HELD_READ_V1], 19 Nov.)
    #
    # [THE_TENSORS_ARE_IN_THE_RAM_V1] [THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1] No rank listens and nothing in
    # FrogTorch's path to its RAM opens a socket: the backends make the RAM's classes, and those and the RAM itself
    # reach the network only through a transport driver's calls. fail-on-old: before 19 Nov psychedelic_backend.py
    # and ddp_hook.py made TensorServer (it listened), and _Session and RamServer called socket themselves.
    def _calls(path):
        t = ast.parse(open(path, encoding="utf-8").read())
        out = set()
        for c in ast.walk(t):
            if isinstance(c, ast.Call):
                f = c.func
                out.add(f.id if isinstance(f, ast.Name) else
                        ("%s.%s" % (f.value.id, f.attr) if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                         else getattr(f, "attr", "")))
        return out
    harness = os.path.normpath(os.path.join(_SEM, "..", "..", "usr", "local", "frognet-ai", "meshfeed.py"))
    for f in (os.path.join(_tuplespace(), "psychedelic_backend.py"), os.path.join(_tuplespace(), "ddp_hook.py"),
              os.path.join(_tuplespace(), "frogtorch.py"), os.path.join(_tuplespace(), "frogtrain.py"), harness):
        made = _calls(f) & {"TensorServer", "StreamSubscription", "TensorClient", "P.TensorServer",
                            "P.StreamSubscription", "P.TensorClient", "MemoryPlane"}
        check(not made, "fail-on-old: %s makes nothing that listens or connects rank to rank (%s)"
              % (os.path.basename(f), ", ".join(sorted(made)) or "none"))
    for f in ("ram_plane.py", "frogtorch_ram.py", "frogtorch_ram_client.py", "frogtorch.py", "frogtrain.py",
              "../aiconnect/producer.py", "../aiconnect/service.py"):
        opens = _calls(os.path.join(_tuplespace(), f)) & {"socket.socket", "socket.create_connection", "create_connection"}
        check(not opens, "fail-on-old: %s opens no socket; its connections are a transport driver's (%s)"
              % (f, ", ".join(sorted(opens)) or "none"))
    # no sleeping anywhere a rank waits; the one sleep left is the diagnostic watchdog's report interval
    for f in ("psychedelic_backend.py", "torch_backend.py", "ddp_hook.py", "frogtrain.py", "torch_store.py",
              "frogtorch.py", "frogtorch_ram.py", "frogtorch_ram_client.py", "reduce_by_read.py"):
        tree = ast.parse(open(os.path.join(_tuplespace(), f), encoding="utf-8").read())
        bad = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if fn.name in ("_psy_start_watchdog", "_loop"):
                continue
            for c in ast.walk(fn):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "sleep"
                        and isinstance(c.func.value, ast.Name) and c.func.value.id == "time"):
                    bad.append(f"{fn.name}:{c.lineno}")
        check(not bad, f"{f}: no time.sleep where a rank waits ({', '.join(bad) or 'none'})")


# ---------------------------------------------------------------------------
# PLANE 4 -- PyTorch's tensors are cells in the RAM; the session with the RAM is a transport driver's connection
# ---------------------------------------------------------------------------
# (Until 19 Nov this plane ran psychedelic_backend.broadcast's source against a stand-in for _read_peer, a method of
# the 11 Nov design. The September broadcast that was revived on 12 Nov has no such method, so its three checks had
# failed since. A broadcast between real ranks is checked in plane_ranks_do_not_listen below.)
import contextlib


@contextlib.contextmanager
def _setting(word, ram_words=None, rams_at=None):
    """This process's transport setting, as /etc/frognet/semantic_transport would give it, for the body's length."""
    if _SEM not in sys.path:
        sys.path.insert(0, _SEM)
    from core import homa_transport as HT
    saved = (HT._chosen, dict(HT._ram_words), HT._rams_at)
    HT._chosen = word
    HT._ram_words.clear()
    HT._ram_words.update(ram_words or {})
    if rams_at is not None:
        HT._rams_at = rams_at
    try:
        yield HT
    finally:
        HT._chosen = saved[0]
        HT._ram_words.clear()
        HT._ram_words.update(saved[1])
        HT._rams_at = saved[2]


@contextlib.contextmanager
def _a_ram(host="127.0.0.1"):
    """A real FrogTorch RAM in this process, and FrogTorch pointed at it as the one RAM."""
    from agent_workload.tuplespace.frogtorch_ram import RamServer
    from agent_workload.tuplespace import frogtorch_ram_client as RC
    srv = RamServer("0.0.0.0", 0)
    env = os.environ.get("FROGTORCH_RAMS")
    os.environ["FROGTORCH_RAMS"] = "%s:%d" % (host, srv.port)
    saved, RC._RAM = RC._RAM, None
    try:
        yield srv
    finally:
        srv.close()
        RC._RAM = saved
        if env is None:
            os.environ.pop("FROGTORCH_RAMS", None)
        else:
            os.environ["FROGTORCH_RAMS"] = env


def _until(cond, s=3.0):
    """Test only: wait for something another thread or process does."""
    import time
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def plane_tensors_in_the_ram():
    """[THE_TENSORS_ARE_IN_THE_RAM_V1] Behaviour, against a real RAM, on each driver: the owner writes its cell once;
    a read held on the family is answered by that write; the bytes are exact; a cell written for k readers goes
    when the k-th has it; an owner's cells are its own; they go with its session; a named read is held for the
    write. John, 17 Nov: "it should be using held reads, and when another node needs a tensor it does not ask the
    other node for it. It gets it from tuple space"."""
    print("\n=== PLANE 4: a tensor is a cell in the RAM, and a held read is answered by its owner's write ===")
    import threading, time
    import torch
    for word in ("tcp", "chorus"):
        with _setting(word), _a_ram() as srv:
            from agent_workload.tuplespace.ram_plane import RamPlane, RamStream, RamTensorClient
            from agent_workload.tuplespace.tensor_plane import READ_CURRENT, plane_view, writable_bytes
            N = 70_000                                   # 280,000 bytes: over chorus's 240,000-byte segment
            a, b = torch.randn(N), torch.randn(N)
            owner, other = RamPlane("10.9.9.1"), RamPlane("10.9.9.2")
            st = RamStream("r1", "10.9.9.1", owner.port, "tier4", family=[r"g\.ars\.\d+\.1"])
            dst = torch.empty(N)
            st.expect("g.ars.1.1", writable_bytes(dst))
            threading.Timer(0.4, lambda: owner.publish("g.ars.1.1", 1, a, incarnation="0", readers=1)).start()
            t0 = time.perf_counter()
            c = st.take("g.ars.1.1", timeout_s=10)
            held = time.perf_counter() - t0
            check(c["direct"] and torch.equal(dst, a) and 0.3 < held < 5 and st.stats()["driver"] == word,
                  "%s: a read held on the family is answered by the owner's write (%.2fs), exact, into its tensor"
                  % (word, held))
            check(_until(lambda: srv.region.stats()["tensors"] == 0) and srv.region.retired == 1,
                  "%s: a cell written for one reader is gone from the RAM once that reader has it" % word)
            other.publish("g.ars.2.1", 2, a, incarnation="0", readers=1)      # the same name, another owner
            owner.publish("g.ars.2.1", 2, b, incarnation="0", readers=1)
            c = st.take("g.ars.2.1", timeout_s=10)
            check(not c["direct"] and torch.equal(plane_view(c["buf"], torch.float32, N), b),
                  "%s: an owner's cells are its own: another owner's cell of the same name is not this reader's" % word)
            owner.publish("g.ars.7.2", 7, a, incarnation="0", readers=1)
            st2 = RamStream("r2", "10.9.9.1", owner.port, "tier4b", family=[r"g\.ars\.\d+\.2"])
            c = st2.take("g.ars.7.2", timeout_s=10)
            check(torch.equal(plane_view(c["buf"], torch.float32, N), a),
                  "%s: a cell written before the read was held answers it at once" % word)
            st2.request([("g.x.1", None)])
            owner.publish("g.x.1", 1, b, incarnation="0")
            c = st2.take("g.x.1", timeout_s=10)
            check(torch.equal(plane_view(c["buf"], torch.float32, N), b),
                  "%s: a cell outside the family is read by one held read on its name" % word)
            cli = RamTensorClient("tier4")
            cli.hold_s = 10.0
            threading.Timer(0.4, lambda: owner.publish("g.bc.3", 3, a, incarnation="0")).start()
            dst2 = torch.empty(N)
            t0 = time.perf_counter()
            cli.read_into(dst2, "r0", "10.9.9.1", owner.port, "g.bc.3", 3, mode=READ_CURRENT)
            held = time.perf_counter() - t0
            check(torch.equal(dst2, a) and 0.3 < held < 5,
                  "%s: [A_READ_IS_HELD_UNTIL_THE_WRITE_V1] a named read asked before its owner's write is held for it "
                  "(%.2fs), not refused" % (word, held))
            mine = lambda: [n for n, k in srv.region._tensors.items() if k.value is not None and n.startswith("10.9.9.1:")]
            theirs = lambda: [n for n, k in srv.region._tensors.items() if k.value is not None and n.startswith("10.9.9.2:")]
            had = len(mine())
            owner.close()
            check(had >= 2 and _until(lambda: not mine()) and len(theirs()) == 1,
                  "%s: a rank's cells go when its session ends (%d went); another rank's stay" % (word, had))
            other.close()
            cli.close()
            st.close()
            st2.close()


def plane_ram_session_is_a_drivers_connection():
    """[THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1] John, 17 Nov: "The connection to the RAM host is expected and
    should be transport transparent." fail-on-old: until 19 Nov _Session opened a TCP socket to the RAM's port
    whatever the setting said."""
    print("\n=== PLANE 4a: a rank's session with the RAM is the driver the setting names ===")
    import json, socket, struct, threading, time
    if _SEM not in sys.path:
        sys.path.insert(0, _SEM)
    from agent_workload.tuplespace import frogtorch_ram_client as RC
    from core import chorus as HF

    def _read(conn, n):
        conn.settimeout(5)
        buf = b""
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                break
            buf += part
        return buf

    head = {"op": "cput", "service": "s", "var": "v", "scope": "x", "value": {"k": 1}, "life": "data", "ack": False}
    thead = {"op": "tput", "name": "t", "gen": 1, "inc": None, "life": "data", "ack": False}
    blob = bytes(range(256)) * 40
    # tcp: the bytes are the ones this session has always sent
    with _setting("tcp"):
        ls = socket.socket()
        ls.bind(("127.0.0.1", 0))
        ls.listen(1)
        s = RC._Session("127.0.0.1", ls.getsockname()[1])
        conn, _ = ls.accept()
        s.send(head)
        s.send(thead, blob)
        want = b"".join(struct.pack("!Q", len(x)) + x for x in (json.dumps(head).encode(), json.dumps(thead).encode(), blob))
        got = _read(conn, len(want))
        check(got == want, "tcp: the bytes on the wire are the ones this session has always sent "
                           "(<!Q length><payload>; %d of %d compared)" % (len(got), len(want)))
        s.close(); conn.close(); ls.close()
    # chorus: the RAM's port plus one, and Homa's DATA frame
    with _setting("chorus"):
        ls = socket.socket()
        ls.bind(("127.0.0.1", 0))
        ls.listen(1)
        try:
            s = RC._Session("127.0.0.1", ls.getsockname()[1] - 1)
            conn, _ = ls.accept()
            s.send(head)
            payload = json.dumps(head).encode()
            # [THE_HEADER_IS_A_DIFFERENCE_V1] what crosses: the opener's word, then the DATA header as a difference
            # against a header of zeros (it is the connection's first), then the payload. The far end of the driver
            # puts the whole Homa DATA frame back; here the bytes are read as they crossed.
            got = _read(conn, len(HF.WIRE))
            rest = b""
            conn.settimeout(5)
            tail = payload[HF.PREFIX:]
            while not rest.endswith(tail):                     # the rest of the one frame that was sent
                part = conn.recv(4096)
                if not part:
                    break
                rest += part
            check(got == HF.WIRE and rest.endswith(tail) and len(rest) < len(payload) + 30 and HF.COMMON.size + HF.DATA_HDR.size == 62,
                  "fail-on-old: told chorus, the session connects to the RAM's port plus one, says how it sends, and sends "
                  "Homa's DATA frame with its header as a difference (the first frame of a connection: %d bytes crossed for a %d-byte header and a %d-byte "
                  "payload)" % (len(rest), HF.HEADER, len(payload)))
            c2 = HF._Conn(("127.0.0.1", 1)); c2._said_wire = True
            import io as _io
            c2._rf = _io.BytesIO(rest)
            n2, back = c2.unpack(c2.receive())
            f = HF._fields(HF.COMMON.pack(*c2._hdr_in[:11]) + HF.DATA_HDR.pack(*c2._hdr_in[11:]))
            check(back == payload and f[4] == HF.DATA and f[6] == HF.HIJACK_FLAGS and f[9] == HF.HIJACK_URGENT and f[11] == len(payload),
                  "and the far end's receive() puts back a whole Homa DATA frame: type, hijack marks, length and the payload exactly")
            s.close(); conn.close()
        except ConnectionError as e:
            check(False, "fail-on-old: told chorus, the session connects to the RAM's port plus one (%s)" % e)
        ls.close()
    # a word per RAM: one memory, both drivers, chosen by which RAM is on the machine
    at = lambda host: ("databasehost.frognet",) if host == "127.0.0.9" else ()
    with _setting("auto", {"databasehost.frognet": "chorus"}, at), _a_ram() as srv:
        check(set(srv.listeners) == {"tcp", "chorus"}
              and srv.listeners["chorus"].port == srv.listeners["tcp"].port + 1,
              "the RAM listens with every driver the setting names: tcp on its port, chorus on its port plus one")
        s9, s8 = RC._Session("127.0.0.9", srv.port), RC._Session("127.0.0.8", srv.port)
        v9, v8 = s9.call({"op": "version"}), s8.call({"op": "version"})
        check((s9.end.name, v9.get("driver"), s8.end.name, v8.get("driver")) == ("chorus", "chorus", "tcp", "tcp"),
              "[A_WORD_PER_RAM_CONNECTION_V1] the connection to the machine databasehost.frognet is on is chorus, "
              "another machine's is the node's word (%s/%s, %s/%s)" % (s9.end.name, v9.get("driver"), s8.end.name,
                                                                      v8.get("driver")))
        s9.send(head)
        rows = s8.call({"op": "cget", "service": "s", "var": "v", "min_rows": 1, "wait_s": 5.0}).get("rows", [])
        check(len(rows) == 1 and rows[0]["value"] == {"k": 1},
              "the transport is transparent: a cell written over chorus is read over tcp, unchanged")
        s9.close(); s8.close()
    # a session ends with its connection
    with _setting("tcp"), _a_ram() as srv:
        hp = ("127.0.0.1", srv.port)
        r = RC.Ram(regions=[hp])
        r.put("tier4a.members", "r0", "r0", {"ip": "x"}, life="session")
        there = lambda: srv.region.cget("tier4a.members", None, 0, 0.0)
        check(_until(lambda: len(there()) == 1), "a session-held cell is in the RAM while its session is open")
        err, box = [], []

        def held():                                      # a rank's data session is its thread's own
            box.append(r.session(hp))
            try:
                r.get_all("tier4a.nothing", wait_s=30.0, min_rows=1)
            except ConnectionError as e:
                err.append(e)
            box.append(r.session(hp))
            box.append(r.get_all("tier4a.members"))
        th = threading.Thread(target=held)
        th.start()
        _until(lambda: len(box) >= 1)
        time.sleep(0.3)
        t0 = time.perf_counter()
        box[0].end.close()                              # the connection ends under a held read
        th.join(5)
        check(len(err) == 1 and time.perf_counter() - t0 < 3 and box[0].ended is not None,
              "[A_SESSION_ENDS_WITH_ITS_CONNECTION_V1] a read held on a connection that ends is told at once (%r)"
              % (err[0] if err else None))
        check(len(box) == 3 and box[1] is not box[0] and box[2] != [],
              "the next request opens a new connection and a new session")
        r._member_session.end.close()
        check(_until(lambda: there() == []), "the cells a session held go when its connection ends")
        try:
            r.put("tier4a.members", "r0", "r0", {"ip": "x"}, life="session")
            check(False, "the session that held this rank's membership is not silently replaced")
        except ConnectionError:
            check(True, "the session that held this rank's membership is not silently replaced")
    # kernel Homa is not carried yet, and says so
    with _setting("homa"):
        try:
            RC._Session("127.0.0.1", 1)
            check(False, "told homa, a session with the RAM stops and says kernel Homa is not carried yet")
        except ConnectionError as e:
            check("step 4" in str(e), "told homa, a session with the RAM stops and says kernel Homa is not carried yet")


def _own_sockets():
    """This process's TCP sockets, from the kernel: (ports it listens on, far ports it is connected to)."""
    inodes = set()
    for fd in os.listdir("/proc/self/fd"):
        try:
            link = os.readlink("/proc/self/fd/" + fd)
        except OSError:
            continue
        if link.startswith("socket:["):
            inodes.add(link[8:-1])
    listen, far = [], []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(name).read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if f[9] in inodes:
                if f[3] == "0A":
                    listen.append(int(f[1].rsplit(":", 1)[1], 16))
                else:
                    far.append(int(f[2].rsplit(":", 1)[1], 16))
    return sorted(listen), sorted(set(far))


def _rank_worker(rank, world, ns):
    """One real rank of a Psychedelic group (run as a process by plane_ranks_do_not_listen)."""
    import hashlib, json
    sys.path.insert(0, _SEM)
    import torch
    import torch.distributed as dist
    from agent_workload.tuplespace import torch_store  # noqa: F401  frognet://
    from agent_workload.tuplespace import psychedelic_backend
    psychedelic_backend.register("psychedelic")
    dist.init_process_group("psychedelic", init_method="frognet://%s" % ns, rank=rank, world_size=world)
    big = [torch.randn(200_000, generator=torch.Generator().manual_seed(r)) for r in range(world)]
    t = big[rank].clone()
    dist.all_reduce(t)
    b = torch.arange(1000.) if rank == 0 else torch.zeros(1000)
    dist.broadcast(b, 0)
    listen, far = _own_sockets()
    print("TIER4 " + json.dumps({"rank": rank, "sum_ok": bool(torch.allclose(t, sum(big), rtol=1e-5, atol=1e-5)),
                                 "bc_ok": bool(torch.equal(b, torch.arange(1000.))), "listen": listen, "far": far,
                                 "sha": hashlib.sha256(t.numpy().tobytes()).hexdigest()[:16]}), flush=True)
    dist.destroy_process_group()
    return 0


def plane_ranks_do_not_listen():
    """Three real ranks, one real RAM, each as its own process, on each driver. Read from the kernel, not from the
    code: no rank listens on anything, and every connection a rank has goes to the RAM. fail-on-old: every rank
    listened (TensorServer) and held a connection to every other rank."""
    print("\n=== PLANE 4b: three real ranks: none listens, each talks only to the RAM, on either driver ===")
    import json, socket, subprocess, time
    shas = {}
    for word in ("tcp", "chorus"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = dict(os.environ, PYTHONPATH=_SEM, FROGNET_SEM_TRANSPORT=word, FROGTORCH_RAMS="127.0.0.1:%d" % port,
                   FROGNET_PSY_DIAG="0", FROGNET_DIAG="0")
        ram = subprocess.Popen([sys.executable, "-m", "agent_workload.tuplespace.frogtorch_ram", "--port", str(port),
                                "--no-announce"], env=env, cwd=_SEM, stdout=subprocess.DEVNULL)
        there = port + (1 if word == "chorus" else 0)

        def up():
            try:
                socket.create_connection(("127.0.0.1", there), timeout=1).close()
                return True
            except OSError:
                return False
        _until(up, 15.0)
        ns = "tier4b%s%d" % (word, port)
        procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--rank-worker", str(r), "3", ns],
                                  env=dict(env, FROGNET_PSY_HOST="127.0.0.%d" % (r + 2)), cwd=_SEM,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for r in range(3)]
        got = []
        for p in procs:
            try:
                out = p.communicate(timeout=180)[0]
            except subprocess.TimeoutExpired:
                p.kill()
                out = p.communicate()[0]
            line = [l for l in out.splitlines() if l.startswith("TIER4 ")]
            got.append(json.loads(line[-1][6:]) if line else {"error": out[-400:]})
        ram.terminate()
        ok = all("error" not in g for g in got)
        check(ok and all(g["sum_ok"] and g["bc_ok"] for g in got) and len({g["sha"] for g in got}) == 1,
              "%s: all_reduce and broadcast are right on every rank and the ranks agree (%s)"
              % (word, got if not ok else got[0]["sha"]))
        if not ok:
            continue
        shas[word] = got[0]["sha"]
        check(all(g["listen"] == [] for g in got),
              "fail-on-old: %s: no rank listens on any port (%s)" % (word, [g["listen"] for g in got]))
        check(all(g["far"] == [there] for g in got),
              "fail-on-old: %s: every connection a rank has goes to the RAM, port %d (%s)"
              % (word, there, [g["far"] for g in got]))
    check(len(shas) == 2 and shas["tcp"] == shas["chorus"],
          "the transport is transparent: the same bits on tcp and on chorus (%s)" % shas)


# ---------------------------------------------------------------------------
# PLANE 5 -- a tuple value survives the store unchanged
# ---------------------------------------------------------------------------
def plane_object_is_not_a_list():
    print("\n=== PLANE 5: an object keyed 0..N-1 is not a list ===")
    import types

    # -- the store -----------------------------------------------------------
    # [API_PHP_IS_VAR_WWW_V1] api.php is var/www/html/api.php: in the repository beside opt/, and on a node at
    # /var/www/html. _SEM/../../var/www/html is that file in both (there has been no web/ copy under
    # opt/frognet_semantic; the check failed on every node, Seattle7 2026-10-14).
    php = _api_php()
    if not os.path.exists(php):
        check(False, "var/www/html/api.php is present (looked at %s)" % php)
    else:
        src = open(php, encoding="utf-8").read()
        check("$jo = json_decode($raw);" in src,
              "read_json_body decodes a second time without assoc")
        check("$j['jsonData'] = $jo->jsonData;" in src,
              "the tuple value is taken from the object decode")
        check("$j['items'][$i]['jsonData'] = $io->jsonData;" in src,
              "upsert_batch items carry the same substitution")
        check("$j = json_decode($r['jsonData'], true);" not in src,
              "fail-on-old: the read path no longer re-encodes from assoc")
        check("$j = json_decode($r['jsonData']);" in src,
              "the read path decodes without assoc")

    # -- the reader ----------------------------------------------------------
    # drift() as shipped, against a bag that came back flattened. Executed, not
    # modelled: the failure was an AttributeError inside this exact method.
    path = os.path.join(_tuplespace(), "ddp_hook.py")
    hsrc = open(path, encoding="utf-8").read()
    fn = [n for n in ast.walk(ast.parse(hsrc))
          if isinstance(n, ast.FunctionDef) and n.name == "drift"][0]
    body = ast.get_source_segment(hsrc, fn)
    ns = {"Optional": object}
    exec("class _S:\n" + "\n".join("    " + l for l in body.splitlines()), ns)

    def state(pub_n):
        s = ns["_S"].__new__(ns["_S"])
        s.rank, s.dbhost = 0, "db.frognet"
        s.pub_n = {0: 7, 1: 7}
        s.bags = {1: {"step": 5, "pub_n": pub_n}}
        return s

    good = state({"0": 5, "1": 5})
    check(good.drift(1, 0) == 2,
          "drift reads a map of bucket -> publish count (7 - 5 = 2)")
    check(good.drift(1, 1) == 2, "drift matches on the int key too")

    # There is deliberately no test here for what drift() does with a
    # flattened pub_n. It dies, as any reader would, and that is the correct
    # shape: the store is the only place this can be wrong and the only place
    # it is fixed. A reader that coped would be the workaround.

    check(state(None).drift(1, 0) is None,
          "no pub_n yet is still None, not an error")

    # [NO_LOCAL_WORKAROUND_V1] The store is the only place this was broken and
    # the only place it is fixed. A reader that also checks would be a second
    # thing to keep in step, and a refusal gate in code that is trying to
    # train.
    dsrc = ast.get_source_segment(hsrc, fn)
    check("isinstance" not in dsrc,
          "drift does not verify the store's version before running")
    check("pn.get(key)" not in dsrc and 'get(str(key), ' not in dsrc,
          "drift reads the string key, not both keys hoping one answers")

    psrc = open(os.path.join(_tuplespace(), "psychedelic_backend.py"),
                encoding="utf-8").read()
    check("_drop_scratch" not in psrc,
          "the scratch-buffer retry that corrected a guessed length is gone")
    tsrc = open(os.path.join(_tuplespace(), "tensor_plane.py"),
                encoding="utf-8").read()
    check("def _drop_scratch" not in tsrc,
          "and so is the method it called, rather than left uncalled")


# ---------------------------------------------------------------------------
# PLANE 6 -- every rank writes only its own memory
# ---------------------------------------------------------------------------
def plane_read_ahead_keying():
    """[EVERY_RANK_WRITES_ONLY_ITS_OWN_MEMORY_V1] John: "Nobody writes into anybody else's space, period."

    The shard all-reduce's writes go to the writer's own region and nowhere else; its reads are held reads on the
    owner's region; phase 2's reads are up before phase 1's reduce. Fail-on-old: 91e90c2 wrote each slice into the
    consumer's region (`region=self._region_of(k)`) and read locally. (The September read-ahead this plane used to
    check is gone with the round-named cells it guessed at; phase 2's early reads are the overlap that remains.)
    """
    print("\n=== PLANE 6: every rank writes only its own memory ===")
    path = os.path.join(_tuplespace(), "psychedelic_backend.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    ar = fn.get("_allreduce_sharded")
    check(ar is not None, "_allreduce_sharded exists")
    if ar is None:
        return
    pubs = [c for c in ast.walk(ar) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == "publish"]
    check(len(pubs) == 2, "two writes: the slices and the reduced slice (%d)" % len(pubs))
    check(all(not any(k.arg == "region" for k in c.keywords) for c in pubs),
          "fail-on-old: no write names another rank's region")
    seg = ast.get_source_segment(src, ar)
    check(seg.index('"p2")') < seg.index("acc_fn(own"),
          "phase 2's held reads are up before phase 1 reduces")
    # The default path (FROGNET_PSY_STREAM, on) is _allreduce_streamed: the September reduction on the family link.
    # (Until 19 Nov three checks here named _read_owner_into and _region_of, the 11 Nov design's reads; they had
    # failed since the September code was revived on 12 Nov.) What is checked now is where its tensors are:
    # [THE_TENSORS_ARE_IN_THE_RAM_V1] every write is a publish of this rank's own cell, every read is a take from a
    # read held in the RAM on a peer's family, and the reduced slice is written once for all its readers.
    st = fn.get("_allreduce_streamed")
    check(st is not None, "_allreduce_streamed exists")
    if st is not None:
        sseg = ast.get_source_segment(src, st)
        check("self._take(" in sseg and "read_into(" not in sseg and "_peer_client(" not in sseg,
              "every read of a peer's cell is a take from the read held on its family")
        check("RamStream(" in ast.get_source_segment(src, fn["_stream"]),
              "fail-on-old: that read is held in the RAM (RamStream), not on a connection to the peer")
        check(sseg.count("self._server.publish(nm, seq, own") == 1 and "readers=W - 1" in sseg,
              "the reduced slice is written once, for its W - 1 readers")
    check("srv = _PLANES[host] = RamPlane(host)" in src,
          "fail-on-old: a rank's cells are written into the RAM (RamPlane); the rank does not serve them")

# ---------------------------------------------------------------------------
# PLANE 7 -- a finished rank leaves; nothing it published goes with its process
# ---------------------------------------------------------------------------
def plane_linger():
    print("\n=== PLANE 7: a rank stays until every peer is done reading it, or gone ===")
    import time
    import types

    path = os.path.join(_tuplespace(), "ddp_hook.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    fn = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "linger"]
    check(bool(fn), "FrogNetHookState.linger exists")

    bp = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "bag_publish"][0]
    check("done" in [a.arg for a in bp.args.args],
          "fail-on-old: the bag carries whether the rank is finished")

    # the live harness (agent_workload/ddpbench.py was dropped 2026-09-15)
    bench = open(os.path.normpath(os.path.join(_SEM, "..", "..", "usr", "local", "frognet-ai", "ddpbench.py")),
                 encoding="utf-8").read()
    check("_STATE.linger(" in bench and bench.index("_STATE.linger(") < bench.index("destroy_process_group"),
          "the harness publishes its done bag before tearing the group down")

    # [A_PEER_IS_NOT_GONE_UNTIL_EVERY_PEER_SAYS_SO_V1] a rank's buckets are in its own memory again
    # ([THE_BITS_STAY_WHERE_THEY_ARE_V1]): a rank that finishes stays until every peer is done or gone. One held
    # read on the members family, against a real RAM.
    if _SEM not in sys.path:
        sys.path.insert(0, _SEM)
    import threading
    from agent_workload.tuplespace.frogtorch_ram import RamServer
    from agent_workload.tuplespace.frogtorch_ram_client import Ram, linger
    srv = RamServer("127.0.0.1", 0)
    hp = ("127.0.0.1", srv.port)
    _env = os.environ.get("FROGTORCH_RAMS")
    os.environ["FROGTORCH_RAMS"] = "127.0.0.1:%d" % srv.port      # the one RAM, configured: no SystemRam here
    try:
        r0, r1, r2 = Ram(regions=[hp]), Ram(regions=[hp]), Ram(regions=[hp])
        for k, r in enumerate((r0, r1, r2)):
            r.put("frogtorch.members.t7", "r%d" % k, "r%d" % k, {"ip": "x", "port": 1}, life="session")

        def r1_done():
            time.sleep(0.5)
            r1.put("frogtorch.members.t7", "d1", "d1", {"t": 0}, life="session")

        def r2_gone():
            time.sleep(1.0)
            r2._member_session.close()
        threading.Thread(target=r1_done).start()
        threading.Thread(target=r2_gone).start()
        t0 = time.time()
        left = linger(r0, "t7", 0, 3, 10.0)
        dt = time.time() - t0
        check(left == [] and 0.9 < dt < 5, "waits for one peer to say done and one to go, then leaves (%.2fs)" % dt)
        r3 = Ram(regions=[hp])
        r3.put("frogtorch.members.t7b", "r0", "r0", {"ip": "x", "port": 1}, life="session")
        r3.put("frogtorch.members.t7b", "r1", "r1", {"ip": "x", "port": 1}, life="session")
        t0 = time.time()
        left = linger(r3, "t7b", 0, 2, 1.0)
        check(left == [1] and time.time() - t0 < 3, "a peer that never finishes is named when the wait runs out")
    finally:
        srv.close()
        if _env is None:
            os.environ.pop("FROGTORCH_RAMS", None)
        else:
            os.environ["FROGTORCH_RAMS"] = _env


def _api_php():
    return os.path.normpath(os.path.join(_SEM, "..", "..", "var", "www", "html", "api.php"))


# ---------------------------------------------------------------------------
# PLANE 8 -- the var coordinate actually filters
# ---------------------------------------------------------------------------
def plane_name_like_is_a_like():
    print("\n=== PLANE 8: a pattern goes on the wire as a pattern ===")
    import urllib.parse
    sys.path.insert(0, _SEM)
    from core import frognet_tuples as T

    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = getattr(req, "full_url", req)
        raise RuntimeError("tier: no store")

    real = T._urlopen
    T._urlopen = _fake_urlopen
    try:
        try:
            T._values_raw("svc", "store:80", name_like="SD:bag.r1.%")
        except Exception:
            pass
    finally:
        T._urlopen = real

    url = seen.get("url", "")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    check("SensorName__like" in q,
          "fail-on-old: the pattern is sent as SensorName__like, the only "
          "form api.php turns into a LIKE clause (%s)" % sorted(q))
    check("SensorName" not in q,
          "fail-on-old: and NOT as plain SensorName, which becomes "
          "SensorName = 'SD:bag.r1.%' and matches nothing")
    check(q.get("SensorName__like", [""])[0] == "SD:bag.r1.%",
          "the pattern survives quoting intact (%r)"
          % q.get("SensorName__like", [""])[0])

    php = _api_php()
    if os.path.exists(php):
        src = open(php, encoding="utf-8").read()
        check("'__like'" in src and "LIKE ?" in src,
              "and the store still builds a LIKE clause from that suffix")

# ---------------------------------------------------------------------------
# PLANE 9 -- frogtorch.Worker and frogtrain.Trainer: their tensors are cells in the RAM too
# ---------------------------------------------------------------------------
# (Until 19 Nov this plane tested memory_plane.MemoryPlane: a worker's own memory, served from a port it listened
# on, read by a connection from each peer. John, 19 Nov: "Why are things listening through MemoryPlane? That sure as
# hell sounds like a shared RAM thing". MemoryPlane is gone; the workers make RamPlane and RamTensorClient.)
def _worker_proc(name, seed):
    """One frogtorch.Worker with a data plane, as a process (run by plane_workers_do_not_listen)."""
    import json
    sys.path.insert(0, _SEM)
    import torch
    from agent_workload.tuplespace.frogtorch import Worker
    mine = torch.randn(60_000, generator=torch.Generator().manual_seed(seed))
    both = sum(torch.randn(60_000, generator=torch.Generator().manual_seed(k)) for k in (1, 2)) / 2
    w = Worker(name, data_plane=True)
    w.publish("tier9", mine, step=1)
    peers = sorted(w.wait_for("tier9", 2, timeout=60))
    out = w.reduce("tier9", mine, step=1)
    listen, far = _own_sockets()
    print("TIER9 " + json.dumps({"name": name, "peers": peers, "mean_ok": bool(torch.allclose(out, both, atol=1e-6)),
                                 "listen": listen, "far": far}), flush=True)
    sys.stdin.read()            # its cells are held by its session: stay until the other worker has read them
    w.retire()
    return 0


def plane_workers_do_not_listen():
    """[THE_TENSORS_ARE_IN_THE_RAM_V1] Two frogtorch.Workers with a data plane, each its own process, one real RAM,
    on each driver: each reduces to the mean of both; from the kernel, neither listens and every connection goes to
    the RAM. fail-on-old: each listened (MemoryPlane) and was connected to the other."""
    print("\n=== PLANE 9: two frogtorch.Workers: their tensors are in the RAM, neither listens ===")
    import json, socket, subprocess
    for word in ("tcp", "chorus"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = dict(os.environ, PYTHONPATH=_SEM, FROGNET_SEM_TRANSPORT=word, FROGTORCH_RAMS="127.0.0.1:%d" % port,
                   FROGNET_DIAG="0")
        ram = subprocess.Popen([sys.executable, "-m", "agent_workload.tuplespace.frogtorch_ram", "--port", str(port),
                                "--no-announce"], env=env, cwd=_SEM, stdout=subprocess.DEVNULL)
        there = port + (1 if word == "chorus" else 0)

        def up():
            try:
                socket.create_connection(("127.0.0.1", there), timeout=1).close()
                return True
            except OSError:
                return False
        _until(up, 15.0)
        procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker-proc", "w%d" % k, str(k)],
                                  env=env, cwd=_SEM, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True) for k in (1, 2)]
        got = []
        for p in procs:
            line, seen = "", []
            while True:
                line = p.stdout.readline()
                if not line or line.startswith("TIER9 "):
                    break
                seen.append(line)
            got.append(json.loads(line[6:]) if line else {"error": "".join(seen)[-400:]})
        for p in procs:
            p.stdin.close()
            p.wait(timeout=30)
        ram.terminate()
        ok = all("error" not in g for g in got)
        check(ok and all(g["mean_ok"] and g["peers"] == ["w1", "w2"] for g in got),
              "%s: each worker reduces to the mean of both (%s)" % (word, "ok" if ok else got))
        if not ok:
            continue
        check(all(g["listen"] == [] for g in got),
              "fail-on-old: %s: no worker listens on any port (%s)" % (word, [g["listen"] for g in got]))
        check(all(g["far"] == [there] for g in got),
              "fail-on-old: %s: every connection a worker has goes to the RAM, port %d (%s)"
              % (word, there, [g["far"] for g in got]))
    check(not os.path.exists(os.path.join(_tuplespace(), "memory_plane.py")),
          "fail-on-old: there is no MemoryPlane: nothing keeps tensors in a process that listens for readers")


def main():
    try:
        import torch  # noqa: F401
    except Exception as e:
        print("  [FAIL] torch is not importable: %r" % (e,))
        print("\nCOLLECTIVE TIER: CANNOT RUN")
        print("  This tier exercises the real DDP hooks against real tensors.")
        print("  It does not skip: agent_workload had no coverage at all, and")
        print("  a tier that passes when it cannot run is that same hole with")
        print("  a green tick on it. Install torch on the simulator host.")
        return 1

    plane_reduction_order()
    plane_hook_sources()
    plane_blocking_reads()
    plane_tensors_in_the_ram()
    plane_ram_session_is_a_drivers_connection()
    plane_ranks_do_not_listen()
    plane_object_is_not_a_list()
    plane_read_ahead_keying()
    plane_linger()
    plane_name_like_is_a_like()
    plane_workers_do_not_listen()

    print()
    if FAILS:
        print(f"COLLECTIVE TIER: {len(FAILS)} FAILED, {PASSES} passed")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"ALL COLLECTIVE TIER CHECKPOINTS PASS ({PASSES})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "--worker-proc":
        sys.exit(_worker_proc(sys.argv[2], int(sys.argv[3])))
    if len(sys.argv) > 4 and sys.argv[1] == "--rank-worker":
        sys.exit(_rank_worker(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))
    sys.exit(main())
