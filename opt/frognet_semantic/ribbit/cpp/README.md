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
