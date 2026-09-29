# Work log / restart point

## Control provenance
- Input: `lispers.net-master.zip`
- SHA-256: `07eb9308c39e7e8a2a58f288e6452fd734e4d08014850c1fbb37148c5432d2f1`
- Untouched extraction used as control: `control/lispers.net-master`
- Public `api_init` methods inventoried: 89.
- No current-tree behavioral unit/conformance suite found; `lispers.net-test-install.py` is installer/release oriented.

## Established control facts
- Dynamic API GETs in `lisp-core.py:lisp_get_api_data()` route to owner processes over internal IPC.
- The path acquires `lisp_ipc_lock`, sends an IPC request, waits synchronously for a reply, releases the lock, decodes bytes, and returns the output.
- `lisp.py:lisp_ipc()` segments IPC messages and retries socket errors with exponential sleep.
- Existing packet-path latency instrumentation includes ITR/ETR/RTR sites.
- `apps/lisp-watch-pps.py`, `apps/iperf-send`, and `apps/iperf-rec` are existing measurement tools.

## Created artifacts
- `CONTRACT.md`: authority and behavioral-family matrix.
- `run_conformance.py`: common test runner.
- `conformance/adapters/control.py`: adapter for untouched lispers.net API.
- `conformance/adapters/command.py`: implementation-neutral JSON command adapter for Ribbit-LISP.
- `tests/test_read_contract.py`: non-destructive API observations.
- `tests/test_roundtrip_contract.py`: mutation-gated CRUD round trip.
- `artifacts/control-api-inventory.json`: extracted public API surface.
- `artifacts/source-characterization.json`: active-source structural counts.
- `artifacts/control-instrumentation.patch`: optional telemetry patch; disabled unless `LISP_METRICS_FILE` is set.

## Current validation
- Python compile checks pass for harness and instrumented `lispapi.py`, `lisp.py`, `lisp-core.py`, `lispmetrics.py`.
- Harness dry-run against no daemon fails its three read observations and skips mutation, as expected. This proves failure is visible rather than silently accepted.

## Next empirical step
Run the non-destructive suite against a live control node. Then enable the isolated mutation fixture and capture telemetry. The topology suite should be added before replacing each corresponding subsystem.

## Checkpoint v0.2 — first green-field C++ slice

- Materialized FrogNet-Living-Network source and verified C++ `frogram` contracts directly.
- `Session`: multiplexed request/reply, persistent session, semantic stats.
- `Memory`: write, write_nowait, partial-index read, held read (`after`, `wait_s`), remove.
- Added `ribbit_cpp/ribbit_lisp.cpp`, a clean-sheet LISP domain engine. No lispers.net algorithm copied.
- Added persistent JSON-lines command adapter so a test case sequence observes one implementation instance.
- Added mapping contract tests for longest-prefix match and IID isolation.
- Local Ribbit result: 6 tests, 6 PASS, 0 FAIL, 0 SKIP with mutation enabled.
- Current backend is process-local state. This is intentionally not claimed as networked Ribbit conformance.
- Next: storage interface + `frogram::Memory` backend; then control-node differential run and packet-level mapping/resolution fixture.

Exact local command:
`LISP_ALLOW_MUTATION=1 python run_conformance.py --adapter command --command './ribbit_cpp/ribbit-lisp' --capabilities system.get,map_cache.list,map_cache.get,map_cache.add,map_cache.delete,site_cache.list,map_resolver.add,map_resolver.get,map_resolver.delete,database_mapping.add,database_mapping.delete`

## 2026-09-23 — v0.3: first FNW1/Ribbit RAM-backed vertical slice

Implemented a real `frogram::Memory` backing option for the green-field C++ mapping engine. The local backend remains available as a deterministic oracle. No lispers.net cache/IPC algorithm was copied.

RAM schema for this slice:
- service: `lisp`
- map-cache variable: `map-cache|<iid>|<group>`
- instance: EID prefix
- bag: mapping domain object (`iid`, `prefix`, `group`, independent RLOC fields)
- resolver variable: `map-resolver`, instance = resolver address

Semantics used from the existing FrogNet implementation (verified from source): `Memory::write`, exact/partial `Memory::read`, `Memory::remove`, held read via `after` + `wait_s`, two-connection `Session` multiplexing, and FNW1 RAW/REPEAT/SAME statistics.

Results preserved under `artifacts/`:
- `ribbit-local-v0.3.txt`: 6/6 conformance tests pass with process-local backing.
- `ribbit-ram-v0.3.txt`: same 6/6 pass over a separate C++ FrogNet RAM server via FNW1.
- `cross-process-v0.3.txt`: independent Ribbit-LISP processes observe write and replacement: PASS/PASS.
- `ram-probe-v0.3.txt`: held read wakes on a remote session write; intentional writer delay 150 ms, observed wake 150 ms. Reader stats: raw=2 repeat=1 same=1, proving repeat/SAME machinery was exercised.

Important non-claims:
- This does not yet implement LISP packet wire behavior.
- This does not yet prove performance superiority to lispers.net.
- `map_cache.list` in the current RAM slice enumerates only IID 0/group empty; it exists only for the current generic collection conformance test and will be replaced by a domain-appropriate query before being treated as complete API behavior.
- Held reads are proven at the FrogNet RAM contract level but are not yet wired into a LISP state-transition worker.

Next vertical slice: derive mapping registration/resolution observable requirements from the control, add dual-target tests, then implement the Ribbit state transition using held reads rather than polling/notification machinery where the contract permits it.

## v0.4 registration/resolution
- Traced `lisp_process_map_register()` and `lisp_site_eid.merge_*` in control source.
- Added implementation-neutral registration/resolution tests.
- Implemented registration state over local and FNW1 RAM backends.
- Added source-authorized TTL=0 deregistration rule.
- Implemented merge as independent xTR truths plus fan-in at resolution; no parent/child registration copies.
- Local suite: 13/13 PASS (`artifacts/ribbit-local-merge-v0.4.txt`).
- FNW1 RAM suite: 13/13 PASS (`artifacts/ribbit-ram-merge-v0.4.txt`).
- False start preserved: attempted to pass a port to fixed-port `ram-server`; server listens on 8788. Corrected test passed.
- Next: registration lifetime/expiry and then wire-facing Map-Register/Map-Reply vertical path.

## v0.5 — expiry + first real LISP wire vertical path

Control-derived lifetime rule verified in `lisp.py:lisp_eid_record.store_ttl()` and `lisp-ms.py:lisp_timeout_site_eid()`: when T is requested, ordinary record TTL is minutes and bit-31 form is seconds; otherwise control uses 180 seconds. Ribbit-LISP implements the same validity rule with per-registration deadlines and no expiry scanner/reaper.

Added byte-level IPv4 and IPv6 Map-Register -> registration truth -> Map-Request -> Map-Reply tests, multiple locator records, negative native-forward Map-Reply, and malformed/truncated register rejection. Same suite passes local and over the separate FNW1 RAM server.

A test exposed whole-second expiry truncation; deadlines were changed to millisecond precision while preserving protocol TTL units.

Latest results: `artifacts/ribbit-local-ipv6-v0.5.txt` and `artifacts/ribbit-ram-ipv6-v0.5.txt`, both 20/20 PASS.

Next: configured-site authorization / accept-more-specifics, then authentication + Map-Notify. No claim yet of full LISP wire conformance: current codec intentionally covers one-record IPv4/IPv6 unicast EIDs and ordinary RLOCs, not LCAF/(S,G)/crypto/DDT.

## v0.6 — site authorization, authentication, Map-Notify, control byte oracle, DDT state

Added configured-site authorization and accept-more-specifics behavior. Unknown or unauthorized registrations are rejected before state mutation. Site policy is represented as independent site truths; Ribbit-LISP does not create Dino's dynamic child site/cache object merely to authorize a more-specific.

Added SHA-1 and SHA-256 Map-Register HMAC verification using the control algorithm: zero authentication bytes, HMAC the complete packet, compare transmitted digest. Integrated site key-id/password gate proves bad auth cannot mutate registration state and valid auth can.

Added Want-Map-Notify acknowledgement generation for the current IPv4 wire slice. Notify preserves nonce and EID records and is authenticated with the configured site key.

Added an offline differential wire oracle that imports Dino's own `lisp.py` packet classes with optional runtime modules stubbed. The IPv4 Map-Register fixture byte-matches Dino's encoder and Ribbit-LISP's resulting Map-Reply byte-matches Dino's encoder for the same fields.

Added DDT delegation truths with longest-prefix referral-set lookup and IID isolation. This is domain-state coverage only; Map-Referral wire encoding and recursive resolver behavior remain next.

Latest full suite: 29/29 PASS local and 29/29 PASS over separate FNW1 RAM process. `tools/test_control_wire_oracle.py` also reports two PASS results.


## v0.7 review hardening
- Removed HMAC password from shared site RAM cells; added external RAM-cell inspection (`tools/check_site_secret.cpp`).
- Propagated transport sender metadata through wire Map-Register ingress and made missing source unauthorized for TTL-zero deregistration.
- Added packet-level TTL-zero forged/missing/authorized sender regression.
- Withdrew `site_cache.list` capability rather than counting a vacuous empty stub as coverage.
- Replaced serialized-substring assertions with structural prefix/RLOC comparisons across registration, site, mapping and DDT tests.
- Fixed non-merge-after-merge replacement.
- Partitioned registration/site RAM variables by prefix length, removing whole-table reads from packet resolution/authorization. Held-read resolver view remains open.
- Local: 31 PASS, 1 explicit SKIP. FNW1 RAM: 31 PASS, 1 explicit SKIP.
- Shared-site secret inspection: PASS.

## v0.8 honest-format checkpoint
- Reformatted `ribbit_cpp/ribbit_lisp.cpp` so statement density is visible rather than hidden in long physical lines.
- Post-format: 894 physical lines, 608 semicolons, 169 `if` sites, 49 `for` sites, longest line 198, zero lines >200 characters.
- Complexity ratio remains explicitly non-publishable because the compared control/Ribbit behavioral surfaces are not yet aligned.
- Post-format qualification: local 31 PASS / 1 honest SKIP; FNW1 31 PASS / 1 honest SKIP; Dino Map-Register and Map-Reply byte differential PASS.

## v0.12 governance
Replaced delete-to-supersede registration semantics with a map-server-owned governing truth. This preserves xTR ownership: old independent truths can remain physically present while resolution selects the currently governed set. TTL-0 withdrawal is represented as current inactive registration state. Held resolver is not yet implemented.

## 2026-09-24 — v0.16/v0.17: held resolver participant
- v0.16: one held participant/view per `(iid, group)` for registration + map-server governance. Manifest and per-/N watchers use FrogNet held reads past observed record IDs; changed instances are applied atomically to local truth. Warmed `resolution.get` performs zero request-triggered RAM reads.
- Cross-process proof starts the resolver warm and empty, then publishes from a separate process; the existing resolver wakes, discovers a new prefix length, resolves the new RLOC, expires from value+clock without a reaper, and observes TTL-0 plus administrative deregistration through current inactive truth.
- `registration.delete` in RAM is represented by governor-owned inactive governance instead of physical removal.
- v0.17: public site policy and its length manifest join the same held participant. `site.delete` publishes inactive current policy. Warmed authorization performs zero authorization-triggered RAM reads.
- `site_keys` remains process-private. External RAM inspection still proves no password field is published.
- Synchronization tests use `resolver.wait` / `resolver.wait_site`, which wait on participant condition variables. Writers publish and return; they do not wait for readers and do not update held views directly.
- Qualification: local 31 PASS / 1 explicit SKIP; FNW1 31 PASS / 1 explicit SKIP; cross-process held proof PASS; package audit PASS; Dino-derived Map-Register and Map-Reply byte fixtures unchanged.

## 2026-09-24 — v0.29: lock-free participant held views

Removed all participant-local mutexes and condition variables from `ribbit_lisp.cpp`. Held registration/governance/site/DDT/map-cache/map-resolver state is now published as immutable snapshots through atomic `shared_ptr` load/store. Each per-prefix-length watcher is the sole writer of its snapshot slot. Snapshot lifetime is standard-library shared ownership; readers cannot observe freed replaced snapshots.

Convergence waits no longer block on private C++ condition variables. Watchers publish `resolver-applied|<iid>|<group>`, `map-cache-applied|<iid>|<group>`, and `map-resolver-applied` truths, one writer per applied cell. Wait operations check held state, then use a held FrogNet Memory read on the applied variable and re-check held state.

The manifest thread owns dynamic watcher creation for its view after bootstrap, so the watcher-vector mutex is gone. Shutdown first closes the FrogNet session, joins manifest owners, then joins their child watchers.

## v0.32 — runtime-lock-free snapshot repair
- Reproduced v0.31 hidden-lock publication through atomic shared_ptr free functions.
- Replaced held-view publication with raw atomic pointers plus lock-free quiescent-state reclamation.
- Added runtime pointer-atomic probe and concurrent replacement/reclamation stress.
- Restored ribbit_lisp.cpp to zero lines over 200 characters.
- Added same-host performance qualification; this run is acquisition-cost parity, not a throughput claim.
- Local and clean FNW1 conformance 43 PASS / 1 SKIP; all independent held-view tests PASS.

## 2026-09-24 — v0.33: ETR direct Map-Reply (IPv4) — Claude, taking over from the v0.32 handoff
- Verified v0.32 archive SHA-256 24629d38946fde0881935b05ba4d9fb4bcc71d7d0248d1819074e3241d72b795; baseline
  local 43 tests / 1 skip reproduced before any change.
- Red recreated against unchanged v0.32 with the contract tightened from the control (TTL 1440, R bit) and a
  decoy Map-Server registration; 44 tests / 1 error. Checkpointed (v0.33-red).
- Green: `wire.etr_request4`; red test exposed a local-backend table shared between ETR database mappings and
  Map-Server registrations; separated (`etr_db`). See CHECKPOINT-v0.33.md for qualification and performance.

## 2026-09-24 — v0.34: IPv6 ETR direct Map-Reply
- Red (45/1 error) checkpointed first; green adds `wire.etr_request6` and folds both ETR ops into one handler.
- Independent-process ETR test extended to IPv6. Recorded: database-mapping view is a full-table scan per
  lookup, unlike the per-length views. See CHECKPOINT-v0.34.md.

## 2026-09-24 — v0.35: observed replacement convergence
- Old cross_process_ram failed 7/20 on v0.34 (asserted immediate visibility). Rewritten to wait for the
  expected value; red test proved map_cache.wait could not express that (ignored value, never timed out).
- Green: `rloc` and `timeout_s` on map_cache.wait; bounded wait_applied. cross_process 20/20. See CHECKPOINT-v0.35.md.

## 2026-09-24 — v0.36: ETR Map-Register from held database mapping
- Characterized from lisp-etr.py/lisp.py (see CHECKPOINT-v0.36-red.md); red = conformance loop test + Dino
  byte oracle extended with the control's own ETR register. Green: `wire.etr_register4`, byte-identical to the
  control including HMAC; Map-Server decoders accept the I-bit trailer. See CHECKPOINT-v0.36.md.

## 2026-09-24 — v0.37: map-server configuration as ETR-owned held truth
- Characterized lisp_map_server_command / lisp_ms; red = held-config register must equal explicit build.
  Green: etr_map_server.* held view, password process-private, raw-reader secret check. See CHECKPOINT-v0.37.md.

## 2026-09-24 — v0.38: periodic ETR registration participant
- Red: independent-process registrar test (UDP, independent Map-Server, observer). Green: registrar with a
  held-read clock, control trigger/refresh/nonce semantics, published send truth, key snapshot. Trigger rule
  corrected to the control's after the latency benchmark exposed a skipped intermediate state. See
  CHECKPOINT-v0.38.md.

## 2026-09-24 — v0.39: ETR consumes Map-Notify
- Characterized lisp_process_map_notify / lisp_send_map_notify_ack (ack record count 0 while copying records).
  Red: conformance + byte oracle. Green: wire.etr_notify4, byte-identical to the control. See CHECKPOINT-v0.39.md.

## 2026-09-24 — v0.40: Map-Notify over the registrar's control socket
- Red: UDP loop test (register out, notify back to its source port, ack expected). Green: one control socket,
  receiver participant, shared notify logic. Batch run exposed a v0.38 defect: a keyless map-server published by
  another process crashed the ETR (SIGABRT, reproduced on v0.39); fixed as a recorded failure. See CHECKPOINT-v0.40.md.

## 2026-09-24 — v0.40a (branch): transport architecture record and registration byte measurement
- TRANSPORT-ARCHITECTURE.md; transport.stats (red first); database_mapping.wait rloc/timeout; FNW1 byte measurement
  versus full UDP Map-Registers. FNW1 without BLDC is larger than UDP at establishment and refresh; one change is
  competitive; BLDC measurement open. Built on a clean v0.40 copy because another session wrote a v0.41 into the
  shared working tree meanwhile. See CHECKPOINT-v0.40a-transport-measurement.md.

## 2026-09-24 — v0.41: multi-record Map-Register authorized once
- From the candidate evaluation: statistical red (18 partial applications in 3,000 rounds on v0.40a), green 0 in
  3,000. Open-mode divergence from the control recorded for decision. See CHECKPOINT-v0.41.md.

## 2026-09-24 — v0.42: no site, no registration (bug fix)
- The "open mode" recorded in v0.41 as a divergence pending decision was a bug: the v0.6 contract already required a
  site; the check was guarded so pre-site tests kept passing. Red, fix, 22 tests and 3 tools given explicit sites,
  5 tests made to observe their sites. See CHECKPOINT-v0.42.md.

## 2026-09-24 — v0.43: native registration, ETR truth governed in place
- FINDINGS-FOR-DINO.md started (report notes; offer issue/PR; his code untouched). Red: independent-process native
  test. Green: etr.identity, ms_governor (children hold each ETR's database variable), decisions, no copy.
  ETR bytes per change 593 (candidate 2,427). Liveness and re-governance next. See CHECKPOINT-v0.43.md.

## 2026-09-24 — v0.44: liveness of native registrations
- Red: kill an ETR, its registration must stop resolving. Green: one liveness cell per ETR, heartbeat participant,
  held liveness view in resolvers, resolver.wait aligned with resolution. Heartbeat ~404 B/beat, independent of N.
  See CHECKPOINT-v0.44.md.

## 2026-09-24 — v0.44 real-machine package
- tools/qualify.sh runs every gate (build, local, clean FNW1, independent-process tests x RUNS, Dino oracle, stress,
  lock-freedom, performance, bytes) into results/<stamp>/. Tools read RIBBIT_RAM_HOST/RIBBIT_RAM_PORT; RAM_SERVER or
  RIBBIT_RAM_EXTERNAL for non-x86-64 hosts. RUN-ON-A-REAL-MACHINE.md. Code identical to v0.44 green.

## 2026-09-24 — v0.45: re-governance of native registrations
- Red: site deleted, native registration still resolved. Green: site authorization derived at read for native
  registrations; no rewrite on policy change. Site delete -> ITR 524 us median. See CHECKPOINT-v0.45.md.

## 2026-09-24 — first cross-Internet run (AI-Host -> streamingfrog.com:8789), findings
- test_held_map_cache and test_held_database_mapping passed 1/3: they left their /24 and /16 active, so a second run
  against the same (external) memory failed its "starts empty" check. Reproduced locally on one shared server (1/3),
  fixed by cleaning up (3/3). Not a network effect.
- wire.request4 rose to 184 us: not the network either. Map-Server resolution copies every held registration per
  request, so its cost grows with the table: 24 us at 1 entry, 772 us at ~1,000, 1,423 us at ~3,000 (local,
  tools/measure_resolution_scaling.py). A shared memory that has accumulated earlier stages' registrations shows it.
  Next slice (v0.46): resolution walks the per-length slots from the longest down, without copying. Red first.
- Held ETR paths stayed ~18 us across the Internet: zero request-time reads holds end to end.
