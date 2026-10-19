# The Redis region's supported contract (boxes on the memory, v29-semantic, 2026-10-18)

What a client can rely on, measured by `tools/accept.sh` against stock Redis 7.2.11 on the same machine. Every class
of difference below comes from a test that is green on stock and not green on a box.

## Supported, as Redis
- The commands of strings, hashes, sets, lists, sorted sets, streams (with consumer groups), keyspace commands and
  MULTI/EXEC/WATCH, as Redis 7.2.11's own tests for them check -- except the classes below.
- **One order, across every box.** Every command from every box is applied in one order (the memory's write order),
  one at a time, as stock Redis applies its clients' commands: a write acknowledged through any box is seen by the
  next command through any box; MULTI/EXEC is isolated; a multi-key read sees one moment; blocked clients are served
  first-blocked first, in the turn of the command that readied them (`tests/run_two_boxes.py`, ten checks).
- Redis's guarantees under concurrency for every read-modify-write family `tests/run_concurrency.py` checks (39),
  from three boxes at once: no increment lost, no element delivered twice, each APPEND answering the length after it.
- RESP2 from stock clients (redis-cli, redis-benchmark, redis-py, ...), to any box.
- **Boxes come and go.** A box that stops is not waited for after 3 s; a box started on a running region takes a copy
  of it and serves at once (acceptance `endpoints` and `loss`: PASS; stock without Sentinel refuses writes on its
  replicas, and stock-aof without Sentinel stops taking writes when its primary dies).

## Different from Redis, by design
- **Many writable endpoints, one keyspace.** Every box takes reads and writes for the whole keyspace. Stock Redis
  replicas refuse writes (READONLY).
- **Replication is not offered.** REPLICAOF, the replication stream, min-replicas-to-write and replica-state checks
  answer "not applicable".
- **Lua scripting, pub/sub and SHUTDOWN are not offered.**
- **Internal representation is not Redis's.** OBJECT ENCODING, shared integers, hash-table chain statistics and the
  points at which an approximate trim (`~`, `LIMIT`) stops differ; exact trims and the data itself match.

## Not supported -- a client must not rely on it
- **Persistence.** The region is the intents in the memory and the copies in the boxes. When the memory stops, the
  region is gone and every box on it stops. DEBUG RELOAD, SAVE, BGREWRITEAOF, AOF/RDB loading and DUMP-based checks
  fail.
- **Throughput comparable to stock on one endpoint.** Measured (TESTING.md, v29, 2 cores): 0.17-0.20x stock with 50
  clients one command at a time, 0.15-0.20x pipelined, 0.15-0.17x for one client: every intent and every row is a
  semantic DIFF on the wire, and the engine's CPU per command is the cost.
  More boxes add clients and places, not command throughput: every box applies every command.

## Suite parity (x86 sandbox, 2 cores, `--clients 1`), the ten core files
| file | stock | box | the reds |
|---|---|---|---|
| unit/type/incr | 32 | 31 | shared-object refcount (encoding) |
| unit/type/string | 82 | 79 | propagation 2; one test skipped on the box (big-value trim, an encoding check) |
| unit/type/hash | 73 | 70 | ziplist encoding 2, DUMP |
| unit/type/set | 119 | 114 | DEBUG RELOAD 3, propagation, HTSTATS |
| unit/type/list | 274 | 261 | DEBUG RELOAD 5, RDB loading, encodings 2, propagation (the replication stream) 5 |
| unit/type/zset | 320 | 316 | propagation 2, DEBUG RELOAD 2 |
| unit/keyspace | 56 | 56 | -- |
| unit/type/stream | 75 | 60 | propagation 7, AOF 2, radix-tree node size for `~`/LIMIT 6 |
| unit/type/stream-cgroups | 66 | 56 | propagation 4, AOF 2, legacy RDB 2, consumer seen/active time and NOACK consumer (2, as before) |
| unit/multi | 63 | 35 | propagation 12, Lua 4, persistence 5, REPLICAOF/min-replicas 2, SHUTDOWN, and their knock-ons |

The same counts as the RAM-host build on every file (accept-20261017-111032), with every red in the same class; the
blocking-order tests that passed there only when the woken client got a core first ("Linked LMOVEs", "Circular
BRPOPLPUSH", unblock inside CLIENT UNBLOCK) now pass by construction.
