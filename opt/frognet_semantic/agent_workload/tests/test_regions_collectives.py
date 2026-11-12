"""test_regions_collectives.py -- every collective Psychedelic serves, on FrogTorch regions, with no peer links.

Two regions on two loopback "machines" (127.0.0.2, 127.0.0.3), four ranks, two per machine. Each rank's cells live
in its own machine's region. The expected answer for every collective is computed from the inputs, independently.

    PYTHONPATH=/opt/frognet_semantic python3 -m agent_workload.tests.test_regions_collectives [backend]

backend: psychedelic (default) or frognet (the Stage I backend).
"""
import os
import socket
import subprocess
import sys
import time

import torch
import torch.distributed as dist

WORLD = 4
HOSTS = ["127.0.0.2", "127.0.0.3"]


def free_port(host):
    s = socket.socket()
    s.bind((host, 0))
    p = s.getsockname()[1]
    s.close()
    return p


def worker(rank, backend, ns):
    from agent_workload.tuplespace import torch_store  # noqa: F401  registers frognet://
    mod = "psychedelic_backend" if backend == "psychedelic" else "torch_backend"
    __import__("agent_workload.tuplespace." + mod, fromlist=["register"]).register(backend)
    dist.init_process_group(backend, init_method="frognet://%s" % ns, rank=rank, world_size=WORLD)
    fails = []

    def check(what, got, want):
        # Sums of floats in a different order differ in the last bits; anything else is a wrong answer.
        if not torch.allclose(got, want, rtol=1e-5, atol=1e-5):
            fails.append("%s: max |got-want| = %g" % (what, (got - want).abs().max().item()))

    base = [torch.arange(8, dtype=torch.float32) * (r + 1) for r in range(WORLD)]
    t = base[rank].clone()
    dist.all_reduce(t)
    check("all_reduce", t, sum(base))
    big = [torch.randn(300_000, generator=torch.Generator().manual_seed(r)) for r in range(WORLD)]
    t = big[rank].clone()
    dist.all_reduce(t)
    check("all_reduce big", t, sum(big))
    t = base[rank].clone()
    dist.broadcast(t, src=2)
    check("broadcast", t, base[2])
    outs = [torch.zeros(8) for _ in range(WORLD)]
    dist.all_gather(outs, base[rank])
    for r in range(WORLD):
        check("all_gather[%d]" % r, outs[r], base[r])
    t = base[rank].clone()
    dist.reduce(t, dst=1)
    if rank == 1:
        check("reduce", t, sum(base))
    g = [torch.zeros(8) for _ in range(WORLD)] if rank == 3 else None
    dist.gather(base[rank], gather_list=g, dst=3)
    if rank == 3:
        for r in range(WORLD):
            check("gather[%d]" % r, g[r], base[r])
    out = torch.zeros(8)
    dist.scatter(out, scatter_list=[b.clone() for b in base] if rank == 0 else None, src=0)
    check("scatter", out, base[rank])
    if rank == 0:
        dist.send(base[0] + 100, dst=3)
    if rank == 3:
        r3 = torch.zeros(8)
        dist.recv(r3, src=0)
        check("send/recv", r3, base[0] + 100)
    if rank == 1:
        dist.send(base[1] + 7, dst=2)
    if rank == 2:
        r2 = torch.zeros(8)
        # Straight to the backend: PyTorch 2.15's dist.recv(src=None) asks a Work its dispatcher wraps, which has
        # no source rank of its own. The backend's own Work does.
        be = dist.group.WORLD._get_backend(torch.device("cpu"))
        w = be.recv_anysource([r2], 0)
        w.wait()
        check("recv_anysource", r2, base[1] + 7)
        if w._source_rank() != 1:
            fails.append("recv_anysource said %r, not 1" % w._source_rank())
    dist.barrier()
    inp = torch.arange(WORLD * 2, dtype=torch.float32) + 10 * rank
    out = torch.zeros(WORLD * 2)
    dist.all_to_all_single(out, inp)
    want = torch.cat([(torch.arange(WORLD * 2, dtype=torch.float32) + 10 * r)[2 * rank:2 * rank + 2]
                      for r in range(WORLD)])
    check("all_to_all_single", out, want)
    dist.barrier()
    dist.destroy_process_group()
    print("rank %d: %s" % (rank, "PASS" if not fails else "FAIL " + "; ".join(fails)), flush=True)
    return 0 if not fails else 1


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--rank":
        sys.exit(worker(int(sys.argv[2]), sys.argv[3], sys.argv[4]))
    backend = sys.argv[1] if len(sys.argv) > 1 else "psychedelic"
    ports = [free_port(h) for h in HOSTS]
    env = dict(os.environ)
    env["FROGTORCH_RAMS"] = ",".join("%s:%d" % (h, p) for h, p in zip(HOSTS, ports))
    rams = [subprocess.Popen([sys.executable, "-m", "agent_workload.tuplespace.frogtorch_ram", "--bind", h,
                              "--port", str(p), "--no-announce"], env=env) for h, p in zip(HOSTS, ports)]
    time.sleep(0.5)                       # test setup only: the hosts' listeners come up
    ns = "t%d" % os.getpid()
    procs = []
    for r in range(WORLD):
        e = dict(env)
        e["FROGTORCH_MEMBER_IP"] = HOSTS[r // 2]
        procs.append(subprocess.Popen([sys.executable, "-m", "agent_workload.tests.test_regions_collectives",
                                       "--rank", str(r), backend, ns], env=e))
    rc = [p.wait(timeout=300) for p in procs]
    for p in rams:
        p.terminate()
    ok = all(c == 0 for c in rc)
    print("%s: %s" % (backend, "ALL PASS" if ok else "FAIL %s" % rc))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
