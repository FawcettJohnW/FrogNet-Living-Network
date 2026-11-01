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
| App tuples | The app's state and configuration, including its access definition and its service locations. | Anything only one machine knows. |
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

## Setting up an app

The admin performs setup at the primary.

1. The admin installs the droplet.
2. The admin defines the app's reach and access:
   - Team.
   - RAM name.
   - Password.
   - Whether login is required.
   - Anonymous, restricted or full access per team.
3. The admin defines the app's behavior when the primary is unreachable:
   - **Fail:** stop unless the primary is visible.
   - **Relocate:** activate an installed droplet on the reachable side.
4. The droplet writes the app's configuration and access definition into the app tuples. Passwords are stored only as
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
9. It reads its app tuples to learn:
   - where its services are;
   - its current shared state;
   - its configuration;
   - its access definition.
10. It connects and operates according to those tuples.
11. It establishes held reads so that changes wake it.

The capability query describes what is currently present. More services can appear as their owners attach and reassert
themselves.

## A Windows or Android device

1. The device attaches through the fabric.
2. It reaches the app's serving droplet.
3. If login is required, the user supplies the team and password once.
4. The droplet checks them against the app's access tuples and password verifier.
5. The device's authorized access is recorded in the app tuples.
6. The device publishes the app truth it owns, then reads the current world through the app.
7. On later connections, whichever droplet is serving authenticates the device against the current access tuples. This
   happens immediately and needs no user action.
8. The user is not asked to log in again.

The device remains a device; it never becomes a Linux fabric member. Its app participation follows the same
write-first, read-second rule.

## Normal running

1. Members and devices publish changes to the truth they own.
2. Running services maintain their capability tuples.
3. Apps read their current app tuples to find their services and shared state.
4. Held reads wake components whose dependencies change.
5. Each component that wakes reads the current values and acts.
6. If a service location changes, the app follows the location its app tuples give.
7. Routing remains the fabric's responsibility.

There is one current network RAM, reached through databasehost. The capability tuples and app tuples are in it. Each
app's RAM server is chosen by that app's own selection over the capability tuples. Every member runs the same
deterministic selection over the same tuples and gets the same answer.

## Split

1. Connectivity changes.
2. The fabric re-forms on each reachable side.
3. Each participant locates the current RAM available through databasehost.
4. Each participant first reasserts the truth it owns into that RAM:
   - members publish their own facts;
   - running services publish their capabilities;
   - app participants publish their owned state;
   - the owners of configuration and access records reassert those records.
5. Participants then query the tuples to learn what is currently present on their side.
6. Each app applies its configured behavior:
   - a **fail** app stops on a side that cannot see its primary;
   - a **relocate** app uses the capability scan and its own selection to activate droplet code already installed on
     members on that side.
7. An activated droplet reasserts its owned truth and publishes its running capability.
8. Apps read their current app tuples and connect to the indicated service.
9. The serving droplet authenticates devices against the app access tuples. Users do not log in again.
10. Held reads keep everyone responding as more participants reassert themselves or connectivity changes again.

Nothing needs to identify the event as a split. Each side operates from its current reachable members and their
asserted truth.

## Merge

1. Connectivity changes again.
2. The fabric re-forms.
3. Participants locate the current RAM through databasehost.
4. Each owner reasserts its own truth before learning the world again.
5. Services publish their current capabilities and locations.
6. App participants reassert their owned state, configuration and access records.
7. The app's selection, run over the merged capability tuples, gives one serving droplet. Every member gets the same
   answer.
8. Apps query the single current app tuple space, which tells each app where to connect.
9. Apps connect to the indicated service and establish held reads.
10. Later changes wake them, and they adapt.
11. Devices authenticate through the serving droplet using the current access tuples, without another user login.

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
  RAM is read only by its sender and addressee; that is the app's access, over who reads which portion.

A device joins the general RAM and the RAMs of its own teams. The access by team in the app tuples grants each one.
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

## Why it works this way

None of this is new machinery. It is the invariants in `AI_READ_FIRST.md` applied to an app.

- **Invariant 9 (no infrastructure separate from the nodes).** The primary is not a controller. It is a droplet the
  admin placed, and its only special job is attachment. Login, the RAM server and access are all served by droplets,
  which run on members.
- **Invariant 3 (authority is elected, not replicated).** The app's RAM server is chosen by the app's own selection
  over the capability tuples, the same way databasehost is chosen for the network RAM. These are two separate
  selections, and each is deterministic, so every member gets the same answer. That covers a split, a merge, and a
  stronger machine joining.
- **Invariant 4a (partitions do not reconcile memory).** Nothing moves at a merge. Owners reassert their truth to the
  current RAM, and anything not reasserted is absent.
- **Invariant 4 (owners write their own truth).** Configuration, access, device records and app state are each
  reasserted by their owner. That is why a relocated droplet can authenticate a device with no login.
- **Rule 5 (do not complete the architecture).** Nothing needs to know it is primary, detect a split, prove anything to
  anyone, or arbitrate between servers. If a step seems to need one of those, the cycle above already handles it.
