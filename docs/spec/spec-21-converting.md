<!-- Generated from the site page spec-21-converting.html (https://fawcettinnovations.com/spec-21-converting.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §21 · Ribbit reference

# Converting a system to Ribbit.

Companion to [§21 of the specification](README.md#s21). The reference process for taking an existing system to Ribbit: what is kept, what is thrown away, the three phases, the truth table, the rewrite of the core algorithms, the register of decisions, what is reported, and how the first MQTT design went wrong. Source: [`ribbit/docs/CONVERTING-TO-RIBBIT.md`](https://github.com/FawcettJohnW/FrogNet-Living-Network/blob/main/opt/frognet_semantic/ribbit/docs/CONVERTING-TO-RIBBIT.md), rendered as it stands in the tree; the same text is Part XII¾ of [Magnum Croakus](https://fawcettinnovations.com/magnum-croakus.html#ribbit-from-its-own-pages-anchor).

Ribbit reference — rendered from the source tree, revision 2026-11-15. These sections are descriptive: the key words of §1.2 do not apply, and the code is the contract.

In this document

1.  [1The model, in five sentences](#r21-1)
2.  [2What you keep and what you throw away](#r21-2)
3.  [3Phase 1 — mechanical conversion: learn what the incumbent actually does](#r21-3)
4.  [4Phase 2 — greenfield: the same contract, built on the memory](#r21-4)
5.  [5Phase 3 — make it froggish](#r21-5)
6.  [6Checkpoints a next thread can pick up from](#r21-6)
7.  [7How it goes wrong — the MQTT conversion's first design](#r21-7)
8.  [7bRedis: one owner for the keyspace, and no copies](#r21-8)
9.  [8For an AI doing a conversion](#r21-9)

The reference model for every conversion: what you keep, what you replace, and the three phases that get you from an existing program to one that uses the memory as memory. It sits on top of `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`, which says how to write Ribbit; this says how to arrive there from someone else's code. Read both before you write a line. Written 2026-10-17 from the MQTT conversion, where the first design got it wrong in the ways listed in §7. Phase 3 (§5) was added 2026-11-15 from the PyTorch and lispers.net conversions, which both went through it.

------------------------------------------------------------------------

## 1. The model, in five sentences

1.  **The contract is kept; everything inside it is yours.** The incumbent's external interfaces — its wire protocol, its API, its configuration, its own test suite, unmodified and passing — are the contract. Its process layout, its server, its internal messages, its schema, its threads are not.
2.  **The memory is memory.** State lives in the tuple space. A participant reports what is (reads) and sets what shall be (writes). Nothing asks another machine for anything.
3.  **Each participant writes its own truth, at its own locations, once.** One writer per location. No acks, no confirmations, no retries of a write that landed, no waiting for a reader.
4.  **Everyone else reads it from there, and triggers are the callbacks.** A held read on a tuple family — any combination of the three indices, fixed or open — fires when something in it changes. The client is told everything is fine *unless* a trigger changes the availability of what it was promised.
5.  **The three indices are free.** Pick them so every common question is one partial-index read on what the reader already knows, and every trigger is a family. There is no required naming convention.

Test any design against one question: *if this were a multithreaded program on one machine, would I do this?* You would not send a thread a message and wait for its reply to update a shared table. You would not ack a memory write. You would not run a server so that threads could ask it what the table says. Do not do it here either.

## 2. What you keep and what you throw away

| keep (the contract)                                                            | throw away (the incumbent's machinery)               |
|--------------------------------------------------------------------------------|------------------------------------------------------|
| the wire protocol its clients speak, byte for byte                             | its server process and its internal RPC / REST / IPC |
| its public API and return values                                               | request/response between its own parts               |
| its configuration surface                                                      | its locks, queues, worker pools, coordinators        |
| its own test suite, unmodified                                                 | its schema, its caches, its maintained counters      |
| its documented semantics (ordering, delivery, durability it actually promises) | its acks, retries, heartbeats between its own parts  |

Clients outside the contract — a stock MQTT library, a Redis client — still do what their protocol says (a QoS 1 publisher waits for PUBACK). Answer them at once, from the memory write. Inside, nothing waits.

## 3. Phase 1 — mechanical conversion: learn what the incumbent actually does

The goal of phase 1 is **understanding**, not the product. You are learning the design, the core algorithms and the contract by changing the incumbent as little as possible and watching what breaks.

1.  **Build and baseline the incumbent.** Build stock. Run its own suite; record pass/fail per test with wall time and bytes (the harness records these). Build the side-by-side load harness now, against stock, so every later number has a stock column measured the same way on the same machines.
2.  **Map the suite** (`<NAME>-SUITE-MAP.md`): every test, what it asserts, which part of the incumbent it exercises, and whether it is applicable. This is the roadmap.
3.  **Find every place the incumbent's parts talk to each other or share state** — REST calls, RPC, shared tables, message queues, global structures touched by more than one thread or process. List them with file and function (`study-*.md`). In a single-process program this is the set of globals more than one connection touches.
4.  **Replace them mechanically, one at a time, with memory reads and writes.** Same shape as the incumbent: if it called a service, call the memory; if it read a table, read the tuple family. No redesign yet. After each one, run the suite.
5.  **Feed it good and bad data.** The suite's cases, plus boundary values, malformed input, adversarial ordering, concurrency it does not test (many clients, fan-in, fan-out, takeover mid-flight), and kills. Record what the incumbent does with each — that behaviour *is* the contract wherever the suite is silent.
6.  **Write down the core algorithms** (`study-*.md`, one per area): what problem each solves, its invariants, what order it guarantees and to whom, what it does under contention, its edge cases, and where in the source. Write it as if the reader will never see the source. Examples: Mosquitto's topic-tree walk and its retained-search order; its session takeover; its QoS 2 state machine; Redis's blocking-pop wake order and WATCH.
7.  **Keep a register** (`QUESTION-3.md`): every red, with the three questions in order — is the implementation wrong; was the contract misread; does the incumbent expose something the memory declines to reproduce. A register entry is a decision with its reason, not a bug.

Phase 1 is finished when you can state, for every piece of shared state: who changes it, who reads it, what order readers need, and what the suite and your bad data say about it. The phase-1 code is scaffolding; it is not shipped.

## 4. Phase 2 — greenfield: the same contract, built on the memory

With the contract and the algorithms understood, start again. Do not port the phase-1 code; do not keep the incumbent's structure because the incumbent had it.

### 4a. The truth table (write it before any code)

One row per piece of state the contract depends on (`DESIGN.md`):

| truth | owner (its one writer) | indices | latest value or distinct state | readers | trigger family | derived instead? |
|-------|------------------------|---------|--------------------------------|---------|----------------|------------------|
|       |                        |         |                                |         |                |                  |

- **Owner**: the participant whose fact it is. If two participants want to write it, the address is wrong — give each its own location and let readers union them, or make the contest state (claims, by write order).
- **Indices**: chosen for the read. Exact questions are one tuple; "everything for X" is a family with X fixed.
- **Latest or distinct**: one tuple overwritten (a slow reader gets the newest) or one tuple per value (every reader sees each, in write order).
- **Trigger family**: what a reader holds a read on, and what it does when it fires.
- **Derived**: counts, sizes, liveness, "who is behind" are computed when asked, not stored.

### 4b. Read-modify-write and ordering

Where the contract needs one order over a contested truth (Redis INCR, LPOP, SETNX, MULTI/EXEC; an election; a takeover): never read, compute and write back across participants. Either give the truth one owner that applies the others' published intents, or publish each change as an intent in the truth's family and let every reader apply them in write-order id. The write-order id is the one order. A reply that reports the result waits for its own write to land in that order — a read of memory, not a message.

### 4c. The participant

- A participant serves the contract's clients, holds what it needs as held views (best practices §3e), writes its own truths, and acts on triggers. 100–250 lines each; behaviour is added as participants and variables.
- Answer the client from the memory write. The only thing that changes an answer already given is a trigger.
- Exceptions only come back. Silence means fine.
- No timers for expiry, polling or retry. No participant-level mutex. No private waits — publish an applied cell.

### 4d. Verify as you go

Each step adds one truth or one participant, then runs: the incumbent's suite (unmodified), the good and bad data from phase 1, and the side-by-side bench. Red first: a rule or behaviour gets a failing test before the change. Measure with the same harness as the stock baseline; keep every run's logs.

### 4e. What you report

Side by side against stock, same driver, on at least two machines with traffic crossing between them (one machine is just embedded RAM; run it on FrogNet's own simulator, not a new harness): throughput, latency, CPU per message, memory, loss, and the suite. Lines and complexity of what replaced the incumbent's machinery against what it removed, counted as statements. Qualification (the contract holds) and characterization (what the box sustains) separately. What is not claimed, on the same page.

## 5. Phase 3 — make it froggish

Phase 2 gives you the contract on the memory, and usually parity with the incumbent. Phase 3 is where the conversion stops imitating the incumbent and starts being what the memory makes possible. PyTorch and lispers.net both went through it, by the same steps, and both came out far ahead: meshfeed trained the same model to the same loss 329× faster than stock DDP on a five-node Internet mesh, and Ribbit-LISP served 17× the lookups and 7.9× the registrations of lispers.net over the Internet on about a hundred-and-fortieth of the CPU per lookup.

**Expect orders of magnitude, and do not stop until you see them.** The incumbent spends most of its time on work the memory removes: conversations, copies, waiting on other machines. Parity means that work is still there. It is where phase 3 starts, not a result. Both conversions reached parity first, and both kept going because the target was never parity; each step below was taken because the number was not yet where it had to be. When the conversion can go no further inside the incumbent's arrangement, the target still stands; that is the signal to change the arrangement (§5e).

### 5a. Put it where it will live, and measure the whole run

Run it on several machines with the Internet between them, the incumbent beside it in the same session as the control, and measure the whole run, not the best step. A single machine hides everything phase 3 is about: there, every round trip is free. Over the Internet, each Ribbit-LISP register waited a round trip to the RAM host, and up to 16 clients lispers.net registered faster; that is what v0.70 to v0.75 removed. PyTorch's backend looked about 60% faster than gloo until the control ran in the same session, minutes apart, on the same network.

Build the instruments into the code before the run, so one run answers the next question without another: every participant prints its build and refuses a mixed set, sockets report what held them, a slow operation says where its time went (`[DIAG-SOCK]`, `[DIAG-REGISTER]`). Use the simulator's slow links (`simulation/slowlink.py`) to get there first.

### 5b. Hunt down every conversation that is left

Phase 2 removes the obvious messaging. What it leaves behind is the expensive part. Go through the code looking for these, and remove each one:

| what is left                                     | PyTorch                                | lispers.net                                                                                   |
|--------------------------------------------------|----------------------------------------|-----------------------------------------------------------------------------------------------|
| a synchronous call: a participant asks and waits | the caller waited for its collective   | each Map-Register was a call to the RAM host (v0.68), then a request with no wait (v0.70)     |
| a copy of someone else's data                    | a RAM host per machine holding tensors | --                                                                                            |
| configuration sent again                         | --                                     | pushed to each process on start, each reply awaited; now written into the memory once (v0.71) |
| a read per thing instead of one held read        | one read per peer bag                  | one read per view; now one held read feeds every view (v0.72)                                 |
| a poll or a tick                                 | the store's blocking reads were spins  | the receive loop ticked; now it blocks (v0.73)                                                |
| acks, retries, caps                              | per-write acks and retry loops         | --                                                                                            |
| bits moved to where the reader is                | tensor bytes staged through a host     | --                                                                                            |

Do it as a loop, not a pass. Trace the code that actually runs, remove the next mechanism that exists only because the incumbent needed it, run the suite, and look again: a working rewrite still hides messaging, and each removal exposes the next. Ribbit-LISP went through five versions of this (v0.70 to v0.75), each one removing a thing the previous version had kept. Tune for performance only after the loop runs dry; tuning first optimizes machinery that should disappear.

The test is the one-machine question from §1, asked again of the code you now have. For each thing, ask where its cost belongs. A decision that needs only what a participant already holds is made there: Ribbit-LISP's front decides each Map-Register itself from the keys and sites it holds as views (v0.75), and the RAM host only stores what it decided. A read is a reference to where the bits already are: FrogTorch's RAM holds where each rank's memory is, and the bytes go once, owner to reader, when the reader needs them.

### 5c. Send adaptively

Whatever a participant writes into the RAM host goes through a window that grows while answers come back on time and halves when they slow. "If the system is backing up, back off." Ribbit-LISP's rows go in batches through such a window (`[WRITES_BACK_OFF_V1]`); without it, its registers collapsed under load.

### 5d. Fix what the measurement exposes, wherever it is

Phase 3's runs find problems outside the conversion, and they get fixed where they are, not worked around in the application. PyTorch's mesh runs found an access point on a single FIFO queue; fq_codel on every interface is now part of node setup, and with it gloo and the backend tied. Ribbit-LISP's runs found a load generator whose uplink was the limit, and the result says so.

The first conversion is also an investigation. It teaches you what the contract requires, which algorithms perform the actual work, and which mechanisms belong to the incumbent's arrangement. Once you understand those distinctions, you have learned enough to stop converting and write the program.

### 5e. Then rewrite the core algorithms as a multithreaded program

There comes a point where you have learned everything the incumbent's framework and implementation can teach: what the contract demands, which algorithms are the real work, and which parts exist only to move data between machines. From there, stop converting. Rewrite the core algorithms as what they always were underneath: a multithreaded program on one computer, with the shared memory as its RAM and held reads as its scheduler. The threads happen to run on different machines; the program does not need to know. That rewrite is where the arrangement changes, inside what the contract allows, and it is where the real gains show. Everything before it gets you to parity; the rewrite is what took PyTorch from a tie with gloo to 329×.

You are usually there when the conversion reaches the floor that the incumbent's own arrangement sets. DDP's arrangement moves a 16.9 MB gradient per machine per step, and no reduction algorithm beats the floor that sets on a slow mesh. meshfeed keeps PyTorch and moves the 4 KB minibatches to the machine that measures fastest instead: the data comes to the compute, placed by measured capability. Ribbit-LISP's first arrangement had the RAM host decide every Map-Register; v0.75 decides at the front that received the packet, and the RAM host stores what it decided. Both kept their contracts: the same held-out loss, matched to an oracle; the same wire, 78 tests.

Phase 3 is finished when nothing in the code waits on another machine except a held read for data it needs, the whole run on real machines is measured against the incumbent in the same session, and the arrangement is the one the memory makes best, not the one the incumbent needed.

## 6. Checkpoints a next thread can pick up from

Every conversion leaves, in its example directory:

| file                  | phase | what it holds                                                             |
|-----------------------|-------|---------------------------------------------------------------------------|
| `<NAME>-SUITE-MAP.md` | 1     | the incumbent's suite, test by test, applicable or not and why            |
| `study-*.md`          | 1     | the core algorithms, as understood, with source references                |
| `QUESTION-3.md`       | 1, 2  | the register: every red and its decision                                  |
| `DESIGN.md`           | 2     | the truth table, the indices, the triggers, the participants              |
| `RESULTS.md`          | 2     | measured, side by side, tied to a version                                 |
| `README.md`           | 2     | build, run, bench — one command each                                      |
| `VERSION`             | 2     | what the logs and the bench print, so every result names what produced it |

A thread that picks up reads, in order: this file, `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`, the example's `DESIGN.md`, `QUESTION-3.md`, then `RESULTS.md`. Before changing anything it re-runs the suite and the bench to confirm it is starting where the files say.

## 7. How it goes wrong — the MQTT conversion's first design

Every one of these was built, measured and then thrown out. Check your design against them.

1.  **A server for the state.** `mqtt-ram`: a separate process holding the region, with its own operations (`hello`, `sub`, `claim`, `pub`, ...). The region is the memory; there is no server to ask.
2.  **Messages instead of memory.** Batches of publishes sent to the server, results sent back, deliveries fetched by held "wait" requests. A participant writes its truth; the others' triggers fire.
3.  **Acks on writes.** Each PUBACK held until the server answered — 136–152 µs per QoS 1 publish against 36 µs for stock, and 0.8× stock throughput on one publisher. The publish is in memory when it is written; the client is told at once.
4.  **Asking who is listening.** The publisher looked up remote subscribers before writing. Write once; the subscribers' triggers find it.
5.  **Calls that block on a round trip** for subscribe, claim, own, clean. Those are writes.
6.  **Ordering by draining.** Waiting for every batch in flight to be answered before a call, to keep Mosquitto's order. The memory's write order is the order.
7.  **Tuning the wrong design.** Lanes, coalescing, chunked deliveries, per-lane sequencers, same-box shortcuts: each was measured and each helped, and every one existed only because of items 1–6. When a fix exists only to make messaging cheaper, the fix is to stop messaging.
8.  **A shape that cannot leave the box.** The fast resident shape (the region in one process) was measured as if it were the product. A design that cannot span machines is not the product; measure the shape that ships.
9.  **A naming convention nobody asked for.** The three indices are free; choose them for the reads.

## 7b. Redis: one owner for the keyspace, and no copies

Redis's state is read-modify-write on keys every client may touch (INCR, LPOP, MULTI/EXEC, a blocked client's place in line). The keys have one owner: the Redis region in the memory. A client never writes a key; its request is a cell in its front's own space, `(redis.ask, <front>, <n>)`, and the region, holding one read on every front's requests, applies it at the key's tuple (examples/redis/src/region.hpp). Two INCRs on one key are two requests to one owner, not two writers.

Two earlier designs each kept a second copy of something and needed a layer to keep it true -- that layer is the pub/sub class of code, and it is what to look for and remove in any conversion:

| design                                                               | the copy                                 | what kept it true                                                                                                          | removed                                             |
|----------------------------------------------------------------------|------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| RAM host + fronts with read caches (main, `[READS_AT_THE_FRONT_V1]`) | each front's cache of values it had read | per-front invalidation feeds, register-before-read ordering                                                                | `coherence.*`, the feeds                            |
| every box a copy, one order (2026-10-17)                             | the whole keyspace in every box          | intent rings, pacing to the slowest reader, heartbeats, gap checks, a snapshot join, the same clock and seed on every copy | `apply.cpp`, rings, heartbeats, the join, dump/load |
| one keyspace (2026-10-19)                                            | none                                     | nothing to keep true                                                                                                       | --                                                  |

The test: if a design needs any mechanism whose only job is to keep two copies equal, the second copy is the bug. A blocked command does not need a thread either: it keeps its call's answer, and the write that readies it wakes it.

## 8. For an AI doing a conversion

- Read §1 and best practices §13 first, every session. The failures are the default behaviour of a model trained on request/response code; they recur unless checked.
- Before writing code, write the truth table (§4a) and show it. If any row's reader gets the truth by sending a message to its owner, the design is wrong.
- Before adding any mechanism, ask the one-machine question (§1). If the mechanism exists only because parts are talking to each other, remove the talking instead.
- Research before answering: the repo, these docs, the example's checkpoint files, prior chats. Do not raise a question the files already answer.
- Report measured, side-by-side numbers. Say which shape, which machine, how many runs, and what is not claimed.
- Keep the contract green at every step; never modify the incumbent's tests to pass.

------------------------------------------------------------------------

Determine your truth. Write your truth. Read the other truths. Answer from memory. Go.

[← §20 Programming Ribbit](spec-20-ribbit-programming.md) [Index](README.md) [§22 API++ →](spec-22-api-plus-plus.md)
