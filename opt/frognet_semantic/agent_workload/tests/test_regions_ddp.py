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
"""test_regions_ddp.py -- DDP training on FrogTorch regions: every rank must end with the same parameters.

Four ranks, two regions on two loopback "machines". Arms: gloo (on the frognet:// store, so even the control has
no TCPStore), psychedelic with no hook, and psychedelic with the faithful hook. Within an arm every rank must have the
same parameter hash; across arms the held-out loss must agree to 3 places (summation order differs).

    PYTHONPATH=/opt/frognet_semantic python3 -m agent_workload.tests.test_regions_ddp
"""
import hashlib
import json
import os
import subprocess
import sys
import time

import torch
import torch.distributed as dist

from agent_workload.tests.test_regions_collectives import HOSTS, free_port

WORLD = 4
STEPS = 12


def worker(rank, arm, ns, out):
    from agent_workload.tuplespace import torch_store  # noqa: F401  frognet://
    backend, hook = (arm.split("+") + [""])[:2]
    if backend == "psychedelic":
        from agent_workload.tuplespace import psychedelic_backend
        psychedelic_backend.register("psychedelic")
    dist.init_process_group(backend, init_method="frognet://%s" % ns, rank=rank, world_size=WORLD)
    torch.manual_seed(0)
    model = torch.nn.Sequential(torch.nn.Linear(16, 256), torch.nn.Tanh(), torch.nn.Linear(256, 256),
                                torch.nn.Tanh(), torch.nn.Linear(256, 1))
    ddp = torch.nn.parallel.DistributedDataParallel(model, bucket_cap_mb=0.25)
    if hook:
        from agent_workload.tuplespace import ddp_hook
        ddp_hook.attach(ddp, rank, WORLD, hook)
    opt = torch.optim.SGD(ddp.parameters(), lr=0.05)
    g = torch.Generator().manual_seed(100 + rank)
    for _ in range(STEPS):
        x = torch.randn(64, 16, generator=g)
        y = x.sum(1, keepdim=True).sin()
        opt.zero_grad()
        torch.nn.functional.mse_loss(ddp(x), y).backward()
        opt.step()
    if hook:
        from agent_workload.tuplespace import ddp_hook
        ddp_hook._STATE.linger()
    hx = torch.randn(512, 16, generator=torch.Generator().manual_seed(7))
    with torch.no_grad():
        held = torch.nn.functional.mse_loss(model(hx), hx.sum(1, keepdim=True).sin()).item()
    sha = hashlib.sha256(b"".join(p.detach().numpy().tobytes() for p in model.parameters())).hexdigest()[:16]
    dist.destroy_process_group()
    with open(out, "w") as f:
        json.dump({"rank": rank, "arm": arm, "sha": sha, "heldout": held}, f)
    return 0


def run_arm(arm, env):
    ns = "d%d%s" % (os.getpid(), arm.replace("+", "_"))
    outs = ["/tmp/regions_ddp_%s_%d.json" % (ns, r) for r in range(WORLD)]
    procs = []
    for r in range(WORLD):
        e = dict(env)
        e["FROGTORCH_MEMBER_IP"] = HOSTS[r // 2]
        # as ddpbench does: every arm's services named by its run id, so an arm never reads the last arm's cells
        for k, v in (("FROGNET_C10D_SERVICE", "c10d"), ("FROGNET_PSY_SERVICE", "psyc10d"),
                     ("FROGNET_TORCH_SERVICE", "torch"), ("FROGNET_HOOK_SERVICE", "ddphook")):
            e[k] = "nb1.%s.%s" % (ns, v)
        procs.append(subprocess.Popen([sys.executable, "-m", "agent_workload.tests.test_regions_ddp", "--rank",
                                       str(r), arm, ns, outs[r]], env=e))
    rc = [p.wait(timeout=600) for p in procs]
    if any(rc):
        return {"arm": arm, "rc": rc}
    res = [json.load(open(o)) for o in outs]
    return {"arm": arm, "shas": sorted({x["sha"] for x in res}), "heldout": [round(x["heldout"], 6) for x in res]}


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--rank":
        sys.exit(worker(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]))
    ports = [free_port(h) for h in HOSTS]
    env = dict(os.environ)
    env["FROGTORCH_RAMS"] = ",".join("%s:%d" % (h, p) for h, p in zip(HOSTS, ports))
    rams = [subprocess.Popen([sys.executable, "-m", "agent_workload.tuplespace.frogtorch_ram", "--bind", h,
                              "--port", str(p), "--no-announce"], env=env) for h, p in zip(HOSTS, ports)]
    time.sleep(0.5)                       # test setup only: the hosts' listeners come up
    results = [run_arm(a, env) for a in ("gloo", "psychedelic", "psychedelic+faithful")]
    # the skipping hooks: ranks need not agree (that is the policy), but every rank must finish and train
    skipping = [run_arm(a, env) for a in ("psychedelic+fresh_only", "psychedelic+every_k")]
    for p in rams:
        p.terminate()
    ok = True
    ref = None
    for r in results:
        print(json.dumps(r))
        if "rc" in r or len(r["shas"]) != 1:
            ok = False
            continue
        if ref is None:
            ref = r["heldout"][0]
        if abs(r["heldout"][0] - ref) > 1e-3:
            ok = False
    for r in skipping:
        print(json.dumps(r))
        if "rc" in r or len(r["heldout"]) != 4 or max(r["heldout"]) > 2 * ref:
            ok = False
    print("ddp: %s" % ("ALL PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
