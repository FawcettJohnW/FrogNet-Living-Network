# Application setup and operation on FrogNet

2026-11-01, John Fawcett. This is the governing description of how an app is set up and how it runs through a split and
a merge. Where `ORGANIZATIONS.md` disagrees with it, this document wins.

The operating rule: **find the current RAM, reassert your own truth, then learn the world again.** Held reads keep you
informed as that world changes.

## Words: one meaning each

| Word | Means | Does not mean |
| --- | --- | --- |
| Fabric | The Linux nodes, their tunnels and their plans. It does all routing and re-forms as connectivity changes. | Anything a server does. Every server is blind to it. |
| Member | A Linux machine with a bare-bones install. It is a full network member and can read the tuples. | A Windows or Android machine. |
| Device | A Windows or Android machine. It attaches through the fabric and uses apps. | A member. It never serves the general AppRam; a Team's AppRam may be on its lead's device (`[GROUP_PLACE_V1]`). |
| databasehost (the SystemRam) | The existing, deterministic selection that locates the current **network** RAM, the RAM host where members read and write. Capability and configuration tuples are in databasehost-control (the highest .1). | The app's RAM server. That is a separate selection. |
| Droplet | An app's RAM server code, installable on any Linux member. | A DigitalOcean machine, or anything on the internet. |
| Primary | The droplet install the admin designated. Its placement defines the app's scope. | Something the server knows. The server does not know it is primary and knows nothing about network state. |
| App RAM server selection | The app's own deterministic criteria and algorithm, run over the capability tuples, that pick which droplet serves the app. | databasehost. |
| Capability tuple | Written by every running service: what is there and where it is reached. | A record of what a machine could host. It records what is there. |
| App configuration tuples | In the network RAM: the app's configuration, its access definition (teams, verifiers, the device records it grants) and where each of its RAMs is served. | The app's data. |
| App data | In the app's own RAMs (its general RAM and its team RAMs): positions, logs, messages, each team's own records. | Anything in the network RAM. |
| Owner | Each participant owns the facts it publishes, and keeps the truth it must reassert when its RAM connection changes. | Anyone who edits another's record. |

## The common operating sequence

Every participating component follows this sequence when it attaches to the current RAM:

1. Locate the RAM through databasehost.
2. Write the truth it owns.
3. Read the tuples it needs to understand the current world.
4. Act on what is present.
5. Hold reads on the tuples or families it depends on.
6. When a held read wakes, read the changed truth and adapt.
7. If the RAM connection changes, repeat from the beginning.

There is no requirement to wait for a complete picture. Each component works from what is present now and responds as
further facts appear.

## An app is an admin app and a client app

**John, 2026-11-01:** "We will need an Admin app and a set of client apps. The client apps are set up with a team
focus, while the admin app is used to set up and configure things. Clients must be able to connect to the RAM before
they can do anything."

- **The admin app** sets the app up and configures it: the steps below. It writes the app configuration tuples. It is a
  standalone app (RangerTrak's: `opt/fdf/src/rangertrak-app`, `RangerTrak-Admin`) that reaches the network through any
  member's door. Its change is the whole configuration, signed with the admin key; every member that has the app checks
  the signature with its own copy of the key, adopts it as its own configuration and says so ([ADMIN_APP_V1]).
- **The client app** is one app. Its main focus follows the user's team (**John:** "They just change the main focus
  for the user. Everything else is the same"): it opens on that team's screen, writes app data into the general RAM and
  the team's RAM, and reads what the team is granted.
- **A client connects first.** Until it has connected to its RAM (its access checked, its RAMs found from the
  configuration tuples), it does nothing. After that it keeps its own truth on the device, to write it again after a
  RAM change.

## Setting up an app

The admin performs setup at the primary.

**One command on the member's console** ([APP_INSTALL_V1], John 2026-11-04): `fdf-app install APP`. Being on the
network is the trust; nothing else is asked for it. It asks the SystemRam for the app's configuration tuple,
`(fdf-app, config, <app>.*)`, and takes the one the read returns (no choosing between members' tuples, no search:
every member holds a read on that family already).
- **Found**: the app is set up. It is shown and adopted as it is; changes are made in the admin app, never at install
  (John: "There is no --clone"). The admin key is never in the SystemRam, so the admin's
  password is asked once (or `--admin USER:PASSWORD`), checked against the check value there, and the key kept here:
  this member can then verify the admin app's changes. This member saves it as its own copy, the one it writes again
  after a failover.
- **Not found**: the app's first install. The arguments (`fdf-app config`'s) if given; otherwise the questions: the
  teams (each with its own RAM), the other roles, each role's category and whether a client may take it with no login,
  users (password asked twice, kept as a verifier), login required, relocate or fail, and the app's administrator.
- Either way it restarts `fdf-app.service`, which writes the configuration into the SystemRam and serves what the
  app's selection gives it. Gate: `rinab/tools/test_fdf_app.py` 16.

What the install sets, step by step:

1. The admin installs the droplet.
2. The admin defines the app's reach and access. **John, 2026-11-01:** "When the admin sets things up, he is setting up a
   role. He may assign that role to a specific individual so that it is activated at login, or it may be assigned
   because his app has a different default screen (Team vs EOC). None of those require deep access to the network
   behind the RAM."
   - The roles, and each role's RAM name when it has one (a team's RAM).
   - The individuals assigned a role, each with a password: the role is activated at their login.
   - The roles a client may take as its default, with no login (its default screen: a Team client, an EOC client).
   - Whether login is required.
   - A role's clients use the general RAM and their role's RAM. **John:** "The access levels for the network are not
     related to the ram that the roles use." The network's levels are its admission and the reach of the devices
     connected at them, restricted for almost everything (`ORGANIZATIONS.md`), not an app's.
3. The admin defines the app's behavior when the primary is unreachable:
   - **Fail:** stop unless the primary is visible.
   - **Relocate:** activate an installed droplet on the reachable side.
4. The droplet writes the app's configuration and access definition as app configuration tuples. Passwords are stored only as
   verifiers.
5. The running service publishes its capability.
6. At the end of the primary install, the remote nodes the admin named are installed and configured automatically.
7. The configured participants retain the truth they own, so they can reassert it when attaching to a new current RAM.

The primary's designation is part of the app configuration. The server code does not need to understand topology or
recognize itself as primary.

## A new Linux machine

1. Install the bare-bones system.
2. The machine becomes a network member.
3. It locates the current RAM through databasehost.
4. It reasserts its own member facts and any capabilities it currently provides.
5. It queries the capability family to discover the services presently visible.
6. The admin chooses which apps to join.
7. If an app is already installed, the machine joins it. Otherwise, installing the app joins it.
8. The app writes the truth it owns.
9. It reads its app configuration tuples to learn where each of its RAMs is served, its configuration and its access
   definition.
10. It connects to the app's RAMs it is authorized for, writes there the app data it owns, and reads the current shared
   state.
11. It establishes held reads so that changes wake it.

The capability query describes what is currently present. More services can appear as their owners attach and reassert
themselves.

### Updating a machine that ran rina-host ([FDF_RAM_ON_PLATFORM_V1], 2026-11-03)

The RAM host is `fdf-ram` (build `fdf-2026-11-03-r39` and later), on the Ribbit platform; every C++ client (`fdf-client`,
`fdf-events`, `fdf-participants`, `fdf-where`, `fdf-attention`, `ram`) is built on the same platform and only those
speak its wire. A host and its clients are updated together, from the one tarball for the machine's type
(`fdf-update-<date>-<n>-x86_64.tgz` or `-aarch64.tgz`). The update rewrites every unit whose `ExecStart` names
`/opt/fdf/bin/rina-host` to `/opt/fdf/bin/fdf-ram`, removes `rina-host`, reloads systemd and restarts the `fdf-*`
units. The region file (`RINA_REGION_FILE`) is the same file: its Permanent records are restored by `fdf-ram` at start
(`[REGION_FILE_V1] N Permanent record(s) restored` in the unit's journal). A phone's team RAM (`libteamram.so`) is
built on the same platform with the RangerTrak app.

## A Windows or Android device

1. The device attaches through the fabric.
2. It reaches the app through its own edge: the app's door on the member it attached to (`ram.php` on a node, the
   Kotlin member on Windows and Android).
3. If its role is activated at login, the user supplies the user name and password once. Otherwise the client takes its
   default role (its default screen), or none when the app allows that.
4. The edge checks the password by reading the app's access tuples: the verifier is there. **Login is a read.** There
   is no login server, nothing is sent to another node, and nothing checks each request afterwards (**John,
   2026-11-01:** "A dedicated login server is a centralized service and is therefore unacceptable."). The answer is the
   role, and the role says which RAMs the app joins.
5. The device's authorized access is recorded in the app configuration tuples. The record is the device's own truth (its
   role and user): the device keeps it and writes it again through its edge after every RAM change, like everything
   else it owns.
6. The device publishes the app truth it owns, then reads the current world through the app.
7. On later connections the device has its role already, so it needs no user action. A relocated droplet needs nothing
   from the device beyond what the device writes again.
8. The user is not asked to log in again.

The device remains a device; it never becomes a Linux fabric member. Its app participation follows the same
write-first, read-second rule.

**On Android the membership is the hidden base's** ([BASE_APP_V1], built). **John (2026-11-03):** "the base disaster
area app is hidden from the user. They don't need to bring it up and make login a 2-step process" ... "it's shared
between all of the various apps and ram regions a given machine needs." The base is frognet-android's app, labelled
Disaster Area, with no launcher icon: one membership per device. A level app (RangerTrak Team, Public, the kart ...)
asks it through `JoinActivity` (`net.frognet.action.JOIN`, with the level's line and the user and password the level
app asked for: the one login; the base attaches again when they change, and the door upgrades an open level,
[ONE_LEVEL_PER_HOST_V1]); `net.frognet.action.LEAVE` ends it. The base sends each app that asked its state (joined,
edge, invited, join) when it changes (`net.frognet.action.MEMBER_STATE`). RangerTrak no longer compiles the member in
(it did, [RT_IS_A_MEMBER_V1]: Team and Public on one phone were two memberships); `:base` is a module of its Gradle
project and of its Studio export. Gates: the type-checks (`frognet-android/tools/app-typecheck.sh`,
`rangertrak-app/android/typecheck.sh`); not run on a phone here.

## Roles, the resilient EOC, and login wherever a device can ([ROLES_NOT_MACHINES_V1], John 2026-11-02)

This section governs where the next one ("Enrollment"), written earlier the same day, names machines. That one says
what is built today; this one is the design. **Built** and **Not built** say which parts exist.

**Everything is a role; the machine is an abstraction.** **John:** "The EOC is a role assigned to a machine." "As it is
for all. The machine is an abstraction we need not consider." "Of course. What else would it be? What else could it
be?" The EOC, each team, each Power crew, ambulances, hospitals, water, general help: each is a role, held wherever
the admin assigns it. An app asks for a role and gets its current holder; nothing is built around a particular
machine. Wherever a role is held, its services come with it: `fdf-org@<role>`, its RAM host and, for the EOC, its
door. (Code that still names a machine and must change: the enrollment QR carries the door's address; `join.env` holds
one `host:port`; the door's identity ask goes to the region that one machine joined. **Not built.**)

**Categories and groups.** **John:** the EOC admin app lets the admin define, tab by tab, the categories they use
(Teams, Power, Water, Ambulance, Hospitals ...); within a category, groups that are "the equivalent of a Team, except
for the thing they are servicing", each with designated leads, as Teams have now. "Power people can update the power
state ... but they can't do water." **Built** for the configuration and the admin app ([CATEGORIES_V1]): a role (a
group) carries its `category`, and the configuration the order of the `categories` (the tabs); a role from a
configuration made before categories is in Teams. `fdf-app config ... --category NAME --role NAME[:default][@CATEGORY]`;
the admin app has one tab per category with its groups and its users; the door's `op=roles` says each role's category
and the order. Gate: `rangertrak-app/test/test_admin.py` step 8. What a category's people can do with its data is the
apps' (next paragraph); the RangerTrak screens do not yet group by category.

**A role's privileges are commentary, not data ownership.** **John:** sensor data is automatic and bears the app ID;
it goes to databasehost like everything else (`metric_upsert`). A role's extra privileges apply to its own data and
are "really only commentary": individual or group chat, missions, scheduling things to happen (running an actuator),
"more an ephemeral grouping thing than anything else". The roles are also the layers of the map screen (where all the
ambulances are). "Data goes to databasehost, but roles define apps define visibility." Extra data and state an app
needs go through that app's own custom server. So databasehost does not check a role on a write.

**General help is a role too.** **John:** "targets don't have to run the general help. That's a role like any other
and has dedicated machines that offer open SSID connection. Connection to the public network gives you tunnel access
to the public RAM of the current EOC, and nothing more." No login for victims.

**Where a group lives is its lead's choice** ([GROUP_PLACE_V1], **built**). **John:** "The Lead should be able to say
that the region lives on databasehost.frognet ... every host in the system must have the PHP for the app alongside the
regular api.php." "There is no 'bring up' a region. It just turns on ... Turning on means someone is calling the API."
- **On the lead's device** (`at: here`, naming the device's guid): a phone, or a FrogNet Host such as a Pi with a router
  "for a more powerful external antenna" (**John:** "Obviously it's #2"). The group goes where the lead goes; it is
  served by that device alone, the app's selection is not asked, and nobody serves it while that device is gone, so
  members who cannot reach it fail.
- **On databasehost** (`at: databasehost`): wherever the app's selection puts it; it stays when the lead leaves.
- The lead says it at the door: `POST ram.php?op=place&app=APP {user, password, at, guid}` (dc-door r7), checked
  against the verifier and refused for a user who leads no group. The door writes `(fdf-app, place, <app>.<group>)`;
  `fdf-app serve` on every member adopts the newest from that group's lead into its own configuration (`places`, beside
  `roles`, so the admin's next configuration keeps it) and writes it again with it: owned, not held.
- Gates: `rinab/tools/test_fdf_app.py` 13-15 (placed here, a non-lead's place ignored, the device gone and nobody
  serving it, back to the selection); `rinab/tools/netns-enroll-test.sh` 9 (the real door).
- **On the lead's phone** (built): Host team (the RangerTrak Team app, [TEAM_HOST_V1]) runs the team's RAM on the phone
  (libteamram) and places the team `here` at the door with the lead's user and password from the phone's enrollment,
  so no member serves it while the phone does: one RAM per team. **Team on databasehost** (the lead's button) places it
  there, stops the phone's RAM (the lead's sponsor stays) and clears the lead's host cell; the core waits on the door's
  held `op=rams&until=TEAM` and says where the team's RAM is now. Gate: `rangertrak-app/test/test_admin.py` 9 (the real
  door, members and AppCore: Host team -> unserved on the members; databasehost -> served again, told 0.5 s after).
  A Pi lead is a member and serves its group with `fdf-app serve`.
- **The admin's view, Groups now** ([GROUPS_NOW_V1], built): every group, its category, where its lead placed it,
  where its RAM is served at that moment, and its leads, live (the admin's held read wakes on a place, a member's
  configuration, capability or map; `fdf-app` says its map in the network RAM, `(fdf-app, map, <guid>)`, when it
  changes). Gate: `test_admin.py` 11.

**The Pi is initialized the way the phone is.** **John:** "It must be a LAN client of the EOC, so it is trusted. It gets
its own tunnel setup the same way a phone would." Then the lead joins the Pi's projected network as an authenticated
user and runs the admin tool, which configures the Pi as the group's host. The lead's admin app "is really doing admin,
not just setup": first it selects and connects to the upstream network and becomes a member. If all hosts share one
SSID and password, devices float between upstreams. When the upstream drops, the group's device can, if configured,
scan for and join the closest host with a signal. **Not built.**

**A merge is a re-evaluation of routes, never of RAM.** **John:** "Merge in the sense that it causes a re-evaluation
of the routes. There is no merging of the RAM if databasehost for any app changes."

**The EOC elects itself: a capability and the app's own selection** (John, 2026-11-03; this replaces the admin's
ordered list of EOCs he described on 2026-11-02). **John:** "a role handler is a capability and the capability tuple is
used to find the primary and the backups, but only one is actually active." "The admin installs the app service on a
machine ... If that connection fails, the app fails. It does not try a backup. A torn network is worse than a
disconnected one." "The selection process is run whenever there is a network event ... This is a held read on the
capability tuple family for the app." "the admin doesn't have to designate backup RAM servers even for the EOC. It is a
role and the machines capable have it installed. Designation and failover/failup are automatic." "The EOC that meets
the criteria best is the same one for everything visible, so it's the EOC." "That's up to the algorithm. All services
work like this -- EOC is just the example. For a media service, the requirements are different than for the EOC."
- **Built** ([APP_DROPLET_V1], [APP_SELECTION_V1]): every member with the app writes its capability tuple
  `(fdf-app, can, <app>.<guid>)` (every scalar its probe measured, never a defaulted one, and the RAMs the install
  offers, `--serve`) and writes it again after any RAM change; `fdf-app serve` holds a read on the family and reruns the
  selection on every event; the door refuses a RAM that is not served ("nothing works until it is"): connect or fail.
- **The app's own selection** is its configuration's `select`, per RAM (`*` for the rest): `need` (capability keys that
  must be true) and `rank` (keys compared in order, higher first; `-key` lower first); a member whose tuple lacks a key
  ranks below every member that has it; the guid breaks the last tie. Every member uses the `select` of the newest
  configuration among the alive members, so all compute the same member. No `select`: the most memory, then the most
  cores, then the highest guid. `fdf-app config ... --select RAM=KEY,-KEY --need RAM=KEY --serve RAM,RAM`, or the
  admin's configuration: the admin app's **Where each RAM is served** (a row per RAM or `*`: need, rank). `fdf_select.py`.
  Gates: `rinab/tools/test_fdf_app.py` 10-12; `rangertrak-app/test/test_admin.py` 10 (from the admin's screen).
- **A device's space follows capability too** ([SPACE_BY_CAPABILITY_V1], **built**). **John:** "There is no fallback
  list. Fallback is determined by capability." Each RAM host's `fdf-org` writes its capability (its probe's scalars,
  its guid) into each space's RAM there and into the region's, `(fdf-space, can, AREA)`. `fdfspace.py` and `Space.kt`
  hold a read on it at every host the space is on, and on every event are on the best alive host by capability
  (`fdf_select` / `Select.kt`); a better host coming back takes the space again; none alive: on nothing, said. This
  replaces [NO_FLAP_V1] (2026-11-01: list order, "never back up while it lives", a fallback RAM). Gates:
  `rinab/tools/netns-space-test.sh` (15), `netns-spaces-test.sh` (14), `netns-join-kt-test.sh` (7).

**Logging in at the targets on a join line** (the bootstrap, before a device sees any capability tuple):
- **The device is told the line at initialization**, and "as it is connected to the networks in the list, it
  opportunistically creates the tunnels"; with no tunnel, "but there is a network available that it can log into, it
  does that and creates the tunnels": the generic credential's one exchange at that target's door, the device's own key,
  then login (section 5.1 of `ORGANIZATIONS.md`). **John:** that "lets the merges work, even if there's a network
  interruption for a minute, and it removes the requirement of prior config."
- **At every target at once** ([LOGIN_AT_ALL_AT_ONCE_V1], **built**): a dead one costs its own timeout, not everyone's.
- **The highest on the line that admits** ([LOGIN_IN_LINE_ORDER_V1], **built**, `fdf-login` and the Android member's
  `OrgLogin.admit`): never the first to answer, so every device with the same line gets the same answer. A lower target
  that answers first waits until each above it has refused or failed. The cost: with the first target unreachable, the
  join takes that target's own give-up time: a door that does not answer is given up in 10 s ([UNREACHABLE_IN_10S_V1],
  John: "Change the 76s to 10"; `netns-admit-kt-test.sh` 6 joins in 10.9 s).
- **One level per host** ([ONE_LEVEL_PER_HOST_V1], **built**, `fdf-org` r21). **John:** "a single machine can only have
  one level to any given host." From an open level to a password level is the upgrade, and the device is restricted
  until that level's login checks the password. (Before r21 the record took the new level and kept "admitted", so a
  device admitted at an open level got a password level's reach with no password; `netns-org-test.sh` 11 fails on r20.)
  A device that holds a password level and asks for an open one (its base attaching again) keeps the level it holds,
  and the door says so; a different password level is refused. An admitted device that logs in again with no password
  is given its session (its edge already reaches it). Gate: `rinab/tools/netns-org-test.sh` 11-13.
- **The same users and passwords at every target, owned** ([ORG_OWNED_V1], **built**, `fdf-org` r23; it replaces
  `fdf-org export` / `import`, [ONE_ORG_ON_EVERY_TARGET_V1], which carried the credential key by hand). Every RAM host
  of the organization writes its definition -- its levels (not their ports) and its users' verifiers and removals, each
  stamped with when it was set -- into the network RAM, `(fdf-org, def, <org>.<host>)` (`FDF_NET_RAM`, else the
  machine's `join.env`), again on every local change and whenever that RAM changes; each holds a read on the family and
  adopts, entry by entry, the newest that is newer than its own (a level new to it gets its own ports). A user's verifier
  is PBKDF2-SHA256 with its own salt (as an app's users'), so it checks on every host and no key leaves a host; a user
  set before r23 (a verifier under its host's own key) checks only there until it is set again. `fdf-org user LEVEL
  USER [--remove]` runs wherever the region's RAM is reached. Gate: `rinab/tools/netns-targets-test.sh` 1-2 (chief set
  on A only, adopted by B in 0.3 s; a level added on A, on B with B's own ports; a removal on A, on B; no password and
  no key in the network RAM), 3-5 (login at B with the password set on A).
- Gates: `rinab/tools/netns-targets-test.sh`, `netns-admit-kt-test.sh`.

**Every hub at 10.253.253.254 collided once a device held two tunnels** (the WAN's and the current EOC target's).
**John:** "Why isn't the hub using the IP address of its host?" "Use RAM." The host's own 10.A.B.1 would loop on a
Linux member, because on the hub's LAN that address is also the tunnel's endpoint; **John chose 10.253.A.B**
([HUB_ADDRESS_FROM_ITS_HOST_V1], **built**). A hub on a node that serves 10.A.B.0/24 takes 10.253.A.B: unique (one
owner per /24), derived from its host, never an endpoint. A hub on a machine serving no FrogNet LAN (StreamingFrog)
keeps 10.253.253.254. `fdf-region-create` works it out and says it (`fdf_regions.own_hub_addr`); `hub.env` records it
as `FDF_HUB_ADDR` (a `hub.env` without it is from before, when every hub was 10.253.253.254, and stays so); `fdf-hub`,
`fdf-org` and `fdf-invite` use it; the hub says it in its row, `(fdf-org, hub, AREA) {..., addr}`, and in its facts,
where members read it. The pool of edges never gives a hub's address. Gate: `rinab/tools/netns-hub-addr-test.sh` (a
device holding both tunnels, one route to each hub, both sessions at once). The RAM manager's `(pond, config, net).hub`
says it from the next binary build.

**Still open in this design** (Claude, raised 2026-11-02, not yet answered):
1. The EOC role needs standing access to every group's region, for the dashboard to chat with a mobile team.
2. (Answered by [GROUP_PLACE_V1]: a group that must outlive its lead's device is placed on databasehost.)
3. Help requests and log entries written on an island during a split stay there: the app has to send its own
   unacknowledged reports up on reconnect.
4. (Built, [REVOKED_EVERYWHERE_V1], 2026-11-03.) `fdf-org revoke d-<key hash>` (the name `fdf-org list` shows), run
   anywhere the region's RAM is reached, revokes the device for the organization: a permanent `(fdf-org, revoked,
   NAME)` cell, carried in the organization's definition in the network RAM. Every RAM host adopts it on its event,
   removes the admission (the hub drops the peer at once) and rebuilds its reach; every door refuses that key from then
   on, so the device needs a new key. Across a split it takes effect at the merge. There is no un-revoke.
   netns-targets check 5: revoked on A, gone at B in 0.1 s, refused at both doors; the r23 `fdf-org` fails it (B kept
   the peer, and the same key logged in again).
5. The open public level needs a per-device rate limit at admission.
6. Scheduled actions (an actuator run) live in a role's RAM and move with its holder: hold them where they run, or make
   running twice harmless.
7. `netns-c-behind-ab-test` (2 checks) fails on recovery timing after a cut, the same before and after `fdf-org` r20
   (found 2026-11-02). (`netns-spaces-test` 6-7 pass since [SPACE_BY_CAPABILITY_V1]: they expected the move back.)

## Enrollment: the WAN region, the EOC, a team's lead and its members ([ENROLL_V1])

> As built on 2026-11-02. Where it names machines (StreamingFrog, Seattle7) it is superseded by the section above.

**John, 2026-11-02:** "The initial Team Lead setup can be done through a QR code, and the lead can generate another QR
code for each member ... The only requirement is that both sides be connected to a network that can see both sides at
the same time." "Be sure to make the design generic ... This is the common way to join a region with an app. Once the
user has authenticated to the WAN app once, additional apps may be installed that have hubs on the LAN/WAN, without
additional authentication." "The 'VPN' is to the WAN hub. Thereafter it's just routing."

A device has one tunnel, to the WAN hub. Everything else (the EOC's node, its app RAMs, a lead's phone) is reached by
routing through it. Enrolling is how a device gets that tunnel's invitation and its role in an app, in one step:
scan a QR code, type the password.

**1. The WAN region** (StreamingFrog, reachable from the Internet; it routes to the EOC's LAN, 10.170.170.0/24).
On StreamingFrog: `fdf-setup` (or `fdf-region-create frognet <public IP>`). Its hub's unit brings up `fdf-org@frognet`
and `fdf-identity@frognet` with it. Then, on any machine in the region, `fdf-org level <Level> ...` and
`fdf-org line <Level>` give the line a node joins with.

**2. The EOC** (Seattle7, 10.170.170.1, on the EOC's network). It joins the WAN region as a node:
`fdf-region-join fdfjoin1.<...>`. Then the app goes on it: the add-in (the door, `/dc/ram.php`) and
`fdf-app config rangertrak general,<team>,... --role eoc --role <team>:default ... --user EOCAdmin:<pw>=eoc
--admin <admin>:<pw>`, with `fdf-app serve` running. From then on the admin works in the admin app (RangerTrak-Admin),
connected through Seattle7's door. The admin adds teams, hospitals, ambulances, users and roles there. The door asks
the region Seattle7 is joined to for invitations (`fdf-identity <app>-<user> --leaf --again --line`, answered where
the WAN hub is).

**3. A team's lead is named by the admin.** In the admin app's Users list, the admin gives the user the team's role,
a password, and ticks **Leads**, then Apply. Every member of the app adopts the change (`"lead": true` on that user).
**QR** shows the lead's enrollment code: `fdfenroll1 http://<Seattle7's door>/dc/ram.php rangertrak <user>`, drawn
as the frog. The lead scans it with the app and types the password. The door checks it against the verifier and
answers with the role, the lead flag and the phone's own WAN invitation. The phone joins the WAN region with it, and
the app opens on the team's screen. Only the lead's phone and the door need to see each other for that one exchange:
the EOC's Wi-Fi, or the Internet.

**Save the assignments.** The assignments live in the network's RAM. If the node holding them goes, they are gone
until the admin puts them back. After a change, the admin presses **Save the current assignments**. To put them back
on the primary or a fallback, the admin picks the save and presses **Restore**, then Apply. Nothing does this on its
own (John: "No, do not automate this part of it. It needs to be intentional").

**4. The lead assigns its members** on the phone's Team members page: each member's name (usually a callsign) and a
password. The phone keeps only verifiers. Opening the page starts the team's sponsor on port 21801, in the app's
foreground service, and it keeps running while the app's service does.
**QR** shows a member's code: `fdfenroll1 http://<the lead's address>:21801/enroll rangertrak <name>`. The lead's
address is whichever the member can reach: the hotspot's, the Wi-Fi's, or the lead's WAN edge. The member scans it and
types the password. The lead's phone checks it, asks the door for the member's invitation as the lead
(`op=identity`, `<app>-<team>-<name>`), and answers as the door would: the role, not a lead, the invitation, and the
door. The member's phone joins the WAN region with its own invitation. From then on, members reach each other, the
lead and the EOC by routing, over the hotspot or the Internet.

**5. Other apps** on an enrolled phone use the same membership. An app whose hub is on the LAN or the WAN is reached
by routing and admits the device as a member of the region, with no new login.

Gates: `rinab/tools/netns-enroll-test.sh` (the door, the hub, a lead's sponsor and two phones joining, on real
pieces); `test/test_admin.py` 5-7 (leads, the QR read by zxing, save and restore); `test/test_frogqr.py`;
`frognet-ram-java/test/EnrollTest.java`.

## Normal running

1. Members and devices publish changes to the truth they own.
2. Running services maintain their capability tuples.
3. Apps read their app configuration tuples to find their RAMs, and read and write their data in those RAMs.
4. Held reads wake components whose dependencies change.
5. Each component that wakes reads the current values and acts.
6. If a RAM's location changes, the app follows the location its configuration tuples give.
7. Routing remains the fabric's responsibility.

There is one current network RAM, reached through databasehost. The capability tuples and the app configuration tuples are in it; each app's data is in that app's own RAMs. Each
app's RAM server is chosen by that app's own selection over the capability tuples. Every member runs the same
deterministic selection over the same tuples and gets the same answer.

## Split

**Where the SystemRam is** ([SYSTEM_SPACE_V1], [LAN_RAM_ALWAYS_V1], [SYSTEM_RAM_FOLLOWED_V1], John 2026-11-04): the
SystemRam may be the droplet's RAM manager, or the LAN's, and it is always one of them. Each node's tunnel daemon walks
the system space's list (the WAN RAM managers in order, then the LAN's machines from the highest .1 down to its own)
and writes where it is now in `/opt/fdf/var/system-ram.env`; every node runs its own LAN RAM manager (its .1, port
8841), so a node is never without one. Everything on the node follows that file: `fdf-client` (its unit reads it),
the door (`fdf.env`), and `fdf-app` (`fdfram.where()` reads it before `join.env`; a read that fails waits for that RAM
to come back or for the file to name another; the file changing while the old one still answers is an event too). On
the new SystemRam `fdf-app` writes this member's configuration and capability again, and the app's own selection runs
over the members on this side: every app RAM is relocatable to the greater or lesser network (John: "if you cannot
reach SystemRam and you have not designated your own machine as a databasehost, then you're dead in the water. That's
not going to happen with SystemRam, but it may for other apps"). Gate: `rinab/tools/netns-system-space-test.sh` (the
app's tuples on hubA; both WAN RAM managers gone: its RAM served on the LAN side and its tuples on the LAN's SystemRam;
hubA back: written there again).

1. Connectivity changes.
2. The fabric re-forms on each reachable side.
3. Each participant locates the current RAM available through databasehost.
4. Each participant first reasserts the truth it owns, each into the RAM it belongs in:
   - members publish their own facts, and running services their capabilities, into the network RAM;
   - the owners of configuration and access records reassert those records into the network RAM. **John, 2026-11-01:**
     "Who writes and reasserts? Everybody." Each droplet writes the configuration and access definition it was installed
     with; each device writes its own access record;
   - app participants reassert their owned data into the app's RAMs once those are served (step 8).
5. Participants then query the tuples to learn what is currently present on their side.
6. Each app applies its configured behavior:
   - a **fail** app stops on a side that cannot see its primary;
   - a **relocate** app uses the capability scan and its own selection to activate droplet code already installed on
     members on that side.
7. An activated droplet reasserts its owned truth and publishes its running capability.
8. Apps read their app configuration tuples, connect to the indicated RAMs, and reassert their data there.
9. Devices keep their roles and write their access records again. Users do not log in again.
10. Held reads keep everyone responding as more participants reassert themselves or connectivity changes again.

Nothing needs to identify the event as a split. Each side operates from its current reachable members and their
asserted truth.

## Merge

1. Connectivity changes again.
2. The fabric re-forms.
3. Participants locate the current RAM through databasehost.
4. Each owner reasserts its own truth before learning the world again.
5. Services publish their current capabilities and locations, and the owners of configuration and access records
   reassert them, into the network RAM.
6. App participants reassert their owned data into the app's RAMs (after step 9).
7. The app's selection, run over the merged capability tuples, gives one serving droplet. Every member gets the same
   answer.
8. Apps read their app configuration tuples in the network RAM, which tell each app where its RAMs are.
9. Apps connect to the indicated service and establish held reads.
10. Later changes wake them, and they adapt.
11. Devices keep their roles and write their access records again, without another user login.

## What makes the current RAM useful

databasehost supplies the location, and the owners supply the data.

The system does not depend on a newly located RAM already holding a complete, correct picture. Participants rebuild
that picture by asserting what they own, reading what others have asserted, and holding reads for later changes.

## An app has as many RAMs as it needs: the general RAM and the team RAMs

One RAM is one audience and one place a tuple query runs. So what must be queried together, across everyone, goes in
one RAM. What belongs to one team, with its own audience and protection, goes in that team's RAM. **John:** "RangerTrak
is as many RAMs as it needs, but absolutely more than 2." "Getting GPS locations for everybody is a tuple query."

- **The general RAM** is shared by everyone in the app. It holds what everybody reads across everybody, each record
  written by its owner.
- **A team RAM** exists for each team the admin defined at setup. It holds that team's own data. Its audience,
  protection and selection are its own: a team RAM can be placed among that team's nodes, and stays with them through
  a split.
- **Messages are not a role.** General messages live in the general RAM: announcements to everyone, and direct
  messages between people in different teams. Team messages live in the team's RAM. A direct message in the general
  RAM is read only by its sender and addressee, and nothing checks that: it is written into the addressee's own
  variable (RangerTrak: `(rt-dm, <mission>.<to station>, <dev>.<ms>)`), which is what the addressee reads; the sender
  keeps its copy as its own truth. **John, 2026-11-01:** "There's no check to be made."

The application joins the general RAM and the RAMs authorized for its user's teams; devices run applications, they do not join RAMs. The access by team in the app configuration tuples grants each one.
Each RAM has its own selection and its own fail or relocate.

### RangerTrak

| RAM | What it holds | Who writes (their own truth) |
| --- | --- | --- |
| general | positions: each device's own current fix (the map is a query over them) | every device |
| general | the roster: rangers, callsigns, teams | each ranger their record; team leads their team |
| general | the radio log (ICS-309) and its handled marks | each scribe their entries; the handler their mark |
| general | the mission, op periods, assignments | command |
| general | announcements; direct messages between teams | the sender |
| one per team (for an EOC: First Responders, Headquarters, Hospitals, Ambulances) | the team's own data (the Hospitals': capacity, receiving status) and its team messages | that team's members |

A position is a device's own truth and is never derived from the log. A logged position is a scribe's record of what
was said on the radio, and stays in the log.

## How it is built

- **`fdf-app config APP RAM,RAM [--partition relocate|fail]`** is the admin's install on a member: the droplet's
  configuration, `/opt/fdf/apps/<app>.json`. The droplet owns it.
- **`fdf-app serve`** (unit `fdf-app.service`, installed by `fdf-join`) runs on every member. For each app installed
  there it writes its configuration tuple `(fdf-app, config, <app>.<guid>)` and its capability tuple
  `(fdf-app, can, <app>.<guid>)` into the network RAM. When its held read on the `fdf-app` family ends (the network RAM
  may be another one now), it writes both again and reads the family from the start.
- On every event (that held read, and a held read on the members' own cells `(pond, member)` for who is alive:
  `[MEMBER_IS_A_TUPLE_V1]`) it runs each RAM's selection over the alive members that can serve the app (default: most
  memory, then cores, then guid). It starts `fdf-ram` for each RAM it is chosen for, on its own `.1` at a port derived
  from `<app>.<ram>`, and stops the others. It holds each droplet's listening socket and hands it to the droplet
  (`RIBBIT_LISTEN_FD`), so a droplet that exits is started again on the same socket and refuses nothing
  (`[LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]`); the exit itself is the event that starts it again. It writes its where cell
  `(pond, where, w:<guid>)`.
- It writes `/opt/fdf/var/app-where.json`, `{"<app>.<ram>": "host:port"}`, the same answer every member computes.
- **The door on a node, `ram.php`**, maps each service to its app and RAM (`apps.json`: `"app"`, `"rams"`). For each
  request it reads `app-where.json`. A service whose RAM is not served yet is refused with 503: the client connects
  first. Every answer carries `"ram"`, the RAM that answered. A page that sees it change reads everything there and
  writes its own truth again: every family it owns, kept on the device (browser storage), not only those it watches.
- **The door's held reads are events** (`[EVENTS_NOT_TIMERS_V1]`, door r11). `op=watch` is one child, `ram watch`: a
  follow on every family the page shows, ended by the first rows. A family whose RAM does not answer is held until
  that RAM says it is up, or the app's RAMs are placed again: the answer says `"again"` and the page reads that family
  in full. ESTALE (the RAM started again) is answered the same way. `op=rams&not=<map>&up=<host:port,..>&wait_s=S`
  holds a member until its RAMs are not that map (they moved) or one of those RAMs says it is up (`"up"`): RangerTrak's
  `AppCore` waits there when a session fails, and its followers open their sessions again on that answer
  (`Follower.again()`), never on a timer. `op=admin` with `after` holds the same way when the network RAM is down.
- **One held request per page** (`ram.php?op=watch`): every family a page shows is held in one request, and each
  family's held read is a child of it. The answer comes when any family has newer cells or is on another RAM now. A
  page costs one web server worker and one browser connection, however many families it shows. With one request per
  family, a node's workers ran out at three devices: a write waited 5 s for a worker.
- **A team's RAM**: a service mapped to `"team"` in `apps.json` is in `<app>.<team>`, named by the request.
  `ram.php?op=rams&app=APP` lists the app's RAMs served now.
- **Access** [APP_ACCESS_V1]: `fdf-app config ... --login --role NAME[:default] --user USER:PASSWORD=ROLE`. Each
  droplet writes the roles, the users with their password verifiers and the default roles into its configuration
  tuple. A login is `ram.php?op=login` at the device's own edge, which reads those tuples and checks the password there.
  An individual's password activates their role; a default role needs none; with login not required a client may take
  no role. The answer is the role. The device keeps it and writes its access record `(fdf-app, device, <app>.<dev>)`
  (`op=device`), again after every RAM change. Nothing checks a request after that: the app joins the general RAM and
  its role's RAM, as a DM is read only by its addressee because it is in the addressee's variable.
- **The client's focus** [APP_FOCUS_V1]: `index.html` connects, then opens on the role's screen: a role's own RAM on
  its messages, `eoc` on the Summary, no role on the Radio Log.
- **`ram.php?op=whoami`** gives the asking device its host name, from the lease this node gave its address (the node's
  facts, `addrs` kind `given`); a lease with no host name (fdf-facts records the MAC) gives none, and the name typed for
  the device is used instead. That name is the app's station ID. **John, 2026-11-01:** "Station ID is hidden from the
  users. Use the host name or device name." Users see the host or device name, never a station ID.

## Why it works this way

None of this is new machinery. It is the invariants in `AI_READ_FIRST.md` applied to an app.

- **Invariant 9 (no infrastructure separate from the nodes).** The primary is not a controller. It is a droplet the
  admin placed, and its only special job is attachment. The RAM server is served by droplets, which run on members.
  Login is not served by anything: it is a read of the access tuples at the device's own edge.
- **Invariant 3 (authority is elected, not replicated).** The app's RAM server is chosen by the app's own selection
  over the capability tuples, the same way databasehost is chosen for the network RAM. These are two separate
  selections, and each is deterministic, so every member gets the same answer. That covers a split, a merge, and a
  stronger machine joining.
- **Invariant 4a (partitions do not reconcile memory).** Nothing moves at a merge. Owners reassert their truth to the
  current RAM, and anything not reasserted is absent.
- **Invariant 4 (owners write their own truth).** Configuration, access, device records and app state are each
  reasserted by their owner. That is why a device needs no new login after a relocate: it writes its own access record again.
- **Rule 5 (do not complete the architecture).** Nothing needs to know it is primary, detect a split, prove anything to
  anyone, or arbitrate between servers. If a step seems to need one of those, the cycle above already handles it.
