<!-- Generated from the site page spec-01-scope.html (https://fawcettinnovations.com/spec-01-scope.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §1

# Scope and terminology.

The first section of the [FrogNet specification](README.md). The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT RECOMMENDED, MAY and OPTIONAL in this document are to be interpreted as described in BCP 14 \[RFC2119\] \[RFC8174\] when, and only when, they appear in all capitals.

## Current implementation clauses: October 12, 2026

## Network Shared RAM

An addressable shared-state surface. Participants publish their own truth and read the state they depend on. The contract does not inherently promise persistence, replication or continuity after a memory-world change.

## Ribbit and semantic transport

Ribbit exposes memory operations to applications. Semantic transport reduces repeated representation below the application. Do not use UnREST as a synonym for every current protocol or infer semantic compression in the Kotlin RAW member.

## Lillypad, pond and DIF

A lillypad is a local network context. A pond is a scoped membership context, corresponding to the DIF idea. A lillypad may participate in several ponds; recursive scope is the architectural model.

## Implemented scope

r19-a15 (October 12, 2026; now CroakOS fdf-2026-11-15-r54) includes region-driven nodes and device bridges, one forward pond and reach into other permitted ponds. Arbitrary recursive-depth qualification is not established by those tests.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Scope](#a1)
2.  [1aThe thesis, and what it maps onto](#a1a)
3.  [2Conformance](#a2)
4.  [3Terminology](#a3)
5.  [4The model this specification assumes](#a4)
6.  [5Document conventions](#a5)
7.  [6References](#a6)

## 1. Scope

This specification defines the FrogNet Living Network: a self-forming, self-healing mesh network that replaces the HTTP substrate with a stateful system optimised for network shared memory. It states what a conforming implementation MUST do. It does not argue for the design; the reasoning lives in the build manual, and each section names the chapter it derives from.

The specification covers five separable bodies of work, in dependency order: network formation (§4, §6), overlay and span (§5, §7), role election (§8), a stateful substrate in place of HTTP (§10, §11), and network shared memory (§9). The fifth is the purpose of the other four. An implementation MAY conform to a subset, and MUST state which sections it claims.

## 1a. The thesis, and what it maps onto

**Stop building a distributed system out of computers. Build a computer out of a distributed system.**

The conventional model is independent computers plus the machinery that makes them behave collectively: endpoints, protocols, synchronisation, retries, coordination. This specification describes the inversion — **the distributed collection is the machine**, and a program uses the abstractions that machine supplies rather than programming the relationships between its parts.

Read literally, that gives every component in this document a counterpart in ordinary computer architecture. The correspondence is not decoration; it is why the rest of the specification takes the shape it does.

| In a machine                            | In a FrogNet                                        | Specified in |
|-----------------------------------------|-----------------------------------------------------|--------------|
| Core                                    | Node                                                | §3           |
| Shared memory                           | FrogNet Memory — one store, one serialization point | §9           |
| Memory controller                       | The elected database host                           | §8           |
| Interconnect                            | The mesh and its bearers                            | §5, §7       |
| Interconnect addressing                 | Routing                                             | §5           |
| Hardware enumeration at boot            | Discovery                                           | §4           |
| Hardware added, removed or rearranged   | Merge, split, heal                                  | §6           |
| Which core takes which duty             | Capability election                                 | §8           |
| Losing a core and carrying on           | Re-election and re-derivation                       | §8, §13      |
| Traffic the interconnect need not carry | SAME/DIFF on the wire                               | §11          |

**The correspondence extends to how the machine is measured.** A conventional computer is asked how fast its processor executes instructions. A FrogNet is not usefully asked that: its nodes already execute independently and concurrently, and adding one adds computation rather than adding a client. What the parts share is memory, so the machine-level question is **how many shared-memory operations the machine services per second** — not how fast any node runs (§14.1a).

That reframes what a large elected host is. It is not a big server the others depend on; it is a more capable memory subsystem fitted to the machine (§17.3). And it reframes the transport mechanisms: persistent sockets, SAME/DIFF, semantic compression and duplicate-work collapse are not networking optimisations bolted on, they are what keeps the memory subsystem from being the part that limits the machine.

The correspondence is a reading aid, not a conformance claim. It breaks in the places this specification names — there is no cache coherence protocol because there is nothing to make coherent (§2a), no notification when a value changes (§17.2), and the interconnect can partition, which a backplane cannot (§6). **Where the analogy and a normative statement disagree, the normative statement governs.**

## 2. Conformance

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT RECOMMENDED, MAY and OPTIONAL in this document are to be interpreted as described in BCP 14 \[RFC2119\] \[RFC8174\] when, and only when, they appear in all capitals.

These key words are normative only where they appear in capitals. Where this document describes behaviour without them, it is describing the reference implementation, not constraining a conforming one.

A claim of conformance MUST name the sections claimed and MUST be accompanied by evidence produced from the implementation's own output, not from description. §16 states the evidentiary standard: a component counts as exercised only where its registered log marker appears in the run's own log, and every oracle MUST be paired with a control that forces it to fail.

## 3. Terminology

Croakus: [§1](https://fawcettinnovations.com/magnum-croakus.html#1-the-idea-in-one-page), [§1a](https://fawcettinnovations.com/magnum-croakus.html#1a-the-mental-model)

| Term             | Definition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Defined in                       |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------|
| `node`           | A host running the FrogNet stack. A node is a whole network alone; membership widens what it can see and changes nothing about how it is programmed.                                                                                                                                                                                                                                                                                                                                                                                                            | [§3](README.md#s3)               |
| `pond`           | A set of nodes sharing one membership, one topology view, and one authoritative FrogNet Memory store. To a node it is not declared: it is the current set of nodes that can reach each other, recomputed whenever reality changes. The region keeps a separate administrative record under a different word — the invitation (`fdf-invite NAME`, one per admitted member, `/opt/fdf/hub/admitted/NAME.json`) — which answers who may join rather than who is reachable (§6.1, §7.3). There is no member limit, group token or pond password.                    | [§6](spec-06-pond.md)            |
| `control host`   | The node holding the coordination plane — capability, presence, the services table. Selected deterministically as the highest node address, not elected. Distinct from the database host.                                                                                                                                                                                                                                                                                                                                                                       | [§8.1a](spec-08-election.md#e1a) |
| `chorus`         | A synthetic infrastructure address plane. **Not a tuple scope** and not walked as a discovery target.                                                                                                                                                                                                                                                                                                                                                                                                                                                           | [§4.2](spec-04-discovery.md#d2)  |
| `scope`          | The third tuple coordinate, qualifying a variable name. Constructed, not free-form: host, node, role or session (§9.2).                                                                                                                                                                                                                                                                                                                                                                                                                                         | [§9.2](spec-09-memory.md#m2)     |
| `region`         | The inter-site mechanism since 12 October 2026: one region host running the RAM manager (`rina-host`, TCP 51888 at 10.253.253.254, inside the hub’s tunnel only) and the hub (`fdf-hub`, UDP 50889, `wg-<AREA>`). The host holds the site key and every member’s public key and edge address; it runs none of the semantic stack, forwards in the kernel, and is not a FrogNet node. A member is alive while its session to the RAM manager is up (§7.1). Its predecessor, the broker (a rendezvous that kept a registration row per node), was removed (§7.8). | [§7](README.md#s7)               |
| `database host`  | The elected node holding the authoritative store, reachable as `databasehost.frognet`. Elected on capability, not by address.                                                                                                                                                                                                                                                                                                                                                                                                                                   | [§8](README.md#s8)               |
| `SotF`           | **Song of the Frogs Adaptive Communications Protocol.** The former media plane of the Communicator (its handler left the tree on 12 October 2026; the Communicator's media now rides fnav to comms-media): a nine-rung ladder that trades fidelity for continuity as the link tightens. Distinct from FNWP-1 and BLDC-1, which carry the control plane beneath it.                                                                                                                                                                                              | [§15](README.md#s15)             |
| `media relay`    | SotF's relay: forwards one media stream and sets each viewer's rung from that viewer's own link. **Distinct from the region hub**, which is the WireGuard rendezvous; the two MUST NOT be conflated.                                                                                                                                                                                                                                                                                                                                                            | [§15](README.md#s15)             |
| `tuple`          | A value addressed by three caller-chosen coordinates. The store imposes no schema; a key exists when it is first written.                                                                                                                                                                                                                                                                                                                                                                                                                                       | [§9.2](spec-09-memory.md#m2)     |
| `FrogNet Memory` | The network-wide addressable store. One store per connected pond, backed by a database. It **is not** a database.                                                                                                                                                                                                                                                                                                                                                                                                                                               | [§9](spec-09-memory.md)          |
| `UnREST`         | The memory-first application programming model over FrogNet Memory.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | [§10](spec-10-unrest.md)         |
| `handler`        | An object conforming to one interface with a small number of virtual slots, through which every exchange is processed. A handler naming a `ROLE_NAME` participates in election; one naming none is a content handler.                                                                                                                                                                                                                                                                                                                                           | [§10.7](spec-10-unrest.md#u7)    |
| `FNWP-1`         | **FrogNet Wire Protocol.** The binary framing: length-prefixed, opcode- and sequence-tagged frames on an established socket. The wire constant is the ASCII literal `FNW1`; the protocol name is FNWP-1 and the two MUST NOT be confused.                                                                                                                                                                                                                                                                                                                       | [§11](README.md#s11)             |
| `BLDC-1`         | **Bullfrog Long Distance Communications Protocol.** Semantic compression by far-end re-execution and comparison. Not a cache: it re-executes against the origin and reports identity, and never answers from a stored copy.                                                                                                                                                                                                                                                                                                                                     | [§11](README.md#s11)             |
| `transport`      | Any bearer capable of carrying FNWP-1 frames. TCP is the reference bearer.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | [§11](README.md#s11)             |
| `hostReset`      | A verb (no arguments in Python; the C++ handler's takes the memories it writes to) any object in any process MAY answer to, meaning re-state what the object is into wherever the coordination plane now lives. Not confined to handlers and not a class hierarchy.                                                                                                                                                                                                                                                                                             | [§6.6](spec-06-pond.md#p6)       |

## 4. The model this specification assumes

Croakus: [§1a](https://fawcettinnovations.com/magnum-croakus.html#1a-the-mental-model)

There is no client and no server. A program does not fetch a value from somewhere; it reads a value that is present, and writes values that then are. The question a distributed program normally asks — which service do I call, and what if it does not answer — is replaced by what is the current value, which has no failure mode of its own.

**Programming-model note.** FrogNet applications should not normally recreate client/server relationships above Memory. A producer publishes what it knows without identifying a consumer; a consumer reads the state it needs without identifying a producer. **If an application is primarily constructing requests, replies, subscriptions, acknowledgements, retries, or recipient lists on top of tuples, it has recreated the programming model FrogNet Memory is intended to replace** (§10.3b).

Conceptually the surface is:

``` code
/* the shape of the surface, not its signatures.
   The implemented operations are put / get / get_all — see §10.3 */

  memset(tuple, value)      write what you know
  value = memget(tuple)     read what is currently true
  memwatch(tuple)           be told when it changes     NOT A FABRIC OPERATION
```

The third is listed so that no reader goes looking for the call, and named that way deliberately: it is **not an unfinished FrogNet operation**. Observing change is application policy and does not belong in the fabric (§10.3a) — a substrate that knew which changes mattered to which program would have those programs’ semantics inside it. There is **no notification when a value changes**, anywhere in FrogNet (§9.4, §17.2); how a program observes change is its own choice (§10.3a). A callback on *role reassignment* is a different mechanism, belongs to the UnREST service definition, and does exist (§10.7). Topology change is separate and does propagate, as a notification that something changed and never as a value (§4a.5, §11.13). The implemented calls are `put()`, `get()` and `get_all()` (§10.3).

**FrogNet Memory defines the distributed memory model; applications define behaviour.** Where a value lives, how it travels, and what happens when two writers arrive at once belong to the memory model once, rather than to every application in turn. What a value means belongs to the application. That division is normative: an implementation MUST NOT require an application to supply any part of the first, and MUST NOT interpret any part of the second.

## 5. Document conventions

Each section names its derivation in the build manual on a cross-reference line beneath its heading. Where a normative statement rests on a value in source, that value is cited with its file. Where a section is not yet complete, an explicit PENDING SOURCE block states what is missing rather than supplying a plausible figure; an unmarked absence is a defect in this document, not a permitted implementation choice.

Two named errors recur in readings of this specification and are corrected here once. First, **nothing is copied**: there are no replicas, no deltas, no anti-entropy, and no merge (§2). Second, **gossip carries topology, never memory**: a gossip wave carries a change notification, never a value (§4).

## 6. References

Normative: all sections of this specification. Informative: the build manual ([Magnum Croakus](https://fawcettinnovations.com/magnum-croakus.html)), [Internals](https://fawcettinnovations.com/internals.html), [UnREST by example](https://fawcettinnovations.com/unrest-examples.html), and [the simulator](https://fawcettinnovations.com/simulator.html). Lineage: Gelernter and Carriero, Linda tuple spaces, Yale, 1985; the three-coordinate key is FrogNet's indexing convention, not Linda's.

[Index](README.md) [Index](README.md) [§2 Architectural Invariants →](spec-02-invariants.md)
