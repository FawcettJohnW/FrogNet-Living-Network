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

# 1. front: local broker is Part VII
sub(r'(or a local one \u2014 Part III)', r'(or a local one \u2014 Part VII)')
# 2. front: book lives in the project's public repository
sub(r'It lives in a repository of its own, and that repository\u2019s Discussions',
    r'It lives in the project\u2019s public repository, and that repository\u2019s Discussions')
# 3. front: engine under the GPL v2
sub('"  the engine, under licence"', '"  the engine, under the GPL v2"')

# 4. Part 0 intro + 0.2 Connect row: one session per program
sub('every participant opens one outbound socket to the RAM manager and speaks FNWP on it;',
    'every participant program opens its own outbound FNWP session to the RAM manager (a request, a return and a data connection, all outbound) and never a connection to another member;')
sub('["Connect", "Open the machine\'s socket to that RAM manager; FNWP runs on it."]',
    '["Connect", "Open this participant\'s session to that RAM manager; FNWP runs on it. Liveness is the session."]')
# 5. 0.3 RAW / FULL
sub('It learns an instance shape, sends a FULL when the peer lacks that shape, a DIFF when fields change, and a REPEAT/SAME form when the learned instance is unchanged.',
    'It learns an instance shape. A message with no matching shape goes RAW; the first message of an instance whose shape is known goes FULL, carrying all its values; a DIFF follows when fields change, and a REPEAT/SAME form when the learned instance is unchanged.')
# 6, 7. 0.3 who opens the socket
sub(r'''b("FNWP carries the representation."), t(" It is the wire: length-prefixed, opcode- and sequence-tagged frames carrying BLDC-1 exchanges over a socket it does not open. Whoever opens the socket decides where it goes \u2014 under FD&F the transport opens one per machine to the RAM manager; FrogNet's compatibility path opens them node to node; an application may open its own, as the Communicator does for media.''',
    r'''b("FNWP carries the representation."), t(" It is the wire: length-prefixed, opcode- and sequence-tagged frames carrying BLDC-1 exchanges over a socket it does not open. Whoever opens the socket decides where it goes \u2014 under FD&F each participant program opens its own session to the RAM manager; FrogNet's compatibility path opens them node to node; an application may open its own, as the Communicator does to its memory server, comms-ram (its media rides a separate wire, fnav, to comms-media).''')
# 9. 0.5 hub
sub('Peer names and IPs may be known, but they are not used to create a second data plane.") ]),',
    'Peer names and IPs may be known, but they are not used to create a second data plane. When FrogNet runs on an FD&F region, its 10/8 IP traffic rides WireGuard hub-and-spoke through the hub on the RAM manager\'s host (fdf-hub, wg-frognet, UDP 50889). The hub\'s peers and routes are written from the region\'s tuples, and still nothing goes member to member.") ]),')
# 8. 0.5 locate: name or address
sub('The application starts from the stable area name. LISP resolves the current RAM-manager location. The manager self-registers its mapping when it starts or moves.',
    'A machine is given one thing: the area\'s name or the RAM manager\'s address. Given the name, LISP resolves the current RAM-manager location: the Map-Resolver\'s address is derived from the name (SHA-256 into a reserved prefix), never configured or looked up in DNS, and the manager self-registers its mapping when it starts or moves. The deployed region (r19-a15) gives each node the address outright: fdf-region-join writes RINA_RAM=165.232.153.144:51888.')
# 10. 0.6 hardware results and what is not shown
sub('programs have reattached after RAM-manager restart with a reliable 200-of-200 flow.") ]),',
    'programs have reattached after RAM-manager restart with a reliable 200-of-200 flow. On real hardware: the kart race and a Communicator call (850x480 at 24 fps, 400' + U('\u2013') + '800 kb/s) between machines that cannot reach each other ran through the droplet on 2026-10-07; since 2026-10-09 John\'s FrogNet nodes run as one region with the RAM manager and hub on the droplet; a Windows PC joined as a bridge on 2026-10-11, and the Communicator ran on a physical phone. Not yet shown: the phone as a bridge on a handset, FrogNet\'s 20 discovery topologies under FD&F\'s own discovery, and encryption (the only cryptography is an HMAC on the mapping registration).") ]),')

# 11. Spec A R.2 attribution
sub("The LISP conversion exposed why that distinction matters when one participant's heartbeat accidentally kept another participant alive.",
    "The Communicator exposed why that distinction matters: one participant's heartbeat rewrote a shared membership row and reinstated a member who had left.")

# 12. Part I opening
sub('every one of these is a pattern the system runs today.', 'each of these uses mechanisms the system runs today.')
# 25. invisible: FD&F region listener
sub('That is a scoped claim about applications, not a claim that a node presents nothing at all to the network.") ]),',
    'That is a scoped claim about applications, not a claim that a node presents nothing at all to the network. On a FrogNet node this holds. An FD&F region is different by design: its RAM manager is a reachable listener (the droplet\'s rina-host on TCP 51888), because every participant connects up to it.") ]),')
# 13. names and boundary
sub('Under FD&F that socket runs from each machine to the RAM manager.', 'Under FD&F each participant program opens its own session to the RAM manager.')
# 14. 1b argued, not proved
sub('the network already there, and the network already there does not notice.") ]),',
    'the network already there, and the network already there does not notice. Coexistence with an existing 10-space is argued, not yet proved in the simulator; Part VII' + U('\u00bd') + ' says what the proof is.") ]),')
# 15. 1b pointer
sub('t(", in the part on connecting across the internet. Read that when you want to know how.',
    't(", in Part VII' + U('\u00bd') + '. Read that when you want to know how.')
# 16, 17. live video
sub(r'as live video, with no call server in the middle and no account anywhere. And because the media is adaptive, a feed on a failing link does not freeze and die the way a normal video call does; it steps down \u2014 full video, to reduced video, to clean audio, to text \u2014 and climbs back when the link recovers.',
    r"as live video, with no account anywhere and no service outside hardware you own: the call's memory and media servers (comms-ram, comms-media) run on one of your own boxes. And because the media is adaptive, a feed on a failing link does not freeze and die the way a normal video call does; it steps down \u2014 full video, to smaller pictures (down to 160x120), to audio only \u2014 and climbs back when the link recovers.")
# 18. what picks the rung
sub('And the rung is chosen by "), b("queue pressure at the far end"), t(" rather than by a bitrate table: if the relay cannot get keyframes out, the rung does not fit, whatever the arithmetic says.',
    'And the rung is chosen by "), b("what the wire does"), t(", not by a bitrate table: the sender steps down when its frames back up or are dropped or its frame rate falls, and climbs only after clean seconds at full rate; the media server sizes each viewer\'s picture to that viewer\'s measured link.')
# 19. uplink
sub(r'The semantic compression is what makes this practical: because the wire carries only differences, that single shared uplink goes much, much further than its raw bandwidth suggests \u2014 a narrow satellite link can feed a working network of dozens.',
    "Traffic between the boxes themselves carries only differences, so the mesh's own coordination costs the shared pipe very little.")
# 20. subscriptions
sub(r'Photos, family chat, a shared calendar, a map of where everyone is, video calls \u2014 the things you pay four or five companies for every month, each holding a slice of your life \u2014 all run on your own boxes instead, private, with no per-person fees and nobody monetizing you on the side.',
    r'Family chat and video calls \u2014 the things you pay companies for every month \u2014 run on your own boxes instead, private, with no per-person fees and nobody monetizing you on the side; anything else that is shared state is one Region away.')
# 21. merge, not catch up
sub('When the internet comes back, they catch up.',
    'When the internet comes back, the islands merge; each box re-asserts its own truth into the one shared space, and nothing has to be reconciled.')
# 22. drop the ambulance (and the text rung, which entry 17 removed)
sub('the patient vitals reaching the hospital ahead of the ambulance, the sensor feed coming off the ridge, the call that drops to text instead of dying',
    'the sensor feed coming off the ridge, the call that drops to audio instead of dying')
# 23. on the record
sub('Every capability in this part has been exercised on real hardware over real links, and the load-bearing demonstrations are worth naming, because',
    'The load-bearing demonstrations below have been exercised on real hardware over real links; the other patterns above are built from the same mechanisms. The demonstrations are worth naming, because')
# 24. two years
sub('across the open internet for a year and a half', 'across the open internet for two years')

# 26. per month
sub('a five-dollar-an-hour cloud instance', 'a five-dollar-a-month cloud instance')
# 27. userland includes C++
sub('The whole userland is Python, PHP, and bash riding the usual Linux services',
    'The userland is Python, PHP, bash and C++ (the Ribbit platform and the FD&F binaries) riding the usual Linux services')
# 28. no i386
sub(r'b("An old i386 desktop "), t("\u2014 the fifteen-year-old tower in the closet.',
    r'b("An old x86-64 desktop "), t("\u2014 the tower in the closet.')
# 29. ARM vs x86-64 rebuild
sub("if the box's processor does not match the architecture the world was built for, the installer rebuilds",
    "if the box is ARM and the world was built on x86-64 (or the reverse), the installer rebuilds")
done()
