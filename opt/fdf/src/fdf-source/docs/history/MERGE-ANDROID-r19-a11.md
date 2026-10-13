history: superseded by docs/OPERATING.md

# MERGE: the Android thread's work on r19 (fdf-2026-10-10-r19-a11)

Base: John's fdf-source-r19.tgz. Build tag `fdf-2026-10-10-r19-a11`. That is r19, plus the a4 changes (MERGE-ANDROID-r16-a4.md), plus PLAN-PONDS, plus what testing on r19 found. The full change against r19 is in `r19-to-r19-a11.diff`.
`rinab/tools/make_deliverables.sh` makes the three deliverables:

- `fdf-droplet.tgz`
- `fdf-install-aarch64.run`
- `fdf-install-x86_64.run`

## What r19-a11 adds to r19

| tag | files | what |
|---|---|---|
| `[KEEPALIVE_5S_V1]` | `rinab/tools/apply_keepalive_5s.py`, `build.sh` (one line); the result is in `platform/ribbit_cpp/frogram.cpp` and `platform/ramsrv/ram_server.cpp` | Keepalive on both ends: idle 2 s, then 3 probes 1 s apart, plus TCP_USER_TIMEOUT 5,000 ms. |
| `[RECV_SAYS_WHY_V1]` | same patcher, in `frogram.cpp` `recv_n` | A return of 0 means the far end closed the connection. A return of -1 now logs the errno; ETIMEDOUT says the 5 s keepalive or user timeout fired. Before this, a silent loss read as a close. |
| `[LEAF_ADDR_FROM_THE_REGION_V1]` | `rinab/src/rina_region.cpp` (announce, session down), `fdf_client.cpp` (prints it) | A leaf off the mesh gets the lowest free 10.252.X.Y. The address is bound to its session and freed when the session ends. A leaf that types its own address gets EINVAL. |
| `[HUB_SAYS_ITS_PEERS_V1]` | `fdf/bin/fdf-hub` = `rinab/tools/fdf-hub`, `region.py` my_channels | The hub announces the pubkeys it holds as peers. A member brings up a hub's channel only once that hub's facts list its key. |
| `[FORWARD_IS_A_FACT_V1]` | `rinab/src/fdf_client.cpp` | `/opt/fdf/var/forward` names the forward pond (`FDF_FORWARD_FILE`). Any other pond's fdf-client announces `{"role":"participant",…}` with no maps. inotify switches it in place, and the log says "is now FORWARD/PARTICIPANT". |
| `[ATTENTION_IS_A_TUPLE_V1]` | `rinab/src/fdf_attention.cpp` (new), `fdf_client.cpp`, `rina_region.cpp` | `fdf-attention "why"` writes the tuple (pond, attention, w:<guid>) {why, from, ts}, and `--clear` removes it. A participant's fdf-client holds a read on that tuple and logs `ATTENTION <area>: why (from)` to `FDF_ATTENTION_DIR/<area>.json`. The cells are swept when the member's session ends. |
| `[FORWARD_SWITCH_IN_PLACE_V1]` | `region.py`, `poll.py` run_region_loop | The daemon watches `forward.want`. The old pond is withdrawn and its maps cleared. The daemon then sets `REGION_RAM`/`AREA`, rewrites join.env and site.env, writes `forward`, and rejoins at once with the same pid. |
| ponds tools | `fdf/bin/fdf-pond-add`, `fdf-forward`, `fdf-pond-remove`, `fdf-ponds`, `fdf-leave` | `fdf-client@<area>.service` from `ponds/<area>.env`. `fdf-leave` also removes ponds, attention, forward and forward.want. |
| `[FACTS_ONE_WRITER_AT_A_TIME_V1]` | `region.py` write_facts, `fdf/bin/fdf-facts` write_facts | The read-merge-rename runs under `flock(facts.json.lock)`, and each writer renames its own tmp file. **This is an r19 bug, not a pond bug.** The daemon and fdf-facts shared `facts.json.tmp`, so one writer could rename the other's truncated tmp. The file then read as `{}`, and the next merge wrote only its own keys. That loses name, guid, subnet, pubkey and role, so the hub gives the node no peer. |
| `[ECHO_UPSTREAM_FROM_THE_ROWS_V1]` | `frognet/var/www/html/frognet_echo.php`, `discovery/real_backends.py` (`region_echo`, RegionEcho), `internet_tunnels_v3/graph_apply.py` (region_hosts.json) | **r19 bug.** In region mode the echo's fields 3 and 4 were read from `facts.echo.upstream`, which nothing writes, so they were always empty (`New-York-2,10.28.28.1,,`). They are what getFrogNet.bash printed, getWlan0IP and getWlan1IP: the node's upstream addresses. They now come from the facts' `uplink` rows in order, with 0.0.0.0 where there is none: `New-York-2,10.28.28.1,<upstream>,0.0.0.0`. descend.py reads field 3 to vouch a neighbour hanging off our LAN. The same rule is used in all three places, and the oracles and the region-loop test now check it. |
| `[LEAF_ROW_FROM_ADDR_V1]` | `fdf/lib/python/fdf_graph.py` (both copies), `fdf_graph_oracle.py` (checks 10, 10c), `netns-region-loop-test.sh` (leaf stage) | **r19 bug.** A leaf with no rows and only `facts.addr` (the phone, or a laptop running only fdf-client) never became a host in the graph. The leaf test was nested under the subnet test, which a leaf never passes. As a result, no node routed or named a phone: the hub held its peer, and Seattle5 sent pings for 10.252.0.1 out its default route on wlan1. Now every hub-linked node routes the /32 on its hub link, a tunnel-less child routes it via its exit, and every node names it. Region-loop test: a and z route the leaf 174–177 ms after its fdf-client starts; a and y ping it; a's /etc/hosts names it. The same test run against r19's graph FAILS 3 checks. |
| `[DEVICE_ROW_V1]` | `graph_apply.py` (region_hosts.json: `role` and `alive` per host, leaves included), `var/www/html/getHosts.php` (passes them through), `usr/local/bin/frognet_monitor_py/` `discovery.py`, `probes.py`, `main_loop.py` (the public repo's files plus this change) | The monitor drew the phone as DOWN after its frognet_echo probe failed; a device runs no FrogNet stack, so that probe can never answer. A leaf is now not echo-probed, and its row reads `<name>  DEVICE  ONLINE`, or `DOWN`, with the rest of the row blank. ONLINE/DOWN is its region session, the aliveness every member already has: a host in its hold-down is still listed with alive=false and reads DOWN, then it is gone. The graph_apply oracle and the region-loop test check the JSON; the screen was checked by running the monitor in a pty. |
| `[COMMS_ON_A_NODE_V1]` `[SERVICE_WHERE_IS_A_TUPLE_V1]` `[WHERE_ONLY_WHILE_A_MEMBER_V1]` | `rinab/src/fdf_where.cpp` (new `fdf-where`), `rina_region.cpp` (session-down sweep), `fdf/bin/fdf-comms.sh`, `rinab/tools/build_comms_deps.sh` and `build_comms_static.sh` (new), `make_deliverables.sh` (`/opt/fdf/comms` in both node bundles), `netns-region-loop-test.sh` (Communicator stage) | **The Communicator's servers run on a node, not a cloud service.** Each node bundle carries static `comms-ram` and `comms-media` in `/opt/fdf/comms`; the arm64 transcode test passes under qemu. On a node, `fdf-comms.sh` starts both and writes the cell `(pond, where, w:<guid>) {"comms":{"ram":"<.1>:8800","media":"<.1>:8994"}}`. The cell belongs to the member and is swept when its session ends. `fdf-where` refuses to write it unless the member's session is alive (a cell written while it is down would outlive it). When the node returns, `fdf-comms` writes it again on the next region event. In the region-loop test, node z hosts the Communicator: the cell appears 230–256 ms after start; the phone's Kotlin member finds it; a leaf streams to it through the hub (119 frames, 250 audio packets); the phone's Java Communicator core watches a feed that node a streams to z (three sites, the hub between them). The cell is gone with z's session and comes back with z. |
| `[CLEANUP_UNTIL_EMPTY_V1]` | `netns-region-loop-test.sh` | Test harness. A process forked between the pid listing and the kill kept the last run's namespace and its root-side veth, so the next run failed with "File exists" and z never reached the hub. Cleanup now kills until each namespace is empty, then deletes the veths by name. This is one cause of test-only failures; the original 2-in-59 convergence failures showed no setup error and remain open. |
| packaging | `make_deliverables.sh`, `build_arm64.sh`, TRANSITION-OPTIMIZATION §5 | fdf-attention is in every bundle and every check. |
| tests | `rinab/tools/netns-ponds-daemon-test.sh` (new), `netns-hub-test.sh` (brought up to date), `netns-leaf-tunnel-`, `-placement-` and `-member-death-test.sh`; `test_region_oracle.py` (fixture has peers, plus control 2h); `netns-region-loop-test.sh` ([DIAG-CONVERGE] dump on a convergence failure) | |

## Tested on this build (sandbox, nothing on hardware)

| test | result |
|---|---|
| `netns-ponds-daemon-test.sh` S=10 (the real daemon in two ponds) | **PASS 24/24.** From `forward.want` written: forward written in 112 ms, hubA dropped n at 157 ms, hubB held n at 201 ms, the route was on at 401 ms, and the first answer came at 423 ms. Ten more switches took 356–636 ms each. Same daemon pid, one session per pond, and facts.json kept its identity. |
| `netns-region-loop-test.sh` (r19's) | 57 of 59 runs PASS, converging in 1 s; the 2 that did not are under "Open". Median timed total is 24–40 ms. |
| `netns-hub-test.sh` | PASS 25/25 after bringing the test up to date. It predated r17: it had no FDF_PY, so fdf-hub could not import fdf_graph. It expected parked peers, a 20 s silent-loss bound, and a leaf that types its own addr. Now: gone is gone, a 4,000 ms silent loss, and the leaf is given 10.252.0.1 by the region. Rejoining uses a new tunnel, raised once the hub's facts hold the key. |
| `mapown_oracle.sh`, `hub_oracle.py` | PASS |
| arm64 under qemu: rina-host, fdf-events, fdf-client (leaf), fdf-attention | Leaf given 10.252.0.1/32. `p` delivered. Notice 1 written. Every binary says r19-a11. |
| `netns-graph-test.sh` | PASS |
| `netns-two-ponds-test.sh` (the phone's logic) | PASS 14/14. The notice took 25 ms. |
| `netns-placement-test.sh` / `netns-leaf-tunnel-test.sh` | PASS 18/18 and 10/10 |
| `netns-member-death-test.sh` | Clean death seen in 33 / 23 ms. Silent death 3,859 / 3,944 ms. Silent region seen by the member in 4,013 / 3,996 ms (C++ / Kotlin). |
| oracles | PASS: graph_apply, test_region (with the new 2h), gen, register_on_404, region_backends, fdf_graph, dbhost_switch_sim |

## Open

`netns-region-loop-test.sh` did not converge in 2 of its first 4 runs on r19-a11. z routed y's /24 over wg0 instead of over y's lease, and z's graph showed `links=0` for 60 s. The next 57 runs in a row passed. Nothing changed between them except the dump added for a failure, and it runs only when a run fails.

I don't have the cause. The failed runs kept only z's routes and daemon tail, not what was announced. The `[DIAG-CONVERGE]` dump now keeps all of it:

- the region's participants and mappings
- every facts file
- every client and fdf-facts log
- z's GRAPH history
- both leases files

A link needs z's `given` row and y's `held` row with the same ip and MAC, as announced.

## Install (droplet first)

    droplet: cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub
    Pi:      sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>
    x86:     sudo bash /tmp/fdf-install-x86_64.run <SITE KEY>
