history: superseded by docs/OPERATING.md

# r17 work log (optimization thread, 2026-10-10)

Base: fdf-source-r16 + John's public repo FrogNet-Living-Network @783671f (2026-10-08) for the files the package
lacks (core/frognet_tuples.py etc.); test tree = repo opt/frognet_semantic overlaid with the package's changed files.

## 1. runMerge.bash:150 baseline wipe  [BASELINES_SURVIVE_THE_WIPE_V1]
`rm -rf /etc/sentinels/*` replaced by a wipe that keeps eight named baselines: frognet_hosts, dnsmasq_content_key,
dnsmasq_upstream.conf, old_host_json, old_hosts_meta, echo_cache, getHosts.cache.json, getHosts.cache.meta.
Logged: `sentinels_wiped removed=N kept=[...]`. Tested on a fake dir: 8 kept, 8 removed (incl. dotfile, subdir).

One wipe, four symptoms (items 1 and 2 of the brief are the same cause):
- frognet_hosts gone -> `_commit_hosts` fh_old="" -> `_topology_changed` true -> `sync_required reason=hosts_changed`,
  chain_dirty=1, propogateNotification every pass.
- dnsmasq_content_key gone -> `DNSMASQ_NO_SENTINEL ... restarting` every merge (live.py:1079 reads it via _sent()).
- dnsmasq_upstream.conf (dnsmasq's resolv-file) gone from the wipe until live.py rewrites it.
- old_host_json/echo_cache gone -> makeHostJson.bash `rebuild` re-curls every peer.

## 2. runAgain says who  [RUNAGAIN_SAYS_WHO_V1]
The BAIL path and the merge watcher append the requester (`$FROGNET_MERGE_WHY`, `watcher:poke`) to runAgain instead
of truncating it; the re-invoke line reads `reason=external_during_pass runAgain_from=nm:wlan0-up,watcher:poke`
(was `reason=changed_24_not_yet_converged`).

## 3. Dead store read on every election  [NO_DEAD_STORE_READ_V1]
`gather_candidates` called `_wan_subnets(dbhost)` -> `T.get("discovery","reach_plane", dbhost=<control>)` once per role
per election and never used the result. In region mode this was the one store read left on the election path, aimed
at the databasehost (10.251.251.1 in the sim) -- the host that has just died when a switchover is due.
`frognet_tuples.STORE_TIMEOUT_S` = max(FROGNET_RPC_BUDGET_FLOOR 15, SEM 10, REAL 10) + 5 = 20 s by default; per role.
The r16 sim's "store never read" was false: T.get raised AssertionError inside `_wan_subnets`' `except Exception`.
Sim now counts calls. Proven both ways:
- r16 elector + counting sim: `FAIL the store was never read -- [(('discovery','reach_plane'), {'dbhost':'10.251.251.1'}), ...]`
- r17 elector: 15/15 PASS.
Line removed.

## 4. Merge writes nothing to the store in region mode  [REGION_MERGE_WRITES_NO_STORE_V1]
- PUBLISH_CAPABILITY (publish_all: frognet_capability_probe.sh + advertise to control and data, ~9 s on a Pi) not run
  when disc.no_crawl; logs `PUBLISH_CAPABILITY not run: region mode ...`.
- REACH_PLANE_TUPLE put not done in region mode (no reader: LAN is the graph; the only read was #3).
Readers of <role>/capability in the repo: frognet_role_elect / hosts.py / service_hosts (region path reads facts via
region_capability), frognet_dbhost_elector.py (stands down in region mode). daemon_main/proxy_main only publish.

Tests after 1-4: graph_apply_oracle ALL OK; test_region_oracle PASS; gen_oracle 8/8; test_region_backends_oracle ALL
PASS; dbhost_switch_sim 15/15 PASS; fdf_graph_oracle ALL OK.

## 5. Store-only units off in region mode  [STORE_UNITS_OFF_IN_REGION_V1]
fdf-region-join stops+disables frognet-dbhost-advertise.timer, frognet-mediahost-advertise.timer (each ran
frognet_capability_probe.sh -- cpu bench + disk write/fsync -- every 60 s, per role; the probe's own header: "computed
every call (there is no cache)"), frognet-dbhost-elect.timer (stands down in region mode), frognet-merge-watcher.service
(propogateNotificationInternal exits in region mode, [REGION_V1]). fdf-leave re-enables them. Only units that exist.

## 6. tools/netns-region-loop-test.sh  [REGION_LOOP_NETNS_V1]  (brief item 14)
The real daemon (python3 -m internet_tunnels_v3, run_region_loop) on z and a in netns, each in its own mount namespace
(private /etc copy, /var/lib/frognet, /var/lib/frognet-tunnel, /run/frognet, /run/wireguard, /opt/fdf/var), against
the real rina-host + fdf-hub; y behind z. Internet on 198.18.0.0/24 so own-subnet sees only the served /24. Log lines
stamped on arrival (epoch ms). N changes on y; reads `REGION: event p timed` on a and z.
Sandbox notes: wireguard-go built from GitHub with a local patch (bind_std.go: an IPv6-less kernel made it report
listen-port 0 -- environment only, not shipped); rinab/platform/ribbit-v0.63 taken from the repo's
ribbit/examples/redis/platform/ribbit-v0.63 (pristine; build.sh's patched output == the package's platform/ except
copyright headers).

## 7. Watch before read  [WATCH_BEFORE_READ_V1]  -- found by #6
run_region_loop read the world (`_one("start")`, which also brings up the hub tunnel: 5.1 s here) BEFORE fdf-events
registered its watches. Every join/announce/mapping in between was never delivered. Two runs of #6 on r16: both
daemons' last rebuild at t=5.3 s showed 1 host; nothing until an unrelated change at t=67 s; `converged` FAIL at 60 s.
With the fix: converged after 5 s, RESULT PASS. region.events() now yields "ready"; the loop takes it before the read.
fdf-hub had the same order (Popen fdf-events, read at once, without waiting for "ready"); fixed the same way.

## 8. One world read, one kernel read per event  [WORLD_READ_ONCE_PER_EVENT_V1] [KERNEL_READ_ONCE_PER_EVENT_V1]
strace -f -e execve on a's daemon, one event, r16: wg show interfaces x2, wg show wg0 peers x2, wg show wg0 x2,
ip -4 route show x1 (+ my_channels computed twice = 4 region reads beyond `world`). r17: `wg show all dump` x1,
`ip -4 route show` x1, my_channels once from the event's world. region.world_in_hand(parts, maps) answers
participants()/mappings() inside the event; wg.kernel_read_once() answers wg_handshake_age / the orphan scan from one
dump until anything changes WireGuard (wg-quick up/down, wg set -> kernel_changed(): every later read in that event is
live; process-wide so the bring-up worker threads end it too). Region mode skips bringup's _broker_reachable (the same
my-channels question the next line asks).

## 9. fdf-facts: one exec per write; route churn ignored  [FACTS_FEW_EXECS_V1] [FACTS_IGNORE_OWN_ROUTES_V1]
hostname -> socket.gethostname(); `ip -o link show` -> /sys/class/net/*/{type,address}; `ip -4 route show default`
-> /proc/net/route. Left: `ip -4 -o addr show`. rows() in-process: 10.3 ms -> 3.0 ms (x86 sandbox, 30 calls).
Equivalence: --show old vs new on z, a, y in the live namespaces: identical (given/hub/own/uplink/held rows).
ip monitor runs with `label`; a [ROUTE] line wakes the writer only if it names a default route (the one route rows()
reads). Lines are reassembled across reads.

## Side by side: tools/netns-region-loop-test.sh, N=6 changes on y, x86 sandbox, LAN RTT to the region ~1 ms
| | r16 | r17 |
|---|---|---|
| converge after start (z has a's & y's /24, a has z's & y's) | not within 60 s (FAIL, 3 of 3 runs) | 1 s |
| y -> a ping after converge | FAIL | PASS |
| median `total` of the timed line, a / z | 39 / 40 ms | 29 / 29 ms |
| median `bringup`, a / z | 28 / 32 ms | 21 / 20 ms |
| execs per event in the daemon (strace) | 7 | 2 |
| my-channels computed per event | 2 (4 region reads) | 1 (0 region reads) |
| fdf-facts rows() | 10.3 ms | 3.0 ms |
| z's fdf-facts passes that ended "unchanged" (whole run) | 4 | 1 |
| change on y -> timed line on a (median) | 108 ms | 92 ms |

## 10. Announcement goes with the session  [ANN_GOES_WITH_THE_SESSION_V1]  (brief item 12)
rina_session_down: when the machine's node record is cleared (same epoch check as before), its `ann` is deleted too.
Every reader already skipped dead rows (fdf_graph, graph_apply, fdf-hub, gen, region.py), so plans are unchanged;
dead rows no longer accumulate for the RAM manager's lifetime nor ride on every `world` read. Checked in #6: y's
client killed -> participants = [z, a, vm], all alive.

## 11. Build and package
Tag fdf-2026-10-10-r17 in fdf_version.hpp, fdf-hub (both copies), fdf-facts, fdf_graph.py (both copies), region.py.
build.sh (needs platform/ribbit-v0.63 -- now in the tarball), build_static_host.sh, the x86 static clients,
build_arm64.sh (arm64 smoke under qemu: rina-host + fdf-client + fdf-events, versions agree, `p` delivered).
tools/make_deliverables.sh assembles the three files; never ships /opt/fdf/var or /opt/fdf/hub/hub.env.
fdf/bin/fdf-region-join was mode 644 in the r16 source tarball; 755 now, and make_deliverables chmods /opt/fdf/bin.

## Install order
Droplet first (the announcement deletion and fdf-hub's watch fix live there), then the nodes. An r17 node works
against an r16 droplet and the reverse (no protocol change); droplet first just gets the dead-row cleanup at once.
  droplet: cd / && tar xzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub
  Pi:      sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>
  x86:     sudo bash /tmp/fdf-install-x86_64.run <SITE KEY>
What to read after: daemon `REGION: loop started ... WATCH_BEFORE_READ_V1 ...`; `REGION: event p timed: ...` (bringup
should drop on the Pis by more than it did here: six fewer fork+execs per event); merge `sentinels_wiped ... kept=[...]`,
no `DNSMASQ_NO_SENTINEL`, `PUBLISH_CAPABILITY not run`, `sync_required` only when something moved.

# r18 (2026-10-10, after John: "makeHostJson is unnecessary when all the data is in RAM; look for other things like that")

## 12. Host list from the region  [HOSTJSON_FROM_THE_REGION_V1]
- runMerge: region mode does not run makeHostJson.bash (was: /etc/hosts + frognet_echo.php curl per peer without an echo,
  --max-time 45, the merge waiting on the slowest). Logs `makeHostJson decision=skip reason=region_mode_...`.
- graph_apply.Applier writes <region_hosts>.json (/var/lib/frognet/region_hosts.json) beside region_hosts, only when its
  content changes: [{ip, hostname, echo}] for the applied hosts (self included), echo = that member's getFrogNet.bash
  line rebuilt from its facts ("<name>,<eth0>,<wlan0>,<wlan1>"); a held-down host keeps its last facts.
- getHosts.php (now in the bundle, /var/www/html): region mode reads that file; no shell-out, no curl. Same output shape.
  Checked: php -l; run in a mount namespace with a fake join.env + json -> [{"ip":"10.2.2.1","name":"y",...},...].
- graph_apply_oracle: new check on the json; the fake writer counts hosts-sentinel writes only. ALL OK.

## 13. Other things the region already holds (John: "look for other things like that")
Done in r18:
- core/role_publish.publish_all: region mode = logged no-op [CAPABILITY_TUPLES_OFF_IN_REGION_V1]. It is every publisher of
  <role>/capability tuples: the merge, frognet-daemon at boot AND on every databasehost float (start_publish_watcher,
  15 s tick), the proxy at boot (_serve_then; "~65 s each" store writes when this node is the databasehost), the
  communicator. Each ran frognet_capability_probe.sh (cpu bench + disk write/fsync). No reader in region mode.
- runMerge, region mode [REGION_MODE_ONCE_V1]: tunnel_autoswitch not run (a second python asking the region the
  daemon's question -- my-channels, two reads -- and able to nuke/bring up the hub channel behind the daemon)
  [AUTOSWITCH_IS_THE_DAEMONS_IN_REGION_V1]; flush_discovery_cache not run (MySQL DELETE on TransitPeerCache, the
  crawl's cache) [NO_CRAWL_CACHE_IN_REGION_V1]; propogateNotification not spawned; broker enrolment retry not run
  [NO_BROKER_ENROLMENT_IN_REGION_V1].
- discovery.live._reconcile_tunnels: region mode not run (`python3 -m internet_tunnels_v3 bring-up-only`: region reads,
  wg scans, could act on the hub channel) [BRINGUP_IS_THE_DAEMONS_IN_REGION_V1].
- fixDefaultRoute Mode B (a node with no uplink of its own): choose_exit_hosts pinged the next hop toward EVERY known
  host and curled getDefaultRoute.php on each, --max-time 15, serially, every merge. Region mode: the candidate is the
  graph's exit for this node (fdf_graph.exit_of: live uplink rows, speed then lower .1), "exit present" by construction;
  the one next-hop ping stays [EXIT_FROM_THE_GRAPH_V1]. Checked: child y -> ['10.3.3.1'] (z); z (own uplink) -> none;
  z's uplink dead -> none.
Note on #1: discovery.live._known_hosts (Mode B's host list off a region) reads frognet_hosts if present -- kept by
the wipe now, but _commit_hosts rewrites it earlier in the same pass, so it reads this pass's list as before.
- fdf-region-join also stops frognet-pond-bootstrap.timer (120 s broker-enrolment retry); fdf-leave restarts it.
- Deliverables: files only in the tar (`FILES_ONLY_TAR_V1`): /etc, /usr/local/bin, /var/www/html keep their own
  owner/mode on extract. getHosts.php ships at /var/www/html/getHosts.php.
Left, reported not changed: registerFrogNetHost.bash (cron 15 min) heartbeats known_frognets in the store (NetworkName
= `hostname` = "FrogNetHost" on every node -> one shared row) for frogs.js / gps_dashboard.html; the daemon's 600 s
capability probe still runs the full benchmark.

## r18 tests
netns-region-loop-test PASS (new: region_hosts.json names z, y, a; echo from facts): median total 16 ms (a) / 18 ms (z),
bringup 9 / 12 ms, change -> timed line 74 / 76 ms; converge 6 s. netns-graph-test 17/17. graph_apply_oracle ALL OK,
test_region_oracle, gen_oracle, test_region_backends_oracle, register_on_404, dbhost_switch_sim 15/15, fdf_graph_oracle,
hub_oracle, mapown PASS. runMerge bash -n; getHosts.php php -l and region path run; publish_all both modes.

# r19 (2026-10-10, John: "frognet_echo can be returned from RAM, and the aliveness checks")

## 14. Alive is the session  [ALIVE_IS_THE_SESSION_V1]
- graph_apply: no admission probe. A planned LAN route is installed at once: its member is alive (session up) and its
  link is a held/given lease pair both ends wrote. r5-r17 sent one frognet_echo.php per newly admitted route (3 s
  ceiling) through the exact next hop. World.probe removed; the timed line's probes=0/0ms stays (format unchanged).
  Hub routes stay gated on the hub handshake (kernel-local). Oracle: "no echo probe is sent"; a next hop that would not
  echo changes nothing.
- discovery.live fixDefaultRoute tail (region mode): a next hop that an alive member wrote in its facts (.1, held
  lease, hub edge) is alive -- no ICMP; a gateway no member wrote (the ISP router on an uplink) is still pinged.
  Checked: member 10.3.3.1 -> True at once; 192.0.2.99 -> real ping (2.0 s, False).
- core/not_frognet [MEMBER_IS_NOT_MARKED_V1]: a host in region_hosts.json (alive members this node routes to) is never
  marked non-FrogNet; mark() declines it. Non-members learned as before. test_not_frognet_skip_oracle ALL PASS; member
  mark -> False / is_marked False; laptop mark -> True / is_marked True.

## 15. Echo from RAM  [ECHO_FROM_THE_REGION_V1]
- frognet_echo.php (now in the bundle): region mode answers from /opt/fdf/var/facts.json: "<name>,<.1>,<upstream>,"
  -- the form RegionEcho already used -- instead of getFrogNet.bash (four helpers, a lock, a cache file). Checked:
  "Seattle7,10.170.170.1,,".
- region_hosts.json echoes use the same form (was "<name>,<eth0>,<wlan0>,<wlan1>" in r18).
