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
   0.6 files each cell before it is published and reads the file back in order.
7. **0.4 and 0.5 put a server back** (a region operation, a queue per ticket, deferred answers, a client-side copy).
   Not a red in any suite: they passed everything. Found by John's review of 2026-11-24 and recorded in
   `AUDIT-NO-HIDDEN-MESSAGING.md`. 0.6 is cells and write order again, with 5 and 6 fixed where they are: a claimant
   reads until its own cell is there, and worldshepherd-ram files cells in order.
8. **`PostgresClaimStore` needs no PostgreSQL** (John, 2026-11-24: "Look at the calls. If they are just SQL, you can
   convert them"). Its four statements are converted in `ticket_sql.py`; the store is unmodified. Green: 31 tests.
