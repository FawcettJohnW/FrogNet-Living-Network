# v0.29 lock-free participant held views — GREEN

Base: v0.27 negative-reply green; v0.28 preserved the architectural red test.

- Zero `std::mutex`, `std::condition_variable`, `std::lock_guard`, or `std::unique_lock` remain in `ribbit_cpp/ribbit_lisp.cpp`.
- Registration, governance, site, DDT, map-cache, and map-resolver held state use immutable snapshots published with atomic `shared_ptr` load/store.
- Each per-prefix-length watcher is the sole writer of its table slot.
- Snapshot lifetime is protected by shared ownership; replacement cannot reclaim a snapshot still held by a request.
- Dynamic child watcher creation is owned by the corresponding manifest watcher after bootstrap; no watcher collection mutex remains.
- `resolver.wait*`, `map_cache.wait`, and `map_resolver.wait` no longer use private condition variables. They wait on FrogNet Memory applied truths and re-check the held snapshot.
- Applied cells are single-writer cells: `registration|N`, `governance|N`, `site|N`, `ddt|N`, `map-cache|N`, and `map-resolver` under their participant/view applied variables.
- Warm request paths remain RAM-read-free; independent-process held resolver/site, DDT, and map-cache tests pass.
- Local conformance: 42 PASS / 1 explicit SKIP.
- Clean FNW1 conformance: 42 PASS / 1 explicit SKIP.
- Synchronization architecture tests: 3 PASS.
- `Memory::remove()` calls in `ribbit_lisp.cpp`: zero.
- Pre-Engine wire/codec region is byte-for-byte source-identical to v0.27; existing Dino-derived Map-Register/Map-Reply byte fixture remains unchanged.
- `database_mapping` retained unchanged.
- `frogram.cpp` still contains transport/session mutexes and condition variables. Those serialize FNW1 socket request/reply machinery below the Ribbit participant state model and were intentionally not altered in this slice.
