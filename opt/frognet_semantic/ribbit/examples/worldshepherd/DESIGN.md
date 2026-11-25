# WorldShepherd SARA on the memory -- design (0.9, 2026-11-25)

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

## WorldShepherd's RAM is a FrogNet app service (`[THE_RAM_IS_A_SERVICE_V1]`)
The app is `worldshepherd` and it has one RAM, `general`
(`fdf-app config worldshepherd general --host general=worldshepherd-ram --admin USER:PASSWORD`).
It is served by the app's own RAM host, `worldshepherd-ram`, started by `fdf-app` on the machine the election names
`worldshepherdHost.frognet` in `/etc/hosts`, on the port in the app's one configuration tuple. `fdf-app` holds its
listening socket. The RAM host runs no operation of WorldShepherd's: a ticket is cells SARAs write and read. It
keeps no file of them.

## The app's RAM host decides its writes (`[THE_APPS_RAM_HOST_DECIDES_ITS_WRITES_V1]`, 0.9)
Who may write a cell is the app's rule, and the app's RAM host enforces it. Every RAM host is derived from the
platform's and is not bound by it: `worldshepherd_ram.cpp` is the platform's `RamHost` with three rules about the
service `worldshepherd`, asked of every write once its coordinates and bag are known (the platform's `refuse_cell`).
Until 0.9 the general `fdf-ram` served this RAM and had none of them: any session could write a claim in any name
saying won (Brandon Davis, reading the code, 2026-11-25).

| rule | |
|---|---|
| `[A_SARA_WRITES_ONLY_ITS_OWN_CELLS_V1]` | a cell names its writer (`sara` `<who>`; `issue.<who>.<n>`, `claim.<who>.<n>`). A name belongs to the machine whose session first wrote it, for the life of the memory, and while that session is up to that session alone. Who owns a name is itself memory: the first cell, by write order, in the host's own `(#worldshepherd, who.<who>)` |
| `[A_CELL_SAYS_WHAT_ITS_KIND_SAYS_V1]` | an issue says `sha` and `exp`; a claim says `sha` and `at`; nothing else is in the service |
| `[WON_IS_CONSISTENT_WITH_THE_TICKET_V1]` | a claim that says won is refused when this memory shows it false; `Ticket` applies the same digest and expiry rule for every reader |

What the host cannot know is whether a consistent claim that says won in a new memory was the winner in the one
before. That is open, and `ACCEPTANCE.md` says what closes it.

## What the acceptance study changed in a SARA (0.9)
`ACCEPTANCE.md` has the measurements. Each rule is in `ribbit_claim_store.py` where it acts.

| rule | |
|---|---|
| `[A_MEMORY_GONE_WHILE_I_WRITE_MY_OWN_V1]` | a memory that goes while a SARA writes its own truth into it: the attach is done again, whole, in the next |
| `[A_CALL_HAS_ONE_CELL_V1]` | a call names its cell once. Made again after its session ended, it reads first and writes only if its cell is not there |
| `[A_SARA_THAT_LEFT_IS_WAITED_FOR_V1]` | a SARA that left the memory less than a ticket's life ago is waited for like one that was there when it was lost |
| `[A_HELD_CLAIM_AGES_V1]` | a claim that is made again is as old as it has been held: what it writes says so, and it is answered at that time |

## How long a cell stays
A cell stays for the life of that memory, not of its
writer's session: a ticket a SARA consumed is still consumed after that SARA has gone.

A SARA is given no address. It finds the RAM as any program on a node does (`fdf_appram.where`: the AppHost line and
the port). 0.1 to 0.7 had a RAM manager of their own (`worldshepherd-ram`), started by hand and named by HOST:PORT;
that is gone (John, 2026-11-24: "It should always be a service").

**One addition to the platform, `fdf-app` r54 (`[SERVING_SAID_V1]`).** Each machine's election writes its own
`/etc/hosts` at its own moment, so a program on another machine could read the new AppHost before that machine's
`fdf-ram` was listening, and nothing on the network said when it was (the `.up` file is on the serving machine only).
`fdf-app` now writes `(fdf-app, serving, <app>.<ram>.<guid>)` = where its socket listens and the pid behind it, in
the SystemRam, and removes it when it serves the RAM no longer. A SARA that finds nothing listening holds a read on
that family (`frognet_held`, which also ends when `/etc/hosts` is written). `rinab/tools/test_fdf_app.py` passes as
before (13 checks).

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

## When the memory moves (`[A_SARA_REASSERTS_ITS_OWN_V1]`, `[THEN_LEARN_THE_WORLD_AGAIN_V1]`)
The operating rule (`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`): find the current RAM, reassert your own truth, then
learn the world again. Nothing is filed and nothing is restored.

- **What a SARA owns, and keeps while it runs:** the tickets it issued, and the claims it won. A claim that lost is
  not its truth.
- **The read it holds:** the SARAs' family, `(worldshepherd, sara, *)`. Each SARA's own cell there says it is here and
  goes with its session. The read ends when the memory is gone (its AppHost moved, or its RAM host ended): that is
  the event.
- **Then, on that SARA's one attaching thread:**
  1. *Find the current RAM.* `fdf_appram.where`. If nothing listens there yet, hold a read on what `fdf-app` says it
     serves, which also ends when `/etc/hosts` is written.
  2. *Reassert.* Write its issues and its won claims into that memory. A won claim says `won`, so it stands whichever
     of it and the issue arrives first. Then write its own cell in the SARAs' family, on the same session, after them.
  3. *Learn the world again.* Hold a read on the SARAs' family until every SARA that was in the memory before is in
     this one. Each of them wrote its own truth before its cell, so the tickets it issued and the claims it won are
     there. A SARA that is not coming is waited for no longer than a ticket lives (the contract's
     `MAX_TTL_SECONDS`, 300 s): after that nothing it issued or consumed before can be claimed by anyone.
  4. *Hold the read again, and proceed.* A call that found the memory gone waited for this and is made again in the
     new memory. It does not raise.
- **So:** a ticket issued before is claimable after; a ticket consumed before is consumed after; a ticket whose
  issuer is gone is absent and a claim on it is refused. Claims in flight while the memory moves are answered, each
  ticket by one winner (`harness/test_the_apphost_moves.py`, checks 5 and 6).
- Only the attaching thread replaces the session, so nothing guards it.
- `RibbitClaimStore("HOST:PORT")` names one RAM host directly, for the simulator and the tests. When nothing answers
  there it says so and its calls raise: nothing else is tried.

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
0.1 to 0.7's own RAM manager (`worldshepherd_ram.cpp`), and a call raising when the memory moved under it.

## Not built yet (next, in this order)
1. SARA's events to ECHO as cells ECHO holds a read on (`event_outbox.py`'s PENDING/DELIVERED, the drain and
   `/v1/reconcile` go). On `main`, nothing in the package carries an event from SARA to ECHO: the integration script
   does it with curl.
2. WS-SOE v0.1C: the intent as the coordinator's cell and the effect as the service's cell.
3. Relay idempotency (branch `hardening/relay-idempotency-v1`), the same shape as the ticket claim.
4. Removing expired tickets' cells (refused claims' too) from the memory, and a SARA forgetting the expired ones it owns.
   The acceptance study measured what not having it costs (`ACCEPTANCE.md`, storage).
5. A run on real nodes (the service test runs `fdf-app`, `worldshepherd-ram`, the role hosts and the election on one machine).
6. Proof of a restored winner, and knowing who was in a memory from something that outlives it (`ACCEPTANCE.md`, open).
