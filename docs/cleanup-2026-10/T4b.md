# T4b — one mechanism each: fallbacks out, the WireGuard implementation recorded, the venv, the uplink by function, one release id (2026-10-12)

Not committed. Against the T4a commit (b31c9df): 157 paths (149 modified, 4 deleted, 4 new: `fdf-wg-impl` and its
opt/fdf copy, `opt/frognet_semantic/BUILD`, `core/build_id.py`; the three opt/fdf/dist files rebuilt), plus this file,
`static-no-fallback.sh`, `t4b-logs/` (NOTES.md is the working log) and, in `t4a-tools/`, `oracles-dir.sh` and
`syntax.sh` (sandbox harness; `netns-gates.sh` now takes `L=` for its log directory). `opt/fdf/src/fdf-source/rinab/build`
is now a real directory in the tree (it was a symlink out of it; both are in .git/info/exclude, so neither is a tracked
change). The gates ran in the
same Docker harness as T4a (Ubuntu 24.04 x86-64 containers, `t4a-tools/`), and on this host for the tree's own suites.

Tag for the whole tree: **`fdf-2026-10-12-r20`** (FD&F binaries, scripts and bundles, and the FrogNet base's new
`opt/frognet_semantic/BUILD`).

## Changes (one line each, why)

### 1. Fallbacks on the runtime/install path (each now one mechanism; a failure is logged, the next event retries)
- `usr/local/bin/frognet_alive.bash` — probes exactly the address asked (.2 is never answered by .1 nor .1 by .2); a
  failure says why on stderr. The 3 tries 0.2 s apart stay: the same probe of the same target, not a second path.
  `[ALIVE_ONE_TARGET_V1]`
- `discovery/real_backends.py` `_get_alive_return_ip` — the dev's own 10.x only; no 10.x → None, the probe is not sent and
  says so (`[PINGPONG] not probed`), the next merge asks again. The /etc/hosts identity and `0.0.0.0` behind it are gone;
  `test_pingpong_return_ip_oracle` now requires None for a dev with no 10.x. `[RETURN_IP_DEV_ONLY_V1]`
- `usr/local/bin/propogateNotificationInternal` — targets come from `discovery.neighbors` only (on the venv python); a
  failure logs its stderr, exits 1 and releases the wave's seen-file; the `/etc/hosts` 10.x.y.1 scan is gone.
  `[NEIGHBORS_ONLY_V1]`
- `frognet-tunnel-setup-v3.sh` is gone (T3b); a search for `POND_NAME`/`GROUP_NAME`/`gateways.conf NETWORK_NAME` pond
  logic finds only `frognet_prune_removed.sh`'s strip list. Nothing to do.
- `frognet_monitor_py/discovery.py` `discover_hosts` — getHosts.php only; a failed read is an empty table until the next
  cycle. The same fallback in `runPingEverything`, the C++ monitor (`frognet_monitor_cpp/src/http.cpp`) and the C#
  monitor (`Client.cs`) is gone too (C++/C# are source here: no curl headers / no dotnet to build them).
  `[MONITOR_HOSTS_ONE_SOURCE_V1]`
- `discovery/resolv.py` (+ `live.py`, the layout oracle, `DOCTRINE.txt` with a dated note) — resolv.conf is
  `127.0.0.1` and the upstream (the default route's next hop) only; no `8.8.8.8`. This reverses the last line of John's
  2026-09-26 layout, as the brief asks. `[RESOLV_LOCAL_UPSTREAM_V1]`
- `setup_lillypad_v4.bash` — NetworkManager's profile is the one owner of the LAN addresses: no flush, no `ip addr add`,
  no "try multiple activation methods", no disconnect/reconnect, no "forcing IP assignment"; `nmcli conn up` once, its
  failure stops the script with nmcli's message; the address check stops instead of forcing. Step 11 no longer writes
  `frognet_eth0_fixup.sh` / `frognet-eth0-fixup.service` (a boot-time second owner: flush, add, conn up). `mariadb` only
  (`start` stops on failure, the restart goes through `svc_restart`, which now logs a failure); `getEth0Address` is
  printed with its failure, never replaced by `$GATEWAY_IP`; `hostnamectl` without `|| hostname`.
  `[NM_OWNS_THE_LAN_ADDRESS_V1]` `[ONE_DB_UNIT_V1]`
- `etc/systemd/system/frognet-eth0-fixup.service`, `usr/local/bin/frognet_eth0_fixup.sh` deleted; `frognet_fixup.sh`
  no longer writes them; the installer's CORE_SERVICES drops the unit; an upgrade removes them (prune list).
- `frognet_reset.sh` `mariadb || mysql` — already one unit since T4a. Same chain removed from `frognet_fixup.sh` and
  `frognet_backup_world.bash`.
- installer B1 procfs beside sysctl — already gone since T4a (no `/proc/sys` write in the installer).
- installer unbound: `unbound` is no longer installed (A2 installed it only for D3 to stop it); D2's stop is gone; D3,
  once: if a unit is on the machine, `disable --now`, a failure warned. `fix_unbound_bypass.sh` is no longer called
  (it also restarted dnsmasq, which that phase must not do). `[UNBOUND_ONE_STEP_V1]`
- installer E6a(4): `conf.d/frognet-unmanaged-ap.conf` is no longer written; `frognet-netstart` decides per mode
  (wireless → `managed no`, wired → `managed yes`), which the permanent file contradicted in wired mode on every NM start.
  An upgrade removes the file; the world manifest no longer lists it. `[AP_MANAGED_BY_NETSTART_V1]`
- installer C3 — the venv's python or the install stops (no `/usr/bin/python3` behind it). See 3.
- `install_databasehost.sh` — one apt list (`mariadb-server php php-mysql libapache2-mod-php apache2 jq curl`), its
  failure stops with apt's message; mysqli checked; `systemctl enable --now mariadb` only. `[ONE_PACKAGE_LIST_V1]`
- `fdf-facts` `sh()` — a failed command is said on stderr (command, rc, its stderr) and returns "". `[SH_FAILURES_SAID_V1]`
- `fdf-facts` uplink — no hard-coded address: the target is `FDF_UPLINK_PING` when configured (the netns tests set it),
  else the default route's own next hop; a default route with no next hop is judged by the interface's carrier. The
  start line names the target. The lease watch was already inotify (`[FACTS_WAKE_ON_EVENT_V1]`); the stale "1 s poll"
  docstring is corrected. `[UPLINK_JUDGED_LOCALLY_V1]`
- installer F3: the `@reboot sleep 240 && runMerge.bash` line is gone (and the F3 grep drops it from an upgraded
  crontab). The boot merge is the NetworkManager hook's: `90-frognet-merge` keeps its route signature in
  `/run/frognet/nm_dispatch_sig` (gone at every boot), so the first interface event after a boot always merges (the
  "/run/frognet is the one-line fix" John had noted in MERGECHURN-20260912). `[BOOT_MERGE_IS_THE_HOOK_V1]`
- `FROGNET_MERGE_LEGACY_TAIL` — not in the tree. Nothing to do.
- Found on the way and fixed, same rule:
  - `runMerge.bash` wiped `/var/run/frognet/*` every merge, including the merge watcher's queue (`merge_requests`) and
    `propogateNotification.php`'s seen-set, so every neighbour notification after the first merge was lost and the
    watcher spun on a missing directory. It now keeps `merge_requests`, `seen_notifications` and `tunnel-daemon.pid`;
    the watcher makes both directories 1777 when they are not; `propogateNotificationInternal` makes the seen-set 1777.
    `[QUEUE_SURVIVES_THE_WIPE_V1]`
  - `propogateNotification.php` (www-data) answered **DUP** when it could not write the seen-set — on a fresh install
    that was every notification (the directory was root's or absent), so a LAN-only node never merged on a
    neighbour's wave. It now says DUP only when the wave's file exists; a write failure is a 500 and an error_log line;
    a failed poke too.
  - `proxy/netutil.py` read the interface roles from mapInterfaces' TEXT with a regex (the first `export eth0Name=`
    literal: the legacy constants, never the override, and after T4b a shell expression). It now sources the file the
    way every shell consumer does, still refusing when a role is empty. `[ROLES_ARE_COMPUTED_V1]`
  - `discovery/live.py` `_reconcile_tunnels` defaulted to `/usr/bin/python3` → the venv (the netns LAN-only harness
    now names its own python, `FROGNET_BRINGUP_PY`).
  - `test_clean_install_oracle` was red at the T4a commit too (T4a's oracle run predates its reset change): it now
    counts what F7's `frognet-system-tune.sh` writes (limits.d/99-frognet.conf) as written by the install, and checks the
    venv pip (`"$VP" show`) for skip-if-present.

### 2. WireGuard implementation: chosen once, by platform, recorded
- `fdf-wg-impl` (new, `opt/fdf/bin` and the source copy): `kernel` when `/sys/module/wireguard` exists or `modprobe -n
  wireguard` finds the module, else `wireguard-go`; written once to `/etc/frognet/wg.impl` (an existing record is kept),
  exit 1 when the choice is wireguard-go and it is not installed. `[WG_IMPL_RECORDED_V1]`
- Writers: the installer's new A2b (the same rule; installs `wireguard-go` only where it is the choice — it left A2's
  list), `fdf-region-create`, the node `.run` (header.sh, after the unpack), and `fdf-hub.service`'s ExecStartPre
  (`--record`, so a region holder made before T4b — StreamingFrog — records it once at its first start after the update).
- Readers, each using only the record: `fdf-hub` (`wg_impl()`: kernel → `ip link add type wireguard`, wireguard-go →
  `wireguard-go`; a failure raises and systemd restarts the hub; three cmp-identical copies), the tunnel daemon
  (`config.wg_impl()`/`wg_quick_env()`: wg-quick runs with `WG_QUICK_USERSPACE_IMPLEMENTATION` set to a command that
  does not exist when the record says kernel — so a kernel failure is the failure — and to `wireguard-go` when it says
  so; no record → the bring-up fails, logged, the next poll retries), and the simulator's `netns_backend`
  (was `[WG_USERSPACE_FALLBACK_V1]`).
- The netns harness sets its choice explicitly: `FDF_WG_USERSPACE=1` → `FDF_WG_IMPL=wireguard-go` in the 15 scripts that
  had it (the readers honour `FDF_WG_IMPL` before the record).
- Platform, not fallback: wg-quick itself still tries `ip link add` first on a wireguard-go machine (that cannot succeed
  where the record says no module; its "[!] Missing WireGuard kernel module" line is wg-quick's).

### 3. Services on the venv the installer builds
- The five python units (`frognet-proxy`, `-daemon`, `-tunnel-daemon-v3`, `-dbhost-elect`, `-hosts-safeboot`) ExecStart
  `/opt/frognet_semantic/venv/bin/python3`.
- A3 installs nothing into the system python (it only names the list; the T4a eight console scripts in
  `/usr/local/bin` no longer appear: G1's `/usr/local/bin` holds only tree files). `lxml` joined the list:
  `core/html_handler.py`/`xml_handler.py` (the proxy's and daemon's format registry) import it, and the system python had
  it only because an apt package pulled `python3-lxml` in. C2 now checks every listed package on every install, not only
  on a rebuild. C3 runs on the venv or stops. `[SERVICES_ON_THE_VENV_V1]`
- Also on the venv: `runMerge.bash` (no `/usr/bin/python3` behind it: logged, exit), `frognet_build_release.sh`'s gate,
  `mergeHostsAndResolv.bash`, `sync_interfaces.sh`, `frognet_monitor`, `frognet_register_candidate.sh`, and the shebangs of
  the elector, safeboot, monitor, `api_semantic.py`, `check_sem_health.py`, `frognet_routing_topology.py` (85 shebangs
  already named the venv). Inline `python3 -` snippets that use only the standard library (json in the reporters) stay
  on the distribution's python3, the venv's own base.
- Platform, not fallback: FD&F's python tools (`fdf-facts`, `fdf-hub`, `fdf-join`'s units, `fdf-term-serve`) stay on
  `/usr/bin/python3`: they are standard-library only and run on the region holder, which has no FrogNet venv.

### 4. Uplink by function, not by name
- The real cause of T4a's G2 was not wlan0Name: `discovery/live.py` took this node's identity from the FIRST dev, in
  name order, holding any 10.x/24 — with the lease into h1's LAN on eth1 and its own LAN on eth2, eth1 sorted first and
  h2 took h1's .1 as its identity. Now `mapinterfaces.served_identity()`: the 10.A.B.1 the node serves, on whichever
  interface carries it (the link into another node's LAN holds a non-.1 lease and is upstream). One rule.
  `[UPLINK_BY_FUNCTION_V1]`
- The "eth0 is always downstream" name test is gone from both classifiers (`mapinterfaces.classify`, bash
  `mapInterfaces`); `live.py`'s `self_identity or <eth0's address>` stand-in is gone.
- `mapInterfaces` computes the roles by function: eth0Name = the wired dev owning a served /24, else (fresh install) the
  wired dev with no address, else the only wired dev; overlays never; wlan0Name = the hostapd radio (a radio is what
  `/sys/class/net/X/wireless` says), then the next radio; a pin is honoured only when it names a device that exists.
  `oracles/oracle_mapinterfaces_names.sh`: **FAIL (8) at T4a → PASS** (it pinned exactly this and was red on the tree).
- G2 re-run WITHOUT the rename: below.

### 5. Builds
- `FDF_BUILD_TAG` and every BUILD_TAG make_deliverables checks → `fdf-2026-10-12-r20` (fdf_version.hpp, fdf-hub ×3,
  fdf-facts ×2, fdf_graph.py ×2, region.py, fdf-reach ×2); tunnel daemon `frognet-tunnel-2026-10-12-t4b`; installer
  `frognet-install-2026-10-12-t4b`.
- `opt/frognet_semantic/BUILD` (new, `build=fdf-2026-10-12-r20`) + `core/build_id.py`. The installer's C1 adds
  `commit=<sha>[+changes]` when it installs from a git checkout (`git -c safe.directory=...`) and `installed=`, logs it,
  and F6 prints it. The proxy (`[Proxy] build fdf-2026-10-12-r20 listening ...` — its fixed `BUILD=2026-03-24-v1` is
  gone, with the same fixed stamp in codec/templates/session/execution), the data daemon (`[Daemon] build ...`) and the
  tunnel daemon (`... base fdf-2026-10-12-r20`) say it at start. `[BASE_BUILD_V1]`
- `rinab/build` was a symlink to `/home/claude/merge/src/rinab/build` (outside the tree; `.git/info/exclude`). It is now
  the tree's own directory, seeded with the cross-built lz4/openssl (`arm-deps`) and the Communicator server binaries
  (`comms-*`, no tag in them) from there. Built here: `tools/build.sh` 103 s, `build_static_host.sh` 1 s,
  `build_static_clients.sh` 43 s, `build_arm64.sh` 144 s — about 5 minutes; every tagged binary carries r20
  (`strings | grep`). `opt/fdf/bin`'s seven binaries replaced by the new static ones.
- `make_deliverables.sh` (15 s): `fdf-install-x86_64.run` 31,073,732 B sha256 d8a01d2f96ef3e75,
  `fdf-install-aarch64.run` 28,159,331 B 98aa09413ab2809f, `fdf-droplet.tgz` 23,234,621 B 6ca8e2eb9309deeb, built
  2026-10-12T18:43Z. The region patch also carries `core/build_id.py`, `discovery/mapinterfaces.py`, `discovery/resolv.py`
  (the patched modules import them). `opt/fdf/BUILD` (the tree's copy) rewritten to r20.

### 6. Licence headers
- The 39 files `test_license_oracle` named got the tree's header, rendered by the tree's own
  `frognet_apply_license.py` (`render`/`insert_at`; checked byte-identical to the existing py, hpp, html and sh headers),
  in HTML after `<!doctype html>`. The 12 Communicator C++ headers included. `test_license_oracle`: **PASS**.

### 7. Deployment specifics
- `opt/fdf/fdf.env` (both copies): no default area (was `rina-area.test`); `fdf-mapping.sh`, `fdf-ram-manager.sh`,
  `fdf-member.sh` stop with "set RINA_AREA in /opt/fdf/var/area.env" (their password message pointed at fdf.env, which
  holds no setting since T4a: fixed). `[NO_BUILT_IN_AREA_V1]`
- Windows `etc/ponds.txt` (both copies): an example (`AREA=RAM-HOST:51888`, with how to fill it); `fdf-bridge.cmd` (both)
  refuses while the example is there, or when the file is missing. `[PONDS_ARE_THE_OPERATORS_V1]`
- `opt/fdf/src/fdf-source/fdf/hub/hub.env` (StreamingFrog's, 165.232.153.144, never shipped: make_deliverables strips it)
  deleted.
- Left, listed: Android `Identity.kt:36` default pond `frognet|165.232.153.144:51888` (needs an app rebuild).
- Remaining `165.232.153.144`/`StreamingFrog`/`Seattle` in the tree: simulator fixtures, a build-script comment, history
  comments, docs. None is a default in shipped runtime.

### 8. One tune script
- `usr/local/bin/frognet_system_tune.sh` (cmp-identical) deleted; F7 runs `frognet-system-tune.sh` (the unit's name) or
  stops when it is missing; the script's own name references fixed. An upgrade removes the old file. `[ONE_TUNE_SCRIPT_V1]`

### Upgrade list
- `frognet_prune_removed.sh`: a T4b block at the end of its T3 section (the installer's C1c runs `--only T3`):
  `frognet-eth0-fixup` (unit off, unit, wants link, script), `conf.d/frognet-unmanaged-ap.conf`,
  `frognet_system_tune.sh`, `/etc/sentinels/nm_dispatch_sig`. Seen working in G4.

## Gates (logs in t4b-logs/)

Which tree each container gate ran (T4a's harness, `t4a-tools/`; the networks are new here: `fn-wan` 172.30.0.0/24,
LANs `--internal` with the docker gateway at .254 so a node can hold .1):
G1 run 1 (17:12) and its re-run (18:2x) ran before the `propogateNotification.php`/seen-set fix; G3, G4 and n1/n2 of G5
(17:47-18:17) ran with everything but that fix; **G2 (final, 18:44) and m2 (G5, 18:44) ran the final tree**, dist rebuilt
at 18:43. The fix touches only the LAN-only notification path, which G2 is the gate for.

**G1 clean LAN-only install** (`node.sh g1 FrogNetHost.G1 172.30.0.21 fn-lan1:10.61.61.1:eth1; docker exec g1 bash
/root/inst.sh G1 10.61.61.1`; `reboot.sh g1`; `node-state.sh`) — g1-install.txt, g1-after-reboot.txt, final-g1-*.
- EXIT 0; `frognet_install.sh build frognet-install-2026-10-12-t4b`; A2b `WireGuard: wireguard-go (no wireguard module
  for 6.18.44-fc-v114), recorded in /etc/frognet/wg.impl`; A3 `21 packages, installed into the venv in C2 (nothing into
  the system python)`; C1 `FrogNet base: build=fdf-2026-10-12-r20 (no git checkout: no commit recorded)` (put-tree copies
  no .git); C3 `[PASS] discovery selfcheck`; F6 all `+`, `echo: G1,10.61.61.1,172.30.0.21,0.0.0.0`, `FrogNet base:
  build=fdf-2026-10-12-r20 installed=...`, "All checks passed."
- `/usr/local/bin` holds only files the tree has (T4a: + 8 pip console scripts). crontab: the heartbeat and
  `@reboot frognet-netstart`; no `sleep 240` line.
- After the reboot: every unit enabled/active, **no failed units**, frognet-eth0-fixup `not-found`; the boot merge ran
  from the hook (`completed`, `discovered`) with no cron; `SELF_IDENTITY 10.61.61.1`; the journal says `[Proxy] build
  fdf-2026-10-12-r20 listening ...`, `[Daemon] build fdf-2026-10-12-r20 ...`, `Starting frognet-tunnel-daemon v3.0.0
  build frognet-tunnel-2026-10-12-t4b (mode lan-only) base fdf-2026-10-12-r20`; resolv.conf `127.0.0.1` + `172.30.0.1`
  only; getHosts.php and both echoes answer; no wg interface; the venv imports lxml/lz4/mysql.connector; the proxy's
  ExecStart is the venv's python. (Run 1 showed the proxy's `eligible=['${_mi_r[0]:-wlan0}', ...]`: the netutil fix
  above came from it.)

**G2 two LAN-only nodes, NO rename** — h1 serves 10.71.71.0/24 on eth1; h2 is on h1's LAN as 10.71.71.20 on **eth1**
and serves 10.72.72.0/24 on eth2 (`--lan-iface eth2`).
- Run 1 (tree before the queue fixes; g2-h*-state.txt): h2 right at once — `SELF_IDENTITY 10.72.72.1`, getHosts H2 and
  H1, echo `H2,10.72.72.1,172.30.0.42,10.71.71.20` (T4a, eth1-named: h2 named itself at h1's address). h1 never merged
  on h2's wave: `merge_requests` gone (runMerge's wipe) → fixed, h1 then converged (g2-h*-state-fixed.txt); a second
  run on that tree showed the PHP answering DUP for every wave (seen-set not writable by www-data) → fixed.
- **Final (final tree; final2-h*-install.txt, final2-h*-state.txt, final2-g2-cross.txt)**: both EXIT 0, F6 all passed.
  After both rebooted: h1 getHosts `[{"ip":"10.72.72.1","name":"H2","echo":"H2,10.72.72.1,172.30.0.42,10.71.71.20"},
  {"ip":"10.71.71.1","name":"H1",...}]`, route `10.72.72.0/24 via 10.71.71.20 dev eth1 metric 22`; h2 getHosts H2 and H1,
  `SELF_IDENTITY 10.72.72.1`, /etc/hosts `10.72.72.1 FrogNetHost.H2`, `10.71.71.1 FrogNetHost.H1`; no failed units on
  either; h1's `/run/frognet/{merge_requests,seen_notifications}` 1777, watcher `fire decision=fork`.
  `databasehost.frognet` → 10.72.72.1 on both. **h1 → 10.72.72.1 3/3, h2 → 10.71.71.1 3/3, h1 reads h2's echo across.**
  The cross-LAN packets need the sandbox allowance (`iptables -I DOCKER-USER -m physdev --physdev-is-bridged -j
  ACCEPT`, removed after): this host's dockerd drops frames between two `--internal` networks (0/2 without it, run 1).

**G3 region** — d2 "regionbox" 172.30.0.10 (python3, wireguard-tools, wireguard-go; no FrogNet).
- `cd / && tar xvzf - < /tmp/fdf-droplet.tgz`; `systemctl start fdf-hub` before create → refused: `fdf-hub:
  /opt/fdf/hub/hub.env does not exist ... Run: /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP`; no /etc/frognet yet.
- `fdf-region-create test 172.30.0.10` rc=0 (g3-region-create.txt): `fdf-wg-impl: wireguard-go (no wireguard module ...),
  recorded in /etc/frognet/wg.impl`, `RAM manager: {"ok":true,"server":"fdf-2026-10-12-r20"}`, `map_site test`, hub
  `wg: wireguard-go interface wg-test (/etc/frognet/wg.impl)`, `start (adopt) ... build=fdf-2026-10-12-r20`.
- n1/n2 `inst.sh N1 10.81.81.1 --region 172.30.0.10:51888 --site-key <key> --area test` (and N2 10.82.82.1): EXIT 0,
  A2b recorded wireguard-go, the .run `fdf-wg-impl: wireguard-go (recorded ...; kept)`, `mapping 10.81.81.0/24 ->
  regionbox:1:50 registered`, `my-channels: hubs=['regionbox']`, wg0 handshake. After both rebooted
  (g3-n*-after-reboot.txt): `WG_KEY: made ...`, `Starting frognet-tunnel-daemon ... build frognet-tunnel-2026-10-12-t4b
  (mode region) base fdf-2026-10-12-r20`, key 0600, hub link up; **N1 → N2 3/3 (1.6 ms), N2 → N1 3/3 (1.7 ms)**.
  fdf-facts: `uplink target=the default route's next hop`, row `uplink eth 10000` (alive). On d2: fdf-ls N1 N2 regionbox
  alive; `wg show wg-test` both peers with their /24s (g3-d2-monitor.txt).
- Outage (g3-outage.txt): d2 off fn-wan 49 s → 0/2 both ways; reconnected → **+20 s 2/2 both ways**, +40, +60 the same;
  routes back on wg-test. No manual step.
- Droplet update (untar the new tgz, daemon-reload, restart the three): hub.env, wg.impl and the site key identical
  before/after; the hub `fdf-wg-impl: wireguard-go (recorded ...; kept)`.

**G4 upgrade** — u1 installed from the T0 baseline (42465dd, `u1t0.sh`, with broker state), rebooted, then the final-ish
tree `--from-repo --preserve` (`u1up.sh`), rebooted (g4-before-upgrade.txt, g4-u1-upgrade.txt, g4-after-upgrade.txt):
- fnid 8ad372ffdda09d8eb87471c2af424374 → same; U1 / 10.63.63.1 recovered; node_private.key md5 936ebf74... → same.
- C1c's T4b block: `rm /etc/systemd/system/frognet-eth0-fixup.service`, `rm /usr/local/bin/frognet_eth0_fixup.sh`,
  `rm /etc/NetworkManager/conf.d/frognet-unmanaged-ap.conf`, `rm /usr/local/bin/frognet_system_tune.sh`; crontab lost
  `@reboot sleep 240 && runMerge.bash`; broker state gone (as T4a); wg.impl recorded; base BUILD written.
- Before: 4 failed units (apache2, dbhost-advertise, mediahost-advertise, tunnel-setup). After: none; `(mode lan-only)
  base fdf-2026-10-12-r20`; merge `discovered`; getHosts and both echoes answer; F6 all passed, EXIT 0.

**G5 leave and uninstall**
- n1 (region member + an operator crontab line): `/opt/fdf/bin/fdf-leave` rc=0 (g5-fdf-leave.txt) → LAN-only, no fdf
  unit, no wg interface; `frognet_install.sh --uninstall` EXIT 0 (g5-n1-uninstall.txt); leftovers (g5-n1-leftovers.txt):
  no unit, crontab = the operator's line only, no froguser, no FrogNet directory, **`/usr/local/bin`: 0 entries** (T4a:
  the 8 pip scripts), dnsmasq/apache2/mariadb disabled/inactive as before, resolv.conf `nameserver 127.0.0.11 ...` as
  found. (Names do not resolve after it: docker's resolver moved ports on the container restart, as T4a explained.)
- n2: `--uninstall` straight from region membership: phase 0 ran fdf-leave (`left the region`), EXIT 0, no /etc/frognet.
- **m2 (final tree)**: LAN-only install EXIT 0, F6 all passed; `--uninstall` at once, no restart (g5-m2-uninstall.txt):
  `removed 185 recorded FrogNet file(s)`, packet-filter rules `the machine's again`, EXIT 0; leftovers
  (g5-m2-leftovers.txt): nothing FrogNet's, `/usr/local/bin: 0 entries`, resolv.conf as before, **`getent hosts
  archive.ubuntu.com` → 91.189.92.24** (before the install: 91.189.91.82, g5-m2-pre.txt).

**Host gates** (FROGNET_DIR and FROGNET_BIN_ROOT = this tree; final tree)
| gate | T4a | T4b |
|---|---|---|
| `python3 -m discovery selfcheck` | 14/14 | **14/14** (selfcheck.txt) |
| `discovery/run_discovery_oracles.py` | 83 + test_license red (39 files) | **84/84 PASS, 0 regression** (oracles.txt) |
| `simulation/run_all.py` | 3 red: DDP, aiconnect, discovery | **2 red: collectives/DDP, aiconnect** (runall.txt) |
| netns-region-loop-test.sh | PASS 13 + 1 SKIP | **RESULT PASS, 13 PASS + 1 SKIP** (region.txt) |
| region-loop NOKEY=a | PASS | **RESULT PASS, 17 PASS + 1 SKIP** (nokey.txt) |
| netns-lan-only-test.sh | PASS 19/19 | **RESULT PASS, 19/19** (lanonly.txt) |
| netns-ponds-daemon-test.sh | PASS 21 | **RESULT PASS, 21** (ponds.txt) |
| netns-reach-test.sh (BRIDGE_KT, FDF_TUN) | PASS 70 | **RESULT PASS, 70** (reach.txt) |
| static-no-broker.sh | 0 violations, 207 allowed | **0 violations, 207 allowed** (static-no-broker.txt) |
| **static-no-fallback.sh** (new) | 105 violations (static-no-fallback-on-T4a.txt) | **0 violations, 35 allowed** (static-no-fallback.txt) |
| oracles/ directory (oracles-dir.sh) | mapinterfaces_names FAIL (8); 4 others red | **mapinterfaces_names PASS**; the same 4 red (oracles-dir-*.txt) |
| syntax (bash -n / ast / php -l) and opt/fdf copies cmp | | **112 files OK; 10 copies cmp-identical** (syntax.txt) |
The netns harness ran with T4a's allowance for bridged frames (netns-gates.sh adds `DOCKER-USER -m physdev
--physdev-is-bridged -j ACCEPT` while it runs and removes it). The suite ran twice: once mid-way (netns-run1/: lan-only 4 FAIL, because the merge's bring-up stage now runs on
the venv and the harness had none — the harness now names its python, `FROGNET_BRINGUP_PY`; then 19/19), and again on
the final tree (the table). region-loop and reach exit 1 after printing RESULT PASS (their last line's test), as at T4a.
`static-no-fallback.sh` greps 22 patterns, one per removed mechanism (alive-alt, returnip-identity, propagate-hosts,
hosts-fallback, public-dns, nm-activation, db-unit-chain, gw-echo, eth0-fixup, belt, unbound-chain, py-fallback,
system-pip, apt-chain, facts-swallow, boot-cron, legacy-tail, wg-fallback, unmanaged-ap, tune-twin, eth0-by-name,
built-in-pond) over etc/ usr/ var/ opt/frognet_semantic opt/fdf; every allowed match carries its reason (history
comments, the prune list, a kernel route lookup, simulator fixtures, the Android default).

## Found, not done

- An upgrade over a pre-T4a install leaves 45 files in `/usr/local/bin` that the tree does not have (G4): 12 are in
  `frognet_prune_removed.sh`'s pre-T3 sections, which the installer's `--only T3` does not run; 9 are console scripts the
  old system pip installed (cbor2, dotenv, gpsdclient, gunicorn, gunicornc, httpx, idna, normalizer, cffi-gen-src); 24
  are T0-era scripts no list names (frognet-tunnel-*, ConnectFamily, ...). Running the whole prune list, and removing
  those pip packages, are John's calls.
- One setting, several sources (left; each is a chain): the tunnel daemon's region settings (tunnel.conf, the
  environment, join.env/site.env — the REGION switch line names four); `getEth0Address` (the LAN dev's 10.x, else
  opts_only.conf's dhcp-range: F6 relies on the second before the reboot, when --norestart has not addressed the LAN);
  `live._dns_upstream` (exit_host.tsv's next hop, else the default route's gateway); `iproute.tear_down_wg` (`wg-quick
  down`, else `ip link del`); the merge hook (a request to the watcher, else `runMerge.bash &` when the watcher is not
  running — fdf-region-join stops it).
- Without 8.8.8.8, external names resolve only through the default gateway (`[DNS_NEXTHOP_ONLY_V1]`). In the containers
  the gateway (docker's 172.30.0.1) serves no DNS, so an installed node resolved no outside name; a home or office router
  does. A network whose gateway is not a resolver has no outside names until the operator gives one.
- "dead" uplink now means the next hop does not answer, not that the Internet does not: an exit whose router answers
  while its ISP is down stays an exit. `FDF_UPLINK_PING=<the operator's own target>` (e.g. the ISP's gateway) judges
  further.
- Pollers left: `frognet-dbhost-elect.timer` and `frognet-dbhost-advertise.timer` (every minute) and
  `registerFrogNetHost.bash` (every 15 min: a heartbeat, `LastHeartbeat`). They are not merge triggers; moving the
  store election onto events is a redesign beyond this brief.
- `discovery/propagate.py` (the simulator's model, test_propagate_oracle B) still models the /etc/hosts .1 scan the real
  script no longer has.
- `var/www/html/{datalogger.py,Sense_Logger_2.py,frognet_gateway.py}` start with `#!/usr/local/bin/frognet_env/bin/python3`,
  which exists nowhere (Pi sensor scripts; sense_hat/picamera are Raspberry Pi OS packages).
- `var/www/html/mapInterfaces` still exports the three constant names (nothing in the tree sources it that I found).
- oracles/: default_route_resolver (9), dhcp_scope (no `etc/dnsmasq_conf_template` in the tree), identity_fails_loud
  (10), opts_only_interface_lines (4) are red at T4a and now: they came with code that is not in this tree.
- run_all: collectives/DDP and aiconnect red, as at T3b/T4a.
- Android `Identity.kt:36` default pond `frognet|165.232.153.144:51888` (an app rebuild).
- h1's `/etc/database_ip` keeps its install-time value (10.71.71.1) while `databasehost.frognet` says 10.72.72.1 (T4a).
- The C++ and C# monitors were edited but not built here (no libcurl headers, no dotnet).
- T7 (John): the same gates on a Pi (aarch64 .run; `modprobe -n wireguard` should record `kernel` on Raspberry Pi OS,
  and wireguard-go is then not installed), and the StreamingFrog droplet update: its fdf-hub records wg.impl once at its
  first start (ExecStartPre) — check its log says `kernel` or `wireguard-go` as expected.
