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

## Characterization -- ONE machine, a 2-core sandbox. This is not the comparison that counts.
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

## Lines (code lines, comments and blanks out)
| | lines |
|---|---|
| original: `connector_ticket_shared.py` 125, `connector_ticket_sqlite.py` 143, `connector_ticket_postgres.py` 128 | 396, plus a Postgres server |
| previous (0.3): `ribbit_claim_store.py` | 73, on FD&F's RAM manager |
| now (0.4): `ribbit_claim_store.py` 44, `worldshepherd_ram.cpp` 96 | 140, and that is the server |

## Not claimed
- Any multi-machine number. The race has not been run across machines; `README.md` gives the commands.
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
