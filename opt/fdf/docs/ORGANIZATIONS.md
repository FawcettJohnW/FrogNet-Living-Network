# Multinetwork connectivity and the distributed RAM islands: design (rev 3)

> **Superseded where it disagrees with `APP-SETUP-AND-OPERATION.md` (2026-11-01), which governs apps, RAM servers,
> login, splits and merges.** Sections 7.2 (incumbency), 8 (split and merge) and 9 (islands, the challenge) hold
> proposals that break `AI_READ_FIRST.md` rule 5 and invariants 3, 4a and 9; do not build from them. Setup, levels and login (sections 4 and 5) and the fabric's egress (section 6)
> are still to be reconciled with it.

2026-11-01. John Fawcett, with Claude. Rev 3 replaces rev 2 (2026-10-31). The setup, levels and login from rev 2 still
stand (sections 4 and 5 here). Rev 2's sections 4, 4a and 6 are superseded: the system space's list of RAM managers,
the standby droplets, spaces on the droplet, and failover between RAM managers. Rev 2 is in git history.

Marks: **John** means he stated it. Quotes are verbatim. **Proposed** means it is Claude's and not yet confirmed.
**Open** means it needs a decision from John (section 15 lists them). **Found** means a defect seen in the code while
writing this, named with its file and line.

## 1. The model

> **John:** "Mrm and ram manager are different things. One is a route and one is a role."

> **John:** "Islands belong to the app(s) and may be protected beyond simple connection. Island reach is also
> controlled by the app."

> **John:** "Database is to and ram manager is through."

> **John:** "If the ram manager is gone, all that means is that the Wan is unreachable and the network has split.
> ... The ram manager is either reachable or not. The wg tunnels and the like are part of the connection between the
> client and ram manager."

In that last quote, "ram manager" is the droplet, which John then named the MRM: "The droplet is the mrm".

The design has three parts, and each has one job:

| Part | What it is | Where it runs | What it holds |
| --- | --- | --- | --- |
| **MRM** (multinetwork route manager) | The point every member attaches to. Its one special job is attachment: admission and login. Routing is the fabric's (the nodes, their tunnels and their plans) and is transparent to it, as to every server. | The organization's droplet, designated by the admin. There is exactly one per network. | Attachments, and the memory its members write. No application data. |
| **RAM manager** | A role. It holds one island's memory. | A member node that the island's app elects, from the capability facts. | One island's cells. |
| **Island** | One app's shared memory. The app decides what it means, who may use it, how far it reaches and how it is protected. | Wherever the role is held right now. | The app's data. |

One cycle runs all of it: discovery, routing, placement and recovery.

1. Members write their current facts into the reachable control RAM: the MRM, or during a split the LAN's control host
   (section 8).
2. Held reads wake the routing and role handlers when those facts change.
3. Each handler acts on the world it holds now. It chooses an exit, keeps an eligible incumbent, or elects a
   replacement.
4. A serving host writes its where cell.
5. Apps follow that cell, reconnect when it changes, and write their own truth again.
6. A split or a merge changes the facts, and the same cycle runs again.

The cycle needs neither complete information nor a pause for agreement. Facts that arrive at different times are just
more changes to act on.

A node reaches the MRM *through* its single egress. It reaches an island *to* the island's role host. On a node's own
LAN that is a LAN route. On another LAN it goes through the MRM. The MRM carries the packets and never sees the island.

## 2. Words

| Word | Meaning | Source |
| --- | --- | --- |
| MRM | The multinetwork route manager: the droplet's `fdf-ram` region, its hub (`fdf-hub`, 10.253.253.254), its mappings, `fdf-org` (admission and login). One per network. | John |
| Network (area, pond) | One FD&F region: one MRM, its members, their /24s. A machine has one forward pond and may be in participant ponds. Reach between ponds is by each pond's `(pond, config, net)`, and colliding addresses are aliased 1:1 into 100.64.0.0/10. That is the "multinetwork" in MRM. | Tree (`VERIFY-2026-10-11.md` "Ponds") |
| Member | An admitted node or device. It is named by identity, `w:<guid>` (a node) or `d-<hash of key>` (a device), never by address. | Tree |
| Egress | A member's one path to the MRM: its own uplink, or the exit host the plan chose for it. | John ("single egress") |
| Split | The MRM is unreachable from some members. Each side goes on as its own network. | John |
| Merge (heal) | The MRM is reachable again. A split and a merge are the same operation: the network is re-evaluated. | John |
| Role | A function that an election puts on one member: `databasehost`, and one role per island. | John |
| RAM manager | The role that holds an island's memory. It is a different function from the MRM. The MRM machine is never a candidate for the role: "Databasehosts are members of the network. Ram hosts are not" (the droplet, in John's naming at the time). | John |
| Island | An app's shared memory. One role per island. | John |
| Where cell | `(pond, where, w:<guid>)` with a bag like `{"<island>": "w:<guid>:<port>"}`. It says where a member serves something. The region removes it when that member's session ends. | Tree (`fdf-comms.sh`) |
| Capability fact | `facts.capability`: a member's cores, installed memory, disk class and services, written by its daemon. Elections read it. | Tree (`[CAPABILITY_IS_A_FACT_V1]`) |
| Level | A group in the customer's own words. Admission gives it network reach: the reach of the devices connected at that level. For almost everything it is **restricted**; the only reason to change it is an admin who wants to ssh into the machines. It is not an app's access: which RAMs a team uses is the app's. | John (2026-11-01: "For almost everything, that is going to be Restricted." "The only reason to change it would be if the admin wanted to ssh into the machines.") |
| databasehost_control | The highest live `.1` on a LAN. During a split it holds that LAN's facts (section 8). | John (2026-06) |

**Found (naming):** "island" also means something else in the tree today. In `Bridge.kt` and `fdf-tun`, an "island" is
a private network aliased from 100.64.0.0/10. That has nothing to do with app islands. **Proposed:** rename that use
to *alias network* (`AliasNet` in the code). From here on, "island" means only an app island.

## 3. The MRM

### 3.1 What it holds

- **Facts:** each member's announced rows (`facts`: name, guid, pubkey, subnet, addrs, uplink rows, capability,
  hub row). Each member writes only its own facts.
- **Mappings:** the /24s each member registers, signed toward its live hub channel.
- **Admission:** levels, users, the door, edges and reach rules (`fdf-org`; the permanent cells `(fdf-org, level/user/door, ...)`
  and the hub's `admitted/NAME.json`).
- **Where cells:** one per serving member, scoped to that member's session.

It holds no application data. **John:** "The ram manager owns the actual data, but that is not the droplet."

**Found:** the radio log pages write their cells (`rt-mission`, `rt-log`, `rt-handled`, `rt-roster`) to `RINA_RAM`.
That is the MRM, so they break this rule. `ram.php` proxies to whatever `fdf.env`'s `RINA_RAM` names. Section 10
moves them.

### 3.2 One MRM, reachable or not

> **John:** "You only have one ram manager in this case. We can have a failover ram manager, but that has to be a
> global failover, which is impossible."

So:

- A join line names one MRM. `fdf-org line --with` (a standby's line inside the primary's) goes.
- `system-space.json` (a list of RAM managers) goes. `hub.json` is enough.
- A device holds one tunnel. With one hub there is nothing for per-tunnel metrics or `SO_BINDTODEVICE` ordering to
  choose between, so `[RAM_ON_ITS_OWN_TUNNEL_V1]` shrinks to "the one tunnel".
- Nothing ever "fails over to another MRM". A member that cannot reach the MRM is on the other side of a split
  (section 8).

### 3.3 The MRM restarting

A restart is a split of every member at once, followed by a merge. Permanent records (levels, users, admissions and
the region file) survive `kill -9`; this was measured earlier ("5b, the region host restarts"). Session-scoped cells
do not survive: facts, mappings and where cells come back from their owners, each of which writes its own again on
reconnect. Islands do not stop: each island's RAM manager runs on a member, and the members on its side keep using
it (section 8). Only reach that goes *through* the MRM stops while it is down.

## 4. Setup: a region, defined whole

**John, 2026-11-01:** "When I make a region, I need to define everything about it. This includes reach, levels, names
-- everything." "Make defaults for everything, but it all must be configurable, including levels, addresses, and
passwords." "The UI can be a front-end for fdf-setup." "This needs to be a program, not a site."

The manager unpacks the region-holder bundle and runs `/opt/fdf/bin/fdf-setup` (`[FDF_SETUP_V1]`). It defines the
region: its name, the address and ports members reach it at, its levels (each one's password or none, reach and
ports), its users and passwords, and one join line per level. Every item has a default (`fdf-setup --defaults` prints
them) and every item can be changed:

- the wizard: each question shows its default; Enter keeps it;
- `fdf-setup --define FILE`: a whole definition (JSON), no questions. A front-end program sends this.

The defaults: the region named after the machine; the machine's own address; RAM manager port 51888 and hub port 50889
(the next free pair when another region here has those); levels Restricted (no password, reach the region only) and
Admin (a password, reach open, for an admin who wants ssh); a user admin at Admin, whose password is asked for.

**A machine holds as many regions as it is given** (`[REGIONS_PER_MACHINE_V1]`; **John:** "A machine may host more than
one ram segment, as long as they're on different ports."). Each region is `/opt/fdf/hub/<AREA>/` (hub.env, admitted/,
org-key, hub.key) with its own units, `fdf-ram-AREA`, `fdf-hub@AREA`, `fdf-client-AREA`, `fdf-identity@AREA` and
`fdf-org@AREA`, and its own reach table, `inet fdf_org_<area>`. Its ports are the ones given; one another region here
uses is refused. The hub address is a /32 on each region's interface, with a host route to every admitted edge, and
edges come from one pool for the machine, so two regions' tunnels never route the same address. A region an earlier
build made moves into its own directory with `fdf-region-create --migrate` (or when a second region is made).
`fdf-invite`, `fdf-org` and `fdf-setup` take `--area AREA` when the machine holds more than one.

Tested by `netns-regions-test.sh`: two regions on one machine, each defined by `fdf-setup --define`; a device in each
reaches its own region and not the other.

Changes in rev 3:

- **Removed:** the wizard's step "the RAM spaces, all at once, with their RAM-manager lists" and "a standby's lines"
  (`netns-setup-test.sh`, which tested them, is replaced by `netns-regions-test.sh`).
  The spaces become islands, and islands are the app's (section 9). An app's islands arrive with its add-in, not from
  the wizard.
- **Gone:** the level→"spaces its applications join" choice. A level is the network's admission and reach only. Which
  RAMs an app's team uses is the app's own (`APP-SETUP-AND-OPERATION.md`: the general RAM and the team's RAM). **John,
  2026-11-01:** "The access levels for the network are not related to the ram that the roles use."

## 5. Admission and login (from rev 2, simplified to one MRM)

### 5.1 Unchanged

- Login order (`[LOGIN_RESTRICTED_FIRST_V1]`, **John**):
  1. The generic credential, for one exchange at the door: the device's own key in, its own edge out.
  2. The generic tunnel down, the device's own tunnel up.
  3. Login on the level's login port; the password is checked.
  4. The edge gets the level's reach.
  5. A session on the level's session port.
- Both exchanges are one JSON line each way over TCP inside the tunnel. A password is never put in a cell.
- A device's name comes from its key (`[NAME_FROM_KEY_V1]`).
- Network reach is enforced by nftables on the MRM, per edge (`[REACH_PER_EDGE_V1]`, one table per region, `inet fdf_org_<area>`).
- A node joins with one command, its level's line (`[ORG_NODE_JOIN_V1]`, `fdf-region-join`).
- An Android phone joins with the same line (`[LOGIN_IS_AN_INVITATION_V1]`, 2026-11-02, John: "the QR is for the EOC";
  `OrgLogin.admit` in frognet-android's member, `fdf-member-kt admit LINE` on the JVM): steps 1-3 at the line's first RAM
  host, then the login is used exactly as an invitation would be -- the hub's tunnel with the phone's own key and the
  edge the door gave, the region at the level's session port inside it. The phone keeps only the line (and the level's
  user and password when it has one). Gate: `rinab/tools/netns-admit-kt-test.sh`.

### 5.2 Changed

- **Admission comes with the hub, wherever it is** (`[ADMISSION_WITH_THE_HUB_V1]`, 2026-11-02, **John:** "I should call
  the hub wherever it is and have it create the credentials, then point the other code at that hub and it works.
  Having it depend on being in any one specific place is stupid"). `fdf-hub@AREA` wants `fdf-org@AREA` and
  `fdf-identity@AREA`, and `fdf-region-create` enables both. `fdf-org serve` makes the credential key, the generic
  credential and the door the first time, and writes where the hub is as `(fdf-org, hub, AREA)` {name, pubkey,
  endpoint, addr}. On any machine in the region, `fdf-org level`, `space`, `line` and `list` are tuples in the
  region's RAM: a level written there gets its ports from the hub's `fdf-org` on the tuple's event, and a line is made
  from the hub's row, so it points at the hub wherever it is now. `fdf-org user` (a level's password) still runs
  where the hub is: the verifier is under the hub's own key. The hub is "here" only when its interface is up here
  (`[HUB_IS_WHERE_IT_RUNS_V1]`): a region directory left from an earlier install does not make a machine the hub (found
  on Seattle7 2026-11-02). Gate: `rinab/tools/netns-admit-kt-test.sh` checks 0.

- ~~**One MRM per line.**~~ Superseded 2026-11-02 (**John**, `APP-SETUP-AND-OPERATION.md`, "Roles, the resilient
  EOC"): the line carries the admin's ordered list of EOC targets, and `fdf-login` logs in at each it can reach
  (`[LOGIN_WHERE_IT_CAN_V1]`). What follows in this bullet was written for one MRM: `login.json` holds one edge and one session, not a list. `fdf-login --node` writes `hub.json`,
  `site.env` and `join.env`; it no longer writes `system-space.json`.
- **The login's answer no longer lists spaces.** It says the member's level and that level's network reach.
- **The primary only attaches.** It is the primary because the admin designated it, by naming it in the join line, and
  for no other reason. It does not know it is the primary. Its one special job is attachment: admitting a device and
  logging it in. It takes every connection. It does not route. Routing is up to the fabric (the nodes, their tunnels
  and their plans) and is transparent to every server, the primary included. It knows nothing about islands, services,
  nodes' state or where anyone attaches from. Who may use a service is checked by the service, against the login
  record (section 9.3).

## 6. Egress: every member's one path to the MRM

> **John:** "All nodes have a single egress to the ram host, and that is used for routing."

### 6.1 How the egress is chosen

The egress is the plan's (`fdf_graph.Graph.exit_of`, `[EXIT_RANK_V1]`). John's rule is: the host itself if it has a
live uplink; otherwise the LAN-reachable host with the fastest uplink, and on a tie the lower `.1`. The rule ranks by
speed, not by hops.

**Proposed:** the plan also returns the ranked list (`exits`). It uses the same key, so `exits[0] == exit_of(me)`. The
loss path walks that list.

### 6.2 What carries it

A child with no uplink of its own reaches the MRM's public endpoint through its exit. Today that path is the kernel's
default route. `[DEFAULT_IS_FROGNETS_V1]` says FrogNet's exit ladder owns the default route, and the applier sets one
only when none exists.

**Found** (`netns-c-behind-ab-test.sh`, red):

- C holds DHCP defaults through A (metric 600) and through B (metric 601).
- A dies with its links up. The kernel keeps the 600 route.
- No runMerge runs on a region event (`[NO_MERGE_ON_REGION_EVENTS_V1]`), so FrogNet's `fixDefaultRoute` never runs.
- C's tunnel stays pointed at a dead host.

**Proposed** (one route, no fight with FrogNet's default):

- The applier owns exactly one route: a /32 to the MRM's endpoint, via the plan's exit, at a metric below DHCP's. That
  route is the egress.
- FrogNet's default route stays FrogNet's.
- When the egress moves, the applier calls FrogNet's own default fixer (`discovery/fixdefault.py`,
  `purge_dead_defaults`) with the new exit, as an event. FrogNet still sets the default; it now hears about the event that moved it.

**Open (Q2):** confirm this split of ownership.

### 6.3 Loss of the egress

John's rule, applied to the egress (`[NO_FLAP_V1]`):

> **John:** "You know within milliseconds that the socket to upstream has closed. You should immediately switch to the
> backup. Within milliseconds, not seconds. Then we don't do shit to go back up until this one dies. No flapping
> around."

**Proposed:**

1. The member's session to the MRM closes. The time to notice depends on how it ends:
   - an RST (the far side's process or kernel answers): milliseconds;
   - a black hole (a host died with its links up): TCP keepalive, measured at 4.8 s.
2. At once, the egress goes to the next entry of `exits` from the last world it held, skipping the exit just lost. The
   /32 is replaced and the WireGuard handshake through the new exit is the only question asked. With wireguard-go
   that measured about 110 ms of a 255–274 ms switch.
3. If that handshake fails, try the next entry. If none answers, the MRM is unreachable from here: that is a split
   (section 8).
4. A better exit appearing later changes nothing while the current one lives. The new plan's `exits[0]` is used only at
   the next loss.

   **Superseded for spaces, John 2026-11-03:** "There is no fallback list. Fallback is determined by capability";
   "Designation and failover/failup are automatic." A device's space is on the best alive RAM host by capability, and
   a better one coming back takes it again ([SPACE_BY_CAPABILITY_V1], `APP-SETUP-AND-OPERATION.md`). The egress (this
   section, proposed, not built) follows the same rule when it is built.

This removes standing sessions to standby RAM managers and the probe tunnels (`fdfsp<N>`). There are no standbys any
more. The only alternatives are exits, and the handshake through one is the probe.

### 6.4 Devices

A phone or a PC has one egress: its own tunnel to the MRM. If it sits on a node's LAN as a DHCP guest, the egress is
that node's. Losing it is a split for that device (section 8).

## 7. RAM roles

### 7.1 The election (as it is, and kept)

- Every node runs the same pure function over the same input, so every node gets the same answer:
  - the input is the alive members' capability facts (`[ELECTION_FROM_FACTS_V1]`, `core/region_capability.py`);
  - the handler is the role's (`core/role_registry.py`, `ROLE_HANDLERS`);
  - `evaluate()` is pure: no probes and no I/O.
- The ranking uses only static inputs (`[DBHOST_STATIC_RANK_V1]`): installed memory, innodb pool, disk class, cores ×
  clock. Load and free space never count. Winning a role must not change the score that won it (the Seattle5 feedback
  loop, 2026-08-08).
- The election runs in-process, off the loop, on member loss, member arrival or a capability change
  (`[ELECT_IN_PROCESS_V1]`, `[ELECTION_OFF_THE_LOOP_V1]`).
- The answer is written as `<role>.frognet <ip>` in `/etc/hosts` (`service_host_lines`).

### 7.2 Incumbency

> **John** (routing law, carried to the data host): a healthy incumbent is held; "no challenger displaces a healthy
> incumbent at any score margin" (`[DBHOST_INCUMBENCY_HOLD_V1]`).

**Found:** in region mode, nothing holds the incumbent.

- `discovery/elect.py:elect_now` builds `etc` fresh from `local_lines` + `addhost_lines` (lines 100–128). Neither
  writes a `databasehost.frognet` line.
- So `select_database_host` (`discovery/hosts.py:493-499`) finds no incumbent.
- `service_host_lines` → `DatabaseRoleHandler.evaluate` takes no incumbent at all.
- Result: a stronger member joining, or a capability fact changing, moves the role.

For a role that holds an island, a move means a different host serves the island. Memory is not moved: every owner
writes its truth again to the new host. A move with no failure behind it is the flap John ruled out.

The incumbency in LAN mode is also per-node: it is read from that node's own `/etc/hosts`. After a heal, the two sides
hold different incumbents, and each side keeps its own. The network then has two hosts for one role.

**Proposed** (`[ROLE_INCUMBENT_FROM_WHERE_V1]`):

- The incumbent is the member whose where cell says it serves the role. It is read from the shared facts, not from any
  node's own files. Every node sees the same where cells, so every node holds the same incumbent.
- `evaluate(candidates, incumbents)`:
  - exactly one incumbent, and it is still eligible: that incumbent;
  - several incumbents (a heal): the best of the incumbents, by the handler's score, then the higher `.1`;
  - none: the best candidate.
- A challenger never wins while an incumbent is eligible.

### 7.3 The role host's duties

The role host is the member that the election names for island `I`. **Proposed:**

1. It starts the island's RAM manager: `fdf-ram`, with the island's Permanent file `island-I.perm` if the app asks for
   persistence (section 9). It listens on a port of its own.
2. It writes its where cell, `{"I": "w:<guid>:<port>"}`. The cell lives with its session.
3. It answers connections only after the challenge (section 9.3).
4. It loses the role in one of two ways: it is no longer alive or eligible, or a merge exposes several incumbents and
   another one wins (section 8.2, step 3). Either way it stops that RAM manager. Its memory is not moved (**John**: "Memory is never moved"). Owners write their truth again to the new host.

## 8. Split and merge

> **John:** "If the ram manager goes away that is a network event and the network needs to reevaluate."

### 8.1 A side without the MRM

A member that cannot reach the MRM (section 6.3, step 3) is in a partition. The partition re-evaluates as a network
of its own.

**Facts.** A partition has no MRM, so it needs somewhere to hold its facts. John has said where:

> **John** (2026-06): "On a split, the _control database is consulted for the new databasehost selection."

> **John** (rev 2, section 4): the LAN's machines from the highest IP (databasehost-control) down; "a machine is never
> without a RAM manager".

**Decided (Q1, John 2026-11-07: "Drop to LAN-only: run runMerge"), `[SPLIT_IS_LAN_ONLY_V1]`:**

- A split is a network event. The member withdraws the region's routes, names and tunnels and runs `runMerge`: the
  partition is LAN-only FrogNet (discovery, the elections, `/etc/hosts`), as a node with no region is.
- Its facts and roles are the LAN's own: `databasehost_control.frognet` and `databasehost.frognet` are the hosts the
  LAN's elections name, each that host's `api.php` on port 80 (`[ROLES_ON_API_V1]`). There is no LAN RAM manager, no
  `fdf-ram` on 8841 and no walk list (`[LAN_RAM_ALWAYS_V1]` and `lan-rams.json` are gone).
- The member keeps a standing session to every entry on the region's list; one answering is the event that rejoins.

**Roles.** The partition elects every role again over its own alive members' capability facts. If an island's host is
inside the partition, the incumbent is held and nothing moves. If it is outside, the partition elects a host of its own
and the island starts there, empty, unless the app persists it. Members write their own truth again.

### 8.2 The merge

A member of the partition with an uplink keeps its session attempt to the MRM standing. The only timer is WireGuard's
own handshake retry (5 s); a link or DHCP event also retries at once. When it reaches the MRM:

1. It moves to the MRM and writes its facts there.
2. Its new uplink fact is an event for the partition: the others take it as their exit (section 6) and follow.
3. The merged network elects again (section 7.2):
   - one incumbent per role is held;
   - two incumbents resolve to one, by score then `.1`;
   - the losing host stops its island's RAM manager, and its members write their truth again to the winner.
4. Reconciling the two sides' data is the app's (RIBBIT.md Part I: "If two regions split and later meet again and the
   application requires reconciliation, the vendor defines the reconciliation semantics"). The radio log already does
   this: every device writes its own entries again where they are missing (`[OWN_TRUTH_AGAIN_V1]`; measured 7.1–7.5 s).

### 8.3 A device alone

A phone with no tunnel and no LAN is a partition of one. A device is never a role candidate (`[ELECT_NODES_ONLY_V1]`).
Whether its app runs on a local RAM until it is back is for the app's selection code to say. The radio log does run on
one, as tested. RangerTrak's says a team's RAM is on its lead's phone (`[TEAM_HOST_V1]`, 2026-11-02): the lead's phone
runs the Ribbit platform's RAM host (`libteamram.so`) for the phones on its hotspot, and says where it is in the app's
general RAM. That is the app's RAM on a device, not a role: the device is still never a candidate for the network's.

## 9. Islands

### 9.0 Services, the install bundle and the login record

> **John:** "There is only one full network server, and that is identified. Below that are how it works under partition.
> Options are fail, relocate. Fail means the service stops working unless it can see the primary. Relocate means the
> droplet code is installed as part of every client install and is activated by the capability tuple scan."

> **John:** "These servers are different than the primary service used for routing. This needs the network up."

> **John:** "Apps decide what happens to them after a split."

> **John:** "The app needs to get a bundle from the unpartitioned server at the time it is installed on the lan device (a
> windows or android device cannot be a droplet)".

**Two layers.**

1. **The network.** The MRM is the one primary for routing, by the admin's designation alone, and never relocates. In a split, the partition's routing
   comes up on its LAN control host (section 8.1).
2. **The services.** These are an app's servers: its RAMs and the login. They run on the network and need layer 1 up
   first.

**Each service's partition mode, set by the app.**

- **Fail:** the service works only while the client sees the primary.
- **Relocate:**
  - The droplet code for the service ships in every node install, dormant.
  - A node advertises in its capability tuple that it can run it.
  - When a partition cannot see the primary, the capability scan over the partition's facts elects one node. That node
    activates the service and writes its where cell, and clients follow it.
  - It is a role, held by the incumbent rule (section 7.2), and never a dedicated server.
  - Windows and Android devices are clients only and never candidates (`[ELECT_NODES_ONLY_V1]`).

**Keys.** The organization's signing key is on the primary, and only there. It moves with the primary when the droplet
floats. Every other machine holds only the organization's public key. Nothing on a LAN can sign.

**The install bundle.** A node gets it from the unpartitioned primary when the app is installed on it. It holds:

- the organization's public key;
- the app's service table: each service's RAM name and partition mode (not the network's levels: see section 4);
- this node's own record: its id, its public key, and the services it may host when relocated, with an expiry.

The service table and the node's record are signed by the organization key, and each carries a version number. While
the primary is reachable, newer versions arrive as facts, and a node keeps the newest.

**The login record.** Every device logs in at the primary: a node, a Windows PC or an Android phone. It receives a
record signed by the organization key:

```
device id, the device's public key, user, team, levels (comma separated),
the services and RAMs it may reach and how, issued, expires
```

The device keeps the record. Its private key never leaves it.

**Why a LAN can still authenticate.** A relocated service checks the login record with the organization's public key,
then has the device prove it holds the private key named in the record (section 9.4). It needs no secret and nothing
online. A stolen node yields nothing that works against another device. A key derived from the device id would need the
master secret on every LAN service. The fnid is public (`frognet_reset.sh`: "/etc/fnid is not a secret").

**What cannot happen in a partition.**

- A node cannot tell a split from a primary that does not answer. Its install does not try to: it completes, and the
  node runs on its LAN. The bundle comes with the node's login at the primary. That login is retried on the next event
  that could make the primary reachable (a link, a DHCP lease, an uplink fact), and is never replaced by anything else.
  Until it succeeds, the node holds no bundle and is not a candidate for any relocated service. It does not know why,
  and it does not need to.
- A device that never logged in at the primary has no record, so no relocated service admits it.
- Gone is gone: there is no second way in.

**Revocation and expiry.**

- A revocation is a newer signed version, and it spreads as a fact.
- As built ([REVOKED_EVERYWHERE_V1], 2026-11-03): `fdf-org revoke NAME` writes `(fdf-org, revoked, NAME)`, which every
  RAM host of the organization adopts from the definitions in the network RAM ([ORG_OWNED_V1]); each drops the
  admission and peer, and its door refuses the key. See APP-SETUP-AND-OPERATION.md, open item 4.
- Inside the partition where it was made, it takes effect at once; elsewhere, at the merge.
- A record issued before a split stays good on the other side until it expires. The expiry length is the admin's
  trade-off.

**Merge. Open (Q8).** When the primary is visible again, does a relocated service stand down at once, with its clients
going back to the primary and writing their truth there? That would be the one failback, like leaving the LAN fallback.
Or does it serve until it dies?

### 9.1 Owned by the app

> **John:** "Islands belong to the app(s) and may be protected beyond simple connection. Island reach is also
> controlled by the app."

An app's add-in declares its islands. **Proposed:** in its `apps.json` entry:

```json
"islands": {
  "rt-eoc":      { "select": "rt_islands.py:Eoc",      "persist": true,  "access": "rt_islands.py:EocAccess" },
  "rt-hospital": { "select": "rt_islands.py:Hospital", "persist": true,  "access": "rt_islands.py:HospitalAccess" }
}
```

- **select** is the app's selection code: a role handler, `evaluate(candidates, incumbents)`, pure, over the capability
  facts. It is loaded into `ROLE_HANDLERS` beside `databasehost` on every node that has the add-in. A member without the
  add-in is not eligible: its capability fact lists the apps it has, and a missing app is a hard gate.
- **persist:** whether the island's RAM manager keeps a Permanent file. Persistence is the app's choice (RIBBIT.md
  Part I).
- **access:** the code that applies the login record's levels and the service table's reach for this island: who may
  connect, and who may read and write which portions (section 9.3).
- **partition:** `fail` or `relocate` (section 9.0).

### 9.2 One role per island; separation by the app's code

> **John:** "separation of the role across different physical hosts is defined by the vendor selection code" ("app",
> by his correction).

> **John:** "in the ranger thing we have different ram islands for eoc vs hospital. The databasehost for the role has to
> be set and the app has to know where it is, and that's done through the capability tuples and the vendor selection".

- Each island is its own role, elected on its own.
- Two islands can land on one host, or the app's code can keep them apart. For example, `Hospital.evaluate` can drop
  the candidate that `Eoc` names; it may read the other islands' incumbents, which are passed in.
- Placement by site needs a site fact. RangerTrak's EOC island belongs at the EOC and the Hospital island at the
  hospital. **Proposed:** `facts.site`, from the node's join (its level or a site name typed at install). **Open (Q5).**

### 9.3 Connecting to an island

> **John:** "The role is automatically authenticated by network membership, plus a challenge when connecting to the
> server with the role".

In order:

1. **Where.** The member reads the island's where cell and resolves `w:<guid>` in its own scope (the region's
   `resolve` op: a `.1`, or `ENOENT` if that member is gone).
2. **Route.** On the member's LAN, a LAN route. Otherwise through the MRM, where network reach must allow it (section
   5.2).
3. **The login record.** The connector presents it. The host checks the organization's signature and the expiry.
4. **The challenge** (`[ROLE_CHALLENGE_V1]`, section 9.4): the connector proves it holds the private key the record
   names.
5. **The app's own check.** The island's `access` applies the record's levels and the service table's reach, and
   decides whether to admit the device and what it may read and write. A Hospital island can refuse a field phone of the Responders
   level even though the network reaches it. That refusal is island reach, and it is the app's.

### 9.4 The challenge, and its review

**What it must be.** These requirements follow from John's sentence ("automatically authenticated by network
membership, plus a challenge when connecting to the server with the role"):

1. **The login record is the authentication.** The primary checked the user and signed the record. At connect, the
   library answers the key proof below, and nobody types anything.
2. **The challenge proves the connection is that member.** Inside a WireGuard tunnel, the source address already is
   the key: the hub accepts an edge's packets only from that edge's key. On a LAN, an address proves nothing. Any host
   on the wire can take one. The challenge has to bind the connection to the member's key on every path.
3. **The role host asks.** It is the one that must be sure. It checks the record with the organization's public key
   and takes the device's public key from the record.
4. **It must work in a split.** The record and the organization's public key are all it uses. It needs nothing
   online.
5. **The rest is the app's.** Which device may read or write what is the app's `access`, over the record's levels
   and the service table (section 9.3, step 5).
6. **No fallback.** A connection that does not answer the challenge is closed and logged. There is no unchallenged
   mode.

**The mechanism. Proposed.** It uses no new keys. Every node and device already holds an X25519 key, its WireGuard key.
Its public half is in the device's login record, and a node's is also in its record in the bundle.

- The connector opens with its member name and a nonce `Nc`.
- The server answers with its own nonce `Ns` and `HMAC(K, "s" ‖ Nc ‖ Ns ‖ island ‖ server guid ‖ member)`.
  `K` is `X25519(server key, member public key)`, which both sides can compute and nobody else can.
- The connector checks that answer, so it knows it is talking to the member whose where cell names it.
- The connector answers `HMAC(K, "c" ‖ Nc ‖ Ns ‖ island ‖ server guid ‖ member)`. The server checks it.
- Then the server runs the app's `access`.
- Both nonces are fresh per connection, so neither answer can be replayed. The island and the server guid are in both
  answers, so an answer cannot be relayed to another island or another host.

**Review.**

| Case | Result |
| --- | --- |
| A host on the LAN with no login record | Nothing to present: fails, logged |
| A forged or altered record | The organization's signature fails |
| A copied record on another machine | It cannot prove the private key: fails |
| An expired record | Refused |
| A revoked device | A newer signed version spreads as a fact. Inside its partition it takes effect at once; across a split, at the merge or at the record's expiry, whichever comes first |
| A replay of a captured exchange | Fresh `Ns`: fails |
| A relay between two islands or two hosts | The island and the server guid are bound in: fails |
| A host answering at the elected host's address (stale ARP, a spoofed `.1`) | It cannot produce the server's answer: the connector refuses it |
| A stolen device (its key and its record) | Passes until revoked or expired; the expiry length is the admin's trade-off |
| A legitimate member the app does not want | Passes the challenge; refused by the app's `access` |
| Cost | One round trip and one X25519 operation on each side, per connection; nothing per request |
| Not provided: confidentiality after the challenge | Off a tunnel, the session is plain TCP, as member sessions are today. `K` can key the session (ChaCha20-Poly1305, as WireGuard uses). **Open (Q6)** |
| A service table change | A newer signed version spreads as a fact; the next connection is decided by it. **Open (Q9):** are sessions already open re-decided at once? |

## 10. Apps

- **The library.** `fdfspace.Space` and `Space.kt` become `Island(name)`. It follows the island's where cell with a held
  read and holds one connection to whoever the cell names. When the cell changes, it reconnects, re-arms the app's
  reads and calls the app's handler, which writes its own truth again. There are no candidate lists, no standing
  sessions to standbys and no fallback list. The app's selection code is the only chooser.
- **Node-served pages** (`/rt/`, `ram.php`). `ram.php` takes `service` → island from the add-in (`rt-*` → `rt-eoc`
  for the EOC post) and proxies to the island's host from the where cell, not to `RINA_RAM`. Its answer's `ram` field
  (`[RAM_IS_SAID_V1]`) is the island host, so a page that sees it change already reads everything and writes its own
  entries again (`[OWN_TRUTH_AGAIN_V1]`). That code does not change.
- **The Communicator** already works this way (its where cell, `comms`). It is the model for the rest.

## 11. What happens when

| Event | Seen by | What happens | Time |
| --- | --- | --- | --- |
| An exit dies (process or kernel RST) | Its children's sessions | The next exit; the /32 moves; a handshake; FrogNet's default fixer is told | ~270 ms measured, incl. ~110 ms WG |
| An exit dies with its links up | Its children's TCP keepalive | As above | ~5 s, then as above |
| A better exit appears | The plan | Nothing until the next loss | — |
| An island host dies | The MRM (its session), then every member's event | Its where cell goes; the island's role is elected again; the new host starts the RAM manager and writes the cell; apps reconnect and write their truth again | One event plus the election (ms) plus the RAM manager's start |
| A stronger node joins | Election | Nothing (incumbent held, section 7.2) | — |
| The MRM dies or restarts | Every member | A split everywhere: each LAN on its control host; islands keep running where their host is; cross-LAN reach stops | Detection as above |
| The MRM is back | Exits' handshakes | The merge (section 8.2) | Not measured. An exit retries its handshake every 5 s, which bounds when the first member reaches the MRM, not when the merge completes (the facts, the elections and the apps' re-writes follow) |
| A site's WAN cut | That site's exit | The site is a partition; its islands go on; islands elsewhere are elected again inside it if the app's code allows | As above |
| A device's tunnel drops | The device | A partition of one (section 8.3) | As above |

## 12. Code: keep, change, remove

**Keep:**

- `fdf-org` admission, levels, users, the door, reach (`[REACH_PER_EDGE_V1]`).
- `fdf-login` (one MRM), `fdf-setup` (minus spaces and standbys), `fdf-region-join`, `fdfjoin1` lines.
- `fdf_graph` (plus `exits`), `graph_apply`, the region loop's event path.
- The elections from facts. `DatabaseRoleHandler` scoring.
- `fdfram.py` (`_Bound` stays for the one tunnel).
- `Join.kt` (one MRM).
- `[RAM_IS_SAID_V1]`, `[OWN_TRUTH_AGAIN_V1]`. `rules.js` and the pages.

**Change:**

| File | Change |
| --- | --- |
| `fdf_graph.py` | `plan()` returns `exits` (ranked) |
| `graph_apply.py` | The egress /32 to the MRM endpoint via the exit; on change, an event to FrogNet's default fixer |
| `poll.py` `run_region_loop` | Loss: walk `exits`, then split onto the LAN control host; merge on reaching the MRM. No `walk` across RAM managers, no `standing` |
| `region.py` | Keeps `own_dot1`, `ensure_lan_ram` (the control host's), `follow`. `lan_fallback` → `control_host()` (the highest live `.1`) |
| `discovery/elect.py`, `discovery/hosts.py`, `core/frognet_service_hosts.py`, `core/database_handler.py` | `[ROLE_INCUMBENT_FROM_WHERE_V1]`: `evaluate(candidates, incumbents)` |
| `core/role_registry.py` | Loads the installed apps' island handlers beside `databasehost` |
| The role host | Starts and stops an island's `fdf-ram`, and writes its where cell (new: `fdf-island`, a child of the tunnel daemon) |
| `fdf-org serve` | Reach to island ports from where cells and level grants |
| `fdfspace.py`, `Space.kt` | → `Island` |
| `ram.php` | Island host from the where cell |
| `Bridge.kt`, `fdf-tun` | "island" → alias network |

**Remove:**

- `system-space.json`.
- `region.Standing`, `answers`, `_probe_up` and `fdfsp<N>`.
- `system_managers` as a list.
- `save_lan` as a walk list (`lan-rams.json`). Done 2026-11-07 (`[ROLES_ON_API_V1]`), with `ensure_lan_ram` and the
  LAN RAM port.
- `fdf-org space`, `level --space`, the space ports, spaces in the login answer, `line --with`.
- `SpaceAt` and `loginAll` across managers in `Join.kt`.
- `fdfspace.from_login`.

## 13. Tests

The incumbent tests decide: `simulation/run_all.py` tiers, `discovery/sim/service_election_check.py`,
`databasehost_convergence_check.py`, `test_service_host_consistency_oracle.py`, `role_registry_lxmlfree_check.py`
(which asserts one role, so it changes when island roles are added; that change is to be named in its commit),
`fdf/lib/python/fdf_graph_oracle.py`, `internet_tunnels_v3/tests/graph_apply_oracle.py`, `test_region_oracle.py` and
`test_rt_pages.py`. Each change runs them before and after.

| Test | Status |
| --- | --- |
| `netns-c-behind-ab-test.sh` | Rewritten for one MRM. Checks: C out through A; A dies with its links up → C through B within the keepalive plus one handshake; A back → C stays on B; B dies → C back through A |
| `netns-system-space-test.sh` | Becomes `netns-split-test.sh`: one MRM, two LANs. MRM killed → each LAN on its control host, islands stay up where their host is; MRM back → one network, one host per island |
| `netns-space-test.sh`, `netns-spaces-test.sh` | Become `netns-island-test.sh`: two islands, selection keeps them apart; the host killed → re-elected, the app re-writes; a stronger node joins → nothing moves; heal with two incumbents → one |
| `netns-join-kt-test.sh` | One MRM; the Kotlin `Island` |
| New: the challenge | A member's key passes; a LAN host with no member key and a revoked member fail |
| `test_rt_pages.py` | Cells on the island host, none on the MRM (asserted by reading the MRM) |

## 14. Order of work (proposed)

1. Incumbency from where cells (7.2): a defect today, independent of the rest.
2. One MRM: remove the lists, standbys, spaces and probe tunnels (12, Remove).
3. Egress and `exits` (6), with `netns-c-behind-ab-test.sh` turning green.
4. The split onto the control host and the merge (8), with `netns-split-test.sh`.
5. Island roles, the role host, where cells (7.3, 9.1–9.2).
6. The challenge and the app's access (9.3).
7. `Island` in Python and Kotlin; `ram.php`; the radio log on `rt-eoc` / `rt-hospital`.

## 15. Open questions for John

1. **The split's facts.** Settled (section 8.1, John 2026-11-07): region mode on a split drops to LAN-only FrogNet;
   `runMerge` runs and the LAN's own roles (api.php on port 80) take over.
2. **The egress route.** The applier owns only the /32 to the MRM via the exit, and FrogNet's default fixer is told when
   it moves (section 6.2). Is that the right split of ownership?
3. **Island access during a split.** Settled (section 9.0): the signed login record, checked locally.
4. **Level→island grants.** Settled (section 9.0): each service's reach by team, in the app's signed service table.
5. **Site.** A `facts.site` for placement by site: taken from the level, or typed at install?
6. **The island session after the challenge.** Should it be encrypted with `K`, or left plain as member sessions are
   today?
7. **The challenge.** Is the key proof in both directions (section 9.4) the challenge John means?
8. **Merge.** Does a relocated service stand down at once when the primary is visible again, or serve until it dies
   (section 9.0)?
9. **Grant changes and open sessions.** Is a session already open re-decided the moment its grant changes?
