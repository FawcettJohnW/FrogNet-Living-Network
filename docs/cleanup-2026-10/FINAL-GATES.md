# Final gates — HEAD c20b7c6, run fresh (2026-10-13)

Tree: `/home/claude/clean` at HEAD **c20b7c6** (T6b), clean at the start (`git status` empty). Every gate below was run
again in this session; nothing was taken from an earlier record. Two gates found real defects (below); they were fixed
in the tree and the affected gates re-run. **Not committed.** Logs: `final-logs/` beside this file.
T0 column: `BASELINE-T0.md` (42465dd) where it recorded the gate; where it did not, the same check was run here on an
export of 42465dd (marked *run here on 42465dd*), or the T0 install's own result as reproduced in G4.

Where: this host (Python, C++, C#, node, the netns harness), and Docker containers from image `frognet-clean:noble`
(Ubuntu 24.04 x86-64 with systemd, the `t4a-tools/` harness). Containers g1, h1, h2, d2, d3, d4, n1, n2, u1, p1, p2 were
made fresh for this run and **all were removed at the end**; so were the networks made for it (fn-lan63/71/72/91/92).

## Defects found and fixed

1. **Licence oracle red on HEAD** (gate 1b). T6b (c20b7c6) committed five Croakus patch scripts with no licence header:
   `opt/frognet_semantic/docs/magnum_croakus/notes/patches-2026-10-13/{P,Q,R,S,patchlib}.py`.
   `run_discovery_oracles.py` on HEAD as committed: `DISCOVERY GATE: FAIL (83 pass, 0 known-fail, 1 regression)
   REGRESSIONS: test_license_oracle` (`LICENSE CHECKS FAILED: 5`; final-logs/oracles-HEAD-before-fix.txt).
   Fix: the tree's header, rendered by `usr/local/bin/frognet_apply_license.py` (`render(".py", "Fawcett Innovations
   LLC", "2016-2026")`, checked equal to the header already in `discovery/resolv.py`), inserted at the applier's
   `insert_at` offset (T4b's method). After: `PASS (84 pass, 0 known-fail, 0 regression)`.
2. **`fdf-region-create` reported a held region with the hub down** (gate 4, G3, while following OPERATING.md).
   On the region host, `systemctl start fdf-hub` before create (T4a's refusal check) left fdf-hub restarting under
   `Restart=always`; create's start of the hub then hit systemd's start limit (5 in 10 s). Create printed `Job for
   fdf-hub.service failed`, `Start request repeated too quickly`, then `== region myarea is held here` and **exited 0**
   (final-logs/walk-region-d2-run1-old-create.txt). Its check `systemctl enable --now ... || { ...; exit 1; }` cannot
   fire: on systemd 255 `systemctl enable --now` exits 0 when the start job fails, `systemctl start` exits 1 (shown on
   d2 with a throwaway unit, same shape: failing ExecStartPre, Restart=always).
   Fix [START_FAILURE_IS_SEEN_V1]: `systemctl enable X && systemctl start X || { ...; exit 1; }` for the RAM manager
   and for fdf-hub + fdf-client-AREA; `systemctl reset-failed` on fdf-hub/fdf-client-AREA just before their start (a
   failure from before hub.env existed); and create now stops (`fdf-hub did not say 'start (adopt)' within 15 s`) when
   the hub never says `start (adopt)`, the line it already waited for. The same `enable --now ... && / ||` test was
   changed the same way in `fdf-region-join` (merge watcher), `fdf-leave` (elector timer) and
   `usr/local/bin/install_databasehost.sh` (mariadb): there too the failure branch could never run. Both copies of the
   three FD&F scripts (`opt/fdf/bin`, `opt/fdf/src/fdf-source/fdf/bin`) are cmp-identical.
   Verified on a throwaway region host d3 (final-logs/g3-hub-before-create-fixed.txt): hub started before create, create
   at once → `start (adopt)`, rc 0, hub active; hub made to fail by a test drop-in → `fdf-region-create: fdf-hub /
   fdf-client-test3 did not start: journalctl -u fdf-hub -u fdf-client-test3`, rc 1.
   `opt/fdf/dist` rebuilt for it (gate 5).
3. **OPERATING.md section 5** said the region host lists its members with `/opt/fdf/bin/fdf-ls` and
   `/opt/fdf/bin/fdf-monitor --once`. On a region host made by fdf-region-create both stop with `no RAM manager: run
   fdf-join HOST:PORT, or set RINA_RAM=host:port`: the RAM address is in hub.env and the units, and there is no
   join.env (fdfram.where() reads env, then join.env). T5 had met the same and typed `RINA_RAM=127.0.0.1:51888`.
   The row now gives `/opt/fdf/bin/fdf-ls 127.0.0.1:51888` and `RINA_RAM=127.0.0.1:51888 /opt/fdf/bin/fdf-monitor
   --once`, both run on d2 (N1, N2, regionbox alive). check-docs: 0 problems after the edit.

Files changed (uncommitted): the five .py above; `opt/fdf/bin/{fdf-region-create,fdf-region-join,fdf-leave}` and their
`opt/fdf/src/fdf-source/fdf/bin` sources; `usr/local/bin/install_databasehost.sh`; `opt/fdf/dist/*` (rebuilt);
`docs/OPERATING.md`; plus this file, `T7-HARDWARE-ACCEPTANCE.md` and `final-logs/`.

## The gates

| # | gate | command | HEAD (this run) | T0 (42465dd) |
|---|---|---|---|---|
| 1a | discovery selfcheck | `cd /opt/frognet_semantic; PYTHONPATH=/opt/frognet_semantic PYTHONUTF8=1 python3 -m discovery selfcheck` | **PASS 14/14** on HEAD and again on the final tree (lifecycle SKIP, dev gate) | PASS 14/14 |
| 1b | discovery oracles | `PYTHONPATH=$PWD PYTHONUTF8=1 python3 discovery/run_discovery_oracles.py` | HEAD as committed: **FAIL 83 + 1 regression (test_license_oracle)** → defect 1 fixed → **PASS 84, 0 regression** (twice, after each fix) | FAIL 80 pass, 6 regression (imports of removed modules) |
| 1c | simulator | `PYTHONPATH=$PWD PYTHONUTF8=1 FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=$PWD/../../usr/local/bin python3 simulation/run_all.py` | **2 tiers red**: collectives+DDP (agent_workload), aiconnect ramp; the other 18 PASS. Same on the final tree | FAIL 6 tiers (DDP, aiconnect, SotF media planes, discovery suite, ...) |
| 1d | oracles/ directory | `bash t4a-tools/oracles-dir.sh /home/claude/clean` | 4 PASS (exit_gw_dot1, mapinterfaces_names, routing_topology 36/36, self_identity_lines); **4 red** (default_route_resolver 9, dhcp_scope: no `etc/dnsmasq_conf_template`, identity_fails_loud 10, opts_only_interface_lines 4), the same 4 as T4b–T5: they test code not in this tree | *run here on 42465dd*: 3 PASS, 5 red (the same 4, with opts_only 6, + mapinterfaces_names 8) |
| 1e | monitor parsers, Python | `cd usr/local/bin; python3 -m frognet_monitor_py.test_parser_vectors_oracle` (+ `test_snapshot_view_oracle`, `test_merge_oracle`) | **PASS 37/37**; snapshot view PASS (15); merge ALL PASS (41) | *run here*: parser **FATAL** (`identity.parse_echo_line / dot_one_of missing`); no snapshot-view oracle; merge ALL PASS |
| 1e | monitor parsers, C++ | cmake + make of `usr/local/bin/frognet_monitor_cpp` (scratch build dir), `ctest`, `frognet_parser_oracle parser_vectors.json`, `frognet_dashboard_oracle` | **0 warnings; ctest 2/2; parser 37/37; dashboard 32/32** | *run here*: ctest 2/2, parser 27/27, dashboard 27/27 |
| 1e | monitor parsers, C# | `dotnet build -c Release -p:TreatWarningsAsErrors=true; dotnet bin/Release/net8.0/frognet-monitor.dll selftest` (copy of frognet_monitor_cs + _shared) | **0 warnings, 0 errors; selftest 60/60** | *run here*: 0 warnings; 48/48 |
| 1f | dashboard oracle | `node var/www/html/oracle_dashboard32_views.js var/www/html/fln_dashboard32.html` | **PASS, 37 ok** | *run here*: T0's own (three-view) oracle PASS 11; HEAD's oracle on the T0 page FATAL `clearAll not defined` |
| 1g | hub oracle (T6a) | `FDF_PY=opt/fdf/lib/python python3 opt/fdf/hub/hub_oracle.py` | **RESULT PASS, 27** | *run here*: PASS 27 |
| 2a | netns region loop | `t4a-tools/netns-gates.sh` (L=final-logs; DOCKER-USER physdev ACCEPT while it ran, removed after) | **RESULT PASS, 13 PASS + 1 SKIP** (Communicator stage, COMMS_BUILD not given); exit 1 after RESULT PASS as always | PASS 13 (+ the same SKIP) |
| 2b | region loop NOKEY=a | (same script) | **RESULT PASS, 17 + 1 SKIP** | not run at T0 |
| 2c | LAN-only | (same script) | **RESULT PASS, 19/19** | not in the T0 tree (T3a) |
| 2d | ponds-daemon | (same script) | **RESULT PASS, 21** | PASS |
| 2e | reach | (same script) `BRIDGE_KT=.../fdf-member-kt FDF_TUN=.../fdf-tun-x86_64` | **RESULT PASS, 70**; exit 1 after RESULT PASS as always | PASS 70/70 |
| 2f | member selftest | `fdf-member-kt selftest` | **RESULT PASS** | PASS |
| — | netns after the fixes | | not re-run: no netns script runs fdf-region-create, fdf-region-join or fdf-leave (grep); the other fixed files are a licence comment | |
| 3a | static-no-broker | `bash docs/cleanup-2026-10/static-no-broker.sh` | **209 allowed, 0 violations**; strict (pages + monitors) 4 allowed, 0 (HEAD and final tree) | *run here on 42465dd*: 172 allowed, **3020 violations** |
| 3b | static-no-fallback | `bash docs/cleanup-2026-10/static-no-fallback.sh` | **43 allowed, 0 violations** (HEAD and final tree) | *run here*: 25 allowed, **237 violations** |
| 3c | check-docs | `bash docs/cleanup-2026-10/check-docs.sh` | **0 problems**, 34 documents, 32 commands (HEAD, and after the OPERATING.md fix) | cannot run: docs/OPERATING.md did not exist |
| 3d | syntax | `bash t4a-tools/syntax.sh` | HEAD: 8 files OK (it checks files changed since the commit, so on a clean HEAD only its 3 fixed extras + the 5 headers); final tree **15 OK, 0 bad**, opt/fdf copies cmp same | — |
| 3d′ | syntax, every tracked file | syntax.sh's rules over `git ls-files` (scratch copy, not in the tree) | **1059 OK**; 2 flagged: `site/{demonstrations,the-claim}.dc.html`, an artefact of syntax.sh's line-based `<script>` cut (one-line `<script>…</script>`); their scripts extracted properly pass `node --check` | *run here*: 1237 OK, 4 flagged (the same two pages' copies under broker/, and fdf-region-create / fdf-wg-impl absent, so their cmp fails) |
| 4 G1 | LAN-only install | `node.sh g1 FrogNetHost.G1 172.30.0.21 fn-lan1:10.61.61.1:eth1; docker exec g1 bash /root/inst.sh G1 10.61.61.1`; `reboot.sh g1` | **PASS.** EXIT 0 in 6 min 25 s; build `frognet-install-2026-10-12-t5`; A2b wireguard-go recorded; C3 selfcheck PASS; `enabled: frognet-dbhost-elect.timer (LAN-only)`; F6 `+ echo: G1,10.61.61.1,172.30.0.21,0.0.0.0`, `+ snapshot: mode=lan-only`, "All checks passed." After reboot: every unit enabled/active, **no failed unit**, daemon `(mode lan-only) base fdf-2026-10-12-r20` + `LAN-only loop started`; snapshot lan-only, members [G1 self], databasehost 10.61.61.1, errors []; `frognet_monitor --once` rc 0; landing 200, dashboard 200, /fdf/ 403; no wg; /usr/local/bin = tree files only | T0 installer on a fresh box (T4a): pip FAILED mysqlclient, NetfilterQueue (no -dev packages). With them put on first (G4's u1, this run): F6 `x echo: ... 502`, "1 check(s) failed"; after reboot **4 failed units** (apache2: `AH00526 Syntax error ... frognet-ssl.conf`, dbhost-advertise, mediahost-advertise, tunnel-setup) |
| 4 G2 | two LAN-only nodes + elector | h1 serves 10.71.71.0/24 (eth1); h2 on h1's LAN as 10.71.71.20 (eth1), serves 10.72.72.0/24 (eth2, `--lan-iface eth2`); both rebooted | **PASS.** Both EXIT 0, all checks passed, elector timer enabled. h1 getHosts H2+H1, route `10.72.72.0/24 via 10.71.71.20 dev eth1 metric 22`; h2 getHosts H2+H1; `SERVICE_ELECT role=databasehost hosts=2 ... winner=10.72.72.1` on both; /etc/hosts and `databasehost.frognet` = **10.72.72.1 on both**; elector runs (`frognet-dbhost-elect.service` finished, 0 errors); h1→10.72.72.1 3/3, h2→10.71.71.1 3/3, each reads the other's echo; no failed unit | not run at T0 |
| 4 G3 | region | d2 by OPERATING.md §3 (below): `fdf-region-create myarea 172.30.0.10`; n1/n2 `inst.sh N1 10.81.81.1 --region 172.30.0.10:51888 --site-key <KEY> --area myarea` (N2 10.82.82.1); reboot | **PASS after defect 2.** Create rc 0, RAM manager `{"ok":true,"server":"fdf-2026-10-12-r20"}`, `map_site myarea: {"ok":true}`, hub `start (adopt)`, second create refused. Nodes EXIT 0: `N1 joined the region`, `mapping 10.81.81.0/24 -> regionbox:1:50 registered (site myarea)`, `my-channels: hubs=['regionbox']`, handshake. After reboot: `WG_KEY: made ...`, `(mode region) base fdf-2026-10-12-r20`, key 0600. **Hub pings N1→N2 3/3 1.5 ms, N2→N1 3/3 1.1 ms** (traceroute: 10.253.253.254 then 10.82.82.1). **Snapshot** both: mode region, pond myarea forward, hub wg0 edge 10.253.8x.8x with handshake age, members N1 N2 (node, alive, region), databasehost 10.82.82.1, errors []. **/fdf/**, fdf_dashboard.html, getParticipants.php 200 on :8080 and :80, participants N1 N2 regionbox alive. Monitor frames on both. d2: `wg show wg-myarea` both peers with their /24s. **Recovery** (d2 off fn-wan 52 s): 0/2 both ways while off, nodes logged `REGION: lost; withdrew its routes and names, tore down 1 tunnel(s)`; **2/2 both ways at +15 s** after reconnect and at +40/+60/+90, path back through the hub, no manual step. Region host update per §3: hub.env, site key, both units, wg.impl md5-identical, hub `start (adopt)`, traffic back. RAM manager stopped 40 s: fdf-client waited in one run (NRestarts +1), re-joined, no start-limit hit | no fdf-region-create (region hosts hand-made, broker era) |
| 4 G4 | upgrade from T0 | u1: T0 tree (git archive 42465dd) via `u1t0.sh` (with broker state), reboot; tree via `u1up.sh` (`--from-repo --preserve`), reboot | **PASS.** `--preserve: node name 'U1' recovered`, `gateway IP 10.63.63.1 recovered`, `LAN interface eth1 from interfaces_override.conf`; C1c ran every section, `unset BROKER_URL`; F6 all passed, EXIT 0. fnid ecb6a86a… and node_private.key md5 8d01382b… **unchanged**; tunnel.conf broker keys gone; tunnel-setup, pond-bootstrap, broker-v4 units, 91/99 hooks, /opt/frognet_semantic/broker all gone; 90-frognet-merge 19 → 107 lines. After reboot: **no failed unit** (before: 4), daemon lan-only base r20, merge completed, getHosts + both echoes answer; **/usr/local/bin holds nothing the tree lacks** (T4b: 45 stray files) | the baseline itself |
| 4 G5 | leave + uninstall | n1 (region member, + operator crontab line): `/opt/fdf/bin/fdf-leave`, then `bash /usr/local/bin/installer/frognet_install.sh --uninstall`; n2: `--uninstall` straight from the region | **PASS.** fdf-leave rc 0: fdf-client/fdf-facts removed, join.env/site.env gone, `re-enabled frognet-dbhost-elect.timer`, daemon `(mode lan-only)`, no wg, snapshot lan-only errors []. Uninstall EXIT 0 on both (n2's phase 0 ran fdf-leave: `left the region`): `removed 182 recorded FrogNet file(s)`, packet-filter rules `the machine's again`, crontab: n1 = the operator line only, n2 none; no froguser, no FrogNet unit/dir/hook, /usr/local/bin 0 entries, /opt/fdf gone, dnsmasq/apache2/mariadb disabled/inactive, resolv.conf `nameserver 127.0.0.11 ...` as found. Names do not resolve afterwards: docker moved its embedded resolver on the container restart (now 39905/40032, restored DNAT 35083/34463) — the container, as in T4a/T4b | no `--uninstall` option |
| 4 walk | OPERATING.md §3 command by command | d2 fresh, then the node install and the update (below) | **followed; 3 differences**, listed below | — |
| 4 LAN region | rehearsal of T7 steps 1 and 10 on x86 | p1 (FrogNet node) holds region `lan` on its LAN address; p2 joins; internet cut; key rotated (below) | **PASS** (below) | — |
| 4 net config | a second pond reachable (T7 step 9) | d4: droplet tgz + `fdf-region-create lan 172.30.0.14`, then a drop-in `Environment=RINA_NET_CONFIG=/opt/fdf/var/net-lan.json` holding `{"reach":"all","map":"allowed"}` (final-logs/pond-net-config-dropin.txt) | default cell `reach none, map refused` (rina-host: `no /opt/fdf/etc/net.json`); after the drop-in and a restart `(pond, config, net) = {..."reach":"all","map":"allowed"...}`. `/opt/fdf/etc/net.json` (rina-host's default path) does not work as given: no bundle ships /opt/fdf/etc | — |
| 5 | dist | BUILD lines + every payload file `cmp` against its source (final-logs/dist-compare.txt) | HEAD's dist (built at ce72a41) **carried HEAD's sources**: x86 .run 111/112 files = tree, aarch64 .run binaries = rinab/build/arm64 + comms-arm64, droplet 57/59 = tree + fdf-hub.service = deliverables/etc-droplet; only opt/fdf/BUILD (generated) differs; all `build=fdf-2026-10-12-r20`. **Rebuilt after defect 2** (`rinab/tools/make_deliverables.sh opt/fdf/dist`, 12:58Z): x86_64.run 31,066,176 B sha256 77fb15748f033ebe; aarch64.run 28,148,395 B dc4ae044fc64389e; droplet.tgz 23,216,582 B b5fc28cc703447eb; payload compare again: every file but BUILD equal to its source | the T2-era builds |

### OPERATING.md §3, followed (final-logs/walk-region-d2.txt, g3-n*-install.txt, g3-droplet-update.txt)

d2: Ubuntu 24.04 container with python3 only, `/tmp/fdf-droplet.tgz` copied in. Each command as written, rc in the log.
- `apt-get install -y python3 wireguard-tools`, `cd / && tar xvzf - < /tmp/fdf-droplet.tgz`, `systemctl daemon-reload`,
  the two `grep 10\.253\.` (printed nothing): as written.
- `/opt/fdf/bin/fdf-region-create myarea 203.0.113.7` → typed with the address members reach, `172.30.0.10` (the doc
  says to). **Difference 1:** it stopped: `fdf-wg-impl: wireguard-go is the choice and it is not installed: install it
  (apt-get install wireguard-go)`. The prose says "install wireguard-go first then"; the command block does not have
  that line. Typed `apt-get install -y wireguard-go`, then create again: rc 0.
- The three `ufw` lines: no ufw on this host (the ports are open on its network); nothing typed.
- Node install: the doc's `frognet_install.sh --from-repo --name NODE2 --ip 10.81.81.1 --no-ssid-projection --region
  ... --site-key KEY --area myarea`. **Difference 2:** typed through `inst.sh`, which adds `--db-pass testpw
  --no-reboot` (the doc's form asks the password twice on a terminal and reboots; T6a typed those on a pty for §2),
  then `reboot.sh`. The clone was the working tree copied by put-tree.sh, not `git clone` (the public repo is still
  e889208).
- Update command `cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart
  fdf-ram-myarea fdf-client-myarea fdf-hub`: as written.
- **Difference 3:** §5's region-host member listing (`fdf-ls`, `fdf-monitor --once`) did not work as written: defect 3,
  fixed in the doc.
- `fdf-hub` before create: refused with the doc's line `fdf-hub: /opt/fdf/hub/hub.env does not exist: this machine holds
  no region yet. Run: /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP` (this check is what exposed defect 2).

### LAN-only region host (rehearsal of T7 step 10, x86)

p1 = a FrogNet LAN-only node (10.91.91.1, installed with inst.sh) standing in for the Pi; p2 = a FrogNet node on p1's
LAN as 10.91.91.20, serving 10.92.92.0/24 (`--lan-iface eth2`). x86-64, so the x86 `.run` stands in for the aarch64 one
(final-logs/lanregion-*.txt). On p1, the commands T7 step 10 gives:
- `cd / && sed '1,/^__PAYLOAD_BELOW__$/d' /tmp/fdf-install-x86_64.run | tar xvzf - opt/fdf` → 60 files, /opt/fdf/BUILD r20
  (only opt/fdf: the payload's sudoers fragment is 0644 in the archive and the `.run` itself chmods it).
- `cp <clone>/opt/fdf/src/fdf-source/deliverables/etc-droplet/systemd/system/fdf-hub.service /etc/systemd/system/ &&
  systemctl daemon-reload`; both 10.253 greps empty.
- `/opt/fdf/bin/fdf-region-create lan 10.91.91.1` → rc 0, RAM manager r20, `map_site lan: {"ok":true}`, hub `start
  (adopt)`. The hub's member name is `FrogNetHost` (`hostname -s` of a FrogNet node).
- p2: `bash /tmp/fdf-install-x86_64.run <KEY> 10.91.91.1:51888 lan` → rc 0, `P2 joined the region`, `mapping
  10.92.92.0/24 -> FrogNetHost:1:50 registered (site lan)`, endpoint 10.91.91.1:50889, handshake.
- **No internet:** fn-wan disconnected from both (no default route on p2). +60 s: handshake renewed, p2 → hub edge
  10.253.253.254 2/2, fdf-client/fdf-facts/daemon active, snapshot region, errors []; `systemctl restart fdf-client` →
  joined and registered again; `fdf-ls 127.0.0.1:51888` on p1 lists FrogNetHost and P2 alive.
- **Key rotation (T7 step 1's two commands, area lan):** the new key written by `tee` (0600), `map_site` →
  `{"ok":true}`. p2 with the old key, at its next registration: `fdf-client: mapping 10.92.92.0/24 -> FrogNetHost:1:50
  REFUSED by the region: HTTP 200: EPERM (authentication failed) = RINA_SITE_KEY in /opt/fdf/var/site.env is not the
  key map_site holds for this area (HMAC mismatch)`. The `.run` again with the new key → `registered`, hub edge 2/2.
- Not shown: traffic carried by this hub between two members (one member here; G3 shows it with a separate host).
- Found on the way: a file put in /tmp before a reboot of a FrogNet node is gone (F7 makes /tmp a tmpfs, 256 MB).

## Found, not fixed (one line each)

- The installer changed in ce72a41 (T6a fixes: elector enabled on LAN-only) but still logs `frognet_install.sh build
  frognet-install-2026-10-12-t5`, as do T5-era installs; `commit=` in /opt/frognet_semantic/BUILD (from a git checkout)
  is what tells them apart.
- `fdf-region-create` gives no way to set the pond's net config: every region it makes publishes `reach none, map
  refused`, so a PC cannot reach it as a second pond without the drop-in (T7 step 9a).
- `fdf-droplet.tgz` holds x86-64 binaries only, and the aarch64 `.run` has no fdf-hub.service and always joins: no one
  file makes a Raspberry Pi a region host (T7 step 10 gives the route through the clone).
- After `fdf-leave`, `/fdf/` still answers 200 (the node keeps /opt/fdf/www); OPERATING says a LAN-only node answers
  403, which holds for one that never joined.
- syntax.sh checks only files changed since the last commit, and its `<script>` cut misreads one-line script blocks.
- This host carried a blanket `-A DOCKER-USER -j ACCEPT` from an earlier session (final-logs/host-docker-user-before.txt).
  With it, the first G3 outage run kept 2/2 while the region host was off, because this host forwarded between the
  docker LANs once the nodes withdrew their wg routes (final-logs/g3-outage-run1-host-forwarding.txt). It was removed and the
  outage re-run; the table shows the second run.
- The harness once left `/etc/hostname` empty in a new container (u1; boot.sh's copy raced); rewritten by hand before
  the T0 install, which sets the name itself.
- Everything in T4a–T6a "Found, not done" that is still open is listed in T7-HARDWARE-ACCEPTANCE.md, "Open code
  questions".
