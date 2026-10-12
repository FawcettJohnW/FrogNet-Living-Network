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

# ================= P1 =================
# P1-1 Part 0 0.5: Map-Resolver address
sub(r'''the Map-Resolver's address is derived from the name (SHA-256 into a reserved prefix), never configured or looked up in DNS,''',
    r'''the Map-Resolver's address is derived from the name (SHA-256 into 198.18.0.0/15) unless RINA_MAP_RESOLVER names one; it is never looked up in DNS;''')

# P1-2 Part 0 0.6: encryption
sub(r'''and encryption (the only cryptography is an HMAC on the mapping registration).") ]),''',
    r'''and encryption of the sessions to the RAM manager (FNWP to the RAM manager is plain TCP; FD&F's own cryptography is the HMAC on mapping registrations; FrogNet traffic through the hub and the bridges rides WireGuard).") ]),''')

# P1-3 Part I 1: listeners on a node
sub(r''' On a FrogNet node this holds. An FD&F region is different by design: its RAM manager is a reachable listener (the droplet's rina-host on TCP 51888), because every participant connects up to it.") ]),''',
    r''' On a FrogNet node the listeners are the node's own machinery (its web server on :80, its daemon on :9009) and, on a node that hosts the Communicator, its memory and media servers (comms-ram :8800, comms-media :8994). An FD&F region is the same by design: its RAM manager is a reachable listener (the droplet's rina-host on TCP 51888), because every participant connects up to it.") ]),''')

# P1-4 Part V 13: no walk on a region
sub(r'''P([ t("On a node joined to an FD&F region, the region already holds every member's own facts, so the merge reads the member set from the region in one call and does not crawl. The probes that measure paths \u2014 reflect, the "), mono(":9009"), t(" ping-pong, the round-trip measure \u2014 run the same either way: the region says who exists, the probes say what is reachable.") ]),''',
    r'''P([ t("On a node joined to an FD&F region the walk does not run. The region holds every member's facts, and "), mono("fdf_graph"), t(" turns them into a plan: who exists, which members share a LAN, and which go through the hub. The tunnel daemon installs that plan on each region event. It admits every planned route without a probe, and gates hub routes on the hub link's WireGuard handshake. The merge then names only subnets that have an installed route. Reflect, the "), mono(":9009"), t(" ping-pong and the round-trip measure belong to the walk, off a region.") ]),''')

# P1-5 Part V 13: getHosts rows
sub(r'''"  { \"ip\": \"10.80.80.1\", \"name\": \"HomeBase\", \"echo\": \"...\" },", "  { \"ip\": \"10.84.84.1\", \"name\": \"ShopBox\",  \"echo\": \"...\" },", "  { \"ip\": \"10.90.90.1\", \"name\": \"MomBox\",   \"echo\": \"...\" }", "]"])''',
    r'''"  { \"ip\": \"10.80.80.1\", \"name\": \"HomeBase\", \"echo\": \"...\", \"role\": \"\", \"alive\": false },", "  { \"ip\": \"10.84.84.1\", \"name\": \"ShopBox\",  \"echo\": \"...\", \"role\": \"\", \"alive\": false },", "  { \"ip\": \"10.90.90.1\", \"name\": \"MomBox\",   \"echo\": \"...\", \"role\": \"\", \"alive\": false }", "]"]),
      P([ t("On a region node the list comes from the region (role and alive filled in; a phone or PC reads role "), mono("leaf"), t("), not from this node's walk.") ])''')

# P1-6 Part III 6: /etc/hosts writers
sub(r'''P([ t("Discovery owns "), mono("/etc/hosts"), t(". On every merge it writes the whole file from the walk's result: "), mono("localhost"), t(", the "), mono("FrogNetHost"), t("/"), mono("FrogNetAdmin"), t(" line for each node, and the "), mono(".frognet"), t(" role names. The write is idempotent and safe to repeat on every merge. Do not keep hand entries there.") ]),''',
    r'''P([ t("Discovery owns "), mono("/etc/hosts"), t(". On every merge it writes the whole file: "), mono("localhost"), t(", the "), mono("FrogNetHost"), t("/"), mono("FrogNetAdmin"), t(" line for each node, and the "), mono(".frognet"), t(" role names. Off a region these come from the walk; on a region node they come from the region's facts. There the tunnel daemon's election also rewrites the file on each region event from the same facts, and both writers name the devices (phones, PCs). The write is idempotent and safe to repeat. Do not keep hand entries there.") ]),''')

# ================= P2 =================
# P2-1 monitor
sub(r'''t(", the network's info center, is a terminal dashboard that does exactly this: it lists every node, shows each one's live telemetry \u2014 echo, round-trip time, cache hit rate, bytes saved \u2014 and lets you drill into a node to see its sensors and open a single reading in full. It writes nothing and runs nowhere special; it is just another reader of the shared store, talking to "), mono("databasehost.frognet"), t(" through the proxy like everyone else. A sensor''',
    r'''t(", the network's info center, is a terminal dashboard. It lists every node and shows each one's live telemetry: echo, round-trip time, cache hit rate and bytes saved. You can drill into a node to see its sensors and open a single reading in full. It reads by name in two calls, "), mono("sensors/list"), t(" for the id and then "), mono("sensor_data/get"), t(" for the payload, sent to "), mono("databasehost.frognet"), t(" through the proxy. It writes nothing to the store. It probes each node's "), mono("frognet_echo.php"), t(" itself. A sensor''')

# P2-2 tuple key
sub(r'''a named row with three parts. A "), b("name"), t(" that is its key \u2014 a sensor's full name, like "), mono("SD:barn/temp"), t("; a "), b("type"), t(" that says which service or kind it belongs to; and a "), b("value"), t(", a small blob of JSON \u2014 the reading itself,''',
    r'''a named row. It is addressed by three coordinates: a "), b("name"), t(" (a sensor's full name, like "), mono("SD:barn/temp"), t("), a "), b("type"), t(" that says which service or kind it belongs to, and the "), b("address"), t(" the writer gives (empty when it gives none). Its "), b("value"), t(" is a small blob of JSON \u2014 the reading itself,''')

# P2-3 upsert payload
sub(r'''"          value:54.6, ts:1719... }        -> one row, overwritten"''',
    r'''"          jsonData:{ value:54.6, ts:1719... } }   -> one row, overwritten"''')

# P2-4 unpublished .1
sub(r'''A live .1 that has not published is still a candidate; its capability is unknown, so it ranks at the floor, and the highest .1 breaks the tie. Every node''',
    r'''A live .1 that has not published is still in the pool, but its capability is unknown, so each role's own gate decides. The database gate (mysql running) refuses it. If no candidate qualifies, the name falls to the highest live .1. Every node''')

# P2-5 host reset watcher
sub(r'''P([ t("What FrogNet runs is a per-process watcher on the name: when "), mono("databasehost.frognet"), t(" resolves somewhere new, it calls each module\u2019s "), mono("hostReset()"), t(" ("), mono("core/frognet_host_reset.py"), t("). On a region''',
    r'''P([ t("What FrogNet runs is one watcher in the frognet-daemon. Every 15 s it reads "), mono("databasehost_control.frognet"), t(" from "), mono("/etc/hosts"), t(". When that changes, it re-publishes this node's capability ("), mono("core/role_publish.py"), t(", "), mono("core/frognet_host_reset.py"), t("). It does this in broker mode only; on a region the publish returns at once. No application module is registered with it. An application detects the change itself, by write-then-read or by the store's generation counter. On a region''')

# P2-6 + P2-7 section 19
sub(r'''Nodes, tunnels and choruses are rebuilt by nodes checking in. A "), b("pond"), t(" is not: after a wipe''',
    r'''Nodes and tunnels are rebuilt by nodes checking in, and each node rejoins "), mono("entire_pond"), t(". Named choruses and their memberships are not: after a reset the operator recreates the ponds and choruses, and members rejoin through the chorus API. A "), b("pond"), t(" is not rebuilt either: after a wipe''')
sub(r'''A pond whose broker goes dark loses only the ability to onboard a stranger or open a new cross-internet tunnel; everything already formed keeps running.") ]),''',
    r'''If the broker service stops, tunnels already formed keep forwarding, because the forwarding is in the kernel; what is lost is onboarding and new tunnels. If the broker machine goes down, every cross-site tunnel goes with it, because they all terminate there. Each LAN keeps running on its own.") ]),''')

# P2-6 + P2-7 section 20
sub(r'''A broker going dark costs you the ability to introduce a stranger or mint a new tunnel; everything already formed keeps running. A "), mono("db reset"), t(" is recoverable, but not by the nodes alone: nodes, tunnels and choruses are rebuilt by nodes checking in, and the ponds are not. The operator recreates each pond''',
    r'''If the broker service stops, you lose introductions and new tunnels and the formed tunnels keep forwarding. If the broker machine goes down, the cross-site links go with it, and every LAN keeps running on its own. A "), mono("db reset"), t(" is recoverable, but not by the nodes alone: nodes and tunnels are rebuilt by nodes checking in, and each node rejoins "), mono("entire_pond"), t("; the ponds and the named choruses are not. The operator recreates each pond''')

# P2-8 frognet-pond remove
sub(r'''"frognet-pond remove              remove a pond", "frognet-pond db ...              the database commands below"''',
    r'''"frognet-pond remove <pond> <chorus> <node>   drop a node from a chorus", "frognet-pond db ...              the database commands below"''')

# P2-9 forwarding
sub(r'''The broker programs "), mono("iptables"), t(" and netfilter moves the packets; nothing''',
    r'''The broker installs routes between the tunnel interfaces inside the pond\u2019s network namespace, plus "), mono("iptables"), t(" port forwards into it, and the kernel moves the packets; nothing''')

# P2-10 region route admission
sub(r'''applied by the tunnel daemon, which admits LAN candidates with one probe each and gates hub routes on the WireGuard handshake.") ]),''',
    r'''applied by the tunnel daemon. It installs a planned LAN route at once, because the member's live session and a lease pair written by both ends already say the link is there. It gates hub routes on the WireGuard handshake.") ]),''')

# P2-11 forwarder routes
sub(r'''Routes to the forwarder's own "), mono("/24"), t(" go straight out the tunnel; routes to subnets "), i("behind"), t(" it go out the same tunnel but "), mono("via"), t(" the forwarder's "), mono(".1"), t(". Two LANs''',
    r'''Every "), mono("/24"), t(" the forwarder carries, its own and the ones "), i("behind"), t(" it, goes out the same tunnel as "), mono("dest dev wgN"), t(" with no gateway. The far kernel routes the inner packet onward. Two LANs''')
sub(r'''"   10.133.145.0/24  via 10.133.144.1   (SEA-2, relayed)",''',
    r'''"   10.133.145.0/24  dev wg2            (SEA-2, relayed through SEA-1)",''')

# P2-12 roles held by nodes
sub(r'''P([ b("A role is not reserved to a node."), t(" Any machine on the pond can register for one \u2014 database or media, the two roles that exist. The election reads capability, not pedigree, so a well-provisioned box that is not itself a FrogNet node can host the historical FrogNet Region backing store or win the mediahost election. (The Communicator''',
    r'''P([ b("Roles are held by nodes."), t(" Every live FrogNetHost (.1) is a candidate for both roles that exist, database and media, and the election picks among them on capability. A box that is not a FrogNet node cannot hold a role. (The Communicator''')

# P2-13 template learning
sub(r'''The first exchange of a structure goes raw \u2014 the whole HTTP request and answer \u2014 and both sides learn its template from it: its shape,''',
    r'''The first exchange of a structure goes raw \u2014 the whole HTTP request and answer \u2014 and the sending proxy learns its template from it: its shape,''')
sub(r'''That template is learned from the raw exchange a moment ago; nobody sends a template. From then on the structure is known on both sides and only the values move.''',
    r'''The far daemon uses the template its own node's proxy learned for the same key. Neither side sends a template. If the far side does not have one yet, it says so and the exchange goes raw again. Once both sides have it, only the values move.''')

done()
