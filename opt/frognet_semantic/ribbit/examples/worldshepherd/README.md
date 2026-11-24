# WorldShepherd SARA on the memory

Brandon Ray Davis's SARA (`github.com/Bdavis1390/Sara_pro`) has one part meant to work across machines: the
connector read-ticket ledger, where a ticket issued by one SARA must be claimed exactly once by one host. This
directory is that ledger on FrogNet's shared memory. WorldShepherd's RAM is a FrogNet app service: the app
`worldshepherd`, its one RAM `general`, served by the platform's `fdf-ram` on the machine the network elects. There is
no WorldShepherd server and no WorldShepherd executable.

Read `WHAT-CHANGED-FROM-SARA.md` first (each difference from SARA's stores and why), then `DESIGN.md`, then `RESULTS.md`.

| file | |
|---|---|
| `ribbit_claim_store.py` | `RibbitClaimStore`, a `SharedClaimStore`: a ticket is its cells; a SARA writes its own and reads. When the memory moves it finds it, writes its own again, learns which SARAs are there, and proceeds |
| `ticket_sql.py` | Brandon's `PostgresClaimStore`, unmodified, with no PostgreSQL: its four SQL calls done as those cells |
| `sara_ui.py` | SARA's browser page `/ui` for the memory: the tickets as they stand, as one held read |
| `harness/run_his_tests.py` | one command: Brandon's own tests, unmodified, on the memory with both stores, then the race between processes |
| `harness/test_the_apphost_moves.py` | the service: configured, elected, served; the serving machine stops and then its `fdf-ram` is killed, with claims in flight |
| `harness/on_the_memory.py` | the pytest plugin `run_his_tests.py` uses (`WS_STORE=sql` for his Postgres store on the memory) |
| `harness/test_one_session_many_threads.py` | 16 threads of one process on one session, 300 tickets |
| `harness/race.py` | many SARAs on many machines claiming the same tickets at once, coordinated through the memory |
| `../../../simulation/worldshepherd_slow_tier.py` | the simulator tier: the race with every SARA on its own shaped link, against the Postgres store |
| `Makefile` | `make`: `lib/libkartram.so` (the session library) and `lib/ribbit.py`. Incremental |
| `WHAT-CHANGED-FROM-SARA.md`, `DESIGN.md`, `AUDIT-NO-HIDDEN-MESSAGING.md`, `study-ticket-claims.md`, `WORLDSHEPHERD-SUITE-MAP.md`, `QUESTION-3.md`, `RESULTS.md`, `VERSION` | the conversion's records |

## Build

Needs Linux, g++ with C++20, `liblz4-dev`, `libssl-dev`, Python 3.10 or later with `pytest` and `fastapi`.

    git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git
    cd FrogNet-Living-Network && git checkout worldshepherd-ribbit-20261124
    (cd opt/fdf/src/fdf-source/rinab && tools/build.sh)                  # fdf-ram, the RAM manager
    (cd opt/frognet_semantic/ribbit/examples/worldshepherd && make)      # the session library

    git clone https://github.com/Bdavis1390/Sara_pro.git
    (cd Sara_pro && git checkout worldshepherd/connectors-distributed-claim-contract-v1-20260913)   # tested at 52b3764

Both builds are incremental: after a change, the same command compiles only what changed. Everything below is run
from `opt/frognet_semantic/ribbit/examples/worldshepherd`, with `SARA=` the Sara_pro directory.

## Test 1: SARA's own tests on the memory, and the race (no node needed, about 10 s)

    python3 harness/run_his_tests.py --sara $SARA

It starts one `fdf-ram` on a free local port and prints one line per check and `RESULT PASS`:

    PASS  1 his connector tests, WS_STORE=memory: ... 31 passed
    PASS  2 his connector tests, WS_STORE=sql: ... 31 passed
    PASS  3 one session, many threads: 2 passed
    PASS  4 the race, memory: 8 processes, 400 tickets: won once 400, twice 0, never 0; ...
    PASS  4 the race, sql: 8 processes, 400 tickets: won once 400, twice 0, never 0; ...

Lines 1 and 2 are the files `tests/test_connector_*.py` of his branch, not changed. In line 1 the name
`ThreadSafeSharedClaimStore` makes a `RibbitClaimStore`; in line 2 it makes his own `PostgresClaimStore`, constructed
with `ticket_sql.connection_factory(...)` in place of a PostgreSQL connection factory.

## Test 2: the RAM as a service, and what SARAs do when it moves (root, about 20 s)

Needs also MariaDB, `php` with mysqli and curl, and the static binaries (`bash opt/frognet_semantic/simulation/setup.sh --yes`
installs and builds all of it). As root:

    python3 harness/test_the_apphost_moves.py --sara $SARA

It configures the app with `fdf-app config worldshepherd general`, installs and serves it on three machines, and
runs three SARAs that find the RAM with no address given. Then the serving machine stops (the RAM is elected onto
another machine, whose memory is empty), and then that machine's `fdf-ram` is killed, each with 80 claims in flight.
Six lines and `RESULT PASS`; line 5 and line 6 must say `80 answered, 0 raised` and `one winner 40, two 0, none 0`.

## Test 3: across a network, in the simulator (about 1 minute; 4 minutes with all deployments)

From `opt/frognet_semantic`:

    python3 simulation/worldshepherd_slow_tier.py --sara $SARA --deploy lan wan

Every SARA is a process on its own shaped link (LAN 0.5 ms; WAN 50 ms each way). It runs `RibbitClaimStore` and his
`PostgresClaimStore` on the memory. With `WS_PG_DSN="host=... dbname=... user=... password=..."` in the environment
it also runs his store on that PostgreSQL as the control (`pip install psycopg2-binary`). `--ramp` raises the number
of claims in flight until a store breaks or the limit is reached.

## The browser page

    WS_RAM=HOST:PORT python3 -m uvicorn --factory sara_ui:standalone          # then open http://127.0.0.1:8000/ui

or, in SARA, in place of its `/ui`: `app.include_router(sara_ui.router(store))`. The page is one read held on the
memory: it shows where the RAM is served, the SARAs in it and every ticket as it stands, and it changes when a cell
is written. On a node leave `WS_RAM` unset.

## On FrogNet nodes

On one node, once (the port is chosen and kept in the app's configuration tuple):

    fdf-app config worldshepherd general --admin USER:PASSWORD

On every other node that may serve the RAM:

    fdf-app install worldshepherd --admin USER:PASSWORD

`fdf-app.service` on each node serves it when that node is elected. A SARA on any node:

    store = RibbitClaimStore()                                              # the memory model
    store = PostgresClaimStore(ticket_sql.connection_factory())             # SARA's own store, unmodified, on it

No address is configured anywhere: `fdf-app where worldshepherd general` prints where it is served now.

## The race across real machines

On every machine that takes part (add `--name Seattle7` to see the node in each line), with `RAM=` what
`fdf-app where worldshepherd general` prints:

    KARTRAM_LIB=$PWD/lib PYTHONPATH=$PWD/harness:$PWD:$PWD/lib:$SARA python3 harness/race.py claimant $RAM

Then on one machine:

    KARTRAM_LIB=$PWD/lib PYTHONPATH=$PWD/harness:$PWD:$PWD/lib:$SARA python3 harness/race.py controller $RAM --claimants N --tickets 400 --store memory

`--store sql` runs it on his `PostgresClaimStore` on the memory; `--store postgres` on a PostgreSQL (every participant
then needs `WS_PG_DSN`). `controller $RAM --end` tells the claimants to stop. The controller prints one JSON line with
`verdict` PASS or FAIL.
