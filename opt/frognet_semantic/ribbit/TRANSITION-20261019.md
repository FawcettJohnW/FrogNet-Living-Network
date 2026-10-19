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

- **Tree:** `/home/claude/work/ribbit` (`VERSION v30-semantic`). It is not a git repo here, and nothing is committed.
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
    | `(mqtt.box, sub, <client>␟<filter>␟<box>)` | subscriptions |
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
- **Scratchpad:** `/tmp/claude-0/-home-claude/75e69e0b-c5a8-5327-900b-a1de1d0a5017/scratchpad`.
  - Scripts: `libcheck.sh` (10 checks), `prof_b.sh`, `prof_q0.sh`, `prof_q1b.sh`.
  - Backups: `region_client.cpp.pre-nomirror`, `rowcodec.hpp.pre-positional`.

### Tests (run all of them after every change)

```
cd cpp; F="-std=c++20 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -Iinclude -Itests"
  g++ $F tests/test_rowcodec.cpp src/frogram.cpp -llz4 -lcrypto      # likewise test_window_wire, test_cellwire
  g++ $F tests/test_ram_memory.cpp && ./a.out include/ram_memory.hpp  # needs the header path argument
  test_family_hooks
cd examples/mqtt; ./tools/build.sh; (cd libram; ./build.sh); bash $SP/libcheck.sh; ./tools/two_boxes.sh
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

### BROKEN NOW — fix first

**`rgn_pub` in `region_client.cpp`, the `[ONE_MEMORY_V1] no subscriber on this box` block.** To answer MQTT 5's "no
matching subscribers", it reads the ENTIRE `(mqtt.box, sub)` family on every publish that has no local subscriber. That
is a whole-family scan per publish, which John ruled out, and it caused the 15× collapse above.

Reading the subscriptions from the one RAM is correct. Scanning the whole family to find them is not. Address the
subscriptions so a publisher reads only what can match its topic. For example, key them by filter, e.g.
`(mqtt.subs, <filter>, <client>␟<box>)`; then a publish on `a/b` reads exactly the families whose filter can match it
(`a/b`, `a/+`, `+/b`, `+/+`, `a/#`, `#`, and the `$share/...` forms), bounded by the topic's depth. Or use
`newest()` on those families when only "any?" is needed.

This is the tuple-design question John keeps making: set the tuples up so the answer is looked up. Fix it, rerun every
test and the across bench, and report the numbers side by side.

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

- **The bench connects boxes straight to `mqtt-ram`'s listener.** The ruling is FrogNet's :9009.
- **2,000/s costs about 4× stock in CPU.** It is fixed per-wake work in B and the memory, not decoding. Profile it.
- **At QoS 1 flat out, B drops messages** (counted gaps). On the 2-core sandbox the whole box is CPU-saturated;
  more than 2 cores (the Pi 5) is needed to judge it.
- **Templates for standard messages** (registration, read requests) are not done beyond kind-3 rows.

### Next, in order

1. Fix the `sub` lookup by addressing (above). Rerun everything plus the bench, and report side by side.
2. Get John's ruling on Redis (a) or (b), then remove the per-box replica.
3. Re-audit every `watch(...)` and every in-process structure in the MQTT box, libmosquitto and the Redis front against
   one question: is this another owner's fact held here? If yes, delete it and read the fact where it lives.
4. Point the boxes at :9009.
5. Profile the 2,000/s path.
6. Pi 5 tarball (one per machine type), docs (DESIGN/RESULTS) and a version bump. Ask before committing.
