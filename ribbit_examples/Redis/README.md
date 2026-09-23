# Redis-on-Ribbit

A Redis-compatible server whose entire keyspace lives in Ribbit shared memory. Built in four days as a proof of concept for the FrogNet programming model, and checked against Redis's own test suite.

## Why this exists

For fifty years, distributed and concurrent software has been written as conversations: queues, locks, coordinators, reapers, request and reply. FrogNet asks whether most of that machinery is actually required by the work, or only by the way we have been programming it.

Redis was chosen because we don't get to define whether it works. Redis 7.2 ships a large, hostile test suite written by other people for their own server. We implemented the Redis contract on Ribbit and let that suite judge the result.

Ribbit's rule is short: determine your truth, write your truth, read the other truths, go. Every Redis connection is a participant. Every key is independently addressable state. Blocking commands are held reads on the state they depend on. Nothing is kept just because Redis has it.

## What disappeared

The memory has no mutex, no lock, no reaper thread and no write contention. It's built from immutable values, per-cell versions, lock-free skip-list indexes, epoch-based reclamation and per-variable wait words. A writer publishes by compare-and-swap. A waiting reader sleeps on a wait word, and the writer wakes it only if someone is waiting there.

Redis semantics that used to need coordination became state:

- **Blocking pops and consumer groups** are settled by claim cells. There's no server queue and no lock.
- **MULTI/EXEC** is a batch published as one value.
- **Streams** are distinct cells addressed by stream ID. The pending-entries list is claim cells, and XACK is a remove.
- **Expiry** is derived from a TTL index. Dead keys are reclaimed on next touch.

Replication, Sentinel, Cluster, failover, MIGRATE, DUMP/RESTORE, SAVE/BGSAVE and CLIENT PAUSE aren't implemented, because in a shared-memory system they have nothing to do. The suite's tests for them are classified as not applicable by construction, file by file.

## Results

All numbers come from `redis-benchmark -c 200 -P 16` against stock Redis 7.2.11 on the same machine. Server and client are pinned to separate cores.

**Raspberry Pi 5** (4 × Cortex-A76, ARM64)

| Cores | Ribbit vs stock Redis |
|---|---|
| 1 | Parity: within 1% on GET, LPUSH and SADD; SET −12% |
| 2 | ≈ 2.0× |
| 3 | 2.2–2.6× (LPUSH 1.03M ops/s) |

**Intel i7-7567U** (2 physical cores), one million keys

| | SET | GET | LPUSH | SADD |
|---|---|---|---|---|
| Stock Redis, 1 core | 271k | 397k | 400k | 193k |
| Ribbit, 1 core | 301k | 365k | 421k | 174k |

On the second physical core, Ribbit adds +78% to +108% over its one-core rate. Stock Redis has no second row, because its command execution runs on a single thread.

**Intel i7-5557U** (2 physical cores, 4 MB L3)

| | SET | GET | LPUSH | SADD |
|---|---|---|---|---|
| Ribbit / stock, 1 core | 0.60× | 0.66× | 0.66× | 0.41× |
| Ribbit / stock, 2 cores | 1.07× | 1.18× | 1.19× | 0.72× |

The small cache exposes the cost of the per-key layout: Ribbit uses more allocations per key than Redis does. This result stays in the record because it shows where the next optimization is.

**Conformance.** One core: 1,502 ok / 207 not applicable across 29 test files. ARM64 with four I/O threads and eight test files in parallel: 1,177 ok / 94 err. Every one of the 94 is a known not-applicable or deliberate-difference test. There were no timeouts, no crashes and no ASAN reports, and the ARM memory model found no ordering hole.

## Deliberate differences

Every failing test was asked three questions:

1. Is our implementation wrong?
2. Did we misread Redis's contract?
3. Should the new model decline this behavior?

Question 3 is recorded, never faked into a pass. `QUESTION-3.md` is the register. It separates semantic disagreements, which stay red on purpose, from mechanisms that disappeared because the behavior could be expressed as state. Two examples:

- Redis can retain stale expiry bookkeeping until a key is touched. Ribbit preserves the observable state without reproducing that internal artifact.
- Redis can serve a blocked client inside the writer's own call. In Ribbit, publication makes the waiter runnable; the writer doesn't wait for the reader. Publication is synchronization, not scheduling.

Across eight very different system conversions, including this one, the base Ribbit vocabulary needed no new primitive. Redis-specific behavior (WATCH, MULTI, blocking arbitration, batch visibility) lives in the Redis region's own API, where each vendor is free to optimize for its own contract.

## Build and run

Requirements: Linux, a C++20 compiler (g++), `make`, and Tcl for the test suite.

```
make
```

This produces `ribbit-redis`. It's one process with the memory and the RESP front together. It's a drop-in server: stock `redis-cli`, `redis-benchmark` and client libraries connect to it unchanged.

```
./ribbit-redis --port 6379
./ribbit-redis --port 6379 --io-threads 4
```

## Run Redis's own test suite against it

Redis's source is not included here. Fetch Redis 7.2.11 from https://download.redis.io and build it normally. Then replace its server with a wrapper, so the suite launches Ribbit instead:

```
cd redis-7.2.11
mv src/redis-server src/redis-server.stock
printf '#!/bin/sh\nexec "${RIBBIT_REDIS:-/path/to/ribbit-redis}" "$@" ${RIBBIT_ARGS}\n' > src/redis-server
chmod +x src/redis-server

./runtest --ignore-encoding --ignore-digest --durable
RIBBIT_ARGS="--io-threads 4" ./runtest --ignore-encoding --ignore-digest --durable --clients 8
```

`--ignore-encoding` and `--ignore-digest` are required, because Ribbit doesn't use Redis's internal encodings or RDB digest. Expect `[err]` lines for not-applicable tests such as DEBUG RELOAD and replication. That's the signature of Ribbit being the server under test.

To restore stock Redis, move `src/redis-server.stock` back.

## Reproduce the benchmarks

Run stock Redis and Ribbit on the same machine with the same `redis-benchmark` line, and pin the server and the client to separate physical cores:

```
redis-benchmark -p 6379 -q -c 200 -P 16 -t set,get,lpush,sadd -r 1000000 -n 2000000
```

Then run Ribbit with `--io-threads 2` and `--io-threads 3` on the same machine. Please publish your numbers with the machine they came from. Reference results describe the hardware that produced them, not a limit.

## Relationship to FrogNet

This proof of concept is self-contained. It doesn't need a FrogNet node, a FrogNet network or any other FrogNet component. The Ribbit memory runs inside the server process.

It demonstrates the programming model that FrogNet carries across networks: independently addressable state, held reads and small participants instead of conversations. In FrogNet, the same model spans machines, sites and radio links as Network Shared RAM, and a Region like this one can be exposed by its owner over the Internet.

More at https://fawcettinnovations.com

## License

Redis-on-Ribbit is free software under the GNU General Public License, version 2 only. You can run it privately, including in production, without any separate license. The GPL's source obligations apply when you distribute it.

Redis is a trademark of Redis Ltd. This project is an independent Redis-compatible implementation and is not affiliated with or endorsed by Redis Ltd. Redis 7.2.11 and its test suite are distributed by their authors under the BSD 3-Clause license and are not part of this repository.

Copyright © 2026 Fawcett Innovations LLC
