# Acceptance -- the Redis region beside stock Redis 7.2.11

`tools/accept.sh` builds stock Redis 7.2.11 from its own source (jemalloc) and runs every test on it and on the region.
Part 1 is parity. Part 2 runs the same test on four arms -- stock with persistence off (as the region), stock with
AOF, stock with AOF and Sentinel, and the region -- and measures stock's result rather than assuming it. Raw output
per stage and arm: `results/accept-<stamp>/`. The supported contract that follows from it: `SUPPORTED-CONTRACT.md`.

    REDIS_SRC=/srv/redis-7.2.11 bash tools/accept.sh             # all stages but inet; REDIS_SRC path <= 40 chars
    STAGES="suite" REDIS_SRC=... bash tools/accept.sh             # one stage
    python3 tests/accept_triage.py results/<run>/SUMMARY.txt $REDIS_SRC --rerun   # class every parity gap

Everything takes IP:port; nothing needs DNS, so it runs on a LAN with no name service.

## Run of 2026-10-13 20:00, sandbox, 2 cores, x86_64 (performance not representative; rerun on the Pi 5)

```
== metrics (lines of code and cyclomatic complexity, stock src/ vs the region's src/)
stock Redis 7.2.11 server, src/ (deps/ excluded)
  replication                                                         1 files     2618 NLOC     87 functions  CCN total    684  max   58  >10:   17
  cluster, sentinel                                                   3 files     8995 NLOC    322 functions  CCN total   2531  max  151  >10:   49
  persistence                                                         9 files     5955 NLOC    212 functions  CCN total   1603  max  208  >10:   35
  scripting, functions                                                8 files     4143 NLOC    203 functions  CCN total    902  max   37  >10:   22
  modules API                                                         2 files     8201 NLOC    523 functions  CCN total   2045  max   71  >10:   33
  networking, event loop, TLS                                        15 files     5895 NLOC    386 functions  CCN total   1731  max  148  >10:   28
  clients (cli, benchmark)                                            6 files    10302 NLOC    263 functions  CCN total   2826  max  179  >10:   77
  command code (t_*, geo, hll, sort, expire, db, multi, pubsub...)     24 files    16606 NLOC    750 functions  CCN total   4937  max  100  >10:  114
  data structures                                                    25 files    12371 NLOC    574 functions  CCN total   2940  max  170  >10:   63
  server core, config, commands table                                16 files    15588 NLOC    552 functions  CCN total   3338  max  168  >10:   64
  other                                                              56 files     5197 NLOC    241 functions  CCN total   1125  max   59  >10:   23
  TOTAL server (clients excluded)                                   85569 NLOC   3850 functions  CCN total  21836
  MATCHED (also without scripting and modules, which the region lacks)    73225 NLOC   3124 functions  CCN total  18889
Redis region
  the region: commands, data model, memory, front (src/)             35 files     7677 NLOC    793 functions  CCN total   4548  max  105  >10:  104
  the vendored Ribbit platform it builds on (platform/)              23 files    11411 NLOC    615 functions  CCN total   3894  max  548  >10:   87
  TOTAL                                                             19088 NLOC   1408 functions  CCN total   8442
# PART 1 -- parity: the same tests on stock and on the region
== suite: Redis 7.2.11's own tests, each file on both arms; a test green on stock and red on the region is a parity gap
  unit/type/incr  stock ok 32 err 0   region ok 31 err 1   green on stock only: 1 (red on the region 1, not run on the region 0)
  unit/type/string  stock ok 82 err 0   region ok 79 err 2   green on stock only: 3 (red on the region 2, not run on the region 1)
  unit/type/hash  stock ok 73 err 0   region ok 70 err 3   green on stock only: 3 (red on the region 3, not run on the region 0)
  unit/type/set  stock ok 118 err 0   region ok 113 err 5   green on stock only: 5 (red on the region 5, not run on the region 0)
  unit/type/list  stock ok 257 err 0   region ok 246 err 11   green on stock only: 11 (red on the region 11, not run on the region 0)
  unit/type/zset  stock ok 303 err 0   region ok 299 err 4   green on stock only: 4 (red on the region 4, not run on the region 0)
  unit/keyspace  stock ok 54 err 0   region ok 54 err 0   green on stock only: 0 (red on the region 0, not run on the region 0)
  unit/type/stream  stock ok 75 err 0   region ok 60 err 15   green on stock only: 15 (red on the region 15, not run on the region 0)
  unit/type/stream-cgroups  stock ok 65 err 0   region ok 55 err 10   green on stock only: 10 (red on the region 10, not run on the region 0)
  unit/multi  stock ok 63 err 0   region ok 35 err 28   green on stock only: 28 (red on the region 28, not run on the region 0)
== concurrency: every read-modify-write family hammered from several connections, Redis's guarantee checked
  stock:
    RESULT GREEN: 39 of 39 families hold their guarantee under concurrency; failing: none
  region:
    RESULT GREEN: 39 of 39 families hold their guarantee under concurrency; failing: none
== bench: redis-benchmark, one endpoint each, same command lines (req/s; region/stock)
  SET  stock 701754  region 67092  ratio 0.10
  GET  stock 851064  region 65147  ratio 0.08
# PART 2 -- beside stock as an operator would set it up: the same test on every arm, stock's result measured
#   stock: persistence off (the region has none) | stock-aof: AOF on | stock-sentinel: AOF + Sentinel failover | ribbit: the region
== endpoints
  stock:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    FAIL endpoint 2: 0 INCRs accepted, 2000 refused (READONLY You can't write against a read only replica.)
    FAIL endpoint 1 reads the total: 2000 of 4000 INCRs sent across all endpoints
    FAIL endpoint 2 reads the total: 2000 of 4000 INCRs sent across all endpoints
    RESULT FAIL stock endpoints (K=2): 3 fail
  stock-aof:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    FAIL endpoint 2: 0 INCRs accepted, 2000 refused (READONLY You can't write against a read only replica.)
    FAIL endpoint 1 reads the total: 2000 of 4000 INCRs sent across all endpoints
    FAIL endpoint 2 reads the total: 2000 of 4000 INCRs sent across all endpoints
    RESULT FAIL stock-aof endpoints (K=2): 3 fail
  stock-sentinel:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    FAIL endpoint 2: 0 INCRs accepted, 2000 refused (READONLY You can't write against a read only replica.)
    FAIL endpoint 1 reads the total: 2000 of 4000 INCRs sent across all endpoints
    FAIL endpoint 2 reads the total: 2000 of 4000 INCRs sent across all endpoints
    RESULT FAIL stock-sentinel endpoints (K=2): 3 fail
  ribbit:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    PASS endpoint 2: 2000 INCRs accepted, 0 refused
    PASS endpoint 1 reads the total: 4000 of 4000 INCRs sent across all endpoints
    PASS endpoint 2 reads the total: 4000 of 4000 INCRs sent across all endpoints
    RESULT PASS ribbit endpoints (K=2): 0 fail
== loss
  stock:
    MEASURE stock: endpoint 2 accepted 0 writes in all, refused 961; first write accepted after endpoint 1 was killed: none within 8 s
    FAIL endpoint 2 kept taking writes after endpoint 1 was killed
    FAIL endpoint 1 after endpoint 1 is back (polled 30.2 s): reads 0 of 23620 acknowledged writes
    FAIL endpoint 2 after endpoint 1 is back (polled 30.2 s): reads 0 of 23620 acknowledged writes
    RESULT FAIL stock loss (K=2): 3 fail
  stock-aof:
    MEASURE stock-aof: endpoint 2 accepted 0 writes in all, refused 963; first write accepted after endpoint 1 was killed: none within 8 s
    FAIL endpoint 2 kept taking writes after endpoint 1 was killed
    PASS endpoint 1 after endpoint 1 is back (polled 0.0 s): reads 22345 of 22345 acknowledged writes
    PASS endpoint 2 after endpoint 1 is back (polled 0.0 s): reads 22345 of 22345 acknowledged writes
    RESULT FAIL stock-aof loss (K=2): 1 fail
  stock-sentinel:
    MEASURE stock-sentinel: endpoint 2 accepted 91839 writes in all, refused 312; first write accepted after endpoint 1 was killed: 1.27 s after
    PASS endpoint 2 kept taking writes after endpoint 1 was killed
    PASS endpoint 1 after endpoint 1 is back (polled 15.6 s): reads 113160 of 113160 acknowledged writes
    PASS endpoint 2 after endpoint 1 is back (polled 15.6 s): reads 113160 of 113160 acknowledged writes
    RESULT PASS stock-sentinel loss (K=2): 0 fail
  ribbit:
    MEASURE ribbit: endpoint 2 accepted 22135 writes in all, refused 0; first write accepted after endpoint 1 was killed: 0.00 s after
    PASS endpoint 2 kept taking writes after endpoint 1 was killed
    PASS endpoint 1 after endpoint 1 is back (polled 0.2 s): reads 26661 of 26661 acknowledged writes
    PASS endpoint 2 after endpoint 1 is back (polled 0.2 s): reads 26661 of 26661 acknowledged writes
    RESULT PASS ribbit loss (K=2): 0 fail
== hostloss
  stock:
    FAIL after the data's home (the primary) was killed and started again, endpoint 1 reads 0 of 21739 acknowledged writes (polled 30.3 s)
    RESULT FAIL stock hostloss (K=2): 1 fail
  stock-aof:
    PASS after the data's home (the primary) was killed and started again, endpoint 1 reads 20445 of 20445 acknowledged writes (polled 0.0 s)
    RESULT PASS stock-aof hostloss (K=2): 0 fail
  stock-sentinel:
    PASS after the data's home (the primary) was killed and started again, endpoint 1 reads 21984 of 21984 acknowledged writes (polled 0.0 s)
    RESULT PASS stock-sentinel hostloss (K=2): 0 fail
  ribbit:
    FAIL after the data's home (the RAM host) was killed and started again, endpoint 1 reads 0 of 4296 acknowledged writes (polled 30.5 s)
    RESULT FAIL ribbit hostloss (K=2): 1 fail
== scale: K endpoints at once, requests/s summed (stock SET can only go to its primary)
  MEASURE stock SET K=1 total 727273 req/s  per endpoint 727273
  MEASURE stock GET K=1 total 904977 req/s  per endpoint 904977
  MEASURE ribbit SET K=1 total 65338 req/s  per endpoint 65338
  MEASURE ribbit GET K=1 total 64082 req/s  per endpoint 64082
  MEASURE stock SET K=2 total 554789 req/s  per endpoint 278164 276625
  MEASURE stock GET K=2 total 1147892 req/s  per endpoint 579710 568182
  MEASURE ribbit SET K=2 total 74116 req/s  per endpoint 36907 37209
  MEASURE ribbit GET K=2 total 75507 req/s  per endpoint 37743 37764
## results in results/accept-20261013-200034
```

## Reading it
- Suite counts are tests, by name (two list tests run twice under one name, once per encoding, and count once).
  Stock 1,122 ok, 0 err; the region 1,042 ok, 79 err, and 1 test the suite skipped on it (it runs only when INFO
  reports jemalloc). Those 80 are classed in `PARITY-TRIAGE-20261013.md`: replication 35, persistence 21,
  encoding 13, scripting 5, cascade 5 (each passes alone on both arms), not run 1. None is unexplained.
- Fixed before this run, found by the earlier one: ZINCRBY lost increments under contention (it answered nil when it
  lost a race) and APPEND counted pieces twice when two folds overlapped (2,700 bytes read back as 2,892), and as
  pieces could answer the same length twice. `[ONE_CELL_SERIALISED_V1]`, `[FOLD_MARK_V1]`, `[APPEND_ON_THE_VALUE_V1]`.
- Stock with persistence off restarts empty, and its replica then resyncs from the empty primary: both read 0. That
  is the configuration that matches the region's (no persistence); stock with AOF reads everything back.
- Sentinel recovers stock from losing its primary: the replica took writes 1.27 s after the kill (312 writes refused
  meanwhile), and the old primary was readable again 15.6 s after its restart. The region's surviving front took
  writes with no gap (0 refused) and the restarted front read everything 0.2 s after its restart.
- Losing the RAM host loses the region: 0 of 4,296 acknowledged writes readable after it restarts.

## Run of 2026-10-13 23:44, after fan-in / multiplexing / consolidation (same sandbox)

Changes: the front is an epoll loop that reads every ready connection and sends their pipelines as ONE call
(`op=redis_batch`, `[EVENT_LOOP_FAN_IN_V1]`, `[FAN_IN_BATCH_V1]`); one session per dispatcher, so several batches are
in flight and each session's semantic engine runs on its own threads at both ends (`[ONE_SESSION_PER_DISPATCHER_V1]`);
the host fans each batch out across its cores with a lock-free pool; the connection context is held in the region and
no longer crosses the wire (`[CONTEXT_IN_THE_REGION_V1]`). Suite and concurrency results identical to the run above.

| redis-benchmark, -P 16 -c 50 | stock | region before | region now | now ÷ stock |
|---|---|---|---|---|
| SET, 1 endpoint | 660,066 | 65,338 | 113,186 | 0.17 |
| GET, 1 endpoint | 930,233 | 64,082 | 130,890 | 0.14 |
| SET, 2 endpoints | 444,199 | 74,116 | 145,124 | 0.33 |
| GET, 2 endpoints | 907,095 | 75,507 | 181,171 | 0.20 |

One connection, one command at a time (no pipelining) got slower: the loss test's single writer on endpoint 2
acknowledged 14,586 SETs in 10 s against 22,135 before -- the batch path adds a hop for a lone connection.

## Run of 2026-10-14 04:46, the batch as RESP on the wire (same sandbox)

`op=redis_raw` ([RESP_ON_THE_WIRE_V1]): the batch crosses the permanent socket (the data socket past the ceiling) as
the pipelines' own RESP in a text frame -- no JSON, no base64, no per-character escaping. The wire's codec holds a body
as text, so a payload that is not UTF-8 goes as base64, flagged; a value of every byte value round-trips. The platform
gains `Session::call_bytes` (tools/apply_call_bytes.py): call() with the answer's bytes unparsed. Suite and concurrency
identical; two-fronts, reconnect and trust oracles green.

| redis-benchmark -c 50 -P 16 | stock | region (JSON batch) | region (RESP batch) | ÷ stock |
|---|---|---|---|---|
| SET, 1 endpoint | 653,595 | 113,186 | 210,084 | 0.32 |
| GET, 1 endpoint | 900,901 | 130,890 | 286,944 | 0.32 |
| SET, 2 endpoints | 411,350 | 145,124 | 297,973 | 0.72 |
| GET, 2 endpoints | 1,061,195 | 181,171 | 406,672 | 0.38 |

The loss test's single sequential writer: 25,659 SETs in 10 s (22,135 originally, 14,586 with the JSON batch).
