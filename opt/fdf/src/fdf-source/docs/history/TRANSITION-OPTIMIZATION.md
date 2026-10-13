history: superseded by docs/OPERATING.md

# Transition brief — optimization and completeness (as of build fdf-2026-10-10-r19)

Read `TRANSITION-README.md` first, then `HANDOFF-RAM-DISCOVERY-2026-10-09.md` (the build record r5–r16 with the log
vocabulary and the rules). This brief is what to pick up: the state, the numbers, the open items, the cleanup list,
and how to build and test. Nothing here is a guess; every number has the test that produced it.

## 1. State

Working on hardware as of r13 (the last build John reported running): Seattle7 → hub → HappyDog → New-York-2 and all
peers 300/300; convergence < 1 s after a region event; merges 2–9 s when the store is healthy. r14–r16 were built and
proven in the simulators and netns, delivered, not yet reported installed by John. Droplet must go first for r15+
(the `world` op); an r15+ node against an older region logs the error and does nothing else (no fallback).

What each of the last three builds did (full table in the handoff):

- r14 `GEN_V1`, `NO_MERGE_ON_REGION_EVENTS_V1`, `NO_FALLBACK_V1`, `GONE_IS_GONE_V1`: the region loop forks no
  runMerge, ever; the generation counter (`gen` fact) is the audit trail; failed election → logged, re-run on next
  event; region lost → withdraw and wait.
- r15 discovery latency: fdf-facts wakes on events (select on `ip monitor` + inotify, 50 ms debounce), uplink ping on
  its own thread, region `world` op (one read per event), one facts write per event, every event timed in the log,
  handshake poll 100 ms, rejoin every 5 s. fdf-hub's graph fallback removed.
- r16 databasehost switchover from facts: `facts.capability` published by the daemon; elections read the alive
  members' facts (`core/region_capability.py`), never a store; mediahost's LAN bucket from the graph (no traceroute);
  the 1/min store-reading elector stands down in region mode.

- r17 (this thread, 2026-10-10; sandbox-proven, not yet on hardware): watch-before-read (start/rejoin no longer
  misses changes), one world read and one `wg show all dump` per event, the dead reach_plane store read removed from
  the election, announcements deleted with their session, fdf-facts down to one exec per write; FrogNet side: runMerge
  keeps its baselines across the sentinel wipe, the merge writes no store tuples in region mode, the store-only units
  are stopped on region join. Full record: `docs/WORKLOG-r17.md` and the r17 row of the handoff.

## 2. Measurements (side by side)

r17 additions (`tools/netns-region-loop-test.sh`, the real daemon on z and a; x86 sandbox, region ~1 ms away):

| what | r16 | r17 |
|---|---|---|
| converge after daemon start | not within 60 s (3/3 runs) | 1–5 s (the tunnel's handshake wait) |
| timed `total` per event, median a/z | 39 / 40 ms | 22–29 ms |
| `bringup` per event, median a/z | 28 / 32 ms | 14–21 ms |
| execs per event in the daemon | 7 | 2 |
| region reads per event | 5 (`world` + 2 participants + 2 map_list) | 1 |
| fdf-facts rows() | 10.3 ms, 4 execs | 3.0 ms, 1 exec |
| store reads per election (region mode) | 1 per role (reach_plane, unused) | 0 |
| dead announcements in `world` | kept for the RAM manager's lifetime | 0 |


| what | before | now | test |
|---|---|---|---|
| change on y → facts.json written | 1,594 ms | 66 ms | `tools/netns-latency-test.sh` |
| facts.json → re-announced | 8 ms | 7 ms | same |
| announce → `p` event on peers | ~0 | ~0 | same |
| peer's apply, in-process | ~15 ms | 5 ms (1 route cmd, 2 ms) | same (`APPLY_TIMED`) |
| region reads per event | 2 (participants, map_list) | 1 (`world`) | same |
| facts writes per impactful event | 3 (maps, view, gen) | 1 | `facts_batch` check |
| end to end, LAN | ~1.8 s | ~80 ms | same |
| member death → `m` on peers, clean | — | 14 ms | same |
| member death → `m`, silent | — | 17.4 s (keepalive 5+3×5) | same |
| databasehost switchover after `m` | manual (ABSTAIN) | ~20 ms, unanimous | `discovery/dbhost_switch_sim.py` |
| old crawl merges (June) | 116 s × up to 3 passes | — | prior chat |
| runMerge today (LAN side only) | 2–9 s healthy; 13.8 s on 3B | unchanged (see §4) | John's `time runMerge.bash` pastes |
| code on the per-event path | ≈4,300 bypassed | ≈1,760 | handoff |
| whole discovery system | ≈16,800 (walk/merge/notify 7,724 + broker 6,776 + daemon broker loop ~2,300) | ≈3,300 + ≈1,600 tests | counted 2026-10-09 |

pipe_workload from Seattle7 (r12): NY2 169/128 ms p50/p95 155.6 req/s; HappyDog 119/119, 231.2; 3B 114/60, 155.5;
Seattle5 136/18, 129.3 — all 300/300.

## 3. Where the per-event time goes now (what is left to shave)

In the daemon, per region event, as `REGION: event p timed: world=… bringup=… apply=… publish=… total=…` will say on
hardware (not yet seen on hardware; the sim can't reach `bringup`):

- `world` = one region RTT (droplet ~30–80 ms from a node on the WAN). Irreducible short of moving the region.
- `bringup` = `_reconcile_bringup_phase` (poll.py ~1644): per event it rebuilds the active-tunnel view from the kernel
  (`wg show` per iface) and the channel set. Not measured in the sim. Candidate: skip when the alive-hub set and the
  kernel iface set are unchanged (a hash, like the event-substance gate).
- `apply` = `ip route show` + one `ip` command per changed route (~2–4 ms each) + an admission probe (one echo,
  3 s ceiling) per *newly admitted* LAN route. Candidates: netlink instead of `ip` subprocesses (pyroute2 or raw
  netlink; saves ~2 ms per command, matters only on a burst), probes in parallel (they are serial).
- `publish` = one facts write + fdf-client's re-announce (7 ms) → one `p` to the fleet → each member does one `world`
  read and a rebuild that applies nothing. That echo is the cost of announcing; it is one event, not three now.
- Election: off the loop, ~20–40 ms from facts; it writes /etc/hosts and HUPs dnsmasq. `_write_view` reads /etc/hosts.
- fdf-facts `publish()` ≈ 57 ms (`ip -o addr`, `ip route`, `iw`, lease file) — runs on every kernel route change,
  including the daemon's own route installs (then "unchanged", no write). Candidate: ignore `ip monitor route` lines
  for metric-50 routes (the applier's own), or read via netlink.

Hold-down (60 s for a mere absence) and the 20 s keepalive are design numbers, not costs.

## 4. Open items, in the order I would take them

r18: in region mode nothing re-derives what the region holds -- makeHostJson replaced by the daemon's
region_hosts.json (item 1's last open part), capability tuples never published, Mode B's exit from the graph, the merge's
autoswitch / bring-up / crawl-cache flush / notification spawn / enrolment retry not run. Left that still asks the
network or the store for something the region could answer: `registerFrogNetHost.bash` (cron, every 15 min) heartbeats
known_frognets in the store (NetworkName = `hostname`, i.e. "FrogNetHost" on every node: one shared row) for frogs.js
and gps_dashboard.html -- membership is the region's; the tunnel daemon's capability probe every 600 s still runs the
full cpu/disk benchmark to announce numbers that do not change.

Status at r17 (details in `docs/WORKLOG-r17.md`):
- 1 DONE (r17 `BASELINES_SURVIVE_THE_WIPE_V1`). Still open inside it: `makeHostJson.bash rebuild` is forced every merge
  and re-curls each peer whose echo is empty (its timeout, serially in the deployed version); the cache now survives,
  so only the peers that never answer cost anything. Needs a decision on `rebuild` in region mode.
- 2 DONE: same cause as 1 (`dnsmasq_content_key` lived in /etc/sentinels).
- 3 DONE (region mode: not run). 4 DONE (stopped by fdf-region-join). 5: runAgain label DONE; watcher stopped in
  region mode; `propogateNotification` already exits in region mode.
- 6, 7 open (engine; 3B's proxy -- py-spy on the proxy during a 503 burst).
- 8 open. Note for it: runMerge's autoswitch "online" test is `broker-reachable`, which in region mode is answered by
  region.my_channels (the region, not the broker) -- removing the broker does not make merges tear tunnels down.
- 10, 11 (first half), 13, 15 open. 11 second half (leaf row) is in fdf-facts already. 12 DONE
  (`ANN_GOES_WITH_THE_SESSION_V1`). 14 DONE (`tools/netns-region-loop-test.sh`).
- New, found this round, open: `region.own_subnet()` "Falls back to config.LOCAL_SUBNET and says so" -- a fallback;
  `bringup` is still ~15 ms of Python per event with nothing to do (handshake-rtts/grace/broker-state files written
  every event): candidate for a no-change skip once hardware numbers are in.


**FrogNet side (the merge, now LAN-only), found while examining merge triggering; not changed:**

1. `runMerge.bash:147 rm -rf /etc/sentinels/*` deletes `frognet_hosts`, `old_host_json`, `echo_cache` every pass →
   `_topology_changed` always true (`sync_required reason=hosts_changed`, `chain_dirty=1` on every pass on every node),
   `mv: cannot stat old_host_json.tmp`, and `makeHostJson.bash rebuild` re-curls `frognet_echo.php` on every peer
   serially (`--connect-timeout 10 --max-time 45`; each `"echo": ""` cost its timeout: 27 s on Seattle7, 16 s on
   HappyDog). Move the baselines to `/var/lib/frognet` (as `region_hosts` already is) or stop wiping.
2. `DNSMASQ_NO_SENTINEL key=… restarting` → dnsmasq restarted on every merge; whatever writes that sentinel isn't.
3. `PUBLISH_CAPABILITY` self-benchmark (disk write, fsync, cpu) ≈ 9 s per merge on a Pi; cache it for an hour, or
   drop it — in region mode the daemon publishes capability as a fact and the store tuple is unused by the election.
4. The 60 s advertise timers (`frognet-dbhost-advertise`, `frognet-mediahost-advertise`,
   `frognet_register_candidate.sh`) write capability tuples nobody reads in region mode. Stop them in region mode.
5. `frognet-merge-watcher` and `propogateNotification` are dead weight in region mode (no pokes are ever sent);
   `runAgain_reinvoke reason=changed_24_not_yet_converged` is mislabelled (it is `external_during_pass`: another
   runMerge hit the held lock).
6. SAME cache miss / template re-learn per request on the semantic engine (prior work `apply_same_miss.py`,
   `SAME_IS_BACK_V1`); `frognet_tuples` lacks `heartbeat` and `on_database_change` (an AttributeError per cycle in
   proxy and daemon metrics); `mv: cannot stat /etc/sentinels/old_host_json.tmp`.
7. 3B's proxy at its 256-slot `_ACTIVE` ceiling (`503 Proxy overloaded`) two minutes after AIConnect was disabled:
   something else still fills it. Not diagnosed. `py-spy dump` on the proxy during a 503 burst is the tool.

**FD&F side:**

8. The broker is unneeded (every endpoint's job is done by the region or the hub; `region.py` is the shim that
   answers the daemon's four broker calls from facts). Removal = stop/disable `frognet-broker-v4` on the droplet,
   delete the non-region branch of `poll.py` (~2,300 lines: `run_poll_loop`, `poll_once`, `_sync_transit_subnets`,
   the broker channel code), drop `frognet-pond-bootstrap`, `frognet-channel-join/advertise`, stop saving
   `tunnel.conf.broker`. First move chorus join/leave into a fact (the one thing the broker did that the region does
   not yet).
9. "Shit all over the disk" (John): the cleanup list is the old path in §2 of the README's counts — 7,724 lines of
   walk/merge/notify/elect, 6,776 of broker, the dispatcher and timers. He wants it cleaned up *after* discovery is
   as fast as it can be. The merge still runs at boot and on NetworkManager events for LAN-local work (iptables,
   autoswitch, resolv/upstream, identity preflight); that is the part to keep or re-home, not delete.
10. `Applier.apply` (CC 86) owns admission, hold-down, withdrawal, default and hosts in one function; split it.
11. `fdf-facts` reads dnsmasq's lease file only; a node serving DHCP another way writes no `given` rows. A leaf
    needs a `leaf` row from facts.role/addr.
12. Stale `FrogNetHost.Seattle*` members (dead duplicates from before `ME_BY_GUID`) sit in the participant list
    while dead; a manager-side expiry would clear them.
13. Databasehost failover on hardware: take the gap between `GRAPH membership: went=[('Seattle3B', …)]` and
    `REGION: ELECT: control=… databasehost=<new>` on another node after a 3B reboot, and the same for `came`, and put
    it in the handoff (sim says ~20 ms after the `m`).
14. The `bringup` phase has no simulator coverage (apply_once bypasses it). A netns run of the real region loop
    (`run_region_loop` with `config` from a test tunnel.conf) would close that gap and let the timed line be read
    without hardware.
15. The mediahost pond-wide oracle (`core/test_mediahost_pondwide_oracle.py`) fails on John's committed tree too;
    it contradicts the on-segment oracle, which is the current doctrine (`FROGNET_NOW_mediahost_onsegment.md`).
    One of them should go.

## 5. How to build

Host needs g++ (C++20), lz4, openssl, jemalloc (x86 static host), `aarch64-linux-gnu-g++` for the Pi binaries
(`tools/build_arm64.sh` fetches and cross-builds lz4 and OpenSSL once into `build/arm64/`).

```
cd rinab
bash tools/build.sh              # applies tools/apply_*.py to platform/ramsrv/ram_server.cpp and ribbit_cpp/frogram.cpp
                                 # (keepalive, refusal-why, session-epoch, http front, ...), builds build/rina-host
bash tools/build_static_host.sh  # build/bin/rina-host-static (x86, static, jemalloc)
bash tools/build_arm64.sh        # build/arm64/{fdf-client,fdf-participants,ram,fdf-events,rina-host} static aarch64
# x86 static clients:
P=platform; for t in fdf_client:fdf-client fdf_participants:fdf-participants ram_tool:ram fdf_events:fdf-events fdf_attention:fdf-attention fdf_where:fdf-where; do
  g++ -O2 -pthread -D_GNU_SOURCE -std=c++20 -I$P/ribbit_cpp -Isrc src/${t%%:*}.cpp build/obj/frogram.o \
      -llz4 -lcrypto -lresolv -static -o build/static/${t##*:}; done
```

r17: `rinab/platform/ribbit-v0.63/` (the pristine platform build.sh copies and patches) is now in the source tarball
-- r16's tarball held only the patched output, so build.sh could not run from it. `tools/make_deliverables.sh OUTDIR`
assembles all three deliverables and refuses if any tag or binary disagrees. It never ships /opt/fdf/var or
/opt/fdf/hub/hub.env (the package copy says 8840/50820; the droplet runs 51888/50889).

The deliverables are assembled from `fdf/` (scripts, python) + the binaries into three bundle trees
(`fdf-node` x86, `fdf-node-arm64`, `fdf-droplet`), each with `/opt/fdf/BUILD` (`build=fdf-YYYY-MM-DD-rN`,
`built=`, `for=`), tarred from `/`; the node installers are `deliverables/header.sh` (with `__ARCH__` substituted)
concatenated with the tgz. Every build: bump the tag in `src/fdf_version.hpp`, `tools/fdf-hub`, `fdf/bin/fdf-facts`,
`fdf/lib/python/fdf_graph.py`, `frognet/.../region.py`; rebuild every binary; ship all three; the tag is on every log
line. (In this session the tree lived at `scratchpad/{src/rinab,pkg-full/opt/fdf,repo,bundle,run}`; the tarball
flattens that to `rinab/`, `fdf/`, `frognet/`, `deliverables/`.)

## 6. How to test (all must pass before a build ships)

| test | what it proves | run |
|---|---|---|
| `rinab/tools/netns-graph-test.sh` | real WireGuard (wireguard-go) in namespaces, y → z → hub → a: rows, graph, admission by echo, hub allowed-ips, both directions ping, hold-down, withdrawal, duplicate /24 refused — 17 checks | root; `bash tools/netns-graph-test.sh` (uses `build/static/*`) |
| `rinab/tools/netns-latency-test.sh` | the stage-by-stage timings in §2, and member death → `m` | root |
| `rinab/tools/netns-region-loop-test.sh` | the REAL daemon (run_region_loop) on z and a, per-node mount namespaces: converge after start, y→a ping, N changes timed by the daemon's own `REGION: event p timed` line, announcement gone with the session | root; `N=6 bash tools/netns-region-loop-test.sh`; needs wg-quick too |
| `frognet/.../discovery/dbhost_switch_sim.py` | switchover from facts, 14 checks, store never read | `FDF_PY=fdf/lib/python PYTHONPATH=frognet/opt/frognet_semantic python3 …` (needs John's full `core/` tree beside it for `frognet_tuples`; the repo copy in the tarball is the changed files only) |
| `frognet/.../internet_tunnels_v3/tests/graph_apply_oracle.py` | applier: admission, hold-down, withdrawal, membership transitions, gen bumps | `python3` from `opt/frognet_semantic` |
| `frognet/.../internet_tunnels_v3/test_region_oracle.py` | region client, my-channels, maps | same |
| `frognet/.../internet_tunnels_v3/gen_oracle.py` | the generation counter, 8 checks | same |
| `fdf/lib/python/fdf_graph_oracle.py` | the graph: adjacency, forest, exits, own-tunnel rule, conflicts | `python3` |
| `rinab/tests/hub_oracle.py` | the hub's desired state: no memory, leaf, child with/without tunnel, parent leaves | `FDF_PY=../fdf/lib/python python3 tests/hub_oracle.py` |
| `rinab/tests/mapown_oracle.sh` | mapping ownership refused with holder named, 4 checks | root (needs built host) |

The netns tests need `ip netns`, `wg`, `wireguard-go`, python3, and `build/static/{rina-host,fdf-client,fdf-events}`.
Clean up stale namespaces first if a run was interrupted:
`for n in hub z a y dup; do ip netns pids $n | xargs -r kill -9; ip netns del $n; done; ip link del br-inet`.

## 7. Log vocabulary to read hardware by (r16)

- daemon: `REGION: loop started (...) [ELECT_IN_PROCESS_V1 VIEW_FACT_ON_SUBSTANCE_V1 GEN_V1 NO_MERGE_ON_REGION_EVENTS_V1];
  region.py build …; the region is build …`; `REGION: capability published as a fact (mysql=… cores=… …)`;
  `REGION: event p timed: world=…ms (participants+mappings, one read) +0ms bringup=…ms apply=…ms (probes=n/…ms
  routes=n/…ms) publish=…ms total=…ms`; `GRAPH build=… rebuild=N gen=M hosts=… links=… me=… exit=… routes=+a/-b …`;
  `GRAPH changed: <member> keys=[…] +[rows] -[rows]`; `GRAPH membership: went=[…] came=[…]`; `GEN: n -> n+1 why=… by=…`;
  `REGION: world gen a -> b (<member> n->m) after …`; `REGION: ELECT: candidates from the region's facts: N member(s)
  with a capability; my LAN (graph links): […]`; `REGION: ELECT: control=… databasehost=… mediahost=… hosts_changed=…`;
  `REGION: election after … done in N s (off the loop)` / `… FAILED after N s: … (re-run on the next event)`;
  `REGION: lost; withdrew its routes and names, tore down N tunnel(s); waiting for the region`.
- fdf-facts: `fdf-facts: build …`; `wrote/unchanged N rows {...} uplink=[…]`; `uplink <dev> alive|DEAD (3 misses)`.
- fdf-client: `joined the region at …, announced […] with facts`; `facts changed, re-announced`; `mapping X -> … registered`;
  `mapping X is NOT in the region though this session registered it; registering again`; `REFUSED by the region:
  EEXIST (X is held by a live session (node:w:<guid>); not transferred)`.
- hub: `event p/m: members=… mappings=… peers=… prefixes=… changes=… build=…`; `graph: hosts=… links=…`;
  `no verified mapping names this hub for X (name): peer without its /24`; `deleted peer … (not a member now)`.
- region: `version` op answers the build; refusals carry `why`.

## 8. Rules in force (from the handoff, restated)

1. adjacency = a `held` row and a `given` row agreeing on (ip, mac), the giver serving that /24
2. the routing graph is a forest: one upward link per host toward its ranked exit (speed, then lower .1)
3. a host with a hub link carries and registers its own /24; an exit carries only tunnel-less children
4. the hub remembers nothing
5. a mapping belongs to the live session that registered it; a second claimant is refused with the holder named
6. absence is held 60 s; positive failure (hub handshake dead, region gone) is withdrawn now; alive is the session -- no echo or ping for what RAM says (r19)
7. the node's own region traffic is sourced from its .1
8. an uplink is dead after three consecutive missed pings, alive after one answer
9. no merge for a region event; the gen counter is the audit trail
10. the elections' input is the region (facts.capability of the alive members), never a store
11. no fallbacks; gone is gone
