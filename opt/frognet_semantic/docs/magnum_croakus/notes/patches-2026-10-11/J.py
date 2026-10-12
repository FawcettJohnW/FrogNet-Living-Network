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

# ---------------- Appendix A ----------------
# A1
sub(r'''The expensive measurements \u2014 a CPU benchmark, a disk write-and-fsync \u2014 run once per boot and are cached; the cheap, time-varying signal is sampled fresh on every call.") ]),''',
    r'''Every measurement, the CPU benchmark and the disk write-and-fsync included, is taken fresh on every call. Nothing is cached. If any field cannot be measured, the probe emits nothing and exits non-zero, and the last good capability stands.") ]),''')
# A5 intro
sub(r'''grouped roughly as compute, memory, storage, media-encode capability, database capability, and a live signal. The fields an election actually weighs:") ]),''',
    r'''grouped roughly as compute, memory, storage, media-encode capability, database capability, identity, and a live signal. The main fields:") ]),''')
# A4
sub(r'''"  arch                cpu architecture (arm64, x86_64, i686, ...)",''',
    r'''"  arch                cpu architecture (aarch64, x86_64, armv7l, ...)",''')
# mem_available_kb (A7: no election reads it)
sub(r'''"  mem_available_kb    free headroom (matters as much as total)",''',
    r'''"  mem_available_kb    free memory right now (no election scores it)",''')
# A5 storage + A3
sub(r'''"  disk_class          SSD / NVMe / rotational",''',
    r'''"  disk_class          nvme / ssd / hdd / sdcard / unknown",
        "  disk_write_mbps     measured write throughput",
        "  disk_fsync_ms       measured fsync time",''')
# A5 identity group + temps_c note
sub(r'''"LIVE SIGNAL  (sampled every call, not cached)",
        "  loadavg             current load",
        "  temps_c             temperatures",''',
    r'''"IDENTITY",
        "  lan_ip              this node's .1 (from getFrogNet.bash)",
        "  av_port             9000, the A/V port",
        "",
        "LIVE SIGNAL  (moves with load; no election scores it)",
        "  loadavg             current load",
        "  temps_c             temperatures (only where the kernel exposes thermal zones)",''')
# A6
sub(r'''P([ t("Note what is "), i("not"), t(" here: there is nothing about the network. The probe describes the box itself \u2014 what it is and what it can do \u2014 and stops at the edge of the machine. The network is not probed; it is "), b("discovered."), t(" Reachability, round-trip times, and routes are the walk's job (Part V), kept deliberately separate from a node's local capability. A box advertises what it can do; the mesh works out how to reach it.") ]),''',
    r'''P([ t("Note what is "), i("not"), t(" here: no reachability, no round-trip times, no routes. The one address in the blob is "), mono("lan_ip"), t(", the node's own .1. That is its identity, not a network measurement. The probe describes the box and stops at the edge of the machine. How to reach the box is worked out elsewhere (Part V).") ]),''')
# A7 + A8
sub(r'''P([ t("This is the appendix that makes the per-service election from Part V concrete. The "), b("database host"), t(" selector leans on cores, memory, "), mono("disk_free_gb"), t(", "), mono("disk_class"), t(", and the InnoDB pool \u2014 it wants a roomy box on fast storage. The "), b("media host"), t(" selector barely cares about disk; it weighs "), mono("encoders"), t(", "), mono("hw_encoder"), t(", "), mono("gpu_render_node"), t(", and "), mono("cpu_bench"), t(" \u2014 it wants a box that can transcode. Same blob, different readers, because host selection is a virtual function each service implements its own way. A node publishes this once and every service's election scores only the parts it cares about.") ]),''',
    r'''P([ t("This is the appendix that makes the per-service election from Part V concrete. The "), b("database host"), t(" selector gates on "), mono("mysql_running"), t(", then scores installed memory, the InnoDB pool, "), mono("disk_class"), t(", and cores \u00d7 clock. These are static properties only; nothing that moves with load is scored. The "), b("media host"), t(" selector gates on "), mono("ffmpeg"), t(" with "), mono("libvpx"), t(", then scores the measured CPU benchmark. A real hardware encoder ("), mono("vaapi"), t(" only with a render node) multiplies that by 2.5, and a box under half a gigabyte of RAM is halved. Same blob, different readers, because host selection is a virtual function each service implements its own way. Each election scores only the parts it cares about.") ]),''')
# A2 (REWRITE)
sub(r'''      H1("Cached fast, sampled fresh"),
      P([ t("The probe is built to be cheap to ask. Benchmarking a CPU and timing a disk fsync are not things you do on every election, so they run once per boot and are cached in "), mono("/run/frognet_avcap.json"), t("; a call reads the cache and adds only the live, fast signal \u2014 load and temperatures \u2014 before emitting the blob. The result is a current, comparable, full picture of the box that costs almost nothing to read, which is what lets the election re-run freely every time the mesh changes.") ]),''',
    r'''      H1("Measured every time"),
      P([ t("The probe takes every reading on every call, with no cache: a cached identity field once went stale for days. If any reading fails, the probe emits nothing and the last good capability stands. Capability rows do not age.") ]),
      P([ t("On an FD&F region the tunnel daemon runs the probe when it starts and every ten minutes after that ("), mono("FROGNET_CAP_REFRESH_SEC"), t(", default 600). It writes the result into the region as this member's "), mono("capability"), t(" fact, but only when the slow-moving part has changed. Load, temperatures, free memory and free disk are not announced.") ]),''')
# A9 (REWRITE)
sub(r'''      H1("One base probe, plus what each service adds"),
      P([ t("The blob above is the shared, general picture, taken once per node. It is deliberately not the whole story, and it is not the end of the road for the data either. Each service's handler "), b("publishes"), t(" its own "), mono("<role>/capability"), t(" tuple into the shared space \u2014 starting from this base picture and folding in whatever its own service needs that the general probe does not measure, a custom probe of its own. The media host is the clearest case: beyond the encoders the base probe reports, it needs "), b("OpenCV2"), t(" present to do its video work, so the media handler's own probe checks for that and includes it in the tuple it publishes. The election then reads those capability tuples back out of the shared space and scores them; it is never handed a blob by anyone. Like everything in FrogNet, capability is exchanged through the tuple space, not passed between components. The base probe answers \u201Cwhat is this box, in general\u201D; each service's custom probe answers \u201Cand does it have the specific thing I need\u201D; and the published tuple, read from the space, is the complete picture the election scores. The handler hooks for this are in Part X.") ]),''',
    r'''      H1("One probe, read two ways"),
      P([ t("There is one probe per node and no per-service probe. Off a region, each merge runs the probe once, and every role handler publishes that same blob as its "), mono("<role>/capability"), t(" tuple. The media role adds the A/V port it serves. The elections read those tuples back from the store.") ]),
      P([ t("On an FD&F region the store is not an election input. The tunnel daemon writes the blob into the region as the member's "), mono("capability"), t(" fact, and every member's elections read the alive members' facts. That way the input does not live on the host that just died.") ]),
      P([ t("Either way, no component hands another a blob. A handler that needs a fact the base probe does not take would have to add it to the probe. None does today.") ]),''')

# ---------------- Appendix B ----------------
# B1
sub(r'''        "fqdn,eth0IP,wlan0IP,wlan1IP",
        "",
        "FIELD     MEANING",
        "fqdn      the node's fully-qualified name (or its short hostname)",
        "eth0IP    the wired address \u2014 the node's .1 identity, if eth0 is up",
        "wlan0IP   the onboard Wi-Fi address, if any",
        "wlan1IP   the external / second Wi-Fi address, if any",''',
    r'''        "name,identity,upstream1,upstream2",
        "",
        "FIELD      MEANING",
        "name       the node name (dnsmasq domain=)",
        "identity   the node's .1 \u2014 always present",
        "upstream1  first upstream address this node holds on a non-eth0 link, or 0.0.0.0",
        "upstream2  second upstream address, or 0.0.0.0",''')
# B2
sub(r'''        "HomeBase.frognet,10.80.80.1,,",''',
    r'''        "HomeBase,10.80.80.1,0.0.0.0,0.0.0.0",''')
# B3 + B4
sub(r'''P([ t("HomeBase is wired, so its identity sits in field two ("), mono("eth0IP = 10.80.80.1"), t(") and the two Wi-Fi fields are empty. A node running as a wireless access point instead carries its identity in "), mono("wlan0IP"), t(" and leaves "), mono("eth0IP"), t(" blank \u2014 which is how the discovery walk tells a wired node from a wireless one without being told. The walk reads this to identify a freshly-probed address;''',
    r'''P([ t("HomeBase's identity sits in field two ("), mono("10.80.80.1"), t("). It has no upstream, so fields three and four read "), mono("0.0.0.0"), t(". Field 2 is the node's identity whether it is wired or projecting an access point. Fields 3 and 4 say which addresses other networks gave it. Off a region, the walk reads this to identify a freshly-probed address;''')
sub(r'''so it is computed once and served from cache in well under a millisecond, each sub-probe bounded by a two-second timeout.") ]),''',
    r'''so it is computed once and served from cache in well under a millisecond, each sub-probe bounded by a two-second timeout.") ]),
      P([ t("On an FD&F region the echo is the node's own facts, in the same four-field form, read from "), mono("/opt/fdf/var/facts.json"), t(". It prints nothing until those facts exist. There is no walk there. Who exists comes from the region's records.") ]),''')

# ---------------- Appendix C ----------------
# C1
sub(r'''Flags: --name, --ip (10.X.Y.1), --broker-host, --broker-port, --no-ssid-projection, --ap-iface, --country.", "root CLI''',
    r'''Flags: --name, --ip (10.X.Y.1), --broker-host, --broker-port, --broker-scheme, --pond, --db-pass, --wifi-psk, --no-ssid-projection / --ssid-projection, --ap-iface, --country, --preserve, --from-repo, --rebuild-venv. Any other argument is refused.", "root CLI''')
# C2
sub(r'''"SSID-on/off and Wi-Fi country, seeded from --no-ssid-projection / --country."''',
    r'''"SSID_PROJECTION, AP_IFACE and WIFI_COUNTRY, seeded from --no-ssid-projection, --ap-iface and --country. Read by frognet-netstart."''')
# C3
sub(r'''"Gateway / uplink facts for a node with a non-FrogNet way out."''',
    r'''"Every node's identity facts: NETWORK_NAME, GATEWAY_IP (.1), ADMIN_IP (.2), GW1/GW2."''')
# C4
sub(r'''["frognet-pond-bootstrap", "Timer that retries the broker every couple of minutes until it answers a health check, then proceeds and disables itself. The node is a complete network the whole time.", "service \u00b7 Part VII"],''',
    r'''["frognet-pond-bootstrap.sh", "Run from the end of each merge while broker enrolment is pending (network events cause merges). On success it starts the tunnel daemon and writes /etc/frognet/pond_bootstrap_done. The node is a complete network the whole time. Not used on an FD&F region.", "script \u00b7 Part VII"],''')
# C5
sub(r'''["frognet-tunnel-setup-v3.sh", "Bootstraps a fresh broker (--bootstrap <group> <name>) or redeems a passcode to join an existing pond \u2014 writing the GROUP_TOKEN membership card.", "CLI \u00b7 Part VII"],''',
    r'''["frognet-tunnel-setup-v3.sh [--pond-password PW]", "Registers this node with the broker named in tunnel.conf (POST /api/v4/register: pond, WireGuard pubkey, /24, /etc/fnid GUID), then starts the tunnel daemon. Only a node with a direct, non-10.x upstream registers.", "CLI \u00b7 Part VII"],''')
# C6
sub(r'''["frognet-broker-admin.sh passcode new --group <g>", "On the broker: mints a one-time passcode for a new node to redeem.", "broker CLI \u00b7 Part VII"],''',
    r'''["frognet-pond create <pond> [--max-users N] [--node-base IP] [--increment N] [--password PW]", "On the broker: creates a pond for nodes to register into. Also list, status, members, allocations, remove, bootstrap.", "broker CLI \u00b7 Part VII"],''')
# C7
sub(r'''        ["/etc/frognet/tunnel.conf", "The broker URL, pond name, choruses to join, and the GROUP_TOKEN redeemed at join. One of the two files you actually touch.", "config \u00b7 Part VII"],
        ["/etc/frognet/tunnel.conf", "Holds the GROUP_TOKEN written when a node redeems a passcode. Not edited by hand.", "config \u00b7 Part VII"],''',
    r'''        ["/etc/frognet/tunnel.conf", "The one broker config file. BROKER_URL, POND_NAME, optional POND_PASSWORD, CHORUSES, MAX_TUNNELS. On an FD&F region it may also carry RINA_RAM (the region join writes /opt/fdf/var/join.env and keeps a copy of this file as tunnel.conf.broker).", "config \u00b7 Part VII"],''')
# C10
sub(r'''"The broker's entire working set in one SQLite file \u2014 a cache the nodes refill, not a system of record. Back it up by copying the file."''',
    r'''"The broker's state in one SQLite file (WAL mode). Nodes, tunnels and choruses are rebuilt by nodes checking in. Ponds are not: the operator creates them. Back it up with SQLite's backup, not a bare copy."''')
# C8
sub(r'''["FrogNet_monitor", "The terminal Network Info Center''',
    r'''["frognet_monitor", "The terminal Network Info Center''')
# C9
sub(r'''"The setup web page's backend: set/clear the broker, register the node, generate a join QR code, toggle SSID projection, reboot \u2014 everything the helper does, with a friendly face."''',
    r'''"The setup web page's backend: set the node's identity, join Wi-Fi, set or clear the broker (host, port, pond, password, choruses), generate a join QR code, toggle SSID projection, reboot."''')
# Probes rows (follow A1 and B1)
sub(r'''"Emits the node's capability as JSON \u2014 compute, memory, storage, media-encode, database, plus a live load/temperature signal. Expensive measurements cached per boot; the live signal sampled per call. The input every election scores."''',
    r'''"Emits the node's capability as JSON \u2014 compute, memory, storage, media-encode, database, identity, plus load and temperature. Every measurement is taken fresh on every call; nothing is cached. The input every election scores."''')
sub(r'''"The echo: one CSV line \u2014 fqdn,eth0IP,wlan0IP,wlan1IP \u2014 answering \u201Cwho are you?\u201D in well under a millisecond from cache. The most-called thing in the system."''',
    r'''"The echo: one CSV line \u2014 name,identity,upstream1,upstream2 \u2014 answering \u201Cwho are you?\u201D in well under a millisecond from cache. On an FD&F region it is read from the node's facts. The most-called thing in the system."''')
# C11
sub(r'''      P([ t("The two files you will actually edit are "), mono("tunnel.conf"), t(" and "), mono("ssid_projection.conf"), t(", and both have friendlier front ends in the helper and the setup page. Everything else''',
    r'''      H1("Region (FD&F)"),
      refTable([
        ["fdf-region-join SITE_KEY [RAM:PORT] [AREA]", "Joins this FrogNet node to the region (site.env, join.env, tunnel daemon in region mode). Stops the broker-mode timers.", "root CLI \u00b7 FD&F"],
        ["fdf-leave", "Leaves the region and turns the broker-mode units back on.", "root CLI \u00b7 FD&F"],
        ["fdf-ls", "The region's members.", "CLI \u00b7 FD&F"],
        ["fdf-comms.sh", "Runs the Communicator's servers (comms-ram :8800, comms-media :8994) on this node and says where they are in the pond.", "root CLI \u00b7 FD&F"],
      ]),
      P([ t("Off a region, the two files you edit are "), mono("tunnel.conf"), t(" and "), mono("ssid_projection.conf"), t(", and both have friendlier front ends in the helper and the setup page. On a region, "), mono("fdf-region-join"), t(" writes what it needs. Everything else''')

# ---------------- Appendix D ----------------
# D1
sub(r'''["Bandwidth / min", "~360 KB", "~40 KB (9\u00d7 less)", "~25 KB (15\u00d7 less)"],''',
    r'''["Bandwidth / min", "~360 KB", "~45 KB (8\u00d7 less)", "~25 KB (15\u00d7 less)"],''')
sub(r'''"~1.1\u00d7 \u2014 marginal"''', r'''"~1.25\u00d7 \u2014 marginal"''')
# D2
sub(r'''capLabel("A small-group HD video call, plus presence, chat, and a game table \u00b7''',
    r'''capLabel("A small-group HD video call, plus presence and chat \u00b7''')
# D3
sub(r'''"adaptive: 1 Mbit \u2192 ~300 Kbps \u2192 a 1-byte heartbeat, and back \u2014 no re-dial"''',
    r'''"adaptive: 1 Mbit \u2192 ~300 Kbps \u2192 audio only, and back \u2014 no re-dial"''')
# D2 (row label; no games)
sub(r'''["Presence, chat, setup, game", ''', r'''["Presence, chat, setup", ''')
# D4
sub(r'''"1 interface; call, chat, games and presence as handlers over one space; the media host is an elected role"''',
    r'''"1 shared memory (comms-ram) and 1 media server (comms-media); call, chat and presence are rows in it; a node's own servers are found through the pond's where cell"''')
# D5
sub(r'''so a tightening link degrades it to a heartbeat instead of dropping it.''',
    r'''so a tightening link sheds the picture to keep the voice instead of dropping the call.''')

# ---------------- Appendix E ----------------
# E2
sub(r'''the LAN inside a cell and WireGuard between cells, because that is what the real code does.") ]),''',
    r'''the LAN inside a cell and WireGuard between cells, because that is what the real code does. This is broker mode, where every node walks and merges. On an FD&F region the walk does not run; the region's records and plan replace it.") ]),''')
# E1
sub(r'''A worked scenario is worth exactly what it pins. This one asserts, at each stage, the properties that make the merge correct \u2014 and every one fails loudly if the real code regresses:") ]),''',
    r'''A worked scenario is worth exactly what it pins. A scenario written this way asserts, at each stage, the properties that make the merge correct; written as an oracle, every one fails loudly if the real code regresses:") ]),''')

# ---------------- Appendix F ----------------
# F1
sub(r'''on every node, and Apache is relegated to a private port where only the local machinery talks to it. Apache is still there and still does its work; it simply stops being the thing the world connects to.") ]),''',
    r'''on every node. Apache moves to port 8080 (8443 for TLS); port 80 belongs to the proxy. Apache still listens on every address; the local machinery is what uses it.") ]),''')
# F2 (REWRITE)
sub(r'''      P([ t("Compression is not free, so it is not unconditional. The decision is made "), b("per hop"), t(", about the next hop only, never about the final destination \u2014 and the default is the fast path everywhere. Semantic mode engages when the next hop measures slow, or when policy names it explicitly.") ]),
      P([ t("The measurement is smoothed, and the switch is deliberately sticky: a running average rather than the last sample, different thresholds for entering and leaving semantic mode, and a minimum time in a mode before it may change again. A link that hovers near the threshold would otherwise flap between modes and spend more effort switching than either mode saves.") ]),''',
    r'''      P([ b("Every FrogNet request rides the codec."), t(" The proxy sends every request for a remote FrogNet address (10/8) through the daemon with SAME/DIFF: templates, references, REPEAT and SAME. Only a request for the node itself goes straight to the local Apache, and only addresses outside 10/8 go out raw.") ]),
      P([ t("The proxy still labels each hop FAST or SEMANTIC, and "), mono("/etc/frognet/semantic_hosts"), t(" can force the label. The label does not change the codec path. The smoothing, hysteresis and hold-down code exists in "), mono("proxy/decision.py"), t(", but no RTT sample reaches it today (the probe is called without a device), so it never switches a hop.") ]),''')
# F3
sub(r'''        "/propogateNotification.php?event=abc",
        "   template: /propogateNotification.php      values: event",
        "",
        "/api.php?entity=sensors&action=values&SensorName=X&parse=1",
        "   template: /api.php?entity=sensors&action=values",
        "   values:   SensorName, parse",
        "",
        "/frognet_echo.php",
        "   template: /frognet_echo.php               values: (none)",
      ]),''',
    r'''        "/propogateNotification.php?event=abc",
        "   identity: /propogateNotification.php?__dyn=event",
        "   values:   event",
        "",
        "/api.php?entity=sensors&action=values&SensorName=X&parse=1",
        "   identity: /api.php?entity=sensors&action=values&__dyn=SensorName,parse",
        "   values:   SensorName, parse",
        "",
        "/frognet_echo.php",
        "   identity: /frognet_echo.php",
        "   values:   (none)",
      ]),
      P([ t("The names of the varying keys, and a JSON body's field set, are part of the identity, because they fix the wire layout. Their values are not. The names ride as "), mono("__dyn="), t("; a body's field set rides as "), mono("__body="), t(" and a checksum.") ]),''')
# F4
sub(r'''It goes over uncompressed, once, and both ends learn the shape from it. That is the FULL of Part IX, and learning it and sending it are the same act.") ]),''',
    r'''It goes over raw, once, and both ends learn the shape from it. The next request is the first FULL: every value, in the learned template.") ]),''')
# F5
sub(r'''        "reference is UNSET     never sent to this peer  ->  FULL",
        "reference is EMPTY     sent before, no values, still none",
        "                                               ->  SAME",
        "reference has VALUES   some of them changed     ->  DIFF",''',
    r'''        "reference UNSET       never sent to this peer        ->  FULL",
        "reference SET, equal  nothing changed (or no values) ->  REPEAT",
        "reference SET, moved  some values changed            ->  DIFF",''')
# F6
sub(r'''P([ t("A frame is a four-byte marker, a one-byte opcode, and a payload. The opcodes''',
    r'''P([ t("A message is a four-byte marker, a one-byte opcode and a payload, carried in a frame with a four-byte length. Replies also carry a four-byte sequence tag. The opcodes''')
sub(r'''P([ t("A repeat of an unchanged request for an unchanged resource is "), b("twenty-one bytes out and twenty-one bytes back"), t(": four of marker, one of opcode, sixteen of identifier, each way. For what would otherwise be a full HTTP request and a full JSON reply.") ]),''',
    r'''P([ t("A repeat of an unchanged request for an unchanged resource is "), b("a 21-byte message each way"), t(": four of marker, one of opcode, sixteen of identifier. On the wire that is 25 bytes out and 29 back. For what would otherwise be a full HTTP request and a full JSON reply.") ]),''')
# F7
sub(r'''          ["RAW",    "to peer",   "hash, plus one uncompressed bootstrap"],''',
    r'''          ["RAW",    "to peer",   "hash, plus one uncompressed bootstrap"],
          ["RAW",    "from peer", "same_id, status, headers, body \u2014 the bootstrap reply"],''')
sub(r'''          ["ERROR",  "either",    "a status and a message \u2014 a refusal, not a reply"],
          ["SEQ_RESET", "either", "put the two sequence counters back in step"],
          ["RTT_PING / PONG / LOOP", "either", "round-trip probes on the same wire"],''',
    r'''          ["ERROR",  "from peer", "a status and a message \u2014 a refusal, not a reply"],
          ["SEQ_RESET", "\u2014", "defined, not used by the proxy or daemon"],
          ["RTT_PING", "to peer", "a round-trip probe on the same wire"],
          ["PONG / LOOP", "from peer", "the answers to RTT_PING"],''')
# F8
sub(r'''P([ t("On the "), b("receiving"), t(" side, the collapse is keyed on the request alone \u2014 not on who asked. Three "), i("different nodes"), t(" asking the same question in the same instant cost the answering node one database execution among them, not three. This protects the origin, which is the scarce resource on a small computer.") ]),''',
    r'''P([ t("On the "), b("receiving"), t(" side, a request whose hash is already being answered can wait for that answer. The hash covers the destination, the template and the sender's reference values, so callers holding the same references share one execution. The wait is "), mono("FROGNET_DAEMON_COALESCE_WAIT"), t(" seconds, and it is 0 by default, which turns this off. When it is on, it protects the origin, which is the scarce resource on a small computer.") ]),''')
# F8 + F9
sub(r'''Neither substitutes for the other: two distinct peers produce two frames the sender cannot merge but one execution the receiver can. Two rules keep the mechanism honest. Health probes are excluded, because a probe handed somebody else's result has measured nothing. And a waiter that waits too long simply goes and does the work itself''',
    r'''Neither substitutes for the other: two distinct peers produce two frames the sender cannot merge, and the receiver merges them only when their references match and the wait is on. Two rules keep the mechanism honest. Only the synthetic health check is excluded; the daemon answers it SAME without running anything. And a waiter that waits too long goes and does the work itself''')
sub(r'''This was learned by removing it. Probes were once exempted from the collapse on the theory''',
    r'''This was learned by removing it. Echo and discovery probes were once exempted from the sending-side collapse on the theory''')
# F10
sub(r'''It is bounded three ways, and all three are needed.") ]),''',
    r'''It is bounded three ways, and all three are needed. This is off a region only. On an FD&F region a change is a facts write, the region tells the members, and no notification is sent.") ]),''')
sub(r'''        "     re-propagation from each receiver still reaches everyone.",''',
    r'''        "     re-propagation from each receiver still reaches everyone.",
        "     if the neighbour list cannot be computed, the node",
        "     notifies every 10.x.y.1 in /etc/hosts instead.",''')
# F11
sub(r'''P([ t("The identifier it replies with is derived from the node\u2019s own identity \u2014 the GUID in "), mono("/etc/fnid"), t(" from Part IV \u2014 together with the request and the content of the response. No cipher is involved and none is claimed; this is identity and derivation, not cryptography. What it buys''',
    r'''P([ t("The identifier is sixteen bytes of HMAC-SHA256, keyed by the node's semantic-cache secret ("), mono("/etc/frognet/sem_cache_secret"), t(", made at install), over the request hash and a hash of the response body. It names an answer; it does not hide one, and it is not a confidentiality claim. What it buys''')
# F12
sub(r'''P([ t("And the reference is advanced "), i("only"), t(" after the far side has accepted the exchange. If the answering node failed \u2014 its database down, its origin returning an error \u2014 neither side moves, and the two references stay in step. This is the quiet invariant''',
    r'''P([ t("The request reference moves as the frame leaves. If the far side cannot decode or answer it, the reference is cleared, and the next request goes FULL, which resets the far side too. An error is never stored as an answer. This is the quiet invariant''')
# F13
sub(r'''is what keeps a bad minute from becoming a bad hour.") ]),''',
    r'''is what keeps a bad minute from becoming a bad hour.") ]),
      P([ t("The table is the port-80 implementation. The Ribbit C++ engine handles \u201CSAME, no copy\u201D differently: it asks the host for the answer the SAME named ("), mono("REQ_SAME_MISS 0x22"), t("). The host serves it from its cache and does not run the request again; if the answer is gone, it says so (410).") ]),''')

# ---------------- aliases for retitled sections ----------------
sub(r'''  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",''',
    r'''  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",
  "cached-fast-sampled-fresh": "measured-every-time",
  "one-base-probe-plus-what-each-service-adds": "one-probe-read-two-ways",''')

done()
