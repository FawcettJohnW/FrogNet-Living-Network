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
| Device | A Windows or Android machine. It attaches through the fabric and uses apps. | A member. It never runs a droplet and is never a candidate for anything. |
| databasehost | The existing, deterministic selection that locates the current **network** RAM, where members read and write. | The app's RAM server. That is a separate selection. |
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
- On every event (that held read, and `fdf-events` for who is alive) it runs each RAM's selection over the alive members
  that can serve the app (default: most memory, then cores, then guid). It starts `rina-host` for each RAM it is chosen
  for, on its own `.1` at a port derived from `<app>.<ram>`, and stops the others. It writes its where cell
  `(pond, where, w:<guid>)`.
- It writes `/opt/fdf/var/app-where.json`, `{"<app>.<ram>": "host:port"}`, the same answer every member computes.
- **The door on a node, `ram.php`**, maps each service to its app and RAM (`apps.json`: `"app"`, `"rams"`). For each
  request it reads `app-where.json`. A service whose RAM is not served yet is refused with 503: the client connects
  first. Every answer carries `"ram"`, the RAM that answered. A page that sees it change reads everything there and
  writes its own truth again: every family it owns, kept on the device (browser storage), not only those it watches.
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
