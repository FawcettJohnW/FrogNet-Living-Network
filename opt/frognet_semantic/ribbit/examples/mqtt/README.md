# MQTT on Ribbit — Mosquitto 2.1.2, its shared state lock-free, its loop multithreaded

Stock MQTT clients talk to a broker that is Eclipse Mosquitto 2.1.2 on the wire, in its config file and in its own
test suite. Behind the wire, everything Mosquitto shared between connections — subscriptions, retained messages,
which thread holds which client, messages in flight between threads, the global counts — lives in a lock-free
**region**, and the broker runs as many event loops (**fronts**) as you give it threads.

    MQTT_THREADS=4 front/src/mosquitto -c mosquitto.conf          # 4 fronts, the region resident in the process
    MQTT_REGION=own front/src/mosquitto -c mosquitto.conf         # this box on a memory of its own (mqtt-ram)
    MQTT_REGION=host:port front/src/mosquitto -c mosquitto.conf   # boxes on several processes/machines, one memory

With MQTT_REGION set, the box writes its truths to the memory and reads the other boxes' from there by triggers:
nothing is asked of another box and nothing is acknowledged (DESIGN.md, "Boxes on the memory"). Two boxes on one
memory, checked with stock clients: `tools/two_boxes.sh`.

DESIGN.md: what Mosquitto shares, what moved, how publish, delivery, handover, limits and signals work.
QUESTION-3.md: every place the contract is met differently, and why. study-*.md: the reading of Mosquitto's source
the design came from. MQTT-SUITE-MAP.md: the suite, test by test. RESULTS.md: measured, side by side.

## Build

    cd opt/frognet_semantic/ribbit/examples/mqtt
    tools/build.sh                 # JOBS=N to choose; compiler output goes to the terminal

Needs g++ (C++17), make, liblz4-dev, libssl-dev, libcjson-dev, libmicrohttpd-dev, libsqlite3-dev, libcunit1-dev,
libargon2-dev, python3. Builds the platform pieces it links (`cpp/`), `front/src/mqtt-ram`, the front
(`front/src/mosquitto`) and its plugins, the suite's test plugins, and `harness/mqtt-load`. Every front object is
rebuilt every time (Mosquitto's makefiles do not track header dependencies). Ends with `build PASS` or stops at the
first error. Nothing is installed anywhere.

## Run unmodified against modified

One command, on any machine (builds both, runs stock and every shape at 1, 2 and 4 fronts, prints the tables):

    tools/bench.sh                                   # FRONTS="1 2 4" SECS=10 REPEAT=3 SUITE=1 SCENARIOS=... to change

Its first line is the version (`VERSION`). The scenarios include many clients at once (`many-256`, `many-1024`,
`crowd-4096`), and every remote shape (`ownN`).
A run that hangs is dumped (threads, kernel stacks, socket queues, `$SYS`, the driver's connections: `[HANG_DUMP_V1]`
lines), stopped, and listed as HUNG; the bench carries on.

`stock/` is Mosquitto's v2.1.2 release tree, untouched. Step by step:

The stock broker: Mosquitto 2.1.2's own release, built the usual way (`make binary` in its tree). Then, from
`examples/mqtt` (as a non-root user — Mosquitto drops privileges when run as root and the suite breaks):

    # Mosquitto's suite, every test, per-test metrics (wall, bytes/packets each way, time waiting per expected packet,
    # broker CPU, RSS, context switches)
    python3 harness/run_suite.py --build-root /path/to/mosquitto-2.1.2 --label stock  --out stock.jsonl
    python3 harness/run_suite.py --build-root front                    --label ribbit --out ribbit.jsonl
    MQTT_THREADS=2 python3 harness/run_suite.py --build-root front --label ribbit-t2 --out t2.jsonl
    MQTT_REGION=own python3 harness/run_suite.py --build-root front --label ribbit-own --out own.jsonl
    python3 harness/compare.py stock.jsonl ribbit.jsonl --md compare.md

    # the same load against each broker, by one driver; broker CPU from /proc
    python3 harness/load_compare.py --broker stock=/path/to/mosquitto-2.1.2/src/mosquitto \
        --broker ribbit=front/src/mosquitto --seconds 10 --repeat 3 --out load.jsonl
    python3 harness/load_table.py load.jsonl

For thread counts, give `load_compare` a two-line wrapper per count (`MQTT_THREADS=4 exec .../front/src/mosquitto "$@"`).
On a machine with fewer cores than fronts plus driver threads, the threaded numbers measure the scheduler, not the
broker: measure scaling where the cores exist.

## Counters

The region keeps per-thread counters, summed when read: `rgn_stats()` resident, op `stats` on `mqtt-ram`
(DESIGN.md lists them). `$SYS` is Mosquitto's, fed from every front's own row.

`MQTT_DIAG=1` in the broker's environment (mqtt-ram inherits it) prints, every 50 ms, every front's backlog terms, the
host's run times per front, each front's answer/apply times, round trips and per-session wire counters
([DIAG-FRONTS] [DIAG-RUN] [DIAG-WAIT] [DIAG-RTT] [DIAG-SESS]; DESIGN.md, "Diagnostics").

## Licence

`front/` is Eclipse Mosquitto 2.1.2 (EPL-2.0 OR BSD-3-Clause), modified; used here under BSD-3-Clause. Everything
else: GPL-2.0-only, Copyright (C) 2016-2026 Fawcett Innovations LLC.
