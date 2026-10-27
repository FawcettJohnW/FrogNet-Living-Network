# API++ — atomic operations on the shared RAM [API_PLUS_PLUS_V1]

The six operations once put on the wire here (cas, swap, add, take, take_first, batch, decided in the memory and
answered per call) are removed: they were request/response against the memory, which the design does not do. Producers
write only into their own space; consumers hold reads. API++ is being rebuilt as convenience functions over write and
held read (ribbit::Space), generalized from what Redis and MQTT each built by hand.

## The primitives, in a region (`lf/apipp.hpp`)

For a vendor that keeps its own memory (immutable values behind atomic pointers, reclaimed by epoch — `lf/ebr.hpp`):

| primitive | promise | was, in Redis | was, in MQTT |
|---|---|---|---|
| `WaitWord` (+ `Watching`) | a waiter registered before it checked is always woken by a writer's `bump()`; a bump with nobody parked writes nothing | `WaitWord`, `bump`, `watch` (memory.cpp); the deadline thread's word (region.cpp) | a front's `gen`/`waiters`/futex (mqtt_region.cpp) |
| `QuietSpan<K>` | inside a span nothing is woken; at its end each distinct key once | `begin_quiet`/`end_quiet` (MULTI/EXEC, `write_many`) | `t_defer_wakes`/`t_wake_list` (`[BATCHED_WAKE_V1]`) |
| `TakeStack<T>` | every push taken exactly once, each producer's in its order | the deadline thread's `incoming` | a front's deliveries (`push`/`take`) |
| `cas`, `swap`, `take`, `update` | one value behind an atomic pointer: replace if unchanged; replace and hand back; exactly one taker; decide-and-retry with no lock | `replace_if`, `exchange`, `take_variable`, `update` | `claim` (session owner) |
| `futex_wait`, `futex_wake` | sleep on a word until it moves | memory.cpp, region.cpp | mqtt_region.cpp |

## Tests

| test | what it holds | broken on purpose |
|---|---|---|
| `tests/test_apipp.cpp` (`test-apipp`) | each primitive under 4 threads | `-DAPIPP_BROKEN=1..5`: lost pushes (crash), 59,493 repeated totals, 2,000/2,000 double takes, 877 lost wake-ups, a duplicate delivery |

It runs in `cpp/qualify.sh` (stages `build`, `lockfree`).

## Redis and MQTT on API++

Redis keeps no copy of the platform any more: its own `ebr.hpp`, `ebr.cpp` and `lfmap.hpp` are gone (the process had
two epoch reclamations, its own and the platform's; it now has one), and its wait word, futex calls, quiet spans
(EXEC), `WaitReg`, the deadline thread's stack and word, and every compare-and-swap, swap and take in its memory are
API++. MQTT's fronts take their deliveries from a `TakeStack` and park on a `WaitWord`; its futex helpers are gone. Its
batched wakes stay its own: its pacing reads the pending list in the middle of a batch, which `QuietSpan` does not offer.

Measured (lizard), main against this branch:

| | NLOC | CCN total |
|---|---|---|
| Redis `src` | 7,840 → 7,560 (−280) | 4,561 → 4,429 (−132) |
| MQTT `region` | 896 → 875 (−21) | 357 → 351 (−6) |
| platform `cpp/include` + `cpp/src` | 11,440 → 11,739 (+299) | 3,862 → 4,001 (+139) |

The same, before and after:

| | main | API++ |
|---|---|---|
| Redis two_fronts / same_after_raw / big_replies / concurrency | 15/15, PASS, PASS, 39/39 | 15/15, PASS, PASS, 39/39 |
| Redis 7.2.11's own suite, 10 files | incr 31, string 79, hash 70, set 114, list 261, zset 316, keyspace 56, stream 60, stream-cgroups 56, multi 35 | the same, file for file |
| MQTT two_boxes | 13/13 | 13/13 (twice) |
| Mosquitto 2.1.2's suite, through the fronts | 272 of 292 runs pass (the 4 `22-http-api-*` tests fail) | 272 of 292, the same 4 |
| platform qualify | FNWP e2e 1 fail per seed, ramrows FAIL | the same, and test-apipp PASS |
