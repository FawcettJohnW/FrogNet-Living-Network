# Phase 0 checkpoint — stock meshtasticd baseline

Date 2026-10-30. Everything below was measured against stock **meshtasticd 2.8.2.332b5b0**
(meshtastic/firmware 332b5b0a2c4f, Wed Oct 28 2026; protobufs 8d0ed0c135f9), Linux native build, no radio
(SimRadio, `Lora: Module: sim`), in a 2-core / 7 GB cloud workspace.

## 1. Stock build

| item | value |
|---|---|
| command | `pio run -e native` in a copy of the checkout (`fwb/`) |
| result | SUCCESS, 760 objects, 15 m 44 s, 0 compiler warnings |
| binary | `bin/meshtasticd-stock-2.8.2.332b5b0`, sha256 f5b19894511c…, reports `2.8.2.332b5b0` |
| source changes | none. Only `.ini` package URLs changed (`fwb-sourcing.patch`), see below |

Package sourcing. This workspace's proxy denies `api.registry.platformio.org` and GitHub archive zips;
GitHub git and PyPI are allowed. Same packages at the same pins, fetched by a different route:

- GitHub `…/archive/<ref>.zip` → `…/<repo>.git#<ref>` (114 refs, all .ini files).
- Registry names → GitHub tags: LovyanGFX 1.2.28, Adafruit seesaw 1.7.9, Melopero RV3028 1.2.0; transitive
  Adafruit ST7735/ST7789 1.12.0, SD 1.3.0 (both in `lib_ignore`, installed only to satisfy resolution).
- `platform-native` 86c62edf cloned and installed as a local platform; it pulls framework-portduino 0fdf803 by git.
- tool-scons 4.41101.0 = SCons 4.11.1 from PyPI. Unity `^2.6.1` = ThrowTheSwitch/Unity tag v2.6.1. Its
  `library.json` at that tag has a `#` comment header (invalid JSON) and says 2.6.0. The header was stripped
  and the version set to 2.6.1 in the local package copy only.

## 2. Oracles against stock

| oracle | what | result | wall |
|---|---|---|---|
| Python CLI unit tests (`pytest`, default markers) | client library, no firmware | 433 passed, 2 skipped | 20 s |
| Python CLI `smokevirt` | CLI against one stock sim node, fresh per test | **40/40 passed** | 282 s |
| Python CLI `smokemesh` | 3 stock sim nodes, chain A-B-C via SIMULATOR_APP bridge | **7/7 passed** | 205 s |
| meshtastic-mcp `scripts/ci_device_mesh_e2e.py` | 2 stock nodes over UDP multicast; inbound text loop | **PASS** | 46 s |
| meshtastic-mcp `tests/unit` | harness self-tests (no firmware) | 797 passed, 87 skipped | 62 s |
| RepeaterTastic interop vectors | 2 stock nodes, UDP multicast; capture + `verify_vectors.py` | **12/12 verified** | 151 s |
| firmware native C++ suites (`bin/run-tests.sh`, coverage env, ASan) | 88 suites | **GREEN 88/88, all CLEAN**; test attribution OK | 40 m 23 s |
| meshtastic-mcp hardware tiers (admin/mesh/telemetry/…) | device behaviour | **NOT RUN against stock** — see §5 | — |

Notes:
- RepeaterTastic, first attempt with their 10 s settle: 11/11 verified, but nodeinfo_broadcast was missing. The
  stock log shows `Skip send NodeInfo since we sent it <60s ago`: the capture's NodeInfo ping fell inside the
  60 s cooldown started by setup. With a 70 s settle, 12/12. `nodeinfo_reply` stays absent; RepeaterTastic documents it
  as best-effort and cites a 600 s reply throttle, but in 2.8.2 replies are held to a 30 min floor (congestion-scaled)
  and suppressed per requester for 12 h (`study-meshtastic-interactions.md` §4); 600 s is only the ham-mode broadcast
  interval. `position_reply` is absent as expected on 2.8 (NO_RESPONSE NAK instead).
- **Stock drift 2.8.0 → 2.8.2** (`rt-shape-diff-2.8.0-vs-2.8.2.txt`): of 12 vectors common to both, 11 have the
  same shape. In `text_broadcast_relayed`, the relayed copy's priority is HIGH in 2.8.2, RELIABLE in 2.8.0. Cause
  not established. Phase 1 matches the pinned stock (2.8.2), not the committed 2.8.0 vectors.

## 3. Coverage of the defined API (`coverage-map.md`, `coverage-map.json`)

Inventory from the pinned protobufs (`api-inventory.json`): ToRadio 6 variants, FromRadio 18, AdminMessage 58,
PortNum 41. Wire coverage = observed in loopback captures of each oracle run (`tools/apitap.py`, TCP 4403-4520 and
UDP 4403, every frame decoded, 0 decode errors). Native suites are counted statically (their source names the
item's nanopb tag or enum: touches, not necessarily asserts).

| section | items | any wire oracle | native suites (static) | no oracle |
|---|---|---|---|---|
| ToRadio | 6 | 3 | 3 | 3 — disconnect, xmodemPacket, mqttClientProxyMessage |
| FromRadio | 18 | 12 | 13 | 5 — log_record, rebooted, xmodemPacket, mqttClientProxyMessage, lockdown_status |
| AdminMessage | 58 | 9 | 24 | 32 |
| PortNum | 41 | 8 | 18 | 22 |

Observed limits of the existing tests:
- `test_smokevirt_factory_reset` sends `--set factory_reset true`; on the wire it is a `set_config`, and the test
  asserts only rc=0. No oracle sends factory_reset_device / factory_reset_config.
- smokemesh uses `-s` (force SimRadio), which per RepeaterTastic's notes also disables PKI in Router::perhapsEncode;
  its DM tests do not exercise PKI. RepeaterTastic's do (dm_text, telemetry are PKI on the wire).

## 4. Stock baseline numbers (`baseline-stock-2.8.2.332b5b0.json`, `srcsize-stock-2.8.2.332b5b0.json`)

Over the air, per exchange (RepeaterTastic capture, 2 nodes). LoRa bytes = 16-byte radio header + encrypted
payload. LongFast airtime computed (SF11, BW 250 kHz, CR 4/5, 16-symbol preamble, CRC on).

| exchange | transmissions | LoRa bytes | LongFast airtime |
|---|---|---|---|
| text broadcast (original + 1 relay) | 2 | 106 | 1282 ms |
| DM want_ack, PKI (DM + ACK + zero-hop ACK) | 3 | 136 | 1800 ms |
| position request → NAK | 2 | 61 | 954 ms |
| telemetry request → reply, PKI | 2 | 98 | 1200 ms |
| traceroute request → reply | 2 | 52 | 873 ms |
| nodeinfo broadcast | 1 | 105 | 1051 ms |

Client API connection setup, want_config_id → config_complete_id (stock dump):

| capture | dumps | frames (median) | bytes (median) | ms min / median / max |
|---|---|---|---|---|
| mcp e2e | 7 | 48 | 1277 | 2.4 / 4.9 / 5.7 |
| RepeaterTastic | 6 | 48 | 1299 | 4.3 / 5.2 / 6.8 |
| smokevirt | 236 | 46 | 1137 | 2.0 / 67.4 / 234.0 |
| smokemesh | 84 | 49 | 1330 | 2.8 / 46.1 / 201.8 |

Source size, cloc code lines, partition by directory (rules in the JSON):

| scope | application | core | board support | generated protobuf |
|---|---|---|---|---|
| whole src + variants | 85,709 | 25,461 | 61,785 | 8,566 |
| compiled into native meshtasticd | 72,751 | 23,775 | 28,395 | 8,566 |

## 5. Gate status

Gate: every oracle runs against stock.

- Met: Python CLI (unit, smokevirt, smokemesh), mcp sim e2e, RepeaterTastic, native C++ suites (88/88).
- **Not met: meshtastic-mcp hardware tiers.** They find devices by USB VID / hub slot; as shipped they cannot target a
  TCP node (answers the proposal's open question: USB-attached hardware only). Stock nodes were provisioned through
  the client API to the harness's session profile and verified (region US, LONG_FAST, slot 88, channel McpTest,
  seeded PSK). A VID shim on `list_devices` was tried and withdrawn on direction: discovery is a read of shared RAM,
  each participant advertising itself. Pending the RAM host and docs (§6).

## 6. Open

- RAM-based participant discovery for the bench, then the mcp tiers against stock. Blocked: the only RAM host in
  this workspace is the vendored ribbit v0.63 from the PyTorch example; it has no session-bound families (an
  advertisement would outlive a dead node) and its client carries the coalescing/cache patches. Needs the current
  ribbit tree.
- Open item in `DESIGN-meshtastic-on-ribbit-v1.md`: the incumbent tests assert RF topology (traceroute route via relay,
  hopLimit=0 reach). Decision needed before Phase 1 design is final.
- Conversion design review (DESIGN.md truth table) against CONVERTING-TO-RIBBIT.md / best practices — not in this
  workspace.

## Files

`api-inventory.json` `coverage-map.{md,json}` `cov/*.json` `baseline-stock-2.8.2.332b5b0.json`
`srcsize-stock-2.8.2.332b5b0.json` `rt-vectors/` `rt-shape-diff-2.8.0-vs-2.8.2.txt` `runs.tsv` `logs/` `pcap/`
`fwb-sourcing.patch`; tools: `apitap.py` `covmap.py` `baseline.py` `srcsize.py` `rt-shape-diff.py` `run-oracle.sh`
`rt-oracle.sh` `rt-host.sh` `build-native.sh` `mcp_sim_provision.py`.
