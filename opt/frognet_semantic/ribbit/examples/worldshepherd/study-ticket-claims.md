# Study: the read-ticket ledger (branch connectors-distributed-claim-contract-v1, 52b3764)

Source: `worldshepherd_sara/connector_ticket_lifecycle.py`, `connector_ticket_shared.py`,
`connector_ticket_sqlite.py`, `connector_ticket_postgres.py`, `connector_execution.py`.

**Problem.** `ReadExecutionBroker.plan_read` issues a ticket: `ticket_id` (48 hex), `issued_at`, `expires_at`,
`max_uses: 1`, hashes of the context and policy envelope, and `sha256` over the whole ticket. `claim_for_execution`
must succeed once, for one caller, before `expires_at`.

**State kept per ticket**, by every store: `ticket_sha256`, `expires_at`, `consumed_at`. Nothing else (two tests
assert that).

**Order of checks in `claim`**, identical in all four stores, and the reason strings are part of the contract:
1. `verify_v2_ticket_shape` fails -> "invalid read ticket" (before the store is touched)
2. unknown id -> "ticket not registered in this ledger"
3. digest differs -> "ticket digest does not match registered ticket"
4. `now > expires_at` -> "ticket expired" (`now` is the caller's; there is no server clock)
5. already consumed -> "ticket already consumed"
6. otherwise consumed at `now` -> "ticket claimed for one-time host execution"
Postgres adds a seventh, "ticket claim lost compare-and-set race", for a claim that lost between its UPDATE and its
re-query. On the memory that case does not exist: a claimant has read the claim before its own, and derives reason 5.

**Duplicate registration** raises `ValueError("duplicate ticket_id")`.

**Under contention.** In-process: one mutex. SQLite: `BEGIN IMMEDIATE`, one writer at a time for the whole file.
Postgres: one UPDATE, then on a miss a second query to say why; a new connection for every call.

**What the suite does not test.** Its one concurrency test (16 threads, 64 claims, one store object) passed against
a store whose winner rule was deliberately wrong (0.1 with the highest id winning instead of the lowest): the threads
did not overlap. The 8-process race in `harness/race.py` caught that mutation (9 of 400 tickets won twice), and
catches 0.6 with the rule reversed (400 of 400).
