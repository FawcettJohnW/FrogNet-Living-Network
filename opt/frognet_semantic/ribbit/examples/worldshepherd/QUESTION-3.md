# Register

1. **`test_shared_store_persists_minimal_claim_state_only` reads the reference store's private dict**
   (`store._records[id].__dict__`). Contract misread? No: what it protects is real (only digest, expiry and
   consumption are kept). Met by state: `RibbitClaimStore.record()` derives the same three-field record from the
   ticket's two cells, and the harness answers `_records[...]` with it. Green. Mechanism elimination (the dict is gone).
2. **"ticket claim lost compare-and-set race"** (Postgres only). Not reproduced: one ticket takes one ask at a time,
   so a claim that is not first is told "ticket already consumed". Declined, by construction.
3. **The suite's concurrency test does not detect a wrong winner rule** (study-ticket-claims.md). Not a red: a gap.
   `harness/race.py` is the test that does.
4. **`test_connector_ticket_postgres.py`, `test_connector_ticket_sqlite.py`** assert the incumbent's SQL and file
   format. Not applicable by construction; they still pass because the incumbent's files are untouched.
5. **0.1 failed Brandon's concurrency test about one run in six** (`TypeError`, no claim in the read after the post).
   Implementation wrong: threads of one process shared a session, and the session answers an identical read in
   flight with one answer. Red first: `harness/test_one_session_many_threads.py` (269 errors on 0.1). Fixed in 0.2
   (the read named the posted cell); in 0.4 the read is gone and every ask carries its asker's name and count.
6. **Write order is not kept across an `fdf-ram` restart.** Not a red in the suite. 0.3 had the losers withdraw;
   0.4 has one writer per ticket and its own file, read back in order.
