# PyTorch on FrogNet shared memory (Psychedelic)

A `torch.distributed` backend and a set of DDP communication hooks that run PyTorch distributed training over FrogNet network shared memory instead of message-passing collectives. PyTorch's API is the contract; everything underneath it is rebuilt on the Ribbit model: each rank publishes what it has, and peers read what they need.

## Where the code is

This example is not in this directory. It lives at the paths the nodes run it from, so the repo is exactly what produced the results below:

| Path | What it is |
|---|---|
| `opt/frognet_semantic/agent_workload/tuplespace/` | The backends and hooks: `torch_backend.py` (the registered c10d backend, all 14 collectives), `psychedelic_backend.py` (the shared-memory reimplementation; `shard` reduce is the default), `ddp_hook.py` (DDP comm hooks: `faithful`, `fresh_only`, `every_k`), `tensor_plane.py` (the tensor data plane), `torch_store.py` (the c10d Store on tuple space), `frogtorch.py`, `frogtrain.py`, `policy.py`, `reduce_by_read.py`, `store_server.py`, `list_store.py`, `finite.py`, `frognet_work.cpp` (the C++20 Work shim PyTorch's completion contract needs) |
| `opt/frognet_semantic/agent_workload/aiconnect/` | Producer/service side of the AI connection |
| `opt/frognet_semantic/agent_workload/experiment/` | Capture, analysis and report tools |
| `usr/local/frognet-ai/` | The benchmark harness: `ddpbench.py` (one DDP run), `hookrun.sh` (the ten-arm DDP campaign), `gate.py` (store-gated start barrier), `netbench1.py` (collectives), `runme.sh`, `repeat.sh`, `proof.sh`, `fit.py`, `hookfit.py`, `proof_collect.py`, `plot_proof.py`, `job_archive.py`, `showerr.py`, `install.sh` |
| `opt/frognet_semantic/simulation/collective_tier.py`, `psychedelic.py` | Simulator tiers for this code, run by `run_all.py` |

`usr/local/frognet-ai/ddpbench.py` is the copy the campaigns run (`hookrun.sh` calls it from its own directory). There is no second copy under `agent_workload/`.

## Requirements

PyTorch (CPU or CUDA) and numpy on every rank, a FrogNet node or an attached machine on every rank, and the SystemRam (`databasehost.frognet`) reachable from all of them. The model and every setting must be the same on every rank.

## Running a DDP campaign

On every rank, at the same time, with the same run id number (one never used before):

```
sh /usr/local/frognet-ai/hookrun.sh <this machine's mesh address> <run id number>
```

That runs ten arms, ddp<N> to ddp<N+9>:

| Arm | Backend | Hook | gradient_as_bucket_view |
|---|---|---|---|
| 0 | gloo (control) | none | 0 |
| 1 | psychedelic/shard | none | 0 |
| 2 | psychedelic | faithful | 0 |
| 3 | psychedelic | fresh_only | 0 |
| 4 | psychedelic | every_k | 0 |
| 5–9 | same five | | 1 |

Each arm waits at a store-gated barrier until all ranks arrive. Results land in `/tmp/nb3`; `hookfit.py` reads them. The rank-to-address table, master address and world size are set at the top of `hookrun.sh`.

## Correctness contract

gloo, psychedelic/shard and faithful compute the same sum, so within each one every rank must end with the same parameter hash, and all three must reach the same held-out loss. fresh_only and every_k incorporate less on purpose, so their ranks are expected to diverge; their held-out loss is the measured cost of the policy.

## Results (campaign ddp58xx, 2026-09-15)

Four ranks: AI-Host (x86, Seattle), Seattle5 (Raspberry Pi 5), Seattle7 (Raspberry Pi 4) and a machine in New York over WireGuard. The SystemRam was in New York, so the control plane sat on the slowest link. Measured per-peer link rates ranged from 25–49 MB/s down to 0.4–0.8 MB/s.

Min step time in ms, ratio against the same session's gloo control:

| Arm | bucket_view=0 | ratio | bucket_view=1 | ratio |
|---|---|---|---|---|
| gloo (control) | 16639 (ddp5800) | 1.00 | 12055 (ddp5885) | 1.00 |
| psychedelic/shard | 10221 (ddp5801) | 1.63x | 10729 (ddp5886) | 1.12x |
| faithful hook | 23521 (ddp5802) | 0.71x | 20902 (ddp5887) | 0.58x |
| fresh_only hook | 13198 (ddp5883) | | 10673 (ddp5888) | 1.13x |
| every_k hook | 5175 (ddp5884) | | 5196 (ddp10009, a later session) | |

Correctness, all four ranks:

| Arm | Parameter hash | Held-out loss |
|---|---|---|
| gloo | 3622a2291d8b592a on every rank | 0.4940 |
| psychedelic/shard | df67ff9ce033941b on every rank | 0.4940 |
| faithful | 93e7773b4bca3814 on every rank | 0.4940 |
| fresh_only | four different hashes, by design | 0.4962–0.4983 |
| every_k | four different hashes, by design | 0.4991–0.5045 |

Shard and gloo hashes differ from each other because shard sums in a different order than gloo's ring; agreement across ranks within a backend is the contract, and every agreeing arm holds the same hash and loss at both bucket_view settings.

fresh_only skipped 20–22% of peer state on the two well-connected ranks and 38–48% on the two slow ones, without being told the topology. every_k skips 51% on every rank, on a schedule.

Excluded, with no mechanism behind them: rank 0's 760 ms in ddp5883 (its peers 13198–14419) and rank 0's 2541 ms in ddp10009 (its peers 5196–5731).

## PyTorch's own test suite

The registered backend (`torch_backend.py`) was run against PyTorch's distributed functional suite: 111 tests, 31 pass, 75 skip as not applicable, 5 fail with documented root causes.

## License

GPL-2.0-only, like the rest of this repository. See COPYRIGHT and LICENSE at the root. Commercial licenses and support are available from Fawcett Innovations LLC.
