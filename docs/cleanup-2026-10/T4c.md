# T4c — the last chains (one source each) and the upgrade clean-up (2026-10-12)

Not committed. Against the T4b commit (0db230f): 30 source paths modified, the three `opt/fdf/dist` files rebuilt, plus
this file, `static-no-fallback.sh`, `t4a-tools/syntax.sh` and `t4c-logs/` (`preview/` = a first container run made
before the watcher fix below; superseded, kept for the record). `live._dns_upstream` not touched (waits on John).
Builds: installer `frognet-install-2026-10-12-t4c`, tunnel daemon `frognet-tunnel-2026-10-12-t4c`; FD&F binaries
unchanged (`fdf-2026-10-12-r20`).

## Changes (one line each, why)

### 1a. Region settings: one file each `[REGION_SWITCH_ONE_FILE_V1]`
- `internet_tunnels_v3/config.py` — RINA_RAM from `/opt/fdf/var/join.env` only (fdf-region-join writes it, fdf-leave removes it), RINA_AREA from `site.env` only; tunnel.conf and the environment are not read; absent → logged (`LAN-only mode: no region switch (<why>)`, or `REGION: no area (...)`). tunnel.conf is read only to name what is left in it, once.
- The same switch had the same chain in eight more readers; each now reads join.env only: `runMerge.bash`, `propogateNotificationInternal`, `frognet_dbhost_elector.py`, `core/role_publish.py`, `frognet_echo.php`, `getHosts.php`, `discovery/real_backends.py` (`ram_from_conf`, and its `RINA_RAM` environment fallback gone), `frognet_routing_topology.py` (its oracle gains "RINA_RAM only in an old tunnel.conf → not configured").
- `frognet_prune_removed.sh` T3 section unsets `RINA_RAM`/`RINA_AREA` in tunnel.conf too (a node whose region lived only there is LAN-only after the upgrade until fdf-region-join runs: gone is gone). `conf.sh`, the installer's C1c comment, `region.py`, `AI_README.md` say so.

### 1b. getEth0Address: the LAN dev's 10.x only `[LAN_DEV_ONLY_V1]`
- `getEth0Address` — the dnsmasq dhcp-range fallback is gone; no 10.x on eth0Name (or no eth0Name) → empty stdout, the reason on stderr, exit 1.
- `getFrogNet.bash` — an echo line with no LAN address is not cached (it was: the empty field would have stuck in /etc/sentinels across the reboot).
- installer F6 — reads what this install set (`NODE_IP`): prints `LAN address: <ip> (on <dev> now | not addressed yet ...)` and expects the echo's .1 field to be that IP, or empty before the reboot addresses the LAN. No second runtime source.

### 1c. WireGuard teardown by its owner `[WG_TEARDOWN_BY_ITS_OWNER_V1]`
- The daemon makes every wgN with `wg-quick up` from /etc/wireguard/wgN.conf (poll.py bring-up), so `wg-quick down` is the one teardown: `frognet_route/iproute.py` and `internet_tunnels_v3/iproute.py` `tear_down_wg` (no `ip link del` after it; the failure logged with wg-quick's stderr), `wg.teardown_wg_iface` (no `ip link delete` branch when the conf is missing; returns success; on failure the conf is kept so the next orphan check / startup reconcile runs the same teardown), `poll.py` callers (orphan teardown, startup reconcile, TEAR_DOWN) no longer delete the conf or count a failed teardown as done.

### 1d. The merge hook: one owner, the watcher `[HOOK_ASKS_THE_WATCHER_V1]`
- Picked "the watcher always runs in both modes": it is event-driven (inotify, idle costs nothing), it already holds the 30 s window and the lock decision, and `frognet-discovered.service` `Wants=` it, so region mode's "off" was never reliable anyway; the other way would have put a second fork-and-lock path in the hook.
- `90-frognet-merge` — writes a request to the watcher's queue only (makes the 1777 dir if the hook runs before the watcher at boot; the watcher fires pending requests when it starts); no `runMerge.bash &`; a request it cannot write is logged and the route signature is not advanced, so the next event asks again.
- `fdf-region-join` / `fdf-leave` (both copies, cmp-identical) — no longer stop/start the watcher; region-join enables it where an older join had turned it off.
- `runMerge.bash` — keeps `/run/frognet/nm_dispatch_sig` in its wipe (it is the hook's state; wiping it made the first interface event after every merge request another one).
- Found on the way, same rule `[WATCHER_HAS_INOTIFY_V1]`: `inotify-tools` was never installed, so the watcher's `inotifywait ... 2>/dev/null || { sleep 1; continue; }` was a silent 1 s poll firing every request at once (no window; seen in the first G1). Installer A2 installs `inotify-tools`; the watcher exits 1 with a message when inotifywait is missing (systemd restarts it) and logs a wait that ends other than by the quiet timeout; the node `.run` (header.sh) requires inotifywait (a node installed before T4c: `apt-get install inotify-tools`).
- `static-no-fallback.sh` +7 patterns: region-sources, eth0-dhcp-range, wg-teardown-alt, hook-fork, watcher-region-off, watcher-poll (each matches the T4b tree, 0 now); the FD&F tools' RINA_RAM reads are allowed with a reason (below).

### 2. Upgrade clean-up `[PRUNE_ALL_ON_UPGRADE_V1]`
- Installer C1c and `frognet_build_release.sh` run `frognet_prune_removed.sh --apply` — every section; `--only` is gone. Every path the script names was checked absent from the tree (a fresh install: `deleted 0 path(s)`).
- New `t1_section`: from `git diff --name-status 42465dd 0db230f` (D) under usr/local/bin, usr/local/sbin, etc/systemd/system, var/www/html, opt/frognet_semantic: 195 files, 128 already named by the T2/T3/T4b sections, the other 67 added (as 57 entries: three wholly deleted directories as one each); `aiconnect.service` and `frognet-post-merge-metrics.service` stopped first. Left out on purpose: `fdf-hub.service`, `fdf-ram-frognet.service`, `fdf-client-frognet.service` — fdf-region-create makes `fdf-ram-<AREA>`/`fdf-client-<AREA>` and the droplet tarball installs fdf-hub.service, so on a region holder of area "frognet" those names are the live region.
- New `pip_console_section`: the nine wrappers go only when the file is pip's wrapper (python shebang + `from <module> import`), dpkg owns neither it nor `/usr/lib/python3/dist-packages/<module>`; nothing is pip-uninstalled. On the T0 node none of the nine modules is an OS package (g4-before-upgrade.txt), so all nine went.
- The script's T2 block and its last lines now respect `FROGNET_PRUNE_ROOT` (they ran systemctl and swept the real /opt/frognet_semantic and /usr/local/bin bytecode even under a scratch root).

## Gates (logs in t4c-logs/; all on the final tree unless said)

Container harness = T4a's (`t4a-tools/`, Ubuntu 24.04, `fn-wan` 172.30.0.0/24, LANs `--internal`, gateway .254).

| gate | command | result |
|---|---|---|
| **G4 upgrade** | `node.sh u1 FrogNetHost.U1 172.30.0.31 fn-lanu:10.63.63.1:eth1`; T0 tree (`git archive 42465dd`) + `u1t0.sh`; `reboot.sh u1`; `u1up.sh` (`--from-repo --preserve`); `reboot.sh u1` | T0 EXIT 0; upgrade EXIT 0, F6 all passed; C1c `deleted 170 path(s); 173 already absent; 0 failed`; **/usr/local/bin before 213 entries, 59 not in the tree → after 154 entries, 0 not in the tree, 0 dpkg-owned, 0 tree files missing** (g4-usr-local-bin-*.txt); fnid 472e2a92..., U1/10.63.63.1, node key md5 67093888... same; tunnel.conf broker keys unset; no failed units after the reboot; the hook's request → `watcher:poke` merge 30 s later |
| **G1** LAN-only | `node.sh g1 FrogNetHost.G1 172.30.0.21 fn-lan1:10.61.61.1:eth1; docker exec g1 bash /root/inst.sh G1 10.61.61.1`; `reboot.sh g1` | EXIT 0, inotify-tools installed, C1c `deleted 0`, `LAN address: 10.61.61.1 (on eth1 now)`, `+ echo: G1,10.61.61.1,172.30.0.21,0.0.0.0`, All checks passed; after reboot all units active, no failed units, daemon `t4c (mode lan-only)`; boot: hook requests 23:15:19 and 23:15:41 → **one** merge at 23:16:11 (`invoked_by who=watcher:poke`); /usr/local/bin = tree files only |
| getEth0Address branches | on g1: address removed, then re-added (g1-geteth0address.txt) | removed: stdout empty, rc 1, `no 10.x address on the LAN dev eth1`; getFrogNet `G1,,172.30.0.21,0.0.0.0` and **no cache written**; back: `10.61.61.1`, cached |
| old RINA_RAM in tunnel.conf | on g1: append `RINA_RAM=203.0.113.9:8840`, restart the daemon, run the prune (g1-old-rina-ram.txt) | `tunnel.conf: old broker keys ignored: RINA_AREA, RINA_RAM (... the region switch is /opt/fdf/var/join.env)`, LAN-only; prune `unset RINA_RAM`, `unset RINA_AREA` |
| **G3** region | d2 regionbox: `cd / && tar xvzf - < /tmp/fdf-droplet.tgz`, `fdf-region-create test 172.30.0.10`; `inst.sh N1 10.81.81.1 --region 172.30.0.10:51888 --site-key <key> --area test` (N2 10.82.82.1); reboot both | rc 0 / EXIT 0 both, mappings registered, `my-channels: hubs=['regionbox']`; after reboot: `REGION switch: /opt/fdf/var/join.env RINA_RAM=172.30.0.10:51888; /opt/fdf/var/site.env RINA_AREA=test -> region mode`, `t4c (mode region)`, watcher active, boot merge via `watcher:poke`, no failed units; **N1 → N2 3/3 (1.78 ms), N2 → N1 3/3 (1.74 ms)** |
| teardown | on n2: `wg.teardown_wg_iface` on wg0 and on a wireguard-go `wgT` made by hand (g3-n2-teardown.txt) | wg0 True (wg-quick down), the daemon brought it back; wgT False: `wg-quick down wgT failed rc=1: ... wgT.conf does not exist (... the next event tries again)`, left up; the orphan scan skips it (`0 peers - not a FrogNet tunnel`) |
| re-join, watcher off | n2: `systemctl disable --now frognet-merge-watcher`, re-run the .run | `== the merge watcher: enabled (it runs in both modes)`, enabled/active |
| fdf-leave | n1: `/opt/fdf/bin/fdf-leave` (g5-n1-fdf-leave.txt) | watcher stays active; `LAN-only mode: no region switch (/opt/fdf/var/join.env: No such file or directory)`; no wg left |
| selfcheck | `python3 -m discovery selfcheck` | **14/14** |
| discovery oracles | `discovery/run_discovery_oracles.py` | **84/84 PASS, 0 regression** |
| simulator | `simulation/run_all.py` | 2 red: collectives/DDP, aiconnect (as T4b) |
| netns region-loop / NOKEY=a / lan-only / ponds / reach | `L=t4c-logs bash t4a-tools/netns-gates.sh` (DOCKER-USER bridged-frames allowance while it runs, removed after) | RESULT PASS 13+1 SKIP / 17+1 SKIP / 19/19 / 21 / 70 (region and reach exit 1 after RESULT PASS, as at T4a/T4b) |
| static-no-fallback.sh | `bash docs/cleanup-2026-10/static-no-fallback.sh` | **0 violations, 43 allowed** |
| static-no-broker.sh | same | **0 violations, 208 allowed** |
| oracles/ directory | `t4a-tools/oracles-dir.sh` | the same 4 red as T4b; routing_topology 36/36 (one new check) |
| syntax + copies | `t4a-tools/syntax.sh` | 34 files OK, copies cmp-identical |
| dist | `tools/make_deliverables.sh opt/fdf/dist` | x86_64.run 31,077,186 B b534afa2ca87b7e4; aarch64.run 28,160,128 B 413e809415717d41; droplet.tgz 23,233,795 B ccca750bbd0d7a4a (built 22:58Z) |

## Found, not done
- `live._dns_upstream` (exit_host.tsv's next hop, else the default route's gateway): untouched, John's call.
- FD&F operator tools keep an environment-then-file chain for RINA_RAM: `fdfram.where()` (env, else join.env; 3 copies) and `fdf-reach` `ram_of` (pond file, else env). Allowed in static-no-fallback with that reason; outside this brief.
- `mapInterfaces`: `eth0Name="${_mi_lan:-eth0}"` — a name default when no wired dev qualifies (getEth0Address then fails on a missing eth0, logged).
- `getFrogNet.bash` still answers `hostname -s` when getOurDomain times out (not cached).
- A region node updated only by the .run before T4c's installer has no inotify-tools: the .run now stops and says `apt-get install inotify-tools`.
- `ribbit/cpp/tests/reference/core/role_publish.py` (a test reference copy) still names tunnel.conf.
- From T4b, unchanged: the u1 upgrade's F6 echo shows `0.0.0.0` upstreams before the reboot (after it, `172.30.0.31`); dbhost timers poll; the C++/C# monitors unbuilt; Android default pond; T7 Pi/StreamingFrog checks.
