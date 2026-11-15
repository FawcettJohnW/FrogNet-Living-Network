<!-- Generated from the site page spec-02-invariants.html (https://fawcettinnovations.com/spec-02-invariants.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §2

# Architectural invariants.

Five statements that hold across every other section. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

## Current implementation clauses: October 12, 2026

## Owned truth

A participant publishes the state it can know. Independent writers use independent addresses. The memory operation replaces a value; it does not automatically reconcile competing writers.

## Derive from the present

Topology, claims and views are derived from authoritative published state. The hub computes its peers from current state and removes those no longer present.

## Publication and scheduling

A write makes parked readers runnable. It does not require those readers to finish before the writer returns. Progress is another owned truth, published after the work.

## Explicit failure

Lost region state withdraws the corresponding routes, names and tunnels. The implementation does not invent an equivalent world from a fallback source. The cleanup of 12 October 2026 removed the legacy fallbacks it listed; its static gate passes with 0 violations (`docs/cleanup-2026-10/static-no-fallback.sh`, FINAL-GATES.md row 3b).

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Statement](#b1)
2.  [2(a) One authoritative copy](#b2)
3.  [3(b) One serialization point](#b3)
4.  [4(c) No replication, no history, no merge](#b4)
5.  [5(d) Topology derived, never distributed](#b5)
6.  [6(e) Independent producer and consumer lifetimes](#b6)
7.  [7What these invariants exclude](#b7)
8.  [7aRationale](#v7a)
9.  [8References](#b8)

## 1. Statement

Croakus: [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory)

Five invariants hold across every other section of this specification. They are not properties of the current implementation that a later one might improve upon; they are the contract. An implementation that relaxes any of them is a different system, and claims made about FrogNet do not transfer to it.

## 2. (a) One authoritative copy

A connected pond MUST hold exactly one **authoritative** copy of each tuple, at the elected database host (a coordination-plane tuple at the control host, §1.3). No other node holds authority over a value.

**Every node runs a database, and that is not a copy of the store.** The local database holds the semantic layer’s templates and per-peer references on every node, always; it holds the tuple space only on whichever node is currently the elected host (§11.14).

The invariant is about authority, not about location. A derivative copy — a materialization, a read cache — is permitted where its agreement with the store is either proven on the request or bounded and declared (§2.2), and it can never become authority. An implementation MUST NOT let a derivative answer become the value of record.

**Consequence.** Every reasoning pattern that begins "when the copies diverge" is inapplicable. There is never a field of candidate values to arbitrate between, so there is no authority rule, no conflict-resolution algorithm, no CRDT, no vector clock, no tombstone, no per-type merge law, and no causal history.

**It forbids a second *authoritative* copy, not answering a read from RAM.** The reference implementation answers reads from RAM in two places. The line is **standing**: local state may answer a read only where its agreement with the store is either proven on that request, or bounded and declared.

| Permitted because            | Mechanism                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Proven equal on this request | The database host MAY serve a RAM materialization when a store-maintained generation counter shows it unchanged. The counter MUST be moved by database triggers so that every writer — another node, the local origin, a human at the prompt — moves it. The snapshot and the counter MUST be read in one consistent transaction. Where the counter or its triggers are absent the cache MUST disable itself for the life of the process. **Fail closed.** |
| Bounded and declared         | A node MAY cache its own forwarded reads, invalidated by writes it observes and backstopped by a short TTL. This is bounded-stale current state, which is what a read returns in any case (§9.4). **The freshness bound is evaluated by the store**, on the clock that stamped the row; a reader MUST NOT judge staleness against its own clock (§9.4, §13.3).                                                                                             |

**Violation signature.** RAM answering a question it has no standing to answer. Two forms, both of which have occurred: a snapshot loaded once and never revalidated, so writes by any other route are invisible; and a cache that returns an empty result set as an authoritative answer because the query carried a parameter it could not express.

## 3. (b) One serialization point

Writes MUST be ordered at a single point, at the database host (the control host for coordination-plane tuples). Last write wins in arrival order at that point.

**Consequence.** The order is total and requires no agreement to establish, because it is the order of arrival at one place. It is also, exactly and inseparably, the write-throughput ceiling (§17.3): the coherence guarantee and the scaling limit are one fact stated twice.

The guarantee covers the write only. An application that reads a value and derives the next one from it has constructed a read-modify-write cycle and owns its races (§9.3).

**Programming-model note.** However, if an application is routinely resolving write contention or attempting to coordinate competing writers, it is strongly suggested that it is using the wrong programming model. Rather than having multiple nodes contend over a shared value, each node should independently write its own truth to shared memory, in its own time. Consumers, also in their own time, can retrieve those tuples with `get_all` and perform application-specific processing across the captured values. The programming pattern is analogous to lockless multithreaded programming on a single computer: independent producers publish state without coordinating with one another, and consumers operate across the resulting state when they need it. In the FrogNet model, there is no requirement for a coordinating server and no supporting REST infrastructure. Memory is written to and read from as if it were local RAM (§10.3b). The worked example is [UnREST by example](https://fawcettinnovations.com/unrest-examples.html); the same move at the topology layer is §4.1a. **Because the memory is directly addressable, no coordinating or resolution server is required. There is no store-and-forward, and no consensus derivation from incomplete data.** Each node writes its own truth in its own time, and consumers directly read that data when they require it (§10.4).

**The analogy is to the programming pattern and not to memory consistency semantics.** Lockless multithreading rests on atomics, memory ordering and happens-before relationships. FrogNet supplies none of those and an implementation MUST NOT be read as providing them. What carries across is the shape of the solution — independent producers, no coordination between them, interpretation at the reader — not the consistency model beneath it.

**The model is contention-free. A write replaces the named value at the store. In modern Ribbit, independent participants own separate addresses and use write-order ids; competing application truths are not reconciled by a last-write-wins merge. No contention.** No writer blocks on another, waits for another, retries after another, or is refused because of another; there is no lock to acquire and therefore nothing to contend for. Two writes to one description both succeed and the later one stands. What such a design loses is information rather than liveness — one truth overwrote another somebody wanted — which is a modelling error (§10.3b), not contention.

**The elected database host is not a counter-example, and the distinction is worth stating because it is the obvious objection.** A coordinating server is one that participants must consult *about each other* — to obtain permission, to learn who else is present, to have an outcome arbitrated, or to be told when to proceed. The database host does none of that. It is the address the memory lives at. A write goes there for the same reason a store instruction goes to a memory controller: that is where the location is, not because something there is deciding anything.

The test is what happens on arrival. A coordinating server evaluates the request against other participants’ state or intent. The database host applies an upsert and returns. **It never consults another node, never waits for one, and never resolves anything between two of them** — there is nothing to resolve, because no two writers address one value (§10.4). Serialization is a property of there being one location, not an act of coordination.

Stated once: **publish what you know, do not coordinate what you do not have to, and derive the answer from shared state.**

## 4. (c) No replication, no history, no merge

An implementation MUST NOT replicate tuples, retain history, or perform any merge procedure. Nothing replicates on write; nothing ferries in the background; nothing merges on rejoin.

**Consequence on partition.** Each fragment elects its own host and has its own store. On reunion nothing is merged, nothing migrates, and no value travels. Values last written elsewhere remain observable until they age past the freshness window — which the **store** evaluates on the clock that stamped the row, never the reader on its own clock (§9.4, §13.3) — at which point they **cease to be observable rather than becoming wrong** (§6, §13).

**Consequence on consistency.** FrogNet is not eventually consistent and does not converge. A value is current or it is not. An implementation MUST NOT describe its behaviour as convergence; the word is reserved in this specification for discovery reaching a stable derived topology (§6.7).

The store holds current state, not history. State is time-ordered, idempotent under repetition, and forgiving of loss. For data of that shape a consensus round over a marginal link agrees on a value that is already stale.

## 5. (d) Topology derived, never distributed

Each node MUST derive topology independently from its own local observation. No controller distributes a view, and no node accepts a topology asserted by another.

**Consequence.** There is no coordinator to lose. A gossip layer exists (§4) and carries change notifications so that neighbours re-derive sooner; it never carries a derived view and never carries a tuple value. The tell is what a wave carries.

The same principle governs roles: every node publishes what only it can know, every node scores the same inputs with the same function, and all arrive at the same winner independently (§8). No vote is exchanged and no announcement is made.

## 6. (e) Independent producer and consumer lifetimes

An application MUST be able to publish state without identifying any consumer, and to read it without identifying any producer. Neither operation may be made contingent on the liveness, reachability, or prior registration of another participant.

**Consequence.** A reader observes values written before it started, and a value outlives its writer **where the writer declared it unowned**. Lifetime is a property the writer states (§10.3): an owned tuple is ephemeral coordination state and is reaped; an unowned one persists and ages out. **Reaping is a sweep, not an exit hook.** A process cleaning up its own tuples at exit is a courtesy that fires on normal exit and graceful signals and MUST NOT be relied on — it does not fire on SIGKILL, power loss, or a crash, which on a contested mesh is when nodes die. The reference implementation no longer sweeps: its tuple reaper was retired, and readers decide liveness from the store-stamped time (§9.4). An implementation MUST NOT treat an owned tuple as removed at the instant its writer stops. What is invariant is not persistence but **independence** — neither operation may be made contingent on the liveness, reachability, or prior registration of another participant.

This is what makes an offline node a whole network rather than a degraded participant, and it is why nothing in the system needs a delivery guarantee: nothing is delivered.

## 7. What these invariants exclude

Each of the following is a category FrogNet is regularly read as, and none is a near miss.

| Not                                 | Because                                                                                                                                                                                                                  |
|-------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A distributed database              | One store, no distribution of data (a)                                                                                                                                                                                   |
| A message broker, queue, or pub/sub | Nothing is *delivered*: no topic, no subscription, no consumer registry, and no notification when a value changes (e, §9.4). A node publishing its own state into the store is a write, not a publication in this sense. |
| Eventually consistent storage       | No convergence; a value is current or it is not (c)                                                                                                                                                                      |
| A mesh VPN                          | The tunnels are a bearer beneath the model, not the model (§7)                                                                                                                                                           |
| A cache                             | BLDC-1 re-executes against the origin and reports identity; it never answers from a stored copy (§11)                                                                                                                    |
| A compression product               | The saving is structural, not statistical; no ratio is claimed for the codec itself (§11, §14)                                                                                                                           |
| New                                 | Linda, Gelernter and Carriero, Yale, 1985 \[LINDA85\]. What is new is that the constraints which made it impractical are gone.                                                                                           |

## 7a. Rationale

Not normative. Collected here so the sections above state requirements without arguing for them.

**On the database-per-node reading.** Every node runs a database and only the elected one holds the tuple space. A reader who sees the first and concludes the memory is replicated has conflated the two roles; it is the most frequent misreading of this architecture.

**On the absence of consensus.** One serialization point removes consensus from the design rather than solving it cheaply. The question “who decides when two values exist under one description” presupposes two values; a description holds a value. **Asking who arbitrates is applying the wrong programming model** — the question is imported from a world where several parties update one shared answer. A correctly modelled application never generates the case, and the useful question is why two parties are writing one key (§10.3b).

**On the derivative-copy line.** An uncached node is slow. A node serving from a snapshot it cannot prove current is wrong, and wrong is not a performance trade-off. That is why §2.2 permits a derivative only where agreement is proven on the request or bounded and declared, and why the cache fails closed when its generation counter or triggers are absent.

## 8. References

Specification [§9](spec-09-memory.md), [§10](spec-10-unrest.md), [§6](spec-06-pond.md), [§13](spec-13-failure.md), [§17](spec-17-limits.md). Build manual: Croakus [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory).

[← §1 Scope](spec-01-scope.md) [Index](README.md) [§3 Node Model →](README.md#s3)
