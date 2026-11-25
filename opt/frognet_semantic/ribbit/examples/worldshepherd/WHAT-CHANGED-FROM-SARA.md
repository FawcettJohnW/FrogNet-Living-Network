# SARA's read-ticket ledger on FrogNet shared memory: what changed, and why

For Brandon Ray Davis, from John Fawcett. Version 0.8, 2026-11-24. This document stands on its own: it says what was
converted, describes every difference between SARA's implementation and the one on memory, gives the reason for each,
and ends with how to build it and run the tests. The code is in this directory
(`opt/frognet_semantic/ribbit/examples/worldshepherd`, branch `worldshepherd-ribbit-20261124` of
`FawcettJohnW/FrogNet-Living-Network`).

## What was converted, and what was not

SARA runs on one machine. The one part of it that is written to work across machines is the connector read-ticket
ledger on the branch `worldshepherd/connectors-distributed-claim-contract-v1-20260913`: a SARA issues a read ticket
(single use, 5 to 300 seconds), and the host about to execute it claims it. Exactly one claim may succeed, wherever
the claimants are. That is the part converted here.

Nothing of SARA's was edited. Its contract is kept exactly: the `SharedClaimStore` protocol (`register`, `claim`,
`status`), `SharedReadTicketLedger`, `ReadExecutionBroker`, the ticket format, the reason strings, and the 31 tests
in `tests/test_connector_*.py`, which run unmodified and pass. The ticket's HMAC, the policy envelope and the receipts
are application logic and were not touched.

Not converted yet: SARA's events to ECHO (`event_outbox.py`, `/v1/reconcile`), WS-SOE v0.1C, and relay idempotency
(branch `hardening/relay-idempotency-v1`). `SqliteReadTicketLedger` is a one-host ledger and is left as it is.

## The words used here

- **The memory.** One shared memory for every machine on the network. A program on any machine reads and writes it
  the way threads of one program read and write their shared variables.
- **Cell.** One value in the memory, addressed by three names: service, variable, instance. Exactly one participant
  writes a given cell, ever. Nobody writes a cell in another participant's space.
- **Family.** The cells that share a service and variable. Here, one family per ticket.
- **Write order.** The memory numbers every write as it arrives. Within a family those numbers are the one order
  every participant sees.
- **Held read.** A read that says "everything in this family after number N" and stays open until something is
  written. It is how a program learns of a change. Nothing polls and nothing is notified by its writer.
- **RAM manager**, or RAM host. The process that holds the memory. Since 0.9 it is `worldshepherd-ram`: the platform's RAM host with
  WorldShepherd's rules about who may write a cell and what it may say. It runs no ticket logic: nothing in it issues, claims or decides.
- **SARA**, below, means one running SARA process using the ledger.

## The test that decides each difference

If the SARAs were threads of one program on one machine, sharing its memory, would the program be written this way?
A thread does not send another thread a request and wait for the reply in order to update a shared table. It does not
run a server so the other threads can ask what the table says. It does not acknowledge a memory write. Each difference
below is a place where SARA's stores do one of those things between machines, and what a shared-memory program does
instead.

## The differences

### 1. Where a ticket's state is

**SARA.** One record per ticket, `(ticket_sha256, expires_at, consumed_at)`, held by a store: a dict inside one
process (`ThreadSafeSharedClaimStore`, which its own docstring says is not a distributed backend), or a row in
PostgreSQL (`PostgresClaimStore`). Every SARA sends the store its operations and the store answers.

**In the memory model.** A process whose job is to hold the state and answer operations on it is a server, and every
use of the state becomes a message to it and a wait for its reply. The state is then only as available as that one
process and each operation costs at least one round trip to wherever it is.

**On memory.** A ticket is its cells: family `(worldshepherd, ticket.<ticket_id>)`. The issuing SARA writes one cell,
`issue.<who>.<n>`, holding the digest and the expiry. Each claiming SARA writes one cell, `claim.<who>.<n>`, holding
the digest it was given and its `now`. `<who>` is that SARA's own name, so no two SARAs can ever write the same cell.
There is no record anywhere that says "consumed".

### 2. How "exactly once" is decided

**SARA.** Compare-and-set on the one record: a lock around read-check-write in the reference store, and in
PostgreSQL `UPDATE ... SET consumed_at = %s WHERE ... consumed_at IS NULL ... RETURNING`. Every claimant tries to
overwrite the same field, and the store lets one through.

**In the memory model.** A field that every participant may overwrite has no owner, so something must stand over it
and arbitrate each write at the moment it is made. That arbiter is the lock, and the lock is the server again. A
conditional write is also a read, a decision and a write that must not be separated, which is exactly what memory
shared across machines cannot promise without one.

**On memory.** Nothing is written conditionally and nothing is overwritten. Every claimant writes its own claim cell,
once, unconditionally. The memory's write order in the ticket's family is the one place every claim passes. The
winner is derived, by every reader, from the same cells with the same rule: the issue is the lowest-numbered issue
cell; the winner is the lowest-numbered claim cell written after it whose digest matches and whose `now` is not past
the expiry. A claimant reads the family until its own cell is in what it read. Then everything written before its
claim is in its hands, and it knows whether it won. Two claimants cannot both conclude they won, because they are
reading one order. `Ticket` in `ribbit_claim_store.py` is that rule, a dozen lines.

### 3. What a claim costs on the wire

**SARA.** A claim on PostgreSQL opens a connection, sends the `UPDATE`, waits for its result, commits, and closes.
A refused claim then opens a second connection and sends a `SELECT` to find out why.

**In the memory model.** Each of those is a request whose reply the caller waits for before it can go on. Across a
network the claim's time is the number of round trips times the link.

**On memory.** A claim is one write that waits for no answer, and one read. Over a simulated 100 ms WAN (50 ms each
way) a claim's median time is 107 ms on memory, 213 ms with SARA's own `PostgresClaimStore` class running on memory
(its statement sequence, with the memory under it), and 1,819 ms with that class on PostgreSQL.

### 4. Why a claim was refused

**SARA.** The `UPDATE` only says that no row changed. The reason comes from a second query, made after the first, on
state that may have changed in between. The store has a sixth reason for that case: "ticket claim lost
compare-and-set race".

**In the memory model.** Two questions asked at two moments are answered from two states. The extra reason exists
only because the verdict and its explanation were read separately.

**On memory.** The verdict and the reason come from the same cells in the same read, in the contract's order: not
registered, digest does not match, expired, already consumed, claimed. The sixth reason cannot occur.

### 5. Locks

**SARA.** `threading.Lock` in the reference store, `BEGIN IMMEDIATE` in the SQLite ledger, the row lock under
PostgreSQL's `UPDATE`.

**In the memory model.** A lock is a queue of callers waiting for one another. It is needed only when two
participants may write the same thing.

**On memory.** No two participants can write the same cell, so there is nothing to lock: none in
`ribbit_claim_store.py`, none in `ticket_sql.py`, and the RAM manager runs no operation of WorldShepherd's.
`AUDIT-NO-HIDDEN-MESSAGING.md` is the review that checked this code for locks, queues, request/reply and copies kept
on the side, and lists what earlier versions of this conversion had and removed.

### 6. Connections

**SARA.** `PostgresClaimStore` calls its connection factory for every operation. Each claim in flight is a connection.

**In the memory model.** A connection per operation is plumbing for request/reply. It also puts a ceiling on how many
claims can be in flight that has nothing to do with the work.

**On memory.** A SARA has one session to the memory for as long as it runs, used by all its threads at once.
Measured, with 8 SARAs over the 100 ms WAN and the number of claims in flight doubled each step: SARA's store on
PostgreSQL carried 96 and broke at 128 ("sorry, too many clients already", PostgreSQL's default of 100 connections).
Both stores on memory finished every step to 32,768 claims in flight on 24 connections, every ticket won exactly once.

### 7. Where the store is, and how a SARA finds it

**SARA.** The caller supplies a connection factory to one database at a configured address.

**In the memory model.** A configured address ties every SARA to one machine. Where the memory lives is the
network's business, and no participant should need the name of any other machine.

**On memory.** WorldShepherd's RAM is a FrogNet app service: the app `worldshepherd`, configured once with
`fdf-app config worldshepherd general`. The network elects the machine that serves it and every node's `/etc/hosts`
names that machine as `worldshepherdHost.frognet`. A SARA is constructed with no address, `RibbitClaimStore()`, and
finds it there. The one WorldShepherd program is its RAM host, `worldshepherd-ram`, which `fdf-app` starts; `make` builds it.

### 8. Failure and restart

**SARA.** The ledger survives because PostgreSQL keeps it on disk. If the database is down, every SARA's claims fail
until it is back. The store's docstring says distributed replay protection is not claimed until it has been validated
with multiple workers and reconnect and failure tests.

**In the memory model.** A file that is restored is one authority's copy of everyone's truth, and after a failure
everyone waits for that authority. In a shared memory each participant is the authority for its own truth and
nothing is restored.

**On memory.** The RAM manager keeps no file of the tickets. Each SARA keeps what it owns for as long as it runs:
the tickets it issued and the claims it won. Each SARA holds one read, on the family of SARAs present. When the
memory goes away (the serving machine stops, or its RAM manager dies) that read ends, and the network elects another
machine, whose memory is empty. Then each SARA does four things, in this order:

1. It finds the RAM where it is served now.
2. It writes its own truth into it: its issues and its won claims. A won claim says `won`, so it stands whichever of
   the two arrives first.
3. It learns the world again: it holds a read on the family of SARAs until every SARA that was in the old memory is
   in the new one. Each wrote its own truth before announcing itself, so their tickets and won claims are there. A
   SARA that never arrives is waited for no longer than a ticket can live (`MAX_TTL_SECONDS`, 300 s), after which
   nothing it issued or consumed can be claimed by anyone.
4. It holds its read again, and its calls proceed. A claim that was in flight when the memory went away is made
   again in the new memory. It does not raise.

A ticket issued before the failure is claimable after it. A ticket consumed before is still consumed. A ticket whose
issuer has gone is absent and a claim on it is refused, which is the safe side for a credential.

Tested (`harness/test_the_apphost_moves.py`): three SARAs, the serving machine stopped with 80 claims in flight, then
the new machine's RAM manager killed with 80 more in flight. All 160 claims were answered, none raised, and each of
the 80 tickets was won by exactly one SARA. The previously consumed ticket was refused as consumed by every SARA
afterwards.

### 9. Observing the ledger

**SARA.** `status()` asks the store. The browser page `/ui` is a fixed page naming endpoints an operator can call.

**In the memory model.** A status call is a request for something the caller could simply read.

**On memory.** `status` is a read of the ticket's family. `sara_ui.py` replaces `/ui` with a page that is one held
read of the memory: where the RAM is served, the SARAs in it, and every ticket as it stands, the same in every SARA's
browser because they all read the same cells. It changes when a cell is written and asks nothing on a timer: in
Chrome, a claim written while the page was open appeared on it with no reload, and the page had made two requests in
all. It writes nothing and shows no digest or token. `results/sara-ui-0.8.png` is that page.

### 10. SARA's PostgreSQL store, kept as written

`PostgresClaimStore` makes four SQL statements through a connection factory. `ticket_sql.py` is a connection factory
that recognizes those four statements, exactly as `connector_ticket_postgres.py` spells them, and carries each out as
the cells above. So SARA's own store class runs unmodified, with no PostgreSQL:

    store = PostgresClaimStore(ticket_sql.connection_factory())

All 31 tests pass with it. It makes the two-step sequence of difference 3 and 4, so it is about half the speed of
`RibbitClaimStore`; it is there to show that the contract, down to the SQL, is kept.

## What stayed exactly the same

The `SharedClaimStore` protocol and everything above it; the reason strings; a duplicate `ticket_id` raising
`ValueError("duplicate ticket_id")`; the store holding only the ticket id, digest, expiry and consumption time (no
connector context, result, policy envelope or credential enters the memory); and expiry being judged against the
`now` the claimant supplies, so the clock-skew bound between SARAs is the same question it was.

## Measured

Every SARA is its own process on its own shaped link, in FrogNet's network simulator
(`simulation/worldshepherd_slow_tier.py`), 8 SARAs racing for the same tickets. Every run: each ticket won exactly once.

| 8 SARAs, 1 claim at a time each | claim, median | claims per second |
|---|---|---|
| WAN 100 ms: `RibbitClaimStore` on memory | 107 ms | 74 |
| WAN 100 ms: SARA's `PostgresClaimStore` on memory | 213 ms | 38 |
| WAN 100 ms: SARA's `PostgresClaimStore` on PostgreSQL 16 | 1,819 ms | 4.5 |
| LAN 0.5 ms: `RibbitClaimStore` on memory | 3.3 ms | 2,069 |
| LAN 0.5 ms: SARA's `PostgresClaimStore` on memory | 5.5 ms | 1,254 |

| claims in flight, WAN 100 ms | on PostgreSQL | on memory |
|---|---|---|
| 96 | completes | completes |
| 128 | breaks: too many clients | completes |
| 32,768 | | completes, 24 connections; about 5,300 claims per second |

These ran in the simulator on one 2-core machine. A run on separate machines is next.

| code lines (comments and blanks out) | |
|---|---|
| SARA: `connector_ticket_shared.py` 125, `connector_ticket_postgres.py` 128, `connector_ticket_sqlite.py` 143 | 396, plus a PostgreSQL server |
| memory: `ribbit_claim_store.py` | 193, about half of them finding the RAM and reasserting after it moves; no server |
| to run SARA's Postgres store as written: `ticket_sql.py` | 49 more |

## Build

Linux, g++ with C++20, `liblz4-dev`, `libssl-dev`, Python 3.10 or later with `pytest` and `fastapi`.

    git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git
    cd FrogNet-Living-Network && git checkout worldshepherd-ribbit-20261124
    (cd opt/frognet_semantic/ribbit/examples/worldshepherd && make)      # worldshepherd-ram and the session library Python loads

    git clone https://github.com/Bdavis1390/Sara_pro.git
    (cd Sara_pro && git checkout worldshepherd/connectors-distributed-claim-contract-v1-20260913)   # tested at 52b3764

## Run the tests

From `opt/frognet_semantic/ribbit/examples/worldshepherd`, with `SARA=` the path of the Sara_pro checkout.

**1. SARA's own tests on memory, and the race between processes.** No FrogNet node needed; about 10 seconds.

    python3 harness/run_his_tests.py --sara $SARA

It starts one RAM manager on a free local port and prints:

    PASS  1 his connector tests, WS_STORE=memory: ... 31 passed
    PASS  2 his connector tests, WS_STORE=sql: ... 31 passed
    PASS  3 one session, many threads: 2 passed
    PASS  4 the race, memory: 8 processes, 400 tickets: won once 400, twice 0, never 0; ...
    PASS  4 the race, sql: 8 processes, 400 tickets: won once 400, twice 0, never 0; ...
    RESULT PASS

Lines 1 and 2 are the unmodified files `tests/test_connector_*.py`. A pytest plugin (`harness/on_the_memory.py`) makes
the name `ThreadSafeSharedClaimStore` construct the store on memory instead: `RibbitClaimStore` in line 1, SARA's own
`PostgresClaimStore` on `ticket_sql.py` in line 2. To see that the race can fail, reverse the winner rule in `Ticket`
(`min` to `max`): the race then fails on every ticket.

**2. The RAM as a service, and failover.** Needs root, MariaDB, and php with mysqli and curl;
`bash opt/frognet_semantic/simulation/setup.sh --yes` installs and builds what is missing. About 20 seconds.

    python3 harness/test_the_apphost_moves.py --sara $SARA

Six lines and `RESULT PASS`. Lines 5 and 6 are the claims in flight during each failure and must read
`80 answered, 0 raised` and `one winner 40, two 0, none 0`.

**3. Across a network, in the simulator.** From `opt/frognet_semantic`; about a minute.

    python3 simulation/worldshepherd_slow_tier.py --sara $SARA --deploy lan wan

Add `WS_PG_DSN="host=... dbname=... user=... password=..."` to the environment (and `pip install psycopg2-binary`)
to run SARA's store on your PostgreSQL beside the two on memory. Add `--ramp` to raise the claims in flight until a
store breaks.

**4. The browser page.**

    WS_RAM=HOST:PORT python3 -m uvicorn --factory sara_ui:standalone

and open `http://127.0.0.1:8000/ui`. `HOST:PORT` is a running `worldshepherd-ram`; test 1 prints nothing you can reuse, so
start one with
`bin/worldshepherd-ram --listen 127.0.0.1:45000 --api /FawcettInnovations.RINA.ram_interface.php`.
Inside SARA, mount it in place of `/ui` with `app.include_router(sara_ui.router(store))`.

## Using it in SARA

    from ribbit_claim_store import RibbitClaimStore
    ledger = SharedReadTicketLedger(RibbitClaimStore())

On FrogNet nodes the app is configured once on one node (`fdf-app config worldshepherd general --admin USER:PASSWORD`)
and installed on the others (`fdf-app install worldshepherd --admin USER:PASSWORD`). `README.md` has the rest.
