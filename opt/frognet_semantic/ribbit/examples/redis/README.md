# Redis on Ribbit -- one keyspace, in the memory

Stock Redis clients (redis-cli, redis-py, Jedis, redis-benchmark ...) talk RESP to a **front** on their own machine or
any reachable one. The keyspace is in the **memory**, once. A front holds none of it: each pipeline a client sends is
one call on the front's session, run by the memory on its pool alongside every other, and the answer is the replies.
Start one front or ten; they all serve the one keyspace, at once, with nothing to copy, replay or join.

    stock client --RESP--> front --one call per pipeline--> memory (ribbit-redis-host): the Redis region, the keyspace

How and why: DESIGN.md (the truth table, blocked commands without a thread, what it removed). The study behind it:
study-one-keyspace.md. The reference process for any conversion: ../../docs/CONVERTING-TO-RIBBIT.md.

## Build
    bash tools/build.sh          # build/ribbit-redis-host (the memory), build/ribbit-redis-front (a front)
Links ribbit/cpp as it is. Needs g++ 11 or newer, liblz4-dev, libssl-dev, libjemalloc-dev, python3 (installed when
missing). Incremental by content; `CLEAN=1` for from scratch. Both log their version (VERSION) when they start.

## Run
    build/ribbit-redis-front --port 6379                       # one front, on a memory of its own (dies with it)
    build/ribbit-redis-host --listen 0.0.0.0:8886              # the memory, for many fronts (a neutral machine)
    RIBBIT_RAM=memhost:8886 RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port 6379 my.conf   # its config is the region's
    RIBBIT_RAM=memhost:8886 build/ribbit-redis-front --port 6379                                     # any number more

## Test and measure
    python3 tests/run_two_fronts.py build 7820         # fifteen checks across two fronts and a third started later
    python3 tests/run_concurrency.py build 7710 3 6    # 39 read-modify-write families from three fronts at once
    python3 tests/run_same_after_raw.py build 7830     # the session's answer references stay equal (SAME after RAW)
    REDIS_SRC=/srv/redis-7.2.11 STAGES=suite bash tools/accept.sh   # Redis 7.2.11's own suite, stock and fronts, side by side
    tools/bench_boxes.sh                               # redis-benchmark: stock, one front, two fronts -- interleaved, medians
    RIBBIT_REDIS_DIAG=/path/file                       # [DIAG-REDIS] one line per event of every call, both ends

## Layout
    src/front.cpp            a front: clients, one call per pipeline, the kills the region wrote for it
    src/region.{hpp,cpp}     the region in the memory: connections, pipelines on the pool, blocked commands parked
    src/redis_ram.cpp        the memory: the platform's RamHost with the region's one operation
    src/memory.*, db.cpp, cmd_*.cpp   Redis's data model and commands
    tests/                   run_two_fronts, run_concurrency, run_same_after_raw, coordinated, accept_*
    tools/                   build, accept, qualify, bench_boxes, latency_relay (a simulated path)

## Documents
DESIGN.md - study-one-keyspace.md - QUESTION-3.md (what Redis's contract forced, entry by entry) - SUPPORTED-CONTRACT.md
- TESTING.md - TRIAGE-SUITE-REDS.md - AUDIT-ONE-WRITER.md - HISTORY-*.md and CHECKPOINT-STEP*.md (the designs before).
