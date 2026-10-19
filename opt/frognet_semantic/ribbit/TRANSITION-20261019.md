# TRANSITION — Ribbit MQTT / Redis, 2026-10-19

For the next session. Read all of it before you touch anything. Then read the FrogNet repo core: the FNWP dispatcher,
the proxy's fan-in and the daemon's fan-out. Those already work, and they are the model; you are not here to
reinvent them. Section 2 lists every correction John has had to make, several of them five or more times. If what you
are about to do contradicts any line in it, stop.

Nothing here is committed. Do not commit or push without asking.

---

## 1. THE MODEL

The tuple space IS memory. It is one RAM. Programs on many machines share it the way threads in one process
share RAM.

- **One RAM.** There is one copy of every fact, and it lives in the memory. No box, front or library keeps a copy of
  another owner's facts: no mirror, no cache, no replica, no "local view kept in step". A copy kept in step is a sync
  problem, and the sync problem is what Ribbit exists to remove. If you need a fact, read it where it is, when you
  need it. A read of the one RAM is a read. Do not frame it as a round trip, a cost or a reason to keep a copy.
- **One owner per tuple.** Only the owner writes it. If two writers can set the same tuple, you have broken the
  model: no races, no last-write-wins. Think lockless multithreaded programming.
- **Producers write in their own time; consumers read when and how they choose.** The consumer decides what to do
  about an anomaly (a gap, a missing reference). Nothing at the system level does a round trip or requires
  synchronization. Client-level synchronization needs a damned good reason.
- **A blocking (held) read is how a consumer is notified.** It does not control the producer.
- **Locking.** There is no mutex anywhere client side. The only lock is the server's per-tuple row lock: only a write
  blocks, and only for the one tuple being written.
- **Use the tuple space fully.** `(a, b, c)` can be any names. Design tuples so the thing you need is looked up at
  its address. Example: a topic's retained message is `(mqtt.retained, <topic>, hwm)`, looked up directly, never
  scanned for. "Just fucking look it up. Why are you reading anything?"
- **Where the memory lives is policy.** A node can be its own RAM ("here" mode). That still means one RAM: everyone
  else reaches it there, and nothing is duplicated.
- **One library for every pub/sub system.** Redis and MQTT use the same library (`ribbit::Space`), each in its own
  tuple space.
- **Data, not strings.** A tuple's value is a JSON bag, and you pass it around as data. Decode once at the edge, pass
  the decoded array (`ribbit::BagArr`) everywhere, and encode at the edge. Never print JSON text so that someone else
  can parse it again.
- **Templates.** If a message is a standard shape (a registration, a read request), template it and send the diff,
  not REQ_RAW / REPLY_RAW.

---

## 2. CORRECTIONS JOHN HAS HAD TO MAKE (do not make him make them again)

The repeated ones are marked ×N, with roughly how many times.

1. **Copies of shared state on every box (×many, and again today).** I built per-box replicas twice:
   - MQTT boxes mirrored other boxes' subscriptions, retained messages, client claims and counts into their local
     region.
   - Redis boxes each hold the whole keyspace and replay every box's commands.

   His words: "You seem to have multiple RAM servers here", and "You are completely defeating everything I am trying
   to do." There is ONE RAM.
2. **Treating a read of the one RAM as a cost (×several, today).** I said reading the `sub` tuples "means a read on
   every publish" as if that were a reason not to. "It means reading the SUB tuples from the goddamned shared RAM
   for which there is one and only one." Then I implemented it as a whole-family scan per publish (see section 4).
   Both are wrong.
3. **Message-passing architecture (×5+).** "I am still unconvinced that there isn't a shitload of message based
   architecture here." This is a decoupled system: producers write, consumers read. Nothing reads a publish from a
   socket and passes it on.
4. **Application threads putting bytes on FNWP (×3).** First I used per-thread connections ("use fnwp socket"), then
   app threads sending on the socket ("Jeezus... There is a process for that"). Writes are handed to the session's
   sender thread, which owns the socket, as the FrogNet proxy's `_DaemonWorker` writer does.
5. **One reader thread (×2).** "There should be hundreds or thousands of those. All at once." Use FNWP's fan-in and
   fan-out: each read is its own thread waiting on its own reply, all in flight at once on the one session. "I have
   described it at least 5 times."
6. **Coalescing.** Several readers of the same tuple block as each duplicate is discovered and are released as a
   group. That exists in FNWP; use it (`[READS_COALESCE_V1]` in `Session::call_frame`).
7. **Races and last-write-wins (×2).** "That implies you have someone other than the owner trying to set a tuple, and
   that is against the rules." I removed the writer-order stale-write check: one owner per row.
8. **Scanning instead of addressing (×3).** The retained "high-water mark" scan: "Just fucking look it up." "Have a
   tuple that holds the high water mark." "If the high water is to a topic, then set up the tuples that way!!!" Also:
   no whole-space reads.
9. **Passing strings (×2, angrily).** "Decode once and create a json array that is passed around." "How about we pass
   around data and use it like data? Decode and encode at the edges, but the value of the tuple is a json bag for a
   reason!"
10. **B falling behind.** "B should not fall behind. It can drop cycles and continue... thread the things or use a
    two level algorithm... the readers need to keep up. See what the readers are doing." Profile first, then fix what
    the profile shows.
11. **Not reading the repo first.** "Read the repo core and see what it does. All of this shit works." Look at what
    FrogNet already does before designing plumbing.
12. **Compaction amnesia.** "You have compacted yourself into a complete moron." This document exists because the
    model was lost across a context compaction. Re-read it after any compaction.
13. **Excuses dressed as analysis.** Don't argue that something costs too much when the rule says do it. Do it right,
    measure it, and report the number.

Older standing rules (from memory, still in force):

- **Bags and the wire.**
  - Bags stay JSON on the wire as semantic SAME/DIFF. No binary frames, no base64 bags.
  - A write is a templated REQ_DIFF and a response is RESP_DIFF.
- **Memory-side engine.** It runs each request as a parallel job; nothing waits.
- **Missing references.** If a diff's reference hasn't landed, that is an error return to the application; it is not
  waited for.
- **No fallbacks.** A failed step is logged and retried on the next event. Gone is gone.
- **Thread pools.** Adaptive: up when needed, retired after about 1 minute idle, no cap at cores-1.
- **Benchmarks.**
  - At least two boxes on separate machines with traffic crossing, run on FrogNet's simulator
    (`/opt/frognet_semantic`), not a homemade harness.
  - Throughput does not count lost messages.
  - Give measured side-by-side numbers, not explanations.
- **The front's permanent connection** goes to FrogNet's :9009, not to `mqtt-ram`'s own listener. The bench still
  violates this (see section 4).
- **Conduct.**
  - Keep working without check-ins. One tarball per machine type.
  - No commits or pushes without asking.
  - Don't make him run diagnostics: the software logs its own version and state.
  - Use `pkill -x`, never `pkill -f` (it killed the shell).
  - The Mosquitto suite runs as `su tester` after `chown -R tester`.

---

## 3. WHERE THINGS ARE

- **Tree:** `/home/claude/work/ribbit` (`examples/mqtt/VERSION` v32-semantic, logged in the box's startup line). It is not
  a git repo, and nothing is committed. **A new session starts with an empty sandbox: the tree arrives only as John's
  tarball.** Anything left in a scratchpad is lost (libcheck.sh and the prof_*.sh scripts were, on 2026-10-19).
- **Platform (`cpp/`):**
  - `include/ram_memory.hpp`: the memory. Rows store `ribbit::BagArrP arr`; Cursor, `newest`, `head_of`.
  - `src/ram_host.cpp`: RamHost, the global `MEM`, `RamHost::memory()`, `reply_rows`, `OP_MEM_NEWEST 0x7B`, and
    `CELL_SEQ` for cell writes.
  - `include/rowcodec.hpp`: `encode_rows` (kind 3 = a new shape by names plus a values packet). The Decoder is
    POSITIONAL (`[POSITIONAL_DECODE_V1]`, done today): a diff is applied by index onto a copy of the reference's
    array, and an identical row shares the reference's array.
  - `include/bagarr.hpp`: `BagArr` (names shared per shape, vals in order; `get`/`str`/`num`/`json`/`parse`).
  - `include/cellwire.hpp`: cell write encode/decode to an arr.
  - `src/frogram.cpp`: the client session.
    - Sender thread with a `QUEUE_MAX` 4096 bound.
    - Reader decodes rows into `tl_rows`.
    - `call_frame` coalesces identical in-flight frames.
  - `src/ribbit_space.cpp` / `include/ribbit_space.hpp`: `ribbit::Space`.
    - `where` = `here` | `here:HOST:PORT` | `HOST:PORT`.
    - Calls: `write`, `write_fields`, `read`, `watch` (one thread per held read), `newest`, `slot`.
- **MQTT (`examples/mqtt/`):**
  - `region/mqtt_region.{hpp,cpp}`: the lock-free region (filter trie, retained trie, per-front delivery stacks,
    batched wakes). It has no mutex. Added today: `Region::sub_of(client, filter, &options, &identifier)`.
  - `front/src/region_client.cpp`: the box (Mosquitto front + `Space`). Tuples:

    | tuple | holds |
    |---|---|
    | `(mqtt, <topic>, <box>.<n%16384>)` | publishes |
    | `(mqtt.topics, all, <topic>␟<writer>)` | directory |
    | `(mqtt.retained, <topic>, hwm)` | retained |
    | `(mqtt.subs, <filter>, <client>␟<box>␟<group>)` | subscriptions, by filter, `$share` stripped ([SUBS_BY_FILTER_V1]) |
    | `(mqtt.subs.shapes, all, <shape>␟<box>)` | the filter shapes in use (manifest) |
    | `(mqtt.box, at, <client>␟<box>)` | client claims |
    | `(mqtt.box, session.<client>, <box>)` | session handover, fields `b f k s`; `f` = the client's subscriptions as `"<options>␟<sid>␟<filter>"` |
    | `(mqtt.box, sys, <box>)` | counts |
  - `libram/libmosquitto_ram.cpp`: the client library on the memory (`MQTT_MEMORY`).
  - `tools/build.sh`, `libram/build.sh`, `tools/two_boxes.sh`, `tools/bench_across.py`, `tools/bench_lib.py`.
    Arms `ribbitN` set `MQTT_THREADS=N` (added today).
- **Redis (`examples/redis/`):**
  - `src/front.cpp`: the box, with intents written as fields.
  - `src/db.cpp` + `cmd_*.cpp`: the engine on a LOCAL `g.mem`, i.e. a replica per box. Wrong; see section 4.
  - `tools/build.sh`, `tests/run_two_boxes.py <build> <port>`, `tests/run_concurrency.py <build> <port>`.
- **Simulator env:**
  ```
  FN=/home/claude/fawcettjohnw/frognet-living-network/opt/frognet_semantic
  FROGNET_SEMANTIC=$FN PYTHONPATH=$FN FROGNET_PROXY_ROOT=$FN FROGNET_BIN_ROOT=$FN/../../usr/local/bin LINKS=veth python3 tools/bench_across.py
  ```
- **perf:** `/usr/lib/linux-tools-6.8.0-147/perf`.
- **Lost with the 2026-10-19 morning sandbox:** `libcheck.sh` (10 checks of libmosquitto on the memory),
  `prof_b.sh`, `prof_q0.sh`, `prof_q1b.sh`. Not rebuilt. Scripts worth keeping go in the tree.
- **A fresh sandbox needs:** `apt-get install iproute2` (for the bench's netns), a `tester` user (`useradd -m tester`),
  `make -C examples/mqtt/stock -j2 WITH_DOCS=no binary` (stock for the bench), and FrogNet cloned to
  `/home/claude/fawcettjohnw/frognet-living-network` (the simulator). `tools/build.sh` installs its own -dev packages,
  including libedit-dev.

### Tests (run all of them after every change)

```
cd cpp; F="-std=c++20 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -Iinclude -Itests"
  g++ $F tests/test_rowcodec.cpp src/frogram.cpp -llz4 -lcrypto      # likewise test_window_wire, test_cellwire
  g++ $F tests/test_ram_memory.cpp && ./a.out include/ram_memory.hpp  # needs the header path argument
  test_family_hooks
  g++ $F tests/test_family_hooks.cpp src/ram_host.cpp src/lf_ebr.cpp src/frogram.cpp -llz4 -lcrypto
  g++ $F tests/test_rows_forward.cpp src/frogram.cpp -llz4 -lcrypto      # forwarded changes, shared encode, two readers
  g++ $F tests/test_ram_memory.cpp src/lf_ebr.cpp   # (and test_row_scan_bound, test_write_window): ram_memory.hpp now needs lf_ebr
cd examples/mqtt/region; g++ -std=c++20 -O2 -Wall -Wextra -Werror -I. test_sub_address.cpp ../front/libcommon/libmosquitto_common.a   # 758,200 pairs
cd examples/mqtt; ./tools/build.sh; (cd libram; ./build.sh); ./tools/two_boxes.sh     # 13/13
chown -R tester . && su tester -c "cd front/test/broker && python3 test.py"   # 272/276; the 4 http-api tests fail as before
cd examples/redis; ./tools/build.sh; python3 tests/run_two_boxes.py build 27400; python3 tests/run_concurrency.py build 27500
```

`cpp/qualify.sh` spends most of its time in the codec mutation suites; run the unit tests directly instead.

---

## 4. STATE AT HANDOFF

### Done this session (all tests green as of the last full run)

- **Decode once (`[DECODE_ONCE_V1]`).**
  - The memory, host, session reader, Space, libmosquitto, the MQTT box and the Redis front all pass decoded arrays.
  - Every Space write sends values (`write_fields`).
- **Positional row decode (`[POSITIONAL_DECODE_V1]`).** B's session reader no longer builds a name-keyed dictionary
  per row.
- **MQTT mirrors removed (`[ONE_MEMORY_V1]`).**
  - Retained messages are looked up at their tuple and handed to Mosquitto for that delivery only, never stored in
    the box's region. A box's retained publish writes only the tuple.
  - Other boxes' subscriptions are no longer replayed into the local trie.
  - Other boxes' counts are read from their `sys` tuples when a limit is checked. `$SYS` retained count is counted
    from the tuples.
  - On handover, a client's subscriptions travel in the session tuple. The giver withdraws its own `sub` tuples and
    cleans its region; the taker subscribes and writes its own `sub` tuples.
  - The `at` watch stays: there a box reads a claim on its own client and gives up that client's session.
  - Two-box 7/7, libcheck 10/10, suite 272/276.

### Measured: brokers across veth, 2-core sandbox, 8 s

| load | stock | after decode-once + positional, before the `sub` read | NOW (with the broken `sub` read) |
|---|---|---|---|
| QoS 1 flat out, msgs/s | 22,856 (1.9M lost) | 103,978 (49,758 lost) | **7,152** (0 lost) |
| QoS 0 flat out, msgs/s | 133,572 (3.2M lost) | 105,876 (0 lost) | **760** |
| 2,000/s, CPU ms/1k (A/B/mem) | 34/31/– | 56/104/114 | 114/106/187 |

Library bench (decode-once only): QoS 1 on-time/s stock 28,682, ours 99,133. At 2,000/s, CPU ms/1k: stock 148,
ours 184.

### Done 2026-10-19 (second session): the `sub` scan

- **[SUBS_BY_FILTER_V1].** Subscriptions are addressed by filter, with a shapes manifest. A topic plus a shape gives
  one filter or none (`region/sub_address.hpp`); the lookup reads the manifest, then one family per fitting shape.
- **Measured: addressing alone did not fix it.** Two-box v3 load 3,456/s; `[DIAG-SUBLOOKUP]` showed 283 µs per lookup
  on the front thread, 5.8 s of front time per run. The synchronous read on the publish path was the cost.
- **[ASYNC_PUBACK_V1].**
  - `rgn_pub` asks nothing of other boxes. Only an MQTT 5 QoS 1 PUBACK uses the answer.
  - For that case, `sub__puback_ask` queues the PUBACK on the client (in PUBLISH order).
  - `rgn_ask_subscribers` runs the lookup on `ribbit::pool_run` (the host's [ADAPTIVE_POOL_V1], now exported).
  - The answer comes back on the front's MPSC list plus its eventfd; `sub__region_drain` takes it and flushes.
  - Pending acks are dropped on disconnect and cleanup.
- **Numbers.** RESULTS.md "v31": QoS 1 87,770/s, QoS 0 104,202/s, MQTT 5 QoS 1 28,356/s (was 6,012 / 441 / 3,398).
- **Tests.** two_boxes 13/13 (5 new: v5 reason codes across boxes and PUBACK order); suite 272/276; Redis 15/15 and
  39/39.

### Done 2026-10-19 (afternoon): the memory's per-message work (v32)

John asked for every overhead found in the memory's profile to be removed ("Just do them all"). Read against the FrogNet
core first: the core does none of these four either (Explore report, 2026-10-19). What was done, each measured
(RESULTS.md "v32"):
- **[LOCKFREE_READS_V1]** row values behind an atomic pointer, retired by EBR; only writers (and the bounded rows walk)
  take the row lock.
- **[LZ4_FLOOR_V1]** no compression at 64 bytes or less; the same in FrogNet's `core/codec.py`.
- **[POSITIONAL_WRITE_V1]** writes decoded by position, with the changed indices.
- **[FORWARD_CHANGES_V1]** the writer's changes are the reader's difference when the reader holds the writer's
  reference.
- **[SHARED_ENCODE_V1]** a forwarded row's packet is made once, for all readers.
- **[ENCODE_CHANGED_V1]** `encode_diff` split, same bytes.

Results: QoS 0 104,202 → 131,794/s (stock 156,130, which loses 4.6M); memory CPU 9.32 → 6.15 ms/1k.

The send to clients stays on the front thread (`out__flush`, one `writev` per client per loop pass). Moving it off was
measured twice, both worse, and reverted (RESULTS.md).

FrogNet core changes, in the FrogNet-Living-Network clone (not committed, delivered as a patch):
- `core/codec.py`: the LZ4 floor.
- `daemon/engine/session.py` [COALESCE_PER_PEER_V1]: in-flight coalescing keyed by (peer, req_hash). A coalesced reply
  is a difference against its own peer's reference; handed to another peer it decoded wrong and that peer's reference
  never moved. `daemon/test_coalesce_per_peer_oracle.py` was red before, green after. Sharing one execution across
  peers needs coalescing inside `ExecutionEngine.execute`; not done. Coalescing is off by default
  (FROGNET_DAEMON_COALESCE_WAIT=0).

**The C++ codec and FrogNet's `core/codec.py` agree again.** `tests/test_semcodec_oracle.py` against 7651d9c failed
before any change made on 2026-10-19. Each difference had a right side, settled per class:
- **Python wrong, fixed in `core/codec.py`:**
  - `_values_equal` had a 1e-9 tolerance, treated 1 == 1.0 and True == 1, and ignored dict key order, so a difference
    left real changes out. Now exact ([EXACT_DIFF_V1], as C++).
  - `decode_error_reply` swallowed a malformed error frame as "not an error" (a fallback). Now it raises
    ([NO_FALLBACK_ERROR_REPLY_V1], as C++).
  - Python's codec lacked the platform's wire types 8–11 ([STRING_SPLICE_V1], [NUMBER_DELTA_V1]), which C++ sends under
    the same WIRE_VERSION 5. Ported, encode and decode, byte for byte.
- **C++ wrong, fixed in `semcodec.hpp`:** a field name given twice kept both in the new reference.
  [DUPLICATE_NAMES_LAST_WINS_V1] makes it one field: its last value, in its first place, as a dict.
- **Harness wrong, fixed:** a field name with a newline broke the driver's one-answer-per-line protocol and shifted
  every later answer. Fixed by [ONE_LINE_PER_ANSWER_V1] (driver and oracle escape; the oracle demands exactly one line
  per case). 'semcodec: ' was added to the oracle's codec-owned message prefixes.
- **Result:** seeds 20260924, 20261019 and 7 PASS (697,621 / 714,020 / 733,103 cases). Seed 1 has 1 difference: a
  corrupt LZ4 frame declaring an impossible size, refused by both (Python MemoryError, C++ length_error). That code is
  untouched and was in the baseline.
- **FrogNet's own Python tests:** the same 9 pass and 7 fail before and after; the 7 need the node's environment.
- **Deployment note:** Python nodes now SEND splices and deltas. A node on the old `codec.py` cannot read them, so the
  Python tree must go to every node together, as the C++ platform already does ("every node at 0.67 together").

### BROKEN BY DESIGN: Redis is a replica per box

Every Redis box holds the whole keyspace in its own local memory (`g.mem` in `db.cpp`). It applies every box's intents
in one order and joins by snapshot copy (`snap.*` / `snapdone.*`). That is N copies kept in sync. It contradicts the
model, and his 2026-09-29 ruling: a RAM host holds the Redis region; RESP fronts are participants connecting up to
it; contended read-modify-write is reduce-by-read (each operation writes its own cell, folded in write order); MULTI
gets no isolation; multi-key reads are best effort.

I asked John which shape replaces the replica, and he has not answered:
- **(a)** The memory owns the keys and the Redis engine runs there; boxes are fronts.
- **(b)** Each key is owned by one box, by hash.

Don't build either until he rules.

### Also wrong, not yet fixed

- **Directory scans.** `hold_filter`, `topics_matching` and `rgn_retained_count` each read the whole
  `(mqtt.topics, all)` family. These go filter → topics, the reverse of the subs lookup, so the shapes manifest does not
  fix them.
- **`rgn_counts_others` reads the whole `(mqtt.box, sys)` family** on every limit check.
- **Session handover is request/reply with a timer.** B claims; A's `at` watch writes the session; B polls
  `session.<client>` for up to 5 s.
- **`Space::watch` re-issues every 30 s** when nothing lands (`rows.empty() → continue`).
- **Client-side locks remain.**
  - `Plane` in `frogram.cpp`: two mutexes, plus a mutex and condition variable per slot.
  - The `frogram.cpp:224` trace mutex.
  - `call_once` in `region_client.cpp` and `libmosquitto_ram.cpp`.
  - Host side, beyond the row lock: `cache_m` in `ram_host.cpp`.
- **MQTT 5 lookup latency.** 1.8 ms average on the pool (2 reads, 2 saturated cores). That latency bounds the v5 rate
  through the client's window.

- **The bench connects boxes straight to `mqtt-ram`'s listener.** The ruling is FrogNet's :9009.
- **2,000/s costs about 4× stock in CPU.** It is fixed per-wake work in B and the memory, not decoding. Profile it.
- **At QoS 1 flat out, B drops messages** (counted gaps). On the 2-core sandbox the whole box is CPU-saturated;
  more than 2 cores (the Pi 5) is needed to judge it.
- **Templates for standard messages** (registration, read requests) are not done beyond kind-3 rows.

### Next, in order

1. ~~Fix the `sub` lookup~~ done 2026-10-19 (v31).
2. Get John's ruling on Redis (a) or (b), then remove the per-box replica.
3. Re-audit every `watch(...)` and every in-process structure in the MQTT box, libmosquitto and the Redis front against
   one question: is this another owner's fact held here? If yes, delete it and read the fact where it lives.
4. Point the boxes at :9009.
5. Profile the 2,000/s path.
6. Pi 5 tarball (one per machine type), docs (DESIGN/RESULTS) and a version bump. Ask before committing.
