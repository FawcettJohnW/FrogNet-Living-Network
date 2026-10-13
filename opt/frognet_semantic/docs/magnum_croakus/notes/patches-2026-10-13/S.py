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
def SU(o, n): sub(U(o), U(n))   # old/new written with real characters; the source holds \uXXXX
# S: CROAKUS-CHECK-2026-10-13.md, entries 1-11

# 1. getHosts example: off a region every row is role "node", alive true (frognet_node.inc.php:136-145)
SU(r'''"  { \"ip\": \"10.80.80.1\", \"name\": \"HomeBase\", \"echo\": \"...\", \"role\": \"\", \"alive\": false },", "  { \"ip\": \"10.84.84.1\", \"name\": \"ShopBox\",  \"echo\": \"...\", \"role\": \"\", \"alive\": false },", "  { \"ip\": \"10.90.90.1\", \"name\": \"MomBox\",   \"echo\": \"...\", \"role\": \"\", \"alive\": false }"''',
    r'''"  { \"ip\": \"10.80.80.1\", \"name\": \"HomeBase\", \"echo\": \"...\", \"role\": \"node\", \"alive\": true },", "  { \"ip\": \"10.84.84.1\", \"name\": \"ShopBox\",  \"echo\": \"...\", \"role\": \"node\", \"alive\": true },", "  { \"ip\": \"10.90.90.1\", \"name\": \"MomBox\",   \"echo\": \"...\", \"role\": \"node\", \"alive\": true }"''')
SU(r'''P([ t("On a region node the list comes from the region (role and alive filled in; a phone or PC reads role "), mono("leaf"), t("), not from this node's walk.") ]),''',
    r'''P([ t("Off a region every host the last merge named is a node and alive. On a region node the list comes from the region (role and alive are the member's; a phone or PC reads role "), mono("leaf"), t("), not from this node's walk.") ]),''')

# 2. dnsmasq binds only 127.0.0.1 and the node's .1 (live.py:996-997, mergeHostsAndResolv.bash:230-231)
SU(r'''b("Node services bind all interfaces."), t(" The proxy on "), mono("80"), t(", Apache on "), mono("8080"), t(" and "), mono("8443"), t(", the data daemon on "), mono("9009"), t(", dnsmasq: what keeps the internet off them is where the node sits, not what they bind to.''',
    r'''t("The proxy on "), mono("80"), t(", Apache on "), mono("8080"), t(" and "), mono("8443"), t(" and the data daemon on "), mono("9009"), t(" "), b("bind all interfaces"), t("; dnsmasq binds only "), mono("127.0.0.1"), t(" and the node’s own .1 ("), mono("listen-address"), t(" and "), mono("bind-interfaces"), t(", written by the merge). What keeps the internet off the first three is where the node sits, not what they bind to.''')

# 3. listener list includes dnsmasq (installer enables it on every node, :2049)
SU(r'''its daemon on :9009) and, on a node that hosts the Communicator''',
    r'''its daemon on :9009, and dnsmasq's DNS and DHCP on its own .1) and, on a node that hosts the Communicator''')

# 4. daemon timers (poll.py:1355-1359 600 s capability probe; :1652 5 s rejoin)
SU(r'''Two timers remain: fdf-client checks its mappings every 30 s and re-registers any the region lacks, and fdf-facts probes the uplink every 3 s.''',
    r'''Three timers remain: fdf-client checks its mappings every 30 s and re-registers any the region lacks, fdf-facts probes the uplink every 3 s, and the tunnel daemon re-runs the capability probe every 600 s. While the region does not answer, the daemon asks again every 5 s.''')
SU(r'''Nothing polls; the tunnel daemon acts on region events, and when the region is gone''',
    r'''Nothing polls the region while it answers; the tunnel daemon acts on region events, and when the region is gone''')

# 5. glossary FULL / template: template is learned from the RAW first exchange (transport_semantic.py:2811, 3152)
SU(r'''gloss("FULL", "A BLDC-1 verdict: the whole structure, sent when no template fits yet — and learned in the act of sending it."),''',
    r'''gloss("FULL", "A BLDC-1 verdict: every value of an exchange whose template is known, sent when there is no reference yet to diff against."),''')
SU(r'''which parts are fixed, which vary, and their types — shared by both ends and learned from the first FULL."),''',
    r'''which parts are fixed, which vary, and their types — learned by each end from the first (RAW) exchange of its shape and never sent."),''')

# 6. chapter 38: same
SU(r'''learned once from that first FULL and never re-sent.''',
    r'''learned once from the first, RAW exchange of its shape and never re-sent.''')
SU(r'''an observer present from the "), i("first"), t(" FULL, who captures the whole conversation from its start,''',
    r'''an observer present from the "), i("first"), t(" exchange of a shape (the RAW frame, which carries the original HTTP), who captures the conversation from its start,''')

# 7. glossary channel set: allocator exists, nothing opens a set (channel_sets.py:180, no caller)
SU(r'''gloss("channel set", "An independent transport channel to a peer — its own socket, sequence space, and worker threads. Up to three per peer, so a call and a bulk transfer do not block each other."),''',
    r'''gloss("channel set", "An independent socket set to a peer, with its own sequence space and threads, as the proxy's allocator defines it: up to three per peer, so a call and a bulk transfer would not block each other. The allocator is unit-tested; nothing opens a set yet, and all traffic uses set 0 (Part IX)."),''')

# 8. echo identity can be empty (getEth0Address:55, frognet_install.sh:2158)
SU(r'''"identity   the node's .1 — always present",''',
    r'''"identity   the node's .1, the served LAN interface's own address; empty while",
        "           that interface has no address (before the first reboot after install, for one)",''')

# 9. Appendix H: three strikes over 15 s, marks expire after 60 s (not_frognet.py:262, 380, 402, 443-515, 536)
SU(r'''a host that refused is marked not-FrogNet and not re-probed in later passes of the same merge. The mark is flushed at the top of every merge. A timeout is never marked.''',
    r'''a host is marked not-FrogNet only after three consecutive definitive failures (refused, or no route) spread over at least 15 seconds. A mark expires after 60 seconds, and every merge flushes the marks at its start. A timeout is never counted.''')

# 10. a . in the area becomes - in the interface (fdf-hub:271, fdf-region-create:130)
SU(r'''because the hub’s interface is "), mono("wg-myarea"), t(". "), mono("203.0.113.7")''',
    r'''because the hub’s interface is "), mono("wg-myarea"), t(". A "), mono("."), t(" in the name becomes "), mono("-"), t(" in the interface: area "), mono("my.area"), t(" is served on "), mono("wg-my-area"), t(", and that is the name the ufw route line prints. "), mono("203.0.113.7")''')
SU(r'''one interface, wg-<AREA>, on UDP 50889, and one peer per live member.''',
    r'''one interface, wg-<AREA> (a . in the area becomes -), on UDP 50889, and one peer per live member.''')

# 11. phase B has no sysctls; C1a applies them (frognet_install.sh:733, 759, 781, 920)
SU(r'''t("Directories, kernel sysctls, the iptables scaffold, the sudoers entries the daemon needs — laid before the world.") ]),''',
    r'''t("The iptables scaffold, directories and the sudoers entries the daemon needs, laid before the world.") ]),''')
SU(r'''t(" (or copies a repository checkout), mints the node GUID,''',
    r'''t(" (or copies a repository checkout), applies the tree's kernel sysctls, mints the node GUID,''')

done()
