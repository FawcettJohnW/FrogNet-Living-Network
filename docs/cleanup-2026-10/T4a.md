# T4a — a machine that is not John's: clean install (LAN-only and region), region creation, upgrade, leave (2026-10-12)

Not committed. Against the T3b commit (1d1e21b): 35 paths (23 modified, 10 deleted, 2 new: fdf-region-create and its
opt/fdf/bin copy; the three opt/fdf/dist files rebuilt), plus this file, t4a-logs/ and t4a-tools/ (the container
harness, sandbox only).

**Where the gates ran.** Docker containers on this host, image frognet-clean:noble: a debootstrapped **Ubuntu 24.04
x86-64** minbase with systemd. No Debian mirror, no Docker Hub, no arm64 emulation here, so the clean-install gate is
x86-64 Ubuntu 24.04 only. The Raspberry Pi (aarch64, Raspberry Pi OS) is John's T7: the same commands, with
fdf-install-aarch64.run. Only the x86 .run was executed here; the aarch64 one was rebuilt and its build checked
(make_deliverables refuses a binary that is not the build tag), not run.

**The harness** (docs/cleanup-2026-10/t4a-tools/; nothing in it is shipped or changes the installer):
boot.sh (a container made to look like a machine: the sandbox's TLS-proxy CA trusted, docker's bind-mounted
resolv.conf/hosts/hostname replaced by ordinary files with the same content), node.sh / net.sh (uplink on `fn-wan`
as eth0, LANs on `--internal` networks, every interface name fixed), put-tree.sh (the working tree as a clone would
look, no .git), inst.sh (the installer in the background), reboot.sh (docker restart = a reboot; docker's three bind
mounts removed again), node-state.sh / region-state.sh / leftovers.sh (what the gates read).
Things the harness had to absorb, found on the way (none is the installer's): the proxy CA (pip got
CERTIFICATE_VERIFY_FAILED; and the image's old ca-certificates package, upgraded by the installer's apt run, dropped
the CA again — boot.sh upgrades it first); docker reorders unnamed interfaces on a restart (u1's eth0/eth1 swapped and
its LAN profile landed on the uplink — every endpoint now has a fixed name); a LAN network with a gateway took the
default route (LANs are `--internal`); dockerd on the host sets FORWARD DROP with br_netfilter, which broke the netns
harness's own bridges (netns-gates.sh accepts bridged frames in DOCKER-USER while it runs, then removes the rule);
the disk filled once with six containers (two installs died in dpkg; those containers were rebuilt, nothing was
gated on them).

## Changes (one line each, why)

Installer, usr/local/bin/installer/frognet_install.sh (build line `frognet-install-2026-10-12-t4a`, logged first)
- A2: + libmariadb-dev, libnetfilter-queue-dev (mysqlclient and NetfilterQueue publish no Linux wheel; pip compiles
  them; build-essential, pkg-config, python3-dev were already listed), + file (C2's arch check), + uuid-runtime
  (uuidgen: frognet-node-guid.sh, registerFrogNetHost.bash, the reporters); ax25-tools/ax25-apps out ([HAM_OUT_V1]
  said they were out, they were not); the MariaDB apt line lost its `|| true`.
- A3: a pip failure stops the install, naming the packages (the services ExecStart /usr/bin/python3: these are what
  they import). Flask, Werkzeug, pyOpenSSL left the pip list: nothing a node runs imports them (one simulation tool
  imports flask), and on Ubuntu 24.04 pip cannot install them over the Debian-owned blinker / cryptography.
- B1 gone; C1a applies the tree's etc/sysctl.d/90-frognet-forwarding.conf after C1 (B1 wrote an older copy that C1
  then overwrote; the procfs "belt-and-suspenders" second write went too).
- C1 --from-repo copies the node file set (frognet_node_files, below), files only, root-owned, not etc/ opt/ usr/ var/
  whole: opt/fdf (≈250 MB: FD&F sources, Android tree, rlite tgz, dist, Windows), the book, the ham scripts, bytecode
  stay in the checkout. 1,610 files land.
- C1 writes the install record /var/lib/frognet-install/files (what it laid down), which the reset removes by.
- A0b (new, before apt): records the machine before FrogNet once, in /var/lib/frognet-install/os-before:
  resolv.conf (as file or link), NetworkManager.conf, hostname, hosts, fstab, and the enabled/active state of the eight
  units the install touches; B2 adds iptables-save/ip6tables-save (after A2, which installs them). Not recorded on
  --preserve or over an install with no record (what is there is FrogNet's; it says so).
- D1 runs before phase A, takes `--lan-iface`, and refuses the uplink: no candidate, or the given/derived interface is
  the default route's device, stops with the interfaces and the option to give (it used to default to eth0, the
  uplink on a one-NIC box, and setup_lillypad step 2 then deleted the uplink's profile). --preserve keeps the
  interface in interfaces_override.conf. D7's "not marking the uplink" branch went (D1 makes it impossible).
- --preserve reads the node's name and IP from opts_only.conf before anything changes (it read them in E1, after D2
  had deleted the file: every non-interactive --preserve died at the E1 prompt with no message — found in G4). E1 says
  why when it cannot ask.
- D4, D5, D7, F2: the tree's files are the files: 99-frognet.cnf (D4 only sizes the pool in it), the four vhosts (their
  journal-logging content moved into the tree's files), 90-frognet-merge, logrotate.d/frognet. Each phase stops if
  its file is missing. 99-ifup is removed (it and 90-frognet-merge each ran runMerge per event); the mysql-server
  logrotate rule (overlapping the distribution's mariadb rule) is removed if no package owns it.
- E1: KNOWN_NODES (John's eleven boxes on 10.101.x) and the 10.101.50.1 prompt example removed.
- E5 makes the certificate pair when the KEY is missing (it checked the .crt; --from-repo shipped a .crt without a
  key, so E5 skipped and Apache could not start: seen on the T0 node).
- F6 starts Apache before asking it, on :8080 (it curled :80, frognet-proxy's, with Apache stopped since D2: never
  passed), wants the echo to be "<NAME>,...", and checks one merge hook, the key, LAN ≠ uplink; FrogUser's password via
  MYSQL_PWD, not argv.
- R (new, after F7): with `--region RAM_HOST:PORT --site-key KEY --area NAME`, runs
  opt/fdf/dist/fdf-install-$(uname -m).run KEY RAM AREA from the payload; the .run is checked before phase A and a
  missing one stops; key/area prompted if omitted (no terminal: stop). Without --region the node is LAN-only. Not run
  when F6 failed.
- `--uninstall [--keep-identity] [--keep-db]` → frognet_reset.sh --uninstall; `--no-reboot`; `-h` prints all 21
  options (t4a-logs/help-options.txt); umask 022 (docker exec runs with umask 0000: dirs came out 0777).
- D2 also clears getFrogNet.cache (see getFrogNet.bash).

frognet_reset.sh (build `frognet-reset-2026-10-12-t4a`)
- Phase 0 (new): fdf-leave when the node is in a region (join.env would otherwise put a reinstalled "LAN-only" node
  back in region mode).
- /usr/local/{bin,sbin,lib,share}: only the files in the install record, then the empty FrogNet dirs four levels down
  (it removed /usr/local/bin and /usr/local/sbin WHOLE). No record (installed before T4a): says so, leaves them.
- Phase 8 puts back the record: resolv.conf, NetworkManager.conf, the packet-filter rules (the record plus an empty
  section for every table it lacks, so FrogNet's filter rules do not survive), systemd-resolved; --uninstall also
  hostname, hosts, fstab and dnsmasq/apache2/mariadb/hostapd/ifplugd/unbound/netfilter-persistent. NetworkManager is
  never stopped (it may carry the SSH). No 8.8.8.8. No record: it says so and names it a failure on --uninstall.
- Globs now also take frognet-*.target, frognet-*.service.d, 99-frognet*.cnf, frognet-universal.*, limits.d/99-frognet.
- `--uninstall`: + crontab lines (others kept; `crontab -r` when only FrogNet's were there), froguser (only if the
  install made it: F4 records that), /opt/fdf and fdf-* units (unless the machine holds a region: hub.env), the record.
- every failed step is named at the end and the exit status is 1; `mariadb || mysql || true` → mariadb.

Node file set, usr/local/lib/frognet_world_manifest.sh + frognet_build_release.sh
- FROGNET_NODE_EXCLUDE (new) and frognet_node_files ROOT: WORLD_PATHS − NEVER_SHIP − NODE_EXCLUDE, listed by GNU tar
  with the same --exclude semantics (archive /dev/null: no data read). The installer copies exactly its output;
  build_release builds the world tar from the same paths with the same excludes (its own inline node excludes moved
  into the manifest; it keeps only packaging and secret-staging excludes; its per-unit re-add loop, which put every
  frognet-* unit into the archive twice, is gone). full_tar.bash does not use NODE_EXCLUDE (the repository keeps the
  book). NEVER_SHIP + etc/ssl/*.crt, *.csr.
- etc/ssl/frognet-universal.crt and .csr deleted (a cert without its key); etc/systemd/system/fdf-{client-frognet,hub,
  ram-frognet}.service deleted (cmp-identical copies of the droplet's units, StreamingFrog/frognet baked in: no node runs
  them). The four Apache vhosts in etc/apache2/sites-available now hold what the installer used to write.

FD&F (opt/fdf/src/fdf-source, opt/fdf copies cmp-identical)
- fdf/bin/fdf-region-create (new): `AREA PUBLIC_IP [--ram-port 51888] [--hub-port 50889] [--ufw]` — site key made once
  (root-only, /opt/fdf/var/site-key-AREA, printed once at the end), fdf-ram-AREA.service written and started, map_site,
  /opt/fdf/hub/hub.env written (the only place it is made), fdf-hub + fdf-client-AREA (FDF_NAME = this machine's
  hostname) started, the firewall lines printed (run with --ufw), the join line printed. A second run stops. PUBLIC_IP
  is whatever address members reach (a LAN address works: no internet needed).
- deliverables/etc-droplet: ships fdf-hub.service only; it does not start without hub.env and says "Run:
  /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP" (EnvironmentFile optional + an ExecStartPre check, so that message is
  the failure). fdf-ram-frognet / fdf-client-frognet are no longer shipped (fdf-region-create writes per-area units).
- fdf.env holds no operator setting: it sources /opt/fdf/var/area.env (never shipped), so an update cannot overwrite
  one (the droplet update in G3 kept hub.env, site key, units, area.env byte-identical).
- header.sh (the .run) and fdf-region-join: SITE_KEY, RAM and AREA are all required (no 165.232.153.144 / frognet);
  fdf-join's examples generic; fdf-leave and the .run print their build; umask 022 in header.sh and fdf-region-create.
- make_deliverables.sh: generic BUILD for= lines (no StreamingFrog/Seattle names); deliverables/BUILD.* (unused) deleted.
- opt/fdf/dist rebuilt: `build=fdf-2026-10-11-r19-a15 built=2026-10-12T15:46Z` for all three
  (x86 .run 31,072,571 B sha256 a784649dbcfd2478; aarch64 .run 28,145,044 B 0f5414e86b449549; droplet tgz
  23,226,211 B 8644d1b4ef715efc). The x86 .run's 39 FrogNet files and 49 fdf files are cmp-identical to the tree.
  Every binary make_deliverables needs was present in the build tree; nothing had to be built.
- README.md (both copies): the install section is fdf-region-create + the three-argument .run / --region.

getFrogNet.bash: chmod 644 on its cache before the rename. mktemp made it 0600, the merge (root) writes it and Apache
(www-data) reads it, so once root had run it every echo of the node came back EMPTY (found at F6, G1).

## Gates (commands and results; logs in t4a-logs/)

Red first, the unchanged T3b tree on a fresh container (baseline-T3b-install.txt): A3 `pip FAILED: mysqlclient`,
`NetfilterQueue`, `Flask` ("Cannot uninstall blinker 1.7.0, RECORD file not found ... installed by debian"),
`pyOpenSSL` (same, cryptography) — warned, went on — then C2 `FATAL venv build failed for 2 of 23: mysqlclient
NetfilterQueue`. The T0 node (G4's base) after its reboot (t0-node-after-reboot.txt, g4-before-upgrade.txt): apache2
FAILED `SSLCertificateKeyFile: file '/etc/ssl/frognet-universal.key' does not exist`; 90-frognet-merge the
installer's 19-line copy plus 99-ifup and 91-frognet-connectivity; its F6 `x echo: curl: (22) ... 502`.

New-tree refusals (d1-refuse-uplink.txt): `--lan-iface eth0` → `FATAL D1: eth0 is the uplink (the default route uses
it) ... interfaces: eth0 eth1 (uplink = ... eth0). Give another interface with --lan-iface IFACE.` rc=1, in 0 s; no
second NIC → `FATAL D1: no wired interface to serve the LAN on besides the uplink ...` rc=1. A3 with failing pip
(a3-stops-on-pip-failure.txt) → `FATAL pip failed for 4 package(s) the services import: ...` EXIT 1.

**G1 clean LAN-only install** — g1: eth0 uplink 172.30.0.21, eth1 LAN.
`bash t4a-tools/node.sh g1 FrogNetHost.G1 172.30.0.21 fn-lan1:10.61.61.1:eth1; docker exec g1 bash /root/inst.sh G1 10.61.61.1`
(= `frognet_install.sh --from-repo --name G1 --ip 10.61.61.1 --db-pass … --no-ssid-projection --no-reboot`), then reboot.sh.
- EXIT 0; `Detected: eth0=eth1 (served LAN) ... uplink=eth0`; `copied 1610 files`; `[PASS] discovery selfcheck (all
  suites)` (C3); F6 27/27 `+`, `echo: G1,10.61.61.1,172.30.0.21,0.0.0.0`, "All checks passed." (g1-install.txt)
- after the reboot (g1-after-reboot.txt): enabled/active — mariadb apache2 dnsmasq NetworkManager cron frognet-proxy
  frognet-daemon frognet-merge-watcher frognet-tunnel-daemon-v3 frognet-eth0-fixup frognet-sysperf
  frognet-dbhost-advertise.timer; no failed units; daemon `(mode lan-only)`, `LAN-only loop started`; merge
  `completed`, `discovered`; databasehost 10.61.61.1; getHosts.php
  `[{"ip":"10.61.61.1","name":"G1","echo":"FrogNetHost.G1,10.61.61.1,172.30.0.21,0.0.0.0",...}]`; echo on :8080 and
  through the proxy on :80 `G1,10.61.61.1,...`; **no wg interface**; no /opt/fdf, no /opt/frognet_semantic/docs, no
  ham scripts, no fdf units on the node; the keypair /etc/ssl/frognet-universal.{crt,key} made by E5; one hook.

**G2 two LAN-only nodes** — h1 serves 10.71.71.0/24; h2 is on h1's LAN as 10.71.71.20 and serves 10.72.72.0/24 on
eth2 (`--lan-iface eth2`: auto-detection would have taken eth1, the link into h1's LAN). Both installs EXIT 0, F6 all
passed. First pass: h2 found h1, h1 did not name h2, and h2's hosts named itself at h1's address — h2's link into
h1's LAN was named eth1, which discovery does not treat as the upstream (it reads wlan0Name). As on a real node (it
joins another node's LAN as a WiFi client), the link was renamed wlan0 (`docker network connect ... ifname=wlan0`, the
same IP) and h2 rebooted; no merge was run by hand that counted (one manual runMerge on each was tried before the
rename; it changed nothing). Then (g2-h1-state.txt, g2-h2-state.txt):
- h1 getHosts.php: `[{"ip":"10.72.72.1","name":"H2",...},{"ip":"10.71.71.1","name":"H1",...}]`; route
  `10.72.72.0/24 via 10.71.71.20 dev eth1 metric 22`
- h2 getHosts.php: `[{"ip":"10.72.72.1","name":"H2","echo":"FrogNetHost.H2,10.72.72.1,172.30.0.42,10.71.71.20",...},
  {"ip":"10.71.71.1","name":"H1",...}]`
- one databasehost: `databasehost.frognet` resolves to **10.72.72.1 on both** (the guest's LAN, as in T3a's netns
  LAN-only test). h1's /etc/database_ip still says 10.71.71.1, written at its install and not rewritten since; no wg.

**G3 region** — d2 "regionbox" 172.30.0.10 (python3, wireguard-tools, wireguard-go only; no FrogNet):
`cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload` then, before fdf-region-create,
`systemctl start fdf-hub` → fails, journal: `fdf-hub: /opt/fdf/hub/hub.env does not exist: this machine holds no
region yet. Run: /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP` (g3-droplet-hub-before-create.txt).
`/opt/fdf/bin/fdf-region-create test 172.30.0.10` (g3-region-create.txt) → `site key: made, stored root-only in
/opt/fdf/var/site-key-test (mode 600)`, `RAM manager: {"ok":true,"server":"fdf-2026-10-11-r19-a15"}`, `map_site test:
{"ok":true}`, hub.env written, `start (adopt): members=1`, `fdf-client: regionbox joined the region`, firewall lines
printed, the key printed once, rc=0; a second run stops ("already holds a region"). The droplet update (untar the new
tgz, daemon-reload, restart the three) left hub.env, site key, both units and var/area.env byte-identical
(g3-droplet-update.txt).
Nodes: `inst.sh N1 10.81.81.1 --region 172.30.0.10:51888 --site-key <key> --area test` (and N2 10.82.82.1), fresh
containers. Phase R (g3-n1-install.txt): the .run's BUILD, `site.env: RINA_AREA=test`, fdf-client + fdf-facts
started, `mapping 10.81.81.0/24 -> regionbox:1:50 registered (site test)`, `my-channels: hubs=['regionbox']`, wg0
endpoint 172.30.0.10:50889 with a handshake, members N1 N2 regionbox alive; EXIT 0 on both. After both rebooted
(g3-n*-after-reboot.txt): `WG_KEY: made /var/lib/frognet-tunnel/node_private.key (none on disk); public key …
[DAEMON_OWNS_ITS_KEY_V1]`, `Starting frognet-tunnel-daemon v3.0.0 build frognet-tunnel-2026-10-12-t3a (mode region)`,
key 0600, fdf-client/fdf-facts/daemon active, hub link up; **N1 10.81.81.1 → N2 10.82.82.1 across the hub: 3/3,
1.4 ms; N2 → N1 3/3, 2.0 ms**. On d2 (g3-d2-monitor.txt): fdf-ls and `fdf-monitor --once` list N1, N2, regionbox,
all alive; `wg show wg-test` has both nodes' public keys with their /24s.
Outage (g3-outage.txt): `docker network disconnect fn-wan d2` → N1→N2 0/2; 49 s later reconnect (same IP, eth0);
+20 s N1→N2 0/2, N2→N1 2/2; **+40 s and after: 2/2 both ways**; fdf-ls on d2: N1 N2 alive, both /24 routes back on
wg-test. No manual step on any machine.

**G4 upgrade** — u1 installed from the T0 baseline (42465dd, git archive copied in; the T0 installer's own fresh-box
failures compensated the way the T0 audit names: the -dev packages put on first; `--broker-host 192.0.2.1 ... --pond
testpond` so it had broker state), rebooted, then the new tree with `--from-repo --preserve --db-pass …
--no-ssid-projection --no-reboot` (no --name/--ip), rebooted. Before → after (g4-before-upgrade.txt,
g4-after-upgrade.txt):
- fnid 8034f4077b49c560f7637d38f35688f0 → same; name U1 / router 10.63.63.1 → same (`node name 'U1' recovered`,
  `gateway IP 10.63.63.1 recovered`, `LAN interface eth1 from interfaces_override.conf`); node_private.key md5
  77e9d737303231b149eb42201f4dd237 → same.
- broker state: tunnel.conf `BROKER_URL=http://192.0.2.1:8443 GROUP_NAME=testpond POND_NAME=testpond` → empty
  (C1c: `unset BROKER_URL/GROUP_NAME/POND_NAME`), the tunnel.conf backups, frognet-tunnel-setup + pond-bootstrap
  units, frognet-broker-v4.service, the 91 hook, 99-ifup, frognet-tunnel-setup-v3.sh, pond-bootstrap.sh,
  /opt/frognet_semantic/broker → all gone; 90-frognet-merge 19 → 102 lines (the tree's).
- services: before apache2 + frognet-tunnel-setup + mediahost-advertise FAILED; after: all enabled/active, no failed
  units, `(mode lan-only)`, getHosts.php and the echo answer, F6 all passed, EXIT 0.
- the upgrade logs `no record of this machine's name resolution before FrogNet (it was installed before T4a)`.

**G5 leave and uninstall** — m1, a region member made the same way as G3 (+ an operator crontab line):
`/opt/fdf/bin/fdf-leave` (g5-fdf-leave.txt) → fdf-client/fdf-facts removed, join.env/site.env gone, store units back,
`Starting frognet-tunnel-daemon ... (mode lan-only)`, no fdf unit loaded or on disk, no wg interface.
`bash /usr/local/bin/installer/frognet_install.sh --uninstall` (g5-uninstall.txt) → relocates, runs the reset,
`removed 187 recorded FrogNet file(s)`, rules restored, units put back, `crontab: FrogNet lines removed`, froguser and
/opt/fdf removed, rc=0. Left (leftovers.sh): no frognet-*/fdf-* unit, crontab = the operator's line only, no froguser,
dispatcher.d = the distribution's, no FrogNet directory or file in /etc, /opt, /var, /usr/local except the eight pip
console scripts (below), dnsmasq/apache2/mariadb back to disabled/inactive (not installed before), resolv.conf
`nameserver 127.0.0.11 ...` exactly as before, NAT rules exactly as recorded. **Name resolution did not work on m1**,
and the reason is the container: docker's embedded resolver moved to new ports when m1 was restarted (now 51811/40847;
the recorded and restored DNAT points at 39455/36949). (Before the fix that restores empty tables too, FrogNet's
filter rules also survived; n1/n2 show the earlier runs.)
- m2, the final installer and reset, LAN-only, uninstalled right after its install with no container restart in
  between (g5-m2-install.txt, g5-m2-uninstall.txt): install EXIT 0, F6 all passed, `recorded the machine's
  packet-filter rules before FrogNet (6 v4 / 0 v6 rules)`; `--uninstall` rc=0, no step failed, `packet-filter rules
  (IPv4): the machine's again`, crontab = the operator's line only, no froguser, no FrogNet unit file, hook or
  directory; resolv.conf `nameserver 127.0.0.11 ...` as found; **`getent hosts archive.ubuntu.com` →
  185.125.190.83** (before the install: 91.189.91.82). One `frognet-dbhost-advertise.service not-found failed` was
  still listed by systemd (it had failed before its file went): the reset now ends phase 5 with
  `systemctl reset-failed` (checked by hand on m2: 0 listed).
- n2, uninstalled straight from region membership (g5-n2-uninstall-from-region.txt): phase 0 ran fdf-leave, then the
  same removals; rc=0. (n2 was installed before iptables were recorded: its DNS stayed broken, as the record predicts.)

Which tree each gate ran: G1, G2 (15:01) and G3's n1/n2 (15:47) ran the installer before the iptables/fstab record,
the E1 message and the last reset fixes; G4 (15:48) had everything but those reset fixes; m1 (16:05) everything but the
empty-table restore; m2 (16:18) the final installer and reset (only `reset-failed` came after). m2 is therefore also
the clean LAN-only install (F6) on the final installer.

**T3b gates** (host; FROGNET_DIR = this tree)
| gate | T3b | T4a |
|---|---|---|
| `python3 -m discovery selfcheck` | 14/14 | **14/14** (selfcheck.txt) |
| `discovery/run_discovery_oracles.py` | 83 pass + test_license (42 files) | **83 pass + test_license (39 files: the three deleted fdf units were three of them)** (oracles.txt) |
| `simulation/run_all.py` | 3 red: DDP, aiconnect, discovery | **the same 3 red** (runall.txt) |
| netns-region-loop-test.sh | PASS 13 + 1 SKIP | **RESULT PASS, 13 PASS + 1 SKIP** (region.txt) |
| region-loop NOKEY=a | PASS | **RESULT PASS** (nokey.txt) |
| netns-lan-only-test.sh | PASS 19/19 | **RESULT PASS, 19/19** (lanonly.txt) |
| netns-ponds-daemon-test.sh | PASS 21 | **RESULT PASS, 21** (ponds.txt) |
| netns-reach-test.sh (BRIDGE_KT, FDF_TUN) | PASS 70 | **RESULT PASS, 70** (reach.txt) |
| static-no-broker.sh | 0 violations, 207 allowed | **0 violations, 207 allowed** (static-no-broker.txt) |
| bash -n | | **25 changed/new shell files OK**; opt/fdf copies cmp-identical (syntax.txt) |
The region-loop and reach scripts exit 1 after printing RESULT PASS (their last line's test; RESULT is the verdict). The netns harness needed
bridged frames accepted on this host while dockerd runs (above); without that the first region-loop run here failed
10 checks on "no answer: urlopen error timed out".

## Found, not done

- System pip puts its console scripts in /usr/local/bin (cbor2, dotenv, gpsdclient, gunicorn, gunicornc, httpx,
  idna, normalizer — 8 on a fresh box); --uninstall leaves them with the pip packages, as it leaves apt's. The one
  mechanism would be the services on the venv C2 already builds (the same packages twice now: system pip and venv).
- The packet-filter restore is a snapshot: it puts back the rules as they were at install and would undo a later
  operator change. Removing exactly the rules setup_iptables adds (it is -C/-A idempotent) would leave others' rules
  alone; netfilter-persistent's rules.v4 (E6 saves FrogNet's plus whatever was there) is reloaded at every boot.
- discovery's resolv.conf always ends `nameserver 8.8.8.8` ([RESOLV_LOCAL_UPSTREAM_GOOGLE_V1] in resolv.py), against
  manageResolv.bash's [DNS_NEXTHOP_ONLY_V1] "no hardcoded 8.8.8.8".
- 90-frognet-merge (the tree's, now the only hook) itself has two paths: the merge watcher's accumulator, or a direct
  `runMerge.bash &` when the watcher is not running — and fdf-region-join stops the watcher, so on a region node the
  second is the path.
- The node .run still carries the "FrogNet region patch" (39 FrogNet files). From this repo they are identical to what
  the installer laid down; a .run built from an older tree would overwrite newer files.
- frognet_system_tune.sh overwrites the distribution's /etc/apache2/mods-available/mpm_prefork.conf (not restorable
  on uninstall) and appends /tmp tmpfs to fstab (the record restores fstab on --uninstall only).
- After fdf-leave, /opt/fdf/var/facts.json is written again (by the daemon in LAN-only mode); nothing reads it there.
- frognet-dbhost-advertise.service fails until databasehost.frognet resolves (first minutes after a boot).
- demo_phase_{1,2}.bash, pipe_workload.py, ham_verify.sh, setup_worker.bash name 10.101.x addresses in usage/demo text.
- map_site on a RAM manager takes no credential: anyone who reaches RAM_PORT can set an area's key (fdf-region-create
  prints the firewall lines; the RAM manager listens on 0.0.0.0).
- T7 (John): the same G1–G5 on a Pi (aarch64 .run, Raspberry Pi OS: wireguard-go and the apt names may differ; a
  missing apt package is still only a warning in A2), and fdf-region-create on the real droplet only for a NEW region
  (it makes a new key; StreamingFrog's existing frognet region keeps its hand-made units and hub.env, which an update
  no longer touches).
