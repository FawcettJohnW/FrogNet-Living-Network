# T3b brief — the broker out of the installer, scripts, units, web, simulation and the repo

Tree /home/claude/clean (git; T0..T3a committed — read docs/cleanup-2026-10/T3a.md first, especially "For T3b").
Edit in place, do NOT commit. John's rules: no fallbacks (one mechanism; a failed step is logged and retried on the
next event), nothing new in /usr/local/bin, builds logged, code is the truth. After this: two modes only, LAN-only and
region; the tunnel daemon owns its WireGuard key (T3a); nothing anywhere talks to a broker.

## Do
1. The broker tree: `git mv opt/frognet_semantic/broker/var/www/html site` (the public website bundle; it is not broker
   code — keep it whole, only move it), then `git rm -r` the rest of opt/frognet_semantic/broker (frognet_broker_v4,
   full_broker.tgz, broker units, etc.). Also etc/systemd/system/frognet-broker-v4.service, usr/local/bin/
   frognet-broker-admin.sh, var/www/html/pond_admin.html, the two Croakus broker figures ONLY if the book no longer
   references them (check opt/frognet_semantic/docs/magnum_croakus/build_magnum.js — if referenced, keep; T6 rewrites).
2. Node scripts that only serve the broker: frognet-tunnel-setup-v3.sh, frognet-pond-bootstrap.sh (+ .service/.timer),
   frognet-tunnel-setup.service, frognet-conf-consolidate.sh (keep its one-time job inside the installer's upgrade
   step if any node can still have broker.conf/pond.conf — see 4), frognet-make-gateway.sh, frognet-make-lanonly.sh,
   frognet-unmake-gateway.sh, frognet_internet_watch.sh, change_pond.bash, frognet-config-v4 (check callers first),
   frognet-guid / frognet-node-guid.sh (only the broker-pond part; /etc/fnid creation must survive if anything uses it),
   frognet_nuke_tunnels (check: is it the LAN-only teardown's tool? if region/LAN-only use it, keep), sync_interfaces.sh
   update-subnets POST, internet_tunnels_v3/frognet_chorus.php + var/www/html/frognet_chorus.php,
   frognet_setup_v4_helper.bash broker subcommands (broker_state, apply_broker, clear_broker, register) and the API's
   broker actions (frognet_setup_v4_api.php 112-131) and the setup page broker card (setup_frognet.html 183-191,
   309-327, 583-604; setup_frognet_dark.html the same) — keep wifi/reboot/qr/ssid; the active 91-frognet-connectivity
   hook (installer D7 1244-1258 writes it; the tree copy is all comments: delete both and the F6 check for it);
   frognet_routing_topology.py if T3a left only a region block nothing reads (check).
   Before deleting each: `git grep -n <name>` for callers outside docs; a caller you cannot remove = keep + say why.
3. Installer usr/local/bin/installer/frognet_install.sh:
   - remove --broker-host/--broker-port/--broker-scheme/--pond and their header/usage lines, the E1 "Gateway? [y/N]"
     broker prompt, E3's broker branch (setup_lillypad_v4.bash is still THE LAN setup: call it without a broker; drop
     its BROKER_URL/--pond args, and in setup_lillypad_v4.bash itself remove the broker args and the tunnel.conf
     broker writes 730-783 except what LAN/region still read), the frognet-tunnel-setup-v3.sh requirement + run, the
     pond_bootstrap_done lines and timer disable, CORE_SERVICES frognet-tunnel-setup + frognet-pond-bootstrap, the D3
     tunnel.conf seed of BROKER_URL/GROUP_NAME, the identity-box broker line, sudoers for the removed helper commands,
     the */15 registerFrogNetHost.bash cron if it is broker registration (check what it does; if it is the LAN host
     record, keep).
   - the installer must NOT make the WireGuard key (the daemon owns it, T3a) — remove any keygen.
   - UPGRADE step (runs in every install, --preserve too): remove the broker keys from /etc/frognet/tunnel.conf
     (BROKER_URL GROUP_TOKEN GROUP_NAME PASSCODE POND_NAME POND_PASSWORD CHORUSES MAX_TUNNELS — check which the
     daemon/region path still reads after T3a and keep those), remove broker.conf/pond.conf, the retired units
     (frognet-tunnel-setup, frognet-pond-bootstrap + timer, frognet-broker-v4, frognet-mediahost*, frognet-reaper),
     /etc/frognet/pond_bootstrap_done, /var/lib/frognet-tunnel/{last_broker_state,broker_channels,channel_map}.json,
     /etc/sentinels/transit_subnets.tsv. Log each removal. Put the same list in a "T3" section of
     usr/local/bin/frognet_prune_removed.sh (see the T2 section's style) so nodes upgraded by untar get it too.
   - the --help must print the whole option list (today `-h` prints lines 2-43 and cuts off).
4. setup_lillypad_v4.bash, frognet_reset.sh, frognet_fixup.sh, frognet-system-tune.sh, conf.sh: broker remnants out.
   discovery/sim/conf_lib_check.py follows conf.sh.
5. Release/packaging scripts (frognet_build_release.sh, frognet_make_build_tar.sh, make_tar.bash, full_tar.bash,
   frognet_world_manifest.sh, frognet_prune_removed.sh): drop broker paths and excludes for files that no longer exist;
   frognet_build_release.sh must not deploy frognet_chorus.php.
6. Simulation: simulation/broker_topology.py (TIER 3) + its run_all entry + live_failures.py use of it;
   test_transit_closure_repro.py; discovery/sim/broker_world.py + proof_plane's REAL BROKER block + selfcheck listing;
   discovery/sim/lifecycle.py broker scenario; frognet_sim.py (TIER 1) broker model: T1 "model suite" must stay green —
   if the broker model is woven into the topology model, replace the broker with the region (hub + RAM manager) in
   the model only if small; otherwise keep it and say exactly why in T3b.md (it is a model, not shipped runtime).
   simulation/frognet_sim.py:38-41 uses the v2 frognet-tunnel-daemon.py (deleted in T1): fix or drop that path.
7. dead-not-broker items T3a listed: poll.py _probe_ip_for_subnet, _onlink_required; config.py GATEWAY_*,
   ECHO_SETTLE_SEC, RECONNECT_*, MERGE_SETTLE_SEC, METRICS_INTERVAL_SEC; region.my_channels reading facts.transit;
   runMerge FROGNET_MERGE_LEGACY_TAIL (the legacy bash tail: a second mechanism nothing sets — remove it and the bash
   scripts only it runs, after checking callers).
8. Every changed file that has a copy under opt/fdf/bin or opt/fdf/src/fdf-source/fdf/bin: keep the copies identical.
9. Docs under opt/frognet_semantic (AI_README, internet_tunnels_v3/AI_README.md, README_FIRST, FROGNET_NOW*): where a
   line tells an operator or an AI to use the broker as current, fix that line to the region or LAN-only; deeper doc
   rewrites are T6.

## Gates (T3a numbers: selfcheck 14/14; discovery 83 + test_license; run_all 3 red: two agent_workload + discovery
## (test_license); netns region-loop PASS, NOKEY=a PASS, LAN-only 19/19, ponds PASS (one timing flake seen), reach 70)
    cd /opt/frognet_semantic (-> /home/claude/clean/opt/frognet_semantic)
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 -m discovery selfcheck
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 discovery/run_discovery_oracles.py
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 FROGNET_PROXY_ROOT=/opt/frognet_semantic \
      FROGNET_BIN_ROOT=/home/claude/clean/usr/local/bin python3 simulation/run_all.py
    cd /home/claude/clean/opt/fdf/src/fdf-source/rinab     # harness now uses the repo's own paths; no arguments
    bash tools/netns-region-loop-test.sh; NOKEY=a bash tools/netns-region-loop-test.sh; bash tools/netns-lan-only-test.sh
    bash tools/netns-ponds-daemon-test.sh
    BRIDGE_KT=/home/claude/frognet-android/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt \
      FDF_TUN=/home/claude/frognet-android/tun/out/fdf-tun-x86_64 bash tools/netns-reach-test.sh
    bash -n every changed shell script (the installer too); py_compile every changed .py
    Installer smoke: `bash usr/local/bin/installer/frognet_install.sh --help` prints every option; grep shows no
    reference to a deleted file (write a quick check: every /usr/local/bin/<x> or /opt/... path the installer names
    exists in the tree or is created by it).
    STATIC GATE (write it as a script in docs/cleanup-2026-10/static-no-broker.sh and run it): no match for
    -i "broker" | BROKER_URL | GROUP_TOKEN | PASSCODE | POND_PASSWORD | /api/v4 | frognet_chorus | pond_bootstrap
    in shipped runtime: etc/ usr/ var/ opt/frognet_semantic (excluding docs/, *.md, tests named test_*, simulation/
    models you kept with a reason) and opt/fdf (excluding src/fdf-source/docs and *.diff). Every allowed survivor is in
    an explicit allow-list in the script with its reason.

Write docs/cleanup-2026-10/T3b.md: removed / changed (one line each, why), the upgrade removal list, the static gate
script and its output, the gate table vs T3a, anything kept with reason. Final message: 6 lines.
