import sys; sys.path.insert(0, '/tmp/claude-0/verify1011'); from patchlib import *

# ---------------- P5 ----------------
# P5.1  Spec C 63: only the C++ fdf-client uses BLDC-1; the Kotlin member sends REQ_RAW (Wire.kt FNWP_RAW_MEMBER_V1)
sub('framed FNW1 and compressed with BLDC-1 against the primed template store ("), mono("fdf.prime"), t("). On the same 605 sends,',
    'framed FNW1. The C++ fdf-client compresses with BLDC-1, its templates primed from "), mono("fdf.prime"), t(" and its reference state each end\'s semantic cache. The Kotlin member on the phone and the Windows PC sends REQ_RAW, uncompressed. On the same 605 sends,')

# P5.2  Spec C 66: two timers remain (fdf_client.cpp MAPS_ASSERTED_V1 30 s; fdf-facts uplink prober 3 s)
sub('one owner per /24. Each region event is one reconcile; nothing polls.") ]),',
    'one owner per /24. Each region event is one reconcile. Two timers remain: fdf-client checks its mappings every 30 s and re-registers any the region lacks, and fdf-facts probes the uplink every 3 s.") ]),')

# P5.3  Spec C 67: AI-Host never ran the region r5-r13
sub('(Seattle7, Seattle3B, Seattle5, HappyDog, New-York-2, AI-Host) ran the region',
    '(Seattle7, Seattle3B, Seattle5, HappyDog, New-York-2) ran the region')

# P5.4  Spec C 67: counts are r19-a14's proof; a15 re-ran region-loop and the election sim
sub('bullet([ t("The region, build r19-a15 in network namespaces: region-loop 20/20',
    'bullet([ t("The region, in network namespaces (build r19-a14; r19-a15 re-ran region-loop and the election simulator, PASS): region-loop 20/20')

# P5.5  Part XV 56: 1.63/1.12 were min-step ratios, 1.03 a median; the letter moved to medians
sub("the sliced backend won every completed comparison. gloo's best step took 1.63 times as long as the sliced backend's with PyTorch's bucket view off, 1.12 times as long with it on, and never less than 1.03 times.",
    "the sliced backend won all four same-campaign comparisons on median step time: gloo took 1.95, 1.64 and 1.75 times as long, and 1.03 times on its best night.")

# P5.6  Part XVIII 70: a node keeps its own /24; the region gives addresses only to devices off the mesh
sub('FrogNet nodes now join an FD&F region and take their membership, addresses and tunnels from it.',
    'FrogNet nodes now join an FD&F region and take their membership, routes, names and tunnels from it; devices off the mesh take their addresses from it too.')

# ---------------- P6 ----------------
# P6.1  Appendix A: the service election never ages a row; the merge floor does (hosts.py DBHOST_BALLOT_FRESHNESS_V1)
sub('If any reading fails, the probe emits nothing and the last good capability stands. Capability rows do not age.") ]),',
    'If any reading fails, the probe emits nothing and the last good capability stands. The service election never ages a capability row. The merge\'s highest-.1 floor ("), mono("discovery/hosts.py"), t(") still ignores a row older than "), mono("FROGNET_BALLOT_MAX_AGE_S"), t(", 1800 s by default.") ]),')

# P6.2  Appendix A: lan_ip fallbacks (frognet_capability_probe.sh:310-331)
sub('''        "  lan_ip              this node's .1 (from getFrogNet.bash)",''',
    '''        "  lan_ip              this node's .1, from getFrogNet.bash; if that gives none,",
        "                      the highest 10.x .1 on a non-wg interface, else any 10.x",
        "                      address, else 127.0.0.1",''')

# P6.3  Appendix A worked example: the merge floor ranks on measured disk and CPU (hosts.py _rank)
sub('t(", and cores \\u00d7 clock. These are static properties only; nothing that moves with load is scored. The "), b("mediahost")',
    't(", and cores \\u00d7 clock. These are static properties only; nothing that moves with load is scored. (The merge\'s highest-.1 floor, which only guarantees that a databasehost line exists, still ranks on measured disk and CPU; the service election replaces its choice whenever capability rows exist.) The "), b("mediahost")')

# P6.4  Appendix B + C: "well under a millisecond" was never measured
sub('so it is computed once and served from cache in well under a millisecond, each sub-probe bounded by a two-second timeout.',
    'so it is computed once and served from a cache file, each sub-probe bounded by a two-second timeout.')
sub('answering \\u201Cwho are you?\\u201D in well under a millisecond from cache.',
    'answering \\u201Cwho are you?\\u201D from a cache file.')

# P6.5  Appendix C: the shipped frognet-pond's verbs (full_broker.tgz payload/usr/local/bin/frognet-pond)
sub('creates a pond for nodes to register into. Also list, status, members, allocations, remove, bootstrap.',
    'creates a pond for nodes to register into. Also ping, list, status, nodes, remove, forget <pubkey>, block/unblock, db, result.')

# P6.6  Appendix F: the 16 bytes a REPEAT carries are the request hash, not the HMAC same_id
sub('P([ t("The identifier is a short hash over the destination, the operation, and the reference values it was measured against \\u2014',
    'P([ t("The request hash a REPEAT carries is sixteen bytes over the destination, the operation, and the reference values it was measured against \\u2014')

# P6.7  Appendix J: phase C rebuilds the venv with pip when the architecture differs
sub('the first phase is the only one that wants the internet.',
    'the first phase wants the internet, and so does the venv rebuild in phase C when the release was built on another architecture.')

# P6.8  Appendix J: the installer also asks about the access point
sub('P([ t("It asks for the database password (the same on every node in the pond) and whether this node is a gateway.',
    'P([ t("It asks for the database password (the same on every node in the pond), whether to project an access point and its passphrase (unless --no-ssid-projection or --wifi-psk is given), and whether this node is a gateway.')

# P6.9  Appendix J: --synthetic takes WxH
sub('t(" sends a synthetic picture ("), mono("--synthetic"), t(");',
    't(" sends a synthetic picture ("), mono("--synthetic WxH"), t(", 1280x720 by default);')

# P6.10 Appendix J: 850 kbps is the ladder walk, not the 900 MHz baseline run
sub('it is a real measurement from a run across a 900 MHz radio with a one-megabit ceiling, not a bench estimate.',
    'it is a real measurement from the ladder walk, the link starved by hand with the bandwidth slider, not a bench estimate.')
sub('The figures above are one recorded run on one radio, with one scene',
    'The figures above are one recorded run, with one scene')

# P6.11 Appendix K: REJECTED -- the book is right (frognet-tunnel-daemon.py:113 is the older daemon, default 4;
#       setup_lillypad_v4.bash:750, run by the installer's phase E, writes MAX_TUNNELS=5000 into tunnel.conf).

# P6.12 Appendix L: generated; see R-56-OPEN.md (not hand-edited).

# P6.13 + P6.14 Glossary broker: the broker holds its own tunnel ends' private keys; one statement of the data path
sub('It terminates them and forwards between them, so it sees inter-site traffic in the clear. Reachable by IP from every node. It DOES carry the inter-site data path \\u2014 cross-site traffic transits it \\u2014 and inter-site traffic is in the clear at that seam. It holds',
    'It terminates them and forwards between them, so it carries the inter-site data path and sees that traffic in the clear. Reachable by IP from every node. It holds')
sub('It holds no private keys, runs none of the semantic stack,',
    'It holds no node\'s private key; the private keys of its own tunnel ends are in broker.db. It runs none of the semantic stack,')

# P6.15 Glossary hub: fdf-hub is the WireGuard concentrator; 51888 is the RAM manager
sub('gloss("hub", "The FD&F region\'s forwarding point on the droplet (StreamingFrog, 165.232.153.144:51888)."),',
    'gloss("hub", "fdf-hub, the region\'s WireGuard concentrator on the droplet (StreamingFrog, 165.232.153.144): one wg interface for the region, one peer per live member. The RAM manager on the same droplet is TCP 51888."),')

# P6.16 Glossary WireGuard: on an FD&F hub a peer carries only its member's prefixes
sub('its AllowedIPs is always 10.0.0.0/8."),',
    'its AllowedIPs on a node is 10.0.0.0/8; on an FD&F hub each member peer carries only the prefixes that member\'s verified mappings and facts give it."),')

# P6.17 Glossary capability probe / advertise: region mode writes a fact, not tuples
sub('as a JSON blob; each role\'s advertiser publishes it as its <role>/capability tuple, which the election scores (Appendix A)."),',
    'as a JSON blob. Off a region, each role\'s advertiser publishes it as its <role>/capability tuple; on an FD&F region the tunnel daemon writes it as the member\'s capability fact. Either is what the election scores (Appendix A)."),')
sub('the control name the election reads and the floating data name consumers see."),',
    'the control name the election reads and the floating data name consumers see. Off a region only."),')

done()
