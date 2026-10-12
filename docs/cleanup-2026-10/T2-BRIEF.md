# T2 brief — retire the Python Communicator remnants, the mediahost role and the game remnants

Working tree: /home/claude/clean (git; T0/T1 committed). Edit files in place; do NOT commit (the orchestrator reviews
and commits). John's rules: no fallbacks (a failed step is logged and retried on the next event, never a second
mechanism), nothing new in /usr/local/bin, versions logged by the software, code is the truth.

KEEP (do not touch): opt/frognet_semantic/ribbit/examples/communicator/** (the C++ Communicator, incl. python/frogcomms
which is its Python port), opt/fdf/src/frognet-android/**, opt/fdf/bin/fdf-comms.sh, the kart demo (opt/fdf/kart,
fdf-kart-ram.sh), the databasehost role and everything it needs, ffmpeg/libvpx used by rinab/tools/build_comms_*.

MIRROR RULE: opt/fdf/src/fdf-source/frognet/ holds byte-identical copies of some FrogNet files (e.g. core/
frognet_role_elect.py, frognet_service_hosts.py, region_capability.py, role_publish.py, discovery/{elect,hosts,live,
dbhost_switch_sim}.py, internet_tunnels_v3/poll.py, usr/local/bin/frognet_dbhost_elector.py), and
opt/fdf/bin + opt/fdf/src/fdf-source/fdf/bin hold copies of frognet_capability_probe.sh, fdf-region-join, fdf-leave.
Every file you change that has a copy: make the copy identical (cp) and list it. Check with cmp at the end.

## Remove
1. The mediahost role: core/role_registry.py entry + import; core/sotf_handler.py (it is also registered as format
   handler "sotf_media" in core/format_registry.py:53-57 but sniff_body_mode never selects it — remove that entry and
   the re-export in the same change, or the proxy/daemon/S3 oracle fail to start); frognet_service_hosts.py:58
   hard-coded mediahost tuple, ROLE_NOUN media + the "#No media servers on this LAN" marker, media-gate logging;
   frognet_role_elect.py lan-only list; region_capability.py lan_of/set_lan/lan (mediahost LAN bucket) if nothing else
   uses them; discovery/live.py election role list (:887) and dead _ensure_bundle_on_path; discovery/elect.py mediahost
   key + fallback list (callers: poll.py:2544 reads only hosts_changed; dbhost_switch_sim reads r["mediahost"]);
   hosts.py role_barrier_ready docstring; poll.py:2462 mediahost re-elect trigger; core/live.py (a stale twin nothing
   imports — delete the file) ; core/not_frognet.py:392 comment.
2. Units/scripts: frognet-mediahost.service, frognet-mediahost-advertise.service/.timer, frognet-reaper.service (+ the
   Before= in frognet-hosts-safeboot.service:21), usr/local/bin/install_mediahost.sh, frognet_candidate.sh (no caller),
   mediahost parts of frognet_register_candidate.sh and frognet_setup_advertisers.sh (they must still work for
   databasehost), installer frognet_install.sh:2099-2114 mediahost timer lines, fdf-region-join/fdf-leave unit lists.
3. Capability probe media fields (frognet_capability_probe.sh :158-180, :333 av_port, and av_port in _AVCAP_NUMERIC_REQ
   :53-55 in the SAME change, or the probe refuses to publish); check_blob.py, oracle_avcap_cache.sh,
   AVCAP_CACHE_SCHEMA_V1.diff, README_AVCAP_CACHE_SCHEMA_V1.md, sysperf_outputter.bash media block.
   Databasehost scoring reads none of the media fields — confirm.
4. proxy/frognet_media_planes.py, proxy/frognet_media_diag.py and their tests; the dead SotF/old-Communicator simulation
   suite (sim_mediahost, sim_sotf_media_codex, run_movies, run_hardware_loop, test_call_*, test_dl_leg_settle,
   test_downlink_backpressure, test_group_call, test_media_engine_pipe, test_media_stream*, test_self_echo,
   test_server_temporal_subset, test_spatial_band, test_stats_overlay, test_temporal_*, tests_topology,
   test_tuple_address_oracle if it only tests comms_control, test_ladder_rate_v2_oracle, test_media_planes,
   test_outplane_nonblocking_oracle, test_plane_direction_oracle, run_all_tests.py, media_assets/) and the run_all.py
   entries for them; discovery/sim/host_reset_check.py's dead module-level imports (fix the test, it is a real
   host-reset oracle — make it import what exists).
5. usr/local/bin: runServerHeadless, runCaptureHeadless, runClient, package_communicator.py,
   build_linux_communicator.sh, build_windows_communicator.sh; usr/local/share/frognet/docs/SOTF_MEDIA_README.md;
   opt/frognet_semantic/FROGNET_NOW_mediahost_onsegment.md.
6. FrogNetFamily database: installer :1013,1374-1375,2169, frognet_fixup.sh:256-260, setup_lillypad_v4.bash:270-272,
   frognet_reset.sh:148, install_databasehost.sh:120-144, test_clean_install_oracle.py:96 (nothing reads it).
7. Games: ribbit/cpp/tests/reference/core/game_role.py and its wiring in that reference copy (role_registry,
   service_hosts, live, role_elect, unrest_handler, local_read_cache) — the S3 oracle (ribbit/cpp/tests/
   test_semtpl_oracle.py) imports reference core.format_registry, keep it working; core/AI_README.md game_role lines;
   core/unrest_handler.py docstring "SotF or a game"; simulation/games/election_split_merge_sim.py (nothing runs it).
   Keep game names used only as test DATA (sim_cache_soak, test_tuple_key_oracle) and the "game" channel-set label.
8. The six T0 import regressions: test_host_reset (working_memory), test_license (relative import),
   test_malformed_capability_guard (frognet_tuples top-level), test_role_registry_lxmlfree (frognet_service_hosts
   top-level), test_safeboot (same), test_service_election (frognet_role_elect top-level). They import modules as if
   the old Communicator bundle dir were on sys.path. Make them import from core/ (core.frognet_service_hosts etc.) the
   way the shipped code does, and make their assertions match one role (databasehost). Also frognet_register_candidate.sh
   :40,47-48 and frognet_dbhost_elector.py:116-117,131-132 import frognet_tuples/frognet_service_hosts from the
   communicator dir (broken today outside region mode): point them at core/.
9. Tests asserting the two-role world (role_registry_lxmlfree_check exact set, test_role_election_uncoupled_oracle,
   malformed_capability_guard_check av_port, safeboot_check, dbhost_barrier_run, dbhost_switch_sim,
   test_capability_no_age_oracle, test_lan_untunnelled_oracle, test_service_host_consistency_oracle,
   test_alive_retry_oracle, simulation/test_unrest_handler_interface_oracle, simulation/machine.py and netdef.py
   default roles, tests/test_sotf_handler.py, core/test_mediahost_*_oracle.py, discovery/
   test_service_handlers_static_oracle.py, tests/frogsim/frogsim_roles.py, discovery/sim/role_election_check.py,
   service_election_check.py): change to the one-role world or delete the mediahost-only ones.
   frognet_monitor_shared/parser_vectors.json:135 mediahost.frognet line is a "role names are not nodes" case: keep it.

## Gates (run them; all must be at least as green as T0, and the six import regressions should now pass)
    ln -sfn /home/claude/clean/opt/frognet_semantic /opt/frognet_semantic   (already set)
    cd /opt/frognet_semantic
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 -m discovery selfcheck            # T0: 14/14
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 discovery/run_discovery_oracles.py   # T0: 80 pass, 6 regr
    PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 FROGNET_PROXY_ROOT=/opt/frognet_semantic \
      FROGNET_BIN_ROOT=/home/claude/clean/usr/local/bin python3 simulation/run_all.py         # T0: 6 tiers red
    cd ribbit/cpp && sed -n 1,30p qualify.sh   (run the S3 oracle the way qualify.sh does)
    bash -n on every changed shell script; python3 -m py_compile on every changed .py
    cd /home/claude/merge/src/rinab && FROGNET_DIR=/home/claude/clean/opt/frognet_semantic \
      bash tools/netns-region-loop-test.sh ; and tools/netns-ponds-daemon-test.sh      # both RESULT PASS at T0
Also: git -C /home/claude/clean grep -n -i "mediahost\|sotf_handler\|frognet_reaper\|frognet_communicator\|
install_mediahost\|game_role\|FrogNetFamily" -- ':!*.md' ':!docs/**' ':!opt/frognet_semantic/docs/**' must show only
intended survivors (list them with a reason).

Write /home/claude/clean/docs/cleanup-2026-10/T2.md: what was removed, what was changed and why (one line each), the
mirror copies synced, the gate results side by side with T0, and anything you could not do. Final message: 5 lines.
