# HANDOFF — RAM-driven discovery (build fdf-2026-10-10-r19)

What was built on 2026-10-09 after PLAN-RAM-DISCOVERY.md, what it was proven on, what each build fixed, and what to run.

## The one-line version

FD&F owns discovery. Every node's `fdf-facts` writes its addresses, the leases it holds and gives (with macs), its
uplink (type, rate, three-miss dead flag) and its region tunnel edge into facts.json; fdf-client announces it and
registers its /24s as signed mappings. Every node builds the same graph (`/opt/fdf/lib/python/fdf_graph.py`) from
everyone's rows; the FrogNet daemon applies its own slice in-process: one echo per newly admitted LAN route, hub
routes gated on the handshake, 60 s hold-down for rows that merely vanish, immediate withdrawal on a positive
failure, hosts from the same plan, traffic sourced from its own .1. The hub applies its slice: each peer's
allowed-ips, from the graph and the signed mappings, recomputed from the present on every event. The crawl does not
run. The RAM manager refuses a member whose served /24 overlaps a live member's, and refuses a mapping that a live
session already holds, naming the holder both times.

## Files (one per machine type; every build ships all three)

| machine | file | install |
|---|---|---|
| Pi nodes (Seattle7, Seattle3B, Seattle5, HappyDog, New-York-2) | `fdf-install-aarch64.run` | `sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>` |
| x86 nodes (AI-Host) | `fdf-install-x86_64.run` | same |
| droplet (RAM manager + hub) | `fdf-droplet.tgz` | `cd / && tar xzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub` |

The installer checks root, arch, python3/wg/systemctl/tar, `/opt/frognet_semantic`; backs up `tunnel.conf.broker`;
unpacks to `/`; prints `/opt/fdf/BUILD`; runs `fdf-region-join` (site.env, join.env, daemon restart, fdf-facts and
fdf-client units, a 15 s report). Re-runnable. Defaults: RAM `165.232.153.144:51888`, area `frognet`.

Restarting `fdf-ram-frognet` ends every member's session: every client reconnects and re-registers within seconds,
routes are withdrawn and re-admitted. Restarting only `fdf-hub` disturbs nothing.

## What the logs say (no commands needed)

- r17 additions: daemon `REGION: loop started (...) [... WATCH_BEFORE_READ_V1 WORLD_READ_ONCE_PER_EVENT_V1
  KERNEL_READ_ONCE_PER_EVENT_V1]`; `KERNEL_READ: wg show all dump rc=…` only on a failed dump; merge `sentinels_wiped
  removed=N kept=[…]`, `PUBLISH_CAPABILITY not run: region mode …`, `REACH_PLANE_TUPLE not written: region mode …`,
  `runAgain_reinvoke … reason=external_during_pass runAgain_from=<nm:…|watcher:poke|manual:…>`; fdf-region-join
  `== region mode: <unit> stopped and disabled`; fdf-hub exits with `fdf-events registered no watch` when the region
  does not answer at start.

- node daemon: `REGION: capability published as a fact (mysql=… cores=… mem_kb=… disk=… ffmpeg=…)` (r16);
  `REGION: ELECT: candidates from the region's facts: N member(s) with a capability; my LAN (graph links): […]`;
  `REGION: event p timed: world=…ms bringup=…ms apply=…ms (probes=n/…ms routes=n/…ms) publish=…ms
  total=…ms` on every event (r15); `GEN: n -> n+1 why=<routes_changed|default_changed|topology_hosts_changed|member_came|member_went|
  service_host_changed|hub_channel_changed|maps_changed> by=<applier|elect|daemon>` on the node that changed something
  that matters (r14; the counter is announced as its `gen` fact); `REGION: world gen a -> b (<member> n->m) after …` on
  every other node (the sum of the alive members' gens, read from the region — nothing is sent over a tunnel);
  `REGION: gen mine=… world=… members=…` per event; `GRAPH build=fdf-2026-10-09-rN rebuild=… gen=… hosts=… links=… uplinks=[…] me=… exit=… routes=+a/-b refused=…
  hosts=+c/-d maps=[…] held=[…] changed=[…]` per event; `GRAPH changed: <member> keys=[…] +[rows] -[rows]` says what
  moved; `GRAPH membership: went=[…] came=[…]`; `REGION: maps -> […]`; `REGION: own […] kept registered`;
  `REGION: ELECT: control=… databasehost=… mediahost=…`; `REGION: election after … done in N s (off the loop)`
- fdf-facts: `wrote N rows {...} uplink=[…]` (the uplink row says `dead` only after three missed pings)
- fdf-client: `mapping X -> hub:1:50 registered`; `mapping X is NOT in the region though this session registered it;
  registering again` (the 30 s assertion); `REFUSED by the region: EEXIST (X is held by a live session (node:w:<guid>);
  not transferred)`
- hub: every `event p/m: members=… mappings=… peers=… prefixes=… changes=… build=…` line carries the build;
  `graph: hosts=… links=…`; `no verified mapping names this hub for X (name): peer without its /24`; `deleted peer …
  (not a member now)`
- RAM manager: `version` op answers the build; `EEXIST` on prefix overlap and on mapping ownership, with the holder

## Builds, in order, and what each fixed (all found on hardware)

| build | change | why |
|---|---|---|
| r5 | plan as written: lease-pair adjacency, forest, exit rank, admission probe, hold-down, graph-driven hub, EEXIST on overlap | — |
| r6 | `CHILD_KEEPS_OWN_MAP_V1`, `HUB_HAS_NO_MEMORY_V1` (no parking; leave = delete) | Seattle7 withdrew its own /24 on becoming a child; "the hub should have no memory" |
| r7 | `OWN_TUNNEL_CARRIES_OWN_V1`: a host with a hub link carries and registers its own /24, child or not; exits carry only tunnel-less children. `NO_PLAN_NO_WITHDRAW_V1` (runMerge's reconcile never publishes maps). `HUB_HEALTH_IS_THE_HANDSHAKE_V1` (merge no longer judges wg0 by a third host's .2) | the kernel's 10.253/16 connected route sends a child's replies out its own tunnel; parking its /24 behind the exit was an asymmetric path the hub dropped. 3B's merge withdrew its mapping every run and marked wg0 dead |
| r8 | `REGION_SRC_IS_MY_DOT1_V1` (hub routes carry `src <my .1>`); `UPLINK_DEAD_IS_THREE_MISSES_V1`; build tag on every hub event line and every GRAPH line | HappyDog alone couldn't reach 3B: its traffic was sourced 10.253.123.123; one lost wifi ping re-parented 3B |
| r9 | `GRAPH_SAYS_WHAT_CHANGED_V1`; `HUB_ROW_IS_THE_REGION_EDGE_V1` (only 10.253.A.B of a served 10.A.B.0/24 is a hub row) | "who is storming" was unanswerable; broker-mode wgN tunnels were facts churn |
| r10 | `OWN_MAP_WHILE_TUNNEL_UP_V1` (own /24 registered whenever a hub channel is up, row or no row) | at daemon start the plan ran before fdf-facts wrote the hub row → `maps=[]` → withdrew itself |
| r11 | fdf-client `MAPS_ASSERTED_V1` (reads map_list every 30 s, re-registers what the region lacks; facts-watch thread fails fast) | a mapping vanished from the region with the session up; the client believed it still held it |
| r12 | rina-host `MAP_OWNER_IS_NOT_TRANSFERRED_V1` (a mapping held by a live session is refused to another registrant, holder named); `REFUSAL_SAYS_WHY_V1`; daemon `ELECTION_OFF_THE_LOOP_V1` (election on its own thread) | map_register silently transferred ownership; Seattle5 (as Seattle7's old exit) took 10.170.170.0/24 then withdrew it — the mechanism behind every loss of that prefix. The election's 20 s store reads stalled route application |
| r19 | John: "frognet_echo can be returned from RAM, and the aliveness checks." `ALIVE_IS_THE_SESSION_V1`: the applier sends no admission echo (a planned route's member is alive by its session; its link is a lease pair both ends wrote) -- rule 6's "failed probe" is gone, hub handshake and region loss still withdraw at once; fixDefaultRoute pings no next hop an alive member wrote in its facts (only gateways no member wrote). `MEMBER_IS_NOT_MARKED_V1`: the proxy's not_frognet cache never marks a host in region_hosts.json. `ECHO_FROM_THE_REGION_V1`: frognet_echo.php answers from the node's own facts ("<name>,<.1>,<upstream>,", the RegionEcho form); region_hosts.json uses the same form. | netns-graph-test 17/17; netns-region-loop-test PASS twice (median total 33-45 ms this time against 16-18 in the r18 run: the same code on the steady path, so sandbox variance -- probes were already 0 on steady events); every oracle PASS (graph_apply: "no echo probe is sent"); test_not_frognet_skip_oracle PASS; frognet_echo.php -> "Seattle7,10.170.170.1,,". |
| r18 | John: "makeHostJson is unnecessary when all the data is in RAM; look for other things like that." Region mode stops re-deriving what the region holds. `HOSTJSON_FROM_THE_REGION_V1`: no makeHostJson.bash (curl per peer, --max-time 45, the merge waiting); the applier writes `/var/lib/frognet/region_hosts.json` (ip, hostname, echo rebuilt from each member's facts) and getHosts.php serves it. `CAPABILITY_TUPLES_OFF_IN_REGION_V1`: `core.role_publish.publish_all` is a logged no-op (it was every capability publisher: merge, frognet-daemon at boot and on each databasehost float, proxy at boot, communicator; each ran the benchmark). `EXIT_FROM_THE_GRAPH_V1`: fixDefaultRoute Mode B takes the graph's exit for this node instead of pinging and curling getDefaultRoute.php on every known host. runMerge: no autoswitch (`AUTOSWITCH_IS_THE_DAEMONS_IN_REGION_V1`), no TransitPeerCache flush, no propogateNotification spawn, no broker enrolment retry; discovery.live: no bring-up-only python (`BRINGUP_IS_THE_DAEMONS_IN_REGION_V1`). fdf-region-join also stops the 120 s pond-bootstrap timer. Deliverables tar files only (`FILES_ONLY_TAR_V1`): extracting at / no longer resets owner/mode of existing directories. | netns-region-loop-test (now also checks region_hosts.json): PASS, median timed total 16 / 18 ms (a / z), converge 6 s; netns-graph-test 17/17; graph_apply_oracle (new json check), test_region_oracle, gen_oracle, test_region_backends_oracle, register_on_404, dbhost_switch_sim 15/15, fdf_graph_oracle, hub_oracle, mapown PASS; getHosts.php region path run in a mount namespace; publish_all region/off-region both exercised; exit feed: child -> its exit, exit -> none, dead uplink -> none. |
| r17 | Found in the sandbox with the new `tools/netns-region-loop-test.sh` (the REAL daemon's region loop on two nodes in netns, each in its own mount namespace, against the real rina-host + fdf-hub) and by reading the merge path. `WATCH_BEFORE_READ_V1`: the region loop read the world, brought up the hub tunnel (5 s), and only then registered fdf-events' watches — every join/announce/mapping in between was never delivered; fdf-hub had the same order. Now the watch is registered (fdf-events says `ready`) before the start read. `WORLD_READ_ONCE_PER_EVENT_V1` + `KERNEL_READ_ONCE_PER_EVENT_V1`: bringup and the applier answer from the event's one `world` read and one `wg show all dump` until something changes WireGuard (7 execs and 4 extra region reads per event → 2 execs, 0). `NO_DEAD_STORE_READ_V1`: `gather_candidates` read `discovery/reach_plane` from the store on databasehost_control once per role per election and never used it — the last store read on the election path, aimed at the host that has just died; the r16 sim's "store never read" was false (its raise was swallowed); the sim now counts calls. `ANN_GOES_WITH_THE_SESSION_V1` (rina-host): a member's announcement is deleted when its session ends, not left dead (the stale Seattle* duplicates). `FACTS_FEW_EXECS_V1` / `FACTS_IGNORE_OWN_ROUTES_V1`: fdf-facts reads links from sysfs, the default route from /proc/net/route, the hostname from gethostname (one exec left), and a non-default route change no longer wakes it. FrogNet side: `BASELINES_SURVIVE_THE_WIPE_V1` (runMerge keeps frognet_hosts, dnsmasq_content_key, dnsmasq_upstream.conf, old_host_json, old_hosts_meta, echo_cache, getHosts.cache.* across its `/etc/sentinels` wipe: one cause for `hosts_changed`/`chain_dirty=1` every pass, `DNSMASQ_NO_SENTINEL … restarting` every merge, and the per-merge echo re-curl), `REGION_MERGE_WRITES_NO_STORE_V1` (no PUBLISH_CAPABILITY benchmark, no reach_plane tuple in region mode), `RUNAGAIN_SAYS_WHO_V1` (`reason=external_during_pass runAgain_from=…`), `STORE_UNITS_OFF_IN_REGION_V1` (fdf-region-join stops the two 60 s advertise timers, the dbhost-elect timer and the merge watcher; fdf-leave restarts them). | `netns-region-loop-test.sh`, N=6 changes on y, x86 sandbox: converge after start r16 not within 60 s (3 of 3 runs) → r17 1–5 s; y→a ping r16 FAIL → PASS; median timed `total` a/z 39/40 → 22–29 ms; `bringup` 28/32 → 14–21 ms; daemon execs per event 7 → 2; fdf-facts rows() 10.3 → 3.0 ms; dbhost_switch_sim 15/15 (the new "store never read" check FAILs against r16's elector with three reach_plane reads of 10.251.251.1); netns-graph-test 17/17; mapown, hub_oracle, fdf_graph_oracle, graph_apply_oracle, test_region_oracle, gen_oracle, test_region_backends_oracle PASS. |
| r16 | databasehost.frognet switchover automatic. `ELECTION_FROM_FACTS_V1`: the election's input is the members' capability FACTS in the region (`core/region_capability.py` answers gather_candidates / _capability_index / _live_roles in the store's row shape from the alive participants), not <role>/capability tuples in a store — which lived on the databasehost, the host that had just died (both roles ABSTAIN, Seattle5 19:00). `CAPABILITY_IS_A_FACT_V1`: the daemon runs frognet_capability_probe.sh at start and every 600 s off the loop and writes facts.capability when the slow-moving part changes (load and free space are not announced and are not election inputs). The daemon re-elects on came/went/hosts and on any member's capability fact changing. `LAN_IS_THE_GRAPH_V1`: the mediahost's on-segment bucket is fdf_graph's links to me, not a traceroute per candidate per election. The 1/min `frognet_dbhost_elector` stands down in region mode (it read the store; it would fight the daemon). `elect_now(participants=, self_ip=, hosts_path=, apply=False)` lets the simulator elect as any node. | sim `discovery/dbhost_switch_sim.py` (five members shaped like the fleet, T.get patched to raise — the store is never read): steady → all five name Seattle3B (37 ms worst); 3B dead → all five switch to the same mysql host in one event (19 ms for all nodes); 3B back → all five name it again; two dead → unanimous; capability change → unanimous; member without a capability fact → ignored; mediahost on-segment from the graph (HappyDog+NY2 agree, Seattle5+Seattle7 agree, 3B alone names itself). netns: a member's death reaches the others as `m` in 14 ms (clean close) and 17.4 s (silent: TCP keepalive 5+3×5 s, John's 20 s). Switchover = that + ~20 ms. |
| r15 | discovery made as fast as it goes, measured in `tools/netns-latency-test.sh` (same topology as the graph test; one real change on y — an address added — stamped at every stage until a has replanned). `FACTS_WAKE_ON_EVENT_V1`: fdf-facts --watch blocks on `ip monitor` + inotify with select(), 50 ms debounce (was a 1 s poll + 1 s debounce). `UPLINK_OFF_THE_WRITE_V1`: the uplink ping runs on its own thread every 3 s; a facts write no longer waits an RTT (or 6 s with the uplink dead). `WORLD_IN_ONE_READ_V1`: region op `world` = participants + mappings in one answer; the daemon, the hub and apply_once read it once per event (one region RTT, not two; an older region is an error, not a fallback). `ONE_WRITE_PER_EVENT_V1`: maps, view and gen land in one facts write per event, so one re-announcement and one fleet-wide `p`, not three. `EVENT_TIMED_V1`: `REGION: event p timed: world=…ms bringup=…ms apply=…ms (probes=n/…ms routes=n/…ms) publish=…ms total=…ms` on every event; `GRAPH … rebuild=N gen=M`. `HANDSHAKE_POLL_100MS_V1`, `REJOIN_EVERY_5S_V1` (was doubling to 60 s). fdf-hub's "fdf_graph unavailable → facts rule" fallback removed. | before: change → facts.json 1,594 ms of an end-to-end 1,768 ms; after: 66 ms → 7 ms announce → ~0 ms event fan-out → 5 ms apply in-process (1 route cmd 2 ms); region reads 0.9 ms participants / 0.5 ms mappings in-process on the LAN. End to end in the daemon ≈ 80 ms on a LAN; a WAN member adds the region's RTT twice (announce, world) |
| r14 | `GEN_V1` the generation counter (`internet_tunnels_v3/gen.py`): one integer per node, +1 only on an impactful change from a closed list, named in the log, announced as the `gen` fact. `NO_MERGE_ON_REGION_EVENTS_V1`: the region loop forks no runMerge, ever — the facts carry what the graph needs and the loop applies all of it (routes, default, hosts, names, elections, maps, hub channel); runMerge is for boot and interface events (NetworkManager dispatcher). `NO_FALLBACK_V1`: a failed in-process election is logged and re-run on the next event, not handed to runMerge. `GONE_IS_GONE_V1`: region lost = its routes, names and tunnels withdrawn and nothing recomputed from elsewhere. `MERGE_SAYS_WHO_V1` (repo diff only, runMerge.bash is FrogNet's): `invoked_by who=<daemon:…|nm:<iface>-<action>|watcher:poke|manual:<shell>>` at every merge's lock_acquired | merges ran only when a node's own routes moved, so a peer's impactful change never reached anyone's merge; the merge's own change detector was blind (`runMerge.bash:147 rm -rf /etc/sentinels/*` deletes the host-table baseline every pass, so `sync_required reason=hosts_changed` / `chain_dirty=1` on every pass on every node); the notification pokes (`propogateNotification` → merge watcher) are never sent in region mode; the 27 s / 16 s gaps after `converge_decision` are `makeHostJson.bash` curling `frognet_echo.php` on every peer serially with a cache that line 147 just deleted |
| r13 | rina-host `SESSION_EPOCH_V1`: a session's id is a monotonic epoch assigned at creation, never the object's address (`tools/apply_session_epoch.py` on the platform's ram_server.cpp, six sites) | addresses are reused after free; node liveness, watches, session-bound mappings and the proc table were all keyed by one, so a later session on the same address could match a dead one's records |

## Proven

- `discovery/dbhost_switch_sim.py` (r16): 14 checks, RESULT PASS; `tools/netns-latency-test.sh` (r16) also measures member death →
  `m`: 14 ms clean, 17,368 ms silent.
- `tools/netns-latency-test.sh` (r15): y gains an address → facts.json 66 ms → re-announced +7 ms → `p` on a +0 ms → a's
  apply 5 ms in-process (python start excluded); before r15 the first stage alone was 1,594 ms.
- `tools/netns-graph-test.sh` (real WireGuard in namespaces, y → z → hub → a): PASS at every build r5–r15 (r14: `GEN: 0 -> 1 why=routes_changed`, two no-change rebuilds do not bump, `member_went` and the withdrawal do), 17 checks:
  rows, admission, hub allowed-ips from the plan, y↔a ping both ways, steady state applies nothing, hold-down then
  withdrawal, hub drops the withdrawn /24, second claimant refused with holder named.
- oracles (no kernel): `fdf_graph_oracle.py` (Seattle7 with and without a tunnel, 7b/7b'/7c), `graph_apply_oracle.py`,
  `hub_oracle.py` 30 checks (8: tunneled child carries itself; 8t: tunnel-less child behind the parent; 8d: parent dead,
  child's own registration enough; leave/return without parking), `mapown_oracle.sh` (second live claimant refused,
  holder named, takes over only after the holder leaves).
- hardware, end of day, from Seattle7 (`pipe_workload.py … echo 300`), 300/300 to every target:

| target | path | direct avg/p50 | FrogNet HOT avg/p50 | burst 300 |
|---|---|---|---|---|
| New-York-2 10.28.28.1 | hub → HappyDog → NY2 | 373 / 244 ms | 169 / 128 ms | 155.6 req/s |
| HappyDog 10.123.123.1 | hub → HappyDog | 203 / 204 ms | 119 / 119 ms | 231.2 req/s |
| Seattle3B 10.251.251.1 | hub → 3B | 115 / 95 ms | 114 / 60 ms | 155.5 req/s |
| Seattle5 10.250.250.1 | LAN | 8 / 7 ms | 136 / 18 ms | 129.3 req/s |

- convergence against June's crawl logs: a change settled in ~2–6 min then (one to three 116 s passes plus the peers'
  own passes); today the daemon has it in the kernel within a second of the region event, and the merge that follows
  is 2–9 s instead of 116 s because it walks nothing.
- size: ≈1,760 new lines (fdf_graph 228, graph_apply 271, region 242, elect 86, fdf-facts 232, fdf-hub 250, +450 in
  poll/live/orchestrate/service_hosts) replace ≈4,300 lines of crawl/broker path that no longer execute in region mode
  (discovery.py 697, descend.py 435, live.py's walk, sources/healthcheck/propagate, real_backends, broker*, peer.py 1,477).

## Rules in force (from the day's decisions)

1. adjacency = a `held` row and a `given` row agreeing on (ip, mac), the giver serving that /24
2. the routing graph is a forest: one upward link per host toward its ranked exit (speed, then lower .1)
3. a host with a hub link carries and registers its own /24; an exit carries only tunnel-less children; nothing is
   ever behind two peers
4. the hub remembers nothing: wanted state from this event's records; leave = delete, return = a new peer from facts
5. a mapping belongs to the live session that registered it; a second claimant is refused with the holder named; the
   client asserts its mappings every 30 s against the region
6. absence is held 60 s; positive failure (region gone, hub handshake dead, failed probe) is withdrawn now
7. the node's own region traffic is sourced from its .1
8. an uplink is dead after three consecutive missed pings, alive after one answer
9. no merge for a region event: the facts are sufficient, the daemon applies them; a change that matters bumps the
   node's gen, and the world gen (sum over alive members) is the audit trail
10. the elections' input is the region (facts.capability of the alive members), never a store
11. no fallbacks: a failed step is logged and retried on the next event, never replaced by another mechanism; gone is gone

## Not done / next

- FrogNet-side, found while examining merge triggering, not changed here: `runMerge.bash:147 rm -rf /etc/sentinels/*`
  deletes `frognet_hosts`, `old_host_json`, `echo_cache` every pass (change detection blind, `mv: cannot stat
  old_host_json.tmp`, every peer re-curled every merge); `DNSMASQ_NO_SENTINEL … restarting` every merge; ~9 s
  `PUBLISH_CAPABILITY` self-benchmark per merge; `frognet-merge-watcher` and `propogateNotification` are dead weight in
  region mode; `runAgain_reinvoke reason=changed_24_not_yet_converged` is mislabelled (it is `external_during_pass`:
  another runMerge hit the held lock).

- Databasehost failover on hardware: the number to take is the gap between `GRAPH membership: went=[('Seattle3B', …)]`
  and `REGION: ELECT: control=… databasehost=<new>` on any other node (simulator: ~20 ms after the `m`; the `m` itself is
  14 ms after a clean close, ~17 s after a silent death).
- `Applier.apply` (CC 86) owns admission, hold-down, withdrawal, default and hosts in one function; split it.
- The semantic engine on the Seattle7 ↔ Seattle5 path re-learns the `sensors&action=values` template on every SAME
  (`SAME cache miss … auto-retrying via raw path … LEARNED template` per request): the ~1 s p50 on bursts and the
  ramp collapse past conc=10. Engine, not region. `apply_same_miss.py` / `SAME_IS_BACK_V1` are the prior work.
- `frognet_tuples` lacks `heartbeat` and `on_database_change`; proxy and daemon metrics log an AttributeError each cycle.
- `fdf-facts` reads dnsmasq's lease file only; a node serving DHCP another way writes no `given` rows.
- A leaf (laptop, AI-Host) needs a `leaf` row; fdf-facts writes it from facts.role/addr.
- The stale `FrogNetHost.Seattle*` members (dead duplicates from before ME_BY_GUID) sit in the participant list; harmless
  while dead, worth a manager-side expiry.
