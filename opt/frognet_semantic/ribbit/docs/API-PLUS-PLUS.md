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

## Gone with its writer (platform) [GONE_WITH_ITS_WRITER_V1]

When a participant's session ends, everything it wrote disappears: the entire family it wrote is gone.

This is the platform's own behaviour (`ram_memory.hpp`, `ram_host.cpp`, `rowcodec.hpp`, `frogram`, `ribbit::Space`),
not something API++ adds.

**Who owns a row.** A row belongs to the session that wrote it last. Only that session's own writes count:

- its cell writes;
- the platform's `write` operation.

A vendor operation's writes are the region's, and stay.

**How readers learn of it.** When the session ends, the host removes each of its rows before the vendor's
`session_down`.

- Each removal takes its place in the family's write order, exactly as a write does: an id, the variable's and the
  service's windows, the readers' words, the row's triggers.
- Every read held on that family is handed the row with `gone` set, in order.
- On the wire, a gone row is its own record (row codec head `GONE`): its id and coordinates, with no bag.
- A read of what is there now never returns a gone row.

**What API++ does with it:**

- `subscribe` skips gone rows (they are not publishes).
- `capable` reports a capability that went away (`Capable::gone`).
- The answerer is handed each request of an asker that is gone, with `SpaceRow::gone` set.

That last point is how a region learns a front has died, with nothing but writes and held reads.

## Linger, by family: the writer's choice [LINGER_BY_FAMILY_V1]

What happens to a family's rows when the writer's session ends is the writer's choice. It is an application decision,
not the platform's. The writer makes it per family, before the family's first write:

| `Space::linger(space, topic, …)` / `Session::linger(service, variable, …)` | when the session ends |
|---|---|
| `Space::GOES` (0, the default) | the rows go at once, as above |
| `N` seconds | the rows stay N seconds, then the host's reaper removes them, each as gone, in order |
| `Space::STAYS` (−1) | the rows stay until written again or removed |

**How it travels.** The choice rides on the frame that names the family on the wire: `F_LINGER` on a cell write's
`F_NAMES`, once per writer thread per family. Nothing is added per write.

**How the reaper works.** At session end the host:

1. removes the rows that go at once;
2. hands each linger the remaining rows chose to its reaper: one thread, a lock-free incoming list, and a condition
   variable whose mutex guards only its own sleep.

When an entry's time comes, the reaper removes only the rows that session still owns. A row written since by someone
else is theirs, and is not reaped.

**Typical choices:**

- A front's requests to a region go at once: the region learns the front is gone.
- A publish from a client that publishes and exits stays a few seconds, so a slow subscriber still gets it.
- A retained message stays.

## Held reads: watch and follow [STANDING_WATCH_V1]

Every API++ read is a `ribbit::Space` held read, each on a thread of its own, handed batches in the family's write
order.

| | the memory here | the memory elsewhere |
|---|---|---|
| `Space::watch` | the memory's cursor on the family, with a hook: a write bumps the read's word, and the thread reads by position | a held read, asked again after each answer; what has not been read stays in the memory (a ring may move past a slow reader, and the reader is told) |
| `Space::follow` | the same as `watch` | one standing follow on the session: the memory streams each batch as it lands into the read's own lock-free list. No request per batch. What has not been handled waits in the reader's process. For readers that keep up |

`subscribe`, `capable` and the `Answerer` use `watch`. The `Asker` uses `follow`.

[SERVICE_PAST_AN_ID_V1] A memory cursor on a whole service, started past an id, used to read a variable's rows only
when that variable was next written. It now reads every variable's rows past the id on its first read.
`test-family-hooks`: 3 of 3 rows with the fix, 0 of 3 without.

## Redis and MQTT on API++

**MQTT** (box `front/src/region_client.cpp`, client library `libram/libmosquitto_ram.cpp`):

- Publishes go through `Publisher` / `subscribe`. Gone: the box's own per-topic counter and both hand-written gap
  checks.
- The topic directory uses `announce`. Wildcard filters use `capable`. Gone: the box's and libram's own directory
  watches.
- Retained messages were one tuple per topic, `(mqtt.retained, <topic>, hwm)`, written by every publisher. Each writer
  now has its own cell (`offer`), and the reader takes the newest (`latest`).
- Linger:
  - box: its retained messages stay when it goes; everything else goes with it;
  - libram: publishes and directory entries stay 5 s (for a client that publishes and exits); retained messages stay.

**Redis** (`src/front.cpp`, `src/redis_ram.cpp`, `src/region.cpp`):

- The front is an `Asker`. Its requests are `(redis.ask, <front>, …)`, and it reads its replies with one standing
  `follow`.
- The region is an `Answerer` on `Space("here")`. Gone: the host's hand-written reader loop.
- The front's one call is gone (`op=redis "h <front>"`, and with it the region's `bind` and `session_down`). When a
  front's session ends, its request cells go with it. The region is handed each one as gone, and that connection
  leaves (`request_gone`).
- `run_two_fronts.py` check 9 ("B stops: its clients leave the region") is green, and red with `request_gone`
  disabled.

Measured (lizard, NLOC / total CCN), main against this branch:

| | NLOC | CCN |
|---|---|---|
| Redis `src` | 7,840 → 7,536 | 4,561 → 4,418 |
| MQTT box `region_client.cpp` | 779 → 750 | 346 → 333 |
| MQTT `libmosquitto_ram.cpp` | 446 → 424 | 339 → 325 |
| MQTT `region` | 896 → 875 | 357 → 351 |
| platform `cpp/include` + `cpp/src` | 11,440 → 11,903 | 3,862 → 4,042 |

The platform grows by the shared lock-free primitives (moved in from Redis), API++, gone-with-its-writer, linger and
its reaper, and `Space::follow`. Redis and MQTT shrink.

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
| `tests/test_ribbit_pp.cpp` (`test-ribbit-pp PORT`) | One process holds the memory, and every participant is its own session to it, as on separate machines. No check waits by re-reading the memory: each waits on what its held reads were given. | `-DPP_BROKEN=1..5` (`ribbit_pp.cpp`), `-DGONE_BROKEN=1..2` (`ram_host.cpp`) and `-DLINGER_BROKEN=1..2` (`cellwire.hpp`, `ram_host.cpp`), each red on its own check (below) |
| `tests/test_lf_primitives.cpp` (`test-lf-primitives`) | Each in-process primitive under 4 threads. | `-DLF_BROKEN=1..5` |

`test-ribbit-pp`:

| check | green | `PP_BROKEN` | red |
|---|---|---|---|
| 4 writers × 1,000 publishes, each on its own session, one subscriber | each writer's 1..1,000 in its order, 4,000, missed 0 | 1 (every publish is count 1) | got 53 |
| a ring of 8 moves 101 publishes past a held-up reader | missed 92 | 2 (subscribe never says) | missed 0 |
| "resize" announced by A and B before the finder, C and D after (plus two "print") | ABCD, once each | 3 (directory read from now on) | CD |
| three writers offer, then the first offers again | w3, then w1 | 4 (first writer's, not newest) | wrong writer |
| 3 askers × 200 requests made before the answerer exists | 600 replies, each to its own asker | 5 (answerer reads from now on) | 0 |
| An asker makes 3 requests and announces itself, then its session ends | The answerer is told 3 requests gone and the finder 1 capability gone; a fresh read finds nothing left | `GONE_BROKEN=1` (host keeps a session's rows) | 0 gone, rows left behind |
| (same) | (same) | `GONE_BROKEN=2` (wire drops the gone record) | 0 gone |
| One session writes one row each to families marked goes, stays 2 s, and stays; the session ends | goes at 0.00 s; stays2 at 2.00 s; kept still there after 3.5 s | `LINGER_BROKEN=1` (the linger never travels) | stays2 at 0.00 s, kept gone |
| (same) | (same) | `LINGER_BROKEN=2` (the reaper never reaps) | stays2 never goes |

Both tests run in `cpp/qualify.sh` (stages `build`, `lockfree`).

## Tuple base class and named capability operations [TUPLE_INTERFACE_PP_V1]

These are additions to `ribbit_pp.hpp`/`ribbit_pp.cpp`, not changes to the base
memory, wire, `Space`, or existing `announce`/`capable` functions.

```cpp
ribbit::pp::TupleName name{"equipment", "capability", "sensor-42"};
ribbit::pp::Capability capabilities(space);
capabilities.SetCapability(name, ribbit::pp::Tuple::ParseBag(
    R"({"temperature":true,"unit":"C"})"));
bool available = capabilities.CheckCapability(name);
auto result = capabilities.GetCapability(name); // JSON bag
```

`TupleName` is exactly three independent strings: `indexA`, `indexB`, `indexC`.
They map directly to the existing base API's space/service, variable/topic, and
instance. An empty B or C leaves that coordinate open on a read. A names a space;
empty A is rejected, as the existing binary read requires a service. Strings are
never concatenated and separators inside a coordinate carry no special meaning.
Set and withdrawal require a complete name, never a family.

`JsonBag` uses the platform's existing `pyv::Obj` JSON model, with an object root.
`Tuple::ParseBag` validates text into that type. It does not use the legacy small
JSON parser that represents all numbers as doubles. JSON integer values must fit
the current wire's signed 64-bit field; larger quantities can be JSON strings.
Nonfinite numbers, binary values, malformed objects, unsupported integer values,
NUL coordinates and coordinates exceeding read frame limits are rejected before
writing. These checks are `++` validation; no core wire format is changed.

| Method | Operation |
|---|---|
| `Tuple::Set(name, bag)` | Validate and delegate to `Space::write` at those three coordinates. |
| `Tuple::Get(name)` | Delegate to `Space::read`; exact tuple or family. |
| `Tuple::HeldRead(name, after, wait_s)` | Delegate to the base held read; update the caller's write-order cursor. |
| `Tuple::Watch(name, callback)` | Deliver current matches, then changes through the base held watch, without a snapshot/hold gap. |
| `Capability::SetCapability(name, bag)` | Set an active capability at that exact tuple. |
| `Capability::WithdrawCapability(name)` | Write inactive truth at that exact tuple. No delete or foreign-cell maintenance. |
| `Capability::CheckCapability(name)` | True if the tuple/family contains an active capability. |
| `Capability::GetCapability(name)` | Get the JSON result including explicit inactive state. |
| `Capability::EnumerateCapabilities(name, include_inactive=false)` | JSON result of the selected tuple/family; active only unless requested otherwise. |
| `Capability::WatchCapabilities(name, callback)` | JSON results for present capabilities, updates, withdrawals, and session loss. |

All reads and callbacks return a JSON bag with the same result shape:

```json
{
  "rows": [{
    "name": {"indexA":"equipment","indexB":"capability","indexC":"sensor-42"},
    "bag": {"temperature":true,"unit":"C"},
    "id": 17,
    "gone": false
  }],
  "after": 17
}
```

Absent tuples and held-read timeouts have empty `rows`. A present empty bag has
one row with `bag:{}`. A session-ended row has `gone:true` and `bag:null` and is
reported by a held read/watch, not a current snapshot. Explicit withdrawal is
`bag:{"_capability_active":false}`; that key is reserved by `SetCapability`.
Malformed state, such as the string `"false"`, is never an available capability.
The other fields are application-owned metadata. There is no built-in device
registry, provider naming scheme, business calculation, or topology dependency.

`Tuple` holds a reference to `Space`. It keeps no capability cache or mutable
registry. As with base `Space::watch`, the Space and callback state must outlive
all watches; the callbacks do not capture the wrapper object. Writers still own
the tuples they publish by the application contract. Neither a tuple name nor a
metadata field confers authenticated authority; this layer does not add ACLs to
core or prevent another authorized raw writer from addressing that tuple.

A write returning means it was handed to the base write path, not that another
consumer ran. Observe results using held reads. Session lifetime and linger are
still the existing base behavior. These functions provide no persistence,
exactly-once effects, atomic read-modify-write or cross-tuple transaction.

### Focused test gate

```sh
cd opt/frognet_semantic/ribbit/cpp
./tools/check_capabilities.sh
```

The gate runs local-memory and actual-session positive/negative cases, the
existing API++ regression suite, and three deliberate semantic mutants. Mutants
must fail behavior assertions and reach their failure summary; a timeout/crash is
not accepted as detection. Standard platform qualification also builds and runs
`test-capabilities` (`STAGES="build capabilities"`).

Atomic CPU compare/exchange is used inside the platform. There is no public
atomic tuple compare-and-swap operation in the inspected base API. A wrapper
performing get/compare/set would not provide atomic CAS and is not offered here.

## Tuple helpers: Exists, WaitUntil and SetMany

These methods are on `Tuple` and inherited by `Capability`. They use the same
TupleName and native JSON bag types.

- `Exists(name)` tests whether a current tuple/family has any rows. An explicit
  inactive capability still exists; use CheckCapability to test availability.
- `WaitUntil(name, predicate, timeout_s=30)` first checks the current full result,
  then uses held reads to wait for changes. The predicate is reevaluated against
  the complete current family, not just its changed rows. The read watermark is
  captured before the snapshot so a write in the snapshot/hold window is seen.
  The returned JSON bag adds `matched` and `timed_out`. Zero timeout performs one
  immediate check. Invalid predicates/timeouts are rejected; predicate exceptions
  propagate. The base wire's millisecond wait resolution applies and predicates
  are not preempted. No application polling timer or private wait is added.
- `SetMany(vector<TupleWrite>)`, where TupleWrite is a pair of TupleName and JsonBag,
  validates and serializes every entry before submitting independent base writes
  in input order. A bad later entry therefore causes no writes. Empty batches are
  allowed and repeated names retain input order. This is not an atomic transaction:
  a transport failure can leave earlier submitted writes applied, without rollback.

The focused gate includes snapshot-to-hold writes, full-family predicates, timeout
and exception paths, presence versus inactivity, batch order, and validation before
side effects. Three additional deliberate mutants must fail: Exists always false,
WaitUntil never holding, and SetMany validating while it writes.

### Explicit connection lifetime

`RibbitConnection` owns the base `Space` session:

```cpp
ribbit::pp::RibbitConnection connection;
connection.ConnectRibbit("127.0.0.1:8959", "myapp");
{
    ribbit::pp::Tuple tuples(connection.GetSpace());
    tuples.Set({"devices", "desired", "sensor-42"},
               ribbit::pp::Tuple::ParseBag(R"({"interval":30})"));
}
connection.DisconnectRibbit();
```

`ConnectRibbit(where, vendor)` accepts the same location modes as `Space`:
`HOST:PORT`, `here`, or `here:HOST:PORT`. A second connect without disconnect
throws `std::logic_error` and preserves the current session. Empty or NUL-bearing
arguments throw `std::invalid_argument`; base connection errors propagate and
leave the object disconnected. There is no implicit retry or global session.

`DisconnectRibbit()` releases the session using the base flush/destruction
behavior. Repeated calls are harmless. Destruction also releases an owned session.
Disconnect is not a guarantee that asynchronous writes landed. Existing
session/linger rules govern the writer's tuples, including their removal.

`IsConnected()` reports whether this object owns a session; it is not a network
health probe. `GetSpace()` returns that session, or throws `std::logic_error`
while disconnected. Reconnect creates a fresh session and does not replay writes
or recreate borrowers/watches.

Finish concurrent operations and destroy objects borrowing `GetSpace()` before
disconnect or connection destruction. The base watch API has no cancellation
handle: keep a watched session alive for the watch's lifetime. These helpers do
not make disconnect safe during an active watch or held read. Connection lifetime
operations require caller serialization; the wrapper introduces no locks.
