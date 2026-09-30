# v0.32 runtime-lock-free held snapshots — GREEN

Base: v0.31 held database-mapping green.

v0.29/v0.31 removed explicit participant mutexes but published immutable held views through the C++17 atomic
free functions for `std::shared_ptr`. On the qualification toolchain those operations are not lock-free, so the
strong runtime lock-free claim was not established by those checkpoints.

v0.32 replaces that publication/lifetime layer with raw `std::atomic<const T*>` slots and lock-free
quiescent-state reclamation. A reader publishes active status before loading a slot and clears it after its
lookup. The sole writer exchanges the immutable snapshot and retires the old one. Retired snapshots are freed
only across a verified quiescent point. No request-path mutex is hidden in a standard-library shared pointer.

Qualification:
- local conformance: 43 PASS / 1 explicit SKIP;
- clean FNW1 conformance: 43 PASS / 1 explicit SKIP;
- synchronization architecture tests: 3 PASS;
- pointer-atomic runtime probe: lock-free YES on this toolchain;
- reclamation stress: 100,001 snapshots constructed / 100,001 destroyed, no ordering failure;
- independent-process resolver/site, DDT, map-cache, and database-mapping held tests: PASS on a clean RAM server;
- warm consumer paths remain zero-direct-RAM-read;
- zero `std::mutex`, `std::condition_variable`, `std::lock_guard`, `std::unique_lock`, `std::shared_ptr`,
  `std::atomic_load(&...)`, and `Memory::remove()` in `ribbit_lisp.cpp`;
- zero lines over 200 characters in `ribbit_lisp.cpp`;
- package_check PASS.

Performance is a required checkpoint gate from here forward. The v0.32 same-host snapshot-acquisition A/B is
recorded in `artifacts/performance-v0.32.txt`. This run measured essentially parity between the old hidden-lock
shared_ptr path and the runtime-lock-free path. It is a microbenchmark, not end-to-end LISP throughput.

The reclamation implementation is a reconstructed participant-local QSBR/RCU-style mechanism. It is not claimed
to be the byte-for-byte Redis-region `ebr.hpp`, whose source was not present in the recovered artifacts.
