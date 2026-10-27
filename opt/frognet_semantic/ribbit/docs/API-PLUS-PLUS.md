# API++ — atomic operations on the shared RAM [API_PLUS_PLUS_V1]

The base API writes, reads, removes, and holds a read on a family. API++ adds the operations the Redis and MQTT
conversions each had to build for themselves: a read-modify-write done **in the memory, at the tuple, in one call**.
Nothing is read across the wire and written back, so nothing between the read and the write can be lost.

There are two halves, one set of semantics:

| | where | who uses it |
|---|---|---|
| **on the wire** | `ramm::Memory` (ram_memory.hpp), `op=` on every RAM host (ram_host.cpp), `frogram::Memory` (frogram.hpp) | any participant, any language that speaks the session |
| **in a region** | `lf/apipp.hpp`: lock-free primitives on immutable values behind atomic pointers | a vendor writing its own region memory (Redis, MQTT) |

## The operations, on the wire

Every one is one `POST <api>?op=<name>` with a JSON body; the reply says what happened. `frogram::Memory` has a call for
each. A tuple is `(service, variable, instance)`, as for `write`.

| op | body | reply | `frogram::Memory` | what it is |
|---|---|---|---|---|
| `cas` | tuple, `expect`, `bag` | `done`, `id`, `was` | `cas(s, v, i, expect, bag)` | lands `bag` only if the tuple's live write id is `expect` (`0`: only if absent). The loser is told what is there. WATCH, SETNX, a lock, an owner |
| `swap` | tuple, `bag` | `id`, `was` | `swap(s, v, i, bag)` | lands `bag` whatever is there and hands back what it replaced. GETSET, a session takeover |
| `add` | tuple, `by`, `field` (default `n`) | `id`, `value` | `add(s, v, i, by, field)` | adds to a numeric field of the bag (created if absent); `value` is the total after **this** add — every caller its own. INCR, a turn counter, a sequence |
| `take` | tuple | `taken` | `take(s, v, i)` | removes the tuple's value and hands it back; exactly one caller gets it. GETDEL, a hand-off |
| `take_first` | `service`, `variable`, `wait_s` | `taken` (with `instance`) | `take_first(s, v, wait_s)` | the family's oldest live tuple (by write id), taken — a queue's pop. With `wait_s` it is held until there is one: a blocking pop with no lock and no queue server |
| `batch` | `writes: [ {tuple, bag}, … ]` | `ids` | `batch(writes)` | several writes in one call; each lands as a write does, and every reader of their families is woken **once**, after all of them |

`was` / `taken` are `{"id": …, "bag": …}` or `null`. A private (`#`) service is refused, as for `write`.

### How it is atomic

The platform memory has one lock: each tuple's writer lock (John, 2026-09-27: "row level locking and no more"), which
every write of that tuple already takes. An API++ operation decides under that lock — it sees the live value, decides,
and lands its value exactly as a write does (an id, the family's window, its readers' words, the tuple's triggers). So
it is ordered with every other writer of that tuple, and with nothing else: two operations on different tuples never
wait for each other. No lock was added (`test-ram-memory`'s lock gate: one row lock, one per-trigger mutex).

### Each call is its own [EACH_CALL_ITS_OWN_V1]

Identical requests in flight are answered by one execution, on the session (`[CLIENT_COALESCES_V1]`) and on the host
(`[HOST_COALESCE_V1]`) — right for a read, and the platform's rule is that a request which must act twice carries its own
value. Every API++ call acts, so `frogram::Memory` puts `"call": "<session token>.<n>"` in each body; the memory never
reads it. A client of another language must do the same. Without it, four threads adding 1 on one session at once
landed 500 of 2,000 adds, and three takers on three sessions reported 1,153 takes of 500 jobs (653 handed out twice:
the host ran one take and told every waiting taker it was theirs) — `test-apipp-wire`, red before, green after.

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
| `tests/test_ram_apipp.cpp` (`test-ram-apipp`) | each memory operation under 4 threads; batch wakes a reader once for 20 writes (20 as plain writes) | `-DMEM_BROKEN=1` (read, then write back, as without API++): 6,477 of 20,000 adds landed |
| `tests/test_apipp_wire.cpp` (`test-apipp-wire PORT ENDPOINT`) | the operations through real sessions, several at once, on FrogNet's endpoint and a vendor's | red before `[EACH_CALL_ITS_OWN_V1]` (above) |

All three run in `cpp/qualify.sh` (stages `build`, `lockfree`).

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
| MQTT two_boxes | 13/13 | 13/13 (twice) |
| Mosquitto 2.1.2's suite, through the fronts | 272 of 292 runs pass (the 4 `22-http-api-*` tests fail) | 272 of 292, the same 4 |
| platform qualify | FNWP e2e 1 fail per seed, ramrows FAIL | the same, and the three API++ tests PASS |
