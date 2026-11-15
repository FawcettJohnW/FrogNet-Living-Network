<!-- Generated from the site page spec-17-limits.html (https://fawcettinnovations.com/spec-17-limits.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §17

# Known limits.

Limits of the current design and implementation, not open questions about the invariants. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

## Current implementation clauses: October 12, 2026

## Capacity

The roughly 58-node arithmetic belonged to the broker-era tunnel allocator, removed 12 October 2026. The region allocates one edge address per member, 64,008 per region (`fdf-invite`); that is an allocator figure, not a measured region-size ceiling. The reviewed FD&F records do not establish a maximum region population.

## Packet aliases

Aliases translate IPv4 packet addresses and checksums, including quoted ICMP packets. Application payload addresses need their own handling. IPv6 is not established by these IPv4 tests.

## Scopes and policy

Several overlapping ponds are qualified in namespaces. Arbitrary recursive depth and automatic transitive authorization are not established. Resolution refuses when several ponds answer.

## Recovery and installation

Memory replacement is not durable replication. Fresh region installs join by invitation (`fdf-invite`, `fdf-region-join`) with no broker or key dependency. Python reset integration, some monitor presentation and hardware failover timing remain open.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; where they describe the broker path, that path was removed in the October 2026 cleanup (docs/cleanup-2026-10/).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Standing](#k1)
2.  [2Observation latency is the reader’s](#k2)
3.  [3One serialization point is a write ceiling](#k3)
4.  [4Node ceiling: one edge address per member, 64,008 per region](#k4)
5.  [5Memory model is one rule, not six](#k5)
6.  [6SAME/DIFF/FULL thresholds are judgement](#k6)
7.  [7Body-mode detection is heuristic](#k7)
8.  [8Receive-side audio priority](#k8)
9.  [9The relay cannot name whose frame it discarded](#k9)
10. [10One stream, one rate](#k10)
11. [11Simulator exclusions](#k11)
12. [12References](#k12)

## 1. Standing

Croakus: [§52](https://fawcettinnovations.com/magnum-croakus.html#52-the-communicator-is-a-hello-world), [§53](https://fawcettinnovations.com/magnum-croakus.html#53-per-client-rate-adaptation), [§54](https://fawcettinnovations.com/magnum-croakus.html#54-smaller-things-named-honestly)

These are limits of the current design and implementation. They are **not** open questions about whether the invariants in §2 hold. None is hidden behind a fallback: each fails visibly and each carries a status — **structural**, **implementation limitation**, or **unresolved**. A structural limit follows from an invariant in §2 and cannot be removed without removing the guarantee that produces it. An implementation limitation is a property of this build. An unresolved item is one the specification does not yet determine.

An implementation claiming conformance MUST reproduce these limits or state explicitly which it has removed and how, since several of them are consequences of the invariants rather than defects to be repaired.

## 2. Observation latency is the reader's, and is bounded by its poll interval

**This is not the absence of a fabric feature.** Earlier revisions listed “no change notification” here as a missing capability. That framing was wrong and is withdrawn: how an application observes change is application policy, and a fabric that knew which changes mattered to which application would have application semantics coupled back into it — the coupling this architecture exists to remove (§10.3a).

**Consequence.** An application that must notice a change chooses a mechanism and pays for it. Observation latency is then bounded by the interval that application chose, not by anything FrogNet does — unless it uses a held read, which waits on the cell and resumes when it changes. Polling is inexpensive here because an unchanged read collapses to a SAME frame (§11), but inexpensive is not free, and a reader that polls slowly observes late. That cost is real and it belongs in this section; the boundary that produces it does not.

## 3. One serialization point is a write ceiling

**Mechanism.** Every write in a connected pond is ordered at the elected database host (§2b).

**Consequence.** Writes are bounded by the throughput of one point and do not scale horizontally. The coherence property and the scaling limit are the same fact stated twice, so this limit cannot be removed without removing the guarantee that makes the model work. **What it does not mean is that the ceiling is fixed.** Every term in the database-host score is a static property of the hardware or its configuration \[DBHOST_STATIC_RANK_V1\], so an operator who provisions a machine built for the role — memory, storage class, CPU — gets that machine elected whenever it is visible, deterministically and without configuring anything. The bound is the capacity of the machine placed under the role, not the capacity of the weakest node that happens to be present. A pond with one large host and forty small ones is an ordinary shape, and the ceiling moves with the large host.

### 3.1 Fronting an external database is not supported today

The obvious extension is to elect a node that *fronts* a database rather than running one — a modest machine holding the role while the storage sits in a managed service behind it. The rest of the pond would be unaffected: it resolves the role by name and does not know what backs it.

**The election machinery does not permit this at present, and the obstacles are specific.**

- **The eligibility gate is local.** `mysql_running` is set by `pgrep -x mysqld` (or `mariadbd`, or a local listener on :3306) on the candidate itself. A node fronting a remote service has no local process to detect and is gated out before it is scored.
- **The score measures the wrong machine.** Every term — installed RAM, innodb pool, disk class, cores × clock — is a property of the candidate, not of the store it would reach. A small fronting node backed by a large service would lose to a large local node backed by nothing.

Neither is a deep obstacle: the connection host is already configurable (`DB_CONFIG.json`), so the data path needs nothing. What is missing is a way for a candidate to advertise the capacity of a store it reaches rather than the hardware it runs on, and \[DBHOST_STATIC_RANK_V1\] constrains the answer — whatever is advertised MUST be static, or holding the role could change the score that won it.

Recorded here as an unbuilt capability rather than a claim. **An implementation MUST NOT report an external store’s capacity through the existing capability fields**, which are specified as properties of the candidate machine (§8.4).

Contention on a single key is whatever the backing database does about contention on a key; a thousand writers fighting over one value is a design problem no substrate repairs. **In the FrogNet programming model this is a signal to stop sharing the value, not to engineer increasingly elaborate contention management.** Give each independent producer its own tuple and let consumers operate across those values with `get_all` (§2b, §10.3b).

**Reads are not bounded the same way, and not by replication.** Nothing is replicated (§2a). Reads relieve the store by three mechanisms, each of which preserves the single copy:

| Mechanism                          | Where                             | What constrains it                                                                                                                                                                         |
|------------------------------------|-----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Request collapse                   | Each sending node, per target     | N callers on one node asking the same peer the same question in the same instant cost one request among them (§11.12); the answering daemon does not wait to share across peers by default |
| Proven-current RAM materialization | Database host only                | Served only where a generation counter proves RAM equals disk on this request; fails closed \[DATA_CACHE_GEN_V2\]                                                                          |
| Local read cache                   | Every node, its own forwards only | Scoped invalidation plus a short TTL; bounded-stale within the freshness window, which the store evaluates on the clock that stamped the row (§9.4)                                        |

None of these is a second authoritative copy, and an implementation MUST NOT describe them as replication.

**Read volume is also not read cost.** The naive reading of one store serving many nodes is many connections, many requests, and a full value returned each time. Two properties of the transport make that the wrong model. Sockets between a proxy and its daemon are persistent (§11a), so a read is not a connection setup. And an unchanged answer is a twenty-one byte frame regardless of the size of the value (§11.10), so a consumer asking a question whose answer has not moved pays a constant, not a payload.

The conventional cost is consumers × frequency × response size. Here the steady state is consumers × frequency × a constant, with the payload transmitted when the answer actually changes. **A high read rate against a stable value is therefore cheap on the wire, and a rising request count does not imply a proportional rise in network load** (§14.1b).

**The claim is not that the limit disappears. It is that the limiting resource changes.** Every machine is bounded by CPU, memory, disk or network, and engineering consists of deciding which will bind first and provisioning past the workload. A conventional store serving many distributed clients is frequently network-bound, because each request carries a full result whether or not anything moved.

Four mechanisms attack that term. Persistent sockets remove connection setup (§11a). SAME removes the payload when nothing changed, and DIFF reduces it when something did (§11). Duplicate-work collapse gives N callers asking one question a single origin execution (§11.12) — which attacks computation, not only bandwidth. And genuinely independent work runs concurrently in the daemon’s worker pool (§11a.1a), so added cores become throughput rather than idle hardware. **Collapse duplicate work; run independent work in parallel.**

What remains after that is more likely to be bounded by CPU than by the wire, which is the better problem to have: cores can be added and this architecture can use them. An operator SHOULD provision the elected host against the term expected to bind, and SHOULD verify which one does rather than assuming (§14.1a).

**Status.** Structural. Not a defect.

## 4. Node ceiling: one edge address per member, 64,008 per region (the 58/16 broker ceiling is gone)

**Mechanism.** The region allocates per member, not per pair of members. `fdf-invite NAME` gives each admitted member one edge address on the hub’s tunnel, the first free `10.253.X.Y` with X 1–252 and Y 1–254 (`opt/fdf/bin/fdf-invite` `free_edge`; `10.253.253.0/24` is the hub’s). The hub carries every member as a peer of one interface, `wg-<AREA>`, routed by key (§7.4). Nothing is allocated per tunnel between members, because there are no tunnels between members: each member has one link, to the hub.

**Consequence.** 252 × 254 = **64,008** members per region before `fdf-invite` stops with `no edge address left in 10.253.0.0/16`. That is the allocator’s ceiling, read from the code; it is not a measured region size, and no test on record has run a region anywhere near it. An implementation MUST NOT state a measured maximum population for a region: none has been measured. Other ceilings that are real at scale — the hub forwarding every cross-site byte of every member through one host (§7.4, §17.3), the RAM manager holding one session per member — are stated as mechanisms, not figures.

**On exhaustion the allocator stops; it does not wrap, reuse, or degrade** (`fdf-invite` `free_edge`: `die("no edge address left in 10.253.0.0/16")`). A conforming implementation MUST fail an allocation it cannot satisfy rather than issuing an address already in use. Within a region a /24 has one owner — the first live member to claim it; a later claimant is refused and the hub logs `REFUSED <subnet> claimed by ...` (`fdf-hub`; §7.4).

**What the ceiling was.** Until 12 October 2026 the inter-site path was the broker (§7.8), which built a full mesh of pairwise tunnels and spent a carrier block on each: 54 bands × 31 tunnels = 1,674 tunnels, and n(n−1)/2 ≤ 1,674 gives n = 58 for a single-pond broker, as few as 16 where one broker carried a second pond. The lesson that section drew still stands and is why the region is shaped as it is: a larger range was never the fix, because tunnels in a full mesh grow as the square of the nodes; **allocating anything at all per tunnel was the defect**. The broker and its allocator (`frognet_broker_v4.py`, `broker/full_broker.tgz`) were removed (`frognet_prune_removed.sh:160`; `docs/cleanup-2026-10/T3b.md`).

## 5. Memory model is one rule, not six

**Mechanism.** Last write wins against the key, with a freshness window the **store** evaluates against the row's own update time (§9.4), plus process-owned tuples reaped by the periodic sweep after their writer stops. That is the whole of it.

**Consequence.** Handable authority, versioning, leases, transactions, optimistic updates and single-writer regions are not provided. An application needing one today has to build it — which is exactly the class of code this model exists to stop people writing.

**Status.** Open for FrogNet's default Region. Later Ribbit regions implement stronger domain rules — truth ownership, governed acceptance, held views, expiration, durable records (Croakus §61).

## 6. SAME/DIFF/FULL thresholds are judgement

**Mechanism.** The thresholds at which the codec selects between full, differential and identity replies are set by judgement rather than derived from measurement.

**Consequence.** There exist payload shapes for which a different threshold would send fewer bytes. No correctness property depends on the choice; only efficiency does.

## 7. Body-mode detection is heuristic

**Mechanism.** The codec classifies a body by inspecting it, never by its declared content type (§10.7). Five content modes are registered — `json`, `xml`, `html`, `text`, `raw`.

**Consequence.** Misclassification to `raw` forfeits compression for that class: a raw body learns no template and every send of it crosses FULL (§11.7). It does not produce a wrong answer — a raw body is transmitted whole and correctly — so the failure is a cost, not a fault. An empty or undecodable body classifies `raw` by construction.

## 8. Receive-side audio priority

**Mechanism.** Outbound, audio takes precedence over video on the shared wire: a voice packet never waits behind a keyframe. Inbound, frames are handled in arrival order.

**Consequence.** The rule that holds in one direction does not hold in the other. It is one rule applied in one more place.

**Status.** Open. Bounded, with an obvious oracle; a good first contribution.

## 9. The relay cannot name whose frame it discarded

**Mechanism.** The media server reports what it shed per viewer (its MediaHold row), not per sender.

**Consequence.** Reduced since the aggregate report stopped moving the rate: the rate now derives from what participants publish about themselves, and a viewer reports its own condition directly. What survives is that the relay still cannot attribute a discard.

**Status.** Partially closed, 11 August 2026. Per-sender attribution is information the relay already holds and does not use.

## 10. One stream, one rate

**Mechanism (was).** The Python Communicator’s media relay forwarded one stream and bounded the call rate to the slowest participant.

**Status: closed in the C++ Communicator.** The media server passes the upload through to viewers whose target is the upload’s size and transcodes once per smaller size wanted, so each viewer gets its own picture at its own rate (`ribbit/examples/communicator/src/media/server.cpp` `fan_video`, `tx_worker`; `STATUS.md` test_room: one viewer at 160x120 while another sees 1280x720). The Python relay is retired (`frognet_prune_removed.sh`, \[T2 2026-10-12\]). See §14.5.

## 11. Simulator exclusions

What the simulator does not model, stated as of the closed feedback loop rather than as of first release. A conforming test suite MUST NOT claim anything in the first column.

| Not modelled                           | Because                                                                                                                                                                                                                                                                                                                                                                                                            |
|----------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Concurrency                            | Merges are serialized. A race that requires two merges in flight cannot be produced.                                                                                                                                                                                                                                                                                                                               |
| Packet loss and jitter *on a topology* | The wire delivers between topology nodes. Per-edge latency **is** modelled and is calibrated from measurement (§17.11.1). Loss, jitter, bandwidth and outage are modelled in the transport tier and the Python media ladder was driven over them before its tier was removed — but a topology edge cannot yet carry those parameters, so a degraded bearer cannot be attached to a named leg of a multi-site pond. |

### 11.1 Correspondence with hardware, and what it covers

An earlier statement of this section said that no simulated result had been confirmed by a real deployment. **That is no longer accurate** and MUST NOT be repeated. A closed feedback loop exists in both directions. The three mechanisms are specified in §16.5 and are not restated here; what belongs in this section is their status.

| Direction (§16.5)             | Status                                                                                       |
|-------------------------------|----------------------------------------------------------------------------------------------|
| Hardware blesses the model    | **20 of 20 topologies** recorded `source: hardware`; the offline engine reproduces every one |
| Hardware calibrates the model | In use; guessed edge timings replaced by measurement                                         |
| Hardware faults become tests  | In use                                                                                       |

Provenance MUST be recorded per baseline, because it decides severity: a mismatch against a `hardware` baseline is a regression — the offline code no longer reproduces blessed kernel truth — while a mismatch against an `offline` baseline is drift since the last blessing and may be intended.

**Correspondence covers committed route tables and reachability, gated against hardware truth.** **Discovery convergence cycle counts are recorded but not gated**, because timing varies; an implementation MUST NOT treat a simulated cycle count as a predicted one. As everywhere in this specification, convergence here means discovery reaching a stable derived topology and nothing else (§2c, §6.7). Nothing here establishes that a simulated *throughput* or *bandwidth* figure predicts a deployed one; those remain separate measurements of separate workloads (§14).

## 12. References

Specification [§2](spec-02-invariants.md), [§9.8](spec-09-memory.md#m8), [§11](README.md#s11), [§14](README.md#s14), [§16](README.md#s16). Build manual: Croakus [§54](https://fawcettinnovations.com/magnum-croakus.html#54-smaller-things-named-honestly). Contributions: [Discussions](https://github.com/FawcettJohnW/FrogNet-Living-Network/discussions).

[← §16 Simulator and Conformance](README.md#s15) [Index](README.md) [§18 Implementation Considerations →](README.md#s18)
