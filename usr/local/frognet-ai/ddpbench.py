#!/usr/bin/env python3
"""ddpbench.py -- DDP training step cost, with and without bucket views.

    python3 ddpbench.py --rank 0 --world 4 --backend psychedelic \
        --addr 10.250.250.100 --master 10.250.250.100 \
        --store databasehost.frognet:80 --runid ddp1 --bucket-view 1 \
        --out /tmp/nb3

WHY THIS EXISTS

netbench1 measures collectives directly. It does not measure DDP, and DDP
is where the copies PyTorch itself makes actually land:

  * a gradient is copied INTO the bucket
  * the bucket is all-reduced
  * the bucket is copied BACK into the gradient

That is two full passes over every parameter, per step, on top of the
collective. PyTorch already has the fix -- Reducer::Bucket holds a flat
tensor with per-variable offset and length, and `gradient_as_bucket_view`
makes each gradient a view into it rather than a copy. It defaults to False.

So this exists to answer one question with a number: what does turning it
on cost or save, per backend.

CORRECTNESS IS PART OF THE MEASUREMENT

Views change what a gradient IS, so a faster step that trains differently
is not a saving. Every run reports a held-out loss and a hash of the final
parameters, computed identically on every rank from a fixed seed. Two runs
that agree on those trained the same model; a run that does not is a wrong
answer however fast it was.

WHAT THE FLAG NEEDS

  gradient_as_bucket_view=True   on the DDP constructor
  optimizer.zero_grad(set_to_none=True)

The second is not optional. zero_grad() writes zeros into the gradients,
and once they are views that writes into the communication buffer.

A caveat from PyTorch's own documentation, worth knowing before reading the
result: with a communication hook registered, the bucket-view alias is
destroyed every iteration, so the flag "alone cannot avoid copies". The
saving should appear most clearly with no hook.
"""
import argparse
import hashlib
import json
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--world", type=int, required=True)
    ap.add_argument("--backend", default="psychedelic")
    ap.add_argument("--reduce", default="", choices=["", "stack", "shard"])
    ap.add_argument("--addr", required=True, help="this machine's mesh address")
    ap.add_argument("--master", required=True)
    ap.add_argument("--master-port", default="29591")
    ap.add_argument("--store", default="")
    ap.add_argument("--runid", default="")
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--pypath", default="/etc/frognet_bundles/communicator")
    ap.add_argument("--out", default="/tmp/nb3")
    ap.add_argument("--bucket-view", default="1", choices=["0", "1"],
                    help="gradient_as_bucket_view. 1 makes each gradient a "
                         "view into the flat bucket instead of a copy.")
    ap.add_argument("--hook", default="",
                    help="ddp_hook name, or empty for stock DDP. The flag's "
                         "saving is clearest with no hook -- see the note in "
                         "this file's docstring.")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--hidden", type=int, default=512,
                    help="model width. Bigger means more gradient bytes, "
                         "which is what the copies are proportional to.")
    ap.add_argument("--layers", type=int, default=6)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--peer-wait", type=float, default=300.0)
    ap.add_argument("--diag", action="store_true")
    a = ap.parse_args()

    for p in ([a.pypath] if os.path.isdir(a.pypath) else []) + [a.tree]:
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ["FROGNET_DIAG"] = "1" if a.diag else "0"
    os.environ.setdefault("FROGNET_SENTINEL_DIR", "/tmp/netbench1.sentinel")
    os.makedirs("/tmp/netbench1.sentinel", exist_ok=True)
    os.environ["MASTER_ADDR"] = a.master
    os.environ["MASTER_PORT"] = a.master_port
    os.environ["FROGNET_PSY_HOST"] = a.addr
    os.environ["FROGNET_PSY_PEER_WAIT_S"] = str(a.peer_wait)
    if a.reduce:
        os.environ["FROGNET_PSY_REDUCE"] = a.reduce
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
    from torch.nn.parallel import DistributedDataParallel as DDP

    view = a.bucket_view == "1"
    r = {"rank": a.rank, "world": a.world, "backend": a.backend,
         "reduce": a.reduce or None, "bucket_view": view, "hook": a.hook or None,
         "steps": a.steps, "hidden": a.hidden, "layers": a.layers,
         "batch": a.batch, "seed": a.seed, "addr": a.addr, "runid": a.runid,
         "status": "FAIL"}

    name = "%sr%d.%s.bv%d%s.json" % ((a.runid + ".") if a.runid else "",
                                     a.rank, a.backend, int(view),
                                     ("." + a.hook) if a.hook else "")
    outpath = os.path.join(a.out, name) if os.path.isdir(a.out) else a.out
    os.makedirs(os.path.dirname(outpath) or ".", exist_ok=True)
    print("  writing %s" % outpath)

    try:
        if a.backend in ("psychedelic", "frognet"):
            mod = ("psychedelic_backend" if a.backend == "psychedelic"
                   else "torch_backend")
            be = __import__("agent_workload.tuplespace." + mod,
                            fromlist=["register"])
            be.register(a.backend)
        r["under_test"] = _under_test(a.tree)

        dist.init_process_group(a.backend, rank=a.rank, world_size=a.world)

        # Identical model on every rank: the seed is shared, the data is not.
        torch.manual_seed(a.seed)
        layers = []
        w = a.hidden
        layers += [torch.nn.Linear(16, w), torch.nn.Tanh()]
        for _ in range(a.layers - 2):
            layers += [torch.nn.Linear(w, w), torch.nn.Tanh()]
        layers += [torch.nn.Linear(w, 1)]
        model = torch.nn.Sequential(*layers)
        r["params_n"] = sum(p.numel() for p in model.parameters())
        r["grad_mb"] = round(r["params_n"] * 4 / 1e6, 3)

        d = DDP(model, gradient_as_bucket_view=view)
        if a.hook:
            from agent_workload.tuplespace import ddp_hook
            ddp_hook.attach(d, a.rank, a.world, a.hook)

        opt = torch.optim.SGD(d.parameters(), lr=0.02)
        tgt = torch.randn(16, generator=torch.Generator().manual_seed(7))
        g = torch.Generator().manual_seed(a.seed * 1000 + a.rank)

        def step():
            x = torch.randn(a.batch, 16, generator=g)
            y = torch.sin(x @ tgt) + 0.3 * (x @ tgt)
            loss = ((d(x).squeeze() - y) ** 2).mean()
            # set_to_none is required with bucket views: zero_grad() would
            # otherwise write zeros into the communication buffer itself.
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            return float(loss)

        # [SAY_WHERE_YOU_ARE_V1] A step here can take tens of seconds, and
        # this printed nothing until the whole arm finished -- so a run that
        # was working and a run that was wedged looked identical for
        # minutes at a time, and the only way to tell them apart was py-spy
        # on another terminal.
        #
        # Every line is flushed and carries a wall clock, so `tail -f` on
        # the log shows progress, and the gap between two lines IS the step
        # cost without waiting for the fit.
        def note(msg):
            print("    [%s] %s" % (time.strftime("%H:%M:%S"), msg),
                  flush=True)

        note("group formed, %d ranks; %.1f MB of gradients"
             % (a.world, r["grad_mb"]))
        for i in range(a.warmup):
            t0 = time.perf_counter()
            step()
            if i == 0:
                note("first step done in %.1f s (this one pays the "
                     "connection setup)" % (time.perf_counter() - t0))
        note("warmup done (%d steps), measuring %d" % (a.warmup, a.steps))

        samples = []
        every = max(1, a.steps // 8)
        t_meas = time.perf_counter()
        for i in range(a.steps):
            t0 = time.perf_counter()
            last = step()
            samples.append((time.perf_counter() - t0) * 1000.0)
            if (i + 1) % every == 0 or i + 1 == a.steps:
                done = sorted(samples)
                note("step %d/%d   last %.0f ms   best %.0f ms   "
                     "elapsed %.0f s" % (i + 1, a.steps, samples[-1],
                                         done[0],
                                         time.perf_counter() - t_meas))
        samples.sort()
        r["ms_step_min"] = samples[0]
        r["ms_step_med"] = samples[len(samples) // 2]
        r["ms_step_max"] = samples[-1]
        r["ms_step_samples"] = samples
        r["train_loss"] = last

        # Correctness. The held-out set is identical on every rank, so a
        # faster step that trained a different model shows up here.
        ge = torch.Generator().manual_seed(4242)
        xv = torch.randn(4096, 16, generator=ge)
        yv = torch.sin(xv @ tgt) + 0.3 * (xv @ tgt)
        with torch.no_grad():
            r["heldout"] = float(((model(xv).squeeze() - yv) ** 2).mean())
        flat = torch.cat([p.detach().flatten() for p in model.parameters()])
        r["param_sha"] = hashlib.sha256(
            flat.numpy().tobytes()).hexdigest()[:16]

        # [A_MECHANISM_NOBODY_CALLS_IS_NOT_A_MECHANISM_V1] The hook counts
        # what it skipped and nothing recorded it, so both skipping arms
        # have been reported as wall clock and held-out loss with no
        # denominator. "Less work incorporated" is a number, not a claim.
        if a.hook:
            try:
                from agent_workload.tuplespace import ddp_hook as _dh
                _st = getattr(_dh, "_STATE", None)
                if _st is not None and hasattr(_st, "stats"):
                    r["hook"] = a.hook
                    r["hook_stats"] = _st.stats()
            except Exception as _e:
                r["hook_stats_error"] = "%s: %s" % (type(_e).__name__, _e)

        for attr in ("plane_stats", "link_stats", "timing_stats"):
            pg = dist.distributed_c10d._get_default_group()
            impl = getattr(pg, "_get_backend", lambda *_: None)(
                torch.device("cpu")) if hasattr(pg, "_get_backend") else None
            if impl is not None and hasattr(impl, attr):
                r[attr.replace("_stats", "")] = getattr(impl, attr)()
        r["status"] = "ok"
    except Exception:
        import traceback
        tb = traceback.format_exc()
        exc = "".join(traceback.format_exception_only(*sys.exc_info()[:2]))
        notes = "\n".join(getattr(sys.exc_info()[1], "__notes__", []) or [])
        head = (exc + ("\n" + notes if notes else "")).strip()
        room = max(0, 3000 - len(head))
        r["error"] = head + ("\n\n--- frames ---\n" + tb[-room:]
                             if room > 200 else "")
    finally:
        # The result is written BEFORE the linger below, so a rank that waited
        # out a dead peer still has its numbers on disk.
        json.dump(r, open(outpath, "w"))
        # [A_PEER_IS_NOT_GONE_UNTIL_EVERY_PEER_SAYS_SO_V1 - John 2026-09-14]
        # Do not take this rank's tensor plane away while peers are still
        # reading it. Exiting here is what failed ddp5402 on a teardown race
        # rather than on anything it computed: a rank that finished became
        # "Connection refused ... that peer's process is gone" to every peer
        # still inside its own steps.
        #
        # In the finally, so a rank that DIED also publishes done and releases
        # the others rather than making them wait out the full timeout for a
        # process that is not coming back.
        #
        # The wait is bounded by the hook state's own timeout_s and it SAYS
        # where it is, once a second is far too quiet for something that can
        # legitimately sit for minutes.
        try:
            from agent_workload.tuplespace import ddp_hook as _hk
            if _hk._STATE is not None:
                _t0 = time.perf_counter()
                _hk._STATE.linger(logger=lambda m: print(
                    "    [%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True))
                print("    [%s] linger done in %.1f s"
                      % (time.strftime("%H:%M:%S"),
                         time.perf_counter() - _t0), flush=True)
        except Exception as _le:
            print("  linger failed: %r -- peers may see this plane close "
                  "under them" % (_le,), file=sys.stderr)
        try:
            import torch.distributed as _d
            if _d.is_initialized():
                _d.destroy_process_group()
        except Exception:
            pass

    if r["status"] == "ok":
        print("  %-11s bucket_view=%-5s  %7.2f ms/step (min)  %.4f heldout  "
              "%s" % (a.backend, view, r["ms_step_min"], r["heldout"],
                      r["param_sha"]))
        return 0
    print("  FAILED: %s" % (r.get("error", "").splitlines() or ["?"])[0],
          file=sys.stderr)
    return 1


def _under_test(tree):
    import hashlib as _h
    out = []
    # [SAY_WHICH_FILE_IS_RUNNING_V1 - John 2026-09-15] ddp_hook is in this
    # list now. It was not, and an out-of-rev copy of it on ONE node wrote
    # bags the current drift() could not read -- which surfaced as
    # "'int' object has no attribute 'get'" with no version evidence
    # anywhere in the output, on the only arms that read a bag mid-step.
    for mod in ("core.frognet_tuples",
                "agent_workload.tuplespace.tensor_plane",
                "agent_workload.tuplespace.torch_backend",
                "agent_workload.tuplespace.psychedelic_backend",
                "agent_workload.tuplespace.ddp_hook"):
        try:
            m = __import__(mod, fromlist=["__file__"])
            p = m.__file__
            out.append({"module": mod,
                        "sha": _h.sha256(open(p, "rb").read()).hexdigest()[:12],
                        "path": p})
        except Exception:
            pass
    try:
        import torch
        out.append({"module": "torch", "sha": torch.__version__, "path": "-"})
    except Exception:
        pass
    # This file itself, by the path it is actually running from. Two copies
    # of ddpbench.py existed for a day and the dead one was the one being
    # edited.
    try:
        out.append({"module": "ddpbench",
                    "sha": _h.sha256(
                        open(os.path.abspath(__file__), "rb").read()
                    ).hexdigest()[:12],
                    "path": os.path.abspath(__file__)})
    except Exception:
        pass
    return out


if __name__ == "__main__":
    raise SystemExit(main())
