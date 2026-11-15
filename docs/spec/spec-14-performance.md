<!-- Generated from the site page spec-14-performance.html (https://fawcettinnovations.com/spec-14-performance.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §14

# Performance and adaptation.

Two adaptations, two mechanisms, and they MUST NOT be conflated. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

The ladder, on camera

## Current implementation clauses: October 12, 2026

## Discovery pipeline

The r15 namespace address-change experiment measured about 80 ms end to end on a LAN. Region publication and world reads add WAN RTT. Different steady-event medians in r18/r19 were recorded as environment variance, not a new speed regression.

## Liveness

Silent death measured around four seconds with the newer C++/Kotlin settings in the namespace comparison. Higher random loss produced more false deaths. Hardware timing and battery cost remain distinct questions.

## Media

The current call reports 1280×720 at 20 fps. The earlier roughly 121 Kbps result is 160×120 at 24 fps. Video adapts and audio gets priority; no universal resolution-at-bandwidth promise follows.

## Compute and state

FrogTorch’s DDP ratios, Redis operation rates, topology-change times and memory write rates come from independent campaigns. Their denominators and unfavorable controls remain attached to the evidence.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; where they describe the broker path, that path was removed in the October 2026 cleanup (docs/cleanup-2026-10/).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Two adaptations, two mechanisms](#a1)
2.  [1aHow to measure this](#p1a)
3.  [1a.1What the recommended pattern costs](#p1a2)
4.  [1bObservability tooling](#p1b)
5.  [2What may be claimed for the codec](#a2)
6.  [2aThe SotF ladder controller](#p2a)
7.  [3The media ladder](#a3)
8.  [4Rate derivation](#a4)
9.  [5One stream, one rate](#a5)
10. [6Publishing figures](#a6)
11. [7References](#a7)

## 1. Two adaptations, two mechanisms

Croakus: [§26](https://fawcettinnovations.com/magnum-croakus.html#26-the-accelerant-semantic-compression), [§49](https://fawcettinnovations.com/magnum-croakus.html#49-evidence-not-arithmetic), [§50](https://fawcettinnovations.com/magnum-croakus.html#50-how-the-ladder-actually-decides)

FrogNet adapts in two independent ways and an implementation MUST NOT conflate them.

|            | Semantic adaptation           | Media adaptation                           |
|------------|-------------------------------|--------------------------------------------|
| Applies to | Every exchange                | The media plane only (SotF)                |
| Mechanism  | Send only what changed (§11)  | Trade fidelity for continuity (§14.3)      |
| Trigger    | Next-hop RTT, per hop (§11.5) | What participants publish about themselves |
| Saving is  | Structural                    | Deliberate degradation                     |

## 1a. How to measure this, and why the usual number is not the number

**In REST the cost is the cost, and it is constant. In FrogNet the cost is the delta, and it is both compressed and variable.**

**The machine-level question is operations per second against shared memory, not instructions per second on any node.** Nodes already execute independently and concurrently; adding one adds computation. What they share is the store, so the rate at which it services operations is what bounds the machine (§1a, §17.3). An implementation reporting performance SHOULD report that rate and the workload mix that produced it, and MUST NOT present a single node’s throughput as a property of the network.

Every conventional throughput metric assumes the first of those. Operations per second is a useful number when an operation has a price; it is a category error when the price is a function of what changed since the last one. So the obvious request — *what is your writes per second?* — has an answer, and the answer is close to meaningless here. An implementation MAY report one; a reader SHOULD NOT weigh it heavily.

**The cost of an operation is not fixed, by design.** An unchanged exchange collapses to twenty-one bytes (§11.10). A changed one carries a difference against a template both ends already hold. Only a first exchange of a given shape carries a full body. So a throughput figure measures how many of each kind happened to occur in that run, and a benchmark that holds the payload constant is measuring the case the architecture exists to avoid.

**Fan-in compounds it.** Duplicate concurrent work collapses to one origin execution answering every waiting requester (§11.12), so requests-per-second and origin-executions-per-second diverge under exactly the conditions that matter, and the ratio between them depends on the arrival pattern rather than on the machine.

**What is meaningful is comparative, and it is run by the reader.** The reference implementation ships `pipe_workload.py` — the tool that produced the published ratios, so the figures on this site and the figures a reader obtains come from the same harness. It drives that harness against six payload shapes — a ~50-byte echo, 500 KB HTML with six dynamic fields, ~1 MB JSON with six and ~500 KB JSON with five, a fifty-message rolling chat window, and a two-hundred-sensor telemetry array — in four phases: direct to the origin with FrogNet out of the path, serial through FrogNet with SAME/DIFF active, a concurrency ramp, and a simultaneous burst.

``` code
python3 pipe_workload.py <target_ip> <payload> [requests]
  payload: echo | kb | mb | json-kb | chat | telem
```

It reaches a tester as part of the ordinary install: the release tarball built by `frognet_build_release.sh` carries a filesystem snapshot that includes it, so no separate step is needed to obtain the benchmark.

Phase 1 is the control and it is reported, not omitted. Serial FrogNet throughput is **similar to the control**, which the tool says in its own output; the gain appears in the ramp. An implementation reporting only the ramp figure without the control is reporting half a measurement.

**Run it on your own hardware, against your own payload shapes, and compare.** Against the figures published here, against another node on your network, against the same box with FrogNet out of the path. Those comparisons are stable. An absolute number lifted from someone else’s run is not, because it is dominated by which mix of SAME, DIFF and FULL that run happened to produce.

## 1a.1 What the recommended pattern costs

§10.3b tells an application to let each producer publish independently and have consumers read the set. The obvious objection is cost, and the numbers answer it.

An unchanged exchange is a twenty-one byte frame, twenty-five on the wire out, twenty-nine back, fifty-four for the round trip (§11.10) — **independent of the size of the value and of how many values are unchanged**. `get_all` is one exchange for a whole service, not one per value, so the reader pays for what moved plus a single constant — not a constant per tuple. The published ratios (§14.1a) put that at roughly ten times better than the conventional shape for churning telemetry and over a hundred times for a mostly-static set.

**An implementation SHOULD NOT introduce change-notification machinery to avoid a polling cost that has already been removed.** The reason polling is conventionally expensive is that each poll carries the answer; here an unchanged poll carries a proof that the answer is unchanged. That is the whole difference, and it is why observation can be left to the reader (§10.3a) without imposing a bandwidth penalty for doing so.

## 1b. Observability tooling reports on a shape that is not there

§14.1a covers throughput. The same reasoning governs the instruments an operator will reach for next, and for the same reason: each was built for request/reply over HTTP, and each will produce a number rather than an error.

| Instrument                                | What it reports here                                                                                                                                                                                                                                                                                                           |
|-------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| APM and distributed tracing               | A trace follows a request from caller to callee. There is no callee: a producer writes where it computes a value and a consumer reads where it needs one, with no causal link between the two acts and no correlation identifier joining them. A tracer will render two unrelated spans and no path, because there is no path. |
| Request-rate dashboards                   | Requests per second counts exchanges, and an unchanged exchange is twenty-one bytes (§11.10). A rising request rate on this system is frequently a *fall* in work done. The metric moves opposite to the quantity the operator believes it is watching.                                                                        |
| Latency percentiles over a mixed workload | SAME, DIFF and FULL are different operations with different costs (§14.1a). A p99 across all three describes a distribution that is a property of the workload mix on that run, not of the system.                                                                                                                             |
| Bytes-on-the-wire alarms                  | Falling traffic is the normal signal of a healthy converged link, not of a stalled one. A threshold alarm calibrated on a REST deployment fires on success.                                                                                                                                                                    |
| Endpoint availability probes              | There is no endpoint to poll. A probe that reports a service up or down is reporting on a host, not on the availability of a value; the value is present or absent, and absence is a normal state rather than an outage (§13.1).                                                                                               |

**An operator MUST calibrate against this system rather than importing thresholds.** Every one of these instruments will return a plausible number, and a plausible number from an instrument measuring the wrong thing is worse than a failure, which at least announces itself.

What is meaningful is stated in §14.1a: the same harness run on two machines and compared. What each node believes about itself is in its own capability and status tuples, readable with `get_all` like anything else — the system reports on itself through its own memory, not through an instrumentation sidecar.

## 2. What may be claimed for the codec

The saving from BLDC-1 is structural, not statistical: the payload is not sent because both ends already hold it (§11.10). **An implementation MUST NOT quote a compression ratio for the codec itself.** A ratio describes a workload, not a mechanism, and the same mechanism yields any ratio depending on how much of the traffic is repetition.

Where measured figures are published they MUST carry their workload and their conditions, and the set MUST include a negative control — a workload the mechanism does badly on — or the figures describe only the cases chosen to flatter it.

Latency figures MUST be published alongside compression figures for the same run. Compression that costs round-trip time has moved a cost rather than removed one, and a reader is entitled to see both from one measurement.

## 2a. The SotF ladder controller

SotF — **Song of the Frogs** — is the adaptive media protocol (§15). The ladder is a small set of named rungs, each a smaller promise than the one above; the source sends the highest rung the link sustains. Where most stacks offer a dial, this offers a staircase.

**Down is cheap and fast. Up is expensive and slow. That asymmetry is the design**, and an implementation that makes the two symmetric will oscillate.

### 2a.1 Stepping down — any one of four

The bearer steps down immediately on any of these, evaluated in this order:

1.  Audio shed by the wire.
2.  More than three consecutive samples carrying drops, or a backlog of eight frames.
3.  More than five drops within a one-second window.
4.  More than three consecutive samples below the frame-rate floor, which is **the greater of ten frames per second and seventy-five percent of the target rate**.

**Both a run count and a rate are required; neither alone is sufficient.** The controller is sampled once per frame — roughly twenty-three times a second — and the drop count is drained on each read, so ten drops in a second arrive as ten dirty samples scattered among thirteen clean ones. A pure consecutive-run rule finds runs of both inside one second and steps nowhere. Measured on hardware: a sustained ten to thirteen drops per second left the ladder oscillating between two rungs for minutes without ever walking down.

**The frame-rate floor MUST be relative, not absolute.** An absolute floor leaves a dead band: a node producing twelve frames against a twenty-four frame target is above ten, so nothing steps down, and far below full rate, so nothing steps up. It holds the top rung at half rate indefinitely, sheds nothing, and reads healthy in every counter. Observed.

### 2a.2 Stepping up — both conditions, or neither

An upgrade requires **three consecutive whole seconds**, each closing with zero drops and holding full frame rate throughout. A second failing either test zeroes the streak.

That is necessary and not sufficient. A rung tried and not held goes on a hold-off before it may be tried again: **fifteen seconds on first failure, doubling on each subsequent failure to a ceiling of five minutes**, cleared only after a full minute clean at that rung. **The hold-off is per rung**, so a link may keep probing the step above one that failed while the failed rung waits.

Without the hold-off a marginal link spends its life one second inside a rung it cannot carry: from the streak counter’s view every attempt starts clean. **The backoff is what remembers.** Zero drops is the absence of evidence, not evidence of health.

## 3. The media ladder

SotF (§1.3, §15) trades fidelity for continuity as the link tightens. **Nine rungs**, higher index richer, recorded in `ribbit/examples/communicator/docs/FNAV-SPEC.md`:

``` code
idx  code  name       kind           needs camera
 8   L8    BULLFROG   video+audio    yes
 7   L7    CHORUS     video+audio    yes
 6   L6    ENSEMBLE   video+audio    yes
 5   L5    DUET       video+audio    yes
 4   L4    SOLO       audio          no   (needs mic)
 3   L3    VOICE      audio          no   (needs mic)
 2   L2    WHISPER    text           no
 1   L1    BEACON     text           no
 0   L0    PULSE      presence       no
```

**A rung names the tracks carried and the video size. It does not name an audio format, and an implementation MUST NOT infer one from a rung.** The Communicator's audio is 16 kHz mono Opus at 24 kb/s (`src/comms/audio.hpp`) on every rung that carries audio. Sample rate and channel count belong to the codex a call was opened with, not to the rung it is currently sending at.

**A video rung needs a camera and only a camera** \[VIDEO_DOES_NOT_NEED_A_MIC_V1\]. A machine with a camera and no microphone sends video with no audio bed — a stream with a note on it, not a disqualification — and the receiver is told there is no audio and plays what there is. Gating video rungs on a microphone made a camera-only box compute a text-only ceiling: a working webcam produced text presence.

The two lowest rungs are a floor and MUST NOT be removed from the available set.

**Adding a rung MUST extend the top, never renumber.** The index travels on the wire, so an existing index must keep its meaning. A peer built before a new top rung existed mislabels it in reporting and decodes the picture correctly, because the decoder reads geometry from the bitstream. The media server does read the index, to know an upload's size without decoding it.

## 4. Rate derivation

**Status: gone from the shipped Communicator.** The derivation this section specifies — one pure function every participant runs over the published rows, with the failed-rate ceiling held in a published row — is not in the C++ Communicator, and its tags \[ONE_DERIVATION_V1\] and \[A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1\] occur in no source file. The media server sets each viewer’s rate itself: the smaller of the viewer’s own cap (its `MediaSpeed` row, matched by identity) and what its link measurably delivers, with the size that just failed held off for a doubling interval in the server’s own state (`ribbit/examples/communicator/src/media/server.cpp`: `set_cap`, `retarget`, `control_tick`; `STATUS.md`: “per-viewer rate from the MEASURED link”). What follows is the design the derivation came from, kept as the record of why; the two rules it produced that survive in the server are §14.4.2 (`[ABSENCE_IS_NOT_A_MEASUREMENT_V1]`, `control_tick`) and the held-off failed size of §14.4.1.

Rate control is **two separable things**, and an implementation MUST keep them apart. Conflating them is what makes the purity requirement below look impossible to satisfy alongside the hold-downs in §14.4.3.

| Stage                 | Is                                               | May consult                                 |
|-----------------------|--------------------------------------------------|---------------------------------------------|
| **Target derivation** | What rate the published state implies, right now | The published rows, and nothing else        |
| **Adoption**          | Whether and when to move to that target          | Elapsed time and the currently adopted rate |

**Target derivation MUST be a pure function of published state**: no clock, no instance state, no I/O \[ONE_DERIVATION_V1\]. Every participant runs the same function over the same rows and lands on the same answer without being told, which is what makes agreement unnecessary rather than merely cheap (§10.6.2). Being pure is also what makes it testable in isolation.

**Adoption is a local policy** and MAY consult the clock, because it decides only when this participant moves — not what the answer is. Two participants applying different hold-downs converge on the same target at different moments; two participants deriving different targets do not converge at all. An implementation MUST NOT feed an adoption decision, or the currently adopted rate, back into the derivation (§10.6.2): a derivation that consults its own last answer is a controller with memory, and two controllers with memory diverge.

### 4.1 A rate that failed is not a candidate

\[A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1\] A rule that promotes whenever every participant is currently content will promote into a rate **already shown not to work**, become discontent, demote, become content, and promote again.

The evidence a consumer publishes MUST therefore be more than a present verdict. It is the geometry and frame rate that consumer is actually receiving, so that a report of struggling *at a size* establishes that the size does not work on that link. From which:

``` code
the ceiling is the smallest size at which anybody is struggling;
nothing may promote to or above it.
```

A derivation with no memory of what failed produced ninety-odd oscillations in five seconds in simulation. That memory MUST live in the published state as a row every participant derives the same ceiling from — not in a controller's private history, because two controllers with memory diverge (§10.6.2).

### 4.2 Absence is not a measurement

Zero frames arriving means the producer sent nothing. It does not mean this consumer is slow, and reading it as slowness is a ratchet: the producer sheds, the consumer reports worse, the rate drops, the producer sheds more (§10.6.3).

### 4.3 Hold-downs

Damping lives in adoption (§14.4), never in derivation. Rate changes MUST be damped in both directions, and asymmetrically: a slower hold before climbing than before dropping. Continuity is for the user, who must never be asked to intervene; a picture that thins has not failed. Visible failure is for the operator, who must never be lied to. An implementation that hides a degradation from its log is not transparent, merely quiet.

## 5. One stream, one rate

**Closed.** The limit this section once stated — the relay forwards one stream and bounds the call to the slowest participant — no longer holds: the C++ media server passes the upload through to viewers whose target is the upload’s size and transcodes once per smaller size wanted, so each viewer gets its own picture (`src/media/server.cpp` `fan_video`, `tx_worker`, `wanted_sizes`; `STATUS.md` test_room: one viewer at 160x120 while another sees 1280x720). Per-client adaptation may be claimed for the C++ Communicator (§17.10). The Python Communicator’s relay was the one-stream relay; it is retired (`frognet_prune_removed.sh`, \[T2 2026-10-12\]: the mediahost role and the old Python Communicator’s remnants).

## 6. Publishing figures

Every published figure MUST carry its provenance, and figures of different provenance MUST NOT be presented as though they were the same kind of thing:

| Kind                  | Means                                                                                                 |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| Verified in source    | A constant read at its line                                                                           |
| Recorded run          | An instrumented measurement, with its workload stated                                                 |
| Read off a diagnostic | Observed on camera, not from an instrument log — publish a bound, not a point figure                  |
| Estimate              | A did-versus-would comparison against published third-party figures. MUST be labelled as an estimate. |

A figure whose provenance cannot be established MUST NOT be published. Where a check reports a rate above 100% or calls a figure sourced that is not, **the check is wrong, not the data** — fix the check.

## 7. References

Specification [§10.6](spec-10-unrest.md#u6), [§11](spec-11-wire.md), [§11a](spec-11a-proxy-daemon.md), [§15](README.md#s15), [§16](README.md#s16), [§17](spec-17-limits.md). Source: `opt/frognet_semantic/ribbit/examples/communicator/`: `src/fnav/bearer.cpp`, `src/media/server.cpp`, `src/comms/audio.hpp`, `docs/FNAV-SPEC.md`. Build manual: Croakus [§49](https://fawcettinnovations.com/magnum-croakus.html#49-evidence-not-arithmetic), [§50](https://fawcettinnovations.com/magnum-croakus.html#50-how-the-ladder-actually-decides), [§53](https://fawcettinnovations.com/magnum-croakus.html#53-per-client-rate-adaptation). Measured results: [the simulator](https://fawcettinnovations.com/simulator.html).

[← §13 Failure Semantics](spec-13-failure.md) [Index](README.md) [§15 Reference Applications →](README.md#s15)
