<!-- Generated from the site page spec-05-routing.html (https://fawcettinnovations.com/spec-05-routing.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §5

# Addressing and routing.

Observe, plan, commit — and the three MUST stay separate. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

How a node finds neighbours and installs routes

## Current implementation clauses: October 12, 2026

## Graph inputs

Addresses and matching DHCP lease observations establish local relationships. Uplink observations and hub state affect exits and remote paths. Read the current fdf_graph implementation for the exact ranking and forest rules.

## Apply the local slice

The daemon installs routes, defaults and names for its identity. Region traffic is sourced from the node’s own .1. A planned hub route is not usable merely because a row exists; it is gated on the handshake.

## Mappings

The manager refuses overlapping served prefixes and refuses transfer of a mapping held by another live session. The hub accepts allowed prefixes from the graph and verified mappings naming that hub.

## Aliases

The bridge gives reached scopes separate aliases from 100.64.0.0/10. Each scope’s packet device carries its own assigned address. The engine updates IPv4/TCP/UDP checksums and quoted ICMP headers. Addresses embedded in payloads remain a limitation.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the LAN-only and region paths remain separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Model](#r1)
2.  [2The unit of evidence](#r2)
3.  [3Metrics](#r3)
4.  [4Planning](#r4)
5.  [5Committing](#r5)
6.  [6Tunnel existence is not tunnel usage \[NO_BROKER_V1\]](#r6)
7.  [7Route sourcing](#r7)
8.  [8References](#r8)

## 1. Model

**Architecture-model note.** Route selection is not a distributed agreement. Each node plans from its own observations and installs its own routes. No route controller distributes a table and no consensus operation establishes the selected path. **If two nodes must exchange decisions in order to agree what route should exist, the implementation has imported coordination into a process this section defines as local derivation** (§4.1a).

Croakus: [§6](https://fawcettinnovations.com/magnum-croakus.html#6-gateways-children-and-peers), [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk), [§22](https://fawcettinnovations.com/magnum-croakus.html#22-one-overlay-many-sites)

Each node serves one /24 and is identified by its `.1` address (§3.2). Routing is the act of bringing the kernel table into agreement with evidence the node gathered itself. No route is accepted on assertion: a node installs only what it measured.

The pipeline is three stages and MUST be kept separate: **observe** (measure paths), **plan** (decide the table, purely), **commit** (apply it to the kernel). The planner is a pure function of its inputs and MUST NOT touch the kernel; the committer MUST NOT decide anything.

## 2. The unit of evidence

An observation is a measured path, not a claim. It asserts: I measured a path to this destination's probe alias by forcing traffic through a specific device and next hop, the full-stack echo round trip took this long, and the echo identified the peer as this address.

| Field       | Meaning                                                                                                        |
|-------------|----------------------------------------------------------------------------------------------------------------|
| `dest`      | The /24 the peer advertises, derived from its identity                                                         |
| `host_path` | The peer's canonical `.1` identity as returned by its own echo. This becomes the `via` on the committed route. |
| `dev`       | The interface the probe went out on                                                                            |
| `rtt_ms`    | Measured full-stack echo round trip                                                                            |

The identity on the route comes from **the peer's own echo**, not from what this node assumed about it. A node that guesses a peer's identity has left the model.

## 3. Metrics

Metrics are fixed roles, not a ranking dial. All FrogNet /24s are committed at one value, so metric no longer serves as a tiebreaker and an operator can find FrogNet's entire footprint by grepping for it.

``` code
ALIAS_METRIC     5    transient aliases
SWEEP_METRIC     5    defensive sweep
PROBE_METRIC     6    transient /32 probes — installed and deleted
                      every walk, regardless of convergence
WINNER_METRIC   22    the committed /24: one per destination
FALLBACK_BASE  100    a surviving alternative path
```

An implementation MUST NOT vary the winner metric to express preference. Preference is expressed by which observation wins the plan, not by the number in the table.

## 4. Planning

The planner produces exactly the kernel operations needed to bring the table into agreement with the evidence, and MUST do nothing the evidence did not authorise.

### 4.1 Which observation wins

Lowest measured RTT wins among observations that compete \[PURE_RTT_V1\]. The earlier rule that an indirect path could not compete with a direct one \[TRACEROUTE_V1\] is retired: a reflection through the node’s own tunnel traced as direct and won.

Equal measured RTTs keep the walk’s order; between vouches with no measured echo, the installed winner goes first \[VOUCH_INCUMBENCY_V1\] — no class weighting and no hardware preference.

### 4.2 One route per destination, and losers are deleted

There is one committed /24 per destination. Every non-winning route for a destination that *has* a winner becomes a removal, one per distinct device, next hop and metric. **Losers are deleted, not demoted.**

**A pass that found no winner at all removes nothing** \[REAP_NONAUTHORITATIVE_HOLD_V1\]: no evidence is not evidence that the existing table is wrong. Otherwise every /24 that is not a winner this pass is removed \[REAP_STALE_WINNERS_V1\]; the node’s own connected /24 is never removed. This is the routing form of the rule in §13.1: absence of measurement is not a measurement.

### 4.3 Fallbacks \[FALLBACK_METRIC_V1\]

Where a destination has several observed paths — a direct tunnel and a LAN-recursive path through a peer's transit advertisement, say — the winner is committed at the winner metric and **every other observation is committed at the fallback metric** rather than discarded. Longest-prefix match with the lower metric carries steady-state traffic over the winner; if the winner's interface goes down, the kernel falls through to the alternative immediately, without waiting for the next merge to re-plan.

Two consequences an implementation MUST observe. Stickiness comparisons MUST consider only routes at the winner metric \[STICKY_WINNER_ONLY_V1\] — a fallback matching an observation must not prevent the winner from switching when a better path appears. And stale removal MUST distinguish a route that matches a current observation, which is a fallback candidate and is kept, from one that matches no observation, which is a stale path and is removed.

### 4.4 Winner stickiness

Replacing a winner is a route replacement that yanks the next hop on the /24 and interrupts flows in progress, so an installed winner whose destination `.1` answers over its own path is held and no challenger displaces it \[ROUTE_INCUMBENCY_HOLD_V1\]; where its health is not confirmed, a challenger MUST be a clear win rather than a marginal one: it must measure below **half** the installed winner’s RTT — `WINNER_HYSTERESIS` = 0.50, tunable via `FROGNET_WINNER_HYSTERESIS`, where 1.0 flips on any improvement and 0.0 never flips (§13.2). Anything less is jitter between near-equal paths, and honouring it rewrites the /24 every pass and latches the re-run flag indefinitely.

## 5. Committing

A real change to the **committed** routing table — the table before and after the pass, not a count of writes \[RUNAGAIN_ON_REAL_DELTA_V1\] — sets the mutation flag that drives announcement \[SYNC_ON_ROUTE_MUTATION_V1\]; a topology change in the host table also counts. The flag keys on the routing table, not on the hosts file: the table is what neighbours need to hear about, and it can churn — a winner installed, a verification backed out, a sweep — while the host table sits unchanged.

**Transient probe routes MUST be excluded from the flag.** They install and delete every walk regardless of convergence, so flagging them fires announcement every cycle as noise (§4a.4.3).

The committer MUST name the exact destinations that changed, so that a mutation can be traced to the /24 that caused it rather than inferred from a table diff.

## 6. Tunnel existence is not tunnel usage \[NO_BROKER_V1\]

Two concerns MUST be kept separate:

| Concern                          | Driven by                                                                                                                                |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| Whether a tunnel exists          | The region: one channel per live hub that holds this node’s key (`region.my_channels`), reconciled by the tunnel daemon per region event |
| Which route serves a destination | Observations                                                                                                                             |

In region mode the hub link exists because the region says so, and the tunnel daemon owns it: it is brought up when missing and torn down when the hub leaves, in the daemon’s own reconcile (`poll.py` `run_region_loop`, `_reconcile_bringup_phase`), never by the route committer. **Only a kernel `wgN` whose hub is no longer in the region is an orphan**, and only those are torn down (`poll.py` `_tear_down_orphan_iface`: “authority, not failure-to-reach”). A LAN-only node has no tunnels at all. The broker’s channel list, which once overrode the planner’s teardown of an active channel that won no /24 \[BROKER_TEARDOWN_V1\], is gone with the broker (`frognet_route/planner.py:39-43, 776`: “is gone \[NO_BROKER_V1\]”); on a region the planner is not asked about hub links. Tearing down a tunnel because an observation beat it this cycle would destroy the standby path the next cycle will need, which is why existence is the region’s decision and not the route planner’s.

## 7. Route sourcing

A route the node reaches directly, and every tunnel route, MUST be sourced from the node's own identity address (§3.2), and MUST NOT be sourced from a transit /30 or a borrowed uplink lease; a LAN route via a first hop carries no source, so the reply returns via that hop \[DOWNSTREAM_VIA_SRC_LESS_V1\]. Neither is propagated across the mesh, so a host beyond a tunnel cannot route a reply back, and the return path fails silently for every child behind the peer — presenting as an application fault several hops from its cause.

The reserved transit range is not walked as a discovery target: it carries the region hub’s edge addresses (10.253.X.Y) and holds no node identities.

## 8. References

Specification [§3.2](spec-03-node.md#n2), [§4 (Discovery)](README.md#s4), [§4a (Triggering)](spec-04a-triggering.md), [§6](spec-06-pond.md), [§7 (Region and tunnels)](README.md#s7), [§13](spec-13-failure.md). Source: `discovery/routes.py`, `discovery/discovery.py`, `discovery/live.py`. Build manual: Croakus [§6](https://fawcettinnovations.com/magnum-croakus.html#6-gateways-children-and-peers), [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk), [§22](https://fawcettinnovations.com/magnum-croakus.html#22-one-overlay-many-sites).

[← §4a Triggering](spec-04a-triggering.md) [Index](README.md) [§6 Pond Formation →](spec-06-pond.md)
