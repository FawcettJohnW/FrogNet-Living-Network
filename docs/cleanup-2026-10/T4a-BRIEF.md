# T4a brief — a machine that is not John's: clean install (LAN-only and region), droplet setup, upgrade, leave

Tree /home/claude/clean (git; T0..T3b committed; read docs/cleanup-2026-10/T3a.md and T3b.md first). Edit in place,
do NOT commit. John's rules: no fallbacks (one mechanism; a failed step is logged and retried on the next event); never
redirect program output away from the terminal in commands given to John; nothing new in /usr/local/bin beyond FrogNet's
own files; every component logs its build; one file per machine type; code is the truth.

## A clean machine to test on (ready)
Docker works here (`dockerd` is running; if not: `(dockerd > /tmp/dockerd.log 2>&1 &)`). Image `frognet-clean:noble` is
a debootstrapped Ubuntu 24.04 minbase with systemd; apt reaches archive.ubuntu.com, pip reaches pypi. Debian mirrors
and Docker Hub are NOT reachable, and there is no arm64 emulation, so the clean-install gate is x86-64 Ubuntu 24.04
(say so in T4a.md; the Pi is John's T7). Boot one:
    docker run -d --name n1 --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run \
      --tmpfs /run/lock -e container=docker -v /home/claude/clean:/repo:ro frognet-clean:noble /sbin/init
    docker exec n1 bash -c 'cp -a /repo /root/FrogNet && cd /root/FrogNet && bash usr/local/bin/installer/frognet_install.sh --from-repo ...'
Use a user-defined docker network for multi-container tests (a "droplet" container + nodes). Remove containers after.

## Do
1. Fresh-box failures found by the T0 audit (fix each at the cause; verify in the container):
   - the venv build: mysqlclient and NetfilterQueue have no wheels — add their build deps (libmariadb-dev,
     libnetfilter-queue-dev, pkg-config, build-essential, python3-dev) to the A2 apt list; also `file`, `uuid-runtime`
     if used; A3 system pip failures must stop the install (they are what the services run on), not warn.
   - Apache: --from-repo ships etc/ssl/frognet-universal.crt without a key; make E5 create the key+cert pair when the
     KEY is missing (not only the cert), or stop shipping the cert — pick one, one mechanism.
   - F6 curls frognet_echo.php with Apache stopped since D2: start what F6 checks before it checks.
   - D1 defaults the served LAN to eth0 even when eth0 is the uplink; lillypad step 2 then deletes the uplink profile
     (drops SSH). Make the installer refuse to pick the uplink as the LAN (stop with a message naming the interfaces and
     the --lan-iface option to give), and add --lan-iface.
   - D7 writes its own old copies of 90-frognet-merge (and sysctl / MariaDB / logrotate) over the tree's newer files:
     install the tree's files instead. 99-ifup + 90-frognet-merge fire two merges per event: keep one.
   - `--from-repo` copies all of etc/ opt/ usr/ var/ onto the node, including opt/fdf/src (Android sources, rlite tgz),
     opt/fdf/dist, opt/fdf/windows, site/, docs/: copy only what a node runs. Write the node file set as an explicit
     list/rule in the installer (and use the same rule in frognet_build_release.sh's world tar, so both agree).
   - KNOWN_NODES table (installer ~1398-1405, John's boxes on 10.101.x) and other John-specific defaults: remove.
   - `-h` / `--help` prints every option.
2. Region, from the same installer: `frognet_install.sh ... --region RAM_HOST:PORT --site-key KEY [--area NAME]`.
   At the end (after F, before reboot), it runs the FD&F node installer for this machine's architecture from the repo
   (opt/fdf/dist/fdf-install-$(uname -m).run with the key/RAM/area). Without --region the node is LAN-only. A missing
   .run for the architecture stops with a message (no fallback). The .run/header.sh and fdf-region-join: the RAM
   address and area are REQUIRED arguments (no built-in 165.232.153.144 / frognet default); fdf-join the same.
   Rebuild opt/fdf/dist with opt/fdf/src/fdf-source/rinab/tools/make_deliverables.sh (build dirs: the rinab build/ is a
   symlink to a built tree with static x86 and arm64 binaries; if a needed binary is missing, say which and build it
   with the tree's own build scripts if they work here) so the dist carries T3/T4 code; record the BUILD line.
3. Droplet / region creation: new opt/fdf/src/fdf-source/fdf/bin/fdf-region-create (+ opt/fdf/bin copy):
   `fdf-region-create AREA PUBLIC_IP [--ram-port 51888] [--hub-port 50889]` on the machine that will hold the region:
   makes the site key (prints it once, stores it root-only), writes /opt/fdf/hub/hub.env (no shipped hub.env needed),
   starts the RAM manager unit, does map_site, enables fdf-client-<area> and fdf-hub, prints the firewall lines for
   the operator (does not run ufw itself unless --ufw). The droplet units: FDF_NAME from the machine's hostname, not
   StreamingFrog; fdf-hub.service must not be able to start without hub.env being made by fdf-region-create (so make
   the dependency explicit and its failure message say "run fdf-region-create"). The droplet tgz overwrites
   /opt/fdf/fdf.env on update: stop that (operator settings survive an update).
   A region must work without the internet: the RAM manager and hub can be a LAN machine — fdf-region-create takes
   whatever address the members reach it at.
4. Leave / reset / uninstall:
   - fdf-leave (T3a made it complete) — verify in the container.
   - frognet_reset.sh deletes ALL of /usr/local/bin and /usr/local/sbin: make it delete only FrogNet's files (the
     same node file set as 1), and stop writing 8.8.8.8 into resolv.conf; it must leave the machine with working
     name resolution the way it found it (put back what the installer replaced: systemd-resolved unmask, resolv.conf).
   - add `frognet_install.sh --uninstall` (or a frognet_uninstall.sh in installer/): reset + remove units, crontab
     lines, froguser, dispatcher hooks the installer added; keep /etc/fnid only with --keep-identity.
5. Upgrade: an install with --preserve over a node installed from the T0 baseline commit (git worktree of 42465dd
   copied into a container, installed, then the new tree with --preserve): the broker state is removed (T3b's upgrade
   step), services come back, the node's identity (fnid, name, IP, key) is kept.

## Gates — run in containers; write every command and its result into T4a.md
  G1 clean LAN-only install of the new tree in a fresh container: installer exits 0, F6 checks pass, services active
     (list them), getHosts.php answers, the C3 selfcheck passed inside, no wg interface.
  G2 two LAN-only nodes on one docker network: they discover each other (getHosts lists both), one databasehost.
  G3 region: container D runs fdf-region-create (area test, its docker IP); containers N1, N2 install with --region
     D:51888 --site-key <printed key> --area test; both join, make their keys, bring up hub links, ping each other's .1
     across the hub; fdf-monitor/getParticipants on D lists both. Then stop D's network (docker network disconnect) and
     reconnect: both come back without manual steps.
  G4 upgrade: T0-baseline install, then new tree --preserve: identity kept, broker files gone, services active.
  G5 leave and uninstall: fdf-leave on N1 → LAN-only, no fdf units; uninstall → no FrogNet units/files/cron/hooks, name
     resolution works.
  Plus the T3b gates (selfcheck, discovery oracles, run_all, the netns suite) still as at T3b, and the static gate
  docs/cleanup-2026-10/static-no-broker.sh still 0 violations.
If a gate cannot run here, say exactly why and what John runs instead (T7).

Write docs/cleanup-2026-10/T4a.md (changes one line each with why; every gate with its command and output). Final
message: 6 lines.
