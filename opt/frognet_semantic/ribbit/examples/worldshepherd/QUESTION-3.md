# Register

1. **`test_shared_store_persists_minimal_claim_state_only` reads the reference store's private dict**
   (`store._records[id].__dict__`). Contract misread? No: what it protects is real (only digest, expiry and
   consumption are kept). Met by state: `RibbitClaimStore.record()` derives the same three-field record from the
   ticket's two cells, and the harness answers `_records[...]` with it. Green. Mechanism elimination (the dict is gone).
2. **"ticket claim lost compare-and-set race"** (Postgres only). Not reproduced: a claimant has read the claim that
   came before its own, so it derives "ticket already consumed". Declined, by construction.
3. **The suite's concurrency test does not detect a wrong winner rule** (study-ticket-claims.md). Not a red: a gap.
   `harness/race.py` is the test that does.
4. **`test_connector_ticket_postgres.py`, `test_connector_ticket_sqlite.py`** assert the incumbent's SQL and file
   format. Not applicable by construction; they still pass because the incumbent's files are untouched.
5. **0.1 failed Brandon's concurrency test about one run in six** (`TypeError`, no claim in the read after the post).
   Implementation wrong: threads of one process shared a session, and the session answers an identical read in
   flight with one answer. Red first: `harness/test_one_session_many_threads.py` (269 errors on 0.1). Fixed in 0.2
   (the read named the posted cell). 0.6: a read without the claimant's own cell decides nothing; it holds until
   the cell is there.
6. **Write order is not kept across an `fdf-ram` restart.** Not a red in the suite. 0.3 had the losers withdraw;
   0.7 has no file and no restart to order: item 9.
7. **0.4 and 0.5 put a server back** (a region operation, a queue per ticket, deferred answers, a client-side copy).
   Not a red in any suite: they passed everything. Found by John's review of 2026-11-24 and recorded in
   `AUDIT-NO-HIDDEN-MESSAGING.md`. 0.6 is cells and write order again, with 5 and 6 fixed where they are: a claimant
   reads until its own cell is there, and worldshepherd-ram files cells in order.
8. **`PostgresClaimStore` needs no PostgreSQL** (John, 2026-11-24: "Look at the calls. If they are just SQL, you can
   convert them"). Its four statements are converted in `ticket_sql.py`; the store is unmodified. Green: 31 tests.
9. **0.6 kept a file so a restart would bring the tickets back.** Not a red. Wrong model: FrogNet recovers by owners
   reasserting, not by restoring (John, 2026-11-24). 0.7 has no file; `harness/test_the_memory_is_another_one.py`
   kills the RAM manager, starts an empty one, and checks what each SARA wrote again. Brandon's SQLite test of
   surviving a reopen is his SQLite store's; on the memory the same outcome comes from reassertion, for as long as
   the issuer and the winner are running.
10. **A stand-alone RAM manager at a fixed address (0.1 to 0.7).** An app's RAM on FrogNet is always a service
    (John, 2026-11-24: "Why isnt the ram a service? It should always be a service"). `worldshepherd-ram` is gone; the
    app `worldshepherd` is served by `fdf-app` with `fdf-ram`. What it exposed in the platform: a program on another
    machine had no event for "the new AppHost's RAM is listening". `fdf-app` r54 says it (`[SERVING_SAID_V1]`).
11. **Two of the rule's three parts (0.7).** "Find the current RAM, reassert your own truth, then learn the world
    again." 0.7 did the first two and its calls proceeded; the interval it left was measured in 0.8's test as a
    ticket refused to both claimants. 0.8 holds a read on the SARAs' family until the SARAs that were there before
    are there again, each having written its own truth first (`[THEN_LEARN_THE_WORLD_AGAIN_V1]`).
12. **A call raising when the memory moved under it (0.7).** It waits for the SARA to be on the new memory and is
    made again there (John: "On failover, assert and set up held reads, then proceed").

