# v0.33 ETR Map-Request -> Map-Reply from held database_mapping (IPv4) — GREEN

Base: v0.32 runtime-lock-free GREEN (24629d38…ae5c12d… see WORKLOG). Red: CHECKPOINT-v0.33-red.md.

## Behaviour
- New ETR-scoped operation `wire.etr_request4`. It decodes the established Map-Request wire format, consults
  ONLY the held ETR database mapping (`database_mapping.get`: longest-prefix, IID/group isolation, inactive
  withdrawal with fallback), and encodes a positive authoritative Map-Reply with `encode_reply4`.
- Reply contract from the control (lispers.net lisp.py `lisp_etr_process_map_request` 7544 ->
  `lisp_build_map_reply` 7384): one record, authoritative, action No-Action, record TTL 1440, R bit on
  every locator. The Map-Server path (`wire.request4`) and the ITR map-cache are not consulted.
- An ETR database miss throws "reply behaviour not established"; no negative reply is invented.
- IID is 0 (the request codec carries no IID), as in `wire.request4`.

## Defect found and fixed by the red test
- In the LOCAL backend, ETR database mappings and Map-Server registrations shared one table (`db`): a
  Map-Server registration was returned by `database_mapping.get`, and `database_mapping.delete` could erase a
  Map-Server registration with the same key. The FNW1 backend was already correct (separate variables).
  Local database mappings now live in their own `etr_db`. The test's decoy /24 registration exposed it.

## Qualification
- Red against unchanged v0.32: 44 tests, 1 error (`unsupported operation: wire.etr_request4`), 1 skip.
- Local: 43 PASS / 1 explicit SKIP (artifacts/ribbit-local-etr-green-v0.33.txt).
- Clean FNW1 (fresh ram-server): 43 PASS / 1 explicit SKIP (artifacts/ribbit-fnw1-etr-v0.33-clean.txt).
- Independent-process held tests: resolver+site, DDT, map-cache, database-mapping all PASS; NEW
  tools/test_held_etr_request.py PASS — a separate writer process publishes mappings, the ETR process answers
  from its held view with zero request-time database-mapping reads, and falls back to the /16 on withdrawal
  (artifacts/held-independent-v0.33.txt).
- Synchronization architecture tests: 3/3 PASS (in the suite).
- Runtime lock-freedom probe: pointer_atomic_lock_free=YES. Reclamation stress: born=100001 dead=100001.
- Dino byte oracle: register4 fixture == control encoder; ribbit reply4 == control encoder (wire codec
  region unchanged).
- ribbit_lisp.cpp: zero participant mutex/CV/lock_guard/unique_lock, zero atomic shared_ptr, zero
  Memory::remove, zero lines over 200 characters. package_check PASS; no bytecode.

## Performance (artifacts/performance-v0.33.txt; 1-core sandbox, FNW1, warmed, 5 x 20,000 pipelined requests
through the JSON-lines interface — interface cost, not a lookup microbenchmark)
- wire.etr_request4 (held database mapping): median 20.36 us/request
- wire.request4 (Map-Server, held registration): median 25.28 us/request, same process and session
- database_mapping_reads after all runs: 0
- Snapshot publication/reclamation code untouched; the v0.32 snapshot microbenchmark was not rerun.

## Not claimed
- IPv6 ETR path (separate red next). ETR negative reply on a database miss. The L (local) bit (host-dependent
  in the control). A control byte oracle for the ETR reply (the control's builder needs host addresses).

## Pre-existing, unchanged, recorded
- tools/cross_process_ram.py (v0.3 era) asserts replacement visibility immediately after another process
  writes, without observing convergence; map-cache is a held view since v0.20, so it fails intermittently.
  Reproduced identically on unchanged v0.32. Needs an observed-convergence rewrite; not touched in this slice.
