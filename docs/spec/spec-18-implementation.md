<!-- Generated from the site page spec-18-implementation.html (https://fawcettinnovations.com/spec-18-implementation.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §18

# Implementation considerations.

This document is normative (§18.8). Where it and an implementation disagree, one of them has a defect — a specification erratum or an implementation bug — and which it is must be established rather than assumed. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Root cause, instrument, prove — and where FrogNet came from

## Current implementation clauses: October 12, 2026

## Current trees

opt/fdf/src/fdf-source contains region, graph, node integration and release/test tools. opt/fdf/src/frognet-android contains the member, Android app and Go packet engine. The Windows bridge is under opt/fdf/windows/bridge.

## Application trees

The C++ Communicator and servers are under opt/frognet_semantic/ribbit/examples/communicator. Android’s one current Communicator is inside the membership app. The old standalone tree is superseded.

## Workload membership

A process or isolated workload can have an identity and scoped state. Network namespaces supply packet-path qualification. This is not evidence of a shipped Kubernetes CNI, orchestration control plane or every container deployment.

## Transition

The October 11 tree retained broker runtime and installer coupling. The October 2026 cleanup removed them; its final gates ran on 13 October (`docs/cleanup-2026-10/FINAL-GATES.md`).

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; where they describe the broker path, that path was removed in the October 2026 cleanup (docs/cleanup-2026-10/).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Reference platform](#i1)
2.  [1aThe substrate is not the architecture](#i1a)
3.  [2Bearers](#i2)
4.  [3Registries](#i3)
5.  [4Regulatory](#i4)
6.  [5Falsification](#i5)
7.  [6Doctrine tags](#i6)
8.  [7Deployment cautions](#i7)
9.  [7aNode identity has one home](#i7a)
10. [8Where the deeper treatment is](#i8)
11. [8aBibliography](#i8a)
12. [9References](#i9)

## 1. Reference platform

Croakus: [§5](https://fawcettinnovations.com/magnum-croakus.html#5-the-wire-and-the-radio), [§38](https://fawcettinnovations.com/magnum-croakus.html#38-encryption-is-a-dial-you-hold), [§58](https://fawcettinnovations.com/magnum-croakus.html#58-autonomous-systems-and-what-that-implies)

The reference implementation targets 64-bit Debian-family Linux; the October 2026 gates ran on Ubuntu 24.04 x86-64 (§3.1). Nothing in §§1–17 depends on that choice; it is what has been built and run, not a requirement on a conforming implementation.

## 1a. The substrate is not the architecture

§18.1 gives the reference platform. It is not a statement about where a FrogNet belongs.

**A node is a node.** An operator chooses the compute, the bearers, the storage and the machine that holds the elected role independently of one another, and none of those choices is visible to an application: it addresses a value, and the value resolves (§10.4). A pond may mix owned servers, colocated hardware, cloud instances, field equipment and radios in one machine, and nothing in this specification distinguishes them.

**This inverts the usual relation to a hosted platform.** A cloud provider presents a distributed computer that applications are written against. Under this specification a provider’s instance is one component of a machine the provider does not define — a capable node, or a capable memory subsystem, alongside parts it has nothing to do with. An implementation MUST NOT introduce a deployment-model distinction the fabric does not have.

**The consequence is that an operator chooses the machine without owning every component of it.** Fifty owned servers, two hundred field devices, laptops, a radio-connected site and one large rented instance can be members of one pond. The rented instance is not a cloud application the rest talks to; it is a particularly capable core of the machine, and no other node is written differently because it is there.

| Objection                                    | Answer                                                                                    | Today                                                                                               |
|----------------------------------------------|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| The available hardware is not capable enough | Add a capable node. Capability election will use it when it is the best candidate (§8.4). | **Works.** A node is a node.                                                                        |
| Serious compute is required                  | Make the capable machine a node. It executes its own work like any other.                 | **Works.**                                                                                          |
| Very large persistent storage is required    | Elect a node that fronts an external database rather than running one.                    | **Not yet** — the eligibility gate and the scoring terms are both local to the candidate (§17.3.1). |

Two of those need no new work and one is an unbuilt capability with the obstacles named. In none of the three does the application change.

So the size of a machine is not bounded by what an operator owns; it is bounded by what can be assembled into it, and the assembly can change without the programming model changing. **That is an architectural property and not a performance claim.** Being able to add a resource does not demonstrate that the machine scales: every machine is bounded by CPU, memory, disk or network, and which one binds must be measured rather than assumed (§14.1a, §17.3).

The engineering is then ordinary: decide whether CPU, memory, disk or network will bind first for the workload, and provision that resource past it (§17.3). What the specification supplies is that the logical surface does not change when the answer does.

## 2. Bearers

TCP is the reference bearer for FNWP-1. WireGuard tunnels, Wi-Fi and unlicensed radio are **interchangeable bearers beneath it**, and the fabric above does not change by a line when one is substituted for another (§12.5).

That interchangeability is a design requirement rather than an observation: an implementation MUST NOT make any layer above the bearer aware of which bearer is in use. The one place bearer identity legitimately enters is per-hop rate measurement (§11.5) and per-edge latency calibration (§16.5), both of which measure the link rather than branch on its type.

**A bearer that reorders or drops is outside what has been established.** Loss and jitter are not carried by a topology edge (§16.7); they are modelled in the transport tier over a single link, and no conformance claim covers a topology under them.

## 3. Registries

The FNWP-1 magic value, the opcode assignments, and the three-coordinate address convention are defined by this specification. **No IANA registry is requested while the protocol is Experimental**, and an implementation MUST NOT claim a registered assignment it does not hold.

Two naming facts that recur as errors and are stated here once. The wire constant is the ASCII literal `FNW1`; the protocol is **FNWP-1**, the FrogNet Wire Protocol, and material naming the protocol by the wire constant is using an obsolete name (§1.3, §11.3). BLDC-1 is the **Bullfrog Long Distance Communications Protocol** and SotF is the **Song of the Frogs Adaptive Communications Protocol**; the three are distinct layers and MUST NOT be used interchangeably.

## 4. Regulatory

Operation on amateur radio allocations is excluded where encryption or commercial content is prohibited. The exclusion is a property of the allocation, not of FrogNet: since FrogNet implements no cryptography of its own (§12.1), an operator on such an allocation is choosing a plaintext bearer, and §12.7 states what that leaves exposed.

**FrogNet is free software under version 2 of the GNU General Public License, and no other version.** This is settled. Material published before this date states otherwise — including an earlier Apache 2.0 statement and, before that, a request-a-licence model. Both are superseded.

Nothing in this specification requires registration, approval, attestation, or a form, and an implementation MUST NOT state or imply that it does. **The one condition is copyleft:** a work derived from the reference implementation and distributed to others carries the GPL with it. That is a condition on **distributing** a derivative — conveying the software, or a work based on it, to another party. It is not a condition on using FrogNet, running it in production at any scale, modifying it for internal use, or implementing this specification independently — an independent implementation written from this document is a separate work and carries whatever licence its author chooses.

It is published as open-source software from the United States. Export-control, sanctions and other laws may apply to particular users, destinations, uses or redistributions, and obtaining or using it makes the obtainer responsible for the laws applicable to them. **Nothing in this specification is a representation that FrogNet has received a commodity classification, export licence, jurisdiction determination or other government ruling**, and an implementation MUST NOT state or imply one.

## 5. Falsification

The standing challenges are maintained with the public repository, each with an oracle definition. Three are named here because they are the claims most worth attacking:

| Challenge              | Against                                                                                                                                                                                                                                                        |
|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Region-host pivot      | §7.7, §12.6 — that an owned region host (the hub, `fdf-hub`), which sees the inter-site path in plaintext and holds the site key, still cannot mint a SAME tag a member will accept, and cannot read a diff against a template it never observed being learned |
| Application recovery   | §6, §13 — that re-assertion re-establishes state without a merge or a replay                                                                                                                                                                                   |
| Credential manufacture | §12.2 — that the SAME identifier cannot be forged without the secret                                                                                                                                                                                           |

A challenge is only a challenge where it can fail. Each MUST carry an oracle whose failure mode is demonstrable, on the same standard as §16.3: an oracle never observed failing has not been shown to test anything.

**Two of the three do not yet carry one, and this document states that rather than implying otherwise.**

| Challenge              | Oracle                                                                                                                                                                                                                                                    |
|------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Application recovery   | **Covered.** `discovery/test_host_reset_oracle.py` and `discovery/sim/host_reset_check.py`, which also exercise idempotence (§6.6.1).                                                                                                                     |
| Credential manufacture | **Not covered.** The existing `same_id` oracles establish that SAME is produced and compared correctly; none attempts to produce an acceptable tag without the secret, which is the claim.                                                                |
| Region-host pivot      | **Not covered.** No oracle in the tree exercises what an owned region host can and cannot do with the traffic it forwards. (The row was stated against the broker until its removal on 12 October 2026; the hub is its successor on the inter-site path.) |

An unexercised challenge is an assertion, and this section MUST NOT be read as evidence for the two above. The claim in each case is a negative — that something cannot be done — and a negative is exactly the kind of claim that needs an oracle rather than an argument.

## 6. Doctrine tags

Requirements throughout this document cite tags of the form `[NAME_V1]`. These are markers in the source, not identifiers in a registry, and they exist so a requirement can be traced to the code that enforces it and to the defect that established it.

Two obligations follow. A cited tag MUST exist in source — a specification citing a tag the tree does not contain is asserting provenance it does not have. And **a tag comment describes intent, not necessarily behaviour**: where a comment and the code below it disagree, the code is the better evidence of what the implementation does and the comment is a defect. This is a rule about reading source, not about the standing of this document (§18.8). **This is common enough in the reference implementation to be expected rather than exceptional.** A survey of the tree against this document found comments describing: a flush that had no caller for weeks (§4.4.2); a reachability probe the same function had removed (§13.2.1); a route-flap threshold of ten per cent where the code applies fifty (§5.4.4); database-host scoring “from MEASURED signal” naming free RAM, measured disk throughput and a CPU benchmark, where the code below it uses installed RAM, disk class and cores × clock — the three inputs \[DBHOST_STATIC_RANK_V1\] exists to forbid (§8.4.1); and a media ladder describing stereo audio on five rungs where the capture path is mono (§14.3).

**In every case the fix landed in the code and the comment above it was not updated.** An implementer reading this tree MUST treat a docstring as a claim to be checked against the lines beneath it, and a reviewer citing source MUST cite the code rather than the comment describing it.

## 7. Deployment cautions

**A node requires its own kernel network stack.** A hypervisor guest supplies one, and because this specification restricts itself to standard kernel facilities — routing tables, netfilter, WireGuard interfaces, network namespaces — a guest is **expected** to be equivalent to bare metal for every purpose here. **That expectation has not been confirmed by test** and is recorded as reasoning rather than as a result.

**A container does not.** It shares the host kernel, so WireGuard requires the module on the host, namespace creation requires `SYS_ADMIN`, and NetworkManager and dnsmasq contend with whatever owns the host’s networking. An implementation MUST NOT present a containerised deployment as conforming unless it holds those privileges, and an operator granting them has reconfigured the host regardless. This is the same constraint that makes simulator tier 4 degrade to a dry run in a container (§16.2).

Stated because each has cost real time and none is discoverable from the specification alone.

**Configuration edits must land where the server reads them.** Where the enabled-site directory holds real files rather than links, an edit to the available copy alone does nothing.

**Stale bytecode wins.** Extraction restores sources with their original modification times, so a compiled artifact from a prior build can be newer and be used in preference. Purge at install and at every merge (§3.5.3, §4a.6).

**A build tarball MUST NOT carry node identity, tunnel keys, certificates, or credentials.** A release gate MUST scan exactly the files injected rather than walking the tree, and MUST fail the build rather than warning.

**Generated documents MUST be edited at their generator.** Editing generated output is lost at the next build, and a specification that quotes a hand-edited generated document quotes something that will not survive.

## 7a. Node identity has one home

Identity is allocated and read at `/etc/fnid`, and nowhere else. `frognet-node-guid.sh --ensure` is the sole creation path (§3.2.1). Every consumer reads that file directly: `internet_tunnels_v3/config.py`.

**A second tool, `frognet-guid`, has been removed.** It wrote a `NODE_GUID=` line into `/etc/frognet/tunnel.conf`: inside the directory a reinstall wipes, which is the failure §3.2.1 exists to prevent, and its `generate` minted a fresh UUID rather than reading the allocated one. Nothing consumed that line — `tunnel.conf` was then parsed for `BROKER_URL`, `PUBKEY` and `GROUP_TOKEN` only, and is now read by nothing — so the rotation it appeared to perform did not occur and the broker row was never retired. The broker followed it out on 12 October 2026 (`frognet_prune_removed.sh:150,160`); in the region there is no row to retire (§3.2.1).

**An implementation MUST NOT reintroduce a second identity location.** Several scripts preserve a `NODE_GUID` key across reconfiguration and exclude it from build tarballs; that is defensive handling of a field none of them consumes, not evidence of a second source of truth.

## 8. Where the deeper treatment is

This specification states what a conforming implementation must do. The build manual states how the reference implementation does it and why alternatives were rejected; [Internals](https://fawcettinnovations.com/internals.html) carries mechanism notes; [the simulator](https://fawcettinnovations.com/simulator.html) and [the demonstrations](https://fawcettinnovations.com/demonstrations.html) carry the evidence.

**This document is normative. The reference implementation is one implementation expected to conform to it.** Where they disagree, one of two things is true and they are not the same thing:

| Situation                                                          | Resolution                                                                              |
|--------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| The specification describes behaviour the designers did not intend | A defect in this document. Issue an erratum and revise; the revision becomes normative. |
| The implementation departs from what this document requires        | A defect in the implementation, whatever this document's provenance.                    |

During drafting the source is the best available **evidence** of intent, and every requirement here was checked against it. That makes source the arbiter of *fact* while drafting; it does not make source the arbiter of *requirement* once published. A specification that defers to code forever cannot be implemented from without consulting that code, which defeats the purpose of publishing it.

## 8a. Bibliography

Citation keys used throughout this specification. A section cites a key; the key is resolved here and nowhere else, so a reference changes in one place.

### 8a.1 Normative references

An implementation cannot conform to this specification without conforming to these.

|             |                                                                                                                                                                                                                         |
|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `[RFC2119]` | Bradner, S., “Key words for use in RFCs to Indicate Requirement Levels”, BCP 14, RFC 2119, March 1997.                                                                                                                  |
| `[RFC8174]` | Leiba, B., “Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words”, BCP 14, RFC 8174, May 2017.                                                                                                                     |
| `[RFC9293]` | Eddy, W., Ed., “Transmission Control Protocol (TCP)”, STD 7, RFC 9293, August 2022. The reference bearer for FNWP-1 (§11.2); a byte stream with no message boundaries, which is why the framing carries its own length. |
| `[RFC2104]` | Krawczyk, H., Bellare, M., and R. Canetti, “HMAC: Keyed-Hashing for Message Authentication”, RFC 2104, February 1997. The construction of the SAME identifier (§11.9).                                                  |
| `[RFC6234]` | Eastlake 3rd, D. and T. Hansen, “US Secure Hash Algorithms (SHA and SHA-based HMAC and HKDF)”, RFC 6234, May 2011. SHA-256, as used in the same identifier.                                                             |
| `[RFC1918]` | Rekhter, Y., et al., “Address Allocation for Private Internets”, BCP 5, RFC 1918, February 1996. The 10.0.0.0/8 space the overlay and transit ranges occupy (§5, §17.4).                                                |
| `[RFC6761]` | Cheshire, S. and M. Krochmal, “Special-Use Domain Names”, RFC 6761, February 2013. The obligations a non-delegated namespace incurs, and which §3.2a meets by construction.                                             |

### 8a.2 Informative references

Cited to locate this design against existing work, or to state precisely what it is not. Conformance does not depend on them.

|             |                                                                                                                                                                       |
|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `[RFC8375]` | Pfister, P. and T. Lemon, “Special-Use Domain ‘home.arpa’”, RFC 8375, May 2018. A registered alternative to the default namespace (§3.2a).                            |
| `[LINDA85]` | Gelernter, D., “Generative Communication in Linda”, *ACM Transactions on Programming Languages and Systems* 7(1), January 1985, 80–112. The tuple-space model (§9.1). |
| `[LINDA89]` | Carriero, N. and D. Gelernter, “Linda in Context”, *Communications of the ACM* 32(4), April 1989, 444–458.                                                            |
| `[PAXOS]`   | Lamport, L., “The Part-Time Parliament”, *ACM Transactions on Computer Systems* 16(2), May 1998, 133–169. Cited in §8.6 for what this specification does *not* do.    |
| `[RAFT]`    | Ongaro, D. and J. Ousterhout, “In Search of an Understandable Consensus Algorithm”, USENIX ATC, June 2014. As above.                                                  |

A citation appearing in a section that is not resolved here is a defect in this document.

## 9. References

Specification [§1](spec-01-scope.md), [§3](spec-03-node.md), [§7](spec-07-broker.md), [§11](spec-11-wire.md), [§12](spec-12-security.md), [§16](spec-16-conformance.md), [§17](spec-17-limits.md). Build manual: Croakus [§5](https://fawcettinnovations.com/magnum-croakus.html#5-the-wire-and-the-radio), [§38](https://fawcettinnovations.com/magnum-croakus.html#38-encryption-is-a-dial-you-hold), [§58](https://fawcettinnovations.com/magnum-croakus.html#58-autonomous-systems-and-what-that-implies), Appendix L (Doctrine Index). Licence and export posture: [download](https://fawcettinnovations.com/download.html).

[← §17 Known Limits](spec-17-limits.md) [Index](README.md) [§19 Ribbit: the model and the method →](spec-19-ribbit.md)
