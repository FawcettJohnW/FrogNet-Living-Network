# The Redis region's supported contract (one keyspace, fronts on the memory, v33-one-keyspace, 2026-10-19)

What a client can rely on, measured by `tools/accept.sh` against stock Redis 7.2.11 on the same machine. Every class
of difference below comes from a test that is green on stock and not green on a front.

## Supported, as Redis
- The commands of strings, hashes, sets, lists, sorted sets, streams (with consumer groups), keyspace commands and
  MULTI/EXEC/WATCH, as Redis 7.2.11's own tests for them check -- except the classes below.
- **One keyspace, any front.** A write acknowledged through any front is seen by the next command through any front;
  blocked clients are served first-blocked first, by the write that readies them; the replies before a blocking
  command in a pipeline are sent as it blocks (`tests/run_two_fronts.py`, fifteen checks).
- Redis's guarantees under concurrency for every read-modify-write family `tests/run_concurrency.py` checks (39),
  from three fronts at once: no increment lost, no element delivered twice, each APPEND answering the length after it.
- RESP2 from stock clients (redis-cli, redis-benchmark, redis-py, ...), to any front; any bytes as values.
- **Fronts come and go.** A front holds nothing: one started on a running region serves the keyspace at once; one
  that stops takes its clients with it and nothing else.

## Different from Redis, by design
- **Many writable endpoints, one keyspace.** Every front takes reads and writes for the whole keyspace. Stock Redis
  replicas refuse writes (READONLY).
- **MULTI/EXEC is not isolated** (ruled 2026-09-29): another connection's write may land between an EXEC's commands;
  its own commands run in order and a parked reader wakes once, after it. **Multi-key reads are best-effort** (ruled).
- **Replication is not offered.** REPLICAOF, the replication stream, min-replicas-to-write and replica-state checks
  answer "not applicable".
- **Lua scripting, pub/sub and SHUTDOWN are not offered.**
- **Internal representation is not Redis's.** OBJECT ENCODING, shared integers, hash-table chain statistics and the
  points at which an approximate trim (`~`, `LIMIT`) stops differ; exact trims and the data itself match.

## Not supported -- a client must not rely on it
- **Persistence.** The region is in the memory. When the memory stops, the region is gone. DEBUG RELOAD, SAVE,
  BGREWRITEAOF, AOF/RDB loading and DUMP-based checks fail.
- **Throughput comparable to stock on one endpoint.** Measured (study-one-keyspace.md, 2 cores, front, memory and
  redis-benchmark on one machine): 0.22-0.28x stock pipelined, 0.31-0.42x with 50 clients one command at a time,
  0.19-0.23x for one client.

## Suite parity (x86 sandbox, 2 cores, `--clients 1`), the ten core files -- v33-one-keyspace identical to the copy design file by file (2026-10-19)
| file | stock | front | the reds |
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
