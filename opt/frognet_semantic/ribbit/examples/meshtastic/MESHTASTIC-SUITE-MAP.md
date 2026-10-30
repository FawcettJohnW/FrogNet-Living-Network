# MESHTASTIC-SUITE-MAP — the incumbent's tests, test by test

Incumbent: meshtastic/firmware 332b5b0 (2.8.2.332b5b0), protobufs 8d0ed0c; Python CLI meshtastic/python 0a18357;
RepeaterTastic 768a030. Stock results: all green (Phase 0, PHASE0-CHECKPOINT.md).

Part = which part of the incumbent the suite exercises, against DESIGN.md: plumbing (moves packets between machines or repairs
that movement: replaced), NodeDB copy (removed), service / client API / platform (kept). **Phase 1 runs every suite unmodified;
all apply.** The Phase 2 column says what the greenfield build does with it. Tests are never modified to pass.

## Native C++ suites (`bin/run-tests.sh`, coverage env, ASan) — 88 suites, 1,707 RUN_TESTs, stock GREEN 88/88

| suite | tests | part | Phase 2 | what it covers (from its header) |
|---|---|---|---|---|
| test_ack_proof | 15 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Act as this node for subsequent crypto calls. |
| test_admin_keys | 15 | service (kept) | applies | Unit tests for AdminKeys::count keyAt isAuthorized in src mesh AdminKeys.cpp, and for the admin-key base64 codec (adminKeyFromBase64 adminKeyToBase64) in src platform portduino PortduinoGlue.cpp. |
| test_admin_radio | 118 | service (kept) | applies | Tests for the radio configuration validation and clamping functions introduced in the radio_interface_cherrypick branch. Targets: 1. getRegion() 2. RadioInterface::validateConfigRegion() 3. RadioInterface::validateConfig |
| test_admin_session_repro | 25 | service (kept) | applies | Deterministic reproduction of the admin session-key behavior discussed on PR #10669 (ndoo's "Admin message without session_key!" report), plus the remote-vs-local redaction of secret material in admin GET responses, plus |
| test_airtime | 68 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for src airtime.{h,cpp} - AirTime::syncNow() and its rolling windows. syncNow() replaced a per-second runOnce() tick with monotonic-uptime bucket rotation so windows stay correct across light sleep. It now tak |
| test_atak | 11 | service (kept) | applies | Test normal string without embedded nulls Should behave the same as strlen() for regular strings |
| test_banner_font_tags | 9 | platform / utility (kept) | applies | Regression tests for the alert-banner font-tag pipeline ([S] [M] [L] line prefixes). The BLE pairing banner (src platform nrf52 NRF52Bluetooth.cpp) sends "Bluetooth\nPIN\n[M]<pin>" with notification type pairing_pin. The |
| test_bme680_iaq | 15 | platform / utility (kept) | applies | The estimator is pure math with no platform dependencies, so this suite has no feature guard: it runs everywhere the native tests run. |
| test_breakout | 6 | platform / utility (kept) | applies | Pure-logic tests for BreakoutGame: initial serve brick state, paddle clamping, the ball waiting on the paddle until launched, brick-clearing on a straight-up serve, and the ball staying within the board. No device global |
| test_channel_keys | 28 | service (kept) | applies | Channel key derivation and hash layer: getKey() PSK expansion, generateHash() golden values, onConfigChanged() primary restore, setChannel() demotion, and perhapsDecode()'s hash fall-through. |
| test_chirpy | 5 | platform / utility (kept) | applies | Pure-logic tests for ChirpyRunnerGame: initial state, jump lifts Chirpy off the ground, obstacles spawn and a collision ends the run, and a dead game is inert. No device globals. |
| test_crypto | 31 | service (kept) | applies | trunk-ignore-all(gitleaks): These are dummy values. Not real secrets. |
| test_default | 20 | service (kept) | applies | Unit tests for Default::getConfiguredOrDefaultMsScaled |
| test_dmshell_recovery | 47 | service (kept) | applies | Unit tests for DMShellRxWindow and DMShellTxHistoryWindow in src modules DMShellRecovery.h - the sequence tracking and replay bookkeeping behind DMShell's retransmission scheme. Two contracts are pinned here, and both ex |
| test_event_channel_phone_api | 4 | client API (kept) | applies | Where a coordinate packet the phone aimed at the event channel actually goes once a channel carries positions. |
| test_event_channel_router | 11 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | The reply path needs the module, a service to send through, a routing module for the response hop limit, and a fix of our own. Scoped to the one test so the rest of the suite stays module-free. |
| test_event_profile_storage | 7 | service (kept) | applies | Include BEFORE TestUtil.h |
| test_firmware_edition | 1 | platform / utility (kept) | applies | devicestate.my_node survives a firmware reinstall, so a vanilla build (no USERPREFS_FIRMWARE_EDITION) must reset a persisted event edition at boot. |
| test_fscommon_getfiles | 8 | platform / utility (kept) | applies | Tests for getFiles() - the bounded file-manifest walk in src FSCommon.cpp that PhoneAPI's STATE_SEND_FILEMANIFEST drives on every phone sync. Nothing else asserts its bounding behaviour: the walk does run unasserted from |
| test_fuzz_config | 4 | service (kept) | applies | Adversarial fuzzing of `meshtasticd --check`, above all DuplicateKeyFinder, whose stack balance rests on yaml-cpp emitting matched start end events. The contract is crash-freedom and termination only (what the report say |
| test_fuzz_decode | 8 | service (kept) | applies | protoish= |
| test_fuzz_emotes | 2 | service (kept) | applies | Adversarial fuzzing of the emote UTF-8 width+truncation walkers - the render path a TEXT_MESSAGE payload reaches AFTER decode. Text payloads are opaque protobuf `bytes`, so PB_VALIDATE_UTF8 never screens them: invalid UT |
| test_fuzz_packets | 10 | service (kept) | applies | Adversarial fuzzing of the packet path - the decrypt decode dispatch pipeline that a crafted RF packet flows through, plus the phone-forward validation gate. Unlike test test_fuzz_decode (pure functions, no fixture) thes |
| test_geocoord_distance | 7 | platform / utility (kept) | applies | Deliberately does NOT include TestUtil.h. This suite is pure-function - no NodeDB, no router, no sockets, no PKC - so the harness-wide guards there (no listening sockets, force_simradio clear) would assert conditions it  |
| test_geofence | 26 | service (kept) | applies | These cover pure geofence helpers without device globals or a fake clock. Store and notification plumbing are not covered here. |
| test_gps_fix_hold | 12 | platform / utility (kept) | applies | Unit tests for shouldArmFixHold() fixHoldInForce() in src gps GPS.cpp - the post-lock ephemeris hold. In power-saving mode (gps_update_interval above GPS_UPDATE_ALWAYS_ON_THRESHOLD_MS) the GPS holds |
| test_gps_update_scheduling | 13 | platform / utility (kept) | applies | Confirms gpsHardsleepThresholdMs()'s pow()-free lookup table tracks the original `2750 pow(seconds, 1.22)` curve closely. |
| test_hop_scaling | 24 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for HopScalingModule in src modules HopScalingModule.{h,cpp} - the sampled hop histogram and the hop limit it recommends for this node's own routine broadcasts. What is pinned: |
| test_hop_start_policy | 14 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Include BEFORE TestUtil.h (provides NodeNum, isFromUs) |
| test_http_content_handler | 1 | platform / utility (kept) | applies | Required by Unity: PlatformIO's weak defaults do not link on MinGW (PE-COFF weak externals). |
| test_key_verification | 2 | service (kept) | applies | Tests for KeyVerificationModule's refusal to run in licensed (ham) mode. Pinned: a licensed node starts no key verification session - neither one the user asks for (sendInitialRequest, reached from the admin INITIATE_VER |
| test_low_battery_shutdown | 7 | platform / utility (kept) | applies | Unit tests for updateLowVoltageCounter() in src Power.cpp - the gate on the low-battery deep sleep. Power::readPowerStatus() calls this once per Power thread cycle (20s) with the freshly probed battery state. When it ret |
| test_mac_from_string | 13 | platform / utility (kept) | applies | Unit tests for MAC_from_string in src platform portduino PortduinoGlue.cpp. Regression coverage for when the function stripped colons from its mac_str parameter but then read bytes from the global |
| test_mem_audit | 7 | platform / utility (kept) | applies | Unit tests for the per-subsystem heap accounting registry - src memory MemAudit.cpp. Covers add set arithmetic, snapshot, tag reuse (pointer and strcmp fallback), unknown null tags and table-full behavior. The registry i |
| test_mesh_beacon | 82 | service (kept) | applies | Unit tests for MeshBeaconModule: - AdminModule::handleSetModuleConfig validation (invalid valid inputs) - MeshBeaconBroadcastModule payload cache lifecycle - MeshBeaconBroadcastModule::sendBeacon sends a correctly formed |
| test_mesh_module | 36 | service (kept) | applies | hopLimit= |
| test_meshpacket_queue | 5 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for MeshPacketQueue::replaceLowerPriorityPacket()'s late-packet branch - the one that evicts an overdue packet from a full queue to make room for a new arrival. tx_after is an absolute millis() deadline, so ev |
| test_meshpacket_serializer | 23 | platform / utility (kept) | applies | Deliberately does NOT include TestUtil.h. This suite is pure-function - no NodeDB, no router, no sockets, no PKC - so the harness-wide guards there (no listening sockets, force_simradio clear) would assert conditions it  |
| test_module_config | 3 | service (kept) | applies | AdminModule module-config lifecycle coverage: every submessage must survive set -> save -> load -> get. Motivated by firmware#11182 Meshtastic-Android#6430 (TAK team role never persisted): a submessage with no case in ha |
| test_mqtt | 77 | service (kept) | applies | precision= |
| test_muted_source | 14 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | isMutedForPacket() source resolution - src mesh Channels.cpp. A DM addressed to us reads the sender's mute bit; every other packet reads the mute bit of the channel it arrived on. |
| test_nexthop_routing | 52 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | favorite= |
| test_nmea_wpl | 6 | platform / utility (kept) | applies |  |
| test_nodedb_blocked | 13 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | withUser= |
| test_nodedb_boot_recovery | 10 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | NodeDB boot-recovery contract: an undecodable config.proto must freeze identity (no keygen, no overwrite), an absent one takes the fresh-install path, and a corrupt nodes.proto does neither. The tests are a ladder (state |
| test_nodedb_identity_hygiene | 21 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | keySeed= |
| test_nodedb_legacy_migration | 10 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | The one-shot v24 -> v25 NodeDatabase migration every 2.7 -> 2.8 upgrader runs: each test hand-encodes a legacy prefs nodes.proto, cold-boots a real NodeDB, and asserts the migrated state (including sanitizeUtf8 of legacy |
| test_nodedb_lora_slot | 20 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | The "heard on the current LoRa config" mark - src mesh NodeDB.cpp and src mesh TypeConversions.cpp. Each node stores the slot it was last heard on; NodeInfo.heard_on_current_lora is that matching the slot the radio is co |
| test_nodedb_save_retry | 9 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | NodeDB save-failure contract, NodeDB::saveProto() and NodeDB::saveToDisk() in src mesh NodeDB.cpp. saveProto() must report a write that did not land. SafeFile::close() verifies the .tmp by hash and renames it over the li |
| test_nodedb_v25_roundtrip | 9 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | channelIndex= |
| test_nodeinfo_send_window | 12 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | shorterTimeout= |
| test_notified_worker_thread | 3 | platform / utility (kept) | applies | Unit tests for NotifiedWorkerThread::checkNotificationExcept() (src concurrency NotifiedWorkerThread.cpp). Every radio driver's trySetStandby() calls it with TRANSMIT_DELAY_COMPLETED before forcing standby, so that a pen |
| test_observer | 16 | platform / utility (kept) | applies | Unit tests for src Observer.h: notification order, the nonzero-return abort chain, CallbackObserver dispatch, ~Observer and ~Observable auto-detach, and list mutation from inside onNotify. |
| test_optin_migration | 14 | service (kept) | applies | hasModule |
| test_osthread_current_thread | 3 | platform / utility (kept) | applies | Unit tests for OSThread::currentThread's lifetime (src concurrency OSThread.cpp). currentThread is the only attribution a log line carries: |
| test_packet_history | 48 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for PacketHistory - the packet deduplication engine used by the mesh routing stack. PacketHistory maintains a fixed-size array of PacketRecords with an optional hash table for O(1) lookup. It tracks which node |
| test_packet_signing | 99 | service (kept) | applies | signer= |
| test_phone_api_config_dump | 13 | client API (kept) | applies | PhoneAPI::getFromRadio() config-dump sequence, asserted on decoded FromRadio protobufs: the order client apps depend on, the heartbeat preempt, SPECIAL_NONCE_ONLY_ jumps, mid-dump restart, and the post-complete drain rea |
| test_pki_admin_fallback | 6 | service (kept) | applies | Tests for the admin-key fallback in Router::perhapsDecode: a PKI unicast from an unknown sender is tried against each configured admin_key; on success the packet decodes and the key is persisted to NodeDB. Drives the rea |
| test_position_module | 16 | service (kept) | applies | These exercise PositionModule's pure broadcast-policy helpers (stationary detection and the interval floor). They take plain values, so no device globals or fake clock are needed. |
| test_position_precision | 24 | service (kept) | applies | Regression guard for #10509: precision 32 below must be ignored (no module settings). |
| test_radio | 55 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | The board LF PA table is the datasheet's 915 MHz design; 433 MHz and anything above 1 GHz keep RadioLib's. |
| test_radiolib_drivers | 0 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for the RadioLib driver calls Meshtastic's radio interfaces make, one set per chip family: SX126x (sx126x_tests.h), SX127x (sx127x_tests.h), SX128x (sx128x_tests.h), LR11x0 (lr11x0_tests.h) and LR2021 (lr2021_ |
| test_rebroadcast_mode | 11 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | wantAck= |
| test_reliable_ack_matrix | 32 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | wantAck= |
| test_rolling_counter | 8 | platform / utility (kept) | applies | Unit tests for RollingCounter. The case that matters is the span sum() covers: an under-sized ring reports WindowMs - BucketMs, and counting the edge bucket whole reports more. |
| test_routing_response_hops | 16 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | decoded= |
| test_rtc | 14 | platform / utility (kept) | applies | Regression coverage for issue #9828: on boards without a hardware RTC (e.g. RP2040), gettimeofday() can return uptime seconds rather than wall-clock time. A later readFromRTC() must not overwrite a higher-quality network |
| test_safefile | 5 | platform / utility (kept) | applies | Covers SafeFile's write-tmp readback rename-over path, on both the fullAtomic and the !fullAtomic construction. See the commit message for why these cannot go red on this host. |
| test_serial | 10 | service (kept) | applies | Required by Unity: PlatformIO's weak defaults do not link on MinGW (PE-COFF weak externals). Outside the guard below so both the portduino and the stub setup() get them. |
| test_snake | 10 | platform / utility (kept) | applies | Pure-logic tests for SnakeGame: ring-buffer advance, reversal rejection, wall self collision, growth on eat, and food-placement validity. No device globals or display stack required. |
| test_stream_api | 19 | client API (kept) | applies | adminAuthorized= |
| test_stream_framing | 12 | client API (kept) | applies | Framing constants mirrored from StreamAPI.cpp (defined only in that translation unit). |
| test_tak_config | 4 | service (kept) | applies | TAK (ATAK) team role value fidelity: the fields set by the app must come back verbatim. Companion to test_module_config, which proves every submessage's presence survives the set -> save -> load -> get lifecycle but swee |
| test_telemetry_payload_fit | 1 | service (kept) | applies | Pins the static_assert in src mesh mesh-pb-constants.h that meshtastic_Telemetry_size fits meshtastic_Constants_DATA_PAYLOAD_LEN. That assert compares two generated constants and cannot tell whether Telemetry_size is a s |
| test_throttle | 18 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Unit tests for src mesh Throttle.{h,cpp} - the firmware's elapsed-time and deadline helpers. These drive the injected clock across the 32-bit millis() wrap, which is not otherwise reachable in a test, and which every cal |
| test_tophone_queue | 4 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | requestId= |
| test_traceroute_nexthop | 3 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Tests that TraceRouteModule only learns next_hop from a traceroute response when the route array agrees with the node that actually relayed the packet. The route is unauthenticated payload, so without that check a forged |
| test_trackball_press | 10 | platform / utility (kept) | applies | Unit tests for TrackballInterruptBase::updatePress(): short long classification, the interrupt latch that keeps a tap released between polls, and repeat timing across the millis() rollover. |
| test_traffic_management | 89 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Verify the module is a no-op when traffic management is disabled. Important so config toggles cannot accidentally change routing behavior. |
| test_transmit_history | 14 | plumbing (replaced) | Phase 2: asserts machinery that no longer exists; register entry per suite (QUESTION-3) | Reset the singleton between tests |
| test_type_conversions | 29 | platform / utility (kept) | applies | Tests for src mesh TypeConversions.cpp covering: - bitfield bit collapse on store + extraction round-trip - long_name short_name truncation at the storage boundaries (wire User stays 40 wide for decoding legacy senders;  |
| test_uptime_clock | 26 | platform / utility (kept) | applies | Unit tests for src UptimeClock.{h,cpp} - the monotonic uptime seam. Covers: test-clock injection, stepping the injected clock, the real-clock fallback, the single-writer wrap carry (readers derive, serviceMonotonic() pub |
| test_userprefs_channels | 9 | service (kept) | applies | Channels::initDefaults() must populate every index named by USERPREFS_CHANNELS_TO_WRITE, and an index configured in part must keep the firmware's own value for every field left out. Under coverage-channel-table native-wi |
| test_utf8 | 24 | platform / utility (kept) | applies | Deliberately does NOT include TestUtil.h. This suite is pure-function - no NodeDB, no router, no sockets, no PKC - so the harness-wide guards there (no listening sockets, force_simradio clear) would assert conditions it  |
| test_warm_store | 15 | NodeDB copy (removed) | Phase 2: asserts the copy that no longer exists; register entry per suite | TRACKER |
| test_waypoint_expiry | 10 | service (kept) | applies | Unit tests for waypointIsActive() and its caller WaypointStore::isExpired(): the expire == 0 and expire == 1 sentinels, ordinary expiry, and an untrusted clock. |
| test_xmodem | 21 | client API (kept) | applies | goodCrc= |

Counts: plumbing 20, NodeDB 8, client API 5, service 29, platform 26.

## Python CLI against meshtasticd (`pytest -m smokevirt`, `-m smokemesh`) — stock 40/40, 7/7

Contract tests: they drive the client API and assert what a client observes. All apply in both phases.

| test | asserts |
|---|---|
| test_smokevirt::test_smokevirt_info | --info connects and prints the standard summary sections. |
| test_smokevirt::test_smokevirt_debug | --info --debug should include DEBUG log lines. |
| test_smokevirt::test_smokevirt_nodes | --nodes prints a node table containing the local node. |
| test_smokevirt::test_smokevirt_qr | --qr prints a non-empty ANSI QR code on stdout. |
| test_smokevirt::test_smokevirt_seriallog | --seriallog FILE should write a serial log file. |
| test_smokevirt::test_smokevirt_test_requires_two_devices | --test with a single device fails cleanly. |
| test_smokevirt::test_smokevirt_invalid_setting | Invalid --get/--set/--ch-set should print available choices. |
| test_smokevirt::test_smokevirt_primary_channel_guard | Cannot delete/disable/enable the PRIMARY channel. |
| test_smokevirt::test_smokevirt_set_owner | --set-owner changes longName persistently. |
| test_smokevirt::test_smokevirt_set_owner_short | --set-owner-short updates the shortName persistently. |
| test_smokevirt::test_smokevirt_set_location | --setlat/--setlon/--setalt persist a fixed position. |
| test_smokevirt::test_smokevirt_remove_position | --remove-position clears any fixed position. |
| test_smokevirt::test_smokevirt_ch_set_name | --ch-set name on ch-index 0 persists. |
| test_smokevirt::test_smokevirt_ch_preset | Each channel preset sets the LoRa modem_preset config value. |
| test_smokevirt::test_smokevirt_ch_set_downlink_uplink | --ch-set downlink_enabled/uplink_enabled flips both flags. |
| test_smokevirt::test_smokevirt_ch_add_then_del | --ch-add creates a SECONDARY channel; --ch-del removes it. |
| test_smokevirt::test_smokevirt_ch_enable_disable | --ch-disable and --ch-enable toggle a secondary channel's role. |
| test_smokevirt::test_smokevirt_ch_del_needs_ch_index | --ch-del without --ch-index should warn and exit non-zero. |
| test_smokevirt::test_smokevirt_seturl_default | --seturl applies a known channel URL. |
| test_smokevirt::test_smokevirt_seturl_invalid | --seturl with an undecodable URL fails cleanly. |
| test_smokevirt::test_smokevirt_configure | --configure applies an inline YAML config. |
| test_smokevirt::test_smokevirt_set_ham | --set-ham sets the ham callsign as the device owner. |
| test_smokevirt::test_smokevirt_set_network | --set wifi_* should persist in LocalConfig.NetworkConfig fields. |
| test_smokevirt::test_smokevirt_get_valid_setting | --get of a known setting should print the field, rc==0. |
| test_smokevirt::test_smokevirt_pos_fields | --pos-fields should mount the requested bit flags in the position config. |
| test_smokevirt::test_smokevirt_factory_reset | --set factory_reset true returns rc=0. |
| test_smokemesh::test_smokemesh_node_db_convergence | Each node should see all 3 nodes in its node DB after convergence. |
| test_smokemesh::test_smokemesh_broadcast_text | A broadcast from node A should arrive on node B. |
| test_smokemesh::test_smokemesh_dm | A DM from node A to node B should arrive on B. |
| test_smokemesh::test_smokemesh_dm_across_relay | A DM from node A to node C must relay through B (chain topology). |
| test_smokemesh::test_smokemesh_hop_limit_prevents_relay | A broadcast with hopLimit=0 from A reaches B but B does not relay to C. |
| test_smokemesh::test_smokemesh_show_nodes | showNodes should report the other nodes in the mesh. |
| test_smokemesh::test_smokemesh_traceroute_across_relay | Traceroute from A to C should show route via B in both directions. |

smokemesh uses `-s` (force SimRadio, PKI off) and a SIMULATOR_APP bridge with a chain topology A–B–C. The relay tests
(`dm_across_relay`, `hop_limit_prevents_relay`, `traceroute_across_relay`) need a real A–B–C path: on Ribbit they run on
FrogNet's simulator with that path (DESIGN.md, traceroute).

## Other oracles

| oracle | asserts | applies |
|---|---|---|
| meshtastic-mcp `scripts/ci_device_mesh_e2e.py` | 2 nodes form a mesh; a broadcast token reaches the DUT over TCP | both phases |
| meshtastic-mcp hardware tiers (`tests/admin`, `mesh`, `telemetry`, …) | device behaviour through the client API | both phases, once discovery reads the RAM (participants `announce`, the harness finds them with `capable`) |
| meshtastic-mcp `tests/unit` | the harness itself | not applicable (no firmware) |
| RepeaterTastic vectors (`capture_vectors.py` + `verify_vectors.py`) | the over-the-air MeshPacket on UDP multicast | Phase 3 (over-the-air edge) only; there is no air inside the system |
