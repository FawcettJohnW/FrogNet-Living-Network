# T3a — the broker out of the node runtime (2026-10-12)

Not committed. 108 files touched against the T2 commit (bc34ae3): 64 modified, 43 deleted, 1 new
(+826 / -21,307 lines; 20,000 of the deletions are the FD&F source mirror). Logs: t3a-logs/before (the unchanged T2
tree, same sandbox) and t3a-logs/after. The red-first runs used a git worktree of bc34ae3 at /home/claude/t3a-t2
(removed afterwards).

After this the node runs in one of two modes, and the log says which: **region** (RINA_RAM set: the RAM manager
session, the fdf-hub link) or **LAN-only** (no RINA_RAM: FrogNet discovery and the election, no WireGuard).

## Removed (and why)

The tunnel daemon (opt/frognet_semantic/internet_tunnels_v3)
- poll.py `_get_opener` and the HTTP halves of `broker_get` / `broker_post`: broker-only. The region halves did not
  need the wrappers either, so the wrappers are gone and the callers call `region.my_channels()` /
  `region.register_node()` directly.
- poll.py `register_with_broker` and its 401/404 re-register branch in the bring-up phase: broker-only. In region mode
  `region._call` turns an HTTPError into a RuntimeError, so that branch could never run there.
- poll.py `_sync_transit_subnets`, `_read_transit_sentinel`, `poll_once`, `_broker_reachable`, `_have_internet`,
  `_node_has_own_uplink`, the non-region body of `run_poll_loop`: broker-only.
- poll.py `_live_chorus_ips`, `_add_chorus_ip`, `_remove_chorus_ip`, `_sync_chorus_ips`: the broker's chorus IPs
  (10.254.x on frognet0); only `poll_once` called them. frognet0 itself stays (both modes make it, as before).
- poll.py the commit phase: `_reconcile_commit_phase`, `_reconcile_tunnels_inner`, `reconcile_tunnels`,
  `reconcile_commit_only`, `_write_channel_map`, `_write_committer_sidecars`, `_run_committer`,
  `_reconcile_local_state_with_kernel`, `_load_local_state`, `_load_last_broker_state`, and the paths
  CHANNEL_MAP_PATH, BROKER_CHANNELS_PATH, TRACEROUTES_PATH, FROGNET_ROUTE_CMD, LAST_BROKER_STATE_PATH. Only the
  `reconcile` and `commit-only` subcommands reached them, and only mergeHostsAndResolv.bash's commit stage called
  `commit-only`; the region loop never ran the commit phase.
- poll.py `_persist_last_broker_state`: the brief listed it as shared (keep, rename). It is called from the shared
  bring-up phase, but its file (last_broker_state.json) was read only by `_load_last_broker_state`, i.e. only by the
  `reconcile` / `commit-only` processes. With those gone the file had no reader, so the write went too. Region effect:
  one file no longer written; nothing read it.
- poll.py `_verify_one_tunnel`, `_verify_tunnels_parallel`, `_subnet_gateway`, `_read_discovered_hosts`,
  `_measure_tunnel_rtt`: the broker "Poll cycle" section; no caller at T2.
- __main__.py subcommands `reconcile`, `commit-only`, `broker-reachable`: nothing live calls them (runMerge's
  autoswitch was the only `broker-reachable` caller; `reconcile` had no caller).
- config.py `BROKER_URL`, `BROKER_DISABLED`, `_broker_reachable`, `BROKER_PROBE_TIMEOUT_SEC`, `GROUP_TOKEN`,
  `POND` (gateways.conf NETWORK_NAME), `CHANNEL_NAME`, `last_digest`, `POLL_INTERVAL_SEC`, `discover_transit_subnets`,
  `_is_lan_dev` and its prefix tables: broker-only. GROUP_NAME, PASSCODE, POND_PASSWORD, CHORUSES and MAX_TUNNELS
  had no reader in config.py at T2 (they are installer keys; T3b).
- the `exit 1` without tunnel.conf, and the "keypair missing -> BROKER_DISABLED" downgrade (config.py 648-658): the
  latter is why a never-enrolled node could not join a region.
- region.py `get` / `post` (the broker's four paths), `transit_map`, `publish_maps`, the update-subnets handler, and
  the `chorus_assignments` field of `my_channels`: only the broker code called them (update-subnets and transit-map
  were reached only from `_sync_transit_subnets`; `publish_plan_maps` is what the region loop uses).
- test_register_on_404_oracle.py: its subject (`register_with_broker`) is gone.
- test_transit_from_routes.py: it extracts `_transit_from_routes` from config.py, which was not there at T2 either
  (it crashed on T2 with AttributeError); its whole subject is gone now.

runMerge.bash and the merge
- runMerge.bash 95-155 `[TUNNEL_AUTOSWITCH_V1]` (broker-reachable, nuke/bring-up on it) and 378-396
  `[DEFERRED_BROKER_SETUP_V1]` (frognet-pond-bootstrap when BROKER_URL): broker-only. The region skips
  (flush_discovery_cache, makeHostJson, PROP) and the one-time `_REGION` read stay.
- mergeHostsAndResolv.bash: the `commit_only` stage (`internet_tunnels_v3 commit-only`). This is how I read
  "commit-only: only the legacy mergeHostsAndResolv tail ... remove that tail too". runMerge's own
  `FROGNET_MERGE_LEGACY_TAIL` (bash fixDefaultRoute/manageResolv, off by default) does not reach commit-only and is
  not broker, so it is left; it is a second mechanism, and is listed for T3b below.
- discovery/live.py: the write of /etc/sentinels/transit_subnets.tsv. Its only reader was `_sync_transit_subnets`.
  `out["computed_transit"]` stays.

Discovery
- TunnelState (was RealBroker) `transits()` and its `node_transit.json` path; FakeTunnelState (was FakeBroker)
  `node_transit` and `transits()`; the stubs in sim/system.py, sim/fabric.py, test_wave_parallel_equivalence_oracle.py;
  the `[VOUCH_TRANSIT_GATE_V1]` gate in discovery.py. Nothing writes node_transit.json, so on every node the gate
  answered True: the vouch path now runs as it always did in production (the reflect/:9009 loop check stays).
- test_vouch_transit_gate_oracle.py (tested only that gate with fake map data) and sim_tunnel_health's
  `s_ny1_orthogonal` (asserted the same gate).

frognet_route (kept: poll's bring-up route install uses its iproute/planner, and the sims use its committer)
- the broker list only: `plan(broker_channels=)`, `commit_final(broker_channels=)`, `commit-final
  --broker-channels-path`. The rule left is the one every remaining caller already got (an active channel that won
  no /24 is listed for teardown); no node calls the committer now. The prover-failure guard keys on the channel being
  up locally.

FD&F
- opt/fdf/src/fdf-source/frognet/ (the mirror, 40 tracked files). make_deliverables.sh now takes the FrogNet files
  from the repo tree (REPO = fdf-source/../../../.., checked to hold opt/frognet_semantic, usr/local/bin, etc,
  var/www/html), with the shipped list explicit in FROGNET_FILES. The mirror's gen_oracle.py (no tree copy since T1)
  is not shipped. The mirror's opt/fdf/bin/fdf-join and opt/fdf/lib/python were identical to fdf-source/fdf (cmp),
  which the bundle already carries.
- frognet-ram-discovery.diff (in the mirror): no program reads it; only HANDOFF-OPTIMIZATION-r19.md,
  TRANSITION-README.md and r16-to-r19.diff mention it, as history.
- fdf-region-join: the tunnel.conf -> tunnel.conf.broker copy and frognet-pond-bootstrap.timer in the unit list.
- deliverables/header.sh: the same tunnel.conf.broker copy.

## Renamed (and why)

- poll.py `_rebuild_active_tunnels_from_kernel_and_broker` -> `_rebuild_active_tunnels_from_kernel_and_hubs`: it
  adopts kernel wg ifaces whose peer is one of the region's hubs.
- poll.py `run_poll_loop` -> `run_daemon_loop` (dispatch) + `run_lan_only_loop`; the bring-up phase's broker_* names
  and log words -> hub_* (`channels_fetched`, `pubkey_not_a_hub`, `hub_gone_channels`, `hub_channels_unreadable`).
- discovery RealBroker -> TunnelState, FakeBroker -> FakeTunnelState, `broker_for_peer_ip` -> `tunnel_for_peer_ip`,
  `broker_lookup` -> `tunnel_lookup`, Discovery's `broker` argument/attribute -> `tunnels` (positional everywhere;
  41 .py files follow by word-boundary rename). They read the daemon's local files in every mode.
- discovery.py `[BROKER_AUTHORITY]` -> `[TUNNEL_CHANNEL_MAP_V1]` (a peer at the far end of one of this node's own
  channels is named by that channel); its log key `broker_channel=` -> `peer_channel=`.
- region.py `post("/api/v4/register")` -> `register_node()`; `get("/api/v4/my-channels")` -> `my_channels()`.
- usr/local/bin/frognet_routing_topology.py `broker_config()` -> `region_config()`, payload key `broker` -> `region`
  (RINA_RAM from join.env or tunnel.conf). Not on the brief's file list, but its oracle's broker block is, and the
  block tested this function; nothing in the tree reads the payload's `broker` key.

## Changed (and why)

- config.py: two modes (`MODE`), tunnel.conf optional (missing = nothing to read; region from join.env still works),
  only RINA_RAM / RINA_AREA read from it, any other key named in ONE warning (`old broker keys ignored: ...
  [NO_BROKER_V1]`) and ignored. `BUILD = "frognet-tunnel-2026-10-12-t3a"`. PUBKEY is derived from node_private.key
  (`wg pubkey`), one source. `ensure_node_key()`: the one creator of node_private.key (umask 077, wg genkey, atomic
  rename, mode 600; node_public.key beside it; logs `WG_KEY: made ... public key <pub> [DAEMON_OWNS_ITS_KEY_V1]`; a
  failure is logged and retried on the next event).
- poll.py `run_region_loop`: `config.ensure_node_key()` before the first announcement; `_one` retries it at the top of
  every event and re-announces once a late key exists. Otherwise byte-for-byte the T2 loop (diffed: those two hunks
  only).
- poll.py `_reconcile_bringup_phase`: no region -> the LAN-only behaviour kept under its own name: every wg iface torn
  down, empty handshake_rtts.json, /etc/sentinels/tunnel_bringup_ready touched, logged at warning
  (`BRINGUP_PHASE: LAN-only (no region) ... [LAN_ONLY_NO_REGION_V1]`; info lines do not reach the merge log). Region
  with no key -> logged, nothing brought up. Region read failure -> the same stale-only teardown T2 did.
- poll.py `run_lan_only_loop`: frognet0, tear down any wg iface left from a region (fdf-leave stops the daemon with
  SIGTERM, which leaves tunnels; LAN-only has no authority to hold one), say so, wait. It does not exit: the unit is
  `Restart=always`, so exiting would be a restart loop. Joining a region restarts it (fdf-region-join).
- __main__.py: logs `Starting frognet-tunnel-daemon v3.0.0 build <BUILD> (mode <mode>)`; one subcommand,
  `bring-up-only`.
- region.py `register_node()`: facts.pond = RINA_AREA (was gateways.conf NETWORK_NAME). Nothing reads facts.pond.
- fdf-leave: removes everything fdf-join / fdf-region-join made (fdf-client, fdf-facts, fdf-term-serve units, every
  fdf-client@<pond> and the template, join.env, site.env, ponds/, attention/, forward, forward.want, facts.json),
  re-enables the store units region-join stopped (not pond-bootstrap), restarts the daemon into LAN-only. It keeps
  node_private.key (the daemon's; a later join announces the same key). opt/fdf/bin copies synced; every
  fdf-source/fdf/bin file cmp-identical to opt/fdf/bin.
- fdf-region-join: its report greps the daemon's build and WG_KEY lines (was POLL).
- netns-reach-test.sh: getPonds.php and frognet_monitor_py from the tree FROGNET_DIR is in (was the mirror and
  ../../../abuild/fullrepo).
- sim_control_plane.py: stubs follow (region.my_channels for broker_get; REGION_RAM/PRIVKEY for BROKER_DISABLED;
  no _have_internet/_node_has_own_uplink/_persist_last_broker_state); the no_internet scenario is gone with its gate,
  and the 21:34 reproduction is driven by a failed hub-channel read. 8 FAIL at T2 -> 5 FAIL (the 5 left are the
  gossip scenarios, `direct_neighbor_targets` returns [] for them; unrelated, not gated).
- live_prober_removal.py: the prover guard checks without the broker list (7 -> 5 checks, all PASS).
- test_region_oracle.py: register_node (pond = RINA_AREA), my_channels, publish_plan_maps (14 -> 11 checks, PASS).
- oracle_routing_topology.py: the broker block is now the region block, plus RINA_RAM-from-join.env and
  old-BROKER_URL-only checks (33 -> 35 passed).
- Comments and docstrings that described the broker as current: poll.py, region.py, config.py, AI_README.md,
  live.py, sources.py, real_backends.py, snapshot.py, planner.py, test_reflect_vouch_gate_oracle.py.

gateways.conf NETWORK_NAME: the daemon no longer reads it. Still read by: the node's own setup (frognet-config-v4,
frognet_setup_helper.bash, frognet_fixup.sh, frognet_eth0_fixup.sh: the node's network name / nmcli connection) and,
for the broker's pond, frognet-node-guid.sh, frognet-conf-consolidate.sh, frognet-tunnel-setup-v3.sh (T3b).

## The two new oracles

### 1. NOKEY (option of tools/netns-region-loop-test.sh: `NOKEY=a`)

Node a starts with no node_private.key (tunnel.conf: a comment only). It must make the key, say so, announce its
public half, and bring up its hub link (ping 10.7.7.1 -> 10.3.3.1 across the hub). Run with
`NOKEY=a FROGNET_DIR=<tree>/opt/frognet_semantic bash tools/netns-region-loop-test.sh /home/claude/merge/src/rinab`.

Red on T2 (before/nokey-on-T2.txt), RESULT FAIL: 8:

    FAIL  converged: z has a's /24 and y's (via y's lease); a has z's and y's (after 60 s)
    FAIL  y -> a over z's LAN, z's tunnel, the hub, a's tunnel (ping from y's .1)
    PASS  both daemons in the region loop
    FAIL  a: node_private.key exists now, mode none (it started with none)
    FAIL  a: the daemon says it made the key and names its public half:
    FAIL  a: the public key it announced (facts.pubkey) is the one it made
    FAIL  a: its hub link is up: 10.7.7.1 -> 10.3.3.1 across the hub ()
    ... (+ the two leaf checks that ride a's hub link)
    T2 daemon: "WireGuard keypair missing - run frognet_tunnel_setup_v3.sh to register with broker; running in
    LAN-only mode this cycle"

Green after (after/nokey.txt), RESULT PASS, every check:

    PASS  a: node_private.key exists now, mode 600 (it started with none)
    PASS  a: the daemon says it made the key and names its public half: WG_KEY: made
          /var/lib/frognet-tunnel/node_private.key (none on disk); public key OJsFgMPA...= [DAEMON_OWNS_ITS_KEY_V1]
    PASS  a: the public key it announced (facts.pubkey) is the one it made
    PASS  a: its hub link is up: 10.7.7.1 -> 10.3.3.1 across the hub (wg0: handshake ...)

### 2. LAN-only (new: tools/netns-lan-only-test.sh) — built in netns, not a simulation

Two nodes in namespaces on one LAN, no RAM manager, no broker: a (eth0 10.11.11.1/24, NO tunnel.conf) and b (wlan0
10.11.11.20, a's DHCP guest; eth0 10.22.22.1/24; tunnel.conf holds only BROKER_URL, GROUP_TOKEN, POND_PASSWORD). On
each: the real tunnel daemon, two rounds of the real `discovery.live` (its bring-up-only stage, the LAN walk, the
hosts commit, the databasehost election), answering each other through the tree's real frognet_echo.php /
getHosts.php (php -S, with the tree's usr/local/bin bound in) and the real data daemon's :9009
(`daemon.engine.server.DaemonServer`, no MySQL behind it: the walk only asks it "alive?"). discovery.live is run
directly, as netns-region-loop-test.sh runs it; runMerge.bash's own wrapper steps (setup_iptables, identity
preflight, the /tmp and /run wipes) are host-wide and are not run in the namespaces.

Red on T2 (before/lanonly-on-T2.txt), RESULT FAIL: 8:

    FAIL  a: the tunnel daemon is running (no tunnel.conf at all) (1 exit/error lines)
          ("Config file /etc/frognet/tunnel.conf not found - run frognet_tunnel_setup_v3.sh first", exit 1)
    FAIL  a: its log says LAN-only and its build
    FAIL  b: its log says LAN-only and its build
    FAIL  b: ONE line names the old broker keys and ignores them
    FAIL  a: the merge's bring-up stage ran and said LAN-only   (rc=1, the same exit)
    FAIL  a: handshake_rtts.json is {} and tunnel_bringup_ready is touched
    FAIL  b: the merge's bring-up stage ran and said LAN-only
    FAIL  b: nothing asked a broker anything (3 broker lines: SYNC_TRANSIT x2, "POLL: broker unreachable")
    PASS  discovery itself: names, routes, ping, both elect databasehost 10.22.22.1, no wg

Green after (after/lanonly.txt), RESULT PASS, 19 of 19:

    PASS  a: the tunnel daemon is running (no tunnel.conf at all) (0 exit/error lines)
    PASS  a: its log says LAN-only and its build: Starting frognet-tunnel-daemon v3.0.0 build
          frognet-tunnel-2026-10-12-t3a (mode lan-only) | LAN-only loop started: no region (RINA_RAM unset) ...
    PASS  b: ONE line names the old broker keys and ignores them: old broker keys ignored: BROKER_URL, GROUP_TOKEN,
          POND_PASSWORD (only RINA_RAM, RINA_AREA are read) [NO_BROKER_V1]
    PASS  a/b: the merge's bring-up stage ran and said LAN-only: BRINGUP_PHASE: LAN-only (no region): no WireGuard;
          tearing down any wg iface [LAN_ONLY_NO_REGION_V1]
    PASS  a/b: handshake_rtts.json is {} and tunnel_bringup_ready is touched
    PASS  a's /etc/hosts names b: 10.22.22.1 FrogNetHost.b;  b's names a: 10.11.11.1 FrogNetHost.a
    PASS  a routes b's /24 via b's lease: 10.22.22.0/24 via 10.11.11.20 dev eth0 metric 22; a -> 10.22.22.1 pings
    PASS  both elected the same databasehost: a says '10.22.22.1', b says '10.22.22.1'
    PASS  a/b: no WireGuard interface;  a/b: nothing asked a broker anything (0 broker lines)

## Gates

The rinab harnesses default to ../../repo and ../../pkg-full next to rinab/, which do not exist beside this tree, so
every netns run here passes the built tree as RINAB_DIR: `bash tools/<test>.sh /home/claude/merge/src/rinab` with
FROGNET_DIR=/home/claude/clean/opt/frognet_semantic (the tools are this tree's; build/, fdf-hub and pkg-full/bin/fdf-facts
come from the built tree, and its fdf-facts and fdf-hub are cmp-identical to this tree's). Without the argument the
region test fails 10 checks on missing paths, before and after alike.

| gate | T2 (recorded) | before T3a (T2 tree, same env) | after T3a |
|---|---|---|---|
| `python3 -m discovery selfcheck` | 14/14 | 14/14 | **14/14** |
| `discovery/run_discovery_oracles.py` | 84 pass + test_license | 84 pass, 1 regr (test_license, 42 files) | **83 pass, 1 regr (test_license, 42 files)**; test_vouch_transit_gate removed with its gate |
| `simulation/run_all.py` | 3 red | 3 red: DDP, aiconnect, discovery (= test_license) | **3 red, the same three** |
| netns-region-loop-test.sh | RESULT PASS | RESULT PASS, 13 PASS + 1 SKIP | **RESULT PASS, 13 PASS + 1 SKIP** |
| netns-ponds-daemon-test.sh | RESULT PASS | RESULT PASS, 21 PASS | **RESULT PASS, 21 PASS** |
| netns-reach-test.sh (BRIDGE_KT, FDF_TUN) | 70 PASS | RESULT PASS, 70 PASS | **RESULT PASS, 70 PASS** (now the tree's www and monitor) |
| NEW: region-loop NOKEY=a | — | RESULT FAIL: 8 (on bc34ae3) | **RESULT PASS** |
| NEW: netns-lan-only-test.sh | — | RESULT FAIL: 8 (on bc34ae3) | **RESULT PASS, 19/19** |
| make_deliverables.sh to a scratch dir | — | (mirror) | **builds all three; the x86 .run's 39 FrogNet files cmp-identical to the tree** |
| test_region_oracle / graph_apply_oracle | | PASS 14 / ALL OK | **PASS 11 / ALL OK** |
| bash -n (11 changed/new shell files), py_compile (53 changed .py) | | | **all OK** |

Not gated, recorded: sim_control_plane 8 FAIL -> 5 FAIL (gossip only); live_prober_removal PASS -> PASS;
sim_tunnel_health PASS -> PASS; oracle_routing_topology 33 -> 35 passed.

## Static grep — intended survivors

`git grep -n -i -E "broker_url|BROKER_DISABLED|register_with_broker|/api/v4|pond_password|group_token" --
opt/frognet_semantic/internet_tunnels_v3 opt/frognet_semantic/discovery usr/local/bin/runMerge.bash opt/fdf`:
- internet_tunnels_v3/config.py:30 — the docstring naming the old keys it ignores (intended).
- internet_tunnels_v3/frognet_chorus.php — the broker's chorus web page (byte-identical to var/www/html/
  frognet_chorus.php, deployed from here by frognet_build_release.sh). Web page + release script: T3b.
- discovery/sim/conf_lib_check.py — tests the installer's tunnel.conf library (fn_conf_set/unset of broker keys). T3b,
  with the installer.
- opt/fdf/src/fdf-source/docs/r16-to-r19.diff, r19-to-r19-a11.diff — historical release diffs.
runMerge.bash: none.

## For T3b (found, not done here)

- The installer and setup scripts that still make the broker's world: frognet-tunnel-setup-v3.sh (the other creator
  of node_private.key, with broker enrolment), setup_lillypad_v4.bash (writes MAX_TUNNELS etc.),
  frognet-pond-bootstrap.sh + its timer, frognet_nuke_tunnels, change_pond.bash (deletes last_broker_state.json /
  broker_channels.json, which nothing writes now), frognet-unmake-gateway.sh, frognet_internet_watch.sh,
  frognet-node-guid.sh / frognet-conf-consolidate.sh (gateways.conf NETWORK_NAME as the broker pond),
  frognet_build_release.sh (frognet_chorus.php), sync_interfaces.sh (POSTs update-subnets to BROKER_URL; legacy bash
  merge), the broker tree opt/frognet_semantic/broker, simulation/broker_topology.py (TIER 3) and frognet_sim.py's
  broker model (TIER 1).
- Old broker keys in tunnel.conf on nodes: the daemon names and ignores them; the installer's upgrade should remove
  them.
- Files deleted here that untarring cannot delete on a node: internet_tunnels_v3/test_register_on_404_oracle.py,
  test_transit_from_routes.py, discovery/test_vouch_transit_gate_oracle.py (test files; nothing on a node runs them);
  /var/lib/frognet-tunnel/{last_broker_state,broker_channels,channel_map}.json and
  /etc/sentinels/transit_subnets.tsv (no longer written; harmless). A T3a section in frognet_prune_removed.sh would
  take them.
- opt/fdf/dist/fdf-install-{x86_64,aarch64}.run and fdf-droplet.tgz are the T2-era builds (they still carry the
  tunnel.conf.broker copy and the old daemon). Rebuild with tools/make_deliverables.sh when releasing.
- runMerge.bash FROGNET_MERGE_LEGACY_TAIL (re-run the bash fixDefaultRoute/manageResolv beside the Python merge, off
  by default): a second mechanism; nothing sets it.
- Dead, not broker, left: poll.py `_probe_ip_for_subnet`, `_onlink_required` (no caller at T2); config.py
  GATEWAY_*, ECHO_SETTLE_SEC, RECONNECT_*, MERGE_SETTLE_SEC, METRICS_INTERVAL_SEC (no reader); region.my_channels still
  reads facts.transit, which only the broker-era update-subnets wrote.
- The rinab harnesses' default paths (../../repo, ../../pkg-full) do not exist beside this tree.

## Orchestrator re-run (after the agent), and a harness fix
The netns harnesses took FrogNet, fdf scripts, templates and fdf_graph from a sandbox layout outside the repo
(`$H/../../repo`, `$H/../../pkg-full`). They now take them from the repo itself: `$H/../fdf` (the FD&F source) and
`$H/../../../../frognet_semantic`. So the netns gates now exercise this tree's fdf-region-join/fdf-leave/fdf-facts, not
older copies. Re-run that way (logs: t3a-logs/repo-harness/): region-loop NOKEY=a PASS; region-loop PASS; LAN-only
PASS 19/19; reach PASS 70; ponds-daemon FAIL 1 on the first run ("n and a route each other's /24", timing), PASS on the
re-run and PASS with the old harness — recorded as a flaky check to watch, not a regression. selfcheck 14/14;
discovery 83 + test_license; run_all the same 3 red.
