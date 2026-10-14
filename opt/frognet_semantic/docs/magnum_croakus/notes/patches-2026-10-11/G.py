import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
D = '\\u2014'   # em dash in source form
Q = '\\u2019'   # right single quote in source form

# 1
sub('gld([ t("A chapter about who may read your traffic is the right place to say that FrogNet has legal surface,',
    'gld([ t("This is as good a place as any to say that FrogNet has legal surface,')
# 2
sub('sit alongside an GPLv2 release', 'sit alongside a GPLv2 release')
# 3
sub('and two such files exist in the tree right now, one of them still asserting an invariant that was deliberately undone.',
    'and one such file was still in the tree when this was written, still asserting an invariant that had been deliberately undone.')
# 4
sub('...codeBlock(["net1:", "  subnet: 10.101.101", "  hardware: {profile: pi5, memory_gb: 64, disk: {class: class3, size_gb: 1000}}", "  projects_ssid: true", "  roles: [mediahost, databasehost]", "  in_range: [net3, net4]", "  tunnel_to: [net5, net6]", "  hosts:", "    - name: net1-db", "      hardware: {profile: db_appliance, memory_gb: 256}", "      roles: [databasehost]"]),',
    '...codeBlock(["networks:", "  net1:", "    subnet: 10.101.101", "    hardware: {profile: pi5, memory_gb: 64, disk: {class: class3, size_gb: 1000}}", "    projects_ssid: true", "    roles: [mediahost, databasehost]", "    in_range: [net3, net4]", "    tunnel_to: [net5, net6]", "    hosts:", "      - name: net1-db", "        hardware: {profile: db_appliance, memory_gb: 256}", "        roles: [databasehost]"]),')
# 5
sub('t(" takes the hardware out of the sentence. You write the network down. It builds it, runs it, and tells you what happened.") ]),',
    't(" takes the hardware out of the sentence. You write the network down. It builds it, runs it, and tells you what happened.") ]),\n'
    '      P([ t("FrogSim is a third instrument, separate from "), mono("discovery/"), t(" and "), mono("simulation/"), t(". Its network definition ("), mono("netdef.py"), t(") ships in "), mono("simulation/"), t("; its report, coverage registry, negative controls, tuple store and namespace app plane are not yet in the published tree.") ]),')
# 6
sub('["Reachability", "0 of 2,256 ordered pairs unreachable"]',
    '["Reachability", "0 of 2,256 ordered pairs unreachable (with the hop limit raised; the shipped validator, still at twelve, reports 299 false failures)"]')
# 7
sub('dataTable(["", "Merges", "Mean"], [["Access point", "52", "99 ms"], ["Leaf", "150", "14 ms"]], [2640, 3300, 3300]),',
    'dataTable(["", "Merges", "Per node"], [["Access point (3)", "97", "32"], ["Leaf (45)", "217", "4.8"]], [2640, 3300, 3300]),')
sub('P([ t("Seven to one. An access point carries',
    'P([ t("About seven to one, per node. Mean merge times depend on the machine the run is on, so they are not printed here. An access point carries')
# 8
sub('357 false failures that became zero when the limit was raised. The two logging faults above. And a helper that removed an access point without detaching the stations associated with it, which took the simulator down with a key error at the exact moment the scenario got interesting.") ]),',
    '357 false failures that became zero when the limit was raised in FrogSim. The two logging faults above. And a helper that removed an access point without detaching the stations associated with it, which took the simulator down with a key error at the exact moment the scenario got interesting. Both fixes were made in FrogSim; "), mono("discovery/sim/system.py"), t(" still stops at twelve, and its "), mono("remove_node"), t(" still leaves the stations attached.") ]),')
# 9
sub('The A/V phone places a call between two nodes and the relay on the far side registers the peer.',
    'The earlier Python A/V phone placed a call between two nodes and the far relay registered the peer; the current Communicator (comms-app, comms-ram, comms-media) has not been run in the simulator.')
# 10
sub('And runs are checked against golden baselines captured from real hardware ' + D + ' the three-node baseline and the kernel-FIB chain, confirmed end to end ' + D + ' which is the difference',
    'And while all twenty built-in topologies have baselines recorded on the real-kernel tier that the offline engine reproduces byte for byte, no run has yet predicted something a real box then confirmed ' + D + ' which is the difference')
# 11, 12
sub('P([ b("FrogNet_monitor"), t(" runs on a node', 'P([ b("frognet_monitor"), t(" runs on a node')
sub('A host reads as "), b("online"), t(" or "), b("degraded"), t(" ' + D + ' degraded meaning its round-trip has gone past 250 milliseconds ' + D + ' or as "), b("idle"), t(" or "), b("down"), t(". Drill',
    'A host reads as "), b("ONLINE"), t(", "), b("IDLE"), t(", "), b("FLAKY"), t(", "), b("SLOW"), t(", "), b("DEGRADED"), t(" or "), b("DOWN"), t(". DEGRADED means its p95 round-trip is at least 1.5 times what that link has measured it should achieve, or over a second when no baseline exists yet; SLOW is 4 times or more; FLAKY is under 80% success. (The C++ monitor uses its own words: OK, FAIR past 250 ms, SLOW past a second, BUSY, SAT, DOWN.) Drill')
# 13
sub('t(" shapes the traffic the way real conversation does, with roughly a fifth of it non-message noise, so you get SAME and DIFF mixed',
    't(" polls a 50-message rolling window in which roughly one poll in five carries a new message, so you get SAME and DIFF mixed')
# 14
sub('exLabel("1 MB JSON, six dynamic fields ' + D + ' polled, not burst")',
    'exLabel("1 MB JSON, six dynamic fields ' + D + ' polled serially, then in a burst")')
sub('P([ t("A hundred and thirty-five bytes per request. Steady state 99.7%, a factor of three hundred and twenty-nine.") ]),',
    'P([ t("Serial, it is about 3.8 KB per request ' + D + ' 99.7%, a factor of 329. At 269 concurrent it is 135 bytes per request.") ]),')
# 15
sub('Per-request cost is identical at 25 serial and at 269 concurrent, so concurrency is not doing the work.',
    'Would-have-been cost per request is identical at 25 serial and at 269 concurrent, so the denominator is not moving with concurrency.')
sub('And the 1.24x by which wire bytes exceed body bytes is packet headers',
    'And the 1.13x by which would-have-been bytes exceed the body is packet headers')
# 16
sub('lead([ t("Part IX introduced the sensor platform', 'lead([ t("Part VIII introduced the sensor platform')
# 17
sub('"tc -s qdisc show dev ham0 | metric_upsert.sh LinkStats HAM"]',
    '"metric_upsert.sh LinkStats HAM \\"$(tc -s qdisc show dev ham0 | jq -Rs \'{qdisc:.}\')\\""]')
sub('"# or piped straight from a kernel tool:"', '"# or wrapping a kernel tool\'s output as JSON:"')
# 18
sub('t(" A unit that fires when something happens rather than on a clock ' + D + ' post-merge metrics run once after a merge completes, gated on sentinels for merge completion and a known data host, so they cannot emit into a pond that has no elected host to write to.") ]),',
    't(" A producer that fires when something happens rather than on a clock ' + D + ' the route snapshot is written once at the end of every merge ("), mono("emit_routes_snapshot.sh"), t(", called by "), mono("mergeHostsAndResolv.bash"), t(").") ]),')
# 19
sub('A snapshot that throws is reported and skipped so one bad metric does not cost the batch; a batch that fails to send is logged and dropped.',
    'A pipeline, Apache or resilience snapshot that throws is reported and skipped; if one of the core snapshots throws, collection stops there and what was gathered is still sent. A batch that fails to send is logged and dropped.')
# 20
sub('P([ t("Start it with "), mono("python3 -m frognet_monitor"), t(". It is a curses program:',
    'P([ t("Start it with "), mono("frognet_monitor"), t(" (it runs "), mono("python3 -m frognet_monitor_py"), t(" from "), mono("/usr/local/bin"), t("). It is a curses program:')
# 21
sub('P([ t("Behind that name are two tables. "), mono("sensors"), t(" holds the metadata',
    'P([ mono("sensors"), t(" holds the metadata')
# 22
sub('The Monitor resolves nothing itself and hardwires nothing; it asks for the name and lets the pond answer.") ]),',
    'The Monitor hardwires nothing. It asks for the name "), mono("databasehost.frognet"), t(" on port 80, and the local proxy carries the request to wherever the pond' + Q + 's data host is now (the C++ monitor sends it to 127.0.0.1:80 with the name in the Host header).") ]),')
# 23
sub('the first press refuses and says how old it is; a second press goes anyway.',
    'the first press refuses and says how old it is; a second press within six seconds, while the refusal is still on screen, goes anyway.')
# 24
sub('The main loop only draws and reads keys ' + D + ' it never waits on a socket.',
    'The main loop only draws and reads keys ' + D + ' it never waits on a socket, except when Enter opens one sensor' + Q + 's JSON, which is fetched on the main loop.')
# 25
sub('P([ t("Because curses owns the terminal, nothing prints. Diagnostics go to "), mono("/tmp/frognet_monitor.log"), t(", and if that file cannot be opened the trace is dropped',
    'P([ t("Because curses owns the terminal, diagnostics go to "), mono("/tmp/frognet_monitor.log"), t("; the one exception is an unreadable "), mono("/etc/hosts"), t(", which prints a line before the reader raises. If the log file cannot be opened, the trace is dropped')
# 26
sub('"=== 10 passed, 0 failed ==="]),',
    '"  PASS  G1 the sentinel directory is /etc/sentinels", "  ' + U('…') + '", "  PASS  G12 the successful launch is traced too", "", "ALL PASS"]),')
sub('P([ t("Ten goals, pinning the sentinel as the source, the three-state reading, the age formatting, the caching, both refusal paths on "), mono("M"), t(", and the indicator' + Q + 's place on the title line.',
    'P([ t("Twelve goals: the sentinel as the source, the three-state reading, the age formatting, the caching, both refusal paths on "), mono("M"), t(", the force-on-second-press window, that every way out of "), mono("start_merge"), t(" is traced, and the indicator' + Q + 's place on the title line.')
done()
