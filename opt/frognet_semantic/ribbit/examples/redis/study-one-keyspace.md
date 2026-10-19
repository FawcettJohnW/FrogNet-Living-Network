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
