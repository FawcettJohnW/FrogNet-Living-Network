# The Redis region's supported contract

What a client of the region can rely on, measured by `tools/accept.sh` against stock Redis 7.2.11. Every class of
difference below comes from a test that is green on stock and not green on the region; `PARITY-TRIAGE-<date>.md`
lists each one with the rule that classed it.

## Supported, as Redis
- The commands of strings, hashes, sets, lists, sorted sets, streams (with consumer groups), keyspace commands and
  MULTI/EXEC/WATCH, as Redis 7.2.11's own tests for them check -- except the classes below.
- Redis's guarantees under concurrency for every read-modify-write family `tests/run_concurrency.py` checks (39):
  no increment lost, no element delivered twice, each APPEND answering the length after it.
- RESP2 from stock clients (redis-cli, redis-benchmark, redis-py, ...), to any front.

## Different from Redis, by design
- **Many writable endpoints, one keyspace.** Every front takes reads and writes for the whole keyspace. Stock Redis
  replicas refuse writes (READONLY).
- **Reads are answered at the front from what it has read** ([READS_AT_THE_FRONT_V1]). A front keeps the reply to a
  plain read of one key (GET, HGET, LRANGE, ZRANGE, ... -- `coherence.cpp` lists them) and answers the same read again
  itself, until the key changes. What a client can rely on:
  - *its own writes*: a client reads back what it wrote, through its own front, every time (the changes a batch made
    reach its front inside the batch's answer, before any reply of that batch is written);
  - *anything it was told OK*: once a write is acknowledged to anyone through a front, every read through that front
    sees it;
  - *other fronts*: a read through another front sees the write once that front has heard of it on its feed -- what a
    Redis replica gives. Measured by `tests/run_coherence.py`: reads never go backwards, every key converged within
    2 ms of the last write, a FLUSHALL was seen at once.
  Not kept: keys with an expiry, replies that are errors, anything inside MULTI or with replies switched off. A front
  that loses its feed keeps nothing until it has a new one. INFO's keyspace_hits/misses count only reads that reached
  the RAM host.
- **Replication is not offered.** REPLICAOF, the replication stream, min-replicas-to-write and replica-state checks
  answer "not applicable". (35 suite tests.)
- **Lua scripting and SHUTDOWN are not offered.** (5 suite tests.)
- **Internal representation is not Redis's.** OBJECT ENCODING, shared integers, hash-table chain statistics and the
  points at which an approximate trim (`~`, `LIMIT`) stops differ; exact trims and the data itself match. (13 tests.)

## Not supported -- a client must not rely on it
- **Persistence.** The region lives in the RAM host's memory only. When the RAM host process stops, every key is
  gone; a front restart loses nothing. DEBUG RELOAD, SAVE, BGREWRITEAOF, AOF/RDB loading and DUMP-based checks fail.
  (21 suite tests; acceptance `hostloss`: 0 of the acknowledged writes readable after the host restarts, against
  all of them for stock with AOF.)
- **Throughput comparable to stock Redis on one endpoint**, except reads of keys already read. Measured on the
  Raspberry Pi 5 (build ddc5465, 2026-10-14): one endpoint gave 0.51x stock with pipelining, 0.43x with 100 clients
  one command at a time, 0.15x for one client; with four endpoints the region's write total equals stock's (1.00x,
  stock's primary feeding three replicas). Kept reads: `warm` and `warmscale` stages.

## Not measured yet
- Across a LAN or the Internet with fronts on other machines (`STAGES=inet`, `tests/coordinated.py`).
- Stock Redis Cluster with a cluster-aware client, which also serves one keyspace from several nodes.
