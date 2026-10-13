import sys; sys.path.insert(0,'/tmp/claude-0/verify1013'); from patchlib import *
import re, patchlib
def S(o, n):
    # o, n written with real characters; the source uses \uXXXX escapes (either hex case)
    pat = ''.join(('\\\\u(?i:%04x)' % ord(c)) if ord(c) > 127 else re.escape(c) for c in o)
    m = re.findall(pat, patchlib.s)
    if len(m) != 1: raise SystemExit('NO MATCH (%d): %r' % (len(m), o[:160]))
    patchlib.s = re.sub(pat, lambda _: U(n), patchlib.s)

# ---- Front matter: hometown names a region, not a broker
S(r'"  broker     streamingfrog.com:18257  (or a local one — Part VII)"]',
    r'"  region     hometown: its RAM manager and hub on a region host (Part VII)"]')

# ---- Part 0 0.5: hub interface is wg-<AREA>; fdf-region-join writes join.env, no built-in region
S(r'(fdf-hub, wg-frognet, UDP 50889)',
    r'(fdf-hub, interface wg-<AREA>, UDP 50889)')
S(r'The deployed region (r19-a15) gives each node the address outright: fdf-region-join writes RINA_RAM=165.232.153.144:51888.',
    r'A FrogNet node is given the address outright: fdf-region-join writes RINA_RAM=<host>:<port>, the address fdf-region-create printed, into /opt/fdf/var/join.env. There is no built-in region.')
S(r'With a directly given address, as the deployed region uses, there is no mapping to follow.',
    r'With a directly given address, as fdf-region-join gives it, there is no mapping to follow.')

# ---- Specification B: no broker behaviour
S(r'bearer, election, partition, healing, broker, security and operational behavior',
    r'bearer, election, partition, healing, region membership, security and operational behavior')

# ---- Part I: the node's listeners
S(r"(its web server on :80, its daemon on :9009)",
    r"(its proxy on :80, its web server on :8080 and :8443, its daemon on :9009)")

# ---- Part II: no media-host election
S(r'who should host the shared database, who should anchor a video call — the network measures',
    r'who should host the shared database — the network measures')

# ---- Part III
S(r'Before brokers and discovery and any of the clever software',
    r'Before regions and discovery and any of the clever software')
S(r'local coordinator or one across the internet — decide',
    r'a LAN on its own or a region whose host is across the internet — decide')
S(r'The two lillypads become one LAN. No broker, no tunnel, no internet.',
    r'The two lillypads become one LAN. No region, no tunnel, no internet.')
S(r'│ WireGuard tunnel (via broker)",',
    r'│ WireGuard (via the region hub)",')
S(r'the pond-wide chorus of all of them, or a named chorus an administrator defines — “family,” “ops.” And ',
    r'the pond-wide chorus of all of them. (Named choruses, “family” or “ops,” were assigned by the broker; the broker is gone, and the current code defines no named chorus.) And ')
S(r'No error, no hang — a split is just a walk that returned a smaller world. Plug it back in',
    r'No error, no hang — a split is just a walk that returned a smaller world. The walk runs with a merge, and a merge runs on an event. The box whose own cable went out sees its interface change and merges at once. The box whose wire stayed up merges on its next event, because in LAN-only mode a neighbour going away is not one; until then its hosts file still names the gone node. Plug it back in')

# ---- Part IV 8: install from a checkout; no broker flags; region flags
S(r'This chapter is about the first two; you will be glad of the third the first time a build goes wrong.',
    r'This chapter is about the first two; you will be glad of the third the first time a build goes wrong. A repository checkout installs the same way with "), mono("--from-repo"), t(", run from the checkout’s root: the installer copies the node file set from the checkout instead of unpacking a tarball. That is the path to a first node, because the release builder snapshots a node that is already running.')
S(r'''        "sudo bash frognet_install.sh \\",
        "     --name HomeBase \\",
        "     --ip   10.80.80.1 \\",
        "     --broker-host streamingfrog.com \\",
        "     --broker-port 18257 \\",
        "     --pond hometown",
      ]),''',
    r'''        "sudo bash usr/local/bin/installer/frognet_install.sh --from-repo \\",
        "     --name HomeBase \\",
        "     --ip   10.80.80.1 \\",
        "     --region <region host>:51888 --site-key <KEY> --area hometown",
      ]),''')
S(r'''      bullet([ b("--broker-host / --broker-port "), t("— where the coordinator lives, an address and port you supply. Leave them empty for a node with no broker; it is still a complete standalone network, and you can add one later from the setup page below.") ]),
      bullet([ b("--pond "), t("— the pond this node joins. The installer asks for it whenever a broker is set.") ]),''',
    r'''      bullet([ b("--region / --site-key / --area "), t("— the region’s RAM manager (host and port), its site key and its name: the three values "), mono("fdf-region-create"), t(" printed on the region host (Part VII). With them, once the install’s own checks have passed, the installer runs the FD&F node installer for this machine ("), mono("opt/fdf/dist/fdf-install-<arch>.run"), t(") and the node joins the region. Leave the last line out and the node is LAN-only: a complete standalone network. A LAN-only node joins later with that same "), mono(".run"), t(" ("), mono("KEY HOST:PORT AREA"), t(") and leaves with "), mono("/opt/fdf/bin/fdf-leave"), t(".") ]),
      bullet([ b("--lan-iface "), t("— the wired interface the node serves. Left out, it is the first wired interface that is not the uplink. The installer never serves the uplink, the interface the default route uses; if that is the only wired interface, it stops before it installs anything.") ]),''')

# ---- Part IV 9: the phases as the installer runs them now
S(r'''      P([ t("Six phases. Knowing them tells you where to look when something goes wrong.") ]),
      bullet([ b("A — Reset and packages. "), t("Wipes a prior install (unless "), mono("--preserve"), t("), then apt and pip dependencies. It refreshes the package index every run.") ]),''',
    r'''      P([ t("The installer works in phases, and each one prints its name as it starts ("), mono("STEP D1"), t(", "), mono("STEP A0"), t(", ...). Knowing them tells you where to look when something goes wrong. The whole log is "), mono("/var/log/frognet_install.log"), t(".") ]),
      bullet([ b("D1 — Interfaces, first. "), t("Finds the uplink and the wired interface to serve before anything is installed, so a wrong choice stops here.") ]),
      bullet([ b("A — Reset and packages. "), t("Wipes a prior install (unless "), mono("--preserve"), t("), records the machine as it was before FrogNet (so "), mono("--uninstall"), t(" can put it back), then the apt packages. It refreshes the package index every run. It chooses the WireGuard implementation once — "), mono("kernel"), t(" when the kernel has the module, else "), mono("wireguard-go"), t(", installed then — and records it in "), mono("/etc/frognet/wg.impl"), t("; a later install keeps the record.") ]),''')
S(r'''mints the node GUID, injects the DB secret, rebuilds the Python venv only if its architecture does not match the box, then runs the discovery self-check on the installed code and stops the install if it fails.") ]),''',
    r'''mints the node GUID, injects the DB secret, and removes what the tree no longer ships (retired units, scripts, pages and settings, on an upgrade). Then the Python venv the services run on: rebuilt when it is missing or its architecture does not match the box (or with "), mono("--rebuild-venv"), t("), and every listed package checked; a package that fails to build stops the install. Last, it runs the discovery self-check on the installed code and stops the install if it fails.") ]),''')
S(r'''t("Name and address, hostname, broker and WireGuard keys, SSL, iptables, SSID projection, the link watcher.") ]),
      bullet([ b("F — Finalize. "), t("Enable services, logging, cron, verify, system tuning, reboot.") ]),''',
    r'''t("Name and address, hostname, the lillypad setup, the node’s own SSL certificate, iptables, SSID projection, the link watcher. No WireGuard key is made here: the tunnel daemon makes its own the first time the node runs in a region.") ]),
      bullet([ b("F — Finalize. "), t("Enable services (on a LAN-only node the database-host elector’s timer too), logging, cron, then the verify: 26 checks, then the node’s own echo and snapshot, ending "), mono("All checks passed."), t(" or "), mono("N check(s) failed."), t(" Then system tuning.") ]),
      bullet([ b("R — Region. "), t("With "), mono("--region"), t(", and only when the verify passed, the FD&F node installer joins the region. Then the reboot.") ]),''')
S(r'''is the first stop: it shows every node the mesh can see and, per peer, the live numbers''',
    r'''is the first stop: it draws the node’s snapshot ("), mono("getSnapshot.php"), t(": build, mode, members, hub link, elections, errors) and, per member, the live numbers''')

# ---- Part IV 10: the setup page as it is
S(r'''through a small helper ("), mono("frognet_setup_v4_helper.bash"), t(") behind it.''',
    r'''through two small helpers ("), mono("frognet_setup_helper.bash"), t(" and "), mono("frognet_setup_v4_helper.bash"), t(") behind it.''')
S(r'''      bullet([ b("Set or clear the broker "), t("— write or remove the broker fields in "), mono("tunnel.conf"), t(" (host, port, pond, password, choruses) without editing a file, then read back the current broker state.") ]),
      bullet([ b("Set the node's name and address, "), t("and join it to a Wi-Fi network as a client. After you apply, the page shows a QR code of the node's new setup address, so you can scan your way back to it after the reboot. (Broker registration is a helper command, not a page button.)") ]),''',
    r'''      bullet([ b("Set the node's name and address, "), t("or let it pick a free one, and join it to a Wi-Fi network as a client, or leave one. When you save, the page shows a QR code of the node's new setup address, so you can scan your way back to it after the reboot.") ]),''')
S(r'''      P([ t("Everything the page does, the helper does on the command line too, so you can script any of it. The page is just the friendly face on the same machinery.") ]),''',
    r'''      P([ t("Everything the page does, the helpers do on the command line too, so you can script any of it. The page is just the friendly face on the same machinery. Region membership is not on the page: a node joins a region with the FD&F node installer and leaves with "), mono("fdf-leave"), t(" (Part VII).") ]),''')

# ---- Part IV 11: the configuration files now
S(r'''under "), mono("/etc/frognet/"), t(", plus the node's identity.''',
    r'''under "), mono("/etc/frognet/"), t(", plus the node's identity and, on a region node, two files under "), mono("/opt/fdf/var/"), t(".''')
S(r'''        "/etc/frognet/tunnel.conf             broker URL, pond, choruses,   via helper/page",
        "/etc/frognet/tunnel.conf             NODE_GUID, pond password      no — set at install / via helper",
''', '')
S(r'''        "/etc/frognet/gateways.conf           node name, its own .1 and .2  no — written by setup_lillypad",
''',
    r'''        "/etc/frognet/gateways.conf           node name, its own .1 and .2  no — written by setup_lillypad",
        "/etc/frognet/wg.impl                 kernel or wireguard-go        no — chosen once at install",
        "/opt/fdf/var/join.env                RINA_RAM: the region switch   no — fdf-region-join / fdf-leave",
        "/opt/fdf/var/site.env                region name and site key      no — fdf-region-join / fdf-leave",
''')
S(r'''      P([ t("The two you will actually touch are "), mono("tunnel.conf"), t(" and "), mono("ssid_projection.conf"), t(", and both have friendlier front ends — the helper and the setup page. The identity''',
    r'''      P([ t("The one you will actually touch is "), mono("ssid_projection.conf"), t(", and it has friendlier front ends — the helper and the setup page. "), mono("join.env"), t(" is the one file that makes a node a region member; "), mono("fdf-region-join"), t(" writes it and "), mono("site.env"), t(", and "), mono("fdf-leave"), t(" removes both. The tunnel daemon’s WireGuard key, "), mono("/var/lib/frognet-tunnel/node_private.key"), t(", is made by the daemon itself the first time it runs in a region. There is no "), mono("tunnel.conf"), t(" to keep: the tunnel daemon reads none, names in one log line any keys an old one left behind, and an upgrade removes them. The identity''')

# ---- Part V
S(r'A node knows itself, its LAN, and its broker. How does a pile of boxes',
    r'A node knows itself and its LAN. How does a pile of boxes')
S(r'cores and clock, installed memory, disk class, codec support — and the math does the rest.',
    r'cores and clock, installed memory, disk class, whether it runs MySQL — and the math does the rest.')
S(r'''      P([ t("And there is not "), i("one"), t(" election — there are as many as there are services. In the historical FrogNet implementation, host selection is a service-handler extension point: "), b("virtual functions"), t(" (you will meet the handler machinery in Part X): each service implements its own selector, so different services choose by different rules. The database host scores static capability — installed RAM first, then disk class and CPU — among boxes actually running MySQL, with the highest address only as a tiebreak (Part VI). The mediahost role selects by locality and capability among directly-attached neighbors. That role still exists in the code, but the service it starts runs an "), mono("fnav.py"), t(" that is not in the tree; the Communicator’s calls today go through its own media server, "), mono("comms-media"), t(" (Part XV), which is not elected. Every node runs a given service's selector and reaches the same answer "), i("for that service"), t(" — deterministic per service — but no two services are obliged to pick the same way.''',
    r'''      P([ t("And election is not one central routine. In the historical FrogNet implementation, host selection is a service-handler extension point: "), b("virtual functions"), t(" (you will meet the handler machinery in Part X): each service implements its own selector, so different services can choose by different rules. The role registry ("), mono("core/role_registry.py"), t(") holds one role today, the database host. It scores static capability — installed RAM first, then disk class and CPU — among boxes actually running MySQL, with the highest address only as a tiebreak (Part VI). An earlier build also elected a media host among directly attached neighbours; it was retired with the old Python Communicator, and the Communicator’s calls go through its own media server, "), mono("comms-media"), t(" (Part XV), which is not elected. Every node runs a service's selector and reaches the same answer "), i("for that service"), t(" — deterministic per service — and a second service would not be obliged to pick the same way.''')
S(r'''      P([ t("Election is scoped. A service the whole mesh shares is chosen across the pond. A service that must stay on the local wire — live media is the example that bites you if you get it wrong — is elected only among directly-attached neighbors, so it can never land on a node you reach only by relaying. Stability comes from''',
    r'''      P([ t("The database host is chosen across the pond: every live .1 the merge sees is a candidate. Stability comes from''')

# ---- Part VI
S(r'A FrogNet node runs in one of two modes. With a broker, which is what this Part describes, the walk finds the members, and capability is published to the control plane.',
    r'A FrogNet node runs in one of two modes. In LAN-only mode, which is what this Part describes, the walk finds the members, and each merge publishes capability to the control plane.')
S(r'''t(", the network's info center, is a terminal dashboard. It lists every node and shows''',
    r'''t(", the network's info center, is a terminal dashboard. It lists the members in the node’s snapshot ("), mono("getSnapshot.php"), t(") and shows''')
S(r'''t(" names only "), mono("127.0.0.1"), t(", the upstream (if any) and "), mono("8.8.8.8"), t(". A name from another pond''',
    r'''t(" names only "), mono("127.0.0.1"), t(" and the upstream, the default route’s next hop, if there is one. There is no public resolver behind it. A name from another pond''')
S(r'"              (broker mode only)"', r'"              (LAN-only mode only)"')
S(r'P([ t("This is broker mode. On a region nothing is published',
    r'P([ t("This is LAN-only mode. On a region nothing is published')
S(r'''      P([ t("If the election names no database host, the name pins to the control host. A lone box elects itself for every role.") ]),''',
    r'''      P([ t("If the election names no database host, the name pins to the control host. A lone box elects itself for every role.") ]),
      P([ t("In LAN-only mode a neighbour’s capability row can reach the control after the control’s merge has elected without it. For that, "), mono("frognet-dbhost-elect.timer"), t(" runs the same election on the control once a minute for ten minutes after each merge and, when the answer changes, rewrites "), mono("/etc/hosts"), t(" and reloads dnsmasq. On a region the elector stands down; the tunnel daemon elects from the members’ facts.") ]),''')
S(r'The mesh splits into two islands, the merge fires on each side, and each re-elects its own most-capable database node.") ]),',
    r'The mesh splits into two islands, the merge fires on each side, and each re-elects its own most-capable database node. In LAN-only mode the merge fires on an event: a node’s own interface changing, a neighbour’s notification, or a merge request. A neighbour that went away is not an event, so a node whose own wire stayed up keeps the old answer until its next one (a test with no event measured 5 min 15 s).") ]),')
S(r'It does this in broker mode only; on a region the publish returns at once.',
    r'It does this in LAN-only mode only; on a region the publish returns at once.')

done()
