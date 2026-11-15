<!-- Generated from the site page spec-09-memory.html (https://fawcettinnovations.com/spec-09-memory.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §9

# Semantics and algorithms.

Companion to [§9 of the specification](README.md#s9). Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\]. The contract in draft form: [memory-contract.html](https://fawcettinnovations.com/memory-contract.html).

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Linda, 1985 — addressing memory by three keys

## Current implementation clauses: October 12, 2026

## Coordinates

service.variable.instance identifies a JSON bag. A write replaces it and receives a write-order id. A partial address selects cells in a table.

## Read surface

Read, held read after an id, read-fresh, write and remove are the conceptual verbs. Exact binding support and names come from its implementation. The modern C++/Python surface and supplied JavaScript guide must not be confused with every legacy wrapper.

## Collections and visibility

Collections use one cell per element. A container owner publishes parts before a header visibility marker. This convention is not a claim of arbitrary cross-cell transactional isolation.

## World changes

The transient store is not replicated application history. A changed memory host invalidates assumptions about the old world. Vendor Internet regions define their own persistence and restart policy.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the local path remains separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Model](#m1)
2.  [2Addressing](#m2)
3.  [3Write semantics](#m3)
4.  [4Read semantics and freshness](#m4)
5.  [4aFreshness classes](#m4a)
6.  [5Coherence and the database-host election](#m5)
7.  [6Partition and merge](#m6)
8.  [7What it is not](#m7)
9.  [8Limits](#m8)
10. [9References](#m9)

## 1. Model

FrogNet Memory is a single addressable store per connected pond, held by the elected database host. A value is addressed by a three-coordinate tuple key. A write stores the value at its key; a read returns the current value, its timestamp, or absence. For operational state, last write wins. The store provides no consensus, no cross-value atomicity, no history, and no total order beyond the serialization order of the single store.

**Lineage.** The tuple-space model is Linda: David Gelernter, “Generative Communication in Linda”, *ACM Transactions on Programming Languages and Systems* 7(1), January 1985, 80–112; and Nicholas Carriero and David Gelernter, “Linda in Context”, *Communications of the ACM* 32(4), April 1989. If a single author of the 1985 paper is named, it is Gelernter; Carriero is the long collaborator on the broader Linda work. **What FrogNet takes from Linda is associative addressing by tuple, and nothing else.** Linda’s `in` (destructive read) and `eval` (process creation) have no counterpart here (§10.3), the three-coordinate key is FrogNet’s convention rather than Linda’s, and Linda placed no serialization point where this places one (§9.7).

## 2. Addressing

A tuple key is three coordinates. The implementation names them **`service`, `var` and `scope`** (§10.3), and that vocabulary is normative here too. The first two are chosen freely by the application; **the third is constructed, not free-form** — a scope takes one of the host, node, role or session forms defined in §10.3. The store imposes no schema and requires no registration; a key exists when it is first written. Coordinate names sort, so hierarchy is a naming convention, not a store feature.

The conventions below show `service` and `var` chosen to fit a problem. They are illustrations of the first two coordinates, not evidence that all three are free-form.

| Use             | Convention                      |
|-----------------|---------------------------------|
| Program state   | `program / variable / user`     |
| Machine globals | `host / globals / variable`     |
| Sensor fleet    | `sensor name / type / location` |

A query MAY specify all three coordinates (one item), any two (a set), or any one (a larger set). A prefix query on a name is the enumeration primitive; the FrogNet monitor reads its sensor rows entirely as such reads.

![The three-coordinate key](https://fawcettinnovations.com/magnum-figures/tuple_address.png)

The three-coordinate key

The third coordinate is a **scope**, and scopes are constructed rather than free-form:

| Scope   | Form               | For                                                                                                        |
|---------|--------------------|------------------------------------------------------------------------------------------------------------|
| host    | `host:<ip>:<pid>`  | A long-running service whose tuple should be reaped once that instance stops refreshing it                 |
| node    | `host:<ip>`        | A periodic refresh writer whose tuple must outlive any single process                                      |
| role    | `host:<ip>:<role>` | Capability tuples. A node advertising two roles MUST qualify by role, so each role's row has its own name. |
| session | `session:<id>`     | Per-call state, one scope per concern                                                                      |

### 2.1 How the three coordinates reach the store

The coordinates are not stored as three columns. They compose:

``` code
SensorType     = <service>
SensorName     = "SD:" + <var> + "." + <scope>
SensorAddress  = the writer's own 10/8 identity — who reported it
```

Two consequences follow from that composition and an implementation MUST observe both.

**The name carries the scope, and the type does not reach the name.** This is why a node advertising two roles MUST qualify its scope by role: `SensorName` does not include `SensorType`, so two roles written under a bare node scope produce the same name. Under the earlier name-only key the upsert overwrote one with the other and left a single row whose role flipped; the key is now Name + Type + Address (below), and the role scope keeps each role's row distinct by name (§9.2, `role_scope`).

**The `SD:` prefix marks a tuple as coordination state.** A name beginning `SD:` is ephemeral service state — capability, presence, level, call signalling — and is a candidate for reaping. Observed sensor data carries no prefix and MUST NEVER be reaped. The prefix is self-identifying, so a dead service's orphans stay recognisable without a registry of what was written.

**Coordination tuples default to the control host, not the elected data host.** Writes and reads through the tuple interface default to `databasehost_control.frognet` (§8.1a), which is where the coordination plane lives. Durable data addressed to `databasehost.frognet` is the elected store of §9.1. An implementation MUST NOT assume one destination for both: the election reads capability rows from the deterministic control host precisely so that it does not depend on the outcome it is computing.

At the store, the address is the unique key **Name + Type + Address** \[TUPLE_ADDRESS_V1\]; all three MUST be present in every write, empty where the caller supplies nothing, because the conflict clause that makes the write an upsert only fires when the whole key is in the statement.

## 3. Write semantics

A write MUST be a single atomic upsert against the unique tuple key (`INSERT ... ON DUPLICATE KEY UPDATE`): resolve-or-create in one statement. A write MUST NOT require a prior read to resolve or create its tuple. Two concurrent writers are ordered by the single serialization point; the later write wins. This guarantee covers the write only: an application that reads a value and derives the next one from it has constructed a read-modify-write cycle and owns its races. **Programming-model note.** A read-modify-write cycle is available where an application genuinely requires one, but it should not be the default model for distributed coordination. Where possible, independent producers SHOULD write independently addressed tuples representing their own current truth, leaving consumers to operate across those values with `get_all` (§2b, §10.3b). This avoids converting shared memory back into a message-passing coordination problem.

Single-writer discipline is the operative convention: each key has one writer by construction — a node writes its own state. Cross-key composition (two values moving as one) is not provided; where an order across values is required, the decision MUST be routed through an elected role rather than inferred from the store.

## 4. Read semantics and freshness

A read MUST NOT block on any remote condition unless the caller asks it to: `sensors&action=values` with `wait_s` and `min_rows` holds the read at the store until that many rows match, at most 30 seconds. It returns the current value with its timestamp, or absence. A value outside the freshness window MUST be treated as absent, not as wrong. The fabric issues no change notification; how an application observes change is its own policy (§10.3a). What the store provides is bounded-stale current state: a read observes some real moment, timestamped.

**The store decides freshness, not the reader** \[ENVELOPE_TS_V1\]. A freshness-bounded read passes its window to the store, which filters on the row's own update time — on the clock that stamped it. A reader MUST NOT compare its own clock against a timestamp inside the payload.

That was the earlier behaviour and it failed exactly as skew predicts: a node whose clock ran two minutes slow disappeared from every roster while publishing correctly, and the only symptom was an empty membership list. One clock decides and it is the one that wrote the row, so skew between nodes cannot matter.

Freshness is **envelope** information. A payload MAY carry a timestamp of its own and it MUST NOT be consulted for this purpose; where a caller reports an age, the age reported MUST be the store's number.

**A row whose freshness cannot be established is not fresh.** Where a bounded read returns a row carrying no update envelope, the reader MUST drop it and MUST say so. A store that does not implement the filter returns everything, and passing those rows through presents a full history as though it were current: departed peers in the roster, months-old sessions offered as joinable.

## 4a. Freshness classes are application vocabulary

Names such as `CONTINUOUS`, `LATEST_ONLY`, `LOSSLESS_EVENTUAL` and `RESIDENT_ONCE` appear in the build manual. **They are examples, not a fabric enumeration.** This specification defines no set of freshness classes and an implementation is not required to recognise any name.

The reason is the boundary stated throughout (§10.3a): **what counts as fresh is an application property.** The store supplies the bound and the timestamp that stamped the row (§9.4); whether four hundred milliseconds is current or fatal is domain knowledge the fabric does not hold. An application MAY name its own classes. An implementation MUST NOT infer behaviour from a class name, and MUST NOT treat any such name as reserved.

## 5. Coherence and the database-host election

Spec: [§8](README.md#s8) · Croakus: [§15](https://fawcettinnovations.com/magnum-croakus.html#15-selecting-the-service-hosts), [§16](https://fawcettinnovations.com/magnum-croakus.html#16-one-name-a-moving-target)

Coherence is structural: one database per network is one serialization point is one order. The election preserves that invariant when membership changes.

``` code
on membership change (join, split, merge, loss of host):
  1. each node publishes its capability row -- cores, memory,
     disk class, MySQL running -- keyed by its own identity
  2. each node reads every capability row
  3. each node runs the same scoring function over the same rows
  4. each node arrives at the same winner independently
  5. the winner assumes databasehost.frognet; every other node
     resolves that name to the winner
```

No votes are exchanged and no announcement is made; determinism over shared inputs replaces coordination. A node MUST rebind `databasehost.frognet` whenever its derived winner changes. Control state for the role lives in a separate plane (`databasehost_control`) so the role can move without the data plane renaming anything.

![Capability scoring — deterministic](https://fawcettinnovations.com/magnum-figures/election.png)

Capability scoring — deterministic

![databasehost against databasehost_control](https://fawcettinnovations.com/magnum-figures/two_planes.png)

databasehost against databasehost_control

## 6. Partition and merge

Spec: [§13](README.md#s13) · Croakus: [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split)

A partition is a defined state. Each fragment holds an election where the host was lost and continues with the members it retains. Writes from a fragment that cannot reach its store are absent, not queued. On heal there is no merge, no conflict, and no replay: the election recomputes, one store is authoritative, and writers resume writing current state. State is re-established because writers keep writing: each producer’s ordinary refresh cycle lands in whichever store is now authoritative, and the picture fills in as they do. **There is no heal-triggered republish of application values, no catch-up phase, and no protocol to describe** — nothing tells a producer to re-send (the fabric's own capability rows are the exception: every node re-advertises them on a merge and when the control host moves), and a value whose writer has stopped does not come back. Where an application needs its state re-asserted promptly rather than at the next refresh, that is what the reassignment callback is for (§10.7a).

## 7. What it is not

**Not replicated DSM.** 1990s distributed shared memory replicated pages with no serialization point and manufactured an order from message traffic. FrogNet Memory has a serialization point; it does not solve that problem, it removes it.

**Not a CRDT.** There is no merge function and no lattice. The store does not converge; a value is current or it is not.

**Where an application requires convergence, it computes it.** Convergence is decided by examining the holistic data state, not by contending for a single value: each producer writes what it independently knows under its own key, and a consumer reads the set with `get_all` and derives the converged answer on its own terms, at the moment it needs one (§10.3b). A merge law must work for every value its type can hold, without understanding any of them; a consumer that knows what the values mean can weight, discard, prefer or decline. An implementation MUST NOT supply a merge function, and an application that needs one has the knowledge to write a better one than the store could.

**Not a cache.** The SAME/DIFF machinery ([spec §11](README.md#s11)) re-executes against the origin on every repeat and reports identity; it never answers from a stored copy.

**Not transactional.** No atomicity across two keys, no consensus, no exactly-once event delivery. Distinct records use distinct owned addresses and readers advance their own marks. External effects that compound when duplicated still require an explicit recovery contract.

## 8. Limits

\(a\) One serialization point is a write-throughput ceiling; the coherence property and the scaling limit are the same fact. Writes are bounded by one point and do not scale horizontally; reads are relieved without a second copy. (b) Modern Ribbit provides held reads after a write-order id. The legacy get/get_all wrapper described in this clause does not provide that same held-read contract. How a consumer observes change is application policy (§10.3a); the latency it accepts follows from the mechanism it chooses, not from a fabric requirement. (c) Composition across keys is the application's problem by design (§3).

## 9. References

Specification [§9 (Memory)](README.md#s9), [§8 (Election)](README.md#s8), [§13 (Failure semantics)](README.md#s13). Build manual: Croakus [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split), [§70](https://fawcettinnovations.com/magnum-croakus.html#70-ribbit-without-frognet). Tuple-space lineage: Gelernter and Carriero, Linda, Yale, 1985; the three-coordinate key is FrogNet's convention, not Linda's. Video walkthroughs: [Intro to Tuples](https://youtu.be/7mKVzQMv5G0), [Setting databasehost.frognet](https://youtu.be/0jzD-U44pW0), [Memory Backing Store](https://youtu.be/ZmaTwfcxHwY).

[← §8 Service Discovery and Election](spec-08-election.md) [Index](README.md) [§10 UnREST →](spec-10-unrest.md)
