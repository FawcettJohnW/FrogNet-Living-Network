# Redis on the memory -- design [ONE_ORDER_V1]

The reference model is `docs/CONVERTING-TO-RIBBIT.md`; this is how Redis meets it. Written 2026-10-17 with the
change from the RAM-host design (a host process executing every pipeline, fronts calling it) to boxes on the memory.

## The model, for Redis

Redis's contract is one order: every command sees every command before it, and nothing between its start and its
end (a transaction is one command). Its core state -- counters, lists, a blocked client's place in line -- is
read-modify-write on contested truths. So the truth a box owns is not a key (any box may write any key): it is what
its clients asked. Each box writes its clients' requests once, as intents, and every box applies every intent, in
write order, to its own copy of the keyspace (`CONVERTING-TO-RIBBIT.md` §4b, the second form). The write order is the
one order; every copy reaches the same state by it, and the box that wrote an intent answers its client from
applying it. Nobody asks another machine anything; nothing is acknowledged.

    client --RESP--> box A ---- intents (written once) ----> memory <---- held read (the trigger) ---- box A, B, C ...
                     (applies every intent in write order; answers its own clients)

## The truth table

| truth | owner (one writer) | indices | latest / distinct | readers | trigger | derived instead |
|---|---|---|---|---|---|---|
| a request: a connection opening (`o`), its pipeline (`p`), its end (`g`), its parked command's time running out (`u`), the region's configuration (`x`) | the box the client is connected to | `(redis, cmd, <box>/<n mod RING>)` | distinct: a ring of RING per box; a slot is rewritten only when every live reader has applied it | every box | a standing trigger on `(redis, cmd, *)` (`Memory::follow`): every box's intents in one family, in write order, as they are written | -- |
| how far a box has applied | that box | `(redis, applied, <box>)` | latest | every box (pacing its own writes) | held read on `(redis, applied, *)` | a box is live while it rewrites it (every 250 ms) |
| a request for the region: a box joining whose history the rings no longer hold | the joining box | an intent `j` in its own ring | distinct | every box; the smallest live one answers | the `cmd` trigger | -- |
| the region as it stands at one write id (a copy) | the box that answered | `(redis, snap.<joiner>, <donor>/<k>)` pieces, then `(redis, snapdone, <joiner>)` `{id, k, b}` | written once | the joiner | held read on `(redis, snapdone, <joiner>)` | -- |
| the keyspace, connections, MULTI queues, WATCH versions, parked commands | nobody: derived | -- | -- | -- | -- | each box's copy, applied from the intents |

An intent's bag: `{"b":box,"n":n,"t":writer's clock ms,"k":kind,"c":the box's number for the connection,"u":parking,
"p":the pipeline's RESP}` -- `"q"` (base64) instead of `"p"` when the bytes are not UTF-8 (`[BINARY_SAFE_INTENTS_V1]`).
An intent over 12 KB goes as pieces (`"k":"P"`), each its own intent in the box's order, gathered per connection and
applied whole with the intent itself (`[BIG_INTENTS_IN_PIECES_V1]`).

## Deciding the same on every box

- **Order**: the write id. A box applies an intent only after every intent with a smaller id (the memory publishes
  in id order, `[PUBLISH_IN_ID_ORDER_V1]`).
- **Time**: while an intent is applied, the clock is the largest writer's clock seen so far in the order (never
  backwards). Expiry, TTL, XADD's `*`, OBJECT IDLETIME, CLIENT LIST ages: all read it (`apply_now_ms`, memory.hpp).
- **Randomness**: the generator is seeded by the intent's write id (`apply_rng`): SPOP, SRANDMEMBER, HRANDFIELD,
  ZRANDMEMBER, RANDOMKEY pick the same on every box.
- **Connection ids**: assigned when the `o` intent is applied, in order: the same on every box.
- **Blocked commands** `[PARKED_IN_THE_ORDER_V1]`: a blocking command is tried without blocking (`try_block`). When
  it would wait, it is parked in arrival order with what its pipeline still holds; after every intent the parked
  commands whose keys changed are tried again, first parked first. Served, it answers and its pipeline goes on. Its
  time running out is an intent written by its own box (`u`); CLIENT UNBLOCK and CLIENT KILL are commands like any
  other. Every box parks and serves the same way; only the box holding the client writes to it.

## Nothing lost, or said

- A writer never overwrites slot `n mod RING` until every live box has applied intent `n - RING` (the readers'
  `applied` tuples). A box that has not rewritten its `applied` for 3 s is gone and is not waited for.
- Each box checks every other box's intents arrive as 1, 2, 3 ... A gap -- an intent overwritten before this box
  applied it, or a box joining a region whose history the rings no longer hold -- means this copy can no longer be
  the region's: the box says so and stops. Gone is gone (`run_two_boxes.py` check 10).
- A box listens for clients only once it holds the region: it has applied the history the memory holds, or -- when the
  rings no longer reach back to every box's first intent -- it has taken a copy (`[JOIN_BY_COPY_V1]`): it writes a `j`
  intent and holds `applied` at 0 (so nothing after its request is overwritten); the smallest live box, applying that
  intent, writes the region as it stands right after it -- keyspace cells with their write ids, creation ids, times and
  accumulators; connections; MULTI queues and watches; parked commands in their line; configuration; every box's place
  in the order -- as pieces; the joiner loads it and follows the order from that write id. Measured: 15 KB, 1 ms to
  write, 511 ms to join (500 of them the wait before asking); `run_two_boxes.py` check 10.

## The participants (front.cpp, apply.cpp)

| participant | does |
|---|---|
| event loops (one per two cores) | read clients, frame pipelines, write `o`/`p`/`g` intents |
| the writer (whoever drains) | the box's intents in order through one lock-free queue, paced by the slowest reader; each its cell's own request on the semantic wire, a DIFF against the box's previous intent (`<box>/<n>` is a moving window, `[MOVING_WINDOW_REFERENCE_V1]`), not answered (`[NO_WRITE_ACK_V1]`) |
| the applier | the standing trigger on `cmd` (its rows DIFFs against the previous row, `[ROWS_ON_THE_SEMANTIC_WIRE_V1]`), run on the session's reader thread as each batch lands: applies every intent in write order (apply.cpp), hands this box's replies to its clients (re-armed before the reply is written: `[REPLIES_IN_ORDER_V1]`) |
| heartbeat | this box's `applied` every 250 ms; a refused write stops the box |
| readers | the trigger on `applied`: the slowest live box, for pacing |
| the watcher | parked clients: hang-ups (`g`) and deadlines (`u`) |

## What changed from the RAM-host design, and why

| before | now |
|---|---|
| a host process executing every pipeline (`op=redis_raw`), fronts calling it and waiting for the answer | the memory holds intents; every box executes; nothing is called |
| connection ids and tokens issued by the host, context held there | connections are state in every copy, opened by an intent |
| blocking commands parked on a host thread (`op=redis_block`), a watcher calling `cancel` | parked in the order, served by whichever intent makes them ready |
| reads answered at the front from a coherent cache (feeds, invalidations, `coherence.cpp`) | every box holds the keyspace: a read is applied in order like a write |
| MULTI/EXEC not isolated (ruled), multi-key reads best-effort (ruled) | both hold by construction: each intent is applied in one turn |
| a vendored platform (v0.63) and 40 patch scripts | ribbit/cpp, linked as it is (`tools/build.sh`) |

Removed: `redis_region.cpp`, `coherence.{hpp,cpp}`, `door/`, `tools/make_host.sh`, `tools/apply_*.py`, `tools/fdf/`,
`platform/`.

## Limits, stated

- **One applier per box.** Every box executes every command, one at a time, as stock Redis does. A box's command
  rate is bounded by one core, whatever the number of boxes; boxes add clients and places, not command throughput.
- **A copy is written on the answering box's applier.** While it is written, that box applies nothing else (a large
  keyspace takes its serialisation time); the other boxes go on, and the joiner's `applied` of 0 holds their rings.
- **Trust.** Anyone who can write the memory can write intents in any box's name; the memory's access is the trust
  boundary (run it inside FrogNet's transport or WireGuard).
- **Persistence.** None: when the memory stops, the region is gone.
