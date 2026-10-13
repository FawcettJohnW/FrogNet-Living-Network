import sys; sys.path.insert(0,'/tmp/claude-0/verify1013'); from patchlib import *
# Q: Parts VII (19-22), VII-half, VIII, IX, X, XI, XII, XII-half, XIII, XIII-half -- the region is the internet path;
# the broker, choruses, mediahost and the monitor's getHosts/etc-hosts reads are history. Evidence: reports/Q.md.
import json, re
import patchlib

def between(a, z, incl_z=False):
    """Exact source from marker a up to (not incl.) marker z, both unique in the current file."""
    cur = patchlib.s
    if cur.count(a) != 1: raise SystemExit('START MARKER count %d: %r' % (cur.count(a), a[:120]))
    i = cur.index(a); j = cur.index(z, i)
    return cur[i:j + (len(z) if incl_z else 0)]

def q(x): return json.dumps(x)            # ensure_ascii: every non-ASCII char becomes \uXXXX, as the source writes it

def R(text):
    """`code` -> mono(), **bold** -> b(), _it_ -> i(); the rest t(). Returns the JS runs array."""
    out = []
    for part in re.split(r'(`[^`]+`|\*\*[^*]+\*\*|\b_[^_]+_\b)', text):
        if not part: continue
        if part.startswith('`'): out.append('mono(%s)' % q(part[1:-1]))
        elif part.startswith('**'): out.append('b(%s)' % q(part[2:-2]))
        elif part.startswith('_') and part.endswith('_') and len(part) > 2: out.append('i(%s)' % q(part[1:-1]))
        else: out.append('t(%s)' % q(part))
    return '[ ' + ', '.join(out) + ' ]'

IND = '      '
def P_(x):   return IND + 'P(%s),\n' % R(x)
def L_(x):   return IND + 'lead(%s),\n' % R(x)
def H1_(x):  return IND + 'H1(%s),\n' % q(x)
def H2_(x):  return IND + 'H2(%s),\n' % q(x)
def EX_(x):  return IND + 'exLabel(%s),\n' % q(x)
def CB_(lines): return IND + '...codeBlock([' + ', '.join(q(l) for l in lines) + ']),\n'
def G_(x):   return IND + '...gld(%s),\n' % R(x)
def BU_(x):  return IND + 'bullet(%s),\n' % R(x)

# =====================================================================================================================
# PART VII -- lead through the end of chapter 22 (before the Part VII-half marker)
# =====================================================================================================================
P7 = ''
P7 += IND + '// [Q_REGION_IS_THE_INTERNET_PATH_V1] Part VII rewritten 2026-10-13: the region (RAM manager + fdf-hub) is the internet path; the broker is history\n'
P7 += L_("Everything so far stands on one local network, complete on its own. This part is how a pond reaches past the LAN: nodes that cannot see each other directly, and whole sites in different cities, joined over the internet. A node does it by being a member of an FD&F **region** (Specification C). One machine, the region host, does the work with two pieces: the region’s RAM manager, which holds the membership, and its hub (`fdf-hub`), one WireGuard interface with one peer per live member. Every member’s one tunnel goes to the hub, and the hub forwards between them, so it is the transit point for every cross-site packet. Neither disturbs anything above: a tunnelled peer looks like just another interface, and the living network on top behaves as it did on one wire. A pond that reaches another pond is the same organism, larger. Nothing polls; the tunnel daemon acts on region events, and when the region is gone its routes, names and tunnel go with it. A node in no region is LAN-only, and needs none of this.")
P7 += H1_("19.  The Region")
P7 += P_("A single node is a network. Two nodes on the same wire find each other with no help. A region exists for the third case: nodes that cannot see each other directly. It is the one piece of FrogNet that can live in the cloud.")
P7 += H2_("What the region holds")
P7 += P_("The region host runs three units. `fdf-ram-<AREA>` is the RAM manager, `rina-host` on TCP `51888`: the region’s memory, where every member holds a session. `fdf-hub` is the WireGuard hub, UDP `50889`, on the interface `wg-<AREA>`. `fdf-client-<AREA>` is the hub’s own membership. `fdf-region-create` makes all three (chapter 20).")
P7 += P_("The region keeps one record of its own per area: the area’s **site key**, written by `map_site` and kept permanently. Everything else lives as long as a member’s session. The session is the enrolment and the GUID in `/etc/fnid` is the identity. A member announces its facts through its session — name, GUID, `/24`, WireGuard public key — and it is alive while its session is up. The mappings that tell the hub which `/24`s a member serves are registered over the same session, signed with the site key, and they go when the session ends. A member that stops leaves the region with its session.")
P7 += P_("The hub is derived from those records alone. It has one peer per live member, and it gives a peer a `/24` only from a verified mapping that names this hub. A `/24` has one owner in a region: the first live member to claim it. A later claimant is refused, and the hub logs `REFUSED <subnet> claimed by ...`. Nothing is configured per member, and the hub polls nothing: each change in the region is one event, and each event is one reconcile.")
P7 += P_("The hub stores none of the traffic it carries. Traffic between members at different sites crosses the hub: each member’s tunnel ends there, and the hub decrypts each packet, forwards it in the kernel and encrypts it onto the far member’s tunnel. So inter-site traffic is in the clear inside the hub host. On one LAN, members talk directly and the hub never sees a byte.")
P7 += P_("When the region cannot be reached, a member does not wait on it. Its tunnel daemon withdraws every route and name the region gave it, tears its tunnel down, and asks again every 5 seconds; when the region answers, the daemon reads it again and brings the tunnel back. The node keeps serving its own `/24` the whole time. If the region host goes down, every cross-site link goes with it, because they all end there.")
P7 += H2_("Where the region can live")
P7 += P_("The region host has one hard requirement: every member must reach it, on TCP `51888` for the RAM manager and UDP `50889` for the hub. Members open both; the region host never connects to a member. For members at different sites, behind home routers and NAT, that means a public address. For a region with no internet, any LAN address every member can reach will do.")
P7 += P_("Where you put it is otherwise open. Ours is a **DigitalOcean droplet**, StreamingFrog. Nothing about FrogNet requires DigitalOcean: any small VPS works, or a box in a colo, a home server with the two ports forwarded, or a machine inside a corporate VPN for a private region. The region host is not a FrogNet node. It needs `/opt/fdf` (from `fdf-droplet.tgz`), `python3`, `wireguard-tools`, and `wireguard-go` where its kernel has no WireGuard module. One machine holds one region.")
P7 += EX_("a region host, on a droplet")
P7 += CB_([
    "host     a droplet with a fixed public IP: 203.0.113.7 (example IP)",
    "RAM      fdf-ram-myarea      rina-host, TCP 51888, records /opt/fdf/var/area-myarea.perm",
    "hub      fdf-hub             WireGuard, UDP 50889, wg-myarea, 10.253.253.254/16",
    "member   fdf-client-myarea   the hub's own membership",
    "key      /opt/fdf/var/site-key-myarea   root-only; printed once",
])
P7 += H2_("Pointing a node at a region")
P7 += P_("A node learns its region from one file, `/opt/fdf/var/join.env`, which names the RAM manager: `RINA_RAM=<host>:<port>`. That file is the switch between the two modes. With it the tunnel daemon runs in region mode; without it the node is LAN-only. The area’s name and its site key are beside it in `/opt/fdf/var/site.env`. `fdf-region-join` writes both files, and `fdf-leave` removes both. `/etc/frognet/tunnel.conf` is not a source of anything now: if an old one is left on the machine, the daemon names its keys in one line and ignores them.")
P7 += EX_("HomeBase's region files")
P7 += CB_([
    "# /opt/fdf/var/join.env  — on HomeBase",
    "export RINA_RAM=203.0.113.7:51888",
    "",
    "# /opt/fdf/var/site.env  (mode 600)",
    "RINA_AREA=myarea",
    "RINA_SITE_KEY=<the key fdf-region-create printed>",
])
P7 += H2_("Joining a pond")
P7 += P_("In a region the pond is the region’s area. A node joins at install, with the three values `fdf-region-create` printed: `frognet_install.sh --from-repo ... --region 203.0.113.7:51888 --site-key KEY --area myarea`. A node already installed LAN-only joins with the same file the installer runs, `fdf-install-$(uname -m).run KEY 203.0.113.7:51888 myarea`. It unpacks `/opt/fdf`, runs `fdf-region-join`, which writes `site.env` and `join.env`, starts `fdf-client` and `fdf-facts`, and restarts the tunnel daemon into region mode.")
P7 += P_("The daemon makes the node’s WireGuard key itself, at its first region start (`WG_KEY: made /var/lib/frognet-tunnel/node_private.key`), and announces only the public half. The node’s `fdf-client` signs each mapping it registers with the site key; the RAM manager refuses one that does not verify. There is no pond password, no passcode and no group token.")
P7 += EX_("Standing up myarea, then adding Mom")
P7 += CB_([
    "# on the region host",
    "/opt/fdf/bin/fdf-region-create myarea 203.0.113.7",
    "    ... prints the firewall lines and the site key, once",
    "",
    "# on MomBox, from the checkout",
    "sudo bash usr/local/bin/installer/frognet_install.sh --from-repo \\",
    "     --name MomBox --ip 10.90.90.1 \\",
    "     --region 203.0.113.7:51888 --site-key KEY --area myarea",
])
P7 += P_("A member with no tunnel of its own is still reached. In the region test (`tools/netns-region-loop-test.sh`), `y`, a child on `z`’s LAN with no tunnel, is reached from `a` over `z`’s LAN, `z`’s tunnel, the hub and `a`’s tunnel.")
P7 += P_("**History.** Until 12 October 2026 this chapter described the broker: a FastAPI service on its own port that registered nodes into ponds with an optional pond password, grouped them into choruses, and handed each node tunnels that ended at the broker. A broker could also run on a LAN, to lay a WireGuard shortcut across a long chain of relays. The broker, its installer, its console and its scripts were removed from the tree on that date. The region replaces it, and has no shortcut use: a member has one tunnel, to the hub.")
P7 += IND + '// [Q_REGION_IS_THE_INTERNET_PATH_V1] chapter 20: fdf-region-create (OPERATING.md section 3), not the broker installer\n'
P7 += H1_("20.  Standing Up a Region")
P7 += P_("The node files left one value pointing at something you may not have yet. `join.env` names a RAM manager, and unless you are joining a region someone else already runs, nothing is listening there. This chapter builds it.")
P7 += P_("One thing is worth saying before the commands, because it changes how carefully you treat the machine. The region host holds the region’s memory and its site key, and every cross-site packet passes through its hub. Build it carefully: it is the one piece with a public address.")
P7 += H2_("Deciding where it lives")
P7 += P_("There is exactly one hard requirement and it decides the rest: every member must be able to reach the region host by IP, on TCP `51888` and UDP `50889`. Members connect to it and it never connects back, so it needs no way into any member’s network — but it does need to be findable from all of them.")
P7 += P_("Internet-facing. Members sit behind home routers, phone tethers, office NAT — places with no stable address and no inbound path. The region host needs a publicly routable IP. A small VPS is the whole requirement. Ours is a DigitalOcean droplet; nothing about FrogNet cares which company rents you the machine.")
P7 += P_("Intranet-facing. Every member is already on one routed site — a campus, a warehouse, a ship. Any local address all of them can reach will do, and the region needs no internet at all. One machine holds one region; a second `fdf-region-create` on the same machine stops and says where the first one is.")
P7 += H2_("Creating the region")
P7 += P_("Copy `opt/fdf/dist/fdf-droplet.tgz` from the checkout to the region host’s `/tmp`. Then, on the region host, as root:")
P7 += CB_([
    "apt-get install -y python3 wireguard-tools",
    "cd / && tar xvzf - < /tmp/fdf-droplet.tgz",
    "systemctl daemon-reload",
    "ip -o addr | grep '10\\.253\\.'",
    "ip route | grep '10\\.253\\.'",
    "/opt/fdf/bin/fdf-region-create myarea 203.0.113.7",
])
P7 += P_("The two `grep`s must print nothing: the hub uses `10.253.0.0/16` on its interface. `myarea` is the region’s name: letters, digits, `.`, `_` and `-`, at most 12, because the hub’s interface is `wg-myarea`. `203.0.113.7` is the address members reach this machine at. `--ram-port` and `--hub-port` change the two ports.")
P7 += H2_("What it builds")
P7 += P_("`fdf-region-create` says each step as it takes it. It records the WireGuard implementation once (`kernel`, or `wireguard-go` where the kernel has no module: install `wireguard-go` first then). It makes the site key, 32 random bytes, and stores it root-only in `/opt/fdf/var/site-key-myarea`. It writes and starts `fdf-ram-myarea`, maps the site key on it with `map_site`, writes `/opt/fdf/hub/hub.env` (the only place that file is made), starts `fdf-hub` and `fdf-client-myarea`, and prints what each said. `fdf-hub` refuses to start on a machine where `hub.env` does not exist, and its journal says to run `fdf-region-create`.")
P7 += P_("It ends with the firewall lines and the site key, printed once:")
P7 += CB_([
    "ufw allow 51888/tcp",
    "ufw allow 50889/udp",
    "ufw route allow in on wg-myarea out on wg-myarea",
])
P7 += P_("Run them, or run `fdf-region-create` with `--ufw`, where the region host has ufw. Otherwise open TCP `51888` and UDP `50889` to the members and let the host forward between the hub’s peers on `wg-myarea`.")
P7 += P_("That leaves one objection, and it deserves a straight answer. The RAM manager speaks plain HTTP and FNWP on its port, unencrypted, and it accepts `map_site` — the call that sets an area’s site key — from anyone who reaches that port. A member is gated by the site key; the port is not. The firewall is what keeps others out, and on a region host open to the internet that is a real exposure, named in Part XII.")
P7 += H2_("Updating the region host")
P7 += P_("A newer `fdf-droplet.tgz` keeps `hub.env`, the site key, the units and `/opt/fdf/var`:")
P7 += CB_(["cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-myarea fdf-client-myarea fdf-hub"])
P7 += H2_("Running it day to day")
P7 += P_("On the region host, `fdf-ls` lists the region’s members (name, alive, OS, cores, memory, IPs), and `fdf-monitor --once` prints one frame of everything the region holds about them. The RAM manager answers its build to `version`, and the hub logs its own when it starts:")
P7 += CB_([
    "/opt/fdf/bin/fdf-ls",
    "/opt/fdf/bin/fdf-monitor --once",
    "journalctl -u fdf-ram-myarea -u fdf-hub -u fdf-client-myarea -f",
])
P7 += P_("On a node, `/opt/fdf/bin/fdf-leave` leaves the region: it removes the region units and files, turns the database-host elector timer back on and restarts the tunnel daemon, which logs `LAN-only mode: no region switch`. The daemon’s WireGuard key is kept, so a later join announces the same key.")
P7 += H2_("When the region host is lost")
P7 += P_("The members’ facts and mappings live in their sessions, so a lost region host takes nothing a member cannot announce again. What it takes is the site key and the address. Run `fdf-region-create` on a new machine, which makes a new key, and run each member’s `.run` again with the new key and address: the file is re-runnable. Each member announces itself on its new session, and the hub is derived again from those records.")
P7 += IND + '// [RESTORE_CH21_V1] restored from the published edition; [Q_REGION_IS_THE_INTERNET_PATH_V1] the hub channel\n'
P7 += H1_("21.  The Tunnel")
P7 += P_("When two members need a link they cannot make for themselves — they cannot reach each other across the internet — the region gives each one a **channel**: a small description of a tunnel to build. The tunnel daemon reads it from the region, one channel per live hub. A channel names the hub’s endpoint and public key, the node’s edge address on the hub’s network — `10.253.A.B/16` for a node that serves `10.A.B.0/24` — the subnets reachable through it, and one constant: `AllowedIPs = 10.0.0.0/8`. The node turns the channel into a WireGuard interface, wgN, and the tunnel is up.")
P7 += P_("The endpoint and the key in that channel are the **hub’s**, not the far node’s. Every member peers with the hub, and no member holds another member’s key. So the tunnels do not run between two frogs at all: they run from each frog to the hub, which ends both and forwards between them. The hub is the transit point, and every cross-site packet goes through it. What that costs, and what an attacker gets for owning it, are the subject of Part XII.")
P7 += P_("The forwarding happens in the kernel. The hub turns on `net.ipv4.ip_forward`, gives each member’s peer its verified `/24`s as AllowedIPs, and routes each `/24` via that member’s edge address on `wg-<AREA>`. Nothing at the application layer parses the packets, buffers them, or writes them anywhere. Each packet is decrypted off one tunnel and encrypted onto the next, so inside the hub host it is in the clear.")
P7 += P_("Two things about that `AllowedIPs` line carry the whole design. It is _always_ the entire FrogNet range, never a narrower set: every tunnel is willing to carry any FrogNet address, so _which_ tunnel a packet takes is left entirely to the route table, not baked into the tunnel. And the edge address in `10.253` is only the carrier addressing between the node and the hub — nothing lives there. The node’s identity and its real traffic still ride its `.1`, as on the wire; the tunnel is plumbing underneath it.")
P7 += P_("So the routes are the simplest thing that could work: `dest dev wgN` — the far subnet, out the tunnel interface, with no gateway at all. Because the interface accepts any `10.x` — that is what `AllowedIPs = 10/8` means — the local kernel drops the packet onto wgN, the hub forwards it out the matching member’s tunnel, and the far node’s kernel routes the inner packet onward. A peer reached by tunnel lands in the route table the same shape as a peer reached by wire.")
P7 += EX_("A channel becomes an interface and a route")
P7 += CB_([
    "the region hands HomeBase one channel, to its hub:",
    "   endpoint    203.0.113.7:50889    (the hub's, from hub.env)",
    "   pubkey      <the hub's public key>",
    "   edge        10.253.80.80/16      (HomeBase serves 10.80.80.0/24)",
    "   subnets     10.90.90.0/24        (MomBox's verified mapping names this hub)",
    "   AllowedIPs  10.0.0.0/8           (carries any 10.x)",
    "",
    "the daemon brings it up as wg0 and installs one route:",
    "   10.90.90.0/24  dev wg0  metric 22   (no gateway needed)",
    "",
    "MomBox has the mirror of this toward the same hub.",
    "the hub routes 10.90.90.0/24 via 10.253.90.90, in the kernel.",
    "",
    "now 10.90.90.1 answers like any LAN peer. apps never know.",
])
P7 += H2_("The tunnel daemon owns the lifecycle")
P7 += P_("Tunnels are not discovery’s job. A dedicated **tunnel daemon** owns them. On each region event it reconciles the kernel’s wg interfaces to the region’s channels, bringing up what is missing and tearing down what the region dropped. On startup it tears down dead leftovers so a reconnect always starts clean. It pointedly never runs `wg-quick down` to refresh a link, because that flushes every `dev wgN` route in a single syscall — and on a node that relays for others, that route table is most of what the node is. Instead it nudges the handshake back to life in place, leaving the routes untouched. That is why a tunnel can flap all day — internet links do — and the picture above it does not flinch.")
P7 += P_("On a region the routes are FD&F’s plan (`fdf_graph`), applied by the tunnel daemon. It installs a planned LAN route at once, because the member’s live session and a lease pair written by both ends already say the link is there. It gates hub routes on the WireGuard handshake: a dead handshake withdraws them at once. A route whose row merely vanished from the region is held for 60 seconds, and removed only if it is still absent then.")
P7 += P_("A LAN-only node holds no tunnel. Its daemon makes `frognet0`, tears down any WireGuard interface left from a region, logs `LAN-only loop started`, and waits; the merge’s bring-up stage finds no region and keeps the node free of WireGuard.")
P7 += H1_("22.  One Overlay, Many Sites")
P7 += P_("A channel carries more than the far node’s own wire. For each member, the hub carries every `/24` that member’s verified mappings name: its own, and those of the hosts whose way out it is. So a single tunnel to the hub can carry a far site’s entire side of the world. Every `/24` a node reaches through the hub goes out the same tunnel as `dest dev wgN` with no gateway; the hub routes it to the member behind it, and that member’s kernel routes the inner packet onward. Two LANs in two cities, each with one member tunnelled to the hub, become one `10.0.0.0/8` overlay — every node addressed by its `.1`, whether it is on the same switch or across the country.")
P7 += EX_("Two sites, one overlay")
P7 += CB_([
    "two sites, one region, one hub:",
    "",
    "  New York LAN                           Seattle LAN",
    "  NY-1  10.102.60.1  --wg0-- hub --wg0--  SEA-1  10.133.144.1",
    "  NY-2  10.102.61.1                       SEA-2  10.133.145.1",
    "",
    "on NY-1 the hub channel carries Seattle's whole side:",
    "   10.133.144.0/24  dev wg0      (SEA-1)",
    "   10.133.145.0/24  dev wg0      (SEA-2, no tunnel; its way out is SEA-1)",
    "",
    "on the hub: both /24s via SEA-1's edge, 10.253.133.144.",
    "NY-2 reaches SEA-2 at 10.133.145.1 as if it were next door.",
])
P7 += P_("Nothing above the route table changes. On a region the discovery walk of Part V does not run; the routes are the region’s plan, as the end of the previous chapter describes. The proxy forwards over wgN with the same FNWP-1 frames it uses on copper. Above the route table there is no local network and remote network; there is one overlay, and the internet is just one more bearer underneath it.")
P7 += H2_("Ponds — more than one region")
P7 += P_("Choruses went with the broker. A node can take part in more than one pond: `fdf-pond-add RAM_HOST:PORT AREA SITE_KEY` holds one more session, to that pond’s RAM manager, all the time — presence, the pond’s shared memory and its notices. One pond is **forward**: its plan is the node’s routes, names and tunnel. `fdf-forward AREA` brings another one forward, and the tunnel daemon makes the switch in place, without a restart.")
P7 += H2_("What keeps it private")
P7 += P_("Trust in FrogNet rests on a few plain mechanisms, not a security product bolted on top. **Identity** is the GUID minted at install in `/etc/fnid`. The session is the enrolment and the GUID is the identity, so a node can be renamed or re-addressed without becoming a stranger. **Joining** a region is the site key: each mapping a member registers is signed with it (HMAC-SHA-256), and the RAM manager refuses one that does not verify, so a session alone gets a peer on the hub and no `/24`. The tunnel daemon makes the node’s WireGuard key, mode 600, and announces only its public half; private keys never leave the box. **Encryption** covers what crosses the open internet: every member’s link to the hub is WireGuard, so traffic is encrypted on each leg between a member and the hub, and is in the clear inside the hub host. On a local wire you trust the segment you own — the node is the `.1`, the DHCP and DNS authority for its own `/24`, and nothing routes onto it without its say. Capability, not a password, decides who holds a role; identity and the site key decide who is in the region at all. The wider security they add up to, and where it stops, is drawn together in Part XII.")
P7 += G_("This part is the most conventional networking in FrogNet, which makes it the most immediately legible to anyone who already knows the ground — and the least forgiving, because a mistake here is a node that cannot be reached at all. If you know WireGuard, and you know address routing through tunnels, the open work is here: what happens on rekey, how a node that moves between bearers keeps its address, a region with more than one hub, and a `map_site` that checks who is asking. The region is exercised in network namespaces by `tools/netns-region-loop-test.sh` (in `opt/fdf/src/fdf-source/rinab`), which runs the real tunnel daemon against the real RAM manager and `fdf-hub`, so a change can be shown rather than argued.")
P7 += '\n'

old7 = between('      lead([ t("Everything so far stands on one local network, complete on its own.',
               '      // [RESTORE_PART_VII_HALF_V1]')
sub(old7, P7)

# aliases for the retitled Part VII headings (old anchors keep working)
sub('  "s-what-the-installer-builds-and-the-wall-in-the-middle-of-it": "what-the-installer-builds",\n',
    '  "s-what-the-installer-builds-and-the-wall-in-the-middle-of-it": "what-the-installer-builds",\n'
    '  // [Q_REGION_ALIASES_V1] Part VII and XII headings retitled when the broker left (2026-10-13)\n'
    '  "19-the-broker": "19-the-region",\n'
    '  "20-standing-up-a-broker": "20-standing-up-a-region",\n'
    '  "where-the-broker-can-live-and-what-it-runs": "where-the-region-can-live",\n'
    '  "pointing-a-node-at-a-broker": "pointing-a-node-at-a-region",\n'
    '  "running-the-installer": "creating-the-region",\n'
    '  "what-the-installer-builds": "what-it-builds",\n'
    '  "choruses-groups-that-span-the-pond": "ponds-more-than-one-region",\n'
    '  "take-the-broker-now-show-me-the-pivot": "take-the-region-host-now-show-me-the-pivot",\n'
    '  "what-the-scan-does-not-prove": "what-an-open-port-does-not-prove",\n')

# =====================================================================================================================
# PART VIII -- one role
# =====================================================================================================================
sub('Every live FrogNetHost (.1) is a candidate for both roles that exist, database and media, and the election picks among them on capability.',
    'Every live FrogNetHost (.1) is a candidate for the one role that exists, the database host, and the election picks among them on capability.')

# =====================================================================================================================
# PART X -- SotF handler and the mediahost role are history
# =====================================================================================================================
sub('"SotF media      implemented*    implemented     implemented", "databasehost    \\u2014  (no-op)      implemented     implemented", "* media overrides the codec slot to manage a live stream"]',
    '"databasehost    \\u2014  (no-op)      implemented     implemented"]')
sub('The media protocol overrides this same slot to manage a live stream. A handler',
    'SotF, the media protocol, overrode this same slot to manage a live stream; its handler left the tree on 12 October 2026. A handler')
sub('tuple; the media election gates on what the probe found (ffmpeg with libvpx). Nothing is handed',
    'tuple; the databasehost election scores what the probe found. Nothing is handed')
sub(U('P([ t("SotF — Song of the Frogs — is the adaptive media protocol, the hot path. It overrides the codec slot, but for SotF that slot does not compress a body; it manages a live "), b("stream"), t(" — video and audio carried as opaque frames — with delivery classes (some frames must arrive, some may be dropped) and an adaptive ladder that steps the picture down, size by size, to audio alone as the link degrades, and climbs back as it recovers. It is real-time and lossy by design, and it rides its own sockets, audio apart from video, so it never blocks, or is blocked by, anything else. It also names the mediahost role, so the election places it on a capable, on-wire box. That role still exists in the code; the service it starts runs an fnav.py that is not in the tree. The Communicator’s calls today go through comms-media (Part XV).") ]),'),
    U('P([ t("SotF — Song of the Frogs — was the FrogNet implementation’s adaptive media protocol, the hot path. Its handler overrode the codec slot, but for SotF that slot did not compress a body; it managed a live "), b("stream"), t(" — video and audio carried as opaque frames — with delivery classes (some frames must arrive, some may be dropped) and an adaptive ladder that steps the picture down, size by size, to audio alone as the link degrades, and climbs back as it recovers. It is real-time and lossy by design, and it rides its own sockets, audio apart from video, so it never blocks, or is blocked by, anything else. It also named the mediahost role, so the election placed it on a capable, on-wire box. The role and the handler were removed on 12 October 2026; "), mono("databasehost"), t(" is the one role in "), mono("core/role_registry.py"), t(". The Communicator’s calls today go through comms-media (Part XV).") ]),'))
sub('"  ShopBox.databasehost.advertise()   -> capability tuple -> new DB", "  HomeBase.mediahost.advertise()     -> capability tuple -> new DB", ""',
    '"  ShopBox.databasehost.advertise()   -> capability tuple -> new DB", ""')
sub('P([ t("What the mediahost does is fan-out, and the shape',
    'P([ t("What the media server does is fan-out, and the shape')

# =====================================================================================================================
# PART XII -- what is exposed now: the region host's two ports, comms ports, node services; no broker
# =====================================================================================================================
sub(between('      P([ t("Start with what a stranger on the internet can even find. ")',
            '      img("airgap_broker.png", 620, 320),\n', incl_z=True),
    P_("Start with what a stranger on the internet can even find. **Node services bind all interfaces.** The proxy on `80`, Apache on `8080` and `8443`, the data daemon on `9009`, dnsmasq: what keeps the internet off them is where the node sits, not what they bind to. A node is behind its uplink’s NAT, its `10`.x addresses are not routable from outside, and it opens no tunnel to the outside: in a region it dials the hub, and its tunnel’s configuration names no listen port and one peer, the hub. Put a node’s uplink on a public address and those ports can be reached unless you filter them."))
sub('P([ t("The claim, in its strongest honest form &mdash; the weaker form',
    'P([ t("The claim, in its strongest honest form \\u2014 the weaker form')
sub('an authorised node that has been taken, the broker, local privilege boundaries',
    'an authorised node that has been taken, the region host, local privilege boundaries')
sub(between('      P([ t("Two FrogNet things on the droplet always face the internet.', '      P([ t("Over radio the picture'),
    P_("The FrogNet things that face the internet are on the region host. Its RAM manager answers on TCP `51888`, reached directly, its HTTP front and its FNWP sessions both, unencrypted. Its hub’s WireGuard listens on UDP `50889`, as silent as any other WireGuard to a packet that does not come from a key it holds. `fdf-region-create` prints the firewall lines that open exactly those two ports, and the route rule that lets the hub forward between its peers on `wg-<AREA>`. When `fdf-services.sh` runs there, the services it starts face the internet too: FD&F’s RAM server on `8840`, the kart race’s on `8799`, and the Communicator’s `comms-ram` on `8800` and `comms-media` on `8994`. On our droplet Apache on 80 and 443 serves the website; that is a deployment choice, not a FrogNet property.")
    + P_("Be exact about what gates the RAM manager. A member is gated: a `/24` reaches the hub only through a mapping signed with the area’s site key, and the RAM manager refuses one that does not verify (`EPERM`). A session with no verified mapping gets a peer on the hub with its edge address and no `/24`. But the call that sets the site key, `map_site`, checks nothing. Anyone who reaches TCP `51888` can make it, and a new key for an area makes every member’s next mapping fail. The firewall is what keeps others out. On a region host whose port is open to the world, that is an open door to the region’s key. The hub routes the region’s verified `/24`s, and every member accepts `10/8` from it, so root on the region host can reach node services. What it lacks is the semantic state those services hold, not a route to them."))

blk = ''
blk += EX_("Two enrollment shapes. Only one of them is here.")
blk += CB_([
    "  what FrogNet does",
    "    operator, on the region host   fdf-region-create (makes the site key)",
    "    operator, on the node          the installer, with --region and --site-key",
    "    node -> RAM manager            a session; its facts; mappings signed with the key",
    "    hub                            a peer, and the /24s the signed mappings name",
    "",
    "  what it is usually mistaken for",
    "    anyone, from anywhere          find the enrollment app",
    "    anyone                         create an account",
    "    anyone                         configure a network",
])
blk += P_("Read down those two columns and the difference is not a hardening measure, it is a different shape. Every step on the left begins with somebody who already has the region host or its key. There is no web form and no console. The traffic to the RAM manager is plain HTTP and FNWP on its own port. FrogNet’s port-80 interception applies only to traffic to `10/8`, so that traffic never enters the semantic wire.")
blk += P_("The semantic wire runs between nodes, inside `10/8`, and between sites that traffic crosses the hub in transit. Be exact about what it buys there, because it is easy to overclaim. Capturing one semantic frame does not necessarily reveal the complete request or response it stands for, since the frame may carry only a difference against shared state — materially different from a sequence of complete, independent, stateless transactions. It is _not_ encryption and no substitute for authentication. An attacker who compromises an endpoint, obtains the templates, or accumulates enough context can reconstruct what was represented. Confidentiality across the open internet comes from the WireGuard bearer; the semantic wire reduces what is on it, and that is a different claim.")
blk += H2_("What a scan actually returns")
blk += P_("A node normally has no public address of its own. Behind its router there is nothing for a scan to reach.")
blk += P_("The region host is the one machine that has to answer. A member getting its first tunnel cannot ride a tunnel that does not exist yet, so first contact crosses the open internet, and the RAM manager must be listening for it. No scan of a region host’s two ports has been recorded for this book. The commands are short, and what they return belongs here once they have been run:")
blk += CB_([
    "nmap -Pn -sT -p 51888 -sV <region host>",
    "nmap -Pn -sU -p 50889 <region host>",
])
blk += P_("**History.** The scan this chapter used to print was run on 17 August 2026, from an ordinary desktop on a domestic connection, against the broker’s port, `18257`. It found three ports open — 80 and 443 for Apache, and 18257 — and the broker answered nine nmap probes with a JSON 404 or a plain-text 400 and nothing nmap could name. The broker was removed from the tree on 12 October 2026, and that scan no longer describes anything that runs.")
blk += H2_("What an open port does not prove")
blk += P_("An open port that answers is a service, and a service is code, and code has bugs. Nothing above says otherwise. An attacker who finds `51888` has found the region’s memory — its members, their facts and mappings — and a `map_site` with no check in front of it. That is why the firewall matters. There is still no application behind it: no user data, no business logic, no sprawl of endpoints.")
blk += P_("Run it yourself. The commands are above.")
blk += H2_("Take the region host. Now show me the pivot.")
blk += P_("Everything above is an argument until somebody tries to break it, so here is the invariant stated as something that can fail, and the terms under which it would.")
blk += P_("**Compromise the region host completely and you still have not compromised the nodes.** That is a stronger claim than “the region host is hard to attack”, and it is a more useful one, because it does not depend on the region host being well written. Assume it is not. Assume it is owned.")
blk += P_("And it is stronger than it first sounds, because the region host is not merely adjacent to the traffic. **It is the inter-site data path.** Every cross-site packet transits its hub. So this is not a claim that the attacker has been handed something peripheral — they have been handed the wire.")
blk += P_("Start with what the region host does see. Its hub ends a separate tunnel from each member and forwards between them in the kernel, so inter-site traffic is in the clear there: it is outside the WireGuard boundary. Its RAM manager holds the region’s memory: every member’s facts (name, GUID, `/24`, public key), every mapping, and the area’s site key, which `fdf-region-create` also keeps root-only in `/opt/fdf/var/site-key-<AREA>`. It runs none of the semantic stack — no templates, no learned structure, no cache — but a capture taken there from the start of a conversation learns the templates, exactly as section 38 says.")
blk += P_("**Transit is handled in the kernel.** The hub forwards by the kernel’s own routing: one route per member `/24`, via that member’s edge address. With the kernel’s WireGuard the bytes never enter a process on that machine; where the region host runs `wireguard-go` (a kernel with no WireGuard module), they pass through that one process. Nothing parses them, nothing buffers them at the application layer, and **nothing writes them to disk.** There is no history of the traffic to seize.")
blk += P_("That matters for the shape of the attack rather than its difficulty. An attacker with root can of course start capturing — but they are starting a capture, not opening a store. What was already forwarded is gone, and what they collect from now on is still keyless and still contextless. The region’s memory is another matter: root on the region host holds it, and can rewrite it.")
blk += P_("The answer to a compromised region host is a new one. Run `fdf-region-create` on a clean machine, which makes a new site key, and run each member’s `.run` again with that key and the new address. The members’ facts and mappings come back with their sessions; nothing else needs restoring.")
blk += P_("So the challenge is not rhetorical. Start the attacker with root on the region host and see whether either of these can be made to go red.")
blk += CB_([
    "  ORACLE 1  region-host pivot",
    "    given    root on the region host, whose hub routes the",
    "             region's /24s and whose 10/8 every member accepts",
    "    red if    a new application-level exchange can be",
    "              originated with an internal FrogNet-only service",
    "",
    "  ORACLE 2  application recovery",
    "    given    root on the region host — all cross-site traffic",
    "             transits its hub, so capture is free",
    "    red if    protected application content can be",
    "              reconstructed without compromising an endpoint",
])
blk += P_("The broker’s third oracle — manufacture enough authority to join as a member without the endpoint’s secret — is not offered against a region host. It holds the site key, and with the key it can sign any mapping. Root on the region host is the region’s authority, and that oracle would be red by construction.")
blk += IND + 'P([ t("Those are the terms. A professional red team, an automated system, or one determined person with a weekend \\u2014 the source of the attempt does not matter, and neither does how clever it is. What matters is whether the run goes red, because a red run is a finding and a hypothetical is not.") ]),\n'
blk += IND + 'P([ t("If one of them goes red, that is the best outcome available: an architectural failure discovered while it can still be fixed, by somebody who was not in the room when it was designed. If none of them do, that result is worth more than another paragraph asserting the property \\u2014 and it is the only kind of evidence this book accepts for anything else, so it would be dishonest to want a softer standard here.") ]),\n'
blk += G_("This is an open invitation with defined failure conditions, which is the only kind worth making. Bring the topology, bring the tooling, bring whatever you like. State which oracle you are attacking, run it, and show the result. A reproducible counterexample is a complete contribution and does not need to arrive with a fix — but a generic “what if”, with no demonstrated path from the region host to a node, is not a finding and will be treated as what it is.")
sub(between('      exLabel("A scan of a node\'s public interface, and of the broker"),',
            '      H2("When you must take input: a drop box, not a door"),'), blk)

sub('for anyone who has not already been handed a tunnel and a token. Access is gated',
    'for anyone who is not already a member. Access is gated')
sub('t(" and the pond password gate the broker, not the segment. That is deliberate.',
    't(" names a node; the site key gates the region\\u2019s mappings. Neither gates the segment. That is deliberate.')
sub('and then a listener on the wire gets nothing; the broker still does.',
    'and then a listener on the wire gets nothing; the region\\u2019s hub still does.')
sub("Between sites, each site's hop to the broker \\u2014 or, on an FD&F region, to the region's hub \\u2014 is WireGuard. That machine decrypts, forwards in the kernel and re-encrypts, so the traffic is encrypted on the wire but not end to end. If you do not trust the broker host, layer your own cipher on the payload.",
    "Between sites, each member's hop to the region's hub is WireGuard. The hub decrypts, forwards in the kernel and re-encrypts, so the traffic is encrypted on the wire but not end to end. If you do not trust the region host, layer your own cipher on the payload.")

# =====================================================================================================================
# PART XIII -- simulator tiers, roles, media host election as history; the monitor's source
# =====================================================================================================================
sub('"  simulation/    N nodes converging over your topology, at", "                 five tiers: pure model, real planner and", "                 committer, real broker, real kernel in", "                 netns, real proxy data plane."]',
    '"  simulation/    N nodes converging over your topology, at", "                 four tiers: pure model, real planner and", "                 committer, real kernel in netns, real", "                 proxy data plane."]')
sub('The system simulator runs the real planner, the real broker handlers, the real bring-up reconcile,',
    'The system simulator runs the real planner, the real bring-up reconcile,')
sub(between('      P([ t("Both take user-defined topologies, internet tunnels and the real broker.', '      H2("Nine of ten were stale"),'),
    P_("Both take user-defined topologies and internet tunnels. You declare nodes with their served ranges, LAN guests, shared peering segments, dead endpoints, dual upstreams and identities, plus a tunnel list, and the actual merge runs per node to a fixpoint. The model tier still models the broker’s channel list: about forty of its scenarios replay broker-era incidents by that name, and it is kept as a model, not shipped code. No simulator tier runs the region’s RAM manager and hub. The region is exercised in network namespaces instead, by `tools/netns-region-loop-test.sh`, against the real RAM manager and `fdf-hub`."))
sub('"    roles: [mediahost, databasehost]",', '"    roles: [databasehost]",')
sub('P([ b("DEMONSTRATED."), t(" Media host election, which for a long time elected nobody.',
    'P([ b("DEMONSTRATED, then retired."), t(" Media host election, which for a long time elected nobody.')
sub('t(". It was not that no host qualified. It was that the question was never properly asked.") ]),',
    't(". It was not that no host qualified. It was that the question was never properly asked. The mediahost role was removed on 12 October 2026; "), mono("databasehost"), t(" is the one role.") ]),')
sub(U('reports the mesh from that node’s point of view. Every host it knows, each one’s state,'),
    U('reports the mesh from that node’s point of view. Every member its node’s snapshot ("), mono("getSnapshot.php"), t(") lists, each one’s state,'))

# =====================================================================================================================
# PART XIII-half -- the route snapshot has no caller in the current merge; the monitor reads getSnapshot.php
# =====================================================================================================================
sub(between('      bullet([ b("Event-driven one-shot."), t(" A producer that fires', '      bullet([ b("In-process background thread.")'),
    BU_("**Event-driven one-shot.** A producer that fires when something happens rather than on a clock. The tree’s example is the route snapshot, `emit_routes_snapshot.sh`, which the old bash merge (`mergeHostsAndResolv.bash`) wrote at the end of every merge. The current merge (`runMerge.bash`, `discovery.live`) does not call it, so nothing in the shipped merge writes the route snapshot now."))
sub('t(" (it runs "), mono("python3 -m frognet_monitor_py"), t(" from "), mono("/usr/local/bin"), t("). It is a curses program:',
    't(" (it runs "), mono("python3 -m frognet_monitor_py"), t(" on the node\\u2019s Python environment, "), mono("/opt/frognet_semantic/venv"), t(", from "), mono("/usr/local/bin"), t("). "), mono("frognet_monitor --once"), t(" prints one frame as text and exits. Without it, it is a curses program:')
sub(between('      P([ t("Before it can read anything the Monitor has to know which node it is standing on,', '      H2("The two calls"),'),
    P_("Before it can read anything the Monitor has to know which node it is standing on, and it asks the node. One read of `http://127.0.0.1/getSnapshot.php`, through the proxy, gives it the node’s name and `.1`, its mode (`lan-only` or `region`), its build, its ponds with the forward one marked, its hub link and the handshake’s age, its members, its elections and its errors. The snapshot takes each field from one source per mode, and says so in `errors` when a source cannot be read. The one thing the Monitor still reads for itself is the domain its sensor names start with: `domain=` in `/etc/dnsmasq.d/opts_only.conf`, falling back to `/etc/frognet_domain` and then the hostname.")
    + P_("The parsing is deliberately split from the reading. `parse_snapshot()` takes the snapshot’s body as a string and returns the summary every view draws from, or nothing when the body is not a snapshot; it runs no request and touches no file. The shared parser vectors run it, and the C++ and C# monitors must give the same answer. When you write your own reader, split it the same way.")
    + H2_("Finding the peers")
    + P_("The peer list is the snapshot’s `members`, in its order: in a region, the plan the tunnel daemon applied; LAN-only, the hosts the last merge named. There is no second source. When the snapshot does not answer, the title says so, the list is empty, and the next cycle asks again; nothing stands in for it."))
sub(between('      P([ t("Both are resolved from "), mono("/etc/hosts"), t(" and never from the resolver,', '      H2("Is a merge running?"),'),
    P_("Both come from the snapshot’s `elections`, which the node reads from the `<role>.frognet` lines in its own `/etc/hosts` and never from the resolver, because in practice the two disagree — on one node the file said `10.250.250.1` while `gethostbyname` said `10.130.130.1`. The file is what every other component routes by, and a monitor that highlights a different box from the one the traffic goes to is worse than one that highlights nothing. The markers are read again with each probe cycle.")
    + P_("And when `/etc/hosts` cannot be read at all, the snapshot says so in its `errors`, and the Monitor draws that line in red. The two states that would otherwise paint identically — a pond with no data host elected, and a node whose hosts file is unreadable — are then different on the screen."))
sub(between('      P([ t("Because curses owns the terminal, diagnostics go to "), mono("/tmp/frognet_monitor.log"), t("; the one exception', '      H2("What to take from it"),'),
    P_("Because curses owns the terminal, diagnostics go to `/tmp/frognet_monitor.log`. If the log file cannot be opened, the trace is dropped rather than allowed to take the interface down. Read that log when a column is blank and you want to know which of the two steps returned nothing."))

done()
