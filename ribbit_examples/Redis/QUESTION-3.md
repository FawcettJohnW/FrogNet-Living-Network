# Question-3 register

The question: what did an independently designed system (Redis, through its own test suite) force us to
ADD to the programming model, and what did it merely force us to EXPRESS with the programming model?

Two kinds of finding, kept apart:

- **Semantic disagreements** — Redis's observable behavior cannot, or should not, be reproduced by the
  current Ribbit model. These stay red. (Entries 1, 6.)
- **Mechanism eliminations** — Redis demanded a semantic property, and Ribbit satisfied it with state
  rather than with Redis's mechanism. These went green. (Entries 2/4, 3, 5.)

The four categories so far, one question each:
- Expiry (1): should a system report stale bookkeeping? Ribbit derives current truth.
- Batch visibility (2, 4): what is the atomic publication unit — one truth, or a set of truths?
  Answered: a set of truths is itself one truth — a batch is a value (the array), published whole,
  members pointing at it, readers resolving through it, wakes once per batch. The one addition.
- Arbitration (3, 5): who owns a contested entry? Publish a claim; claim order decides; the winner acts;
  the claim disappears. Coordination without a coordinator.
- Synchronous handoff (6): does satisfying another participant's dependency oblige the publisher to wait
  for that participant to execute? Publication is synchronization; it is not scheduling.

Entries 2 and 6 are not both "timing". Entry 2 was about what one publication covers. Entry 6 is about
whether a wake is a handoff. Nothing in entry 6 is inconsistent; entry 2 was, and the array settled it.

---

## Semantic disagreements (red on purpose)

1. `unit/expire.tcl` — "Redis should lazy expire keys"
   Redis: with active expiry disabled, DBSIZE keeps counting keys whose ttl has passed until something
   touches them; then they vanish.
   Ribbit: liveness is derived from value + ttl + now, not maintained by a counter. DBSIZE reports the
   truth now. Reproducing the red would mean deliberately answering stale to emulate Redis's bookkeeping.
   Nothing is missing from Ribbit.

6. `unit/type/list.tcl` — "Linked LMOVEs" (passes or fails by scheduling).
   Redis serves a blocked client INSIDE the writer's call: RPUSH returns only after the parked BLMOVE
   has popped, so the tester's next LRANGE sees the pop. That is an execution-order relationship, not a
   state one: the writer does not finish until the dependent participant has been run far enough to
   consume the value.
   Ribbit: the writer publishes and returns. Publication makes the truth visible and makes the parked
   participant runnable; scheduling it is a separate matter. On one core the tester can read before it
   runs. A run-order fix in the RESP front would contaminate the experiment. Kept red.
   With the event-loop front the family is larger and more visible, because the event thread answers the
   tester before a handed-off participant has been scheduled: "Circular BRPOPLPUSH", and "Blocking command
   accounted only once in commandstats after timeout" (CLIENT UNBLOCK returns before the unblocked
   participant has finished its command; Redis unblocks it synchronously inside CLIENT UNBLOCK).
   "PUSH resulting from BRPOPLPUSH affect WATCH" joined the family once batches landed: the watcher's EXEC
   now regularly runs before the parked BRPOPLPUSH has pushed to the watched key.
   On a Pi 5 with four real cores (io-threads 4, eight test files in parallel) every test in this family
   passed: with a core available, the woken participant runs before the tester's next command lands. The
   disagreement is real but only observable when the participant is starved of a core.

## Mechanism eliminations (green, by state)

2. `unit/type/list.tcl` — "MULTI/EXEC is isolated from the point of view of BLPOP",
   "BLPOP, LPUSH + DEL should not awake blocked client" (and the BLMPOP variants),
   "BLPOP unblock but the key is expired and then block again - reprocessing command";
   `unit/type/stream.tcl` — "XREAD + multiple XADD inside transaction".
   Redis: a client blocked on a key must not observe the intermediate states of a MULTI/EXEC batch.
   The question was what the atomic publication unit is: one truth or a set of truths. The answer, John's:
   a batch is a value. The array of writes that belong together is one cell, published whole before any
   member lands; the members land carrying a pointer to it; a reader that meets a member sees the entire
   batch and decides (a later member removes this variable: the cell is gone; a later member rewrites this
   instance: that is the version). No state word, no pending flag, no commit CAS -- the atomicity is the one
   every cell already has. The wake is batch-granular: every wake inside a batch (and inside EXEC) is held
   and delivered once at the end, so a held read wakes after the whole batch and decides from the final
   state. Green: every "isolated" / "should not awake" / "reprocessing" test in list.tcl and zset.tcl.
   What went into the memory for this, and it is the first and only addition the suite forced: the batch
   array (data), the member pointer (one field per node), reader resolution through the array (a walk of
   a small immutable vector), and a quiet span for wakes. WATCH is not in the memory and will not be:
   it is Redis's contract, and it lives in the Redis API layer (version compare at EXEC, over the memory's
   write-order ids). A version-conditional write I had added for it is removed again -- the rule, John's:
   anything specific to Redis is implemented in the Redis API, never in the memory, whenever that makes
   sense. The memory learned one shape (an array with members pointing back), not a Redis feature.

4. `unit/type/zset.tcl` — the BZPOPMIN family of the same tests. Same answer, green.


3. Served-in-order for blocked pops is state, not a queue: a parked participant writes a claim ticket
   under the key in the db's claims region; the lowest live ticket is entitled to pop; leaving removes
   the ticket and touches the key. "BRPOPLPUSH with multiple blocked clients" and the unblock-fairness
   tests went green without a server-side queue. The first place the Redis contract (clients served in
   arrival order) was met by adding state to the memory rather than a mechanism to the server.

5. `unit/type/stream-cgroups.tcl` — "Blocking XREADGROUP ... reprocessing command": two parked readers
   of the same group's `>` were both served the same entry. The group cursor (last delivered id) is one
   cell, and two participants were writing it. Arbitration is real semantics: somebody must own the
   entry. Same answer as entry 3: a parked XREADGROUP places a claim ticket; the lowest live ticket
   delivers, the others re-wait. Green.
   Two smaller findings from the same file:
   - A participant's bookkeeping must not look like a data event. The consumer's seen-time rewrite on
     every wake moved the key's own wait word and woke the writer itself and every other parked reader;
     it is now written once before the first park.
   - SET over a container lands the string before the elements go, so a reader that wakes in between
     sees WRONGTYPE, never a missing key. That is publication ORDER within a two-step change — the
     externally meaningful transition — not a global transaction. Distinct from entry 2.

## Rule of placement (corrected)
A region is an implementation, and its API is how you talk to it. The Redis region is the Redis RAM
implementation; its API is free to be optimized for Redis -- WATCH, MULTI, blocking arbitration, batch
semantics, whatever Redis needs -- because it belongs to that region. api.php is discovery: how a participant
finds a region and learns its API. Nothing forces every region to speak one API or to be built from one
primitive set; another region can carry an entirely different optimized API for what it holds. This is the
vendor's opportunity: a region implementer proves value by what its API makes fast and true.
So the earlier wording -- "Redis semantics in a front over general primitives" -- was wrong. Redis-specific
semantics live in the Redis region's API, made of whatever the region wants. What this project has been
calling "the memory" (memory.hpp, lfmap, ebr) is that region's implementation; it happens to be general
because Redis needed general shapes, and that is a fact about this region, not a constraint of the model.

## Cumulative

Streams and consumer groups — entries as addressed truths, group position as truth, PEL/ownership as
claim truth, ACK as removal, blocked delivery as a held dependency plus claim arbitration, INFO blocking
statistics derived from what parked participants publish — required no queue, coordinator, mutex,
condition variable, reaper, transactional region snapshot, or new memory primitive. The complicated part
stayed application state. One thing forced its way into the memory: the batch as a value (entry 2), which
is a cell holding an array and a pointer from each member back to it -- a shape the memory already had,
not a mechanism it lacked.
