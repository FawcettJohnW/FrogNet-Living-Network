<!-- Generated from the site page technicalhome.html (https://fawcettinnovations.com/technicalhome.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

**Engineering specifications, source references and implementation details.** This is the skilled route. For what CroakOS does and why it matters, start at [the home page](https://fawcettinnovations.com/index.html); for the whole machine in plain terms, [How CroakOS works](https://fawcettinnovations.com/croakos.html).

Specification

# FrogNet Protocol Specification

Draft 0.9, revision 2026-08-31. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Prerequisites

### Technical prerequisites

This specification assumes the reader has an advanced working knowledge of the architecture and use of the following technologies:

FrogNet deliberately uses standard APIs, protocols, operating-system facilities, and tools wherever practical. As a result, its dependencies and relationships cross multiple engineering layers and are dispersed throughout the system.

Required

- Linux
- HTTPS proxies
- Distributed systems
- Shared memory
- Semantic compression
- Custom wire protocols
- Python

Recommended

- Conventional networking and routing
- TCP/IP
- DNS and DHCP
- WireGuard
- REST and HTTP
- Relational databases and SQL
- systemd
- Shell scripting
- Radio networking
- FFmpeg
- Real-time audio/video systems

It is strongly suggested that you load the FrogNet codebase into an AI capable of repository-wide analysis. This makes it considerably easier to trace those dependencies and relationships across the complete system.

Featured

## Two conversions, measured over the Internet.

compute

### [Psychedelic FrogTorch](https://fawcettinnovations.com/psychedelic-frogtorch.html)

329×

PyTorch distributed: parity with gloo, then meshfeed, where the data comes to the compute. Batch 64 and batch 4096 on a five-node Internet mesh, same held-out loss.

[The case study](https://fawcettinnovations.com/psychedelic-frogtorch.html)

protocols

### [Ribbit-LISP](https://fawcettinnovations.com/ribbit-lisp.html)

17×

lispers.net’s map-server and map-resolver, the front deciding each Map-Register. 0 of 78 tests failed against 14; dated runs from September and November.

[The case study](https://fawcettinnovations.com/ribbit-lisp.html)

Also: [CroakOS, the whole machine](https://fawcettinnovations.com/croakos.html) · [the simulator](https://fawcettinnovations.com/simulator.html) · [the conversion method](https://fawcettinnovations.com/converting.html) · [every system](https://fawcettinnovations.com/conversions.html)

## The specification keeps its full mechanism record.

All twenty detailed specification documents, author walkthroughs, clause ids and citations remain available below. Each now includes current implementation clauses for r19-a15 (October 12, 2026; the CroakOS build now is fdf-2026-11-15-r54) and the modern Ribbit surface, while retaining the full existing technical explanation.

[FD&F ownership and graph](https://fawcettinnovations.com/fdf.html) · [Ribbit programming discipline](https://fawcettinnovations.com/programming.html) · [Exact implementation baseline](https://fawcettinnovations.com/implementation.html) · [Current acceptance gates](https://fawcettinnovations.com/evidence.html#fdf-evidence)

FrogNet Specification — Draft 0.9, revision 2026-08-31. Cite a conformance claim against this revision, not against a section number alone.

**Ribbit (§19–§22).** The programming surface that §9 and §10 lead to is documented in Ribbit's own words: the model and the method, the programming rules, the conversion process and API++. Those four are rendered from the documents in the source tree and are descriptive, not RFC 2119 clauses.

**Read in order or by section.** Sections §4a and §11a are numbered on the half convention: they specify components that had no section of their own, and no existing section number changes because of them.

|                      |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Documents            | 20 normative (§1–§18), plus the four Ribbit reference documents §19–§22, rendered from the source tree and descriptive rather than normative                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Normative statements | 476 MUST (of them 210 MUST NOT)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Provenance           | Every requirement checked against the reference implementation; each constant cited with its source file                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Unfinished           | Four source-verification gaps remain, each marked `PENDING SOURCE` at the point it applies: discovery cadence and withdrawal in §6, the reassignment-callback registration API in §10, the template disuse interval in §11, and association retry policy in §13. None is filled with a plausible figure. Allocator exhaustion, previously listed here, is closed: the behaviour was stated in §17.4 from the broker’s allocator source; the broker was removed on 12 October 2026 (§7.8), and the region gives one edge address per member (`fdf-invite` `free_edge`, 64,008 per region). |
| Status               | Draft 0.9, revision 2026-08-31. Experimental; no registry assignment is claimed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Licence              | The implementation is open source under GPLv2. The specification is published so an implementation can be written against the contract rather than against the code.                                                                                                                                                                                                                                                                                                                                                                                                                      |

[](spec-01-scope.md)

§1

Scope and TerminologyWhat this specification covers, how conformance is claimed, the normative terminology, and the model the rest of it assumes. [](spec-02-invariants.md)

§2

Architectural InvariantsFive statements that hold across every other section: one authoritative copy, one serialization point, no replication or merge, topology derived rather than distributed, and independent producer and consumer lifetimes. [](spec-03-node.md)

§3

Node Model, Identity, Installation and BootstrapWhat a node is, its two unrelated identities, the GUID lifecycle, gateway and child roles, and the requirements an installer must meet. [](spec-04-discovery.md)

§4

Discovery and MembershipThe bounded walk, per-path deduplication, the negative cache and what may never mark it, the liveness gate, and what a walk outputs. **Depends on §11a**: discovery's liveness probe is the daemon's `:9009` exchange and its path-ranking round trip rides the proxy; its echo and getHosts go direct. [](spec-04a-triggering.md)

§4a

Triggering and the Merge ControllerWhat causes a merge to run: link and lease events, the single controller, re-entrancy, the converge loop, and how the mesh announces to itself. [](spec-05-routing.md)

§5

Addressing and RoutingObservations as evidence, metric roles, pure planning, fallback routes, commit and mutation flagging, and why tunnel existence is not tunnel usage. [](spec-06-pond.md)

§6

Pond Formation, Merge, Split and HealingFormation and splitting as one mechanism, local detection, re-establishment by re-assertion, and the definition of convergence. [](spec-07-broker.md)

§7

Internet Extension: the RegionWhat the region is (RAM manager, hub, one host), where it lives, admission by invitation, the hub derived from the records, loss of the region, tunnel liveness, trust, and what the broker was. [](spec-08-election.md)

§8

Service Discovery and ElectionTwo hosts and only one elected, capability publication, the candidate pool, static scoring, and why nothing that moves with load may select a role. [](spec-09-memory.md)

§9

FrogNet MemoryThe store: addressing, write and read semantics, who decides freshness, coherence and the database-host election, partition behaviour, and non-goals. [](spec-10-unrest.md)

§10

UnREST — the Programming ModelThe exchange before and after, the three operations, coupling and lifetime, what the application no longer writes, ownership rules, handlers and concurrency. [](spec-11-wire.md)

§11

FNWP-1 and BLDC-1Frame format and opcodes, template learning, the three reference states, how SAME is proven, sequencing, duplicate-work collapse and gossip bounds. [](spec-11a-proxy-daemon.md)

§11a

The Proxy and the DaemonInterception, socket sets and the cost of overlay, the daemon execution gate, worker pool sizing, and the two caches with their different standing. **Prerequisite of §4**: discovery's liveness probe is the daemon's `:9009` exchange, and its path-ranking round trip rides the proxy (§4.1b). [](spec-12-security.md)

§12

Security Architecture and Trust BoundariesNo cryptography of its own, the absent surface, the reference-only wire and its honest boundary, encryption as a bearer choice, and remaining exposure. [](spec-13-failure.md)

§13

Failure Semantics and RecoveryFailure classes and required responses, what does not expire, writes during a partition, and what is not provided. [](spec-14-performance.md)

§14

Performance and AdaptationSemantic versus media adaptation, the nine-rung ladder, pure rate derivation, why a failed rate is not a candidate, and what may be claimed for a figure. [](spec-15-applications.md)

§15

Reference ApplicationsThe Communicator as the reference application, the Monitor as the consumer half of §10, governed shared state in the games, and what a reference application is not. [](spec-16-conformance.md)

§16

Simulator and Conformance TestingSubstitution points, the tier ladder, coverage proven from output, isolation requirements, hardware correspondence, and what is not modelled. [](spec-17-limits.md)

§17

Known Limits and Unresolved IssuesEvery known limit with its mechanism, consequence and status — structural limits distinguished from open work. [](spec-18-implementation.md)

§18

Implementation and Security ConsiderationsReference platform, interchangeable bearers, registries and protocol naming, regulatory posture, falsification challenges, doctrine tags and deployment cautions. [](spec-19-ribbit.md)

§19

Ribbit: the model and the methodRibbit's own account: the shared-memory island, how to code like a frog, how the wire is optimised, debugging, building and deploying, and using AI for maximum code velocity. [](spec-20-ribbit-programming.md)

§20

Programming Ribbit: best practicesWhat is yours to change, addressing, held views, one writer per truth, derived state, coordination as state, participants, the incumbent's tests as oracle, honest measurement, and what first implementers get wrong. [](spec-21-converting.md)

§21

Converting a system to RibbitThe reference conversion process: the contract kept and the machinery replaced, phase 1 and phase 2, the truth table, the question-3 register, what is reported, and how it goes wrong. [](spec-22-api-plus-plus.md)

§22

API++: convenience functions over the shared RAMPublish and subscribe without a broker, cells gone with their writer, linger by family, held reads that watch and follow, Redis and MQTT on API++.

## Clarifications

**Nothing is copied.** A connected pond holds one authoritative copy of each value at one place. There are no replicas, no deltas, no anti-entropy, and no merge procedure — on reunion after a partition nothing is merged and no value travels (§2, §6, §13).

**Gossip carries topology, never memory.** A change notification propagates between directly attached neighbours; a tuple value never does, because there is only one copy of it. (§4a, §11).

## Companion material

[Internals](https://fawcettinnovations.com/internals.html) gives mechanism notes alongside the normative text. [UnREST by example](https://fawcettinnovations.com/unrest-examples.html) is worked code. [The simulator](https://fawcettinnovations.com/simulator.html) and [the demonstrations](https://fawcettinnovations.com/demonstrations.html) carry the evidence. [Magnum Croakus](https://fawcettinnovations.com/magnum-croakus.html) is the build manual and the derivation for every section here.

**This specification is normative. The reference implementation is one implementation expected to conform to it** (§18.8). Where the two disagree, either this document has a defect — corrected by erratum and revision — or the implementation departs from what this document requires. Source is the arbiter of what the reference implementation *does*; it is not the arbiter of what a conforming implementation *must* do.
