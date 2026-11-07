# SenzMatica first slice: remote configuration

Selected 2026-10-28. Deliver device publication, an operator setting change and
device-reported applied state and a final MQTT completion publication, while
telemetry and other workloads continue through the original path on the same
devices. This demonstrates rolling conversion, followed by a paired two-machine
comparison.
This is the execution contract; implementation and measurements are pending.

## Original implementation to exercise

Backend source pin: `1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4`.
Frontend source pin: `15d55adedaa791a2f12a50704ce29bb940bbdcf1`.
Paths below are relative to their respective repositories.

| Step | Actual source |
|---|---|
| Operator submits configuration | DMS `src/components/sub-components/EditConfigureDevice/EditConfiguration.tsx`, `handleSaveClick`: builds `remoteConfigurations`, PUTs to `/core/device/deviceParameterConfiguration/{device}/user/{userId}`. Its success popup says message **sent**, not applied. |
| API dispatch | Core `core-lib/src/main/java/com/magma/core/api/CoreApi.java`, `updateDeviceParameterConfiguration`. |
| Validate field definitions and values | `core-data/src/main/java/com/magma/core/util/DeviceParameterConfigurationUtil.java`, `validateParametersInRemoteConfiguration`. |
| Select and send changes | `core-lib/src/main/java/com/magma/core/service/CoreService.java`, `updateDeviceParameterConfiguration`, `compareParameters`, `generateMessageForModifiedIds`, `splitMessage`. |
| MQTT transport | `core-lib/src/main/java/com/magma/core/configuration/MQTTConfiguration.java`. |
| Classify device response | `core-lib/src/main/java/com/magma/core/gateway/MQTTMessageHandler.java`, `identifyMessageType`, `handleConfigurationUpdate`. |
| Apply confirmed parameters to stored view | `CoreService.doHandleDeviceConfigurationUpdates`, `updateDeviceParameters`, `saveDeviceConfigurationHistory`. |

Start with an existing integer parameter in the String product format, whose
successful acknowledgment path is explicit in this source. The fixture's parameter
ID/value must be recorded, not presented as a known real customer's hardware
setting. Avoid IDs 97/98/99, which have special handling. Trace and validate the
fixture through the untouched running original before using it as an oracle.

The original compares existing matching parameter IDs; it does not discover new
parameters through that comparison. String payloads are retained, split into
nominal 100-character chunks, assigned numbered topics and recorded in MongoDB.
The success handler recovers the stored outbound payload using device/topic
number, updates the stored configuration, and deletes the pending message.

Source defects to preserve as findings rather than parity claims:

- Failure/partial-success phrases do not contain `rm-conf-successful`; the public
  classifier therefore does not route them to its configuration failure handler.
- The alternate long-form success phrase accepted inside the configuration handler
  also is not selected by the classifier on an otherwise ordinary topic.
- Success handling has no check that the message being applied is the newest
  operator intent. Exercise old and newer outstanding changes explicitly.

These are source findings. The working success path still requires a live
baseline run; source inspection is not a timing baseline.

## Converted state and ownership

Use the existing C++ API++ `TupleName` and JSON bags. Each tuple is owned by the
participant that states that truth. One designated operator writer per device
is sufficient for this slice; multi-operator arbitration is outside this slice.

| Truth | A | B | C | Writer |
|---|---|---|---|---|
| Device configuration capability | application/customer space | `capability\|<device-id>` | parameter ID | Device |
| Desired value | same | `desired-config\|<device-id>` | parameter ID | Operator |
| Applied value/result | same | `applied-config\|<device-id>` | parameter ID | Device |

The vertical bar is literal within B. A/B/C remain separate strings, not a
serialized path. Device identity is stable and independent of address/topology.
A capability bag describes the actual parameter definition. Desired bag contains
`generation` and `value`; applied bag contains the generation being reported,
actual read-back `value`, and `status` (`applied` or `failed`). Use an operator
incarnation plus increasing sequence for generation, so restart cannot reuse an
old generation. This is application correlation, not an acknowledgment queue.

The device publishes its capability and current applied setting, then holds reads
on its desired family. It validates and performs the setting operation, reads
back what was applied, and writes its result. The operator reads the applied
family. It displays success only when the report is applied, its generation
matches the current desired generation, and its actual value matches the desired
value. A failure or older generation remains visible without claiming completion
of a newer change. A duplicate observation of already-applied state causes no
additional physical operation. On reappearance, device publishes its actual state
and reads the current desired state.

Absent capability family means absent device for this workflow. The operator's
live desired tuple can remain while the device is absent. Do not derive current
presence from a retained applied record or add a separate connectivity registry.
No durable intent across the operator's death is claimed by this first slice;
operator lifetime/persistence must be explicit in the run manifest.

## Rolling conversion in the running system

1. Keep the original backend, broker, telemetry and other workloads running.
   Establish their working behavior and record it as the control.
2. Select the remote-configuration workload for migration. Route its desired
   changes into RAM; disable the original MQTT command route for those changes.
   Each change has one execution path. Unselected workloads retain their old path.
3. The device consumes desired RAM state, applies the setting and publishes its
   actual applied result to RAM, using the ownership/generation rules above.
4. At the end of this process, the converted workload publishes the completion
   through MQTT so existing consumers see the expected result. The same running
   system continues to show telemetry and configuration results.
5. Convert further workloads independently. Remove each compatibility publication
   when its remaining consumers read RAM. Retire shared services only when no
   remaining workload needs them.

The MQTT completion is a real integration step. Freeze its topic/payload and
consumer behavior from the runnable original before implementing it. The existing
success handler requires a pending Message identified by device/topic number;
a success string without that context cannot update the old view. Preserve only
the necessary correlation at this boundary, or adapt the consumer to the RAM
applied result. Do not send the configuration down the old command path as well.

Publish success only after the matching actual applied result. Failure remains
failure. Record MQTT publication/consumer failure separately from device apply
failure. Any retry repeats the completion publication, not the physical action.
Test duplicate publication against the consumer and handle it without a second
application or incorrect history. The final publication, retained correlation and
consumer adaptation are included in code accounting and end-to-end measurements.

## The switch

Remote configuration has one switch. **ON:** changes travel through the
distributed OS (desired/applied tuples in RAM). **OFF:** changes travel through
the original MQTT command path, untouched. Telemetry and every other workload
stay on MQTT in both positions.

- **The switch selects a path; it is not a fallback.** With the switch ON, a RAM
  failure is logged and the change stays pending/failed. It is never re-sent over
  MQTT. With the switch OFF, nothing touches RAM.
- **One switch, one place.** The operator side reads it at the moment a change is
  submitted. The device participant serves both paths at all times, so flipping
  the switch never requires touching the device.
- **Flippable live.** Flipping it while the system runs affects only changes
  submitted afterward. A change already in flight completes on the path it started
  on. Every change records which path executed it.
- **Exactly one execution per change in either position.** ON: no legacy MQTT
  configuration command is emitted. OFF: no desired tuple is written.
- **Logged state.** The switch position, and every flip with its time, is written
  to the log so a run states which path it measured without anyone running a
  command to find out.
- **Same view either way.** In both positions the result reaches the existing
  consumer/view, so an operator watching the UI sees the same outcome.

**One library.** Java reaches RAM through a JNI binding to the C++ API++ library,
built the way the Node binding wraps `RamClient`. There is no second client
implementation: no pure-Java wire code, no REST shim. The switch lives in
`CoreService.updateDeviceParameterConfiguration`; with it ON, that method writes
the desired tuple through the binding and the original MQTT send is not called.
The binding targets Java 8 and loads its native library inside Spring Boot 1.5;
the `.so` is built per machine architecture and ships in that machine's one
tarball. Binding code counts as converted application code in the accounting.

**API++ calls (library head `c74fc65`, `ribbit_pp.hpp`).**

| Who | Call | On |
|---|---|---|
| Both | `RibbitConnection::ConnectRibbit` / `GetSpace` | one session per participant |
| Device | `Capability::SetCapability` | (space, `capability\|<device-id>`, parameter ID) |
| Device | `Tuple::Watch` | (space, `desired-config\|<device-id>`, "") |
| Device | `Tuple::Set` | (space, `applied-config\|<device-id>`, parameter ID) |
| Operator (Java, via JNI) | `Tuple::Set` | (space, `desired-config\|<device-id>`, parameter ID) |
| Operator (Java, via JNI) | `Tuple::Watch` | (space, `applied-config\|<device-id>`, "") |
| Operator (Java, via JNI) | `Capability::WatchCapabilities` | (space, `capability\|<device-id>`, "") |

Presence is `WatchCapabilities`/`CheckCapability`, which report session loss as
gone. Not `Exists`: by contract, `Exists` counts an explicitly withdrawn
capability as present. The operator's clock stops in its `Watch` callback on the
matching applied row, so the converted timing boundary is a held read, never a poll.

JNI notes: `Watch` callbacks run on the library's own pool threads, so the binding
attaches each to the JVM and holds global references to the Java callback. Watches
have no cancel API and live as long as their session, so the Java side keeps one
connection for the backend's lifetime and disconnects only at shutdown.

The paired measurements are this switch: control trials run OFF, converted trials
run ON, on the same running system, machines, link and workloads.

## Acceptance runs

| Case | Required observation |
|---|---|
| Device publishes | Correct deterministic family is visible with current actual setting. |
| Normal change | Device applies supported value; operator observes matching generation/value. |
| Invalid setting | Validation rejects it; actual setting remains unchanged; no success. |
| Device rejects operation | Device reports failed with actual value; operator remains unsatisfied. |
| Duplicate desired observation | No repeated physical operation for unchanged applied state. |
| Change while absent | No false completion; when device returns it reads and applies current intent. |
| Two changes during absence | Returning device acts on current intent; old report cannot satisfy it. |
| Old report after newer desired | New intent remains pending until its own matching applied result. |
| Device disappears | Its live capability/applied family disappears; desired state is not evidence of presence. |
| Foreign device/customer | Its report cannot satisfy this device/customer's desired setting. |
| Malformed report | Rejected visibly; no crash and no success. |
| Coexisting workloads | Telemetry and other unconverted workloads keep producing their expected results during RAM configuration changes. |
| Single selected path | A converted change emits no legacy MQTT configuration command and causes one physical application. |
| MQTT completion | Matching applied result produces the expected MQTT publication and existing consumer/view update. |
| MQTT unavailable after apply | Applied truth stays correct; compatibility delivery is visibly pending/failed; no repeated physical action. |
| Repeated MQTT completion | Existing consumers do not misapply state or duplicate history; setting is not applied again. |
| Switch OFF | Change travels the original MQTT command path; no desired tuple is written; view updates as in the original. |
| Switch ON | Change travels RAM; no legacy MQTT command is emitted; view updates through the completion publication. |
| Flip during run | Changes submitted before the flip finish on their original path; changes after it take the new path; each records its path. |
| RAM unavailable, switch ON | Change is logged pending/failed; it is not re-sent over MQTT. |

Mutation controls must bypass generation matching, accept failed status, accept
wrong read-back value, and repeat physical application of identical state. Each
must fail a behavior assertion. Topology/role transitions use the real simulator
path described in the parent plan; manually swapping stores is not proof of it.

## Two-machine measurements

Machine A runs the operator, original backend/broker/database and RAM host.
Machine B runs the device participant: converted remote configuration through
RAM, with other workloads still on MQTT. For the control trials the switch is OFF,
so configuration also uses the original path. Keep the same other workloads active in both modes. Use the same machines/link, field definitions, initial values,
requested changes, device apply/read-back operation, resource limits and capture
boundary. Record machine identities, versions, CPU/OS, placement, link settings,
source commits and all commands. Distinct processes on one host do not qualify.

The public repository supplies server code, not established device firmware. A
protocol participant on B must therefore be labeled a device test participant.
Its chosen apply/read-back action and its source are part of the measurement.
Do not attribute its implementation or timing to SenzMate firmware.

1. Warm both paths with the same 100 excluded changes. Run at least three measured
   trials of 1,000 sequential alternating valid changes. Report trial results and
   pooled distributions, sample counts, failures and timeouts. Load tests follow
   after this baseline works.
2. On A, start a monotonic timer immediately before submitting desired state
   (baseline PUT versus converted Set). Stop on A only when the operator observes
   the matching device-applied result. Also time completion through the final
   MQTT publication and the existing consumer/view update. The control uses its
   corresponding applied/view completion. Label both timing boundaries. Separate
   send/accepted feedback from completion. These timings use A's clock throughout.
3. Capture both directions at B's physical network interface, restricting to the
   measured peer/ports, with identical capture placement and offload settings.
   Count captured original packet lengths including retransmissions and protocol
   traffic. Account for the final MQTT completion and consumer traffic as well
   as RAM traffic; separate configuration traffic from ongoing telemetry where
   identifiable, and report total coexistence traffic. Report the layer/boundary
   and idle traffic separately; do not subtract
   idle traffic silently. Bytes per change = measured bytes / successful changes,
   with failures also disclosed. Application payload length is not wire bytes.
4. Publish median, p95 and p99 using a documented percentile rule, raw per-change
   timings, capture files and packet/byte counts. Keep failure/recovery cases
   separate from healthy-operation percentiles. Never discard timeouts as though
   they were successful fast operations.
5. Record a live run on the same two machines: device publication, valid operator
   change, matching applied report, absent-device change and stale-report test.
   Keep MQTT, the Java backend and other device workloads running. Show the RAM
   configuration result arriving through MQTT in the existing view while
   telemetry continues. Verify the selected configuration did not also travel
   through the old MQTT command path.

The first results table is deliberately unfilled until these runs exist:

| Path | Successful / attempted | Median ms | p95 ms | p99 ms | Wire bytes/change | Application code remaining | Application code removed |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original public DMS | pending | — | — | — | — | pending | 0 |
| Converted C++/Ribbit + MQTT completion | pending | — | — | — | — | pending | pending |

Publish a second latency set for end-to-end completion through the existing
consumer/view; the first table's desired-to-applied timing alone does not include
the entire compatibility boundary. Keep both boundaries clearly labeled.

Code accounting uses a checked-in source manifest and explicit method spans for
mixed classes such as CoreService/MQTTMessageHandler. Count executable nonblank,
noncomment application lines consistently, including new validation/generation/
UI-binding code. Count retained domain code and removed transport bookkeeping
separately; list reused platform/library code as reused, never as eliminated.
Only count code actually absent from the converted execution path; the original
source remains intact as the control. Do not count an entire shared handler,
backend, frontend or firmware implementation as removed for one converted slice.

## Execution state

2026-10-28: source trace and this contract completed. Maven 3.9.9 was downloaded
and starts under workspace Java 17. The untouched original build fails before
compilation: direct Maven resolution reports DNS failure, and a configured
workspace HTTP proxy reports connection refused. Curl can retrieve the parent POM,
so this is a build-tool connectivity blocker, not evidence the artifact is absent.
See `evidence/remote-configuration/baseline-build-attempt.log`.

No original server, converted application, simulator acceptance suite, paired
benchmark or two-machine demonstration has run for this slice. MongoDB, VerneMQ
and a Java 8 runtime are not provisioned here. No second machine target is supplied.
Next gate: resolve original build/deployment and run its successful configuration
fixture; then implement the converted slice against that oracle. Do not replace
that gate with an invented MQTT baseline.
