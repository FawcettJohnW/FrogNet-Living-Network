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

## A ticket is one record (`[A_TICKET_IS_ONE_RECORD_V1]`, 0.11)
**This replaces 0.9's truth table and its claim, and everything above that describes a ticket as "its cells, derived
by every reader": the sections "The app's RAM host decides its writes" and "What the acceptance study changed"
describe 0.9's cells (`ticket.<id>` holding `issue.<who>.<n>` and `claim.<who>.<n>`, a claim saying `won`).**

How it got here, in John's words on 2026-11-25. On 0.9, where every claimant wrote its claim into the ticket's
family: "That's not froggish. One thread is writing into the process space of another thread." "The ticket should be
writing into its own space, after it does what it needs to do. If you're going to go OO, go fully OO." On a rebuild
that walked the service's log and took four calls a claim: "You do not have to use what is in ram_memory.hpp. You
can derive or just write your own thing. You can combine things so there's only one call to the server." On 0.10,
which kept the ticket as bookkeeping cells and read its state back as text on every call: "There should be no walks.
No acks, no copying things around." "Stop now and restart. You have the contract and the validation. The rest is now
greenfield. Write it like a single machine multithreaded lockless system."

| truth | owner (its one writer) | where it lives | how it changes |
|---|---|---|---|
| the ticket: digest, expiry, issuer | the ticket | one record in `worldshepherd-ram`, found by its id | its issue is one word, set once by one compare-and-swap |
| who won it | the ticket | one word in that record | set once by one compare-and-swap |
| the ticket table | the RAM host | added to, never moved, never freed while the memory lives | a record is added once |
| how the ticket stands, for everyone to read | the ticket | `(worldshepherd, tickets.<its issuer>, <ticket>)` `{"sha","exp","by","bn","won","wn","at"}` | written when made and when won; the family is one issuer's tickets, so the issuer's one held read on it fires when any of them is won |
| I am here | the SARA | `(worldshepherd, sara, <who>)`, gone with its session | the only cell a SARA writes itself |
| what I issued and what I won | the SARA | in its own process | it makes those calls again in a new memory |
| who won each ticket I issued | the issuing SARA | in its own process, with the issue; read from its held read on `(worldshepherd, tickets.<itself>)` | it says so when it makes the issue again in a new memory |

The ticket's two methods are region operations of `worldshepherd-ram`, each **one call** on the caller's session:

    op=issue {"who","ticket","sha","exp","n"}            -> {"state": the state, or null}
    op=claim {"who","ticket","sha","at","n"[,"held":1]}  -> {"outcome": claimed|consumed|digest|expired|absent, "state"}
    op=status {"who","ticket"}                           -> {"state": the state, or null}

A claim is refused in this order: no such ticket; another digest; `at` past the expiry; a winner already. `<who>` is
the store's name plus a random token made when it opens; `<n>` is the number of the call. A call made again (its
session ended under it) carries the same `<n>` and is answered as before. An operation is refused unless `<who>` is
the calling session's (`[A_SARA_WRITES_ONLY_ITS_OWN_SPACE_V1]`): the name belongs to the machine whose session first
named it, and to that session alone while it is up. `status` is the ticket asked how it stands: one call, found by
the ticket's id. (Through 0.11h it was a read of the cell `(worldshepherd, ticket, <ticket>)`. The cell is now in its
issuer's family, which a SARA that did not issue the ticket does not know; a read naming the ticket and no family
walks the service's cells in `ram_memory.hpp`, and nothing here walks.)

Nothing is locked. Nothing is searched for but a ticket by its id. Nothing is freed while the memory lives, so there
is no reclaimer in WorldShepherd and nobody is asked whether a free is safe.

**After the memory is replaced**, each SARA brings its own back to the new memory in one call, `op=again`
(a line `{"who"}`, then a line for each issue and each won claim; lines and not lists, because the wire keeps a
list out of line in a file on the RAM host's disk): its issues, and the claims it won, as held claims
(`[A_SARA_BRINGS_ITS_OWN_BACK_IN_ONE_CALL_V1]`: one call per ticket, one after another, measured 10 to 33 s of
recovery on a 100 ms link where 0.9 took 0.7 s). A held claim made before its ticket's issue is kept on the ticket; the issue, when
made, takes the first of them that names its digest within its expiry.

**Who won, in a new memory** (`[THE_ISSUER_SAYS_WHO_WON_V1]`). The issuer holds one read on its own tickets' family,
sees each of them won, and keeps the winner with the issue. Its line in `op=again` then carries `"won","wn","at"`,
and the ticket is made again already consumed by that winner: a member that says it held it is answered consumed.
A ticket won while its issuer could not see it (its link down, or the memory replaced before the ticket's write
reached its read) is made again as issued and not won, and the first valid claim takes it. The ticket cannot know
whether a claim that says `held` really won in the memory before (ACCEPTANCE.md, access:restored-winner).

### The session library's reclaimer (`[NOBODY_IS_ASKED_V1]`, `cpp/include/ebr.hpp`)
Found by running four 4,000-ticket races in a row: each ran slower than the last (slowest claimant 5.8 s, 8.8 s,
11.4 s, 17.2 s) with the RAM host's CPU flat. `ribbit::Ebr` kept a record for every thread that ever took a guard and
walked all of them twice per finished call; the race starts a thread per claim. Now a guard counts itself into the
current epoch and out again; there is no record of a thread and nothing is walked. The same four races: 5.1 s,
5.7 s, 5.3 s, 5.3 s. `cpp/tests/ebr_stress.cpp` (8,000 threads, each new) passes under AddressSanitizer and
ThreadSanitizer. **The RAM host's own reclaimer (`cpp/src/lf_ebr.cpp`) still walks its fixed array of thread slots.**

### Measured (4,000 tickets, 7 claimants, every call in flight at once, x86, 2026-11-25)
| | 0.9 | 0.10 | 0.11 |
|---|---|---|---|
| slowest claimant, LAN link | 9.1 s | 6.4 s | 5.3 s |
| slowest claimant, 100 ms link | 10.5 s | 7.1 s | 5.4 s |
| RAM host CPU, LAN link | 4.6 s | 3.2 s | 2.7 s |
| worst p99 claim, first run | not kept | 808 ms | 73 ms |
| RAM host memory per 4,000 tickets | not kept | about 100 MB | about 27 MB |
Every ticket was won once in every run. Brandon's 31 connector tests pass on both stores. The acceptance study
passes on both links (`ACCEPTANCE.md`, "The study on 0.11").

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
6. Proof of a restored winner whose issuer did not see the win, and knowing who was in a memory from something that
   outlives it (`ACCEPTANCE.md`, open).
