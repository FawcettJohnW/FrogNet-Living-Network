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
import sys; sys.path.insert(0,'/tmp/claude-0/verify1013'); from patchlib import *
import re as _re, patchlib as _pl
def S(old, new, cnt=1):
    # old/new written with real characters; source holds them as backslash-u escapes (either hex case)
    pat = ''.join(('\\\\u(?i:%04x)' % ord(c)) if ord(c) > 127 else _re.escape(c) for c in old)
    m = _re.findall(pat, _pl.s)
    if len(m) != cnt: raise SystemExit('NO MATCH (%d, want %d): %r' % (len(m), cnt, old[:160]))
    _pl.s = _re.sub(pat, lambda _m: U(new), _pl.s)

# ---------------- Part XV
S('''P([ t("On a FrogNet node, "), mono("fdf-comms.sh"), t(" starts "),''',
    '''P([ t("On a FrogNet node in a region, "), mono("fdf-comms.sh"), t(" starts "),''')
S('''The broker bundle names three console files and explicitly ships no site — which was true when it was written''',
    '''The broker bundle (the broker has since been removed) named three console files and explicitly shipped no site — which was true when it was written''')

# ---------------- Specification C
S('''and it is now also what FrogNet nodes join in place of the broker and the getHosts crawl.") ]),''',
    '''and it is now the internet path of a FrogNet node: a node in a region keeps its membership in the region's RAM manager and one WireGuard link to the region's hub, fdf-hub. It replaced FrogNet's broker, which is gone.") ]),''')
S('''t(", and every node elects the databasehost and mediahost from those facts; "), mono("fdf-client")''',
    '''t(", and every node elects the databasehost from those facts; "), mono("fdf-client")''')
S('''The FrogNet region uses the direct form: "), mono("fdf-region-join"), t(" defaults to "), mono("165.232.153.144:51888"), t(", area "), mono("frognet"), t(". The bootstrap''',
    '''A FrogNet node uses the direct form: "), mono("fdf-region-join SITE_KEY RAM_HOST:PORT AREA"), t(" takes the RAM manager's address and the area, all three required, and the installer's "), mono("--region"), t(" passes them. The bootstrap''')
S('''P([ b("The region."), t(" A FrogNet pond on FD&F is a region. On the droplet StreamingFrog (165.232.153.144): the RAM manager "), mono("rina-host"), t(" on TCP 51888 ("), mono("fdf-ram-frognet"), t(", Permanent records in "), mono("/opt/fdf/var/area-frognet.perm"), t("), the hub "), mono("fdf-hub"), t(" on UDP 50889 ("), mono("wg-frognet"), t(", 10.253.253.254/16), and the hub's own membership ("), mono("fdf-client-frognet"), t("). A node joins with one installer ("), mono("sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>"), t(", or the x86_64 one), which runs "), mono("fdf-region-join"), t(": site.env, join.env, "), mono("fdf-client"), t(", and the tunnel daemon restarted in region mode.") ]),''',
    '''P([ b("The region."), t(" A FrogNet pond on FD&F is a region, and a region is made on the machine that holds it with one command, "), mono("fdf-region-create AREA PUBLIC_IP"), t(" ("), mono("--ram-port"), t(", default 51888; "), mono("--hub-port"), t(", default 50889; "), mono("--ufw"), t("). It makes the site key once (root-only in "), mono("/opt/fdf/var/site-key-AREA"), t(", printed once), starts the RAM manager "), mono("rina-host"), t(" as "), mono("fdf-ram-AREA"), t(" (Permanent records in "), mono("/opt/fdf/var/area-AREA.perm"), t("), maps the site key on it, writes "), mono("/opt/fdf/hub/hub.env"), t(", and starts the hub "), mono("fdf-hub"), t(" ("), mono("wg-AREA"), t(", 10.253.253.254/16) and the hub's own membership ("), mono("fdf-client-AREA"), t("). A machine holds one region. PUBLIC_IP is whatever address the members reach it at; a LAN address makes a region with no internet.") ]),
      P([ t("A node joins at install: "), mono("frognet_install.sh --region RAM_HOST:PORT --site-key KEY --area AREA"), t(". Its last phase runs "), mono("fdf-install-$(uname -m).run KEY RAM_HOST:PORT AREA"), t(" from the checkout; a node already installed LAN-only runs that file itself. The file runs "), mono("fdf-region-join"), t(": site.env, join.env, "), mono("fdf-client"), t(", and the tunnel daemon restarted in region mode. The daemon owns its WireGuard key. It makes "), mono("/var/lib/frognet-tunnel/node_private.key"), t(" at its first region start and announces the public half; nothing in the installer makes a key, and "), mono("fdf-leave"), t(" keeps it, so a later join announces the same one. The FrogNet app's built-in first pond, area frognet at 165.232.153.144:51888, is the author's own region, on the droplet StreamingFrog.") ]),''')
S('''P([ t("That replaced the broker's pairwise tunnels (2·N(N−1)/2 interfaces) with one interface and N peers''',
    '''P([ t("That replaced the broker's pairwise tunnels (2·N(N−1)/2 interfaces; the broker itself was removed in October 2026) with one interface and N peers''')
S('''Joining FrogNet to the region then removed, in region mode, the broker, the crawl, makeHostJson, the store's capability tuples and the merge pokes. A mechanism''',
    '''Joining FrogNet to the region then removed, in region mode, the broker, the crawl, makeHostJson, the store's capability tuples and the merge pokes. The October 2026 cleanup removed the broker from the tree: a FrogNet node is LAN-only or in a region, and its tunnel daemon's first log line says which. A mechanism''')
S('''All nodes name the same new databasehost within one event of its loss.") ]),''',
    '''All nodes name the same new databasehost within one event of its loss.") ]),
      bullet([ t("After the October 2026 cleanup (build fdf-2026-10-12-r20), in network namespaces: region-loop PASS 13 + 1 skipped; ponds-daemon 21; reach 70; LAN-only 19/19; the hub oracle 27. In Ubuntu 24.04 containers: a region made with "), mono("fdf-region-create"), t(", two nodes installed with "), mono("--region"), t(", both in region mode after the reboot, N1 to N2 3/3 at 2.3 ms.") ]),''')
S('''The same Ribbit programming surface runs above both, and FrogNet nodes now run their own discovery and tunnels on an FD&F region.''',
    '''The same Ribbit programming surface runs above both. A FrogNet node in a region takes its membership from the RAM manager and its tunnel from fdf-hub; a LAN-only node runs FrogNet's own discovery.''')

# ---------------- Part XVIII
S('''FD&F runs Ribbit without FrogNet's discovery, routing, elections, broker or tunnels.''',
    '''FD&F runs Ribbit without FrogNet's discovery, routing, elections or tunnels.''')


# ---------------- Part XVI 60b
S('''How they get there is not understood. It is disruptive and it is on the list.") ]),''',
  '''How they got there was never shown. The October 2026 cleanup removed the second owners of the LAN address (setup_lillypad_v4.bash's flush and "), mono("ip addr add"), t(", and the boot-time frognet-eth0-fixup); NetworkManager's profile is now the one owner. Whether that ends this case has not been shown on hardware. It stays on the list.") ]),''')
S('''...codeBlock(["$ sudo setup_lillypad_v4.bash <NetworkName> <GatewayIP> <BROKER_URL> --pond <PondName>", "", "   gives the machine its identity without running install;", "   reconfigures the network to the pond named"]),''',
  '''...codeBlock(["$ sudo setup_lillypad_v4.bash <NetworkName> <GatewayIP>", "", "   gives the machine its identity without running install;", "   rewrites the node's network configuration"]),''')
S('''...codeBlock(["  /etc/sentinels     discovery working files, and the", "                     transients written during execution", "", "  /etc/frognet       configuration, plus dated backups.", "                     tunnel.conf is the one that matters."]),''',
  '''...codeBlock(["  /etc/sentinels     discovery working files, and the", "                     transients written during execution", "", "  /etc/frognet       configuration: gateways.conf,", "                     ssid_projection.conf, wg.impl", "", "  /opt/fdf/var       on a region node only: join.env (the", "                     region switch) and site.env (root-only)"]),''')
S('''P([ t("A WireGuard interface appears in "), mono("ip a"), t(" and carries no traffic. Re-register with the broker rather than debugging the interface:") ]),
      ...codeBlock(["$ frognet-tunnel-setup-v3.sh          re-register, restart daemon", "$ systemctl restart frognet-tunnel-daemon-v3", "", "   the restart clears dead local interfaces and rebuilds", "   the tunnels from what the broker currently says"]),''',
  '''P([ t("A LAN-only node has no WireGuard at all. A region node has one link, to the hub. When it appears in "), mono("ip a"), t(" and carries no traffic, read the snapshot's hub entry (its handshake age) and the region's answer, rather than debugging the interface:") ]),
      ...codeBlock(["$ curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool", "                                     hub: iface, edge, handshake_age_s", "$ journalctl -u fdf-client          REFUSED by the region: the site key", "                                     is not the one the area holds", "$ systemctl restart frognet-tunnel-daemon-v3", "", "   the daemon brings the link up again from the region's", "   records; there is no broker to ask"]),
      P([ t("A REFUSED line means the node's site key is wrong: run the node's "), mono(".run"), t(" again with the key "), mono("fdf-region-create"), t(" printed.") ]),''')
S('''What "), i("is"), t(" a bug: the notification that should follow a completed merge does not always fire. A node finishes, tells its nearest neighbours to sync, and sometimes nobody hears. "), mono("makeHostJson.bash rebuild"), t(" is supposed to run at the end of the merge and sometimes has not. Run the merge again.") ]),''',
  '''What "), i("was"), t(" a bug: the notification that should follow a completed merge did not always fire. A node finished, told its nearest neighbours to sync, and sometimes nobody heard. Two causes were found and fixed in October 2026: "), mono("runMerge.bash"), t(" wiped the merge watcher's queue and the notification seen-set on every merge, and "), mono("propogateNotification.php"), t(" answered DUP when it could not write its seen-set, so a fresh node never merged on a neighbour's wave. Whether those were the whole of it has not been shown in the field. If a neighbour still has not heard, run the merge again.") ]),''')
S('''Its tunnel is one WireGuard channel to the hub; to re-assert it, run "), mono("sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>"), t(" (or the x86_64 installer) again. Add "), mono("fdf-client"), t(" to the services to check.") ]),''',
  '''Its tunnel is one WireGuard channel to the hub; to re-assert it, run the node's "), mono(".run"), t(" again from the checkout: "), mono("sudo bash opt/fdf/dist/fdf-install-$(uname -m).run <SITE KEY> <RAM_HOST:PORT> <AREA>"), t(". Add "), mono("fdf-client"), t(" to the services to check.") ]),''')

# ---------------- Appendix A
S('''grouped roughly as compute, memory, storage, media-encode capability, database capability, identity, and a live signal.''',
  '''grouped roughly as compute, memory, storage, database capability, identity, and a live signal.''')
S('''        "MEDIA ENCODE",
        "  ffmpeg, ffmpeg_version       encoder toolchain present",
        "  libvpx, encoders             codecs (libvpx, libx264, libx265 ...)",
        "  hw_encoder, gpu_render_node  hardware acceleration available",
        "",
        "DATABASE",''',
  '''        "DATABASE",''')
S('''        "                      address, else 127.0.0.1",
        "  av_port             9000, the A/V port",
''',
  '''        "                      address, else 127.0.0.1",
''')
S('''exLabel("Why two services read the same blob differently"),''',
  '''exLabel("How a service reads the blob"),''')
S(''' The "), b("mediahost"), t(" selector gates on "), mono("ffmpeg"), t(" with "), mono("libvpx"), t(", then scores the measured CPU benchmark. (The role still exists; the service it starts runs an "), mono("fnav.py"), t(" that is not in the tree. The Communicator’s calls go through "), mono("comms-media"), t(".) A real hardware encoder ("), mono("vaapi"), t(" only with a render node) multiplies that by 2.5, and a box under half a gigabyte of RAM is halved. Same blob, different readers, because host selection is a virtual function each service implements its own way. Each election scores only the parts it cares about.") ]),''',
  ''' The database host is the one elected role. The mediahost selector, which read media-encode fields, was removed with the mediahost role in October 2026, and the probe no longer takes those fields. Host selection is still a virtual function each service implements its own way, so a service added later scores only the parts of the same blob it cares about.") ]),''')
S('''Off a region, each merge runs the probe once, and every role handler publishes that same blob as its "), mono("<role>/capability"), t(" tuple. The media role adds the A/V port it serves. The elections read those tuples back from the store.") ]),''',
  '''Off a region, each merge runs the probe once, and the role handler publishes that blob as its "), mono("<role>/capability"), t(" tuple (databasehost is the one role). The election reads those tuples back from the store.") ]),''')

# ---------------- Appendix I
S('''Its WireGuard key is kept across tunnel rebuilds, but it is replaced whenever the key files are removed, as a full reset does.''',
  '''Its WireGuard key, which the tunnel daemon makes itself, survives an upgrade and a reset with --keep-identity, and is replaced when a full reset removes it.''')
S('''It runs at install, and "), mono("frognet-make-gateway.sh"), t(" runs it if the file is missing. A deliberate rotation''',
  '''It runs at install. A deliberate rotation''')
S('''Everything else "), b("reads"), t(", and a reader that finds the file missing refuses. That refusal is the whole design.''',
  '''Everything else "), b("reads"), t(". The tunnel daemon reads it when it starts; in a region, a missing file is logged as "), mono("CRITICAL: No GUID at /etc/fnid"), t(", with the command that makes one, and the daemon does not make one itself. That refusal is the whole design.''')
S('''        "  read, and refuse   ->  registration stops with a clear",
        "                         error; an operator restores or",
        "                         deliberately re-issues the identity",
        "",
        "  read, and invent   ->  the node registers as a stranger",
        "                         carrying a familiar NAME, and the",
        "                         broker now holds two live records",
        "                         claiming to be the same machine",''',
  '''        "  read, and refuse   ->  the daemon says so, loudly; an",
        "                         operator restores or deliberately",
        "                         re-issues the identity",
        "",
        "  read, and invent   ->  the node announces itself as a",
        "                         stranger carrying a familiar NAME,",
        "                         and the region holds two identities",
        "                         for the same machine",''')
S('''      H1("How the broker uses it"),
      P([ t("The identity travels with every registration. The broker matches on it first — before the name, before the keys, before the address — and what it finds decides what happens next.") ]),
      P([ t("A known identity means this is a machine already on record. Its row is updated in place: it keeps its history, its group memberships, its number. A changed name is just a rename. A changed encryption key is handled explicitly — the tunnels carrying the old key are torn down so they rebuild against the new one, rather than lingering as connections nobody can use.") ]),
      P([ t("An unknown identity means a new machine, and that is where the care is needed — because a name is unique among the live nodes of a network. A new machine arriving under a name that is already taken is refused: the broker's unique (pond, name) index rejects the row, and today the node sees a server error rather than a message naming the conflict. The refusal is still the system telling you something true: either two machines have been given the same name, or one machine has lost its identity and is about to be enrolled twice.") ]),''',
  '''      H1("How the region uses it"),
      P([ t("The tunnel daemon announces the identity in the region as "), mono("facts.guid"), t(", with the node's name, its /24 and its WireGuard public key. The session is the enrollment; the identity is what the region knows the machine by. A service says where it runs as "), mono("w:<guid>:<port>"), t(" in a where cell, and each reader resolves the identity in its own scope to that machine's address there, or to ENOENT when it is gone (Chapter 65).") ]),
      P([ t("A node that comes back after a rebuild with its identity kept ("), mono("--preserve"), t(", or a reset with "), mono("--keep-identity"), t(", both of which keep the daemon's key too) announces the same identity and the same key. What the region refuses is a second owner: a /24 a live member already holds is refused, and "), mono("fdf-hub"), t(" logs "), mono("REFUSED <subnet> claimed by ..."), t(", naming the holder.") ]),
      P([ t("Until October 2026 the broker matched each registration on this identity and kept a row per machine. The broker is gone, and its row with it.") ]),''')
S('''      P([ t("Because an identity is durable, retiring one is an explicit act with its own operation. It is not a side effect of anything. Retiring tears down the machine's tunnels, removes it from its groups, marks its key as retired and frees its name for reuse — in one step, so no fragment of the old machine is left behind holding a resource the new one needs.") ]),
      P([ t("There is one trap worth knowing before you meet it. The retire operation is keyed on the identity itself. So if a machine loses its identity file and is then re-enrolled, the record blocking its name is keyed by an identifier that no longer exists anywhere, so the GUID retire ("), mono("POST /api/v4/retire-guid"), t(") cannot reach it. The operator clears it by key on the broker: "), mono("frognet-pond forget <pubkey>"), t(". The identity is not a secret. Keep a copy of "), mono("/etc/fnid"), t(".") ]),''',
  '''      P([ t("Because an identity is durable, retiring one is an explicit act. It is not a side effect of anything. "), mono("frognet-node-guid.sh regenerate"), t(" writes a new one, read-only. It is local: nothing else is told. The tunnel daemon reads the file when it starts, so the new identity is announced after the daemon restarts, and everything the old one wrote in the region went with its session.") ]),
      P([ t("The identity is not a secret. Keep a copy of "), mono("/etc/fnid"), t(".") ]),''')
S('''  "s-how-the-broker-uses-it": "how-the-broker-uses-it",''',
  '''  "s-how-the-broker-uses-it": "how-the-region-uses-it",''')
S('''  "67-fd-f-qualification": "67-what-was-removed-and-what-is-measured",''',
  '''  "67-fd-f-qualification": "67-what-was-removed-and-what-is-measured",
  // [R_NO_BROKER_ALIASES_V1] appendix headings retitled when the broker left the tree (2026-10-13)
  "how-the-broker-uses-it": "how-the-region-uses-it",''')

# ---------------- Appendix C
S('''["frognet_install.sh", "Lays the frozen world down and configures this node. Flags: --name, --ip (10.X.Y.1), --broker-host, --broker-port, --broker-scheme, --pond, --db-pass, --wifi-psk, --no-ssid-projection / --ssid-projection, --ap-iface, --country, --preserve, --from-repo, --rebuild-venv. Any other argument is refused.", "root CLI · Part IV"],''',
  '''["frognet_install.sh", "Lays the node file set down and configures this node; run from a checkout's root with --from-repo. Flags: --name, --ip (10.X.Y.1), --lan-iface, --db-pass, --wifi-psk, --no-ssid-projection / --ssid-projection, --ap-iface, --country, --no-reboot, --rebuild-venv, --from-repo; --region RAM_HOST:PORT --site-key KEY --area NAME (also join a region); --preserve (upgrade in place); --uninstall [--keep-identity] [--keep-db]; -h. Any other argument is refused. Without --preserve, a machine that has FrogNet is reset first.", "root CLI · Part IV"],
        ["frognet_reset.sh", "Resets the node (identity and databases unless --keep-identity / --keep-db); --uninstall also puts back what the install recorded of the machine before it. Leaves the region first when the node is in one.", "root CLI · Part IV"],''')
S('''["setup_lillypad_v4.bash", "Complete node-provisioning script — the end-to-end bring-up the installer drives.", "root CLI · Part IV"],''',
  '''["setup_lillypad_v4.bash <NetworkName> <GatewayIP> [--norestart]", "Complete node-provisioning script — the end-to-end bring-up the installer drives (phase E calls it with --norestart).", "root CLI · Part IV"],''')
S('''      H1("Broker, tunnels & membership"),
      refTable([
        ["frognet-pond-bootstrap.sh", "Run from the end of each merge while broker enrolment is pending (network events cause merges). On success it starts the tunnel daemon and writes /etc/frognet/pond_bootstrap_done. The node is a complete network the whole time. Not used on an FD&F region.", "script · Part VII"],
        ["frognet-tunnel-setup-v3.sh [--pond-password PW]", "Registers this node with the broker named in tunnel.conf (POST /api/v4/register: pond, WireGuard pubkey, /24, /etc/fnid GUID), then starts the tunnel daemon. Only a node with a direct, non-10.x upstream registers.", "CLI · Part VII"],
        ["frognet-pond create <pond> [--max-users N] [--node-base IP] [--increment N] [--password PW]", "On the broker: creates a pond for nodes to register into. Also ping, list, status, nodes, remove, forget <pubkey>, block/unblock, db, result.", "broker CLI · Part VII"],
        ["/etc/frognet/tunnel.conf", "The one broker config file. BROKER_URL, POND_NAME, optional POND_PASSWORD, CHORUSES, MAX_TUNNELS. On an FD&F region it may also carry RINA_RAM (the region join writes /opt/fdf/var/join.env and keeps a copy of this file as tunnel.conf.broker).", "config · Part VII"],
        ["broker.db", "The broker's state in one SQLite file (WAL mode). Nodes, tunnels and choruses are rebuilt by nodes checking in. Ponds are not: the operator creates them. Back it up with SQLite's backup, not a bare copy.", "broker · Part VII"],
      ]),''',
  '''      H1("Tunnels & membership"),
      refTable([
        ["frognet-tunnel-daemon-v3", "The tunnel daemon. Its first line names the mode: lan-only (no WireGuard) or region (the RAM manager session and one link to the hub). In a region it makes its own WireGuard key at its first start.", "service · Part VII"],
        ["/opt/fdf/var/join.env", "The region switch: RINA_RAM, the RAM manager's address. Written by fdf-region-join, removed by fdf-leave. Absent: the node is LAN-only.", "config · FD&F"],
        ["/opt/fdf/var/site.env", "The region's site key and area (RINA_SITE_KEY, RINA_AREA). Root-only.", "config · FD&F"],
        ["/var/lib/frognet-tunnel/node_private.key", "The daemon's WireGuard key. Made by the daemon; kept by --preserve, by --keep-identity and by fdf-leave.", "state · Appendix I"],
        ["/etc/frognet/wg.impl", "kernel or wireguard-go, chosen once per machine at install and read by everything that makes a WireGuard interface.", "config · Part IV"],
        ["/etc/frognet/tunnel.conf", "Not needed in either mode. Old broker keys in it are ignored (the daemon names them) and the upgrade (C1c) removes them.", "config · Part VII"],
      ]),
      P([ t("The broker, frognet-tunnel-setup-v3.sh, frognet-pond-bootstrap.sh, frognet-pond and broker.db were removed from the tree in October 2026.") ]),''')
S('''["frognet_monitor", "The terminal Network Info Center: every node the mesh can see, with live per-peer telemetry — echo, round-trip time, cache-hit rate, bytes saved — and drill-down into a node's sensors. The first stop when a node misbehaves.", "CLI · Part VI"],''',
  '''["frognet_monitor [--once]", "The terminal monitor: this node's snapshot (members, ponds, hub link, elections), an echo probe to each member, and drill-down into a node's sensors. --once prints one frame as text. The first stop when a node misbehaves.", "CLI · Part VI"],
        ["getSnapshot.php", "The node snapshot on :8080: build, mode, ponds, members, hub link, elections, errors. The monitor and the dashboard draw it and nothing else.", "HTTP · Part VI"],
        ["fln_dashboard32.html", "The dashboard, drawn from getSnapshot.php; a failed read shows the failure, not an old picture.", "web · Part VI"],''')
S('''["frognet_setup_v4_api.php", "The setup web page's backend: set the node's identity, join Wi-Fi, set or clear the broker (host, port, pond, password, choruses), make a QR code of an address''',
  '''["frognet_setup_v4_api.php", "The setup web page's backend: set the node's identity, join Wi-Fi, make a QR code of an address''')
S('''— compute, memory, storage, media-encode, database, identity, plus load and temperature.''',
  '''— compute, memory, storage, database, identity, plus load and temperature.''')
S('''        ["fdf-region-join SITE_KEY [RAM:PORT] [AREA]", "Joins this FrogNet node to the region (site.env, join.env, tunnel daemon in region mode). Stops the broker-mode timers.", "root CLI · FD&F"],
        ["fdf-leave", "Leaves the region and turns the broker-mode units back on.", "root CLI · FD&F"],''',
  '''        ["fdf-region-create AREA PUBLIC_IP [--ram-port 51888] [--hub-port 50889] [--ufw]", "On the region host: makes the site key, starts the RAM manager fdf-ram-AREA and the hub fdf-hub, writes hub.env, and prints the firewall lines, the site key and the join command. One region per machine.", "root CLI · FD&F"],
        ["fdf-install-<arch>.run KEY RAM_HOST:PORT AREA", "The node's FD&F installer, from opt/fdf/dist in the checkout; frognet_install.sh --region runs it. Unpacks /opt/fdf, serves /fdf/, runs fdf-region-join. Re-runnable; a newer build updates the node.", "root CLI · FD&F"],
        ["fdf-region-join SITE_KEY RAM_HOST:PORT AREA", "Joins this FrogNet node to the region (site.env, join.env, fdf-client, fdf-facts, tunnel daemon in region mode); all three arguments required. Stops and disables frognet-dbhost-elect.timer: in a region the daemon elects.", "root CLI · FD&F"],
        ["fdf-leave", "Leaves the region: removes the fdf units, join.env, site.env and the pond files, turns the database-host timer back on, and restarts the tunnel daemon LAN-only. Keeps the daemon's key.", "root CLI · FD&F"],''')
S('''["fdf-comms.sh", "Runs the Communicator's servers (comms-ram :8800, comms-media :8994) on this node and says where they are in the pond.", "root CLI · FD&F"],''',
  '''["fdf-comms.sh", "Runs the Communicator's servers (comms-ram :8800, comms-media :8994) on this region node, in the foreground, and says where they are in the pond. A node not in a region stops.", "root CLI · FD&F"],''')
S('''P([ t("Off a region, the two files you edit are "), mono("tunnel.conf"), t(" and "), mono("ssid_projection.conf"), t(", and both have friendlier front ends in the helper and the setup page. On a region, "), mono("fdf-region-join"), t(" writes what it needs.''',
  '''P([ t("The one file you edit is "), mono("ssid_projection.conf"), t(", and it has friendlier front ends in the helper and the setup page. The region switch is "), mono("join.env"), t(": "), mono("fdf-region-join"), t(" writes it and "), mono("fdf-leave"), t(" removes it.''')
S('''  "s-broker-tunnels-membership": "broker-tunnels-membership",''',
  '''  "s-broker-tunnels-membership": "tunnels-membership",''')
S('''  "how-the-broker-uses-it": "how-the-region-uses-it",''',
  '''  "how-the-broker-uses-it": "how-the-region-uses-it",
  "broker-tunnels-membership": "tunnels-membership",''')

# ---------------- Appendix J
S('''A build you can actually perform, end to end: two ordinary machines and a broker, from nothing installed''',
  '''A build you can actually perform, end to end: two ordinary machines and a region host, from nothing installed''')
S('''Three machines, and none of them special. Two become FrogNet nodes; one becomes the broker. Any 64-bit Debian-family Linux will do for the nodes — a spare laptop, a mini PC, a Raspberry Pi 4 or better. The broker wants a public address if the two nodes are in different buildings, which in practice means the cheapest VPS you can rent. If both nodes are on the same wire you do not need the broker at all until step four,''',
  '''Three machines, and none of them special. Two become FrogNet nodes; one holds the region. A node is a Debian-family Linux machine with two network interfaces: the uplink, and a wired interface it serves its LAN on. What was tested after the October 2026 cleanup is Ubuntu 24.04 on x86-64; a Raspberry Pi (aarch64, Raspberry Pi OS) ran FrogNet in the field before that cleanup and has not been re-qualified since. The region host is any Linux machine the nodes can reach. It wants a public address if the two nodes are in different buildings, which in practice means the cheapest VPS you can rent. If both nodes are on the same wire you do not need the region at all until step four,''')
S('''P([ b("Virtual machines are expected to work; this has not been confirmed. Containers cannot be supported."), t(" FrogNet restricts itself''',
  '''P([ b("Virtual machines are expected to work; this has not been confirmed."), t(" The gates of the October 2026 cleanup ran each node as an Ubuntu 24.04 container with systemd on one host, through a harness made for it ("), mono("docs/cleanup-2026-10/t4a-tools"), t(": fixed interface names, ordinary resolv.conf and hosts files, LANs on internal networks). That is a test bed, and the harness is not shipped. FrogNet restricts itself''')
S('''      P([ t("A FrogNet release is two files: the world tarball and the installer. Put them on the first machine and run it. The address you pick is the node's identity, and it ends in .1 because .1 is always FrogNetHost in any subnet.") ]),
      ...codeBlock([
        "sudo ./frognet_install.sh --name HomeBase --ip 10.80.80.1 --country US",
        "",
        "# no wireless you want it to project?",
        "#   add  --no-ssid-projection",
      ]),
      P([ t("It asks for the database password (the same on every node in the pond), whether to project an access point and its passphrase (unless --no-ssid-projection or --wifi-psk is given), and whether this node is a gateway. Answer no for now; Step 4 adds the broker.") ]),
      P([ t("What you should see. The installer unpacks the world, mints /etc/fnid — the node's permanent identity, made once and never regenerated — and brings the services up. When it finishes you have a complete network on one box: its own DNS, its own DHCP, its own web stack, its own address. Not a client waiting for a server. Confirm it:") ]),
      ...codeBlock([
        "frognet_monitor",
        "",
        "# one node, itself, healthy. that is a network.",
      ]),
      P([ t("If it does not. The installer's phases are listed earlier in the book and they fail loudly; the first phase wants the internet, and so does the venv rebuild in phase C when the release was built on another architecture. A box that installs and then shows nothing in the monitor: check that the services came up (the installer's last phase names any that did not).") ]),''',
  '''      P([ t("On the first machine, as a user with sudo, clone the repository and install from the checkout's root. The address you pick is the node's identity, and it ends in .1 because .1 is always FrogNetHost in any subnet.") ]),
      ...codeBlock([
        "sudo apt-get install -y git",
        "git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git",
        "cd FrogNet-Living-Network",
        "sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name HomeBase --ip 10.80.80.1 --no-ssid-projection",
        "",
        "# a Wi-Fi radio you want it to project?",
        "#   leave out --no-ssid-projection, and give --wifi-psk or answer the prompt",
      ]),
      P([ t("It asks for the database password, twice (the same on every node in the pond), and the Wi-Fi passphrase when projection is on. Then it runs to the end, checks 26 things, prints "), mono("+"), t(" or "), mono("x"), t(" for each and "), mono("All checks passed."), t(", and reboots. On the test machines that took 5 to 10 minutes, most of it apt and the Python packages.") ]),
      P([ t("What you should see. The installer lays the node down, mints /etc/fnid — the node's permanent identity, made once and never regenerated — and reboots into the services. Then you have a complete network on one box: its own DNS, its own DHCP, its own web stack, its own address. Not a client waiting for a server. Confirm it:") ]),
      ...codeBlock([
        "journalctl -b -u frognet-tunnel-daemon-v3 --no-pager | grep -E 'Starting frognet-tunnel-daemon|LAN-only'",
        "/usr/local/bin/frognet_monitor --once",
        "systemctl --failed",
        "",
        "# mode lan-only; one member, itself; no failed unit. that is a network.",
      ]),
      P([ t("If it does not. The installer fails loudly and names its phase; its whole log is "), mono("/var/log/frognet_install.log"), t(". It stops before installing anything when the only wired interface is the uplink: give the served one with "), mono("--lan-iface"), t(". After the reboot, "), mono("systemctl --failed"), t(" names any unit that did not come up.") ]),''')
S('''      H1("Step 2 — The second node, and no broker anywhere"),
      P([ t("Install the second machine the same way, on its own /24, and put it on the same physical wire as the first — same switch, same cable run, or joined to the first node's projected access point.") ]),
      ...codeBlock([
        "sudo ./frognet_install.sh --name Workshop --ip 10.80.90.1 --country US",
      ]),
      P([ t("What you should see. Within a minute or so, each node in the other's monitor — with a measured round-trip time and a live echo. Nothing was configured to make that happen. No broker exists yet. No tunnel exists yet. You did not tell either machine about the other.") ]),
      ...codeBlock([
        "frognet_monitor          # on HomeBase: Workshop is there, with RTT",
        "ping 10.80.90.1          # and it answers",
      ]),''',
  '''      H1("Step 2 — The second node, and no region yet"),
      P([ t("Install the second machine the same way, with its own 10.X.Y.1. Put its uplink on the first node's LAN (it takes a lease from the first node, which is then its default route) and serve its own LAN on another wired interface ("), mono("--lan-iface"), t(" when there is more than one candidate).") ]),
      ...codeBlock([
        "sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name Workshop --ip 10.80.90.1 --no-ssid-projection",
      ]),
      P([ t("What you should see. After both have rebooted, each names the other (in "), mono("getHosts.php"), t(", in "), mono("/etc/hosts"), t(" and among the snapshot's members), routes to the other's /24, and both name the same database host. Nothing was configured to make that happen. No region exists yet. No tunnel exists. You did not tell either machine about the other.") ]),
      ...codeBlock([
        "/usr/local/bin/frognet_monitor --once   # on HomeBase: Workshop is a member",
        "ping 10.80.90.1                          # and it answers",
      ]),''')
S('''Everything from here — the broker, the tunnel, the call — is about extending that''',
  '''Everything from here — the region, the tunnel, the call — is about extending that''')
S('''      H1("Step 3 — The broker"),
      P([ t("On the third machine, from the extracted broker bundle:") ]),
      ...codeBlock([
        "sudo ./install_frognet_broker.sh            # --port 18257 is the default",
      ]),
      P([ t("It does not ask questions; everything is a flag. What you should see is the postflight:") ]),
      ...codeBlock([
        "service       active, <pid> pid",
        "listening     0.0.0.0:18257",
        "api           GET /api/v4/ponds -> 200",
        "data          0 pond(s) visible; database preserved at",
        "              /var/lib/frognet_broker_v4/broker.db",
        "installed: frognet_broker_<stamp>",
      ]),
      P([ t("Then make the pond the nodes will join:") ]),
      ...codeBlock([
        "frognet-pond create hometown [--password PW]   # on the broker",
        "frognet-pond list                              # hometown, with its entire_pond chorus",
      ]),
      P([ t("The broker URL you carry to the nodes is "), mono("http://<broker host>:18257"), t(".") ]),
      H1("Step 4 — A node across the internet"),
      P([ t("On each node (it needs a real non-10 uplink: a WAN lease, or Wi-Fi client upstream):") ]),
      ...codeBlock([
        "sudo frognet-make-gateway.sh --host your.broker.example \\\\",
        "     --port 18257 --pond hometown [--password PW]",
      ]),
      P([ t("It writes "), mono("BROKER_URL"), t(" and "), mono("POND_NAME"), t(" into "), mono("/etc/frognet/tunnel.conf"), t(", checks "), mono("/etc/fnid"), t(", and registers ("), mono("POST /api/v4/register"), t("). A node whose uplink is not up yet stays LAN-only; enrolment is retried from "), mono("runMerge"), t(" on the next network event.") ]),
      ...codeBlock([
        "frognet-pond nodes hometown   # on the broker: both, registered",
        "frognet_monitor               # on either node: the other one, with RTT",
        "ping 10.80.90.1               # from HomeBase, across the internet",
      ]),
      P([ t("If it does not work: check "), mono("frognet-pond nodes"), t(" first. A node missing there did not register; its tunnel-setup log names why.") ]),''',
  '''      H1("Step 3 — The region"),
      P([ t("The third machine holds the region: its RAM manager, which holds the membership, and its hub, "), mono("fdf-hub"), t(", which relays WireGuard between members at different sites. It is not a FrogNet node. It needs "), mono("python3"), t(", "), mono("wireguard-tools"), t(", "), mono("wireguard-go"), t(" where its kernel has no WireGuard module, and "), mono("/opt/fdf"), t(" from "), mono("opt/fdf/dist/fdf-droplet.tgz"), t(" in the checkout. Copy that file to the region host's "), mono("/tmp"), t(", then, as root on the region host:") ]),
      ...codeBlock([
        "apt-get install -y python3 wireguard-tools",
        "cd / && tar xvzf - < /tmp/fdf-droplet.tgz",
        "systemctl daemon-reload",
        "ip -o addr | grep '10\\\\.253\\\\.'",
        "ip route | grep '10\\\\.253\\\\.'",
        "/opt/fdf/bin/fdf-region-create hometown 203.0.113.7",
      ]),
      P([ t("The two "), mono("grep"), t("s must print nothing: the hub uses 10.253.0.0/16 on its interface. "), mono("hometown"), t(" is the region's name (letters, digits, "), mono("."), t(", "), mono("_"), t(", "), mono("-"), t(", at most 12); "), mono("203.0.113.7"), t(" is the address the nodes reach this machine at, a public address for nodes in different buildings or a LAN address for a region with no internet. "), mono("fdf-region-create"), t(" records the WireGuard implementation, makes the site key (stored root-only in "), mono("/opt/fdf/var/site-key-hometown"), t("), starts the RAM manager "), mono("fdf-ram-hometown"), t(", maps the site key on it, writes "), mono("/opt/fdf/hub/hub.env"), t(", starts "), mono("fdf-hub"), t(" and "), mono("fdf-client-hometown"), t(", and prints what each said. Then it prints the firewall lines and the site key, once:") ]),
      ...codeBlock([
        "ufw allow 51888/tcp",
        "ufw allow 50889/udp",
        "ufw route allow in on wg-hometown out on wg-hometown",
      ]),
      P([ t("Run them (or run "), mono("fdf-region-create"), t(" with "), mono("--ufw"), t(") where the region host has ufw; otherwise open TCP 51888 and UDP 50889 to the nodes and let the host forward between the hub's peers on "), mono("wg-hometown"), t(". Keep the site key: the nodes need it. The RAM manager accepts "), mono("map_site"), t(" from anyone who reaches its port, so the firewall is what keeps others out. A second "), mono("fdf-region-create"), t(" on the same machine stops: a machine holds one region.") ]),
      P([ t("If it does not. "), mono("fdf-region-create"), t(" stops at the step that failed and names the journal to read ("), mono("journalctl -u fdf-ram-hometown"), t(", "), mono("journalctl -u fdf-hub"), t(").") ]),
      H1("Step 4 — A node across the internet"),
      P([ t("Take Workshop to another building, on any uplink that reaches the region host. Each node joins with the three values "), mono("fdf-region-create"), t(" printed. These two are installed LAN-only already, so each runs its FD&F installer from the checkout's root:") ]),
      ...codeBlock([
        "sudo bash opt/fdf/dist/fdf-install-x86_64.run KEY 203.0.113.7:51888 hometown",
      ]),
      P([ t("("), mono("fdf-install-aarch64.run"), t(" on a Raspberry Pi.) A new node joins at install instead, with "), mono("--region 203.0.113.7:51888 --site-key KEY --area hometown"), t(" added to the Step 1 command; the installer's last phase runs the same file. The file unpacks "), mono("/opt/fdf"), t(", serves the region view at "), mono("/fdf/"), t(", and runs "), mono("fdf-region-join"), t(", which writes "), mono("site.env"), t(" and "), mono("join.env"), t(", starts "), mono("fdf-client"), t(" and "), mono("fdf-facts"), t(", restarts the tunnel daemon into region mode and shows the result for 15 s. The daemon makes its own WireGuard key at that first region start ("), mono("WG_KEY: made /var/lib/frognet-tunnel/node_private.key"), t("); nothing else makes one.") ]),
      ...codeBlock([
        "journalctl -u frognet-tunnel-daemon-v3 --no-pager | grep 'Starting frognet-tunnel-daemon'",
        "                              # ... (mode region) ...",
        "curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool",
        "                              # \\"mode\\": \\"region\\", both members, the hub link",
        "/opt/fdf/bin/fdf-ls           # on the region host: both nodes",
        "ping 10.80.90.1               # from HomeBase, through the hub",
      ]),
      P([ t("No address changed. Each node keeps its own 10.X.Y.1 and its LAN; what it gained is a session to the RAM manager and one WireGuard link to the hub.") ]),
      P([ t("If it does not work. "), mono("REFUSED by the region: ... EPERM"), t(" in "), mono("journalctl -u fdf-client"), t(" means the site key is wrong: run the "), mono(".run"), t(" again with the key "), mono("fdf-region-create"), t(" printed. A node that stays LAN-only logs "), mono("LAN-only mode: no region switch (...)"), t(": "), mono("/opt/fdf/var/join.env"), t(" is missing; run the "), mono(".run"), t(" again. Two nodes with the same /24 are refused by the hub ("), mono("REFUSED 10.X.Y.0/24 claimed by ..."), t(" in "), mono("journalctl -u fdf-hub"), t("). To leave the region, "), mono("/opt/fdf/bin/fdf-leave"), t(" on the node.") ]),''')
S('''      P([ t("On an FD&F pond node, "), mono("/opt/fdf/bin/fdf-comms.sh"), t(" runs the same two servers and writes the pond's where cell, so the phone's Communicator finds them with nothing typed.") ]),''',
  '''      P([ t("On a node in the region (Step 4), "), mono("sudo /opt/fdf/bin/fdf-comms.sh"), t(" runs the same two servers in the foreground (Ctrl-C stops both) and writes the pond's where cell, so the phone's Communicator finds them with nothing typed. On Linux, "), mono("comms-app"), t(" is given that node's addresses by hand: "), mono("build/comms-app --ram 10.80.80.1:8800 --media 10.80.80.1:8994 --name Gorp"), t(". The October 2026 cleanup's gates did not cover "), mono("fdf-comms.sh"), t(" on a node.") ]),''')
S('''A broker extended it across the internet, and no address changed.''',
  '''A region extended it across the internet, and no address changed.''')
S('''  "s-step-2-the-second-node-and-no-broker-anywhere": "step-2-the-second-node-and-no-broker-anywhere",
  "s-step-3-the-broker": "step-3-the-broker",''',
  '''  "s-step-2-the-second-node-and-no-broker-anywhere": "step-2-the-second-node-and-no-region-yet",
  "s-step-3-the-broker": "step-3-the-region",''')
S('''  "broker-tunnels-membership": "tunnels-membership",''',
  '''  "broker-tunnels-membership": "tunnels-membership",
  "step-2-the-second-node-and-no-broker-anywhere": "step-2-the-second-node-and-no-region-yet",
  "step-3-the-broker": "step-3-the-region",''')

# ---------------- Appendix K (rewrite: the broker's ceiling is history; the region's limits are what the code and gates show)
def BLOCK(start, end, new):
    a = _pl.s.count(start); b_ = _pl.s.count(end)
    if a != 1 or b_ != 1: raise SystemExit('BLOCK markers not unique: %d %d %r %r' % (a, b_, start[:60], end[:60]))
    i = _pl.s.index(start); j = _pl.s.index(end, i); j = _pl.s.index('\n', j)
    _pl.s = _pl.s[:i] + U(new) + _pl.s[j:]
BLOCK('''      // [RESTORE_APPK_LEAD_V1] restored from the published edition''',
      '''Fifty-eight nodes, and sixteen on a shared broker. It should be fifty-nine thousand.''',
'''      // [RESTORE_APPK_LEAD_V1] restored from the published edition; [R_APPK_REGION_V1] rewritten 2026-10-13: the broker's ceiling went with the broker
      lead([ t("A system that does not tell you where its ceiling is has arranged for you to find it at the worst possible moment. So here is what FrogNet's code and its tests say, plainly: "), b("no member limit is written in the code, and the largest region tested has three members."), t(" The fifty-eight-node ceiling earlier editions stated was the broker's, and it went with the broker in October 2026. The rest of this appendix lists what the code does limit, what has been shown, where the old number came from, and what is open.") ]),
      H1("What the code limits"),
      bullet([ b("Addresses."), t(" A node is 10.X.Y.1 and serves 10.X.Y.0/24; every node needs its own X.Y.") ]),
      bullet([ b("One owner per /24."), t(" In a region a /24 belongs to the first live member to claim it; a later claim is refused, and "), mono("fdf-hub"), t(" logs "), mono("REFUSED <subnet> claimed by ..."), t(".") ]),
      bullet([ b("Reserved ranges."), t(" 10.253.0.0/16 and 10.254.0.0/16 are not node /24s in a region: the hub's edges (10.253.A.B for the member at 10.A.B.0/24) and its own address (10.253.253.254) are in 10.253. Leaves, phones and PCs, get 10.252.x.y addresses from the RAM manager.") ]),
      bullet([ b("Region names."), t(" At most 12 characters, because "), mono("wg-<AREA>"), t(" is an interface name. One machine holds one region.") ]),
      H1("What has been shown"),
      P([ t("In the gates of the October 2026 cleanup the largest region had three members, two nodes and the hub's own, and the largest LAN-only pond had two nodes. On hardware before the cleanup, the region carried five FrogNet nodes through builds r5\\u2013r13 (Chapter 67). The join cost per member on the current build has not been measured.") ]),
      P([ t("What grows with the members is linear. The hub holds one WireGuard interface for the region and one peer per live member; each member holds one edge address and one link per hub. Nothing is allocated per pair of members.") ]),
      H1("Where fifty-eight came from"),
      P([ t("Until October 2026 a FrogNet pond across the internet was a full mesh of tunnels the broker built, and every tunnel took a carrier block of addresses from a reserved range. Tunnels in a full mesh grow as the square of the nodes, so the broker's allocator capped a pond at about fifty-eight nodes, and at sixteen where one broker carried a second pond. Earlier editions said the fix was not a bigger range but allocating per node instead of per tunnel. The region is that: one interface, one peer and one edge address per member.") ]),
      P([ t("The question those editions left open, whether each peer's permitted addresses narrow everywhere or only at the far end, the region answers at the far end. The hub gives each member peer only the prefixes that member's verified mappings name; each node still sends all of "), mono("10.0.0.0/8"), t(" down its one link, so the route reasoning on the node stays unconditional.") ]),

      H1("What is not a FrogNet limit"),
      P([ t("Several numbers in this area look like ceilings and are not. Each belongs to somebody, and it is not the software.") ]),
      dataTable(
        ["Number", "Whose it is", "Note"],
        [
          ["Members in a region", "nobody yet", "no limit is written in the code; the gates went to three members"],
          ["Node addressing", "the network administrator", "each node's own 10.X.Y.1, chosen at install"],
          ["Open files per process", "the operator", "the shipped units do not set it yet; set LimitNOFILE=infinity in them"],
          ["WireGuard links a node holds", "the mode", "none LAN-only; in a region, one per live hub"],
          ["Throughput", "the hardware", "measured, not derived. The capability probe already reports what each machine can do"],
          ["Discovery depth", "the software", "LAN-only: two levels per pass, from every node already routed; depth comes from re-merging, so a chain of any length is walked to its end"],
        ],
        [2500, 2100, 4640]
      ),
      P([ t("The per-node maximum tunnel count earlier editions warned about ("), mono("MAX_TUNNELS"), t(" and its leftover defaults) went with the broker; the upgrade removes it from "), mono("tunnel.conf"), t(".") ]),

      // [RESTORE_APPK_SUM_V1] restored from the published edition
      H1("The honest summary"),
      P([ t("No member limit is written in the code. What has been shown is small: three members in a region and two LAN-only nodes in the gates, five nodes in a region on hardware before the cleanup. The fifty-eight-node ceiling was the broker's allocator, and it is gone with the broker.") ]),
      P([ t("The guidance is short. A region of a dozen boxes has not been measured, and neither has anything larger. Plan it, measure it, and say what you saw.") ]),
      // [RESTORE_APPK_GUILD_V1] restored from the published edition
      H1("If this is the kind of problem you like"),
      P([ t("Then the guild could use you.") ]),
      P([ t("This appendix describes a problem that is open, not one that is closed. Somebody has to find the region's real ceiling: the join cost per member on the current build, the hub's cost per peer, the RAM manager's cost per session, and what a region of tens or hundreds of members does when one leaves. None of that has been measured, so none of it is written down.") ]),
      P([ t("It is also a problem with an unusually good shape for someone arriving new. It is bounded \\u2014 one RAM manager, one hub, one daemon per node. It is measurable \\u2014 the network-namespace tests in rinab and the container harness in "), mono("docs/cleanup-2026-10/t4a-tools"), t(" stand regions up, a topology that fails is an oracle, and Part XIII says a change is not done until you have written one. And it is numbers before it is opinion, which means a proposal can be argued on its merits rather than on who made it.") ]),
      ...gld([ t("The old ceiling was fifty-eight nodes, and it went with the broker. The new one has not been found: nothing in the code sets it, and no gate has gone past three members. That is the most legible open problem in the system. Routing people, and anyone who has done distributed coordination without an authority: this is the seam. Note the second half of that, because it is the unusual part. Most people who have worked on distributed systems have done it where something coordinates \\u2014 a leader, a quorum, a controller. Here every node reaches its own determination from shared state, and the determinations agree because they read the same facts rather than because anyone was told.") ]),''')
S('''  "s-what-the-replacement-changes": "what-the-replacement-changes",''',
  '''  "s-what-the-replacement-changes": "where-fifty-eight-came-from",''')
S('''  "s-where-fifty-six-comes-from": "where-fifty-eight-comes-from",''',
  '''  "s-where-fifty-six-comes-from": "where-fifty-eight-came-from",''')
S('''  "s-why-a-bigger-number-is-not-the-fix": "why-a-bigger-number-is-not-the-fix",''',
  '''  "s-why-a-bigger-number-is-not-the-fix": "where-fifty-eight-came-from",''')
S('''  "step-3-the-broker": "step-3-the-region",''',
  '''  "step-3-the-broker": "step-3-the-region",
  "where-fifty-eight-comes-from": "where-fifty-eight-came-from",
  "why-a-bigger-number-is-not-the-fix": "where-fifty-eight-came-from",
  "what-the-replacement-changes": "where-fifty-eight-came-from",''')

# ---------------- Appendix E
S('''This is broker mode, where every node walks and merges. On an FD&F region the walk does not run; the region's records and plan replace it.''',
  '''The wg links between cells are the broker-era fabric: the simulator still models them, but no node makes such a link now. A LAN-only node walks and merges over its LANs and direct cables, with no WireGuard; on an FD&F region the walk does not run, and the region's records and plan replace it.''')

# ---------------- Appendix M (glossary)
S('''it reads the FrogNet-hosted Ribbit Region through the same API calls any application can use and paints what it finds.''',
  '''it reads the node's snapshot (getSnapshot.php: members, ponds, hub link, elections) and the sensors through the same API any application can use, and paints what it finds; --once prints one frame.''')
S('''gloss("broker", "The rendezvous that introduces nodes which cannot find each other and, across the internet, hands out WireGuard tunnels. It terminates them and forwards between them, so it carries the inter-site data path and sees that traffic in the clear. Reachable by IP from every node. It holds the pond's registry: each node's install GUID (its matching key), public key and subnet, the tunnels and their address and port allocations, and any pond password hash. It holds no node's private key; the private keys of its own tunnel ends are in broker.db. It runs none of the semantic stack, and is not a member of any FrogNet. The optional console is a PHP page in the broker host's web root, with frognet-pond as its command-line equivalent. It is separate from the broker service and is not required for enrollment, rendezvous, or tunnels."),''',
  '''gloss("broker", "History. Until October 2026, the rendezvous that enrolled FrogNet nodes across the internet and built a WireGuard tunnel between each pair, with its registry in broker.db and frognet-pond as its command line. It was removed from the tree. A node's internet path is now a region: the RAM manager for membership and fdf-hub for the WireGuard relay (Specification C)."),''')
S('''cores, memory, encoders, storage — as a JSON blob. Off a region, each role's advertiser publishes it as its <role>/capability tuple;''',
  '''cores, memory, storage, database — as a JSON blob. Off a region, each merge publishes it through the role handler as its <role>/capability tuple;''')
S('''gloss("chorus", "A named subgroup of nodes within a pond."),''',
  '''gloss("chorus", "The book's name for a subgroup of nodes within a pond. The broker's choruses went with the broker; 10.254.0.0/16, their old address range, is still kept out of node /24s."),''')
S('''the databasehost picks the most-capable node running a database, the mediahost role picks by locality and capability (its service runs an fnav.py that is not in the tree; the Communicator’s calls go through comms-media, which is not elected).''',
  '''the databasehost, the one elected role, picks the most-capable node running a database. Off a region the merge elects; in a region the tunnel daemon elects from the members' facts. The Communicator's media server is not elected.''')
S('''gloss("GROUP_TOKEN", "A v2-era tunnel.conf field; unused by the v4 broker."),''',
  '''gloss("GROUP_TOKEN", "An old tunnel.conf field from the broker era; the upgrade (C1c) removes it."),''')
S('''It runs where it is started; on an FD&F pond node, fdf-comms.sh starts it and says where it is in the pond's where cell. (FrogNet's elected mediahost role, LAN-only and gated on ffmpeg+libvpx, still exists in core/sotf_handler.py; the service it starts points at a file no longer in the tree.)"),''',
  '''It runs where it is started; on a region node, fdf-comms.sh starts it and says where it is in the pond's where cell. It is not elected: FrogNet's mediahost role was removed in October 2026."),''')
S('''gloss("pond", "Under FrogNet, the nodes that register together at a broker. Under FD&F, the members of one pond in a region, held in the region's memory."),''',
  '''gloss("pond", "The nodes that form one FrogNet: LAN-only, the nodes on connected LANs, which share one database password; in a region, the members of one pond, held in the region's memory."),''')
S('''and chorus and pond are broker groupings."),''',
  '''and a pond is the region's grouping of members."),''')
S('''gloss("hub", "fdf-hub, the region's WireGuard concentrator on the droplet (StreamingFrog, 165.232.153.144): one wg interface for the region, one peer per live member. The RAM manager on the same droplet is TCP 51888."),''',
  '''gloss("hub", "fdf-hub, the region's WireGuard concentrator on the region host: one interface, wg-<AREA>, on UDP 50889, and one peer per live member. The RAM manager on the same host is TCP 51888. fdf-region-create starts both."),
      gloss("region host", "The machine that holds a region: its RAM manager (fdf-ram-<AREA>) and its hub. Any Linux machine the members can reach, with /opt/fdf from fdf-droplet.tgz; it is not a FrogNet node. One machine holds one region."),
      gloss("LAN-only / region mode", "A FrogNet node's two modes, named in the tunnel daemon's first log line. LAN-only: discovery, the election and routes between connected LANs, no WireGuard. Region: also a member of an FD&F region. The switch is /opt/fdf/var/join.env, written by fdf-region-join and removed by fdf-leave."),''')

_pl.done()
