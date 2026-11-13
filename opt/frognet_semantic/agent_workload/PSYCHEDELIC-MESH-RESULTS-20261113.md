# Psychedelic and the FrogNet mesh: results, 12–13 November 2026

This report stands on its own. Every number is from a run named in it. Anything that isn't measured is marked as an
estimate.

## 1. Summary

- **DDP's arrangement, gloo against Psychedelic.**
  - **With the network as it was:** Psychedelic shard beat gloo, at 1,045 s against 1,379 s for 40 measured steps
    (1.32×). It also beat gloo at bucket view 1, 1,170 s against 5,465 s (4.7×).
  - **The network was the cause.** The difference came from a fault in the network, not from the backend. Seattle5's
    Wi-Fi access point ran a single first-in-first-out queue (pfifo_fast). That queue slowed gloo's ring more than
    Psychedelic's parallel reads.
  - **With the queue fixed (fq_codel), the two tie.** gloo took 1,018 s and shard 1,040 s, with the same held-out loss
    of 0.4947.
- **The floor.**
  - Under DDP's arrangement, every node moves about 54 MB per step at W=5, in and out combined. That holds for gloo's
    ring and for shard alike, because both are bandwidth-optimal.
  - Seattle7's path carries about 23–25 Mb/s (~3 MB/s) in total, in both directions combined, measured with iperf3.
  - That puts a floor of about 18 s per step under any DDP algorithm on this mesh. The measured steps are ~20–26 s.
- **Change the arrangement, not the algorithm.**
  - The training answer needs only the sum of every node's gradient. A node's minibatch is 4,352 bytes; its gradient
    is 16.9 MB.
  - **meshfeed** sends each node's minibatches through the tuple space to the machine that measures fastest.
  - On the mesh it trained ddpbench's model to ddpbench's held-out loss, 0.4947, in **2.9 s** for 40 measured steps.
    gloo took 1,018 s and shard 1,040 s. That is about **350×**.
- **What that result is.** One machine, BAMacBook, did all of the arithmetic. The other four contributed their data,
  4 KB per step each. The plan was chosen from measured step times.
  - For this workload, the right use of the network is to carry data, not gradients. DDP can't make that choice,
    because its ranks are fixed.
  - It is not yet shown that splitting the compute across the mesh beats gloo. The extended tests in section 9 are
    built to measure that, and haven't run on the mesh yet.

## 2. The mesh, the workload, how it was measured

**Nodes in the five-rank campaigns**, with their hookrun ranks:

| rank | node | address | measured step (W×64 samples, all cores) |
|---|---|---|---|
| 0 | Seattle5 | 10.250.250.1 | 117 ms |
| 1 | Seattle7 | 10.170.170.1 | 369 ms |
| 2 | New-York-1 | 10.123.123.1 | 346 ms |
| 3 | BAMacBook | 10.188.188.1 | 64 ms |
| 4 | Seattle3B | 10.251.251.1 | 95 ms |

Step times are from meshfeed's own measurement in run mf1. September's campaigns used four ranks: ai-host (x86, on
Seattle5's wired LAN at 25–37 MB/s), Seattle5, Seattle7 and New-York-1.

**Workload** (ddpbench.py; the same in every test here):
- **Model.** Linear(16,1024), Tanh, 4 × [Linear(1024,1024), Tanh], Linear(1024,1). That is 4,216,833 parameters,
  or 16.9 MB of gradient.
- **Initialization.** Seed 17, so every rank starts from the same model.
- **Data.** Rank r's data comes from Generator(17×1000 + r): one minibatch of 64 × 16 inputs per step, with targets
  sin(x·t) + 0.3(x·t), where t comes from Generator(7).
- **Training.** SGD at learning rate 0.02, 8 warmup steps, then 40 measured steps.
- **Check.** Held-out loss on 4,096 samples from Generator(4242), plus a SHA-256 of the final parameters.

**Method.**
- Each control runs in the same session as the arms it's compared with.
- The comparison is total time, not the best single step.
- The control's numbers are read from its run, never estimated.
- Diagnostics come from the code's own lines:
  - `[DIAG-STREAM] LINK/SENT/LANDED` with TCP_INFO;
  - `[DIAG-PSY] ROUND/STALL`;
  - `[DIAG-MESHFEED] STEP`;
  - the kernel's interface and queue counters;
  - iperf3.

## 3. DDP campaigns: gloo against Psychedelic

### 3.1 Before and after the queue fix (bucket view 0)

| arm | before fq_codel | with fq_codel |
|---|---|---|
| gloo, 40 measured steps | 1,379 s (ddp4420) | **1,018 s** (ddp4430) |
| Psychedelic shard, 40 measured steps | **1,045 s** (ddp4421) | 1,040 s (ddp4431) |
| shard vs gloo | 1.32× ahead | 0.98× (2% behind) |
| gloo first step / warmup | 82.6 s / 6 m 19 s | 31.7 s / 2 m 41 s |
| shard first step / warmup | 28.3 s / 3 m 17 s | 25.4 s / 3 m 15 s |
| held-out, both arms | 0.4947 | 0.4947 |

### 3.2 The whole pre-fix campaign (ddp4420–4429, BAMacBook's view)

| arm | bucket view 0: 40 steps | heldout | bucket view 1: 40 steps | heldout |
|---|---|---|---|---|
| gloo | 1,379 s | 0.4947 | 5,465 s | 0.4947 |
| Psychedelic shard | **1,045 s** | 0.4947 | **1,170 s** | 0.4947 |
| hook faithful | 2,623 s | 0.4947 | 3,041 s | 0.4947 |
| hook fresh_only | 1,275 s | 0.5006 | 1,159 s | 0.5008 |
| hook every_k | 701 s | 0.5004 | 659 s | 0.5004 |

- **The skipping hooks.** fresh_only and every_k give a different answer: held-out 0.5004–0.5008 against 0.4947,
  because they skip peers' contributions.
- **Their min ms/step means nothing.** A rank that falls behind finds its peers' cells already landed, so it reports
  steps of 48–738 ms while elapsed time tells the real story.

### 3.3 The September results, for reference

Four ranks: ai-host, Seattle5, Seattle7, New-York-1. Times are median seconds per step.

| campaign | bucket view | gloo | shard | shard vs gloo |
|---|---|---|---|---|
| ddp570x | off | 29.8 | 15.3 | 1.95× |
| ddp580x | off | 22.0 | 13.4 | 1.64× |
| ddp588x | on | 13.4 | 13.0 | 1.03× |
| ddp1500x | on | 22.7 | 12.9 | 1.75× |

Shard held at 12.3–15.3 s across five campaigns; gloo swung between 13.1 and 62 s. The September report's explanation:
gloo's ring makes every rank wait on the slowest hop, while a shard rank pays only for its own reads. faithful lost to
gloo in every September campaign (0.40–0.82×). The report calls it the hook family's control, not a contender.

## 4. Network findings

### 4.1 The queue on Seattle5's access point

Seattle7 and Seattle3B both joined Seattle5's wlan0 as Wi-Fi clients. Interface counters, read after the campaign:

| host / interface | role | queue discipline | sent | drops |
|---|---|---|---|---|
| Seattle5 wlan0 | access point for Seattle7 and Seattle3B | **pfifo_fast**, 1,000 packets | 34.0 GB | **9,364** |
| Seattle5 eth0 | wired | pfifo_fast | 23.4 GB | 0 |
| Seattle7 wlan1 | client, also carries wg0 | fq_codel | 24.4 GB | 0 |
| Seattle3B wlan1 | client | **pfifo_fast** | 13.3 GB | 0 |

**What the link showed while the queue was there.** The Seattle7 ↔ Seattle3B link is a 3 ms path. Earlier runs
measured it at 0.06–0.25 MB/s. Its sender's TCP_INFO showed rtt 787–1,676 ms and cwnd up to 2,415 segments, while only
14–421 of its own segments were unacknowledged. The flow was waiting behind every other flow in a shared queue of more
than a second.

**The fix.** fq_codel gives each flow its own queue and holds queueing delay near 5 ms.

| | |
|---|---|
| applied by hand | `tc qdisc replace dev wlan0 root fq_codel` on Seattle5; `... dev wlan1 ...` on Seattle3B |
| made permanent | `net.core.default_qdisc=fq_codel`, in `/etc/sysctl.d/90-frognet-fq_codel.conf` on both |
| in the tree | [FQ_CODEL_EVERYWHERE_V1]; see section 12 |

Two minutes after the change, Seattle5's wlan0 had passed 160 MB with 0 drops and 0 backlog.

### 4.2 The ceiling on Seattle7's path

iperf3 between Seattle7 and Seattle5, over the route then in use (`10.170.170.1 dev wg0`):

| test | Seattle7 → Seattle5 | Seattle5 → Seattle7 | combined |
|---|---|---|---|
| `-R`, one direction only | — | 22.6–23.0 Mb/s | 23.0 Mb/s |
| `--bidir` | 23.0 Mb/s | 2.5 Mb/s, 59 retransmits | **25.5 Mb/s** |

The path carries about 23–25 Mb/s (~3 MB/s) in total, in both directions combined. When both directions run at once,
Seattle7's outbound flow takes nearly all of it. Seattle5's wg0 showed 0 errors and 23 drops in 3.5 million packets.

### 4.3 Bytes through the Wi-Fi nodes in ddp4431

These are totals from every SENT and LANDED line:

| node | in | out | per step | average over the arm |
|---|---|---|---|---|
| Seattle7 | 1,295 MB | 1,295 MB | 54.0 MB | 2.05 MB/s |
| Seattle3B | 1,295 MB | 1,295 MB | 54.0 MB | 2.05 MB/s |

Per link in ddp4431 (receiver medians):

| link | median rate | median wire time |
|---|---|---|
| Seattle7 ← Seattle5 | 0.67 MB/s | 3.8 s per cell (the slowest) |
| Seattle7 ← New-York-1 / BAMacBook | 4.2–4.3 MB/s | — |
| Seattle7 ← Seattle3B | 1.46 MB/s | — |
| Seattle3B ← Seattle5 | 54 MB/s | 47 ms |

### 4.4 Routing

In ddp4431, Seattle7 reached Seattle5 over wg0 (`dev wg0`, connect 94 ms). In earlier runs that path went over Wi-Fi
(`dev wlan1`, 4.5 ms). Seattle7 ↔ Seattle3B now also goes over wg0, with a 549 ms connect. FrogNet's routing chose
these paths. This report only records them.

## 5. The floor for any DDP algorithm on this mesh

- **The volume is fixed.** Under DDP's arrangement, every node has to send and receive at least 2(W−1)/W × S per step.
  At W=5 and S=16.9 MB, that is ~27 MB each way, ~54 MB in total.
- **gloo and shard both hit it.** gloo's ring and Psychedelic's shard both move exactly that much. The byte counts in
  4.3 confirm it for shard.
- **The time follows.** 54 MB through Seattle7's ~3 MB/s path is a floor of **about 18 s per step**. The measured steps
  are 20.7 s (gloo) and ~26 s (shard), which is within 1.5× of the floor.
- **No reduction algorithm can do better while every node computes on its own data and holds its own model.** Before
  the fix, Psychedelic's 1.32× lead came from gloo also paying for the queue in 4.1.

## 6. Comm hooks on the family link (r12)

The hook arms had kept their September code path. Each bucket was published, then every peer was read in turn by
request and reply, and the hook returned a Future that was already complete.

**The change (r12, 2ca496f).**
- **Naming.** Each bucket is published as its own cell for each step: `h<rank>.hb.<bucket>.<step>`.
- **faithful** holds a read on each peer's family, and each peer's bytes land directly in their slot.
- **fresh_only and every_k** ask only for the peers they actually take, and retire their own cells by age.
- **Overlap.** The average is computed in rank order on a pool thread, which then completes the Future, so DDP carries
  on with backward. `FROGNET_HOOK_STREAM=0` restores the old path.

**Measured.**
- **Loopback** (4 ranks, hidden 1024): faithful went from 459–514 ms/step min to 168–203. The parameters were
  bit-identical. No hook: 99–102.
- **Mesh warmup.** ddp4322 on the old path took 10 m 46 s to warm up. ddp4422 on r12 took 8 m 53 s.

**What faithful measures.**
- faithful moves each rank's whole bucket to every peer, (W−1) × S, against shard's 2(W−1)/W × S. At W=5 that is
  2.5× the bytes, and faithful measured 2.51× and 2.60× slower than no hook.
- Its steps swung between 0.7 and 137 s, which is stall-shaped rather than steady at wire rate. So the byte ratio
  doesn't fully explain it.
- September's faithful ran 2.4–2.8× slower than shard and lost to gloo every time. It is the hook family's control.

## 7. A single machine with shared memory: shmbench

shmbench.py trains the same workload five ways on one machine and checks that each gets the same answer:
- **oracle.** One thread computes W replica gradients in turn, sums them in rank order, and takes one SGD step.
- **gloo.** W processes running DDP over 127.0.0.1.
- **replicas.** DDP's structure in shared memory: W threads, W copies of the parameters, reduce-scatter plus
  all-gather, and W optimizer steps.
- **owner.** One copy of the parameters. Thread i owns 1/W of it. It waits until its slice is present in every
  gradient buffer, sums it in rank order and updates it once. The next forward pass waits on the slices' version
  numbers.
- **batch.** One model on one batch of W × 64 samples, with intra-op threads. The sum over workers happens inside the
  GEMM.

**Seattle5** (4 cores, W=4, hidden 1024, 40 steps):

| arm | median ms/step | vs gloo | heldout | param sha | gradient MB per worker |
|---|---|---|---|---|---|
| gloo | 307.4 | 1.00× | 0.493987 | cc252f76978de27f (ring order) | 25.3 |
| oracle | 295.9 | 1.05× | 0.493987 | 470a9acf9cadaeb8 | — |
| replicas | 264.0 | 1.17× | 0.493987 | 470a9acf9cadaeb8 | 25.3 |
| owner | 221.4 | 1.38× | 0.493987 | 470a9acf9cadaeb8 | 12.7 |
| batch | **116.7** | **2.80×** | 0.493987 | c113b8a36734b357 | 0 |

All checks passed: replicas and owner are bit-identical to the oracle, and batch is within 2.98e-08 per parameter.
In a 2-core container, at W=2, gloo took 48.7 ms/step and batch 22.7 (2.16×).

shmbench runs on one machine, and no other node takes part. In ddpbench every rank's data comes from a seed, so for
this workload a single machine can do all the work.

## 8. The data comes to the compute: meshfeed on the mesh

**meshfeed.py (meshfeed1, 37f813a).** Every node runs it with the same run id.
1. **Data.** Each node reads its own minibatches from its own disk (`/var/cache/frognet/meshfeed/…`). This is
   ddpbench's data for that rank, and no other node has it.
2. **Capability.** Each node times the step the compute node would run and publishes the time in its own cell of
   `meshfeed.<id>`. Nodes on different builds refuse to run together.
3. **Plan.** Rank 0 owns the plan cell, which names the fastest node as the compute node. Every node holds a read on
   it.
4. **Feed.** The other nodes publish their minibatches on their own tensor plane, 4,352 bytes each, read once and then
   retired.
5. **Compute.** The compute node holds a family read on each feeding node and runs one batch of W × 64 per step.
6. **Result.** The compute node writes a result cell, and every node reads and prints it.

**Run mf1** (5 ranks, hidden 1024, 40 measured steps):

| | gloo ddp4430 | shard ddp4431 | **meshfeed mf1** |
|---|---|---|---|
| 40 measured steps | 1,018 s | 1,040 s | **2.9 s** |
| group formed → done | 1,211 s | 1,261 s | **4.0 s** |
| median step | ~20.7 s | ~26 s | **70.3 ms** |
| data into the compute node per step | — | — | 17 KB |
| waiting for data per step | — | — | 0.2 ms |
| held-out | 0.4947 | 0.4947 | **0.4947** |

**What happened in mf1.**
- **Compute node.** BAMacBook, at 64 ms measured.
- **Feed.** The four feeding nodes published their 48 minibatches each (209 KB) in 8–23 ms. Each crossed the WAN in
  3–27 ms (sender `send_ms`).
- **Waits.** Every minibatch had landed before it was asked for.
- **Answer.** The parameter hash (8418a8dc7272e928) is meshfeed's own, because one big batch sums in the GEMM's order
  on BAMacBook's architecture.

## 9. The extended tests: meshfeed2 and meshrun.sh (8ca12d0)

**What meshfeed2 adds:**
- **`--k N`.** The compute set is the N fastest nodes. Each node's minibatches go to one of them, with more going to
  the faster ones by measured step time. Each computes the gradient for its own share in one batch. The set then sums
  those gradients with an all_reduce (Psychedelic shard by default, or gloo), and every member applies the same
  update.
- **`--feed-hz F`.** A node publishes one minibatch every 1/F seconds, reading it from disk at that moment. Every arm
  reads each minibatch from a memory-mapped `.npy` file when it is sent.
- **`--check`** (k=1). After training, the compute node trains the oracle on the same minibatches. It prints both
  held-out losses at full precision, the largest parameter difference, and how long the oracle took.

**Verified on loopback** (4 ranks, hidden 1024):

| test | held-out | check |
|---|---|---|
| k=1, batch 64 | 0.493986964 | oracle 0.493986934 (relative 6.0e-08); largest parameter difference 1.5e-08 |
| k=1, batch 256 | 0.701401949 | oracle 0.701401949; largest parameter difference 1.5e-08 |
| k=2, batch 256 | 0.701401949 | both compute nodes hold the identical model (644671e576e457e8) |
| k=1, batch 512 | 0.994074345 | oracle 0.994074345 |
| k=3, batch 512 | 0.994074345 | all three compute nodes identical (c00002d6ef9ee53c) |
| k=1, batch 64, feed at 10 Hz | — | step median 98.9 ms, set by data arriving every 100 ms |

**The campaign** (`sh meshrun.sh $(getEth0Address) <base>`, run on all five nodes; arm N runs as `mf<base+N>`).
It has not run on the mesh yet:

| arm | what | shows |
|---|---|---|
| 0 | k=1, batch 64, 40 steps, oracle check | ddpbench's workload |
| 1 | k=1, batch 1024, 200 steps, oracle check | 70 KB per node per step, read from disk |
| 2 | k=1, batch 1024, 200 steps, data arriving at 5 Hz | live data |
| 3 | k=1, batch 4096, 10 steps, oracle check | heavy compute on one machine |
| 4 | k=2, batch 4096 | compute shared by two nodes |
| 5 | k=3, batch 4096 | compute shared by three nodes |
| 6 | ddpbench gloo, batch 4096 | control for 3–5 |
| 7 | ddpbench Psychedelic shard, batch 4096 | control for 3–5 |

## 10. What is established, what is not

**Established:**
- With the network configured correctly, gloo and Psychedelic shard tie under DDP's arrangement on this mesh. Both
  sit near the floor set by the bandwidth of Seattle7's path.
- The 1.32× and 4.7× wins from the earlier campaign depended on a misconfigured access-point queue.
- Moving 4 KB minibatches through the tuple space to the fastest node gives ddpbench's answer about 350× faster than
  either DDP backend, on this workload and this mesh.
- The tuple space delivered the data with 0.2 ms of waiting per step. All minibatches were published up front, so
  every one was already waiting when asked for.

**Not established:**
- That splitting the compute across the mesh beats gloo. Arms 3–7 are built to measure that.
- Streaming a dataset too big to publish up front, on the mesh. Arm 2 tests data arriving over time.
- Bit-identical answers across architectures. Rounding differs between ARM and x86, so answers are checked by
  held-out loss and the largest parameter difference.

**Why the current benchmark isn't a fair test of distributed training.**
- Compute is ~0.1% of a step.
- Every rank's data comes from a seed.
- There was no single-machine control.

A fair test needs data that exists only on its node, enough compute per step that one machine can't keep up, and the
best single machine as a control alongside gloo and Psychedelic.

**The direction agreed (13 November).** Treat the mesh as one machine through the tuple space. The dataset is held as
tuples and read by held reads; parameters live in shared memory, each slice owned by one writer. Placement is chosen
from each machine's measured capability, not from where the data happens to be. The tuple space is preferred over NFS
if its performance holds; mf1 says it does at this scale.

## 11. Ribbit-LISP

Branch `ribbit-lisp-perf-20261112`, applying the lessons from the PyTorch work.

**v0.66-registers-in-flight (93d2ae4)**
- **[REGISTERS_IN_FLIGHT_V1].** A sender's strand sends its Map-Registers in order and no longer waits for each
  answer. The session keeps them in order and the RAM host runs them in that order. Each Map-Notify goes back as its
  answer lands.
  - One sender's burst of 300 registers at a 24 ms RAM round trip went from **36.6 to 1,756 registers/s**.
  - A closed-loop sender, one register at a time, is unchanged at 39/s, one round trip each.
- **[MIXED_BUILDS_REFUSED_V1].** A front refuses a RAM host whose LISP region it doesn't speak, naming both versions.
- **[SOCKET_SAYS_WHAT_HELD_IT_V1].** Each session logs TCP_INFO when it ends, on both client and RAM host: rtt, cwnd,
  retransmits, and the busy, rwnd-limited and sndbuf-limited times.
  - First reading, NY2 → streamingfrog: rtt 77–87 ms, 0 retransmits, `sndbuf_limited_ms=0`. The 32 KB send buffer
    held nothing back.
- **New tools.**
  - `tools/bench_register_rtt.py`: closed-loop senders plus an open-loop burst.
  - `tools/delay_line.py`: a delay line where chunks in flight overlap. `shaped_relay.py` waits out each chunk's delay
    before reading the next, so it measured about two round trips per register where there is one.
- Qualification passed every stage.

**v0.67-region-compat (38bfd69).** The build check now accepts RAM hosts listed in `RIBBIT_LISP_REGION_COMPAT`
(v0.65–v0.67). v0.66 changed nothing `lisper-ram` runs, so the droplet needs no reinstall. Tested against a v0.65
`lisper-ram`: the 300-register burst completed in 0.066 s.

**Lessons document:** `ribbit/docs/PERFORMANCE-LESSONS.md`. It records what measured better, what measured worse and
was reverted, and how to measure.

**lispers.net acceptance test.** The test hasn't run yet.
- NY2 built v0.67 (`build PASS`, 405 s on its Pi).
- The first agent attempt used a wrong address for BABox (10.111.11.1). The repo's hookrun lists BABox at
  10.155.155.1. The commands now take the address from `getEth0Address`.
- NY2 has Python 3.11.2. lispers.net 0.643 ships only as Python 3.12 bytecode, so the systems machine needs 3.12.
- The agent node had the package unpacked but not built. The next step is `STAGES=build tools/qualify.sh` there.

## 12. Code delivered

| what | branch, commit | install script |
|---|---|---|
| r12: comm hooks on the family link | revive-september-20261112, 2ca496f | frogtorch-r12-hook-2ca496f.sh |
| fq_codel in system tuning and setup_lillypad | revive-september-20261112, 1ee95cf | frognet-fq_codel-1ee95cf.sh |
| shmbench.py | revive-september-20261112, 6b80c7e | shmbench-6b80c7e.sh |
| meshfeed.py (meshfeed1) | revive-september-20261112, 37f813a | meshfeed-37f813a.sh |
| meshfeed2 + meshrun.sh | revive-september-20261112, 8ca12d0 | meshrun-8ca12d0.sh |
| Ribbit-LISP v0.66 | ribbit-lisp-perf-20261112, 93d2ae4 | ribbit-lisp-v0.66-93d2ae4.sh, ribbit-lisp-v0.66-93d2ae4-package.tgz |
| Ribbit-LISP v0.67 | ribbit-lisp-perf-20261112, 38bfd69 | ribbit-lisp-v0.67-38bfd69-package.tgz |

**[FQ_CODEL_EVERYWHERE_V1] in detail.**
- `frognet-system-tune.sh` writes `net.core.default_qdisc = fq_codel` into `/etc/sysctl.d/99-frognet.conf`, which is
  read early in every boot.
- It also resets every interface still on pfifo_fast, so the kernel reattaches the default, and logs each one plus a
  total.
- `setup_lillypad_v4.bash` now runs the tuning script as Step 12b. Under `--norestart` it leaves it to the installer's
  phase F7.
- The switch itself couldn't run in the build container, whose kernel doesn't support `default_qdisc`. The reset loop
  was tested in a network namespace.

## 13. Open items

- **Run meshrun.sh on the mesh.** Arms 3–7 decide whether splitting the compute pays on this mesh, against gloo and
  shard at batch 4096.
- **Run frognet-system-tune.sh on every node.** This switches the interfaces still on pfifo_fast, including Seattle5's
  eth0, to fq_codel. Do it between arms, not during one.
- **faithful's 0.7–137 s step swings.** These are stall-shaped and not fully explained by its 2.5× volume. The per-cell
  lines from ddp4422 or ddp4432 would show where its time goes.
- **lispers.net acceptance.** Needs a systems machine with Python 3.12, or an agent change to start lispers.net under
  a separately installed `python3.12`. Then build there and rerun.
- **The 26 s shard step against the 18 s floor.** The gap is about 8 s per step, either from the radio sitting idle
  part of each step or from the one-sided sharing `--bidir` shows. It isn't analyzed yet.
- **Native C++ link.** One segfault in 11 runs, cause unknown. It stays off by default.
- **Repository integration.** Another thread is merging the branches into one PR. Everything above lives on
  `revive-september-20261112` and `ribbit-lisp-perf-20261112`.
