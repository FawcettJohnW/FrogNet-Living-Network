<!-- Generated from the site page spec-04-discovery.html (https://fawcettinnovations.com/spec-04-discovery.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §4

# Discovery and membership.

Membership is derived by observation, never accepted on assertion. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

How a node finds neighbours and installs routes

First boot: routes appear, and the node is on the network

## Current implementation clauses: October 12, 2026

## Fact ownership

fdf-facts observes the node; fdf-client announces its facts and signed mappings. A member session owns its announcement and mappings. They leave when its session ends.

## Watch before read

Register event watches before the first world read. The world operation supplies participants and mappings in one response. This removes the startup gap that earlier builds missed.

## Aliveness

r19 uses the session as member aliveness. A lease pair supplies adjacency evidence. The graph applier sends no admission echo for live region members; non-member gateways and handshake health have different checks.

## Local discovery remains

LAN-only discovery uses the existing local discovery/merge machinery. Its recursive crawl and negative-cache behavior should not be described as the region membership algorithm.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Model](#d1)
2.  [1aArchitecture-model note](#d1a)
3.  [1bTransport dependency and bootstrap order](#d1b)
4.  [2What is not walked](#d2)
5.  [3Deduplication is per path, not per destination \[HOP_VIA_V1\]](#d3)
6.  [4The negative cache](#d4)
7.  [5Proving a link](#d5)
8.  [6Output](#d6)
9.  [7References](#d7)

## 1. Model

Croakus: [§7](https://fawcettinnovations.com/magnum-croakus.html#7-how-a-lan-forms-and-splits), [§13](https://fawcettinnovations.com/magnum-croakus.html#13-we-eat-our-own-cooking), [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk)

Each node derives membership by observation. No node accepts a topology asserted by another, and no controller distributes one (§2d). What a walk produces is evidence for §5 to plan from and names for the node's own hosts file; what fires a walk is §4a.

The walk is bounded by depth. `MAX_DEPTH = 2` (`discovery/discovery.py`:58): a node descends at most two levels from its own segment, so the cost of a walk is bounded by topology rather than by the size of the mesh.

## 1a. Architecture-model note

FrogNet discovery is deliberately **observational rather than authoritative**. There is no discovery server that owns the membership list, and no central service from which a node obtains the network’s current topology. Each node independently discovers what it can reach, exchanges its observations with other nodes, and derives its own view of the network from the resulting information.

This distinction matters. Applying a traditional service-discovery model — introducing a central registry, or treating one node’s membership view as authoritative — would reintroduce a dependency the discovery system is specifically designed not to require. **The network discovers itself from the observations of its members.**

**This is the same move §2b makes one layer up.** Discovery declines to coordinate nodes around an authoritative description of the network; Memory declines to coordinate producers around a shared mutable answer. In both cases participants publish what they independently know and the answer is derived from the accumulated state rather than negotiated in advance. The principle is one sentence: **publish what you know, do not coordinate what you do not have to, and derive the answer from shared state.**

## 1b. Transport dependency and bootstrap order

Discovery's liveness probe is the daemon's `:9009` exchange, and the round trip that ranks paths rides the proxy (§11a). **A proxy or daemon that is not working presents as a discovery failure.**

Two ordinary endpoints do most of the work. `echo` answers *who are you* — a node’s name, address and interface shape. `getHosts` answers *who do you know* — the hosts that node has already discovered. Both are plain HTTP to the neighbour's port 80, sent direct and uncompressed (marked to bypass the local proxy).

§11a is numbered after this section and is a prerequisite of it. The numbering follows the layered exposition, not the dependency.

### 1b.1 Bootstrap order

**The link is not a precondition of the probe. It is a product of it.**

1.  The daemon binds `0.0.0.0:9009` at boot. No link exists yet.
2.  Discovery selects a candidate address and probes it. \[PINGPONG_GATE_V1\] The probe is the `:9009` ping-pong, the same probe used for every route kind. It MUST NOT be an HTTP echo: an echo answers only on a node’s `.1` identity and therefore returns a false negative for a live on-segment node, and does not exercise the loop check.
3.  The probe opens its own socket to the peer's `:9009` and closes it after the exchange.
4.  The proxy opens its own socket to that node's `:9009` for its exchanges, which are semantically compressed (§11). Discovery's echo and getHosts do not ride it.

A walk may also be triggered on demand rather than waited for: `run_merge` forces the walk from the impatient end. Propagation is otherwise neighbour-by-neighbour, and a node three hops away may not observe a rename or re-address until the node between them has (§6.7).

\[ALIVE_9009_RETRY_V1\] A negative verdict MUST be retried before it is acted on, up to `FROGNET_ALIVE_9009_TRIES` attempts, three by default (§4.4).

## 2. What is not walked

A candidate address MUST be skipped where any of the following holds. These are not optimisations; each removes a class of false result.

| Skip                                       | Because                                               |
|--------------------------------------------|-------------------------------------------------------|
| Not a 10.x address                         | Outside the mesh's address space                      |
| The chorus on-link range                   | A synthetic infrastructure plane, not node identities |
| Any local address                          | That is this node                                     |
| The `.2` of the local subnet               | A probe alias, not an identity                        |
| An already-attempted path                  | See §4.3                                              |
| An address proven non-FrogNet this session | See §4.4                                              |

## 3. Deduplication is per path, not per destination \[HOP_VIA_V1\]

A walk MUST deduplicate on the tuple of device, destination and candidate next hop — **not on the destination alone**. The same node may be reachable more than one way: a direct one-hop seed and a relayed path learned from a peer's host list.

If deduplication keyed on the destination, whichever path was attempted first would suppress the others, so a failing or looping relay path could poison a working direct path to the same node. Each distinct next-hop attempt MUST be allowed to stand on its own evidence.

## 4. The negative cache

A host that **definitively** proves not to be a FrogNet node is remembered for sixty seconds within the session and MUST NOT be re-probed within that time; the transport marks it only after three consecutive definitive failures over at least fifteen seconds. Re-probing an ordinary LAN box every cycle burns a full probe budget and feeds a bootstrap storm.

### 4.1 What may mark, and what MUST NOT

| Signal                               | Marks?                        | Because                                                                                                        |
|--------------------------------------|-------------------------------|----------------------------------------------------------------------------------------------------------------|
| Connection refused                   | **Yes**                       | Something answered and there is no service                                                                     |
| No route / network unreachable       | **Yes**                       | Definitive absence                                                                                             |
| On-segment `:9009` ping-pong refused | **Yes**                       | Definitive absence                                                                                             |
| Timeout                              | **NEVER** at `.1`/`.2` (§4.3) | A real FrogNet node that is merely slow or briefly unreachable would be silenced until the next merge          |
| Connection reset                     | **NEVER**                     | A reset means something *was* there and dropped it, which a real peer can do under load. Not proof of absence. |

The cost of getting this wrong is not theoretical. On 2026-08-08 a single legitimate connection-refused, during a two-second restart of the elected database host, marked that host as non-FrogNet. It was never dialled again: every sensor write failed, the capability tuple never landed, and the role floated.

### 4.2 The cache MUST be flushed, and the flush MUST have a caller \[FLUSH_HAS_A_CALLER_V1\]

A session is the interval between merges. The cache MUST be cleared at the top of every merge, so a host that joins FrogNet later gets exactly one fresh probe on the next merge.

**The requirement is that the flush actually runs.** The documented flush stage named a command whose entire body cleared an unrelated database table; nothing in the tree ever called the cache's own flush, so a mark was permanent for the life of the process. The comment describing the behaviour was false from the day it was written, and the false comment is what hid the fault for weeks.

Two consequences for a conforming implementation. A flush stage MUST be named for what it flushes. And an implementation MUST NOT rely on a documented flush without a caller in the tree — unreachable clearing state and never-invoked clearing state produce the same permanent mark by opposite routes.

### 4.3 The cache is read where it is written \[NOT_FROGNET_SKIP_V1\]

The walk MUST read the same negative cache it writes. Without the read the cache is write-only at that site, and every convergence pass within one merge re-pays the full probe budget on the same stray LAN clients. **The cache MUST NOT carve out node identity addresses.** The one exception is a live member of an FD&F region, which RAM vouches for and which is never marked. \[ONE_STATE_V1\] A refusal or a no-route still marks a `.1`, by design: the transport does not need to know whether an address is philosophically a member, only whether to dial it, and unreachable is unreachable. The carve-out that once protected role octets required a second, memory-only retire set to stop dialling them, and that set was what took real nodes out of service until the proxy restarted — the protection was the fault. There is nothing to protect against, because the cache is merge-scoped and cleared at the top of every merge (§4.2): a `.1` that is briefly down gets one fresh probe on the next merge, exactly like anything else. One state: it answered, or it did not.

The one place the octet still matters is narrower. A timeout is definitive absence *only* in the DHCP client range `.3–.254`, where no FrogNet node lives; the `.1` and `.2` octets are excluded from that branch alone, so a slow role host is not marked on a timeout (§4.1).

### 4.4 Test isolation \[SENTINEL_DIR_HONOURED_V1\]

The cache is shared between processes through a sentinel file and each process reloads only when the file's modification time changes, so the membership test is cheap on the hot path. The sentinel directory MUST be resolved **per call** and MUST honour the environment override, not pinned at import.

Pinning it made oracles read and write the live node's real cache: tests failed on the machine's history rather than on the code, and a test run could write marks back into production state.

## 5. Proving a link

Reachability MUST be proven by the same probe for every route kind, and that probe is the daemon's own liveness exchange — not the HTTP echo \[PINGPONG_GATE_V1\].

The reason is an asymmetry in what each can answer. The daemon binds all addresses, so it answers on whichever LAN address a probe arrives at. The proxy echo can answer only on the node's identity address. Gating an on-segment leg on the echo therefore false-negatives a live on-segment node, and skips the loop check as well.

The liveness exchange gates reachability **and** detects a path that loops back through this node. The echo is demoted to a post-gate identity fetch: it establishes *who* the peer is, never *whether* it is there.

An ambiguous negative MUST be retried before it is believed (§6.5); a decisive verdict MUST NOT be. A timeout MUST NOT mark the peer (§4.4.1).

## 6. Output

A walk produces two things and no more: **observations**, which are evidence for the planner (§5.2), and **names**, written into this node's own hosts file. A discovered host needs a name there and not merely a route \[DESCEND_NAME_V1\]; a route without a name is reachable and unaddressable.

A walk MUST NOT install a route directly. Planning and commitment are §5, and separating them is what makes the plan a pure function of the evidence.

## 7. References

Specification [§2d](spec-02-invariants.md#b5), [§3](spec-03-node.md), [§4a (Triggering)](spec-04a-triggering.md), [§5](spec-05-routing.md), [§6](spec-06-pond.md), [§8](spec-08-election.md). Source: `discovery/discovery.py`, `discovery/descend.py`, `core/not_frognet.py`. Build manual: Croakus [§7](https://fawcettinnovations.com/magnum-croakus.html#7-how-a-lan-forms-and-splits), [§13](https://fawcettinnovations.com/magnum-croakus.html#13-we-eat-our-own-cooking), [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk), Appendix B (The Echo Probe), Appendix H (The Liveness Gate).

[← §3 Node Model](spec-03-node.md) [Index](README.md) [§4a Triggering →](spec-04a-triggering.md)
