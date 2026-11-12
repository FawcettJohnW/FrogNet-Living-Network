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
        check("return _gather(state, t, key, n," in body,
              f"{name}: its sum is _gather's, which keys every bucket by rank")
    # [EVERY_PEER_AT_ONCE_V1] The rank order lives in _gather now: self at its own rank, peers by rank, a
    # skipped peer simply absent, the stack built over the ranks in ascending order.
    g = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_gather"]
    check(bool(g), "_gather exists")
    if g:
        gb = ast.get_source_segment(src, g[0])
        check("{state.rank: t.clone()}" in gb, "_gather: self placed at its own rank index")
        check("slots[r] = x" in gb, "_gather: each peer lands at its own rank index")
        check("torch.stack([slots[q] for q in sorted(slots)])" in gb,
              "_gather: skipped peers leave a hole, order preserved")
        check("state.pool().submit(_read" in gb, "_gather: every peer's read is up at once")


# ---------------------------------------------------------------------------
# PLANE 3 -- every waiting read blocks, with a satisfiable condition
# ---------------------------------------------------------------------------
def plane_blocking_reads():
    print("\n=== PLANE 3: every wait is one held read in the memory ===")
    # [ALL_IN_THE_REGION_V1] A rank waits by holding one read on the cell it needs, in FrogTorch's own RAM. The
    # loops this plane used to check -- reads re-asked until rows appeared, consumers acknowledging, a root
    # waiting for the acknowledgements, BLOCK_CAP re-arming every 5 s -- are what was removed. fail-on-old: none of it
    # may come back.
    gone = {"psychedelic_backend.py": ("_await_consumers", "_consumed", "_read_fresh", "_PLANES",
                                       "_shared_plane"),
            "torch_backend.py": ("BLOCK_CAP",)}
    for f, names in gone.items():
        tree = ast.parse(open(os.path.join(_tuplespace(), f), encoding="utf-8").read())
        seen = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        seen |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        seen |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for nm in names:
            check(nm not in seen, f"fail-on-old: {f} has no {nm}")
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
# PLANE 4 -- behaviour: a broadcast asks once, and never falls back to asking again
# ---------------------------------------------------------------------------
def plane_ask_count():
    print("\n=== PLANE 4: a broadcast is one held read, and a failed read is an error ===")
    import types

    path = os.path.join(_tuplespace(), "psychedelic_backend.py")
    src = open(path, encoding="utf-8").read()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "broadcast"][0]
    body = ast.get_source_segment(src, fn)

    class _NullWait:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ns = {"_base": types.SimpleNamespace(_Work=lambda t: t), "_PsyWait": _NullWait}
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

    def run(rank, read):
        log = {"reads": [], "published": []}
        s = ns["_S"].__new__(ns["_S"])
        s._rank, s._size, s._timeout_s, s._incarnation = rank, 4, 3.0, "i0"
        s._next_seq = lambda: 7
        s._my_region = lambda: "r"
        s._server = types.SimpleNamespace(publish=lambda *a, **k: log["published"].append(a[0]))

        def _read_peer(peer, name, seq, t, wait_s=0.0, retain=False):
            log["reads"].append((peer, name, wait_s))
            return read()
        s._read_peer = _read_peer
        err = None
        try:
            s.broadcast([_T()], types.SimpleNamespace(rootRank=0))
        except Exception as e:
            err = e
        return log, err

    log, err = run(0, lambda: _T())
    check(err is None and log["published"] == ["i0.bc.7"] and not log["reads"],
          f"the root writes its cell and reads nothing ({log})")
    log, err = run(2, lambda: _T())
    check(err is None and log["reads"] == [(0, "i0.bc.7", 3.0)] and not log["published"],
          f"a non-root holds ONE read on the root's cell, for the group timeout ({log['reads']})")

    def gone():
        raise TimeoutError("held read expired")
    log, err = run(2, gone)
    check(isinstance(err, TimeoutError) and len(log["reads"]) == 1,
          f"fail-on-old: a read that fails is raised, not asked again ({len(log['reads'])} read(s), {err!r})")



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
    check("_read_local_into" not in seg and "_read_owner_into" in seg,
          "fail-on-old: every read is a held read on the OWNER's region")
    ro = fn.get("_read_owner_into")
    check(ro is not None and "self._region_of(owner)" in ast.get_source_segment(src, ro),
          "the read goes to the owner's region")
    check(seg.index('"p2")') < seg.index("acc_fn(own"),
          "phase 2's held reads are up before phase 1 reduces")
    check(seg.count("self._server.publish(\"%s.arr\"") == 1 and "for p in peers:\n                self._server"
          not in seg, "fail-on-old: the reduced slice is written once, not once per peer")

# ---------------------------------------------------------------------------
# PLANE 7 -- a finished rank leaves; nothing it published goes with its process
# ---------------------------------------------------------------------------
def plane_linger():
    print("\n=== PLANE 7: a rank that finishes leaves; its buckets stay in its region ===")
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

    if not fn:
        return
    body = ast.get_source_segment(src, fn[0])
    ns = {}
    exec("class _S:\n" + "\n".join("    " + l for l in body.splitlines()), ns)
    # [ALL_IN_THE_REGION_V1] a rank's buckets are in its region, not its process: a rank that finishes first leaves
    # while its peers are still reading them. linger waits for nobody.
    st = ns["_S"].__new__(ns["_S"])
    st.rank, st.world, st.step, st.timeout_s = 0, 4, 40, 6.0
    published, reads = [], []
    st.bag_publish = lambda step, done=False: published.append((step, done))
    st.bag_read = lambda *a, **k: reads.append(1) or {}
    t0 = time.time()
    ok = st.linger(logger=lambda m: None)
    dt = time.time() - t0
    check(ok and published == [(40, True)], f"publishes its own done exactly once ({published})")
    check(not reads, "fail-on-old: and reads nobody's bag -- there is nothing to wait for")
    check(dt < 0.5, f"returns at once ({dt:.2f}s)")


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
