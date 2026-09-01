# FrogNet comprehensive simulator — STATUS / HANDOFF

## 0-NEWEST-8. 2026-06-08 — SotF BROWSER PLAYBACK server built + verified (PRIMER 3)

NEW FILE: `simulation/sotf_browser_server.py` (Flask, single port, no build step).
Drives `sotf_video_stream_test` from a browser: form (bandwidth/latency/jitter/outage)
→ Start → the frames that SURVIVED the SotF transport play in a `<video>`, live stats
beside it. Existing `sotf_video_stream_test.py` was NOT modified — the server imports its
real symbols (`parse_ivf`, `generate_test_video`, `SotFSender`, `SotFReceiver`,
`StreamStats`, `_percentile`) and stands up transport via `create_connected_pair`.
`_run_stream_into_state` mirrors `run_stream_test`'s setup loop but keeps the receiver in
scope so `received_bytes` can be pulled out for reassembly.

KEY DEVIATION FROM PRIMER 3 (intentional, primer is wrong against the code): the primer's
pattern (a) chunks OPAQUE pre-encoded WebM bytes through SotF and admits it "blurs the
per-FRAME dynamics." But the real transport ships ONE VP8 frame per `send_frame`, and
`SotFReceiver` reconstructs the EXACT frame bytes keyed by seq (byte-verified). So the
server instead collects the DELIVERED VP8 frames, re-muxes them into IVF, then
`ffmpeg -c:v copy` to WebM (NO re-encode). A lost delta is a genuinely missing frame; the
original frame seq is preserved as pts so drops show as freeze/stutter rather than the clip
silently playing faster. Output WebM frame count == `frames_delivered` (verified).

ENDPOINTS: `GET /` (page) · `POST /test/start` (JSON params → `{stream_id}`) ·
`GET /test/stats/<sid>` (status/done/delivery_pct/p50-p95/first_frame_is_keyframe) ·
`GET /test/stream/<sid>` (the WebM, `video/webm`). Form bandwidth is kbps →
bandwidth_bps = kbps*1000/8 (bytes/sec). Timeouts auto-scaled same rule as CLI `main()`.
ONE STREAM AT A TIME (`_RUN_LOCK`) because `transport_sim_tier` uses module-level executors;
concurrent `/test/start` → 409.

VERIFIED in-container (flask 3.1.3, ffmpeg, cv2 all present): clean LAN 30/30 delivered +
100% byte integrity; contested RF (64 kbps, outage 0.5 / 800 ms) 22–23/30 delivered, output
WebM valid vp8 320x240 with nb_read_frames == delivered every run; HTTP path: `/`=200,
start→sid, stats reports delivery% + p50/p95, stream→`video/webm`, concurrent start→409.

WRINKLES (not bugs): reassembly drives off per-frame `outcome.seq` (real seq), NOT
`received_bytes` directly, to sidestep `SotFReceiver._wrap_repeat`'s synthetic `last_seq+1`
path — irrelevant for testsrc (frames differ; REQ_REPEAT won't fire), matters only for a
clip with byte-identical consecutive frames. If heavy loss drops frame 0's keyframe, the
first delivered frame isn't a keyframe and the clip may not start cleanly — surfaced as
`first_frame_is_keyframe:false` + a page warning, not a silent broken file.

RUN: `python3 simulation/sotf_browser_server.py [--host H --port P]` → `http://127.0.0.1:8080/`.

NEXT: PRIMER 3 pattern (b) MSE (start-before-finish) — LOW value here since the sim isn't
realtime (nothing to watch live); deferred. Live capture (getUserMedia) deferred. Swap point
for both = the `/test/stream` body + `loadVideo()` JS. Eventually swap the simulated transport
for PRIMER 1's physical-transport factory and the same UI drives real hardware.

CONTAINER GOTCHA burned this session: do NOT `pkill -f sotf …` — the pattern matches your own
shell's argv and self-kills the command (rc=-1, no output). Kill test servers by PORT:
`fuser -k 8080/tcp`.

## 0-NEWEST-7. 2026-06-07 (pm-2) — BAMacBook STILL looping after hysteresis: third
## cause found + fixed. [FALLBACK_RESHUFFLE_NOT_CONVERGENCE_V1] in discovery/routes.py.
## After deploying [WINNER_HYSTERESIS_V1], the metric-22 winners are STABLE (HYSTERESIS_HOLD
## fires, transit=[]), but slash24_mutated=1 every pass still → runAgain to the cap. Root
## cause (cross-checked against the live BAMacBook log + routes.py source): the N-deep
## FALLBACK ladder (metrics 100/101/102) re-sorts on jittery secondary RTTs every pass even
## when the WINNER and the dev SET are unchanged (e.g. 10.130.130 came in wg2@100/ens9@101/
## wg0@102 and was rewritten wg0@100/wg2@101/ens9@102 — secondaries 116/117/122ms). Each slot
## swap is a real /24 `ip route replace` via install_if_changed, and rtmut flagged
## route_table_mutated on ANY /24 write — including fallbacks. That contradicted rtmut's own
## documented definition ("converged iff the WINNER SET did not move"). FIX: install_if_changed
## now passes flag_mutation=(metric==WINNER_METRIC), so only a winner-metric (22) /24 write
## flags convergence; fallback installs at >=100 do not (same exclusion already applied to
## probe /32s and reap deletes). The fallbacks still install and re-order freely — they just
## don't block convergence. Proven: test_sync_on_route_mutation_oracle.py grew E (ladder
## reshuffle with stable winner does NOT flag) and F (winner-dev change at 22 still flags);
## discovery gate 35 pass / 0 regression; runagain-convergence oracle green; full M6 green.
## DEPLOY: ship discovery/routes.py (part of discovery/*); re-merge then converges (winner
## held by hysteresis, ladder free to wobble, slash24_mutated=0 once the winner set settles).

## REBUILD RECIPE CHANGED (2026-06-06): tar MUST include etc/ now.
## etc/setup_iptables (John's production host file) is in the tree. The reflect
## loop-detector trap lives HERE (proxy reflect handler on 18432 + this iptables
## REDIRECT trap with --mark 1 RETURN bypass) — NOT Apache. Rebuild with:
##   tar czf $OUT opt usr etc   (NOT just opt usr — that drops etc/setup_iptables)

Authoritative continuity doc. If context is compacted, READ THIS FIRST.

## 0-NEWEST-6. 2026-06-07 (pm) — BABox transit over-claim + BAMacBook winner oscillation: BOTH FIXED, gate 35-green

Diagnosed from BABox + BAMacBook runMerge logs + broker.db dump. The two boxes were
NOT in a forwarding loop (BAMacBook transit=[], sees BABox's .47 foot as NOT_FROGNET,
never routes via it). They were each failing to CONVERGE (slash24_mutated=1 every pass,
runAgain to depth cap=10), for two independent reasons — both now fixed and oracle-proven:

  [TRANSIT_GUEST_UPLINK_V1]  BABox is a WAN gateway (owns 10.111.11) that is ALSO a
  leased guest (10.179.179.47) on BAMacBook's wlan0 LAN, reaching the mesh THROUGH it.
  The old transit rule excluded the mesh-ward uplink dev only on a LAN-child, so BABox
  advertised transit for everything it reached over the guest dev: [10.102.60, 10.130.130,
  10.179.179, 10.250.250] — all UPSTREAM of it. Confirmed persisted in broker.db. FIX:
  live.resolve_uplink_dev() (extracted pure helper) detects the guest dev — a non-FrogNet/
  non-wg iface holding a 10.x lease (host octet not 1/2) on a /24 it doesn't own — and sets
  uplink_dev even for a gateway; discovery.py transit loop now excludes uplink_dev regardless
  of has_own_uplink. BABox -> transit=[]. Seattle5 (real gateway, .1 on its own eth0) UNCHANGED.
  Oracle: discovery/test_transit_guest_uplink_oracle.py.

  [WINNER_HYSTERESIS_V1]  BAMacBook is multi-homed (wg1->NY-1, wg2->Seattle5); reaches the
  Seattle cluster ~equally over both (RTTs 98-138ms straddle/jitter) and wg2 flaps. promote()
  elected rank-0 on raw lowest-rtt with no stickiness, so the winner dev flipped wg1<->wg2
  every pass -> /24 mutated -> never converged. transit=[] throughout, so independent of the
  BABox bug. FIX: promote() holds the incumbent winner dev (Routes.winner_dev) when it is
  still a MEASURED candidate within max(best*1.25, best+25ms) of the best rtt; inert with no
  incumbent (first convergence) and never sticks to a DEAD/spiked path (failover preserved).
  Oracle: discovery/test_winner_hysteresis_oracle.py.

Gate after fixes: discovery 35 pass / 0 known-fail / 0 regression; run_all.py all in-container
tiers PASS. Files touched: discovery/{discovery.py,routes.py,live.py}. The fix LOGIC is proven
at the discovery-oracle layer (test_transit_guest_uplink_oracle, test_winner_hysteresis_oracle).
END-TO-END coverage ADDED this session (closing the gap these bugs exposed): three new
frognet_sim builders — topo_dual_gw_guest_lan (BABox/BAMacBook/NY/Seattle, multi-homed guest
gateway on a LAN owner's segment), topo_triple_gw_one_lan (3 WAN gateways on one LAN), and
topo_wan_gw_lan_and_tunnels (WAN gateway with local LAN children + 2 tunnels). Registered in
frognet_sim.main() AND live_engine._BUILDERS (model + real-engine tiers; now 23 topologies),
plus test_multi_gateway_lan_e2e() asserting bounded convergence + all-pairs reachability +
loop-free + guest-subnet propagation. The model tier proves STRUCTURE/convergence/reachability
for these shapes; transit-over-claim and hysteresis LOGIC remain oracle-proven (the model tier
does not compute transit_subnets and uses deterministic RTTs, so it cannot itself reproduce the
transit advertisement nor the jitter-driven flap). DEPLOY: ship discovery/* mesh-wide, re-merge
(BABox emits transit=[], BAMacBook stops flipping); BABox re-register rewrites its broker row;
clear stale broker rows (BABox old transit, Seattle6 bogus 10.250.250, dup Seattle2).

## 0-NEWEST-5. 2026-06-07 — GATE FULLY GREEN vs shipped source; entries 0-NEWEST-3/4 below are HISTORY

Current state, verified this session against the live tree (build 20260605-f-VOUCHGATE):
both gates PASS, no known-fail, no regression.
  - Discovery oracle gate: 33 pass / 0 known-fail / 0 regression
      PYTHONPATH=/home/claude/v25/opt/frognet_semantic \
        python3 discovery/run_discovery_oracles.py
  - Comprehensive M6 sim (run_all.py): all in-container tiers PASS — model suite,
    real-engine 20 topologies, failure modes, broker ponds, regression, netns plan,
    proxy decision/keying, transport calibration, discovery suite. TIER 4 execute +
    TIER 5 transport remain box-tier (sim is single-table LPM; cannot prove hop-by-hop
    arrival — unchanged hard limit).

INVOCATION GOTCHA (fresh container): run_all.py loads the live proxy + monitor code
and FATALs "cannot locate live patched code" unless you point it at them. In this
tree they are in-tree; set both roots:
    export FROGNET_PROXY_ROOT=/home/claude/v25/opt/frognet_semantic   # has internet_tunnels_v3/, proxy/
    export FROGNET_BIN_ROOT=/home/claude/v25/usr/local/bin            # has frognet_monitor_py
The discovery gate does NOT need these; only run_all.py does.

CORRECTION to 0-NEWEST-3/4 below: the "5 oracles deliberately RED pending box"
state is OBSOLETE. NEXT_HOP_IDENTITY stayed reverted; the box runs the lease/relay-via
shape (e.g. `10.160.160.0/24 via 10.250.250.221 dev eth0`,
`10.28.28.0/24 via 10.102.60.230 dev eth0`), and the oracles agree with shipped
discovery.py — test_walk / test_runmerge / test_seattle6 / test_ny2_alive_gate /
test_transit_from_winners are GREEN, not red. The directional-rule feature described
in 0-NEWEST-4 was NOT needed; treat that section as a record of a falsified path, not
a TODO. Entries 0-NEWEST-4 and older are retained below as history.

## 0-NEWEST-4. 2026-06-06 — NEXT_HOP_IDENTITY REVERTED; directional rule is the real fix

NEXT_HOP_IDENTITY (0-NEWEST-3) was FALSIFIED BY HARDWARE and has been fully
REVERTED from discovery/discovery.py (tree clean, all 23 discovery oracles green).
Two physical runs killed it: it aimed NY's mesh-ward routes at NY-2 (LAN leaf,
onlink) -> black hole; and Seattle's at Seattle6's lease -> black hole. The wg
tunnels are FINE (every peer fresh handshake; ALL endpoints = broker
165.232.153.144, i.e. hub-relayed, NOT node-to-node — you cannot find a peer by
probing /30 ±1). The fault was always the ROUTING DIRECTION.

HARDWARE-VALIDATED RULE (John installed these by hand; forward+return ping
confirmed both ways): for each node, next hop = the neighbor TOWARD the
destination along the guest chain (2->3->6->5->tunnel):
  - MESH-WARD (toward gateway/tunnel) -> via the UPSTREAM neighbor == the node's
    own default-via gateway, on the uplink dev (Three: via 10.160.160.1 wlan1;
    Six: via 10.250.250.1 wlan1).
  - DOWNSTREAM (a served leaf + below) -> via the DOWNSTREAM neighbor on the
    AP dev (Six: via 10.130.130.1 wlan0; Five: via 10.250.250.221 eth0).
  - GATEWAY (Five) -> remote side egresses the TUNNEL (dev wgN), downstream via
    the AP neighbor.
The pre-fix bug aimed mesh-ward routes DOWN the chain (3->Two, 6->Three), which
black-holes (the leaf has no path up/out). The asymmetry "Sea pings NY, NY can't
ping Sea" was the return path pointed backward.

FOLD-IN (the physical-feedback the user set up): the validated tables for
Seattle2/3/5/6 are captured verbatim in simulation/folded_seattle_chain_routes.py
as the ORACLE the implementation must reproduce. This is ground truth, not a model.

IMPLEMENTATION (NOT yet written — deliberately, after 2 falsified rushes): it is a
real directional-routing feature, because discovery has NO uplink-awareness today
(Discovery.__init__ takes no default dev/gw) AND fixdefault runs AFTER the walk in
run_merge_live (live.py:406) — so discovery must read the EXISTING kernel default
route at walk start to learn "which way is up", then select next-hop by direction
instead of HOP_VIA's "via the address that answered". Build it against the
folded oracle and confirm it reproduces those tables + keeps the 23 oracles green
BEFORE shipping. Forwarding itself remains box-verified (the sim's route_egress is
single-table LPM; it cannot prove hop-by-hop arrival) — but the SHAPE target is
now real hardware, so a shape match against the oracle is meaningful.

## 0-NEWEST-3. 2026-06-06 — NEXT_HOP_IDENTITY via + onlink [implemented, gate RED pending box]

John's Seattle5 finding, mechanism nailed via the lan_chain sim docs: 10.250.250.221
is Seattle6's DHCP LEASE on the gateway segment, NOT a dead relay. The working
route (box-proven by ping) uses Seattle6's IDENTITY 10.160.160.1 + onlink. So the
pattern is: downstream /24 -> `via <first-hop node IDENTITY .1> dev <lan> onlink`,
src-LESS — "everything via the first hop, by identity." This INVERTS HOP_VIA_V1
("via the answering/lease address, never .1, never onlink").

IMPLEMENTED in discovery/discovery.py [NEXT_HOP_IDENTITY_V1]:
  - HOP install (~line 226+): when not the attached-onlink case, p_via =
    parent_via if set else net_dot(host_path,1) (the first-hop identity);
    p_onlink = 0 if on local subnet else 1; src cleared (src-less downstream).
  - child_parent_via (~line 257): threads the first-hop IDENTITY down the chain
    (was chosen_via = the lease/answering addr), so all descendants ride the one
    adjacent node by identity.
  - vouch CAND (~line 296): onlink set when cvia off-segment; src field emptied.

SIM/ORACLE STATUS: 5 discovery oracles fail — test_walk, test_hosts, test_runmerge,
test_fabric_integration, test_seattle6. ALL show the SAME intended flip, e.g.
`10.28.28.0/24 via 10.102.60.230 ... metric 22` -> `via 10.28.28.1 ... metric 22
onlink` (src dropped). NOT behavioral regressions — they baseline the OLD
HOP_VIA_V1 shape. Evidence the change didn't break selection: test_fabric_integration
still prints "PASS exercise: 6 packets egress the expected dev" (route_egress LPM
selection intact); only its table-shape compare fails. These 5 must be re-baselined
to the identity+onlink shape ONCE forwarding is confirmed (below). Do NOT re-baseline
blind.

HARD LIMIT (do not pretend otherwise): the simulator verifies route SHAPE and
single-table route SELECTION; it CANNOT execute hop-by-hop forwarding/arrival.
The change works iff each next-hop node answers ARP for its IDENTITY on the shared
segment. PROVEN on John's Seattle fabric (the pings). UNVERIFIED for NY — NY-2's
identity 10.28.28.1 must be ARP-reachable on the 10.102.60 wire, else this
black-holes NY. BOX TEST before trusting: on a node, install
`ip route replace <remote/24> via <next-hop-identity .1> dev <lan> onlink` and ping
through it; confirm the next hop answers ARP for its .1 on that wire
(`ip neigh show <identity> dev <lan>`).

TAR carries the discovery.py change but the discovery gate is INTENTIONALLY RED
(the 5 oracles above) pending the box forwarding confirmation + re-baseline. The
earlier src-pin claim (0-NEWEST-2) is now subsumed: src on downstream links was
confounded; the real lever is via-by-identity + onlink (this entry). Box A/B from
the user confirmed it was the via, not the src.

## 0-NEWEST-2. 2026-06-06 — src-pin is too wide (Seattle5) [SRC_PIN_SCOPE]  OPEN

John's finding from Seattle5 (FrogNetHost.Seattle5, identity 10.250.250.1 on eth0,
WAN uplink wlan1, reaches mesh via first-hop relay 10.250.250.221 on eth0): every
mesh /24 is installed `via 10.250.250.221 dev eth0 src 10.250.250.1 metric 22`.
The src is TOO WIDE. John's rule: "src should only be on WLAN nodes, and then only
on the FIRST HOP of the node. Everything else goes VIA that first hop" — i.e. a
discovered downstream /24 must be src-LESS; only the WLAN node's own first hop /
exit default carries src=identity.

Root cause (read, confirmed): discovery/discovery.py `dev_src()` returns
self_identity for EVERY dev (LAN and wg), and promote() stamps it on every
installed link (discovery.py:151 src=dev_src(dev); :238/:297 into CAND; :331/:362
install_if_changed(...,src)). The connected first-hop route is fine; the
downstream `via` links are the offenders.

DEMONSTRATION: simulation/sim_src_pin.py drives REAL dev_src + REAL
Routes.install_if_changed + REAL FakeKernel. It reproduces Seattle5's table
exactly and asserts John's rule; against the current tree it is RED BY DESIGN
(7/7 downstream routes wrongly pinned) — that red IS the proof the code violates
the rule. It goes green when the fix lands. (Standalone in simulation/, NOT under
run_discovery_oracles, so it does not gate other suites.)

FORK — needs John before the code change: the existing [LEAF_SRC_PIN_V1] oracle
(discovery/test_leaf_src_oracle.py, checkpoints 3/4/4b) DELIBERATELY pins
identity on wg egress because a child beyond a tunnel sourced from the transit /30
(10.253.203.90) is 100% loss (NY1: 10.120.120 via wg2). John's literal rule ("src
only on WLAN nodes") strips src from wired NY1's wg routes -> reverts to /30 src
-> re-breaks NY1. So either (a) wg egress is an EXCEPTION (keep identity src on
wg even on non-WLAN nodes), or (b) NY1's /30 problem gets a different fix (e.g.
assign identity to the wg iface / a routing rule). The unambiguous half (strip
src from downstream LAN-egress via-routes; keep the WLAN exit-default src — the
leaf_src checkpoints 1/5b) is safe to implement now; the wg half is the fork.
NOT YET IMPLEMENTED pending John's call. When fixing: also invert leaf_src
checkpoints 3/4/4b to match (they currently lock in the too-wide behavior).

## 0-NEWEST. 2026-06-06 — handshake-keyed teardown w/ 2-cycle grace [HANDSHAKE_GRACE_V3]

Resolved a policy argument (John invited the pushback): "tear a tunnel down when
it's non-responsive" was conflating layers. The only in-merge liveness signal is
the L7 echo (frognet_echo.php via Apache/proxy/DB); a WG tunnel can be L3-alive
(fresh handshake) while the echo blips. Keying TEARDOWN on the echo churns live
tunnels (= the 21:34 Seattle5 failure). Settled by simulator.

Decision, now installed:
  - Per-merge ROUTE/HOST filter keys on the ECHO — already correct in
    discovery/healthcheck.py (dead-echo iface dropped from the walk; no route
    built, no host added). NO CHANGE NEEDED.
  - Daemon TEARDOWN keys on the HANDSHAKE. New testable helper
    internet_tunnels_v3/poll.py `_tunnel_health_verdict()`:
      fresh handshake (< HANDSHAKE_DEAD_SEC)        -> keep_live (echo blips are L7)
      stale, in-place refresh succeeds              -> refreshed (no teardown)
      stale, refresh fails, < grace cycles          -> grace_hold (KEPT, counted)
      stale, refresh fails, >= grace cycles         -> teardown_dead (rebuild later)
      no iface recorded                             -> teardown_no_iface (grace N/A)
    Grace = config.HANDSHAKE_GRACE_CYCLES (default 2; env
    FROGNET_TUNNEL_HANDSHAKE_GRACE_CYCLES; set 1 = pre-V3 first-failure teardown).
    Persistent per-channel consecutive-fail counter in
    /var/lib/frognet-tunnel/handshake_grace.json (config.GRACE_STATE_PATH),
    atomic write, pruned to {} on a healthy fleet, reset on keep_live/refreshed,
    dead-key swept when a channel leaves _active_tunnels.
    Verbose logging at every branch: tag TUNNEL_HEALTH (age, threshold, refresh
    result, fail_cycle=N/grace, action) + PHASE3_TUNNEL_TEARDOWN_SUMMARY now
    carries grace_held=[...]. The pre-V3 inline STALE_HANDSHAKE_PURGE/REFRESH
    loop in _reconcile_bringup_phase was REPLACED by the helper call.

Test: simulation/sim_tunnel_health.py drives the REAL poll._tunnel_health_verdict
(injected age_fn/refresh_fn + real config thresholds) and asserts BOTH actions
and log content; the rejected echo-keyed policy is kept only as a modeled foil.
Also drives REAL discovery.healthcheck.HealthCheck (echo filter) and
discovery.sources.FakeBroker.transits (transit gate). All green. Regressions
green: sim_control_plane (drives real reconcile), live_prober_removal,
planner fallback, live_failures, and the three discovery oracles
(vouch_route / vouch_transit_gate / healthcheck) — NO regression.

NOTE — NY1 (10.102.60.1) "no reach beyond own LAN" is NOT fixed by this and was
never a teardown problem: its black holes (Seattle /24s via 10.102.60.230 / NY-2,
rtt=1000000) come from the VOUCH_TRANSIT_GATE running inert because
/var/lib/frognet-tunnel/node_transit.json is absent/empty (transits() fail-open
-> every lateral vouch promoted unverified). Zero VOUCH_SKIP in the NY1 log
confirms inert. Fix is to POPULATE the transit map (daemon _sync_transit_map from
broker /api/v4/transit-map); then transits(NY-2,Seattle5)=False -> VOUCH_SKIP ->
honest "no route", surfacing the real fault (wg0/BABox + wg2/Seattle5 echo-dead).
Still PENDING John's on-box `cat node_transit.json` + why it's empty. A defensive
own-uplink-gated fail-closed for lateral vouches was discussed, NOT implemented
(would regress test_vouch_route_oracle if not gated on own-uplink).

UPDATE 2026-06-06 (NY-2 log, FrogNetHost.New-York-2, eth0=10.28.28.1/.2,
eth1=10.102.60.230, default via 10.102.60.1=NY-1):
- Confirms inert-gate theory on a 2nd node. NY-2 is LAN-only ("no non-10.x
  default route"); tunnel-setup-v3 declines to register it; tunnel.conf absent
  -> reconcile sys.exit(1) (config.py:328) -> never fetches a transit-map. The
  `frognet_nuke_tunnels` rm -rf /var/lib/frognet-tunnel wiped any map. So
  transits() inert, zero VOUCH_SKIP, NY-1 vouches the whole mesh into NY-2 as
  black holes via 10.102.60.1 — exactly as predicted.
- BUT for NY-2 this is NOT a bug: a LAN-only leaf SHOULD route the mesh via its
  upstream (NY-1). BAMacBook installs via NY-1 at a REAL rtt=742 (NY-1's wg1 up
  + forwarding); only the Seattle /24s are black holes, and only because NY-1
  can't currently forward to Seattle. Those /24s share NY-2's default next-hop
  (10.102.60.1), so they're redundant, not independently harmful. Nuking /
  re-running setup can't change this — NY-2 is correctly LAN-only.
- KEY NUANCE: a populated transit map fixes NY-1 (NY-2 is unregistered ->
  transits(NY-2,*)=False -> suppress NY-1's lateral black holes) but does NOT
  change NY-2 (NY-1 IS a registered transit for Seattle -> transits(NY-1,
  Seattle5)=True -> vouch installs). The map encodes "CAN relay" (channel
  exists), not "is relaying right now" (echo passing), so it never suppresses a
  route through a transiently echo-dead tunnel. The teardown-grace work and the
  transit map are both orthogonal to the actual break.
- ACTUAL FAULT (consistent across both logs): NY-1<->Seattle5 (wg2) and
  NY-1<->BABox (wg0) are echo-dead (code 000, prior ny1.log). databasehost.frognet
  = Seattle5 10.250.250.1, behind the dead wg2 — why the whole NY side can't
  reach the DB. Investigation belongs on the Seattle5 / BABox ends (far-end echo
  / Apache / proxy / DB), not on either NY node.

## 0-NEW. 2026-06-06 — control-plane sim + Seattle5 21:34 teardown fix (read first)

### Root cause (Seattle5 2026-06-05 21:34): NOT a tunnel going quiet.
wg0/1/2 were established and handshaking for 3h; a single in-flight merge's
`_reconcile_bringup_phase` hit a TRANSIENT control-plane failure (one of
`_have_internet` / `_node_has_own_uplink` / broker `my-channels` fetch) and
called `_tear_down_all_kernel_wg_ifaces`, nuking all three live tunnels.
NetworkManager (which manages the wg ifaces) witnessed each RTM_DELLINK, logged
`-> unmanaged 'removed'`, and nm-dispatcher fired a runMerge per removal — those
are the `BAIL lock_held` floods, feedback not cause. `[BROKER_AUTHORITATIVE_V1
2026-06-01]` is the regression: pre-June-1 a broker blip left ifaces alone.

### FIX (internet_tunnels_v3/poll.py): `_tear_down_stale_only`
The three TRANSIENT short-circuits (no_internet/lan_only_node/broker_unreachable)
now protect any iface whose `wg_handshake_age < HANDSHAKE_LIVE_SEC` (180s) — a
handshaking tunnel is positive on-wire proof, a failed local check is not
authority to delete it. Operator `BROKER_DISABLED` and positive broker omission
(orphan/broker_removed) still tear down fully — authority preserved.

### SIM (simulation/sim_control_plane.py): NEW control-plane layer
Models the wg kernel, NetworkManager dispatcher (fires runMerge on every link
add/del), dnsmasq (lease → runMerge), and the reconcile lock, driving the REAL
`poll._reconcile_bringup_phase` (only ping/wg/broker/ip-link/bring-up I/O faked).
6 scenarios PASS: baseline-quiet; transient no_internet & broker_unreachable now
survive; PRE-FIX path reproduces the exact 21:34 teardown + 3× `lock_held`
storm; positive broker-omission still tears down; dnsmasq+NM callbacks drive the
real reconcile. Run: `python3 -m simulation.sim_control_plane`.

### Also: planner.py `[PROVER_FAILURE_GUARD_V1]` (defensive, NOT this incident)
`all_probes_failed` removal no longer yanks a live broker-valid /24 on a .2
prover-only failure. In v25 nothing writes the failures file, so this branch is
currently dormant — kept as a latent-landmine guard.
Regression: `python3 -m simulation.live_prober_removal`.

---

## 0. CURRENT STATE — build 2026-06-05-v25  (NEWEST — read first)

### Recovery: /mnt/user-data/outputs/frognet_FULLTREE_v25.tar.gz (discovery gate 23/0/0)
### discovery.py banner unchanged (build=20260605-f-VOUCHGATE; discovery.py not
### touched this build).

### [SYNC_REQUIRED_TRIGGER_V1] + [NEIGHBOR_SCOPE_V1] — restore over-the-net
### notification the Python cutover dropped, neighbor-scoped.
- WHY: the cutover (runMerge.bash -> `python3 -m discovery.live`) replaced
  mergeHostsAndResolv.bash, which was the ONLY thing that (a) wrote
  /etc/sentinels/sync_required and (b) called propogateNotification. live.py
  imported propagate_notification but never called it; orchestrate.py left
  propagation "wired later". Result: sync_required never set (so every
  `converge_decision sync_required=0`), epidemic gossip dark, nodes converged
  only via their own daemon poll. The bash propogateNotification script was
  intact but orphaned.
- RESTORE (3 wiring points):
  1. discovery/live.py `_commit_hosts`: clears then writes
     /etc/sentinels/sync_required iff the host table (/etc/hosts or
     frognet_hosts) changed this merge. (dnsmasq-forwarder deltas NOT yet wired
     — host-table change is the dominant trigger.)
  2. usr/local/bin/runMerge.bash: after discovery.live, `if [[ -f
     $SYNC_REQUIRED ]]; then propogateNotification; fi` (logs PROP
     sync_required=YES/NO), before the converge_decision read.
- CHANGE — [NEIGHBOR_SCOPE_V1]: notify ONLY directly-attached neighbors instead
  of every .1 in /etc/hosts. Epidemic re-propagation from each receiver still
  reaches the whole mesh; per-node fan-out drops O(fleet) -> O(neighbors).
  - Direct neighbors = distinct `via` next-hops of FrogNet /24 routes on a
    PHYSICAL dev (LAN: captures downstream clients + upstream gateway; relayed
    distant nodes are dests, never next-hops, so they fall out) + the .1 of each
    active WG tunnel peer (channel_name "<Name>-<subnet3>"; AllowedIPs is 10/8
    so unusable). Excludes wg/frognet0, transit /30 (10.253.x), chorus
    (10.254.x), and own addresses.
  - discovery/neighbors.py: pure `direct_neighbor_targets(route_text,
    channel_names, local_ips)` + a `python3 -m discovery.neighbors` CLI (reads
    live `ip route`, active/*.json, local addrs; prints targets).
  - usr/local/bin/propogateNotificationInternal: PEER_IPS now from the helper;
    falls back to the legacy /etc/hosts .1 scan only if the helper exits non-zero.
- ORACLE: discovery/test_direct_neighbors_oracle.py (gate now 23) — real NY1
  (-> NY2 LAN + 3 tunnel peers), 10.120.120 leaf (-> single uplink), Seattle5
  (-> Seattle6 LAN + 2 tunnel peers), + local-addr filtering.
- ON-BOX CONFIRM NEEDED (not in this tree — lives in the web root): the RECEIVE
  side, propogateNotification.php and whatever re-invokes
  propogateNotificationInternal on receipt. The sender curls
  http://<neighbor>/propogateNotification.php?event=<uuid>; verify that endpoint
  and its trigger still exist on each node, else gossip sends but receivers
  don't act.
- DEPLOY: live.py + neighbors.py -> /opt/frognet_semantic/discovery/;
  runMerge.bash + propogateNotificationInternal -> /usr/local/bin/ (chmod +x);
  clear __pycache__. Verify: merge that changes hosts logs `PROP
  sync_required=YES`, then `peer_scan source=direct_neighbors
  direct_targets_found=N` with N small (neighbors only), and per-peer
  `peer_result ip=… rc=…`.

---

## 0-PREV. build 2026-06-05-v24  (VOUCH_TRANSIT_GATE — see below)

### Recovery: /mnt/user-data/outputs/frognet_FULLTREE_v24.tar.gz (discovery gate 22/0/0)
### discovery.py banner build=20260605-f-VOUCHGATE.

### [VOUCH_TRANSIT_GATE_V1] — the real fix for "tunnels chosen for LAN / loop"
- ROOT: walk()'s VOUCH_ROUTE_V1 created a vouch candidate for EVERY host in a
  relay's getHosts (the full propagated list), not just hosts the relay relays.
  A downstream leaf (New-York-2) thus vouched the whole mesh -> looping LAN
  candidates; a tunnel peer that reaches a subnet back through us did the same.
- DISCRIMINATOR (correct, replaces the wrong LAN-vs-tunnel tie-break): keep a
  vouch for child C via relay R only if R actually TRANSITS C's /24.
  - Seattle5->Seattle3 via Seattle6: Seattle6 transits 10.130.130 -> KEEP.
  - Seattle5->Seattle3 via BAMacBook/New-York-1 (wg): they don't transit
    10.130.130 -> SUPPRESS.
  - New-York-1->Seattle5 via New-York-2: New-York-2 is UNREGISTERED (no own WAN
    -> not in broker nodes) -> absent from the transit map -> SUPPRESS.
- IMPLEMENTATION (4 files):
  1. discovery/discovery.py: vouch loop gates on self.broker.transits(host_path,
     cdest); on fail logs VOUCH_SKIP and skips the candidate (recursive walk still
     runs, so a real echo can still win). host_path = relay identity (.1).
  2. discovery/real_backends.py: RealBroker.transits() reads
     /var/lib/frognet-tunnel/node_transit.json {relay-/24-prefix -> [reachable
     /24 CIDRs]}. File absent/empty -> return True (gate INERT). Relay present ->
     dest in list. Relay absent (but map present) -> False.
  3. discovery/sources.py: FakeBroker.transits() (node_transit dict, same
     semantics, default empty -> True). sim/system.py + sim/fabric.py _Broker
     stubs got an inert transits() so existing oracles are unchanged.
  4. broker/frognet_broker_v4.py: GET /api/v4/transit-map (node-facing,
     pond-scoped via pubkey) -> {prefix: owned+transit}. internet_tunnels_v3/
     peer.py: _sync_transit_map() fetches it each poll and atomically writes
     node_transit.json (NODE_TRANSIT_PATH); called in poll_once right after
     _sync_transit_subnets().
- ORACLE: discovery/test_vouch_transit_gate_oracle.py (gate now 22): relay
  transits dest -> KEEP; relay doesn't -> SUPPRESS; relay absent -> SUPPRESS;
  empty map -> INERT (all kept). All other 21 oracles unchanged.
- SAFE TO DEPLOY EARLY: with no node_transit.json the gate is inert (= v21/v23
  behaviour: NY1 correct via wg, Seattle5 still tunnel-vouches). It ACTIVATES
  only once the map is present AND transit_subnets is populated.
- MANDATORY DEPLOY ORDER (the gate STRANDS routes if transit is empty/wrong):
  1. Deploy internet_tunnels_v3/config.py [TRANSIT_FROM_ROUTES_V1] to ALL nodes;
     restart frognet-tunnel-daemon-v3. Confirm broker nodes.transit_subnets is
     correctly non-empty (esp. Seattle5 -> 10.160.160/10.130.130/10.120.120;
     Seattle6 -> 10.130.130; New-York-1 -> 10.28.28). Until Seattle5 advertises
     transit incl. 10.130.130, NY1's only good vouch for Seattle3 (via wg2 ->
     Seattle5) would be suppressed -> Seattle3 unreachable from NY1.
  2. Deploy broker/frognet_broker_v4.py (transit-map endpoint); restart broker.
  3. Deploy peer.py + discovery.py to all nodes; clear __pycache__; runMerge.
     Confirm node_transit.json appears and `[FROGNET-BUILD] ...-f-VOUCHGATE`.
  Verify: NY1 Seattle3/2/BABox via wg (NOT via 10.102.60.230/NY2); Seattle5
  Seattle3/2/BABox via 10.250.250.221 dev eth0 (Seattle6).

---

## 0-PREV. build 2026-06-05-v23  (LAN-beats-tunnel REVERTED; see below)

### Recovery: /mnt/user-data/outputs/frognet_FULLTREE_v23.tar.gz (gate 21/0/0)
### discovery.py reverted to canonical v21 logic (rtt-only promote sort). Banner
### bumped to build=20260605-e-REVERT so the revert is visible in merge logs.

### v22 [LAN_BEATS_TUNNEL_VOUCH_V1] WAS WRONG — REVERTED
- v22 made promote() prefer LAN over tunnel at equal rtt. It fixed Seattle5
  (Seattle3/2/BABox via the eth0 Seattle6 neighbor) but BROKE NY1: NY1's Seattle
  subnets went `via 10.102.60.230 dev eth0` — through NY2, NY1's downstream LAN
  leaf, which does NOT relay Seattle. That's a loop; NY1's correct path is its wg
  tunnel. LAN-vs-tunnel is the WRONG discriminator.
- Real discriminator = whether the vouching neighbor actually RELAYS the dest:
  Seattle6 sits in front of Seattle3 (transits 10.130.130) -> valid LAN vouch;
  NY2 only KNOWS Seattle5 from propagated getHosts (transits nothing relevant) ->
  looping vouch.
- ROOT (design): [VOUCH_ROUTE_V1] in walk() creates a vouch candidate for EVERY
  host in a neighbor's getHosts (full propagated list), not just hosts it relays.
  A downstream leaf (NY2) thus vouches the whole mesh -> looping LAN candidates.
  It only "worked" before because a real tunnel echo (rtt < 1000000) beats the
  vouch; the loop surfaces when the direct echo is absent/transiently failed.
- CORRECT FIX (NOT yet implemented; gated on transit_subnets being live): in
  walk()'s VOUCH_ROUTE_V1 child loop, append a vouch candidate for child C via
  neighbor N only if N transits C's /24. The broker already ships each peer's
  transit_subnets in /api/v4/my-channels (`"transit_subnets": _parse_transit(peer)`),
  so the node has it. Seattle6 transits 10.130.130 -> Seattle5's eth0 vouch stands;
  NY2 transits nothing relevant -> NY1's eth0 vouch for Seattle5 suppressed -> falls
  to wg2. THIS is why transit_subnets matters (v20/v21 thread): it is the authority
  separating a valid LAN relay from a looping leaf vouch.
- SEQUENCING: deploy v21 [TRANSIT_FROM_ROUTES_V1] internet_tunnels_v3/config.py
  first; confirm broker nodes.transit_subnets is non-empty; THEN add vouch
  validation. Doing it now (transit_subnets still []) would suppress ALL vouches and
  strand the legit Seattle5->Seattle3 LAN path too.
- NET STATE on v23 (=v21 discovery logic): NY1 correct again (Seattle via wg);
  Seattle5 still routes Seattle3/2/BABox via a tunnel vouch (the original, lesser
  complaint) until the transit-validated vouch fix lands.

---

## 0-PREV. build 2026-06-05-v21  (node transit_subnets route-derive)

### Recovery: /mnt/user-data/outputs/frognet_FULLTREE_v21.tar.gz (self-contained, gate 21/0/0)

### This build (v21) — transit_subnets NOT SET: REAL root is node-side (v20 was wrong)
- HARD EVIDENCE (broker journal 2026-06-05 22:02): NY1/Seattle5/BABox each
  "POST /api/v4/update-subnets HTTP/1.1" 200 OK — NOT 409, NOT 401. The broker
  ACCEPTS the update; transit_subnets stays []. => the node is POSTing an EMPTY list.
  The v20 broker conflict-409 theory is WRONG: that branch never executes (added is
  empty, so update-subnets returns 200 "unchanged"). The v20 broker edits
  (TRANSIT_SHARED_OK_V1, request_provided_transit=bool) are harmless latent hardening,
  NOT the fix — left in place but demoted.
- REAL root: node `config.discover_transit_subnets()` returned [] because it learned
  transit ONLY by HTTP-probing peers' frognet_echo.php (DHCP-lease downstream +
  connected-/24 .1 upstream). Those echoes fail (runMerge log: TUNNEL_DEAD
  ch=Seattle6 reason=echo_failed_code=503; ch=BABox reason=wrong_peer), and WAN-uplink
  hubs (NY1 eth1, Seattle5 wlan1) own NO connected FrogNet /24 -> no fallback -> [].
- FIX [TRANSIT_FROM_ROUTES_V1] (internet_tunnels_v3/config.py): discover_transit_subnets
  now derives transit from the KERNEL ROUTING TABLE the discovery engine already
  installed (authoritative), not echo probes. A node transits a /24 iff the table has
  `10.A.B.0/24 via <10.x gw> dev <physical, non-wg, non-frognet0>` (LAN-relayed),
  excluding own/10.253/10.254. NY1's table already has
  `10.28.28.0/24 via 10.102.60.230 dev eth0` -> transit ['10.28.28.0/24']; dev=wg2 mesh
  routes + own /24 correctly excluded. Pure helper `_transit_from_routes(route_text,
  local_subnet)` is unit-tested.
- ORACLE: internet_tunnels_v3/test_transit_from_routes.py (run: python3 that file) —
  validates against NY1's REAL ip r + downstream-hub + leaf + WAN-gw/[30 exclusion.
- DEPLOY: copy internet_tunnels_v3/config.py to each node, restart
  frognet-tunnel-daemon-v3; next poll's _sync reads kernel routes -> POSTs real transit
  -> broker stores. Verify: sqlite3 broker.db "SELECT name,transit_subnets FROM nodes".
  CANNOT run on box here (no live routes/broker) -> needs on-box confirm.
- SEPARATE (not chased): reconcile logs "No GROUP_TOKEN ... updates rejected (401)" but
  update-subnets returns 200 — daemon has the token; reconcile reads a different/empty
  config. Cosmetic unless reconcile's own broker calls matter.

---

## 0-PREV. build 2026-06-05-v20  (WRONG ROOT — broker 409 theory; superseded by v21)

### FASTEST RECOVERY (self-contained, no box)
- `/mnt/user-data/outputs/frognet_FULLTREE_v20.tar.gz` = COMPLETE tree (opt/ + usr/,
  __pycache__ excluded). Restore: `mkdir T && tar xzf .../frognet_FULLTREE_v20.tar.gz
  -C T && cd T/opt/frognet_semantic && python3 -m discovery.run_discovery_oracles`
  -> PASS 21/0/0. Discovery gate unaffected by this build (broker-only change).

### This build (v20) — broker transit_subnets NOT GETTING SET (children-behind-tunnel routing)
- John's broker dump: transit_subnets is [] for most nodes (only Seattle6/BABox set).
  transit_subnets is what tells peers which /24s to route through a tunnel to this
  node; empty -> tunnel routing to children is broken.
- Pipeline (traced, grounded): node `config.discover_transit_subnets()` -> reported via
  `peer/poll._sync_transit_subnets()` POST /api/v4/update-subnets -> broker stores
  (UPDATE nodes SET transit_subnets). NOTE: threads.py's /api/v1/advertise WS push is
  DEAD (broker has no websockets, line 5) — update-subnets is the only live setter.
- ROOT (broker, frognet_broker_v4.py update_subnets handler ~2890): the handler called
  `_transit_conflict()` and raised HTTPException(409) if ANY added subnet was already
  declared transit by a DIFFERENT active node. But transit is consumed PER-PEER
  (`_node_subnets_for_side` = peer.owned + peer.transit, fed per-tunnel via my-channels),
  and the node-side route engine resolves multiple paths to a /24 by metric/echo — so
  two nodes transiting the same subnet is MESH REDUNDANCY, not a conflict (e.g. Seattle5
  as Seattle6's downstream-server AND SeattleThree as Seattle6's upstream-client both
  reach 10.160.160). Worse: the 409 rejected the WHOLE update, and
  `_sync_transit_subnets` treats 409 as "retry next cycle", so a node with even one
  shared-path subnet never persisted ANY transit (incl. its non-shared entries like
  SeattleThree's 10.120.120). That is why most nodes stayed []; Seattle6/BABox happened
  to claim only-them subnets and won.
  FIX [TRANSIT_SHARED_OK_V1]: removed the 409. `_transit_conflict` is now log-only;
  update-subnets persists the full validated transit and installs routes for all of it.
- SECONDARY hardening (frognet_broker_v4.py ~1643) [TRANSIT_PRESERVE_V1]:
  `request_provided_transit = bool(req.transit_subnets)` so a register call that pushes
  an empty list preserves the DB value (clearing is update-subnets' job). Defensive;
  not the primary root.
- Broker PARSES (ast). CANNOT run broker here (no FastAPI/DB/netns) -> NEEDS on-broker
  confirm: restart broker, watch a node's next _sync; expect
  "[TRANSIT_SHARED_OK_V1] ... shared transit (allowed)" then transit_subnets populating.
  Re-dump: sqlite3 broker.db "SELECT name,transit_subnets FROM nodes WHERE active=1".
- Discovery gate unaffected (broker-only change): 21 pass / 0 known-fail / 0 regression.

---

## 0-PREV. build 2026-06-05-v19  (route src = identity; children-behind-tunnel src fix)

### FASTEST RECOVERY (self-contained, no box)
- `/mnt/user-data/outputs/frognet_FULLTREE_v19.tar.gz` = COMPLETE tree (opt/ + usr/,
  __pycache__ excluded). Restore: `mkdir T && tar xzf .../frognet_FULLTREE_v19.tar.gz
  -C T && cd T/opt/frognet_semantic && python3 -m discovery.run_discovery_oracles`
  -> PASS 21/0-knownfail/0-reg. No box, no overlay.

### This build (v19) — route SRC = identity (fixes children-behind-a-tunnel pings)
- John on NY1: pings to children behind a tunnel (Seattle2 10.120.120, Seattle6
  10.160.160) = 100% loss; direct peer Seattle5 partially works; LAN child NY2 works.
  Every FAILING route was src-pinned to the TRANSIT /30 (`src 10.253.203.90`): the
  remote child receives the echo from 10.253.203.90 and has NO route back to the /30
  (transits aren't propagated) -> reply dropped. Seattle5's mirror: 10.28.28 via wg2
  src 10.253.203.94 -> Seattle5->NY2 also 100% loss. Identity (10.102.60.1) is
  reachable everywhere, so routes must source from it.
- ROOT CAUSE: `Discovery.dev_src()` returned the transit /30 (dev_src_map) for wg and
  "" for LAN. That src flows into every discovered route (walk line ~139 -> candidate
  -> promote install). FIX: `dev_src()` returns `self.self_identity` for discovered
  FrogNet routes (wg AND LAN); falls back to old behavior only when no identity is
  threaded. Safe because identity_preflight guarantees the node bears its identity.
  WAN default unaffected (handled in fixdefault, its WAN-egress guard still passes).
- This RESOLVES the long-standing leaf_src KNOWN-FAIL (was: LAN leaf sourced from the
  borrowed lease). KNOWN_FAIL is now empty; gate = 21 pass / 0 known-fail / 0 reg.
- Oracles:
  - test_leaf_src_oracle: wg-egress assertion FLIPPED (was "keeps transit src on wg"
    — that was the bug) to "pins identity on wg too"; added NY1 reproduction (wg child
    10.120.120/24 pins src=identity, not the /30). Now PASSES, gating.
  - test_seattle6_oracle: good-path now models wlan0 UP (real `ip r` shows no
    linkdown — it's a live AP); ORACLE_FINAL_S6 winners gain src=10.160.160.1. The
    identity-DOWN breakage case (B) is unchanged and now even richer (identity down ->
    winners AND defaults src-pinned to identity both drop = the doc-5 silent degrade).
- SEPARATE node-side items still open: NY1 dnsmasq advertises 10.250.250 (wrong
  dhcp-range, fix on node); local-hostname fix (v16) still wants on-box confirm.

---

## 0-PREV. build 2026-06-05-v18  (identity preflight: prefix from interface)

### This build (v18) — identity preflight: served subnet comes from the INTERFACE
- John on NY1, `ip a`: eth0 bears 10.102.60.1 + 10.102.60.2 (identity, correct);
  eth1=192.168.1.245 (WAN client); wlan0 down. Preflight still ABORTed demanding
  10.250.250.1/.2. TWO bugs, both now fixed:
  1. (v17) ident_iface was hardwired eth0 — fixed: discovered via mapInterfaces.
  2. (v18) ident_prefix came from a SEPARATE `_ident_prefix_from_dnsmasq()` read that
     returned 10.250.250 (stale/foreign dhcp-range) while eth0 serves 10.102.60. So
     even resolving eth0, decide() compared eth0's 10.102.60.x to 10.250.250 -> FAIL.
- FIX (identity_preflight.py): `resolve_identity_iface` now returns
  `(dev, addrs, served_prefix)` and DERIVES served_prefix from the .1 the chosen iface
  bears (`_served_prefix_of`). `_gather()` uses that iface-borne prefix; dnsmasq/soft
  give only a HINT for which iface to prefer + a soft fallback. Reproduced exact NY1
  `ip a` -> resolves eth0, served=10.102.60, decide=PROCEED (despite the 10.250.250
  hint). decide() itself UNCHANGED.
- SEPARATE ANOMALY to fix on the node (NOT blocking now): NY1's dnsmasq advertises
  10.250.250 (Seattle5's subnet) — looks like a copied dhcp-range; would hand wrong
  leases to DHCP clients on eth0's segment. Preflight no longer depends on it.
- Regression: test_identity_preflight_oracle resolver cases REWRITTEN to real NY1 data
  (identity on eth0, WAN on eth1, wrong dnsmasq hint) — asserts dev=eth0,
  served=10.102.60, PROCEED; plus AP-two-clients->wlan0, borrowed-.191 not-identity,
  unconfigured->("","",""). (v17's fixture had identity on eth1 — fiction; corrected.)
  Gate 20 pass / 1 known-fail / 0 regression, verified standalone.

---

## 0-PREV. build 2026-06-05-v17  (identity iface discovery — superseded by v18 above)
- DURABLE: `/mnt/user-data/outputs/frognet_sim_m1_m6_v17.tar.gz` (cumulative, onto box
  at `/`). RESTORE: `mkdir merged && cp -a box/* merged/ && tar xzf .../v17.tar.gz -C
  merged && find merged -name __pycache__ -type d -prune -exec rm -rf {} +` (clearing
  pycache is part of restore — box ships stale .pyc that shadow restored .py).
  Verified box+v17 from scratch -> gate PASS 20/1-knownfail/0-regression.

### This build (v17) — identity interface must be DISCOVERED, not assumed eth0
- John on NY1: identity_preflight ABORTED claiming `identity interface eth0 (mode=
  wired) is missing 10.250.250.1/.2`. The node is fine — its identity is on **eth1**.
  The bug: `identity_preflight._gather()` hardwired `ident_iface = eth0Name or "eth0"`
  for wired mode. Any physical iface can bear the wired identity and multiple ifaces
  can be DHCP clients, so eth0 cannot be assumed. mapInterfaces is the resolver.
- FIX (identity_preflight.py): new pure `resolve_identity_iface(ifaces, ident_prefix)`
  using the mapInterfaces served-/24 rule — prefer the iface holding
  `<ident_prefix>.1`, else any physical iface where `_owns_served24` (bears a 10.x.1
  /24). `_gather()` now gathers via `RealInterfaceMap().gather()` and resolves through
  it; eth0Name/AP_IFACE override resolution removed. Pure `decide()` UNCHANGED (the
  10-case decision table still passes). Reproduced NY1 (eth0=WAN client, eth1=identity)
  -> resolves eth1 -> PROCEED; unconfigured -> "" -> FAIL (fail-closed preserved).
- NOTE — already-correct, left as-is: `live.py` self_identity is ALREADY discovered
  via `classify()`/FROGNET_FROGNET_DEVS (mode-agnostic), so it picks eth1 too; its
  `eth0_ip` is only a now-moot fallback in `local=(self_identity or eth0_ip,...)`.
  `classify(eth0_name="eth0")` default is fine for NY1: the identity iface is excluded
  from upstream-DEVIPS by `_owns_served24`, and eth0 (the WAN) is the right one to
  exclude. If a future node has its WAN on a non-eth0 iface, that default would need
  the discovered WAN iface — flag if it surfaces.
- Regression: test_identity_preflight_oracle extended with 4 resolver cases (NY1->eth1,
  AP+2 DHCP clients->wlan0, borrowed .191 lease NOT mistaken for identity, unconfigured
  ->unresolved). Gate 20 pass / 1 known-fail / 0 regression.

---

## 0-PREV. build 2026-06-05-v16  (discovery vouch-route + local hostname)

### Tarball / restore
- DURABLE: `/mnt/user-data/outputs/frognet_sim_m1_m6_v16.tar.gz` — cumulative, applies
  onto box at `/`. RESTORE: `mkdir merged && cp -a box/* merged/ &&
  tar xzf .../frognet_sim_m1_m6_v16.tar.gz -C merged` THEN
  `find merged -name __pycache__ -type d -prune -exec rm -rf {} +` (box ships stale
  .pyc that shadow restored .py — clearing pycache is part of the restore, not
  optional). Verified box+v16 from scratch → gate PASS 20/1-knownfail/0-regression.
- Gate: `python3 -m discovery.run_discovery_oracles` (auto-globs discovery/test_*.py).

### This build (v16) — two real-hardware bugs from John's 4-node chain
- **[VOUCH_ROUTE_V1]** (discovery.py): getHosts is the ROUTING AUTHORITY. `walk()`'s
  child loop now establishes a route to EVERY vouched host THROUGH the upstream
  (the path that reached it) and records the host — regardless of a direct echo,
  which FAIL_ECHOs for deep nodes on the borrowed uplink lease (return path breaks)
  while the upstream still forwards. Candidate `kind="vouch"`, rtt=1000000 so a real
  echo candidate (same via/dev, lower rtt) wins when present. `promote()`: a vouch
  installs ONLY at rank 0 (sole/best) — never as a metric-100 fallback (a hub
  vouched by many tunnels would otherwise grow a redundant backup per peer; that was
  the walk_oracle/hosts_oracle over-reach caught by the gate). Vouch winners are
  EXEMPT from [VERIFY_ROUTE_V1] backout (`kind != "vouch"` gate) — a direct alive()
  would fail for the same return-path reason the echo did.
  - get_hosts now surfaces NAMES: `parse_gethosts` → `[(ip,name)]`; `FakeGetHosts`
    normalizes bare-IP legacy setups → `(ip,"")`; sim `_GetHosts` → `(ip,node_name)`.
  - SIM PROOF: lan_chain now converges FULL MESH — Seattle6/3/2 each route to all 8
    hosts (was 4 each). New `test_vouch_route_oracle.py` (echo-fail host still routed
    at metric 22; echo-OK host has no redundant m100; survives a dead verify).
- **Local hostname** (live.py:400): `local=(self_identity or eth0_ip, domain)` was
  `(eth0_ip, domain)`. AP nodes carry identity on wlan0, have NO IPv4 on literal
  eth0 → eth0_ip="" → local_lines emitted a broken self line (every node except
  wired Seattle5 was missing its own `<.1> FrogNetHost.<Name> FrogNetHost`).
  Proven via local_lines("" vs self_identity). **NEEDS ON-BOX CONFIRM** — the sim
  injects `local=` directly so it can't exercise the eth0_ip derivation.

### DROPPED this build
- The runAgain/converge re-run loop idea. John: `runAgain`=concurrent_attempt (a
  merge requested via the web service while one holds the lock) is the CORRECT
  existing semantics; one clean pass usually suffices; a manual second pass does NOT
  fix the deep routes. The deep-route defect was echo-gating, fixed by VOUCH_ROUTE.

### Still KNOWN-FAIL (tracked, non-gating)
- test_leaf_src_oracle: dev_src() returns "" for LAN egress → routes source from the
  lease not identity. NOTE: vouch routes via an ATTACHED upstream happen to inherit
  src=identity (the attached parent's src), but echo routes don't — inconsistent.
  Full fix = dev_src→self_identity for LAN egress; needs a Seattle2 leaf `ip r`.

---

## 0-LEGACY. build 2026-06-05-v9  (netns harness layer; superseded by §0 for discovery)

### Where the work lives (survive-compaction map)
- DURABLE: `/mnt/user-data/outputs/frognet_sim_m1_m6_v10.tar.gz` — the overlay,
  applies onto box at `/` (paths are `opt/frognet_semantic/...`). This is the
  recovery point; everything in it is the source of truth for the sim code.
- EPHEMERAL working tree: `/home/claude/merged/opt/frognet_semantic/` (box tree +
  overlay applied). `/home/claude/box/` = raw extracted box tree. If `/home/claude`
  is wiped, reconstruct: `mkdir merged && cp -a box/* merged/ &&
  tar xzf /mnt/user-data/outputs/frognet_sim_m1_m6_v10.tar.gz -C merged`.
- Repackage after editing merged: tar the file list in §10 + `simulation/baselines`
  + `simulation/failure_scenarios`, bump `SIM_BUILD` in regression_baselines.py.

### Patch ledger (all present in v7; greppable tags)
- [IFNAME_LEN_V1]  veth names `fv{si}_{mi}h/t` (≤15 char IFNAMSIZ). CONFIRMED on box.
- [PYCACHE_PURGE_V1]  run_all.py + live_engine.py wipe project __pycache__ (never
  venv) and set dont_write_bytecode BEFORE any project import. Closes the stale-
  bytecode-shadowing failure mode (old .pyc silently ran old code post-overlay).
  Proven: planted stale pyc → 0 dirs after run, fresh source executes.
- [VETH_RECLAIM_V1]  netns_backend provision does `ip link del fv{si}_{mi}h`
  (quiet) immediately before `ip link add` (sync reclaim — kills the async-reap
  `File exists` collision); teardown deletes host-side veths explicitly instead of
  trusting `ip netns del` cascade. Verified at command level on topo_asym_lan2; CONFIRMED on box run 4 — cleared all 4 failing topos.
- [RECORD_ON_SUCCESS_V1]  baseline blessed ONLY if topo fully reaches. WORKING on
  box: protected asym_lan2 + multilan-bridge good baselines from being clobbered by
  the veth-leak run (that's why hw count was 18 not 16).

### HARDWARE RUN 4 (v7 on box) — 20/20, LOOP CLOSED
v7 applied clean. **All 20 topologies pass on the real kernel** (`ALL REAL-ENGINE
TOPOLOGIES PASS`), all 20 baselines recorded source=hardware, namespaces clean.
VETH_RECLAIM_V1 cleared the last 4 (asym_lan2_wg_lan3, two-/30-admin-mesh-bridge,
two_aps_ham, mixed_iface_no_router).

**RESOLVED — the 4 failures were provisioner artifacts, NOT real committer bugs.**
John flagged they might be real; the data settled it. Once the iface existed (veth
reclaim), all four reach 100% AND their route tables match offline. Confirmed by
ingesting hardware_feedback3 (the v7 bless, 20/20 hardware baselines) and running
the offline regression: `regression_baselines.py` re-converges offline and EVERY
topology matches its hardware baseline + all 3 failure-scenario replays pass
(`ALL REGRESSION CHECKS PASS`). So: zero real-vs-fake divergence across all 20
shapes; FakeIPRoute faithfully reproduces the real kernel's committer output. The
offline gate is now backed by hardware truth for the full set — this is the whole
exercise validated.

v8 = v7 code + the 20 hardware baselines folded into simulation/baselines (was a
16hw/4offline mix; now 20hw). No code change v7→v8; baseline data only.

### NEXT ACTION
Loop is closed for the `frognet_route` (tunnel/snapshot) plane. **SCOPE EXPANDED
(John, run on real hardware):** the production route engine on LAN/gateway nodes is
`discovery/discovery.py` + `discovery/routes.py` (the runMerge `WALK`/`PROMOTE_WINNER`
engine), which is SEPARATE from `frognet_route` and was NOT covered by the netns
20/20. The simulation must reproduce the real `runMerge` runs across the WHOLE
pathway (discovery + routes too).

DISCOVERY PATHWAY

### STANDING RULE — regression scenarios (John)
Every fix lands as a NAMED scenario in the regression suite, capturing the common
breakage and the expected recovery, so it is permanently guarded. The suite is a
catalog of "the kinds of things that commonly screw things up and how we recover."

DISCOVERY GATE (one command): `python3 -m discovery.run_discovery_oracles` — runs all
discovery/test_*.py oracles, gates on exit code, REPORTS known-fails (tracked, not
gating). Wired into simulation/run_all.py as TIER D, so the whole pathway gates as one.

COMMON-BREAKAGE CATALOG (scenarios now in the suite):
- identity interface DOWN (no carrier): kernel rejects src-pinned exit defaults ->
  node has no working default. RECOVERY: bring iface up (shown); LOUD-FAIL merge
  (pending production fix). Scenario: test_seattle6_oracle [B/C].
- relay/uplink goes dark -> children FAIL_ECHO, stale /24 winners persist (no negative
  sweep). Seen doc-3; carryover behavior noted (recovery policy TBD).
- on-segment non-FrogNet client -> NOT_FROGNET (ignored). Covered: walk oracle.
- seeded-but-dead peer (BABox 10.111.11) -> FAIL_ECHO, culled. Covered: walk/seattle6.
- borrowed-lease leaf sources from lease not identity -> return path breaks. KNOWN-FAIL
  (test_leaf_src_oracle); fix pending, needs Seattle2 leaf log to ground.
- dual-IP / identity flip on a peer (Seattle5 241<->250): identity ambiguity, relay
  breaks. RECOVERY: re-run setup_lillypad_v4 collapses to single IP (John confirmed).

### DISCOVERY PATHWAY — status:
- `discovery/sim/` is John's existing fake/real harness (topology builders, fake
  edges echo/rtt/gethosts/broker, FakeKernel with table() readback, `*_oracle.py`
  golden tests). 4/5 oracles pass in-container; `test_leaf_src_oracle` FAILS
  (pre-existing, unrelated to the production runs — needs its own look).
- [DISCOVERY_DOC5_SEATTLE6] DONE: `discovery/repro_seattle6.py` reproduces the real
  Seattle6 runMerge (the hardware `ip r`) through the ported discovery over fake
  edges. Decision lines match the production log verbatim (ARP/WALK/CANDIDATE with
  RTTs 24/109/127/134, 10.111.11.1 FAIL_ECHO, 10.250.250.20 NOT_FROGNET, 4×
  PROMOTE_WINNER); final table == box `ip r` byte-for-byte. Seeded CONNECTED-ONLY,
  so the four /24 winners are computed+installed, not planted.
- [SEATTLE6_LINKDOWN_NEGCASE] DONE: `discovery/repro_seattle6_defaults.py` reproduces
  the doc-5/doc-3 pair with wlan0 link-state as the only variable. Same discovery +
  real `fixdefault.install_exit_defaults` (src-pinned to identity via `_src_args`):
  wlan0 DOWN -> kernel rejects the src-on-dead-iface defaults -> final table == doc-5
  `ip r` exactly (no defaults); wlan0 UP -> identical `src 10.160.160.1` defaults
  accepted. This RESOLVES the doc-5 "missing defaults" mystery: they were pinned to
  10.160.160.1 on the linkdown wlan0 and the kernel won't keep them.
- [FAKEKERNEL_LINKDOWN_SRC] FakeKernel now refuses a route whose preferred-src lives
  on a linkdown/dead connected iface (`_src_iface_down`), modeling that negative case.
  Observable reproduced; exact kernel cause (reject-at-install vs drop-on-carrier) is
  an on-box detail to confirm. 4 discovery oracles + doc-5 10.x repro unchanged.
- [FAKEKERNEL_LINKFLAGS] Fixed FakeKernel fidelity gap: it dropped the kernel
  link-state token (`linkdown`/`dead`) on seed→render. RouteEntry now carries
  `linkflags` and show() re-emits them. Scoped to that allowlist; 4 passing oracles
  unchanged. (kernel.py is now an overlay delta.)

ENV UPDATE (John): doc-3 is NO LONGER AUTHORITATIVE. Seattle5's dual-IP was fixed by
re-running setup_lillypad_v4; 250 is the survivor. doc-3's identity-flip + all-children-
FAIL_ECHO was a transient artifact of that now-gone dual-IP state. FULL DOC-3 REPRO DROPPED.

IDENTITY-INTERFACE RULES (John, standing):
- The identity interface = the .1 of the served FROGNET /24 (hostapd iface), derived in
  live.py:run_merge_live from FROGNET_FROGNET_DEVS[0], regardless of egress iface.
- If the identity interface is DOWN, the system cannot function and the MERGE MUST FAIL
  LOUDLY (nonzero rc, clear error) BEFORE installing routes. Today it does NOT — it derives
  identity from the down iface, proceeds, fixdefault installs src-pinned defaults the kernel
  rejects, exits rc=0 (the doc-5 silent-degrade defect). Detection data already exists:
  _up_devs() keys on state UP/UNKNOWN; a NO-CARRIER iface reports state DOWN.
- SOFT-INTERFACE mode (historical, REMOVED from frognet-netstart): accept a presented identity
  IP on a synthetic iface and run with no inbound connectivity — the explicit exception to the
  loud-fail gate. Restoring it is pending (need old shape: dummy iface? how IP is supplied).
- AP mode: identity on wlan0/1 (install-assigned), eth0 NOT disabled and NOT identity. Already
  honored by frognet-netstart (wired vs wireless) and by self_identity (served /24 .1).

DONE — [VERIFY_ROUTE_V1] post-finalize verifyRoute restored (PRODUCTION):
- John: the bash original called verifyRoute (a targeted ping) after FINALIZING each
  route. The port had dropped it — it validated the pre-promote .2/metric-6 PROBE
  (echo_probe) but never re-checked the finalized .1/metric-22 winner (different route,
  and after [ATTACHED_ONLINK_SRC_V1] a different src too).
- discovery.py promote(): after install_if_changed installs the rank-0 winner, send a
  targeted alive to the dest .1 over that route. Conservative (REFLECT_PROBE_V1 style):
  back the winner out ONLY on an explicit dead verdict, then fall through to the next
  candidate (different dev). Logs VERIFY_OK / VERIFY_FAIL. Edge defaults None -> skip,
  so the other 18 oracles are unaffected.
- New edge: sources.FakeVerify (sim/tests) + real_backends.RealVerify (`ping -I dev -c1
  -W2 <dest1>`, UNVALIDATED on-box) wired into live.py Discovery(verify=RealVerify()).
- REGRESSION: test_verify_route_oracle.py — winner whose finalized route fails verify is
  backed out and the next candidate (diff dev) promoted; clean verify keeps the nearest.
- Gate: 19 pass, 1 known-fail, 0 regression.
- NOTE: this is the safety net for the src change — if src=identity yields a route that
  doesn't actually carry (remote has no return route to the identity /24 yet), verify
  now catches it and backs out + LOGS it, instead of silently installing a blackhole.

DONE — [ATTACHED_ONLINK_SRC_V1] next-hop-only-rule fix (PRODUCTION discovery.py):
- John's root cause: a route whose `via` falls inside its OWN destination /24 (e.g.
  Seattle3's `10.160.160.0/24 via 10.160.160.1`) violates the next-hop-only rule —
  that subnet is one the node is directly ON (its uplink DHCP lease), so it is on-link,
  not reached through a next-hop.
- Fix in discovery.py walk(): when chosen HOP via has dest24_of(via)==dest24, emit
  on-link (via="") with src=self_identity instead of `via <dest's .1>`. Promotes to
  `<sub>/24 dev <iface> scope link src <identity> metric 22` — exactly John's target.
- REGRESSION: test_attached_onlink_oracle.py drives the real walk()+promote() for the
  Seattle3->Seattle6 uplink and asserts (A) on-link src=identity, (B) the `via 10.160.160.1`
  form is gone, (C) a genuine remote one hop further (10.250.250 via Seattle6) is UNCHANGED
  (fix is surgical). test_walk_oracle golden updated: NY1's two via=10.28.28.1/.2 candidates
  (same violation, losing candidates -> final table unchanged) corrected to on-link via=.
- Gate: 18 pass, 1 known-fail, 0 regression.
- HEADS-UP for the hardware retest: this pins src on the ATTACHED route only. Remote routes
  (`<remote>/24 via <parent .1>`) still source from the lease, because dev_src() returns ""
  for LAN egress (the test_leaf_src_oracle known-fail). If deep nodes still don't learn the
  tunnel remotes after this, the next lever is dev_src->self_identity for LAN egress (one line).

DONE — multi-host LAN-chain reproduction (the "deep children don't learn ancestors"
scenario, grounded in the four 2026-06-05 runMerge logs Seattle5/6/3/2):
- sim/lan_chain.py: build_seattle_chain() = 8-node TopologySpec (Seattle5 gateway ->
  guest chain Seattle6->Seattle3->Seattle2; wg remotes NY1/NY2/BAMacBook + dead BABox).
  ChainSystem(System) overrides _echo_carries to model the borrowed-lease return-path
  failure: crossing the gateway<->tunnel boundary, only a node whose uplink lease is on
  the GATEWAY's own /24 (i.e. a DIRECT guest, depth<=1) gets a return-routable echo.
  Grounded in the logs' lease addrs: S6=10.250.250.221 (gw /24, works), S3=10.160.160.191,
  S2=10.130.130.47 (deep /24s, FAIL_ECHO). Structural rule -> stable across cycles.
- system.py: added _echo_carries(observer,query_ip) hook (default True = unchanged) so
  the constraint is injectable without disturbing the rest of the suite.
- test_lan_chain_oracle.py: converges ChainSystem and asserts the OBSERVED split —
  Seattle6 learns NY1/NY2/BAMacBook; Seattle3 and Seattle2 learn NONE of the remotes but
  DO learn their LAN ancestors; and the base (return-path-blind) System WRONGLY gives
  Seattle3 the remotes (documents the fabric gap shapes.py had flagged). In the gate.
- CAVEAT: faithful to the LAN-side logs; exact cross-boundary physics (borrowed-/24 vs
  wg /30-transit) still needs an NY1/BAMacBook `ip r` to settle. Fix evaluated vs this
  scenario either way. Likely shares root with test_leaf_src_oracle (LAN probes unpinned).

DONE — loud-fail gate + soft mode (PRODUCTION; on-box verify the gatherers):
- discovery/identity_preflight.py: pure decide(mode,soft,soft_ip,ident_prefix,ident_iface,
  iface_up,iface_addrs) -> proceed|fail|soft. Real-system gatherers (netmode, ip addr,
  dnsmasq subnet, soft conf) marked UNVALIDATED on-box.
- runMerge.bash: calls `python3 -m discovery.identity_preflight` at the FRONT (after lock+
  iptables+sentinels, before discovery_merge_py); nonzero -> flog_error ABORT + exit. The
  configured identity iface (wired eth0Name / AP wlan0|1) must be UP and bear BOTH .1 and .2;
  else the merge fails loudly instead of degrading silently (doc-5).
- frognet-soft-standalone (usr/local/bin): enable [IP] | disable | status -> writes
  /etc/frognet/soft_standalone.conf. Soft mode = the exception: preflight synthesizes a dummy
  iface (frognet-soft0) bearing .1/.2; no inbound, routing/merge still run.
- REGRESSION: test_identity_preflight_oracle.py — decision table (wired/AP up-with-.1+.2 ->
  proceed; down/missing-.1-or-.2/no-iface -> fail; soft -> synthesize). In the gate (16 pass).

FLAG (separate, from the Seattle5 run): install_offlan_defaults hit rc=2 — it `add`s the WAN
default (192.168.0.1 metric 601) while an onlink default at the same metric already exists ->
collision. Candidate for a replace-or-skip fix + its own scenario.

OPEN (discovery):):
1. [DONE] build_seattle6 folded into discovery/sim/topology.py; canonical suite
   member is discovery/test_seattle6_oracle.py (good/breakage/recovery). Standalone
   repro_seattle6*.py retired.
1b. (was) Fold as a real oracle
   (`test_seattle6_oracle.py`) instead of the standalone repro driver.
2. Add the other production runs as oracles as John sends logs: the Seattle chain
   (Seattle2→3→6→5→Tunnel), NY2→NY1→Tunnel, BABox→Tunnel, BAMacBook→Tunnel.
3. Wire discovery oracles into `run_all.py` so the GATE covers discovery+routes;
   treat the runMerge `ip r` outputs as the hardware baselines (same contract).
4. The exit-default ladder (`fixdefault`, metric 601-603) is installed to the MAIN
   table in the runMerge log but ABSENT from the final `ip r` — discovery oracle
   scope is 10.x only (matches), but the fixdefault discrepancy needs its own
   resolution (separate policy table? later strip? confirm with a node).
5. `test_leaf_src_oracle` pre-existing failure.

NEXT: items 1–3 above (fold + extend + gate), then the frognet_route loop and the
discovery loop are both under the gate. Optional RTT-probe auto-emit still pending.

---

Last validated: all 7 in-container tiers green via `run_all.py`, run against the
REAL box tree (all_all5a) — not a reconstruction — Python 3.12, no venv (stubs).

## HARDWARE RUN 1 (2026-06-05, real netns on FrogNetHost) — RESULTS
First real-kernel bless. Headline: **all 16 topologies that provisioned
successfully produced committed route tables BYTE-IDENTICAL to the offline
FakeIPRoute model** — including LAN topologies (lan_only_chain, mixed,
solo_wlan0_ap, asym_* with LAN segments). Zero real-vs-fake divergence. The
offline gate is now backed by hardware truth for those 16 (baselines re-blessed
source=hardware). This is the validation the whole exercise targeted: FakeIPRoute
faithfully reproduces the real kernel's view of committer output.

The run also SURFACED A SIM BUG (not a FrogNet bug), now fixed:
- [IFNAME_LEN_V1] netns_backend derived veth names from node names
  (`v{si}_{node}_h` / `tmp_{node}_{iface}`), overflowing Linux's 15-char IFNAMSIZ
  for names >=7 chars (tmp_BlackBox_eth1=17, tmp_Seattle_eth0=16). `ip link add`
  rejected the veth -> the LAN iface never existed -> every route over it failed
  ENETUNREACH. The committer's ROUTE_INSTALL_FAILED errors were downstream
  symptoms, NOT a planner/committer fault. Fixed: short index-based transient
  names (`fv{si}_{mi}h/t`), renamed to the real short iface inside the ns. Dry-run
  confirms all ifnames <=6 chars now. The 4 affected topologies (topo_pair,
  topo_pond_with_workers, topo_two_aps_ham_radio, topo_mixed_iface_no_router)
  remain source=offline baselines pending a RE-RUN with the fix — do not trust
  their hardware run-1 numbers (0/4, 27/45, 9/18, 16/32 were provisioning
  artifacts).
- [FINDSPEC_GUARD_V1] transport_tier._have_real_prereqs crashed on the box with
  `ValueError: mysql.__spec__ is None` (mysql namespace-package quirk), failing
  TIER 5. Guarded; the box path now falls through to the loopback self-test so
  TIER 5 still proves the feedback plumbing.

RE-RUN to bless the remaining 4 (or re-bless all 20) on the box, as root:
  FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1 FROGNET_SIM_RECORD_BASELINES=1 python3 live_engine.py
Expect "ALL REAL-ENGINE TOPOLOGIES PASS" now. Send baselines/ back; the offline
regression diff against the run-1 hardware baselines is the real check.

## THE CLOSED FEEDBACK LOOP ([FEEDBACK_LOOP_V1]) — how the sim learns + self-guards
The sim now learns from hardware and catches regressions OFFLINE, no box needed:
- `regression_baselines.py` blesses each topology's observable contract
  (committed /24 route table per node + reachability) into `baselines/<topo>.json`
  and, on every gate run, RE-CONVERGES through the real planner+committer offline
  and DIFFs against the baseline. A mismatch is a regression — surfaced with the
  baseline's source so severity is clear (vs a HARDWARE baseline = real regression;
  vs an OFFLINE baseline = drift since last bless). Proven: reverting the iproute
  metric fix flips 16/20 topologies RED offline with the exact metric-100->22 diff.
- The committed-route table needs NO probe to capture: offline it's the real
  engine over FakeIPRoute; on the box it's `ip route show` readback (real truth).
  So a hardware run BLESSES truth and the offline gate guards against drift from it.
- `failure_scenarios/*.json` are data-driven faults + honest expectations
  (survivors_all_reach / no_phantom_to / partition groups), replayed offline
  through the real engine. Every captured real fault becomes a permanent offline
  test by dropping in one JSON — the sim GROWS without code edits. Seeded with the
  ring/pond/chain cases.
- `model_feedback.apply_calibration()` now runs at gate start, folding measured
  RTTs (model_calibration.json) into frognet_sim.EDGE_RTT_BY_KIND so timing-
  dependent decisions reflect the real network.

How to use it:
- Bless OFFLINE baselines (bootstrap / after an intended behavior change):
    `python3 run_all.py --record-baselines`
- Bless HARDWARE baselines (the gold standard; box, as root):
    `FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1 FROGNET_SIM_RECORD_BASELINES=1 python3 live_engine.py`
  Then commit the updated `baselines/` so the offline gate guards real truth.
- Regression check is automatic: TIER R in `run_all.py` (baselines + scenarios).
- After a hardware run produces `model_calibration.json` (RTT probe — see below),
  the next gate auto-applies it.

REMAINING BOX FILL-IN for the loop: the only artifact a hardware run does NOT yet
auto-produce is the RTT calibration, because the real netns path installs routes
but never times a probe (same root as the TIER 4 CAVEAT). Wire a timed probe
(ping or wg handshake-time) on the real backend -> RunRecorder.rtt(kind, ms) ->
.write(); the consume + apply + regression machinery is already done and tested.

## CHANGELOG (2026-06-05, pre-hardware audit)
Applied the m1_m6 overlay onto the real box tree and validated. Fixes landed in
live_engine.py before any hardware run is trustworthy:
- [SENTINEL_ISOLATION_V1] commit_final's snapshot_path/failures_path defaulted to
  /etc/sentinels (bound at import; the module-global patch didn't rebind them), so
  a box sim run would have WRITTEN route_snapshot.tsv into and TRUNCATED
  discovery_failures.tsv in the LIVE /etc/sentinels the running services use. Now
  passed explicitly from the per-run temp dir. Found by the regression run.
- [NETNS_LIFECYCLE_V1] converge_real now teardown()s the netns testbed. WAS A
  REAL BUG: live_engine.main() runs all 20 builders; node names repeat, so every
  topology provisioned the SAME `fns_<node>` namespaces with NO cleanup. The 2nd
  topology's `ip netns add` would collide and every later `ip netns exec` would
  run inside the STALE namespace from a prior topology -> committer reads a
  polluted table -> meaningless / possibly false-green TIER 4 results, plus
  leftover namespaces after every run. Fix: pre-clean (idempotent) before
  provision + finally-teardown after converge. Now self-cleaning across the 20
  topologies AND across re-runs.
- [EXECUTE_INTERLOCK_V1] The documented two-flag interlock is now real:
  FROGNET_SIM_BACKEND=real without FROGNET_SIM_EXECUTE=1 raises a clear error
  instead of silently shelling out (the real converge can't dry-run — its
  RealIPRoute needs the per-node wrapper scripts a real runner writes).
Verified: committer.py/iproute.py overlay diffs are ONLY the logger reparent and
the FakeIPRoute metric-honor (no logic revert vs the Jun-2 box). frognet_planner_
fallback_tests.py still green (no metric-22 regression).

## TIER 4 CAVEAT — what a green real-kernel run does and does NOT prove
TIER 4 (M2) validates that the committer's `ip route` / `wg set` argv are ACCEPTED
by a real kernel and read back correctly. It does NOT prove packets flow: every
wg endpoint is 127.0.0.1:<port> inside its own netns (the two ends are in
different namespaces and can't handshake across loopback), and reachability is
judged by the harness MODEL's trace_packet, not a real ping. True end-to-end
needs cross-netns-reachable wg endpoints (a veth underlay, not loopback) + a real
probe replacing the model trace. Don't over-trust a green TIER 4 as data-plane
proof — it's route-installation proof.

## 0. The goal (John's words)
A complete, comprehensive representation of a live networking system that tests
discovery + broker + proxy/daemon, as components AND integrated, across many
topologies with success and failure modes, through operation and shutdown — so
code can be deployed with confidence. Rule: **simulate in the container; on real
hardware drop the fakes and run the installed code; feed real-run results back
to refine the offline model.**

## 1. Trees / paths
- Live box tree (ground truth) lives under `/opt/frognet_semantic/` and
  `/usr/local/bin/`. In this container it's reconstructed at
  `/home/claude/work/tree_box/...` from the uploaded `all_all5a.tar`.
- The simulator is `/opt/frognet_semantic/simulation/`.
- The discovery-package track (`/opt/frognet_semantic/discovery/`) was
  ABANDONED per John — do not invest there. The real planner/committer live in
  `/opt/frognet_semantic/frognet_route/`.

## 2. The harness John wants us in
`simulation/frognet_sim.py` (3238 lines) — the comprehensive live sim. Sections:
1 helper parsers (imported live), 2/2a route-discovery topology MODEL +
observe/plan/commit MODEL, 2b 20 topology builders, 2c live merge-cycle
orchestration MODEL, 3 bug regressions, 4/4b orchestration + reboot/poll/
teardown/churn/invariants (the LIFECYCLE model). Runs 120 checks. Imports live
PARSERS from `internet_tunnels_v3` and `usr/local/bin/frognet_monitor_py`.
Run: `FROGNET_PROXY_ROOT=/opt/frognet_semantic FROGNET_BIN_ROOT=/usr/local/bin python3 frognet_sim.py`

KEY FINDING: frognet_sim Section 2a is a *model* of planner/committer, not the
real code. M1 replaces that with the installed planner+committer.

## 3. Milestones — status
- **M1 real planner+committer** — DONE, validated. `live_engine.py` drives the
  REAL `frognet_route.planner.plan` + `committer.commit_final` over an injected
  IPRoute backend across all 20 harness topologies. 20/20 reachability, converge
  2–5 cycles. Seam: `ipr_factory(node)`.
- **M2 real kernel in netns** — provisioner BUILT (real wg keypair/peer/AllowedIPs=10.0.0.0/8 + teardown), dry-run validated (36 cmds for 3-node pond); executes on box. `netns_backend.py`. `FakeIPRoute` here /
  `RealIPRoute(ip_bin=wrapper)` on box. Selector: env `FROGNET_SIM_BACKEND=fake|real`,
  and `FROGNET_SIM_EXECUTE=1` to actually run `ip`/`wg`.
- **M3 real broker in the loop** — DONE, validated. `broker_topology.py` runs the
  REAL `frognet_broker_v4` (web/ns/subprocess stubbed) to decide pond pairings,
  builds a Topology, validates via M1. 3 ponds green.
- **M4 proxy semantic data plane** — decision plane DONE, validated.
  `proxy_dataplane.py` drives real `decide_path_for_target`, `canonical_semantic_key`,
  origin codec. Transport on :9009 + daemon + DB store are BOX-TIER.
- **M5 lifecycle** — model DONE (frognet_sim Section 4/4b). Real systemd lifecycle
  is BOX-TIER.
- **Failure modes** — DONE, validated. `live_failures.py`: kill node/link, re-converge
  through REAL planner+committer, assert reroute + black-hole (no phantom route) +
  honest partition (no false reach). ring/broker-pond/chain scenarios green.
- **M6 orchestrator** — DONE. `run_all.py` gates all tiers (now 7). GATE: PASS in-container.
  One command on box: `python3 /opt/frognet_semantic/simulation/run_all.py`

## 4. The fake/real seam per layer (this is the whole architecture)
| layer | fake (container) | real (box) |
|---|---|---|
| kernel routes | `FakeIPRoute` | `RealIPRoute` via netns wrappers (M2) |
| broker transport/ns | stubbed fastapi/pydantic/uvicorn + temp sqlite | live uvicorn + real DB |
| template store | `mysql.connector` stub | real MariaDB store |
| proxy transport | not exercised | `transport_real`/`proxy_main` :9009 |
| provisioning | `DryRunner` (records argv) | `ShellRunner` (executes, root) |
Drop the fakes on hardware; the SAME decision code runs.

## 5. LIVE-CODE changes (only these 3 — everything else is new files in simulation/)
1. `frognet_trace.py` (×3: top-level, frognet_route/, broker/) — gated logging,
   OFF by default. Toggle: `FROGNET_TRACE=1` or `FROGNET_LOG_LEVEL=TRACE`.
2. `frognet_route/committer.py` — logger reparented to `frognet_log` spine
   (try/except fallback). No logic change.
3. `frognet_route/iproute.py` — `FakeIPRoute.install` now honors `r.metric`
   (was hardcoding ROUTE_METRIC=22, flattening the planner's metric-100 fallback
   → period-2 route churn). RealIPRoute already honored it. THIS FIXED the
   convergence oscillation. REGRESSION NOTE: if a frognet_route test suite exists
   on the box (not in our extract), re-run it — anything asserting old wrong
   metric-22-on-fallback will now see 100.
New support files (not live services): `frognet_log.py`, `relog_tree.py`
(tree-wide print→logger migration script; run on box for daemon/proxy/broker).

## 6. OPEN ITEMS (next work, in priority order)
1. DONE — M2 provisioner now generates real wg keypairs, peers with
   AllowedIPs=10.0.0.0/8, has teardown(), AND converge_real now CALLS teardown
   ([NETNS_LIFECYCLE_V1]) so the 20-topology loop and re-runs are self-cleaning.
   `_genkey` shells out on a real runner; placeholders in dry-run. NOT YET
   EXECUTED on a real kernel (needs box). See TIER 4 CAVEAT re: data plane.
2. DONE — feedback loop closed offline ([FEEDBACK_LOOP_V1]): `model_feedback.py`
   applies measured RTTs to EDGE_RTT_BY_KIND (auto-applied at gate start), and
   `regression_baselines.py` blesses per-topology contracts + replays failure
   scenarios, re-converging through the real engine OFFLINE to catch regressions
   with no box. (EDGE_RTT_BY_KIND still GUESSED until a hardware RTT probe writes
   model_calibration.json — see the remaining box fill-in.)
3. DONE — `run_all.py --preflight` checks Linux/root/ip/wg/mysql/proxy/daemon.
4. M4 transport: `transport_tier.py`. Calibration-capture plumbing (loopback
   round-trip -> RunRecorder -> calibration -> apply) VALIDATED here. Real
   proxy_main/daemon round trip is the BOX fill-in (real_round_trip seam; needs
   lz4+mysql+daemon). Loopback sockets work in-container; egress does not.
5. TODO (box fill-in) — wire a timed probe (ping / wg handshake-time) on the real
   backend to RunRecorder.rtt(kind, ms) so a hardware run auto-produces
   model_calibration.json. This is the ONLY feedback artifact not yet auto-emitted;
   the apply + regression machinery that consumes it is done and tested.

## 7. Real contracts learned (don't re-derive; these are load-bearing)
- Observations to `planner.plan` MUST set `source='sync_waveN'`; `_obs_wave`
  reads it. Wave 1 → `_winning_via` returns host_path (dest .1) onlink; wave 2+ →
  returns obs.via (relay .1), no onlink. Empty source = wave 0 = wave-1 shape =
  wrong via for relayed routes. (This was M1 bug #1.)
- `planner.plan` is IDEMPOTENT when fed its own output IF metrics are preserved
  (winner m22 + fallback m100). Verified by isolation test.
- `canonical_semantic_key` ignores HTTP method EXCEPT POST `upsert_by_name`
  sensor-data, which keys on `SensorType`+`MetricName` from the JSON body.
- `_extract_metric_name` requires `SensorName` of ≥4 dot-segments
  (`site.node.sensor.metric`); fewer → empty metric.
- WG tunnel routes are dev-only (`dest dev wgN`, no via); broker sets
  `AllowedIPs=10.0.0.0/8` on every channel. (Standing FrogNet rule.)
- Every node hostname is `FrogNetHost`; identify by subnet or broker pubkey.

## 8. THE VENV — DO NOT DELETE FROM THE LIVE SYSTEM
The live proxy & daemon run under `#!/opt/frognet_semantic/venv/bin/python3`.
Real third-party deps in production: fastapi, pydantic, uvicorn (broker),
mysql.connector (proxy/core store), bs4, lz4, aiortc (SotF). The SIMULATOR runs
without the venv only because it STUBS these. Removing the venv breaks the live
services. To save disk: `pip freeze > requirements.txt`, prune `~/.cache/pip`
and `__pycache__` — never the venv.

## 9. How to run everything
Container: `FROGNET_PROXY_ROOT=<tree> FROGNET_BIN_ROOT=<bin> python3 simulation/run_all.py`
Box, real backends: prepend `FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1` (as root).
Individual tiers: `python3 simulation/{live_engine,broker_topology,proxy_dataplane,netns_backend}.py`
Silence logs: default. Verbose: `FROGNET_LOG_LEVEL=DEBUG` or `FROGNET_TRACE=1`.

## 10. Delivered bundles (in /mnt/user-data/outputs)
`frognet_sim_m1_m6.tar.gz` (now includes transport_tier.py, model_feedback.py, SIM_STATUS.md) — all simulation/ files + the 3 live-code changes +
frognet_log.py/relog_tree.py. Apply over `/opt/frognet_semantic/`.
