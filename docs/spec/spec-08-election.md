<!-- Generated from the site page spec-08-election.html (https://fawcettinnovations.com/spec-08-election.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §8

# Service discovery and election.

No vote, no announcement, no coordinator. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Where the moving authority comes from

## Current implementation clauses: October 12, 2026

## Capabilities in the region

The daemon publishes slow-moving capability facts. Region election reads those facts from alive participants instead of querying capability tuples on the failed databasehost.

## Deterministic choice

Members apply the same selection rules to the available eligible state. The recorded simulation checks unanimous choices under death, return and capability changes, and counts store reads to ensure none occur on that path.

## Liveness cost

Election after known membership loss is local work. Silent-loss detection precedes it and depends on the session and path. Do not report the local election timing as the complete failover delay.

## Legacy role remnants

The mediahost role and the old Python Communicator and game remnants were retired on October 12, 2026; `databasehost` is the only registered role. The current Communicator uses its own RAM/media services.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the LAN-only and region paths remain separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1The model](#e1)
2.  [1a. Two hosts, and only one of them is elected](#e1a)
3.  [2Publication](#e2)
4.  [3The candidate pool](#e3)
5.  [3aElection is a handler slot](#e3a)
6.  [4Scoring](#e4)
7.  [5Planes](#e5)
8.  [6Non-goals](#e6)
9.  [7References](#e7)

## 1. The model

Croakus: [§15](https://fawcettinnovations.com/magnum-croakus.html#15-selecting-the-service-hosts), [§16](https://fawcettinnovations.com/magnum-croakus.html#16-one-name-a-moving-target)

A role is elected without a vote, an announcement, or a coordinator. Every node publishes what only it can know about itself, every node reads the same rows from the same place, every node runs the same scoring function, and all arrive at the same winner independently.

``` code
1  every node publishes its own capability tuple
2  every node reads every capability row from the control plane
3  every node runs the same scoring function over the same rows
4  every node arrives at the same winner independently
5  the winner assumes the role name; every other node resolves
   that name to the winner
```

The governing requirement is stronger than agreement: **the determination MUST be identical on every node by construction**, not reached by negotiation. Everything else in this section follows from taking that literally.

## 1a. Two hosts, and only one of them is elected

The scheme above has an apparent bootstrap problem: reading the capability rows requires knowing where they are, and where they are is what the election decides. It is resolved by **separating the plane the election reads from the plane it decides**.

[TABLE]

There is no bootstrap problem because the control host needs no election to find: every node computes the same highest address from the same membership. **This is the only place the highest address selects anything**; in the election proper it is a tiebreak and nothing more (§8.4.3). Published material describing a role as chosen by address is describing one of these two facts, and an implementation MUST NOT generalise either into the other.

The separation is also why a role can move without the data plane renaming anything (§9.5).

**Read the control address, not the control name, within a merge** \[SERVICE_HOSTS_ELECTION_V1\]. The name resolves through the hosts file the merge has not committed yet, so resolving it mid-merge lags a merge: a node publishes its capability to the previous control while the election reads the fresh one, leaving the fresh control ranking on stale data. The address is deterministic and already available from the membership this merge computed.

Publication MUST happen before the election reads, against that same fresh control, and MUST be logged on success as well as failure \[PUBLISH_EVIDENCE_V1\]. A silent success means a merge log cannot answer whether this node re-asserted its capability — the question every stale-ballot investigation begins with.

## 2. Publication

Each node writes a capability tuple per role it offers, on a periodic refresh (§3.6). The write MUST be unowned so the row outlives the short-lived writer and is not removed by the writer’s exit cleanup (§10.3), and the scope MUST be qualified by the role: a node advertising two roles under a bare node scope produces one row whose role flips as each write overwrites the other (§10.3).

Server software is not a precondition for advertising. Eligibility is decided by the gates in the scoring function, not by what happens to be installed.

## 3. The candidate pool

The pool is the set of capability rows in the store. Two rules make every node's pool identical.

**Rows do not age out and MUST NOT be gated on age** (§13.2.1). A departed node's rows stay in the store but leave the pool: only the live `.1`s of the converged hosts block are candidates \[POOL_IS_THE_LIVE_BLOCK_V1\]. Where a host holds several rows, every node MUST select the same one — newest by timestamp per address — because selecting by store order lets two nodes pick different rows for one host and score them differently, splitting the winner even under a deterministic function. The timestamp is a tiebreak, never a gate.

**No local reachability probe.** An implementation MUST NOT test candidate liveness during a gather. A local probe is a per-node input: every node probes from its own vantage, obtains a different pool, and elects a different winner. Liveness is already a shared fact — the rows are in one store and the pool is the converged hosts block (§13.2.1).

Candidates MUST be emitted in a deterministic order, sorted by address, so the lists themselves are byte-identical everywhere. A read failure MUST NOT collapse into an empty pool \[GATHER_NOT_SILENT_V1\].

One list is gathered: **pond-wide**, WAN-inclusive, including remote sites reached over the overlay. There is no LAN list \[ONE_ROLE_V1\]: it existed for the retired media role and cost a trace per candidate per merge.

## 3a. Election is a handler slot, not a fixed algorithm

**There is no single election function.** Election is one of three slots on the handler interface (§10.7): the codec slot asks *how do you compress*, the lifecycle slot asks *how do you announce and recover*, and the election slot asks ***are you fit to hold a role?***

The slot is two methods: one scores a single candidate, one evaluates a slate and picks. **A handler that names a role overrides them and becomes a candidate. A handler that names no role inherits empty defaults and is never a candidate**, and the election machinery does not special-case it. Role membership is one field on the class; everything follows from whether it is set.

**The scoring function is therefore per role, and roles differ deliberately.** §8.4 specifies the database host’s. It is not the general rule, and an implementation MUST NOT apply it to another role.

| Role           | Score                                                                                                                                                         | Why                                                                                                           |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `databasehost` | measured static hardware — RAM and innodb pool headroom, measured disk throughput and fsync latency, free capacity as a gate, measured cross-architecture CPU | the store is memory- and fsync-bound, so fitness genuinely differs between machines \[DBHOST_STATIC_RANK_V1\] |

**A flat score is a design choice, not an unfinished one.** Where candidates do not genuinely differ, returning a load-weighted score lets ordinary jitter outrank the tiebreak and flap the role every pass. A flat score makes the address tiebreak (§8.4.3) the sole decider, which yields one stable holder that moves only when the current holder actually leaves.

A role author choosing a score SHOULD ask whether candidates differ in a way that matters for this role. If they do not, the score SHOULD be flat. \[DBHOST_STATIC_RANK_V1\] applies to any role that does rank: every term MUST be a property of the hardware or its configuration, never anything that moves with load, or holding the role can change the score that won it.

## 4. Scoring

A role handler supplies a pure `score(candidate)` and a pure `evaluate(hosts_list)`. Both MUST be pure reads of the candidate dictionaries: no clock, no local state, no I/O.

### 4.1 Every term MUST be static \[DBHOST_STATIC_RANK_V1\]

**Nothing that moves with load may enter a score.** Every term MUST be a property of the hardware or of its configuration, so that a candidate scores identically write-to-write, every reader agrees, and **holding the role cannot change the score that won it**.

This is the section's most expensive lesson and it was learned three times over.

| Term                              | Was                                | Why it had to go                                                                                                                                                                                                                                                                                                        |
|-----------------------------------|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Load average                      | Scored \[DBHOST_NO_LOAD_V1\]       | The role is a durable assignment, not a moment-to-moment scheduler. A transient reading must not move it.                                                                                                                                                                                                               |
| Disk throughput and fsync latency | Measured, re-taken every advertise | **fsync latency is load average wearing a hardware name** — 2 ms idle, 50 ms busy, a 3.3× swing in the dominant disk term. Replaced by disk *class*, a property of the media that does not move when the box gets busy.                                                                                                 |
| CPU benchmark                     | Re-run by the advertiser           | A benchmark on a busy box measures the busy, not the box. Replaced by cores and clock.                                                                                                                                                                                                                                  |
| Free RAM                          | Available memory                   | Installed RAM, never free. A transient free-memory reading must not move a durable assignment.                                                                                                                                                                                                                          |
| Free space                        | A step multiplier at 1 GB and 5 GB | **A cliff in a continuous input is a coin toss dressed as arithmetic.** A node near either line had its entire score halved or doubled by a few megabytes of ordinary churn and flipped back on the next advertise. Free space is no longer scored or gated: it moves, and a floor on a moving input is the same cliff. |

**The failure mode is a feedback loop, not a lottery.** The elected host takes every node's writes, its fsync degrades, its score drops, it loses the role, and the new winner inherits the load and degrades in turn. Measured on 2026-08-08 from one node: seven database-host changes in one hour, with the hosts file holding a steady twenty-four names throughout. **Winning the role destroyed the qualification for it.**

### 4.2 Gates and terms

A gate returns ineligible outright. A term ranks. An implementation MUST NOT convert a gate into a multiplier, which is what produced the free-space cliff above.

``` code
GATES     the service is present AND RUNNING, not merely installed

TERMS     RAM        installed memory, plus credit for a configured
                     buffer pool — the dominant factor
          disk       media class, not this second's throughput
                     nvme 1.6  ssd 1.3  hdd 1.0  sdcard 0.4  unknown 0.9
          CPU        cores x clock
```

Where a running flag is absent because an older publisher never sent one, an implementation MAY fall back to a presence flag rather than over-reject. Free capacity MUST NOT rank: a machine with 40 GB free is not a better database host than one with 20.

### 4.3 Selection and tiebreak

Score every candidate, drop the ineligible, take the best. **Here the highest address breaks a tie between equal scores and nothing else.** It is not a selection criterion for an elected role — contrast §8.1a, where it deterministically selects the control host and no scoring occurs at all.

\[EVAL_ISOLATE_V1\] A malformed candidate blob MUST NOT abort the role's election. The offending candidate is dropped and the rest are scored, so the legitimate winner is still elected pond-wide rather than the whole role falling through to a fallback.

## 5. Planes

| Role          | Elected over                      | Because                                                                                                                                            |
|---------------|-----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| Database host | The pond-wide, WAN-inclusive list | The store is the pond's authority, so the best machine wins pond-wide including remote sites across the overlay — not merely the best on this LAN. |

Control state for a role lives in a separate plane from the data plane, so the role can move without the data plane renaming anything (§9.5).

## 6. Non-goals

No vote is exchanged, no ballot is transmitted, and no announcement is made — determinism over shared inputs replaces coordination. There is no quorum and no consensus round.

**Architecture-model note.** FrogNet election is not consensus. Nodes do not vote, negotiate, form a quorum, or agree through a protocol. Each node publishes the facts only it can know, every observer applies the same deterministic scoring function to the available facts, and the same winner therefore emerges independently. **If nodes must communicate their preferred winner or wait for agreement before selecting one, the implementation has replaced FrogNet election with a conventional distributed election protocol.**

A role is elected because exactly one machine must hold a store; it is not a coordinator, and nothing else in FrogNet elects a leader to arbitrate between writers (§10.3b).

**This is not a consensus protocol, and it should not be read against one.** Paxos (Lamport, “The Part-Time Parliament”, *ACM TOPLAS* 16(2), 1998) and Raft (Ongaro and Ousterhout, “In Search of an Understandable Consensus Algorithm”, USENIX ATC, 2014) solve a harder problem: agreement among processes that hold *different* inputs and must converge on one value by exchanging messages. That machinery — ballots, terms, quorums, leader leases — exists to reconcile disagreement about the input.

Here every candidate scores the **same rows** with the **same pure function** (§8.4), so there is no disagreement about the input to reconcile. Nodes do not agree; they compute. The cost of that is stated rather than hidden: **the guarantee is only as good as the shared inputs.** Where nodes see different rows — a partition, a stale hosts file (§8.4.2) — they will reach different answers, and the specification treats that as the ordinary case rather than a fault (§6.4). A consensus protocol would prevent divergence at the cost of a round trip and a quorum; this one accepts divergence and re-derives on merge.

There is no incumbency bonus in the score; stability comes from the terms being static, not from favouring the holder. Nothing notifies a node that the role moved: each detects it locally and reconciles (§6.5, §6.6).

## 7. References

Specification [§2d](spec-02-invariants.md#b5), [§3.6](spec-03-node.md#n6), [§6](spec-06-pond.md), [§9.5](spec-09-memory.md#m5), [§10.3](spec-10-unrest.md#u3), [§13](spec-13-failure.md). Source: `core/frognet_role_elect.py`, `core/database_handler.py`, `core/frognet_tuples.py`. Build manual: Croakus [§15](https://fawcettinnovations.com/magnum-croakus.html#15-selecting-the-service-hosts), [§16](https://fawcettinnovations.com/magnum-croakus.html#16-one-name-a-moving-target), [§17](https://fawcettinnovations.com/magnum-croakus.html#17-two-names-two-planes), Appendix A (The Capability Probe).

[← §7 Internet Extension: the Region](README.md#s7) [Index](README.md) [§9 FrogNet Memory →](spec-09-memory.md)
