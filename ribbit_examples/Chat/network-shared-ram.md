# Network Shared RAM

**What it is, how it is built, what has been shown to work, and how an application gets its own shared memory.**

Fawcett Innovations LLC — working description, 18 September 2026.

---

## 1. The claim, stated plainly

Network Shared RAM is a programming contract: an application addresses a value by a name, writes it, and reads it, and the fact that the value lives on another machine across a network does not appear in the application's code. Writing replaces. Reading returns what is there now. A read can be held open until something is there, or until something newer than what the reader already holds arrives. There is no message to send, no reply to match, no reconciliation to perform, and no authority to consult. The value is the present state, not a log of how it got there.

This is not a claim that networks behave like physical memory. They do not: a network is slower, more variable, and more failure-prone than a memory bus. The claim is narrower and it has been demonstrated rather than argued: **a shared-memory programming contract can be made to operate over real network physics, and the point at which a given implementation on given hardware stops satisfying a given requirement can be measured.** The rest of this document is what that means in practice.

The objection worth answering is not "is this provably optimal." Nobody needs that. The objection is the basic one — *network shared memory won't work in practice; the network is too slow, too variable, too failure-prone, and the abstraction will hide the limits until things fall apart.* The answer is in two parts, and both are now backed by running code: it works, and its limits are not mysterious — they are discoverable per machine and per path, by measurement, on the actual hardware.

---

## 2. The contract

A cell is addressed by three coordinates — service, variable, instance — and holds one value (a JSON "bag"). The operations are few:

- **write(service, variable, instance, bag)** — replace the cell's contents. The write takes the next number in the memory's own write order, assigned at the one point where all writes serialize. That number is how "newer" is defined.
- **read(service, variable[, instance])** — return what is there now. Leaving the instance open reads every instance of the variable: a partial index is a set.
- **read(..., after=N, wait_s=F)** — the blocking read. Held open by the memory until something has been written past write-order N, then returns it; on expiry it returns what it has, which is short of what was asked, so "not yet" is distinct by construction.
- **read(..., fresh_s=S)** — return only cells written within the last S seconds. This is how presence and liveness are expressed without a separate mechanism.
- **remove(id)** — delete a cell.

Everything an application does is built from these. There is no other verb. Two independent behaviours matter and are kept distinct:

- **Latest-value (replacement).** A cell holds one value; a new write supersedes the old. A reader slower than a writer is *expected* to skip intermediate values — it is always handed the newest, never a backlog. This is current truth: a kart's position, a cursor, a gauge.
- **Distinct-state (accumulation).** When every value must be seen, each value is given its own cell (its own instance). Nothing is ever replaced; every reader observes every value exactly once, in order. This is a queue, a chat log, a set of gradient contributions.

The same contract expresses both. The application chooses which by how it addresses cells, not by asking for a different service.

---

## 3. Two planes

The contract above is the **slow plane**: structured state, addressed and durable within a session, read and written through the memory. It is where coordination lives — presence, rosters, plans, barriers, results, descriptors.

State that changes on every tick does not belong in the slow plane. A kart's position at 60 Hz, a video frame, an audio packet — these are the **fast plane**. The fast plane is a reflector: one publisher's stream is fanned out to every subscriber over kept connections, send-or-drop, keyed by stream name. Nothing is stored; what arrives is current because a value that cannot be sent right now is dropped, not queued, and the next value is newer anyway. The send buffer is deliberately small: the buffer is the latency, and a small buffer bounds how stale an in-flight value can be.

This two-plane split is not new to the benchmark; it is how the FrogNet Communicator already carries a video call — audio and video on separate planes, video latest-droppable, audio protected — and how the game profile carries position (latest-droppable) separately from events (lossless). The slow plane holds the descriptor that says where the fast plane is; the fast plane carries the bytes. An application shares slow state by connecting to the same memory and fast state by connecting to the same reflector.

**What depends on what.** Network Shared RAM (the memory and the reflector) depends on FrogNet Core for exactly two things: transport (the wire) and compression (the codec). It needs nothing else from Core — no election, no tunnels, no broker, no daemon, no template store — to run one application's memory. This is the separation of FN Memory from FN Core: FN RAM does not depend on FN Core except for transport and compression.

---

## 4. Implementation methods

The contract has been implemented more than once, and the implementations interoperate. That is the strongest form of the existence claim: a client written one way talks to a server written another way, through the same shared state, and neither can tell the difference.

### 4.1 The wire

A permanent socket carries length-prefixed frames. A session is two outbound connections — a request channel and a return channel, established by a HELLO and a HELLO RETURN handshake. Replies are tagged with an implicit sequence number and matched by it, so a read held open for many seconds does not block a write going out and coming back on the same session. Requests on a session are executed in the order they arrive; only a held read is set aside so it does not block the writes behind it.

A request the far end already holds can be re-sent as a 21-byte REPEAT rather than in full; if the answer has not changed, the reply is a 21-byte SAME, served from a copy the client keeps. An idle reader therefore costs 21 bytes out and 21 bytes back per wait. When the far end has forgotten a request, it answers MISS and the client re-sends in full. Error replies are never cached, so a transient failure cannot become a permanent answer.

### 4.2 Transient in-memory RAM (C++)

A single process holds the cells in memory, serves the wire, and is the memory. The blocking read parks on a condition variable and is woken by the write that satisfies it — there is no polling interval to tune. Every write takes the next id under one lock, so the id order is the commit order and a reader asking for "everything after N" can never be shown N+1 while N is still in flight. The memory is indexed by write order, so a read or a remove costs what it returns rather than what the memory holds. Its request cache is bounded by both entry count and entry size. A restart is an empty memory; this is the right choice for transient state.

### 4.3 Persistent database-backed RAM (PHP + MariaDB)

The same application-visible contract, with the state stored in a relational database behind a PHP API. Writes take their write-order number from a single row inside each write's transaction, so ids are in commit order and concurrent writers queue rather than deadlock. The blocking read polls the table on a short interval. This implementation is dramatically slower than the in-memory one and entirely appropriate where it fits: the state survives a restart, and the required rate is low. It is the same contract; the choice between the two is per application and can be changed without touching a client.

### 4.4 The fast plane

A current-value publish/subscribe plane: a name holds one value with a generation; publishing replaces; a publish whose generation is not newer than the one held is dropped, so an older state can never replace a newer one. A read blocks until the plane has something newer than the reader holds; expiry is its own answer. The plane stamps its own arrival order so one "after" value is exact across a whole prefix. Publish is send-or-drop at the sender's socket. This is the mechanism the reflector generalizes for one-to-many.

### 4.5 Clients and languages

There is one C++ client library — Session, Memory, Plane — behind a flat interface, POSIX and Winsock, no dependency beyond sockets, threads, and a small JSON reader. There is a Python client of the same shape. They are two implementations of one contract, and they have been shown to exchange messages through the same memory and to read each other's writes on the same plane. The Python client is the byte-for-byte reference; the C++ vectors reproduce it.

---

## 5. What has been demonstrated

Every result below is from running code. Absolute performance figures were taken on a constrained sandbox (a single shared CPU core, loopback) and on a small number of physical machines; **they are reference measurements, not properties of the architecture.** The ratios and the behaviours hold; the numbers belong to the hardware they were measured on, which is why the tooling measures each machine rather than publishing a fixed number.

### 5.1 A real application across the real Internet

A peer-to-peer chat client — two threads and nothing else — was built on the contract. One thread reads the terminal and, on Return, writes the line to the recipient's buffer; the other sits in a blocking read on its own buffer and displays what arrives. Message ordering is the memory's write order; presence is a cell rewritten on a timer and read with a freshness window; addressing one line to several people is several writes. It runs stand-alone on Windows and Linux, in Python and in C++, and the two interoperate. It was run across the open Internet between separate machines behind separate NATs, reaching a shared memory on a public endpoint.

The instructive part: after it worked, new features — a presence list, multi-recipient addressing — were added **with no server change at all.** Each feature was a new address in the same memory. In a message-passing design each would have needed a new endpoint, a schema change, and a coordinated client/server release. Here the application's behaviour lives in the programs that read and write; the memory does not know what the application is. An old client and a new client run against the same memory at once.

### 5.2 Qualification: does the contract hold under load?

A contention test puts ten participants in a mesh, ninety communicating pairs, every message its own cell, every reader required to observe every sequence number from every writer exactly once and in order, while reads, writes, and removes all contend for the one memory. **One dropped sequence number anywhere fails the run.** Across the reported runs, on both the in-memory and the database-backed implementations, at every load tested: no gaps, no duplicates, no ordering failures, every reader ended on the writer's last value. Skipping occurs only where the contract *says* it may — latest-value cells under a reader slower than its writer — and there it is measured, not failed.

This is the existence claim made concrete. It is not "write something and read it back." It is the hard case — accumulation under contention, with correctness required — passing.

### 5.3 Characterization: where does this machine stop satisfying a requirement?

The same machinery, driven up a ladder of increasing load, finds the boundaries. The skipped fraction of a latest-value stream is `1 − (reader rate ÷ writer rate)`, and this predicted measured results to within tenths of a percent. On a server whose blocking read re-checks on an interval, a latest-value reader loses nothing up to roughly one-over-that-interval; on the condition-woken server there is no such interval and no such tuning. Acknowledgement is the flow control: a writer that waits for each acknowledgement cannot outrun the memory, so nothing is dropped; a writer that does not wait gains throughput at the cost of delay and backlog, and most of those extra writes are never seen. The buffer is the latency: small socket buffers turn an overload into back-pressure on the writer instead of a growing backlog of already-stale values.

From these measurements a **recommended operating envelope** is derived by explicit rules — a policy states what is acceptable (p99 latency, freshness, skip fraction, CPU, queue growth, payload budget, headroom); each rule finds the highest tested load that stays inside the policy; the recommendation is a headroom fraction of that boundary. Every line names the run it came from; a boundary never reached is reported as "not reached (≥ X)"; what was not measured is listed as not measured, never guessed. The envelope can be recomputed against a stricter policy without re-benchmarking. One machine's distinct-state limit was found to be set by receive-queue growth while CPU was still low — the rule engine reported "workload not recommended: the limit is not compute" without being told to look for it.

### 5.4 The self-organizing benchmark

The benchmark that produces all of this is itself a small FrogNet application, and it needs no orchestrator. Participants discover one another **through the shared memory being tested**: each writes its presence, reads the others', and the roster is the set of fresh rows. The plan is a pure function of the roster — everyone reads the same roster and computes the same phases, so nobody assigns them. The barrier is a row count on a cell: everyone writes "ready" and blocks on the ready-set reaching N; nobody calls "go," because the start condition is simply a fact in the memory that all can see at once. Results are writes; the report is a read. The synchronization primitive for benchmarking network shared memory is network shared memory.

There is no direct participant-to-participant link and there cannot be — participants meet at a shared memory (slow plane) and at a reflector (fast plane), both server-side, both possibly the same address. Every interaction is a write reflected to readers. The benchmark measures that, because that is the only topology that exists.

---

## 6. Two experiments this enables (distributed training)

The prior "Psychedelic" work replaced a training framework's distributed coordination with FrogNet Memory: gradient contributions became independently materialized state that ranks consumed without the traditional message choreography, and it measured favourably against the conventional collective on a heterogeneous mesh despite carrying experimental machinery. The C++ implementation of the memory/plane contract opens a clean pair of experiments, kept strictly separate:

- **A — implementation.** Port the existing algorithm's FrogNet-facing hot path to the C++ library, same tensors, same collective semantics, same bit-identical result. Does removing interpreter overhead move the curve? On a bandwidth-limited WAN, the prediction is that it barely moves — which would *strengthen* the case that the advantage comes from the choreography, not the implementation. On a fast fabric, where software overhead has room to show, it should move more. The acceptance criterion is the exact parameter hash: reproduce it or it isn't Experiment A, and timing is not looked at until it passes.
- **B — architecture.** Once A establishes the baseline, redesign around what the C++ memory/plane and today's measured control rules make possible: one blocking wait across a whole round instead of peer-by-peer; opportunistic consumption order with deterministic accumulation order preserved for the bit-identical result; measured flow control; appropriate shedding of stale state.

A is meant to be boring. Boring on the WAN is an excellent result.

---

## 7. The public and per-app shared-memory experience

There are two audiences, and the contract serves both.

### 7.1 The public experience: "can this machine FrogNet?"

A single self-contained executable — `frogbench` — answers the question directly, with nothing installed. Run bare, it brings up its own RAM target in-process on loopback and runs the full suite against the machine: **qualification** (does the contract hold here — distinct-state, latest-value, blocking read, removal — PASS or FAIL) and **characterization** (the paced ladders the operating envelope is read from). It ends by saying whether this machine *can* FrogNet and how well. The same binary, unmodified, has produced a passing verdict and three different envelopes on a one-core sandbox, a 4-core laptop, and a 12-core laptop — same contract, same integrity, different limits, discovered rather than guessed. It is a demo, a conformance test, a benchmark, a profiler, and — feeding the planner — a capacity tool, in one file.

Pointed at peers, it forms an ephemeral test mesh: it reports which peers are online and when, exchanges a specific message with each and confirms the exact bytes returned, and on request climbs to a maximum sustainable rate within a latency budget. Because no two ordinary machines can open a direct socket to each other through their NATs, the public demo uses a **rendezvous on a broker with a public address**: a password-gated endpoint launches a transient RAM-only server keyed by a user-supplied word and returns its port; two strangers who run the benchmark with the same word share one memory across the real Internet. The launcher reuses a live server for a repeated word, assigns a random free port, caps how many can run, and each server frees itself on idle. All of this is built into the executable: one word, no ports pasted, no firewall for the participant to touch.

The result is a receipt, not a slogan. Someone need not trust a published number; their own machine, and their own link, produced the measurements, and the thresholds that turned those measurements into guidance are inspectable. The hardware characterizes itself. That is a better answer to "how many users can FrogNet support?" than any fixed figure — and it means no giant hardware-compatibility matrix has to be published.

### 7.2 The vendor experience: a per-app shared memory, owned by the app

An application does not use "FrogNet's memory." It uses **its own** memory, under its own name, with its own database name (never "FrogNet"), carrying no vocabulary from any other application — a chat app's memory knows nothing of sensors, karts, or gradients, and vice versa. A vendor ships the reference client implementing the contract with the handlers it needs, and a packager — run on a development host — produces a **pair of deployment packages**:

- a **server package** whose install script takes a port and a directory, stands up the application's own database, a loopback-only API, and the FNW1 listener as the one public port, touching nothing else on the host; and
- a **client package** whose install script takes the memory's address and installs the client — Python, or a native C++ binary built for the target platform (Linux and Windows), recorded so the client finds its memory with no arguments.

The server is not a FrogNet node. It installs only enough to run the comms for that one service — a listener in front of that app's memory, no daemon, no tunnels, no broker — which is what "separating FN Memory from FN Core" means in deployment. Every package is content-addressed and self-verifying; the packager refuses to ship one that does not match its manifest. New application behaviour is new addresses in the same memory, so a vendor ships a new client without touching the server, and old and new clients share one memory at once.

The planning tool closes the loop for the vendor. Given a workload — so many devices, each publishing so many current-state updates, so many persistent updates, so much bulk data — and the measured envelopes of the hosts and the measured characteristics of the paths, it reports, by arithmetic over measurements, whether this LilyPad carries that workload and, if not, what to change: move this state class to the fast plane, place transient memory on a faster host, cap database-backed updates, put a reflector on the devices' side of a thin link. It distinguishes three verdicts — inside the envelope, exceeds it, or *not established* because part of the workload lies outside what was measured — and for the third it prints the exact command that would measure it. It never turns "untested" into a yes or a no.

---

## 8. Summary of the state of the evidence

Two accomplishments, which should not be conflated:

- **Qualification** — *does network shared memory function under the claimed semantics?* Demonstrated, for both a transient in-memory implementation and a database-backed one, including the hard distinct-state contention case where every value is accounted for, and including a real application interoperating across implementations and languages over the open Internet.
- **Characterization** — *what can a particular implementation on a particular machine and path sustain?* Produced by driving each host and path to the point where an explicit policy is crossed, and turned into a per-machine operating envelope and a per-LilyPad capacity plan, every figure carrying the measurement and the policy that produced it.

The first establishes that the architecture works. The second determines its limits. The physical-machine and Internet-path measurements now being gathered broaden and sharpen the characterization; they are not what makes the thing network shared memory. It is already running. The qualification suite is the receipt.

*Reference figures in this document were measured on constrained sandbox hardware and a small number of physical machines and are illustrative of behaviour and ratio only. Every deployment measures its own hardware and paths; the tooling exists to produce those measurements and to show the policy by which they become guidance.*
