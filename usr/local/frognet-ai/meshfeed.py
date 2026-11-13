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
"""meshfeed.py -- the data comes to the compute: ddpbench's training, with each node's minibatches read out of the
tuple space by the machine that computes fastest, instead of every node exchanging 16.9 MB of gradient per step.

Why (John, 2026-11-13): under DDP's arrangement -- every node computes on its own data and holds its own model -- any
reduction moves 2(W-1)/W x 16.9 MB through every node per step (54 MB at W=5), and through Seattle7's ~3 MB/s path
that is an 18 s floor that gloo and Psychedelic both sit on. The answer needs only the sum of every node's gradient,
and a gradient can be computed wherever the node's data can be read. A node's minibatch is 4,352 bytes (64 x 16
inputs and 64 targets); its gradient is 16.9 MB.

Run on every node, like hookrun.sh runs ddpbench.py, same run id:

    python3 meshfeed.py --rank R --world W --runid ID --addr $(/usr/local/bin/getEth0Address)

What each node does:
  1. Its data: a file on its own disk, /var/cache/frognet/meshfeed/r<R>-s<seed>-b<batch>-n<N>.pt, written the first
     time from ddpbench's own generator for that rank (Generator(seed*1000 + R), one randn(batch,16) per step, so the
     model trained is ddpbench's model). After that the run READS it from disk; no other node has it.
  2. Its capability: it times the step the compute node will run -- one batch of W x batch on all its cores -- and
     publishes that with its plane address and build, in its own cell of meshfeed.<ID>.
  3. Rendezvous: a held read on meshfeed.<ID> until all W cells are there. Mixed builds refuse, naming them.
  4. The plan: rank 0 owns the plan cell. The compute node is the fastest measured (lowest ms per step; --compute R
     on rank 0 overrides). Every node holds a read on the plan cell.
  5. Every node but the compute node publishes its N minibatches on its own tensor plane, each cell read once
     (readers=1) and then retired. It writes in its own time -- nothing paces the producer.
  6. The compute node holds a family read on each node's minibatch cells (mf<R>\\.mb\\.\\d+): each write fires it and
     the 4 KB lands straight into that node's slot. Each step it takes the W minibatches in rank order, runs ONE
     batch of W x batch on all its cores (shmbench's batch arm), and updates the one copy of the parameters.
  7. The compute node writes the result cell; every node holds a read on it, prints it, and exits.

The same answer: the mean over the W x batch samples equals DDP's mean of W per-rank means exactly, with ddpbench's
model, seed, data, learning rate and step count, so the held-out loss is ddpbench's (0.4947 at hidden 1024, W=5); the
GEMM sums in its own order, so the parameter hash is its own (shmbench's batch arm: 3e-08 from the oracle).

Extended (meshfeed2, John 2026-11-13 "Construct the extended tests"):
  --k N            the compute set is the N fastest nodes. Every node's minibatches go to one of them (more to the
                   faster ones, by measured step time); each computes the gradient of its own share in one batch,
                   and the compute set sums their gradients with an all_reduce (--reduce-backend psychedelic|gloo,
                   psychedelic = the shard plane) before every node in it applies the same update.
  --feed-hz F      a producer publishes one minibatch every 1/F s, read from its file at that moment: data that
                   arrives over time, not a dataset published up front (0 = as fast as the disk reads it).
  --check          (k=1) after training, the compute node trains the oracle on the same minibatches -- one thread,
                   each replica's gradient in turn, summed in rank order, as shmbench's oracle -- and reports both
                   held-out losses at full precision, the largest parameter difference and the oracle's time.
  The data file is a .npy, memory-mapped: each minibatch is read from disk when it is published.

Lines: [DIAG-MESHFEED] STEP per step (each node's minibatch: waited ms, landed before it was asked, landed straight
into its slot; compute ms), the plane's own [DIAG-STREAM] LINK/LANDED/SENT lines with TCP_INFO, and the progress lines
ddpbench prints, so the two compare line for line.
"""
import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time

BUILD = "20261113-meshfeed2"
SERVICE = "meshfeed"


def note(msg):
    print("    [%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--world", type=int, required=True)
    ap.add_argument("--runid", required=True)
    ap.add_argument("--addr", required=True, help="this node's mesh address (getEth0Address)")
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--hidden", type=int, default=1024)
    ap.add_argument("--layers", type=int, default=6)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--warmup", type=int, default=8)
    ap.add_argument("--compute", type=int, default=-1, help="on rank 0: put the compute on this rank (default: fastest)")
    ap.add_argument("--data-dir", default="/var/cache/frognet/meshfeed")
    ap.add_argument("--timeout", type=float, default=1800.0, help="seconds any wait may take before the run fails")
    ap.add_argument("--k", type=int, default=1, help="compute set: the K fastest nodes")
    ap.add_argument("--reduce-backend", default="psychedelic", choices=["psychedelic", "gloo"])
    ap.add_argument("--feed-hz", type=float, default=0.0, help="publish one minibatch every 1/F s (0: as read)")
    ap.add_argument("--check", action="store_true", help="k=1: train the oracle afterwards and compare")
    ap.add_argument("--peer-wait", type=float, default=300.0)
    a = ap.parse_args()
    sys.path.insert(0, a.tree)
    import torch
    from agent_workload.tuplespace import frogtorch_ram_client as T
    from agent_workload.tuplespace import tensor_plane as P

    W, R, N = a.world, a.rank, a.warmup + a.steps
    svc = "%s.%s" % (SERVICE, a.runid)
    cores = os.cpu_count() or 1
    print("meshfeed %s  rank %d of %d at %s  %s  torch %s  %d cores  run %s"
          % (BUILD, R, W, a.addr, socket.gethostname(), torch.__version__, cores, a.runid), flush=True)

    # ---------------------------------------------------------------------------------------------- 1. the data
    os.makedirs(a.data_dir, exist_ok=True)
    import numpy as np
    path = os.path.join(a.data_dir, "r%d-s%d-b%d-n%d.npy" % (R, a.seed, a.batch, N))
    tgt = torch.randn(16, generator=torch.Generator().manual_seed(7))
    if not os.path.exists(path):
        g = torch.Generator().manual_seed(a.seed * 1000 + R)
        xs = torch.stack([torch.randn(a.batch, 16, generator=g) for _ in range(N)])
        ys = torch.sin(xs @ tgt) + 0.3 * (xs @ tgt)
        with open(path + ".tmp", "wb") as f:
            np.save(f, torch.cat([xs, ys.unsqueeze(-1)], dim=2).contiguous().numpy())
        os.replace(path + ".tmp", path)
        note("data: wrote %s (%d minibatches of %d, %.1f MB), this node's only"
             % (path, N, a.batch, os.path.getsize(path) / 1e6))
    disk = np.load(path, mmap_mode="r")                     # [N, batch, 17]: inputs | target, read when used

    def cell(t):
        return torch.from_numpy(np.array(disk[t], dtype=np.float32))
    cell_b = int(disk[0].size) * 4
    note("data: %s on disk, %d minibatches, %d bytes each, memory-mapped" % (path, N, cell_b))

    # ---------------------------------------------------------------------------------------------- 2. capability
    def build_model():
        torch.manual_seed(a.seed)
        w = a.hidden
        layers = [torch.nn.Linear(16, w), torch.nn.Tanh()]
        for _ in range(a.layers - 2):
            layers += [torch.nn.Linear(w, w), torch.nn.Tanh()]
        layers += [torch.nn.Linear(w, 1)]
        return torch.nn.Sequential(*layers)

    torch.set_num_threads(cores)
    probe = build_model()
    xb = torch.randn(W * a.batch, 16)
    yb = torch.randn(W * a.batch)
    best = None
    for _ in range(3):
        t0 = time.perf_counter()
        loss = ((probe(xb).squeeze() - yb) ** 2).mean()
        torch.autograd.grad(loss, list(probe.parameters()))
        dt = (time.perf_counter() - t0) * 1000.0
        best = dt if best is None else min(best, dt)
    del probe
    server = P.TensorServer(a.addr)
    me = {"host": a.addr, "port": server.port, "build": BUILD, "cores": cores, "ms_step": round(best, 2),
          "node": socket.gethostname()}
    note("capability: one step of %d x %d on %d cores = %.1f ms; plane at %s:%d"
         % (W, a.batch, cores, best, a.addr, server.port))

    # ---------------------------------------------------------------------------------------------- 3. rendezvous
    T.put(svc, "r%d" % R, "r%d" % R, me, own=False)
    deadline = time.time() + a.timeout
    peers = {}
    while time.time() < deadline and not set(range(W)).issubset(peers):
        for row in T.get_all(svc, wait_s=min(30.0, a.timeout), min_rows=W):
            v, val = row.get("var", ""), row.get("value")
            if re.fullmatch(r"r\d+", v) and isinstance(val, dict):
                peers[int(v[1:])] = val
    if not set(range(W)).issubset(peers):
        sys.exit("meshfeed: rendezvous incomplete after %.0f s -- ranks %s never published"
                 % (a.timeout, sorted(set(range(W)) - set(peers))))
    builds = {peers[r]["build"] for r in range(W)}
    if len(builds) != 1:
        sys.exit("meshfeed: mixed builds, refusing: %s"
                 % ", ".join("r%d=%s" % (r, peers[r]["build"]) for r in range(W)))
    note("group formed, %d ranks: %s" % (W, ", ".join("r%d %s %.0f ms" % (r, peers[r]["node"], peers[r]["ms_step"])
                                                       for r in range(W))))

    # ---------------------------------------------------------------------------------------------- 4. the plan
    if R == 0:
        if a.compute >= 0:
            cset = [a.compute]
            why = "forced by --compute"
        else:
            cset = sorted(sorted(range(W), key=lambda r: (peers[r]["ms_step"], r))[:max(1, min(a.k, W))])
            why = "the %d fastest measured" % len(cset)
        # every rank's minibatches go to one compute node: its own to itself, the rest to whichever compute node
        # would finish its share soonest (measured time per minibatch, ms_step / W)
        load = {c: 1 for c in cset}
        assign = {c: c for c in cset}
        for r in range(W):
            if r in cset:
                continue
            c = min(cset, key=lambda c: ((load[c] + 1) * peers[c]["ms_step"], c))
            assign[r] = c; load[c] += 1
        T.put(svc, "plan", "plan", {"compute": cset, "assign": {str(r): assign[r] for r in range(W)},
                                    "why": why}, own=False)
    plan = None
    while time.time() < deadline and plan is None:
        for row in T.get_all(svc, wait_s=min(30.0, a.timeout), min_rows=W + 1):
            if row.get("var") == "plan" and isinstance(row.get("value"), dict):
                plan = row["value"]
    if plan is None:
        sys.exit("meshfeed: no plan from rank 0 after %.0f s" % a.timeout)
    CSET = [int(c) for c in plan["compute"]]
    ASSIGN = {int(r): int(c) for r, c in plan["assign"].items()}
    C = CSET[0]                                                    # the compute leader writes the result
    # rows in meshfeed.<ID> once each stage is written: W rank cells, the plan, a model hash per compute node (k>1),
    # the result -- each held read waits for its row count, so none of them spins
    ROWS_SHAS = W + 1 + (len(CSET) if len(CSET) > 1 else 0)
    ROWS_RESULT = ROWS_SHAS + 1
    note("plan: compute on %s -- %s; %s"
         % (", ".join("r%d %s" % (c, peers[c]["node"]) for c in CSET), plan["why"],
            "; ".join("r%d <- %s" % (c, ",".join("r%d" % r for r in range(W) if ASSIGN[r] == c)) for c in CSET)))

    # ---------------------------------------------------------------------------------------------- 5. feed
    if R not in CSET:
        t0 = time.perf_counter()
        for t in range(N):
            if a.feed_hz > 0:
                due = t0 + t / a.feed_hz
                if due > time.perf_counter():
                    time.sleep(due - time.perf_counter())
            server.publish("mf%d.mb.%d" % (R, t), t + 1, cell(t), incarnation=str(R), readers=1)
        note("feed: %d minibatches read from disk and published to r%d in %.1f s (%d bytes); waiting for the result"
             % (N, ASSIGN[R], time.perf_counter() - t0, N * cell_b))
        res = None
        while time.time() < deadline and res is None:
            for row in T.get_all(svc, wait_s=min(60.0, a.timeout), min_rows=ROWS_RESULT):
                if row.get("var") == "result" and isinstance(row.get("value"), dict):
                    res = row["value"]
        if res is None:
            sys.exit("meshfeed: no result after %.0f s" % a.timeout)
        print_result(res, a)
        server.close()
        return 0

    # ---------------------------------------------------------------------------------------------- 6. compute
    mine = [r for r in range(W) if ASSIGN[r] == R]                 # the minibatches this compute node takes
    J, K = CSET.index(R), len(CSET)
    subs = {}
    for r in mine:
        if r != R:
            subs[r] = P.StreamSubscription("mf-c%d" % R, peers[r]["host"], int(peers[r]["port"]),
                                           "meshfeed-r%d-from-r%d" % (R, r), family=[r"mf%d\.mb\.\d+" % r])
    if K > 1:                                                       # the compute set's own all_reduce
        os.environ["FROGNET_PSY_HOST"] = a.addr
        os.environ["FROGNET_PSY_PEER_WAIT_S"] = str(a.peer_wait)
        os.environ["FROGNET_PSY_REDUCE"] = "shard"
        for k_, v_ in (("FROGNET_C10D_SERVICE", "c10d"), ("FROGNET_PSY_SERVICE", "psyc10d"),
                       ("FROGNET_TORCH_SERVICE", "torch")):
            os.environ[k_] = "mf.%s.%s" % (a.runid, v_)
        import torch.distributed as dist
        if a.reduce_backend == "psychedelic":
            from agent_workload.tuplespace import psychedelic_backend
            psychedelic_backend.register("psychedelic")
        from agent_workload.tuplespace import torch_store  # noqa: F401  registers frognet://
        dist.init_process_group(a.reduce_backend, init_method="frognet://mf%s" % a.runid, rank=J, world_size=K)
        note("compute set formed: %d nodes, all_reduce on %s" % (K, a.reduce_backend))
    model = build_model()
    ps = list(model.parameters())
    torch.set_num_threads(cores)
    slots = {r: torch.empty(a.batch, 17) for r in mine}
    kept = [] if (a.check and K == 1) else None

    def step(t):
        tw = time.perf_counter()
        waits = []
        for r in mine:
            if r == R:
                slots[r].copy_(cell(t)); waits.append("r%d local" % r); continue
            name = "mf%d.mb.%d" % (r, t)
            c_ = subs[r].take(name, dst=P.writable_bytes(slots[r]), timeout_s=a.timeout)
            if c_["n"] != cell_b:
                raise RuntimeError("r%d's %s is %d bytes, expected %d" % (r, name, c_["n"], cell_b))
            if not c_.get("direct"):
                slots[r].reshape(-1).copy_(P.plane_view(c_["buf"], torch.float32, slots[r].numel()))
                subs[r].release(c_)
            waits.append("r%d %.1fms%s%s" % (r, c_.get("waited_ms", 0.0), " early" if c_.get("early") else "",
                                              " direct" if c_.get("direct") else ""))
        data_ms = (time.perf_counter() - tw) * 1000.0
        if kept is not None:
            kept.append({r: slots[r].clone() for r in mine})
        tc = time.perf_counter()
        xy = torch.cat([slots[r] for r in mine])                     # rank order
        x, y = xy[:, :16], xy[:, 16]
        loss = ((model(x).squeeze() - y) ** 2).mean()
        gs = torch.autograd.grad(loss, ps)
        comp_ms = (time.perf_counter() - tc) * 1000.0
        red_ms = 0.0
        if K > 1:
            # sum of my replicas' mean gradients = (my batch mean) x len(mine); the set's sum / W = the global mean
            tr = time.perf_counter()
            flat = torch.cat([g.reshape(-1) for g in gs]).mul_(len(mine))
            dist.all_reduce(flat)
            flat.div_(W)
            red_ms = (time.perf_counter() - tr) * 1000.0
            off = 0
            gs2 = []
            for p in ps:
                gs2.append(flat[off:off + p.numel()].view_as(p)); off += p.numel()
            gs = gs2
        with torch.no_grad():
            for p, g in zip(ps, gs):
                p.add_(g, alpha=-a.lr)
        print("[DIAG-MESHFEED] STEP %d data_ms=%.1f compute_ms=%.1f reduce_ms=%.1f loss=%.5f  %s"
              % (t, data_ms, comp_ms, red_ms, float(loss.detach()), "  ".join(waits)), flush=True)
        return data_ms, comp_ms, red_ms

    note("compute: %d minibatches of %d per step on %d cores (%s)" % (len(mine), a.batch, cores,
                                                                       ",".join("r%d" % r for r in mine)))
    t_formed = time.perf_counter()
    for i in range(a.warmup):
        t0 = time.perf_counter()
        step(i)
        if i == 0:
            note("first step done in %.1f s" % (time.perf_counter() - t0))
    note("warmup done (%d steps), measuring %d" % (a.warmup, a.steps))
    samples, dsum, csum, rsum = [], 0.0, 0.0, 0.0
    t_meas = time.perf_counter()
    every = max(1, a.steps // 8)
    for i in range(a.steps):
        t0 = time.perf_counter()
        dm, cm, rm = step(a.warmup + i)
        samples.append((time.perf_counter() - t0) * 1000.0)
        dsum += dm; csum += cm; rsum += rm
        if (i + 1) % every == 0 or i + 1 == a.steps:
            note("step %d/%d   last %.0f ms   best %.0f ms   elapsed %.1f s"
                 % (i + 1, a.steps, samples[-1], min(samples), time.perf_counter() - t_meas))
    measured_s = time.perf_counter() - t_meas
    formed_s = time.perf_counter() - t_formed
    ge = torch.Generator().manual_seed(4242)
    xv = torch.randn(4096, 16, generator=ge)
    yv = torch.sin(xv @ tgt) + 0.3 * (xv @ tgt)

    def held_and_sha(m):
        with torch.no_grad():
            h = float(((m(xv).squeeze() - yv) ** 2).mean())
        fl = torch.cat([p.detach().flatten() for p in m.parameters()])
        return h, hashlib.sha256(fl.numpy().tobytes()).hexdigest()[:16], fl

    heldout, sha, flat_final = held_and_sha(model)
    if K > 1:                                                        # every compute node must hold the same model
        T.put(svc, "sha.c%d" % R, "sha", {"sha": sha, "node": peers[R]["node"]}, own=False)
    if R != C:
        res = None
        while time.time() < deadline and res is None:
            for row in T.get_all(svc, wait_s=min(60.0, a.timeout), min_rows=ROWS_RESULT):
                if row.get("var") == "result" and isinstance(row.get("value"), dict):
                    res = row["value"]
        if res is None:
            sys.exit("meshfeed: no result after %.0f s" % a.timeout)
        print_result(res, a)
        server.close()
        return 0
    agree = None
    if K > 1:
        shas = {}
        while time.time() < deadline and len(shas) < K:
            for row in T.get_all(svc, wait_s=min(30.0, a.timeout), min_rows=ROWS_SHAS):
                v = row.get("var", "")
                if v.startswith("sha.c") and isinstance(row.get("value"), dict):
                    shas[v[5:]] = row["value"]["sha"]
        agree = {"compute_shas": shas, "agree": len(set(shas.values())) == 1 and len(shas) == K}
    check = None
    if kept is not None:
        note("check: training the oracle on the same %d steps (one thread, each replica in turn, rank order)" % N)
        torch.set_num_threads(1)
        om = build_model()
        ops = list(om.parameters())
        tq = time.perf_counter()
        for st in kept:
            acc = None
            for r in range(W):
                xr, yr = st[r][:, :16], st[r][:, 16]
                lr_ = ((om(xr).squeeze() - yr) ** 2).mean()
                gr = torch.autograd.grad(lr_, ops)
                acc = list(gr) if acc is None else [x_ + y_ for x_, y_ in zip(acc, gr)]
            with torch.no_grad():
                for p, g in zip(ops, acc):
                    p.add_(g / W, alpha=-a.lr)
        oracle_s = time.perf_counter() - tq
        oh, osha, oflat = held_and_sha(om)
        check = {"oracle_heldout": oh, "oracle_sha": osha, "oracle_s": oracle_s,
                 "max_abs_dp": float((oflat - flat_final).abs().max()),
                 "heldout_rel": abs(oh - heldout) / abs(oh)}
    s_ = sorted(samples)
    res = {"build": BUILD, "compute": CSET, "compute_node": ",".join(peers[c]["node"] for c in CSET), "world": W,
           "cores": cores, "k": K, "batch": a.batch, "feed_hz": a.feed_hz, "steps": a.steps,
           "ms_step_min": s_[0], "ms_step_med": s_[len(s_) // 2], "ms_step_max": s_[-1], "measured_s": measured_s,
           "formed_to_done_s": formed_s, "data_ms_mean": dsum / a.steps, "compute_ms_mean": csum / a.steps,
           "reduce_ms_mean": rsum / a.steps, "heldout": heldout, "param_sha": sha,
           "bytes_per_step_in": (len(mine) - 1) * cell_b, "agree": agree, "check": check,
           "streams": {str(r): subs[r].stats() for r in subs}}
    T.put(svc, "result", "result", res, own=False)
    print_result(res, a)
    server.close()
    return 0


def print_result(res, a):
    note("result: compute on %s (k=%d), batch %d per node, feed %s"
         % (res["compute_node"], res["k"], res["batch"], ("%.1f Hz" % res["feed_hz"]) if res["feed_hz"] else "as read"))
    note("  %d measured steps in %.2f s (group formed to done %.2f s); per step: min %.1f ms, median %.1f ms, "
         "max %.1f ms -- of it %.1f ms waiting for minibatches, %.1f ms computing, %.1f ms reducing"
         % (res["steps"], res["measured_s"], res["formed_to_done_s"], res["ms_step_min"], res["ms_step_med"],
            res["ms_step_max"], res["data_ms_mean"], res["compute_ms_mean"], res["reduce_ms_mean"]))
    if res.get("agree"):
        note("  compute set models: %s -- %s" % (res["agree"]["compute_shas"],
                                                  "identical" if res["agree"]["agree"] else "DIFFERENT"))
    c = res.get("check")
    if c:
        note("  check: heldout %.9f, oracle %.9f (relative %.2e); largest parameter difference %.3g; "
             "oracle sha %s; oracle took %.1f s on one thread"
             % (res["heldout"], c["oracle_heldout"], c["heldout_rel"], c["max_abs_dp"], c["oracle_sha"],
                c["oracle_s"]))
    print("  meshfeed    k=%d compute=%-14s  %8.2f ms/step (min)  %8.2f ms/step (median)  %.9f heldout  %s"
          % (res["k"], ",".join("r%d" % x for x in res["compute"]), res["ms_step_min"], res["ms_step_med"],
             res["heldout"], res["param_sha"]), flush=True)


if __name__ == "__main__":
    sys.exit(main())
