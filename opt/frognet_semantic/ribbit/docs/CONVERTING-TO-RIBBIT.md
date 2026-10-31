# Converting a system to Ribbit — the reference process

The reference model for every conversion: what you keep, what you replace, and the two phases that get you from an
existing program to one that uses the memory as memory. It sits on top of `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`,
which says how to write Ribbit; this says how to arrive there from someone else's code. Read both before you write
a line. Written 2026-10-17 from the MQTT conversion, where the first design got it wrong in the ways listed in §6.

---

## 1. The model, in five sentences

1. **The contract is kept; everything inside it is yours.** The incumbent's external interfaces — its wire protocol,
   its API, its configuration, its own test suite, unmodified and passing — are the contract. Its process layout,
   its server, its internal messages, its schema, its threads are not.
2. **The memory is memory.** State lives in the tuple space. A participant reports what is (reads) and sets what
   shall be (writes). Nothing asks another machine for anything.
3. **Each participant writes its own truth, at its own locations, once.** One writer per location. No acks, no
   confirmations, no retries of a write that landed, no waiting for a reader.
4. **Everyone else reads it from there, and triggers are the callbacks.** A held read on a tuple family — any
   combination of the three indices, fixed or open — fires when something in it changes. The client is told
   everything is fine *unless* a trigger changes the availability of what it was promised.
5. **The three indices are free.** Pick them so every common question is one partial-index read on what the reader
   already knows, and every trigger is a family. There is no required naming convention.

Test any design against one question: *if this were a multithreaded program on one machine, would I do this?* You
would not send a thread a message and wait for its reply to update a shared table. You would not ack a memory write.
You would not run a server so that threads could ask it what the table says. Do not do it here either.

## 2. What you keep and what you throw away

| keep (the contract) | throw away (the incumbent's machinery) |
|---|---|
| the wire protocol its clients speak, byte for byte | its server process and its internal RPC / REST / IPC |
| its public API and return values | request/response between its own parts |
| its configuration surface | its locks, queues, worker pools, coordinators |
| its own test suite, unmodified | its schema, its caches, its maintained counters |
| its documented semantics (ordering, delivery, durability it actually promises) | its acks, retries, heartbeats between its own parts |

Clients outside the contract — a stock MQTT library, a Redis client — still do what their protocol says (a QoS 1
publisher waits for PUBACK). Answer them at once, from the memory write. Inside, nothing waits.

## 3. Phase 1 — mechanical conversion: learn what the incumbent actually does

The goal of phase 1 is **understanding**, not the product. You are learning the design, the core algorithms and the
contract by changing the incumbent as little as possible and watching what breaks.

1. **Build and baseline the incumbent.** Build stock. Run its own suite; record pass/fail per test with wall time
   and bytes (the harness records these). Build the side-by-side load harness now, against stock, so every later
   number has a stock column measured the same way on the same machines.
2. **Map the suite** (`<NAME>-SUITE-MAP.md`): every test, what it asserts, which part of the incumbent it exercises,
   and whether it is applicable. This is the roadmap.
3. **Find every place the incumbent's parts talk to each other or share state** — REST calls, RPC, shared tables,
   message queues, global structures touched by more than one thread or process. List them with file and function
   (`study-*.md`). In a single-process program this is the set of globals more than one connection touches.
4. **Replace them mechanically, one at a time, with memory reads and writes.** Same shape as the incumbent: if it
   called a service, call the memory; if it read a table, read the tuple family. No redesign yet. After each one,
   run the suite.
5. **Feed it good and bad data.** The suite's cases, plus boundary values, malformed input, adversarial ordering,
   concurrency it does not test (many clients, fan-in, fan-out, takeover mid-flight), and kills. Record what the
   incumbent does with each — that behaviour *is* the contract wherever the suite is silent.
6. **Write down the core algorithms** (`study-*.md`, one per area): what problem each solves, its invariants, what
   order it guarantees and to whom, what it does under contention, its edge cases, and where in the source. Write
   it as if the reader will never see the source. Examples: Mosquitto's topic-tree walk and its retained-search
   order; its session takeover; its QoS 2 state machine; Redis's blocking-pop wake order and WATCH.
7. **Keep a register** (`QUESTION-3.md`): every red, with the three questions in order — is the implementation
   wrong; was the contract misread; does the incumbent expose something the memory declines to reproduce. A
   register entry is a decision with its reason, not a bug.

Phase 1 is finished when you can state, for every piece of shared state: who changes it, who reads it, what order
readers need, and what the suite and your bad data say about it. The phase-1 code is scaffolding; it is not shipped.

## 4. Phase 2 — greenfield: the same contract, built on the memory

With the contract and the algorithms understood, start again. Do not port the phase-1 code; do not keep the
incumbent's structure because the incumbent had it.

### 4a. The truth table (write it before any code)

One row per piece of state the contract depends on (`DESIGN.md`):

| truth | owner (its one writer) | indices | latest value or distinct state | readers | trigger family | derived instead? |
|---|---|---|---|---|---|---|

- **Owner**: the participant whose fact it is. If two participants want to write it, the address is wrong — give
  each its own location and let readers union them, or make the contest state (claims, by write order).
- **Indices**: chosen for the read. Exact questions are one tuple; "everything for X" is a family with X fixed.
- **Latest or distinct**: one tuple overwritten (a slow reader gets the newest) or one tuple per value (every reader
  sees each, in write order).
- **Trigger family**: what a reader holds a read on, and what it does when it fires.
- **Derived**: counts, sizes, liveness, "who is behind" are computed when asked, not stored.

### 4b. Read-modify-write and ordering

Where the contract needs one order over a contested truth (Redis INCR, LPOP, SETNX, MULTI/EXEC; an election; a
takeover): never read, compute and write back across participants. Either give the truth one owner that applies the
others' published intents, or publish each change as an intent in the truth's family and let every reader apply them
in write-order id. The write-order id is the one order. A reply that reports the result waits for its own write to
land in that order — a read of memory, not a message.

### 4c. The participant

- A participant serves the contract's clients, holds what it needs as held views (best practices §3e), writes its
  own truths, and acts on triggers. 100–250 lines each; behaviour is added as participants and variables.
- Answer the client from the memory write. The only thing that changes an answer already given is a trigger.
- Exceptions only come back. Silence means fine.
- No timers for expiry, polling or retry. No participant-level mutex. No private waits — publish an applied cell.

### 4d. Verify as you go

Each step adds one truth or one participant, then runs: the incumbent's suite (unmodified), the good and bad data
from phase 1, and the side-by-side bench. Red first: a rule or behaviour gets a failing test before the change.
Measure with the same harness as the stock baseline; keep every run's logs.

### 4e. What you report

Side by side against stock, same driver, on at least two machines with traffic crossing between them (one machine is
just embedded RAM; run it on FrogNet's own simulator, not a new harness): throughput, latency, CPU per message, memory, loss,
and the suite. Lines and complexity of what replaced the incumbent's machinery against what it removed, counted as
statements. Qualification (the contract holds) and characterization (what the box sustains) separately. What is not
claimed, on the same page.

## 5. Checkpoints a next thread can pick up from

Every conversion leaves, in its example directory:

| file | phase | what it holds |
|---|---|---|
| `<NAME>-SUITE-MAP.md` | 1 | the incumbent's suite, test by test, applicable or not and why |
| `study-*.md` | 1 | the core algorithms, as understood, with source references |
| `QUESTION-3.md` | 1, 2 | the register: every red and its decision |
| `DESIGN.md` | 2 | the truth table, the indices, the triggers, the participants |
| `RESULTS.md` | 2 | measured, side by side, tied to a version |
| `README.md` | 2 | build, run, bench — one command each |
| `VERSION` | 2 | what the logs and the bench print, so every result names what produced it |

A thread that picks up reads, in order: this file, `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`, the example's
`DESIGN.md`, `QUESTION-3.md`, then `RESULTS.md`. Before changing anything it re-runs the suite and the bench to
confirm it is starting where the files say.

## 6. How it goes wrong — the MQTT conversion's first design

Every one of these was built, measured and then thrown out. Check your design against them.

1. **A server for the state.** `mqtt-ram`: a separate process holding the region, with its own operations
   (`hello`, `sub`, `claim`, `pub`, ...). The region is the memory; there is no server to ask.
2. **Messages instead of memory.** Batches of publishes sent to the server, results sent back, deliveries fetched
   by held "wait" requests. A participant writes its truth; the others' triggers fire.
3. **Acks on writes.** Each PUBACK held until the server answered — 136–152 µs per QoS 1 publish against 36 µs
   for stock, and 0.8× stock throughput on one publisher. The publish is in memory when it is written; the client
   is told at once.
4. **Asking who is listening.** The publisher looked up remote subscribers before writing. Write once; the
   subscribers' triggers find it.
5. **Calls that block on a round trip** for subscribe, claim, own, clean. Those are writes.
6. **Ordering by draining.** Waiting for every batch in flight to be answered before a call, to keep Mosquitto's
   order. The memory's write order is the order.
7. **Tuning the wrong design.** Lanes, coalescing, chunked deliveries, per-lane sequencers, same-box shortcuts:
   each was measured and each helped, and every one existed only because of items 1–6. When a fix exists only to
   make messaging cheaper, the fix is to stop messaging.
8. **A shape that cannot leave the box.** The fast resident shape (the region in one process) was measured as if it
   were the product. A design that cannot span machines is not the product; measure the shape that ships.
9. **A naming convention nobody asked for.** The three indices are free; choose them for the reads.

## 6b. Redis: one owner for the keyspace, and no copies

Redis's state is read-modify-write on keys every client may touch (INCR, LPOP, MULTI/EXEC, a blocked client's place in
line). The keys have one owner: the Redis region in the memory. A client never writes a key; its request is a cell
in its front's own space, `(redis.ask, <front>, <n>)`, and the region, holding one read on every front's requests,
applies it at the key's tuple (examples/redis/src/region.hpp). Two INCRs on one key are two requests to one owner, not two writers.

Two earlier designs each kept a second copy of something and needed a layer to keep it true -- that layer is the
pub/sub class of code, and it is what to look for and remove in any conversion:

| design | the copy | what kept it true | removed |
|---|---|---|---|
| RAM host + fronts with read caches (main, `[READS_AT_THE_FRONT_V1]`) | each front's cache of values it had read | per-front invalidation feeds, register-before-read ordering | `coherence.*`, the feeds |
| every box a copy, one order (2026-10-17) | the whole keyspace in every box | intent rings, pacing to the slowest reader, heartbeats, gap checks, a snapshot join, the same clock and seed on every copy | `apply.cpp`, rings, heartbeats, the join, dump/load |
| one keyspace (2026-10-19) | none | nothing to keep true | -- |

The test: if a design needs any mechanism whose only job is to keep two copies equal, the second copy is the bug.
A blocked command does not need a thread either: it keeps its call's answer, and the write that readies it wakes it.

## 7. For an AI doing a conversion

- Read §1 and best practices §13 first, every session. The failures are the default behaviour of a model trained on
  request/response code; they recur unless checked.
- Before writing code, write the truth table (§4a) and show it. If any row's reader gets the truth by sending a
  message to its owner, the design is wrong.
- Before adding any mechanism, ask the one-machine question (§1). If the mechanism exists only because parts are
  talking to each other, remove the talking instead.
- Research before answering: the repo, these docs, the example's checkpoint files, prior chats. Do not raise a
  question the files already answer.
- Report measured, side-by-side numbers. Say which shape, which machine, how many runs, and what is not claimed.
- Keep the contract green at every step; never modify the incumbent's tests to pass.

---

Determine your truth. Write your truth. Read the other truths. Answer from memory. Go.
