# T3b — the broker out of the installer, scripts, units, web, simulation and the repo (2026-10-12)

Not committed. Against the T3a commit (a4e538d): 216 paths touched — 104 renamed (the website bundle to `site/`),
50 deleted, 61 modified, 1 new script (+524 / -20,288 lines; 14,500 of the deletions are the broker tree).
Logs: `t3b-logs/`. The T3a numbers in the gate table are T3a's recorded "after" (t3a-logs/after, repo-harness).

After this: two modes only, LAN-only and region. The tunnel daemon owns its WireGuard key (T3a); nothing in the
installer makes one. Nothing in the shipped runtime talks to a broker (the static gate below, 0 violations).

## Removed (one line each, why)

The broker tree
- `opt/frognet_semantic/broker/` (frognet_broker_v4 and its tools, full_broker.tgz, five broker units): the broker.
  Callers outside it: only the sims removed below and the world manifest's exclude lines (removed).
- `opt/frognet_semantic/broker/var/www/html` → **moved whole to `site/`** (`git mv`, 104 files): the public website
  bundle, not broker code. Nothing in the tree referenced its old path except the manifest's vendor line (removed).
- `etc/systemd/system/frognet-broker-v4.service`, `usr/local/bin/frognet-broker-admin.sh`, `var/www/html/pond_admin.html`:
  the broker's unit, admin CLI and admin page; no other caller.
- Croakus figures `airgap_broker.png`, `broker_rendezvous.png`: **kept** — build_magnum.js 1355 and 1903 still place them (T6).

Node scripts (each `git grep`ped first; every caller was another removed file, the installer, or a comment)
- `frognet-tunnel-setup-v3.sh` + `frognet-tunnel-setup.service`: broker enrolment, and the second creator of
  node_private.key. Callers: installer E3, change_pond, make-gateway, internet_watch, pond-bootstrap (all removed).
- `frognet-pond-bootstrap.sh` + `.service` + `.timer`: retried broker enrolment. Callers: installer, the 91 hook.
- `frognet-conf-consolidate.sh`: folded broker.conf/pond.conf into tunnel.conf. Both files hold only broker keys
  (BROKER_URL, POND_NAME, POND_PASSWORD, CHORUSES), so nothing is left to fold: the upgrade step removes them.
- `frognet-make-gateway.sh`, `frognet-make-lanonly.sh`, `frognet-unmake-gateway.sh`: broker enrol / deregister.
- `frognet_internet_watch.sh`: broker register/deregister on uplink change; runMerge does not call it (only
  make-lanonly and unmake-gateway named it).
- `change_pond.bash`: rewrote broker.conf/GROUP_NAME and re-enrolled. No caller.
- `frognet-config-v4`: wrote broker.conf/tunnel.conf broker keys (and a gateways.conf setup_lillypad already writes). No caller.
- `frognet-guid`: wrote NODE_GUID into tunnel.conf and retired it on the broker; nothing reads tunnel.conf's
  NODE_GUID (config.py reads /etc/fnid). No caller.
- `frognet_nuke_tunnels`: callers were frognet-unmake-gateway.sh and T3a's removed autoswitch. LAN-only's teardown is
  the daemon's own (`run_lan_only_loop`, `_reconcile_bringup_phase` LAN-only branch); region never used it. Removed.
- `etc/NetworkManager/dispatcher.d/91-frognet-connectivity` (tree copy, all comments) and the installer's D7 heredoc that
  wrote the live one (it only acted when BROKER_URL was set and pond_bootstrap_done absent), and F6's check for it.
- `frognet_chorus.php` (internet_tunnels_v3/ and var/www/html/): the broker's chorus page. Only caller:
  frognet_build_release.sh D2's deploy (removed).
- `frognet_setup_v4_helper.bash`: subcommands broker_state, apply_broker, clear_broker, register, `_parse_broker_url`,
  the conf.sh source and the POND_CONF / GATEWAYS_CONF / WG_* variables. Kept: reboot, qr, ssid_projection_get/set.
- `frognet_setup_v4_api.php`: actions broker-state, apply-broker, clear-broker. Its `default:` now lists the real
  actions (it had omitted the two SSID ones).
- `setup_frognet.html`: the broker card, loadBroker/toggleBroker, the apply-all broker branch, its CSS, the
  "choose a pond to swim in" line; button "Save & reboot". `setup_frognet_dark.html`: the same (Step 3 card,
  broker-state load, toggleBroker, finalApply step 2). Both scripts pass `node --check`.
- `sync_interfaces.sh`: `push_transits_to_broker` (the update-subnets POST) and its call.

Simulation
- `simulation/broker_topology.py` (TIER 3), its run_all entry, live_failures' `scenario_reroute_broker_pond`: the real
  broker in the loop. (It ran with ~0 checks at T3a: no broker tree to load.)
- `simulation/test_transit_closure_repro.py`: reproduced a bug against the real broker; no caller.
- `discovery/sim/broker_world.py`, proof_plane's REAL BROKER block (it SKIPped: its default broker dir does not exist),
  lifecycle's `BrokerLifecycle` + `scenario_broker_lifecycle`.

T3a's dead-not-broker list
- poll.py `_probe_ip_for_subnet`, `_onlink_required`, and `_ip_to_int` (its only caller was `_onlink_required`).
- config.py GATEWAY_GRACE_SEC, GATEWAY_CHECK_INTERVAL_SEC, GATEWAY_FAIL_THRESHOLD, ECHO_SETTLE_SEC, RECONNECT_BASE_SEC,
  RECONNECT_MAX_SEC, MERGE_SETTLE_SEC, METRICS_INTERVAL_SEC: no reader; their env names are set nowhere.
- region.my_channels: no longer reads the node's own facts.transit (only the broker-era update-subnets wrote it).
- runMerge.bash `FROGNET_MERGE_LEGACY_TAIL` / `LEGACY_TAIL` (bash fixDefaultRoute + manageResolv beside the Python
  merge, nothing set it). **fixDefaultRoute and manageResolv.bash kept**: mergeHostsAndResolv.bash runs both, and it is
  still called by var/www/html/runMerge.php and frognet_transit_watch.sh; oracle_default_route_resolver.sh runs manageResolv.bash.

## Changed (one line each, why)

- installer `frognet_install.sh`:
  - header + `--help`: `-h` printed lines 2-43 and stopped mid-list; three parsed options (--ssid-projection,
    --country, --ap-iface) were never listed. usage() now prints the header from the title to the Phase list:
    all 11 options + -h (t3b-logs/installer-smoke.txt checks each parsed option is in the help).
  - --broker-host / --broker-port / --broker-scheme / --pond: parsing, validation, header lines.
  - E1: the identity box's Broker line and the "Gateway? [y/N]" broker prompt.
  - E3: the broker URL / pond logic, the frognet-tunnel-setup-v3.sh requirement and run, the pond_bootstrap_done lines,
    the timer disable. setup_lillypad_v4.bash is called with `<name> <ip> --norestart` only.
  - D3: the tunnel.conf seed (BROKER_URL=, GROUP_NAME=). No tunnel.conf is created: LAN-only needs none (T3a).
  - D7 91 hook, F1 CORE_SERVICES frognet-tunnel-setup + frognet-pond-bootstrap, its note and the timer disable, F6's
    hook check, the sudoers comment (the fragment stays: the helper's wifi/reboot/qr/ssid actions need it).
  - No WireGuard keygen: the only one was frognet-tunnel-setup-v3.sh's; a comment in E3 says the daemon owns the key.
  - **C1c is the upgrade step** (every install, --preserve too): `frognet_prune_removed.sh --apply --only T3`, each removal
    logged; a failure is logged and the next install retries it. Was the consolidation call.
  - Kept: the `*/15 registerFrogNetHost.bash` cron — it is the LAN host record (known_frognets heartbeat on
    databasehost.frognet/api.php), not broker registration.
- `setup_lillypad_v4.bash`: the BROKER_URL positional and --pond, Step N (tunnel.conf BROKER_URL/PASSCODE/GROUP_TOKEN/
  GROUP_NAME/MAX_TUNNELS) and Step N2 (broker keys via conf.sh). Nothing LAN or region reads any of them. A third
  positional is now an error, not silently a broker URL.
- `frognet-node-guid.sh`: kept (installer C1 `--ensure` makes /etc/fnid; config.py and rinab's machine_id read it);
  its broker part went: the retire call in `regenerate`, `_pond_name`, `_broker_url`, the conf variables.
- `usr/local/lib/frognet/conf.sh`: reduced to FROGNET_CONF + `fn_conf_unset`, the one function with a caller (the T3
  upgrade's key removal). fn_conf_get/set/backup and fn_pond_set had only broker callers. fn_conf_unset now returns 1
  (temp file removed) if chmod/chown --reference fails, instead of falling back to chmod 600 / ignoring it.
- `discovery/sim/conf_lib_check.py` follows: fn_conf_unset removes exactly the named keys (every line of them), keeps
  RINA_RAM / RINA_AREA, the comment and mode 600; an absent file is left absent. test_conf_lib_oracle PASS.
- `frognet_prune_removed.sh`: a T3 section (list below), `--only T3`, `FROGNET_PRUNE_ROOT` (run against a scratch
  root), and exit 1 if any removal failed (each failure logged). t3b-logs/prune-t3.txt: dry run, apply, re-run on a
  scratch root — 4 keys unset (RINA_RAM / RINA_AREA / comment / mode kept), 14 paths, node_private.key untouched,
  second run 0.
- `frognet_build_release.sh`: D2 no longer deploys frognet_chorus.php (D2 = the tunnel daemon unit only); excludes for
  broker.conf, pond.conf, frognet-tunnel-setup.sh gone; phase B runs the T3 prune on the build host and stops the
  build if it fails, so retired credentials cannot reach a world tar from a build host that was never upgraded.
- `frognet_make_build_tar.sh`, `make_tar.bash`, `full_tar.bash`, `frognet_world_manifest.sh`: broker paths and excludes for
  files that no longer exist (broker.conf, pond.conf, frognet-tunnel-setup.sh, the broker tree's db/files.zip/vendor,
  pond_bootstrap_done, the 91 hook); comments.
- `sync_interfaces.sh`: `broker_for_peer_ip` → `tunnel_for_peer_ip`, [BROKER_AUTHORITY] → [TUNNEL_CHANNEL_MAP_V1],
  log key broker_channel= → peer_channel= (it reads handshake_rtts.json; the same rename discovery.py got in T3a).
- `simulation/frognet_sim.py:37-41`: the docstring named the v2 frognet-tunnel-daemon.py (deleted in T1); the code
  already imports internet_tunnels_v3/names.py. Now names that; the never-tested local_subnets_from_routes line dropped.
- `simulation/sim_control_plane.py`: SimBroker → SimRegion (it is the sim of region.my_channels); results identical (5 FAIL, the gossip ones, as at T3a).
- `discovery/sim/{fabric,system,topology}.py`, `tests/frogsim/derive_node.py`: the tunnel-state stub `_Broker` → `_Tunnels`, local `broker` → `tunnels`.
- `frognet_postinstall_20260912.sh` (one-shot Sept payload postinstall, no caller): its pond_bootstrap_done report.
- Comments that described the broker as current, reworded to region / LAN-only: 90-frognet-merge, region_capability.py
  ("broker mode" → LAN-only), descend.py, hosts.py, live.py, orchestrate.py, names.py, constants.py, config.py,
  frognet-wg-cleanup, mapInterfaces, mergeHostsAndResolv.bash (its reconcile is the daemon's bring-up-only),
  fln_dashboard32.html, frognet_route/{__init__,observation,planner}.py, shapes.py, kernel_check.py,
  host_reset_check.py, folded_seattle_chain_routes.py, poll.py, oracle_default_route_resolver.sh, fdf-reach and
  fdf_graph.py (both copies, still cmp-identical).
- Docs (lines that told an operator or an AI the broker is current): AI_README.md (internet_tunnels_v3 row, the
  broker/ row, the "broker source is in full_broker.tgz" trap → "There is no broker"), README_FIRST.md (layer list),
  FROGNET_NOW_guid_identity.md (its SUPERSEDED banner named frognet-tunnel-setup-v3.sh as a reader),
  FROGNET_PRIMER_load_me_first.md (AI_README says read it second; banner: its broker steps are history),
  discovery/README.md (two source lines), root README.md (tree map: internet_tunnels_v3 and broker/). Deeper rewrites: T6.
- opt/fdf copies: none of the changed files has a copy under opt/fdf/bin or fdf-source/fdf/bin except fdf-reach and
  fdf_graph.py, edited in both places (cmp-identical). make_deliverables' FROGNET_FILES all still exist.

## The upgrade removal list (frognet_prune_removed.sh T3 section; installer C1c and release phase B run it)

- units, `systemctl disable --now` first: frognet-tunnel-setup.service, frognet-pond-bootstrap.service + .timer,
  frognet-broker-v4/-v3, frognet-metering, frognet-mediahost (+ -advertise .service/.timer), frognet-reaper; then their
  unit files, drop-in dirs and `*.wants` links; `systemctl daemon-reload`.
- /etc/frognet/tunnel.conf keys, key-wise (fn_conf_unset), each logged: BROKER_URL GROUP_TOKEN GROUP_NAME PASSCODE
  POND_NAME POND_PASSWORD CHORUSES MAX_TUNNELS NODE_GUID. Kept: RINA_RAM, RINA_AREA — the only keys the daemon reads
  (config.py CONF_KEYS); nothing else reads tunnel.conf for any of the listed keys. NODE_GUID is added to the brief's
  list: frognet-guid wrote it, nothing reads it.
- files: /etc/frognet/broker.conf, pond.conf, tunnel.conf.broker (T3a's fdf-region-join copy), every broker.conf.*,
  pond.conf.*, tunnel.conf.bak.*, tunnel.conf.20* (backups carrying GROUP_TOKEN/PASSCODE), /etc/frognet/pond_bootstrap_done,
  /var/lib/frognet-tunnel/{last_broker_state,broker_channels,channel_map}.json, /etc/sentinels/transit_subnets.tsv,
  /etc/sentinels/internet_state(.lock).
- the 91 hook, and every file T3a/T3b deleted that untarring cannot: the 12 node scripts above, frognet_chorus.php (both),
  pond_admin.html, /opt/frognet_semantic/broker, broker_world.py, broker_topology.py, test_transit_closure_repro.py, and
  T3a's test_register_on_404_oracle.py, test_transit_from_routes.py, test_vouch_transit_gate_oracle.py.
- Not touched: /var/lib/frognet-tunnel/node_private.key / node_public.key (the daemon's), /etc/fnid.

## Static gate

`docs/cleanup-2026-10/static-no-broker.sh` (run from anywhere). It greps `-i broker` and, case-sensitive,
`BROKER_URL|GROUP_TOKEN|PASSCODE|POND_PASSWORD|/api/v4|frognet_chorus|pond_bootstrap` over etc/ usr/ var/
opt/frognet_semantic opt/fdf (tracked + untracked, binaries skipped), excluding opt/frognet_semantic/docs, *.md, test_*,
opt/fdf/src/fdf-source/docs and *.diff. Every match is printed ALLOWED or VIOLATION; exit 1 on any violation. `site/`
(the website bundle) is outside the scope by the brief's list.

Output (t3b-logs/static-no-broker.txt): **207 allowed, 0 violations**, exit 0. The same script on the T3a commit
(t3b-logs/static-no-broker-on-T3a.txt): 188 allowed, **1618 violations**. A planted `BROKER_URL=x # talk to the broker`
in usr/local/bin is reported as a VIOLATION, exit 1.

Allowed, by reason (each entry in the script's ALLOW list with its path, line regex and reason):

    109  simulation/frognet_sim.py - TIER 1 model (see "Kept" below)
     25  frognet_prune_removed.sh - names the paths and keys it removes
     19  lines carrying [NO_BROKER_V1] / [BROKER_TEARDOWN_V1] / [BROKER_AUTHORITY]: the tag that records the removal
     13  ribbit/cpp/tests/reference/ - frozen 2026-08 Python engine, the oracle ribbit/cpp/qualify.sh runs the C++ port against
      9  rinab/tools/netns-lan-only-test.sh - plants old broker keys, asserts they are ignored and nothing asks a broker
      9  ribbit/examples/lispers.net/artifacts/ - recorded run_all output
      8  BrokerHost / brokerhost.seattle2 - a machine's name in history notes and fixtures
      3  oracle_routing_topology.py - asserts no broker field, and that an old BROKER_URL alone is LAN-only
      7  fdf-facts (2 lines x 2 copies) / fdf-hub (3 copies) - history: the incident behind [HUB_ROW_IS_THE_REGION_EDGE_V1]; what the hub replaced
      1  internet_tunnels_v3/config.py:30 - the docstring naming the old keys it ignores
      1  rinab/tools/netns-region-loop-test.sh - its header (no old broker keys on its nodes)
      1  rinab/src/rina_region.cpp:611 - why 10.252.254.0/24 is never given to a leaf (C++ of the built RAM manager)
      2  exp3-topologies.json / simulation/baselines/topo_mixed_iface_no_router.json - frognet_sim's topology label "no broker"

## Gates (T3a → T3b)

| gate | T3a (recorded) | T3b |
|---|---|---|
| `python3 -m discovery selfcheck` | 14/14 | **14/14** (selfcheck.txt) |
| `discovery/run_discovery_oracles.py` | 83 pass + test_license (42 files) | **83 pass, 1 regression: test_license, 42 files** — same; test_conf_lib PASS on the new contract (oracles.txt, license.txt) |
| `simulation/run_all.py` | 3 red: DDP, aiconnect, discovery (= test_license) | **3 red, the same three**; TIER 3 "broker-decided ponds" (0 checks) is gone (runall.txt) |
| netns-region-loop-test.sh | RESULT PASS, 13 PASS + 1 SKIP | **RESULT PASS, 13 PASS + 1 SKIP** (region.txt) |
| region-loop NOKEY=a | RESULT PASS | **RESULT PASS** (nokey.txt) |
| netns-lan-only-test.sh | RESULT PASS, 19/19 | **RESULT PASS, 19/19** (lanonly.txt) |
| netns-ponds-daemon-test.sh | RESULT PASS (one timing flake seen) | **RESULT PASS, 21 PASS** (ponds.txt) |
| netns-reach-test.sh (BRIDGE_KT, FDF_TUN) | RESULT PASS, 70 | **RESULT PASS, 70** (reach.txt) |
| bash -n / py_compile | all OK | **all OK: 20 shell files (installer included), 32 .py** (syntax.txt); php -l the API; node --check both setup pages |
| installer smoke | — | **--help prints all 11 options + -h; every installer path exists in the tree or is created by it; none names a T3b-deleted file** (installer-smoke.txt) |
| static-no-broker.sh | (1618 violations on T3a) | **0 violations, 207 allowed** |

Not gated, recorded (extra.txt): test_region_oracle PASS; graph_apply_oracle ALL OK; oracle_routing_topology 35 passed;
live_prober_removal PASS; sim_tunnel_health PASS; sim_control_plane 5 FAIL (gossip, as T3a); oracle_default_route_resolver.sh
FAIL (9) — the same 9 on the T3a tree, so not this change.

## Kept, with the reason

- `simulation/frognet_sim.py`'s broker model (TIER 1, "model suite" green). `World.broker_channels` is the channel list
  `merge_cycle` brings up and tears down, and about 40 scenarios (stale entries, phantom channels, a LAN /24 listed as a
  channel, poll-vs-kernel drift, peers appearing/disappearing between cycles) replay broker-era incidents by that name,
  with 23 learned baselines keyed to topology names. Swapping in a hub + RAM manager would be a re-model of the merge
  pipeline, not a small edit. It is a model, not shipped runtime; the static gate allow-lists it by file.
- `frognet_routing_topology.py`'s region block. Nothing in the tree reads its payload's `region` key — and nothing reads
  any other key of that published `<domain>.Routing.Topology` sensor either (no caller runs the script; it is published
  for consumers in any language). `region` states the node's mode from RINA_RAM; it is not a broker remnant, and its
  oracle (35 checks) covers it. Dropping one field of a published payload on "no in-tree reader" would apply equally
  to the whole payload; left for the owner of that sensor.
- fixDefaultRoute, manageResolv.bash, mergeHostsAndResolv.bash, sync_interfaces.sh: the bash merge still has callers
  (runMerge.php, frognet_transit_watch.sh, make_deliverables ships mergeHostsAndResolv.bash).
- /etc/fnid creation (frognet-node-guid.sh --ensure, installer C1): the daemon and rinab read it.
- The Croakus broker figures: build_magnum.js places both (T6 rewrites the book).
- frognet_setup_v4_helper.bash + its sudoers line: reboot / qr / SSID projection, used by both setup pages and installer E7.

## Found, not done (for later)

- `frognet_reset.sh --keep-identity` says it keeps "WG keys", but phase 3 removes /var/lib/frognet-tunnel whole —
  including node_private.key, the daemon's region key (T3a). A reinstall with --keep-identity therefore announces a new
  key. Not broker; a one-line exception in phase 3 would fix it.
- `fdf-hub` (3 copies) still reads a member's `facts.transit` when it has no plan; no node writes that field now.
- `var/www/html/runMerge.php` queues `/usr/local/bin/mergeHostsAndResolv.bash` (the old bash merge), not runMerge.bash.
- `frognet_routing_topology.py` has no caller at all (only its oracle).
- `usr/local/bin/frognet-system-tune.sh` and `frognet_system_tune.sh` are byte-identical duplicates.
- `make_tar.bash` / `full_tar.bash` tar a live / without the T3 prune (frognet_build_release.sh now runs it); a host
  that was never upgraded should run `frognet_prune_removed.sh --apply --only T3` before using them.
- The book (opt/frognet_semantic/docs/magnum_croakus, site/), root README.md prose, AI_READ_FIRST.md rule 11, FROGNET_NOW.md,
  SIM_STATUS.md still describe the broker: T6.
- opt/fdf/dist/*.run and fdf-droplet.tgz are still T2-era builds (T3a note): rebuild with tools/make_deliverables.sh.

## Orchestrator follow-up
- frognet_reset.sh --keep-identity now keeps /var/lib/frognet-tunnel/node_private.key and node_public.key (the
  daemon's key since T3a) and removes the rest of that directory ([KEY_IS_IDENTITY_V1]); tested on a scratch dir.
- Re-run: selfcheck 14/14; netns region-loop PASS; NOKEY=a PASS; LAN-only PASS; static-no-broker 0 violations.
