# RangerTrak on FrogNet: the conversion plan

2026-10-31 · John Fawcett, with Claude. The live copy is a Claude Doc; this file is its export. The checkpoint files the reference process names (`RANGERTRAK-SUITE-MAP.md`, `study-*.md`, `QUESTION-3.md`, `DESIGN.md`, `RESULTS.md`, `README.md`, `VERSION`) land in this directory as the phases produce them.

## Decision, 2026-10-31 (John): our own app

"You own it all. This is an app. It does not need the angular shit to do this." The radio log is FrogNet's own app,
written fresh, GPL-2.0-only like the rest of the tree: node-served pages (`opt/fdf/src/fdf-source/fdf/www/rt`, an add-in of the
DisasterComm launcher, `../dc/ram.php` as its door to the RAM), no Angular, nothing of RangerTrak's code in it.
RangerTrak stays what the app is measured against: its screens, its words, its forms (ICS-309 numbers `A007`,
`26-0142 / OP 2 / A007`) and its suite, which `RESULTS.md` and `RANGERTRAK-SUITE-MAP.md` record. Where this plan says the
conversion keeps his `domain/` or his screens, read: the app does what they do, in its own code. The Windows and Android
devices open the same pages (decided the same day: node-served pages first).

## Decision, 2026-11-01 (John): the general RAM and the team RAMs

The app is set up and run as `opt/fdf/docs/APP-SETUP-AND-OPERATION.md` says, which governs here. "RangerTrak is as many
RAMs as it needs, but absolutely more than 2." It has a general RAM that everyone in the app joins (positions, roster,
radio log, mission and assignments, announcements, direct messages between teams) and one RAM per team (that team's own
data and its team messages). That document's "RangerTrak" table is the layout.

What this changes in the pages built 2026-10-31 (`[RT_PAGES_V1]`):
- They write to the network RAM through `ram.php` and `RINA_RAM`. They move to the app's own droplets, found from their
  where cells, chosen by RangerTrak's selection over the capability tuples.
- `map.html` derives each callsign's position from log entries. A position becomes each device's own tuple in the
  general RAM, and the map a query over those tuples; a logged position stays the scribe's record in the log.
- Kept as they are: `rules.js`, the screens, the forms, and `[OWN_TRUTH_AGAIN_V1]` (each device writes its own
  entries again on a RAM change).

## Decision, 2026-11-01 (John): the demo is presence, chat and the map, in an app

"Presence and chat and some sensor data", "on the phone and on the desktop", "in an app" -- not the browser, and not
the radio log ("absolutely the least interesting thing there is"). This replaces the demo below.

- **Who:** the EOC on the Windows app; a first responder on a phone (Android app). Windows first, then Android.
- **Splash:** "RangerTrak / powered by the FrogNet Living Network", while the app opens its RAM session.
- **Presence includes GPS:** each device writes its own presence with its position (the phone from its GPS on every
  fix; a desktop from Windows' location service when it has one -- none, no position; nothing is made up).
- **Pre-populated RAM:** instances of Teams, Hospitals, Ambulances, Fire, Power and Victims, placed in a lat/long area
  John gives, with simulated GPS. Ambulances, teams and victims move on the map in real time. Each simulated instance
  is its own writer of its own tuples, as a real device would be (a simulator participant, not rows written once).
- **EOC default view:** the big map, everything on it, chat beside it.
- **Team default view:** teams colour-coded so each is told apart at a glance. Double-click a team member: a chat box
  to that one member or to all of the team, with a video link when the connection is fast enough.
- **Library, not usurpation:** nothing is taken whole from the Communicator. What is useful (the member's RAM session
  and join line, the held-read loop, the presence writer, the media link for video) is abstracted into a library item
  that both use.
- **Independent of this:** the ESP32 work, which makes a Sensors page (all in an area, all of a type, all out of
  tolerance, ...).

## The demo this plan is for (2026-10-31, superseded by the decision above)

John Cornelison sees RangerTrak's screens and words running at three posts at once, with no Board or coordination
server to deploy: a scribe at each post typing what teams say over the radio, every post's radio log, roster and map
showing everyone's entries as they are typed, and each device's own position on every map. The pages are served by
the posts' nodes; the app's RAMs are served by droplets on members, as `APP-SETUP-AND-OPERATION.md` says. A post loses
its uplink; its scribe keeps working; the others keep each other; the uplink comes back and the post's entries are on
every screen again, because their owners wrote them again to the RAM that serves the app now. Nothing is merged,
restored or sent by hand.

## What RangerTrak is, from its source

Read from a clone of [SteadyEOC/RangerTrak](https://github.com/SteadyEOC/RangerTrak) on 2026-10-31: Angular 22, TypeScript, AGPLv3. 47,506 lines of TypeScript in 271 files (7,282 of them pure rules in `src/app/domain/`, no Angular, no storage), 8,782 of HTML, 10,376 of SCSS; 137 Karma spec files, 16,074 lines; a Playwright end-to-end driver (`tools/e2e.js`); a 1,202-line Node "Board" server (`tools/board/`). One browser tab per device holds the whole state in memory and writes it whole to IndexedDB on every change (`ARCHITECTURE.md`, "Data stored on the device": 33 keys; "One active tab per browser": a `navigator.locks` lock so two tabs don't overwrite each other).

The state that is the mission, by its own inventory:

| Entity | Fields that matter | Where it lives today | Who writes it today |
| --- | --- | --- | --- |
| Ranger (roster) | `uid` (app-minted join key), `id` (REW/TEW credential), `callsign`, `fullName`, `phone`, `team`, `role`, `note`, photo | IndexedDB `rangers`, `rangertrak-photos` | the scribe who imported or typed it; copied to other devices by Setup file, hand-over or backup |
| Radio log entry | `uid`, `rangerUid`, `callsign`, `location` (lat/lon + the typed form), `date`, `status`, `notes`, `msgNo` `{mission, opPeriod, station, seq}`, `handled`, 213 fields, attachments | IndexedDB `radioLog`, whole log rewritten per entry | the scribe's device stamps `msgNo` at save; a field phone's entries arrive unnumbered in a Report Packet and are numbered on merge |
| Mission settings | `mission`, `event`, `opPeriod`, `opPeriodStart/End`, default lat/lon, which coordinate formats to show | localStorage `appSettings` | the scribe; a field phone never learns them (E-252) |
| Mission locations (places) | named places with categories | IndexedDB `locations` | the scribe |
| AAR / ICS-214 notes | kind, text, time | IndexedDB `aarNotes` | the scribe |
| Mission journal | append-only hash chain of actions | IndexedDB `missionJournal` | each device, its own, never merged |
| Attachments, layers, secrets, prefs | | IndexedDB, localStorage | per device |

What talks to what, which is the list of things the conversion removes:

- **Report Packet**: a field phone exports its unnumbered entries as a file; the scribe imports it; `mergeEntries()` numbers them and reports `msgNo` clashes. "Sent" means put in a file (`fieldModeSentUids`); nothing comes back.
- **Hand-over file**: one scribe's device to the next, with *Take over station X* or *Log alongside as station Y* to keep message numbers from colliding (ADR D-68).
- **Mission backup / restore / import safety copy**: whole-mission JSON plus an attachments zip; a 24-hour safety copy before any restore.
- **The Board** (`tools/board/`): a Node server on the post's Wi-Fi; the scribe's device claims it with a publish key and POSTs a snapshot every 5 s; viewers GET `/api/mission`; the server tracks viewers, Wi-Fi name, firewall, certificate, battery.
- **Command Post Server**: the Board kit's launcher, a double-click `.bat`/`.command` that needs Node 24 on a laptop.
- **Service worker and tab lock**: so one browser tab works offline and two tabs don't destroy each other.

Every one of these exists because two devices have no shared memory. That is the whole finding of phase 1 before phase 1 has run.

## The contract: behaviour and proof, in fresh code

The app is FrogNet's own (decision of 2026-10-31). Nothing of RangerTrak's code is in it. What is kept is what a
scribe or a viewer can see and do, and the proof that it is right:

- **The screens and their words**: Entry, Radio Log, Rangers, Map, Mission, Messages (213), Summary, After-action (214),
  Log. Same fields, statuses, colours and overdue bands.
- **Coordinates**: lat/lon, DD/DDM/DMS, MGRS, UTM, Maidenhead, Plus Codes; "coordinates always, addresses when
  connected".
- **The printed forms**: ICS-309 with the mark and Msg # columns, ICS-213 with the full number, ICS-214, the CSV
  columns.
- **Message numbers**: `{mission, opPeriod, station, seq}`, printed as the station ID and its count (RangerTrak's `A007`;
  here the station ID is the app's hostname) and in full as `26-0142 / OP 2 / <station>-007`, stamped once.
- **The proof**: RangerTrak's `domain/` specs are table-driven. Their cases become oracles for this app's own rules
  (`rules.js`): the same inputs, the same outputs, run against the fresh code. `RANGERTRAK-SUITE-MAP.md` says which
  apply. Stock RangerTrak's suite and e2e, run as they are, are the baseline (`RESULTS.md`).

## What is thrown away

| Machinery | Lines (statements counted at phase 1) | Why it existed | What replaces it |
| --- | --- | --- | --- |
| Report Packet export, import, `mergeEntries()`, `findMsgNoClashes`, `packet-time-check` | `shared/export/report-packet.ts`, `radio-log.service.ts` merge paths, `domain/msg-no.ts` clash half | a field phone had no way to write to the scribe's log | the field device writes its own entry cells; the scribe's screen reads them |
| Hand-over file, *Take over station* / *Log alongside* | `shared/export/handover-file.ts`, the dialog | a second scribe had to receive the first one's whole state | the second scribe's device holds a read on the same families; it has the state before it is asked for |
| Mission backup, restore, import safety copy, undo last import | `backup.service.ts`, `import-safety.service.ts`, `domain/import-safety.ts` | the only copy of the mission was in one browser's IndexedDB | nothing kept by hand: the mission is in the app's RAMs, each owner keeps its own truth to write again, and the durable file is the post's own, below |
| The Board: server, claim, publish key, 5-second snapshot POST, viewer page, causes, self-check, status line, kit, launchers | `tools/board/` (1,202 lines), `command-post-publish.service.ts`, `domain/board-*.ts`, `src/command-post-view/` | viewers on the post's Wi-Fi had no way to see the scribe's screen | the node at the post serves the same screens read-only from the same cells; no server to claim, no key, no snapshot |
| Service worker, `ngsw-config.json`, tab lock, `RecordStore`, the 33 storage keys and their migrations | `shared/storage/`, `domain/*-migration.ts` | one tab had to hold the state and survive a reload | each device keeps only its own truth (to write it again after a RAM change or a reload); the shared state is in the app's RAMs |
| `fieldModeSentUids`, `rt-board-publisher`, `missionJournal` copy-in-backup | prefs | counting what was sent, who holds the Board | not needed: a write that landed is sent |

## The admin app and the client app

As `APP-SETUP-AND-OPERATION.md` ("An app is an admin app and a client app") says. RangerTrak's **admin app**
sets up the teams, their passwords (verifiers), access by team, the general RAM and the team RAMs, relocate, and the
remote nodes to install, all as configuration tuples in the network RAM. Its **client app** is one app whose main focus
follows the user's team: Scribe opens on a post's radio log, Field on a phone's entry and position, Command on missions,
op periods and assignments, and each team the admin defines on its own screen (Hospitals: capacity and receiving
status). Everything else is the same app. It connects to its RAM before it shows anything. Starting a mission and making assignments is Command's client work, app data in the general RAM, not the
admin app's.

## The RAMs and what is in them

As `APP-SETUP-AND-OPERATION.md` ("An app has as many RAMs as it needs") lays out: a general RAM that every RangerTrak
application joins, and one RAM per team the admin defined. Each RAM has its own selection, its own droplet and its own
relocate. **The application holds one session to each RAM it is authorized for, and each is independent: a RAM moving
or failing changes nothing about the others** (the application follows that RAM's location and writes its own truth
again there).

## The truth table

`<dev>` is the writing device's id; `<mission>` the mission id its starter minted. Every location has one writer.

| truth | RAM | owner | indices | latest or distinct | readers |
| --- | --- | --- | --- | --- | --- |
| Mission: name, event, op period, start, end, default lat/lon | general | command (the scribe who started it) | `(mission, settings, <mission>)` | latest | every application |
| Assignment | general | command | `(mission, assign, <mission>.<team>)` | latest | every application |
| Ranger | general | the device that typed or imported that ranger | `(roster, <mission>, <uid>)` | latest per uid | every application |
| Device: name, station ID (its hostname), team | general | the device | `(who, <mission>, <dev>)` | latest | Messages (DMs are addressed by station ID) |
| Position | general | **the device itself**: its own current fix | `(pos, <mission>, <dev>)` | latest | Map, Rangers, board: a query over `(pos, <mission>, *)` |
| Radio log entry | general | the scribe's device | `(log, <mission>.<op>, <dev>.<seq>)` | distinct, never overwritten; a correction is a new entry naming `revises` | every application, the post's 309 |
| A position reported on the radio | general | the scribe, inside the entry | the entry's `location` | | the log and the 309; never a device's position |
| Handled mark | general | **each handler, its own** | `(handled, <mission>, <entry>.<dev>)` | latest per handler | the displayed mark is derived: the newest mark across handlers |
| Place | general | the device that typed it | `(places, <mission>, <uid>)` | latest | Map, Entry |
| Announcement | general | the sender | `(msg, <mission>, <dev>.<seq>)` | distinct | everyone |
| Direct message between teams | general | the sender | `(dm, <mission>.<to>, <dev>.<seq>)` | distinct | the sender and the addressee only (the app's access) |
| Team data (the Hospitals': capacity, receiving status) | that team's | the team member who owns it | the team's own families | | that team, and whom it grants |
| Team message | that team's | the sender | `(msg, <mission>, <dev>.<seq>)` | distinct | that team |
| AAR / 214 note | general | the device that typed it | `(notes, <mission>, <dev>.<seq>)` | distinct | After-action |
| Overdue / needs attention | | | | **derived**, never stored | Map, Rangers, Radio Log |
| Mission journal | not in a RAM | the device | local | | that device's Log page |

**Message numbers.** **John, 2026-11-01:** "The station ID is the app hostname." A hostname is already unique on the
network, so the station ID is unique by construction and nobody types it: no clash to detect, warn of or claim.
**John, 2026-11-01:** "Station ID is hidden from the users. Use the host name or device name." A device that sends no
host name with its lease (many Android phones) uses the name typed for the device. Users see that name, not a station ID.
RangerTrak's typed station letter, its clash report (`findMsgNoClashes`) and its *Take over station* / *Log
alongside* choice all go. `<dev>.<seq>` stays the entry's identity in the RAM; the printed number is the station ID and
that station's own count.

## The participants

| participant | runs on | writes | holds reads on |
| --- | --- | --- | --- |
| **Scribe** | a laptop or phone, on a post's Wi-Fi (the post's pages) or as a member of the region | its entries, its handled marks, rangers and places it typed, the mission if it started it | the log, roster, places, handled and mission families; positions for the map |
| **Field** | a phone in the field | its own position, its own entries, its own marks | the same families |
| **Post** | the node at the post (`fdf/bin/rt-post309`; its unit is to come with the app's install) | the 309 and 213 files | the log and handled families |

**Each device keeps its own truth.** Everything a device wrote is kept on that device (browser storage for a page) as
well as written to the RAM, so that after a RAM change, or a page reload during a partition, it can write its own
truth again. Nothing else is kept on a device. **The post's 309 file is written from the log in write order, each
entry once by its id**, so a lost and resumed read leaves no gap and no duplicate.

**Time.** Overdue bands advance with the clock, with no write: the pages redraw them on a timer of their own. That is
the app's timing, not a read.

## The work, in order

1. **The platform**: an app's droplet chosen by its selection over the capability tuples, activated on the member it
   picks, writing its where cell; the app configuration tuples (where each RAM is, access by team). Proven in network
   namespaces before the app depends on it.
2. **The pages follow their RAMs**: `ram.php` and `rt.js` connect to the general RAM's droplet from its where cell, not
   to `RINA_RAM`. The two-post test (`test_rt_pages.py`) runs again, the cut and the rejoin included.
3. **Positions** become each device's own tuples; the map becomes a query over them.
4. **The truth-table corrections**: handled marks per handler; the station ID taken from the app's hostname; own
   truth kept on the device for a reload.
5. **Team RAMs** and team messages; direct messages and their access.
6. **Login and access by team** from the app configuration tuples.
7. **The rest of the screens**: 213, Summary, 214.

Each step: its own test first, the incumbent tests after, results in `RESULTS.md`.

## The multi-site demo

Three posts, one region, one field phone. Each post is a node with its own Wi-Fi. The region's droplet is where
members attach; it holds no entry. The app's RAMs are served by droplets on members. Scribes at posts are on their
post's Wi-Fi; the field phone runs the app as a member.

What Cornelison does, in order, at his own keyboard:

1. Types an entry at post A. It is on B's and C's Radio Log before he looks up; the phone shows it too.
2. Walks with the phone. Its position moves on every post's map, with nothing logged.
3. Logs an entry on the phone. It appears at all three posts, numbered with the phone's station ID, its app hostname.
4. Pulls post B's uplink. B's scribe keeps typing, on the general RAM relocated to B's side; A and C keep each other.
5. Plugs B back in. One general RAM serves the app again; every device writes its own entries there again, and every
   screen reads the current RAM. B's entries are on every screen within seconds. Nothing was restored or merged.
6. Reloads the scribe's page at B during the cut, before step 5: B's own entries still come back at step 5.
7. Prints the 309 at post C: every entry, in write order, once each, with the marks.
8. Opens the board page on a phone on post A's Wi-Fi: the same data, read-only.

Not in the demo: anything needing the Internet (geocoding off, coordinates only).

## What is measured and reported

Side by side against stock, on at least two machines with traffic between them (`RESULTS.md`, tied to a `VERSION`):

| measured | stock RangerTrak | on FrogNet |
| --- | --- | --- |
| Entry typed at one post to visible at another | a Report Packet: minutes, by hand | ms from the write to the other screen |
| Two scribes logging at once, 500 entries each | merge, clash report, renumbering | one log in write order; 0 identity clashes by construction |
| A post loses and regains its uplink | nothing moves until a file does | ms from the rejoin to the last missing entry on screen |
| A page reloaded during a partition | its IndexedDB copy | its own entries on every screen after the rejoin |
| The proof | stock's suite and e2e, pass counts | the `domain/` cases as oracles for `rules.js`, pass counts |
| Bad data | what stock does | two scribes at once, a wrong clock, a device killed mid-entry, 5,000 entries |

Not claimed: Internet-dependent features; phone battery; more than the demo's three posts; the region's droplet
destroyed rather than cut.

## Prepared for the Meshtastic merge (2026-11-01)

The Meshtastic on FrogNet proposal (John, 2026-10-30) and RangerTrak share their model: each node or
device is the only writer of its own cells, and every reader acts on a held read. RangerTrak's per-device families are
the same truths Meshtastic's client API serves, so the Meshtastic client edge can read and write them as they are:

| RangerTrak cell | Meshtastic | The Meshtastic edge |
| --- | --- | --- |
| `(rt-pos, <mission>, <dev>)` `{name, lat, lng, alt, acc, at}` | Position (`latitude_i`, `longitude_i`, `altitude`, `time`) | a node's position is its own cell; a Meshtastic node's is written by the FrogNet node that hears it, as that node's |
| `(rt-who, <mission>, <dev>)` `{name, host, station, team}` | NodeInfo / User (`long_name`, `short_name`, `id`) | the node list is a read of this family |
| `(rt-msg, <mission>, <dev>.<ms>)` | TEXT_MESSAGE_APP on the primary channel | an announcement is a channel message |
| `(rt-dm, <mission>.<to>, <dev>.<ms>)` | a direct message (`to` = the node) | in the addressee's variable; nothing checks it |
| `(rt-team@<team>, <mission>, <dev>.<ms>)` | a message on a secondary channel | a team's RAM is a channel's audience |
| the device's access record | the node's keys and channel PSKs | each written again by its owner after a RAM change |

What the merge adds, not built here: the Meshtastic client edge (ToRadio / FromRadio) reading and writing these cells;
the over-the-air edge, which writes what it hears from a stock node as that node's cells; and liveness bound to the
node's session, so a device that has left drops off the map (RangerTrak's cells do not go with a session today).
