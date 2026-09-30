# Redis across the Internet -- the RAM host on a neutral machine, fronts anywhere
    bash tools/build.sh          # build/ribbit-redis-host, build/ribbit-redis-front

## The RAM host (streamingfrog)
    mkdir -p /var/lib/ribbit-redis/blobs
    printf 'requirepass CHOOSE-ONE\n' > /etc/ribbit-redis.conf        # any Redis config lines
    FROGNET_BLOB_ROOT=/var/lib/ribbit-redis/blobs RIBBIT_REDIS_CONFIG=/etc/ribbit-redis.conf \
      build/ribbit-redis-host --listen 0.0.0.0:PORT --api /Fawcett.Redis.ram_interface.php
The host's Redis configuration comes from RIBBIT_REDIS_CONFIG and afterwards only from CONFIG SET (after AUTH). No
front can push one unless it holds RIBBIT_REDIS_ADMIN, which the host was started with (leave it unset to refuse all).

## Fronts, on any machine, connecting up
    FROGNET_BLOB_ROOT=/tmp/ribbit-front-blobs RIBBIT_RAM=streamingfrog:PORT build/ribbit-redis-front --port 6379
    redis-cli -a CHOOSE-ONE ...        # any stock client, against its local front
Every front serves the same keyspace. A front holds no data: kill it and start it again, or start ten.

## What the host cannot be told
Each connection gets an id and a random token from the region; every call must present both; authentication (AUTH,
requirepass) is held in the region per connection and never taken from a call. tests/run_trust.py.

## What is NOT here (stated, not implied)
- The wire is FNW1 in the clear: no encryption between front and host. Put the path inside FrogNet's own transport
  or a tunnel (WireGuard) until the platform encrypts.
- A host restart is a new, empty region (no persistence, by construction); fronts reconnect on their next call
  (tests/run_reconnect.py).
- A front that dies leaves its connections' small records (and any MULTI queues) in the region.

## Qualify, including the deployment
    REDIS_SRC=~/redis-7.2.11 RAM_ENDPOINT=streamingfrog:PORT bash tools/qualify.sh
Stages: build, oracles (two fronts, trust, reconnect, same-miss), Redis's own suite through local fronts, the suite
across a simulated 24 ms path, and deploy: two fronts here against the named host (keys prefixed per run, removed
after) plus latency and redis-benchmark through a front connected up to it.
