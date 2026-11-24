# Audit: is anything message-based hiding under the memory? (2026-11-24, of 0.5; fixed in 0.6)

John, 2026-11-24: "Review the memory-based code and make sure it isn't hiding message-based shit under the covers.
No races. No locks. No copying shit around. All of it."

Every file of the example and the tier was read against `docs/CONVERTING-TO-RIBBIT.md` (sections 1, 4b, 7, 7b) and
`docs/PROGRAMMING-RIBBIT-BEST-PRACTICES.md` (sections 3b, 4, 6, 13). The test for each line: if this were a
multithreaded program on one machine, would I do this?

## Found in 0.5, all removed in 0.6

| # | what | where (0.5) | why it is messaging, a lock or a copy | 0.6 |
|---|---|---|---|---|
| 1 | `op=ticket`: a SARA sent `{"do":"claim",...}` and waited for the answer | `worldshepherd_ram.cpp` `operation()`, `ribbit_claim_store.py` `_ask()` | a request answered by a response, and a process whose job is to hold the state and answer operations: the first two red flags of the conversion method | gone. The RAM manager has no operations. A SARA writes its own claim cell and reads the ticket's cells |
| 2 | one ask at a time per ticket: 256 `ribbit::Ordered`, a ticket hashed to one | `worldshepherd_ram.cpp` `order_[]` | a queue that serializes callers is a lock. Two unrelated tickets on one stripe waited for each other | gone. The only order is the memory's write order in the ticket's family |
| 3 | `ribbit::defer_answer` / `answer_later` for every ask | `worldshepherd_ram.cpp` | machinery that exists only to deliver a reply | gone with 1 |
| 4 | `who` and `n` in every ask so the session would not coalesce two asks, and `coalesces("ticket") == false` | both files | a mechanism that exists only because the thing was a request | gone with 1. `who.n` now only names a SARA's own cell |
| 5 | a refused claim's state kept in a thread-local and handed to the next status query | `ticket_sql.py` `_Asker.stands` | a copy of the memory held between two statements | gone. The status query is a read |
| 6 | the region read a ticket, decided, then wrote (`do_register`, `do_claim`) | `worldshepherd_ram.cpp` | read-modify-write, safe only because of the lock in 2 | gone. Nothing reads then writes: each SARA writes once, unconditionally |
| 7 | a network write to `ticket.*` refused, because the cells were the region's | `worldshepherd_ram.cpp` `refuse_write` | followed from 1 | gone. Each cell is its writer's |
| 8 | the race's controller waited for the id of its instruction (`write_landed`) | `harness/race.py` | waiting on a write nothing reads back | posted. (`--end` still waits: the process ends its session right after, which is the one use `ribbit.py` names for it) |

How it got there: 0.1 to 0.3 were cells and write order, the shape section 4b of the method describes. They had two
real defects (a read answered by another thread's earlier identical read; ids not kept across an `fdf-ram` restart).
0.4 fixed both by moving the decision into a region operation. That fixed the symptoms by putting a server and a
queue back. 0.6 goes back to cells and fixes the two defects where they are (below).

## What 0.6 is, line by line

- **A claim**: post my cell `(worldshepherd, ticket.<id>, claim.<who>.<n>)`, once, unanswered. Read the family. If my
  cell is not in what I read, hold a read past the last id I saw. When my cell is there, every cell written before
  it is there too, and the winner (the lowest id among the claims that count) cannot change. A read is a read: the
  write that answers a held one is my own post or another SARA's.
- **No race**: nothing is decided by timing between two SARAs. Each derives the winner from the same cells by the
  same rule. The defect of 0.1 (a stale coalesced read) cannot give a wrong answer: a read without my cell in it is
  not used to decide anything, only to know where to hold from.
- **No lock**: none in `ribbit_claim_store.py`, `ticket_sql.py` or `worldshepherd_ram.cpp`. The file is appended with
  one `write()` a record on an `O_APPEND` descriptor.
- **No copy**: a store keeps nothing between calls but its own name and count. `ticket_sql.py` keeps nothing between
  statements. The file is the one second copy of anything, and it is the platform's Permanent-cell hook: what makes
  a ticket outlive the process.
- **Nobody writes another's cell.** No operation runs in the RAM manager. Nothing is acknowledged or retried.

## What is still there, and is not hidden

- **The read's bytes.** A claimant reads the whole family: 2,100 to 3,200 bytes a claim with 8 claimants, against
  about 240 for 0.4's one ask. That is the price of deriving from cells, measured in RESULTS.md.
- **A read is a round trip on the wire.** One a claim (107 ms across a 100 ms round trip), the same as 0.4's ask.
- **Brandon's `PostgresClaimStore`, unmodified, asks for the status after a refused claim**: two reads for a loser
  where `RibbitClaimStore` makes one. It is his code as written.
- **The session library** (`frogram`, through `kartram.cpp`) speaks FNW1 frames. That is the bus, not this example's
  code, and it was not changed.
- **After a restart**, two cells of one family that were in flight together can come back with their ids exchanged
  (the hook files before it publishes). A consumed ticket stays consumed and nothing claimed after the restart can
  win it; `status()` can then report the other of two racing claims' `at`. Stated in `ribbit_claim_store.py`'s
  header and in RESULTS.md. Closing it needs the platform to file a cell with its id.
- **A refused claim leaves its cell** (unknown ticket, wrong digest, expired). It never counts for any reader. The
  file grows until expired tickets are removed, which is not built.

## The rest of the directory
- `harness/on_the_memory.py`: puts a store on the memory behind the name Brandon's tests use. No logic of its own.
- `harness/race.py`: each claimant writes its own `can`, `won` and `result` cells and holds a read on `go`; the
  controller writes `go` and holds reads on `can` and `result`. No polling, no participant named to another.
- `simulation/worldshepherd_slow_tier.py`: starts processes and relays. It is the simulator, not the product.

## How to check this audit
`grep -nE "operation\(|Ordered|defer_answer|answer_later|mutex|lock|threading.local|op=ticket" worldshepherd_ram.cpp
ribbit_claim_store.py ticket_sql.py` finds two lines: a comment, and `resident_operation("restore", ...)`, which writes
the file back into the memory once at start, before any session. The behaviour is tested, not grepped:
Brandon's 31 tests for both stores, `harness/test_one_session_many_threads.py` for both, `harness/race.py` with the
winner rule reversed (fails, 400 of 400), and the restart check.
