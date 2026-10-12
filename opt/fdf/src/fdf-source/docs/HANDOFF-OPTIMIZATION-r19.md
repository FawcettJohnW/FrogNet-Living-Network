# HANDOFF — optimization thread, r17 → r19 (build fdf-2026-10-10-r19, 2026-10-10)

Start here. Then `TRANSITION-README.md` (what the system is, where it runs, the rules), `TRANSITION-OPTIMIZATION.md`
(open items, build, test, log vocabulary) and `HANDOFF-RAM-DISCOVERY-2026-10-09.md` (the build record r5–r19).
`WORKLOG-r17.md` has every step of this thread with its evidence. Review diffs: `docs/r16-to-r19.diff` (whole package);
`frognet/frognet-ram-discovery.diff` (32 FrogNet-side files against John's repo @783671f).

## 1. State

- r19 built and delivered: `fdf-install-aarch64.run`, `fdf-install-x86_64.run`, `fdf-droplet.tgz`, `fdf-source-r19.tgz`.
  **Not yet installed on hardware.** The last build John reported running is r13. Every number below is from the
  sandbox (x86, network namespaces), not from the fleet.
- Install order: droplet first, then nodes.
  ```
  droplet: cd / && tar xzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub
  Pi:      sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>
  x86:     sudo bash /tmp/fdf-install-x86_64.run <SITE KEY>
  ```
- The doctrine John set in this thread: **when the data is in RAM (the region), nothing asks the network or a store for
  it.** In order: makeHostJson is unnecessary; frognet_echo comes from RAM; the aliveness checks come from RAM (alive =
  the session). Apply it to anything else found.

## 2. What each build did

**r17: discovery path and merge baselines**
- `WATCH_BEFORE_READ_V1`: the region loop registered fdf-events' watch AFTER its start read and the 5 s hub bring-up,
  so every change in that window was never delivered (both daemons stale for over 60 s, 3 of 3 runs). Fixed in the
  daemon and in fdf-hub.
- `WORLD_READ_ONCE_PER_EVENT_V1` + `KERNEL_READ_ONCE_PER_EVENT_V1`: one `world` read and one `wg show all dump` per
  event (was 5 region reads and 7 execs).
- `NO_DEAD_STORE_READ_V1`: a reach_plane store read (never used) on every election, aimed at the databasehost. The r16
  simulator's "store never read" check was swallowed; it now counts calls.
- `ANN_GOES_WITH_THE_SESSION_V1` (rina-host): announcements are deleted with their session; no dead rows.
- `FACTS_FEW_EXECS_V1`, `FACTS_IGNORE_OWN_ROUTES_V1`: fdf-facts makes one exec per write, and non-default route churn
  no longer wakes it.
- FrogNet side:
  - `BASELINES_SURVIVE_THE_WIPE_V1`: runMerge's `rm -rf /etc/sentinels/*` keeps 8 baselines. This was one cause for
    hosts_changed on every pass, the dnsmasq restart on every merge, and the echo re-curl.
  - `REGION_MERGE_WRITES_NO_STORE_V1`, `RUNAGAIN_SAYS_WHO_V1`.
  - `STORE_UNITS_OFF_IN_REGION_V1`: fdf-region-join stops the advertise timers, the elect timer, the merge watcher
    and pond-bootstrap; fdf-leave restarts them.

**r18: stop re-deriving what the region holds**
- `HOSTJSON_FROM_THE_REGION_V1`: makeHostJson.bash does not run in region mode. The applier writes
  `/var/lib/frognet/region_hosts.json` and `getHosts.php` serves that file.
- `CAPABILITY_TUPLES_OFF_IN_REGION_V1`: `core.role_publish.publish_all` is a no-op in region mode. It was every
  capability publisher: merge, frognet-daemon boot and float, proxy boot, communicator.
- `EXIT_FROM_THE_GRAPH_V1`: fixDefaultRoute Mode B takes the graph's exit. Before, it pinged and curled
  getDefaultRoute.php on every known host, 15 s timeout each, serially.
- runMerge skips the autoswitch, the TransitPeerCache flush, propogateNotification and the enrolment retry.
  discovery.live skips its bring-up-only python.
- `FILES_ONLY_TAR_V1`: deliverables tar files only, so existing directories keep their owner and mode.

**r19: echo and aliveness from RAM**
- `ALIVE_IS_THE_SESSION_V1`:
  - The applier sends no admission echo.
  - fixDefaultRoute pings no next hop that an alive member wrote. It still pings non-member gateways.
  - Rule 6 now reads: positive failure = hub handshake dead or region gone.
- `MEMBER_IS_NOT_MARKED_V1`: the proxy's not_frognet cache never marks a host in region_hosts.json.
- `ECHO_FROM_THE_REGION_V1`: frognet_echo.php answers from the node's own facts: `"<name>,<.1>,<upstream>,"` (the
  RegionEcho form, also used in region_hosts.json).

## 3. Measured (sandbox) — side by side

| what | r16 | r17 | r18 | r19 |
|---|---|---|---|---|
| converge after daemon start | not within 60 s (3/3) | 1–5 s | 6 s | 5 s |
| timed `total` per event, median a/z | 39/40 ms | 22–29 ms | 16/18 ms | 33–45 ms* |
| execs per event in the daemon | 7 | 2 | 2 | 2 |
| region reads per event | 5 | 1 | 1 | 1 |
| fdf-facts rows() | 10.3 ms | 3.0 ms | 3.0 | 3.0 |
| echo probes per newly admitted route | 1 (3 s ceiling) | 1 | 1 | 0 |

\* Same code on the steady path as r18 (steady events had `probes=0` already). Read it as sandbox variance; the
hardware timed line decides.

**Merge estimate given to John (not measured):** about 1–3 s on a Pi (was 2–9 s); 3B about 4–5 s (was 13.8 s).
Take the real number from the first r19 merge's stage lines.

## 4. What to read on hardware after install (no commands needed)

- Daemon start line: `REGION: loop started (...) [... WATCH_BEFORE_READ_V1 WORLD_READ_ONCE_PER_EVENT_V1
  KERNEL_READ_ONCE_PER_EVENT_V1 HOSTJSON_FROM_THE_REGION_V1 ALIVE_IS_THE_SESSION_V1]`.
- Per event: `REGION: event p timed: world=… bringup=… apply=… publish=… total=…`. Compare it with the r13 lines.
- Merge lines:
  - `mode region=1`
  - `autoswitch action=none reason=region_mode_…`
  - `sentinels_wiped removed=N kept=[…]`
  - `flush_discovery_cache decision=skip`
  - `PUBLISH_CAPABILITY not run` and `PUBLISH_ALL not run`
  - `REACH_PLANE_TUPLE not written`
  - `reconcile_tunnels not run`
  - `EXIT_FROM_THE_GRAPH me=… exit=…`
  - `makeHostJson decision=skip`
  - no `DNSMASQ_NO_SENTINEL` line
  - `sync_required` only when something moved
  - the merge's total wall clock
- fdf-region-join: `== region mode: <unit> stopped and disabled`.
- Databasehost failover (open item 13): the gap between `GRAPH membership: went=[('Seattle3B',…)]` and
  `REGION: ELECT: … databasehost=<new>` on another node.

## 5. Open — next, in order

1. **Get r19 on hardware and take the numbers**: the timed line per node, merge wall clock, 3B, and the failover gap.
2. `registerFrogNetHost.bash` (cron, every 15 min) heartbeats known_frognets in the store for frogs.js and
   gps_dashboard.html. NetworkName is `hostname`, which is "FrogNetHost" on every node, so all nodes share one row.
   Membership is the region's. Same doctrine: serve those pages from the region. John's call.
3. The tunnel daemon's capability probe runs the full cpu/disk benchmark every 600 s to announce slow-moving numbers.
   Candidate: benchmark once per boot, re-read only the cheap fields.
4. `region.own_subnet()` "falls back to config.LOCAL_SUBNET and says so": a fallback (John's rule 11).
5. Bringup is still ~10–20 ms of Python per event with nothing to do (handshake_rtts, grace counts and broker-state
   files are written every event). Decide on a no-change skip after the hardware numbers are in.
6. From the original brief, still open: item 6 (SAME template re-learn; `frognet_tuples` lacks `heartbeat` and
   `on_database_change`), 7 (3B proxy at its 256 `_ACTIVE` ceiling: py-spy during a 503 burst), 8 (broker removal; the
   autoswitch no longer depends on it), 9 (cleanup of the old path), 10 (split `Applier.apply`), 11 first half
   (lease file only from dnsmasq), 13 (hardware failover), 15 (the mediahost oracle contradiction).

## 6. Build and test

Build:
```
cd rinab
bash tools/build.sh
bash tools/build_static_host.sh
# x86 static clients (TRANSITION-OPTIMIZATION.md §5)
bash tools/build_arm64.sh
bash tools/make_deliverables.sh OUTDIR
```
- `build.sh` needs `platform/ribbit-v0.63` (in the tarball since r17).
- `make_deliverables.sh` refuses on any tag or binary mismatch. It never ships /opt/fdf/var or /opt/fdf/hub/hub.env
  (the package copy says 8840/50820; the droplet runs 51888/50889).
- Every build bumps the tag in: `src/fdf_version.hpp`, `tools/fdf-hub` and `fdf/bin/fdf-hub` (keep them identical),
  `fdf/bin/fdf-facts` (two places), both `fdf_graph.py` copies, and `frognet/.../region.py`. A new build is always a new
  tag.

Tests (all PASS at r19):
- `tools/netns-graph-test.sh` (17 checks).
- `tools/netns-region-loop-test.sh` (`N=6`). This is the real daemon on z and a, each in its own mount namespace. It
  checks converge, ping, N timed changes, region_hosts.json, and that an announcement goes with its session.
  `PAUSE=<s>` holds the namespaces for strace.
- `tools/netns-latency-test.sh`.
- `tests/hub_oracle.py`, `tests/mapown_oracle.sh` (needs the clients in `build/bin`).
- `fdf_graph_oracle.py`.
- FrogNet side: `graph_apply_oracle`, `test_region_oracle`, `gen_oracle`, `test_region_backends_oracle`,
  `test_register_on_404_oracle`, `dbhost_switch_sim` (15 checks), `discovery/test_not_frognet_skip_oracle`. These need
  John's full tree: clone the public repo `FawcettJohnW/frognet-living-network` and overlay `frognet/opt/frognet_semantic`
  on its `opt/frognet_semantic`.

Sandbox notes (environment only, never shipped):
- Install iproute2, wireguard-tools, `g++-aarch64-linux-gnu`, liblz4-dev, libssl-dev and libjemalloc-dev, plus
  qemu-user-static for arm64 smoke tests.
- wireguard-go: build it from GitHub with the Go deps cloned from github.com/golang/* (proxy.golang.org is blocked).
  Patch `conn/bind_std.go` so an IPv6-less kernel doesn't make it report listen-port 0.
- The netns tests expect `../../repo/opt/{fdf,frognet_semantic}` and `../../pkg-full/opt/fdf` relative to `rinab/`.
  Symlinks are fine.

## 7. Rules in force

Rules 1–11 as in `TRANSITION-OPTIMIZATION.md` §8, with rule 6 now: absence is held 60 s; positive failure (hub handshake
dead, region gone) is withdrawn now; alive is the session, so there is no echo or ping for what RAM says.

John's standing rules:
- one tarball per machine type, all three every build, new tag;
- logs say what is deployed, never "old version";
- no fallbacks; gone is gone;
- the hub has no memory;
- nothing new in /usr/local/bin (r17–r19 replace only the four files r16 already shipped there);
- node commands use `cd / && tar xzf - < /tmp/<file>`; no output redirection;
- lead with the answer; measured numbers side by side; estimates when he asks for an estimate;
- if the data is in RAM, read RAM.
