# Redis across the Internet -- the memory on a neutral machine, boxes anywhere
    bash tools/build.sh          # build/ribbit-redis-host, build/ribbit-redis-front

## The memory (streamingfrog)
    build/ribbit-redis-host --listen 0.0.0.0:PORT
It holds intents, not keys: what each box's clients asked, in write order. It has no Redis in it.

## Boxes, on any machine
    printf 'requirepass CHOOSE-ONE\n' > /etc/ribbit-redis.conf        # any Redis config lines
    RIBBIT_RAM=streamingfrog:PORT RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port 6379 /etc/ribbit-redis.conf   # the first
    RIBBIT_RAM=streamingfrog:PORT build/ribbit-redis-front --port 6379                                                   # any more
    redis-cli -a CHOOSE-ONE ...        # any stock client, against its local box
Every box holds the whole region and answers its clients from its own copy, in the one order. A command's reply
waits for one round trip to the memory (its intent written, then read back in order); a box adds clients and places,
not command throughput (DESIGN.md, Limits).

## What is NOT here (stated, not implied)
- The wire is FNW1 in the clear, and anyone who can write the memory can write intents in any box's name: run the
  path inside FrogNet's own transport or a tunnel (WireGuard).
- A memory restart is a new, empty region; a box whose memory is gone says so and stops.

## Qualify, including the deployment
    REDIS_SRC=~/redis-7.2.11 RAM_ENDPOINT=streamingfrog:PORT bash tools/qualify.sh
Stages: build, oracles (two boxes, concurrency), Redis's own suite through local boxes, the suite across a simulated
24 ms path, and deploy: a box here on the named memory, latency and redis-benchmark through it.
