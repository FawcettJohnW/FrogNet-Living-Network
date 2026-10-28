# Acceptance run, 2026-10-28 — build fdf-2026-10-28-r36 (member fdf-kt-2026-10-28-a19, kart k3, comms c2, dc d1)

Every gate of FINAL-GATES.md, plus the netns tests, the single-host checks, the rlite suite, the Kotlin member's tests,
the kart phone test, DisasterComm end to end and the Ribbit platform's qualifications, on branch bundles-20261027.
Netns tests ran each in its own mount + network namespace (unshare; private /run/netns and /run/wireguard). Gate 4
ran in Docker systemd containers built from frognet-clean:noble.

## Results

| Gate | What | Result |
|---|---|---|
| 1a | discovery selfcheck | PASS 14/14 |
| 1b / 1b2 | discovery oracles / plane | PASS 84, 0 regression / PASS (after the license fixes below) |
| 1c | simulator, every tier | PASS. In G1: all tiers except DAF and S-scale, which need the region binaries; both PASS on the build host with r36 (DAF: 20 topologies + 3 scenarios; S-scale E=1,2,5, verified 400/400). Real backend (`FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1`): mode-2 echo PASS on the build host (G1 has no ffmpeg, and its DNS is the node's own) |
| 1d | oracles/ directory | PASS |
| 1e | parsers: Python / C++ / C# | PASS / PASS (0 warnings, ctest 2/2, parser 37/37, dashboard 32/32) / PASS |
| 1f / 1g | dashboard oracle / hub oracle | PASS / PASS |
| proxy oracles | on the proxy's venv in G1 | 13 PASS. The 7 that fail also fail on origin/main: tuple_key (needs an argument), semcache (imports `core.db_credentials`, removed), cache_coherence (opens `opt/proxy/...`, gone), resp_hash, tuple_address (live store 500), instance_references (codec API), fanout_wire_order (waits forever on 10.77.77.1) |
| 2a / 2b | region loop / NOKEY | PASS / PASS (ported to invitations) |
| 2c | LAN-only | PASS |
| 2d / 2e | ponds daemon / reach | FAIL: design gap, below |
| 2f | member selftest | PASS |
| netns | graph, hub, egress, invisible, scale (S=200 E=10) | PASS (graph, hub, egress, scale ported to invitations) |
| netns | latency, member-death (C++ and Kotlin) | measured (RESULT DONE) |
| checks | daf, memory-order, map-region, map-removal, mapwait, selfreg, long-leaf, region-file, dashboard, chat, discovery, move, rule, coalesce, map-oracle (against lispers.net), kart | PASS |
| tests | reach-alias, mapown, eexist, kart owned cells | PASS |
| rlite | suite 1–30 | 30 PASS (0004-shim-eth needs a real eth0: PASS outside the isolation wrapper) |
| Android tools | fdftun, member-death, leaf/bundles/add-ins (49) | PASS |
| Android tools | placement, two-ponds | FAIL: design gap, below |
| app | app-typecheck | PASS (192 classes) |
| kart | phone test (core, Robolectric phone race, telemetry and settings tuples) | PASS |
| Communicator | desktop tests; Android core against it (27,095 vectors; core) | PASS |
| DisasterComm | the demonstration through its pages, headless | PASS |
| 3a / 3b / 3c / 3d | no-broker / no-fallback / docs / syntax | 0 / 0 / 0 problems / 100 OK, 0 bad |
| 4 G1 | LAN-only install, reboot | PASS (r36) |
| 4 G2 | two LAN-only nodes + elector, reboot | PASS: databasehost 10.72.72.1 on both, pings 3/3 |
| 4 G3 | region by invitation, reboot | PASS. Covered: fdf-region-create; N1 by fdf-invite; N2's line from fdf-identity on N1 (an existing name refused); core leaf C1 (line from N1) joined through its user-space tunnel; add-ins (kart on the holder, dc and kart host on N1, comms on C1); each add-in's own cell; resolve; C1's Communicator reached through the hub; nothing listens on the holder outside the tunnel but udp 50889; no private key in any log; after a reboot of all four, every unit active and every cell back |
| 4 G4 | upgrade in place (`--preserve`) | PASS from origin/main's tree (T0 42465dd is not in this repository): fnid kept, name/IP/interface recovered, r36 running, no failed unit, no stray file in /usr/local/bin |
| 4 G5 | leave + uninstall | PASS: fdf-leave (N1's dc cell gone with it), uninstall on both (180 files, packet filter restored, operator crontab kept, add-in units gone) |
| 5 | dist compare | PASS: every payload file equals its source, except the generated BUILD |
| Ribbit | redis qualify (build, oracles, suite) | GREEN 39/39 families |
| Ribbit | lispers.net qualify | PASS |
| Ribbit | MQTT: Mosquitto's suite on the Ribbit front | 272/276, as RESULTS.md records (the 4 http-api tests fail on stock too) |
| Ribbit | cpp qualify | semcodec, fnwp e2e and ramrows FAIL with identical counts on origin/main (the Python reference's lz4 4.4.5); the rest PASS |

Not run here: T7 (hardware), the APK build (no Android SDK), multi-machine tests (redis deploy/inet, MQTT two_boxes), Windows.

## Fixed by this run
- rina-host: SIGTERM killed it without `exit()` (DAF's tick thread starts before main and had TERM unblocked): no atexit reports, no coverage. r36.
- fdf-chat@.service on the region holder read 127.0.0.1; since r31 the RAM manager is on the hub's tunnel address. The unit restarted forever (found by G3).
- kart add-in k3: the holder's kart memory listened on 0.0.0.0:8799, visible from the Internet; it now listens on the control RAM's address (found by G3).
- frognet-tree tarball: only what git tracks (a tree where examples were built shipped their build products: 1,148 extra files).
- aarch64 core bundle: the runtime wheel's platform tag (it never built).
- core/not_frognet.py: an unreadable region_hosts.json was silently "no members"; now said.
- MQTT build rewrote a tracked header without its licence notice; lisper-ram did not link (lf_ebr).
- docs/OPERATING.md §3 still described the site-key flow; rewritten for invitations, the core, add-ins, phones and Windows.
- Tests brought to r31/r32: region-loop, scale, graph, latency, egress and hub on invitations; member-death (event lines, kill by PID); chat-check (pages as installed); eexist (had no pass criterion or fixtures); map-oracle (the key lispers.net is given); phone-test (fetches its build deps); communicator-test (this tree's layout); no-import-guard (vendored and frozen trees exempt).

## Open: design decisions
- Ponds, the forward switch, reach, off-mesh placement and the phone's two ponds were built on public RAMs and uninvited keys. Since r31 the hub peers only invited keys, and the daemon has one hub.json. 2d, 2e, two-ponds and placement fail for that reason.
- A second hub in one region: fdf-hub puts 10.253.253.254 on every interface it makes, the RAM manager's address.
- The droplet still ships the pre-add-in kart/comms services (fdf-kart.service, fdf-services.sh on a public :8840, fdf-comms.sh, fdf-kart-host.sh) beside the add-ins.
