# EOC: the app and how people join

2026-10-31 · John Fawcett, with Claude. The live copy is a Claude Doc; this file is its export, kept in the tree so the plan travels with the code.

## The app

One icon on the phone or the PC: **EOC**. It opens to the work, and the person never reads the words FrogNet, region, pond, Ribbit or invitation. Joining is scanning its level's code and, for some levels, typing a user and password; everything else happens underneath.

The screens are the DisasterComm apps (`DC_APPS_V1`, `opt/fdf/src/fdf-source/fdf/www/dc`) plus what RangerTrak does today:

| Screen | Who writes | What |
| --- | --- | --- |
| Help requests | The person needing help, or someone for them | Name, how many, needs, status (open / safe / resolved), location, contact |
| Responders | Each team, ambulance, equipment holder, volunteer | Kind, capabilities, availability, crew, location |
| Hospital capacity | Each hospital | Receiving status (open / limited / diversion / closed), capabilities, beds |
| Coordination | Whoever acts on a request, per unit | Assigned, en route, on scene, assisted, transported, released |
| Chat | Each sender | Text for communities, field teams and coordination centres |
| Positions | The scribe at the post, or the device itself | Where a team is, when, condition; lat/lon, MGRS, UTM, Maidenhead, Plus Codes |
| Radio log | The scribe | ICS-309 entries; ICS-213 messages |

Every truth has one owner and the owner writes its own cell; nobody edits anybody else's record. A request's state is read, not stored: its own status plus the newest action from each unit. Which screens a device shows is decided by its level, in the app.

## Two shapes, one cell model

At the post there is a node. The scribe's laptop is on its Wi-Fi, an ordinary DHCP client, and the right shape there is what `DC_APPS_V1` already is: pages the node serves, writing cells into the node's RAM. Nothing to install; every device at the post sees the same roster, radio log and positions on its held read.

In the field, with no node nearby, the phone or PC is a member of the region through the hub: that is the EOC app. Same cells, same owners, so the two shapes cannot disagree.

RangerTrak's problem, each device keeping its own log and logs merged by hand as files, is gone in both shapes: the cells are the log.

## How the bridge and the app join an r31 region

An r31 region's RAM manager is on `10.253.253.254:51888` inside the hub's tunnel (`[RAM_IN_THE_TUNNEL_V1]`), and the hub answers only a WireGuard key it was given ahead of time (`[ADMITTED_BY_INVITE_V1]`, `fdf-hub` `read_admitted`). So the first packet is a WireGuard handshake from an admitted key, and the session comes after.

The a15 bridge and the app had it the other way round (`etc\ponds.txt`, session first, tunnel from the hubs the region listed) and could not join. r36 turned it around: the Windows bridge a19 (`setup.cmd fdfinv1.<...>`), the Android app (`Identity.kt`, `MemberService.kt`) and the member library (`Invite.kt`) bring the tunnel up from the invitation first and open the session through it. This was on the `bundles-20261027` branch, not on `main`, until `integrate-20261031`.

## Setup, levels, login and the RAM manager

None of it is EOC code. It is the platform's, written up in `opt/fdf/docs/ORGANIZATIONS.md`: the manager sets up their own droplet with `fdf-setup`, describes their organization as levels, hands out one join line per level, and every device logs in at its level; the system RAM manager is the manager's droplet (or a standby), and databasehost-control when none answers.

For an EOC the levels are the manager's own words: First Responders, Headquarters, Hospitals, Ambulances. Most are RAM only; a few reach further (Headquarters to a server at a post, say). The app shows the screens a level needs; the level's reach is what the network lets it get to.

## Order of work

Windows first, then Android, then the HTTP pages the post serves. Each step is one deliverable that runs.

1. **The platform** (`ORGANIZATIONS.md` section 7): levels and reach in the admission record and `fdf-hub`; login over the generic credential; per-level session ports; the RAM manager list in the merge; `fdf-setup`. Proven in network namespaces (section 8).
2. **The Windows app**: the screens on the member's JVM, logging in at its level.
3. **Android**: the same screens over the same cells; a signed APK.
4. **The post's HTTP pages**: the same screens, read-only board included, served by the node, no Angular.

What each step is tested against is the incumbent suites, extended where the behaviour is new; nothing is accepted on the strength of an AI saying it works.

## What RangerTrak contributes

John Cornelison's [RangerTrak](https://github.com/EOCOnline/rangertrak) (AGPLv3, beta) is the single-device version of the field case: a scribe types what teams say over voice radio, offline, no server. Three parts of it carry over as library code with no framework in them:

- coordinate conversion on the device: lat/lon, MGRS, UTM, Maidenhead, Plus Codes;
- the offline basemap: MapLibre with PMTiles, areas saved beforehand;
- the printed forms: ICS-309 radio log, ICS-213 message.

Everything else, the Angular PWA, the per-device log, the hand-merged backups, the read-only Board over local Wi-Fi, is the part FrogNet removes. That is the conversion story for his site: the TypeScript client was written to work around having no shared state, and the cells are the shared state.
