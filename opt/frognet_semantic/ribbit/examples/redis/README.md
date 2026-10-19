# Redis on Ribbit -- boxes on the memory

Stock Redis clients (redis-cli, redis-py, Jedis, redis-benchmark ...) talk RESP to a **box** on their own machine or
any reachable one. Every box holds the whole Redis region and shares one **memory** with the others: each box writes
what its clients ask, once, as intents in the memory, and applies every box's intents in write order to its own copy.
Every copy is the same; any box takes reads and writes for the whole keyspace. Nothing is asked of another machine.

    stock client --RESP--> box --intents--> memory (ribbit-redis-host) <--held read, write order-- every box

How and why: DESIGN.md (the truth table, deciding the same on every box, what changed from the RAM-host design).
The reference process for any conversion: ../../docs/CONVERTING-TO-RIBBIT.md.

## Build
    bash tools/build.sh          # build/ribbit-redis-host (the memory), build/ribbit-redis-front (a box)
Links ribbit/cpp as it is. Needs g++ 11 or newer, liblz4-dev, libssl-dev, libjemalloc-dev, python3 (installed when
missing). Incremental by content; `CLEAN=1` for from scratch.

## Run
    build/ribbit-redis-front --port 6379                       # one box, on a memory of its own (dies with it)
    build/ribbit-redis-host --listen 0.0.0.0:8886              # a memory for many boxes (a neutral machine)
    RIBBIT_RAM=memhost:8886 RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port 6379 my.conf   # first box: its config is the region's
    RIBBIT_RAM=memhost:8886 build/ribbit-redis-front --port 6379                                     # any number more
A box started on a running region replays its history when the memory still holds all of it, and otherwise takes a
copy of the region from a live box (DESIGN.md); it serves once it holds the region.

## Test and measure
    python3 tests/run_two_boxes.py build 7820          # fifteen checks across boxes (blocking, kill, unblock, MULTI, a box gone, a box joining by copy)
    python3 tests/run_concurrency.py build 7710 3 6    # 39 read-modify-write families from three boxes at once
    REDIS_SRC=/srv/redis-7.2.11 STAGES=suite bash tools/accept.sh   # Redis 7.2.11's own suite, stock and boxes, side by side
    tools/bench_boxes.sh                               # redis-benchmark: stock, one box, two boxes -- interleaved, medians
    REDIS_SRC=... RAM_ENDPOINT=memhost:8886 bash tools/qualify.sh   # build, oracles, suite, simulated Internet, deploy

## Layout
    src/front.cpp            a box: clients, its intents (paced by the slowest reader), the trigger that applies the order
    src/apply.{hpp,cpp}      the region as every box holds it: connections, pipelines, parked commands
    src/redis_ram.cpp        the memory: the platform's RamHost, no operations of its own
    src/memory.*, db.cpp, cmd_*.cpp   Redis's data model and commands (the apply clock and seed: memory.hpp)
    tests/                   run_two_boxes, run_concurrency, coordinated (machines across the Internet), accept_*
    tools/                   build, accept, qualify, bench_boxes, latency_relay (a simulated path)

## Documents
DESIGN.md - QUESTION-3.md (what Redis's contract forced, entry by entry; the one-order section) - SUPPORTED-CONTRACT.md
- TESTING.md - TRIAGE-SUITE-REDS.md - AUDIT-ONE-WRITER.md - HISTORY-RAM-HOST-README.md (the design this replaced) -
HISTORY-ONE-PROCESS-README.md - CHECKPOINT-STEP*.md (history).
