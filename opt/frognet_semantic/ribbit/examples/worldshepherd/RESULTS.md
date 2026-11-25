# Results -- worldshepherd-ribbit 0.9, its RAM served by the app's own RAM host (worldshepherd-ram 0.9, fdf-app r55), 2026-11-25

## 0.9: the app's RAM host and the acceptance study
`ACCEPTANCE.md` has the study: every case, both links, and what it found. What else was run on 0.9, all on `worldshepherd-ram`:

| check | result |
|---|---|
| Brandon's `tests/test_connector_*.py`, unmodified, `RibbitClaimStore` (`harness/run_his_tests.py`) | 31 passed |
| the same, his `PostgresClaimStore` on `ticket_sql.py` | 31 passed |
| one session, 16 threads, 300 tickets, both stores | 2 passed |
| the race, 8 processes, 400 tickets, no network: `RibbitClaimStore` | every ticket won once; 3,785 claims/s, claim median 1.65 ms (0.8, on `fdf-ram` with no rules: 4,642 and 1.5) |
| the same race, his store on the memory | every ticket won once; 2,279 claims/s, claim median 2.92 ms |
| simulator, lan, 8 SARAs, 8 claims at once each: `RibbitClaimStore` / his store on the memory | PASS; 3,451 / 1,736 claims/s; claim median 14.1 / 26.8 ms |
| simulator, wan-100ms, 8 SARAs, 1 claim at once each: `RibbitClaimStore` / his store on the memory | PASS; claim median 107.4 / 213.6 ms; 74.0 / 38.2 claims/s |
| simulator, wan-100ms, 8 SARAs, 8 claims at once each | PASS; claim median 109.7 / 218.7 ms; 455 / 227 claims/s |
| the service, `harness/test_the_apphost_moves.py`, with `fdf-app config --host general=worldshepherd-ram` | 6 checks pass (`results/apphost-moves-0.9.log`): the RAM on the new AppHost 4.7 s after the old one stopped; 80 claims in flight across the move and 80 across a killed RAM host, all answered, 40 tickets one winner each |
| `fdf-app`'s own test, `rinab/tools/test_fdf_app.py`, on r55 | 13 checks pass |
| the acceptance study, lan and wan-100ms, 5 repetitions (`results/acceptance-0.9-20261125.jsonl`) | PASS on both; no ticket won twice, none lost, no call raised; one case OPEN and one FINDING (`ACCEPTANCE.md`) |

Not run again on 0.9: the full simulator matrix, the PostgreSQL control and the ramp. Their 0.7 numbers are below.

## 0.8: the RAM is a service; on failover a SARA reasserts, learns the world again, and proceeds
Measured 2026-11-24 on `fdf-ram` (fdf-2026-11-15-r54, fdf-app r54), before the app had a RAM host of its own.
Sections below this one were measured on 0.7 with its own RAM manager (`worldshepherd-ram`), which 0.8 removed. What
was run again on 0.8, all on `fdf-ram`:

| check | result |
|---|---|
| Brandon's `tests/test_connector_*.py`, unmodified, `RibbitClaimStore` (`harness/run_his_tests.py`) | 31 passed |
| the same, his `PostgresClaimStore` on `ticket_sql.py` | 31 passed |
| one session, 16 threads, 300 tickets, both stores | 2 passed |
| the race, 8 processes, 400 tickets, no network: `RibbitClaimStore` | every ticket won once; 4,642 claims/s, claim median 1.5 ms |
| the race, the same: his `PostgresClaimStore` on the memory | every ticket won once; 2,977 claims/s, claim median 2.6 ms |
| simulator, lan, 8 SARAs: `RibbitClaimStore` / his store on the memory | PASS; claim median 3.3 ms / 5.5 ms; 2,069 / 1,254 claims/s |
| simulator, wan-100ms, 8 SARAs: `RibbitClaimStore` / his store on the memory | PASS; claim median 107 ms / 213 ms; 74 / 38 claims/s (0.7: 107 / 213) |
| `rinab/tools/test_fdf_app.py` with `fdf-app` r54 | PASS, 13 checks, as r53 |

The service (`harness/test_the_apphost_moves.py`, 4 runs, all PASS; log of one in `results/apphost-moves-0.8.log`):

| check | result |
|---|---|
| 1 `fdf-app config worldshepherd general`; installed and served on 3 machines | one configuration tuple; `worldshepherdHost.frognet` elected, `fdf-ram` answering about 300 ms after |
| 3 the serving machine's `fdf-app` stopped | the AppHost on another machine and all 3 SARAs on its memory about 4.75 s after the stop (`fdf-app`'s stop and the election; the SARAs follow in under 50 ms) |
| 4 after | the ticket consumed before is refused as consumed by all 3 SARAs; the ticket issued before is claimed once |
| 5 80 claims in flight across that move | 80 answered, 0 raised; 40 tickets, one winner each |
| 6 the new AppHost's `fdf-ram` killed, 80 claims in flight | all 3 SARAs on the memory `fdf-app` started 40 to 80 ms after; 80 answered, 0 raised; 40 tickets, one winner each |

Before `[THEN_LEARN_THE_WORLD_AGAIN_V1]` (a SARA proceeding as soon as it had reasserted its own), checks 5 and 6
failed: 1 ticket of 40 was refused to both claimants as not registered, its issuer not yet having written it.

Not run again on 0.8: the full simulator matrix (24 runs), the PostgreSQL control, and the ramp. Their 0.7 numbers
are below; the store's claim path is unchanged and the two deployments run again match.


FrogNet tree: branch `homa-under-croakos-20261116` (b938ac9), ribbit-platform 0.71. Incumbent: Sara_pro branch
`worldshepherd/connectors-distributed-claim-contract-v1-20260913` (52b3764), unmodified.

## Qualification (the contract holds), 0.7
"Postgres store on the memory" is Brandon's `PostgresClaimStore`, unmodified, on `ticket_sql.py`: no PostgreSQL.

| | stock | RibbitClaimStore | Postgres store on the memory |
|---|---|---|---|
| the branch's five connector test files, unmodified | 31 passed | 31 passed, 20 runs of 20 | 31 passed, 20 runs of 20 |
| the same run with no RAM manager named | -- | refuses to start | refuses to start |
| 8 separate processes, each its own session, 400 tickets, every process claims every ticket | Postgres: 400 won once (3 runs) | 400 won once, 0 twice, 0 never | 400 won once, 0 twice, 0 never |
| the same race with the winner rule reversed on purpose (highest id wins) | -- | FAIL: 400 of 400 won more than once | FAIL: 400 of 400 |
| 16 threads of one process on one session, 300 tickets (`harness/test_one_session_many_threads.py`) | -- | every ticket won once (3 runs) | every ticket won once (3 runs) |
| the RAM manager killed and an empty one started on the same socket (`harness/test_the_memory_is_another_one.py`): each SARA writes its own truth again | -- | the issuer wrote its 2 tickets, the winner its 1 claim, the loser nothing; the consumed ticket is consumed and refuses a new claim; the ticket issued before and not yet claimed is claimed, once (3 runs) | the same (3 runs) |
| a claim that won, and its ticket, written into a memory in every order | -- | the won claim stands in all five orders | (the same derivation) |

## With no network between the parts -- one machine, a 2-core sandbox
8 claimants, 400 tickets, 3,200 claims a run (`harness/race.py`). PostgreSQL 16.15 on the same machine, default
settings, through `PostgresClaimStore` as written.
| | claims /s |
|---|---|
| stock, `PostgresClaimStore` on PostgreSQL (3 runs) | 83, 81, 75 |
| 0.3: cells each SARA wrote, on `fdf-ram` (3 runs) | 4,111, 4,305, 4,894 |
| 0.4: a region operation, since removed (3 runs) | 4,669, 5,098, 5,151 |
| 0.6: `RibbitClaimStore`, every cell synced to a file (1 run) | 2,748 |
| 0.7: `RibbitClaimStore`, no file (1 run) | 4,120 |
| 0.7: `PostgresClaimStore` on the memory (1 run) | 2,394 |

## In the simulator: every SARA on its own link (`simulation/worldshepherd_slow_tier.py`), 0.7
All stores unmodified. Each SARA is its own process, and everything it says to the store goes through its own
shaped link (`simulation/slowlink.py`): its one session to worldshepherd-ram, or its Postgres connections. The issuer
is beside the store. 8 SARAs, 40 tickets, 320 claims a run, the same driver (`harness/race.py`) for all. "at once" is
how many claims each SARA makes at a time. All 24 runs: every ticket won exactly once.
Rows: `results/worldshepherd-slow-tier-0.7-20261124.jsonl`.

| links | at once | store | claims /s | a claim, median | worst 99th pct | bytes per claim | connections |
|---|---|---|---|---|---|---|---|
| lan (0.5 ms) | 1 | Postgres store on PostgreSQL | 76.3 | 104.1 ms | 156 ms | 9,633 | 600 |
| lan (0.5 ms) | 1 | Postgres store on the memory | 1,366.6 | 5.5 ms | 13 ms | 2,345 | 24 |
| lan (0.5 ms) | 1 | RibbitClaimStore | 2,612.0 | 2.5 ms | 13 ms | 2,158 | 24 |
| lan (0.5 ms) | 8 | Postgres store on PostgreSQL | 82.0 | 765.3 ms | 1,191 ms | 9,633 | 600 |
| lan (0.5 ms) | 8 | Postgres store on the memory | 2,443.7 | 20.3 ms | 56 ms | 3,051 | 24 |
| lan (0.5 ms) | 8 | RibbitClaimStore | 3,802.2 | 12.7 ms | 32 ms | 2,209 | 24 |
| wan-100ms | 1 | Postgres store on PostgreSQL | 4.5 | 1,818.8 ms | 1,923 ms | 9,629 | 600 |
| wan-100ms | 1 | Postgres store on the memory | 38.4 | 212.8 ms | 221 ms | 2,369 | 24 |
| wan-100ms | 1 | RibbitClaimStore | 74.4 | 107.5 ms | 111 ms | 2,196 | 24 |
| wan-100ms | 8 | Postgres store on PostgreSQL | 33.0 | 1,857.5 ms | 2,247 ms | 9,633 | 600 |
| wan-100ms | 8 | Postgres store on the memory | 291.7 | 216.3 ms | 223 ms | 3,202 | 24 |
| wan-100ms | 8 | RibbitClaimStore | 573.7 | 109.0 ms | 118 ms | 2,236 | 24 |
| 4g | 1 | Postgres store on PostgreSQL | 5.1 | 1,651.0 ms | 1,741 ms | 9,633 | 600 |
| 4g | 1 | Postgres store on the memory | 41.7 | 194.4 ms | 221 ms | 2,363 | 24 |
| 4g | 1 | RibbitClaimStore | 79.7 | 99.8 ms | 111 ms | 2,157 | 24 |
| 4g | 8 | Postgres store on PostgreSQL | 36.0 | 1,710.5 ms | 2,034 ms | 9,633 | 600 |
| 4g | 8 | Postgres store on the memory | 303.7 | 203.7 ms | 219 ms | 3,093 | 24 |
| 4g | 8 | RibbitClaimStore | 586.9 | 106.2 ms | 111 ms | 2,202 | 24 |
| mixed | 1 | Postgres store on PostgreSQL | 4.4 | 1,647.8 ms | 1,849 ms | 9,629 | 600 |
| mixed | 1 | Postgres store on the memory | 37.4 | 192.7 ms | 221 ms | 2,316 | 24 |
| mixed | 1 | RibbitClaimStore | 74.3 | 99.9 ms | 111 ms | 2,169 | 24 |
| mixed | 8 | Postgres store on PostgreSQL | 32.9 | 1,672.9 ms | 2,399 ms | 9,633 | 600 |
| mixed | 8 | Postgres store on the memory | 292.4 | 200.9 ms | 224 ms | 2,639 | 24 |
| mixed | 8 | RibbitClaimStore | 581.0 | 103.3 ms | 112 ms | 2,204 | 24 |

Across a 100 ms round trip a claim through `RibbitClaimStore` takes one round trip (107 ms). Through Brandon's
store on PostgreSQL it takes 1,819 ms, about seventeen: it opens a connection for the claim and, when it loses,
another to ask why. His same store on the memory takes two (213 ms): the claim, and his status query after a refusal.
A SARA keeps three connections for its life (its session); PostgreSQL took 600 for 320 claims.
Over the slow links `RibbitClaimStore` is 16 to 17 times the claims a second of his store on PostgreSQL, and his own
store moved onto the memory is 8 to 9 times.

Against 0.4 (a region operation each SARA asked and waited on; removed, see `AUDIT-NO-HIDDEN-MESSAGING.md`; rows in
`results/worldshepherd-slow-tier-0.4-20261124.jsonl`): the same one round trip a claim over slow links (107 ms then
and now); 2,150 to 3,200 bytes a claim now against 236 to 421, because a claimant reads the ticket's cells; on the
LAN 2,612 claims a second now against 2,744.

What this is: the simulator's links, on one machine. Latency, jitter and rate are simulated; loss and outages were
not run. It is not a run on the nodes.

## How many claims at once: the ramp (`worldshepherd_slow_tier.py --ramp`), 0.7
Every SARA across a 100 ms round trip (wan-100ms), 8 SARAs, each making more and more claims at once on its one
session; at least two tickets for every claiming thread, every SARA claiming every ticket. A store that does not
finish a level is BROKE there, with what it said. Every finished run: every ticket won exactly once.
Rows: `results/worldshepherd-ramp-wan-0.7-20261124.jsonl`.

| claims in flight (8 SARAs x at once) | his store on PostgreSQL | his store on the memory | RibbitClaimStore |
|---|---|---|---|
| 64 | 33 /s, median 1,854 ms | 292 /s, median 217 ms | 567 /s, median 110 ms |
| 96 | 41 /s, median 1,927 ms | 362 /s, median 216 ms | 719 /s, median 110 ms |
| 128 | BROKE: PostgreSQL said "sorry, too many clients already" | 472 /s, median 220 ms | 921 /s, median 111 ms |
| 256 | not run (broke at 128) | 1,059 /s, median 224 ms | 1,814 /s, median 120 ms |
| 512 | not run (broke at 128) | 1,876 /s, median 229 ms | 3,172 /s, median 119 ms |
| 1,024 | not run (broke at 128) | 2,259 /s, median 337 ms | 3,708 /s, median 180 ms |
| 2,048 | not run (broke at 128) | 2,942 /s, median 526 ms | 4,279 /s, median 296 ms |
| 4,096 | not run (broke at 128) | 2,913 /s, median 982 ms | 5,266 /s, median 501 ms |
| 8,192 | not run (broke at 128) | 3,152 /s, median 1,787 ms | 5,224 /s, median 682 ms |
| 16,384 | not run (broke at 128) | 2,868 /s, median 3,636 ms | 5,515 /s, median 922 ms |
| 32,768 | not run (broke at 128) | 2,204 /s, median 9,635 ms | 4,636 /s, median 2,332 ms |

- His store on PostgreSQL carried 96 claims at once and broke at 128: it opens a connection per call and PostgreSQL's
  default `max_connections` is 100.
- Both stores on the memory finished every level up to 32,768 claims in flight (8 SARAs x 4,096 threads), still on 24
  connections, with no ticket won twice or never. No level broke them; the ramp stopped there.
- Their claims a second stop rising at about 5,300 (RibbitClaimStore) between 4,096 and 16,384 in flight, and a
  claim's median time rises from 110 ms to seconds. Everything in this run shares one 2-core machine: 8 Python
  processes of up to 4,096 threads, 8 Python relays and the RAM manager. Which of them sets that ceiling was not
  measured.

## Lines (code lines, comments and blanks out)
| | lines |
|---|---|
| original: `connector_ticket_shared.py` 125, `connector_ticket_sqlite.py` 143, `connector_ticket_postgres.py` 128 | 396, plus a PostgreSQL server |
| previous (0.4): `ribbit_claim_store.py` 44, `worldshepherd_ram.cpp` 96 | 140 |
| 0.6: `ribbit_claim_store.py` 74, `worldshepherd_ram.cpp` 44 (a file) | 118 |
| now (0.7): `ribbit_claim_store.py` 111, `worldshepherd_ram.cpp` 9 | 120: no server logic and no file; 37 of the store's lines are a SARA keeping and reasserting its own truth |
| 0.8: `ribbit_claim_store.py` 193, no C++ (the RAM is the platform's `fdf-ram`) | 193: about half is finding the RAM, reasserting and learning the SARAs after it moves |
| now, to run his Postgres store as written: `ticket_sql.py` | 49 more |
| the browser page: `sara_ui.py` | 105 |

## Not claimed
- A run on real machines. The simulator's links are above; the race has not been run on the nodes (`README.md` gives the commands).
- Lossy links, outages, and a link cut during a claim: not run.
- That most of the Postgres gap is the memory: much of it is the adapter's connection per call. A pooled Postgres
  would be faster than these figures. It is measured as Brandon wrote it.
- Anything about ECHO, WS-SOE, the outbox or relay idempotency: not converted yet (DESIGN.md, "Not built yet").
- After the memory is another one: between a consumed ticket's issuer reasserting it and its winner reasserting the
  claim, the ticket reads as not consumed, and a claim made then wins it. Not closed.
- A RAM manager that comes back at another address: a SARA attaches to the address it was given. Finding it where
  the role now is needs worldshepherd-ram to be a FrogNet service (DESIGN.md, "Not built yet").
- A SARA never forgets a ticket it issued or won: what it would reassert grows for as long as it runs.
- Cells of expired tickets and refused claims stay in the memory for its life; nothing removes them yet.

## Found on the way
- `fdf-ram` (fdf-2026-11-15-r54) brings Permanent cells back after a restart with new ids, not in their old order
  (a family written issue-then-claim came back claim 1, issue 2). worldshepherd-ram keeps no file: 0.7.
- The session library coalesces identical requests in flight, reads included. A read made right after one's own
  post can be answered with another thread's earlier read. 0.1 failed Brandon's concurrency test one run in six
  from this.
- Brandon's concurrency test (16 threads, 64 claims, one ticket) does not detect a wrong winner rule: it passed 0.1
  with the rule reversed. `harness/race.py` detects it.
- In the sandbox, `fdf-ram` refused a write with a large body: `[SEMTPL-TRAIN] learn FAILED: blob store: mkdir
  /opt/frognet_semantic/blob_cache: No such file or directory`. The sandbox has no `/opt/frognet_semantic`.
