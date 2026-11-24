# WorldShepherd SARA on the memory

Brandon Ray Davis's SARA (`github.com/Bdavis1390/Sara_pro`) has one part meant to work across machines: the
connector read-ticket ledger, where a ticket issued by one SARA must be claimed exactly once by one host. This
directory is that ledger on FrogNet's shared memory, with WorldShepherd's own RAM manager. Read `DESIGN.md` first,
then `RESULTS.md`.

| file | |
|---|---|
| `worldshepherd_ram.cpp` | `worldshepherd-ram`: the RAM manager. No operations and no file |
| `ribbit_claim_store.py` | `RibbitClaimStore`, a `SharedClaimStore`: a ticket is its cells; a SARA writes its own and reads, and writes its own again when the memory is another one |
| `ticket_sql.py` | Brandon's `PostgresClaimStore`, unmodified, with no PostgreSQL: its four SQL calls done as those cells |
| `AUDIT-NO-HIDDEN-MESSAGING.md` | the review of this code for messaging, locks and copies under the memory, and what it removed |
| `harness/on_the_memory.py` | runs Brandon's own tests, unmodified, against worldshepherd-ram (`WS_STORE=sql` for his Postgres store on it) |
| `harness/test_one_session_many_threads.py` | 16 threads of one process on one session, 300 tickets |
| `harness/test_the_memory_is_another_one.py` | the RAM manager killed and an empty one started: what each SARA reasserts |
| `harness/race.py` | many SARAs on many machines claiming the same tickets at once, coordinated through the memory |
| `../../../simulation/worldshepherd_slow_tier.py` | the simulator tier: the race with every SARA on its own shaped link, against the Postgres store |
| `Makefile` | `make`: `bin/worldshepherd-ram`, `lib/libkartram.so` (the session library), `lib/ribbit.py`. Incremental |
| `DESIGN.md`, `study-ticket-claims.md`, `WORLDSHEPHERD-SUITE-MAP.md`, `QUESTION-3.md`, `RESULTS.md`, `VERSION` | the conversion's records |

Needs: g++ (C++20), liblz4-dev, libssl-dev; Python 3.10+; and, for the tests, the Sara_pro branch
`worldshepherd/connectors-distributed-claim-contract-v1-20260913` checked out (`SARA=` its directory).

Build, on each machine, from this directory:

    make

The RAM manager, on one machine every SARA can reach (it says its build when it starts). Under systemd, give it a
socket unit for that port: its listening socket then outlives it, and a SARA's connect made while it is being started
again waits there.

    bin/worldshepherd-ram --listen 0.0.0.0:PORT

Everything below names it as `RAM=host:PORT`.

Brandon's tests on the memory:

    cd $SARA && KARTRAM_LIB=$OLDPWD/lib PYTHONPATH=$OLDPWD/harness:$OLDPWD:$OLDPWD/lib:. WS_RAM=$RAM python3 -m pytest -p on_the_memory -q tests/test_connector_ticket_shared.py tests/test_connector_ticket_lifecycle.py tests/test_connector_control.py

In the simulator, every SARA on its own shaped link (from `opt/frognet_semantic`; it says what is missing before it
runs). It runs `RibbitClaimStore` and Brandon's Postgres store on the memory, and needs no PostgreSQL:

    python3 simulation/worldshepherd_slow_tier.py --sara $SARA

With `WS_PG_DSN="host=... dbname=... user=... password=..."` in the environment it also runs his store on that
PostgreSQL, as the control (`pip install psycopg2-binary`).

The race, across machines. On every machine that takes part, from this directory (as many times as you want
claimants; add `--name Seattle7` to see the node in each line):

    KARTRAM_LIB=$PWD/lib PYTHONPATH=$PWD/harness:$PWD:$PWD/lib:$SARA python3 harness/race.py claimant $RAM

Then on one machine:

    KARTRAM_LIB=$PWD/lib PYTHONPATH=$PWD/harness:$PWD:$PWD/lib:$SARA python3 harness/race.py controller $RAM --claimants N --tickets 400 --store memory

`--store sql` runs it on Brandon's `PostgresClaimStore` on the memory. `--store postgres` runs that store on a
PostgreSQL; every participant then needs `WS_PG_DSN`
in its own environment (and `pip install psycopg2-binary`). `controller $RAM --end` tells the claimants to stop.
The controller prints one JSON line with `verdict` PASS or FAIL; each claimant prints its build.
