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
| `tests/` | the platform's tests; the oracle the C++ wire is compared with is FrogNet's live Python engine, `opt/frognet_semantic` |

## 0.73 (2026-11-26): a stored bag is ASCII, in one form
`[A_STORED_BAG_IS_ASCII_V1]` (John: "The stored bag should be ASCII. Convert the binary before storing it.")

- **What was wrong.** A bag written as text was stored as its writer sent it: spaced or not, raw UTF-8 or escaped,
  `1.50` or `1.5`. The RAM-answer handler (`ramrows.hpp`), which rebuilds an answer from a difference and hands that to
  the reader, wrote its own form: compact, raw UTF-8, every number a double. So a reader was given different bytes for
  the same cell depending on whether its answer came whole or as a difference, and an integer past 2^53 came back
  changed. `qualify.sh` stages `fnwp` (556 of 2,000 answers differed) and `ramrows` (none exact) failed on it.
- **Now.** `include/bagtext.hpp`, `ribbit::stored_bag(text)`: one pass, no tree, in `ramm::Memory::write` -- every bag
  written as text, from the network or from this process, is stored compact and ASCII (`\uXXXX`, a surrogate pair
  above U+FFFF), an integer as its digits, a float as its shortest round-trip text. It is byte for byte what the wire
  writes for the same value (`semtpl::dumps_ascii`) and what a bag written as fields already read back as.
  `ramrows.hpp` rebuilds in that form and keeps integers exact (`Json::parse` keeps a number's text).
- **Checked.** `tests/bagtext_check.cpp` (in `qualify.sh`'s `ramrows` stage): 200,008 texts against
  `dumps_ascii(json_loads(text))`, under AddressSanitizer. `qualify.sh`: `fnwp` PASS on 3 seeds and both endpoints,
  `ramrows` PASS, and every other stage but the two races below. Redis's concurrency audit 39 of 39; fdf's
  `test_fdf_app.py` 14 of 14, `memory-order-check.sh`, `daf-check.sh`; the chat example's test; WorldShepherd's whole
  validation (`examples/worldshepherd/RESULTS.md`).
- **A reader sees a change:** a bag reads back in the stored form, not as it was typed. `{"a": 1}` reads back
  `{"a":1}`; `"é"` reads back `"\u00e9"`. The value is the same.
- **Two racing memory tests fail some runs, before and after this work** (`qualify.sh` stage `memory` repeats them and
  reports FAIL if any run fails). Measured on this 2-core machine, runs passed: `test-row-scan-bound` 27 of 30 at
  bc7221b, 22 of 30 at 0.72, 23 of 30 now ("reader missed the final write of 1 rows"); `test-write-window` 197 of 200
  at bc7221b, 196 of 200 at 0.72, 239 of 240 now (the failures are segmentation faults). Not found yet.

## 0.72 (2026-11-25): nobody is asked; a call's time is counted from when it is sent
Found by WorldShepherd's race on John's nodes and in the simulator (`examples/worldshepherd/RESULTS.md`).

- **`[NOBODY_IS_ASKED_V1]`, both reclaimers.** `include/ebr.hpp` (a session's, and the host's request coalescing) kept a
  record for every thread that ever took a guard, freed only with the Ebr, and walked them all twice per finished
  call: a session used by 4,000 new threads a run got slower every run. `include/lf/ebr.hpp` and `src/lf_ebr.cpp`
  (the memory's) kept a record per thread in a fixed array and read every record to move the epoch, and each thread
  handed its retired lists on when it ended. In both, a thread now counts itself into the epoch it enters and out
  when it leaves: one count and one retired list per epoch (three, used in turn), no record of a thread, nothing
  walked, nothing handed on. `tests/ebr_stress.cpp` (8,000 threads, each new) and `tests/lf_ebr_bench.cpp` pass under
  AddressSanitizer and ThreadSanitizer.
  Measured for the memory's reclaimer, 2-core x86: a guard alone costs 4.2 ns before and 17.2 ns after on one
  thread, 9 to 10 ns before and 52 to 95 ns after on 2 to 8 threads (every guard now writes one shared word).
  Through a whole system it does not show on this machine: Redis on the memory, one front, `redis-benchmark -c 50`:
  SET 50.1k to 51.9k/s before, 50.0k to 54.8k/s after; GET 49.6k to 51.5k/s before, 48.7k to 62.2k/s after;
  WorldShepherd's 4,000-ticket race: RAM host CPU 2.7 s before, 2.8 s after. **Not measured: a host with many cores
  and many sessions at once, where one shared word per epoch is the cost of having no walk.**
- **`[A_CALLS_TIME_IS_COUNTED_FROM_WHEN_IT_IS_SENT_V1]`, `src/frogram.cpp`.** "No reply within the timeout" was counted
  from when a call was queued. It is counted from when the sender takes the call to send it, or from the last frame
  the session received, whichever is later: a far end working through what the session sent it is not silent. A far
  end that says nothing for the limit ends every call waiting on it, as before. 28,000 claims at once against a RAM
  host that answers 680 a second (aarch64 under emulation) failed before and pass now.
- Qualified with: `STAGES="build memory lockfree capabilities" bash qualify.sh` (PASS); Redis's concurrency audit, 39 of
  39 families; `rinab/tools/test_fdf_app.py`, 14 of 14, on rebuilt static binaries; `memory-order-check.sh` and
  `daf-check.sh` PASS; the chat and communicator examples' session tests PASS.
- `qualify.sh` stages `fnwp` and `ramrows` failed before this change and after it (answers that differed in bytes
  where a bag held non-ASCII text). Fixed in 0.73.

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
- `[SAME_MISS_FROM_CACHE_V1]` (semwire.hpp, fnwp_engine.hpp; the Python half is in the live engine: core/semcache_wire.py, daemon/engine/session.py, daemon/cache/semcache_db.py, proxy/transport_semantic.py): a RESP_SAME naming an
  answer the client no longer holds is fetched from the host's cache (REQ_SAME_MISS, 0x22); the request is never run a
  second time. The host keeps every REPEAT entry; answers over 64 KB are not kept, and SAME is said only for a held answer.
- `[VENDOR_STREAM_V1]` (semwire.hpp, frogram.hpp/.cpp, ram_host.hpp/.cpp): a vendor's own records on the session's
  permanent sockets, past the semantic engine. `Session::stream(record)` sends one on the request socket (op VENDOR,
  numbered and ordered with every call and post, answered by nothing); the host hands it to `RamHost::stream(peer,
  record)` inline on the session's socket thread; `StreamPeer::push(record)` sends one back on the return socket at
  any time (op VENDOR_PUSH, tagged VENDOR_SEQ); `Session::on_stream` receives it. `RamHost::stream_closed` says the
  session ended. Default: a host speaks none. Used by examples/mqtt.
- `[CLIENT_COALESCES_V1]` (frogram.hpp/.cpp): an identical request already in flight on the session is not sent again;
  both callers get the one answer. A request that must act twice has to differ (carry its own value).
- `[WIRE_ORDER_TURNS_V1]` (frogram.hpp/.cpp): each instance's replies are applied in arrival order, in the caller's turn.

## FD&F's platform work, in the platform (2026-10-17) [PLATFORM_FROM_FDF_V1]
FD&F (`opt/fdf/src/fdf-source/rinab`) built FrogNet core's transport model in C++ without locks, as patches applied to its
own copy of v0.63 at build time. They are now in the platform itself, merged three ways (v0.63 + the patches this platform
already had, against FD&F's result), so every vendor starts with them:

| | |
|---|---|
| client fan-in / fan-out | `[CLIENT_LOCKLESS_V1]` lock-free job lists, an in-flight ring by sequence number, a futex word per caller; `[CLIENT_APPLIER_V1]` the engine and the wire-order turns without their locks; `[CLIENT_COALESCE_LOCKLESS_V1]` coalescing without its mutex; `[CLIENT_LANDING_LOCKLESS_V1]` no lander threads |
| host fan-out / fan-in | `[HOST_FANOUT_V1]` the reader only reads and decodes, a lock-free pool runs the requests, one writer per session drains a lock-free reply queue -- no send lock; `[SESSION_ORDER_V1]` a session's requests run in its order, sessions in parallel; `[HOST_COALESCE_V1]` one execution per request in flight, across sessions; `[DEFERRED_ANSWER_V1]` a held read parks as a continuation, not a thread (`ribbit::defer_answer` / `ribbit::answer_later`); `[WORK_MOVED_NOT_COPIED_V1]` |
| the engine | `[ENGINE_LOCKLESS_V1]` the server engine without its mutex; `[NO_ANSWER_HASH_V1]`, `[PROOF_BYTES_FIRST_V1]`, `[ALLOC_ONLY_WHEN_NEEDED_V1]`, `[TEMPLATE_PRECOMPUTED_V1]`, `[DECODE_ALLOC_ONCE_V1]`, `[SAME_AS_LAST_ANSWER_V1]`, `[CLIENT_KNOWN_SHAPE_V1]`, `[PRIMED_TEMPLATES_V1]`, `[PRIMED_LEARNED_ONCE_V1]` |
| the memory | `[FIRE_KEYS_ONCE_V1]`, `[PUBLISH_IN_ID_ORDER_V1]`, `[EBR_READER_PER_INSTANCE_V1]` |
| sessions | `[SESSION_IS_LIVENESS_V1]` / `[SESSION_PEER_V1]` (the vendor hears `session_up` / `session_down`), `[SESSION_EPOCH_V1]`, `[KEEPALIVE_5S_V1]`, `[HTTP_FRONT_V1]`, `[REFUSAL_SAYS_WHY_V1]`, `[CALL_TEXT_V1]`, `[FUTEX_PORTABLE_V1]`, `[WINDOWS_PORTABLE_V1]` |

Added here: `[NOBODY_WAITS_V1]` `Session::call_async` (coalesced like `call`); `ribbit::Ordered` (a vendor's own in-order
work on the host's pool); `ServerEngine::handle` takes the writer's actions too; Redis's `[SHAPE_NEEDS_AN_OBJECT_V1]`.
Removed: `[VENDOR_STREAM_V1]` (records past the semantic engine). Not taken (RINA-region hooks, FD&F's alone):
`[HOST_ANNOUNCES_ITSELF_V1]`, `[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`, `[REGION_COALESCE_PER_SESSION_V1]`. Built as C++20.

**An app's RAM host decides its own writes** (`[THE_APPS_RAM_HOST_DECIDES_ITS_WRITES_V1]`, 2026-11-25). Who may write a
cell, and what it may say, is the app's rule, and the app's RAM host enforces it: every RAM host is derived from
`ribbit::RamHost` and is not bound by the general one. A host that has such rules overrides `rules_on_cells()` to
say so and `refuse_cell(session, service, variable, instance, bag)`, which is asked of every write that arrives on
the network API, in each of its three forms (the skimmed write, the JSON write, the cell wire), once its coordinates
and its bag are known. It returns why the write is refused, or "" to take it. A host that does not say so is asked
nothing and pays nothing. First user: `examples/worldshepherd/worldshepherd_ram.cpp`, which keeps what it knows (who
owns a name, which sessions are up) as cells in a service of its own (`#...`, never served to the network).
`fdf-app config APP RAM --host RAM=PROGRAM` serves a RAM with such a host (`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`).

**An opening session watches the connections it has made** (`[AN_OPENING_SESSION_WATCHES_ITS_OTHER_SOCKETS_V1]`,
`src/frogram.cpp`, 2026-11-25). A session opens three connections in turn. When the host is replaced between them,
the first is to a host that is gone and the next to its successor, which holds a half pair for 10 s; the session
used to wait that long, blind to its first connection having ended. It now fails at once and the caller opens again.
Measured in WorldShepherd's acceptance study: recovery 10,120 ms before, 596 ms after.

## The memory's verbs in binary (2026-10-17) -- what MQTT and Redis on the memory use
A participant that uses the memory as memory writes its truths and holds triggers on families; these are the verbs
for that, as frames on the session's permanent sockets, past the semantic engine (frogram.hpp, ram_host.cpp):

| | |
|---|---|
| `[EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1]` `Memory::write_nowait` | a non-waiting write is the cell's own request on the semantic wire: a DIFF against that cell's reference; never packed with others |
| `[MOVING_WINDOW_REFERENCE_V1]` engine | a cell N of a window (`N`, or `<writer>/N`) is differenced against cell N-1; when N-1 was the template's last cell on the session, only the instance of the location travels |
| `[STRING_SPLICE_V1]` codec | a changed string field in a DIFF travels as its changed middle against the reference |
| `[NUMBER_DELTA_V1]` codec | in a DIFF, an integer, a float (its IEEE bits) or a decimal-integer string travels as the zigzag varint of how far it moved from the reference, when that is shorter (types 9, 10, 11) |
| `[REPEAT_REPLAYS_VALUES_V1]` engine | a REQ_REPEAT is answered with the values its request decoded to (kept whole), not by decoding its difference again against a reference that has since moved |
| `[NO_WRITE_ACK_V1]` engine | a write nobody waits for asks for no answer (FLAG_NO_ANSWER); only a refusal comes back; its frame carries no request hash |
| `[ROWS_ON_THE_SEMANTIC_WIRE_V1]` `rowcodec.hpp` | rows given to a reader (read after an id, a standing trigger) travel as DIFFs against the session's previous row of their shape |
| `[ROW_REFERENCE_OF_K_V1]` `[ROW_RECORD_COMPACT_V1]` `rowcodec.hpp` | the reference is the one of the shape's last 8 rows with the most equal fields, named in the record; record headers are varints. Test rows: 81.0 -> 43.6 B a row |
| `[NO_WRITE_ACK_V1]` | nothing waits for a write and nothing answers one: a write frame holds no in-flight slot; only a refusal is answered (counted in `stats().posts_refused`) |
| `[READS_AFTER_IN_BINARY_V1]` `Memory::read` with `after >= 0` | a held read in binary (op 0x7E), on the host's pool |
| `[RAW_ROWS_V1]` `Memory::read_raw` | the same, each bag left as its text (`RawCell`) for a reader with its own format |
| `[FOLLOW_STREAM_V1]` `Memory::follow` | a standing trigger: one request (op 0x7D), then the family's rows as they are written, in write order, until the session ends; delivered on the session's reader thread. The host's follower is a thread parked in the memory's wait |
| `[IN_FLIGHT_BY_SEQ_V1]` | an in-flight request is found by its sequence number, not its slot |

## Typed tuple and capability interface

`ribbit_pp.hpp` adds `TupleName {indexA,indexB,indexC}`, `Tuple` and derived
`Capability`. Set/get/held read share that name type and read results are JSON
bags. See [API++](../docs/API-PLUS-PLUS.md#tuple-base-class-and-named-capability-operations-tuple_interface_pp_v1)
and run `tools/check_capabilities.sh` for the focused positive/negative and mutation gate.
The base API is unchanged.

## FD&F's write contract, for a vendor that serves FD&F's clients (2026-11-02) [OWN_FLAG_DECIDES_V1]
`RamHost::own_flag_decides()` (default false: `[GONE_WITH_ITS_WRITER_V1]`, everything a session writes goes with it).
A vendor that says true answers writes as FD&F's RAM manager does (`fdf-ram`, and fdf-ram before it) (`[OWNED_CELL_GOES_WITH_THE_SESSION_V1]`): a write with
`"own":true` goes with its session (refused without one: plain HTTP has none), any other stays until written again or
removed, and `"permanent":true` is refused (this host keeps no file). First used by the Team app's team RAM on a lead's
phone (`opt/fdf/src/openeoc-app/android/teamram`), which passes OpenEOC's own app test as every RAM.
The roles (`databasehost.frognet`, `databasehost_control.frognet`) are not RAM hosts: each is one host's `api.php` on
port 80, with held reads in api.php (`[ROLES_ON_API_V1]`, `AI_READ_FIRST.md` section 10a). This contract is for
services: the region's RAM manager and the AppRams.
