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

## MULTI/EXEC isolation -- RULED (John 2026-09-29)
"What possible reason could you have for stopping *everything* for one query." Redis isolates a transaction by running
nobody else during EXEC. Here nothing stops for one transaction: EXEC runs its queue in order, commands from other
participants interleave, WATCH is checked when EXEC arrives. Not a defect; the answer to the question.

## Multi-key reads at one moment -- RULED (John 2026-09-29)
"We will do a best-effort, but if the store underneath is changed by another thread, then the store is changed."
A multi-key read reads each key as it is when read; concurrent changes are the store changing. Not a defect.

---

## One keyspace (2026-10-19) [ONE_KEYSPACE_V1] -- what it changes in this register

The keyspace is in the memory once; a front's pipeline is one call, run on the memory's pool in parallel with every
other connection's, each key's tuple taking one request at a time (DESIGN.md). It replaces ONE_ORDER_V1 (2026-10-17:
every box a copy, applying every box's intents in write order), whose own entries 7-9 -- a box joining late by a copy,
intents that were not UTF-8, intents larger than a write frame -- are gone with the copies and the intents. What it
does to the entries above:

- **Entry 1 (lazy expiry)** stands: liveness is derived from value + ttl + the memory's clock.
- **Entries 2/4 (a batch is a value)** stand for the *STORE family and RENAME; an EXEC's writes land one by one with
  every wake held to its end, so a parked reader wakes once, after it. Another connection's write can land between an
  EXEC's commands -- the 2026-09-29 ruling (nothing stops for one transaction). `run_two_fronts.py` check 7 asserts no
  increment is lost and measures how many transactions saw another write in their middle.
- **Entries 3/5 (claims decide who is served)** are the line again: a parked command keeps its ticket while it waits
  (`[CLAIM_OUTLIVES_THE_TRY_V1]`), and the lowest ticket under a key is served (`run_two_fronts.py` check 4).
- **Entry 6 (served right after the write that readied it)** holds without a thread: the writer's own thread serves
  the parked commands its command woke, before its next command (`[WOKEN_BY_THE_WRITE_V1]`).
- **The two rulings (no MULTI/EXEC isolation; multi-key reads best-effort)** are in force again, as ruled 2026-09-29.
  (Under ONE_ORDER_V1 they held by construction, because one applier ran everything.)

New entries:

10. **Bytes on the session.** The session carries a call as text; Redis values are any bytes. Each byte travels as the
    code point of the same number (`[BYTES_AS_CODE_POINTS_V1]`): exact for any bytes, ASCII unchanged, no base64.

11. **Answers that finish out of order.** A blocked command's answer comes long after the calls behind it. The session
    keeps one answer reference per instance; calls with no instance share one, so out-of-order answers were
    differenced against a reference the front did not hold. Each connection's calls name a slot as their instance
    (`[A_CHAIN_PER_CONNECTION_V1]`); a slot is not a number (a numeric instance is read as a moving window -- see 12).

12. **Platform: the moving window after a REQ_MISS** (not fixed here; reported 2026-10-19). A "next in window" frame
    carries no location; the receiver takes one past the last it decoded. A REQ_MISS on the frame before leaves the
    sender's last location advanced and the receiver's not, and the next window frame decodes into the wrong instance,
    silently (seen: sent instance=4, decoded instance=2). Redis no longer uses the window; MQTT's publish window can.

13. **Platform: SAME after RAW** (fixed, `[SAME_ONLY_WHEN_BOTH_MOVE_V1]`, `cpp/include/fnwp_engine.hpp`). An answer the
    reply template cannot hold goes RAW and moves neither end's reference; the same answer again came as RESP_SAME, which
    moves the client's reference and did not move the host's. The next difference broke the front (incr.tcl: "a splice
    longer than its reference"). The host now moves its reference exactly as the client will before it says SAME, and
    does not say SAME when the client could not. `tests/run_same_after_raw.py`: red before, green after. The Python
    daemon's RAW-path SAME (`_build_raw_reply`) has the same shape; it diverges only if the proxy extracts what the
    daemon's proof refused -- not yet shown, not changed.
