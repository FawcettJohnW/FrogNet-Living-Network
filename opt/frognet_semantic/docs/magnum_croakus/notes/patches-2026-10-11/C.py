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
# Patch C: Part VI (DatabaseHost) and Part VII (Connecting Across the Internet), from reports/C.md

# ---------- aliases for retitled / removed H2 anchors ----------
A = r'''  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",
'''
sub(A, A + r'''  // [C_PART6_7_ALIASES_V1] Part VI/VII subsections retitled to match the code
  "waiting-for-everyone": "absent-is-an-answer",
  "s-waiting-for-everyone": "absent-is-an-answer",
  "bootstrapping-and-handing-out-keys": "joining-a-pond",
  "s-bootstrapping-and-handing-out-keys": "joining-a-pond",
  "what-you-will-be-asked": "running-the-installer",
  "s-what-you-will-be-asked": "running-the-installer",
  "what-the-installer-builds-and-the-wall-in-the-middle-of-it": "what-the-installer-builds",
  "s-what-the-installer-builds-and-the-wall-in-the-middle-of-it": "what-the-installer-builds",
''')

# ---------- #30 Part VI lead: the two modes ----------
sub(r'''but Ribbit itself invents none of those semantics."), ]),
''', r'''but Ribbit itself invents none of those semantics."), ]),
      // [C30_TWO_MODES_V1]
      P([ t("A FrogNet node runs in one of two modes. With a broker, which is what this Part describes, the walk finds the members, and capability is published to the control plane. On an FD&F region ("), mono("fdf-region-join"), t("), FD&F owns membership: the members come from the region, each member\u2019s capability is a fact the tunnel daemon probes and announces (at start and every 600 s), and the elections read those facts, not the store. "), mono("databasehost.frognet"), t(" is the same name in both modes, chosen by the same scoring code.") ]),
''')

# ---------- #1 gld: what makes a role live ----------
sub(r'''a role is live when it has both a candidate tuple in the transient and a registered handler, and the handler decides what \u201Cbest\u201D means.''',
    r'''a role is live when a handler for it is registered; every live .1 FrogNetHost is a candidate for it, and the handler decides what \u201Cbest\u201D means.''')

# ---------- #2 the score is a weighted sum ----------
sub(r'''past it the box with the most RAM, then the faster disk, takes the role; the highest address only breaks a tie.''',
    r'''past it, a weighted sum of installed RAM (the dominant term), disk class and CPU decides; the highest address only breaks a tie.''')

# ---------- #31a region: election in-process ----------
sub(r'''The instant the reachable set changes, the answer changes with it, on every box.") ]),
''', r'''The instant the reachable set changes, the answer changes with it, on every box.") ]),
      P([ t("On a region the tunnel daemon runs the same election in-process on the region event that changed its input: a member leaving or returning, or a capability fact changing. A dead database host is elected around within a second, and the new answer is in "), mono("/var/lib/frognet/view.json"), t(".") ]),
''')

# ---------- #5 opt-in History entity ----------
sub(r'''There is no history, no log, no revision. That is what the word "), i("transient"), t(" is doing in the name.") ]),''',
    r'''The tuple keeps no history, no log, no revision. That is what the word "), i("transient"), t(" is doing in the name. An application that wants a time series appends to the separate History entity ("), mono("entity=history&action=append"), t("); that is its choice, not the Region\u2019s default.") ]),''')

# ---------- #3 names from other ponds: resolv.conf and dnsmasq forwarders ----------
sub(r'''      P([ t("So each node lists, as a nameserver, "), b("the machine at the far end of every tunnel it holds"), t(". Each pond\u2019s primary knows its own pond\u2019s names, and a query that the local resolver cannot answer falls through to a machine that can. The order is what makes it cheap: the local resolver first, so own-pond and "), mono(".frognet"), t(" names never leave the box, peers next, and the WAN last.") ]),
      P([ b("The list is not configured, and it is not read off the routing table."), t(" It comes from the walk, which is the one place the answer exists without ambiguity: every peer the merge records carries the device it answered on and the name it gave for "), i("itself"), t(". That is precisely the join \u2014 which machine is at the end of which tunnel \u2014 and it is written into "), mono("/etc/resolv.conf"), t(" at the end of the same merge pass that learned it.") ]),
''', r'''      P([ mono("/etc/resolv.conf"), t(" names only "), mono("127.0.0.1"), t(", the upstream (if any) and "), mono("8.8.8.8"), t(". A name from another pond reaches the right primary through dnsmasq. Each merge writes a "), mono("server=/<domain>/<its .1>"), t(" line for every FrogNetHost in the converged hosts block into "), mono("/etc/dnsmasq.d/frognet_forwarders_auto.conf"), t(". It is written from the hosts block, the one place that names which .1 serves which domain. It is not read off the routing table.") ]),
''')

# ---------- #4 how --local-service is lifted ----------
sub(r'''      P([ t("So a FrogNet node disables it, by configuration rather than by editing the distro\u2019s own service file. That distinction matters more than it looks: the file that carries the flag lives under "), mono("/usr/share"), t(", so a patched copy is restored by the next package upgrade and the failure returns as one journal line and then silence. Configuration survives the upgrade.") ]),
      P([ t("It is not replaced with a list of interfaces to listen on, either. Naming interfaces is a written-down answer to a question whose answer moves \u2014 a node changes subnet, a tunnel comes up \u2014 and the list is then stale with nothing reporting that it is. What the node gives up is a protection against being an open resolver, which matters on a box with a public address and is moot on one without. The trade is made deliberately, because silent DNS failure across the fabric costs more, more often, than a risk on a node that should not have had a public interface in the first place.") ]),
''', r'''      P([ t("dnsmasq drops "), mono("--local-service"), t(" whenever "), mono("interface="), t(" or "), mono("listen-address="), t(" is configured, and a FrogNet node has both: "), mono("interface="), t(" lines rendered from "), mono("/etc/dnsmasq_conf_template"), t(", and "), mono("listen-address=127.0.0.1,<self .1>"), t(" plus "), mono("bind-interfaces"), t(", which the merge writes. No distro file is edited.") ]),
''')

# ---------- #6 what the control plane holds ----------
sub(r'''t(" is the coordination plane. It holds the capability records the election reads, and nothing else. No application ever addresses it, nothing you build should touch it, and it holds no data of yours.") ]),''',
    r'''t(" is the coordination plane. It holds the "), mono("SD:"), t(" tuples: capability records, "), mono("reach_plane"), t(", presence, beacons and the Services table. It holds no application data of yours.") ]),''')

# ---------- #32 control host on a region ----------
sub(r'''and in a well-formed pond it does not need to, because .1 is always FrogNetHost in any subnet.") ]),''',
    r'''and in a well-formed pond it does not need to, because .1 is always FrogNetHost in any subnet. On a region the pool is the region\u2019s nodes only. A leaf\u2019s address (10.252.x, the first of which ends in .1) is named in "), mono("/etc/hosts"), t(" but is never a candidate.") ]),''')

# ---------- #7 order of operations ----------
sub(r'''      ...codeBlock(["1  build the host block from what the walk proved", "", "2  FLOOR      databasehost.frognet = highest .1", "              (a guarantee that the name always resolves,", "               even before anyone has published anything)", "", "3  CONTROL    databasehost_control.frognet = highest .1", "              arithmetic, capability-free, written FIRST", "", "4  publish    every role handler writes its own capability", "              to BOTH planes - control is authoritative,", "              data is a mirror for ordinary readers", "", "5  ELECT      read the capability records FROM control,", "              score them, repoint databasehost.frognet"]),
      P([ t("Step two is the part people trip over.''',
    r'''      ...codeBlock(["1  build the host block from what the walk proved", "", "2  CONTROL    compute the highest .1, with no election", "", "3  FLOOR      databasehost.frognet = the best mysql-running .1", "              per control's capability rows, holding a healthy", "              incumbent; the highest .1 if there is no", "              capability data", "", "4  write the control line:", "              databasehost_control.frognet = highest .1", "", "5  publish    every role handler writes its capability", "              (broker mode only)", "", "6  ELECT      every role's handler over control's rows;", "              if no databasehost is named, it pins to", "              the control"]),
      P([ t("Step three is the part people trip over.''')

# ---------- #31b Publishing to both: broker mode only ----------
sub(r'''Reading from a plane whose location is fixed by arithmetic breaks that loop.") ]),
''', r'''Reading from a plane whose location is fixed by arithmetic breaks that loop.") ]),
      P([ t("This is broker mode. On a region nothing is published to either plane for the election; capability is a fact in the region, and the election reads the members\u2019 facts, so it never depends on a store on the host that just left.") ]),
''')

# ---------- #8 the barrier is gone ----------
sub(r'''      H2("Waiting for everyone"),
      P([ t("One more rule, and it is the reason the halves of a torn network can rejoin without arguing. The election does not decide the moment it has "), i("some"), t(" capability records. It waits until control holds a fresh record for "), b("every"), t(" live machine it can see. Until then the name stays pinned to the control host \u2014 always valid, always resolvable \u2014 and the roles are left exactly as they are.") ]),
      P([ t("Without that, the first node to finish merging would decide over a partial pool and the second would decide over a fuller one, and the two would land on different winners. Both would be following the rule correctly. Waiting for a complete pool is what turns \u201cevery node computes the same answer\u201d from a hope into a property. A node on its own is trivially complete \u2014 it is the only machine, and it has published its own capability \u2014 so a lone box elects itself for every role and never waits for anybody.") ]),
''', r'''      H2("Absent is an answer"),
      P([ t("The election does not wait. It runs on every converged merge. A live .1 that has not published is still a candidate; its capability is unknown, so it ranks at the floor, and the highest .1 breaks the tie. Every node reads the same store and the same converged hosts block, so every node reaches the same answer from the same absence.") ]),
      P([ t("If the election names no database host, the name pins to the control host. A lone box elects itself for every role.") ]),
''')

# ---------- #9 the write-then-read detector is the application's; what FrogNet runs ----------
sub(r'''      P([ t("And detection is where FrogNet\u2019s involvement ends. It "), b("fires the callbacks"), t(" and stops. What to do next''',
    r'''      P([ t("What FrogNet runs is a per-process watcher on the name: when "), mono("databasehost.frognet"), t(" resolves somewhere new, it calls each module\u2019s "), mono("hostReset()"), t(" ("), mono("core/frognet_host_reset.py"), t("). On a region the daemon also writes the new databasehost into "), mono("/var/lib/frognet/view.json"), t(" within a second of the change. The node-heartbeat hooks that the proxy and daemon call ("), mono("NODE_HEARTBEAT_TS_V1"), t(") are not defined in the shipped core, so they do nothing.") ]),
      P([ t("And detection is where FrogNet\u2019s involvement ends. What to do next''')

# ================= Part VII =================

# ---------- #33 Part VII lead: the region replaces the broker for its members ----------
sub(r'''and nothing above them changes shape when it does.") ]),
      H1("19.  The Broker"),''',
    r'''and nothing above them changes shape when it does."), t(" A node can instead be a member of an FD&F region (Specification C). There the region replaces the broker: membership is the node\u2019s session with the RAM manager, and its one tunnel goes to the region\u2019s hub (fdf-hub, beside the RAM manager on StreamingFrog), which forwards between members. AllowedIPs is still 10.0.0.0/8. Nothing polls; the daemon acts on region events, and when the region is gone its routes, names and tunnel go with it. The broker keeps serving nodes that are not on a region.") ]),
      H1("19.  The Broker"),''')

# ---------- #29 #11 #10 \u00a719 para 2 ----------
sub(r'''      P([ t("The broker is "), b("stateless"), t(" \u2014 a rendezvous, not a record. What it seems to know \u2014 who is in which "), b("pond"), t(", who is in which "), b("chorus"), t(" \u2014 it holds no authority over and keeps nothing the network depends on: all of it is re-derived from nodes registering and polling, and a node that stops polling simply ages out. Wipe the broker clean and the next round of polls rebuilds its whole picture. So when''',
    r'''      P([ t("The broker holds little. The ponds and the addresses it handed out are its own records. Everything else \u2014 who has registered, which tunnels exist, who is in which "), b("chorus"), t(" \u2014 is re-derived from nodes registering and polling, and a node that stops polling ages out. Nodes, tunnels and choruses are rebuilt by nodes checking in. A "), b("pond"), t(" is not: after a wipe or "), mono("db reset"), t(" the operator recreates each pond with the same base, step and password, and the nodes re-register on their next poll. So when''')
sub(r'''And "), b("the broker is never a party to the data"), t(": on one LAN, nodes talk directly and the broker never sees a byte; across the internet, the WireGuard tunnels it hands out meet at the broker, which relays the encrypted bytes without holding or reading anything.''',
    r'''And "), b("the broker stores none of the data"), t(": on one LAN, nodes talk directly and the broker never sees a byte; across the internet, the tunnels it hands out terminate at the broker, which decrypts each one and forwards between them in the kernel, so inter-site traffic is in the clear inside the broker.''')

# ---------- #12 LAN-only node keeps its broker config ----------
sub(r'''t(": it carries no broker configuration at all and reaches the wider world through that one peer.''',
    r'''t(": it keeps its broker configuration but does not register, and it reaches the wider world through that one peer.''')

# ---------- #11 Where the broker can live, para 3 ----------
sub(r'''t("), but that file is a cache the nodes refill, not a system of record: there is no database server to administer, no cluster to keep alive, and nothing in it the network cannot rebuild by checking in again. That is why''',
    r'''t("). Most of it is refilled by the nodes checking in; the pond definitions are not, so back it up. There is no database server to administer and no cluster to keep alive. That is why''')

# ---------- #13 backup line ----------
sub(r'''"backup   copy that one file. that's the whole pond's membership.",''',
    r'''"backup   frognet-pond db backup  (SQLite's backup API; a bare copy misses the WAL)",''')

# ---------- #14 CHORUSES format and role ----------
sub(r'''t(": the broker URL, the pond, the choruses to join.") ]),''',
    r'''t(": the broker URL, the pond, and the choruses it means to join (recorded here; the join itself is a call to the broker\u2019s chorus API).") ]),''')
sub(r'''"CHORUSES=family,workshop",''', r'''"CHORUSES=\"family workshop\"",''')

# ---------- #15 pond-bootstrap is event-driven ----------
sub(r'''      P([ t("A timer, "), mono("frognet-pond-bootstrap"), t(", reads it and tries the broker every couple of minutes until it answers a health check; the node is a complete network the whole time, and the moment the broker is reachable, bootstrap proceeds and disables itself.") ]),''',
    r'''      P([ t("Each merge runs "), mono("frognet-pond-bootstrap"), t(" until enrolment is done. A merge is what a network change already produces (a DHCP lease, an interface up or down, a connectivity change), so the broker is tried at the moment it might have become reachable. When "), mono("GET /api/v4/ponds"), t(" answers 200, the node registers, starts the tunnel daemon and writes "), mono("/etc/frognet/pond_bootstrap_done"), t(", and the merge stops calling it. The node is a complete network the whole time.") ]),''')

# ---------- #16 REWRITE: Joining a pond ----------
sub(r'''      H2("Bootstrapping and handing out keys"),
      P([ t("The first node to reach a fresh broker "), b("bootstraps"), t(" it \u2014 claims it and gets the admin token to mint credentials for everyone else. After that, joining is two steps: an admin mints a "), b("passcode"), t(", a node "), b("redeems"), t(" it for a long-lived "), b("group token"), t(".") ]),
      exLabel("Standing up hometown, then adding Mom"),
      ...codeBlock([
        "# 1. First node bootstraps the broker and names the group",
        "frognet-tunnel-setup-v3.sh \\",
        "     http://streamingfrog.com:18257 \\",
        "     --bootstrap hop-2026-spring hometown",
        "",
        "# 2. On the broker admin, mint a passcode for Mom's box",
        "frognet-broker-admin.sh passcode new --group hometown   ->  ribbit-9F3K",
        "",
        "# 3. On MomBox, redeem it to join",
        "frognet-tunnel-setup-v3.sh \\",
        "     http://streamingfrog.com:18257  ribbit-9F3K",
      ]),
      P([ t("What lands on MomBox is a "), mono("GROUP_TOKEN"), t(" written into that same "), mono("/etc/frognet/tunnel.conf"), t(" \u2014 its membership card. With it the tunnel daemon asks the broker to wire it to its peers, and a tunnel comes up between "), mono("10.90.90.1"), t(" and the rest of hometown. \u201CDistributing broker knowledge\u201D is just those two things: the "), mono("tunnel.conf"), t(" that points each node at the broker, and the passcodes you hand out to redeem.") ]),
''', r'''      // [C16_JOINING_A_POND_V1] rewritten to match frognet-tunnel-setup-v3.sh and broker v4
      H2("Joining a pond"),
      P([ t("The broker\u2019s installer bootstraps it once and keeps the admin token on the broker machine. The operator creates a pond: "), mono("frognet-pond create hometown --node-base 10.101.100.1 --increment 10 --max-users 50 [--password PW]"), t(". A node joins with three lines in "), mono("/etc/frognet/tunnel.conf"), t(" ("), mono("BROKER_URL"), t(", "), mono("POND_NAME"), t(" and, if the pond has one, "), mono("POND_PASSWORD"), t(") and "), mono("frognet-tunnel-setup-v3.sh [--pond-password PW]"), t(".") ]),
      P([ t("That script generates the node\u2019s WireGuard keypair and posts its GUID ("), mono("/etc/fnid"), t("), public key and "), mono("/24"), t(" to "), mono("/api/v4/register"), t(". The broker identifies the node by its GUID. There is no passcode and no group token. \u201CDistributing broker knowledge\u201D is that one file.") ]),
      exLabel("Standing up hometown, then adding Mom"),
      ...codeBlock([
        "# on the broker",
        "frognet-pond create hometown --node-base 10.101.100.1 --increment 10 --password hop-2026",
        "",
        "# on MomBox: /etc/frognet/tunnel.conf",
        "BROKER_URL=http://streamingfrog.com:18257",
        "POND_NAME=hometown",
        "POND_PASSWORD=hop-2026",
        "",
        "# then",
        "frognet-tunnel-setup-v3.sh --pond-password hop-2026",
      ]),
''')

# ---------- #17 local broker caveat (\u00a719) ----------
sub(r'''t(" Same software, same file \u2014 the address you put in it, and the problem the tunnel solves, is the only difference.") ]),
''', r'''t(" Same software, same file \u2014 the address you put in it, and the problem the tunnel solves, is the only difference.") ]),
      P([ t("Only a node with its own non-FrogNet default route registers ("), mono("frognet-tunnel-setup-v3.sh"), t("), so both ends of a shortcut must be such nodes.") ]),
''')

# ---------- #11 \u00a720 opening: what the broker stores ----------
sub(r'''the broker holds no authority over your network and nothing it stores is irreplaceable. What it appears to know is re-derived every time a node checks in. It is never a party to your data. Its database is a cache the nodes refill. Build it carefully because it is the one piece with a public address \u2014 not because the network depends on it, which it does not.") ]),''',
    r'''the broker holds no authority over your network, and most of what it stores is re-derived every time a node checks in. The ponds are the exception; they are its own records. It terminates the tunnels and forwards between them, so inter-site traffic passes through it. Build it carefully: it is the one piece with a public address.") ]),''')

# ---------- #18 REWRITE (a): What you will be asked + Running the installer -> Running the installer + Ponds ----------
sub(r'''      H2("What you will be asked"),
      P([ t("One script does the whole install \u2014 clean machine or upgrade \u2014 and asks for what it cannot work out. Have these ready:") ]),
      ...codeBlock(["  the public name clients will use     (FQDN, or a LAN address)", "  the port the API answers on          (default 18257)", "  the web document root                (default /var/www/html)", "  the WireGuard port range             (default 51901-51999)", "  the name of the first pond           (e.g. hometown)", "  its size, node base, and step        (50, 10.101.100.1, 10)", "  an optional pond join password", "  the operator console password        (required)", "  an admin API token                   (or let it generate one)"]),
      P([ t("The node base is the address the first node's gateway takes, and the step is how far apart the following nodes land in the third octet \u2014 base "), mono("10.101.100.1"), t(" with a step of 10 gives you "), mono("10.101.100.1"), t(", "), mono("10.101.110.1"), t(", "), mono("10.101.120.1"), t(", and so on. Two constants are not negotiable and the installer will refuse you if you fight them: the base ends in .1, because .1 is always FrogNetHost in any subnet, and the pond lives inside "), mono("10.0.0.0/8"), t(" clear of "), mono("10.253"), t(" and "), mono("10.254"), t(", which are the transit and chorus ranges the broker allocates from.") ]),
      H2("Running the installer"),
      exLabel("A clean machine, start to finish"),
      ...codeBlock(["sudo ./install_frognet_broker.sh", "", "  ... it asks the questions above, then:", "  - installs dependencies and the Python environment", "  - lays down the gatekeeper, the web front doors, the CLI", "  - writes the systemd units and the Apache vhost", "  - starts everything and bootstraps the admin token", "  - creates your first pond THROUGH the running gate", "  - runs a postflight and prints one line", "", "BROKER GATE: PASS 10/10"]),
      P([ t("That last line is the only proof the install worked, and it is worth reading rather than glancing at. The postflight does not ask the services whether they are happy; it tries things and reports what happened. It sends a real request through the real front door and waits for the real answer. It attempts, as the web user, to read the admin token and the private configuration, and the install fails if either succeeds. It checks that nothing is listening where the old broker used to. It calls an administrative route with no credential and requires a refusal, then calls it with one and requires an answer. A green line means those things were tried, not assumed.") ]),
      P([ t("The same script upgrades an existing broker, and works out which case you are in by looking for a database. Three modes:") ]),
      ...codeBlock(["fresh          no database found; build everything", "upgrade-keep   replace the code; keep every pond, node, tunnel", "upgrade-wipe   replace the code, then wipe \u2014 properly", "", "sudo ./install_frognet_broker.sh --mode upgrade-keep", "sudo ./install_frognet_broker.sh --verify-only   # postflight only"]),
      P([ t("The wipe deserves a sentence, because the obvious version of it is wrong. A wipe does not delete the database file. It brings the new stack up and then asks the running broker to reset itself, so the wipe runs down the same teardown path a single tunnel takes when it is retired. That matters because the database is not the only state: behind every tunnel record are two WireGuard interfaces, two port forwards, and a network namespace. Delete the records and all of that survives with nothing left that names it \u2014 ports stay held, and the next install allocates around ghosts. You find out weeks later, when new tunnels quietly stop being created, and it looks nothing like its cause.") ]),
''', r'''      // [C18_INSTALLER_REWRITE_V1] rewritten to match install_frognet_broker.sh and frognet-pond in the broker bundle
      H2("Running the installer"),
      ...codeBlock(["sudo ./install_frognet_broker.sh [--port 18257] [--webroot DIR] \\", "     [--no-console] [--no-packages] [--dry-run]"]),
      P([ t("It asks no questions. It installs the Python environment, writes "), mono("frognet-broker-v4.service"), t(" (uvicorn on "), mono("0.0.0.0:<port>"), t(", plain HTTP), backs up an existing database with SQLite\u2019s backup API, mints the admin token through "), mono("POST /api/v4/bootstrap"), t(" and keeps it in "), mono("/etc/frognet/admin_token_v4.conf"), t(". It finishes with a postflight: the unit is active and "), mono("GET /api/v4/ponds"), t(" answers 200. It is idempotent, so running it again on an existing broker is the upgrade.") ]),
      H2("Ponds"),
      P([ t("Create ponds afterwards: "), mono("frognet-pond create <name> --node-base <10.x.y.1> --increment <n> --max-users <n> [--password PW]"), t(".") ]),
      P([ t("The node base is the address the first node\u2019s gateway takes, and the increment is how far apart the following nodes land in the third octet \u2014 base "), mono("10.101.100.1"), t(" with an increment of 10 gives you "), mono("10.101.100.1"), t(", "), mono("10.101.110.1"), t(", "), mono("10.101.120.1"), t(", and so on. The broker refuses a node base that does not end in .1, and refuses to register a node subnet inside "), mono("10.253"), t(" or "), mono("10.254"), t(", which are the transit and chorus ranges it allocates from.") ]),
''')

# ---------- #18 REWRITE (b) + #19 #20 #22: What the installer builds ----------
sub(r'''      H2("What the installer builds \u2014 and the wall in the middle of it"),
      P([ t("Two things listen on a broker machine, and keeping them straight is the whole of its security design.") ]),
      P([ t("The broker service itself. It opens and manages its own port \u2014 the one you handed out \u2014 terminates its own encryption, allocates the WireGuard ports, and programs the namespaces. Nodes talk to it directly. There is no web server in front of it, and nothing about it is a web page. This is the machine your tunnels attach to, and the port a node connects to is the broker answering for itself.") ]),''',
    r'''      H2("What the installer builds"),
      P([ t("The broker service. It opens and manages its own port \u2014 the one you handed out \u2014 speaks plain HTTP, allocates the WireGuard ports, and programs the namespaces. Nodes talk to it directly. There is no web server in front of it. This is the machine your tunnels attach to, and the port a node connects to is the broker answering for itself.") ]),
      P([ t("WireGuard ports come from "), mono("FROGNET_WG_PORT_BASE"), t("/"), mono("MAX"), t(" (code default 51901-51999; the shipped drop-in sets 51000-59999). Behind every tunnel record are two WireGuard interfaces and two port forwards inside the pond\u2019s network namespace.") ]),''')
# drop the web tier / console / air-gap paragraphs, keep the HTTP paragraph
sub(r'''      P([ t("The web tier, on port 80.''', r'''      P([ t("[C_DROP_WEB_TIER]The web tier, on port 80.''')
import patchlib as PL
s_start = PL.s.index(r'''      P([ t("[C_DROP_WEB_TIER]''')
s_end = PL.s.index(r'''      P([ t("Which leaves the obvious objection''')
dropped = PL.s[s_start:s_end]
assert dropped.count("\n") == 7, dropped.count("\n")
assert 'spool/admin' in dropped and 'fails open' in dropped
PL.s = PL.s[:s_start] + PL.s[s_end:]
sub(r'''      P([ t("That is not a way into your network, and the distinction is the design rather than a quibble: a port answering is not a route in. What answers holds no credential. It cannot route to the 10/8 plane. Every request it will act on is a named entry on a list, checked before anything runs. And on the console side, the part a stranger with a browser can reach cannot even reach the broker \u2014 it writes a note into a directory and waits. There is no socket behind it to pivot to and no key behind it to steal.") ]),
      P([ t("So a web tier with an air gap behind it is not a compromise between exposure and convenience, arrived at reluctantly. It is the recommended architecture, and here it is simply what the software does \u2014 which is also why running the console and the broker on one machine is a reasonable thing to do rather than something to apologise for. Co-location is safe when the only thing crossing between the two is a file in a directory, checked against a list of the operations permitted to exist. The attack surface remains zero.") ]),
      P([ t("None of this touches your nodes. They were never talking to the web server; they talk to the broker service, on its own port, exactly as they did before. A console can be rebuilt this way without a single node noticing.") ]),
''', r'''      P([ t("A port answering is not a route in by itself. Admin routes require the bearer token. The same machine terminates the pond\u2019s tunnels, so whoever owns it owns the inter-site seam (Part XII). FrogNet does not force more on it and does not prevent it.") ]),
''')

# ---------- #17 #24 #25 intranet case ----------
sub(r'''and the broker mints a tunnel between them exactly as it would across the internet. The tunnel does not make them reachable \u2014 they already were. It makes them adjacent. Traffic stops threading eight machines and goes straight.") ]),''',
    r'''and the broker mints a tunnel between them exactly as it would across the internet. The tunnel does not make them reachable \u2014 they already were. It makes them adjacent. Traffic stops threading eight machines and goes straight. Only a node with its own non-FrogNet default route registers ("), mono("frognet-tunnel-setup-v3.sh"), t("), so both ends of a shortcut must be such nodes.") ]),''')
sub(r'''"  every packet crosses eight machines. ~40 ms, 8 ways to fail."''',
    r'''"  every packet crosses eight machines, and each is one more way to fail."''')
sub(r'''"  ~3 ms, and the eight relays keep their own traffic."''',
    r'''"  the eight relays keep their own traffic."''')
sub(r'''      P([ t("Which leaves the obvious objection, and it deserves''', r'''      P([ t("That leaves one objection, and it deserves''')
sub(r'''The chain does not disappear; it drops to a fallback and takes over unchanged the moment the tunnel stops answering.''',
    r'''The chain does not disappear. It is still in the walk, and the next merge, when the tunnel stops answering, ranks it best again.''')

# ---------- #18 REWRITE (c): Running it day to day ----------
sub(r'''      H2("Running it day to day"),
      P([ t("One command talks to the broker, and it goes through the same front door every node uses. There is no private back channel for the operator, deliberately: if the gate is broken you find out with your first command, instead of having a side entrance that hides the outage.") ]),
      ...codeBlock(["frognet-pond ping             is the gate alive", "frognet-pond list             every pond", "frognet-pond status plant     one pond in full", "frognet-pond members <pond>   who has registered", "frognet-pond forget <pubkey>  force-deregister a node", "frognet-pond block  <pubkey>  refuse a key, tear its tunnels down", "frognet-pond result <id>      fetch a request that timed out"]),
      ...codeBlock(["journalctl -u frognet-gatekeeper-broker -f"]),
      P([ t("Every refused request is quarantined with its reason recorded, so \"why did that call fail\" is answered by reading two files rather than by guessing. The failure modes are few enough to list. Every call timing out means the private service is down \u2014 check it. Requests piling up unanswered mean it is running but stuck, and because it works strictly one request at a time, the culprit is the last one it started. A node suddenly refused where it used to be served means the gate's checks are stricter than what was silently tolerated before; the log names the exact field.") ]),
''', r'''      H2("Running it day to day"),
      P([ mono("frognet-pond"), t(" talks to the broker\u2019s port with the bearer token.") ]),
      ...codeBlock(["frognet-pond list               every pond", "frognet-pond status <pond>       one pond in full", "frognet-pond nodes [pond]        who has registered", "frognet-pond forget <pubkey>     force-deregister a node", "frognet-pond block <pubkey>      refuse a key, tear its tunnels down", "frognet-pond unblock <pubkey>    lift a block", "frognet-pond blocklist           every blocked key", "frognet-pond remove              remove a pond", "frognet-pond db ...              the database commands below"]),
      ...codeBlock(["journalctl -u frognet-broker-v4 -f"]),
''')

# ---------- \u00a720 db section: no web front door; the teardown rule stated here; #11 db reset ----------
sub(r'''These are reachable from a web front door, and the difference''',
    r'''These are reachable over the broker\u2019s own port, and the difference''')
sub(r'''The rule underneath all four is the same one as the wipe, and it is the only thing about them worth memorising: kernel state comes down before records go away. Every one of these tears the tunnels down through the same path the broker uses to retire a single tunnel \u2014 interfaces deleted, port forwards removed, namespaces released \u2014 and only then deletes anything.''',
    r'''The rule underneath all four is the only thing about them worth memorising: kernel state comes down before records go away. Delete the records alone and the interfaces and port forwards behind them survive with nothing left that names them \u2014 ports stay held, and later allocations work around ghosts. Every one of these tears the tunnels down through the same path the broker uses to retire a single tunnel \u2014 interfaces deleted, port forwards removed \u2014 and only then deletes anything.''')
sub(r'''That is also why db reset is a recoverable operation rather than a catastrophe \u2014 the nodes hold the truth, and they will tell it to a blank broker the next time they check in.") ]),''',
    r'''A "), mono("db reset"), t(" is recoverable, but not by the nodes alone: nodes, tunnels and choruses are rebuilt by nodes checking in, and the ponds are not. The operator recreates each pond with the same base, step and password, and the nodes re-register on their next poll.") ]),''')

# ---------- \u00a721: in the clear inside the broker (#10, #26 consistency) ----------
sub(r'''nothing at the application layer parses them, buffers them, or writes them anywhere. That is why the chapter can say the broker carries the traffic and the security part can say it cannot read it, without the two sentences fighting.") ]),''',
    r'''nothing at the application layer parses them, buffers them, or writes them anywhere. Each packet is decrypted off one tunnel and encrypted onto the next, so inside the broker it is in the clear.") ]),''')

# ---------- #28 endpoint port ----------
sub(r'''"   endpoint    203.0.113.7:51820   (the broker, reachable)",''',
    r'''"   endpoint    203.0.113.7:51907   (a per-tunnel port the broker allocated)",''')

# ---------- #27 the tunnel daemon polls ----------
sub(r'''owns them, on its own loop: on a broker event it brings the interfaces up and installs their routes; a periodic reconciliation rebuilds any interface that has vanished, within tens of seconds; on startup it tears down dead leftovers so a reconnect always starts clean.''',
    r'''owns them. With a broker, it reconciles kernel wg interfaces to the broker\u2019s channel list on each poll (every 5 minutes) and at the start of every merge, bringing up what is missing and tearing down what the broker dropped. On an FD&F region it does the same on each region event. On startup it tears down dead leftovers so a reconnect always starts clean.''')

# ---------- #33 \u00a721 pointer: region routes ----------
sub(r'''the daemon rebuilds the bearer, the routes return, and discovery never saw a thing.") ]),
''', r'''the daemon rebuilds the bearer, the routes return, and discovery never saw a thing.") ]),
      P([ t("This chapter is broker mode. On a region the routes are FD&F\u2019s plan ("), mono("fdf_graph"), t("), applied by the tunnel daemon, which admits LAN candidates with one probe each and gates hub routes on the WireGuard handshake.") ]),
''')

# ---------- #34 \u00a722 walk ranking is broker mode (and no fallback wording) ----------
sub(r'''so when a destination is reachable both directly and over a tunnel, the faster wins and the other waits as a fallback.''',
    r'''so when a destination is reachable both directly and over a tunnel, the faster wins and the other stays in the walk, ranked again on the next merge. That is broker mode. On a region the walk does not run; the routes are the region\u2019s plan, as the end of the previous chapter describes.''')

# ---------- #35 choruses on a region ----------
sub(r'''and they are what the broker actually strings together across the internet.") ]),''',
    r'''and they are what the broker actually strings together across the internet. On a region a pond is the region\u2019s area. Choruses and "), mono("10.254"), t(" are unchanged for now.") ]),''')

# ---------- #26 What keeps it private ----------
sub(r'''a pond is a one-time exchange \u2014 a node redeems a "), b("passcode"), t(" with the broker for a "), b("group token"), t(" it then carries as its credential, and it generates its own WireGuard keypair, handing the broker only its "), i("public"), t(" key; private keys never leave the box. ''',
    r'''a pond is registration: the node generates its own WireGuard keypair and presents its GUID, its "), i("public"), t(" key and the pond password, if the pond has one; private keys never leave the box. ''')
sub(r'''every cross-site tunnel is WireGuard, so traffic between sites is encrypted end to end.''',
    r'''every cross-site tunnel is WireGuard, so traffic is encrypted on each leg between a node and the broker, and is in the clear inside the broker.''')

done()
