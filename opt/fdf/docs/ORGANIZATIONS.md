# An organization on FrogNet: setup, levels, login, and the RAM manager

2026-10-31. John Fawcett, with Claude. The live copy is the Claude Doc "An organization on FrogNet: design and plan
(rev 2)"; this file is its export plus what the code decided, kept in the tree so the design travels with the code.
Rules are marked **John** (he stated them) or **proposed** (Claude's, not yet confirmed).

An organization stands up its own FrogNet with nobody from Fawcett Innovations in the room. The manager rents a droplet,
runs one wizard and hands out one line per level; every device installs with that line and is in. Applications run on
top and never deal with networks, servers, accounts or sync. The EOC is the first customer; RangerTrak is the first
application. Setup, levels and login are the platform's, in the ++ libraries; every Ribbit application gets them.

## Words

| Word | What it is | Source |
| --- | --- | --- |
| RAM space | One shared memory: First Responders, EOC Admin, the system's own. Each has its own ordered list of RAM managers. | John |
| System RAM | The space the network itself runs on: facts, mappings, participants. | John |
| WAN RAM host | A RAM manager outside the FrogNet network, reachable by every host: the manager's droplet, and standbys in order. | John |
| databasehost-control | Always the machine with the highest IP address in the network. Unchanged. | John |
| Level | A group in the customer's own words: First Responders, Hospitals, Ambulances; Viewer, Editor, Admin. | John |
| Reach | What a device's edge may touch: RAM only, a list of spaces and endpoints, or everything. Admission defines it. | John |
| Device admission | Once per device per RAM host: its own tunnel, its edge, its level, its reach. | John |
| Application membership | Applications join RAM spaces, not devices; each holds its own session to each of its spaces. | John |
| Generic credential | One WireGuard credential per RAM host, in every join line, used for one exchange. | John |
| Login port, session port | Two random ports per level on a RAM host. | John |
| Merge | A node applying one RAM event: read the world, derive its routes and channels, write what changed. There is no discovery. | John |

## 1. Setup: the manager, once, on their own droplet

Install the one droplet bundle, run `/opt/fdf/bin/fdf-setup`. It asks before it changes anything and fixes what is
missing once the manager says yes. **John.** In order: the RAM manager (`fdf-region-create`); the RAM spaces, all at
once, with their access and their RAM-manager lists (**John**); the levels in the manager's own words, each with a
password or none, its reach and the spaces its applications join (**John**); users and passwords (**John**); the generic
credential and one join line per level, printed and as a QR; the manager's own device's first login. A standby is set
up by running the wizard there with the same spaces, levels and users (**proposed**); `fdf-org line LEVEL --with <the
standby's line>` puts the standby into the primary's line.

`fdf-setup` (`[FDF_SETUP_V1]`) does it in that order: what the machine needs (installed with apt-get once the manager
says yes), `fdf-region-create` when the machine holds no region, `fdf-org init` and `fdf-org.service`, the spaces, the
levels (the examples offered are Watcher, Player, Host; Viewer, Commenter, Editor, Admin; First Responders,
Headquarters, Hospitals, Ambulances), users and passwords, a standby's lines, then one line per level printed and kept
root-only in `/opt/fdf/var/lines/LEVEL.line` (`fdf-setup --lines` prints them again). Run again, it shows what is there
and keeps it. The device side of the manager's first login is the line itself (`fdf-login LINE`, or the app's install);
the wizard says so rather than reaching a device it cannot reach. Tested by `netns-setup-test.sh`: the wizard answered
as a manager would, names with spaces, a password level; devices join with the printed lines, a wrong password is
refused; a second run changes nothing.

What survives a restart of the droplet (**proposed**; implemented, tested by `netns-org-test.sh` check 10):

| What | Kept as |
| --- | --- |
| Levels (ports, reach, password or none, spaces), users (encrypted), the door | permanent cells `(fdf-org, level/user/door, ...)` |
| Admissions: device, edge, level, state | the hub's `admitted/NAME.json` files, as `fdf-invite` writes them, plus level and state |
| The credential key | `/opt/fdf/var/org-key`, root-only, like the site key; a verifier is HMAC-SHA256(key, scrypt(password, salt)) |
| Reach | not kept: rebuilt by `fdf-org serve` from the above at start and after every change |

## 2. Levels and reach

A level is however the customer thinks of their own organization. **John.** Admission defines reach: RAM only (most
levels), a list of spaces and endpoints, or open. **John.**

Reach is enforced by nftables on the RAM host, bound to each device's edge (`[REACH_PER_EDGE_V1]`, table `inet
fdf_org`, rebuilt whole). **Proposed.** WireGuard's allowed-ips cannot do it: they say which source a peer may use, not
what it may contact, and `fdf-hub` writes no filter rules. A level's session port is DNAT'ed to the RAM port and each
edge may reach only its own level's (`[PORTS_BOUND_TO_ADMISSION_V1]`). Members admitted by `fdf-invite` are untouched.
Reach covers what an edge touches through its tunnel; a LAN behind a node is outside it unless the node enforces it too.

## 3. Adding a device

Copy the bundle for that kind of device, run it with the level's join line, type a user and password if the level has
one. **John.** A client gets its edge; a node also gets its /24 from the RAM manager (**proposed**) and is wired or
projecting, never both: with no SSID it must be attached to a physical router in AP mode, which by definition supplies
no DHCP; the node serves DHCP and DNS to it (**John**).

A node joins with its level's line in one command (`[ORG_NODE_JOIN_V1]`): `sudo bash frognet_install.sh fdfjoin1.<...>`
on a new node, or `sudo ./fdf-install-ARCH.run fdfjoin1.<...>` on one that runs FrogNet already. Both end in
`fdf-region-join LINE`, which asks for the user and password when the level has one, runs `fdf-login LINE --node` (the
daemon's key; `system-space.json`, `hub.json`, `site.env`, `join.env`) and starts the daemon into region mode. An
invitation (`fdfinv1.`) still works the same way. Not built: the node's /24 from the RAM manager (the installer still
asks for its `10.X.Y.1`).

Login, at each RAM manager on the line (`[LOGIN_RESTRICTED_FIRST_V1]`, **John**):

1. The generic credential, for one exchange at the door: the device's own public key in, its own edge out, restricted to
   its level's login port.
2. The generic tunnel down, the device's own tunnel up.
3. Login on the level's login port; the password is checked; the edge's reach becomes the level's.
4. A session on the level's session port.

On Windows and Android the same login is the Kotlin member's (`member/.../Join.kt`, `[JOIN_LINE_V1]`), on the bridge:
one TUN, fdf-tun, and each RAM manager an island whose 10.253.253.254/32 is aliased 1:1 from 100.64.0.0/10 on a tunnel of
its own; the door is an island on the generic credential's key for its one exchange. `Space.kt` is `fdfspace` for
those devices. `fdf-member-kt join LINE SPACE...` runs it on a JVM (`netns-join-kt-test.sh`: a wrong password refused,
logged in at both RAM hosts through their aliases, both spaces on A, Admin cut off on A: on B in 12.9-13.2 s, back in
0.8 s). The JVM's HTTP client has no TCP keepalive setting, so a silent loss is seen later there than in Python (7.7 s).

What the code decided (**proposed**):

- Both exchanges are one JSON line each way over TCP inside the tunnel, never cells: a RAM manager has no per-cell
  privacy, so a password in a cell would be readable by every restricted device. The WireGuard source address is the
  identity: the hub accepts a packet from an edge only from that edge's own key.
- A device's name is `d-` plus a hash of its public key (`[NAME_FROM_KEY_V1]`), so nobody can claim another's device;
  the device ID and nickname are carried beside it.
- Every RAM host's hub is 10.253.253.254, so each RAM manager's tunnel gets a route to that one address at its own
  metric (100 for the first on the list, 101 for the next) and every connection is bound to its tunnel
  (`[RAM_ON_ITS_OWN_TUNNEL_V1]`, `SO_BINDTODEVICE`). Nothing else on the device is routed differently.
- The device's own state (its key, and per RAM manager its edge, session and order) is `/opt/fdf/var/login.json`,
  root-only; `fdf-login --up` brings the tunnels back without a login.

## 4. Which RAM manager

Every space has its own ordered list; each member uses the first that answers. After the list, every space falls back
the same way: the LAN's machines from the highest IP (databasehost-control) down to the node's own; a machine is never
without a RAM manager. **John.** Every node decides for itself and has its own connection, which changes no routes.
**John.** Reachable means through the network: members of one space on one connected network converge on the same RAM
manager; different RAM managers within one space only when the network has actually partitioned. **John.** Separate
worlds, different applications in different spaces, are membership and allowed. **John.**

The system space, on a node (`internet_tunnels_v3/region.py` and `poll.py`, `[SYSTEM_SPACE_V1]`; **proposed** where
not marked John):

- The list is `/opt/fdf/var/system-space.json`: `fdf-login --node` writes it from the join line, one entry per RAM
  manager (its hub, this node's edge there, its session). A node joined by invitation has one entry, from `hub.json`.
  After the list come the LAN's RAM managers from the highest IP down to the node's own (**John**).
- The LAN's RAM managers are known, not discovered: the .1 of every /24 the node's plan routes on a LAN device
  (`lan-rams.json`). On a WAN RAM manager the plan sees the whole network and replaces the list; on a LAN one it only
  adds, so a node alone on its own RAM still tries the higher ones.
- Every node runs its own LAN RAM manager, `rina-host` on its .1, port 8841, a child of the tunnel daemon
  (`[LAN_RAM_ALWAYS_V1]`), so the walk always ends somewhere.
- The tunnel daemon walks the list: a WAN entry's hub channel comes up, then it is asked (`version`); a LAN entry has
  no hub channel, and every WireGuard tunnel comes down (**John**: failover from the WAN to the LAN is the removal of the
  WG tunnels). The LAN's routes stay, so the node can still reach the LAN RAM managers above its own.
- The node's own session (`fdf-client`) follows: `join.env` stays what was configured (it is the region switch);
  where the system space is now is `system-ram.env` (`ponds/<forward>.now` for the forward pond), read by the unit after
  it, and the session is restarted when it changes.
- A standby above the current entry is asked through a probe tunnel of its own (`fdfsp<N>`, the node's key, its edge
  at that RAM host, `SO_BINDTODEVICE`, no route, down after each question: `[SPACE_PROBE_TUNNEL_V1]`), so nothing is
  routed differently and the current hub's peer is never moved.

## 4a. Spaces (step 5)

Applications join RAM spaces, not devices (**John**); the admin sets the spaces up at setup (**John**). What the code
decided (`[SPACES_V1]`, **proposed**):

- `fdf-org space NAME` adds a space on a RAM host: its own RAM manager, `rina-host` inside the tunnel on a random port
  of its own, with its own Permanent records (`space-NAME.perm`). `fdf-org serve` runs it, says when it exits, and
  starts it again. The system's own space is the region itself.
- `fdf-org level NAME --space S ...` names the spaces a level's applications join (each must exist). An edge of that
  level reaches each of them (nftables, as for its session port); an edge of another level does not.
- The login's answer says where each of the level's spaces is on that RAM host; `fdf-login` keeps it per RAM manager.
- An application holds one `fdfspace.Space` per space (`fdfspace.from_login(name)`), each with its own list in the
  join line's order, its own failover and failback. A level that joins no such space is told so.

Measured in `netns-spaces-test.sh` (two RAM hosts, two spaces, a Responders device and an HQ device): Admin's RAM
manager on A cut off, the HQ device's Admin on B in 7.7 s while both devices' Responders stay on A; the cut healed, back
in 1.0 s; Admin's RAM manager on A killed, fdf-org starts it again and the device is back on A with its truth.

## 5. Run

Each node writes only its own facts and holds one read; a change is an event, and the merge applies it, writing back
only what changed (**proposed**: today `write_facts` rewrites the file every time).

## 6. Failover, and no failback

Per space, in the shared library (`fdfspace.Space`, `Space.kt`) and, for the system space, in the tunnel daemon
(`region.Standing`); the application only receives the change and re-arms its reads and re-writes its truth. **John.**
Memory is never moved.

**John, 2026-11-01** (`[NO_FLAP_V1]`): "You know within milliseconds that the socket to upstream has closed. You should
immediately switch to the backup. Within milliseconds, not seconds. Then we don't do shit to go back up until this one
dies. No flapping around."

- **Standing sessions**: a held read on every candidate all the time (a WAN RAM manager through a probe tunnel of its
  own on a node). They are how the switch knows, at the moment it needs to, which candidates are alive.
- **Lost**: the current one's session closes, which takes milliseconds when its RAM manager or its host goes (a silent
  path, TCP keepalive: about 5 s). The move is at once, to the first candidate in list order whose standing session is
  alive. Nothing is asked then.
- **Back**: a candidate coming back changes nothing while the current one lives. The one exception (**proposed**): a node
  or device on its LAN fallback, its last resort and not a peer, takes a configured RAM manager that comes back.

On a node, measured in `netns-system-space-test.sh` (two hubs, z on the Internet, w behind z with its own tunnel;
one box, wireguard-go): hubA's RAM manager dying, z's new session on hubB 255 ms after the loss, w's 274 ms (of which
bringing up the new hub channel with userspace WireGuard is about 110 ms); hubA back, both still on hubB 12 s later; both
WAN RAM managers gone, both on the LAN's highest (w's 10.4.4.1) in 2.3 s, no hub channel left (only the standing
sessions' probe tunnels, which route nothing) and z still routing w's /24; hubA back, both off the LAN onto it in 4.7 s.

For devices, measured in `netns-space-test.sh` (one box, wireguard-go), from the kill to the application's change:
26-32 ms (A dies, onto B; B dies, onto A; both die, onto the device's own RAM); A back, both stay on B; a silent
partition (no socket closes) 4.8 s, by TCP keepalive; healed, nobody moves.

## 7. Where it is in the tree

| Piece | File |
| --- | --- |
| Credential manager, levels, users, lines, revoke, reach rules | `fdf/bin/fdf-org` (`fdf-org.service` in the droplet bundle) |
| The manager's wizard | `fdf/bin/fdf-setup` |
| Device login | `fdf/bin/fdf-login` (Linux); the member library for Windows and Android is next |
| A space's RAM manager, failover, failback; several spaces from the login | `fdf/lib/python/fdfspace.py` |
| Spaces on a RAM host (their RAM managers, reach, the login's answer) | `fdf/bin/fdf-org` (`space`, `serve`) |
| A connection bound to one tunnel | `fdf/lib/python/fdfram.py` (`Ram(hostport, device=...)`) |
| The system space on a node: the list, the walk, the LAN fallback, following | `internet_tunnels_v3/region.py`, `poll.py` (`run_region_loop`) |
| Tests | `rinab/tools/netns-org-test.sh` (steps 1-3), `netns-space-test.sh` (step 4, devices), `netns-system-space-test.sh` (step 4, nodes), `netns-spaces-test.sh` (step 5), `netns-setup-test.sh` (step 6), `netns-join-kt-test.sh` (the Kotlin member's login) |

Not yet built: C behind A and B (step 4: C's own connection egressing through either), the node's /24 from the RAM manager (step 7),
the Windows bridge's setup.cmd and the Android app taking a join line (the
member library's login is built: `Join.kt`, `Space.kt`).
