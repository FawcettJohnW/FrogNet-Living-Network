# FD&F on real machines

Everything installs under /opt/fdf; nothing goes into /usr/local/bin. Run as root. How to create a region, join
nodes, phones and Windows PCs, and leave: `docs/OPERATING.md` in the FrogNet repository (section 3). This file is the
FD&F package's own notes: what is shown where, the build, and the older set-ups the package still carries.

## What is shown where (build fdf-2026-10-12-r20)

"netns" is the repository's network-namespace tests on one Linux machine (`src/fdf-source/rinab/tools/netns-*-test.sh`).
"containers" is the October 2026 cleanup's install gates: Ubuntu 24.04 x86-64 machines as privileged systemd containers
(`docs/cleanup-2026-10/` in the FrogNet repository, T4a–T5b). "hardware" is John's machines, from his own reports, all
made before the cleanup; none has been re-run on this build.

| capability | netns | containers | hardware |
|---|---|---|---|
| a region made with `fdf-region-create`; nodes joined by the `.run` or `frognet_install.sh --region`; node to node across the hub | region-loop PASS (T5) | G3 PASS: 3/3 pings each way after the reboot (T4a–T5b) | John's region and Pis, before the cleanup |
| the region after the region host's network drops and returns | — | G3 outage: pings both ways 20 s after it returned, no manual step (T4b) | — |
| a node with no WireGuard key makes its own and announces it | region-loop `NOKEY=a` PASS (T5) | G3 (`WG_KEY: made ...`) | — |
| leaving a region (`fdf-leave`) | — | G5 PASS (T4a, T4b, T4c) | — |
| one node in two ponds, the forward pond switched | ponds-daemon PASS, 21 (T5) | — | — |
| reach across ponds: aliases, quarantine, `resolve`, ambiguity refused | reach PASS, 70 (T5) | — | not run (one pond on hardware) |
| the region view on the node's Apache at `/fdf/` | — | G3 PASS: 200 on :8080 and :80, members listed (T5b) | — |
| Windows bridge (`windows/bridge`, fdf-kt a15, fdf-tun b11) | the same Kotlin member, reach stage 7 | — | on the mesh, and off it as a leaf via the hub; ping and ssh to a node (before the cleanup) |
| Android app (a15, fdf-tun b11) | fdf-tun suite; member selftest | — | a handset joined and reached (before the cleanup). No APK is built or shipped |
| the Communicator's servers on a node (`fdf-comms.sh`) | the region-loop stage is skipped in the gates | — | not run on a node |
| the phone and `fdf-bridge.cmd ask comms` finding them from the where cell | reach stage 7 (`ask comms`) | — | not run |
| kart demo calls through comms-app | stand-in comms-app (`rinab/experiments/RESULTS.md`) | — | not with the real Communicator |
| LISP area mapping (`rina-lisp`, oracle against lispers.net) | `rinab/experiments/RESULTS.md` D5 | — | not on hardware |

Historical RINA Mode B measurements are in `src/fdf-source/rinab/README.md`, marked as such.

## The files

One file per machine type, in `dist/`; the build each carries is in /opt/fdf/BUILD and on its log lines:

    region host:  cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP
    Pi node:      sudo bash fdf-install-aarch64.run <SITE KEY> <PUBLIC_IP:51888> <AREA>
    x86 node:     sudo bash fdf-install-x86_64.run <SITE KEY> <PUBLIC_IP:51888> <AREA>
    or, at FrogNet install:  frognet_install.sh ... --region PUBLIC_IP:51888 --site-key KEY --area AREA

`fdf-region-create` prints the site key once and the line a node joins with. A later build on the region host: untar,
`systemctl daemon-reload`, restart `fdf-ram-AREA fdf-client-AREA fdf-hub`; the region's settings (hub.env, the site
key, the units, /opt/fdf/var) are not in the package and survive. A node's `.run` unpacks /opt/fdf and the FrogNet
region modules and runs /opt/fdf/bin/fdf-region-join; all three arguments are required (there is no built-in region).

## Build from source

The source is `src/fdf-source` (rinab: the C++ and its tests; fdf: the scripts, pages and hub; deliverables: the `.run`
header and the region host's unit). It needs g++ (C++20), lz4, openssl, jemalloc, and aarch64-linux-gnu-g++ for the Pi
binaries. In T4b the build was, about five minutes in all:

    cd opt/fdf/src/fdf-source/rinab
    bash tools/build.sh && bash tools/build_static_host.sh && bash tools/build_static_clients.sh && bash tools/build_arm64.sh
    bash tools/make_deliverables.sh <outdir>      # the three files; refuses on any tag or binary mismatch

`make_deliverables.sh` also wants the Communicator's static servers in `build/comms-{x86_64,arm64}`
(`tools/build_comms_deps.sh` and `tools/build_comms_static.sh`) and stops naming them when they are missing. `build/` is
not tracked in git.

## The earlier set-ups: one RAM server, or the mapping system

Everything from here on is FD&F before the region: a RAM server every member is told about, or a mapping system with a
movable RAM manager, and `fdf-member.sh` on each machine. The scripts still ship (a lab, a demo). The FrogNet region is
"The files" above and `docs/OPERATING.md`.

### Area name and password

The area's name and password are this machine's settings, in /opt/fdf/var/area.env (never shipped; /opt/fdf/fdf.env
sources it). There is no default area: a script that needs one and finds none says
`set RINA_AREA in /opt/fdf/var/area.env`. Every machine in the area uses the same `RINA_AREA`. The password,
`RINA_AREA_PASSWORD`, goes only on the mapping machine and on machines that may serve as the RAM manager; make it
once, when the area is created:

    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'

### The services of one demo machine, together

    /opt/fdf/bin/fdf-services.sh start     # FD&F (8840), the race (8799), the Communicator (8800, 8994)
    /opt/fdf/bin/fdf-services.sh status    # who is listening on each port
    /opt/fdf/bin/fdf-services.sh stop

Output stays on the terminal that started them. `COMMS_BIN` defaults to /opt/communicator/build; the package's own
comms-ram and comms-media are in /opt/fdf/comms (`COMMS_BIN=/opt/fdf/comms`). A port already held by something else is
said, with the program holding it, and that service is not started.

### Direct: one RAM server everyone is told about

On the RAM server (no mapping system, no password):

    /opt/fdf/bin/fdf-ram-server.sh

On each member, put the line `export RINA_RAM=<the RAM server's address>:8840` in /opt/fdf/var/join.env (fdf.env
sources it), then run `/opt/fdf/bin/fdf-member.sh` with no argument (it still needs `RINA_AREA`). Each member connects out to the RAM server, so NAT and a home router
need nothing; the RAM server's port 8840 must be open to the members. If the RAM server restarts, restart the members.

### The mapping system (one machine; it stays up)

    /opt/fdf/bin/fdf-mapping.sh

It prints the mapping system's address, computed from the area's name, binds it on lo (anycast: the network's route to
198.18.0.0/15 brings it here), and serves the mapping there.

### The RAM manager (one machine; it may be the same one)

    /opt/fdf/bin/fdf-ram-manager.sh [<mapping-machine-ip>]

It runs on port 8840, on the address this machine reaches the mapping system from, and registers itself there. Give
`<mapping-machine-ip>` only when it runs on a different machine from the mapping and the network does not yet route
198.18.0.0/15.

### Each member

    /opt/fdf/bin/fdf-member.sh <mapping-machine-ip>

`<mapping-machine-ip>` is only the route to 198.18.0.0/15. The member is given only the area's name. It prints
`[FDF_DISCOVERY_V1] announced <hostname> at [...]` when it has joined.

### Check, from any member

    . /opt/fdf/fdf.env
    fdf-participants                    # every machine: hostname, IPs, alive, cores, CPU benchmark, memory
    rina-lisp mr-address $RINA_AREA     # the mapping system's address, as every machine computes it

### Move the RAM manager

Ctrl-C `fdf-ram-manager.sh` (leave `fdf-mapping.sh` running), then start it on another machine or interface. The
members print `[MAP_WATCH_V1] re-attached to the area by name` by themselves.

### The kart demo

On the race machine (open TCP 8799 to the players):

    /opt/fdf/bin/fdf-kart-ram.sh

On each player's machine (Windows, Mac or Linux; Python 3 and pygame: `pip install pygame`):

    python3 /opt/fdf/kart/kartdemo.py --name <your-name> --race-host <race-machine-ip>:8799

Arrow keys or WASD. Each player sees the others' karts and call tiles, all through the region; there is no game
server. The demo speaks plain HTTP to the region (ram.php), which the RAM server answers on the same port as FNWP.

Each other player's tile has a **Call** button. It opens the Communicator (comms-app) straight into a call to that
player; their game sees the call in the shared memory and opens theirs, where the call rings. On the race machine (open
TCP 8800 and 8994), with the Communicator built: `COMMS_BIN=<communicator>/build /opt/fdf/bin/fdf-comms.sh`. Each
player's game is told where comms-app is: `--comms-app <communicator>/build/comms-app` (`--comms-ram` and
`--comms-media` default to the race machine, ports 8800 and 8994).

### Windows: the older client folder

The Windows bridge (this computer in every pond at once) is `windows/bridge`; see its README.txt and
`docs/OPERATING.md`. `windows/README-windows.txt` is the older client folder (`fdf-client.exe <RAM-HOST>:8840`, the
kart demo, the browser terminal, the monitor) for the 8840 set-ups above.

### Web pages ([FDF_WEB_UNDER_APACHE_V1])

On a FrogNet node in a region the node's Apache serves /opt/fdf/www/fdf at /fdf/ (the region members,
fdf_dashboard.html); its PHP runs as www-data under /opt/fdf/fdf.env, read on each request. On a machine that is not a
FrogNet node, serve the same directory with
`. /opt/fdf/fdf.env && PHP_CLI_SERVER_WORKERS=4 php -S 0.0.0.0:8090 -t /opt/fdf/www` and open
`http://<that machine>:8090/fdf/`. `/fdf/chat.html` needs `. /opt/fdf/fdf.env && python3 /opt/fdf/bin/chat-service.py`
on the same machine.

### Join a network, list it, and log in from a browser ([FDF_JOIN_V1] [FDF_LS_V1] [FDF_TERM_V1] [FDF_BROWSER_V1])

Everything goes through the RAM manager: no port is opened on the machines, no machine connects to another. On each
Linux machine (as root; the address must be an IP):

    /opt/fdf/bin/fdf-join <RAM-HOST>:8840        # writes /opt/fdf/var/join.env, starts fdf-client.service
    /opt/fdf/bin/fdf-ls                          # the participants: name, alive, TERM (accepts logins), OS, IPs
    /opt/fdf/bin/fdf-ls --watch                  # the same, printed again on every change (held, not polled)
    /opt/fdf/bin/fdf-leave                       # undoes fdf-join (and fdf-term-serve --install)

On each machine that should accept browser logins (after `fdf-join`):

    /opt/fdf/bin/fdf-term-serve --install        # fdf-term-serve.service: each browser session is /bin/login on a PTY
    journalctl -u fdf-term-serve -f              # sessions opened / ended / rows removed

The monitor, on any member:

    /opt/fdf/bin/fdf-monitor                     # members, alive, addresses, OS, cores, CPU, memory, load, terminal
    /opt/fdf/bin/fdf-monitor --once              # print it all once (--json: as JSON)

On the machine with the browser: `/opt/fdf/bin/fdf-browser` serves the page on 127.0.0.1:8091 and opens it.
`fdf-browser --listen 0.0.0.0:8091 --no-open` lets a browser on another machine use it: there is no authentication on
it, and keystrokes (passwords included) cross the network unencrypted. On Windows (Python 3), from the older client
folder: `python fdf-browser.py <RAM-HOST>:8840`.

Tuples: input `term/<target>/<session>.<k>`, output `termout/<session>/<k>`, advertisements `term.hosts/<target>/ad`
(k = seq mod 256 / 512: a bounded ring; a rewrite replaces the row and takes a new id, so held reads see every write).

## What the sandbox proved

The checks behind each step are in `src/fdf-source/rinab`: `experiments/RESULTS.md` and `tools/*-check.sh`.
