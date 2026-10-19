# Redis on the memory -- design [ONE_KEYSPACE_V1]

The reference model is `docs/CONVERTING-TO-RIBBIT.md`. Written 2026-10-19; replaces ONE_ORDER_V1 (every box a copy of
the keyspace, kept the same by an intent ring) and main's RAM-host design (one keyspace, but fronts keeping read caches
true with invalidation feeds). Both kept more than one copy of something true; that layer is gone (study-one-keyspace.md).

## The model, for Redis

There is one keyspace and it is in the memory: the Redis region. The region is the one owner of every key -- a client
never writes a key, it asks, and its request is the RAM call (2026-10-18). A front speaks RESP to stock clients and makes
that call on its session for each pipeline it reads; the memory runs each call as a job on its pool, in parallel with
every other, and a key's tuple takes one request at a time (2026-10-18: the only data serialisation is on the server, at
the tuple). The answer is the call's reply. Nothing is copied, cached, fed, invalidated, paced or joined.

    stock client --RESP--> front --one RAM call per pipeline (the session)--> memory: the Redis region (the keyspace)

## The truth table

| truth | owner (one writer) | indices | latest / distinct | readers | trigger | derived instead |
|---|---|---|---|---|---|---|
| a key's value, one cell per element (hash field, set member, list position, zset member, stream entry) and its ttl cell | the region, applying the requests in arrival order at the tuple | `(<db>, <key>, <element>)` | latest | requests in the region | a blocked command's held read on the key's word | sizes, counts, liveness (ttl against the clock) |
| a blocked command's place in line | the region, for that request | `(claims.<db>, <key>, <ticket>)` | distinct, in arrival order | the other blocked commands on that key: the lowest ticket is served | the key's word | -- |
| a connection (its db, protocol, name, MULTI queue, WATCHes, auth) | the region, opened by its front's request | the region's connection table, `<front>/<n>` | latest | its own requests; CLIENT LIST, CLIENT KILL, CLIENT UNBLOCK, INFO | -- | -- |
| a connection the region killed (CLIENT KILL) | the region | `(redis.kill, <front>, <n>)` | written once | its front | the front's held read on `(redis.kill, <front>, *)`: it closes the socket | -- |
| which session a connection came on | the region, when the connection opens (`o`) | the connection | for the session's life | the region: a session that ends takes its connections with it (invariant 5) | session down | -- |
| the region's configuration | the front that starts the memory (its file), then CONFIG SET | the region's config | latest | every request | -- | -- |

## The call

One kind of request, `op=redis`, its body a line naming what it is, then its bytes -- each byte carried as the code point
of the same number, since the session carries text (`[BYTES_AS_CODE_POINTS_V1]`). A pipeline's call names its
connection's slot as its instance, so each connection's answers keep their own reference on the session
(`[A_CHAIN_PER_CONNECTION_V1]`; QUESTION-3 entry 11).

| request | runs | answer |
|---|---|---|
| `o <front>/<n> <addr> <laddr>`: a connection opens | inline, in wire order (before its first pipeline) | none awaited |
| `p <front>/<n>` + the pipeline's RESP | on the pool | `<authenticated><close>` then the replies' RESP |
| `g <front>/<n>`: the client hung up | inline | none awaited |
| `x` + the configuration as RESP | inline | none awaited |

A connection has at most one pipeline in the region: its front reads the client's next bytes only after the answer
(Redis's own order for one connection). A blocked command keeps its call unanswered; the front holds the socket open for
hang-ups only.

## Blocked commands, without a thread

A blocking command (BLPOP and family, BZPOP*, XREAD/XREADGROUP BLOCK) is tried at once. If it would wait, its call's
answer is kept, its claim ticket stays in the line, and it is registered on the key's word. The write that moves the
word wakes it: the writer's own thread runs the parked command again right after its command, before its next one
(Redis serves an unblocked client before the pipeline goes on), and the lowest ticket is served. Its time running out is
one deadline thread for every parked command (one thread waits for all, 2026-10-18). CLIENT UNBLOCK and a hang-up end it
the same way. Each connection has one runner at a time: an atomic flag and a mask of what is pending (a pipeline, a wake,
a deadline, a hang-up); whoever takes the flag runs everything pending, so nothing waits on a lock.

## What it removed

| before | now |
|---|---|
| every box a copy of the keyspace, applying every box's intents (`apply.cpp`, the intent ring, its writer and pacer) | one keyspace in the memory |
| `applied` heartbeats, gap checks, a box that stops on a gap | nothing to keep in step |
| a late box joining by a snapshot copy (`snap.*`, `snapdone`, dump/load) | a front holds nothing: start one and it serves |
| the same clock and the same random seed on every copy | the region's clock and generator |
| main: fronts' read caches, per-front invalidation feeds (`coherence.*`) | reads are the call; the semantic wire's SAME is the cache |
| main: a parked request thread per blocked command; a fixed worker pool | a kept answer and a wake; the platform's adaptive pool |
| main: vendored platform v0.63 and 40 patch scripts | ribbit/cpp, linked as it is |

## Limits, stated

- **Trust.** Anyone who can call the memory can name any front's connection; the memory's access is the trust boundary
  (run it inside FrogNet's transport or WireGuard).
- **Persistence.** None: when the memory stops, the region is gone.
- **Redis pub/sub** (SUBSCRIBE, PUBLISH) is not implemented in this tree (it was not in the trees before either); it is a
  write and a held read when it is.
