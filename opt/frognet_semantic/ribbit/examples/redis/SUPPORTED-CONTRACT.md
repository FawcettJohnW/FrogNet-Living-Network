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
- **Throughput comparable to stock Redis.** On the run of 2026-10-13 (2-core sandbox), one front gave 0.10x stock's
  SET rate and 0.08x its GET rate.

## Not measured yet
- Across a LAN or the Internet with fronts on other machines (`STAGES=inet`, `tests/coordinated.py`).
- Stock Redis Cluster with a cluster-aware client, which also serves one keyspace from several nodes.
