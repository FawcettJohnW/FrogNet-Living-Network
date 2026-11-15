<!-- Generated from the site page spec-16-conformance.html (https://fawcettinnovations.com/spec-16-conformance.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §16

# Simulator and conformance testing.

It simulates the environment and runs the shipping code. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Attack the mechanics under arbitrary topologies

## Current implementation clauses: October 12, 2026

## Red before green

The r19-a15 hosts regression failed against a14 before it passed against the corrected code. The check makes the failure mechanism concrete.

## Real services and kernels

Region-loop, graph, hub, placement, reach and packet-engine suites use real manager/hub code and namespaces. Packet checksums are tested against recomputation; concurrency and configuration refusal have explicit checks.

## Keep suites distinct

fdf-tun 20/20 and reach 68/68 are separate r19-a14 suites. Their counts are not a single synthetic acceptance total, and they do not establish all physical-device behavior.

## Recorded operations

The October 12 video adds observed Windows/Android use. Tests and measurements reproduced in this site are checkpoint records; the website edit has not rerun those network or compute campaigns.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; where they describe the broker path, that path was removed in the October 2026 cleanup (docs/cleanup-2026-10/).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Principle](#c1)
2.  [2Tiers](#c2)
3.  [3Coverage is proven from output, not asserted](#c3)
4.  [4Isolation](#c4)
5.  [5Correspondence with hardware](#c5)
6.  [6Pre-activation](#c6)
7.  [7What is not modelled](#c7)
8.  [8References](#c8)

## 1. Principle

Croakus: [§12](https://fawcettinnovations.com/magnum-croakus.html#12-validate-it-in-the-simulator), [§38b](https://fawcettinnovations.com/magnum-croakus.html#38b-the-simulator-is-a-shared-instrument), [§39](https://fawcettinnovations.com/magnum-croakus.html#39-never-change-a-live-mesh-blind)

The simulator **simulates the environment and runs the shipping code**. It is not a model of FrogNet; it is FrogNet with its surroundings substituted. An implementation MUST NOT satisfy this section with a reimplementation of the algorithms under test, because a reimplementation proves only that two pieces of code agree.

Only four things may be substituted:

| Substituted              | Replaced by                                                               |
|--------------------------|---------------------------------------------------------------------------|
| The machine              | A capability blob                                                         |
| The store's HTTP surface | An implementation of the same contract (§10.3)                            |
| The wire                 | One network namespace per node, with its own routing table and hosts file |
| The origin               | A deterministic responder                                                 |

**Node identity is the one permitted reimplementation**, and it MUST be rebound per simulated node (§3.2). Everything else runs as shipped.

## 2. Tiers

Fidelity increases with tier, and a conforming suite MUST gate on all tiers it claims:

``` code
1  model        algorithm, topologies, bug regressions, lifecycle.
                pure model; the fast pre-flight
2  real-engine  the REAL planner and committer over every topology
3  fdf-memory   FD&F discovery from memory: a real rina-host and a
                real fdf-client per node; complete verification plan
4  real-kernel  the same convergence over real kernel routing and
                tunnel tooling, in namespaces.  Box only
5  data-plane   the REAL proxy and daemon pushing real traffic over
                the namespace mesh.  Box only
```

A container runs tiers 1–3 and tier 4 as a dry-run plan; a box run executes tier 4. Tier 5's in-process data-plane checks run in both; its real proxy/daemon round trip is box-only. **A suite MUST state which tiers it ran**, because a green result at tier 1 and a green result at tier 4 are different claims.

Tier 4 and 5 emit measured per-edge RTTs and any failures, which feed the calibration loop of §16.5.

## 3. Coverage is proven from output, not asserted

**Coverage MUST be evidenced from the run's own output**, and MUST NOT be claimed from the fact that a code path was called. The requirement is on the evidence, not on its form: an implementation MAY produce it however it chooses, and this specification does not require any particular instrumentation.

Whatever mechanism carries the evidence, it MUST be validated against the implementation every run. A registry of markers that has drifted from the code records coverage of the registry rather than of the components — which is the failure this requirement exists to prevent, in whatever form the evidence takes.

The reference implementation satisfies this with registered log markers revalidated against source each run. **That is one conforming mechanism, not the requirement.** An independent implementation is free to evidence coverage by any means that ties an observed artifact of the run to the component it exercised.

**Every oracle MUST be paired with a control that forces it to fail.** An oracle that has never been observed failing has not been shown to test anything.

**The control MUST run in the same gate as the oracle it guards.** A control that exists but is never invoked establishes nothing at the moment it is needed. The reference implementation carried such a pair — a pre-fix copy of a capability probe, kept as the failing control for a cache-schema oracle — where neither was wired into an automated gate and both ran only by hand. The control was removed first, after which the oracle could no longer fail, and both are now gone (October 2026). A suite MUST report which of its oracles had their controls exercised in the run, so an oracle whose control is dormant is visible rather than silently counted as passing.

## 4. Isolation

A suite MUST NOT share mutable state with the live node or with a previous run, and MUST **fail hard** where isolation cannot be established rather than proceeding unisolated.

Two ways this has actually broken, both of which a conforming suite MUST prevent:

\[SENTINEL_ISOLATION_V1\] Cross-run leakage through the shared negative-cache sentinel silently poisoned discovery oracles: tests failed on the machine's history rather than on the code, and a run could write marks back into production state. Sentinel paths MUST be resolved per call and MUST honour the isolation override (§4.4.4).

\[SIM_TMP_MUST_NOT_MATCH_THE_MERGE_GLOB_V1\] The harness's temporary directory was named with a prefix that the merge controller's own cleanup glob removes, so a merge firing mid-run deleted the harness's isolation directory underneath it. **A test harness MUST NOT place state where the system under test deletes it.**

## 5. Correspondence with hardware

The loop runs in both directions and a conforming suite SHOULD implement both.

| Direction                     | Mechanism                                                                                                                                                                                                       |
|-------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Hardware blesses the model    | A run on real hardware records committed route tables by kernel readback and writes them as a versioned baseline. The offline gate re-runs the real planner and committer and asserts the output still matches. |
| Hardware calibrates the model | Measured per-edge RTT medians by bearer kind override the model's guessed values at run time, before the timing tier.                                                                                           |
| Hardware supplies tests       | Each captured real fault is recorded as a scenario with its observed expectation and replayed offline thereafter.                                                                                               |

**Provenance MUST be recorded per baseline**, because it decides severity. A mismatch against a hardware baseline is a regression — the offline code no longer reproduces blessed kernel truth. A mismatch against an offline baseline is drift since the last blessing and may be intended.

Current state, and the bound on what it proves, are in §17.11: committed route tables and reachability are gated against hardware truth across the full topology set; convergence cycle counts are recorded but **not** gated; and no throughput or bandwidth figure is claimed as predictive.

## 6. Pre-activation

The suite is not only a development tool. Before any FrogNet service is enabled on a newly installed node, the installer runs it against the real code just installed on that machine, and **the install terminates if it does not pass** (§3.5.4). That is the last point at which a broken build can be stopped before it becomes a running node.

## 7. What is not modelled

A conforming suite MUST NOT claim either of these:

| Not modelled                           | Because                                                                                                                                                                                                                                                                                                                                                                                                                               |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Concurrency                            | Merges are serialized. A race requiring two merges in flight cannot be produced.                                                                                                                                                                                                                                                                                                                                                      |
| Packet loss and jitter *on a topology* | The wire delivers between topology nodes. Per-edge latency is modelled and calibrated; loss and jitter are not carried by a topology edge. They exist in the transport tier, where a link is modelled with latency, jitter, bandwidth and outage (the media ladder's tier was removed with the Python Communicator) — but that tier runs one link, not a converged topology, so a suite MUST NOT claim topology behaviour under loss. |

A failure that reproduces only under loss, jitter, or concurrent merges is outside what a green suite has established, and an implementation MUST NOT treat a green run as evidence against it.

## 8. References

Specification [§3.5](spec-03-node.md#n5), [§4.4](spec-04-discovery.md#d4), [§4a](spec-04a-triggering.md), [§5](spec-05-routing.md), [§14.6](spec-14-performance.md#a6), [§17.11](spec-17-limits.md#k11). Source: `simulation/run_all.py`, `simulation/regression_baselines.py`, `simulation/model_feedback.py`, `simulation/frognet_sim.py`, `simulation/netns_backend.py`. Oracles are not in one place: `discovery/` holds the largest set, with further ones under `simulation/`, `oracles/`, `proxy/` and `core/`. Build manual: Croakus [§12](https://fawcettinnovations.com/magnum-croakus.html#12-validate-it-in-the-simulator), [§38b](https://fawcettinnovations.com/magnum-croakus.html#38b-the-simulator-is-a-shared-instrument), [§39](https://fawcettinnovations.com/magnum-croakus.html#39-never-change-a-live-mesh-blind), Appendix E. Findings: [the simulator page](https://fawcettinnovations.com/simulator.html).

[← §15 Reference Applications](README.md#s15) [Index](README.md) [§17 Known Limits →](spec-17-limits.md)
