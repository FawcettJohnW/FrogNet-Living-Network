# v0.44 Liveness of native registrations — GREEN

Base: v0.43 GREEN. Red: CHECKPOINT-v0.44-red.md.

## Behaviour
- Each ETR publishes ONE liveness cell, etr-live/<xtr_id> {etr, alive_until}, refreshed by a heartbeat participant
  (`etr_liveness.start interval_s lifetime_s`, defaults 20 s / 60 s; `etr_liveness.stop`). The heartbeat's clock is a
  bounded held read on its own control cell: the timeout is the beat, a stop wakes it at once.
- The governor marks each accepted native registration with the ETR's liveness key (`live_key` = xTR-ID).
- Resolvers hold the liveness cells (one lock-free snapshot, one watcher). A registration with a liveness key
  resolves only while alive_until is in the future. Nothing is written when an ETR dies: death is derived from time
  at read. An ETR that never publishes liveness does not resolve. Registrations without a liveness key (the UDP
  Map-Register path) are unaffected.
- `resolver.wait` now answers exactly what resolution answers (unexpired, and live for native registrations).
  Because death is time-derived, a waiter observes it at its held-read timeout granularity (<= 5 s chunks), not
  through a wake.
- Change to the v0.43 contract: a native registration requires a live ETR. The v0.43 native test, the native
  latency bench and the byte tool now start liveness.

## Qualification
- tools/test_native_liveness.py 10/10 runs of the final code; tools/test_native_registration.py 5/5.
- Local 49 PASS / 1 SKIP; clean FNW1 49 PASS / 1 SKIP, 3/3; all independent-process held, registrar, native and
  liveness tests PASS; atomicity stress 600 rounds: 0 partial, 0 wrongly accepted; Dino byte oracle 4 PASS; runtime
  lock-free probe YES; reclamation stress born=100001 dead=100001; zero participant locks/CVs, atomic shared_ptr,
  Memory::remove, lines over 200; package_check PASS.

## Bytes (artifacts/liveness-bytes-v0.44.txt, native-change-bytes-v0.44.txt)
- Steady state: one heartbeat costs the ETR ~404 B (write + held read) and a holding resolver ~404 B, independent of
  the number of database mappings. Conventional steady state: one full Map-Register per map-server per 60 s, 88 B at
  N=1, 340 B at N=10, 3,100 B at N=100 (5 packets). On FNW1 without BLDC, native liveness costs more than UDP below
  roughly 13 mappings and less above; per ETR per interval, not per mapping.
- One RLOC change: ETR 593 B, Map-Server 2,095 B, ITR 1,070 B.

## Performance (artifacts/performance-v0.44.txt)
- Database change -> governor -> ITR resolves the new RLOC, N=100: median 1.72 ms, p90 2.37 ms.
- Other paths unchanged within noise.

## Not claimed
- Re-governance on site-policy change (v0.45). Multiple governors. Liveness waits wake by time-out, not by event.
