# Real-kernel topology tier: split, merge and RAM restart

Run with `tools/topology.py` where network namespaces are permitted. Three namespaces on one Linux
host, joined by a bridge, real TCP between them:

| Namespace | Address | Runs |
|---|---|---|
| ops | 10.77.0.10 | MongoDB, Mosquitto, converted Java backend (operator, JNI into API++) |
| ram | 10.77.0.20 | the RAM memory (`native/region_host.cpp`, test equipment) |
| dev | 10.77.0.30 | synthetic device participant under a supervisor (Restart=always, RestartSec=2) |
| root | 10.77.0.1 | the harness and an independent RAM probe session (never partitioned) |

A split is a silent nftables drop inside one namespace, both directions; heal deletes the rule. The
switch is ON throughout. Asserted in every scenario: **I1** no UPDATED history for a value the device
never held; **I2** no value physically applied twice by one device process; **I3** a returning device
applies only the latest desired value. Everything else is recorded as data.

## How merge works ([SESSION_END_ENDS_PROCESS_V1], [FRESH_SESSION_ON_END_V1])

A session that ends takes its writer's tuples with it, and nothing in `ribbit::Space` redials. So each
participant sees its own session end through a held read it owns, and takes a fresh one:

- **Device:** its desired-family loop is a `HeldRead` on its own thread. A failed read means the session
  is gone; the process ends ("a failed read ends the session ... the fresh one reads again", as
  fdf-client does). Its supervisor starts a fresh process 2 s later (`node/systemd/senz-device.service`,
  same settings as `fdf-client.service`), which announces its capability, writes its actual setting and
  reads the current desired state.
- **Operator (Java backend):** it keeps serving telemetry and everything else, so it reattaches in-process:
  one session thread holds a read on the space (new JNI `HeldRead`). When that read fails, the RAM path is
  off (submits fail visibly, never fall back to MQTT), a fresh session is taken 2 s later, the operator's
  own desired truths are written again, and the space is read from the start so every applied result is
  seen again. Completion is idempotent by its history marker.

## Results (three runs, all PASS; seconds from heal, runs 1/2/3)

| Scenario | What happened |
|---|---|
| S1 change across namespaces | Applied. |
| S2 device split 3 s (under the 5 s keepalive), change during the split | Session survived; applied ~0.4 s after heal, once. |
| S3 device split 15 s, change during the split | Capability gone ~5 s after the split. Change stayed pending, no false success. **Applied by a fresh device session 3.2 / 3.3 / 3.2 s after heal**, once, with no manual restart. |
| S4 two changes while the device is down | Only the latest applied; the intermediate never. |
| S5 operator split 15 s, change during the split | Desired tuples gone ~5 s after the split. A change submitted after that failed visibly ("RAM is unavailable; no MQTT fallback"). **The change made during the split was reasserted and applied 2.8 / 3.0 / 3.1 s after heal**; the next change worked. No restart. |
| S6 RAM memory restarted empty | **Device and operator back with their truths reasserted in 2.0 / 2.0 / 2.0 s**; next change applied. No restart of anything else. |
| S7 device split 60 s, change during the split | **Applied 14.8 / 15.8 s after heal** (runs 2/3), with no manual restart. |

Timings come from the harness's 50 ms observation loop; they are not latency measurements.

## Remaining finding: long outages rejoin slowly

S7 is slower than S3 because a fresh process started during the split blocks in `connect()`.
`frogram::dial` has no connect bound, so the kernel's SYN backoff (1, 2, 4, 8, 16, 32 s) decides when it
next tries; after a 60 s split that is up to ~16 s past heal. A bounded connect in `dial` (fail within the
same 5 s as the keepalive/user timeout, then the supervisor's 2 s retry) would bring long outages in line
with S3. That is a core frogram change and has not been made here.

Not covered: FrogNet discovery/region/RAM-role election (fixed RAM address here), WireGuard (kernel
module unavailable here), concurrent conflicting changes, multi-host WAN.
