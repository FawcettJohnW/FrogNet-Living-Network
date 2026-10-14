# Acceptance -- the Redis region beside stock Redis 7.2.11

`tools/accept.sh` stands up stock Redis 7.2.11 (built from its own source, jemalloc) beside the region and runs every
test on both. Part 1 is parity; part 2 is what the region does that stock cannot, with stock's result measured on the
same run. Raw output per stage and arm: `results/accept-<stamp>/`.

    REDIS_SRC=/srv/redis-7.2.11 bash tools/accept.sh          # all stages but inet
    STAGES="suite" REDIS_SRC=... bash tools/accept.sh          # one stage
    STAGES="inet" RAM_ENDPOINT=host:port REDIS_SRC=... bash tools/accept.sh

Everything takes IP:port. Nothing needs DNS, so it runs on a LAN with no name service.

## Run of 2026-10-13, sandbox, 2 cores, x86_64 (not representative for performance; rerun on the Pi 5)

```
== suite: Redis 7.2.11's own tests, each file on both arms; a test green on stock and red on the region is a parity gap
  unit/type/incr  stock ok 32 err 0   region ok 31 err 1   green on stock only: 1
  unit/type/string  stock ok 82 err 0   region ok 79 err 2   green on stock only: 3
  unit/type/hash  stock ok 73 err 0   region ok 70 err 3   green on stock only: 3
  unit/type/set  stock ok 119 err 0   region ok 114 err 5   green on stock only: 5
  unit/type/list  stock ok 274 err 0   region ok 261 err 13   green on stock only: 11
  unit/type/zset  stock ok 320 err 0   region ok 316 err 4   green on stock only: 4
  unit/keyspace  stock ok 56 err 0   region ok 56 err 0   green on stock only: 0
  unit/type/stream  stock ok 75 err 0   region ok 60 err 15   green on stock only: 15
  unit/type/stream-cgroups  stock ok 66 err 0   region ok 56 err 10   green on stock only: 10
  unit/multi  stock ok 63 err 0   region ok 35 err 28   green on stock only: 28
```

Suite totals: stock 1,160 ok, 0 err. Region 1,078 ok, 81 err. 80 tests green on stock and red on the region, listed in `PARITY-GAPS-20261013.txt`; they have not yet been checked against TRIAGE-SUITE-REDS.md.

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
  the region: commands, data model, memory, front (src/)             35 files     7608 NLOC    788 functions  CCN total   4522  max  105  >10:  104
  the vendored Ribbit platform it builds on (platform/)              23 files    11411 NLOC    615 functions  CCN total   3894  max  548  >10:   87
  TOTAL                                                             19019 NLOC   1403 functions  CCN total   8416
== concurrency: every read-modify-write family hammered from several connections, Redis's guarantee checked
  stock:
    RESULT GREEN: 38 of 38 families hold their guarantee under concurrency; failing: none
  region:
    RESULT RED: 36 of 38 families hold their guarantee under concurrency; failing: ZINCRBY, APPEND
    results/accept-20261013-192114/concurrency-ribbit.txt:FAIL ZINCRBY -- expected 2700, region has 2693 (-7)
    results/accept-20261013-192114/concurrency-ribbit.txt:FAIL APPEND -- appended 2700 bytes, STRLEN 2892
== bench: redis-benchmark, one endpoint each, same command lines (req/s; region/stock)
  SET  stock 806452  region 60957  ratio 0.08
  GET  stock 826446  region 61996  ratio 0.08
# PART 2 -- what the region does that stock cannot: the same test on both, stock's result measured
== endpoints
  stock:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    FAIL endpoint 2: 0 INCRs accepted, 2000 refused (READONLY You can't write against a read only replica.)
    FAIL endpoint 1 reads the total: 2000 of 4000 INCRs sent across all endpoints
    FAIL endpoint 2 reads the total: 2000 of 4000 INCRs sent across all endpoints
    RESULT FAIL stock endpoints (K=2): 3 fail
  ribbit:
    PASS endpoint 1: 2000 INCRs accepted, 0 refused
    PASS endpoint 2: 2000 INCRs accepted, 0 refused
    PASS endpoint 1 reads the total: 4000 of 4000 INCRs sent across all endpoints
    PASS endpoint 2 reads the total: 4000 of 4000 INCRs sent across all endpoints
    RESULT PASS ribbit endpoints (K=2): 0 fail
== loss
  stock:
    FAIL after endpoint 1 was killed, endpoint 2 accepted 0 more writes (387 refused in all)
    PASS while endpoint 1 is down, the survivor (endpoint 2) reads 23543 of 23543 keys written
    FAIL endpoint 1, started again: reads 0 of 23543 keys written
    PASS endpoint 2, after endpoint 1 is back: reads 23543 of 23543 keys written
    RESULT FAIL stock loss (K=2): 2 fail
  ribbit:
    PASS after endpoint 1 was killed, endpoint 2 accepted 4782 more writes (0 refused in all)
    PASS while endpoint 1 is down, the survivor (endpoint 2) reads 15981 of 15981 keys written
    PASS endpoint 1, started again: reads 15981 of 15981 keys written
    PASS endpoint 2, after endpoint 1 is back: reads 15981 of 15981 keys written
    RESULT PASS ribbit loss (K=2): 0 fail
== scale: K endpoints at once, requests/s summed (stock SET can only go to its primary)
  MEASURE stock SET K=1 total 696864 req/s  per endpoint 696864
  MEASURE stock GET K=1 total 877193 req/s  per endpoint 877193
  MEASURE ribbit SET K=1 total 65466 req/s  per endpoint 65466
  MEASURE ribbit GET K=1 total 67958 req/s  per endpoint 67958
  MEASURE stock SET K=2 total 437398 req/s  per endpoint 218579 218818
  MEASURE stock GET K=2 total 898030 req/s  per endpoint 414938 483092
  MEASURE ribbit SET K=2 total 69379 req/s  per endpoint 34795 34584
  MEASURE ribbit GET K=2 total 71685 req/s  per endpoint 35772 35913
## results in results/accept-20261013-192114
```
