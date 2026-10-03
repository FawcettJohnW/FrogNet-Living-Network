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
        check("slots[state.rank] = t.clone()" in body,
              f"{name}: self placed at its own rank index")
        check("parts = [x for x in slots if x is not None]" in body,
              f"{name}: skipped peers leave a hole, order preserved")


# ---------------------------------------------------------------------------
# PLANE 3 -- every waiting read blocks, with a satisfiable condition
# ---------------------------------------------------------------------------
def plane_blocking_reads():
    print("\n=== PLANE 3: waiting reads block, and can be satisfied ===")
    path = os.path.join(_tuplespace(), "psychedelic_backend.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_read_all"]
    check(len(calls) >= 2, f"_read_all call sites found ({len(calls)})")
    for c in calls:
        kw = {k.arg for k in c.keywords}
        op = (c.args[0].value if c.args and isinstance(c.args[0], ast.Constant)
              else "<expr>")
        check("wait_s" in kw, f"line {c.lineno} ({op}): passes wait_s")
        check("min_rows" in kw, f"line {c.lineno} ({op}): passes min_rows")
        # The condition has to match the number of WRITERS for that operation.
        mr = [k.value for k in c.keywords if k.arg == "min_rows"]
        if mr:
            bad = isinstance(mr[0], ast.Attribute) and mr[0].attr == "_size"
            check(not bad,
                  f"line {c.lineno} ({op}): min_rows is not self._size "
                  f"(a broadcast has one writer; W is unsatisfiable)")

    bc = [c for c in calls
          if c.args and getattr(c.args[0], "value", None) == "bc"]
    if bc:
        mr = [k.value for k in bc[0].keywords if k.arg == "min_rows"][0]
        check(isinstance(mr, ast.Constant) and mr.value == 1,
              "broadcast waits for min_rows=1 (the root is the only producer)")

    tb = open(os.path.join(_tuplespace(), "torch_backend.py"),
              encoding="utf-8").read()
    t = ast.parse(tb)
    mod = {n.targets[0].id for n in t.body
           if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    check("BLOCK_CAP" in mod,
          "BLOCK_CAP is module-level (was a local, so broadcast could not "
          "reach it)")


# ---------------------------------------------------------------------------
# PLANE 4 -- behaviour: a waiting store must collapse the ask count
# ---------------------------------------------------------------------------
def plane_ask_count():
    print("\n=== PLANE 4: a store that honours wait_s collapses the asks ===")
    import time
    import types

    path = os.path.join(_tuplespace(), "psychedelic_backend.py")
    src = open(path, encoding="utf-8").read()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "broadcast"][0]
    body = ast.get_source_segment(src, fn)

    # broadcast registers its wait with _PsyWait; the stub needs it or the
    # body raises NameError and the ask count comes back 0, which reads as a
    # pass on a test that never ran.
    class _NullWait:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ns = {"time": time, "_base": types.SimpleNamespace(BLOCK_CAP=5.0),
          "READ_CURRENT": 0, "_PsyWait": _NullWait,
          "_psy": lambda *a, **k: None}
    exec("class _S:\n" + "\n".join("    " + l for l in body.splitlines()), ns)

    class _T:
        shape = (1,)
        dtype = "f"

        def copy_(self, x):
            pass

        def reshape(self, x):
            return self

        def to(self, x):
            return self

    def run(store_waits):
        asks = {"n": 0}
        s = ns["_S"].__new__(ns["_S"])
        s._rank, s._size, s._timeout_s = 1, 4, 3.0
        s._next_seq = lambda: 7
        s._publish_tensor = lambda *a: None
        s._consumed = lambda *a: None
        s._await_consumers = lambda *a: None
        s._client = types.SimpleNamespace(
            read=lambda *a, **k: (None, 0, False))
        t0 = time.time()

        def _read_all(op, seq, wait_s=0.0, min_rows=0):
            asks["n"] += 1
            if store_waits and wait_s > 0:
                time.sleep(min(wait_s, 0.25))
            if time.time() - t0 > 0.6:
                return {0: {"host": "h", "port": 1, "name": "n",
                            "gen": 1, "shape": [1], "dtype": "f"}}
            return {}

        s._read_all = _read_all
        try:
            s.broadcast([_T()], types.SimpleNamespace(rootRank=0))
        except Exception:
            pass
        return asks["n"]

    waiting = run(True)
    spinning = run(False)
    print(f"        asks against a waiting store : {waiting}")
    print(f"        asks against a spinning store: {spinning}")
    # A count of zero means the body raised, not that it was efficient.
    check(waiting > 0 and spinning > 0,
          f"both arms actually ran (waiting={waiting}, spinning={spinning})")
    check(waiting * 5 < spinning,
          f"blocking read collapses the ask count ({spinning} -> {waiting})")
    check(spinning > waiting,
          "degrades to polling against a store that ignores the parameters, "
          "rather than breaking")



# ---------------------------------------------------------------------------
# PLANE 5 -- a tuple value survives the store unchanged
# ---------------------------------------------------------------------------
def plane_object_is_not_a_list():
    print("\n=== PLANE 5: an object keyed 0..N-1 is not a list ===")
    import types

    # -- the store -----------------------------------------------------------
    # api.php lives in the tree at web/ and is DEPLOYED to /var/www/html. The
    # tier reads the tree copy; a node that has one but not the other has a
    # deployment problem this tier is not the place to find.
    php = os.path.join(_SEM, "web", "api.php")
    if not os.path.exists(php):
        check(False, "web/api.php is present in the tree (looked at %s)" % php)
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
# PLANE 6 -- read-ahead states a name, not a length
# ---------------------------------------------------------------------------
def plane_read_ahead_keying():
    print("\n=== PLANE 6: read-ahead does not guess a length ===")
    import torch
    import types

    path = os.path.join(_tuplespace(), "psychedelic_backend.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    ra = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_read_ahead"]
    check(bool(ra), "_read_ahead exists")
    if ra:
        args = [a.arg for a in ra[0].args.args]
        check("ln" not in args,
              "fail-on-old: _read_ahead no longer takes a length (%s)"
              % ", ".join(args))

    check("key = (seq, str(own.dtype))" in src,
          "the consumption site keys the guess on the round and dtype")
    check("key = (seq, ln_r, str(own.dtype))" not in src,
          "fail-on-old: the length is out of the lookup key")
    check("exact: bool = True" in open(
              os.path.join(_tuplespace(), "tensor_plane.py"),
              encoding="utf-8").read(),
          "read_into can be told the caller does not know the length")

    # -- behaviour: the real _hwm / _stage / _ahead_stage against DDP's own
    # -- alternating bucket sizes.
    want = ("_hwm", "_stage", "_ahead_stage")
    fns = {n.name: ast.get_source_segment(src, n) for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name in want}
    if set(fns) != set(want):
        check(False, "found %s" % sorted(fns))
        return
    ns = {"torch": torch}
    exec("class _B:\n" + "\n".join(
        "\n".join("    " + l for l in fns[k].splitlines()) for k in want), ns)

    b = ns["_B"].__new__(ns["_B"])
    b._ln_hwm = {}
    like = torch.empty(0, dtype=torch.float32)

    # DDP hands these two bucket sizes out alternately; at world 4 the phase-1
    # slice is a quarter of each.
    lens = [262657 // 4, 791552 // 4] * 6
    allocs, seen = 0, None
    widths = set()
    for ln in lens:
        rows = b._stage(3, ln, like)
        if b._stage_buf is not seen:
            allocs += 1
            seen = b._stage_buf
        widths.add(tuple(r.numel() for r in rows))
    check(allocs == 2,
          "_stage allocates once per distinct maximum, not per round "
          "(%d allocations over %d rounds)" % (allocs, len(lens)))
    check(widths == {(lens[0],) * 3, (lens[1],) * 3},
          "every row is still exactly the round's slice length")

    ahead = b._ahead_stage(3, like)
    check(all(a.numel() == max(lens) for a in ahead),
          "the speculative landing area is the high-water mark (%d)"
          % max(lens))

    # -- and the keying, through the real _read_ahead.
    issued = []

    class _Pool:
        def submit(self, fn, *a):
            issued.append(a)
            f = types.SimpleNamespace()
            f.cancel = lambda: None
            f.done = lambda: True
            f.exception = lambda: None
            f.result = lambda: None
            return f

    rafn = ast.get_source_segment(src, ra[0])
    ns2 = {"torch": torch}
    exec("class _R:\n" + "\n".join(
        "\n".join("    " + l for l in x.splitlines())
        for x in (rafn, fns["_ahead_stage"], fns["_hwm"])), ns2)
    rb = ns2["_R"].__new__(ns2["_R"])
    rb._ahead, rb._ln_hwm = {}, {"torch.float32": max(lens)}
    rb._drop_ahead = lambda: None
    rb._peer_inc = lambda p: "c%d" % p
    rb._read_slice_into = None
    rb._read_ahead(9, like, [1, 2, 3], 0, 30.0, _Pool())

    check(list(rb._ahead) == [(9, "torch.float32")],
          "the guess is filed under the round it is for (%s)"
          % list(rb._ahead))
    check((9, "torch.float32") in rb._ahead,
          "and is found on the next round whatever length that round turns "
          "out to be -- which is the miss that made prefetch_hit False in "
          "every ddp54xx log")
    check(all(a[5] == "pf" and a[6] is False for a in issued),
          "every speculative read goes out on the pf channel, exact=False")


# ---------------------------------------------------------------------------
# PLANE 7 -- a finished rank does not take its plane away
# ---------------------------------------------------------------------------
def plane_linger():
    print("\n=== PLANE 7: a rank that finishes waits for the rest ===")
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

    bench = open(os.path.join(_SEM, "agent_workload", "ddpbench.py"),
                 encoding="utf-8").read()
    _has_linger = "_STATE.linger()" in bench
    check(_has_linger,
          "fail-on-old: the harness lingers before the process ends -- without "
          "it the rank returns from the training loop and the process exit "
          "closes its TensorServer under peers that are still reading")
    check(_has_linger
          and bench.index("_STATE.linger()") < bench.index("destroy_process_group"),
          "and does it before tearing the group down")

    if not fn:
        return
    body = ast.get_source_segment(src, fn[0])
    ns = {"time": time}
    exec("class _S:\n" + "\n".join("    " + l for l in body.splitlines()), ns)

    def run(peers_finish, timeout_s=6.0):
        """peers_finish: which peers ever publish done."""
        st = ns["_S"].__new__(ns["_S"])
        st.rank, st.world, st.step, st.timeout_s = 0, 4, 40, timeout_s
        published = []
        st.bag_publish = lambda step, done=False: published.append((step, done))
        bags = {r: {"step": 40, "done": (r in peers_finish)}
                for r in (1, 2, 3)}
        st.bag_read = lambda wait_s=0.0, min_rows=0: bags
        msgs = []
        t0 = time.time()
        ok = st.linger(logger=msgs.append)
        return ok, time.time() - t0, published, " ".join(msgs)

    ok, dt, pub, msg = run({1, 2, 3})
    check(ok, "returns once every peer reports done")
    check(pub == [(40, True)],
          f"publishes its own done exactly once ({pub})")
    check(dt < 2.0, f"and does not sit out the timeout when it need not ({dt:.1f}s)")

    ok, dt, pub, msg = run({1, 2})
    check(not ok, "fail-on-old: a peer that never finishes is not waited on forever")
    check(4.0 < dt < 12.0, f"the wait is bounded by the group timeout ({dt:.1f}s)")
    check("3" in msg and "EXPIRED" in msg,
          "and the expiry names the rank it was waiting on")
    check(pub == [(40, True)],
          "it still published its own done, so nobody waits on IT")


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

    php = os.path.join(_SEM, "web", "api.php")
    if os.path.exists(php):
        src = open(php, encoding="utf-8").read()
        check("'__like'" in src and "LIKE ?" in src,
              "and the store still builds a LIKE clause from that suffix")


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
    plane_ask_count()
    plane_object_is_not_a_list()
    plane_read_ahead_keying()
    plane_linger()
    plane_name_like_is_a_like()

    print()
    if FAILS:
        print(f"COLLECTIVE TIER: {len(FAILS)} FAILED, {PASSES} passed")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"ALL COLLECTIVE TIER CHECKPOINTS PASS ({PASSES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
