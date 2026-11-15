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
"""shmbench.py -- the same training answer on ONE machine, five ways: what shared memory removes from DDP.

The question (John, 2026-11-13): consider why PyTorch works the way it does, then reconsider the problem under a
single-machine multithreaded process model. Is there a method that gets the same answer, using shared memory, in a
shorter time?

Synchronous data-parallel SGD needs every replica to apply the same mean gradient each step. DDP's shape follows from
the replicas being separate processes: each holds its own parameters, each runs the same optimizer step, and the mean
reaches every copy by an all-reduce (reduce-scatter computes the sum; all-gather hands it back to each copy). In one
address space only the sum is necessary. These arms measure what each piece costs:

  arm        what it does                                                          gradient bytes moved per worker
  ---------  --------------------------------------------------------------------  -------------------------------
  oracle     one thread: W replica gradients in turn, summed in rank order,          -- (the reference answer)
             one optimizer step
  gloo       PyTorch as it is: W processes, DDP, gloo on 127.0.0.1                    2(W-1)/W S over TCP loopback
  replicas   W threads, DDP's structure in shared memory: W parameter copies,         2(W-1)/W S
             reduce-scatter + all-gather between them, W optimizer steps
  owner      W threads, ONE parameter copy: thread i owns 1/W of it, waits until      (W-1)/W S, no all-gather
             its slice is present in every gradient buffer (the data is the flag),
             sums it in rank order, updates its slice once; the next forward waits
             on the slices' versions, not on a barrier
  batch      one model, one batch of W x b, W intra-op threads: the sum over          0: the sum is the batch
             workers is the batch dimension of the weight-gradient GEMM               dimension inside the GEMM

The same answer, tested:
  replicas and owner must produce the oracle's parameters BIT FOR BIT: every sum is in rank order, every op elementwise
  or computed per replica with the same thread count.
  batch is the same mathematics (mean of W equal-sized means = mean over the W x b batch) in a different summation
  order, so it is checked by held-out loss and the largest parameter difference, not by hash.
  gloo sums in ring order (its own hash, as ddpbench always showed); checked by held-out loss.

The workload is ddpbench's: Linear(16,H), Tanh, (layers-2) x [Linear(H,H), Tanh], Linear(H,1); seed 17 for the model,
replica r's data from Generator(seed*1000 + r), target from Generator(7), held-out set from Generator(4242); SGD lr 0.02.
Hidden 1024, 6 layers is 4,216,833 parameters (16.9 MB), the mesh campaigns' model.

    python3 shmbench.py                       # W = this machine's cores, hidden 1024, 40 steps after 8 warmup
    python3 shmbench.py --world 4 --hidden 512 --steps 20 --arms oracle,owner,batch

Prints one line per arm as it finishes, then the table, then PASS/FAIL per check; exits 1 if any check fails.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time

import torch

SHM_BENCH_VERSION = "20261113-shm1"


# ------------------------------------------------------------------------------------------------ the workload
def build_model(a):
    torch.manual_seed(a.seed)
    w = a.hidden
    layers = [torch.nn.Linear(16, w), torch.nn.Tanh()]
    for _ in range(a.layers - 2):
        layers += [torch.nn.Linear(w, w), torch.nn.Tanh()]
    layers += [torch.nn.Linear(w, 1)]
    return torch.nn.Sequential(*layers)


def target():
    return torch.randn(16, generator=torch.Generator().manual_seed(7))


def replica_gens(a):
    return [torch.Generator().manual_seed(a.seed * 1000 + r) for r in range(a.world)]


def batch_for(g, a, tgt):
    x = torch.randn(a.batch, 16, generator=g)
    return x, torch.sin(x @ tgt) + 0.3 * (x @ tgt)


def heldout_and_sha(model, tgt):
    ge = torch.Generator().manual_seed(4242)
    xv = torch.randn(4096, 16, generator=ge)
    yv = torch.sin(xv @ tgt) + 0.3 * (xv @ tgt)
    with torch.no_grad():
        h = float(((model(xv).squeeze() - yv) ** 2).mean())
    flat = torch.cat([p.detach().flatten() for p in model.parameters()])
    return h, hashlib.sha256(flat.numpy().tobytes()).hexdigest()[:16], flat.clone()


def flat_views(model):
    """Move the model's parameters into one flat tensor and make each parameter a view of it, so a slice of the flat
    vector is a slice of the model."""
    ps = list(model.parameters())
    flat = torch.cat([p.detach().flatten() for p in ps]).contiguous()
    off = 0
    for p in ps:
        n = p.numel()
        p.data = flat[off:off + n].view_as(p)
        off += n
    return flat, ps


def replica_grad(model, ps, x, y, out):
    """One replica's gradient of its own mean loss, written into `out` (its own buffer) -- torch.autograd.grad, so
    nothing accumulates into the shared parameters' .grad."""
    loss = ((model(x).squeeze() - y) ** 2).mean()
    gs = torch.autograd.grad(loss, ps)
    off = 0
    for g in gs:
        n = g.numel()
        out[off:off + n].copy_(g.reshape(-1))
        off += n
    return float(loss.detach())


def rank_order_mean_into(acc, bufs, sl):
    """acc[sl] = (bufs[0][sl] + bufs[1][sl] + ... + bufs[W-1][sl]) / W, in rank order -- every arm that claims the
    oracle's bits uses exactly this."""
    acc[sl].copy_(bufs[0][sl])
    for b in bufs[1:]:
        acc[sl].add_(b[sl])
    acc[sl].div_(len(bufs))


def slices(n, w):
    step = (n + w - 1) // w
    return [slice(i * step, min(n, (i + 1) * step)) for i in range(w)]


class Timer:
    def __init__(self, a):
        self.a, self.samples, self.t0 = a, [], None

    def run(self, step_fn):
        for _ in range(self.a.warmup):
            step_fn()
        t_all = time.perf_counter()
        for _ in range(self.a.steps):
            t0 = time.perf_counter()
            step_fn()
            self.samples.append((time.perf_counter() - t0) * 1000.0)
        self.total_s = time.perf_counter() - t_all


def result(name, a, timer, model, tgt, extra=None):
    h, sha, flat = heldout_and_sha(model, tgt)
    s = sorted(timer.samples)
    r = {"arm": name, "total_s": timer.total_s, "ms_min": s[0], "ms_med": s[len(s) // 2], "ms_max": s[-1],
         "heldout": h, "sha": sha, "flat": flat}
    r.update(extra or {})
    print("  %-9s %8.1f ms/step (median)  %8.2f s for %d steps  heldout %.6f  sha %s"
          % (name, r["ms_med"], r["total_s"], a.steps, h, sha), flush=True)
    return r


# ------------------------------------------------------------------------------------------------ arms
def arm_oracle(a):
    """The reference: one thread, single-threaded ops, W replica gradients in turn, rank-order mean, one SGD step."""
    torch.set_num_threads(1)
    model, tgt, gens = build_model(a), target(), replica_gens(a)
    flat, ps = flat_views(model)
    bufs = [torch.empty_like(flat) for _ in range(a.world)]
    acc = torch.empty_like(flat)
    whole = slice(0, flat.numel())

    def step():
        for r in range(a.world):
            x, y = batch_for(gens[r], a, tgt)
            replica_grad(model, ps, x, y, bufs[r])
        rank_order_mean_into(acc, bufs, whole)
        flat.add_(acc, alpha=-a.lr)

    t = Timer(a); t.run(step)
    return result("oracle", a, t, model, tgt)


def arm_replicas(a):
    """DDP's structure inside one address space: W threads, W parameter copies, an all-reduce between them done as
    reduce-scatter (thread i sums slice i in rank order) and all-gather (every thread copies every summed slice into its
    own copy), then W identical optimizer steps. Barriers between the phases, as the collective's completion is."""
    torch.set_num_threads(1)
    tgt, gens = target(), replica_gens(a)
    models = [build_model(a) for _ in range(a.world)]
    fv = [flat_views(m) for m in models]
    flats = [f for f, _ in fv]
    bufs = [torch.empty_like(flats[0]) for _ in range(a.world)]
    acc = torch.empty_like(flats[0])
    sls = slices(flats[0].numel(), a.world)
    bar = threading.Barrier(a.world)
    go, done = threading.Barrier(a.world + 1), threading.Barrier(a.world + 1)
    stop = [False]

    def worker(r):
        model, ps = models[r], fv[r][1]
        while True:
            go.wait()
            if stop[0]:
                return
            x, y = batch_for(gens[r], a, tgt)
            replica_grad(model, ps, x, y, bufs[r])
            bar.wait()                                    # every gradient is in
            rank_order_mean_into(acc, bufs, sls[r])       # reduce-scatter: my slice of the sum
            bar.wait()                                    # every slice is summed
            for s in sls:                                 # all-gather: every summed slice into my own buffer
                bufs[r][s].copy_(acc[s])
            flats[r].add_(bufs[r], alpha=-a.lr)           # my own optimizer step on my own copy
            done.wait()

    ths = [threading.Thread(target=worker, args=(r,), daemon=True) for r in range(a.world)]
    for th in ths: th.start()

    def step():
        go.wait(); done.wait()

    t = Timer(a); t.run(step)
    stop[0] = True; go.wait()
    for th in ths: th.join()
    same = all(torch.equal(flats[0], f) for f in flats[1:])
    return result("replicas", a, t, models[0], tgt, {"copies_agree": same})


def arm_owner(a):
    """ONE parameter copy. Thread r writes only its own gradient buffer and only its own slice of the parameters.
    Readiness is the data itself: a per-step count of gradient buffers present (owners wait for W), and a per-slice
    version (a forward waits until every slice it reads is at the step it needs). No all-gather, one optimizer step per
    slice, no lock on any data -- the condition variable only carries the wakeup."""
    torch.set_num_threads(1)
    model, tgt, gens = build_model(a), target(), replica_gens(a)
    flat, ps = flat_views(model)
    bufs = [torch.empty_like(flat) for _ in range(a.world)]
    acc = torch.empty_like(flat)
    sls = slices(flat.numel(), a.world)
    cv = threading.Condition()
    present = {}                          # step -> gradient buffers written for that step
    version = [0] * a.world               # slice i holds the parameters after `version[i]` updates
    taken = [0] * a.world                 # replica r has consumed its buffer for this many steps (owners done reading)
    go, done = threading.Barrier(a.world + 1), threading.Barrier(a.world + 1)
    stop = [False]
    waits = {"fwd_ms": [0.0] * a.world, "own_ms": [0.0] * a.world}

    def worker(r):
        n = 0                                             # the step this thread is on
        while True:
            go.wait()
            if stop[0]:
                return
            t0 = time.perf_counter()
            with cv:                                      # forward reads every slice at version n
                cv.wait_for(lambda: min(version) >= n)
                cv.wait_for(lambda: taken[r] >= n)        # my buffer was read by every owner last step
            waits["fwd_ms"][r] += (time.perf_counter() - t0) * 1000.0
            x, y = batch_for(gens[r], a, tgt)
            replica_grad(model, ps, x, y, bufs[r])
            with cv:
                present[n] = present.get(n, 0) + 1
                cv.notify_all()
            t0 = time.perf_counter()
            with cv:                                      # my slice is present in every buffer
                cv.wait_for(lambda: present.get(n, 0) >= a.world)
            waits["own_ms"][r] += (time.perf_counter() - t0) * 1000.0
            rank_order_mean_into(acc, bufs, sls[r])
            flat[sls[r]].add_(acc[sls[r]], alpha=-a.lr)
            with cv:
                version[r] = n + 1
                if min(version) >= n + 1:                 # every owner has read every buffer for step n
                    for q in range(a.world):
                        taken[q] = n + 1
                    present.pop(n, None)
                cv.notify_all()
            n += 1
            done.wait()

    ths = [threading.Thread(target=worker, args=(r,), daemon=True) for r in range(a.world)]
    for th in ths: th.start()

    def step():
        go.wait(); done.wait()

    t = Timer(a); t.run(step)
    stop[0] = True; go.wait()
    for th in ths: th.join()
    steps = a.warmup + a.steps
    return result("owner", a, t, model, tgt,
                  {"wait_fwd_ms_per_step": max(waits["fwd_ms"]) / steps,
                   "wait_slice_ms_per_step": max(waits["own_ms"]) / steps})


def arm_batch(a):
    """One model, one batch: the W replicas' batches concatenated in rank order, loss = mean over all W x b samples,
    W intra-op threads. Mean of W equal-sized means equals the mean over the whole batch, so the gradient is the
    oracle's in exact arithmetic; the GEMM's accumulators do the sum, in their own order."""
    torch.set_num_threads(a.world)
    model, tgt, gens = build_model(a), target(), replica_gens(a)
    flat, ps = flat_views(model)

    def step():
        xs, ys = zip(*[batch_for(gens[r], a, tgt) for r in range(a.world)])
        x, y = torch.cat(xs), torch.cat(ys)
        loss = ((model(x).squeeze() - y) ** 2).mean()
        gs = torch.autograd.grad(loss, ps)
        with torch.no_grad():
            for p, g in zip(ps, gs):
                p.add_(g, alpha=-a.lr)

    t = Timer(a); t.run(step)
    torch.set_num_threads(1)
    return result("batch", a, t, model, tgt)


def _gloo_rank(r, a_dict, port, q):
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel as DDP
    a = argparse.Namespace(**a_dict)
    torch.set_num_threads(1)
    dist.init_process_group("gloo", init_method="tcp://127.0.0.1:%d" % port, rank=r, world_size=a.world)
    model, tgt = build_model(a), target()
    d = DDP(model)
    opt = torch.optim.SGD(d.parameters(), lr=a.lr)
    g = torch.Generator().manual_seed(a.seed * 1000 + r)

    def step():
        x, y = batch_for(g, a, tgt)
        loss = ((d(x).squeeze() - y) ** 2).mean()
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()

    t = Timer(a)
    dist.barrier(); t.run(step); dist.barrier()
    if r == 0:
        h, sha, flat = heldout_and_sha(model, tgt)
        q.put({"samples": t.samples, "total_s": t.total_s, "heldout": h, "sha": sha, "flat": flat.numpy().tobytes()})
    dist.destroy_process_group()


def arm_gloo(a):
    import socket
    import torch.multiprocessing as mp
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    ps = [ctx.Process(target=_gloo_rank, args=(r, vars(a), port, q)) for r in range(a.world)]
    for p in ps: p.start()
    got = q.get(timeout=3600)
    for p in ps: p.join(timeout=60)
    import numpy as np
    flat = torch.from_numpy(np.frombuffer(got["flat"], dtype=np.float32).copy())
    s = sorted(got["samples"])
    r = {"arm": "gloo", "total_s": got["total_s"], "ms_min": s[0], "ms_med": s[len(s) // 2], "ms_max": s[-1],
         "heldout": got["heldout"], "sha": got["sha"], "flat": flat}
    print("  %-9s %8.1f ms/step (median)  %8.2f s for %d steps  heldout %.6f  sha %s"
          % ("gloo", r["ms_med"], r["total_s"], a.steps, r["heldout"], r["sha"]), flush=True)
    return r


ARMS = {"oracle": arm_oracle, "gloo": arm_gloo, "replicas": arm_replicas, "owner": arm_owner, "batch": arm_batch}


# ------------------------------------------------------------------------------------------------ the suite
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--world", type=int, default=os.cpu_count() or 2, help="workers W (default: this machine's cores)")
    ap.add_argument("--hidden", type=int, default=1024)
    ap.add_argument("--layers", type=int, default=6)
    ap.add_argument("--batch", type=int, default=64, help="per-worker batch b")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--warmup", type=int, default=8)
    ap.add_argument("--arms", default="oracle,gloo,replicas,owner,batch")
    ap.add_argument("--heldout-tol", type=float, default=1e-4, help="relative held-out tolerance for batch and gloo")
    ap.add_argument("--json", help="also write the results here")
    a = ap.parse_args()
    arms = [x for x in a.arms.split(",") if x]
    if "oracle" not in arms:
        arms.insert(0, "oracle")                         # every check is against the oracle

    n = sum(p.numel() for p in build_model(a).parameters())
    S = n * 4
    print("shmbench %s  torch %s  %d cores  W=%d  hidden %d x %d layers  %d params (%.1f MB)  batch %d per worker  "
          "%d steps after %d warmup" % (SHM_BENCH_VERSION, torch.__version__, os.cpu_count() or 0, a.world, a.hidden,
                                        a.layers, n, S / 1e6, a.batch, a.steps, a.warmup), flush=True)
    res = {}
    for name in arms:
        if name not in ARMS:
            sys.exit("unknown arm %r (arms: %s)" % (name, ", ".join(ARMS)))
        res[name] = ARMS[name](a)

    o = res["oracle"]
    moved = {"oracle": "--", "gloo": 2 * (a.world - 1) / a.world * S, "replicas": 2 * (a.world - 1) / a.world * S,
             "owner": (a.world - 1) / a.world * S, "batch": 0.0}
    print()
    print("  %-9s %10s %10s %10s %10s %12s  %-16s %14s %10s" % ("arm", "median ms", "min ms", "total s", "vs gloo",
                                                               "heldout", "param sha", "max |dp|", "grad MB/w"))
    base = res.get("gloo")
    for name in arms:
        r = res[name]
        dp = float((r["flat"] - o["flat"]).abs().max())
        vs = ("%.2fx" % (base["total_s"] / r["total_s"])) if base else "--"
        mv = moved[name]
        print("  %-9s %10.1f %10.1f %10.2f %10s %12.6f  %-16s %14.3g %10s"
              % (name, r["ms_med"], r["ms_min"], r["total_s"], vs, r["heldout"], r["sha"], dp,
                 "--" if mv == "--" else "%.1f" % (mv / 1e6)))
    if "owner" in res:
        print("\n  owner waits per step (slowest thread): %.1f ms for the parameters before forward, %.1f ms for its "
              "slice in every buffer" % (res["owner"]["wait_fwd_ms_per_step"], res["owner"]["wait_slice_ms_per_step"]))

    print()
    fails = 0

    def check(ok, what):
        nonlocal fails
        print("  %s  %s" % ("PASS" if ok else "FAIL", what))
        fails += 0 if ok else 1

    for name in ("replicas", "owner"):
        if name in res:
            check(res[name]["sha"] == o["sha"], "%s: parameters bit-identical to the oracle (%s vs %s)"
                  % (name, res[name]["sha"], o["sha"]))
    if "replicas" in res:
        check(res["replicas"]["copies_agree"], "replicas: all %d parameter copies identical" % a.world)
    for name in ("batch", "gloo"):
        if name in res:
            rel = abs(res[name]["heldout"] - o["heldout"]) / abs(o["heldout"])
            check(rel <= a.heldout_tol, "%s: held-out %.6f within %.0e of the oracle's %.6f (relative %.2e)"
                  % (name, res[name]["heldout"], a.heldout_tol, o["heldout"], rel))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"version": SHM_BENCH_VERSION, "args": vars(a),
                       "arms": {k: {kk: vv for kk, vv in v.items() if kk != "flat"} for k, v in res.items()}}, f,
                      indent=1)
    print("\n  %s" % ("all checks passed" if not fails else "%d check(s) FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
