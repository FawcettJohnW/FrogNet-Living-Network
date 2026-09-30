# Programming Ribbit — Best Practices

What a program is when the network is memory. Written from nine conversions (PyTorch, Redis, Sourri, the
Communicator, Monitor, the chat client, lispers.net and the rest) and from watching two implementers pick
the model up cold. The mistakes in the last section are the ones they made. Across all nine the memory's
contract did not change: the lispers.net conversion ran from v0.4 to v0.29 against a byte-identical client
library and used three verbs (write, non-waiting write, read/held read) and never remove.

---

## 1. What is yours to change

The memory's contract is small on purpose: an address of three coordinates and five verbs — write, read,
held read, read-fresh, remove. That is the whole of what the memory promises. Everything above it belongs
to the owner of the region:

- what a variable means and how it is named
- what goes in a bag and how much
- which instances exist, and what the instance coordinate encodes
- what reads the region's api.php offers beyond the raw cell (sums, counts, prefixes, joins, projections)
- what a reader keeps hot and what it fetches
- what is derived at read time and what is stored
- which participants exist, what each waits on, what each writes

Reshaping these for the problem is not a liberty taken with the model; it is the job. A region is an
implementation and its API is the vendor's. The memory acquires a primitive only when the shape is
general (in nine conversions: once — the batch array). If you find yourself wanting the memory to do
something, first ask whether the region's schema or API should do it.

## 2. Addressing

The address is service.variable.instance, matched independently. Spend it well.

- **Service** is the application's name for the region (`lisp`, `db0`, `Sourri`).
- **Variable** is the kind of truth plus whatever makes a table: `registration|<iid>|<group>` puts the
  isolation key in the name so a reader never filters what it did not ask for.
- **Instance** is the key of one truth: the EID prefix, the field name, the list position, the xTR id.

One truth, one cell. A coordinate holds one value and writing replaces it. This is not last-write-wins:
there are no competing versions, so there is nothing to reconcile.

Two behaviours from one contract, chosen by addressing: a **latest value** (one cell, a slow reader gets the
newest, never a backlog) and **distinct state** (one cell per value, every reader sees every value once, in
order). Decide which you mean before you name the variable.

Collections are sets of single-record cells — one per element, index or key in the instance — read singly
with SAME/DIFF. A collection is never a value inside a bag. A bag that grows without bound is a table that
should have been a variable.

Prefer instance encodings that are small and order-preserving where order matters (8-byte big-endian for a
list position, not 16 hex digits); an instance that fits a small string costs no allocation at either end.

## 3. Reading: hold truth, do not fetch it

The single largest habit to unlearn. A conventional program asks for what it needs when it needs it. A
Ribbit program **keeps** what it needs and is woken when it changes.

- A resolver does not read the registration table on every query. It reads it once, keeps it, and parks a
  held read on the variable; a change wakes it and it updates. Queries are answered from what it holds.
- A held read is a participant waiting on a truth that does not exist yet. It is not a poll with a long
  timeout; nothing is re-read on a timer.
- Read the partial index you mean. A read with the instance open is a table; a read with it closed is a
  cell. A reader that reads the whole variable to find one instance has the wrong address.
- Freshness is the reader's arithmetic: `written_ms + ttl` against now, or `fresh_s` on the read. Nothing
  expires anything; a stale cell is simply not believed.
- Readers decide. A reader takes what is published and makes its own determination — liveness, ownership,
  whether a batch is complete, whether a foreign fetch failed. No participant waits for another's decision.

## 3a. Read-ahead and pipelining

The wire makes an unchanged answer cost 21 bytes. Design reads so that most of them are unchanged.

- **Pipeline single reads; do not read a range.** N cells you will need are N reads sent without waiting
  between them, answered in order; the ones that did not change come back as SAME from the client's own
  copy. Reading a range as one answer forfeits SAME for the whole range whenever any element moved.
- **Read ahead by sequence, never by guess.** The correct read-ahead is "everything past write-order N"
  or "the next K generations"; the memory knows what exists. A read-ahead keyed on a length you predicted
  is a bug that waits for the one time the length is wrong (`THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS`).
- **Bounded history is a ring.** The last N turns are N cells addressed by turn number; a reader
  pipelines N single reads and pays for the ones that changed. Sourri's history read fell from 339 kB to
  31 kB per run this way, with no change to the memory.
- **A held read is the read-ahead for the future.** Park on the variable past the last write-order id you
  hold; you are woken with what is new and nothing you already have.
- **Cache nothing yourself.** The library's semantic cache is the cache. A second copy in the application
  is a second thing that can be stale.

## 3b. Races, and why last-write-wins is not the model

There is no last-write-wins because there are no competing writers. A coordinate has one owner. If two
participants are writing the same cell, the address is wrong, not the arbitration.

- **Each participant writes its own cells.** Two xTRs registering the same prefix write `prefix|xtr-a`
  and `prefix|xtr-b`; a reader unions them. Nobody merges two writes into one cell.
- **Where one truth is contested, the contest is state.** A claim cell per contender; write-order decides;
  the winner acts. The race is not prevented; it is published and resolved by reading.
- **Where a write depends on a read, keep the dependency in one owner.** A read-modify-write across
  participants is the message-passing model wearing a disguise. Give the modified truth an owner, and let
  the others publish what they want done (a Want, an intent) and read the result.
- **Detecting change is arithmetic.** Every cell carries its write-order id. "Has this changed since I
  read it" is a comparison, not a lock. Redis's WATCH is exactly that, done in the region's API.
- **Reunion needs no reconciliation.** When a split heals, each coordinate still has one value — the one
  its owner wrote. There is no conflict to resolve because nothing was allowed to conflict.
- **Time is not an ordering.** Never order by timestamp across writers; order by write-order id within a
  variable, and by ownership across them.

## 3c. Containers and pointers

- **A container is a set of cells plus a header.** The header cell (`Order.<id>` with count, status, the
  key of each part) is written last, after every part cell; a reader that meets the header meets a complete
  container. The parts are ordinary cells (`Order.<id>.<n>`), read singly and pipelined.
- **A pointer is a tuple name.** A cell that holds `Customer.<id>` points at that customer; a cell that
  holds `Order.<id>` and a variable points at a whole collection. Following a pointer is a read. A pointer
  to nothing reads as nothing, and that is a result, not an error.
- **Do not nest.** A structure inside a bag cannot be read singly, cannot come back as SAME, and cannot
  be pointed at. Flatten it into cells and point.
- **Sets, maps, arrays, rings** are the same shape: one cell per element, the key in the instance, the
  container as the partial index over them. The instance encoding is the index: make it fixed-width and
  order-preserving where order matters.
- **The visibility marker is written last.** Whether it is a header, a batch array's completion, or a
  region's "materialized" cell, the thing that says "this is whole" is the last write, so a reader never
  mistakes a partial for a whole.
- **Shared structure is a pointer, not a copy.** Two orders for one customer point at one customer cell.
  A copy is a second truth.

## 3d. Other addressing optimizations

- **Put the isolation key in the variable name**, not in a filter: `registration|<iid>|<group>`.
- **Put the selection key in the instance** when reads want it: a per-length variable
  (`registration|iid|group|/24`) makes longest-prefix a short walk from long to short instead of a scan.
- **Hierarchical keys (prefixes, paths, domain names): one variable per level plus a manifest.**
  `registration|iid|group|/24`, and a manifest cell listing the lengths in use; longest match is a walk
  from the longest listed length down, over what is held, never a scan. A vendor `longest-match` read in
  api.php is the region-level alternative.
- **Direct-map what you look up by hash.** A region's own index of name → cell is the vendor's; a
  lookup should be a load and a compare.
- **Prime the region.** Anything the application would fetch at startup is written into the region before
  the application is started: decks dealt, catalog loaded, permissions set. Startup is a read of what is
  already there, not a sequence of requests.
- **Memory for truths, Plane for streams.** Anything too large, frequent or perishable for a cell — media,
  tensors, per-tick kinematics — goes on the Plane with a generation, send-or-drop and a blocking
  generation wait. Keep only its descriptor in a cell.
- **Non-waiting writes for state about to be replaced.** A presence heartbeat, a position, a counter
  nobody reads back: `write_nowait`. The only thing that slows the writer is TCP when the far end is
  behind.
- **Learn templates, then ship primed.** Develop with the full-learning library; package with the portable
  library and the primed store; the shipped semantic cache is real.

## 3e. Held views inside a participant

A participant that answers requests from truth it holds keeps a **held view**: a partial index kept current
by a held read, handed to request handlers as an immutable snapshot.

- **Read, then hold.** Read the partial index, take the highest write-order id, park a held read past it.
  Every watcher starts this way.
- **One writer per slot.** Split the view the way the memory is split: one watcher per variable (per prefix
  length, per table) is the only thing that ever replaces that slot. A watcher never touches another's slot.
- **Publish snapshots, do not mutate.** The watcher builds the new table and publishes it with one atomic
  store; request handlers load the pointer and read. Nothing in the request path waits for a watcher.
- **Snapshot lifetime must be lock-free too.** A handler may load a snapshot the instant before it is
  replaced; freeing it must wait until no handler holds it. Use epoch reclamation or hazard pointers.
  `std::atomic_load/atomic_store` on `shared_ptr`, and `std::atomic<std::shared_ptr>`, are NOT lock-free
  in libstdc++ (they take a pooled mutex or a spin bit); a source file with no mutex that calls them still
  locks on every request. Assert `is_lock_free` in a test.
- **A manifest watcher owns child creation.** When the set of variables to hold is itself dynamic (the
  prefix lengths in use), one watcher holds the manifest and starts child watchers; nothing else adds
  threads, so no collection lock is needed.
- **Withdrawal is a truth.** Publish inactive state rather than removing the cell, so a held reader sees the
  withdrawal as a value instead of an absence it has to interpret.
- **Zero participant-level mutexes and condition variables is the target**, not an optimization. If an
  object is written by more than one thread, the design is wrong: ask who owns this truth, who writes it,
  who reads it, and why it is mutable in place.

Candidate for the client library, not the memory: a packaged held view (`memory.hold(service, variable)`
returning a lock-free snapshot and the watermark to publish as the applied cell). Every conversion so far
built its own; building it once would make holding the default.

## 4. Writing: your own truth, once

- Write only your own cells. A participant that writes another participant's cell has taken on an authority
  the model does not have.
- Write once, without knowing your readers. A writer never confirms, never waits for an acknowledgement
  from a reader, never retries a write that landed. If the reply to a command does not depend on the write
  id, use the non-waiting write.
- Publication is synchronization; it is not scheduling. Writing makes a waiting participant runnable and
  returns. It does not wait for that participant to run. Do not build anything that depends on the reader
  having acted by the time the writer returns.
- A record of an action is not the action. Write the sentinel after the thing has happened, never before.
- Publish current state, not deltas. If a reader needs the delta, it computes one from the two states it
  holds.

## 4a. What never goes in a cell

- Secrets. A shared key, a password, an HMAC key is not a truth for readers; every participant that can
  mount the region can read it. Keep key material in the owning participant's configuration or in a region
  only it mounts; publish the existence and policy of the thing, never its key.
- Authority you did not verify. A write that changes another party's truth (a deregistration, a
  withdrawal) carries the writer's verified identity from the transport, and the rule for an absent or
  foreign identity comes from the incumbent's contract, never from a default.

## 5. Derived, not maintained

Anything that can be computed from published state is computed when asked, not kept.

- Counts, sizes, "how many are blocked", "which keys expire soonest": derived from cells and indexes at
  read time. A maintained counter is bookkeeping that can go stale and needs a lock to stay right.
- Liveness is derived from the value cell, the ttl cell and the clock. There is no reaper. If something
  must eventually be reclaimed, the region owner runs a participant that reads what is dead and removes it
  — server-side, on the region's schedule, not the reader's.
- A dead variable is one with no cells. It does not need to be told it is dead.

## 6. Coordination as state

There is no queue, coordinator, lock, condition variable or message anywhere above the memory. The
patterns that replace them:

- **Claims.** Several participants want one truth (a pop, a delivery, an election). Each writes a claim
  cell; claim order (the write-order id) decides; the winner acts; the claim is removed. A newcomer defers to
  any live claim already present. Nobody arbitrates.
- **Wants.** A participant that needs state nobody has published writes a Want cell (`Want.<needer>.<what>`).
  Whoever can supply it is parked on Wants and acts. A failed supply is written as a cell, not thrown.
- **Batches.** A set of truths that must appear together is itself one truth: the array of writes is
  published as a cell first, the members land carrying a pointer back to it, and a reader that meets a
  member resolves through the array. Wakes inside a batch are held until it is complete so a held read wakes
  once, after, and decides from the final state.
- **Presence** is a cell with a freshness window. Being present is having written recently.
- **Elections** are every node computing the same winner from the same published capability. The selector
  belongs to the service. There is no coordinator to be down.
- **Governed state.** When intent must be resolved (a game move, a seat taken), the intender publishes
  intent, one governing participant publishes the resolved truth, everyone else reads the resolved truth.
  That participant is an application choice, not a FrogNet authority.

## 7. Participants

A participant is a program that lives in a blocking read, does one thing when woken, and writes what it did.

- It holds nothing between wakes that it could re-derive from the memory.
- It never decides for anyone else.
- It records where it got to in its own mark cell, so a kill loses and repeats nothing.
- It writes a failure as a cell rather than throwing at a caller that is not there.
- It publishes what it has applied: an applied cell ("applied through write-order id N of variable V")
  that it alone writes after each update. Anyone who needs to know the participant has caught up — a test,
  an operator, another participant — waits on that cell with a held read. Never expose a private flag or a
  condition variable for it; the applied cell is both the synchronization and the telemetry.
- It is 100–250 lines. There is no framework. If it is longer, it is two participants.

Behaviour lives in participants, not in an ever-growing central server. Adding a capability is adding a
participant and a variable, not a branch in a switch.

## 8. Foreign objects

Anything whose truth lives outside the memory — a database row, a device, a REST service, a file — is read
by exactly one participant whose job is that source. It writes what it found as `<Source>.<key>.<part>`
cells, keyed in the source's own terms, with provenance in every bag. Needers write Wants; the source
participant is parked on them. Freshness is the reader's arithmetic. Nobody else talks to the source.

## 9. The region's API

api.php is the vendor's compiler. Generic reads (a cell, a partial index) are the floor. Add a read the
moment a caller would otherwise read everything and filter: `sum`, `count`, `fields`, `pair`, `prefix`,
longest-match, join. Choose them from what the simulator or the oracle shows callers doing, not in advance.
An API-specific semantic (Redis's WATCH, a game's turn rule) lives here and nowhere lower.

## 10. Testing: the incumbent's own tests as oracle

When converting an existing system, its own test suite is the specification and the existing implementation
is evidence of what the contract demands — never the reverse.

- Map the suite: applicable, not-applicable-by-construction, and why.
- For every red ask three questions, in order: is the implementation wrong; was the contract misread; does
  the incumbent expose something the memory declines to reproduce. The third kind goes in a register with
  its reason. A register entry is not a bug.
- The register has two kinds of finding: disagreements (kept red, with the reason) and mechanism
  eliminations (the incumbent demanded a property, the region met it with state, green).
- Tests assert externally observable behaviour and never the incumbent's internals (its cache, IPC, locks,
  process layout, timers).
- Mutation is gated. Fixtures are removed in `finally`. A dry run must fail when the oracle is absent;
  a green that asserts nothing is a red you have not found yet.
- Parse results and compare values. A substring match on serialized JSON is not an assertion.
- Red first. A behaviour or architectural rule gets a test that fails against the current code, preserved
  as its own checkpoint, before the change that makes it pass.
- Architectural rules are testable: no participant-level mutex, no request-path memory read, no key
  material in a readable cell, no `remove` where inactive state is meant. A source grep proves only what the
  source says; where the rule is about runtime behaviour (lock-freedom), test the runtime.
- Tests wait for observed state (a held read on an applied cell), never for a sleep.

## 11. Measuring honestly

- Count what does not move with the machine's mood when wall-clock is noisy: allocations per command,
  bytes on the wire, cells read per query.
- Format code normally before counting it. A 71-line file with 224 statements is 224 statements.
- Separate domain complexity (what your region needs), platform complexity (FrogNet's own RAM and wire,
  reused) and control complexity (the incumbent's machinery). Never let the platform vanish into the
  comparison.
- Qualification (the contract holds) and characterization (what this host sustains) are separate results.
  A benchmark does not establish failure semantics; a PASS does not establish an envelope.
- Every reported number ties to a frozen checkpoint. Measured and estimated are labelled.
- Say what is not claimed, on the same page as what is.

## 12. Packaging

Ship no bytecode. Ship whole directories. Verify by extracting and running the oracles. Every bundle carries
DOCTRINE.txt and a way to check the site. No fallbacks in a library: a failure is thrown, nothing is
substituted. The store never exposes its database; the memory is reached only through the semantic wire.

## 13. What first implementers get wrong

Both cold-start implementers so far made the same mistakes, in this order. Check for them before anything
else.

1. **Fetching instead of holding.** Reading the whole variable per query. The fix is a held read and a
   local copy, not a faster read.
2. **Storing what should be derived.** A TTL saved in the bag and never consulted; a counter maintained
   instead of computed.
3. **Treating the contract as the allowance.** Not reshaping the schema or adding api.php reads because it
   did not know it could. It can. That is the job.
4. **A stub that passes a test.** Advertising a capability whose implementation returns an empty answer.
   Withdraw the capability; let the test skip honestly.
5. **A flattering count.** Dense one-line code counted as lines. Count statements.
6. **A default where the contract has a rule.** Deregistering on a missing source because the code path
   needed a default. Settle it from the oracle and write it in the contract.
7. **A collection inside a bag.** A list in a value instead of one cell per element.
8. **Writing someone else's cell.** Usually to "fix" something the reader should have derived.
9. **Waiting for the reader.** Building on the assumption that the woken participant has run by the time
   the writer returns.
10. **A timer.** For expiry, for polling, for retry. There is a held read for the first two and no need for
    the third.
11. **Shared mutable state inside the participant.** Several watcher threads updating one container behind
    a mutex. One writer per slot, immutable snapshots.
12. **A hidden lock.** Removing every mutex from the file and then calling a library function that takes
    one. Check lock-freedom at runtime.
13. **A private wait.** A condition variable so a test can know the participant caught up. Publish an
    applied cell instead.
14. **A secret in shared memory.** Key material in a readable cell.
15. **A check that switches off when its table is empty.** "No sites configured, so authorize everything" kept older
    tests passing and quietly made deleting the last policy open the door. An empty policy table authorizes nothing;
    fix the tests, not the check.

---

Determine your truth. Write your truth. Read the other truths. Go.
