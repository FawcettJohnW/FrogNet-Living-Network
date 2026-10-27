# API++: convenience functions over the shared RAM [API_PLUS_PLUS_V2]

The base API (`ribbit_space.hpp`, `frogram.hpp`) writes a value, reads one, and holds a read on a family. API++
(`cpp/include/ribbit_pp.hpp`, `cpp/src/ribbit_pp.cpp`) is a small set of convenience functions built only from those.
It adds nothing to the memory and nothing to any host. Every function does one of two things:

- writes into the caller's own space; or
- holds a read on someone else's.

No function is a call that anyone answers. Nothing crosses between machines except what the RAM itself carries.

The functions are generalized from what the Redis and MQTT conversions each built by hand.

## Read this first: publish/subscribe here is not a broker

`Publisher` / `subscribe` are **not** implemented with traditional publish/subscribe techniques. There is:

- no broker;
- no subscription list;
- no fan-out server;
- no acknowledgement;
- no queue.

A publish is a write into the publisher's own cells. A subscription is a read held on a family. These functions are
offered **for convenience only**, so a program that already thinks in topics has a place to start.

Users are encouraged to explore the proper architectures:

- each producer writes its truth where it lives, in its own space;
- each consumer holds reads on exactly the families it needs;
- the shape of the data, not a topic name, decides who reads what.

A design that has been through that exercise rarely needs topics at all.

## The functions

| function | what it writes / reads | from |
|---|---|---|
| `Publisher(sp, space, writer, order, ring).publish(topic, fields)` | Writes cell `(space, topic, <writer>.<n mod ring>)`, the writer's own ring on the topic. The bag carries the caller's fields plus `_b` (the writer) and `_n` (its publish count on the topic, 1, 2, …). Once written, the publish is done. | MQTT `do_publish`, `Space::slot` |
| `subscribe(sp, space, topic, from, on, missed)` | Holds a read on `(space, topic)`. Each publish goes to `on` in the topic's write order. If a writer's ring moved past a slow reader, `missed(topic, writer, gap)` says how many were lost. `from < 0` means from the moment of the call. | MQTT `hold_topic`, `give` |
| `announce(sp, directory, capability, who, about)` | Writes `(directory, all, <capability>␟<who>)`: the participant's own cell saying "I handle *capability*". `about` is whatever a finder should know. | MQTT topic directory |
| `capable(sp, directory, match, on)` | Every participant whose capability `match` accepts. Those announced already are delivered at once. Then a held read starts past the newest id the first read saw, so no announcement falls between the two. | MQTT `hold` (wildcards) |
| `offer(sp, space, topic, writer, bag)` | Writes `(space, topic, <writer>)`: the writer's own last value on the topic. | MQTT retained message (see below) |
| `latest(sp, space, topic, out)` | The newest, by write id, of every writer's last value on the topic. | |
| `Asker(sp, service, me, on_reply).ask(key, …)` | The asker writes its request to `(<service>.ask, <me>, <key>)` in its own space. It holds one read on `(<service>.answer, <me>)`, set up at the moment of construction. | Redis front |
| `Answerer(sp, service, on_request).answer(asker, key, …)` | The answerer holds one read on the whole `<service>.ask` space, from its first write, so a request made before the answerer existed is still read. It writes each reply to `(<service>.answer, <asker>, <key>)`. | Redis region (`[REPLIES_AS_TRUTH_V1]`) |

Not in API++, because they are the base API:

- `write` / `write_fields` (put);
- `read` (get);
- `watch` (a held read).

## What moving Redis and MQTT onto it fixes

- **MQTT's retained message breaks "producers only write into their own space".** Every publisher writes the same
  tuple, `(mqtt.retained, <topic>, hwm)`. `offer` / `latest` give each writer its own cell instead, and the reader takes
  the newest.
- **The Redis front makes one call.** `op=redis "h <front>"` is request/response, and it ties the session's end to the
  front's connections. `announce` plus `Asker` / `Answerer` do the same with writes and held reads.

## The in-process primitives are not API++

`cpp/include/lf/primitives.hpp` (`[LF_PRIMITIVES_V1]`, formerly `lf/apipp.hpp`) is thread plumbing inside one process,
for a vendor's own region memory. Redis and MQTT both use it, and it is never on the wire. It holds:

- `WaitWord`;
- `QuietSpan`;
- `TakeStack`;
- `cas` / `swap` / `take` / `update` on one value behind an atomic pointer.

An earlier API++ put `cas`, `swap`, `add`, `take`, `take_first` and `batch` into the platform memory, decided at the
tuple and answered per call. That was removed (4ab86a2). It was request/response against the memory, which the design
does not do.

## Tests

| test | what it holds | broken on purpose |
|---|---|---|
| `tests/test_ribbit_pp.cpp` (`test-ribbit-pp PORT`) | One process holds the memory, and every participant is its own session to it, as on separate machines. No check waits by re-reading the memory: each waits on what its held reads were given. | `-DPP_BROKEN=1..5`, each red on its own check (below) |
| `tests/test_lf_primitives.cpp` (`test-lf-primitives`) | Each in-process primitive under 4 threads. | `-DLF_BROKEN=1..5` |

`test-ribbit-pp`:

| check | green | `PP_BROKEN` | red |
|---|---|---|---|
| 4 writers × 1,000 publishes, each on its own session, one subscriber | each writer's 1..1,000 in its order, 4,000, missed 0 | 1 (every publish is count 1) | got 53 |
| a ring of 8 moves 101 publishes past a held-up reader | missed 92 | 2 (subscribe never says) | missed 0 |
| "resize" announced by A and B before the finder, C and D after (plus two "print") | ABCD, once each | 3 (directory read from now on) | CD |
| three writers offer, then the first offers again | w3, then w1 | 4 (first writer's, not newest) | wrong writer |
| 3 askers × 200 requests made before the answerer exists | 600 replies, each to its own asker | 5 (answerer reads from now on) | 0 |

Both tests run in `cpp/qualify.sh` (stages `build`, `lockfree`).
