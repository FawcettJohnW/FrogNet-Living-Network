# WorldShepherd SARA on the memory -- design (0.4, 2026-11-24)

Incumbent: Brandon Ray Davis's SARA, `github.com/Bdavis1390/Sara_pro`.
- `main` (234ef0c, 7 Oct 2026): `deployments/sara_verified_local_v1`, three services on one machine. No network code.
- branch `worldshepherd/connectors-distributed-claim-contract-v1-20260913` (52b3764): the connector read-ticket
  ledger and its `SharedClaimStore` contract. This is the part meant to span machines, and its own text says it does
  not yet: "distributed replay protection is not claimed until it is validated against a live shared Postgres
  deployment with multiple SARA workers" (`connector_ticket_postgres.py`).

Contract kept: `SharedClaimStore` (`register`, `claim`, `status`), `SharedReadTicketLedger`, `ReadExecutionBroker`,
the ticket format, the reason strings, and the branch's five connector test files (31 tests), unmodified.

## What a ticket is, in SARA's terms
A SARA issues a read ticket (single use, 5-300 s). A host that is about to execute it claims it. Exactly one claim
may succeed, wherever the claimants are. The stock stores make that one claim with a lock (`ThreadSafeSharedClaimStore`),
a `BEGIN IMMEDIATE` transaction (`SqliteReadTicketLedger`) or `UPDATE ... WHERE consumed_at IS NULL` (`PostgresClaimStore`).

## WorldShepherd has its own RAM manager
`worldshepherd-ram` (`worldshepherd_ram.cpp`): the Ribbit platform's RAM host (`ribbit::RamHost`, `ribbit/cpp`) with
WorldShepherd's region compiled in, answering `/worldshepherd-api`. It is the one owner of every ticket, the way
the Redis region owns its keyspace (`docs/CONVERTING-TO-RIBBIT.md` section 7b). A SARA never writes a ticket: it
asks, and its ask is the RAM call.

## The truth table
| truth | owner (its one writer) | address | latest / distinct | readers | trigger | derived instead |
|---|---|---|---|---|---|---|
| a ticket was issued: its digest and expiry | the region, for the issuing SARA's ask | `(worldshepherd, ticket.<id>, issue)` | written once | the region (every claim), `status` | none | -- |
| the ticket is consumed, and when | the region, for the first valid claim | `(worldshepherd, ticket.<id>, claim)` | written once | the region (every later claim), `status` | none | `consumed`: the cell is there. Who lost: nothing is kept; a loser is told on its own session |

A network write to `ticket.*` is refused: those cells are the region's.

## The ask
`op=ticket`, on the SARA's one permanent session, answered on that session:

    {"do":"register","id":ID,"sha":SHA,"exp":T,"who":W,"n":N}  -> {"ok":true} | {"ok":false,"reason":"duplicate ticket_id"}
    {"do":"claim","id":ID,"sha":SHA,"now":T,"who":W,"n":N}     -> {"ok":true,"consumed_at":T} | {"ok":false,"reason":...}

- **One ask at a time per ticket** (`[ONE_ASK_AT_A_TIME_PER_TICKET_V1]`): the session's thread hands the ask to the
  ticket's `ribbit::Ordered` and reads its next frame; the ask runs on the host's adaptive pool and its asker is
  answered from there (`ribbit::answer_later`). Asks for different tickets run in parallel. There is no lock, no
  transaction and no compare-and-set: within one ticket there is one order, and the first valid claim writes the cell.
- **Every ask is its own request**: it carries the asking store's name and count (`who`, `n`). The session answers an
  identical request in flight with that request's answer (`[CLIENT_COALESCES_V1]`); two threads claiming one ticket
  with the same `now` would otherwise both be told they won. The host does not run two asks as one either
  (`coalesces("ticket")` is false).
- **Status is a read** of the ticket's two cells. Nothing is asked.
- **Tickets are filed** (`[TICKETS_ARE_FILED_V1]`): each cell is appended to `WORLDSHEPHERD_RAM_FILE` and synced
  before it is written into the memory and before its asker is answered; at start the file is read back in the
  order written. The host does not start without the file named, or on a file with a damaged line.

## What is gone
Brandon's side: the lock, the transaction, the compare-and-set statement, the "lost the race" re-query, the
connection per call, the table and its schema, and the database server.
This conversion's own 0.1-0.3 (tickets as cells each SARA wrote on FD&F's RAM manager, winner by write order): the
two reads and a post per claim, the claimant's confirming read, and the losers' withdrawals. RESULTS.md has both.

## Not built yet (next, in this order)
1. SARA's events to ECHO as cells ECHO holds a read on (`event_outbox.py`'s PENDING/DELIVERED, the drain and
   `/v1/reconcile` go). On `main`, nothing in the package carries an event from SARA to ECHO: the integration script
   does it with curl.
2. WS-SOE v0.1C: the intent as the coordinator's cell and the effect as the service's cell.
3. Relay idempotency (branch `hardening/relay-idempotency-v1`), the same shape as the ticket claim.
4. Removing expired tickets' cells and compacting the file: the region's job, on its schedule.
5. `worldshepherd-ram` as a FrogNet service: its capability tuple, its port in its configuration tuple, and
   selection of its host. Today it is started by hand and named by HOST:PORT.
