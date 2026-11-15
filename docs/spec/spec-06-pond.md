<!-- Generated from the site page spec-06-pond.html (https://fawcettinnovations.com/spec-06-pond.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §6

# Pond formation, split and healing.

Formation and splitting are the same machinery. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

A standalone, autonomous mesh — every FrogNet identical

## Current implementation clauses: October 12, 2026

## Several scopes

A participant can keep one session per configured pond. One pond is forward; other ponds retain membership and may permit routed reach.

## Bring forward

Switching forward roles reannounces on existing sessions and changes the path. Notices are tuples that can request attention and offer Bring forward on Android.

## Split and RAM

A change in connected scope may require a new transient application RAM host. Applications treat it as a changed world, reassert owned truth and discover current dependencies. No general application-history merge is supplied.

## Evidence boundary

Multiple overlapping scopes and repeated switches have namespace evidence. Arbitrary hierarchy depth, every physical bearer and policy composition require their own tests.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the LAN-only and region paths remain separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Definition](#p1)
2.  [2Formation](#p2)
3.  [3Split](#p3)
4.  [4Heal](#p4)
5.  [3aChorus](#p3a)
6.  [4aThe store that stops being elected](#p4a)
7.  [5Detection](#p5)
8.  [6Re-establishment: hostReset](#p6)
9.  [7Convergence](#p7)
10. [8What does not happen](#p8)
11. [9References](#p9)

## 1. Definition

Croakus: [§7](https://fawcettinnovations.com/magnum-croakus.html#7-how-a-lan-forms-and-splits), [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split)

To a node, a pond is not declared and is not configured. It is the set of nodes that can currently reach each other, sharing one membership, one topology view, and one authoritative store — recomputed from observation every time observation changes. **An implementation MUST NOT hold a pond definition that survives contradiction by the current walk.**

**The region holds a different object, under a different word, and the two MUST NOT be conflated.** The region’s administrative record is the invitation: on the region host `fdf-invite NAME` makes NAME’s WireGuard key, gives it an edge address (the first free 10.253.X.Y; 64,008 per region) and records it in `/opt/fdf/hub/admitted/NAME.json`; the hub has one peer per admitted member and none for a key that was never invited (`fdf-hub` logs `NOT ADMITTED`) (§7.3, §7.4, §17.4). There is no member limit, no group token and no pond password. That record persists whether or not the member is currently reachable, until `fdf-invite NAME --revoke`. The broker-side pond this paragraph once described — named, with a member limit and a group token — went with the broker on 12 October 2026 (`docs/cleanup-2026-10/T3b.md`).

The distinction is which question is being answered. **The invitation answers who is entitled to join. A node’s pond answers who is reachable right now**, and only the second governs anything in this section. A node MUST NOT treat admission as evidence of reachability, and MUST NOT keep a peer in its topology because the region still lists it as admitted: in the region a member is alive while its session to the RAM manager is up, and its facts go when the session ends (§7.1).

The same machinery handles formation and splitting, and MUST NOT distinguish them. A node does not decide whether it is joining or losing; it recomputes what it can see. A split is a walk that returned a smaller world.

## 2. Formation

Nodes that come to share a reachable segment form a pond. Each node probes, proves the link in both directions, and writes the proven peer into its own hosts file; the peer does the mirror image. Within a small number of walks each node's `/etc/hosts` carries the other and the pond exists. Nothing was configured and no node was told.

A node MUST NOT enter a peer into its membership on an unproven observation. Reachability MUST be proven in both directions before a peer is admitted; a one-way probe response is not a link.

``` code
WORKED EXAMPLE — forming

  HomeBase runs alone.
  ShopBox is plugged into the switch.

  HomeBase, next walk   probe wire -> answer from 10.84.84.1
                        prove alive -> write to /etc/hosts
  ShopBox,  next walk   mirror image

  two-node pond. nobody configured anything.
```

Verified where the source states them. `FROGNET_ALIVE_9009_TRIES` defaults to **3** and is read at call time \[ALIVE_9009_RETRY_V1\]: only an *ambiguous* negative — `None` or `alive=False` — is re-asked. Decisive verdicts (a float RTT, `LOOP`, `REFUSED`, `alive=True`) MUST NEVER be retried. The ping-pong is acknowledged to false-negative on high-RTT and tunnel paths, and one miss at the walk dropped measured candidates to vouch-only and flapped the single-winner roles pass to pass. A truly dead path still fails after all tries: the gate is unchanged and the final negative is still believed. This is not a fallback — nothing is hidden.

A timeout MUST NOT mark a peer as not-FrogNet: a slow real node is not an absent one. Default-route resolution uses a 15 s timeout; the control-plane name read uses 3.0 s.

PENDING SOURCE — the walk interval, probe timeout, and the number of consecutive failures before a peer is withdrawn are not stated as named constants in `discovery/discovery.py`. They are properties of the trigger cadence (§4a) rather than of a timer, and are to be established from an instrumented run rather than inferred.

## 3. Split

**Architecture-model note.** A partition is not a replication event and reunion is not a reconciliation event. Each reachable region simply becomes the machine available there and operates on the state available there. When reachability returns, FrogNet re-establishes one topology and one authority; it does not reconstruct a distributed history. **An application that requires historical reconciliation should model the facts it needs to preserve as independent state** rather than expecting the fabric to infer its semantics after reunion (§10.3b).

A split is a defined state, not an error. When a path is lost, each side recomputes from what it can still see and each becomes a smaller pond that keeps running. There MUST be no error, no hang, and no degraded mode: an implementation MUST NOT treat loss of membership as a failure condition of the node.

Each fragment MUST hold an election where an elected role was lost (§8). When the database host moves, **the memory moves with it**: FrogNet Memory is not migrated to the new host, not replicated to it, and not reconciled against the old one. It lives on whichever machine currently holds the role, and what is in memory is whatever is on that machine.

``` code
WORKED EXAMPLE — the database host follows the split

  BEFORE      databasehost.frognet = 10.80.80.1  (HomeBase, most RAM)

  link drops — two islands:
      island A { HomeBase 10.80.80.1 }   -> databasehost.frognet = HomeBase
      island B { ShopBox  10.84.84.1 }   -> databasehost.frognet = ShopBox

  link returns — one island:
      most capable overall               -> databasehost.frognet = HomeBase
```

Applications on both sides keep asking for the same name and keep getting an answer. Neither side pauses.

## 3a. Chorus

A chorus is **a synthetic infrastructure address plane**, carried on the reserved `10.254/16` on-link range. It is not a tuple scope (§9.2), it is not a pond, and a candidate address in that range MUST be skipped by discovery: the range carries no node identity, so walking it produces false results rather than members (§4.2).

**Status: removed.** Choruses were the broker’s: it paired the members it served as one chorus and put 10.254.x addresses on their `frognet0`. They went with the broker on 12 October 2026 — the daemon’s chorus code (`_live_chorus_ips`, `_add_chorus_ip`, `_remove_chorus_ip`, `_sync_chorus_ips`), the `CHORUSES` key and the chorus web page (`docs/cleanup-2026-10/T3a.md:21,39,244`; `frognet_prune_removed.sh:101,158,161`). The region has no counterpart: members are grouped by nothing but their admission and their sessions (§7). What remains is the skip: `10.254/16` is still excluded as a candidate address by discovery and election (`dhcp_tracking.bash:60`, `core/frognet_role_elect.py:54-60`, the daemon’s own-address search in `config.py:372`), so an address left there by an old build produces no false member.

## 4. Heal

On rejoin, fragments MUST re-run discovery and election. The surviving store is authoritative and there is **no merge of divergent tuples** (§2c). No log is replayed, no histories are reconciled, and no negotiation determines whose value wins. Values written on the far side age out of readers' freshness windows and cease to be observable; new writes land on the new host because that is where the name now points.

An implementation MUST NOT perform automatic reconciliation. Reconciliation would require the platform to invent an answer about data whose meaning it does not hold, and an invented answer that looks correct is the failure this system is built to avoid.

## 4a. What happens to the store that stops being elected

**Nothing happens to it.** It is not told, it is not drained, and it is not reconciled. It was never told it had been elected either.

The database exists on every node that can hold the role (§3). Election does not create it and de-election does not remove it. What changes is only whether traffic arrives: **activity on those tables picks up, or it stops.**

An implementation MUST NOT introduce a handover, a drain, a transfer, or a shutdown sequence on the losing side. There is no state transition to implement, because being elected was never a state the store held — it is a property of where the name resolves (§8.5, §13.3a).

Rows left in a store that is no longer elected are simply not read. They are not deleted, not migrated, and not authoritative. If that node is elected again later, they are whatever they were, subject to the ordinary freshness rules (§9.4).

## 5. Detection

Detection of a moved store is **local**. Nothing is pushed, because the machine that would have announced the change may be the machine that left. An implementation MUST NOT depend on being told.

| Mechanism          | How it detects                                                                                                                                                                                                                                          | Suits                                  |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------|
| Write-then-read    | A process keeps one entry of its own — its own name, its own timestamp — writes it, and reads it back next cycle. Same value returned means the same memory. Missing, or an older timestamp than it last wrote, means the memory changed underneath it. | Any process with a cycle               |
| Generation counter | A counter maintained by the store itself moves on every change from any writer.                                                                                                                                                                         | Processes needing any-change detection |
| Name resolution    | Resolve `databasehost_control.frognet` each tick and compare to the previous answer. Different means the role moved; same means do nothing.                                                                                                             | The daemon, proxy, and bundle shells   |

A process that has just started has nothing to compare against and correctly detects nothing, because it holds no stale state. First observation MUST be recorded without firing a reconcile.

## 6. Re-establishment: `hostReset`

Croakus: [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed)

A newly elected host's store starts empty. It is repopulated not by copying — the old store may be on the unreachable side of the break — but by **every participant re-asserting its own current truth** into the new space.

``` code
WORKED EXAMPLE — island A repopulates its own store

  split — ShopBox is island A's database host. its store is empty.
  the merge fires hostReset() everywhere:

      HomeBase.databasehost.advertise()  -> capability tuple -> new store
      ShopBox.databasehost.advertise()   -> capability tuple -> new store
      HomeBase.mediahost.advertise()     -> capability tuple -> new store

  within a beat, island A knows who can host what
  — rebuilt from each node re-asserting itself, NOT copied.
```

`hostReset` MUST be trigger-agnostic: it takes no argument indicating why it was called and MUST behave identically on boot, merge, split, float, or manual invocation. It has one behaviour — publish what this node currently is, to wherever the coordination plane currently lives — which is what makes it safe to call from anywhere, at any time, as often as desired. A reconcile that varied by trigger would require every caller to know its own situation, which is a larger promise than any caller can keep.

`hostReset` is a protocol, not a method on a class hierarchy: any object in any process MAY answer to it, and the watcher fans it across whatever modules the process holds without knowing what they are. A module participates by implementing one no-argument method. Nothing registers it and no interface is declared, and a module that holds nothing legitimately implements nothing and is skipped.

### 6.1 Order is load-bearing

The reconcile is **three phases in a fixed order**, and an implementation MUST NOT interleave them:

``` code
for each module:   reset world -> ensure MY consistency -> write MY memory
then ONCE:         read the SHARED memory by tuple vector
                   (re-establish relationships)
culminating in:    render the presentation layer over the vector just read
```

Every module MUST re-assert its own memory before **any** shared read happens, and the shared read MUST happen once, not once per module. Relationships are then established over a store that every module has already repopulated, rather than over a half-reasserted one. The dispatcher owns the shared read; the per-module work is each module's own.

**The third phase is the dispatcher's too.** Any rendering of the reconciled world — the Communicator UI in the reference implementation — happens once, over the vector the second phase read, and MUST NOT be driven per module.

**hostReset MUST be idempotent.** Re-asserting the same authority MUST NOT churn a version, and two consecutive calls with nothing changed between them MUST produce the same result. That is what makes the “call it from anywhere, at any time, as often as desired” property above true rather than merely tolerated, and it is exercised as a conformance check (§16).

**One module MUST NOT sink the reconcile.** A failure in one module is recorded against that module and the pass continues. A reconcile that aborts on the first error leaves every module after it un-reasserted, which is a worse state than the one that triggered it.

The trigger is the existing change detector and MUST NOT be a new message: on-mesh, the merge already knows the database host line moved; off-mesh, a name-resolution delta. Either calls the same entry point (§6.5).

Two consequences an implementation MUST design around. First, writes made by `hostReset` land on a store that has never seen them, so the first exchange of each shape crosses FULL — the new host holds no template to diff against — and only then collapses to DIFF and SAME (§11). Second, `hostReset` re-establishes current state, not a log; it is not a historical recorder and MUST NOT be used as one.

## 7. Convergence

Convergence is complete when every member's derived topology is stable against current observations. The term is reserved for this meaning. An implementation MUST NOT describe memory behaviour as convergence: the store does not converge (§2c).

## 8. What does not happen

An implementation claiming conformance to this section MUST NOT provide, and MUST NOT be described as providing: a merge procedure, a replay log, an anti-entropy pass, a background sync, data ferrying between fragments, a quorum, or a notification that the topology changed. Detection is local, re-establishment is by re-assertion, and what to do about it is the application's, because only the application knows what its own state means. FrogNet reports that the ground moved and stops.

## 9. References

Specification [§4 (Discovery)](README.md#s4), [§8 (Election)](README.md#s8), [§9 (Memory)](spec-09-memory.md), [§13 (Failure semantics)](spec-13-failure.md). Build manual: Croakus [§7](https://fawcettinnovations.com/magnum-croakus.html#7-how-a-lan-forms-and-splits), [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed).

[← §5 Addressing and Routing](README.md#s4) [Index](README.md) [§7 Internet Extension →](README.md#s7)
