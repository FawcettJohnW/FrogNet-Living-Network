# Redis region on the RAM host -- build and run
    bash tools/build.sh                      # build/ribbit-redis-host, build/ribbit-redis-front
## Deployment shape
    # the RAM host (a neutral machine, e.g. streamingfrog)
    FROGNET_BLOB_ROOT=/var/lib/ribbit/blobs build/ribbit-redis-host --listen 0.0.0.0:PORT --api /Fawcett.Redis.ram_interface.php
    # any number of fronts, anywhere, connecting up
    RIBBIT_RAM=streamingfrog:PORT build/ribbit-redis-front --port 6379
## Redis's own suite (a fresh region per test server: the front starts its own host when RIBBIT_RAM is unset)
In redis-7.2.11: src/redis-server is  #!/bin/sh  exec /path/to/build/ribbit-redis-front "$@" ${RIBBIT_ARGS}
    ./runtest --single unit/type/string --ignore-encoding --ignore-digest --durable --clients 1
    python3 tests/run_two_fronts.py build src/redis-cli 9611
