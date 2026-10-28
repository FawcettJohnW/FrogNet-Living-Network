# Operating FrogNet

This is the operator's guide: what to install where, how to install, upgrade, leave and remove it, how to see what
is running, and what the logs say when something is wrong. Every command here names the machine it runs on. Paths
that start with `usr/`, `opt/` or `docs/` are in the repository checkout; paths that start with `/` are on the machine.

Build ids at the time of writing (each program logs its own; see "What is running"):
installer `frognet-install-2026-10-12-r20`, tunnel daemon `frognet-tunnel-2026-10-12-r20`, FrogNet base and FD&F
`fdf-2026-10-12-r20`.

## 1. What FrogNet is

FrogNet is a self-forming network of Linux machines (nodes). Each node serves its own LAN, 10.X.Y.1/24.
Nodes that can reach each other find each other, name each other and route to each other's LANs with no
configuration beyond the node's name and its 10.X.Y.1.
Above the network, the nodes share one memory (Ribbit): programs write and read values instead of sending messages.
A node runs in one of two modes, and its tunnel daemon's log says which:

- **LAN-only.** The nodes on connected LANs find each other (discovery), elect one database host, and route to each
  other's /24s. No WireGuard, no region, nothing outside the LANs is needed.
- **Region.** The node is also a member of an FD&F region: it keeps a session to the region's RAM manager and one
  WireGuard link to the region's hub (fdf-hub), and the hub carries traffic between members at different sites. A
  region needs no internet: the region host only has to be reachable by the members (a LAN address works).

The switch between the two is one file on the node, `/opt/fdf/var/join.env` (written by `fdf-region-join`,
removed by `fdf-leave`).

### What runs where

| unit | node, LAN-only | node, region | region host |
|---|---|---|---|
| `frognet-proxy` (HTTP :80) | yes | yes | — |
| `frognet-daemon` (data daemon :9009) | yes | yes | — |
| `frognet-tunnel-daemon-v3` | yes, `mode lan-only` | yes, `mode region` | — |
| `frognet-merge-watcher` (runs `runMerge.bash` on requests) | yes | yes | — |
| `frognet-sysperf`, `frognet-gps`, `frognet-transit-boot` (once at boot: a direct cable to another node becomes a 10.253.253.x/30 link) | yes | yes | — |
| `frognet-dbhost-elect.timer` (1/min; works only on the control, the highest .1, for 10 min after a merge) | yes | stopped and disabled by `fdf-region-join`; `fdf-leave` enables it again | — |
| `apache2` (:8080, :8443), `mariadb`, `dnsmasq`, `NetworkManager`, `cron` | yes | yes | — |
| `fdf-client` (the node's session to the RAM manager), `fdf-facts` | — | yes | — |
| `fdf-ram-<AREA>` (the RAM manager, `rina-host`, TCP 51888) | — | — | yes |
| `fdf-hub` (WireGuard hub, UDP 50889, interface `wg-<AREA>`) | — | — | yes |
| `fdf-client-<AREA>` (the hub's own membership) | — | — | yes |

Who elects the database host. LAN-only: every merge publishes the node's capability to the control and elects
(`SERVICE_ELECT role=databasehost ... winner=...` in `/var/log/frognet-merge-watcher.log`). A neighbour's capability
can reach the control after the control's merge has elected; for that, `frognet-dbhost-elect.timer` runs the same
election on the control once a minute for 10 minutes after each merge and, when the answer changes, rewrites
`/etc/hosts` and reloads dnsmasq (`elector: databasehost re-elected` in `journalctl -u frognet-dbhost-elect`). In a
region the tunnel daemon elects from the members' facts and the elector does nothing.

A region host is not a FrogNet node: it needs `/opt/fdf` (from `fdf-droplet.tgz`), `python3`, `wireguard-tools`,
and `wireguard-go` where its kernel has no WireGuard module. One machine holds one region.

## 2. Install a node, LAN-only

**The machine.** Ubuntu 24.04 (x86-64 is what was tested; see section 6), two network interfaces: the uplink (the
interface the default route uses: your router, DHCP) and a wired interface the node serves its LAN on. The installer
never serves the uplink: it stops before installing anything if the only wired interface is the uplink.

**The checkout.** On the machine, as a user with sudo:

    sudo apt-get install -y git
    git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git
    cd FrogNet-Living-Network

**The install.** From the checkout's root:

    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name NODE1 --ip 10.61.61.1 --no-ssid-projection

`--name` is the node's name (letters, digits, `_`, `-`). `--ip` is the node's own address, 10.X.Y.1; it serves
10.X.Y.0/24. Each node needs its own X.Y. `--no-ssid-projection` is for a machine whose Wi-Fi does not project the
node's own Wi-Fi network (or that has no Wi-Fi); leave it out to project it, and give `--wifi-psk` or answer the
prompt. The installer asks for the database password (below), then runs to the end and reboots. Options the
installer takes (`--help` prints all of them):

| option | what it does |
|---|---|
| `--lan-iface IFACE` | the wired interface to serve; default: the first wired interface that is not the uplink |
| `--db-pass PASS` | the FrogUser database password; every node in the pond uses the same one. Omitted: asked twice, hidden |
| `--no-ssid-projection` | do not project the node's Wi-Fi network. Use it on a machine with no Wi-Fi radio that can be an access point |
| `--wifi-psk PSK`, `--country CC`, `--ap-iface IFACE` | the projected Wi-Fi network's passphrase (8–63 characters, the same on every node of the pond), regulatory country, radio |
| `--no-reboot` | stop at the end instead of rebooting |
| `--preserve` | upgrade in place (section 4) |
| `--region RAM_HOST:PORT --site-key KEY --area NAME` | also join a region (section 3) |
| `--rebuild-venv` | rebuild the Python environment the services run on, even when the existing one fits |
| `--uninstall [--keep-identity] [--keep-db]` | remove FrogNet (section 4) |

**What it asks** (each only when the option is not given): the database password (twice, hidden: every node of
the pond uses the same one), the node name, the 10.X.Y.1, "Continue?" when that address already answers a ping
(without `--ip`), the Wi-Fi passphrase when Wi-Fi projection is on, and "Confirm?" unless both `--name` and `--ip` were
given. Projection is on unless `--no-ssid-projection` is given or the wired interface has no cable and no Wi-Fi radio
can be an access point; when the wired interface has no cable and a radio can, it asks whether to project, which
radio, and the country.

**How long.** On the x86-64 test machines (containers on one host) the install took 5 to 10 minutes from the first
log line to the last (T4c–T5b records; 5 min 29 s when this section was followed for T6a). Most of it is apt and the Python packages, which `pip` compiles where no
wheel exists. A Raspberry Pi has not been timed since this cleanup.

**What it checks (phase F6).** Before the reboot the installer verifies 26 things and prints `+` or `x` for each:
the hostname; mariadb running; apache2 enabled, started and running; dnsmasq, frognet-proxy and frognet-daemon
enabled; systemd-resolved not active; the FrogNet database and the FrogUser login; the interface override file;
IP forwarding; the certificate and its key; the sudoers file and its syntax; qrencode; runMerge.bash; the SSID
projection file; gateways.conf and the dnsmasq file naming the node; one NetworkManager merge hook; the LAN
interface is not the uplink. Then it asks the node itself: `echo:` (frognet_echo.php must answer
`<NAME>,<.1 or empty before the reboot>,...`) and `snapshot:` (getSnapshot.php must answer with a mode). It ends
with `All checks passed.` or `N check(s) failed.` A failed check is not fatal by itself: without `--no-reboot` the
installer asks whether to reboot anyway. The whole log is `/var/log/frognet_install.log`.

**After the reboot.** The services start, the NetworkManager hook asks the merge watcher for the first merge, and
the LAN comes up on the served interface. Look at:

    journalctl -b -u frognet-tunnel-daemon-v3 --no-pager | grep -E 'Starting frognet-tunnel-daemon|LAN-only'
    curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool
    /usr/local/bin/frognet_monitor --once
    systemctl --failed

The daemon says `Starting frognet-tunnel-daemon v3.0.0 build ... (mode lan-only) base fdf-2026-10-12-r20` and
`LAN-only loop started`. The snapshot says `"mode": "lan-only"`, lists the node itself among `members`, and names the
database host under `elections`. `systemctl --failed` lists no unit.

**A second node.** Install it the same way with its own 10.X.Y.1, its uplink on the first node's LAN (it takes a
lease from the first node, which is then its default route) and its served LAN on another wired interface
(`--lan-iface` when there is more than one candidate). After both have rebooted, each names the other in
`getHosts.php` and `/etc/hosts` (the snapshot's members are the same rows), routes to the other's /24, and both name
the same database host. That is the two-node test of this cleanup (T4b, G2).

## 3. Regions

A region is joined by invitation. The region host's RAM manager listens only inside the hub's WireGuard tunnel
(10.253.253.254); a member reaches the region only after the hub has its key. The hub has a member's key only when the
operator has invited that member. An invitation is one line, `fdfinv1.<...>`. It is a secret: it holds the member's
private key and the region's site key. There is no password and no account; a machine that reaches the region was
invited, and that is the trust.

The order:

1. Set up the region host (`fdf-region-create`).
2. Invite the first member on the region host (`fdf-invite NAME`) and install it with that line. The first member is
   a desktop: a FrogNet node, a Linux machine with the core bundle, or a Windows PC with WSL.
3. Every later member gets its line from any machine already in the region (`fdf-identity NAME`). Give the line to
   that machine's installer, or to the phone or Windows PC.

### Create a region (on the region host)

The region host is any Linux machine the members can reach: a public address for members at other sites, a LAN
address for a region with no internet. Copy `opt/fdf/dist/fdf-droplet.tgz` from the checkout to the region host's
`/tmp`, then on the region host, as root:

    apt-get install -y python3 wireguard-tools
    modprobe -n wireguard || apt-get install -y wireguard-go
    cd / && tar xvzf - < /tmp/fdf-droplet.tgz
    systemctl daemon-reload
    ip -o addr | grep '10\.253\.'
    ip route | grep '10\.253\.'
    /opt/fdf/bin/fdf-region-create myarea 203.0.113.7

The two `grep`s must print nothing: the hub uses 10.253.0.0/16 on its interface. `myarea` is the region's name
(letters, digits, `.`, `_`, `-`, at most 12); `203.0.113.7` is the address members reach this machine at.
`fdf-region-create` records the WireGuard implementation (kernel, or wireguard-go where the kernel has no module),
makes the site key (stored root-only in `/opt/fdf/var/site-key-myarea`), makes the hub's interface `wg-myarea`,
starts `fdf-ram-myarea` (the RAM manager, on 10.253.253.254:51888, inside the tunnel), maps the site key on it, writes
`/opt/fdf/hub/hub.env`, and starts `fdf-hub`, `fdf-client-myarea`, `fdf-identity` (it answers `fdf-identity` from the
members) and `fdf-chat@51888`. It prints what each said, then the firewall lines:

    ufw default deny incoming
    ufw allow 50889/udp
    ufw route allow in on wg-myarea out on wg-myarea
    ufw allow in on wg-myarea to 10.253.253.254 port 51888 proto tcp

Run them (or run `fdf-region-create` with `--ufw`) where the region host has ufw. Otherwise open UDP 50889 to the
members and let the host forward between the hub's peers on `wg-myarea`. From the internet the host shows one UDP port,
and that port answers nothing to a key the hub was not given. `--ram-port` and `--hub-port` change the two ports. A
second `fdf-region-create` on the same machine stops: a machine holds one region.

`fdf-hub` refuses to start before `fdf-region-create` has run; its journal says
`fdf-hub: /opt/fdf/hub/hub.env does not exist: this machine holds no region yet. Run: /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP`.

Update the region host to a newer `fdf-droplet.tgz` (hub.env, the site key, the admitted members, the units and
`/opt/fdf/var` are kept):

    cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-myarea fdf-client-myarea fdf-hub fdf-identity

### Invite a member

On the region host, as root, for the first member (and for any member, whenever the region host is at hand):

    /opt/fdf/bin/fdf-invite NODE1          a node or desktop
    /opt/fdf/bin/fdf-invite PHONE1 --leaf  a phone or Windows PC (a leaf: an address, no subnet)

It makes the member's WireGuard key and gives it an edge address (10.253.X.Y). It admits the member
(`/opt/fdf/hub/admitted/NODE1.json`), so the hub has the peer at once, and prints the invitation once.
`fdf-invite --list` shows who is admitted. `fdf-invite NODE1 --revoke` removes the peer now.

On any machine already in the region (a node, a core, the region host), as root, without the region host:

    /opt/fdf/bin/fdf-identity NODE2
    /opt/fdf/bin/fdf-identity PHONE2 --leaf

It asks the region through the shared memory and prints the same kind of line. A name that already has an identity is
refused. `--again` replaces it, and the old key stops working.

### Install a node into a region

Install as in section 2, with the invitation:

    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name NODE1 --ip 10.81.81.1 --no-ssid-projection --invite fdfinv1.<...>

The installer checks before phase A that `opt/fdf/dist/fdf-install-$(uname -m).run` is in the checkout and that the
invitation decodes. After F6 (and only when F6 passed) its phase R runs that file with the invitation. A node already
installed LAN-only joins with the same file:

    sudo bash opt/fdf/dist/fdf-install-x86_64.run fdfinv1.<...>

(`fdf-install-aarch64.run` on a Raspberry Pi.) It unpacks `/opt/fdf` and the FrogNet region modules, installs the
sudoers fragment the snapshot needs, serves the region view at `/fdf/` on the node's Apache, and runs
`/opt/fdf/bin/fdf-region-join`. That writes the invitation's key (`/var/lib/frognet-tunnel/node_private.key`), the
hub (`/opt/fdf/var/hub.json`), `site.env` and `join.env`. It then starts `fdf-client` and `fdf-facts`, restarts the
tunnel daemon into region mode (the hub tunnel first, then the region inside it), and shows the result for 15 s. The
file is re-runnable; running it again with a newer build updates the node. The older `--region`, `--site-key` and
`--area` options stop with a message: a region is joined by invitation.

### A Linux machine that is not a node: the core

`fdf-core-linux-<arch>-<build>.tgz` makes a laptop, a server, a VM or WSL a member, with no FrogNet node install. Copy
it to `/tmp`, then as root:

    cd /tmp && tar xzf fdf-core-linux-x86_64-<build>.tgz && cd fdf-core-<build>
    ./install --invite fdfinv1.<...>

Get the line from `fdf-invite NAME --leaf` or `fdf-identity NAME --leaf`. `install` looks first: it refuses a FrogNet
node (use the `.run`), the region host, and a machine of another processor, and says which bundle is right. It puts
the core in `/opt/fdf` (nothing in `/usr/local/bin`) and makes `fdf-core.service`. The service brings up the tunnel
from the invitation in user space (no kernel module, no wg tools) and joins the region inside it. Without
`--invite` the machine runs stand-alone: its own RAM manager on 127.0.0.1, for the add-ins alone.

On Windows the first member is WSL 2 with systemd. If systemd is off, `install` asks, then turns it on in
`/etc/wsl.conf` and offers to restart WSL.

### Add-ins

The Communicator (`fdf-addin-comms-...`), the kart race (`fdf-addin-kart-...`) and DisasterComm (`fdf-addin-dc-...`)
are separate bundles. Each installs on any machine with a core (a core bundle, a node, or the region host):

    cd /tmp && tar xzf fdf-addin-comms-linux-x86_64-<build>.tgz && cd fdf-addin-comms-<build> && ./install

Each add-in runs as `fdf-addin-<name>.service`, tied to the core's unit. It writes its own tuple to the control RAM,
`(pond, where, <member>/<add-in>)`, held by its own session, so the tuple goes when the add-in stops. Its `ADDIN` file
says what it does when the network goes (`on-loss=exit`, `rediscover` or `stay`).

### Leave a region

On the node, as root:

    /opt/fdf/bin/fdf-leave

It removes `fdf-client`, `fdf-facts`, `fdf-term-serve` and every `fdf-client@<area>`. It deletes `join.env`,
`site.env`, `hub.json` and the pond files, turns the database-host timers back on and restarts the tunnel daemon. The
daemon then logs `LAN-only mode: no region switch (/opt/fdf/var/join.env: No such file or directory)`. The member is
still admitted at the hub until `fdf-invite NAME --revoke`.

### Phones

The FrogNet Android app is in `opt/fdf/src/frognet-android` (`app/` is the app, `member/` the member protocol it
shares with the core and the Windows bridge, `tun/` fdf-tun, the packet engine). **No APK ships**: build it from that
directory with an Android SDK (API 34) and the Gradle wrapper, `./gradlew :app:assembleDebug`, or open the directory
in Android Studio. On the phone, paste the line from `fdf-identity PHONE --leaf` and press "Use invitation", then
"Start" (Android asks to allow the VPN). The region, the phone's key, name and address are the invitation's; the
tunnel comes up first and the phone joins the region inside it, and again at every boot.

A phone on a node's LAN or Wi-Fi with no app is an ordinary DHCP client of that node.

### Windows PCs

`fdf-windows-<build>.zip` (the same files as `opt/fdf/windows/bridge` in the checkout; `BUILD.txt` says the build):

1. Unzip to `C:\fdf`.
2. Download `wintun-0.14.1.zip` from https://www.wintun.net and copy `bin\amd64\wintun.dll` into `C:\fdf`. It is the
   one file not included.
3. Get the PC's line: `fdf-identity PC1 --leaf` on any machine in the region.
4. In a terminal opened as Administrator: `cd C:\fdf` then `setup.cmd fdfinv1.<...>`. It checks first, keeps the
   invitation and makes the scheduled task "FrogNet member" (at every startup, as SYSTEM, its output in
   `var\fdf-bridge.log`), and starts it. `setup.cmd remove` takes the task away.

`C:\fdf\README.txt` has the rest (`fdf-bridge.cmd ask status`, `ask comms`, `forward <pond>`).

### The Communicator

Install the Communicator add-in (above) on the machine that is to host it: a node, a core or the region host. Its
servers (`comms-ram`, TCP 8800; `comms-media`, TCP 8994) start with it. It writes
`(pond, where, w:<member>/comms) {"comms":{"ram":"w:<member>:8800","media":"w:<member>:8994"}}`, which lives as long
as its session. Clients find the servers from that cell: the phone's "Communicator" button, and on Windows
`fdf-bridge.cmd ask comms`, whose output is the `--ram ... --media ...` to give `comms-app.exe`. The desktop
Communicator is built from `opt/frognet_semantic/ribbit/examples/communicator` (`./build.sh` on Linux; `WINDOWS.md`).

## 4. Upgrade, leave, uninstall, reinstall

**Upgrade** (keeps the node's name, 10.X.Y.1, `/etc/fnid`, the tunnel daemon's key and the databases): update the
checkout, then from its root

    git pull
    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve

`--preserve` reads the name, address and LAN interface from the node's own files. Phase C1c removes what the tree no
longer ships (`usr/local/bin/frognet_prune_removed.sh --apply`, every section): retired units (stopped first),
scripts, web pages and configuration files; the old tunnel.conf keys, `RINA_RAM` and `RINA_AREA` included
(`join.env` is the region switch); old configuration backups that carried credentials; the console scripts an old
system `pip` put in `/usr/local/bin` when no OS package owns them. Each removal is logged; a removal that fails is
logged and the next install retries it. The upgrade that was tested is a LAN-only node (T4a–T4c, G4). On a region
node, run its `.run` again after the upgrade (it is re-runnable; T5b re-ran it on a member): that also brings the
newer FD&F build.

**Leave a region:** `/opt/fdf/bin/fdf-leave` (above).

**Uninstall:**

    sudo bash /usr/local/bin/installer/frognet_install.sh --uninstall

It runs `frognet_reset.sh --uninstall`: `fdf-leave` first when the node is in a region; every FrogNet unit, file and
directory, the files the install recorded laying down in `/usr/local` (and only those), the FrogNet crontab lines,
`froguser` when the install made it, `/opt/fdf` (unless the machine holds a region), the databases; and it puts back
what the install recorded before it changed anything (`/var/lib/frognet-install/os-before`): resolv.conf,
NetworkManager.conf, the packet-filter rules, hostname, hosts, fstab and the enabled/active state of the services it
touched. Packages installed by apt stay. `--keep-identity` keeps `/etc/fnid` and the daemon's WireGuard key;
`--keep-db` keeps the database. Its log is `/var/tmp/frognet_reset.log`; a step that failed is named at the end and
the exit status is 1. An install made before October 2026 has no record of the machine before FrogNet; the reset says
so and cannot put the name resolution back.

**Reinstall.** Running the installer without `--preserve` on a machine that has FrogNet resets it first (phase A0:
`frognet_reset.sh`, new identity, databases dropped) and installs onto the clean machine. To reinstall and keep the
node's identity, reset with `--keep-identity` first, from the checkout's root:

    sudo bash usr/local/bin/installer/frognet_reset.sh --keep-identity --yes
    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name NODE1 --ip 10.61.61.1 --no-ssid-projection

## 5. What is running

**On the node:**

| what | where |
|---|---|
| landing page (name, .1, mode, build; links) | `http://<node>/` (the proxy on :80) or `http://<node>:8080/` (Apache) |
| the node snapshot (build, mode, ponds, members, hub link, elections, errors): what the monitor and the dashboard draw | `http://<node>:8080/getSnapshot.php` |
| the dashboard (members, ponds, hub links, database host; drawn from the snapshot only) | `http://<node>/fln_dashboard32.html` |
| the region's members (region nodes only; a LAN-only node answers 403) | `http://<node>/fdf/` |
| the monitor, one frame as text / full screen | `/usr/local/bin/frognet_monitor --once` / `/usr/local/bin/frognet_monitor` |
| the region's members from the region host (it has no `join.env`, so give the RAM manager: 51888 or the `--ram-port` used) | `/opt/fdf/bin/fdf-ls 127.0.0.1:51888`, `RINA_RAM=127.0.0.1:51888 /opt/fdf/bin/fdf-monitor --once` |

**Build lines and logs** (each program says its build when it starts):

| component | build line | log |
|---|---|---|
| installer | `frognet_install.sh build frognet-install-2026-10-12-r20 starting on ...` | `/var/log/frognet_install.log` |
| reset / uninstall | `FrogNet reset (build frognet-reset-2026-10-12-t4a) on ...` | `/var/tmp/frognet_reset.log` |
| FrogNet base | `/opt/frognet_semantic/BUILD` (`build=fdf-2026-10-12-r20`, `installed=...`); F6 prints it | — |
| tunnel daemon | `Starting frognet-tunnel-daemon v3.0.0 build frognet-tunnel-2026-10-12-r20 (mode lan-only\|region) base fdf-2026-10-12-r20` | `journalctl -u frognet-tunnel-daemon-v3` |
| proxy | `[Proxy] build fdf-2026-10-12-r20 listening ...` | `journalctl -u frognet-proxy` |
| data daemon | `[Daemon] build fdf-2026-10-12-r20 ...` | `journalctl -u frognet-daemon` |
| merge | `runMerge INFO ... invoked_by who=watcher:poke ...` per merge | `/var/log/frognet-merge-watcher.log`; the last merge's trace `/run/frognet/debug/latest/trace.log` |
| FD&F on a node | `/opt/fdf/BUILD` (`build=fdf-2026-10-12-r20`); the `.run`, `fdf-region-join` and `fdf-leave` print it | `journalctl -u fdf-client -u fdf-facts` |
| RAM manager | `{"ok":true,"server":"fdf-2026-10-12-r20"}` (its `version` answer; `fdf-region-create` prints it) | `journalctl -u fdf-ram-<AREA>` |
| hub | `start (adopt) ... build=fdf-2026-10-12-r20` | `journalctl -u fdf-hub` |
| Windows bridge | `BUILD.txt`; every line it prints | its terminal |
| Android app | the first line of its screen, `FrogNet member <build>`; "Copy log" / "Share log" | the app |

## 6. Requirements and limits

- **Operating system.** Tested after this cleanup: Ubuntu 24.04 x86-64, in containers with systemd (install,
  two-node LAN-only, region, upgrade, leave, uninstall). Raspberry Pi OS on aarch64 (John's nodes) ran FrogNet in the
  field before this cleanup and has not been re-qualified after it. The installer uses apt; it is for Debian-family
  systems. The region host was tested as an Ubuntu 24.04 container with python3, wireguard-tools and wireguard-go.
- **WireGuard.** Chosen once per machine at install (phase A2b; `fdf-wg-impl` on a region host or an older node) and
  recorded in `/etc/frognet/wg.impl`: `kernel` when the kernel has the module, else `wireguard-go` (installed then).
  Everything that makes a WireGuard interface reads that file (the tunnel daemon, fdf-hub); an existing record is
  kept by later installs.
- **DNS.** The node's resolv.conf is `nameserver 127.0.0.1` (dnsmasq) and dnsmasq's only upstream is the default
  route's next hop. There is no public resolver behind it. The router the uplink points at must serve DNS; on a
  network whose gateway is not a resolver the node resolves no outside name.
- **Addresses.** A node is 10.X.Y.1 and serves 10.X.Y.0/24; every node needs its own X.Y. In a region, a /24 has one
  owner: the first live member to claim it; a later one is refused (`fdf-hub` logs `REFUSED <subnet> claimed by ...`).
  10.253.0.0/16 and 10.254.0.0/16 are not node /24s in a region (the hub's edges and its own address are in 10.253;
  `fdf-hub` logs `no edge for ...`), and leaves (phones, PCs) get 10.252.x.y addresses from the RAM manager.
- **Region names.** At most 12 characters (`wg-<AREA>` is an interface name).
- **Not measured.** No member count limit is written in the code; the largest region in the gates had three members
  (two nodes and the hub's own), the largest LAN-only pond two nodes.
- **The database password** is the same on every node of a pond (`--db-pass`).

## 7. Troubleshooting by symptom

| symptom | what the log says | what to do |
|---|---|---|
| the install stops at once | `[FATAL]   D1: eth1 is the uplink (the default route uses it) ...` or `[FATAL]   D1: no wired interface to serve the LAN on besides the uplink ...` | give the served interface with `--lan-iface`; a machine with one wired interface cannot be a node |
| the install stops in phase C2 | `[FATAL]   venv build failed for N of M package(s): ...` | the named packages did not build; the `FAILED:` lines above it say which, and pip's output why (usually a missing `-dev` package or no network) |
| `--region` stops before phase A | `ERROR: --region: .../opt/fdf/dist/fdf-install-<arch>.run not found` | the checkout lacks the `.run` for this machine's architecture |
| F6 `x  echo: ...` | `(want NAME,,...)` before the reboot is normal only when the LAN is not addressed yet; the line before it says `LAN address: ... (... not addressed yet ...)` | reboot; if it persists, `journalctl -u apache2` |
| the snapshot has errors, or no members, right after install | `snapshot: ... errors=['/usr/local/bin/makeHostJson.bash: no output']` | normal before the first merge (after the reboot) |
| a node's LAN has no address | `getEth0Address` prints `no 10.x address on the LAN dev <dev>` on stderr; the echo's .1 field is empty | `nmcli connection show`; the node's profile owns the address |
| no merges | `[MERGE-ACCUM] inotifywait not found (install inotify-tools): ...` in `/var/log/frognet-merge-watcher.log` | `apt-get install inotify-tools`, then `systemctl restart frognet-merge-watcher` |
| a node stays LAN-only after joining | `LAN-only mode: no region switch (...)` in the tunnel daemon's journal | `/opt/fdf/var/join.env` is missing: run the `.run` again |
| old settings are ignored | `old broker keys ignored: ...` in the tunnel daemon's journal | nothing: the upgrade's C1c removes them from tunnel.conf |
| a region node's /24 is not routed | `fdf-client: mapping 10.X.Y.0/24 -> ... REFUSED by the region: ... EPERM ... = RINA_SITE_KEY in /opt/fdf/var/site.env is not the key map_site holds for this area` in `journalctl -u fdf-client` | run the `.run` again with the key `fdf-region-create` printed |
| the hub does not start | `fdf-hub: /opt/fdf/hub/hub.env does not exist ...` | run `fdf-region-create` on that machine |
| two nodes with the same /24 in a region | `REFUSED 10.X.Y.0/24 claimed by ...` in `journalctl -u fdf-hub` | give one of them another 10.X.Y.1 (reinstall) |
| outside names do not resolve | resolv.conf has only `127.0.0.1` and the gateway | the gateway does not serve DNS (section 6) |
| `databasehost.frognet` still names a LAN-only node that is gone | the last `SERVICE_ELECT role=databasehost ... winner=` in `/var/log/frognet-merge-watcher.log` is from before it left | a LAN-only node does not notice a neighbour leaving; the next merge (an interface change, a neighbour's notification, or any file created in `/run/frognet/merge_requests`) elects again |
| the Windows bridge refuses | `fdf-bridge: ... is still the example ...`, `wintun.dll is missing`, `run this from a terminal opened as Administrator` | do what the line says |
| an old node's `.run` stops | `fdf-install: inotifywait is required and missing (apt-get install inotify-tools)` | `apt-get install inotify-tools`, run the `.run` again |
| the dashboard is empty | the page says `getSnapshot.php failed at HH:MM:SS: <why>` | Apache or the snapshot is not answering; the next refresh draws again when it does |
