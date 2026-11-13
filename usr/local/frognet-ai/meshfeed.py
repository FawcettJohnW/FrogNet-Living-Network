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

BUILD = "20261113-meshfeed1"
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
    path = os.path.join(a.data_dir, "r%d-s%d-b%d-n%d.pt" % (R, a.seed, a.batch, N))
    tgt = torch.randn(16, generator=torch.Generator().manual_seed(7))
    if not os.path.exists(path):
        g = torch.Generator().manual_seed(a.seed * 1000 + R)
        xs = torch.stack([torch.randn(a.batch, 16, generator=g) for _ in range(N)])
        ys = torch.sin(xs @ tgt) + 0.3 * (xs @ tgt)
        torch.save({"x": xs, "y": ys}, path + ".tmp")
        os.replace(path + ".tmp", path)
        note("data: wrote %s (%d minibatches of %d), this node's only" % (path, N, a.batch))
    d = torch.load(path)
    cells = torch.cat([d["x"], d["y"].unsqueeze(-1)], dim=2).contiguous()        # [N, batch, 17]: inputs | target
    cell_b = cells[0].numel() * cells.element_size()
    note("data: read %s from disk, %d minibatches, %d bytes each" % (path, N, cell_b))

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
        c = a.compute if a.compute >= 0 else min(range(W), key=lambda r: (peers[r]["ms_step"], r))
        T.put(svc, "plan", "plan", {"compute": c, "why": "forced by --compute" if a.compute >= 0 else
                                    "fastest measured: %.1f ms per step" % peers[c]["ms_step"]}, own=False)
    plan = None
    while time.time() < deadline and plan is None:
        for row in T.get_all(svc, wait_s=min(30.0, a.timeout), min_rows=W + 1):
            if row.get("var") == "plan" and isinstance(row.get("value"), dict):
                plan = row["value"]
    if plan is None:
        sys.exit("meshfeed: no plan from rank 0 after %.0f s" % a.timeout)
    C = int(plan["compute"])
    note("plan: compute on r%d (%s) -- %s; the other %d ranks feed it %d bytes per step each"
         % (C, peers[C]["node"], plan["why"], W - 1, cell_b))

    # ---------------------------------------------------------------------------------------------- 5. feed
    if R != C:
        t0 = time.perf_counter()
        for t in range(N):
            server.publish("mf%d.mb.%d" % (R, t), t + 1, cells[t], incarnation=str(R), readers=1)
        note("feed: %d minibatches published in %.1f ms (%d bytes); waiting for the result"
             % (N, (time.perf_counter() - t0) * 1000.0, N * cell_b))
        res = None
        while time.time() < deadline and res is None:
            for row in T.get_all(svc, wait_s=min(60.0, a.timeout), min_rows=W + 2):
                if row.get("var") == "result" and isinstance(row.get("value"), dict):
                    res = row["value"]
        if res is None:
            sys.exit("meshfeed: no result after %.0f s" % a.timeout)
        print_result(res, a)
        server.close()
        return 0

    # ---------------------------------------------------------------------------------------------- 6. compute
    subs = {}
    for r in range(W):
        if r != C:
            subs[r] = P.StreamSubscription("mf-c%d" % C, peers[r]["host"], int(peers[r]["port"]),
                                           "meshfeed-r%d-from-r%d" % (C, r), family=[r"mf%d\.mb\.\d+" % r])
    model = build_model()
    ps = list(model.parameters())
    torch.set_num_threads(cores)
    slots = [torch.empty_like(cells[0]) for _ in range(W)]

    def step(t):
        tw = time.perf_counter()
        waits = []
        for r in range(W):
            if r == C:
                slots[r].copy_(cells[t]); waits.append("r%d local" % r); continue
            name = "mf%d.mb.%d" % (r, t)
            cell = subs[r].take(name, dst=P.writable_bytes(slots[r]), timeout_s=a.timeout)
            if cell["n"] != cell_b:
                raise RuntimeError("r%d's %s is %d bytes, expected %d" % (r, name, cell["n"], cell_b))
            if not cell.get("direct"):
                slots[r].reshape(-1).copy_(P.plane_view(cell["buf"], torch.float32, slots[r].numel()))
                subs[r].release(cell)
            waits.append("r%d %.1fms%s%s" % (r, cell.get("waited_ms", 0.0), " early" if cell.get("early") else "",
                                              " direct" if cell.get("direct") else ""))
        data_ms = (time.perf_counter() - tw) * 1000.0
        tc = time.perf_counter()
        xy = torch.cat(slots)                                   # rank order: r0's 64, r1's 64, ...
        x, y = xy[:, :16], xy[:, 16]
        loss = ((model(x).squeeze() - y) ** 2).mean()
        gs = torch.autograd.grad(loss, ps)
        with torch.no_grad():
            for p, g in zip(ps, gs):
                p.add_(g, alpha=-a.lr)
        comp_ms = (time.perf_counter() - tc) * 1000.0
        print("[DIAG-MESHFEED] STEP %d data_ms=%.1f compute_ms=%.1f loss=%.5f  %s"
              % (t, data_ms, comp_ms, float(loss.detach()), "  ".join(waits)), flush=True)
        return data_ms, comp_ms

    note("compute: %d minibatches of %d per step on %d cores" % (W, a.batch, cores))
    t_formed = time.perf_counter()
    for i in range(a.warmup):
        t0 = time.perf_counter()
        step(i)
        if i == 0:
            note("first step done in %.1f s" % (time.perf_counter() - t0))
    note("warmup done (%d steps), measuring %d" % (a.warmup, a.steps))
    samples, dsum, csum = [], 0.0, 0.0
    t_meas = time.perf_counter()
    every = max(1, a.steps // 8)
    for i in range(a.steps):
        t0 = time.perf_counter()
        dm, cm = step(a.warmup + i)
        samples.append((time.perf_counter() - t0) * 1000.0)
        dsum += dm; csum += cm
        if (i + 1) % every == 0 or i + 1 == a.steps:
            note("step %d/%d   last %.0f ms   best %.0f ms   elapsed %.1f s"
                 % (i + 1, a.steps, samples[-1], min(samples), time.perf_counter() - t_meas))
    measured_s = time.perf_counter() - t_meas
    ge = torch.Generator().manual_seed(4242)
    xv = torch.randn(4096, 16, generator=ge)
    yv = torch.sin(xv @ tgt) + 0.3 * (xv @ tgt)
    with torch.no_grad():
        heldout = float(((model(xv).squeeze() - yv) ** 2).mean())
    flat = torch.cat([p.detach().flatten() for p in ps])
    s = sorted(samples)
    res = {"build": BUILD, "compute": C, "compute_node": peers[C]["node"], "world": W, "cores": cores,
           "ms_step_min": s[0], "ms_step_med": s[len(s) // 2], "ms_step_max": s[-1], "measured_s": measured_s,
           "formed_to_done_s": time.perf_counter() - t_formed, "data_ms_mean": dsum / a.steps,
           "compute_ms_mean": csum / a.steps, "heldout": heldout,
           "param_sha": hashlib.sha256(flat.numpy().tobytes()).hexdigest()[:16],
           "bytes_per_step_in": (W - 1) * cell_b, "streams": {str(r): subs[r].stats() for r in subs}}
    T.put(svc, "result", "result", res, own=False)
    print_result(res, a)
    server.close()
    return 0


def print_result(res, a):
    note("result: compute on r%d (%s), %d cores" % (res["compute"], res["compute_node"], res["cores"]))
    note("  %d measured steps in %.1f s (group formed to done %.1f s); per step: min %.1f ms, median %.1f ms, "
         "max %.1f ms -- of it %.1f ms waiting for minibatches, %.1f ms computing"
         % (a.steps, res["measured_s"], res["formed_to_done_s"], res["ms_step_min"], res["ms_step_med"],
            res["ms_step_max"], res["data_ms_mean"], res["compute_ms_mean"]))
    note("  %d bytes into the compute node per step" % res["bytes_per_step_in"])
    print("  meshfeed    compute=r%-2d  %8.2f ms/step (min)  %.4f heldout  %s"
          % (res["compute"], res["ms_step_min"], res["heldout"], res["param_sha"]), flush=True)


if __name__ == "__main__":
    sys.exit(main())
