# Testing Redis on Ribbit -- what is run, what the numbers mean (2026-09-29)

## The test tools and what each one proves
| tool | what it proves | how to read it |
|---|---|---|
| `tests/run_concurrency.py build PORT FRONTS THREADS` | every read-modify-write family (38) holds Redis's guarantee when commands from several fronts run truly at once | GREEN n/n. A FAIL names the family and the violation (lost increments, an element delivered twice, two SETNX winners). One CPU hides races: run it on a multi-core machine. |
| `tests/coordinated.py participate / coordinate` | machines across the Internet share one region; the Go signal is a Redis list | counter check: region total == acknowledged INCRs (nothing lost, nothing applied twice); visibility check: every machine reads every other machine's writes |
| `tools/qualify.sh` stage `oracles` | two fronts share one keyspace, a forged call is refused (trust), a front survives losing its host (reconnect), an unresolved SAME is answered from the cache, never re-run | RESULT GREEN per oracle |
| `tools/qualify.sh` stage `suite` | Redis 7.2.11's own tests through a local front and host (each test server gets a fresh region) | ok / err per file; the table below says what every err is |
| `tools/qualify.sh` stage `inet` | the same suite with front and host 24 ms apart (simulated) | as `suite`; slower, same classes |
| `tools/qualify.sh` stage `deploy` | two fronts against the real RAM host (streamingfrog), latency and redis-benchmark through a front | GREEN, then numbers |
| redis-benchmark through a front | throughput and latency | compare with the round trip: 1 client ~ 1/RTT; 50 clients ~ 50/RTT |

## How to read Redis's suite numbers
Every failing test is in one of these classes -- a number alone means nothing without its class:
- **machinery**: the test exercises something this system replaces by construction -- replication and propagation to
  replicas, persistence (RDB, AOF, DEBUG RELOAD/LOADAOF, DUMP/RESTORE, SAVE), Redis's internal encodings (ziplist,
  listpack, quicklist, shared-object refcounts, sparse HyperLogLog, stream radix-tree node size), SHUTDOWN, WAIT. The
  server answers "not applicable" by name.
- **not implemented**: a command family not built in the RAM-host shape. Today: pub/sub (SUBSCRIBE, PSUBSCRIBE,
  SSUBSCRIBE, PUBSUB), ACL, SLOWLOG, CLIENT PAUSE, MONITOR, LATENCY, Lua (EVAL, SCRIPT, FUNCTION, FCALL), CLIENT
  TRACKING. The server answers "unknown command".
- **knock-on**: a test that failed only because an earlier failure left state behind (a transaction left open, a user
  ACL SETUSER never created, maxmemory left set). Run alone, the ten core-file knock-ons pass.
- **question-3**: a real difference in contract, recorded in QUESTION-3.md: lazy expiry (truth is derived, a
  stale counter never lingers), cross-connection arrival order (BRPOPLPUSH/WATCH), plus the two rulings -- no
  MULTI/EXEC isolation, multi-key reads best-effort.
- **to trace**: not yet traced to its cause. Every one of these is open work until traced.

The ten core type files are fully traced, test by test, in TRIAGE-SUITE-REDS.md.

## Coverage: every file run, x86 sandbox, front + host on one machine
| file | ok | err | what the reds are |
|---|---|---|---|
| integration/logging | 0 | 2 | to trace 2 |
| integration/redis-benchmark | 7 | 1 | to trace 1 |
| integration/redis-cli | 25 | 7 | to trace 4, not implemented 3 -- cut short by the 300 s sandbox limit |
| unit/acl | 2 | 98 | not implemented 78, knock-on 18, to trace 2 |
| unit/acl-v2 | 0 | 34 | not implemented 34 |
| unit/aofrw | 0 | 22 | to trace 14, machinery 7, not implemented 1 |
| unit/auth | 7 | 0 | - |
| unit/bitfield | 20 | 2 | machinery 1 (replica), knock-on 1 |
| unit/bitops | 45 | 0 | - |
| unit/client-eviction | 0 | 4 | to trace 4 -- cut short by the 300 s sandbox limit |
| unit/dump | 1 | 27 | to trace 14, machinery 13 |
| unit/expire | 59 | 12 | machinery 9, question-3 1 (lazy expiry: truth is derived), to trace 2 |
| unit/functions | 0 | 111 | not implemented 102, machinery 5, to trace 4 |
| unit/geo | 64 | 0 | - |
| unit/hyperloglog | 22 | 5 | machinery 1 (no sparse encoding), encoding 4 (sparse/dense PFDEBUG, corruption detection of sparse form) |
| unit/info | 8 | 12 | to trace 10, not implemented 2 |
| unit/info-command | 3 | 0 | - |
| unit/introspection | 28 | 23 | to trace 11, not implemented 8, machinery 4 |
| unit/introspection-2 | 38 | 9 | to trace 8, not implemented 1 |
| unit/keyspace | 56 | 0 | - |
| unit/latency-monitor | 0 | 1 | not implemented 1 |
| unit/lazyfree | 1 | 3 | to trace 3 |
| unit/limits | 0 | 1 | to trace 1 |
| unit/maxmemory | 1 | 8 | to trace 6, not implemented 2 -- cut short by the 300 s sandbox limit |
| unit/memefficiency | 5 | 1 | not implemented 1 |
| unit/multi | 35 | 28 | machinery 24 (propagation 12, Lua 4, persistence 5, REPLICAOF/min-replicas 2, SHUTDOWN), knock-on 4 |
| unit/networking | 0 | 7 | to trace 5, machinery 2 |
| unit/obuf-limits | 0 | 4 | not implemented 3, to trace 1 |
| unit/oom-score-adj | 1 | 5 | to trace 5 |
| unit/other | 16 | 13 | machinery 6, not implemented 5, to trace 2 |
| unit/pause | 0 | 18 | not implemented 15, knock-on 1, machinery 1, to trace 1 |
| unit/printver | 0 | 0 | - |
| unit/protocol | 25 | 0 | - |
| unit/pubsub | 0 | 10 | not implemented 10 |
| unit/pubsubshard | 0 | 8 | not implemented 8 |
| unit/querybuf | 0 | 3 | to trace 3 |
| unit/quit | 3 | 0 | - |
| unit/replybufsize | 0 | 1 | to trace 1 |
| unit/scan | 21 | 0 | - |
| unit/scripting | 0 | 63 | not implemented 63 |
| unit/shutdown | 2 | 7 | to trace 6, machinery 1 |
| unit/slowlog | 0 | 17 | not implemented 17 |
| unit/sort | 52 | 1 | not implemented 1 (EVAL) |
| unit/tls | 0 | 0 | - |
| unit/tracking | 0 | 1 | not implemented 1 |
| unit/type/hash | 70 | 3 | machinery 3 (ziplist encoding x2, DUMP) |
| unit/type/incr | 31 | 1 | machinery 1 (shared-object refcount) |
| unit/type/list | 260 | 14 | machinery 13, question-3 timing 1 (BRPOPLPUSH/WATCH across connections) |
| unit/type/list-2 | 2 | 0 | - |
| unit/type/list-3 | 11 | 0 | - |
| unit/type/set | 114 | 5 | machinery 5 (DEBUG RELOAD x3, propagation, HTSTATS) |
| unit/type/stream | 60 | 15 | machinery 15 (propagation 7, AOF 2, radix-tree node size for '~'/LIMIT 6) |
| unit/type/stream-cgroups | 56 | 10 | machinery 10 (propagation 4, AOF 2, legacy RDB 2, DUMP, LOADAOF) |
| unit/type/string | 79 | 2 | machinery 2 (propagation) |
| unit/type/zset | 316 | 4 | machinery 4 (propagation x2, DEBUG RELOAD x2) |
| unit/violations | 1 | 0 | - |
| unit/wait | 0 | 14 | machinery 10, to trace 3, not implemented 1 |

**57 files, 1547 tests passing, 637 failing.**

Pi 5 (aarch64) runs of the core files give the same classes; hash.tcl reports 72 there because Redis runs its
"HINCRBYFLOAT for correct float representation" test on x86_64 only.

## What still has to be run
1. **Trace every "to trace" row above** (info, introspection, introspection-2, other, maxmemory, networking,
   oom-score-adj, querybuf, lazyfree, shutdown, client-eviction, dump, aofrw, functions, wait, pause, obuf-limits,
   replybufsize, limits, logging, redis-cli, redis-benchmark, expire, bitfield, hyperloglog) to one of the classes,
   fixing what is a defect.
2. **The three files cut by the sandbox's 300 s limit** -- unit/maxmemory, unit/client-eviction, integration/redis-cli
   -- run to the end on a real machine.
3. **The whole suite on each machine** (AI-Host x86, FrogNetHost Pi 5 aarch64): `SUITE="<all files above>" STAGES=suite`.
4. **The suite under parallel pressure**: runtest `--clients 16` on the Pi (the tables above are `--clients 1`).
5. **The suite across the simulated Internet path**: `STAGES=inet` with every file, not only incr and string.
6. **run_concurrency.py on each multi-core machine** (38 families), and with more fronts/threads (`8 32`).
7. **coordinated.py with three or more machines**, 50+ clients each, 120 s+, and with a machine joining through a
   FrogNet parent (as AI-Host does through Sea5).
8. **The deploy stage** against streamingfrog (two fronts, benchmarks) with requirepass off for its two-front check.
9. **A soak run** (not yet written): hours of mixed load, watching the host's memory -- pieces folding, EBR
   reclamation, the claim and connection records of fronts that died.
10. **Failure injection** beyond the reconnect oracle: kill a front mid-transaction, kill the host under load, drop
    the path for longer than the call timeout.
11. **Stock Redis on the same machine** with the same redis-benchmark and coordinated loads, for the comparison
    table.
