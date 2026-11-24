# WorldShepherd SARA on the memory -- design (0.7, 2026-11-24)

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

## WorldShepherd has its own RAM manager, and it has no operations
`worldshepherd-ram` (`worldshepherd_ram.cpp`): the Ribbit platform's RAM host (`ribbit::RamHost`, `ribbit/cpp`),
answering `/worldshepherd-api`. Like `chat-ram`, it adds no logic: a ticket is cells SARAs write and read. It keeps
no file. A cell stays for the life of the memory, not of its writer's session: a ticket a SARA consumed is still
consumed after that SARA has gone.

## The truth table
One family per ticket: service `worldshepherd`, variable `ticket.<ticket_id>`.

| truth | owner (its one writer) | instance | latest / distinct | readers | trigger | derived instead |
|---|---|---|---|---|---|---|
| a ticket was issued: its digest and expiry | the issuing SARA | `issue.<who>.<n>` | distinct, written once | every claimant, `status` | none | which issue is the ticket: the lowest id. A later issue of the same id is a duplicate, and its writer reads that |
| a SARA claims the ticket: the digest it holds, and its `now` | the claiming SARA | `claim.<who>.<n>` | distinct, written once | every claimant of that ticket, `status` | a claimant's held read past the last id it saw, until its own cell is there | the winner: the claim that says `won` (a claim its writer wrote again, below); otherwise the lowest id among the claims written after the ticket, with its digest, not past its expiry. `consumed`, `consumed_at` |

`<who>` is the store's own name plus a random token made when it opens, `<n>` its own count: no two SARAs can write
the same cell, nothing is claimed from anything shared, nobody writes in another's space. The id is the memory's
write order in the family: the one place every write to the ticket passes (method, section 4b).

## A claim
Post my claim cell, once, unanswered. Read the family. If my cell is not in what I read, hold a read past the last
id I saw, until it is (`[I_READ_UNTIL_MY_OWN_CELL_IS_THERE_V1]`). Then every cell written before mine is in what I
hold, and I derive: no ticket, wrong digest, expired, already consumed, or mine. Registering is the same with an
issue cell. Status is a read. Nothing is asked of anyone, and no cell is written conditionally.

Why the hold: the session answers a read with an identical read already in flight, which can predate my post. That
read is not wrong, it just does not hold my cell yet, so it only tells me where to hold from.

## When the memory is another one (`[A_SARA_REASSERTS_ITS_OWN_V1]`)
The operating rule (`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`): find the current RAM, reassert your own truth, then
learn the world again. Nothing is filed and nothing is restored.

- **What a SARA owns, and keeps while it runs:** the tickets it issued, and the claims it won. A claim that lost is
  not its truth.
- **The event:** each SARA holds one read, on its own cell. It ends when the memory is gone.
- **Then:** that SARA's one attaching thread opens a session to the RAM manager at its address and writes its issues
  and its won claims into the memory that answers. A won claim says `won`, so it stands whichever of it and the issue
  arrives first. Only that thread replaces the session, so nothing guards it.
- **So:** a ticket issued before is claimable after; a ticket consumed before is consumed after; a ticket whose
  issuer is gone is absent and a claim on it is refused.
- **A call that finds the memory gone raises.** Nothing retries it. What to do is the caller's.
- Run worldshepherd-ram on a listening socket that outlives it (the platform's
  `[LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]`): a SARA's connect made while it is being started again waits in the
  socket's queue. Without that, a SARA that finds nobody at the address says so and tries nothing more.
- **Not closed:** between a consumed ticket's issuer reasserting it and its winner reasserting the claim, the ticket
  reads as not consumed, and a claim made then wins it.

## Brandon's Postgres store, unmodified, with no PostgreSQL
`PostgresClaimStore` is handed a connection factory and makes four SQL calls. `ticket_sql.py` is that factory: each
of the four statements, recognized as his module spells it, is done as the cells above (`[THE_SQL_CALLS_ARE_CELLS_V1]`);
any other statement is refused by name. His store, his 31 tests and his reason strings run as written.

## What is gone
Brandon's side: the lock, the transaction, the compare-and-set statement, the connection per call, the table and
its schema, the database server, and the database's file.
This conversion's own 0.4 and 0.5: a region operation each SARA asked and waited on, a queue per ticket, deferred
answers, and a client-side copy between two SQL statements. `AUDIT-NO-HIDDEN-MESSAGING.md` has each one.
0.6's file of every cell, and its restore.

## Not built yet (next, in this order)
1. SARA's events to ECHO as cells ECHO holds a read on (`event_outbox.py`'s PENDING/DELIVERED, the drain and
   `/v1/reconcile` go). On `main`, nothing in the package carries an event from SARA to ECHO: the integration script
   does it with curl.
2. WS-SOE v0.1C: the intent as the coordinator's cell and the effect as the service's cell.
3. Relay idempotency (branch `hardening/relay-idempotency-v1`), the same shape as the ticket claim.
4. Removing expired tickets' cells (refused claims' too) from the memory, and a SARA forgetting the expired ones it owns.
4a. Closing the interval between an issuer's and a winner's reassertion.
5. `worldshepherd-ram` as a FrogNet service: its capability tuple, its port in its configuration tuple, and
   selection of its host. Today it is started by hand and named by HOST:PORT, so a SARA reattaches to that address:
   a RAM manager that moves is found only when placement is an event the SARA holds a read on.
