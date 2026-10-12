# HANDOFF: FrogNet on FD&F, 2026-10-09 — Stages 0–3 built and proven in the sandbox; hardware next

Read PLAN-FROGNET-ON-FDF.md first (the design, the rulings, the six security contracts). This file says what exists,
what was proven, and what to run on real machines. It supersedes nothing: HANDOFF-FDF-2026-10-08.md still describes the
node package, the kart demo and the browser terminal.

## Downloads

| File | sha256 (16) | What |
|---|---|---|
| `fdf-node.tgz` | 125aae2a80600af0 | COMPLETE node package (the 2026-10-08 package + everything below); on a fresh machine untar this alone |
| `fdf-region-update.tgz` | 225c2b1058bf07b0 | Untar over an installed fdf-node.tgz: new static x86 `rina-host`, `fdf-client`, `fdf-participants`, `ram`, `fdf-events`; `fdf-hub` + its oracle + README + hub.env; systemd units `fdf-client.service`, `fdf-hub.service`; `tools/build_arm64.sh`, `tools/apply_keepalive.py` |
| `fdf-arm64.tgz` | 44ade043ed0ddf5f | The same member binaries, static aarch64 (Pi). Untar AFTER the two above |
| `frognet-region-patch.tgz` | 2371811a58aedb56 | FrogNet tree changes, rooted at `/`: `opt/frognet_semantic/discovery/{real_backends.py,descend.py,live.py,test_region_backends_oracle.py}`, `opt/frognet_semantic/internet_tunnels_v3/{region.py,config.py,poll.py,peer.py,test_region_oracle.py}`, `usr/local/bin/propogateNotificationInternal` (exits 0 in region mode) |
| `frognet-region.diff` | | The same as a diff against commit 783671f (348 lines + 2 new files), for the repo |
| `fdf-windows-leaf.zip` | 40461c0539695dbf | `fdf-client.exe` that reads `facts.json` (a Windows leaf) + README + the deploy doc |
| `fdf-rinab-src.tgz` | 70d5ce9f6990a6c7 | The FD&F source tree with every change below (build: `tools/build.sh`, then `tools/build_rlite.sh`, `tools/build_static_host.sh`, `tools/build_arm64.sh`) |
| `PLAN-FROGNET-ON-FDF.md` | | The plan, current |

## What was built (tags, in the code)

Region (rina-host, `src/rina_region.cpp`):
- `[IPCP_FACTS_V1]` `announce` carries an opaque `facts` JSON object; `participants` returns it, plus each row's `id`
  (ownership tiebreak: lowest id = first claimant).
- `[MAP_PREFIX_LOCATOR_SET_V1]` `map_register` takes an EID prefix as `name`, its locator set as the signed `rloc`
  string (`hub:prio:weight,...`), the DIF's site key by `site`, and `own:true` to bind it to the session. `map_list`
  (one read of a site's mappings), `map_remove` (own only), `map_watch` name `*` (every registration and removal).
  Session end removes owned mappings and notifies watchers. Security contract 2: a `map|*` row exists only through
  this op; the HMAC covers name|rloc|ttl|ts exactly as before.
- `[SESSION_SWEEP_ALWAYS_V1]` BUG FIX: `rina_session_down` returned early for sessions that never said hello/node,
  so their watches (pwatch/mwatch) leaked forever. The sweeps now run for every session.
- `[KEEPALIVE_20S_V1]` (`tools/apply_keepalive.py`, in build.sh) SO_KEEPALIVE 5/5/3 on both ends of every FNWP
  socket. Verified with `ss -tnoi`: timer armed on all sockets. Silent loss = dead at 20 s; traffic resets it.

Member (`src/fdf_client.cpp`, Linux):
- `[IPCP_FACTS_V1]` reads `FDF_FACTS` (default `/opt/fdf/var/facts.json`), announces it, re-announces through its own
  session on inotify when the file is replaced (`mv`), never polls.
- `[MAP_FROM_FACTS_V1]` `facts.maps = [{"eid","rlocs"}]` → signed Map-Register (RINA_SITE_KEY under RINA_AREA),
  own:true; changed → re-registered; dropped → `map_remove`. Verified live (register, change, withdraw, session end).
- `src/fdf_events.cpp` (new): one held session; prints `p` / `m` / `gone` lines for non-FNWP programs.

Hub (`tools/fdf-hub`, Python, ~260 lines): see PLAN Stage 1 and `/opt/fdf/hub/README.md`. Oracle
`tests/hub_oracle.py` 15/15 (ownership, AllowedIPs never moved by last-write, facts-say-who/mappings-say-what,
transit, leave, adopt-on-start, leaf). Dry run against a live region: join → edge-only peer; mapping → /24 + route;
leave → peer gone.

Tunnel daemon (`internet_tunnels_v3`):
- `region.py` (new): the broker's four paths from the region, in the broker's shapes. `RINA_RAM=` in tunnel.conf
  switches it on; `broker_get/post` in poll.py and peer.py dispatch. `run_region_loop`: one `poll_once` per
  fdf-events line; on `gone`: `view.json` phase=recalc FIRST, tear down, runMerge. Commit phase writes phase=steady
  (gen+1, dbhost, hosts_hash) and mirrors it into facts (`view`). `_publish_region_maps` after bringup: our /24 and
  transit /24s with every live hub as a locator, prio by handshake RTT.
- Oracle `test_region_oracle.py` 13/13. Existing `test_register_on_404_oracle` and `test_transit_discover_v2` still
  pass; `test_transit_from_routes` fails before and after (pre-existing).

Discovery (`discovery/`):
- `[REGION_BACKENDS_V1]` `RegionRecords/RegionEcho/RegionGetHosts` in real_backends.py; selected by
  `FROGNET_DISCOVERY_SOURCE=region` in live.py. One read of the participants per merge. Probes untouched.
- descend.py: `[IMMEDIATE_ECHO_NO_RTT_V1]` now applies only when the echo was a probe (`echo.is_measurement`); from
  records every member "echoes", and that must not install a route (published != reachable). The no-reflect probe
  path now says PROBE_FAIL like the reflect path.
- Oracle `test_region_backends_oracle.py` 13/13 with the REAL engine: every alive member named in one merge, a
  dead session not named, an alive-but-unreachable member: PROBE_FAIL said, no route, and (HOSTS_GATE, the engine's
  own rule) no name. PLAN 3.6 is amended accordingly: a name without a route is a lie to dnsmasq's clients.
- All 86 discovery oracles run: 72 pass; the 14 that fail, fail identically on the unpatched tree (sandbox
  environment: real kernel, lxml, shipped paths). `python3 -m discovery selfcheck`: 14/14 suites.

ARM64 (`tools/build_arm64.sh`): static aarch64 `fdf-client`, `fdf-participants`, `ram`, `fdf-events`, `rina-host`
(glibc malloc). Cross-built lz4 and OpenSSL 3.0.13 libcrypto. Verified under qemu-aarch64: joined an x86 host,
listed in fdf-participants, registered a mapping.

## Real WireGuard, real forwarding: `tools/netns-hub-test.sh` (also in /opt/fdf/hub/)

Three network namespaces on one box (hub, n1 10.1.1.0/24, n2 10.2.2.0/24) on a veth "Internet"; the RAM manager and
fdf-hub in `hub`; each member runs the real fdf-client (facts + signed mapping) and a WireGuard tunnel built from
`region.py`'s `my_channels()` -- the daemon's own wg_config. WireGuard is wireguard-go where the kernel has none
(the sandbox); `wg` drives it identically. 18/18, measured:

- facts -> peer on the hub: ~1.2 s after the facts file (fdf-client connect + announce + hub reconcile)
- mapping -> /24 in allowed-ips + route via 10.253.A.B: on the next event
- n1 -> n2 ping THROUGH the hub (two tunnels, ip_forward): 3/3, ~1.2 ms
- clean leave (session ended): prefixes gone on the hub in 131-139 ms; route gone; n1 -> n2 fails (control)
- rejoin: prefixes back 123-126 ms after the new session; ping works AT ONCE
- SILENT loss (link down, no FIN): the RAM manager's keepalive declared the session dead and the hub parked the
  peer 19,438 ms after the link died -- the 20 s ruling, measured. Link + client back: 1,117 ms to prefixes restored.

Then, same namespaces (24/24 in all):
- Stage 4, a LEAF: a namespace with wg and one record {role leaf, addr 10.252.0.7/32, pubkey}, no /24, no mapping.
  The hub installed its /32 (allowed-ips + an on-link route) 11-12 ms after the facts; leaf -> n1 and n1 -> leaf
  ping through the hub. (A leaf's /32 is routed on-link on the hub; found and fixed here: `routes {addr: ""}`.)
- Stage 5, the hub side, a SECOND hub (hubB, its own namespace and interface): members tunnel to both, their
  locator sets name both (`hubA:1:50,hubB:2:50`); each hub installs the same prefixes independently from the same
  mappings; with hub A's agent killed and the members' routes pointed at hub B, n1 -> n2 flows through hub B.
  The member-side choice between hubs is routes.py's (measured RTT, incumbency hold), not exercised here.
  (wireguard-go names its control socket by interface and it is not per-namespace: two hubs on one box need two
  interface names. Real hubs are different machines.)

`[HUB_PARK_V1]` came out of this test: deleting a leaving member's peer and re-adding it on rejoin gave a NEW peer
entry with no session, so n1 -> n2 blackholed until WireGuard rekeyed (up to 120 s). The hub now PARKS a leaver
(allowed-ips cleared: cannot be routed to, cannot send in; session and endpoint kept) and deletes a parked peer only
when WireGuard's own last-handshake age exceeds 180 s (reading kernel state, not a timer of ours). A member that
comes straight back (false leave, rejoin) gets its prefixes on the same entry, no re-handshake. Oracle 21/21.

`[MEMBER_IDENTITY_V1]` also came out of it: every FrogNet node's hostname is "FrogNetHost", and fdf-client keyed
its record on the hostname, so three nodes were ONE member. The member's name is FDF_NAME, else facts.name, else
the hostname; the record key is facts.guid when present. The announcement's `name` is what the tools show.

## Not done / not possible here

- fdf-hub's `ip link add type wireguard` path (kernel WireGuard) has not executed; the userspace fallback has. On
  the droplet the kernel path runs first; if it fails, `FDF_WG_USERSPACE=1` + `apt-get install wireguard-go`.
- `run_region_loop` on region loss tears down ALL kernel wg ifaces (`_tear_down_all_kernel_wg_ifaces`), not only the
  region's. Fine while a region-mode node has only region tunnels; narrow it when a node has both.
- discovery.live and propogateNotificationInternal now read `RINA_RAM=` from /etc/frognet/tunnel.conf themselves:
  that line is the ONE switch (no FROGNET_DISCOVERY_SOURCE needed; it still overrides).
- The facts `hosts` field (non-member hosts behind a node) is written as [] by nobody yet; RegionGetHosts reads it.
- fdf-join installs fdf-client.service (as before) and now gives it FDF_FACTS, RIBBIT_TEMPLATES and
  EnvironmentFile=-/opt/fdf/var/site.env (RINA_AREA, RINA_SITE_KEY). The separate unit of the first cut is gone.
- Windows fdf-client reads facts.json once at start (no inotify, no maps): enough for a leaf. Built, not run on Windows.
- Stage 4 (leaf), Stage 5 (two hubs, exp4 readdress), the 2.6 loss measurement, and the 3.5 crawl-vs-region
  timing are hardware work.

## First hardware run: see DEPLOY-REGION-2026-10-09.md (John's machines, 2026-10-09)

## Generic order (the droplet variant)

1. Droplet: untar fdf-node.tgz (existing) + fdf-region-update.tgz; `apt-get install wireguard-tools`; set the site
   key (`map_site`); `systemctl enable --now fdf-client fdf-hub`; `ufw allow 50820/udp` (outside the broker's 51000-59999). Expect
   `[FDF-HUB] start (adopt): members=1 ... peers=0` (the hub sees itself only).
2. AI-Host (x86 node): fdf-node + region update + frognet-region-patch; join.env, site.env, tunnel.conf lines,
   `FROGNET_DISCOVERY_SOURCE=region`; restart the daemon. Expect on the droplet: `event p: members=2 peers=1`, then
   after the node's tunnel handshakes and it publishes maps: `event m: ... prefixes=1`, `wg show wg-frognet` with the
   node's pubkey and allowed-ips `10.253.A.B/32,10.A.B.0/24`.
3. FrogNetHost (Pi): the same plus fdf-arm64.tgz. Then `ping 10.A.B.1` across, and `cat /var/lib/frognet/view.json`
   on both.
4. Pull the Pi's uplink: on the droplet `event p: peers=1` within 20 s (keepalive) or at once (clean close); on the
   Pi `VIEW: phase=recalc reason=lost:region` then runMerge then `phase=steady`. Restore; rejoin. Record timestamps.

## Rules kept

No polling, no heartbeats (kernel keepalive only, idle sockets only). No data between members through FD&F. No
fallbacks (PROBE_FAIL is said). Nothing in /usr/local/bin. Node untar from /tmp. Security deferred except contracts
1–5, which the code now satisfies as far as the sandbox can show.
