# WORKLOG — PLAN-PONDS, built on fdf-2026-10-10-r16-a4, merged onto r19 as fdf-2026-10-10-r19-a8

## 1. [HUB_SAYS_ITS_PEERS_V1] (plan §3.6)
- fdf-hub (both copies): after each reconcile, `peers` = the pubkeys it now holds, written into its own facts only when
  the set changed (`facts: peers=N announced`); its fdf-client re-announces; the `p` that comes back finds the same set.
- region.py my_channels: a node's channel to a hub only once that hub's `peers` holds the node's key
  (`REGION: hub X does not hold my key yet; its channel waits for the event that shows it`).
- Kotlin Leaf: up only when my key is in the hub's peers (`tunnel: hub X does not hold my key yet; waiting ...`).
- netns-placement-test.sh, BlackBox leaves -> the phone's first handshake, 15 runs: 255, 226, 144, 161, 171, 160, 139,
  169, 164, 143, 205, 178, 184, 167, 167 ms; all 18/18. Before (a3): 138, 129, 5,129 ms — one run in three lost the race.

## 2. [FORWARD_IS_A_FACT_V1] fdf-client (plan §3.1)
- read_facts(): `/opt/fdf/var/forward` (FDF_FORWARD_FILE) names the forward pond; my RINA_AREA == it, or no forward file:
  facts.json (today); otherwise `{"role":"participant","name","guid"}` — and sync_maps withdraws my mappings (none in
  those facts). inotify reacts to the forward file too; logs `<area> is now FORWARD ...` / `... a PARTICIPANT ...`;
  the join line says `as a PARTICIPANT` when it is one.
- Local test (rina-host r16-a4 + this fdf-client): participant row, no mapping; `forward` = game → mapping registered in
  46 ms, role node; back → `mapping withdrawn`, role participant.

## 3. [ATTENTION_IS_A_TUPLE_V1] (plan §3.4–5)
- fdf-client: a held read on (pond, attention) on its own session; a participant says
  `ATTENTION <area>: <why> (from <who>)` and writes /opt/fdf/var/attention/<area>.json. Write → logged: 21 ms.
- fdf-attention (new, src/fdf_attention.cpp): `fdf-attention "<why>"` writes (pond, attention, w:<guid>) as this
  member; one cell per member (the next notice replaces it); `--clear` removes them.
- rina_region.cpp rina_session_down: the member's node session ends → its (pond, attention, <ns>) cells removed (through
  the RAM, rina_resident_memory(), not the region's records — the first try read the wrong store and swept nothing).
  Checked: notice id 1 present; GameHost's fdf-client killed → cells [].

## 4. [FORWARD_SWITCH_IN_PLACE_V1] daemon (plan §3.2) — written on a4; tested on r19-a8 (§6)
- region.py: FDF_VAR, pond_env(area), read_word/write_word, end_events() (the held fdf-events child kept in _events_proc).
- poll.py run_region_loop: `region-forward` thread (inotify on FDF_VAR, `forward.want`); a different pond with a
  ponds/<area>.env → `REGION: forward A -> B requested`, end_events(); the loop runs its loss path, then _do_switch:
  facts.maps [] (the old pond's client withdraws there), REGION_RAM/AREA set, join.env + site.env rewritten, `forward`
  written (each fdf-client@<area> follows), `REGION: forward A -> B ... joining now`; no 5 s wait.
- fdf/bin: fdf-pond-add (converts an r16-joined node's pond to ponds/<area>.env + fdf-client@<area>), fdf-forward
  (forward.want; waits for the daemon's line), fdf-pond-remove (refuses the forward pond), fdf-ponds.
- Blocked: the real-daemon netns test (the optimization thread's netns-region-loop-test.sh) fails on the r16-a4 BASELINE
  too (converged FAIL; `[Errno 16] Device or resource busy: '/etc/hosts.frognet.tmp' -> '/etc/hosts'`): r16's loop has
  the read-before-watch bug r17 fixed, and the extracted test predates that thread's /etc full-copy fix. Needs r18's
  source to build and test on. Asked John for fdf-source-r18.tgz.

## 5. The phone in two ponds (plan §3.7) — member a5, app type-checked, JVM runner `ponds`
- Member: `onAttention` (held read on (pond, attention) on its own session; others' notices only); `[WATCH_BEFORE_READ_V1]`
  the participant watch registered right after `node`, before any read (found: the hub's `peers` re-announce fell
  between onJoined's world read and the later watch; the leaf waited forever for an event already gone — one run in three).
- Leaf: `isForward`; a participant pond never places itself and takes down only a tunnel IT brought up; `becameForward`.
- App: Identity.ponds/forward, MemberService one session per pond, an Android notification per notice with
  "Bring forward" (ACTION_FORWARD), the pond list (Forward / Remove / Add) in the screen. Type-checks against API 34.
- tools/netns-two-ponds-test.sh (two RAM managers, two hubs, a leaf in each, the phone runner in both): RESULT PASS 14/14,
  five runs in a row. Notice in B -> the phone: 22–32 ms. Bring B forward: hubA drops the phone 37–85 ms, hubB holds it
  111–147 ms, handshake 180–220 ms, first ping through hubB 196–232 ms. Ten switches each run, forward written -> first
  ping answered through the new hub: 107–211 ms; no leftover peer; one session per pond throughout.
- Harness fixes on the way (not product): the JVM wg driver re-adds 10/8 after flushing the address (the kernel drops the
  route with it; GoBackend sets its own); helper leaves wait for their key in the hub's peers, as the rule says.

## 6. On r19 (fdf-2026-10-10-r19-a8): the daemon switch tested, and what that found
- Merged the a4 diff + §1–§5 onto r19 (John's fdf-source-r19.tgz). r19's own netns-region-loop-test and netns-graph-test
  PASS on it before anything else was run.
- rinab/tools/netns-ponds-daemon-test.sh (new): the REAL daemon (internet_tunnels_v3, own mount namespace) on node n in
  two ponds (two rina-hosts, two fdf-hubs, a node in each), fdf-client@pondA + fdf-client@pondB, fdf-facts, fdf-attention.
- First run: switches 1–3 fine, switch 4 back to B left hubB without n's peer. Cause, from the logs: n's facts.json had
  lost name, guid, subnet, pubkey and role (only addrs, maps, gen, view left), so n announced no pubkey in B and the hub
  gave it no peer. The daemon (loop + election thread) and fdf-facts both merge into facts.json through ONE tmp name,
  `facts.json.tmp`: writer 2 truncates writer 1's tmp before writer 1 renames it -> the file reads `{}` -> the next
  merge writes only its own keys. Present in r19 itself (not a pond change); the switches only make it frequent.
  Reproduced alone: three writers, 3,000 merges each, old code -> `{"b": 2997, "c": 2999}` (guid, pubkey gone).
- `[FACTS_ONE_WRITER_AT_A_TIME_V1]` region.write_facts and fdf-facts write_facts: read-merge-rename under
  flock(<facts>.lock), each writer renames its own tmp (pid, thread). Same stress: every key kept.
- The test's end check assumed an even switch count (fixed: the last switch's pond), and it now also checks facts.json
  still carries n's identity after every writer.
- Result S=10: RESULT PASS 24/24. forward.want -> forward written 112 ms; hubA dropped n 157 ms; hubB holds n 201 ms;
  B's route on n 401 ms; n -> b answered 423 ms; ten more switches 356–636 ms each; same daemon pid; one session per pond.
- `[RECV_SAYS_WHY_V1]` (in apply_keepalive_5s.py): frogram's recv_n said "the far end closed the connection" for ANY
  failure, so a keepalive kill (ETIMEDOUT) read as a close. Now 0 = closed; -1 = strerror, and ETIMEDOUT names the
  5 s keepalive. Found because the death test grepped the main loop's line and the attention thread's _exit came first.
- test_region_oracle: the fixture's hubs had no `peers`, so my_channels' [HUB_SAYS_ITS_PEERS_V1] gate gave no channel;
  fixture now carries peers, plus control 2h (a live hub without my key yields none).
- a8 (John: "frognet_echo is not coming back right", New-York-2 answered `New-York-2,10.28.28.1,,`): r19's region echo
  read fields 3-4 from facts.echo.upstream, which nothing writes. `[ECHO_UPSTREAM_FROM_THE_ROWS_V1]`: the facts'
  uplink rows (getFrogNet.bash's getWlan0IP / getWlan1IP), 0.0.0.0 where none, in frognet_echo.php, RegionEcho and
  region_hosts.json. region-loop now checks z -> 198.18.0.3, y -> 10.3.3.20 (its lease on z's LAN). Everything rerun PASS.
- r19-a9: first phone (emulator) on the real mesh: the hub held its peer (10.252.0.1/32, handshake), Seattle5 had no
  route and sent the ping out wlan1. fdf_graph only made a leaf row from facts.addr INSIDE the subnet fallback, which
  a leaf never enters: `[LEAF_ROW_FROM_ADDR_V1]`. region-loop test now carries a phone-like leaf: routed on a and z in
  ~175 ms, pinged from a and y, named; the same test on r19's graph fails 3.
- r19-a10: `[DEVICE_ROW_V1]` monitor row for a leaf: <name> DEVICE ONLINE|DOWN (region session), no echo probe; region_hosts.json/getHosts.php carry role + alive.
- r19-a11 + app a7: the Communicator on the mesh. Node side: static comms-ram/comms-media in /opt/fdf/comms; fdf-comms.sh
  on a node says where they are, (pond, where, w:<guid>) {"comms":{ram,media}} via fdf-where (written only while the
  member is alive, swept with its session). Phone side: the Communicator (comms-android r2) is a screen of the member app;
  "Communicator" reads the forward pond's where cells (Member.services) and opens it with those servers -- nothing typed,
  no droplet default. Proven: region-loop Communicator stage (Kotlin member finds it; Java core watches a node's feed
  through the hub); the merged CommunicatorActivity under Robolectric against live servers + Python "Home": called and
  calling, chat both ways, End -> online, offer withdrawn.
- app a8 + Communicator r3 (2026-10-10 afternoon), on John's hardware:
  - The member app with the Communicator on a **physical phone**: "responsive and worked properly" (John). First
    hardware run of the phone's camera, codecs and audio; until then only Robolectric and the Java core had run.
  - "The Windows communicator thinks it always has a call from the phone": the window rang for any invitation row
    (own=false, read fresh 120 s, never removed) and hid Answer only when a call began; and the C++ Room had no
    leave_call, so the desktop kept offering every session it ever started. `[RINGS_ONLY_WHILE_OFFERED_V1]` (both apps:
    rings only while <= 60 s old and the caller still offers the session; re-checked each tick) and
    `[LEAVE_WITHDRAWS_THE_OFFER_V1]` (desktop hang-up). test_ring PASS 9/9 on a real comms-ram; Android suite PASS.
  - Emulator call started far behind and caught up slowly; cause not found (no frame carried a time).
    `[STAMP_THE_PICTURE_V1]`: desktop senders burn `#frame HH:MM:SS.mmm` into the picture; both viewers show a "here"
    clock; lag = here - stamped. 64 ms measured on one machine. Emulator video "reversed": not explained -- the phone
    un-mirrors only a camera that reports LENS_FACING_FRONT; a9 (comms-android r4) logs `[camera] opened camera <id>:
    facing ...` (`[CAMERA_SAYS_WHICH_V1]`) so the log says which.
  - Build tags: `comms-app-2026-10-10-r3` (console first line, window title), `comms-android-2026-10-10-r4`,
    `fdf-kt-2026-10-10-a9`.
  - Delivery: the Windows app is rebuilt from the WHOLE Communicator tree (communicator-r3-full.zip, extracted above
    `comms\`); an 8-file overlay on John's older tree failed to configure (missing src/pyabi, tools/room_probe.cpp).
- r19-a12 (2026-10-10 night): other networks, reached as they allow -- MERGE-r19-a12.md has the detail.
  - `[IDENTITY_IN_TUPLES_V1]` where cells carry `w:<guid>:<port>`; the region's `resolve` gives the address in the
    reader's scope (node .1, leaf/reacher /32, ENOENT when gone). fdf-where `self:<port>`; Member.endpoint on the phone.
  - `[NET_CONFIG_IS_A_TUPLE_V1]` `(pond, config, net)` from the RAM manager's net.json (reach none|listed|all, map
    allowed|refused); members can't write or remove it (`[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`). No index (John).
  - `[REACH_V1]` fdf-reach + fdf-client@<area> FDF_REACH=1: own interface rch-<h>, own table/rules/chains, 1:1 NETMAP
    aliases from 100.64/10 only if map allowed; refused = down + session ends (exit 4, not restarted); the daemon re-plans
    when the forward pond's routes change. `[REACH_SELF_ALIAS_V1]` the given address is never local (A's phone and n's
    B address were both 10.252.0.1 in the test). Found on the way: a `wgr-*` interface was torn down by the daemon's
    orphan scan (it owns every `wg*` with one peer) -> named `rch-*`.
  - `[PONDS_ARE_LISTED_V1]` getPonds.php; the monitor's Networks section; the dashboard's Networks panel.
  - Build hygiene: tools/build_static_clients.sh (build/static was hand-built; its rina-host was stale), static host into
    build/static, the Windows fdf-client compiles again (broken since PONDS_V1).
  - Reviewed independently: 10 defects found (shell injection from another network's answer, an apply/replan race that
    ended sessions, participant-vs-participant collisions, transient errors taken as refusals, the forward pond refusable
    after a switch, ...) -- all fixed (MERGE-r19-a12.md "Reviewed"; REACH_ONE_AT_A_TIME_V1).
  - netns-reach-test 35/35; region-loop 20/20; ponds-daemon 21/21; hub 25/25; graph 17/17; Kotlin member tests; oracles.
- r19-a13 (2026-10-11): two colliding remote ponds reached at once (netns-reach-test 2d: B and F both serve 10.7.7.0/24,
  as A does; own aliases, own private sides, each answers, re-plans and removal of one leave the other untouched).
  The test found two older daemon bugs, both fixed with oracle checks that fail on the old code:
  - `[APPLIED_IS_THE_KERNEL_V1]` a node whose own session ended lost its hub routes for good (wg0 torn down took them
    from the kernel; the applier still "held" them). Now reconciled with the kernel every pass: g answered 1,759 ms after
    its session came back (before: never).
  - `[ELECT_NODES_ONLY_V1]` the phone (10.252.0.1, ends in .1) was elected the CONTROL host. Leaves and reachers are no
    longer candidates; they are named by a plain line.
  - reach 47/47, region-loop 20/20, ponds-daemon 21/21, oracles + election sim pass. MERGE-r19-a13.md.
- r19-a14 (2026-10-11): the phone as a bridge (PLAN-BRIDGE §3.1) and resolution by identity alone (§3.2); review points
  (ambiguity detectable, alias quarantine with tests, payload addresses documented as a limitation, PLAN-BRIDGE §6.1).
  - fdf-tun (Go, frognet-android/tun): one TUN, a wireguard-go device per scope, 1:1 rewrite + checksums + ICMP quoted
    headers; --tun (netns) / --fd-socket (Android: TUN fd by SCM_RIGHTS, numbered protect requests); JSON set/status.
  - Kotlin: Bridge.kt (ReachPlanner = fdf-reach's rules, AliasLedger on disk, Bridge.decide under one lock, Reach);
    app: BridgeVpn replaces GoBackend; participant facts reach:true + pubkey; MemberService.resolve.
  - Node: fdf-reach resolve w:… with no area (exactly one network may answer, else exit 7), .released ledger with 600 s
    quarantine, covered destinations, most-specific alias in here().
  - Found by the tests: two ponds planned at once took one alias block (PLAN_UNDER_THE_BRIDGE_LOCK_V1); a stale
    wireguard-go from a killed run answering `wg set` (its UAPI socket is not per namespace; the test cleans it).
  - Independent review (2026-10-11): 16 points; fixed: set deadlock under traffic (configure outside the packet lock),
    failed Up never retried, set not all-or-nothing, late protect answer taken for the next socket, dead fdf-tun never
    noticed (now: everything down, every session ends), uncaught errors on worker threads, dropped-by-re-plan blocks not
    quarantined, ledger lost on restart (now on disk), Bring forward taking the new tunnel down, ICMP quoted headers,
    removeIsland half-state, ambiguity counting only reached answers, covered /32s, forward pond re-added as an island,
    other islands' routes in scope, carrier-grade NAT (excluded on Android 13+).
  - Runs on this build: fdftun 20/20, reach 68/68 (stage 7 = the Kotlin bridge: 14), region-loop 20/20 (with the
    Communicator and the phone's code), ponds-daemon 21/21, graph 17/17, hub 25/25, leaf-tunnel 10/10, placement
    18/18, two-ponds 14/14; oracles (graph, graph_apply, hub, region, region backends, gen, election sim,
    reach_alias_test), fdf-member-kt selftest 24/24, fdf-tun go test. MERGE-r19-a14.md.

