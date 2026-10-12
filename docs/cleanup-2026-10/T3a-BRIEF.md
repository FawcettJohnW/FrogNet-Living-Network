# T3a brief — the broker out of the node RUNTIME (config, tunnel daemon, runMerge, discovery, FD&F join/leave)

Working tree /home/claude/clean (git; T0-T2 committed). Edit in place, do NOT commit. Rules (John's): no fallbacks — a
failed step is logged and retried on the next event, never a second mechanism; gone is gone; nothing new in
/usr/local/bin; every component says its build in its log; code is the truth. Two supported modes after this:
LAN-only (FrogNet discovery + election, no WireGuard) and region (RINA_RAM set: RAM manager membership, fdf-hub relay).
A node may be in several ponds (opt/fdf/var/ponds/*.env, one fdf-client@<pond> each, the daemon applies the forward
pond's graph) — that must keep working exactly as today.

## The facts you start from (all verified, file:line in the T0 tree)
- region.py is reached only through poll.py broker_get/broker_post (dispatch when RINA_RAM set, 452-455, 508-511);
  run_region_loop (2341-2671) calls the SAME _reconcile_bringup_phase (1709-2050) as the broker loop, and
  _bring_up_tunnel / _rebuild_active_tunnels_from_kernel_and_broker / _persist_last_broker_state / the teardown helpers.
  Those are SHARED: keep them, rename them to say what they do (region_get/region_post, ..._from_kernel_and_plan, etc.).
- Broker-only (delete): _get_opener 441-449, the HTTP branches of broker_get/broker_post, register_with_broker 466-505
  and its 401/404 branch 1716-1755, _sync_transit_subnets 544-600, poll_once 1107-1180, _broker_reachable 1191-1203,
  _have_internet 1206, _node_has_own_uplink 1214, the non-region run_poll_loop body 2675-2713, `broker-reachable` and
  `commit-only` subcommands in __main__.py if nothing live calls them (commit-only: only the legacy
  mergeHostsAndResolv tail, off by default — remove that tail too), frognet_route's broker-channels teardown rule if
  only commit-only reaches it (check; if frognet_route is otherwise live, keep it and drop only the broker list).
- config.py load_config 493-664: exits 1 without tunnel.conf in every mode; BROKER_URL/BROKER_DISABLED/
  _broker_reachable 227-289; region forces BROKER_DISABLED False 534-537; missing keypair sets BROKER_DISABLED 648-658
  (this is why a never-enrolled node cannot join a region); POND from gateways.conf NETWORK_NAME 619-632.
- The ONLY creator of /var/lib/frognet-tunnel/node_private.key today is frognet-tunnel-setup-v3.sh:132-145 (broker
  enrolment). Nothing on the region path makes it.
- LAN-only today: the daemon unit runs run_poll_loop which does nothing useful; discovery.live's `bring-up-only` hits the
  BROKER_DISABLED gate, tears down all wg, writes an empty handshake_rtts.json and touches
  /etc/sentinels/tunnel_bringup_ready. Keep that LAN-only behaviour (teardown + empty rtts + sentinel), under a name
  that says LAN-only.
- runMerge.bash: 95-155 autoswitch on broker reachability (region skips it at 121-126); 378-396 pond-bootstrap when
  BROKER_URL set. Both go. Region skips stay.
- discovery: RealBroker (real_backends.py:197) and FakeBroker (sources.py:78) are LOCAL FILE readers (handshake_rtts,
  active/*.json), used in every mode: rename (e.g. TunnelState / FakeTunnelState) and keep. RealBroker.transits() reads
  node_transit.json, which nothing writes now: remove that filter and its file. discovery.py BROKER_AUTHORITY 601-613,
  715-729: rename to what it is (the tunnel channel map).
- runMerge/discovery.live calls `internet_tunnels_v3 bring-up-only` (live.py 550-587, not in region mode).
- The FD&F source mirror opt/fdf/src/fdf-source/frognet/ is a byte-identical copy of 37 tree files laid over nodes by
  the .run. DELETE the mirror directory and make opt/fdf/src/fdf-source/rinab/tools/make_deliverables.sh take those
  files from the repo tree itself (TOP of the repo = the directory containing opt/frognet_semantic, usr, etc, var).
  Keep the list of which files the .run carries explicit in make_deliverables.sh. Keep frognet-ram-discovery.diff only
  if something reads it; otherwise delete it.
- opt/fdf/bin/* are installed copies of opt/fdf/src/fdf-source/fdf/bin/*. Edit the fdf-source copy, then copy to
  opt/fdf/bin, and cmp.
- fdf-region-join: copies tunnel.conf to tunnel.conf.broker (:27) and disables units incl. frognet-pond-bootstrap.timer
  (:31-37); header.sh:16 does the same backup; fdf-leave re-enables those units. The "way back to the broker" goes:
  fdf-leave returns the node to LAN-only.

## Do
1. Keys: the tunnel daemon owns its WireGuard key. In region mode, if /var/lib/frognet-tunnel/node_private.key is
   absent, the daemon creates it (umask 077, wg genkey / wg pubkey; log the public key and that it made it) before
   its first bring-up. This is the one creator. LAN-only needs no key. (The installer stops making it in T3b.)
2. config.py: two modes. Remove BROKER_URL, BROKER_DISABLED, _broker_reachable, GROUP_TOKEN, GROUP_NAME, PASSCODE,
   POND_PASSWORD, CHORUSES, MAX_TUNNELS readers if broker-only. tunnel.conf: keep only what the region/LAN path reads;
   if tunnel.conf is missing, LAN-only is the answer (no exit 1 loop). Old broker keys found in tunnel.conf: log ONE
   line naming them and ignore them (the installer's upgrade removes them in T3b). POND for region.post register:
   take it from what the region path really has (join.env RINA_AREA / ponds), not gateways.conf; if gateways.conf
   NETWORK_NAME is still needed for something else, say what.
3. poll.py: delete the broker-only code, rename the shared code, keep run_region_loop behaviour byte-for-byte in
   effect. The daemon's no-region loop becomes the LAN-only loop (frognet0 + nothing, or exit cleanly if it has
   nothing to do — decide from what the unit and runMerge expect; no polling a broker).
4. runMerge.bash, discovery/live.py, real_backends.py, sources.py, discovery.py, region.py, __main__.py: as above.
5. fdf-region-join, fdf-leave, header.sh (opt/fdf/src/fdf-source/deliverables/header.sh): drop tunnel.conf.broker and
   the broker unit handling; fdf-leave removes everything fdf-join/region-join made (fdf-facts.service, site.env,
   join.env, fdf-client units, ponds) and restarts the daemon into LAN-only. Sync opt/fdf/bin copies.
6. Tests: sim_control_plane.py stubs follow the renames; internet_tunnels_v3/test_region_oracle.py and
   test_register_on_404_oracle.py (delete the 404 one with register_with_broker); discovery tests using
   FakeBroker/RealBroker follow the rename; oracles/oracle_routing_topology.py broker block.
7. NEW red-first oracle (write it in opt/fdf/src/fdf-source/rinab/tools/, same harness style as
   netns-region-loop-test.sh, or as an option of it, e.g. NOKEY=a): node a starts with NO node_private.key and no
   tunnel.conf broker keys; it must join the region, create its key, and bring up its hub link (ping across the hub).
   Run it on the T2 commit first (git stash or a worktree) to show it is RED there, then GREEN after your change.
8. NEW LAN-only oracle: two FrogNet nodes in namespaces on one LAN, no RAM manager anywhere, the real daemon + runMerge
   / discovery.live: they discover each other, elect databasehost, and no wg interface exists. If building it in netns
   is too large, make it a simulation oracle that drives the real discovery.live + the daemon's LAN-only path, and say
   which.

## Gates (record before/after in T3a.md; T2 numbers: selfcheck 14/14; discovery 84 pass + test_license; run_all 3 red)
    cd /opt/frognet_semantic  (symlink to /home/claude/clean/opt/frognet_semantic)
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 -m discovery selfcheck
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 discovery/run_discovery_oracles.py
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 FROGNET_PROXY_ROOT=/opt/frognet_semantic \
      FROGNET_BIN_ROOT=/home/claude/clean/usr/local/bin python3 simulation/run_all.py
    cd /home/claude/clean/opt/fdf/src/fdf-source/rinab   (build/ is a symlink to a built tree; tools are the tree's)
    FROGNET_DIR=/home/claude/clean/opt/frognet_semantic bash tools/netns-region-loop-test.sh       # RESULT PASS
    FROGNET_DIR=... bash tools/netns-ponds-daemon-test.sh                                         # RESULT PASS
    FROGNET_DIR=... BRIDGE_KT=/home/claude/frognet-android/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt \
      FDF_TUN=/home/claude/frognet-android/tun/out/fdf-tun-x86_64 bash tools/netns-reach-test.sh  # 70 PASS
    + your two new oracles; bash -n / py_compile on everything changed.
    Static: git grep -n -i -E "broker_url|BROKER_DISABLED|register_with_broker|/api/v4|pond_password|group_token" --
      opt/frognet_semantic/internet_tunnels_v3 opt/frognet_semantic/discovery usr/local/bin/runMerge.bash opt/fdf
      → only intended survivors, each listed with its reason (T3b removes the installer/scripts/units/broker tree).

Write /home/claude/clean/docs/cleanup-2026-10/T3a.md: removed / renamed / changed (one line each with why), the two new
oracles with their red-on-T2 and green-after output, the gate table, survivors for T3b. Final message: 6 lines.
