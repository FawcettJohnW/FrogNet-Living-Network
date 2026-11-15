<!-- Generated from the site page spec-12-security.html (https://fawcettinnovations.com/spec-12-security.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §12

# Security architecture and trust boundaries.

The properties claimed here are architectural. The encryption is not FrogNet’s. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

The surface, named honestly

## Current implementation clauses: October 12, 2026

## Local placement

A service can remain inside a local network. Actual exposure depends on routing, forwarding and the services enabled by that deployment.

## Transport seam

WireGuard protects a tunnel leg. The hub terminates and forwards between legs; application confidentiality across a compromised hub requires protection above that seam.

## Region authority

The manager holds membership and mapping state. Mapping authentication does not establish universal cell permissions or encrypted transport for every RAM API.

## Owner discipline

Write your own truth is an addressing rule. Deployments needing writer/reader authorization must enforce and test it in their host. Semantic compression is not a confidentiality control.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Position](#s1)
2.  [1aTesting this](#s1a)
3.  [2Identity and authentication](#s2)
4.  [3The absent surface](#s3)
5.  [4The wire carries references, not messages](#s4)
6.  [5Encryption is a dial](#s5)
7.  [6Trust boundaries](#s6)
8.  [7Remaining exposure](#s7)
9.  [8Non-goals](#s8)
10. [9References](#s9)

## 1. Position

Croakus: [§36](https://fawcettinnovations.com/magnum-croakus.html#36-nothing-on-the-internet-answers), [§37](https://fawcettinnovations.com/magnum-croakus.html#37-reaching-the-inside-means-being-near-it), [§38](https://fawcettinnovations.com/magnum-croakus.html#38-the-wire-carries-references-not-messages), [§39](https://fawcettinnovations.com/magnum-croakus.html#39-encryption-is-a-dial-you-hold)

**FrogNet implements no cryptography of its own.** It uses facilities the operating system and other software provide — WireGuard, TLS, OpenSSL, HMAC-SHA-256 for authentication and integrity. This is a design boundary, not an omission: it is what makes encryption a decision about bearers the operator controls rather than a change to the fabric.

The properties this section claims are **architectural** — the absent surface, the proximity requirement, the reference-only wire. Each rests on mechanism specified elsewhere in this document. An implementation MUST NOT present the available encryption as the primary security property, because the architectural properties hold whether or not a byte is encrypted, and the encryption is not FrogNet's.

## 1a. Testing this, and why the usual tools report little

**A conventional assessment will return a clean report, and the report will not mean what it appears to mean.** Web scanners, fuzzers and exploitation frameworks are built around HTTP request/reply against a listening service. That target is absent here, so the tooling finds nothing and says so — which an assessor MUST NOT read as evidence of a small attack surface. It is evidence that the instrument and the system disagree about where the surface is.

The same caution that applies to benchmarks (§14.1a) applies to scanners, and for the same reason: **the interesting surface is not shaped like the one the tool expects.**

**A web server is a familiar target. The proxy and daemon are not one.** They speak FNWP-1 on their own dedicated socket (§11a), not HTTP on 80. A scanner that finds the port finds a framed binary protocol it has no parser for.

**Reading the frames does not yield the traffic.** The wire carries verdicts against templates the observer does not hold (§12.4). For most exchanges what transits is `SAME` — four bytes of magic, an opcode, and a sixteen-byte identifier — which says that a value did not change and says nothing about what the value is. A capture is a record of which exchanges occurred and when, not of what was said. The bound is real and it is stated with its limit: an observer present from the first exchange of a structure learns the template in the same act the endpoints do (§12.4.1).

**Reaching the fabric requires reaching the LAN.** There is no public listener into a pond. Across sites the tunnels terminate at the region host (the hub, `fdf-hub`), which is inside the encryption boundary and should be assumed owned (§7.7) — and owning it yields the inter-site path, the site key and the members’ facts and mappings, not a node, not a member’s private key, and not the pond secret. Getting inside a pond means getting inside the network the pond is on, by whatever means one normally gets inside a network.

**Which is where the ordinary tools do apply.** These are standard Linux machines. Everything true of a Linux host is true of a node: unpatched packages, weak credentials, an exposed service somebody added, a careless sudoers entry. **An implementation MUST NOT present FrogNet as a substitute for host hardening**, and an operator SHOULD apply the same controls they would apply to any Linux fleet. FrogNet changes what is on the wire. It does not change what is on the box.

**Build your own image and walk it around.** Add whatever instrumentation, hardening or attack tooling a real assessment needs, then use `frognet_build_release.sh` to roll a release tarball from a healthy node and install it across a network you control. Testing someone else’s deployment from outside establishes very little; testing your own from inside establishes what you actually want to know.

## 2. Identity and authentication

Each node holds a permanent identity and a keypair set at install (§3.2). Peers authenticate by key. **There is no certificate authority** and no third party in the authentication path. In an FD&F region the region host admits each member ahead of time by invitation (`fdf-invite`), which makes its WireGuard key.

The SAME identifier is a keyed authentication tag — HMAC-SHA-256 over a shared secret, truncated (§11.9). It proves that a party holding the secret asserted this response for this request. **It conceals nothing and encrypts nothing.** An implementation MUST NOT operate without a secret in place: the fallback is derived from a constant published in the source, so with it the tag is forgeable by anyone holding the source and proves nothing.

**Where an implementation does fall back, it MUST say so on every derivation, loudly, at critical severity.** A missing secret is not a startup-time condition that can be reported once and forgotten: the file may be removed, replaced, or never written, and silence is how a node runs unauthenticated for months without anyone noticing. The reference implementation derives the fallback from the machine id so that two nodes do not share one forgeable key, which reduces the blast radius and does not restore the property.

## 3. The absent surface

A node MUST NOT expose an internet-reachable application endpoint. Fabric traffic is captured on the node and converges on one internal seam, which is the defined place for an operator to add encryption covering every bearer.

Where a process must accept input from the public internet, it SHOULD be isolated behind a one-way file spool: the public tier holds no credential and can only write a request file; a separate watcher with no network address validates against an allow-list and performs the operation. The property being bought is that the internet-facing tier has nothing worth taking and no way to act.

## 4. The wire carries references, not messages

After the first exchange of a structure, the wire carries verdicts against a template, and **the template lives in each node's local cache** — learned once from that first FULL and never re-sent (§11.8, §11.14).

A capture is therefore a stream of opcodes, sequence numbers, and changed values measured against a structure the observer does not hold:

``` code
captured mid-conversation:

    opcode RESP_DIFF   seq 42
    payload  <field 3 -> 54.6>  <field 7 -> 1719...>

without the template, held only in each node's cache:

    field 3 of WHAT?   field 7 of WHAT?
    names? units? shape?
    -> two numbers and nowhere to put them
```

The meaning is in the cache, not on the wire. It is the same fact that makes the wire thin, read as a security property: the wire is thin because it says so little on its own. A capture also matches no catalogued signature, because there is no envelope announcing what it carries — the envelope was agreed once and left on both machines.

### 4.1 The boundary

**This is encoding, not encryption, and an implementation MUST state it as such.** An observer present from the first FULL, capturing the conversation from its start, learns the template in the same act the endpoints do and can follow the differences from there.

What reference-diffing provides is precise and bounded: it raises the cost of a full capture and guts the value of a partial one. It does not by itself make traffic unreadable to a patient listener who saw the beginning, and the design does not ask it to. Traffic crossing anything the operator does not own crosses a WireGuard bearer, and a listener there gets no capture at all. Two layers: the semantic wire is why a late or partial observer learns nothing useful; the bearer is why there is nothing to observe.

## 5. Encryption is a dial

Because FrogNet contains no cipher, adding encryption is a decision about bearers rather than a change to the fabric. Put a WireGuard bearer under a link and that link is encrypted end to end; put one under every link and nothing crosses in the clear. In both cases **the fabric above does not change by a line**.

Between sites it is already present: cross-internet tunnels are WireGuard by default (§7). On a local wire the operator owns, the choice is theirs — a shared building, a hostile RF environment, or a policy requirement are all reasons to put a bearer on that wire too, or to layer a cipher on the payload. The architecture is indifferent to which, because it routes over whatever is laid beneath it (§18).

## 6. Trust boundaries

| Trusted                     | Not trusted      |
|-----------------------------|------------------|
| The hardware                | The region host  |
| The node keys               | Any third party  |
| The chosen federation peers | Any transit path |

The region host holds no member’s private key once the invitation is handed over (`fdf-invite` records name, public key and edge in `admitted/NAME.json`; the private key is in the invitation line only) and no pond secret; it runs none of the semantic stack and is not a FrogNet node (§7.7). It does, by construction, hold the site key (`/opt/fdf/var/site-key-<AREA>`, root-only), every member’s public key and edge address, the members’ facts and mappings in its RAM manager, and it terminates every member’s tunnel and forwards between them in the kernel (§7.4).

**Compromise of the region host therefore yields the inter-site data path in plaintext at IP level**, not merely ciphertext metadata: addresses, ports, timing, volumes, and the bytes. With the site key it can also sign mappings the RAM manager will accept. Earlier statements that the transit point yields position only are superseded.

What it does not yield is the meaning of that traffic without further work, and the ability to forge the semantic layer:

| An owned region host gains                                                                                                      | An owned region host does not gain                                                                                          |
|---------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| The inter-site bytes, in the clear                                                                                              | The templates those bytes are diffed against, unless it observed from the first exchange of each structure (§12.4.1)        |
| Read, drop, delay and inject at IP level; forge a mapping (it holds the site key)                                               | The ability to mint a SAME tag a member will accept — the secret is a pond member’s and the region host is not one (§12.2)  |
| Full traffic analysis of which sites talk to which; the membership (each member’s name, GUID, /24 and public key are its facts) | Any tuple or role: FrogNet Memory is not placed there — the RAM manager’s records are membership, facts and mappings (§7.7) |

An implementation MUST NOT place a node credential, a tuple, or a role on the region host to simplify rendezvous; every such placement converts a transit point into a member and removes the one boundary it is still outside.

## 7. Remaining exposure

Stated plainly rather than minimised. Each of these is real and none is closed by anything in this specification.

| Exposure                                        | Requires                                                                                                                                                 |
|-------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Radio proximity on a plaintext bearer           | Being physically near the RF                                                                                                                             |
| Physical or Wi-Fi access to a node              | Being near the wire and holding a token                                                                                                                  |
| Path observation of a live session              | Presence from the first exchange (§12.4.1)                                                                                                               |
| **An owned region host on the inter-site path** | Nothing further — it is already inside the tunnel boundary (§12.6). Confidentiality across the seam MUST be supplied above the tunnel if it is required. |
| A compromised member node                       | Yields what that node was entitled to see, and no more                                                                                                   |

The last is a consequence of the memory model rather than a defect in it: a member reads the store, and a compromised member reads what a member reads. There is no per-value access control in FrogNet Memory (§17.5), and an implementation MUST NOT imply one exists.

## 8. Non-goals

FrogNet provides **no confidentiality of its own** — the bearer provides it, and across sites the bearer terminates at the region host rather than at the far endpoint (§7.4). No certificate authority, no key escrow; revocation is the region host withdrawing a member's admission (`fdf-invite NAME --revoke`). **No per-value or per-caller authorisation** in the store. No audit log: the store holds current state, not history (§2c). No claim that semantic encoding is a cipher, and no claim of a cryptographic classification that has not been issued.

## 9. References

Specification [§2](spec-02-invariants.md), [§3.2](spec-03-node.md#n2), [§7.7](spec-07-broker.md#b7), [§9](spec-09-memory.md), [§11.9](spec-11-wire.md#w9), [§11a](spec-11a-proxy-daemon.md), [§17](spec-17-limits.md), [§18](README.md#s18). Source: `core/semcache_id.py`, `core/frognet_secret.py`, `internet_tunnels_v3/wg.py`. Build manual: Croakus [§36](https://fawcettinnovations.com/magnum-croakus.html#36-nothing-on-the-internet-answers), [§37](https://fawcettinnovations.com/magnum-croakus.html#37-reaching-the-inside-means-being-near-it), [§38](https://fawcettinnovations.com/magnum-croakus.html#38-the-wire-carries-references-not-messages), [§39](https://fawcettinnovations.com/magnum-croakus.html#39-encryption-is-a-dial-you-hold).

[← §11a The Proxy and the Daemon](spec-11a-proxy-daemon.md) [Index](README.md) [§13 Failure Semantics →](spec-13-failure.md)
