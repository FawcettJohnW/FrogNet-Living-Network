# Suite map

## Branch connectors-distributed-claim-contract-v1 (52b3764): 31 tests, run as its CI does (`PYTHONPATH=.`)
| file | tests | asserts | on the memory |
|---|---|---|---|
| `test_connector_ticket_shared.py` | 5 | one winner of 64 claims; tampered and expired refused; only three fields kept; status hides the digest | 4 run on `RibbitClaimStore` through `harness/on_the_memory.py`; the fifth supplies its own fake store |
| `test_connector_ticket_lifecycle.py` | 8 | ticket format, TTL bounds, claim once, receipt after claim | applicable; uses the in-process `ReadTicketLedger` by its own choice |
| `test_connector_control.py` | 9 | policy: allow/deny, roles, data classes | not about the store |
| `test_connector_ticket_postgres.py` | 5 | the SQL text of the Postgres adapter | not applicable by construction: asserts the incumbent's SQL |
| `test_connector_ticket_sqlite.py` | 4 | SQLite file reopen, schema columns | not applicable by construction: asserts the incumbent's file |
Stock: 31 passed. With worldshepherd-ram substituted for the reference store: 31 passed, 4 stores made on the memory.

## main (234ef0c), deployments/sara_verified_local_v1: 919 tests
Stock on Python 3.13: 919 collected, 1 failed (`test_sda_transport_identity.py::test_real_mtls_...`: the test's own
certificate has no Authority Key Identifier, which Python 3.13's default verification refuses), 3 min 28 s.
State-and-delivery subset, the targets of the next steps (120 tests, 2.8 s, all pass): `test_event_outbox`,
`test_echo_persistence`, `test_echo_checkpoint*` (3 files), `test_prime_passport_api`, `test_prime_sentinel_service`,
`test_prime_sentinel_issuance_store`, `test_ws_soe_v01a/b/c`, `test_runtime_relay_fixtures`.
The remaining ~800 tests are solvers, CLIs and evidence tooling with no shared state: not conversion targets.
