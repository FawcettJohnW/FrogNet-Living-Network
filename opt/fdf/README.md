# FD&F on real machines

Everything installs under /opt/fdf; nothing goes into /usr/local/bin. Run as root (the RAM manager machine adds an
address to an interface; members add one route).

## Install (every machine)

    cd / && tar xvzf - < /tmp/fdf-node.tgz

The binaries in /opt/fdf/bin were built on Ubuntu 24.04 (they need libssl3, liblz4-1, libjemalloc2, libstdc++).
On a machine where they do not run, build from source with that machine's own compiler:

    mkdir -p /opt/fdf/build && cd /opt/fdf/build
    tar xzf /opt/fdf/src/fdf-rinab-src.tgz && tar xzf /opt/fdf/src/rlite-6ac276b.tgz
    cd rinab && tools/build.sh && tools/build_rlite.sh /opt/fdf/build/rlite-6ac276b
    cp build/bin/* /opt/fdf/bin/ && cp build/lib/librina-api.so /opt/fdf/lib/

The area's name is set once, in /opt/fdf/fdf.env (RINA_AREA; default rina-area.test). Every machine in the area
uses the same name.

The area's password is made once, when the area is created, and set as RINA_AREA_PASSWORD in /opt/fdf/fdf.env on
the mapping machine and on any machine that may serve as the RAM manager (not on plain members):

    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'


## The droplet's services, together

    /opt/fdf/bin/fdf-services.sh start     # FD&F (8840), the race (8799), the Communicator (8800, 8994)
    /opt/fdf/bin/fdf-services.sh status    # who is listening on each port
    /opt/fdf/bin/fdf-services.sh stop

Output stays on the terminal that started them. COMMS_BIN defaults to /opt/communicator/build. A port already held by
something else is said, with the program holding it, and that service is not started.

## Direct: one RAM server everyone is told about (e.g. a droplet)

On the RAM server (no mapping system, no password):

    /opt/fdf/bin/fdf-ram-server.sh

On each member, add to /opt/fdf/fdf.env:

    export RINA_RAM=<ram-server-public-address>:8840

then run, with no argument:

    /opt/fdf/bin/fdf-member.sh

Each member connects out to the RAM server, so NAT and a home router need nothing. The RAM server's port 8840 must be
open to the members. If the RAM server restarts, restart the members (no mapping system to wait on in this setup).

## The mapping system (one machine; it stays up)

    /opt/fdf/bin/fdf-mapping.sh

It prints the mapping system's address, computed from the area's name, binds it on lo (anycast: the network's route to
198.18.0.0/15 brings it here), and serves the mapping there.

## The RAM manager (one machine; it may be the same one)

    /opt/fdf/bin/fdf-ram-manager.sh [<mapping-machine-ip>]

It runs on port 8840, on the address this machine reaches the mapping system from (on the mapping machine itself: its
default route's address), and registers itself there. Give <mapping-machine-ip> only when it runs on a different
machine from the mapping and the network does not yet route 198.18.0.0/15.

## Each member

    /opt/fdf/bin/fdf-member.sh <mapping-machine-ip>

<mapping-machine-ip> is only the route to the reserved prefix 198.18.0.0/15 (anycast, in a real deployment). The member is
given only the area's name. Its agent prints "[FDF_DISCOVERY_V1] announced <hostname> at [...]" when it has joined.

## Check, from any member

    . /opt/fdf/fdf.env
    fdf-participants                    # every machine: hostname, IPs, alive, cores, CPU benchmark, memory
    rina-lisp mr-address $RINA_AREA     # the mapping system's address, as every machine computes it

## Move the RAM manager

Ctrl-C fdf-ram-manager.sh (leave fdf-mapping.sh running), then start it on another machine or interface. The members print
"[MAP_WATCH_V1] re-attached to the area by name" by themselves.

## The kart demo (its own region, on the droplet beside FD&F's)

On the droplet (open TCP 8799 to the players):

    /opt/fdf/bin/fdf-kart-ram.sh

On each player's machine (Windows, Mac or Linux; needs Python 3 and pygame: pip install pygame):

    python3 /opt/fdf/kart/kartdemo.py --name <your-name> --race-host <droplet-public-ip>:8799

Arrow keys or WASD. Each player sees the others' karts and call tiles, all through the region; there is no game
server. The demo speaks plain HTTP to the region (ram.php), which the RAM server answers on the same port as FNW1.

## Calls from the race: the real Communicator

Each other player's tile in the game has a **Call** button. It opens the Communicator (comms-app) straight into a call
to that player; their game sees the call in the shared memory and opens their Communicator, where the call rings.

On the race machine (open TCP 8800 and 8994), with the Communicator built (its build/ directory):

    COMMS_BIN=<communicator>/build /opt/fdf/bin/fdf-comms.sh

Each player's game needs to know where comms-app is (default: comms-app / comms-app.exe on the PATH):

    python3 kartdemo.py --name john --race-host <race-ip>:8799 --comms-app <communicator>/build/comms-app

(--comms-ram and --comms-media default to the race machine, ports 8800 and 8994.)

## Windows: the FD&F client, and the kart demo

Copy the folder /opt/fdf/windows to the Windows machine. It needs nothing installed for the client (fdf-client.exe is
one static program, no DLLs); the kart demo needs Python 3 and pygame (pip install pygame).

In one Command Prompt (leave it open: while it runs, this machine is a member, listed alive):

    fdf-client.exe <droplet-public-ip>:8840

In another:

    python kartdemo.py --name <your-name> --race-host <droplet-public-ip>:8799

From any Linux member, `. /opt/fdf/fdf.env && fdf-participants` lists the Windows machine by its computer name, with
its IPs, cores and memory, alive while fdf-client.exe runs.

## Web pages (on any machine with PHP)

    . /opt/fdf/fdf.env && PHP_CLI_SERVER_WORKERS=4 php -S 0.0.0.0:8090 -t /opt/fdf/www

Then open http://<that machine>:8090/fdf/fdf_dashboard.html, and /fdf/chat.html with, on the same machine:

    . /opt/fdf/fdf.env && python3 /opt/fdf/bin/chat-service.py

## Join a network, list it, and log in from a browser ([FDF_JOIN_V1] [FDF_LS_V1] [FDF_TERM_V1] [FDF_BROWSER_V1])

Everything goes through the RAM manager: no port is opened on the machines, no machine connects to another.

The RAM manager needs this package's rina-host (its plain-HTTP front now accepts DELETE, which the terminal uses to
remove a session's rows). On the droplet: `fdf_service stop`, untar, `fdf_service start`.

On each Linux machine (as root; the address must be an IP):

    /opt/fdf/bin/fdf-join 165.232.153.144:8840     # writes /opt/fdf/var/join.env, starts fdf-client.service
    /opt/fdf/bin/fdf-ls                            # the participants: name, alive, TERM (accepts logins), OS, IPs
    /opt/fdf/bin/fdf-ls --watch                    # the same, printed again on every change (held, not polled)
    /opt/fdf/bin/fdf-leave                         # undoes fdf-join (and fdf-term-serve --install)

On each machine that should accept browser logins (after fdf-join):

    /opt/fdf/bin/fdf-term-serve --install          # fdf-term-serve.service: each browser session is /bin/login on a PTY
    journalctl -u fdf-term-serve -f                # sessions opened / ended / rows removed

The monitor (frognet_monitor for FD&F), on any machine:

    /opt/fdf/bin/fdf-monitor                       # members, alive, the address each is seen from, IPs, OS, cores,
                                                   # CPU, memory, load, terminal; Enter: everything held about one
    /opt/fdf/bin/fdf-monitor --once                # print it all once (--json: as JSON)

It redraws when a member joins, leaves or announces, or a machine starts or stops offering terminals (held, no timer).

On the machine with the browser:

    /opt/fdf/bin/fdf-browser                       # serves the page on 127.0.0.1:8091 and opens it

The page lists the network's machines; the green ones that run fdf-term-serve take a click and give a login prompt.
Tabs for several sessions; closing a tab or the page ends that login. `fdf-browser --listen 0.0.0.0:8091 --no-open`
lets a browser on another machine use it: there is no authentication on it, and keystrokes (passwords included) cross
the network unencrypted, like everything else in the demo.

On Windows (Python 3, nothing to pip install), from the copied windows folder:

    python fdf-browser.py 165.232.153.144:8840

Tuples: input `term/<target>/<session>.<k>`, output `termout/<session>/<k>`, advertisements `term.hosts/<target>/ad`
(k = seq mod 256 / 512: a bounded ring; a rewrite replaces the row and takes a new id, so held reads see every write).

## What the sandbox proved

The checks behind each step are in /opt/fdf/src/fdf-rinab-src.tgz: experiments/RESULTS.md and tools/*-check.sh.
