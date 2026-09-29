# ribbit/cpp -- the platform

What every vendor's executable and client library is built from.

| | |
|---|---|
| `include/ram_host.hpp`, `src/ram_host.cpp` | `ribbit::RamHost` -- the RAM host as a base class. Pure virtual: `vendor_id()`, `vendor_version()`. Virtual, with defaults: `api_path()` (`/<vendor_id>-api`), `default_listen()` (`0.0.0.0:8800`), `handles()`/`operation()` (the vendor's own operations, run in the memory itself; none by default), `private_service()`, `region_service()`. A vendor's `main()` is `return MyRam().run(argc, argv);` |
| `include/ram_client.hpp`, `src/ram_client.cpp` | `ribbit::RamClient` -- the client as a base class. Pure virtual: `vendor_id()`, which names the endpoint exactly as the host's does. `connect(host, port)`; cell `write`/`read`/`remove`, where a read with `after` and `wait_s` is held by the host; `call(op, body)` for the vendor's own operations. Failures are `frogram::Unreachable` or `frogram::Refused`, never a fallback |
| `include/ram_memory.hpp` | the memory: rows with their own multi-reader/single-writer lock, insert-only lock-free tables, waits and wake-ups, a fixed window of each variable's recent writes for held reads |
| `include/frogram.hpp`, `src/frogram.cpp` | the session: two connections and a dedicated data socket to the host, the wire engines, held reads |
| `include/fnwp_*.hpp`, `semwire.hpp`, `semcodec.hpp`, `semtpl.hpp`, `dataplane.hpp` | FNW1, the semantic wire: templates, SAME/DIFF, the data socket's fair segments |
| `include/ebr.hpp` | epoch-based reclamation for lock-free readers |
| `tools/ribbit_ram.cpp` | `ribbit-ram`: the host with no region, answering FrogNet's default endpoint `/ram.php` -- the smallest vendor, and what the platform tests run against |
| `tools/tuple_write.cpp` | `tuple-write`: cells written and read from a script, one JSON line at a time |
| `tests/` | the platform's tests, and `tests/reference/` -- FrogNet's Python semantic engine, the oracle the C++ wire is compared with |

## Qualification

```
./qualify.sh
```

builds everything into `bin/` and runs the platform's gates: `semwire`, `semcodec`, `semtpl` (the C++ wire against
the Python reference, with mutation tests), `fnwp` (the wire engines end to end, on the default and a vendor-named
endpoint), `dataplane` (sessions over real TCP, and fan-in over a shaped wire), `memory` (row locks only; the write
window's exact answers), `lockfree` (lock-freedom and reclamation), `ramrows`, and `perf`. `STAGES="build memory"`
runs a subset; results go to `results/<stamp>/`.

## Client fixes in the platform (2026-09-29)
Until now these lived only in examples/redis, applied to its own copy of v0.63 at build time. They are now in the
platform itself, so every vendor's library and RAM host gets them:
- `[REPLY_EXTRACTION_PROVEN_V1]` (fnwp_engine.hpp): the client proves the template holds an answer before trusting its
  fields; an answer of another shape goes RESP_RAW instead of being rebuilt as the old shape.
- `[SAME_MISS_FROM_CACHE_V1]` (semwire.hpp, fnwp_engine.hpp; Python half in tests/reference): a RESP_SAME naming an
  answer the client no longer holds is fetched from the host's cache (REQ_SAME_MISS, 0x22); the request is never run a
  second time. The host keeps every REPEAT entry; answers over 64 KB are not kept, and SAME is said only for a held answer.
- `[CLIENT_COALESCES_V1]` (frogram.hpp/.cpp): an identical request already in flight on the session is not sent again;
  both callers get the one answer. A request that must act twice has to differ (carry its own value).
- `[WIRE_ORDER_TURNS_V1]` (frogram.hpp/.cpp): each instance's replies are applied in arrival order, in the caller's turn.
