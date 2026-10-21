# MQTT on Ribbit — design (as built)

Incumbent: Eclipse Mosquitto 2.1.2. Contract: MQTT 3.1 / 3.1.1 / 5 on the wire, Mosquitto's config file, its plugin
ABI, and its broker suite (`test/broker/test.py`, every test). Everything behind the wire is open.

## What Mosquitto shares (from reading `src/`; detail in study-*.md)

One process, one thread, one global `struct mosquitto_db db`. Every piece of broker state hangs off it:

| Mosquitto state | where | who touches it |
|---|---|---|
| message store `db.msg_store`, `base_msg.ref_count` (plain int) | database.c | every publish, delivery, retain, ack |
| subscription trees `db.normal_subs` / `db.shared_subs`, leaves holding raw `context*` | subs.c | SUBSCRIBE, UNSUBSCRIBE, fan-out; takeover rewrites every leaf |
| retained tree `db.retains` | retain.c | publish with retain, every SUBSCRIBE |
| per-client `msgs_in` / `msgs_out` (inflight + queued, 8 counters, quotas) | database.c | the fan-out writes into the *subscriber's* lists |
| contexts by id / by socket, expiring sessions, will delay, keepalive ring | context.c, session_expiry.c, will_delay.c, keepalive.c | connect, disconnect, the loop's timers |
| `$SYS` metrics, `persistence_changes`, `last_db_id` | sys_tree.c, database.c | everything; read by the HTTP API thread without a lock |
| ~40 file-static globals (listener tables, plugin state, signal flags, ...) | across src/ | the one loop |

It is lock-free only because it is single-threaded. The fan-out writes into other clients' lists, takeover rewrites
pointers in the shared trees, and counters are maintained, not derived.

The split: what is **shared between connections** — subscriptions, retained messages, which front holds which client,
the messages in flight between fronts, the global counts — moves into the **region**, lock-free. What is
**per-connection** — a client's own message lists, its packets, its keepalive, its will — stays with the front holding
that connection, in Mosquitto's own code, unchanged. A message crosses from one to the other as a delivery.

## Shapes

    stock client --MQTT--> front thread 0 ┐
    stock client --MQTT--> front thread 1 ├── the region (resident in the process)            MQTT_REGION unset
    stock client --MQTT--> front thread N ┘

    stock client --MQTT--> front process --FNW1--> mqtt-ram (RamHost "mqtt", /mqtt-api)       MQTT_REGION=host:port
                                                                                             MQTT_REGION=own (spawns it)

The front is Mosquitto 2.1.2's broker with its shared state removed (`front/src`). `MQTT_THREADS=N` runs N front
threads in one process ([FRONT_THREADS_V1]); each is a whole Mosquitto event loop with its own `db` (a thread-local
pointer, `#define db (*mosq_db)`), its own epoll, its own clients. All listen on the same ports (`SO_REUSEPORT`): the
kernel spreads connections. Process-wide duties stay with front 0: `$SYS`, bridges, the HTTP API, the unix socket.

The same region code runs resident (linked into the front, [CO_RESIDENT_FRONT_V1]) or in `mqtt-ram` across FNW1. The
front calls it through one C ABI (`front/src/region_client.h`); the backend is chosen at `rgn_open`.

## The region (`region/mqtt_region.{hpp,cpp}`) — no mutex anywhere

Lock-free skip lists (`ribbit::lf::LFMap`) with epoch reclamation (`ribbit::lf::ebr`), both now platform pieces
([LF_PLATFORM_V1]). Every shared object is immutable once published, or one atomic word with one writer.

- **Msg** — immutable `{id, topic, payload, props, qos, retain, origin, source id/user/listener port, expiry}`, an atomic
  refcount held by each delivery in flight and by the retained slot. Made only when something must keep it
  ([LAZY_MESSAGE_V1]); ids come from the making front's own range, `(front+1)<<44 | n`, not a shared counter.
- **Filter trie** — one `Node` per level, children in an `LFMap` plus creation order; `+` and `#` children also held as
  direct atomic pointers. At each node: subscriptions by client (each a `SubSlot` whose atomic pointer holds an
  immutable `Sub`; re-subscribe swaps it in place, keeping its order) and share groups (members in join order, a `turn`
  counter for rotation). Mosquitto's walk (`sub__search`, `subs__process`, `subs__shared_process`), tokenising
  (`sub__topic_tokenise`, including its leading empty level and `$share` stripping), QoS rules, no-local,
  retain-as-published and `allow_duplicate_messages` are ported exactly, so who receives what, in what order, is
  Mosquitto's.
- **Retained trie** — one atomic `retained` pointer per topic node. Writing replaces; empty payload withdraws. The
  subscribe-time search is `retain__search` ported exactly.
- **Client** — keyed by client id, never freed: `front` (the claim word: which front holds it), `protocol`, its
  filters. A `Sub` points straight at its `Client`, so the fan-out reads the holder with one load, no lookup.
- **Front** — per front: a lock-free stack of deliveries other fronts produced for its clients, an eventfd (resident)
  or futex word (remote) to wake it, an `armed` flag (set only when it is about to sleep), and its published counts.

### Publish

The publishing front calls `rgn_pub` with views into Mosquitto's own buffers. Under one epoch guard the region walks
both tries; for each match it reads the subscriber's `front`:

- **this front**: the delivery is handed back through a callback while the walk's guard is held — the front runs
  Mosquitto's `sub__deliver` inline on its own `base_msg`. No copy of the message, no copy of the client id.
- **another front**: the message is made (once per publish), a ref taken, and a `Delivery` pushed on that front's
  stack (one CAS). The wake is **batched** ([BATCHED_WAKE_V1]): the push only marks the target; the front writes
  each marked front's eventfd once, before it sleeps, and only if that front is armed. One write per front per loop
  iteration instead of one per message.
  A QoS 0 delivery bound for another front (co-resident or remote) whose undrained deliveries exceed `max_queued_messages` x its
  clients is dropped and counted, as Mosquitto drops QoS 0 for a full subscriber ([QOS0_BACKLOG_V1]). Undrained is
  derived: every thread's count of pushes to that front, less that front's count of what it took — single-writer
  cells, summed by the reader every 32nd QoS 0 push.
- then retain (after the live fan-out, as Mosquitto does).

Before it reads more input, a front that delivered to a front now more than 1024 behind gives up its core until that
front catches up — at most 2 ms per loop iteration, or 50 ms once that front is past half its hold limit
([PACED_PUBLISH_V1]) — the pacing Mosquitto gets for free by reading and delivering on one thread.

### Receive

Before sleeping each front publishes its counts, flushes its wakes, arms, and drains its stack if anything is there;
the eventfd is also in its epoll set. A drained delivery becomes a Mosquitto `base_msg` and goes through `sub__deliver`
into that client's lists — Mosquitto's queueing, inflight, quotas, unchanged.

**Held for room ([HOLD_FOR_ROOM_V1]).** A QoS 1/2 delivery for a connected client whose outgoing queue is full is not
taken: the front keeps it, counts it as still undrained, and offers it again each loop pass (the client's PUBACKs wake
the loop); that client's later deliveries wait behind it. Stock Mosquitto rarely meets this case, because reading a
publish and delivering it are one thread; across fronts, without the hold, the queue limit dropped what the publisher
had already been told was accepted (measured: 35% of acknowledged QoS 1 messages at 2 fronts, 78% at 4 on the Pi 5).
Held deliveries count toward the pacing above, so the publishing fronts slow down instead. Past the hold limit
(`max_queued_messages` x the front's clients, at least 16384) the front delivers anyway and Mosquitto's own limit
applies, as in stock. Remote fronts hold the same way, keeping their own copy of the message, and tell the host how
many they hold with their counts.

**Back-pressure on the same front.** When publisher and subscriber are on the same front, stock Mosquitto's own path
drops a QoS 1/2 message for a connected subscriber with a full queue -- after the publisher's PUBACK. Here the delivery
is held (behind anything already held for that client) and the publisher is *paused*: its socket is taken out of the
front's epoll read set, so TCP and the publisher's own inflight window slow it down while every other client on that
front carries on. Each loop pass offers the held again; when none is held, paused publishers are read again. A client
with deliveries held is never paused (its PUBACKs are what make room). Bounded: past 16384 held, or once the oldest
has waited 1 s (a publisher must not go unread past its keepalive), the held are delivered and Mosquitto's own limit
applies.

**Back-pressure across fronts.** The region marks a publish that sent a QoS 1/2 delivery into a front more than 1024
behind (counting what that front holds). Its publisher is paused at its socket the same way, and read again once the
most-behind other front is under 512; while any is paused the loop looks every millisecond (remote: each look asks
the host for a fresh figure with a counts-only batch). Only the publishers feeding the backlog stop; every other client
on their front -- including subscribers -- carries on.

`$SYS/broker/ribbit/held/{now,total,publishers-paused,delivered-at-bound}` report all of it, summed over fronts.

The bounds apply to every hold (same front, from another front, remote) and to every pause: a delivery is held at most
1 s, at most 16384 per front, and a publisher is never left unread longer than 1 s. Past them, Mosquitto's own queue
limit applies. `RIBBIT_HOLD_MAX` and `RIBBIT_HOLD_MAX_MS` change them, to test them; the front logs the values it runs
with at start ("Hold for room: up to ...").

### Boxes on the memory ([MEMORY_IS_MEMORY_V1], 2026-10-17; docs/CONVERTING-TO-RIBBIT.md)

The rules every conversion keeps: it rides the permanent socket, and its state is memory, addressed by tuple. A box
(one broker process, any number of fronts) answers every client from its own region, in this process: a call is a
call, a publish fans out to this box's clients there and then, and the client is answered at once. Between boxes there
is no server and no message. Each box writes its own truths to its own tuples (service `mqtt`), once, never waiting,
and reads the other boxes' truths where they are, when it needs them. Nothing another box owns is copied into this box
([ONE_MEMORY_V1]): one RAM, one copy of every fact, one owner per tuple. As of 2026-10-19 (`region_client.cpp` header is
the authority):

| tuple | written by (its one owner) | read by |
|---|---|---|
| `(mqtt, <topic>, <box>.<n mod 16384>)` | the box a publish was made on (n: its count on the topic) | every box whose clients subscribe to the topic: a held read per topic, its own thread ([READERS_IN_PARALLEL_V1]) |
| `(mqtt.topics, all, <topic>␟<box>)` | a box, the first time it publishes on a topic | boxes with wildcard subscribers: which topics to hold reads on |
| `(mqtt.retained, <topic>, hwm)` | the topic's retained message (or its clear) | a box whose client subscribes: looked up at its address, handed over for that delivery, never stored |
| `(mqtt.subs, <filter>, <client>␟<box>␟<group>)` | the box holding the client; `<filter>` without `$share/<group>/` | a box answering MQTT 5's "no matching subscribers" ([SUBS_BY_FILTER_V1]) |
| `(mqtt.subs.shapes, all, <shape>␟<box>)` | a box, the first time it uses a filter shape | the same: which filters to look up |
| `(mqtt.box, at, <client>␟<box>)` | the box the client connected to | the box that held it: gives the session up |
| `(mqtt.box, session.<client>, <box>)` | the box that gave the session up (fields `b f k s`; `f` its subscriptions) | the box that took the client over |
| `(mqtt.box, sys, <box>)` | each box: its counts | every box, when it checks a broker-wide limit |

- **Write once, read the rest.** A publish is written once at its topic, whoever listens; a box whose clients
  subscribe holds a read on that topic and is woken. The publisher never looks up who reads it.
- **Answered from the write.** PUBACK, PUBREC, PUBCOMP and SUBACK go when the box has set its truth, as Mosquitto
  sends them. The one exception is MQTT 5's "no matching subscribers" on a QoS 1 PUBACK ([ASYNC_PUBACK_V1]): when this
  box has no subscriber to the topic, the answer covers the other boxes, so it is asked of the memory -- on the
  process's adaptive pool, never on the front thread -- and that client's PUBACK waits for it, in PUBLISH order (MQTT 5
  section 4.6). A PUBACK this box can answer at once waits only behind its own client's earlier ones. No other client and
  no other publish waits.
- **Subscriptions are addressed by filter ([SUBS_BY_FILTER_V1]).** A filter's shape is its levels with each literal
  replaced by `L` (`load/+` is `L/+`). A topic and a shape give exactly one filter, or none, so the lookup reads the
  shapes manifest and then one family per shape in use that fits the topic: no scan of subscriptions, and not 2^depth
  candidates. `region/test_sub_address.cpp` checks the address math against `mosquitto_topic_matches_sub` on 758,200
  filter/topic pairs ($ topics, empty levels, `#` parents, `$share`).
- **Takeover across boxes.** Connecting writes `at`; the box that held the client reads it, writes the session (its
  messages and its subscriptions) as `session.<client>`, withdraws its own `subs` tuples and drops the connection; the
  new box reads the session and writes the subscriptions as its own.
- **Lost is said ([LOST_IS_SAID_V1]).** A reader more than 16,384 publishes behind a writer on a topic finds the
  writer's count jumping and says how many it missed. Nothing paces the writer.
- **One line per subscriber ([ONE_LINE_PER_SUBSCRIBER_V1]).** While deliveries from another front are held for a
  client whose queue is full, this front's own deliveries to it wait behind them. Measured before (16 publishers into
  one subscriber, QoS 1, 2 fronts): the own front took every slot the queue freed, the held were forced in after 1 s and
  Mosquitto dropped 8,460 of 403,540 acknowledged messages. After: 0 lost at 1, 2 and 4 fronts on every QoS 1/2 cell.
  `[DIAG-HOLD]` and `[DIAG-DROP]` in the broker log say when a hold bound forces deliveries and what was full.
- **Several boxes on one machine, or one per node:** `MQTT_REGION=host:port` names the memory (`front/src/mqtt-ram`
  is a plain memory host, no operations); `MQTT_REGION=own` starts one beside the broker; unset, the box is alone.
- `tools/two_boxes.sh`: two brokers on one memory, stock clients -- across-box delivery, retained set and clear,
  wildcards, session moves and takeover -- and the load across boxes beside the load on one.

On the wire ([EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1], v29, ribbit-platform 0.66). Every write and every read rides the
semantic wire; nothing is sent in binary to get around it.
- **A publish is one cell in a moving window.** The box writes publish n to `msg.<box>/<n mod 16384>`, its own
  request, a DIFF against the cell written just before it -- the same box's previous publish
  ([MOVING_WINDOW_REFERENCE_V1]). When that cell was the last one sent, the location does not travel either, only the
  instance. Changed strings travel as their changed middle ([STRING_SPLICE_V1]): a payload that differs in its
  timestamp bytes costs those bytes. The origin trailer goes once per session, not per frame. Nothing answers a write
  ([NO_WRITE_ACK_V1]); only a refusal comes back.
- **The bag is regular JSON, in the wire's own form ([BAGS_ARE_JSON_V1], [CANONICAL_BODY_V1]).** Keys sorted, ASCII;
  payload and properties as JSON strings when they are UTF-8, base64 under `q`/`prq` only when they are not; all four
  keys always present ([ONE_SHAPE_PER_PUBLISH_V1]), so every publish has one shape and one reference.
- **A pass's publishes are numbered as a block** under the box's order lock and written before the loop sleeps, before
  this pass's PUBACKs leave: the family is 1, 2, 3 ... in write order across the box's fronts.
- **Triggers are standing ([TRIGGERS_FOLLOW_V1], v30).** A trigger reads its family's state once, then asks once to
  be given every row written after it (`Memory::follow`, [FOLLOW_STREAM_V1]): the memory pushes each batch as it is
  written. It was a held read per batch -- at 2,000 msgs/s across a link, 7,209 requests for 8,000 messages, each a
  packet and an acknowledgement. The rows are applied on the trigger's own thread as before.
- **Reads.** A row is a DIFF against the best of its shape's last 8 rows on the session -- the one with the most
  fields equal, so a publisher's previous publish when several interleave ([ROW_REFERENCE_OF_K_V1],
  `ribbit/cpp/include/rowcodec.hpp`). Numbers travel as the amount they moved ([NUMBER_DELTA_V1]: a counter +1 is
  1 B, a timestamp a few ms on 2-3 B, a window instance "N" against "N-1" 1 B). The record header is varints
  ([ROW_RECORD_COMPACT_V1]). Measured on the test rows: 81.0 B -> 43.6 B a row (whole rows 151.3 B).
- **Measured** (2-core sandbox, one publish of a 64-byte payload): 248 B as a whole binary frame (v28) -> 73 B; write
  replies 45 B -> 0; across a two-box run 85 MB of rows arrived as 26 MB. `[DIAG-WIRE]` at exit gives each box's bytes.

- **A REPEAT replays values, not an edit ([REPEAT_REPLAYS_VALUES_V1]).** The memory keeps, for each answered request,
  the values it decoded to; a REPEAT is those values. It kept the difference frame and decoded it again against the
  cell's reference as it was by then -- harmless while every changed field travelled whole, wrong for a splice or a
  delta (found by test_window_wire: a retained cell's `live` 1 became 2).

What it replaced, all deleted: mqtt-ram's operations, the MQTT handler messages, batches and their answers, held-read
chunks, per-call round trips, lanes and their sequencer, the same-box shortcut, the pipeline in the front.

### Session handover ([SESSION_HANDOVER_V1])

A CONNECT claims the client by exchanging its `front` word — the newer claim wins, nobody arbitrates. If another front
held it, the connecting front parks the CONNECT where Mosquitto parks a delayed authentication and pushes a
*HandoverWanted*. The holder runs the old context's half of Mosquitto's takeover (its will, its DISCONNECT 0x8E), writes
the session whole (`front/src/session_move.c`, blob "MQS1": protocol, clean flag, expiry, mids, subscriptions, all four
message lists with their messages) and pushes *HandoverDone*. The new front reads it into a ghost context, and
Mosquitto's own takeover code runs on that, unchanged. Deliveries reaching the old front after the claim are forwarded;
those reaching the new front while it waits are stashed and given after.

### Global limits, signals, metrics

- `global_max_clients`, `global_max_connections`, `max_connections`: each front publishes its own counts in its own slot
  ([FRONT_COUNTS_V1]); a check adds the others' to its own exact count. Derived, never a shared counter.
- Signals: the handler bumps a generation per signal; every front acts when its seen generation is behind, and all
  fronts are woken through their eventfds.
- `$SYS`: each front counts into its own row and writes its gauges there ([METRIC_ROWS_V1]); front 0 sums the rows
  when it publishes. The retained count is derived in the region.

## The front's writes ([WRITE_COALESCED_V1], 2026-10-17)

Measured first (gperftools, real time, own1 qos1-4x4, sandbox): 38% of the front's CPU was `send()` to client sockets
and 11% `epoll_ctl` -- Mosquitto writes every packet as it is queued (`packet__queue` -> `packet__write`) and asks for
EPOLLOUT before and drops it after each one: one send and two epoll_ctl per PUBLISH, per PUBACK. The wire to mqtt-ram
was 11% (`ClientEngine::prepare`), the region and Mosquitto's own logic the rest. (callgrind put SHA-256 at 17%: it
runs OpenSSL's software path; the machine has SHA-NI, and OpenSSL uses it outside valgrind.)

Now a queued packet waits for this loop pass's flush (`out__flush`, at the top of `mux__handle`, before the loop
waits): each client with packets queued gets one `sendmsg` with all of them (up to 64 iovecs a call, TLS clients one
SSL_write each as before). EPOLLOUT is asked for only when the socket would block and dropped once the queue drains.
A queue that reaches 64 packets or 64 KB is written at once, so a burst never meets `max_queued_messages` unwritten.
What is queued goes out before a socket closes (a CONNACK or DISCONNECT just before the close still arrives).
One place in Mosquitto read an empty queue as "the socket took it": after SUBACK, retained messages are written only
if `out_packet == NULL`; a queue holding only this pass's packets counts the same (`out_dirty`).

Effect, same machine, CPU per 1k received (front / mqtt-ram), received/s:
qos1-4x4 12.1 / 3.8 ms, 94.7k -> 7.7 / 3.6 ms, 146.7k (stock 145.0k);
qos1-1x1 23.7 / 10.3 ms, 40.6k -> 14.0 / 7.0 ms, 50.5k (stock 144.4k, which loses 23% of acknowledged messages);
qos0-4x4 -> 4.0 / 1.3 ms, 260.6k (stock 421.6k). Suite: own1 and r1 272/276, the same 4 HTTP-API tests as stock.

## Deliveries to a remote front in chunks ([CHUNKED_DELIVERY_V1], 2026-10-17)

Measured with the [DIAG-*] lines (below), own2 qos0-4x4, publishers on front 0 and subscribers on front 1 (the kernel's
SO_REUSEPORT spread decides): front 1 had one held read, answered with everything pending -- ~620 KB of base64 per
answer, on the data socket (236 MB in 5 s on one session) -- and its own publish batches took ~1 s to round-trip
behind it while the host ran each in 20 us; the region's QoS 0 backlog rule then dropped what was bound for it.
Throughput was 14k-258k/s from run to run.

Now a front keeps MQTT_WAITS (8) held reads in flight; the host parks them in the order its session ran them and
answers them oldest first, each with one chunk of at most 128 deliveries, a BATCH_OUT whose first entry is E_SEQ {n}
(1, 2, 3, ... per front). One dispatcher per front at a time, claimed by compare-and-swap; a thread that does not get
it leaves, and the holder looks again before letting go. The front checks the sequence and refuses a gap.

## Allocator and engine tables ([JEMALLOC_V1], [HASHED_ENGINE_MAPS_V1])

malloc/free were 15% of the front (gperftools): the front links jemalloc (Mosquitto's own WITH_JEMALLOC) and so does
mqtt-ram. The engine's tables keyed by a digest or an id (request cache, seen requests, answers by same_id, the
client's held answers, the per-instance references) were red-black trees comparing 16-byte keys at every level --
12% of mqtt-ram in lookups and 13% in the memcmp under them; they are hash tables now (the keys are already random
bytes). The platform qualifier (semtpl oracle, fnwp e2e seeds 1-3, sessions, data socket, memory) passes.

## Diagnostics (MQTT_DIAG=1)

Set in the broker's environment; mqtt-ram inherits it. Every 50 ms:
  [DIAG-FRONTS] (mqtt-ram) per front: pushed, taken, backlog, held, clients, the QoS 0 limit, whether a held read is
               parked; publishes, QoS 0 dropped, paced
  [DIAG-RUN]    (mqtt-ram) per front: batches run, ops, run time sum/max, the longest gap between two
  [DIAG-WAIT]   (front) answers applied by kind and bytes, apply time sum/max, inbox depth, ops and batches in flight,
               held, how far behind
  [DIAG-RTT]    (front) round trips of its requests, average and max
  [DIAG-SESS]   (front) each session's wire: raw/full/diff/repeat/same, data-socket frames and bytes, bytes out/in

## Region counters

`rgn_stats` / op `stats`: `pubs`, `deliveries_own`, `deliveries_pushed`, `wakes`, `wakes_batched`, `waits`, `subs`,
`unsubs`, `retained_set`, `retained_cleared`, `retained`, `messages_made`, `qos0_dropped`, `paced`, `paced_us`,
`fronts`, `ebr_pending`, `ebr_freed`. Each thread counts into its own line; the sum is taken when read.

## Persistence and plugins

`persistence true`: Mosquitto's own writer, fed subscriptions from each client's mirror and retained messages from the
region (`rgn_retained_all`); restore writes them back. Persistence plugins see retained messages through
`retain__persist_view`. Mosquitto's plugin ABI assumes one broker thread, so `MQTT_THREADS > 1` with plugins or
persistence is refused at start, loudly — the shape for those is several front processes on one `mqtt-ram`.
QUESTION-3.md lists every place the contract is met differently, and why.

## Measurement

`harness/run_suite.py` runs Mosquitto's own suite, unmodified, against any `BUILD_ROOT/src/mosquitto`, stock or ours,
recording per test: wall, bytes and packets each way, time waiting for each expected packet, broker CPU, RSS, context
switches (`metrics_wrap.py`); `compare.py` sets two runs side by side. `harness/load_compare.py` drives every broker
with the same C++ driver (`mqtt-load`) and reads broker CPU from /proc. Numbers: RESULTS.md.
