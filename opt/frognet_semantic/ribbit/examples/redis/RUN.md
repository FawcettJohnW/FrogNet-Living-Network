# Redis boxes on the memory -- build and run
    bash tools/build.sh                      # build/ribbit-redis-host (the memory), build/ribbit-redis-front (a box)
## Deployment shape
    # the memory (a neutral machine, e.g. streamingfrog)
    build/ribbit-redis-host --listen 0.0.0.0:PORT
    # boxes, anywhere, each holding the region; the first one's configuration is the region's
    RIBBIT_RAM=streamingfrog:PORT RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port 6379 /etc/redis.conf
    RIBBIT_RAM=streamingfrog:PORT build/ribbit-redis-front --port 6379
## Redis's own suite (a fresh region per test server: the box starts its own memory when RIBBIT_RAM is unset)
    REDIS_SRC=/srv/redis-7.2.11 STAGES=suite bash tools/accept.sh
