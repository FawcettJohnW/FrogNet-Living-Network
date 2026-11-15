# Psychedelic and the FrogNet mesh: results, 12–13 November 2026

This report stands on its own. Every number comes from a run it names, and the run's log is the source. Anything not
measured is labelled as an estimate.

## 1. Summary

**Headline.** On a five-node FrogNet mesh spanning Seattle, New York, Wi-Fi, WireGuard and home Internet links, the
same model was trained to the same held-out loss under stock PyTorch DistributedDataParallel (DDP) and under FrogNet
meshfeed.

| workload | stock DDP (gloo) | stock DDP (Psychedelic shard) | FrogNet meshfeed | speed-up vs gloo | same answer |
|---|---|---|---|---|---|
| batch 64, 40 steps | 1,018 s (ddp4430) | 1,040 s (ddp4431) | **3.09 s** (mf500) | **329×** | held-out 0.4947 on all three; mf500 matches a single-thread oracle to 6.0e-08 |
| batch 4096, 10 steps | 264 s (mf506) | 271 s (mf507) | **48.2 s** (mf503) | **5.5×** | held-out 0.6916 on all three; mf503 matches the oracle exactly |
| batch 4096, 10 steps, compute on 2 nodes | 264 s | 271 s | 100.7 s (mf504) | 2.6× | both compute nodes hold the identical model |
| batch 4096, 10 steps, compute on 3 nodes | 264 s | 271 s | 121.1 s (mf505) | 2.2× | all three compute nodes hold the identical model |

- **Batch 64.** An earlier batch-64 meshfeed run, mf1, took 2.9 s, which is **351×** against the same gloo control.
  It had no oracle check. The batch-64 DDP controls ran earlier on 13 November in a separate session: same mesh, same
  model, with the fq_codel queue fix in place. The batch-4096 controls ran in the same session as the arms they are
  compared with.

**Why.**
- Under DDP every node computes on its own data and then exchanges gradients. Here a gradient is 16.9 MB, so each node
  moves about 54 MB per step. Through the slowest path on this mesh, that sets a floor of about 18 s per step for any
  DDP reduction algorithm.
- A node's minibatch is 4,352 bytes at batch 64, and 278,528 bytes at batch 4096.
- meshfeed moves the data instead of the gradients. Each node keeps its minibatches on its own disk and publishes them
  through FrogNet's tuple space. The machines that measure fastest pull them and do the arithmetic, which is ordinary,
  unmodified PyTorch.

**DDP, gloo against Psychedelic.**
- With Seattle5's access point on a single FIFO queue (pfifo_fast), Psychedelic shard beat gloo 1.32× at bucket view 0
  and 4.7× at bucket view 1.
- With the queue replaced by fq_codel, the two tie: within 2% at batch 64, within 3% at batch 4096.
- Both sit near the bandwidth floor. No reduction algorithm can beat it while DDP's arrangement holds.

**Spreading the compute.**
- Splitting batch-4096 compute over 2 or 3 machines beat DDP, but lost to the one fastest machine.
- The gradient sum among the compute set took 7.3 s (k=2) and 10.2 s (k=3) per step, against a 4.8 s step on one
  machine.
- The planner chooses machines by measured compute speed alone. It doesn't yet measure links.

## 2. The mesh, the workload, how it was measured

### 2.1 Nodes

Five ranks; addresses are each node's eth0 address as `getEth0Address` reports it. Step times are each node's own
capability measurement in meshrun campaign 500 (13 November): one training step of W × batch samples on all cores,
with W = 5.

| rank | node | address | batch 64 | batch 1024 | batch 4096 | links (as used on 13 Nov) |
|---|---|---|---|---|---|---|
| 0 | Seattle5 | 10.250.250.1 | 119 ms | 2,191 ms | 8,694 ms | wired eth0; Wi-Fi access point (wlan0) for Seattle7 and Seattle3B |
| 1 | Seattle7 | 10.170.170.1 | 354 ms | 5,666 ms | 20,581 ms | Wi-Fi client of Seattle5; reaches Seattle5 over wg0 |
| 2 | New-York-1 | 10.123.123.1 | 369 ms | 5,645 ms | 20,009 ms | WAN |
| 3 | BAMacBook | 10.188.188.1 | 63 ms | 1,068 ms | 4,598 ms | WAN |
| 4 | Seattle3B | 10.251.251.1 | 95 ms | 1,460 ms | 6,201 ms | Wi-Fi client of Seattle5 |

The measurements were repeated at the start of every run, and varied by up to 35% between runs. Seattle3B, for
example, measured 6,178–8,362 ms at batch 4096.

September's campaigns, cited in section 3.3, used four ranks: ai-host (x86, on Seattle5's wired LAN at 25–37 MB/s),
Seattle5, Seattle7 and New-York-1.

### 2.2 Workload

ddpbench.py's training, the same in every test in this report:

- **Model.** Linear(16,1024), Tanh, 4 × [Linear(1024,1024), Tanh], Linear(1024,1).
  - 4,216,833 parameters, which is 16.9 MB of float32 gradient.
- **Initialization.** Seed 17, so every replica starts from the same model.
- **Data.** Rank r's data comes from Generator(17×1000 + r).
  - Each minibatch is `batch` samples of 16 inputs.
  - Targets are sin(x·t) + 0.3(x·t), with t from Generator(7).
  - A step's global batch is W × batch samples: every rank's minibatch for that step.
- **Training.** SGD, learning rate 0.02.
  - Warmup: 8 steps (2 at batch 4096).
  - Measured: 40 steps at batch 64, 200 at batch 1024, 10 at batch 4096.
- **Check.** Held-out mean-squared loss on 4,096 samples from Generator(4242), plus a SHA-256 of the final parameters.

### 2.3 Method

- **Timing.** Comparisons use the total time of the measured steps. For DDP that is the elapsed time at the last
  measured step; for meshfeed it is the measured-steps time the leader reports. Medians and minima are reported
  alongside. Warmup and connection setup are excluded from both.
- **Controls.** A control's numbers are read from its own log, never estimated.
- **Same answer.** Checked three ways:
  - Held-out loss, printed to 9 digits by meshfeed and to 4 by ddpbench.
  - Against an oracle (meshfeed `--check`): after training, the compute node trains a reference model on one thread,
    on the same minibatches, summing each replica's gradient in rank order. It reports both held-out losses, their
    relative difference, and the largest difference in any parameter.
  - Across a compute set: every member's parameter hash.
- **Why parameter hashes differ between methods.** Summation order differs, and so does rounding between ARM and x86.
  So the hash is used only within a method; across methods the check is held-out loss and the largest parameter
  difference.
- **Diagnostics** come from the code's own log lines: `[DIAG-STREAM] LINK/SENT/LANDED` with TCP_INFO,
  `[DIAG-PSY] ROUND/STALL` and `[DIAG-MESHFEED] STEP`, plus the kernel's interface and queue counters and iperf3.

### 2.4 Code that ran in campaign 500

meshrun.sh prints the first 12 hex digits of each script's SHA-256 at start. All five nodes printed the same values:

| file | sha256 (first 12) | source |
|---|---|---|
| meshrun.sh | 21b9991bde04 | revive-september-20261112, 8ca12d0 |
| meshfeed.py | e57b38d1a60b | revive-september-20261112, 8ca12d0 (`BUILD = "20261113-meshfeed2"`) |
| ddpbench.py | bc7e10bed0a7 | revive-september-20261112, e235d26f |
| gate.py | f19618e7d238 | revive-september-20261112 |

On ddpbench.py:
- The branch's current ddpbench.py (09388fa5, a8ce80f95f79) adds only per-peer progress lines
  ([SAY_WHERE_THE_TIME_WENT_V1]). Its training and timing are identical to the version that ran.
- ddpbench's DDP is stock `torch.nn.parallel.DistributedDataParallel`. The gloo arm uses PyTorch's own gloo backend;
  the shard arm uses FrogNet's Psychedelic backend with `--reduce shard`.

## 3. DDP campaigns: gloo against Psychedelic

### 3.1 Before and after the queue fix (batch 64, bucket view 0)

| arm | before fq_codel | with fq_codel |
|---|---|---|
| gloo, 40 measured steps | 1,379 s (ddp4420) | **1,018 s** (ddp4430) |
| Psychedelic shard, 40 measured steps | **1,045 s** (ddp4421) | 1,040 s (ddp4431) |
| shard vs gloo | 1.32× ahead | 0.98× (2% behind) |
| gloo first step / warmup | 82.6 s / 6 m 19 s | 31.7 s / 2 m 41 s |
| shard first step / warmup | 28.3 s / 3 m 17 s | 25.4 s / 3 m 15 s |
| held-out, both arms | 0.4947 | 0.4947 |

### 3.2 The whole pre-fix campaign (ddp4420–4429, BAMacBook's view, batch 64)

| arm | bucket view 0: 40 steps | held-out | bucket view 1: 40 steps | held-out |
|---|---|---|---|---|
| gloo | 1,379 s | 0.4947 | 5,465 s | 0.4947 |
| Psychedelic shard | **1,045 s** | 0.4947 | **1,170 s** | 0.4947 |
| hook faithful | 2,623 s | 0.4947 | 3,041 s | 0.4947 |
| hook fresh_only | 1,275 s | 0.5006 | 1,159 s | 0.5008 |
| hook every_k | 701 s | 0.5004 | 659 s | 0.5004 |

- **The skipping hooks get a different answer.** fresh_only and every_k reach held-out 0.5004–0.5008 against 0.4947,
  because they skip peers' contributions.
- **Their min ms/step means nothing.** A rank that falls behind finds its peers' cells already landed, so it reports
  48–738 ms steps while its elapsed time tells the real story.

### 3.3 Batch 4096, with fq_codel (meshrun campaign 500, arms 6 and 7)

Per rank. ddpbench times its own steps; each rank's total is its elapsed time at measured step 10.

| arm | rank's view | 10 measured steps | median step | best step | first step | held-out | param sha |
|---|---|---|---|---|---|---|---|
| gloo (mf506) | BAMacBook | 264 s | 25.3 s | 19.5 s | 22.7 s | 0.6916 | ffbe252538db53b1 |
| | Seattle5 | 260 s | 24.8 s | 20.8 s | 27.5 s | | |
| | Seattle7 | 259 s | 24.4 s | 20.7 s | 24.4 s | | |
| shard (mf507) | BAMacBook | 271 s | 27.0 s | 21.3 s | 32.1 s | 0.6916 | bfc5168d26edd225 |
| | Seattle5 | 271 s | 27.4 s | 22.7 s | 22.0 s | | |
| | Seattle7 | 269 s | 26.4 s | 23.3 s | 31.4 s | | |

Shard came in 3% behind gloo. Individual steps swung between 19.5 and 34.5 s in both arms.

### 3.4 The September results, for reference

Four ranks: ai-host, Seattle5, Seattle7, New-York-1. Times are median seconds per step, batch 64.

| campaign | bucket view | gloo | shard | shard vs gloo |
|---|---|---|---|---|
| ddp570x | off | 29.8 | 15.3 | 1.95× |
| ddp580x | off | 22.0 | 13.4 | 1.64× |
| ddp588x | on | 13.4 | 13.0 | 1.03× |
| ddp1500x | on | 22.7 | 12.9 | 1.75× |

- **Spread.** Shard held at 12.3–15.3 s across five campaigns, while gloo swung between 13.1 and 62 s.
- **The September report's explanation.** gloo's ring makes every rank wait on the slowest hop; a shard rank pays only
  for its own reads.
- **faithful** lost to gloo in every September campaign (0.40–0.82×). The report calls it the hook family's control,
  not a contender.

## 4. Network findings

### 4.1 The queue on Seattle5's access point

Seattle7 and Seattle3B both join Seattle5's wlan0 as Wi-Fi clients. Interface counters, read after the pre-fix
campaign:

| host / interface | role | queue discipline | sent | drops |
|---|---|---|---|---|
| Seattle5 wlan0 | access point for Seattle7 and Seattle3B | **pfifo_fast**, 1,000 packets | 34.0 GB | **9,364** |
| Seattle5 eth0 | wired | pfifo_fast | 23.4 GB | 0 |
| Seattle7 wlan1 | client, also carries wg0 | fq_codel | 24.4 GB | 0 |
| Seattle3B wlan1 | client | **pfifo_fast** | 13.3 GB | 0 |

**What the link showed while the queue was there.** The Seattle7 ↔ Seattle3B link is a 3 ms path, yet it measured only
0.06–0.25 MB/s.
- Its sender's TCP_INFO showed rtt 787–1,676 ms and cwnd up to 2,415 segments.
- Only 14–421 of its own segments were unacknowledged at the time.
- So the flow was waiting behind every other flow in a shared queue more than a second deep.

**The fix.** fq_codel gives each flow its own queue and holds queueing delay near 5 ms.

| | |
|---|---|
| applied by hand | `tc qdisc replace dev wlan0 root fq_codel` on Seattle5; `… dev wlan1 …` on Seattle3B |
| made permanent | `net.core.default_qdisc=fq_codel` in `/etc/sysctl.d/90-frognet-fq_codel.conf` on both |
| in the tree | [FQ_CODEL_EVERYWHERE_V1], section 11 |

Two minutes after the change, Seattle5's wlan0 had passed 160 MB with 0 drops and 0 backlog.

### 4.2 The ceiling on Seattle7's path

iperf3 between Seattle7 and Seattle5, over the route then in use (`10.170.170.1 dev wg0`):

| test | Seattle7 → Seattle5 | Seattle5 → Seattle7 | combined |
|---|---|---|---|
| `-R`, one direction | — | 22.6–23.0 Mb/s | 23.0 Mb/s |
| `--bidir` | 23.0 Mb/s | 2.5 Mb/s, 59 retransmits | **25.5 Mb/s** |

- **Capacity.** The path carries about 23–25 Mb/s (~3 MB/s) in total, both directions combined.
- **Sharing.** With both directions running at once, Seattle7's outbound flow takes nearly all of it.
- **Errors.** Seattle5's wg0 showed 0 errors and 23 drops in 3.5 million packets.

### 4.3 Bytes through the Wi-Fi nodes (ddp4431, shard, batch 64)

These are totals from every SENT and LANDED line:

| node | in | out | per step | average over the arm |
|---|---|---|---|---|
| Seattle7 | 1,295 MB | 1,295 MB | 54.0 MB | 2.05 MB/s |
| Seattle3B | 1,295 MB | 1,295 MB | 54.0 MB | 2.05 MB/s |

Per link (receiver medians):

| link | median rate | median wire time per cell |
|---|---|---|
| Seattle7 ← Seattle5 | 0.67 MB/s | 3.8 s (the slowest) |
| Seattle7 ← New-York-1 / BAMacBook | 4.2–4.3 MB/s | — |
| Seattle7 ← Seattle3B | 1.46 MB/s | — |
| Seattle3B ← Seattle5 | 54 MB/s | 47 ms |

### 4.4 Routing

FrogNet's routing chose these paths; this report only records them.
- **Seattle7 → Seattle5.** In ddp4431 the path went over wg0 (`dev wg0`, connect 94 ms). In earlier runs it went over
  Wi-Fi (`dev wlan1`, 4.5 ms).
- **Seattle7 ↔ Seattle3B.** Also over wg0, with a 549 ms connect.

## 5. The floor for any DDP algorithm on this mesh

- **The volume is fixed.** Under DDP's arrangement each node must send and receive at least 2(W−1)/W × S per step. At
  W = 5 and S = 16.9 MB, that is ~27 MB each way, ~54 MB in total.
  - gloo's ring and Psychedelic's shard are both bandwidth-optimal, so both move exactly that much.
  - The byte counts in 4.3 confirm it for shard.
- **The time follows.** 54 MB through Seattle7's ~3 MB/s path is a floor of **about 18 s per step**, before any
  compute.
  - **Batch 64.** Measured steps are ~20.7 s (gloo) and ~26 s (shard), within 1.5× of the floor.
  - **Batch 4096.** Each rank also computes its own 4,096-sample gradient. Estimated from the capability table, that is
    about 4 s on Seattle7 and New-York-1. Measured medians are 24.4–27.4 s.
- **No reduction algorithm can do better while every node computes on its own data and holds its own model.**
  Psychedelic's pre-fix 1.32× lead came from gloo also paying for the queue in 4.1.

## 6. Comm hooks on the family link (r12)

**Before r12.** The hook arms had kept their September code path. Each bucket was published, then every peer was read
in turn by request and reply, and the hook returned a Future that was already complete.

**The change (r12, 2ca496f, `BUILD 20261112-september-family-link-r12-hook`).** [HOOK_ON_THE_FAMILY_LINK_V1]:
- **Naming.** Each bucket is published as its own cell for each step: `h<rank>.hb.<bucket>.<step>`.
- **faithful** holds a read on each peer's family, and each peer's bytes land directly in their slot.
- **fresh_only and every_k** ask only for the peers they actually take. Their own cells are retired by age
  (`HOOK_KEEP_STEPS`).
- **Overlap.** The average is computed in rank order on a pool thread, which then completes the Future, so DDP carries
  on with backward.
- `FROGNET_HOOK_STREAM=0` restores the old path.

**Measured.**
- **Loopback** (4 ranks, hidden 1024). faithful went from a 459–514 ms/step minimum to 168–203, with bit-identical
  parameters. No hook: 99–102.
- **Mesh warmup.** ddp4322 on the old path took 10 m 46 s to warm up; ddp4422 on r12 took 8 m 53 s.

**What faithful measures.**
- **Its volume.** faithful moves each rank's whole bucket to every peer, (W−1) × S, against shard's 2(W−1)/W × S. At
  W = 5 that is 2.5× the bytes, and it measured 2.51× and 2.60× slower than no hook.
- **Its stalls.** Its steps swung between 0.7 and 137 s, which is stall-shaped, not steady at wire rate. The byte ratio
  doesn't fully explain it (section 14).

## 7. A single machine with shared memory: shmbench

shmbench.py (6b80c7e) trains the same workload five ways on one machine and checks that each gets the same answer:

- **oracle.** One thread computes W replica gradients in turn, sums them in rank order, and takes one SGD step.
- **gloo.** W processes running DDP over 127.0.0.1.
- **replicas.** DDP's structure in shared memory: W threads, W copies of the parameters, reduce-scatter plus
  all-gather, and W optimizer steps.
- **owner.** One copy of the parameters, of which thread i owns 1/W.
  - Each thread waits until its slice is present in every gradient buffer, sums it in rank order and updates it once.
  - The next forward pass waits on the slices' version numbers.
- **batch.** One model on one batch of W × 64 samples, with intra-op threads. The sum over workers happens inside the
  GEMM.

**Seattle5** (4 cores, W = 4, hidden 1024, 40 steps):

| arm | median ms/step | vs gloo | held-out | param sha | gradient MB per worker |
|---|---|---|---|---|---|
| gloo | 307.4 | 1.00× | 0.493987 | cc252f76978de27f (ring order) | 25.3 |
| oracle | 295.9 | 1.05× | 0.493987 | 470a9acf9cadaeb8 | — |
| replicas | 264.0 | 1.17× | 0.493987 | 470a9acf9cadaeb8 | 25.3 |
| owner | 221.4 | 1.38× | 0.493987 | 470a9acf9cadaeb8 | 12.7 |
| batch | **116.7** | **2.80×** | 0.493987 | c113b8a36734b357 | 0 |

- **Checks.** All passed: replicas and owner are bit-identical to the oracle, and batch is within 2.98e-08 per
  parameter.
- **A smaller machine.** In a 2-core container at W = 2, gloo took 48.7 ms/step and batch 22.7 (2.16×).

**The lesson.** The sum of W gradients equals the gradient of one batch of W minibatches. Computing that batch on one
model removes the reduction entirely. meshfeed (section 8) applies this across the mesh.

## 8. meshfeed: the data comes to the compute

### 8.1 How it works

meshfeed.py runs on every node with the same run id. Everything between nodes goes through FrogNet's tuple space, as
cells and held reads.

1. **Data.** Each node writes its own minibatches to its own disk, as a memory-mapped `.npy` at
   `/var/cache/frognet/meshfeed/r<R>-s<seed>-b<batch>-n<N>.npy` with shape [N, batch, 17] (16 inputs plus the
   target).
   - The file holds ddpbench's data for that rank, and no other node has it.
   - A minibatch is read from the file at the moment it is sent.
2. **Capability.** Each node times one step of W × batch on all its cores. It publishes that time and its tensor-plane
   port as its row in the run's rendezvous family.
   - The leader collects the rows with one held read for W rows: no polling.
   - Nodes on different builds refuse to run together.
3. **Plan.** Rank 0 writes the plan cell:
   - the compute set: the k fastest measured;
   - which feeders each compute node takes, more of them going to the faster nodes;
   - why it chose them.
   Every node holds a read on it.
4. **Feed.** Each node publishes its minibatches on its own tensor plane as cells `mf<R>.mb.<t>`, with readers = 1, so
   each is read once and then retired.
   - With `--feed-hz F`, it publishes one every 1/F seconds.
5. **Compute.**
   - **k = 1.** The compute node holds a family read on each of its feeders. For each step it assembles every
     minibatch into one batch and takes one SGD step.
   - **k > 1.** Each compute node computes the gradient of its own share of the step's minibatches in one batch, and
     scales it by its share.
     - The set then sums the gradients with an all_reduce: Psychedelic shard by default, or gloo.
     - Every member divides by W and applies the same update.
     - The process group is `frognet://mf<runid>`.
6. **Result.** Each compute node writes its parameter hash. The leader writes the result cell, which every node holds a
   read on and prints.
   - The result records timing, the breakdown (waiting for data / computing / reducing), held-out loss, agreement
     across the compute set, and the oracle check.

**The check (`--check`, k = 1).** After training, the compute node trains the oracle on the same minibatches, as
described in 2.3.

### 8.2 Verified on loopback (4 ranks, hidden 1024)

| test | held-out | check |
|---|---|---|
| k=1, batch 64 | 0.493986964 | oracle 0.493986934 (relative 6.0e-08), largest parameter difference 1.5e-08 |
| k=1, batch 256 | 0.701401949 | oracle 0.701401949, largest parameter difference 1.5e-08 |
| k=2, batch 256 | 0.701401949 | both compute nodes identical (644671e576e457e8) |
| k=1, batch 512 | 0.994074345 | oracle 0.994074345 |
| k=3, batch 512 | 0.994074345 | all three compute nodes identical (c00002d6ef9ee53c) |
| k=1, batch 64, feed at 10 Hz | — | median step 98.9 ms, set by data arriving every 100 ms |

### 8.3 First mesh run: mf1 (meshfeed1, 37f813a, 5 ranks, batch 64)

| | gloo ddp4430 | shard ddp4431 | **meshfeed mf1** |
|---|---|---|---|
| 40 measured steps | 1,018 s | 1,040 s | **2.9 s** |
| group formed → done | 1,211 s | 1,261 s | **4.0 s** |
| median step | ~20.7 s | ~26 s | **70.3 ms** |
| data into the compute node per step | — | — | 17 KB |
| waiting for data per step | — | — | 0.2 ms |
| held-out | 0.4947 | 0.4947 | **0.4947** |

- **Placement.** The compute node was BAMacBook.
- **Feed.** The four feeders published their 48 minibatches each (209 KB) in 8–23 ms, and each minibatch crossed the
  WAN in 3–27 ms.
- **Arrival.** Every minibatch had landed before it was asked for.

## 9. The extended campaign: meshrun 500 (13 November)

`sh meshrun.sh $(/usr/local/bin/getEth0Address) 500` was run on all five nodes. Arm N runs as `mf<500+N>`. Arms 6–7
run ddpbench behind a gate.py barrier, so every rank starts together.

**Clock.** BAMacBook's clock is Eastern, the Seattle nodes' Pacific. Arm 0 started at 06:33:31 Pacific, and arm 7
ended at 07:10:52 Pacific.

**Every node's log agrees.** BAMacBook, Seattle5 and Seattle7 were checked line by line. Each meshfeed result line,
including the hash, is the same on every node, because every node prints the leader's result cell.

### 9.1 Results

| arm | run | what ran | measured steps | total | min / median / max step | waiting for data | computing | reducing | held-out | check |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | mf500 | k=1, batch 64 | 40 | **3.09 s** | 68.2 / 69.6 / 157.7 ms | 0.3 ms | 71.3 ms | 0 | 0.494662166 | oracle 0.494662195 (rel 6.02e-08); max \|Δp\| 2.98e-08 |
| 1 | mf501 | k=1, batch 1024 | 200 | **246.1 s** | 1,081 / 1,211 / 1,687 ms | 0.4 ms | 1,217 ms | 0 | 0.491882801 | oracle 0.491882801 (rel 0); max \|Δp\| 1.86e-08 |
| 2 | mf502 | k=1, batch 1024, feed 5 Hz | 200 | 234.7 s | 1,041 / 1,137 / 2,421 ms | 0.4 ms | 1,167 ms | 0 | 0.491882801 | same param sha as mf501 |
| 3 | mf503 | k=1, batch 4096 | 10 | **48.2 s** | 4,532 / 4,918 / 4,969 ms | 2.2 ms | 4,810 ms | 0 | 0.691565514 | oracle 0.691565514 (rel 0); max \|Δp\| 1.49e-08 |
| 4 | mf504 | k=2, batch 4096 | 10 | 100.7 s | 7,739 / 10,184 / 12,395 ms | 0.6 ms | 2,729 ms | 7,337 ms | 0.691565514 | both compute nodes 1b3bfcb197e4b833 |
| 5 | mf505 | k=3, batch 4096 | 10 | 121.1 s | 9,173 / 11,682 / 15,485 ms | 0.4 ms | 1,880 ms | 10,222 ms | 0.691565514 | all three 981b751d5b3bb70d |
| 6 | mf506 | ddpbench gloo, batch 4096 | 10 | 259–264 s | best 19.5–20.8 s, median 24.4–25.3 s | — | — | — | 0.6916 | ffbe252538db53b1 |
| 7 | mf507 | ddpbench shard, batch 4096 | 10 | 269–271 s | best 21.3–23.3 s, median 26.4–27.4 s | — | — | — | 0.6916 | bfc5168d26edd225 |

The computing and reducing figures are per step, averaged over the measured steps. The DDP ranges are across the
three ranks' views (3.3).

**Final parameter hashes** for the k = 1 runs:
- mf500 8418a8dc7272e928; its oracle 8a32fb696eae720d.
- mf501 abff80d6b36caab6 (mf502 identical); its oracle 15b32e04db0b386c.
- mf503 5b92ec0e0afa654a; its oracle 602ad813f7f3978e.

**Oracle cost** on one thread: 7.8 s for 48 steps at batch 64, 342.4 s for 208 steps at batch 1024, and 86.4 s for 12
steps at batch 4096.

### 9.2 Placement chosen

From each node's log:

| run | compute set | who fed whom |
|---|---|---|
| mf500–mf503 | BAMacBook (63 / 1,068 / 1,098 / 4,598 ms) | BAMacBook ← all five |
| mf504 | BAMacBook, Seattle3B | BAMacBook ← Seattle5, New-York-1, BAMacBook; Seattle3B ← Seattle7, Seattle3B |
| mf505 | Seattle5, BAMacBook, Seattle3B | Seattle5 ← Seattle5; BAMacBook ← Seattle7, New-York-1, BAMacBook; Seattle3B ← Seattle3B |

### 9.3 Comparisons, side by side

| workload | stock DDP gloo | stock DDP shard | meshfeed | meshfeed vs gloo | meshfeed vs shard |
|---|---|---|---|---|---|
| batch 64, 40 steps | 1,018 s (ddp4430) | 1,040 s (ddp4431) | 3.09 s (mf500) | **329×** | **337×** |
| batch 64, 40 steps (mf1) | 1,018 s | 1,040 s | 2.9 s (mf1) | 351× | 359× |
| batch 1024, 200 steps | not run | not run | 246.1 s (mf501) | — | — |
| batch 4096, 10 steps, k=1 | 264 s (mf506) | 271 s (mf507) | 48.2 s (mf503) | **5.5×** | **5.6×** |
| batch 4096, 10 steps, k=2 | 264 s | 271 s | 100.7 s (mf504) | 2.6× | 2.7× |
| batch 4096, 10 steps, k=3 | 264 s | 271 s | 121.1 s (mf505) | 2.2× | 2.2× |

**The batch-64 controls are from a separate session.** ddp4430 and ddp4431 ran earlier on 13 November on the same mesh
and model, with fq_codel in place. They are not part of campaign 500.

**Batch 1024 has no DDP control.** As an estimate: DDP's floor of ~18 s per step for the gradients, plus about 1.1 s of
compute on the slowest ranks, puts it near 19–25 s per step against meshfeed's 1.21 s. That is roughly 16–21×. Not
measured.

### 9.4 What the campaign shows

1. **The same answer.**
   - Every k = 1 run matches the single-thread oracle: exactly at batch 1024 and 4096, and to 6e-08 at batch 64. The
     largest parameter difference in any run was 3e-08.
   - Every compute set ended with an identical model on every member.
   - At batch 4096 all five methods (k = 1, 2, 3, gloo, shard) reach held-out loss 0.6916.
2. **The data was never what slowed a step.**
   - Waiting for minibatches was 0.3–2.2 ms per step, out of 70 ms to 11.7 s.
   - The minibatches were read from each owner's disk when sent, and crossed the WAN to the compute node.
3. **Live feed (mf502).**
   - The feeders paced publishing at 5 Hz: Seattle5 and Seattle7 each took 41.4 s to publish 208 minibatches.
   - The compute node consumed them at about 0.85 steps/s, so data still arrived faster than it was used.
   - The run is valid, and its answer is bit-identical to mf501. But it shows live data keeping ahead of compute, not a
     data-bound run. Arm 2 changes to `--feed-hz 0.5` for that case.
4. **Spreading the compute did split the compute.**
   - Computing per step fell from 4.81 s (k=1) to 2.73 s (k=2) and 1.88 s (k=3).
   - But the gradient sum among the set took 7.34 s (k=2) and 10.22 s (k=3), so both lost to k = 1.
   - The planner ranks machines by compute alone. It put Seattle3B, a Wi-Fi client, in a set with BAMacBook, which
     reaches it across the WAN.
5. **Link-aware placement.**
   - Seattle5 and Seattle3B share a 54 MB/s link (4.3). There, the 16.9 MB sum would take about 0.3 s.
   - As an estimate, with Seattle5 taking 2 minibatches (~3.5 s) and Seattle3B 3 (~3.7 s), a step would take about
     4 s, against BAMacBook alone at 4.92 s. That is a small gain at best on this mesh, because BAMacBook is about
     twice as fast as either. Not measured.

## 10. Adaptivity: what is built, what is not

**Built and measured.**
- **Placement is measured, not configured.** Each run times every node and plans from the times. Across campaign 500
  the plan changed with the workload without any change to configuration: mf505 added Seattle5 when asked for three.
- **Any number of compute nodes.** `--k N`, with shares weighted by measured speed. k = 1, 2 and 3 were run.
- **Data stays with its owner.** Each node's minibatches stay on its own disk until they are read for sending.
- **Mixed builds refuse to run together.** Each node publishes its BUILD, and the leader refuses a group whose builds
  differ.

**Not built.**
- **Replanning during a run.** The plan is fixed at the start of a run. If a node drops out mid-run, its held reads go
  unanswered and the run stops.
- **Link rates in the plan.** Capability cells carry step time and tensor-plane port, not link rates.

**The design for live adaptation, on FrogNet's existing mechanisms.**
- **The planner as a FrogNet role.** One active holder, elected on capability. On split and merge it reasserts itself,
  as roles already do.
- **Membership.** A node's departure shows up as its permanent socket closing, and is published as a membership cell.
  The planner holds a read on that family.
- **Plan epochs.** Each plan carries an epoch, and a step runs under exactly one epoch. A step cut short by a departure
  is rerun under the next epoch, which reassigns the departed node's data.
- **Parameters in the role's RAM host.** They are held as cells in a family that outlives any one session, so a new
  compute set resumes from the last completed step.

## 11. Code delivered

| what | branch, commit | install script |
|---|---|---|
| r12: comm hooks on the family link | revive-september-20261112, 2ca496f | frogtorch-r12-hook-2ca496f.sh |
| fq_codel in system tuning and setup_lillypad | revive-september-20261112, 1ee95cf | frognet-fq_codel-1ee95cf.sh |
| shmbench.py | revive-september-20261112, 6b80c7e | shmbench-6b80c7e.sh |
| meshfeed.py (meshfeed1) | revive-september-20261112, 37f813a | meshfeed-37f813a.sh |
| meshfeed2 + meshrun.sh | revive-september-20261112, 8ca12d0 | meshrun-8ca12d0.sh |
| Ribbit-LISP v0.66 | ribbit-lisp-perf-20261112, 93d2ae4 | ribbit-lisp-v0.66-93d2ae4.sh, ribbit-lisp-v0.66-93d2ae4-package.tgz |
| Ribbit-LISP v0.67 | ribbit-lisp-perf-20261112, 38bfd69 | ribbit-lisp-v0.67-38bfd69-package.tgz |

**meshfeed.py options:** `--rank --world --runid --addr --tree --hidden --layers --batch --seed --lr --steps --warmup
--compute --data-dir --timeout --k --reduce-backend --feed-hz --check --peer-wait`.

**[FQ_CODEL_EVERYWHERE_V1].**
- **Default discipline.** `frognet-system-tune.sh` writes `net.core.default_qdisc = fq_codel` into
  `/etc/sysctl.d/99-frognet.conf`.
- **Existing interfaces.** It resets every interface still on pfifo_fast, so the kernel reattaches the new default,
  and logs each one and a count.
- **Install.** `setup_lillypad_v4.bash` runs it as Step 12b; under `--norestart` it is left to the installer's
  phase F7.
- **Testing.** The reset loop was tested in a network namespace. The build container's kernel lacks
  `default_qdisc`.

## 12. Ribbit-LISP

Branch `ribbit-lisp-perf-20261112` applies the lessons from the PyTorch work.

**v0.66-registers-in-flight (93d2ae4).**
- **[REGISTERS_IN_FLIGHT_V1].** A sender's strand sends its Map-Registers in order without waiting for each answer.
  The RAM host runs them in that order, and each Map-Notify returns as its answer lands.
  - **Burst.** One sender's burst of 300 registers at a 24 ms RAM round trip went from **36.6 to 1,756
    registers/s**.
  - **Closed loop.** A sender doing one register at a time is unchanged at 39/s, one round trip each.
- **[MIXED_BUILDS_REFUSED_V1].** A front refuses a RAM host whose LISP region it doesn't speak, and names both
  versions.
- **[SOCKET_SAYS_WHAT_HELD_IT_V1].** Each session logs TCP_INFO when it ends, on both client and RAM host: rtt, cwnd,
  retransmits, and busy, rwnd-limited and sndbuf-limited times.
  - First reading, NY2 → streamingfrog: rtt 77–87 ms, 0 retransmits, `sndbuf_limited_ms=0`.
- **Tools.**
  - `tools/bench_register_rtt.py`: closed-loop senders and an open-loop burst.
  - `tools/delay_line.py`: a delay line where chunks in flight overlap. The older `shaped_relay.py` serialized chunks
    and so measured about two round trips per register.
- **Qualification** passed every stage.

**v0.67-region-compat (38bfd69).**
- **The change.** `RIBBIT_LISP_REGION_COMPAT` accepts v0.65–v0.67 RAM hosts. v0.66 changed nothing `lisper-ram` runs,
  so the droplet needs no reinstall.
- **Tested** against a v0.65 `lisper-ram`: the 300-register burst completed in 0.066 s.

**Lessons:** `ribbit/docs/PERFORMANCE-LESSONS.md`.

**lispers.net acceptance has not run yet.**
- NY2 built v0.67 (`build PASS`, 405 s).
- BABox is at 10.155.155.1. Commands now take the address from `getEth0Address`.
- lispers.net 0.643 ships as Python 3.12 bytecode, and NY2 has 3.11.2.

## 13. Reproducing these results

**Install.** On each node, as root, install meshrun-8ca12d0.sh, which installs meshfeed.py, meshrun.sh, ddpbench.py
and gate.py under `/usr/local/frognet-ai`. Check the printed hashes against 2.4.

**Run.** On all five nodes, at about the same time:
```
cd /usr/local/frognet-ai && sh meshrun.sh $(/usr/local/bin/getEth0Address) <base>
```
meshrun.sh maps the five addresses in 2.1 to their ranks and refuses any other address. To run on other machines, edit
that map and `WORLD`. The tuple space is reached at `databasehost.frognet:80`.

**Logs.** Each run's log is at `/tmp/nb3/mf<id>.rank<R>.log`, and every meshfeed result line carries the final
parameter hash and the oracle check.

**What to compare:**
- held-out loss across methods;
- meshfeed's oracle check;
- the hash agreement within a compute set;
- total measured time against the gloo and shard controls run in the same campaign.

## 14. Open items

- **Matched controls at every size.** Add gloo and shard at batch 64 and 1024 to meshrun, so every ratio has a control
  from the same campaign. Also add a single-machine control: the compute node training on locally generated data for
  all five ranks.
- **A collector.** It should rebuild every table here from all ranks' logs and write a SHA-256 manifest of the logs
  and scripts.
- **A standalone kit.** frogtorch_ram, tensor_plane and meshfeed, runnable without a FrogNet install, with a README.
- **meshrun changes.**
  - Arm 2 to `--feed-hz 0.5`.
  - `--compute-set` to name a set explicitly, plus arm 8: Seattle5 and Seattle3B.
  - Progress lines from the oracle.
- **A link-aware planner.** Measure link rates into the capability cells, and let a cost model choose k and the set.
- **Live adaptation** (section 10). Then test it by dropping a node mid-run.
- **Run frognet-system-tune.sh on every node.** This switches interfaces still on pfifo_fast, including Seattle5's
  eth0. Do it between arms, not during one.
- **faithful's 0.7–137 s step swings.** The per-cell lines from ddp4422 would show where its time goes.
- **The gap above the DDP floor.** The shard step is 26 s at batch 64 against an 18 s floor. This is not analyzed.
- **lispers.net acceptance.** Needs a systems machine with Python 3.12, or an agent change to start lispers.net under
  a separately installed `python3.12`.
- **Native C++ link.** One segfault in 11 runs, cause unknown. It stays off by default.
