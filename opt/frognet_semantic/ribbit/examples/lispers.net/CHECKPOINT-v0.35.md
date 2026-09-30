# v0.35 Observed replacement convergence — GREEN

Base: v0.34 GREEN. Red: CHECKPOINT-v0.35-red.md (46 tests, 1 failure, deterministic, against unchanged v0.34).

## Behaviour
- `map_cache.wait` accepts an optional `rloc`: "present" then means present AND carrying that locator, so a
  reader can observe a replacement converge rather than assume it. Optional `timeout_s` bounds the wait and
  returns "timeout"; without it the wait is unbounded, exactly as before.
- `wait_applied` gained the optional bound (held reads shortened to the remaining time); default unchanged.
- Local backend answers the same predicate from its table.
- tools/cross_process_ram.py waits for the value it expects from another process (observed convergence);
  writers remain asynchronous; no sleeps.

## Qualification
- Local: 45 PASS / 1 explicit SKIP. Clean FNW1: 45 PASS / 1 explicit SKIP.
- Independent-process held tests all PASS (artifacts/held-independent-v0.35.txt).
- cross_process_ram: 20/20 PASS on v0.35 (artifacts/cross-process-green-v0.35.txt); the old tool failed 7/20
  on v0.34 (artifacts/cross-process-old-tool-v0.34.txt).
- Runtime lock-free probe YES; Dino byte oracle PASS; zero participant locks, atomic shared_ptr,
  Memory::remove, lines over 200; package_check PASS.

## Performance (artifacts/performance-v0.35.txt; request path untouched — regression check)
- wire.etr_request4 20.67, wire.request4 25.06, wire.etr_request6 25.52 us/request median (v0.34: 21.40,
  25.50, 26.22). database_mapping_reads 0. No regression.
