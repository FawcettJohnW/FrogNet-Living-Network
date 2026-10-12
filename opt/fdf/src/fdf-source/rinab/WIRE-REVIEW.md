# The wire: every proxy/daemon mechanism, its C++ counterpart, and whether the region's traffic uses it

## Sources

- **Python** (seattle5-20261002): proxy/transport_semantic.py, proxy/channel_sets.py, daemon/engine/session.py,
  core/semcache_wire.py, core/codec.py, core/template.py, core/json_handler.py.
- **C++** (platform v0.63 as built here, patches applied): ribbit_cpp/{semwire,semcodec,semtpl,fnwp_engine,frogram,
  dataplane}.hpp/.cpp, ramsrv/ram_server.cpp.
- **What the region's traffic actually does** was measured with tools/wire-mix.sh (`RINA_WIRE_STATS=1`: each
  program prints its session's FNWP counters at exit). Data: evidence/wire-mix-1.txt.

## Measured: what goes on the wire (one machine, the fanned-out host, one request per SDU)

| run | requests (raw / full / diff / repeat) | replies (raw / diff / same) | bytes out / in | per exchange |
|---|---|---|---|---|
| ping 1,000 (client) | 4 / 3 / 2,001 / 0 | 1 / 2,003 / 0 | 192,896 / 143,256 | 2 requests; 193 B out, 143 B in |
| rr 2,000 (client) | 4 / 3 / 4,003 / 0 | 1 / 4,004 / 0 | 385,178 / 287,877 | 2 requests; 193 B out, 144 B in |
| perf 5,000 × 1,400 B (client) | 5 / 3 / 5,003 / 0 | 0 / 5,006 / 0 | 696,308 / 185,844 | 1 request per SDU; 139 B per 1,400-B SDU (LZ4 on a constant payload) |
| server program, whole run | 4 / 3 / 6,568 / 0 | 51 / 6,519 / 0 | 622,643 / 715,503 | 49 replies took the data socket |

**What the counts show.**

- Every request after the first few is REQ_DIFF: templates are learned and used.
- Every reply is RESP_DIFF.
- REQ_REPEAT and RESP_SAME never occur. No two region requests are byte-identical (`after`, sequence numbers and
  SDU payloads all move), and SAME, as both the daemon and the C++ engine implement it, is "this same request hash,
  answer unchanged".

## Mechanism by mechanism

| # | mechanism (where in Python) | C++ | used by the region's traffic |
|---|---|---|---|
| 1 | FNW1 framing, MAGIC + op; REQ_FULL/REPEAT/RAW/DIFF, RESP_RAW/DIFF/SAME, REQ_MISS, ERROR (semcache_wire.py) | semwire.hpp | yes (table above) |
| 2 | req_hash truncated to 16 B (codec.py `REQ_HASH_LEN`) | semwire.hpp | yes |
| 3 | request template per format; REQ_DIFF carries only the changed fields (codec.py `encode_*_diff`) | semtpl.hpp, fnwp_engine.hpp | yes: 2,001 of 2,008 ping requests |
| 4 | response template; RESP_DIFF carries only the changed fields (json_handler.py, daemon `_reexecute_semantic`) | semtpl.hpp, fnwp_engine.hpp `daemon_reply` | yes: every reply after the first |
| 5 | instance references: one template, many instances keyed by the location in the data (`[INSTANCE_REFERENCES_V1]`) | fnwp_engine.hpp | **no**. The region endpoint's templates have no location fields, so all calls of one shape share one reference. |
| 6 | LZ4 only when it shrinks the payload (codec.py `_lz4_smart`) | semcodec.hpp (LZ4F) | yes (perf: 139 B per 1,400-B SDU) |
| 7 | RESP_SAME when a repeated request's answer is unchanged; the body is hashed (`[SAME_COMPARES_THE_BODY_V1]`) | fnwp_engine.hpp: `sha32(a.body)` on every answer | **never fires** (0 SAME); the hashing still runs on every answer |
| 8 | REQ_REPEAT: an identical request goes as its hash only | fnwp_client/engine | **never fires** (0) |
| 9 | the SAME cache, memory only, bounded (`[SAME_CACHE_IS_MEMORY_ONLY_V1]`); SAME-miss recovery (`[SAME_MISS_AUTORETRY_V1]`) | fnwp_engine.hpp (4,096 entries, `[SAME_MISS_FROM_CACHE_V1]`) | present, unused (no SAME) |
| 10 | the response reference moves on RESP_SAME (`[SAME_MOVES_REFERENCE_V1]`) | fnwp_engine.hpp | present |
| 11 | references move in wire order (`[REFERENCES_MOVE_IN_WIRE_ORDER_V1]`) | frogram `[WIRE_ORDER_TURNS_V1]`; host encodes on the session writer | yes |
| 12 | fan-in at the proxy: callers → the peer's worker → one writer; identical in-flight requests coalesced (`_coalesced_rpc`) | frogram::Session writer + `[CLIENT_COALESCES_V1]` | yes; coalesced = 0 (no identical in-flight requests) |
| 13 | fan-out at the daemon: reader → 256-thread pool → reply queue → one writer, no shared send lock (session.py) | **was missing**: the host ran requests inline on the reader. **Now** `[HOST_FANOUT_V1]`: a lock-free MPMC work ring and pool, a lock-free MPSC reply queue, and one writer per session | yes, from this build |
| 14 | channel sets: up to 3 independent sets (send + return sockets) per peer, so a bulk flow cannot stall a latency-critical one (channel_sets.py) | **absent** | **no**: SDUs, waits and control share one set |
| 15 | separate data socket for large RAW bodies, fair segment interleaving; the ceiling is a time, not a size | dataplane.hpp, frogram, ram_server | yes (49 frames in the run) |
| 16 | adaptive per-peer timeouts and retries (`[ADAPTIVE_TIMEOUT_V1]`, `_PeerLatency`) | absent | not wanted: Mode B rules out timed re-asks |
| 17 | the engine's state (templates, references, SAME cache) | fnwp_engine.hpp: **one `std::mutex m_` per engine**, taken by `begin` (reader) and `finish` (writer) | yes, and it puts a lock on the fan-in/fan-out path |

## What is not right yet, in the order it will be fixed

1. **(7) Hashing every answer to detect change.** The C++ engine SHA-256s every answer body (`sha32(a.body)`) and
   compares. That is the mechanism John ruled a bug, and fixing it is authorized. In the region, "unchanged" is a fact
   the memory already has: the write ids of what the answer was read from. The change is to answer SAME from that,
   not from a hash.
2. **(17) The engine mutex.** Decode (reader) and encode (writer) take one lock. Each side's state is its own: the
   request references are the reader's, the response references and SAME cache are the writer's. Templates learned
   on the writer are published to the reader without a lock (an atomic snapshot).
3. **(5) Instance references for the region.** Give the region endpoint's templates a location field (the op, plus
   the flow or input space), so each instance is differenced against its own previous message.
4. **(14) Channel sets.** A second set for SDU traffic, so control calls and held reads do not queue behind it.
   Measured before and after.
5. **(8) REQ_REPEAT** cannot fire while every request carries a moving `after` or sequence number. That is inherent
   to one request per SDU, and is recorded, not changed.

---

## Done since the review (each measured; rlite's 30 tests pass after each, at `ulimit -n 1024`)

| step | change | whole-host instructions, rr of 500 | per transaction |
|---|---|---:|---:|
| baseline | host fanned out (`[HOST_FANOUT_V1]`), one request per SDU (`[ONE_REQUEST_PER_SDU_V1]`), per-flow order (`[FLOW_ORDER_V1]`) | 410.4 M (callgrind.out.whole) | 821 k |
| 1 | `[NO_ANSWER_HASH_V1]`: no SHA-256 / HMAC of answers. "Unchanged" = the same status and bytes as the answer the engine already holds; same_ids are minted, not hashed. | 326.2 M (callgrind.out.whole2) | 652 k |
| 2 | `[PROOF_BYTES_FIRST_V1]`: the reply proof accepts a byte-identical rebuild without parsing. `[LZ4_NEVER_SMALLER_SKIPPED_V1]`: payloads ≤ 24 B are not compressed, since an LZ4 frame here is never under 24 B (measured over inputs of 1–200 B). Both give exactly the bytes the long way gives. | 294.3 M (callgrind.out.whole3) | 588 k |

**This profile showed what the earlier api-only profile hid.** The region's own work (`api()`) is 49 M of the 294 M
(17%). The rest is the FNWP engine and the session: encode (`ServerEngine::finish`) 131 M, decode (`engine_frame` /
`begin`) 35 M, pool and threads 20 M.

**Where finish still spends, per answer:**

- `rebuild_reply` (the proof's rebuild, 18 k): the byte comparison usually fails, because the region writes compact
  JSON and the rebuild writes Python's `", "` / `": "` spacing, so the JSON parse still runs (`json_try`, 10 k).
- `diff_encode_response` (16 k, including LZ4 on payloads over 24 B).

**Next, in order:**

1. The engine mutex (17).
2. The proof: compare in the form both sides already have (the extracted fields), not by rebuilding and reparsing
   text.
3. Instance references for the region (5).
4. Channel sets (14).

rr, perf and p16 in bench.sh after each step.

---

## The engine without its mutex, and the intermittent "Flow allocation timed out" (found, fixed)

**`[ENGINE_LOCKLESS_V1]`** (tools/apply_engine_lockless.py): each piece of server-engine state has one owner.

- The reader owns the request references and its own repeat map.
- The writer owns the response references, the answer cache and template learning.
- Templates reach the reader as immutable snapshots through one atomic pointer.
- A REQ_FULL reset and a SAME-miss travel to the writer through the session's lock-free reply queue, in decode order.

**The intermittent failure (0029 and others: "Flow allocation timed out" / ENOENT).** tools/loop-test.sh runs one
test repeatedly against one host and dumps the region at the first failure. `RINA_EXIT_TRACE` records each exiting
program's stack. Together they found it:

- `rinaperf -lw` registers, then forks into the background. The client allocates a few milliseconds later.
- The parent's session ended before the child's own connection was up. The registration's input space was then held
  by a program with no session, so the region refused the allocation (or never delivered it).
- The daemon "dying" seen earlier was the test's own exit trap killing it after the client had already failed.

**Fixes:**

- **`[FORK_KEEPS_REGISTRATIONS_V1]`.** A forked child's proc id is minted by the parent before the fork, and the
  parent announces it (op `fork`) while still alive.
  - Each holder of an input space has its own cell, `ibh|<inbox>|<proc>`, so concurrent holds cannot lose one.
  - Names go only when the last holder's session ends. The child counts as live from its announcement, so requests
    are written to its stream, which it reads once connected.
  - A child that forks again before its own connection is up connects first, then announces.
- **`[NO_FALLBACKS]`.** A forked child that cannot reach the region now says so; a `catch (...) {}` used to swallow
  it.

**Results:**

| check | result |
|---|---|
| 0029 against one host | 90/90 (loop-0029-90-runs.txt); before the fix it failed within 1–46 runs |
| 0011, 0014, 0028 | 25/25 each |
| rlite's 30 tests, `ulimit -n 1024` | 30/30, host descriptors flat at 4 |

**Remaining edge:** a child that is announced but dies before it ever connects leaves its input space "live" with
nobody reading it. An allocation to it then waits for the allocator's own timeout.

---

## The client session's fan-in and fan-out without locks (`[CLIENT_LOCKLESS_V1]`, tools/apply_client_lockless.py)

**Before.** Every call and post took the job-queue mutex (with two condition variables), the in-flight map's mutex
and the statistics mutex; each caller then slept on its own mutex and condition variable.

**Now:**

- **Fan-in.** Callers push onto a lock-free MPSC list. Resends, made in a call's turn, go on a second list that the
  sender drains first. A bounded count holds callers back with an atomic wait, and the sender sleeps on a futex word.
- **In flight.** A 65,536-slot ring indexed by sequence number: the sender stores a slot, the reader takes it with
  one exchange.
- **Fan-out.** Each caller waits on its own completion word (FUTEX_WAIT, with the call's timeout).
- **Counters** are atomics.
- **Death.** One reader claims it and writes the reason, then sets the flag and finishes every caller still in
  flight. A sender that stored a slot after that takes it back.

**Results:** rlite's 30 tests pass (`ulimit -n 1024`, host descriptors flat at 4); perf delivers 20,000/20,000;
0029 and 0011 pass 40/40 each against one host.

**Still locked on the client:**

- the engine (`ClientEngine::m_`: prepare on the sender; complete on the reader and the payload-landing threads);
- the wire-order turns (`gate_m`);
- the coalescing table (`co_m`);
- the data-socket and lander bookkeeping (`dat_m`, `land_m`).

Next: the engine, by the same single-owner split as the server.

---

## perf: "Timeout while waiting for result message" (intermittent; open, narrowed)

**Fixed on the way:**

- **`[CREDIT_IS_CONTIGUOUS_V1]`** (region). Credit was the highest sequence number seen. With requests executed in
  parallel, an SDU still missing below it could have its ring cell overwritten. Credit is now the contiguous count,
  kept in the receiver's own cell `taken|<fid>|<proc>`.
- **`[FLOW_DELIVERY_V1]`** (librina). The one event thread blocked in `send()` on a full flow, so a program waiting on
  another flow (rinaperf's server waits on its control flow) deadlocked. Each flow now has its own delivery path, in
  order, with an inline fast path when nothing is queued for it (`[DELIVER_IN_ARRIVAL_ORDER_V1]`).
- **Delivery survives EINTR,** and reports any real failure instead of silently ending the flow.
- **`[CHANNEL_SETS_V1]`** (librina + region). A program's SDUs travel on a second, independent set, as the proxy keeps
  up to three. Bulk SDU replies no longer queue ahead of the held read's answer. Liveness stays with the control
  set.
- **Cross-flow order is not required.** rinaperf handles a stop that overtakes data: the stop carries the count, and
  the server prints "stop delayed".

**What a failing run shows now** (`RINA_WIRE_STATS=1`, tools/perf-count.sh dumps at failure):

- the server process received every SDU: data flow delivered 20,001 of 20,001; control flow 2 of 2, the
  configuration and the stop;
- the server wrote only 1 SDU on its control flow (the ticket), never the result;
- the server's threads are parked on futexes in rinaperf's own data, not in librina.

The next step is to find which of rinaperf's waits the server worker is in, and why it never reaches its result write.

Rate here: about 1 failure in 5 runs; passing runs deliver 20,000/20,000 at 47–50 Mbps receiver-side.

---

## perf's lost result message: found and fixed (`[CLOSE_KEEPS_WRITES_V1]`)

**How it was found:**

- At a failing run the server had no perf worker thread left, and no sender thread for its control flow.
- With the sender made to say why it stops: `flow …-2-…: reading what the program wrote failed: Connection reset by
  peer`.

**Cause.** rinaperf's server counts its 20,000 SDUs, writes its result, and closes the control flow, with the
client's "stop" still unread on it. On an AF_UNIX SOCK_SEQPACKET pair, closing with unread data makes the kernel
report ECONNRESET once to the other end, before the messages the closer wrote, which are still queued. librina took
that error as the end of the flow, so the result was never sent. In rlite, a closed flow still delivers what was
written to it.

**Fix** (librina `sender`): ECONNRESET is taken once and reading continues, so everything the program wrote is sent,
then the end of the flow. EINTR is retried. Every other way a flow's sender stops is now said on stderr; before it
was silent.

**Results:**

| check | result |
|---|---|
| perf 20,000 × 1,400 B, back to back | 24/24 delivered 20,000 with the result message (14 + 10 runs); before, about 1 in 5 timed out |
| rlite's 30 tests, `ulimit -n 1024` | 30/30, host descriptors flat at 4 |

**Diagnosis aids kept:** rinaperf and rina-echo-async are built with `-g`. tools/perf-count.sh prints, at a failure,
the region's taken/credit cells, each program's event stream, every server thread's system call, and the process
map.

---

## Coalescing at the RAM server (`[HOST_COALESCE_V1]`, tools/apply_host_coalesce.py)

**As the daemon does it** (daemon/engine/session.py, REQUEST COALESCING): one execution per request hash in flight,
across sessions.

- A later identical request leaves a continuation and its session carries on.
- When the one execution answers, every session that asked gets that answer, each encoded by its own session's
  writer, against its own references.
- Loops are left to the existing loop detector (ruling 2026-10-05).

**Lock-free:**

- a 4,096-slot table of atomic entry pointers;
- each entry's waiters on a Treiber stack, which the first execution seals when it answers (a waiter that finds it
  sealed runs its own execution);
- entries freed through the platform's epoch reclamation (ribbit::Ebr).

The host prints its counts when it exits.

**Proof** (tests/coalesce_held.cpp, built as build/bin/coalesce-held): 8 sessions issue the same held read, then one
write. All 8 got the written row, from **1 execution**: 7 were answered by another's execution.

**rlite's suite:** 30/30. It coalesced nothing, because its requests never repeat while in flight (each carries its
own flow, sequence or `after`). That is the expected behaviour for one request per SDU. **perf:** 5/5.

**Next:** client-side coalescing exists (`[CLIENT_COALESCES_V1]`) but uses a mutex-guarded map (`co_m`); it goes
lock-free the same way.

---

## Coalescing in the client without a lock (`[CLIENT_COALESCE_LOCKLESS_V1]`), and landing without threads or locks (`[CLIENT_LANDING_LOCKLESS_V1]`)

**Client coalescing.**

- Identical calls in flight share one request, as before (`[CLIENT_COALESCES_V1]`), but the table is now 4,096
  atomic slots instead of a mutex-guarded map.
- An entry leaves the table before its call is finished, so a call made after the answer is its own call.
- Entries are freed through ribbit::Ebr.
- Proof (tests/client_coalesce.cpp → build/bin/client-coalesce): 8 threads of one session made the same held read.
  All 8 got the row, and the session counted 7 calls served by one request.

**Landing.**

- Before, every reply that arrived as a marker got its own thread, kept in a vector under `land_m`, blocking until its
  payload landed.
- Now the data reader hands each landed payload straight to the applier, and the applier pairs payload and marker in
  either order, since the two arrive on different sockets.
- The data socket's single sender lost a lock nothing else ever took (`dat_m`).
- perf: 6/6, with 21 replies arriving through the data socket in a checked run.

**Locks left in frogram.cpp:**

- the trace writer, which only runs with `FROGRAM_TRACE` set;
- the separate `Plane` API, which neither Mode B nor the Redis sources use.

On the session path, calls, posts, sending, in-flight tracking, replies, turns, completion, coalescing and landing
all run without locks.

**rlite's 30 tests,** `ulimit -n 1024`: 30/30.

---

## Step 1: SDUs by reference (`[SDU_BY_REFERENCE_V1]`)

**What changed:**

- A program that writes back exactly the bytes it was given on a flow (an echo, a forward) now sends a reference to
  the cell those bytes already live in: its own stream, by instance and the cell id it meant. It no longer sends a
  copy.
- The peer's cell holds the reference.
- The bytes leave the memory once, in the held `wait` of the program that reads them. A reference whose cell has since
  been rewritten is recorded in `dropped`, never silently skipped.
- The library remembers the last 8 SDUs it gave the program on each flow (bytes, sequence, cell id), each entry
  published whole under a sequence word. That is the in-memory value the program's write is matched against; no
  lock.

**What stays:** with rlite's fd interface, a program reads with read() on its socket. So the SDUs it will read are
delivered to its library ahead of the read, through the program's own held `wait`, which is the only place they go.

**Measured.** Ping 1,000 × 1,000 B, data-set bytes out: client 144,509 (copies) against server 110,138 (references);
no drops. rinaperf's payload is constant and compresses to about 144 B under LZ4, so the saving here is small. A
payload that does not compress saves its full size on every echo.

**Per-step numbers** (tools/quick-bench.sh → evidence/quick-bench.tsv):

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| baseline (bb156253) | 0.724 / 0.704 | 1,358 / 1,420 | 20,000 | 43.6 / 43.4 |
| step 1a | 0.723 / 0.757 | 1,366 / 1,260 | 20,000 | 45.2 / 41.4 |

That is within the run-to-run spread.

---

## Step 2: the primed template store (`[PRIMED_TEMPLATES_V1]`, tools/apply_primed_templates.py)

**How it works.** Both ends learn a template from one exchange (request + answer) through the same function, so the
store holds the exchanges, called exemplars. Each end learns from them at start exactly as it would have learned from
the wire, with nothing on the wire.

**Learning run.** `RIBBIT_TEMPLATES_RECORD=FILE`: every template a client learns appends its exemplar, one JSON line
per exemplar (one O_APPEND write). `build/bin/prime-dedupe IN OUT` keeps one exemplar per template, keyed exactly as
the engines key them (method + canonical semantic key).

- rlite's whole suite recorded 1,086 exemplars, which reduce to 12 templates.
- The store is `templates/rina.prime`; the run is kept as `evidence/templates-learning-run.rec`.

**Every run.** `RIBBIT_TEMPLATES=FILE`: each ClientEngine and ServerEngine learns the store when it is made, once per
process read.

- run.sh, bench.sh, tools/quick-bench.sh and tools/perf-count.sh give it to both ends; `PRIME=0` turns it off.
- An exemplar that no longer learns is reported and skipped.

**Measured,** one short program (a 3-ping rinaperf client):

| | requests (raw / full / diff) | bytes out |
|---|---|---|
| learned on the wire | 3 / 2 / 5 | 1,764 |
| primed | **0** / 3 / 6 | **973** |

rlite's 30 tests pass primed. The suite's total time did not move here (3.2 → 3.9 s for tests 1–18, 16.9 → 17.8 s for
19–30, within this machine's spread). Its time is dominated by the tests' own waits (0026/0027 are 13 of the 17
seconds).

**Rule for keeping it right:** the store must be regenerated whenever a request or answer shape changes, and both ends
must be given the same file.

---

## Step 3: instance references for region calls. Measured, and NOT kept.

**Tried:** sends and credit requests named their flow as the instance (service/variable/instance in the body). The
engine's location fields for a RAM-interface call then give each flow its own reference, so each SDU is differenced
against the previous SDU of its own flow.

**A/B,** 16 parallel flows × 300 pings, the client's SDU-set bytes out, alternating builds:

| run | without | with per-flow instances |
|---|---:|---:|
| 1 | 688,114 | 783,285 |
| 2 | 687,717 | 783,614 |

With instances the client sent 14% more. A difference carries its location every time
(`[INSTANCE_REFERENCES_V1]`: "the location travels with every difference"), and the shared reference was already
cheap: consecutive SDUs differ in the sequence number and nothing else, whichever flow sent them. The library is
back to one reference per shape. Revisit only for traffic whose consecutive SDUs on a flow resemble each other far
more than SDUs across flows.

---

## Step 4: lazy cleanup (`[LAZY_CLEANUP_V1]`)

**Before.** When a program's session ended, the region read every program's records: every input-space holder, every
registration and every flow. It rewrote whatever belonged to the departed program, at session end, on the session's
thread.

**Now a session's end changes only that program's own cells:**

- its liveness;
- its holds, kept in its own variable `held:<proc>`, which the memory indexes, so only its own cells are read;
- its flows' credit waits, from its own variable `pfl:<proc>`;
- its held `wait`.

A registration it leaves behind is found unheld by the next read that touches it. That read uses the answer at once,
the in-memory decision, and writes the erase as it goes. This covers allocation's lookups and `rlite-ctl`'s
registration listing.

**Results:**

- rlite's 30 tests: 30/30.
- 0029 (fork/daemonize), 0015 (registration) and 0011 against one host: 30/30 each.

**Per-step numbers** (tools/quick-bench.sh, evidence/quick-bench.tsv):

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| baseline (bb156253) | 0.724 / 0.704 | 1,358 / 1,420 | 20,000 | 43.6 / 43.4 |
| + SDUs by reference | 0.723 / 0.757 | 1,366 / 1,260 | 20,000 | 45.2 / 41.4 |
| + primed + lazy cleanup | 0.689 / 0.692 | 1,392 / 1,540 | 20,000 | 49.0 / 47.5 |

---

## Step 5: SAME for an answer identical to the instance's last one (`[SAME_AS_LAST_ANSWER_V1]`)

**Problem.** Most answers that repeat do so under requests that never repeat. Every posted SDU is a new request (its
sequence number moves) and every one is answered `{"ok":true}`. Each of those answers was extracted, proved (rebuilt
and both parsed), differenced and offered to LZ4.

**Change.** The writer keeps, per instance, the status, bytes and same_id of the last answer it sent. Same status and
same bytes, compared exactly with nothing hashed, means RESP_SAME naming that same_id, decided before any
extraction. Nothing else changes on either side:

- the reference is already those fields;
- the client holds that body under that id;
- the server holds it for a SAME-miss.

A REQ_FULL reset clears it with the reference.

**Measured:**

- **rr 2,000, the client's SDU set:** 2,002 of 2,003 replies are now RESP_SAME. Bytes in on that set are 42,138,
  about 21 B per reply.
- **Whole host, rr 500:** 330.9 M → 310.6 M instructions (callgrind.out.whole4 → whole5).
- **rlite's 30 tests:** 30/30.

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| + primed + lazy cleanup | 0.689 / 0.692 | 1,392 / 1,540 | 20,000 | 49.0 / 47.5 |
| + SAME as last answer | 0.689 / 0.695 | 1,475 / 1,570 | 20,000 | 50.9 / 42.3 |

**Where the host still spends** (whole5): ServerEngine::finish 35% (the `wait` answers: rebuild_reply 9%,
diff_encode_response 10% with LZ4); the region's own work (`api`) 24%; malloc/free about 25% across everything.

---

## Step 6: a row's pattern keys made once (`[FIRE_KEYS_ONCE_V1]`, tools/apply_fire_keys.py)

**Before.** On every write, `Memory::fire` built six pattern-key strings (allocations) and hashed each, to find the
held reads covering the row.

**Now.** A row's address is set once, so its six keys and hashes are computed when the row is made and kept in it.
Same lookups, same wakes.

**Measured:** `Memory::write` (inclusive, which covers fire) 18.6 M → 13.2 M on rr 500 (callgrind.out.whole6).
rlite's 30 tests: 30/30.

**Still open in the platform memory (v0.63 ramm::Memory).** It locks: one `std::shared_mutex` per row, and a mutex
plus condition variable per trigger. John's memory rule (Redis memory v3) is no locking in the memory. Replacing this
memory with a lock-free one (immutable values published by compare-and-swap, held reads on wait words chosen by their
variable that wake only when someone is waiting, epoch reclamation), as the Redis memory v3 did, is the next
platform-memory step.

---

## Step 7: the RINA region's own memory (`[RINA_MEMORY_V1]`, src/rina_memory.hpp)

**Why its own.** A region is an implementation and its API is how you talk to it (ruling 2026-09-22). Its memory is
the shape of what RINA does, under the memory rules: no lock, no write contention, immutable values published by
exchange or compare-and-swap, held reads that wake only when someone waits, epoch reclamation.

**InputSpace,** one per program. Everything addressed to it (flow requests, verdicts, SDUs, its end), numbered in the
order it was addressed.

- Many producers, one consumer: the program's wait.
- An event is immutable once published, and its number is its place.
- Segments of 1,024 slots are appended as needed and freed once the program has read past them.
- A read stops at the first number not yet published, so it never shows a gap.

**FlowDir,** one per flow direction (fid, receiving program).

- The SDUs in flight, by sequence number, in a ring of 4,096 slots, which is the credit window. An echo's reference
  finds the very bytes, and an SDU is overwritten only after its receiver has taken it.
- The receiver's contiguous taken count.
- Credit, published by quarters.

**Tables.** Insert-only, open addressing, atomic pointers.

**Moved onto it:** events (ev), send, wait, references and credit. Control records (registrations, flow records,
holders) are still in the v0.63 memory; they are the next piece.

**Held reads as continuations (`[DEFERRED_ANSWER_V1]`,** tools/apply_deferred_answer.py + make_host.sh).

- A wait with nothing to return, or a credit wait not yet past `have`, takes its request's continuation (`rina_defer`)
  and parks it in the program's input space or the flow direction.
- The next push, or the taking that frees credit, answers it (`rina_answer`) through the session's writer, and any
  identical coalesced requests with it.
- Before, the host started a thread for every held read, on every round trip. Now no thread waits.

**A platform bug found on the way (`[EBR_READER_PER_INSTANCE_V1]`,** tools/apply_ebr_readers.py).

- ribbit::Ebr gave each thread ONE remembered reader record. A thread alternating between two Ebr instances (the
  coalescing table's and the RINA memory's) registered a new record on every switch.
- Every instance's reader list grew by one per request for the life of the process, and `quiescent()` walks that list
  on every retire.
- Measured: host CPU per rr run 60 → 145 → 243 ticks over three runs of 1,500, rr down to 270 tps, `Ebr::collect` 23%
  of the host (evidence/callgrind.out.ebr-growth).
- A thread now keeps one record per instance (up to 8). The Redis host uses the same Ebr.

**Measured, three rr runs of 1,500 on one host:**

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| rr tps | 1,931 | 1,810 | 1,819 |
| host CPU ticks | 28 | 32 | 32 |

**Per-step numbers:**

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| step 6 (a1887e07) | 0.689 / 0.695 | 1,475 / 1,570 | 20,000 | 50.9 / 42.3 |
| step 7 | 0.623 / 0.590 | 1,887 / 1,863 | 20,000 | 48.1 / 48.1 |

**rlite's 30 tests:** 30/30.

**Open:** the host's resident memory grows about 3.5 MB per 1,500-transaction run.

**Debugging aid:** build/bin/rmem-probe PROC | FID|PROC.

---

## Step 8: control records on the region's memory (`[RINA_RECORDS_V1]`)

**What moved:** IPC processes, DIF policy, registrations, flow records, holders, liveness and sessions now live in the
region's memory as records: (variable, instance) → an immutable value, published by exchange.

- A variable's records sit in an open-addressing table whose slots are claimed once, by compare-and-swap. A full table
  chains one twice its size.
- The control code and rlite-ctl's ported command logic are written against frogram::MemoryApi; `RecordsApi` is that
  interface over the records.
- The region no longer calls the generic tuple memory for anything. v0.63's memory stays in the host only for its
  generic tuple API, which RINA does not use.

**Found and fixed on the way:**

- `rlite-ctl terminate` addresses the machine's agent by writing `ev:agent:<ns>`. As a record, that no longer woke the
  agent's held wait, so each agent took 10 s to exit and the next one 10 s to start.
- A write to `ev:<program>` is now an event pushed into that program's input space.
- Agent exit is about 1.4–2.0 s, the same as the step-6 build; that delay predates this work.

**Results:**

- rlite's 30 tests: 30/30.
- 0029 and 0015 against one host: 20/20 each.

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| step 7 | 0.623 / 0.590 | 1,887 / 1,863 | 20,000 | 48.1 / 48.1 |
| step 8 | 0.554 / 0.557 | 1,787 / 1,758 | 20,000 | 44.2 / 44.3 |

**Host memory over seven rr runs of 1,500 on one host:** it grew by 4.4, 2.7, 1.2, 2.0, 1.0 and 0.65 MB. The growth
tapers, consistent with each long-lived session's engine caches (4,096 entries each) filling, not with a leak.

---

## Step 9: the host's allocator (`[HOST_ALLOCATOR_V1]`, tools/build.sh)

**Why.** The whole-host profile of step 8 (callgrind.out.whole8, rr 500: 278.3 M instructions) has malloc/free at about
38% of the host. The FNWP semantic codec allocates per field: finish (encode) is 40%, request decode is 30%, and
rebuild_body / decode_request / build_url are 66 M between them.

**Measured A/B,** the same host build with only the allocator changed (LD_PRELOAD), rr 3,000 + perf 20,000,
alternating:

| allocator | rr tps | perf Mbps | host CPU ticks |
|---|---:|---:|---:|
| glibc | 2,285 / 1,903 | 42.2 / 45.5 | 181 / 181 |
| jemalloc | 2,650 / 2,426 | 43.6 / 44.4 | 154 / 156 |

**Change.** tools/build.sh links the host with jemalloc when the machine has it (`apt install libjemalloc2`) and says
which allocator it used; `NO_JEMALLOC=1` forces glibc.

**Per-step numbers:**

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| step 8 | 0.554 / 0.557 | 1,787 / 1,758 | 20,000 | 44.2 / 44.3 |
| step 9 (jemalloc host) | 0.504 / 0.455 | 2,054 / 2,402 | 20,000 | 56.2 / 44.8 |

**rlite's 30 tests:** 30/30.

**Next in the codec:** request decode rebuilds the request text (rebuild_body, build_url) from its fields, and the
region then parses that text again (Json::parse 6%). The reply proof's JSON comparison (json_try 7%) still runs for
answers whose rebuilt bytes differ.

---

## Where the client side spends (evidence/callgrind.out.client-rr300: a rinaperf rr client, 300 transactions)

The client program costs 190.4 M instructions for 300 transactions, about 635 k per transaction. That is more than the
host spends per transaction (about 557 k).

| function (inclusive) | share |
|---|---:|
| `ClientEngine::prepare_locked` (building each request frame) | 70% |
| `semtpl::canonical_semantic_key` (the template key, recomputed for every request) | 30% |
| `infer_schema` / `leaf_type` (schema inference over the body, including value-dependent leaf typing) | about 29% / 15% |
| `json_loads` / JsonScan (parsing the body the library just wrote) | about 8% each |
| malloc/free | about 30% |

**Next, client side.** Find the template key for a request without re-inferring the body's whole schema on every call,
while the key stays exactly what the engines compute. Leaf typing looks at values, so a cache keyed on structure alone
would not be exact. This is the next piece.

---

## Step 10: preallocation (John, 2026-10-05: allocate to the expected size plus a little, memcpy, static buffers that stay at the high-water mark, allocate only when absolutely necessary)

**Measured with tools/malloc-sites.sh:** it links the built host objects with -rdynamic, preloads tools/mcount.c (which
samples 1 malloc in 64 with its call stack), runs rr 2,000, and prints mallocs per transaction and the top call sites.

| change | host mallocs per rr transaction |
|---|---:|
| before (step 9) | 854 |
| `[NONFINITE_COPY_ONLY_IF_NEEDED_V1]`: a parsed JSON tree is copied (to replace NaN/Infinity) only if it holds one; otherwise used as it is | 801 |
| `[TEMPLATE_PRECOMPUTED_V1]`: field order, the URL's static part and the body mode computed once when a template is learned; a template with no location fields decodes a request once, not twice | 689 |
| `[BODY_PLAN_V1]`: a JSON request body's shape (from the field order alone) built once per template as a skeleton; each request renders it straight into a thread-local buffer that keeps its high-water mark, with no tree per request | 640 |

**Exactness.** tests/body_plan_exact.cpp: the skeleton rendering is byte-identical to rebuild_json + dumps_ascii for
field orders with nesting, the rows list, a field given twice, and conflicting paths, 8 of 8.

**Results:**

- rlite's 30 tests: 30/30.

| label | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| step 9 | 0.504 / 0.455 | 2,054 / 2,402 | 20,000 | 56.2 / 44.8 |
| step 10 (a) | 0.524 / 0.491 | 1,965 / 2,089 | 20,000 | 41.4 / 42.0 |
| step 10 (b) | 0.548 / 0.521 | 2,052 / 2,101 | 20,000 | 50.3 / 52.8 |

**The next sites** (malloc-sites):

- parsing the answer for extraction (semcodec JsonScan);
- the region re-parsing request bodies it was just handed (frogram::Json::parse);
- decode_request;
- build_url_pre;
- copies of ServerEngine::Work;
- dumps_ascii_str.

---

## Step 10, continued: the server finished to its known shapes

| change | host mallocs per rr transaction |
|---|---:|
| (from above) skeleton bodies | 640 |
| `[WORK_MOVED_NOT_COPIED_V1]` (tools/apply_work_moves.py): a request's decoded Work is moved through the host, not copied 4–5 times; copied only if the region defers its answer | 615 |
| `[WAIT_ANSWER_IS_TEXT_V1]` + `[CALL_TEXT_V1]`: the wait's answer is the region's own lines ("E\n", then "<number>\t<event>\n"), not JSON. The host's codec carries it as one raw field and builds no tree of it; `Session::call_text` hands the client the text. The primed store was regenerated (1,091 exemplars → 12 templates). | 367 |
| `[KNOWN_SHAPE_V1]` + `[ROUTE_HELD_V1]`: send and wait bodies are read in place (string_views, the SDU's data used as the JSON token it already is); the sender's program and each flow's route (peer, input space, flow direction) are held, filled once by compare-and-swap and forgotten when a session ends. Anything that does not look exactly like the known shape is parsed as before. | 335 |
| `[DECODE_ALLOC_ONCE_V1]` (tools/apply_decode_alloc.py): the merged reference moved, not copied; the repeat map recycles its oldest node (no allocation when warm); a URL with no query values made once per template | 335 |

The host counts which entry each call took:
`[KNOWN_SHAPE_V1] sends read in place 4005, waits read in place 4013, other calls parsed 16`, for rr 2,000.

**rlite's 30 tests:** 30/30, primed with the regenerated store.

**A measuring caveat found here.** This sandbox's speed drifts. The step-9 build re-run now gives rr 1,846 / 1,859 tps,
against 2,054 / 2,402 when first measured. From here on, comparisons are made back to back in the same session:

| same session | ping ms | rr tps | perf Mbps |
|---|---|---|---|
| step 9 build (1a0e6598), re-run now | 0.581 / 0.523 | 1,846 / 1,859 | 46.3 / 46.1 |
| this build | 0.470 / 0.457 / 0.460 | 2,320 / 2,317 / 2,320 | 42.2 / 43.0 / 43.5 |
| A/B, decode-side patch off / on (alternating) | 0.461 / 0.459, 0.429 / 0.461 | 2,393 / 2,339, 2,340 / 2,334 | 42.0 / 42.9, 41.0 / 42.3 |

**Next: the client side**, by the same rule (its data's shape is known, so it should not allocate). Its largest cost
is recomputing each request's template key: 30% of a client's instructions.

---

## Step 11: the client side by its known shapes

**Client instructions, rinaperf rr 300** (callgrind; evidence/callgrind.out.client-rr300 → …-known-shape):
190.4 M → 117.3 M → **98.4 M**.

- **`[CLIENT_KNOWN_SHAPE_V1]`** (tools/apply_client_known_shape.py)
  - *Template key.* Outside the sensor-data upsert, the key depends on the method, the path and the body's field
    paths, never on its values. So it is kept per shape: the body with values elided, written into a buffer that keeps
    its high-water mark, is the lookup. canonical_semantic_key fell from 30% of the client to under 6%, now paid once
    per shape.
  - *Template proof.* The body is rebuilt from the template's skeleton and compared byte for byte; it is parsed only
    when the bytes differ.
  - *Exactness.* tests/shape_key_exact.cpp: 3,000 bodies of every shape the library writes, 10 shapes, 4 keys; no body
    has a key different from its shape's.
- **`[PRIMED_LEARNED_ONCE_V1]`** (tools/apply_engine_once.py): the primed store is learned once per process, not once
  per engine. Every session made an engine, and every engine learned all the exemplars again: 12% of a short client.
  The host benefits the same way, one engine per rlite-ctl call.
- **`[TEMPLATE_BY_POINTER_V1]`:** a request holds a pointer to its template in the engine's snapshot, which is never
  freed while the engine lives, instead of a copy (6% of a client).

**Same session, alternating, the previous tarball (98c2156d) against this build:**

| build | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| 98c2156d | 0.438 / 0.444 | 2,417 / 2,377 | 20,000 | 43.4 / 43.5 |
| this build | **0.301 / 0.303** | **3,562 / 3,254** | 20,000 | **155.4 / 152.6** |

**rlite's 30 tests:** 30/30. **perf:** 3/3 delivered 20,000 (145.6 Mbps in perf-count).

**What remains in the client** (callgrind): `build_request` is 37% of it, and its `req_hash` 21%: the request hash is
SHA-256 over the request, which the wire contract defines. It is left as is: FNWP is not to be changed in general.
