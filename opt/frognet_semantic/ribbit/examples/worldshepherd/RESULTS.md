# Results -- worldshepherd-ribbit 0.6 on worldshepherd-ram 0.6, 2026-11-24

FrogNet tree: branch `homa-under-croakos-20261116` (b938ac9), ribbit-platform 0.71. Incumbent: Sara_pro branch
`worldshepherd/connectors-distributed-claim-contract-v1-20260913` (52b3764), unmodified.

## Qualification (the contract holds), 0.6
"Postgres store on the memory" is Brandon's `PostgresClaimStore`, unmodified, on `ticket_sql.py`: no PostgreSQL.

| | stock | RibbitClaimStore | Postgres store on the memory |
|---|---|---|---|
| the branch's five connector test files, unmodified | 31 passed | 31 passed, 20 runs of 20 | 31 passed, 20 runs of 20 |
| the same run with no RAM manager named | -- | refuses to start | refuses to start |
| 8 separate processes, each its own session, 400 tickets, every process claims every ticket | Postgres: 400 won once (3 runs) | 400 won once, 0 twice, 0 never | 400 won once, 0 twice, 0 never |
| the same race with the winner rule reversed on purpose (highest id wins) | -- | FAIL: 400 of 400 won more than once | FAIL: 400 of 400 |
| 16 threads of one process on one session, 300 tickets (`harness/test_one_session_many_threads.py`) | -- | every ticket won once (3 runs) | every ticket won once (3 runs) |
| a ticket claimed, the RAM manager stopped and started (47,763 cells restored), the ticket claimed again | -- | still consumed; the new claim is refused; cells back in the order filed | (the same cells) |
| worldshepherd-ram started with no file named | -- | does not start | |

## With no network between the parts -- one machine, a 2-core sandbox
8 claimants, 400 tickets, 3,200 claims a run (`harness/race.py`). PostgreSQL 16.15 on the same machine, default
settings, through `PostgresClaimStore` as written.
| | claims /s |
|---|---|
| stock, `PostgresClaimStore` on PostgreSQL (3 runs) | 83, 81, 75 |
| 0.3: cells each SARA wrote, on `fdf-ram` (3 runs) | 4,111, 4,305, 4,894 |
| 0.4: a region operation, since removed (3 runs) | 4,669, 5,098, 5,151 |
| 0.6: `RibbitClaimStore` (1 run) | 2,748 |
| 0.6: `PostgresClaimStore` on the memory (1 run) | 1,889 |
0.6 is slower here than 0.3 on the same design. The cause is not established: one difference is that
`worldshepherd-ram` does an `fdatasync` for every cell; how `fdf-ram` files its Permanent cells was not read.

## In the simulator: every SARA on its own link (`simulation/worldshepherd_slow_tier.py`), 0.6
All stores unmodified. Each SARA is its own process, and everything it says to the store goes through its own
shaped link (`simulation/slowlink.py`): its one session to worldshepherd-ram, or its Postgres connections. The issuer
is beside the store. 8 SARAs, 40 tickets, 320 claims a run, the same driver (`harness/race.py`) for all. "at once" is
how many claims each SARA makes at a time. All 24 runs: every ticket won exactly once.
Rows: `results/worldshepherd-slow-tier-0.6-20261124.jsonl`.

| links | at once | store | claims /s | a claim, median | worst 99th pct | bytes per claim | connections |
|---|---|---|---|---|---|---|---|
| lan (0.5 ms) | 1 | Postgres store on PostgreSQL | 74.4 | 107.6 ms | 171 ms | 9,633 | 600 |
| lan (0.5 ms) | 1 | Postgres store on the memory | 1,502.4 | 5.0 ms | 10 ms | 2,310 | 24 |
| lan (0.5 ms) | 1 | RibbitClaimStore | 2,091.3 | 3.4 ms | 9 ms | 2,136 | 24 |
| lan (0.5 ms) | 8 | Postgres store on PostgreSQL | 83.4 | 750.0 ms | 1,220 ms | 9,633 | 600 |
| lan (0.5 ms) | 8 | Postgres store on the memory | 1,999.9 | 23.4 ms | 62 ms | 2,901 | 24 |
| lan (0.5 ms) | 8 | RibbitClaimStore | 2,762.5 | 17.1 ms | 48 ms | 2,161 | 24 |
| wan-100ms | 1 | Postgres store on PostgreSQL | 4.6 | 1,815.5 ms | 1,936 ms | 9,633 | 600 |
| wan-100ms | 1 | Postgres store on the memory | 38.6 | 213.7 ms | 276 ms | 2,331 | 24 |
| wan-100ms | 1 | RibbitClaimStore | 72.7 | 107.8 ms | 214 ms | 2,164 | 24 |
| wan-100ms | 8 | Postgres store on PostgreSQL | 33.1 | 1,855.8 ms | 2,276 ms | 9,633 | 600 |
| wan-100ms | 8 | Postgres store on the memory | 290.7 | 217.0 ms | 227 ms | 3,160 | 24 |
| wan-100ms | 8 | RibbitClaimStore | 564.6 | 109.8 ms | 122 ms | 2,214 | 24 |
| 4g | 1 | Postgres store on PostgreSQL | 5.0 | 1,649.1 ms | 1,716 ms | 9,629 | 600 |
| 4g | 1 | Postgres store on the memory | 42.2 | 195.8 ms | 237 ms | 2,313 | 24 |
| 4g | 1 | RibbitClaimStore | 78.8 | 100.8 ms | 123 ms | 2,130 | 24 |
| 4g | 8 | Postgres store on PostgreSQL | 36.5 | 1,693.2 ms | 2,003 ms | 9,629 | 600 |
| 4g | 8 | Postgres store on the memory | 303.6 | 201.8 ms | 220 ms | 3,107 | 24 |
| 4g | 8 | RibbitClaimStore | 578.6 | 105.7 ms | 114 ms | 2,168 | 24 |
| mixed | 1 | Postgres store on PostgreSQL | 4.4 | 1,652.0 ms | 1,853 ms | 9,633 | 600 |
| mixed | 1 | Postgres store on the memory | 37.2 | 197.6 ms | 223 ms | 2,271 | 24 |
| mixed | 1 | RibbitClaimStore | 74.0 | 102.5 ms | 112 ms | 2,131 | 24 |
| mixed | 8 | Postgres store on PostgreSQL | 32.9 | 1,674.2 ms | 2,396 ms | 9,633 | 600 |
| mixed | 8 | Postgres store on the memory | 288.3 | 201.5 ms | 237 ms | 2,815 | 24 |
| mixed | 8 | RibbitClaimStore | 554.1 | 106.6 ms | 137 ms | 2,181 | 24 |

Across a 100 ms round trip a claim through `RibbitClaimStore` takes one round trip (108 ms). Through Brandon's
store on PostgreSQL it takes 1,816 ms, about seventeen: it opens a connection for the claim and, when it loses,
another to ask why. His same store on the memory takes two (214 ms): the claim, and his status query after a refusal.
A SARA keeps three connections for its life (its session); PostgreSQL took 600 for 320 claims.
Over the slow links `RibbitClaimStore` is 16 to 17 times the claims a second of his store on PostgreSQL, and his own
store moved onto the memory is 8 to 9 times.

Against 0.4 (a region operation each SARA asked and waited on; removed, see `AUDIT-NO-HIDDEN-MESSAGING.md`; rows in
`results/worldshepherd-slow-tier-0.4-20261124.jsonl`): the same one round trip a claim over slow links (107 ms then,
108 ms now); 2,100 to 3,200 bytes a claim now against 236 to 421, because a claimant reads the ticket's cells; on the
LAN 2,091 claims a second now against 2,744.

What this is: the simulator's links, on one machine. Latency, jitter and rate are simulated; loss and outages were
not run. It is not a run on the nodes.

## Lines (code lines, comments and blanks out)
| | lines |
|---|---|
| original: `connector_ticket_shared.py` 125, `connector_ticket_sqlite.py` 143, `connector_ticket_postgres.py` 128 | 396, plus a PostgreSQL server |
| previous (0.4): `ribbit_claim_store.py` 44, `worldshepherd_ram.cpp` 96 | 140 |
| now (0.6): `ribbit_claim_store.py` 74, `worldshepherd_ram.cpp` 44 | 118, and no server logic |
| now, to run his Postgres store as written: `ticket_sql.py` | 45 more |

## Not claimed
- A run on real machines. The simulator's links are above; the race has not been run on the nodes (`README.md` gives the commands).
- Lossy links, outages, and a link cut during a claim: not run.
- That most of the Postgres gap is the memory: much of it is the adapter's connection per call. A pooled Postgres
  would be faster than these figures. It is measured as Brandon wrote it.
- Anything about ECHO, WS-SOE, the outbox or relay idempotency: not converted yet (DESIGN.md, "Not built yet").
- Behaviour across a split: a claimant that cannot reach worldshepherd-ram gets an error, and claims nothing.
- The file grows with every ticket and every refused claim; nothing removes expired tickets yet.
- Exact order across a restart for two cells in flight together (`ribbit_claim_store.py`'s header says what that can change).

## Found on the way
- `fdf-ram` (fdf-2026-11-15-r54) brings Permanent cells back after a restart with new ids, not in their old order
  (a family written issue-then-claim came back claim 1, issue 2). worldshepherd-ram restores in the order filed.
- The session library coalesces identical requests in flight, reads included. A read made right after one's own
  post can be answered with another thread's earlier read. 0.1 failed Brandon's concurrency test one run in six
  from this.
- Brandon's concurrency test (16 threads, 64 claims, one ticket) does not detect a wrong winner rule: it passed 0.1
  with the rule reversed. `harness/race.py` detects it.
- In the sandbox, `fdf-ram` refused a write with a large body: `[SEMTPL-TRAIN] learn FAILED: blob store: mkdir
  /opt/frognet_semantic/blob_cache: No such file or directory`. The sandbox has no `/opt/frognet_semantic`.
