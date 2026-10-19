# Testing Redis on Ribbit -- what is run, what the numbers mean (2026-09-29; tools updated 2026-10-19 for one keyspace, fronts on the memory)

## The test tools and what each one proves
| tool | what it proves | how to read it |
|---|---|---|
| `tests/run_concurrency.py build PORT BOXES THREADS` | every read-modify-write family (39) holds Redis's guarantee when commands from several fronts on one memory run at once | GREEN n/n. A FAIL names the family and the violation (lost increments, an element delivered twice, two SETNX winners). One CPU hides races: run it on a multi-core machine. |
| `tests/coordinated.py participate / coordinate` | machines across the Internet share one region; the Go signal is a Redis list | counter check: region total == acknowledged INCRs (nothing lost, nothing applied twice); visibility check: every machine reads every other machine's writes |
| `tests/run_two_fronts.py build PORT` (qualify `two-fronts`) | two fronts on one memory: a write on A read at once on B, blocking served across fronts, timeouts, arrival order, CLIENT UNBLOCK/LIST/KILL across fronts, MULTI/EXEC beside the other front (nothing lost; no isolation, ruled), a hang-up while blocked, a front gone (its clients leave), a front started later serving the one keyspace at once | RESULT PASS 15/15 |
| `tests/run_same_after_raw.py build PORT` (qualify `same-after-raw`) | the session's answer references stay equal on both ends when an answer goes RAW and comes again as SAME (QUESTION-3 entry 13) | RESULT PASS |
| `tools/accept.sh` / `tools/qualify.sh` stage `suite` | Redis 7.2.11's own tests through a front on a memory of its own (each test server gets a fresh region) | ok / err per file; the table below says what every err is |
| `tools/qualify.sh` stage `inet` | the same suite with front and memory 24 ms apart (simulated) | as `suite`; slower, same classes |
| `tools/qualify.sh` stage `deploy` | a front on the real memory (streamingfrog), latency and redis-benchmark through it | numbers |
| `tools/bench_boxes.sh` | redis-benchmark side by side: stock, one front, two fronts; interleaved, medians | ratio to stock per shape |

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

## Measured: boxes on the memory beside stock (v28-one-order, ribbit-platform 0.64, 2026-10-17)
x86 sandbox, 2 cores shared by the server(s), the memory and redis-benchmark; `tools/bench_boxes.sh`, 3 runs each,
interleaved, median [min..max] requests/s; -r 100000. "Two boxes": two boxes on one memory, the clients split
between them (for -c 1, one client on each: two in all).

| shape | cmd | stock | one box | two boxes | one box / stock | two boxes / stock |
|---|---|---|---|---|---|---|
| -c 50 -P 16 | GET | 1,481,481 | 552,486 | 462,606 | 0.37 | 0.31 |
| -c 50 -P 16 | INCR | 1,058,201 | 292,825 | 303,117 | 0.28 | 0.29 |
| -c 50 -P 16 | LPUSH | 1,342,281 | 335,570 | 323,998 | 0.25 | 0.24 |
| -c 50 -P 16 | SET | 966,183 | 269,179 | 261,247 | 0.28 | 0.27 |
| -c 50 -P 1 | GET | 98,231 | 53,893 | 50,841 | 0.55 | 0.52 |
| -c 50 -P 1 | INCR | 100,401 | 50,581 | 45,700 | 0.50 | 0.46 |
| -c 50 -P 1 | LPUSH | 112,739 | 53,504 | 53,216 | 0.47 | 0.47 |
| -c 50 -P 1 | SET | 108,695 | 53,022 | 48,983 | 0.49 | 0.45 |
| -c 1 -P 1 | GET | 23,255 | 6,285 | 17,952 (2 clients) | 0.27 | 0.77 |
| -c 1 -P 1 | INCR | 23,419 | 5,875 | 18,143 (2 clients) | 0.25 | 0.77 |
| -c 1 -P 1 | LPUSH | 24,875 | 6,361 | 14,577 (2 clients) | 0.26 | 0.59 |
| -c 1 -P 1 | SET | 22,727 | 6,082 | 15,731 (2 clients) | 0.27 | 0.69 |

Where a box's time goes (gperftools, -c 50 -P 1 SET): send 27% (client replies and the session's frames), recv 11%,
futex 9%, applying the commands 13%; the memory: futex/syscalls 35%, send 23%, recv 17%. Every command is one more
write and one more read through the memory than stock does: the cost is the hop, not the commands. What was taken
out on the way here (each measured): intents as binary writes, not through the engine (`[WRITES_IN_BINARY_V1]`:
one client 4.2k -> 7.2k/s); no answer to a write (`[NO_WRITE_ACK_V1]`: memory CPU per command 13.9 -> 9.3 us, -c 50
-P 1 37k -> 46k/s); the standing trigger instead of a read per batch (`[FOLLOW_STREAM_V1]`); the bag read in one
pass (`[RAW_ROWS_V1]`). Not claimed: any multi-machine number (run `tools/lan.sh` on the Pis).

Acceptance (`STAGES="endpoints loss hostloss" K=2`): endpoints PASS (stock FAIL: replicas refuse writes); loss PASS
(a box killed mid-write and started again joins by copy and reads 58,024 of 58,024 acknowledged writes; stock-aof
FAIL without Sentinel, stock-sentinel PASS after 15.7 s); hostloss FAIL as for stock without AOF (no persistence:
when the memory goes, the region goes).

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

## v29: intents and rows on the semantic wire (`results/bench-boxes-v29-SUMMARY.txt`, 2-core sandbox, medians of 3)

Each intent is its cell's own semantic DIFF against the box's previous intent (`<box>/<n>` is a moving window); the
rows the applier reads come back as DIFFs against the previous row. x stock: 50 clients one command at a time
0.17-0.20 (v28: 0.47-0.55); pipelined 0.15-0.20 (v28: 0.25-0.37); one client 0.15-0.17 (v28: 0.25-0.27). The engine's
CPU per command is the cost; two-box checks 15/15 and concurrency 39/39 as before.
