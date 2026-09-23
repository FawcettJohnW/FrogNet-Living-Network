# Testing Redis-on-Ribbit against Redis's own suite

Redis 7.2.11 ships a large test suite written for its own server. Here it's used as the external judge: Ribbit is the server under test, and nothing in the suite was written with Ribbit in mind.

## Set up

Build Redis 7.2.11 and `ribbit-redis` (see BUILDING.md), and install Tcl. Then put Ribbit behind the suite's server path:

```
bench/install_wrapper.sh ~/redis-7.2.11
```

This keeps the stock binary as `src/redis-server.stock` and installs a small script as `src/redis-server` that runs `ribbit-redis` with the suite's arguments. To put stock Redis back:

```
bench/install_wrapper.sh --restore ~/redis-7.2.11
```

## Run

```
bench/run_suite.sh ~/redis-7.2.11
```

By default it runs the data-structure and core test files (strings, counters, hashes, sets, lists, sorted sets, streams and consumer groups, keyspace, expiry, bits, geo, HyperLogLog, transactions, SORT, SCAN, protocol, auth and introspection), four files at a time, with Ribbit on one event thread.

Settings:

| Variable | Default | Meaning |
|---|---|---|
| `IO_THREADS` | 1 | Ribbit event threads |
| `CLIENTS` | 4 | test files run in parallel |
| `FILES` | the list above | e.g. `FILES="unit/type/list unit/type/zset"` |
| `OUT` | `./suite-<date>` | where the log and summary go |

The pressure run, many threads with many files at once, is the one that exercises the lock-free memory hardest:

```
IO_THREADS=4 CLIENTS=8 bench/run_suite.sh ~/redis-7.2.11
```

You can also call the suite directly from the Redis tree. `--ignore-encoding` and `--ignore-digest` are required:

```
cd ~/redis-7.2.11
RIBBIT_ARGS="--io-threads 4" ./runtest --single unit/type/list --ignore-encoding --ignore-digest --durable
```

## Reading the result

`run_suite.sh` prints one line, and writes the details to `OUT/summary.txt`:

```
ok 1177   err 94   timeouts/exceptions 0
```

followed by every `[err]` grouped by test file.

**`[ok]`** is a test Redis wrote, passing against Ribbit.

**`[err]` does not mean broken.** Almost every `[err]` falls into one of three expected groups:

1. **Not applicable by construction.** The test checks machinery Ribbit doesn't have because a shared-memory system doesn't need it: replication and MASTERAUTH, DEBUG RELOAD and RDB/AOF persistence, internal encodings (`OBJECT ENCODING`, listpack/quicklist conversions), shared-integer objects, memory-usage internals, CLIENT PAUSE, cluster and failover.
2. **Deliberate semantic differences.** Recorded in `QUESTION-3.md`. For example, Redis keeps counting expired keys until something touches them, while Ribbit reports the current truth; and Redis runs a blocked client inside the writer's own call, while Ribbit makes the waiter runnable without making the writer wait for it.
3. **Not implemented in this proof of concept.** Lua scripting, functions and pub/sub aren't implemented, so those test files aren't in the default list.

**What would be a real failure:**

- any timeout or exception line
- the server crashing or the run hanging
- an `[err]` in a data-structure test that doesn't fall into the groups above

On a Raspberry Pi 5, with four event threads and eight test files in parallel, the recorded result was 1,177 ok and 94 err, every err in the expected groups, with no timeout and no exception.

## Every red test gets three questions

1. Is our implementation wrong? Fix it, add a regression test, re-run everything.
2. Did we misread Redis's contract? Correct the model before correcting the code.
3. Should the new model decline this behavior? Record it in `QUESTION-3.md`. Never turn it into a fake pass.

If you find an `[err]` that doesn't fit the expected groups, that's exactly the kind of result this project wants to hear about.

## Memory-safety check

For an AddressSanitizer build:

```
make clean
make CXXFLAGS="-std=c++20 -O1 -g -fsanitize=address -fno-omit-frame-pointer -pthread"
```

Then run the suite as above. Any ASAN report appears in the server log under the suite's `tests/tmp` directory and in `OUT/suite.log`. Put the normal build back with `make clean && make` afterwards.
