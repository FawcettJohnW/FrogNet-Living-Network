# Matched audit: PyTorch's distributed stack and its Ribbit replacements

Run on 2026-11-15 with lizard 1.26.0, using the rules of the lispers.net audit: defaults, functions only. Module-level
code does not count as a function on either side. The stock code is PyTorch v2.15.0 (31a77fc9), the release the
runs used, and gloo 74cc005a, its pinned submodule. Every file is listed in `files-*.txt` and every function in
`functions-*.csv`.

## What each side contains

**Stock backend: gloo on CPU over TCP, as `ProcessGroupGloo` uses it.**
- `ProcessGroupGloo` and its `gloo/` helpers. The CUDA files are excluded.
- `TCPStore` with the libuv backend, which is the default in this release, plus `socket`, `PrefixStore` and
  `torch/distributed/rendezvous.py`.
- From gloo: the algorithms `ProcessGroupGloo` calls (allreduce, allgather(v), alltoall(v), barrier, broadcast,
  gather(v), reduce, scatter), plus context, math, types, `common/`, the rendezvous context and stores, and the
  TCP transport.
- Excluded from gloo: the legacy algorithm templates the CPU path does not call (ring, bcube, halving-doubling,
  local), CUDA, MPI, NCCL, ibverbs, uv, TLS, tests, benchmarks and examples.

**Stock DDP** is the stock backend plus the parts of DDP itself:
- `torch/nn/parallel/distributed.py`;
- the reducer (excluding CUDA), `comm`;
- the default and Python comm hooks;
- DDP's logger.

**Ribbit backend** (`agent_workload/tuplespace/`) is the same contract with every collective implemented:
`torch_backend.py`, `psychedelic_backend.py`, `tensor_plane.py`, `finite.py` and `torch_store.py` (the c10d store
and the `frognet://` rendezvous). It also includes the native link `frognet_link.cpp` and `frognet_work.cpp`. Both
the Python byte mover and the C++ one are counted, so the duplicate counts against Ribbit.

**meshfeed** is `usr/local/frognet-ai/meshfeed.py`, the training arrangement that replaces DDP and its backend.

**Platform** is `frogtorch_ram.py` (the RAM) and `frogtorch_ram_client.py`, reported in its own row.

## What the audit does not do

It does not run a reachability analysis on the stock side, as the lispers.net audit did. Whole files are counted,
which includes option handling inside `ProcessGroupGloo` for device types the run did not use. Both sides are
counted the same way. Stock is mostly C++ and Ribbit is mostly Python; the two lines are not the same unit. CCN
counts decisions in both languages and is the more direct comparison.

## Result

| | files | functions | NLOC | total CCN | functions over CCN 15 |
|---|---:|---:|---:|---:|---:|
| stock backend: ProcessGroupGloo + gloo + TCPStore | 103 | 982 | 10,658 | 2,129 | 4 |
| Ribbit backend | 7 | 282 | 3,621 | 1,139 | 8 |
| Ribbit backend + platform | 9 | 332 | 4,117 | 1,356 | 10 |
| stock DDP: DDP + the stock backend | 115 | 1,203 | 14,360 | 2,920 | 9 |
| meshfeed | 1 | 7 | 323 | 111 | 1 |
| meshfeed + platform | 3 | 57 | 819 | 328 | 3 |
| (platform alone) | 2 | 50 | 496 | 217 | 2 |

**The backend, which keeps the collective contract:** 66% less code and 47% less complexity than gloo's stack. With
the platform included, that is 61% less code and 36% less complexity.

**meshfeed, the rewrite:** 98% less code and 96% less complexity than DDP over gloo. With the platform included,
that is 94% less code and 89% less complexity.

The backend and meshfeed do different jobs. The backend keeps every collective exact, so DDP runs over it unchanged.
meshfeed changes the arrangement: the data comes to the compute, and no collective is left to implement.
