# Results -- worldshepherd-ribbit 0.4 on worldshepherd-ram 0.4, 2026-11-24

FrogNet tree: branch `homa-under-croakos-20261116` (b938ac9), ribbit-platform 0.71. Incumbent: Sara_pro branch
`worldshepherd/connectors-distributed-claim-contract-v1-20260913` (52b3764), unmodified.

## Qualification (the contract holds)
| | stock | on worldshepherd-ram |
|---|---|---|
| the branch's five connector test files, unmodified | 31 passed | 31 passed, 20 runs of 20 (4 stores made on the memory each run) |
| the same run with no RAM manager named | -- | refuses to start |
| 8 separate processes, each its own session, 400 tickets, every process claims every ticket | Postgres: 400 won once, 0 twice, 0 never (3 runs) | 400 won once, 0 twice, 0 never (3 runs) |
| the same race against a RAM manager with the already-consumed check taken out on purpose | -- | FAIL: 400 of 400 won more than once |
| 16 threads of one process on one session, 300 tickets (`harness/test_one_session_many_threads.py`) | -- | every ticket won once (3 runs) |
| a ticket claimed, the RAM manager stopped and started (4,304 cells restored), the ticket claimed again | -- | still consumed; the new claim is refused; cells back in the order written |
| a write to a ticket's cell over the network | -- | refused, HTTP 403 |
| worldshepherd-ram started with no file named | -- | does not start |

## Characterization with no network between the parts -- ONE machine, a 2-core sandbox
Same driver (`harness/race.py`), 8 claimants, 400 tickets, 3,200 claims a run, three runs each, in one session.
Postgres 16.15 on the same machine, default settings, through Brandon's `PostgresClaimStore` as written (a new
connection per call).
| | register /s | claims /s (3 runs) | slowest claimant |
|---|---|---|---|
| stock, `PostgresClaimStore` | 68-83 | 83, 81, 75 | 38.8-42.5 s |
| 0.3: cells each SARA wrote, on `fdf-ram` | 896-1,358 | 4,111, 4,305, 4,894 | 0.65-0.78 s |
| 0.4: `worldshepherd-ram`, one ask per claim | 693-1,187 | 4,669, 5,098, 5,151 | 0.62-0.69 s |
Raw: `results/race-postgres.jsonl`, `results/race-memory.jsonl` (0.4).

Both sides are durable: Postgres with fsync on; worldshepherd-ram syncs each cell to its file before answering.
On one machine a round trip costs nothing, so 0.4's one ask against 0.3's two reads and a post barely shows. Across
machines each of those is a trip.

## In the simulator: every SARA on its own link (`simulation/worldshepherd_slow_tier.py`)
Both systems unmodified. Each SARA is its own process, and everything it says to the store goes through its own
shaped link (`simulation/slowlink.py`): its Postgres connections, or its one session to worldshepherd-ram. The
issuer is beside the store. 8 SARAs, 40 tickets, 320 claims a run, the same driver (`harness/race.py`) for both.
All 16 runs: every ticket won exactly once. Rows: `results/worldshepherd-slow-tier-20261124.jsonl`.

| links | claims at once per SARA | store | claims /s | a claim, median | worst 99th pct | bytes per claim | connections |
|---|---|---|---|---|---|---|---|
| lan (0.5 ms) | 1 | Postgres | 50.4 | 157.5 ms | 237 ms | 9,633 | 600 |
| | | memory | 2,743.8 | 2.3 ms | 11 ms | 236 | 24 |
| | 8 | Postgres | 57.0 | 1,142.8 ms | 1,886 ms | 9,633 | 600 |
| | | memory | 5,155.5 | 6.8 ms | 29 ms | 264 | 24 |
| wan-100ms (50 ms each way +-5) | 1 | Postgres | 4.5 | 1,827 ms | 1,938 ms | 9,625 | 600 |
| | | memory | 74.2 | 107 ms | 112 ms | 236 | 24 |
| | 8 | Postgres | 30.7 | 2,004 ms | 2,447 ms | 9,633 | 600 |
| | | memory | 580.3 | 108 ms | 113 ms | 328 | 24 |
| 4g (40 ms each way +-15, 10 Mb/s) | 1 | Postgres | 4.9 | 1,666 ms | 1,921 ms | 9,633 | 600 |
| | | memory | 80.6 | 99 ms | 113 ms | 235 | 24 |
| | 8 | Postgres | 35.0 | 1,759 ms | 2,116 ms | 9,633 | 600 |
| | | memory | 601.8 | 101 ms | 111 ms | 310 | 24 |
| mixed (lan, wifi, 4g, wan-100ms in turn) | 1 | Postgres | 4.4 | 1,662 ms | 1,868 ms | 9,629 | 600 |
| | | memory | 74.3 | 99 ms | 112 ms | 395 | 24 |
| | 8 | Postgres | 31.3 | 1,699 ms | 2,772 ms | 9,633 | 600 |
| | | memory | 579.9 | 101 ms | 114 ms | 421 | 24 |

Across a 100 ms round trip a claim on the memory takes one round trip (107 ms). The same claim through
`PostgresClaimStore` takes 1,827 ms, about seventeen: it opens a connection for the claim and, when it loses, another
to ask why. On the memory a SARA keeps three connections for its life (its session); Postgres took 600 for 320 claims.
With slow links the memory is 16 to 19 times the claims a second and the median claim is 17 times shorter.

What this is: the simulator's links, on one machine. Latency, jitter and rate are simulated; loss and outages were
not run. It is not a run on the nodes.

## Lines (code lines, comments and blanks out)
| | lines |
|---|---|
| original: `connector_ticket_shared.py` 125, `connector_ticket_sqlite.py` 143, `connector_ticket_postgres.py` 128 | 396, plus a Postgres server |
| previous (0.3): `ribbit_claim_store.py` | 73, on FD&F's RAM manager |
| now (0.4): `ribbit_claim_store.py` 44, `worldshepherd_ram.cpp` 96 | 140, and that is the server |

## Not claimed
- A run on real machines. The simulator's links are above; the race has not been run on the nodes (`README.md` gives the commands).
- Lossy links, outages, and a link cut during a claim: not run.
- That most of the Postgres gap is the memory: much of it is the adapter's connection per call. A pooled Postgres
  would be faster than these figures. It is measured as Brandon wrote it.
- Anything about ECHO, WS-SOE, the outbox or relay idempotency: not converted yet (DESIGN.md, "Not built yet").
- Behaviour across a split: a claimant that cannot reach worldshepherd-ram gets an error, and claims nothing.
- The file grows with every ticket; nothing removes expired tickets yet.

## Found on the way
- `fdf-ram` (fdf-2026-11-15-r54) brings Permanent cells back after a restart with new ids, not in their old order
  (a family written issue-then-claim came back claim 1, issue 2). worldshepherd-ram restores in the order written.
- The session library coalesces identical requests in flight, reads included. A read made right after one's own
  post can be answered with another thread's earlier read. 0.1 failed Brandon's concurrency test one run in six
  from this.
- Brandon's concurrency test (16 threads, 64 claims, one ticket) does not detect a wrong winner rule: it passed 0.1
  with the rule reversed. `harness/race.py` detects it.
- In the sandbox, `fdf-ram` refused a write with a large body: `[SEMTPL-TRAIN] learn FAILED: blob store: mkdir
  /opt/frognet_semantic/blob_cache: No such file or directory`. The sandbox has no `/opt/frognet_semantic`.
