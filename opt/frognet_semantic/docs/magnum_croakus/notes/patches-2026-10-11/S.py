import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *

# 1. sessions are three outbound TCP connections per program; Communicator media rides fnav
sub('each machine opens one outbound socket to it, carrying FNWP,',
    'each participant program opens one FNWP session to it (three outbound TCP connections: requests, replies, data),')
sub('"The FD&F memory every machine opens one outbound socket to; all data goes through it, never member to member."',
    '"The FD&F memory every participant program holds one FNWP session to (three outbound TCP connections); all data goes through it, never member to member."')
sub('under FD&F the transport opens one per machine to the RAM manager; FrogNet\'s compatibility path opens them node to node; an application may open its own, as the Communicator does for media.',
    'under FD&F each participant program opens its own session to the RAM manager (three outbound TCP connections); FrogNet\'s compatibility path opens them node to node; an application may open its own, as the Communicator does to its memory server, comms-ram (its media rides a separate wire, fnav, to comms-media).')

# 2. FrogNet_monitor -> frognet_monitor; the monitor chapter is 42c in Part XIII½
sub('b("FrogNet_monitor"), t(" (Part VI) is the first stop:', 'b("frognet_monitor"), t(" (Part XIII\\u00bd, chapter 42c) is the first stop:')
sub('t("That reader is real. "), b("FrogNet_monitor"), t(", the network', 't("That reader is real. "), b("frognet_monitor"), t(", the network')
sub('The FrogNet_monitor info center from Part VI is', 'The frognet_monitor info center (Part XIII\\u00bd, chapter 42c) is')

# 3. derive_rate: the August Python rule, not the C++ media server's
sub('A receiver now publishes what it is getting and whether it is keeping up, and every participant derives the rate from those rows \\u2014 the receiver is heard directly rather than inferred from drop reports. The C++ media server now sends a client something other than what arrived, so one stream and one rate bound only the Python relay (Chapter 60).',
    'In the Python Communicator a receiver published what it was getting and whether it was keeping up, and every participant derived the rate from those rows \\u2014 the receiver was heard directly rather than inferred from drop reports. That derivation is gone. In the C++ Communicator the media server sets each viewer\\u2019s rate and transcodes the one upload down to it, so one stream and one rate bound only the Python relay (Chapter 60).')

# 4. games: the engine is an example; the /game origin and boardgame role were removed 25 September 2026
sub('SotF and the turn-based game handler are evidence of that extension model, not new Ribbit primitives.',
    'SotF is evidence of that extension model. The turn-based game handler is kept as a design example: an early /game origin and a boardgame role were built and were removed on 25 September 2026, and FrogNet ships no games. Neither is a new Ribbit primitive.')
sub('the way you work on a frog, oracle first.") , t(" And this',
    'the way you work on a frog, oracle first.") , t(" The game engine in this part is a worked example. The /game origin and the boardgame role it ran under were removed on 25 September 2026; FrogNet ships no games today.") , t(" And this')
sub('t("One elected engine governs the board \\u2014 the board-game role from Part V, resolved by name as "), mono("boardgame.frognet"), t(". Turn-based play is elected pond-wide.")',
    't("One elected engine governs the board, resolved by one pond-wide name such as "), mono("boardgame.frognet"), t(". The boardgame role that did this was removed on 25 September 2026; the election shape is the one Part V describes for the roles that remain.")')
sub('Backgammon is the same "), mono("_game"), t(" machinery with a heavier rulebook \\u2014 and the shared board-game engine carries it alongside hearts, connect four, reversi, and liar\\u2019s dice. It also has a bug',
    'Backgammon is the same "), mono("_game"), t(" machinery with a heavier rulebook. The shared board-game engine carried it alongside hearts, connect four, reversi, and liar\\u2019s dice until the games were removed on 25 September 2026. It also had a bug')
sub('That is why a single change gives a lobby and spectators to backgammon, hearts, connect four, reversi, and liar\\u2019s dice at once',
    'That is why a single change gave a lobby and spectators to every game that engine carried at once')

# 5. no photos application in the tree
sub('The box that handles the family photos in good weather is the same box that coordinates',
    'The box that serves the network in good weather is the same box that coordinates')
sub('The box that handles the family photos in good weather is the box that coordinates',
    'The box that serves the network in good weather is the box that coordinates')

# 6. Part XII: say what listens
sub('P([ t("That is a description of the deployment, not a claim about how hard it would be to break in. There is no application attack surface to harden, because there is no application attack surface.") ]),',
    'P([ t("That is a description of the deployment, not a claim about how hard it would be to break in. The application listeners that do exist are few, and chapter 36 names each one.") ]),')
sub('P([ t("So a node presents "), b("no surface"), t(" to the internet at all. This is not a firewall tightened until only the right ports are open. The exposed request/reply endpoint \\u2014 the always-open port waiting to be asked \\u2014 is precisely the thing FrogNet dropped when it dropped request/reply (Part IX). There is no door to harden because none was ever cut.") ]),',
    'P([ t("So a node behind its uplink presents "), b("no surface"), t(" to the internet. This is not a firewall tightened until only the right ports are open. What a node does listen on is one memory per application, not one endpoint per operation: where the Communicator runs, its memory server "), mono("comms-ram"), t(" binds "), mono("0.0.0.0:8800"), t(" and its media server "), mono("comms-media"), t(" binds "), mono("0.0.0.0:8994"), t(" ("), mono("fdf-comms.sh"), t("). Anyone who can reach that node can reach those two ports. The per-operation request/reply endpoint is the thing FrogNet dropped when it dropped request/reply (Part IX).") ]),')
sub('There is no remotely callable application interface to discover, so there is nothing to characterise, so there is no request to construct.',
    'There is no per-operation application interface to discover. What listens is one memory per application, a RAM host that participants read and write over FNWP, and on a node behind its uplink the internet cannot reach it.')
sub('and none of them is reachable by talking to a published interface from anywhere on the internet.',
    'and on a node behind its uplink none of them is reachable by talking to a published interface from the internet.')
sub('Inbound access from the internet is not switched off on a FrogNet node \\u2014 it was never switched on. A misconfiguration cannot open what does not exist, and there is no rule to forget.',
    'Inbound access from the internet is not switched off on a FrogNet node behind its uplink \\u2014 nothing on the internet routes to it. Put its uplink on a public address and its listeners can be reached unless you filter them (chapter 36).')
# 8. semantic frames are on the node-to-node path, not the broker service
sub('P([ t("Be exact about what that buys, because it is easy to overclaim. Capturing one semantic frame',
    'P([ t("The semantic wire runs between nodes, inside "), mono("10/8"), t(", and that traffic crosses the broker in transit. Be exact about what it buys there, because it is easy to overclaim. Capturing one semantic frame')

# 7. the mediahost role exists but its service runs a missing fnav.py; calls go through comms-media
sub('A media host, which has to sit on the same wire as the people it serves, selects by locality and capability among directly-attached neighbors.',
    'The mediahost role selects by locality and capability among directly-attached neighbors. That role still exists in the code, but the service it starts runs an "), mono("fnav.py"), t(" that is not in the tree; the Communicator\\u2019s calls today go through its own media server, "), mono("comms-media"), t(" (Part XV), which is not elected.')
sub('can host the historical FrogNet Region backing store or serve as the media host.',
    'can host the historical FrogNet Region backing store or win the mediahost election. (The Communicator\\u2019s media server, comms-media, is not elected: it runs where it is started.)')
sub('It also names the media-host role, so the election places it on a capable, on-wire box.',
    'It also names the mediahost role, so the election places it on a capable, on-wire box. That role still exists in the code; the service it starts runs an fnav.py that is not in the tree. The Communicator\\u2019s calls today go through comms-media (Part XV).')
sub('The "), b("media host"), t(" selector gates on "), mono("ffmpeg"), t(" with "), mono("libvpx"), t(", then scores the measured CPU benchmark.',
    'The "), b("mediahost"), t(" selector gates on "), mono("ffmpeg"), t(" with "), mono("libvpx"), t(", then scores the measured CPU benchmark. (The role still exists; the service it starts runs an "), mono("fnav.py"), t(" that is not in the tree. The Communicator\\u2019s calls go through "), mono("comms-media"), t(".)')
sub('the databasehost picks the most-capable node running a database, a media host picks by locality and capability."',
    'the databasehost picks the most-capable node running a database, the mediahost role picks by locality and capability (its service runs an fnav.py that is not in the tree; the Communicator\\u2019s calls go through comms-media, which is not elected)."')

# 8. leftovers
sub('The GUID in "), mono("/etc/fnid"), t(" and the group token gate the broker, not the segment.',
    'The GUID in "), mono("/etc/fnid"), t(" and the pond password gate the broker, not the segment.')
sub('set or clear the broker (host, port, pond, password, choruses), generate a join QR code, toggle SSID projection, reboot.',
    'set or clear the broker (host, port, pond, password, choruses), make a QR code of an address (the page shows the node\'s new setup address), toggle SSID projection, reboot.')
# opening FD&F summary, to match Specification C
sub('A machine announces itself by writing its capability tuple.',
    'A machine announces itself by writing its name, addresses, capability and facts.')
sub('The hub\'s peers and routes are written from the region\'s tuples, and still nothing goes member to member.',
    'The hub\'s peers and routes are written from the region\'s tuples, and nothing goes member to member across the Internet. Nodes on the same LAN still reach each other directly; that is FrogNet\'s own transport, not FD&F\'s.')
sub('A move drops sessions; each participant re-resolves, reconnects, and reattaches. No application reconfiguration is required.',
    'A RAM-manager restart or move drops sessions. In name mode each participant re-resolves, reconnects, and reattaches, with no application reconfiguration. With a directly given address, as the deployed region uses, there is no mapping to follow.')

# 9. Afterword: 25 FOR THE GUILD panels render; 24 before the Afterword's own
sub('twenty-four of them carry a panel like this one',
    'twenty-four of them, before this one, carry a panel like this one')

done()
