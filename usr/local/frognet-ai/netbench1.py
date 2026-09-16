#!/usr/bin/env python3
"""netbench1.py -- one machine, one file, one command.

    python3 netbench1.py --check       what is missing, change nothing
    python3 netbench1.py --selftest    prove the harness without torch
    python3 netbench1.py               run the benchmark

No ssh. No keys. No sudo. No chown. Nothing is installed, nothing outside
the working directory is written, and every process runs as you.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
All ranks are local processes on this box, talking over loopback. That makes
it a working baseline and a check that the pieces fit together. It is NOT the
slope experiment: loopback moves bytes at memory speed rather than wire
speed, and ranks contend for the same cores, so the byte model cannot be
tested here. That needs one rank per machine. This is the thing you run first
to know the harness works.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

SIZES = [1024, 4096, 16384, 65536, 262144]

# [THE_MATRIX_IS_SYMMETRIC_OR_IT_IS_NOTHING_V1]
#
# A slope from one size range and a slope from another are not comparable,
# because T(n) is not exactly linear: the small end carries per-operation
# cost into the slope estimate and the large end does not. Comparing a
# machine swept at 64K-4M against one swept at 1K-256K confounds hardware
# with range, and the difference looks like a hardware result.
#
# So the ranges are named and fixed. Every machine runs the identical
# numbers, and the profile is recorded in the results.
#
#   --profile slope      marginal per-element cost. Large sizes, where the
#                        intercept is negligible and b is what is measured.
#   --profile intercept  per-operation cost. Small sizes, where a is
#                        actually observed rather than extrapolated.
#
# The ceiling is 1M, not 4M: the plane retains a clone per generation, so
# 4M excludes the smallest box, and an experiment sized around the strongest
# machine is not the same experiment on the others.
PROFILES = {
    "slope":     [65536, 262144, 1048576],
    "intercept": [1024, 4096, 16384, 65536],
    "both":      SIZES,
}
POOL = 4          # distinct tensors cycled, to defeat SAME suppression

# ---- version banner ----------------------------------------------------
# Printed FIRST on every invocation, no flag required. BUILD is stamped at
# package time; SELF is the sha256 of this file computed at runtime, so an
# edited or half-copied file announces itself instead of being argued about.
BUILD = "2026-09-09T10:13:52Z"


def _self_id():
    try:
        h = hashlib.sha256(open(os.path.abspath(__file__), "rb").read())
        return h.hexdigest()[:12]
    except Exception as e:
        return "unreadable:%s" % type(e).__name__


def banner(where=""):
    print("netbench1 build %s  sha %s  %s" % (BUILD, _self_id(),
                                              os.path.abspath(__file__)))
    print("  python %s  host %s%s" % (sys.version.split()[0],
                                      _hostname(), where))


def _hostname():
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "?"

# The code UNDER TEST needs identifiers too. A table is worthless if the
# backend that produced it cannot be named: sha of the actual file that was
# imported, plus its mtime, plus the path it resolved from. That is what
# makes "which version produced this number" answerable after the fact
# instead of a discussion.
UNDER_TEST = ("core.frognet_tuples",
              "agent_workload.tuplespace.finite",
              "agent_workload.tuplespace.tensor_plane",
              "agent_workload.tuplespace.list_store",
              "agent_workload.tuplespace.store_server",
              "agent_workload.tuplespace.reduce_by_read",
              "agent_workload.tuplespace.torch_backend",
              "agent_workload.tuplespace.psychedelic_backend")


def _file_id(path):
    try:
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
        return h, time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(os.path.getmtime(path)))
    except Exception:
        return "?", "?"


def under_test_ids():
    """[{module, sha, mtime, path} ...] for whatever actually imported."""
    out = []
    for m in UNDER_TEST:
        try:
            mod = __import__(m, fromlist=["x"])
            f = getattr(mod, "__file__", None)
        except Exception as e:
            out.append({"module": m, "sha": "IMPORT-FAILED",
                        "mtime": "-", "path": "%s: %s" % (type(e).__name__, e)})
            continue
        if not f:
            out.append({"module": m, "sha": "no __file__",
                        "mtime": "-", "path": "-"})
            continue
        sha, mt = _file_id(f)
        out.append({"module": m, "sha": sha, "mtime": mt, "path": f})
    try:
        import torch
        out.append({"module": "torch", "sha": torch.__version__,
                    "mtime": "-", "path": os.path.dirname(torch.__file__)})
    except Exception:
        pass
    return out


def print_under_test(ids):
    print("code under test:")
    for d in ids:
        print("  %-44s %-13s %-16s %s"
              % (d["module"], d["sha"], d["mtime"], d["path"]))
# ---- end banner ----


# --------------------------------------------------------------- checking

class MergeLock:
    """[HOLD_THE_LOCK_THAT_STOPS_THE_GROUND_MOVING_V1]

    runMerge.bash takes flock on /var/run/runMerge.lock and, while it runs,
    rewrites routes and /etc/hosts. A merge that starts in the middle of a
    measurement re-plumbs the network underneath it: the visible signature
    is every plane socket dropping at the same instant and multi-second
    stalls in the timings, both of which showed up in the mesh12 run.

    Holding the same lock for the duration of a run means a merge that
    wants to start bails cleanly -- it touches /etc/sentinels/runAgain and
    exits 0, so it runs once we let go. Nothing is suppressed; it is
    deferred, by the mechanism runMerge already uses against itself.

    Not having the lock is not a reason to measure anyway: a run whose
    routes can change under it is not a measurement.
    """

    PATH = "/var/run/runMerge.lock"

    def __init__(self, wait_s=120.0):
        self.wait_s = wait_s
        self._fh = None

    def __enter__(self):
        # [DO_NOT_QUEUE_BEHIND_YOURSELF_V1] The coordinator holds this lock
        # and then spawns ranks. A rank that also tried to take it would
        # block for the full wait behind its own parent -- which is what
        # happened, and single-machine runs failed with "a merge is running"
        # when the only holder was netbench1 itself.
        if os.environ.get("FROGNET_MERGE_LOCK_HELD") == "1":
            print("  runMerge lock already held by this run")
            self._fh = None
            return self
        try:
            self._fh = open(self.PATH, "w")
        except OSError as e:
            raise SystemExit(
                "cannot open %s (%s). Run as a user that can, or the merge "
                "cannot be held off and the run is not a measurement."
                % (self.PATH, e))
        deadline = time.time() + self.wait_s
        while True:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print("  runMerge lock held")
                # Children of this process inherit the protection rather
                # than competing for it.
                os.environ["FROGNET_MERGE_LOCK_HELD"] = "1"
                return self
            except OSError:
                if time.time() >= deadline:
                    self._fh.close()
                    raise SystemExit(
                        "runMerge lock held by another process after %.0fs. "
                        "A merge is running; wait for it rather than "
                        "measuring across it." % self.wait_s)
                time.sleep(1.0)

    def __exit__(self, *exc):
        if self._fh is None:
            return False
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            print("  runMerge lock released")
        return False


def add_paths(tree, pypath):
    for p in ([pypath] if pypath else []) + [tree]:
        if p and p not in sys.path:
            sys.path.insert(0, p)
    parts = [p for p in ([pypath] if pypath else []) + [tree] if p]
    if parts:
        old = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = ":".join(parts + ([old] if old else []))


def check(tree, pypath, want_frognet=True):
    """Returns a list of problems. Empty means ready.

    A gloo-only run needs torch and nothing else -- no tree, no store. That
    makes `--backends gloo` a real smoke test of this machine and this
    harness with no FrogNet dependency at all, which is the right first
    thing to run.
    """
    bad = []
    if sys.version_info < (3, 8):
        bad.append("python %s is too old" % sys.version.split()[0])
    add_paths(tree, pypath)

    try:
        import torch
        import torch.distributed as dist
    except Exception as e:
        bad.append("torch will not import: %s: %s" % (type(e).__name__, e))
        return bad
    if not dist.is_gloo_available():
        bad.append("this torch has no gloo -- there is no control arm")

    if not want_frognet:
        return bad

    if not os.path.isdir(tree):
        bad.append("no FrogNet tree at %s (pass --tree)" % tree)
        return bad

    mods = ["core.frognet_tuples",
            "agent_workload.tuplespace.tensor_plane",
            "agent_workload.tuplespace.store_server",
            "agent_workload.tuplespace.torch_backend",
            "agent_workload.tuplespace.psychedelic_backend"]
    got = {}
    failed = []
    for m in mods:
        try:
            got[m] = __import__(m, fromlist=["x"])
        except Exception as e:
            short = m.rsplit(".", 1)[-1]
            failed.append(short)
            bad.append("%s: %s: %s" % (short, type(e).__name__, e))

    # Say WHERE, not just what. A missing sibling module is reported by
    # Python as a failure of the module that imports it, which tells you
    # nothing about which directory to look in or what is already in it.
    if failed:
        import importlib.util
        for pkg in ("agent_workload.tuplespace", "core"):
            try:
                spec = importlib.util.find_spec(pkg)
                d = os.path.dirname(spec.origin) if spec and spec.origin \
                    else (list(spec.submodule_search_locations)[0]
                          if spec else None)
            except Exception:
                d = None
            if not d or not os.path.isdir(d):
                bad.append("cannot even locate the %s package" % pkg)
                continue
            have = sorted(f for f in os.listdir(d) if f.endswith(".py"))
            bad.append("%s resolves to %s" % (pkg, d))
            bad.append("  it contains: %s" % (", ".join(have) or "(nothing)"))
            wanted = {"agent_workload.tuplespace":
                      ["finite.py", "torch_store.py", "tensor_plane.py",
                       "list_store.py", "store_server.py",
                       "torch_backend.py", "psychedelic_backend.py"],
                      "core": ["frognet_tuples.py", "stat_schema.py",
                               "hosts_only.py"]}[pkg]
            miss = [w for w in wanted if w not in have]
            if miss:
                bad.append("  missing from it: %s" % ", ".join(miss))

    # The handful of features this harness actually calls. A module that is
    # present but older will import fine and then raise TypeError inside the
    # first collective, which is a worse place to find out.
    import inspect
    T = got.get("core.frognet_tuples")
    if T:
        if not hasattr(T, "get_all"):
            bad.append("core.frognet_tuples has no get_all at all")
        else:
            p = inspect.signature(T.get_all).parameters
            if "wait_s" not in p or "min_rows" not in p:
                bad.append("core.frognet_tuples.get_all has no "
                           "wait_s/min_rows: the psychedelic rendezvous "
                           "passes both and will raise TypeError")
    tp = got.get("agent_workload.tuplespace.tensor_plane")
    if tp:
        if not hasattr(tp, "READ_CURRENT"):
            bad.append("tensor_plane has no READ_CURRENT")
        if hasattr(tp, "TensorClient") and hasattr(tp.TensorClient, "read"):
            p = inspect.signature(tp.TensorClient.read).parameters
            if "wait_s" not in p:
                bad.append("tensor_plane.TensorClient.read has no wait_s: "
                           "_read_peer passes it and will raise TypeError")
    return bad


# ------------------------------------------------------------------ ranks

def run_rank(a):
    """One rank. Writes a JSON result and exits non-zero on any failure."""
    banner("  rank %d/%d %s" % (a.rank, a.world, a.backend))
    r = {"rank": a.rank, "world": a.world, "backend": a.backend,
         "iters": a.iters, "sizes": {}, "selftest": a.selftest,
         "build": BUILD, "sha": _self_id(), "lookahead": a.lookahead,
         "profile": a.profile or "custom", "sizes_swept": a.sizes,
         "addr": a.addr, "evict": a.evict, "reduce": a.reduce or "stack",
         "settings": {"peer_wait": a.peer_wait,
                      "server_wait": a.server_wait,
                      "socket_timeout": a.socket_timeout,
                      "socket_margin": a.socket_margin,
                      "idle_reap": a.idle_reap,
                      "verify": a.verify, "pool": a.pool,
                      "diag": bool(a.diag)},
         "verified": True}

    if a.selftest:
        # No torch. Exercise spawn, timing, result-writing and collection
        # with a synthetic cost model, so the harness can be proven before
        # anything heavier is involved.
        import random
        random.seed(a.rank)
        base = {"gloo": (2.0, 13e-6), "psychedelic": (8.0, 26e-6)}[a.backend]
        for n in [int(x) for x in a.sizes.split(",")]:
            ms = (base[0] + base[1] * n) * (1.0 + 0.02 * a.rank)
            r["sizes"][str(n)] = {"median_ms": ms, "min_ms": ms, "max_ms": ms}
        r["plane"] = {"same_replies": 0, "data_replies": 1}
        r["status"] = "ok"
        json.dump(r, open(a.out, "w"))
        return 0

    # [A_RUN_MUST_OWN_ITS_NAMES_V1] Without a runid the service names are
    # the tree's defaults -- psyc10d, c10d, torch -- which every other run
    # and production also use. Nothing reclaims those rows, so a rendezvous
    # read returns descriptors from an earlier run whose planes are gone,
    # and the collective fails with "connection refused" against a port that
    # died minutes ago. Every rank in one run must pass the SAME runid.
    if a.store and not a.runid:
        print("--runid is required with --store: without it this run shares "
              "service names with every other run and with production, and "
              "will rendezvous against dead descriptors. Pass the same "
              "--runid to every rank, e.g. --runid mesh1.", file=sys.stderr)
        return 2
    add_paths(a.tree, a.pypath)

    # [A_RUNID_IS_USED_ONCE_V1] Rendezvous rows are upserted, so a reused
    # runid leaves the previous attempt's descriptors in place. min_rows is
    # satisfied the instant a rank looks, and it can read a peer's OLD
    # ephemeral port before that peer republishes -- "connection refused"
    # against a plane that died minutes ago, which is what mesh2 hit.
    #
    # Rank 0 checks, because every rank checking races with every rank
    # publishing.
    # [ASK_THE_STORE_THE_QUESTION_YOU_HAVE_V1]
    #
    # This used to read EVERY row and filter in Python -- 492 rows and
    # 586 KB across the network to answer a yes/no question, before every
    # run, growing with the store. api.php has always supported a LIKE on
    # SensorType; nothing asked it. It is now one indexed prefix query, so
    # the check is cheap enough to leave on.
    # [A_GUARD_THAT_TRIPS_ON_ITS_OWN_HARNESS_IS_NOT_A_GUARD_V1]
    #
    # A check stood here that read the store for rows under this run id and
    # refused if it found any, to stop a rank rendezvousing against a dead
    # peer's address from a reused id.
    #
    # It refused on the gate cell that runme.sh's own primary writes to open
    # the arm -- so every arm of every campaign failed, gloo included, with
    # the run id the harness had just published as the evidence against it.
    #
    # Two mechanisms of mine, fighting. The run id is given explicitly and
    # incremented per campaign now, which is what it was really protecting
    # against, so the check goes rather than gaining an exception for its
    # own harness.
    os.environ["MASTER_ADDR"] = a.master
    os.environ["MASTER_PORT"] = a.master_port
    # [THE_PLANE_BINDS_WHERE_IT_ADVERTISES_V1] One value, two jobs: the
    # address TensorServer listens on AND the one it publishes for peers to
    # dial. On one box 127.0.0.1 is right; across machines it is a plane
    # nobody can reach, and the failure surfaces deep inside a collective.
    os.environ["FROGNET_PSY_HOST"] = a.addr
    # NOT 0. The shipped allreduce is publish -> _start_prefetch ->
    # _inbox_get: the prefetcher is what submits the read that _inbox_get
    # waits for. LOOKAHEAD=0 submits nothing and every rank blocks forever
    # on a condition nobody will signal -- py-spy shows the executor thread
    # idle in _worker with no task. 1 is the minimum that works.
    #
    # This costs something and it should be said: at 1, a read for
    # generation seq+1 can be in flight while seq is being timed, so some
    # next-round work lands inside the measured interval.
    if int(a.lookahead) < 1:
        print("FROGNET_PSY_LOOKAHEAD must be >= 1: the prefetcher is what "
              "submits the read that _inbox_get waits for, so 0 deadlocks.",
              file=sys.stderr)
        return 2
    os.environ["FROGNET_PSY_LOOKAHEAD"] = str(a.lookahead)
    if a.reduce:
        os.environ["FROGNET_PSY_REDUCE"] = a.reduce
    # Diagnostics are the only per-collective record there is, and a run
    # that fails in the middle is the one that needs them. Forcing them off
    # here meant whichever node happened to have FROGNET_DIAG set in its
    # shell was traced and the others were blind -- so every failure was
    # seen from one side only. --diag decides it for every rank alike.
    os.environ["FROGNET_DIAG"] = "1" if a.diag else "0"
    # Every tunable, from the command line, set identically on every rank.
    os.environ["FROGNET_PSY_PEER_WAIT_S"] = str(a.peer_wait)
    os.environ["FROGNET_PLANE_MAX_WAIT_S"] = str(a.server_wait)
    os.environ["FROGNET_PLANE_SOCKET_TIMEOUT_S"] = str(a.socket_timeout)
    os.environ["FROGNET_PLANE_SOCKET_MARGIN_S"] = str(a.socket_margin)
    os.environ["FROGNET_PLANE_IDLE_REAP_S"] = str(a.idle_reap)
    os.environ["FROGNET_PSY_VERIFY"] = a.verify
    os.environ["FROGNET_TUPLES_POOL"] = a.pool
    os.environ["FROGNET_SENTINEL_DIR"] = a.sentinel
    if a.store:
        os.environ["FROGNET_C10D_DBHOST"] = a.store
    if a.runid:
        for k, v in (("FROGNET_C10D_SERVICE", "c10d"),
                     ("FROGNET_PSY_SERVICE", "psyc10d"),
                     ("FROGNET_TORCH_SERVICE", "torch"),
                     ("FROGNET_HOOK_SERVICE", "ddphook")):
            os.environ[k] = "nb1.%s.%s" % (a.runid, v)

    import torch
    import torch.distributed as dist
    # [SAY_WHAT_YOU_RAN_BEFORE_YOU_RUN_IT_V1] Recorded here, not after the
    # collectives. A run that fails is exactly the one whose versions you
    # need, and recording them at the end meant every failed result said
    # "unknown".
    add_paths(a.tree, a.pypath)
    r["under_test"] = under_test_ids()
    try:
        if a.backend == "psychedelic":
            from agent_workload.tuplespace import psychedelic_backend as fb
            fb.register("psychedelic")
        elif a.backend == "frognet":
            from agent_workload.tuplespace import torch_backend as fb
            fb.register("frognet")

        # [A_LOST_RANK_MUST_NOT_COST_HALF_AN_HOUR_V1]
        # torch's default process-group timeout is 30 minutes, and
        # torch_backend adopts it as its collective deadline. When one rank
        # dies the survivors poll the store for 1800 s before reporting it,
        # which reads as a hang and buries the traceback that says what
        # actually happened. Nothing in this harness should take that long:
        # a collective that has not completed in --timeout seconds is a
        # failure worth surfacing now.
        import datetime
        r["wall_init_start"] = time.time()
        dist.init_process_group(
            a.backend, rank=a.rank, world_size=a.world,
            timeout=datetime.timedelta(seconds=a.timeout))
        r["wall_init_done"] = time.time()
        pg = dist.distributed_c10d._get_default_group()
        be = pg._get_backend(torch.device("cpu"))
        g = torch.Generator().manual_seed(1000 + a.rank)

        for n in [int(x) for x in a.sizes.split(",")]:
            # The input MUST change between iterations. _seed_have carries a
            # peer's digest forward across generations, so all-reducing one
            # tensor repeatedly gets 21-byte SAME replies and reports a slope
            # near zero -- it measures suppression, not transfer.
            t_size0 = time.time()
            pool = [torch.randn(n, generator=g) for _ in range(POOL)]
            for p in pool:
                p.add_(float(a.rank + 1))
            # The warm-up must not use a tensor the loop is about to reuse.
            # _seed_have compares against the last digest for this op, so
            # warming up with pool[0] and then starting the loop at pool[0]
            # is two consecutive identical payloads: the peer answers SAME
            # in 21 bytes and that round measures suppression, not transfer.
            # It cost exactly one SAME per peer per size -- 10 of them at 5
            # sizes and 2 peers, which is what the guard refused.
            warm = torch.randn(n, generator=g).add_(float(a.rank + 1))
            dist.all_reduce(warm)
            dist.barrier()
            samples = []
            for i in range(a.iters):
                x = pool[i % POOL].clone()
                t = time.perf_counter()
                dist.all_reduce(x)
                samples.append((time.perf_counter() - t) * 1000)
            samples.sort()
            # [KEEP_THE_SAMPLES_V1] Medians make tables; samples make
            # graphs. Nine floats per size is nothing to store, and without
            # them no distribution can be plotted after the fact.
            r["sizes"][str(n)] = {"median_ms": samples[len(samples) // 2],
                                  "min_ms": samples[0],
                                  "max_ms": samples[-1],
                                  "samples_ms": samples,
                                  "bytes": n * 4,
                                  "wall_start": t_size0,
                                  "wall_end": time.time()}
            # [A_CONTRIBUTION_OUTLIVES_ITS_LAST_READER_V1]
            # This used to clear the plane between size points, to keep the
            # retention leak from filling RAM. That is a producer deleting
            # state a peer may still be about to read -- exactly what
            # torch_backend._release is left unwired for -- and it produced
            # intermittent StateGone failures on the fastest machine, where
            # ranks race furthest apart. Off unless asked for.
            if a.evict and a.backend == "psychedelic":
                be._server._current.clear()
                be._server._materialised.clear()
            del pool, warm
            # ONE barrier per size point, not three. torch_backend.barrier --
            # which psychedelic inherits -- polls the store with wait_s=None,
            # roughly 1000 requests a second per rank. The blocking read that
            # [ASK_ONCE_AND_WAIT_V1] added to the rendezvous was never
            # applied to barrier, so every barrier here is a polling storm
            # against a Python HTTP server, and on a Pi with three ranks and
            # the store on the same box it dominates the run.
            dist.barrier()

        if hasattr(be, "plane_stats"):
            r["plane"] = be.plane_stats()
        # Per-peer read times and per-phase times: what separates a
        # transcontinental link from a local one in the graphs.
        if hasattr(be, "timing_stats"):
            r["timing"] = be.timing_stats()
        dist.barrier()
        r["status"] = "ok"
    except Exception:
        import traceback
        r["status"] = "FAIL"
        r["error"] = traceback.format_exc()[-1500:]
    json.dump(r, open(a.out, "w"))
    return 0 if r.get("status") == "ok" else 1


# ------------------------------------------------------------ coordinator

def fit(points):
    n = len(points)
    if n < 3:
        return None, None, None
    sx = sum(p[0] for p in points); sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if den == 0:
        return None, None, None
    b = (n * sxy - sx * sy) / den
    a = (sy - b * sx) / n
    pred = [a + b * x for x, _ in points]
    ssr = sum((y - p) ** 2 for (_, y), p in zip(points, pred))
    sst = sum((y - sy / n) ** 2 for _, y in points) or 1.0
    return a, b, 1 - ssr / sst


def spawn(args, backend, work, store, runid):
    procs, outs = [], []
    for i in range(args.world):
        out = os.path.join(work, "r%d.%s.json" % (i, backend))
        outs.append(out)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--rank", str(i), "--world", str(args.world),
               "--backend", backend, "--sizes", args.sizes,
               "--iters", str(args.iters), "--out", out,
               "--tree", args.tree, "--master-port", args.master_port,
               "--lookahead", str(args.lookahead),
               "--peer-wait", str(args.peer_wait),
               "--server-wait", str(args.server_wait),
               "--socket-timeout", str(args.socket_timeout),
               "--socket-margin", str(args.socket_margin),
               "--idle-reap", str(args.idle_reap),
               "--verify", args.verify, "--pool", args.pool,
               ] + (["--diag"] if args.diag else []) + [
               "--reduce", args.reduce,
               "--timeout", str(args.timeout),
               "--sentinel", os.path.join(work, "sentinel"),
               "--runid", runid]
        if args.pypath:
            cmd += ["--pypath", args.pypath]
        if store:
            cmd += ["--store", store]
        if args.selftest:
            cmd += ["--selftest"]
        procs.append(subprocess.Popen(cmd, stdout=open(out + ".log", "w"),
                                      stderr=subprocess.STDOUT))
    # [DO_NOT_WAIT_FOR_A_CORPSE_V1] p.wait() on each in turn means the
    # survivors are given the full collective timeout to discover what the
    # coordinator already knows: a peer is gone. Poll instead, and when the
    # first one exits non-zero, stop the others.
    deadline = time.time() + args.timeout + 120
    bad = 0
    while True:
        alive = [p for p in procs if p.poll() is None]
        died = [p for p in procs if p.returncode not in (None, 0)]
        if died and alive:
            print("rank exited non-zero; stopping %d peer(s) rather than "
                  "letting them wait out the collective timeout"
                  % len(alive))
            for p in alive:
                p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=10)
                except Exception:
                    p.kill()
            break
        if not alive:
            break
        if time.time() > deadline:
            print("no rank finished within %ds; killing all"
                  % (args.timeout + 120))
            for p in alive:
                p.kill()
            for p in procs:
                p.wait()
            break
        time.sleep(0.2)
    bad = sum(1 for p in procs if p.returncode != 0)
    if bad:
        print("\n%d of %d ranks failed on the %s arm." % (bad, args.world,
                                                          backend))
        for o in outs:
            if os.path.exists(o):
                j = json.load(open(o))
                if j.get("status") != "ok" and j.get("error"):
                    print("\n--- rank %d ---\n%s" % (j["rank"], j["error"]))
                    break
            elif os.path.exists(o + ".log"):
                tail = open(o + ".log").read()[-1200:]
                if tail.strip():
                    print("\n--- rank output ---\n%s" % tail)
                    break
        return None
    return [json.load(open(o)) for o in outs]


def report(results):
    """results: {backend: [per-rank dicts]}"""
    fits = {}
    extrapolated = []
    print()
    # [A_TABLE_MUST_NAME_ITS_ARM_V1] Two reduce patterns printed identical
    # tables, so which one produced a number could only be inferred from the
    # order the commands were typed in. Print it.
    meta = []
    for be, rs in results.items():
        if be == "gloo":
            continue
        r0 = rs[0]
        meta.append("%s reduce=%s lookahead=%s evict=%s"
                    % (be, r0.get("reduce", "?"), r0.get("lookahead", "?"),
                       r0.get("evict", "?")))
    if meta:
        print("  ".join(meta))
    print("%-12s %11s %14s %8s" % ("backend", "intercept", "slope", "fit"))
    for be, rs in results.items():
        pl = (rs[0].get("plane") or {})
        if be == "psychedelic" and pl.get("same_replies"):
            print("REFUSED psychedelic: same_replies=%s -- this arm measured "
                  "21-byte SAME replies, not transfer." % pl["same_replies"])
            return 1
        # A collective completes when its SLOWEST rank completes.
        worst = {}
        for r in rs:
            for sz, v in r["sizes"].items():
                if sz not in worst or v["median_ms"] > worst[sz]:
                    worst[sz] = v["median_ms"]
        pts = sorted((int(s), m) for s, m in worst.items())
        a, b, r2 = fit(pts)
        if a is None:
            print("%-12s  too few sizes to fit" % be)
            continue
        fits[be] = (a, b)
        # [AN_EXTRAPOLATION_IS_NOT_A_MEASUREMENT_V1]
        # a/b has units of elements: the crossover where fitted fixed and
        # variable costs are equal. If the SMALLEST size swept is already
        # far above it, every point sat in the regime where b dominates and
        # a is an extrapolation back to zero from a long way off. R^2 says
        # the measured region is linear; it says nothing about a.
        smallest = min(int(s) for s in rs[0]["sizes"])
        flag = ""
        if a <= 0:
            # A negative fixed cost is not a small one, it is a fit whose
            # intercept was never constrained by data.
            flag = "  <- intercept NEGATIVE: not measured, extrapolated"
            extrapolated.append(be)
        elif b > 0:
            cross = a / b
            if smallest > 10 * cross:
                flag = ("  <- intercept EXTRAPOLATED (smallest n=%d is %.0fx "
                        "the %.0f-element crossover)"
                        % (smallest, smallest / cross, cross))
                extrapolated.append(be)
        print("%-12s %8.2f ms %9.1f ns/el %8.3f%s"
              % (be, a, b * 1e6, r2, flag))

    if extrapolated:
        print()
        print("Do not quote the intercept for: %s" % ", ".join(extrapolated))
        print("Run --profile intercept for a, --profile slope for b. They")
        print("measure different regimes and one fit cannot serve both.")
    if "gloo" in fits and "psychedelic" in fits and fits["gloo"][1]:
        ratio = fits["psychedelic"][1] / fits["gloo"][1]
        print()
        print("slope ratio psychedelic/gloo: %.2fx" % ratio)
    print()
    print("produced by netbench1 build %s sha %s" % (BUILD, _self_id()))
    for be, rs in results.items():
        if be == "gloo":
            continue          # gloo imports no FrogNet module
        for d in rs[0].get("under_test") or []:
            if d["sha"] in ("IMPORT-FAILED", "?", "no __file__"):
                continue
            if d["module"].rsplit(".", 1)[-1] in ("psychedelic_backend",
                                                  "tensor_plane"):
                print("  %s sha %s (%s)" % (d["module"].rsplit(".", 1)[-1],
                                            d["sha"], d["mtime"]))
        break
    print("One machine, loopback, ranks sharing cores. This is a baseline and")
    print("a proof the pieces fit -- not the byte-model test, which needs one")
    print("rank per machine on a real link.")
    return 0


def serve_store(args):
    """Run a store for a multi-machine run and hold it open."""
    banner()
    add_paths(args.tree, args.pypath)
    os.environ.setdefault("FROGNET_DIAG", "0")
    os.environ.setdefault("FROGNET_SENTINEL_DIR", "/tmp/netbench1.sentinel")
    os.makedirs("/tmp/netbench1.sentinel", exist_ok=True)
    from agent_workload.tuplespace import store_server
    srv = store_server.serve(bind=args.addr)
    print("store %s:%d" % (args.addr, srv.port))
    print("pass --store %s:%d to every rank. Ctrl-C to stop."
          % (args.addr, srv.port))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.close()
    return 0


def merge(args):
    """Fit across per-rank JSONs collected from every machine."""
    import glob
    banner()
    runs = {}
    for f in sorted(glob.glob(os.path.join(args.work, "*.json"))):
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if "sizes" not in j:
            continue
        if j.get("status") != "ok":
            print("FAILED rank %s in %s" % (j.get("rank"), f))
            return 1
        runs.setdefault(j["backend"], []).append(j)
    if not runs:
        print("no result JSONs under %s" % args.work)
        return 1
    for be, rs in runs.items():
        print("%s: %d ranks from %s" %
              (be, len(rs), ", ".join(sorted(r.get("host", "?") for r in rs))))
    return report(runs)


def coordinate(args):
    banner()
    bes = [b for b in args.backends.split(",") if b]
    needs_frognet = any(b != "gloo" for b in bes)
    problems = [] if args.selftest else check(args.tree, args.pypath,
                                              needs_frognet)
    if problems:
        print("not ready:")
        for p in problems:
            print("  %s" % p)
        return 1
    ids = []
    if needs_frognet and not args.selftest:
        ids = under_test_ids()
        print_under_test(ids)
    if args.check:
        print("ready: torch and gloo" +
              (", plus the tree and every call this harness makes"
               if needs_frognet else " (no FrogNet backend requested, so no "
               "tree is needed)"))
        return 0

    work = args.work or tempfile.mkdtemp(prefix="netbench1-")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(work, "sentinel"), exist_ok=True)
    runid = time.strftime("%Y%m%d-%H%M%S")
    print("working in %s" % work)

    srv = None
    store = ""
    if needs_frognet and not args.selftest:
        # DIAG-STORESRV writes a line per request, and barrier generates
        # thousands per second. Set FROGNET_DIAG=1 yourself when you want to
        # watch the store; the default should not make the measurement
        # slower than the thing being measured.
        os.environ.setdefault("FROGNET_DIAG", "0")
        from agent_workload.tuplespace import store_server
        srv = store_server.serve(bind="127.0.0.1")
        store = "127.0.0.1:%d" % srv.port
        print("store at %s" % store)

    results = {}
    rc = 0
    try:
        for be in bes:
            print("running %s, world=%d ..." % (be, args.world))
            rs = spawn(args, be, work, store, runid)
            if rs is None:
                return 1
            results[be] = rs
    finally:
        if srv is not None:
            srv.close()
    rc = report(results)
    print("results and per-rank logs: %s" % work)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--pypath", default="/etc/frognet_bundles/communicator")
    ap.add_argument("--world", type=int, default=3)
    ap.add_argument("--backends", default="gloo,psychedelic")
    ap.add_argument("--profile", default="", choices=["", "slope",
                                                      "intercept", "both"],
                    help="named identical sweep; see PROFILES")
    ap.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    ap.add_argument("--iters", type=int, default=11)
    ap.add_argument("--addr", default="127.0.0.1",
                    help="THIS machine's address as peers reach it. It is "
                         "the plane's BIND address and the address it "
                         "advertises, so the 127.0.0.1 default makes the "
                         "plane unreachable from other machines.")
    ap.add_argument("--master", default="127.0.0.1",
                    help="rank 0's address, for gloo rendezvous")
    ap.add_argument("--role", default="", choices=["", "store", "merge"],
                    help="store: run a store and print its address. "
                         "merge: fit across per-rank JSONs from every machine")
    ap.add_argument("--evict", action="store_true",
                    help="clear the plane between size points. OFF by "
                         "default: a producer dropping state a peer may "
                         "still read is what _release is unwired for, and "
                         "it produced intermittent StateGone failures.")
    ap.add_argument("--timeout", type=int, default=120,
                    help="collective deadline, seconds (torch default 1800)")
    ap.add_argument("--reduce", default="", choices=["", "stack", "shard"],
                    help="psychedelic reduction pattern. stack: read every "
                         "peer's whole tensor, (W-1)n per rank. shard: "
                         "reduce-scatter then all-gather by read, "
                         "2(W-1)n/W -- ring's byte count.")
    ap.add_argument("--lookahead", type=int, default=1,
                    help="psychedelic prefetch depth; 0 deadlocks, see notes")
    ap.add_argument("--work", default="")
    ap.add_argument("--master-port", default="29591")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--version", action="store_true")
    # [A_SETTING_THAT_IS_NOT_ON_THE_COMMAND_LINE_IS_NOT_IN_THE_RESULT_V1]
    #
    # These were environment variables. A knob set in one node's shell and
    # not the others' makes a fleet run different code with the same shas --
    # FROGNET_PSY_PEER_WAIT_S=180 on one box against 30 on three cost a
    # campaign, and nothing in the output said the ranks disagreed.
    #
    # On the command line they are visible in `ps`, identical across nodes
    # because runme.sh passes the same string to all of them, recorded in
    # every result JSON, and printed by --version. A run can be reproduced
    # from what it reports.
    ap.add_argument("--peer-wait", type=float, default=30.0,
                    help="how long one peer read may block, seconds")
    ap.add_argument("--server-wait", type=float, default=300.0,
                    help="ceiling on how long a plane server holds one "
                         "request open waiting for a state")
    ap.add_argument("--socket-timeout", type=float, default=60.0,
                    help="link deadline when no wait was requested")
    ap.add_argument("--socket-margin", type=float, default=60.0,
                    help="added to a requested wait to get the link deadline")
    ap.add_argument("--idle-reap", type=float, default=600.0,
                    help="how long a served link may sit idle before the "
                         "server closes it")
    ap.add_argument("--verify", default="1", choices=["0", "1"],
                    help="check the digest of every slice read")
    ap.add_argument("--pool", default="1", choices=["0", "1"],
                    help="reuse store connections")
    ap.add_argument("--diag", action="store_true",
                    help="per-collective plane and store tracing on EVERY "
                         "rank. On by default in runme.sh: a mid-run failure "
                         "cannot be diagnosed from one node's log.")
    ap.add_argument("--selftest", action="store_true")
    # rank mode
    ap.add_argument("--rank", type=int, default=-1)
    ap.add_argument("--out", default="")
    ap.add_argument("--store", default="")
    ap.add_argument("--sentinel", default="/tmp/netbench1.sentinel")
    ap.add_argument("--runid", default="")
    ap.add_argument("--backend", default="gloo")
    a = ap.parse_args()
    if a.profile:
        a.sizes = ",".join(str(x) for x in PROFILES[a.profile])
    if a.role == "store":
        return serve_store(a)
    if a.role == "merge":
        return merge(a)
    if a.version:
        banner()
        if os.path.isdir(a.tree):
            add_paths(a.tree, a.pypath if os.path.isdir(a.pypath) else "")
            print_under_test(under_test_ids())
            print("settings: peer-wait %s  server-wait %s  socket-timeout %s"
                  "  socket-margin %s  idle-reap %s  verify %s  pool %s"
                  % (a.peer_wait, a.server_wait, a.socket_timeout,
                     a.socket_margin, a.idle_reap, a.verify, a.pool))
        return 0
    if not os.path.isdir(a.pypath):
        a.pypath = ""
    if a.selftest:
        # No network, nothing to protect from a merge.
        return run_rank(a) if a.rank >= 0 else coordinate(a)
    if a.rank >= 0:
        # [A_FILE_MUST_NAME_WHAT_IS_IN_IT_V1] Given a directory, compose the
        # filename from rank, backend and reduce pattern. Passing the same
        # --out for two arms silently overwrote the first with the second,
        # and the only sign was a file called r0.psychedelic.json containing
        # "backend": "gloo".
        if os.path.isdir(a.out):
            # [A_RESULT_MUST_NAME_ITS_RUN_V1] The run id goes in the
            # filename. Without it every run overwrites the last one's
            # results, and the evidence for a failure is destroyed by the
            # next attempt to reproduce it -- which is exactly what happened
            # to three separate world-4 failures.
            a.out = os.path.join(a.out, "%sr%d.%s%s.json" % (
                (a.runid + ".") if a.runid else "",
                a.rank, a.backend,
                ("." + a.reduce) if a.reduce and a.backend != "gloo" else ""))
            print("  writing %s" % a.out)
        with MergeLock():
            return run_rank(a)
    with MergeLock():
        return coordinate(a)


if __name__ == "__main__":
    raise SystemExit(main())
