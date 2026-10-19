# Study: one keyspace -- what both trees do, and what goes [ONE_KEYSPACE_V1]

2026-10-19. John: "there should only be one. The point between them is that all pub/sub disappears in a memory
model, so that whole class of code goes away."

## What each tree is

| | main (7651d9c) | branch before this (ONE_ORDER_V1, 2026-10-17) |
|---|---|---|
| where the keyspace is | once, in the RAM host's Redis region | a full copy in every box (`g.mem`), plus the intents in the memory |
| who runs a command | the RAM host (`op=redis`, a pipeline per request, parallel per session) | every box runs every command (one applier per box) |
| how the copies agree | -- | every box follows every box's intent ring (`(redis, cmd, *)`), paced by `(redis, applied, *)` heartbeats, joins late by a snapshot copy (`snap.*`, `snapdone`) |
| reads | answered at the front from a coherent read cache, kept true by per-front invalidation feeds (`coherence.cpp`, `[READS_AT_THE_FRONT_V1]`) | from the box's own copy |
| contested read-modify-write | decided in the memory at the cell (`add`, `replace_if`, `update`, `take_variable`: `[THE_MEMORY_DECIDES_V1]`) | one order: every copy applies the same intents in write-order id |
| blocked commands | parked on a RAM host thread (`op=redis_block`) | parked in the order on every box |
| platform | vendored v0.63 + 40 patch scripts | ribbit/cpp, linked as is |

Both carry a distribution layer -- feeds, invalidations, rings, heartbeats, pacing, snapshot joins -- whose only job is
to keep more than one copy of something true. That is the pub/sub class of code. With one keyspace in one memory there
is nothing to keep true: nobody holds a second copy.

## The shape (decided from his rulings)

- **One keyspace, in the memory.** The Redis region lives once (the RAM host, or a box acting as its own RAM).
- **Each client request is the RAM call** (2026-10-18): the front frames the client's pipeline and makes the call on
  its session -- through the session's sender, never a thread putting frames on the wire (2026-10-19).
- **The memory runs each request as a parallel job** (2026-10-18, 2026-10-14: a pool, never a thread per request); the
  only data serialisation is at the tuple (2026-10-18): main's memory-decided operations, unchanged.
- **The region's API is Redis's** (2026-09-22, 2026-10-14: the vendor's API, customised for performance).
- **Fronts hold nothing** (one RAM rule, 2026-10-19; best practices 3a "cache nothing yourself"): no read cache, no
  invalidation feed, no copy, no intents, no heartbeats, no join.
- **Waiting is a held read in the memory**, woken by the write that satisfies it (Ribbit invariant 4), not a parked
  request thread: BLPOP & co., XREAD BLOCK, WAIT-free.
- **Redis pub/sub is the memory's own write and held read**: PUBLISH writes the message once; a subscribed connection
  is a held read on the channel's family (and on the pattern's). No delivery code.

## What is deleted

From the branch: the intent rings and their writer/pacer, the applier, `applied` heartbeats, the gap checks, the join
by copy (`snap.*`, `snapdone`, `dump`/`load` for joins), the `o/p/g/u/x/j/P` intent kinds, the same-clock and
same-seed machinery that only existed so N copies agree.
From main: `coherence.{hpp,cpp}`, the read-at-the-front cache and its feeds, `op=redis_block`'s parked threads,
`platform/` v0.63 and the patch scripts, `door/`.

## Baseline, before (lizard 1.24.1, cyclomatic complexity; measured 2026-10-19)

    branch: distribution (front, apply, ram)     nloc    883  functions   67  CCN total   388  max  34  over-10  13
    main: distribution (front, region, coherence) nloc   1185  functions  111  CCN total   587  max  35  over-10  11
    branch: all src                              nloc   8168  functions  831  CCN total  4749  max 105  over-10 113
    main: all src                                nloc   8394  functions  863  CCN total  4910  max 105  over-10 111
    command code (branch cmd_*, db)              nloc   4369  functions  457  CCN total  3178  max 105  over-10  80

Main also carries `platform/` (v0.63, vendored) and the `tools/apply_*.py` patch scripts; not counted above.

Stock, same tool, Redis 7.2.11 from github.com/redis/redis tag 7.2.11:

    stock Redis 7.2.11 src/ (no deps/)           nloc  95870  functions 4113  CCN total 24662  max 208  over-10 525
    stock Redis deps/ (lua, hiredis, ...)        nloc  79576  functions 4522  CCN total 14379  max 319  over-10 165

## Results, v33-one-keyspace (2026-10-19, 2-core x86 sandbox; everything on one machine)

### Contract
- Redis 7.2.11's own suite, the ten core files, stock vs a front: identical to the copy design file by file --
  incr 31/32, string 79/82, hash 70/73, set 114/119, list 261/274, zset 316/320, keyspace 56/56, stream 60/75,
  stream-cgroups 56/66, multi 35/63 (every red in the classes of SUPPORTED-CONTRACT.md).
- `run_concurrency.py` 39/39 from three fronts; `run_two_fronts.py` 15/15; `run_same_after_raw.py` PASS.

### Throughput, redis-benchmark (tools/bench_boxes.sh: medians of 3, interleaved with stock in the same run;
### ratio to that run's stock -- stock itself moved between the two runs, so compare the ratios)

| shape | cmd | copy design, 1 box | one keyspace, 1 front | copy design, 2 boxes | one keyspace, 2 fronts |
|---|---|---|---|---|---|
| -c 50 -P 16 | get | 0.36 | 0.27 | 0.33 | 0.27 |
| -c 50 -P 16 | incr | 0.37 | 0.28 | 0.29 | 0.29 |
| -c 50 -P 16 | lpush | 0.32 | 0.22 | 0.25 | 0.24 |
| -c 50 -P 16 | set | 0.37 | 0.24 | 0.29 | 0.30 |
| -c 50 -P 1 | get | 0.50 | 0.42 | 0.50 | 0.41 |
| -c 50 -P 1 | incr | 0.40 | 0.31 | 0.41 | 0.32 |
| -c 50 -P 1 | lpush | 0.49 | 0.35 | 0.51 | 0.34 |
| -c 50 -P 1 | set | 0.45 | 0.34 | 0.42 | 0.38 |
| -c 1 -P 1 | get | 0.22 | 0.19 | 0.36 | 0.53 |
| -c 1 -P 1 | incr | 0.25 | 0.21 | 0.38 | 0.57 |
| -c 1 -P 1 | lpush | 0.24 | 0.23 | 0.38 | 0.63 |
| -c 1 -P 1 | set | 0.24 | 0.21 | 0.42 | 0.62 |

Raw runs: results/bench-boxes-20261019-151338 (copy design, built from cc87622) and results/bench-boxes-20261019-151732
(one keyspace). On this machine one keyspace is slower than the copy design with 50 clients (roughly 0.75x of its ratio)
and faster with two single clients on two fronts; not yet profiled. Not claimed: anything about separate machines --
the benchmark rule (2026-10-18) is two boxes on separate machines with traffic crossing, and this is one machine.

### Size and complexity (lizard 1.24.1, the same script as the baseline above)

    copy design: front+apply+host                nloc    883  functions   67  CCN total   388  max  34  over-10  13
    one keyspace: front+region+host+bytes+diag   nloc    619  functions   52  CCN total   263  max  30  over-10   7
    copy design: all src                         nloc   8168  functions  831  CCN total  4749  max 105  over-10 113
    one keyspace: all src                        nloc   7756  functions  795  CCN total  4546  max 105  over-10 105
    main (RAM host + read caches): all src       nloc   8394  functions  863  CCN total  4910  max 105  over-10 111
    stock Redis 7.2.11 src/                      nloc  95870  functions 4113  CCN total 24662  max 208  over-10 525

git, the Redis example: against the copy design (cc87622) src/ 584 insertions, 1,019 deletions; against main (which
vendored the v0.63 platform and 40 patch scripts) 1,518 insertions, 15,974 deletions.

## Throughput as fronts are added, and the second hop (2026-10-19, after [NO_SECOND_HOP_V1])

redis-benchmark -c 50 -P 1 incr, the 50 clients split across K boxes/fronts on one memory, all on one 2-core machine
(scratchpad cpu_k.sh; requests/s in all):

| K | copy design | one keyspace |
|---|---|---|
| 1 | 52,122 | 36,816 |
| 2 | 51,216 | 43,690 |
| 3 | 41,439 | 39,320 |
| 5 | 32,104 | 44,379 |

The copy design slows as boxes are added (every box executes every command); one keyspace holds and is ahead at 5.
One front behind: a request and an answer per pipeline through the session, where the copy design sent unanswered
writes and read batched rows. Measured thread switches per command, one front: copy design front 0.61 / memory 0.28;
one keyspace before [NO_SECOND_HOP_V1] 1.37 / 1.97, after 1.31 / 1.06 (one front: ~33,000 -> 36,800-38,300 req/s).
Next lever: many connections' pipelines in one call (fan-in). Not claimed: separate machines.

## Requests/s at 1-5 fronts, v33 (writes and one standing read, piece cells, follow fix) -- 2026-10-19

`cpu_k.sh`: one memory, K fronts, `redis-benchmark -c 50/K -P 1 -t incr`, 180,000 requests in all. Copy design = cc87622
(v32); one keyspace = this build. Three rounds, interleaved, one 2-core machine (everything on it). Median of three:

| K | copy design | one keyspace | ratio |
|---|---|---|---|
| 1 | 57,405 | 61,909 | 1.08 |
| 2 | 64,625 | 80,589 | 1.25 |
| 3 | 47,521 | 84,926 | 1.79 |
| 5 | 34,673 | 77,062 | 2.22 |

The copy design falls as fronts are added (each write is copied to every front); one keyspace holds. All processes
share 2 cores here, so these are not separate-machine numbers.
