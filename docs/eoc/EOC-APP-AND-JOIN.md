# EOC: the app and how people join

2026-10-31 · John Fawcett, with Claude. The live copy is a Claude Doc; this file is its export, kept in the tree so the plan travels with the code.

## The app

One icon on the phone or the PC: **EOC**. It opens to the work, and the person never reads the words FrogNet, region, pond, Ribbit or invitation. Joining is scanning a code or typing a short one; everything else happens underneath.

The screens are the DisasterComm apps (`DC_APPS_V1`, `opt/fdf/www/dc` on the `fdf-carry` branch) plus what RangerTrak does today:

| Screen | Who writes | What |
| --- | --- | --- |
| Help requests | The person needing help, or someone for them | Name, how many, needs, status (open / safe / resolved), location, contact |
| Responders | Each team, ambulance, equipment holder, volunteer | Kind, capabilities, availability, crew, location |
| Hospital capacity | Each hospital | Receiving status (open / limited / diversion / closed), capabilities, beds |
| Coordination | Whoever acts on a request, per unit | Assigned, en route, on scene, assisted, transported, released |
| Chat | Each sender | Text for communities, field teams and coordination centres |
| Positions | The scribe at the post, or the device itself | Where a team is, when, condition; lat/lon, MGRS, UTM, Maidenhead, Plus Codes |
| Radio log | The scribe | ICS-309 entries; ICS-213 messages |

Every truth has one owner and the owner writes its own cell; nobody edits anybody else's record. A request's state is read, not stored: its own status plus the newest action from each unit. Which screens a device shows is decided by the class it registered as (below), in the app.

## Two shapes, one cell model

At the post there is a node. The scribe's laptop is on its Wi-Fi, an ordinary DHCP client, and the right shape there is what `DC_APPS_V1` already is: pages the node serves, writing cells into the node's RAM. Nothing to install; every device at the post sees the same roster, radio log and positions on its held read.

In the field, with no node nearby, the phone or PC is a member of the region through the hub: that is the EOC app. Same cells, same owners, so the two shapes cannot disagree.

RangerTrak's problem, each device keeping its own log and logs merged by hand as files, is gone in both shapes: the cells are the log.

## How the bridge and the app join an r31 region

An r31 region's RAM manager is on `10.253.253.254:51888` inside the hub's tunnel (`[RAM_IN_THE_TUNNEL_V1]`), and the hub answers only a WireGuard key it was given ahead of time (`[ADMITTED_BY_INVITE_V1]`, `fdf-hub` `read_admitted`). So the first packet is a WireGuard handshake from an admitted key, and the session comes after.

The a15 bridge and the app had it the other way round (`etc\ponds.txt`, session first, tunnel from the hubs the region listed) and could not join. r36 turned it around: the Windows bridge a19 (`setup.cmd fdfinv1.<...>`), the Android app (`Identity.kt`, `MemberService.kt`) and the member library (`Invite.kt`) bring the tunnel up from the invitation first and open the session through it. This was on the `bundles-20261027` branch, not on `main`, until `integrate-20261031`.

## Join by invitation

An invitation is one line, `fdfinv1.<base64url JSON>`, made by `fdf-invite NAME [--leaf]` on the region's machine. It carries (`fdf-invite`, `inv = {...}`):

| Field | What |
| --- | --- |
| `area`, `site_key` | the region's name and the key that signs mappings |
| `ram` | `10.253.253.254:51888`, the RAM manager inside the tunnel |
| `hub.name`, `hub.pubkey`, `hub.endpoint`, `hub.addr` | the WireGuard peer: its key, its `ip:port` on the Internet, `10.253.253.254` |
| `member.name`, `member.edge`, `member.kind`, `member.privkey` | this member's name, its `10.253.X.Y` on the tunnel, `node` or `leaf`, and the private key the hub already admitted |

The member's order of operations, replacing the a15 order:

1. Decode the invitation. Store it once (`var\invite`); the private key in it is this device's key from now on, replacing the one `ensureIdentity` would have made.
2. Bring the forward tunnel up from the invitation alone: `Bridge.setForward(Hub(name, pubkey, endpoint, addr), edge)`, which sends fdf-tun one `set` with the hub as the forward peer, `allowed 10.0.0.0/8`, `given` = the edge. No region has been asked anything.
3. Open the member session to `ram` through the tunnel and announce (`Member.runOnce` as it stands: `node`, `announce`, `version`, `watch_participants`, the held wait).
4. `Leaf.tunnel` no longer decides whether a tunnel exists; it only confirms the hub the region lists is the one the invitation named, and says so if not.
5. Session ends, tunnel stays (the hub peers the key whether or not a session is up, `fdf-hub desired()`); the next session reopens through it. A dropped session means not alive, nothing more. Revocation is the other fact: the hub drops the peer and no session can start until a new invitation arrives.

The app's `Identity.kt` and `MemberService.kt` get the same change; `ponds.txt` and `Add a pond` go away.

## Levels and login

Hand-run `fdf-invite NAME` and `fdf-identity NAME` stay for the devices the admin admits one by one. Everyone else logs in at a level.

**The admin, once, on each RAM host:**

- defines the levels it will support, for example Watcher, Player, Host;
- gives each level its admission: open (anyone who logs in), something specifically allowed, or a list;
- gives each level its qualifications: a user and password, or none;
- sets up the credential manager. The credentials are stored encrypted in tuple space.
- Each level gets two random ports on the RAM host: its **login port** and its **session port**.
- A generic WireGuard credential is made for logging in. It is supplied to users and goes into the install.

**The EOC manager sets the network up** on their own droplet. They log in to it, install the one droplet bundle (`cd / && tar xvzf - < /tmp/fdf-droplet-<build>.tgz`), and run the wizard, `/opt/fdf/bin/fdf-setup`. It walks them through, asking before it changes anything and fixing what it finds missing (wireguard-go, the firewall) once they say yes:

1. **The RAM manager:** the region's name, the droplet's public address (found, shown, confirmed), then `fdf-region-create` as it is today.
2. **Levels:** offers Watcher, Player, Host; for each, password or none, and admission open, specifically allowed, or a list. It picks the random login and session ports.
3. **Users and passwords** for the levels that need them, stored encrypted in tuple space.
4. **Distributing the keys:** the generic WireGuard credential, and for each level one join line (RAM host address, credential, login port) printed and shown as a QR, ready to hand out or put in an install.
5. **Logging in:** the manager's own first device logs in at Host with the line from step 4, and the wizard shows it on the session port.

The wizard can be run again to add levels or users; anything it made is shown, not remade.

**At install**, the client is given the RAM host's address, the generic WireGuard credential, and the login port for its level. If the level requires them, the person supplies a user and password.

**Login**, in order:

1. The client brings up a tunnel on the generic credential. The login ports are reachable only inside it.
2. The client connects to its level's login port and logs in with the user and password. A level with no password admits at once, with the device ID as the name and a nickname the person can set.
3. The credentials are validated against tuple space, which gives the level.
4. The RAM host returns the level's session port and the device's own WireGuard peer: its own key and its own address, admitted there and then.
5. The client drops the generic tunnel, brings up its own, and connects to the RAM manager on the level's session port inside it. Every later start goes straight to its own tunnel and the session port.

The generic credential carries one exchange. It is one WireGuard peer, so two devices logging in at the same instant collide: the RAM host's endpoint for that peer goes to whichever handshook last, and the other gets no answer. The library logs in again by itself; the person never does. Nothing after login runs on the generic credential.

**At the session port**, the RAM manager accepts a connection only from an edge admitted at that level, under that level's admission: open, specifically allowed, or on the list. It is one lookup at accept, in the tuple space where the login recorded the admission. Revoking a device removes its admission, and its next connect is refused.

The client logs in this way at every RAM host on its list, so a standby already holds its peer before it is needed.

## Joining is a library, not EOC code

How people join is a standard pattern and belongs in the ++ libraries beside API++ (`ribbit/docs/API-PLUS-PLUS.md`), so every app on FrogNet joins the same way and none writes its own.

| piece | where it runs | what it is |
| --- | --- | --- |
| `join(invitation)` | every member | decode, tunnel up from the invitation, session through it (in the tree: `Invite.kt`) |
| `login(level)` | every member, once per RAM host | the five steps above; stores the device's own peer and the level's session port |
| the credential manager | each RAM host | levels, their ports and their qualifications, credentials encrypted in tuple space; validates a login, admits the device's own peer, answers with the session port |

## Constraints

- On a node's Wi-Fi none of this applies: the phone is a DHCP client and talks to the node's RAM directly. Login is for a device on the Internet reaching a RAM host.
- If a device is lost, the admin revokes it; the person logs in again and is a new member with a new key. No key is ever re-issued.

**Alive and admitted are two facts.** Alive is the session (`[SESSION_IS_LIVENESS_V1]`): a device whose session drops is not alive, and keeps its peer and its permission to reconnect. Admitted is the peer: a revoked device loses it and no session can start. Neither implies the other.

**Latest values and logs.** Positions, statuses, capacity and availability are latest-value cells. ICS-309 radio-log entries and ICS-213 messages are one cell each, owned by the scribe, lingering by family (`[LINGER_BY_FAMILY_V1]`) so the scribe's machine going away does not take the afternoon's log with it. The durable record is the post's own: the node at the post writes the ICS-309 file from its held read, which is the printed form. Nothing is replayed; the file is what is kept.

## The system RAM manager: the WAN RAM host if it answers, otherwise the highest IP

This is the **system** RAM manager only. Other services may have their own RAM managers at completely different places; nothing here touches them.

- **The WAN RAM host** is a machine every host in the set can reach: a droplet or another external node. It is not part of the FrogNet network. The admin names it (in order, if there is more than one) in the device's config.
- **databasehost-control** is unchanged: always the machine with the highest IP address in the network.
- **Every node checks for itself**, independently: no node learns the answer from another or decides it for another.
- **Every node has its own connection** to the system RAM manager, or to any RAM manager: its own admission, its own tunnel to that RAM manager's address, its own session. Nodes on one LAN can all reach it, and each still connects for itself. A RAM connection changes no routes, the node's own or any other's. Today the tunnel that carries the RAM session also carries the hub's transit routes (`remote_subnets` in `region.my_channels()`); those become separate: the RAM connection installs only the RAM manager's address, and site-to-site routes are not affected by which RAM manager a node is connected to.
- **What a merge is.** Every node writes its own facts to the RAM through its own session and holds one read on it (`fdf-events`). A change fires an event (`p` participants, `m` mappings, `gone`), and the merge is the node applying it (`_one("event ...")` in `internet_tunnels_v3/poll.py`): it reads the world from the RAM, derives its own routes and channels, and writes its facts back, one write per event. Split and merge are the same operation.
- **At the beginning of the merge** (the LAN connecting to the Internet is one such event), each node checks whether the configured WAN RAM host is available. If it is, the node connects on the peer and session port its login there gave it. If it is not, the system RAM manager is databasehost-control, the highest IP. Walking the list top to bottom, the first that answers wins.
- **Losing the WAN RAM host** ends the region session; that is the event. If more than one RAM manager is configured, the node switches to the first standby immediately on noticing: it is already admitted there, so the switch is a handshake and a session. Only when no configured RAM manager answers do the tunnels come down and the system RAM become databasehost-control.
- **A RAM manager coming back** (the primary while a node is on a standby, or any configured one while it is on databasehost-control) is a network event, and like every event it runs the merge. The only addition is the check inside the merge: walk the configured RAM managers top to bottom, connect to the first that answers, otherwise databasehost-control. If the answer at the end of the merge is not the RAM manager the node had at its start, the node switches and applications deal with it (below). No probe and no extra cell.
- Memory is not moved either way: applications are told the RAM host changed, re-arm their held reads and write their own truth again (the failover rule).

What changes in the code: today a device holds one invitation (`/opt/fdf/var/hub.json`, `var\\invite` on Windows, `Identity` on Android) and `region.my_channels()` builds one tunnel from it. That becomes one admission per configured WAN RAM host, checked in order at the start of the merge, with databasehost-control as the answer when none is available.

## Code findings, on the integrated tree

The first reading of this plan was done on `main`, which lacked r36. On `integrate-20261031` (main plus r36 and today's branches):

| Finding | Where | What it means |
| --- | --- | --- |
| Invitations are already joined by the member library: `Invite.kt` decodes `fdfinv1` the way `fdf-region-join` does. | `member/.../Invite.kt` `[LEAF_BY_INVITE_V1]` | Step 1 below is done. |
| The Windows bridge (a19) joins by invitation: `setup.cmd fdfinv1.<...>` keeps it in `var\invite` and makes the startup task. | `opt/fdf/windows/bridge` | Step 2 is done. |
| Android joins by invitation through the hub's tunnel. | `app/.../Identity.kt`, `MemberService.kt` `[ADMITTED_BY_INVITE_V1]` | The app's join is done; `ponds.txt` goes when login arrives. |
| An invited leaf's address is its edge; no `10.252` address is allocated for it. | `rinab/src/rina_region.cpp:626-635` `[LEAF_BY_INVITE_V1]` | The two-addresses defect is fixed. |
| `fdf-identity NAME --leaf` makes an invitation from any machine already in the region. | `opt/fdf/bin/fdf-identity` | Hand-made admissions no longer need the region host. |
| `DC_APPS_V1` is in the tree. | `opt/fdf/www/dc` | The cells the EOC screens read are there. |
| A device holds one invitation, one hub. | `region.py` `HUB_JSON`, `my_channels()` | The ordered list above. |
| No levels, no login, no credential manager. | nowhere | Step 3. |

## Order of work

Windows first, then Android, then the HTTP pages the post serves. Each step is one deliverable that runs.

1. ~~`join()` in the member library~~: in the tree (`Invite.kt`).
2. ~~Windows join~~: in the tree (bridge a19).
3. **Levels, login and the setup wizard**: `fdf-setup` on the droplet (RAM manager, levels, users, keys, first login); the credential manager on the RAM host (levels, login and session ports, credentials encrypted in tuple space); `login(level)` in the member library. Proven in a network namespace against a real `fdf-region-create` region: two devices logging in at once, a level with a password and one without, each ending on its own tunnel at the level's session port.
4. **The system RAM manager at merge**: the configured WAN RAM hosts checked in order at the start of the merge, registered and connected if one answers, databasehost-control (highest IP) if none does. Node (`region.py`), member library (Windows and Android). Proven in a namespace: WAN RAM host up, it is the RAM; cut it, the highest IP is; restore it, the reconnect's merge moves back.
5. **The Windows app**: RangerTrak's screens on the member's JVM.
6. **Android**: the same screens over the same cells; a signed APK.
7. **The post's HTTP pages**: the same screens, read-only board included, served by the node, no Angular.

What each step is tested against is the incumbent suites, extended where the behaviour is new; nothing is accepted on the strength of an AI saying it works.

## What RangerTrak contributes

John Cornelison's [RangerTrak](https://github.com/EOCOnline/rangertrak) (AGPLv3, beta) is the single-device version of the field case: a scribe types what teams say over voice radio, offline, no server. Three parts of it carry over as library code with no framework in them:

- coordinate conversion on the device: lat/lon, MGRS, UTM, Maidenhead, Plus Codes;
- the offline basemap: MapLibre with PMTiles, areas saved beforehand;
- the printed forms: ICS-309 radio log, ICS-213 message.

Everything else, the Angular PWA, the per-device log, the hand-merged backups, the read-only Board over local Wi-Fi, is the part FrogNet removes. That is the conversion story for his site: the TypeScript client was written to work around having no shared state, and the cells are the shared state.
