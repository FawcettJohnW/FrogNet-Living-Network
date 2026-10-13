# T7 — hardware acceptance: John's list, in the order to run it

Everything here was gated in containers (x86-64 Ubuntu 24.04) or network namespaces on 2026-10-13; see FINAL-GATES.md.
This list is for John's machines: StreamingFrog (the droplet, x86-64), the Raspberry Pis (aarch64, Raspberry Pi OS),
the phone (app a15) and the Windows PC. Every log line quoted below is one the code prints; the ones marked *(seen)*
were seen in this run's gates, word for word apart from names and addresses.

## Before step 1

- **The tree must be on GitHub first.** The cleanup is not committed; the public repo is still e889208, from before
  the cleanup. `git pull` / `git clone` on a Pi gets this tree only once it is committed and pushed. Check on any clone:
  `git log --oneline -1` shows the cleanup commit, and the installer then logs `FrogNet base: build=fdf-2026-10-12-r20
  commit=<that sha>`.
- **One file per machine type** (all in the checkout; sha256 of this build):

| machine | the one file | in the checkout | sha256 |
|---|---|---|---|
| StreamingFrog (x86-64 droplet) | `fdf-droplet.tgz` | `opt/fdf/dist/fdf-droplet.tgz` | `b5fc28cc703447ebfac8c075516be825e70d80e5106d2c5af4de1e345f6ffb9b` |
| Raspberry Pi | `fdf-install-aarch64.run` | `opt/fdf/dist/fdf-install-aarch64.run` | `dc4ae044fc64389efedc5713064f13b1c5300ae9827aa283d4337fd5bcdd011b` |
| x86-64 node | `fdf-install-x86_64.run` | `opt/fdf/dist/fdf-install-x86_64.run` | `77fb15748f033ebe10a15f7c55087fbc2c1ce1d0a1c82236685c6c997f4aec11` |
| Windows PC | `fdf-bridge.zip`, made from `opt/fdf/windows/bridge` (step 6) | — | — |
| phone | nothing: app a15 as it is installed | — | — |

  The FrogNet base on a Pi comes from its clone (`frognet_install.sh --from-repo`); the `.run` is in that clone too,
  so on a Pi with a clone nothing has to be copied. When a file is copied to a machine, it goes to `/tmp` and
  `sha256sum /tmp/<file>` must print the hash above.
- **`/tmp` on a FrogNet node is a tmpfs** (the installer's tune step puts it in fstab, 256 MB): a file staged there
  is gone after a reboot. Stage, then use, before rebooting. (Found in this run.)
- On StreamingFrog you are root (`ssh root@165.232.153.144`). On a Pi, commands run as the user who made the clone,
  from the clone's root (`~/FrogNet-Living-Network`), with `sudo` where shown. Nothing is redirected; read what it prints.

## 1. Back up the droplet, rotate the site key

The frognet site key was in git history (T1); rotating it makes a new one that never was. Members keep running until
their next registration, which the new key must reach first: run steps 1–3 in one sitting.

On StreamingFrog:

    cat /opt/fdf/BUILD
    systemctl list-units --all --no-legend 'fdf-*'
    cd / && tar czvf /tmp/streamingfrog-before-r20.tgz opt/fdf etc/systemd/system/fdf-*

From the PC: `scp root@165.232.153.144:/tmp/streamingfrog-before-r20.tgz .` (and a DigitalOcean snapshot of the
droplet, from its control panel, if you want the whole machine).

The new key, then the RAM manager told of it (the same request `fdf-region-create` makes; `frognet` is the area,
51888 the RAM manager's port in `fdf-ram-frognet.service`):

    (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | tee /opt/fdf/var/site-key-frognet); echo
    python3 -c 'import json,urllib.request; k=open("/opt/fdf/var/site-key-frognet").read().strip(); r=urllib.request.Request("http://127.0.0.1:51888/FawcettInnovations.RINA.ram_interface.php?op=rina", data=json.dumps({"op":"map_site","name":"frognet","key":k}).encode(), headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(r, timeout=5).read().decode())'

- Look for: the first command prints the 64-character key (that is `<NEWKEY>` below; it stays in
  `/opt/fdf/var/site-key-frognet`, mode 600); the second prints `{"ok":true}`.
- Pass: the backup tgz is on the PC, `{"ok":true}`, and `ls -l /opt/fdf/var/site-key-frognet` shows `-rw-------`.
- Until a member has the new key, its next registration says *(seen)*
  `fdf-client: mapping 10.X.Y.0/24 -> StreamingFrog:1:50 REFUSED by the region: HTTP 200: EPERM (authentication
  failed) = RINA_SITE_KEY in /opt/fdf/var/site.env is not the key map_site holds for this area (HMAC mismatch)`.
  Step 3 gives each region node the new key. Phones and PCs are leaves and need no key.
- Rehearsed here with area `lan`: both commands as above; the old key refused as quoted, the new one `registered`.

## 2. Update StreamingFrog to the r20 droplet tgz (or recreate it)

From the PC: `scp opt/fdf/dist/fdf-droplet.tgz root@165.232.153.144:/tmp/`. On StreamingFrog:

    sha256sum /tmp/fdf-droplet.tgz
    grep -E '^(RINA_RAM|FDF_AREA|FDF_HUB_NAME|FDF_ENDPOINT|FDF_LISTEN|FDF_FACTS)=' /opt/fdf/hub/hub.env
    cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub
    cat /opt/fdf/BUILD /etc/frognet/wg.impl
    journalctl -u fdf-hub -n 15 --no-pager
    journalctl -u fdf-client-frognet -n 5 --no-pager
    /opt/fdf/bin/fdf-ls 127.0.0.1:51888

- The `grep` must print six lines. The new `fdf-hub.service` passes `--facts ${FDF_FACTS}`; StreamingFrog's units
  already did, so its hub.env should have it. If `FDF_FACTS=` is missing, stop here and say so.
- Look for: `build=fdf-2026-10-12-r20` in BUILD; the hub records WireGuard once at this first start:
  `fdf-wg-impl: kernel (/sys/module/wireguard exists), recorded in /etc/frognet/wg.impl` or `fdf-wg-impl: kernel
  (modprobe -n wireguard: the module is there for <kernel>), recorded in /etc/frognet/wg.impl` (expected on the droplet:
  `kernel`; every machine here recorded `wireguard-go`, because this sandbox kernel has no module); `[FDF-HUB] start
  (adopt): members=N mappings=M peers=P prefixes=... build=fdf-2026-10-12-r20` *(seen)*; `fdf-client: build
  fdf-2026-10-12-r20; region at 127.0.0.1:51888 is build fdf-2026-10-12-r20` and `fdf-client: StreamingFrog joined the
  region at 127.0.0.1:51888, announced [...]` *(seen, with the test names)*.
- Pass: those lines, `fdf-ls` lists StreamingFrog `yes`, and `systemctl is-active fdf-ram-frognet fdf-client-frognet
  fdf-hub` prints `active` three times. The update keeps hub.env, the units and /opt/fdf/var: md5-identical before and
  after in G3.
- Or recreate (a new region, new key, replaces step 1's key; the hub's port is whatever you give):

      systemctl disable --now fdf-hub fdf-client-frognet fdf-ram-frognet
      rm /opt/fdf/hub/hub.env /opt/fdf/var/area-frognet.perm /opt/fdf/var/site-key-frognet
      cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload
      ip -o addr | grep '10\.253\.'
      ip route | grep '10\.253\.'
      /opt/fdf/bin/fdf-region-create frognet 165.232.153.144 --hub-port <FDF_LISTEN from the grep above>

  The two greps must print nothing (if they show the old `wg-frognet`: `ip link del wg-frognet`). Look for: `RAM
  manager: {"ok":true,"server":"fdf-2026-10-12-r20"}`, `== map_site frognet: {"ok":true}`, `start (adopt)`, the key
  printed once, rc 0. Since this run, a hub that does not start makes it stop with `fdf-region-create: fdf-hub /
  fdf-client-frognet did not start: journalctl -u fdf-hub -u fdf-client-frognet` (it used to say the region was held).

## 3. Upgrade one Pi in place with `--preserve`, then the rest

On the first Pi, before (write these down):

    cat /etc/fnid
    sudo md5sum /var/lib/frognet-tunnel/node_private.key
    sudo cat /opt/fdf/var/join.env

Then, from its clone:

    cd ~/FrogNet-Living-Network
    git pull
    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve

It asks the pond's FrogUser database password twice (the one the pond already uses), runs, and reboots after
"All checks passed." (with a failed check it asks first).
- Look for, in its output and `/var/log/frognet_install.log`: `frognet_install.sh build frognet-install-2026-10-12-t5
  starting on FrogNetHost.<NAME> - aarch64 - ...`; `--preserve: LAN interface <dev> from
  /etc/frognet/interfaces_override.conf`, `--preserve: node name '<NAME>' recovered from opts_only.conf (not
  prompting)`, `--preserve: gateway IP 10.X.Y.1 recovered from opts_only.conf (not prompting)` *(seen)*; `WireGuard:
  kernel (...), recorded in /etc/frognet/wg.impl` (Raspberry Pi OS has the module; if it says `wireguard-go`, say so);
  `FrogNet base: build=fdf-2026-10-12-r20 commit=<sha>`; `STEP C1c` with its removals and `C1c: retired state
  removed` *(seen)*; F6 `+` lines, `+  snapshot: mode=...`, `All checks passed.`
- After the reboot:

      cat /etc/fnid
      sudo md5sum /var/lib/frognet-tunnel/node_private.key
      sudo journalctl -b -u frognet-tunnel-daemon-v3 --no-pager | grep -E 'Starting frognet-tunnel-daemon|LAN-only|REGION switch'
      systemctl --failed

  The daemon says `Starting frognet-tunnel-daemon v3.0.0 build frognet-tunnel-2026-10-12-t5 (mode lan-only|region)
  base fdf-2026-10-12-r20`.
- A Pi that is in the region: run its `.run` with the new key (the upgrade does not touch FD&F; this brings r20 and
  the key):

      sudo bash opt/fdf/dist/fdf-install-aarch64.run <NEWKEY> 165.232.153.144:51888 frognet

  Look for *(seen)*: `== site.env: RINA_AREA=frognet RINA_SITE_KEY=<64 chars, ends ......>` (the last six of
  `<NEWKEY>`), `fdf-client: <NAME> joined the region at 165.232.153.144:51888, announced [...] with facts`,
  `fdf-client: mapping 10.X.Y.0/24 -> StreamingFrog:1:50 registered (site frognet)`, `REGION: my-channels:
  hubs=['StreamingFrog'] ...`, `endpoint: 165.232.153.144:<hub port>`, `latest handshake: N seconds ago`.
- Pass: fnid and the key's md5 the same as before; `systemctl --failed` lists nothing; a region Pi's daemon says
  `(mode region)` and the mapping line says `registered`. G4 here: identity kept, 4 failed units before, none after,
  and nothing left in /usr/local/bin that the tree lacks.
- Then the other Pis, the same way, one at a time.

## 4. A fresh Pi, installed LAN-only

Raspberry Pi OS, its uplink connected (the interface the default route uses), a wired interface to serve. On it:

    sudo apt-get install -y git
    git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git
    cd FrogNet-Living-Network
    sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name <NAME> --ip 10.X.Y.1 --no-ssid-projection

(Leave out `--no-ssid-projection` and give `--wifi-psk <the pond's passphrase> --country US` to project the node's
Wi-Fi; give `--lan-iface <dev>` when it picks the wrong wired interface.)
- Look for: `Detected: ...` naming the served interface and the uplink (the install stops at once with `FATAL D1:
  <dev> is the uplink (the default route uses it) ...` if the only wired interface is the uplink); `WireGuard:
  kernel (...)`; `[PASS] discovery selfcheck (all suites)`; `enabled: frognet-dbhost-elect.timer (LAN-only)`
  *(seen)*; `+  echo: <NAME>,10.X.Y.1,...`, `+  snapshot: mode=lan-only name=<NAME> build=fdf-2026-10-12-r20 ...`,
  `All checks passed.` *(seen)*. Time it: x86 took 5–6.5 min here; no Pi has been timed since the cleanup.
- After the reboot:

      sudo journalctl -b -u frognet-tunnel-daemon-v3 --no-pager | grep -E 'Starting frognet-tunnel-daemon|LAN-only'
      curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool
      sudo /usr/local/bin/frognet_monitor --once
      systemctl --failed
      sudo grep 'SERVICE_ELECT role=databasehost' /var/log/frognet-merge-watcher.log

  Look for *(seen)*: `LAN-only mode: no region switch (/opt/fdf/var/join.env: No such file or directory); no
  WireGuard`, `Starting frognet-tunnel-daemon v3.0.0 build frognet-tunnel-2026-10-12-t5 (mode lan-only) base
  fdf-2026-10-12-r20`, `LAN-only loop started: ...`; snapshot `"mode": "lan-only"`, itself in `members`, the database
  host under `elections`, `"errors": []`; `SERVICE_ELECT role=databasehost hosts=N bad=0 winner=... dbhost=...`.
- Pass: those lines; `systemctl --failed` empty; when it is on another node's LAN, each names the other in
  `getHosts.php` and both name the same database host (G2 here: `winner=10.72.72.1` on both, pings 3/3 both ways).

## 5. That Pi joins the region

From its clone:

    sudo bash opt/fdf/dist/fdf-install-aarch64.run <NEWKEY> 165.232.153.144:51888 frognet

- Look for: `== unpacking the node bundle into /`, the BUILD (`for=aarch64 FrogNet node (Pi)`), `== FD&F region view:
  http://<this node>/fdf/ (Apache, /opt/fdf/www/fdf)`, `fdf-wg-impl: kernel (recorded in /etc/frognet/wg.impl; kept)`,
  `== joining the region at 165.232.153.144:51888 (area frognet)`, `== region mode: frognet-dbhost-elect.timer stopped
  and disabled`, then the join lines of step 3, and in the daemon's journal `WG_KEY: made
  /var/lib/frognet-tunnel/node_private.key (none on disk); public key ... [DAEMON_OWNS_ITS_KEY_V1]` and `Starting
  frognet-tunnel-daemon ... (mode region) base fdf-2026-10-12-r20` *(all seen)*.
- Then:

      ping -c 3 -I 10.X.Y.1 <another region node's 10.A.B.1>
      curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool
      curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/fdf/

- Pass: 3/3 (G3: 3/3, 1–1.5 ms between two containers; across the internet expect tens of ms); snapshot `"mode":
  "region"`, pond `frognet` forward, `hub` wg0 with a handshake age, the region's nodes in `members`, `"errors": []`;
  `/fdf/` 200. On StreamingFrog, `wg show` lists the Pi's key with `10.X.Y.0/24`.

## 6. The phone (app a15, unchanged) and the Windows PC join; calls through comms-media on a node

**Phone.** The app's built-in first pond is `frognet|165.232.153.144:51888`, which is this region; nothing to change.
Press "Start" (allow the VPN). Look for, in the app's log ("Copy log"): its first line `FrogNet member <build>`, `<name>
joined the region at 165.232.153.144:51888 as w:..., announced [...]`, and its tunnel through the hub; the region gives
it a 10.252.x.y address. Pass: the phone's browser opens `http://10.X.Y.1/` of a region node (the landing page).

**Windows PC.** On the Linux machine with the clone, from its root:

    python3 -m zipfile -c /tmp/fdf-bridge.zip opt/fdf/windows/bridge/*

Copy `/tmp/fdf-bridge.zip` to the PC and unzip it into `C:\fdf` (it holds `fdf-bridge.cmd`, `fdf-tun.exe`, `etc`,
`lib`, `runtime`). Copy `bin\amd64\wintun.dll` from wintun-0.14.1.zip (www.wintun.net) into `C:\fdf`. Edit
`C:\fdf\etc\ponds.txt`: replace the line `AREA=RAM-HOST:51888` with

    frognet=165.232.153.144:51888

Stop any old fdf-client.exe and hand-made WireGuard tunnel (the same key must not be in two tunnels). In a terminal
opened as Administrator: `cd C:\fdf` then `fdf-bridge.cmd`.
- Look for (README.txt): the ponds, `<PC name> joined the region at 165.232.153.144:51888 ...`, `bridge: forward via
  hub StreamingFrog as 10.252.x.y`. Refusals say why: `fdf-bridge: ... is still the example ...`, `wintun.dll is
  missing`, `run this from a terminal opened as Administrator`.
- From a second terminal: `fdf-bridge.cmd ask status` (each tunnel's handshake and bytes). Pass: a handshake, and
  `ping 10.X.Y.1` of a region node answers.

**Calls.** On one region Pi: `sudo /opt/fdf/bin/fdf-comms.sh` (foreground; Ctrl-C stops it). Look for `fdf-comms: the
Communicator on this node at 10.X.Y.1:8800 / 10.X.Y.1:8994, said in the pond at 165.232.153.144:51888`. On the PC:
`fdf-bridge.cmd ask comms` → `OK --ram H:P --media H:P`; then `comms-app.exe --name John <those two> --camera ... --mic
N` (comms-app.exe is not in the tree: build it per `opt/frognet_semantic/ribbit/examples/communicator/WINDOWS.md`,
or use the one you have). On the phone: "Communicator". Pass: the phone and the PC see each other in the
Communicator, and a call carries audio and video both ways. Never run here (the region-loop test skips that stage).

## 7. A node leaves

On one region Pi:

    sudo /opt/fdf/bin/fdf-leave

- Look for *(seen)*: `== fdf-leave, package=... build=fdf-2026-10-12-r20 ...`, `removed fdf-client.service`, `removed
  fdf-facts.service`, `removed /opt/fdf/var/join.env, site.env, ponds/, attention/, forward, forward.want,
  facts.json`, `re-enabled frognet-dbhost-elect.timer`, `restarted frognet-tunnel-daemon-v3: LAN-only (its log says
  'LAN-only loop started')`, `left the region: this node is LAN-only FrogNet`; in its daemon journal `LAN-only mode: no
  region switch ...`. On another region node: `GRAPH membership: went=[('<NAME>', '10.X.Y.1')] came=[]` in
  `journalctl -u frognet-tunnel-daemon-v3`. On StreamingFrog: `journalctl -u fdf-hub` says `deleted peer ... (not a
  member now)` and `fdf-ls 127.0.0.1:51888` no longer shows it alive.
- Pass: `ip -o link show type wireguard` on it prints nothing; its snapshot says `lan-only`; the others lose the route
  to its /24 via wg0; it keeps its key (`sudo md5sum /var/lib/frognet-tunnel/node_private.key` unchanged), so a later join
  announces the same one.
- To remove FrogNet from a machine completely instead: `sudo bash /usr/local/bin/installer/frognet_install.sh
  --uninstall` (G5: EXIT 0, everything FrogNet's gone, the operator's crontab line kept). Not on a Pi you keep.

## 8. The droplet restarts and everyone comes back

On StreamingFrog: `reboot`. Watch a region Pi meanwhile:

    sudo journalctl -u frognet-tunnel-daemon-v3 -u fdf-client -f

- Look for *(seen)*, while it is down: `REGION: lost; withdrew its routes and names, tore down 1 tunnel(s); waiting for
  the region`, `REGION: not reachable (fdf-events registered no watch (gone)); will keep trying every 5s, quietly`,
  `fdf-client: the session to 165.232.153.144:51888 ended: ...`; when it is back: `fdf-client: <NAME> joined the
  region ...`, `fdf-client: mapping ... registered (site frognet)`, `GRAPH membership: went=[] came=[...]`, `REGION:
  my-channels: hubs=['StreamingFrog'] ...`. On StreamingFrog after boot: `systemctl is-active fdf-ram-frognet
  fdf-client-frognet fdf-hub` three times `active`; `journalctl -u fdf-hub -b` has `fdf-wg-impl: kernel (recorded in
  /etc/frognet/wg.impl; kept)` and `start (adopt): members=...`.
- Pass: with nothing typed on any node, pings across the hub answer again; G3 here: 0/2 while the host was off the
  network, 2/2 both ways 15 s after it was back, and a 40 s RAM-manager stop re-joined with one fdf-client restart.
  Time it from the droplet answering ping. The phone and the PC come back the same way (the bridge says `session
  ended: ...` and then joins again); if either does not, that is a finding.

## 9. Two regions at once on one PC

A second region is needed. Make it on a LAN Pi now (9a), the same way step 10 uses it; then the PC joins both.

**9a. A LAN Pi as region host.** No single file does this on a Pi: `fdf-droplet.tgz` holds x86-64 binaries, and the
aarch64 `.run` lacks `fdf-hub.service` and always joins a region. So, on a FrogNet Pi (installed, LAN-only, not
joined to any region), from its clone, with the `.run` and the unit both from the clone:

    cd / && sed '1,/^__PAYLOAD_BELOW__$/d' ~/FrogNet-Living-Network/opt/fdf/dist/fdf-install-aarch64.run | sudo tar xvzf - opt/fdf
    cat /opt/fdf/BUILD
    sudo cp ~/FrogNet-Living-Network/opt/fdf/src/fdf-source/deliverables/etc-droplet/systemd/system/fdf-hub.service /etc/systemd/system/
    sudo systemctl daemon-reload
    ip -o addr | grep '10\.253\.'
    ip route | grep '10\.253\.'
    sudo /opt/fdf/bin/fdf-region-create lan 10.P.Q.1

(`10.P.Q.1` = that Pi's own .1, the address the other nodes reach it at; `lan` = the region's name, at most 12
characters.) Then let a second pond be reached (by default a region made this way publishes `reach none, map
refused`, and a PC reaching it as a second pond is refused):

    sudo mkdir -p /etc/systemd/system/fdf-ram-lan.service.d
    printf '{"reach":"all","map":"allowed"}\n' | sudo tee /opt/fdf/var/net-lan.json
    printf '[Service]\nEnvironment=RINA_NET_CONFIG=/opt/fdf/var/net-lan.json\n' | sudo tee /etc/systemd/system/fdf-ram-lan.service.d/net.conf
    sudo systemctl daemon-reload && sudo systemctl restart fdf-ram-lan fdf-client-lan fdf-hub
    sudo journalctl -u fdf-ram-lan --no-pager | grep NET_CONFIG

- Look for: the tar lists `opt/fdf/...` (60 files) and `cat /opt/fdf/BUILD` says `for=aarch64 FrogNet node (Pi)`; the
  greps print nothing; create's `RAM manager: {"ok":true,"server":"fdf-2026-10-12-r20"}`, `== map_site lan:
  {"ok":true}`, `[FDF-HUB] start (adopt): ...`, the key printed once, rc 0 *(seen, x86)*; the last line
  `rina-host: [NET_CONFIG_IS_A_TUPLE_V1] (pond, config, net) = {"area":"","reach":"all","map":"allowed",...}`
  *(seen)*. The hub is named `FrogNetHost` (the short hostname of every FrogNet node).
- A second Pi joins it, from its clone: `sudo bash opt/fdf/dist/fdf-install-aarch64.run <LANKEY> 10.P.Q.1:51888 lan`
  (`<LANKEY>` = the key create printed). Use a Pi that is not in `frognet`: the `.run` makes `lan` its region; a node
  in two ponds at once is `fdf-pond-add`'s job (netns ponds-daemon PASS here, never on hardware).
- Rehearsed on x86 (two FrogNet containers): create, join, `registered`, handshake. The net.json drop-in on a separate
  region host. Not run: a Pi; this hub carrying traffic between two of its members.

**9b. The PC in both.** `C:\fdf\etc\ponds.txt`:

    frognet=165.232.153.144:51888
    lan=10.P.Q.1:51888

(the first line is the forward pond). Ctrl-C the bridge if it runs, then `fdf-bridge.cmd` again (Administrator).
- Look for (README.txt): both joins; `bridge: forward via hub StreamingFrog as 10.252.x.y`; `[lan] reach into lan
  (all, map allowed) as <addr> there ...; aliased 100.64.a.0/24 -> 10.P.Q.0/24` (or `REACH REFUSED into lan: <why>`:
  the net.json step did not take).
- Pass: `fdf-bridge.cmd ask status` shows two tunnels with handshakes; a node of `frognet` answers at its 10.x address
  and a node of `lan` at its 100.64.a.x alias; `fdf-bridge.cmd forward lan` brings `lan` forward and both still answer.
  The member code for this passed netns reach 70/70 here; the bridge itself has not run on Windows since the cleanup.

## 10. A region with no internet (RAM manager and hub on a LAN Pi)

The region `lan` from 9a, with the internet taken away: unplug the uplink of every machine in it, or the router's WAN
cable, so nothing reaches 165.232.153.144.
- On a member Pi of `lan`:

      ping -c 3 165.232.153.144
      sudo wg show
      systemctl is-active fdf-client fdf-facts frognet-tunnel-daemon-v3
      curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool
      sudo systemctl restart fdf-client
      sudo journalctl -u fdf-client -n 5 --no-pager

  and on the region-host Pi: `/opt/fdf/bin/fdf-ls 127.0.0.1:51888`.
- Look for: the ping fails (the internet is really gone); `wg show` endpoint `10.P.Q.1:50889` with a recent handshake;
  `active` three times; snapshot `"mode": "region"`, pond `lan` with ram `10.P.Q.1:51888`, `"errors": []`; after the
  restart `fdf-client: <NAME> joined the region at 10.P.Q.1:51888 ...` and `mapping ... registered (site lan)`
  *(seen)*; fdf-ls lists the host and the member alive.
- Pass: those, with no internet for the whole check; with two members of `lan` at different LANs, a ping between
  their .1s answers through the hub (not shown here: the rehearsal had one member).
- Rehearsed on x86: both machines off the docker uplink, no default route; 60 s later the handshake had renewed, the hub
  edge answered 2/2, a fresh re-join registered.

## Never run here

- arm64: the aarch64 `.run` was rebuilt and its payload compared file by file with its sources; it was never executed.
  Nor was Raspberry Pi OS, its apt names, or the install time on a Pi.
- Kernel WireGuard: every machine here recorded `wireguard-go` (this kernel has no module).
- Real Wi-Fi: SSID projection, hostapd, a node joining another node's Wi-Fi as its uplink (G2 used wired links).
- A real phone: no APK was built (no Android SDK); app a15 as John has it.
- Windows: the bridge, wintun, `comms-app.exe`; two ponds on one PC (the same member code passed netns reach 70).
- The real StreamingFrog: its hand-made units and hub.env, its update, its reboot.
- A Communicator call (`fdf-comms.sh` on a node; the region-loop test skips that stage).
- A FrogNet Pi as region host carrying traffic between two members (rehearsed on x86 with one member).
- `--preserve` on a node that is a region member (G4 was LAN-only); a bare cable between two nodes (transit-boot's
  positive case); DNS through a real router (the docker gateway serves none).

## Open code questions (from the cleanup records and this run)

- `map_site` takes no credential: anyone who reaches the RAM manager's port can set an area's key (T4a); the firewall is all.
- In LAN-only mode a neighbour leaving is not an event: its role and hosts lines stay until some other event merges (T6a).
- DNS: dnsmasq's only upstream is the default route's next hop, no public resolver, so a gateway that is not a resolver means no outside names (T4b); `live._dns_upstream` still chains exit_host.tsv then the gateway (T4c).
- `fdf-region-create` has no way to set the pond's net config: every region it makes publishes `reach none, map refused` until `RINA_NET_CONFIG` is given (this run; step 9a).
- No one file makes a Pi a region host: the droplet tgz is x86-64 only and the aarch64 `.run` has no fdf-hub.service (this run).
- The installer changed after T5 (the elector on LAN-only, ce72a41) and still logs `frognet-install-2026-10-12-t5` (this run).
- `core/frognet_secret.py` still derives from a published default secret when none is set (CRITICAL logged each time) (T6a).
- The post-merge elector rewrites only the control's /etc/hosts; a non-control node that elected over a partial store waits for its own next merge (T6a).
- `/etc/database_ip` keeps its install-time value while `databasehost.frognet` moves (T4a; seen again on n1 here).
- Timers left: the region daemon re-probes capability every 600 s in its loop, `frognet-dbhost-elect.timer` 1/min in its window, `registerFrogNetHost.bash` every 15 min (T4b, T6a).
- One setting, several sources: the daemon's region settings (tunnel.conf, env, join.env/site.env), `getEth0Address` (LAN dev, else opts_only.conf), `iproute.tear_down_wg` (wg-quick down, else ip link del), the merge hook (the watcher, else `runMerge.bash &`), `fdfram.where()` (env, else join.env), `fdf-reach ram_of` (T4b, T4c).
- A "dead" uplink means the next hop does not answer, not that the internet is gone; `FDF_UPLINK_PING` judges further (T4b).
- The uninstall's packet-filter restore is a snapshot (it would undo later operator rules); the tune step overwrites mpm_prefork.conf and adds /tmp tmpfs to fstab (T4a).
- The node `.run` carries a FrogNet region patch: a `.run` from an older tree overwrites newer files (T4a).
- A region host keeps files a newer droplet tgz no longer has: there is no prune there (T6a).
- The Android app's built-in first pond is John's droplet, `Identity.kt:36` (an app rebuild) (T4b).
- `getFrogNet.bash` answers `hostname -s` when getOurDomain times out; `mapInterfaces` names `eth0` when no wired dev qualifies (T4c).
- A FrogNet node as region host names its hub `FrogNetHost` (`hostname -s`) (this run).
- After `fdf-leave`, `/fdf/` still answers 200 and `/opt/fdf/var/facts.json` is written again (this run; T4a).
- Held requests: each open FD&F page holds an Apache worker in waitParticipants.php (T5b).
- `databasehost.conf` (*:80) and the *:443 vhosts are enabled but unreachable (Apache listens on 8080/8443) (T5).
- `var/www/html/{datalogger.py,Sense_Logger_2.py,frognet_gateway.py}` name `/usr/local/bin/frognet_env/bin/python3`, which exists nowhere (T4b).
- `discovery/propagate.py` (the simulator's model) still models the /etc/hosts scan the real script dropped (T4b).
- The Croakus and site/ still describe the broker in chapters 19–22 and "From Clean to the Communicator" (T6a).
- The C++ and C# monitors are not built on nodes (T5).
- `rinab/tools/dashboard-check.sh` was never run (T5b).
- Comments in core/ still name "the advertiser" as a writer of capability rows (T6a).
