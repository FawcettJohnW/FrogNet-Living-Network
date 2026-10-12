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
import patchlib as PL

def span(start, end):
    a = PL.s.index(start); b = PL.s.index(end, a)
    return PL.s[a:b]

# ---------------- Part XV1/2 ----------------
# 3. opening note: product name and licence split
sub('it is a separate commercial product built "), i("on"), t(" FrogNet, sold and supported by Fawcett Innovations. Nothing',
    'it is Freemium, a separate offering from Fawcett Innovations built "), i("on"), t(" FrogNet and kept out of the open tree. Nothing')

# 1. section 56: interim label, withdrawal kept, later same-campaign numbers
sub('P([ t("Thirty-seven complete runs across twelve campaigns, every rank reporting. Per-element cost, taken from the slowest rank',
    'P([ t("Thirty-seven complete runs across twelve campaigns, every rank reporting. The interim figures, 13 September, per element, taken from the slowest rank')
sub('P([ t("That is the result, and it is worth being plain about what it is not. It is not faster. An earlier claim that it was \\u2014 by about sixty percent \\u2014 did not survive',
    'P([ t("That was the interim result. An earlier claim that it was faster \\u2014 by about sixty percent \\u2014 did not survive')
sub(span('      P([ t("What it "), i("is"), t(": a Python implementation, on a shared-memory substrate,', '      P([ t("On a single machine it loses'),
    U(r'''      P([ t("Measured properly afterwards — gloo as the control in the same campaign, minutes apart — the sliced backend won every completed comparison. gloo's best step took 1.63 times as long as the sliced backend's with PyTorch's bucket view off, 1.12 times as long with it on, and never less than 1.03 times. The sliced backend's median step stayed in a 24 percent band while gloo's moved 127 percent on the same machines, because a ring is held to its slowest link and independent reads are not. gloo, the sliced backend and the faithful hook each produced one parameter hash on every rank and the same held-out loss, 0.4940.") ]),
'''))

# 2. section 58: scope of the first two rows
sub('        "concepts in the worker   2                9",\n      ]),\n',
    '        "concepts in the worker   2                9",\n      ]),\n'
    '      P([ t("The first two rows come from a four-worker parameter-publication fixture, not from the all_reduce path. An all_reduce still needs every rank.") ]),\n')

# ---------------- Part XVI ----------------
# 4.
sub('P([ t("Everything in Part XIV works, and it is worth being clear',
    'P([ t("Everything in Part XV works, and it is worth being clear')

# 5. section 60: built in the C++ Communicator; byte fan kept as the Python relay's history
sub(span('      P([ t("This is the largest thing the media path does not do yet,', '\n      // [RESTORE_53B_V1]'),
    U(r'''      P([ b("Closed in the C++ Communicator."), t(" The sender uploads one stream. The media server passes it through to every viewer who can take it, and for a viewer who cannot, decodes it once and re-encodes it at the size that viewer's measured link allows — once per size in use, never larger than the upload. One slow viewer no longer lowers anyone else's picture. The cost is that the media server is no longer content-blind: its CPU is part of its capability.") ]),
      P([ b("What the Python relay did."), t(" The Python relay was a byte fan. It could pass a frame to a viewer or drop it, and that was the entire set of moves available to it — it never decoded anything, which is why it was small and fast and content-blind. So the only way it could serve a constrained client was to drop whole frames, and dropping every third frame of a 1280x720 stream does not produce a smaller picture. It produces a "), i("slideshow at full resolution"), t(": a low frame rate, no pixellation, and a viewer wondering whether the call has frozen.") ]),
      P([ t("Its answer was one stream and one rate. Every participant published what it alone knew — a sender what it was sending, a receiver what it was getting and whether it was keeping up — and every participant derived the same rate from those rows. The slowest published report bounded the call. The relay's own report moved a sender once per hold rather than once per window it persisted — "), mono("[ONE_STEP_PER_HOLD_V1]"), t(". Nobody sent faster than the slowest end could take, and that was the ceiling of the shape: the relay only ever had the one stream to forward.") ]),
'''))

# 6. setup_lillypad
sub('"$ setup_lillypad.bash <name> <ip> <broker-host:port> --pond"',
    '"$ sudo setup_lillypad_v4.bash <NetworkName> <GatewayIP> <BROKER_URL> --pond <PondName>"')

# 7. names
sub('t(" "), mono("frognet_net_start"), t(" reports that the job failed.',
    't(" "), mono("frognet-netstart"), t(" reports that the job failed.')
sub('"$ frognet_ssid_projection on | off"', '"$ frognet-ssid-projection on | off"')
sub('mono("make_host_json build"), t(" is supposed to run at the end of the merge',
    'mono("makeHostJson.bash rebuild"), t(" is supposed to run at the end of the merge')
sub('Minutes, not seconds, and a "), mono("run_merge"), t(" on the impatient end',
    'Minutes, not seconds, and a "), mono("runMerge.bash"), t(" on the impatient end')

# 8. tunnel restart
sub('"   the restart tells the broker to flush and reissue"',
    '"   the restart clears dead local interfaces and rebuilds", "   the tunnels from what the broker currently says"')

# 9. region mode, end of the Convergence subsection
sub(' is supposed to run at the end of the merge and sometimes has not. Run the merge again.") ]),\n',
    ' is supposed to run at the end of the merge and sometimes has not. Run the merge again.") ]),\n'
    '      P([ t("On a node in an FD&F region none of this applies. A change reaches every node as one region event, in well under a second on a LAN; there is no neighbour-by-neighbour propagation, no makeHostJson and no merge notification. Its tunnel is one WireGuard channel to the hub; to re-assert it, run "), mono("sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>"), t(" (or the x86_64 installer) again. Add "), mono("fdf-client"), t(" to the services to check.") ]),\n')

# 10. receive-side audio priority: two planes, delete the entry
sub(span('      bullet([ b("Receive-side audio priority."),', '      bullet([ b("Backpressure is reported in aggregate."),'), '')

# consequence of 5: the rung entry's last sentence
sub(' What survives of this entry is the sentence above it: one stream and one rate, bounded by the slowest end, until the relay can send a client something other than what arrived.") ]),',
    ' The C++ media server now sends a client something other than what arrived, so one stream and one rate bound only the Python relay (Chapter 60).") ]),')

# 11. region policy
sub(' Early FrogNet Memory commonly used last-write-wins plus age-out for current-state cells.',
    ' FrogNet\'s default Region holds one value per coordinate \\u2014 current state, not history \\u2014 with age-out for current-state cells.')

# 12. guild
sub(span('      ...gld([ t("Read that list again as a work list, because that is what it is.', '      ...partTitle("Specification C"'),
    '      ...gld([ t("Read that list again as a work list. The edges still open are small and bounded and each has an obvious oracle \\u2014 naming whose frame the media server discarded is information it already has. If you want a first contribution that is real rather than ceremonial, take one from here.") ]),\n')

# ---------------- Specification C: REWRITE ----------------
SPEC_C = r'''      lead([ t("FD&F is the second transport specified by this Croakus. LISP locates, RINA names, and Ribbit holds the state and wakes the readers. Every member connects outward to one RAM manager, writes what it knows about itself there, and learns about the others only from there. Nothing is sent member to member. It began as a test of the boundary — if Ribbit is unchanged above both FrogNet and FD&F, the boundary has earned its name — and it is now also what FrogNet nodes join in place of the broker and the getHosts crawl.") ]),
      img("fdf_ram_manager_topology.png", 620, 350),
      exLabel("FD&F: every member connects outward to the RAM manager; no member-to-member path through FD&F"),

      H1("62.  The Three Separations"),
      P([ t("FD&F began as a deliberate question: whether the architecture right for the real world is a hybrid of LISP, RINA and FrogNet. It came after lispers.net and rlite had each been converted onto Ribbit, and it took its present shape when the incidental plumbing of each was stripped away. "), b("LISP separates identity from location."), t(" "), b("RINA separates naming and scope from any one physical structure;"), t(" in FD&F a DIF is a shared RAM, a region. "), b("Ribbit separates distributed computation from message choreography."), t(" With those three in place, much of the machinery each implementation carried moves to a lower layer or goes.") ]),
      img("fdf_rina_lisp.png", 620, 345),
      exLabel("RINA names the thing; LISP locates it; Ribbit defines what programs do once attached"),
      P([ t("The name is literal: "), b("Farinacci, Day & Fawcett"), t(". It acknowledges the three lines of work whose separations meet here. It does not imply endorsement by Dino Farinacci or John Day; the architecture and the measurements stand on their own.") ]),

      H1("63.  The FD&F Participant Model"),
      P([ b("A member is an individual machine:"), t(" a FrogNet node, a Linux or Windows computer, a phone. It may sit on a LAN, behind NAT, or anywhere on the Internet. It opens no inbound port.") ]),
      P([ t("Each member holds one FNWP session to its region's RAM manager ("), mono("rina-host"), t("): three outbound TCP connections — requests, replies, and a data socket for large replies — framed FNW1 and compressed with BLDC-1 against the primed template store ("), mono("fdf.prime"), t("). On the same 605 sends, FD&F's C++ BLDC-1 is byte-identical to FrogNet's Python BLDC-1.") ]),
      P([ b("Liveness is the session."), t(" Both ends set TCP keepalive (idle 2 s, three probes 1 s apart) and a 5,000 ms user timeout, so a silent loss is seen in about four seconds and a clean close in tens of milliseconds. When a session ends, everything it wrote goes with it: the announcement, the mappings, the leaf address, the where cells, the attention notices. Gone is gone.") ]),
      P([ b("Discovery is state."), t(" A member announces its name, its IPv4 addresses, its capability and its facts (a JSON object it keeps in "), mono("/opt/fdf/var/facts.json"), t(" and re-announces when the file changes). Capability is FrogNet's capability probe: a FrogNet node's daemon runs "), mono("frognet_capability_probe.sh"), t(" and publishes it as "), mono("facts.capability"), t(", and every node elects the databasehost and mediahost from those facts; "), mono("fdf-client"), t(" on a plain machine reports cores, memory and OS under the same field names. The participant list is a query. Change is a held read. Nothing broadcasts on a LAN and nothing propagates neighbour to neighbour.") ]),
      P([ b("Applications talk only through the shared space."), t(" Ribbit programs and web pages read and write the region; page input goes in as "), mono("<App>.<QueueName>.<submitter>"), t(". rlite's own programs run unchanged against "), mono("librina_region.so"), t(", each SDU one request through the RAM manager. A qualification capture fails on any member-to-member application packet.") ]),

      H1("64.  The Six Transport Definitions"),
      img("transport_six_definitions.png", 620, 350),
      exLabel("Ribbit binds to six transport definitions, not to a routing implementation"),
      dataTable(["Definition", "FD&F answer"], [
        ["Locate", "Given the area's name, the RAM manager's address: from its LISP mapping, or given directly."],
        ["Connect", "The FNWP session to that RAM manager."],
        ["Announce", "Name, addresses, capability and facts, written into the region (op announce)."],
        ["Participants", "The live announcements (op participants); a member whose session ended is not listed."],
        ["Capability", "One participant's capability, from its announcement."],
        ["Change", "A held wait that returns when the participant set changes (op watch_participants)."],
      ], [1800, 7500]),
      P([ b("No definition carries a route, a path or a neighbour"), t(" ("), mono("src/transport.hpp"), t("). That omission is normative. A transport that routes keeps its routing inside, as FrogNet does. A conformance client that knows only these six gets an answer to each ("), mono("transport-conformance"), t(").") ]),
      P([ t("The FrogNet region (Chapter 66) does keep a graph of the network in the region, and FrogNet nodes route their own IP traffic from it. That graph is data FrogNet reads; it is not a definition Ribbit binds to, and the RAM manager routes nothing.") ]),

      H1("65.  Name, Location, and Session"),
      P([ b("RINA names things; LISP locates them.") ]),
      P([ b("Names."), t(" An area is a DIF. Inside it a machine is named by identity, "), mono("w:<guid>"), t(", never by address, and a service says where it runs as "), mono("w:<guid>:<port>"), t(" in a where cell. Each reader resolves the identity in its own scope with the region's "), mono("resolve"), t(" op: a node's .1, a leaf's /32, or "), mono("ENOENT"), t(" when that member is gone. A where cell written with an address is not taken.") ]),
      P([ b("Locations."), t(" A FrogNet node's /24 is a LISP EID prefix. "), mono("fdf-client"), t(" registers it with a signed Map-Register (HMAC under the pond's site key) whose locator set names the hub. The region refuses a /24 that overlaps a live member's, and a mapping that a live session already holds, naming the holder both times. The hub installs a prefix only from a verified mapping that names it.") ]),
      P([ b("Bootstrap."), t(" A machine must be told one thing: where the shared space is. That is either the area's name — the Map-Resolver's address is then derived from the name by rule (SHA-256 of the name into a reserved prefix, bound anycast by whoever serves the area's mapping) — or the RAM manager's address directly. The FrogNet region uses the direct form: "), mono("fdf-region-join"), t(" defaults to "), mono("165.232.153.144:51888"), t(", area "), mono("frognet"), t(". The bootstrap is meant to be encrypted; today it is in the clear, and the only signature is the HMAC on mappings.") ]),
      P([ b("Recovery."), t(" A RAM-manager restart ends every session. Members reconnect, re-announce and re-register; nothing tells a neighbour, because FD&F has no neighbour relation. In name mode a member holds a read on the area's mapping and follows the RAM manager to its new address by itself. With a directly given address there is no mapping to follow.") ]),

      H1("66.  The Region, the Ponds, and the Bridges"),
      P([ b("The region."), t(" A FrogNet pond on FD&F is a region. On the droplet StreamingFrog (165.232.153.144): the RAM manager "), mono("rina-host"), t(" on TCP 51888 ("), mono("fdf-ram-frognet"), t(", Permanent records in "), mono("/opt/fdf/var/area-frognet.perm"), t("), the hub "), mono("fdf-hub"), t(" on UDP 50889 ("), mono("wg-frognet"), t(", 10.253.253.254/16), and the hub's own membership ("), mono("fdf-client-frognet"), t("). A node joins with one installer ("), mono("sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>"), t(", or the x86_64 one), which runs "), mono("fdf-region-join"), t(": site.env, join.env, "), mono("fdf-client"), t(", and the tunnel daemon restarted in region mode.") ]),
      P([ t("Each node writes its facts ("), mono("fdf-facts"), t(": its addresses, the leases it holds and gives with their MACs, its uplink, its hub edge 10.253.A.B). Every node builds the same graph from everyone's rows ("), mono("fdf_graph.py"), t(") and its daemon applies its own slice: LAN routes, a default toward the chosen exit, hub routes once the handshake is up, "), mono("/etc/hosts"), t(", the elections. The hub applies its slice: one WireGuard peer per live member, AllowedIPs only from verified mappings, one owner per /24. Each region event is one reconcile; nothing polls.") ]),
      P([ t("That replaced the broker's pairwise tunnels (2·N(N−1)/2 interfaces) with one interface and N peers, and the getHosts crawl, makeHostJson and propagateNotification with one "), mono("world"), t(" read per event. Echo and aliveness are answered from the region. IP between nodes rides WireGuard to the hub on the RAM manager's host, never node to node across the Internet; LAN paths stay direct, which is FrogNet's own transport. A device off the mesh is a leaf: the region gives it the lowest free 10.252.X.Y/32, the hub peers it, and the nodes route and name it. Nobody types an address.") ]),
      P([ b("Ponds."), t(" A machine is in one forward pond and any number of participant ponds ("), mono("ponds/<area>.env"), t(", one "), mono("fdf-client@<area>"), t(" each, the "), mono("forward"), t(" file names which). The forward pond gets the full facts, the routes, the names and the tunnel. A participant pond gets "), mono("{\"role\":\"participant\"}"), t(", shared memory and attention notices — the tuple "), mono("(pond, attention, w:<guid>)"), t(". "), mono("fdf-forward <area>"), t(" switches in place, without reconnecting. Each RAM manager publishes "), mono("(pond, config, net)"), t(" from its "), mono("net.json"), t(": "), mono("reach"), t(" none, listed or all, and "), mono("map"), t(" allowed or refused. Members can read it and cannot write it. A node reaches another pond's services as that pond's config allows, on its own interface "), mono("rch-<h>"), t(", mapping any destination that collides with its own addresses 1:1 into 100.64.0.0/10. A refused reach ends that pond's session.") ]),
      P([ b("The bridges."), t(" The phone and the PC are bridges: members of every pond they list at once, with one TUN. The phone runs the FrogNet member app (Kotlin) and "), mono("fdf-tun"), t(" (Go, wireguard-go): one VPN TUN at 100.127.255.1, one WireGuard device per pond, and the 1:1 rewrite at the IP header, including ICMP errors' quoted headers. On the phone all of 10/8 is the forward pond's, so every other pond is aliased. The Windows PC runs the same Kotlin member on a bundled Java runtime, "), mono("fdf-tun.exe"), t(" on a Wintun adapter, and netsh for its address and routes. An application asks for "), mono("w:<guid>:<port>"), t("; exactly one network may answer, and two answers are refused as ambiguous. A released alias block returns to the same pond and prefix and to nothing else for 600 s. Addresses written inside application payloads are not rewritten; FrogNet's own applications name endpoints by identity, so they never carry one.") ]),
      P([ b("The Communicator on a region."), t(" A node runs "), mono("comms-ram"), t(" (8800) and "), mono("comms-media"), t(" (8994) with "), mono("fdf-comms.sh"), t(", which writes the where cell "), mono("(pond, where, w:<guid>) {\"comms\":{\"ram\":\"w:<guid>:8800\",\"media\":\"w:<guid>:8994\"}}"), t(". The cell lives only while that node's session does. The desktop Communicator ("), mono("comms-app"), t(", C++/Qt) gets its servers from the bridge ("), mono("fdf-bridge.cmd ask comms"), t("); the Android Communicator is a screen of the FrogNet app, opened with its pond's servers.") ]),

      H1("67.  What Was Removed, and What Is Measured"),
      P([ t("The October 7 restart removed working machinery: member-to-member UDP/Ethernet data, route derivation in the agents, route-change resend, the NAT relay and its election, the LAN-join broadcast, host-change neighbour propagation, the Map-Resolver probe and election, the topology rig, and the experiments that existed to qualify them. The code worked. The boundary was wrong. Removing it took the per-machine agent from 784 lines to 327. Joining FrogNet to the region then removed, in region mode, the broker, the crawl, makeHostJson, the store's capability tuples and the merge pokes. A mechanism does not earn a layer because it is already written.") ]),
      P([ b("No fallbacks."), t(" A fallback that reaches around the architecture can turn a test green and invalidate the result. Qualification prints configured state, counts observed behaviour rather than exit codes, captures the wire, and fails on any member-to-member application packet.") ]),
      P([ b("Measured, in the sandbox:") ]),
      bullet([ t("rlite's 30 integration tests pass with flow data through the RAM manager. Unfavourable: 1.03 requests and about 520 µs per transaction, against about 22 µs on the old member-to-member path.") ]),
      bullet([ t("Three machines announce, are listed with name, IP and capability, and one that leaves is gone; 110 packets on the shared network, 0 between machines.") ]),
      bullet([ t("Given only the area's name, three machines derive the same Map-Resolver address and join; after the RAM manager moves, all three re-attach by themselves in about 300 ms, with no packet between machines and no DNS packet.") ]),
      bullet([ t("A flat single-unit send cut the frame from 111–117 bytes to 87–88 and the bytes out by 23 percent. The LISP mapping region agrees with lispers.net in seven of seven cases, and a 5-second TTL expires at about 5.1 s. Permanent records survive kill -9, and a reliable flow completes 200/200 across a RAM-manager restart.") ]),
      bullet([ t("Unfavourable: FrogSim's 20 topologies pass 6 — after the restart FD&F reaches only where IP already routes to the RAM manager — and a machine that changes its address keeps a stale locator.") ]),
      bullet([ t("The region, build r19-a15 in network namespaces: region-loop 20/20 (the Communicator and the phone's code included); reach 68/68; fdf-tun 20/20; ponds-daemon 21/21; hub 25/25; graph 17/17. A change on one node is replanned on the others in about 80 ms on a LAN. A member's death is seen in about 20 ms when clean and about 4 s when silent. All nodes name the same new databasehost within one event of its loss.") ]),
      P([ b("On hardware:") ]),
      bullet([ t("The droplet and the FrogNet fleet (Seattle7, Seattle3B, Seattle5, HappyDog, New-York-2, AI-Host) ran the region through builds r5–r13; each fix in that range was found there.") ]),
      bullet([ t("2026-10-07: a Windows laptop behind a home NAT, every connection outward to the droplet, held an 850x480, 24 fps call at 400–800 kb/s through the droplet's RAM and media servers.") ]),
      bullet([ t("2026-10-10: the FrogNet app's Communicator ran on a physical phone as a leaf.") ]),
      bullet([ t("2026-10-11: a Windows 11 PC joined as a bridge, first on Seattle5's LAN, then from another network through the hub, and logged into a node through it.") ]),
      bullet([ t("Not yet shown on hardware: the phone's bridge ("), mono("fdf-tun"), t(" on a handset), a second pond reached from the PC, and the join cost per member on the current build.") ]),
      P([ t("The bad numbers stay because qualification is not advertising. The question is first whether the abstraction is correct and transport-independent; performance follows a green contract.") ]),
      img("evidence_method.png", 620, 345),
      exLabel("The evidence discipline: incumbent behaviour is the oracle; measurements are data, including the bad ones"),

      H1("68.  Why FD&F Matters to Ribbit"),
      P([ t("FrogNet and FD&F disagree about transport internals. FrogNet discovers a changing physical graph, splits, heals and merges, and stays sovereign and offline-first. FD&F puts the meeting place at one reachable RAM manager and keeps routing out of its definitions. The same Ribbit programming surface runs above both, and FrogNet nodes now run their own discovery and tunnels on an FD&F region. The programming surface is therefore not an accident of FrogNet's topology.") ]),
      img("multi_transport.png", 620, 345),
      exLabel("One Ribbit programming model above transports that solve reachability in different ways"),
      ...gld([ t("The point of FD&F is not that every network should look like FD&F. Once the upper programming surface stops depending on how the transport works, transports become replaceable answers to a lower question. FrogNet can be sovereign. FD&F can be manager-centred. An InfiniBand implementation can answer the same definitions its own way. Ribbit should not care.") ]),
'''
sub(span('      lead([ t("FD&F is the second transport specified by this Croakus. It is intentionally unlike FrogNet.', '\n      // [RESTORE_WHAT_FOLLOWS_V1]'),
    U(SPEC_C))
sub('  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",\n',
    '  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",\n'
    '  "66-what-was-removed": "67-what-was-removed-and-what-is-measured",\n'
    '  "67-fd-f-qualification": "67-what-was-removed-and-what-is-measured",\n')

# ---------------- Part XVIII ----------------
# 16.
sub('lead([ t("Everything before this part is built and running. This part is not.',
    'lead([ t("Everything in the parts before this one is built and running, except what Part XVI lists. This part is not.')
# 13.
sub('Specification C answers the first half: FD&F runs Ribbit over a transport that shares no machinery with FrogNet.") ]),',
    U(r'''Specification C answers the first half: FD&F runs Ribbit without FrogNet's discovery, routing, elections, broker or tunnels. It shares FrogNet's wire — FNWP framing and BLDC-1, byte-identical on the same sends — and FrogNet's capability probe. It also runs the other way round: FrogNet nodes now join an FD&F region and take their membership, addresses and tunnels from it.") ]),'''))
# 14.
sub('The honest state of this: Ribbit does not do it, no design exists, and the parts of the problem that are hard',
    U("The honest state of this: a machine can now be in several ponds at once, each pond its own memory, and reach into another as far as that pond’s own configuration allows. That is membership in two memories, not two memories seeing parts of each other. Ribbit does not do the second, and no design for it is built. The parts of the problem that are hard"))
# 15.
sub('It now exists: the C++ RAM host keeps a Region in memory and persists it to a plain disk file, with no MySQL, and FD&F\\u2019s RAM manager is that host.',
    'It now exists: FD&F\\u2019s RAM manager, built on the C++ RAM host, keeps the Region in memory and appends its Permanent records to a plain disk file, with no MySQL.')

done()
