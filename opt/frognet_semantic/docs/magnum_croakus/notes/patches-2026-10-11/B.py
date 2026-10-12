################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
# Report B -- Parts III, IV, V.  All OLD/NEW strings are builder source form (\uXXXX escapes, raw strings).

# ---------- Part III ----------
# 1. Wired Ethernet: one node serves one network; a second box joins by taking a lease on that segment
sub(r"""there is nothing to configure: plug both boxes into the same switch and they share a segment.") ]),""",
    r"""there is nothing to configure: cable the new box's uplink into the segment an existing node serves.") ]),""")
sub(r"""t("Run an Ethernet line from the house to the garage, both into the same switch. HomeBase is "), mono("10.80.80.1"), t("; ShopBox came up as "), mono("10.84.84.1"), t(". Nothing else is required \u2014 they share a broadcast domain, so the next discovery walk on either box sees the other as an immediate peer and the two lillypads become one LAN.""",
    r"""t("HomeBase is "), mono("10.80.80.1"), t("; ShopBox came up as "), mono("10.84.84.1"), t(". Run a cable from ShopBox's uplink port into the segment HomeBase serves. ShopBox takes a lease from HomeBase's DHCP, and the next walk on each box sees the other as an immediate peer through that lease. The two lillypads become one LAN.""")
# 2. Forming a LAN
sub(r"""t("HomeBase runs alone. You plug ShopBox into the switch; on its next walk HomeBase probes its wire, gets an answer from "), mono("10.84.84.1"), t(", proves it alive, writes it into the hosts file.""",
    r"""t("HomeBase runs alone. You cable ShopBox's uplink into HomeBase's segment; ShopBox takes a HomeBase lease, and on its next walk HomeBase finds "), mono("10.84.84.1"), t(" through that lease, proves it, and writes it into the hosts file.""")
# 3. ifplugd timing
sub(r"""t(" hands out addresses. A link watcher fires it the instant you plug or unplug, so""",
    r"""t(" hands out addresses. A link watcher ("), mono("ifplugd"), t(") runs it about 3 seconds after you plug in and 10 seconds after you unplug, so""")
# 4. helper applies at once; its write drops AP_IFACE / WIFI_COUNTRY
sub(r"""Set the preference with the v4 setup helper and "), mono("frognet-netstart"), t(" honors it on its next run:") ]),""",
    r"""Set it with the v4 setup helper; the helper applies it at once by running "), mono("frognet-netstart"), t(":") ]),""")
# 4b + 5. after the code block
sub(r"""t(" \u2014 and the same control sits on the node's setup web page (Part IV). This switch governs only the node's own onboard radio.") ]),""",
    r"""t(" \u2014 and the same control sits on the node's setup web page (Part IV). In this build the helper rewrites "), mono("ssid_projection.conf"), t(" with the on/off switch alone, so a chosen AP interface and country go back to "), mono("wlan0"), t(" and US. This switch governs only the radio the node projects its own SSID on ("), mono("AP_IFACE"), t(", "), mono("wlan0"), t(" unless you chose another).") ]),""")
# 6. Queens drive
sub(r"""Dan's trunk-mounted node through Queens is exactly this behavior at traffic speed: the topology changed under the mesh block by block and the walk re-formed it, nobody touching anything.""",
    r"""Dan's trunk-mounted node through Queens is this at traffic speed: a 900 MHz HaLow uplink held through the city while the same node served its own access point to sensors, with nobody touching anything.""")
# 7. Chorus
sub(r"""t(" is a named group of nodes that belong together across the pond, wherever they physically sit \u2014 \u201Cfamily,\u201D \u201Cops,\u201D the circle the broker actually meshes over the internet. And "),""",
    r"""t(" is a group of lillypads: the pond-wide chorus of all of them, or a named chorus an administrator defines \u2014 \u201Cfamily,\u201D \u201Cops.\u201D And "),""")
# 8. DNS: dnsmasq serves /etc/hosts and forwards per-domain
sub(r"""The first is name resolution. FrogNet does not run DNS for the mesh \u2014 it resolves the mesh's own names through "), mono("/etc/hosts"), t(", the oldest and most reliable resolver there is. Every node keeps its own hosts file; peer names, the floating "), mono("databasehost.frognet"), t(", and each "), mono("<role>.frognet"), t(" service name all live there, and the node's local resolver on its "), mono(".1"), t(" serves those same names to the phones and laptops on its lillypad.""",
    r"""The first is name resolution. The mesh's names live in "), mono("/etc/hosts"), t(". Every node keeps its own; peer names, the floating "), mono("databasehost.frognet"), t(", and each "), mono("<role>.frognet"), t(" service name all live there. The node's "), mono("dnsmasq"), t(" serves that file as DNS on its "), mono(".1"), t(" and forwards each remote node's domain to that node ("), mono("server=/<domain>/<ip>"), t("), both rewritten on every merge.""")
# 9. Discovery owns the whole of /etc/hosts
sub(r"""P([ t("Discovery is what fills that file \u2014 "), b("non-destructively."), t(" Every time the topology changes, the walk recomputes the mesh and writes the result into "), mono("/etc/hosts"), t(", but only its own lines: it replaces the FrogNet-managed entries (the peers and the "), mono("<role>.frognet"), t(" names) and leaves everything else in the file untouched. The write is idempotent and safe to repeat on every merge. Discovery owns its block of the hosts file and nothing more, so your own entries \u2014 and the system's \u2014 are never clobbered.") ]),""",
    r"""P([ t("Discovery owns "), mono("/etc/hosts"), t(". On every merge it writes the whole file from the walk's result: "), mono("localhost"), t(", the "), mono("FrogNetHost"), t("/"), mono("FrogNetAdmin"), t(" line for each node, and the "), mono(".frognet"), t(" role names. The write is idempotent and safe to repeat on every merge. Do not keep hand entries there.") ]),""")
# 10. default route is managed
sub(r"""P([ t("The second is the default route, and FrogNet leaves it alone "), i("on purpose"), t(": the default route is how a node gets off the FrogNet and out to the upstream \u2014 the internet, or really whatever happens to be out there. Discovery installs routes only for the mesh, the "), mono("10/8"), t(" plane, as the RTT-ranked winners and fallbacks from the walk; anything that is "), i("not"), t(" a mesh address follows the untouched default route out the uplink, exactly as it would on any Linux box. Keeping it intact is also what keeps ordinary name resolution working: mesh names come from "), mono("/etc/hosts"), t(", and every other name is forwarded to the upstream resolver \u2014 which the node can still reach precisely because the default route was never disturbed. So both halves""",
    r"""P([ t("The second is the default route: how a node gets off the FrogNet and out to the upstream \u2014 the internet, or whatever is out there. Discovery manages the "), mono("10/8"), t(" routes, the RTT-ranked winners and fallbacks from the walk, and it also keeps a working default route. A node with its own uplink keeps that default, dead defaults are removed, and a node with no uplink takes its default through an attached peer that advertises a live exit. Non-mesh traffic and ordinary DNS leave by that default. So both halves""")

# ---------- Part IV ----------
# 11 + 12. reset; what the installer asks
sub(r"""t(", which takes a node back to what the installer found. This chapter is""",
    r"""t(", which wipes the FrogNet install, state, identity and databases. The installer runs it itself on a box that already has FrogNet, so a reinstall lands on a clean machine; "), mono("--preserve"), t(" upgrades in place instead. This chapter is""")
sub(r"""You run it as root and answer four things:") ]),""",
    r"""You run it as root and answer a few things:") ]),""")
sub(r"""        "     --broker-host streamingfrog.com \\",
        "     --broker-port 18257",""",
    r"""        "     --broker-host streamingfrog.com \\",
        "     --broker-port 18257 \\",
        "     --pond hometown",""")
sub(r"""you can add one later from the setup page below.") ]),""",
    r"""you can add one later from the setup page below.") ]),
      bullet([ b("--pond "), t("\u2014 the pond this node joins. The installer asks for it whenever a broker is set.") ]),
      P([ t("The installer also asks for the FrogUser database password, which every node in the pond shares ("), mono("--db-pass"), t("), and, when it will project an SSID, the pond's one Wi-Fi passphrase ("), mono("--wifi-psk"), t(").") ]),""")
# 13. phase A
sub(r"""bullet([ b("A \u2014 Packages. "), t("apt and pip dependencies. The only phase that wants the internet, and only the first time.") ]),""",
    r"""bullet([ b("A \u2014 Reset and packages. "), t("Wipes a prior install (unless "), mono("--preserve"), t("), then apt and pip dependencies. It refreshes the package index every run.") ]),""")
# 14. phase C
sub(r"""bullet([ b("C \u2014 Extract the world. "), t("Unpacks "), mono("frognet_world.tgz"), t("; if the box's architecture differs from the build's, rebuilds the Python venv in place (this is the platform independence from Part II made real).") ]),""",
    r"""bullet([ b("C \u2014 Lay down the world. "), t("Unpacks "), mono("frognet_world.tgz"), t(" (or copies a repository checkout), mints the node GUID, injects the DB secret, rebuilds the Python venv only if its architecture does not match the box, then runs the discovery self-check on the installed code and stops the install if it fails.") ]),""")
# 15. phases D, E, F
sub(r"""bullet([ b("D \u2014 Clean and configure. "), t("Wipes stale state from a cloned box, then writes this node's real configuration \u2014 name, address, broker, identity.") ]),
      bullet([ b("E \u2014 Provision. "), t("Brings up the services, generates local SSL, applies tuning.") ]),
      bullet([ b("F \u2014 Finalize. "), t("Enables services, wires logging and cron, verifies, reboots.") ]),""",
    r"""bullet([ b("D \u2014 Clean and configure. "), t("Interfaces, stale state, database, web server, DHCP, NetworkManager, schema.") ]),
      bullet([ b("E \u2014 Identity and provision. "), t("Name and address, hostname, broker and WireGuard keys, SSL, iptables, SSID projection, the link watcher.") ]),
      bullet([ b("F \u2014 Finalize. "), t("Enable services, logging, cron, verify, system tuning, reboot.") ]),""")
# 16. liveness checks
sub(r"""t(" check is the bidirectional ping-pong the discovery walk runs, and a candidate counts as reachable only when its "), i("daemon"), t(" answers it.""",
    r"""t(" checks are the ones the walk runs: a reflect probe for a relayed path and the bidirectional "), mono(":9009"), t(" ping-pong to the daemon for a direct or tunnel path.""")
# 17. clear broker keeps the file
sub(r"""t("\u2014 write or remove "), mono("tunnel.conf"), t(" (host, port, pond, password, choruses)""",
    r"""t("\u2014 write or remove the broker fields in "), mono("tunnel.conf"), t(" (host, port, pond, password, choruses)""")
# 18. no register button; QR is the setup address
sub(r"""bullet([ b("Register the node "), t("with the broker, and generate a "), b("join QR code"), t(" \u2014 point a phone at it to bring a device onto the pond without typing a thing.") ]),""",
    r"""bullet([ b("Set the node's name and address, "), t("and join it to a Wi-Fi network as a client. After you apply, the page shows a QR code of the node's new setup address, so you can scan your way back to it after the reboot. (Broker registration is a helper command, not a page button.)") ]),""")
# 19-22. config file table
sub(r"""        "/etc/frognet/tunnel.conf             GROUP_TOKEN (membership)      no \u2014 from redeem",
        "/etc/frognet/ssid_projection.conf    SSID on/off, wifi country     via helper/page",
        "/etc/frognet/gateways.conf           gateway / uplink facts        rarely, by hand",""",
    r"""        "/etc/frognet/tunnel.conf             NODE_GUID, pond password      no \u2014 set at install / via helper",
        "/etc/frognet/ssid_projection.conf    SSID on/off                   via helper/page",
        "/etc/frognet/ssid_projection.conf    AP interface, wifi country    at install",
        "/etc/frognet/wifi.env                AP WPA2 passphrase (pond)     at install (--wifi-psk)",
        "/etc/frognet/gateways.conf           node name, its own .1 and .2  no \u2014 written by setup_lillypad",""")
# 23. the builder is a model
sub(r"""FrogNet's simulator runs the "), i("real"), t(" discovery, routing, and election code over a network you describe, so what it says your pond will do is what your pond will do.""",
    r"""The topology builder below models discovery and routing so you can check a shape in seconds. The real discovery engine runs against shapes in "), mono("discovery/sim"), t(" (the same self-check the installer runs before it activates a node), and the builder's shapes also run against a real kernel (below).""")
sub(r"""and run it. The simulator converges every node with the actual algorithm and hands you""",
    r"""and run it. The builder converges every node and hands you""")
# 24. example output as run
sub(r"""        "#  gate learns:  10.10.20.0/24 via 10.10.20.1 dev eth1",
        "#  every node reaches every other; converges in a couple of cycles",""",
    r"""        "#  gate learns:  10.10.20.0/24 via 10.10.99.2 dev eth1",
        "#  every node reaches every other; converges in 2 cycles",""")
# 25. twenty shapes
sub(r"""More than twenty worked shapes already ship with the simulator \u2014 pairs, chains, stars, rings, ponds, hub-and-spoke, mixed bearers, multi-gateway \u2014""",
    r"""Twenty worked shapes ship with the simulator \u2014 pairs, chains, stars, rings, ponds, hub-and-spoke, mixed bearers, tunnel transits \u2014""")
# 26. convergence grows with length
sub(r"""Convergence itself is cheap: a small, constant number of cycles for any connected shape, however wide.") ]),""",
    r"""Convergence itself is cheap: a wide shape converges in two or three cycles; a long chain takes about one cycle per hop (five for a five-node chain).") ]),""")

# ---------- Part V ----------
# 27. lead
sub(r"""FrogNet uses its own web stack to do it. Discovery is HTTP, riding the same compressed proxy your applications ride.") ,""",
    r"""FrogNet uses its own web stack to do it. Discovery is HTTP to the neighbours' own web servers, sent past the local proxy so a walk never queues behind production traffic.") ,""")
# 28. direct, uncompressed
sub(r"""Both are plain HTTP, both compressed by the same engine as everything else. Discovery is FrogNet making HTTP calls to FrogNet.") ]),""",
    r"""Both are plain HTTP to the neighbour's port 80, sent direct and uncompressed (marked to bypass the local proxy). The round-trip measurement that ranks paths is the one discovery request that rides the local proxy. Discovery is FrogNet making HTTP calls to FrogNet.") ]),
      P([ t("On a node joined to an FD&F region, the region already holds every member's own facts, so the merge reads the member set from the region in one call and does not crawl. The probes that measure paths \u2014 reflect, the "), mono(":9009"), t(" ping-pong, the round-trip measure \u2014 run the same either way: the region says who exists, the probes say what is reachable.") ]),""")
# 30. 4800 baud
sub(r"""P([ t("That pays off twice. Discovery inherits every property of the transport \u2014 the compression, the parallelism, the transport-agnosticism \u2014 so the mesh finds itself over a 4800-baud radio link as readily as over Ethernet. And there is no second networking stack to maintain. The network discovers itself with the same tool it serves applications with.") ]),""",
    r"""P([ t("That pays off twice. Discovery needs nothing from the bearer but IP, so it runs over any carrier the node has. And there is no second networking stack to maintain. The network discovers itself with the same web stack it serves applications with.") ]),""")
# 31. what reaches the proxy
sub(r"""The proxy takes over port 80. Every HTTP request crossing the node's interface \u2014 an application's, a browser's, and discovery's own echo and getHosts calls \u2014 arrives at the proxy first; nothing speaks HTTP on a FrogNet node without passing through it. That is why discovery gets the compression and the transport for free: it is not special-cased, it is simply more port-80 traffic, handled the same way as everything else.") ]),""",
    r"""The proxy takes over port 80. HTTP to any "), mono("10/8"), t(" address \u2014 from an application, a browser, or a client on the lillypad \u2014 is redirected into the proxy. Discovery's own echo and getHosts are marked to go around it.") ]),""")
# 32. non-10 HTTP never intercepted
sub(r"""P([ t("The first thing the proxy does with a request is look at where it is bound, because not everything gets the FrogNet treatment. A request addressed to a "), b("FrogNet address \u2014 the 10-net"), t(" \u2014 enters the semantic path: a candidate for compression and the established-socket transport. A request addressed "), b("anywhere else"), t(" \u2014 the public internet, a printer on the house LAN, anything outside 10/8 \u2014 is simply "), i("passed through"), t(", forwarded to the real upstream untouched. The proxy is in the path for everything, but it does semantic work only for FrogNet's own traffic; the rest it gets out of the way of.") ]),""",
    r"""P([ t("A request addressed to a "), b("FrogNet address \u2014 the 10-net"), t(" \u2014 enters the semantic path: a candidate for compression and the established-socket transport. HTTP to "), b("anywhere outside 10/8"), t(" \u2014 the public internet, a printer on the house LAN \u2014 is never intercepted; it leaves by the normal route. The proxy sees only FrogNet-bound HTTP.") ]),""")
# 33. proxy -> destination daemon on :9009
sub(r"""P([ t("For a FrogNet-bound request, the proxy hands it to the daemon, the historical FrogNet worker pool that hosts the semantic codec and domain handlers. It reaches the daemon over a "), b("dedicated loopback socket"), t(". In current""",
    r"""P([ t("For a FrogNet-bound request, the proxy opens (and keeps) a socket to the destination node's daemon on "), mono(":9009"), t(" and carries the exchange over it as FNWP frames. The daemon is the historical FrogNet worker pool that hosts the semantic codec and domain handlers. In current""")
# 34. proof per path kind
sub(r"""Each candidate is proved before it is trusted \u2014 a light bidirectional liveness probe, not an application health check, and a candidate counts only if its daemon answers.""",
    r"""Each candidate is proved before it is trusted \u2014 a reflect probe for a path through a relay, a bidirectional "), mono(":9009"), t(" ping-pong to the daemon for a direct or tunnel path \u2014 not an application health check.""")
# 35. .2 proves
sub(r"""t(" address \u2014 that is the wire everyone's live connections are on. So discovery never probes on "), mono(".1"), t(". For each destination it wants to test, it installs a tiny, temporary route to a "), mono(".2"), t(" address \u2014 a unique "), mono(".2"), t(" per destination, so parallel probes never collide \u2014 sends its liveness probe over that, and tears the "), mono(".2"), t(" route down when the walk is done. The doctrine""",
    r"""t(" address \u2014 that is the wire everyone's live connections are on. Discovery proves paths on "), mono(".2"), t(". For each destination it wants to test, it installs a temporary "), mono("/32"), t(" route to a "), mono(".2"), t(" address \u2014 a unique "), mono(".2"), t(" per destination, so parallel probes never collide \u2014 sends its probe over that, and removes the "), mono("/32"), t(" as soon as the probe finishes. Each destination that wins keeps a small "), mono(".2/32"), t(" alias route beside its /24. (The round-trip helper, "), mono("frognet_alive"), t(", still retries on "), mono(".1"), t(" when "), mono(".2"), t(" does not answer.) The doctrine""")
# 36. RTT of first hop
sub(r"""As it proves each destination it measures the "), b("round-trip time"), t(" to it \u2014 and""",
    r"""As it proves each path it measures the "), b("round-trip time"), t(" of that path's first hop \u2014 and""")
# 37. hysteresis remains
sub(r"""An earlier version of the rule allowed a challenger to win if it was at least fifty per cent faster. It was replaced by the flat prohibition, because the correct number of times to disturb a working path is zero.""",
    r"""An earlier rule let a challenger win if it was at least fifty per cent faster. The flat prohibition now runs first, because the correct number of times to disturb a working path is zero; the fifty per cent bar remains only for a route whose health could not be confirmed.""")
# 38. hop cap
sub(r'''"  c >  32 (default hop cap)         -> LOOP among others, dropped"''',
    r'''"  c >= 32 (default hop cap)         -> LOOP among others, dropped"''')
# 39. depth
sub(r"""...gld([ t("The walk descends two levels: a node's children and their children. A box behind a box behind a box is not reached, and that is a constant in one file rather than anything the design requires -- which makes it the sort of limit that stays until somebody with a deep enough network cares. If that is you, the guild is interested.") ]),""",
    r"""...gld([ t("Each walk descends two levels, children and grandchildren. The next merge starts from the routes already installed, so a deeper chain is walked to its end over a few passes.") ]),""")
# 40. advertised facts
sub(r"""A node advertises what it can do \u2014 compute, memory, storage throughput, codec support \u2014""",
    r"""A node advertises what it can do \u2014 cores and clock, installed memory, disk class, codec support \u2014""")
# 41. database score
sub(r"""The database host selects the most-capable box that is actually running MySQL \u2014 it is memory-bound, so the most RAM wins, with the highest address only as a tiebreak (Part VI).""",
    r"""The database host scores static capability \u2014 installed RAM first, then disk class and CPU \u2014 among boxes actually running MySQL, with the highest address only as a tiebreak (Part VI).""")
# 42. no game host
sub(r"""among directly-attached neighbors. A board-game host selects by whatever a game needs. Every node""",
    r"""among directly-attached neighbors. Every node""")
# 43. role handler
sub(r"""Adding a new kind of service host is implementing one virtual function, not editing a central election.""",
    r"""Adding a new kind of service host is writing one handler \u2014 its score and evaluate callbacks \u2014 and adding it to the role registry, not editing a central election.""")
# 44. no stability margin
sub(r"""A small stability margin keeps a marginal challenger from constantly unseating a healthy incumbent.""",
    r"""Stability comes from what is scored: only static capability, so a box's score does not move with load, and every node computes the same winner merge after merge.""")
done()
