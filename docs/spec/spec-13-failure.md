<!-- Generated from the site page spec-13-failure.html (https://fawcettinnovations.com/spec-13-failure.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §13

# Failure semantics and recovery.

Every condition here is a defined state, not an error. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

## Current implementation clauses: October 12, 2026

## Session end

Announcements and session-owned mappings leave with the session. Clean close and silent death have different costs; the five-second setting has a documented false-death tradeoff on lossy paths.

## Positive failure and absence

Handshake failure and region loss withdraw when observed. Rows that merely disappear have a 60-second graph hold-down. Do not turn that into a universal application expiration policy.

## Application RAM

A new transient RAM host is a new memory world. Participants reconstruct owned state. Arbitrary data survival, history merging or external exactly-once effects are not implied.

## Bridge loss

Lost fdf-tun ends sessions and removes the TUN and reach state. Alias releases enter quarantine. Rejoining derives a new plan from the current world.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Principle](#f1)
2.  [2Failure classes](#f2)
3.  [3Writes during a failure](#f3)
4.  [3aRole handoff while the network is intact](#f3a)
5.  [4Recovery](#f4)
6.  [5Convergence after failure](#f5)
7.  [6Non-goals](#f6)
8.  [7References](#f7)

## 1. Principle

**Operating-model note.** Loss of a peer, bearer, route, service host, or remote region is not automatically an application failure. FrogNet continuously recomputes the machine that remains reachable. **An application SHOULD avoid building recovery choreography around topology events the fabric already absorbs, and react only when the state it actually requires is no longer available.**

Croakus: [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed)

Every condition in this section is a defined state, not an error. An implementation MUST NOT surface any of them as a fault to the application, and MUST NOT enter a degraded or suspended mode on any of them. The application is told that the ground moved; what that means is the application's, because only the application knows what its own state means.

This is a design position, not a convenience. Automatic recovery would require the platform to invent an answer about data it does not understand, and an invented answer that looks correct is the failure mode the whole system is built to avoid.

## 2. Failure classes

| Condition                      | Detection                                                                                                                                                                                                                                            | Required response                                                                                                                                                                                                              | What continues                                                                                                      |
|--------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Verified link lost             | Next walk returns no answer from the peer                                                                                                                                                                                                            | MUST re-derive routes; MUST withdraw the peer from membership                                                                                                                                                                  | Everything reachable by another path                                                                                |
| Station loses its access point | Association lost                                                                                                                                                                                                                                     | MUST re-associate through discovery, not through a stored association                                                                                                                                                          | Local operation; the node is a whole network alone                                                                  |
| Partition                      | Membership recomputed smaller (§6.3)                                                                                                                                                                                                                 | MUST hold an election in each fragment where a role was lost                                                                                                                                                                   | Both fragments, independently, with their own stores                                                                |
| Database host lost             | Local detection per §6.5 — write-then-read, generation counter, or name resolution                                                                                                                                                                   | MUST elect; every role-bearing object MUST re-assert via `hostReset`                                                                                                                                                           | Reads and writes against the new host, from the beat the name rebinds                                               |
| Region lost                    | The held `fdf-events` session ends (“gone”)                                                                                                                                                                                                          | MUST withdraw every route and name the region gave, tear down its tunnels and ask again every 5 s; MUST NOT compute a substitute from elsewhere (`poll.py` `run_region_loop` \[GONE_IS_GONE_V1\] \[REJOIN_EVERY_5S_V1\]; §7.5) | The node’s own /24. Every cross-site link ends at the region host, so all of them go with it until it answers again |
| Node lost                      | Peers withdraw it on failed proof; its process-owned tuples are not reaped: there is no sweep, and the exit-time cleanup does not run when a node is lost; it drops out of the election pool because it is no longer in the live hosts block (§10.3) | MUST NOT hold its membership open pending return                                                                                                                                                                               | Everything else                                                                                                     |

Liveness retry is specified in §6.5: `FROGNET_ALIVE_9009_TRIES` defaults to 3, only ambiguous negatives are re-asked, and a timeout MUST NOT be recorded as definitive absence. `WINNER_HYSTERESIS = 0.50` (`discovery/discovery.py`:50) is **not** an election constant and MUST NOT be cited as one: it governs route next-hop flap. A challenger must beat the installed metric-22 winner's measured RTT by at least 50% to displace it, because replacing a winner is an `ip route replace` that yanks the next-hop on the /24 and interrupts flows in progress. Tunable via `FROGNET_WINNER_HYSTERESIS`; 1.0 flips on any improvement, 0.0 never flips.

PENDING SOURCE — the association retry policy and the tuple reaper interval. The tunnel keepalive and handshake window are no longer pending and are stated in §7.6: `PersistentKeepalive` 25 s, rekey approximately 120 s, `HANDSHAKE_LIVE_SEC` 180 s. There is no tuple sweep: the reference implementation retired its reaper.

### 2.1 What does not expire

**Capability rows do not age out, and an implementation MUST NOT gate an election on their age** \[CAPABILITY_DOES_NOT_AGE_V1\]. They are written unowned (§10.3), so they survive their writer and persist until a newer value overwrites them. Removing them is not election aging; there is no reaper.

An earlier design refused any ballot past a maximum age \[BALLOT_ADMISSIBILITY_V1\] and is superseded. It answered the right question with the wrong evidence. The fossil that won a database-host election for a full day was not a stale *description* — that machine still had its cores. It was an **unreachable** machine, and the question to ask is whether it is there, not how recently it wrote. Age only correlated with liveness and the correlation broke in both directions: a live node whose advertiser write timed out for half an hour was refused, while a dead node that wrote a minute before dying was admitted.

A missing envelope MUST NOT disqualify a candidate either: refusing a ballot for having no timestamp is an age judgement by another name, and against an older store it removed every node pond-wide.

**Nor may liveness be re-measured locally.** An implementation MUST NOT probe candidate reachability during a gather. There was such a probe and it was wrong: a local probe is a *per-node* input, so every node probes from its own vantage, obtains a different pool, and elects a different winner — measured 2026-08-08 as three simultaneous answers for the database host across six nodes.

Liveness is **already a shared fact**. The candidate pool is the live hosts block: every node holds the same converged block, a capability row whose writer is not in it is dropped, and a live host with no row enters unpublished. A fact each node measures for itself is not a fact they can agree on, so the determination MUST be identical on every node by construction rather than by agreement. Candidates MUST be emitted in a deterministic order — sorted by address — so the lists themselves are identical everywhere.

No per-node measurement remains. The one that did — whether a candidate sits on *this* node's LAN — selected the plane for a LAN-scoped role, and left with the role that needed it: candidates are one pond-wide list.

**A read failure MUST NOT collapse into an empty candidate set** \[GATHER_NOT_SILENT_V1\]. Silence turns “could not read” into “no candidates” with no trace, which is how a single malformed row once presented as a role with zero hosts. The failure MUST be logged and the accumulated partial list still returned, so one role cannot wedge the whole merge.

Where a host holds more than one capability row, every node MUST select the same one — the newest by timestamp per address \[DBHOST_DETERMINISTIC_ROW_V1\]. Selecting by store order lets two nodes pick different rows for one host and score them differently, splitting the winner even where the scoring function is deterministic. Here the timestamp is a tiebreak, never a gate.

## 3. Writes during a failure

**Do not coordinate the writers. Preserve their independent truths and let the reader interpret the state (§10.3b).** A failed write is not queued for replay and a returning node does not synchronise. Each writer resumes writing its own truth, and the reader sees what is present now.

Writes from a fragment that cannot reach its store are **absent, not queued**. An implementation MUST NOT buffer writes for later delivery, MUST NOT retry them against a different store, and MUST NOT report them as succeeded. On heal there is no merge, no conflict, and no replay (§2c, §6.4).

The consequence for readers is the one property most often got wrong: a value written on the far side of a partition remains observable until it ages past the freshness window, and then **ceases to be observable rather than becoming wrong**. A value outside the window MUST be treated as absent, not as stale-but-usable.

**The store decides freshness, not the reader** \[ENVELOPE_TS_V1\]. The rule and its rationale are §9.4 and are not restated here. Its consequence under failure: because one clock decides and it is the one that wrote the row, a partition cannot make a value look fresh to one side and stale to the other.

**A row whose freshness cannot be established is not fresh.** Where a freshness-bounded read returns a row carrying no update envelope, the reader MUST drop it and MUST say so. A store that does not implement the filter returns everything, and handing those rows back presents a full history as though it were current: departed peers in the roster, months-old calls offered as joinable.

## 3a. Role handoff while the network is intact

§13.3 covers the partition case: the store is unreachable and writes are absent. This section covers the other one, which is asked more often and is not the same — **the network is healthy and the role simply moves**, because a better-qualified host appeared, the current host went down, or a merge caused every node to derive the answer again (§8).

**Applications do not track the move.** They address the store by name, not by machine. The name is `databasehost.frognet` and it always resolves to whichever host currently holds the role — including, on a lone machine, to itself (§8.1). There is no state in which the role does not resolve to something: a single node is trivially ready for every role and elects itself for all of them.

\[NO_FALLBACK_V1\] The name MUST resolve from the hosts file and MUST NOT fall back to DNS. Resolution is cached briefly — five seconds by default — so a client picks up a change on its next resolution rather than holding a stale address indefinitely.

An unreadable or empty hosts map MUST raise a distinct error and MUST NOT present as “name not found”. The two are different faults with different responses: one is a broken node, the other is a role that has not yet been elected. An implementation that collapses them makes a local file-permission failure look like a network condition.

### 3a.1 What an application sees

**An operation in flight when the role moves fails**, and an application MAY register a callback that fires after the reassignment, on receipt of which it reasserts its own memory and reads to see what survived.

That callback belongs to the UnREST service definition rather than to failure handling, because it is part of the programming model an application writes against. It is specified in **§10.7a**, including why an in-flight operation MUST NOT be re-aimed at the new host, and why a role event is not a value-change notification.

## 4. Recovery

Recovery is not a separate code path. The method that introduces a node the first time reintroduces it after any of the conditions above: `hostReset` fires, every object that answers the verb re-states what it is, and the new store is populated by re-assertion rather than by transfer (§6.6). There is no disaster path because a split is not a disaster.

An implementation MUST NOT provide a distinct recovery routine for partition. Where one exists, it is a second implementation of `advertise`, and two implementations of one rule are two rules.

## 5. Convergence after failure

Convergence is defined as in §6.7: every member's derived topology stable against current observations. It is a statement about topology only. The store does not converge and MUST NOT be described as converging (§2c).

## 6. Non-goals

The following are not provided and MUST NOT be claimed: durable queueing of writes across a partition; exactly-once delivery of anything; a replay or write-ahead log; automatic conflict resolution; a quorum or consensus round; notification that a failure occurred. Events that compound when duplicated do not belong in the store (§9.3).

## 7. References

Specification [§2](spec-02-invariants.md), [§6](spec-06-pond.md), [§8](README.md#s8), [§9](spec-09-memory.md), [§7 (Region)](README.md#s7). Build manual: Croakus [§18](https://fawcettinnovations.com/magnum-croakus.html#18-split-merge-and-why-it-is-safe), [§29](https://fawcettinnovations.com/magnum-croakus.html#29-re-establishing-memory-after-a-split), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed).

[← §12 Security Architecture](spec-12-security.md) [Index](README.md) [§14 Performance and Adaptation →](README.md#s14)
