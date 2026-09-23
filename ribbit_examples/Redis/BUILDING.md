# Building Redis-on-Ribbit

## Requirements

- Linux (x86-64 or ARM64). It has been built and run on Ubuntu x86-64 and on a Raspberry Pi 5.
- g++ with C++20 support (g++ 10 or later; tested with 13.3)
- make

The server itself needs nothing else: no Redis source, no FrogNet components, no third-party libraries.

On Debian or Ubuntu:

```
sudo apt-get install build-essential
```

## Build the server

```
make
```

This produces `ribbit-redis` in the top directory. `make clean` removes the objects and the binary.

`src/cmdtable.hpp` is generated from Redis 7.2.11's command definitions and is already included. You only need `make REDIS_SRC=/path/to/redis-7.2.11` if you delete it and want it regenerated.

## Run it

```
./ribbit-redis --port 6379
./ribbit-redis --port 6379 --io-threads 4
```

It also accepts a Redis-style config file as its first argument:

```
./ribbit-redis redis.conf --port 6379
```

Check it with any Redis client:

```
redis-cli -p 6379 ping
```

## Build stock Redis (for the test suite and benchmarks)

`redis-cli`, `redis-benchmark` and Redis's test suite come from Redis's own source tree. You also need Tcl for the suite.

```
sudo apt-get install tcl pkg-config
wget https://download.redis.io/releases/redis-7.2.11.tar.gz
tar xzf redis-7.2.11.tar.gz
cd redis-7.2.11
make MALLOC=libc
```

Then follow "Run Redis's own test suite against it" in README.md to put the wrapper in place. `redis-server.wrapper` in this directory is a ready-made copy. Set `RIBBIT_REDIS` to the full path of your `ribbit-redis` binary, or edit the default path in the wrapper.

## Profiling and tools

`tools/` contains the small benchmarks used during development (`membench.cpp`, `waitbench.cpp`) and the generator for the command table. They aren't needed to build or run the server.
