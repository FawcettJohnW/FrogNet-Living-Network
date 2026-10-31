# RangerTrak on FrogNet: the conversion plan

2026-10-31 · John Fawcett, with Claude. The live copy is a Claude Doc; this file is its export. The checkpoint files the reference process names (`RANGERTRAK-SUITE-MAP.md`, `study-*.md`, `QUESTION-3.md`, `DESIGN.md`, `RESULTS.md`, `README.md`, `VERSION`) land in this directory as the phases produce them.

## The demo this plan is for

John Cornelison sees RangerTrak, his screens and his words, running at three posts at once with no server anywhere: a scribe at each post typing what teams say over the radio, and every post's radio log, roster and map showing everyone's entries as they are typed. A post loses its uplink; its scribe keeps working; the others keep each other; the uplink comes back and the post's entries are on every screen again, with nothing merged, restored or sent. His Report Packets, hand-over files, the Board and the Command Post Server are not in the demo because they no longer exist.

The plan follows `ribbit/docs/CONVERTING-TO-RIBBIT.md`: his contract kept, his machinery removed, two phases, the checkpoint files. It is the tenth conversion and the first whose author will be in the room.

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

## The contract: what is kept

The contract is what a scribe or a viewer can see and do, byte for byte where there are bytes:

- **The screens and their words**: Entry, Radio Log, Rangers, Map, Mission, Messages (213), Summary, After-action (214), Log. Same fields, same statuses and colours (`--rt-status-*`, `--rt-elapsed-1..7` mean the same under every skin), same overdue bands (`domain/overdue.ts`).
- **Coordinates**: lat/lon, DD/DDM/DMS, MGRS, UTM, Maidenhead, Plus Codes, parsed and printed by `domain/coordinate.ts`; addresses only when connected (Nominatim, or Google with the operator's own key). "Coordinates always, addresses when connected" stays the rule.
- **The printed forms**: ICS-309 with the mark and Msg # columns, ICS-213 with the full number in its margin, ICS-214 from the activity notes, the map print sheet (`domain/map-print-layout.ts`), the CSV columns (`msg_mission`, `msg_op_period`, `msg_station`, `msg_seq`, `msg_no`, `handled`).
- **Message numbers**: `{mission, opPeriod, station, seq}`, `A007`, `26-0142 / OP 2 / A007`; stamped once; never restamped (`domain/msg-no.ts`). The station letter stays a per-device fact.
- **Imports**: roster and places from spreadsheets with remembered column mappings (`domain/roster-sheet.ts`), Setup files, demo scenarios (Montecito, Oso), custom `.pmtiles`.
- **Both map engines** as they are: Leaflet with `leaflet.offline`, MapLibre with PMTiles (ADR D-56).
- **The suite, unmodified**: 137 spec files (the `domain/` specs are table-driven and run without a browser), `tools/e2e.js`, `test:regional`. The specs that assert the removed machinery (backup, merge, Board, tab lock) are mapped as not applicable in `RANGERTRAK-SUITE-MAP.md`, with the reason, not deleted.

The mission journal (D-67) is kept as a contract too: the paper anchor printed on the 309 is a hash of what this device did. On FrogNet it stays per device, as it is today, and still never merges.

## What is thrown away

| Machinery | Lines (statements counted at phase 1) | Why it existed | What replaces it |
| --- | --- | --- | --- |
| Report Packet export, import, `mergeEntries()`, `findMsgNoClashes`, `packet-time-check` | `shared/export/report-packet.ts`, `radio-log.service.ts` merge paths, `domain/msg-no.ts` clash half | a field phone had no way to write to the scribe's log | the field device writes its own entry cells; the scribe's screen reads them |
| Hand-over file, *Take over station* / *Log alongside* | `shared/export/handover-file.ts`, the dialog | a second scribe had to receive the first one's whole state | the second scribe's device holds a read on the same families; it has the state before it is asked for |
| Mission backup, restore, import safety copy, undo last import | `backup.service.ts`, `import-safety.service.ts`, `domain/import-safety.ts` | the only copy of the mission was in one browser's IndexedDB | nothing: the mission is in the post's RAM and every device's held view; the durable file is the post's own, below |
| The Board: server, claim, publish key, 5-second snapshot POST, viewer page, causes, self-check, status line, kit, launchers | `tools/board/` (1,202 lines), `command-post-publish.service.ts`, `domain/board-*.ts`, `src/command-post-view/` | viewers on the post's Wi-Fi had no way to see the scribe's screen | the node at the post serves the same screens read-only from the same cells; no server to claim, no key, no snapshot |
| Service worker, `ngsw-config.json`, tab lock, `RecordStore`, the 33 storage keys and their migrations | `shared/storage/`, `domain/*-migration.ts` | one tab had to hold the state and survive a reload | a Ribbit space per device; the migrations' *shape* becomes the cell bag's shape, once |
| `fieldModeSentUids`, `rt-board-publisher`, `missionJournal` copy-in-backup | prefs | counting what was sent, who holds the Board | not needed: a write that landed is sent |

The `domain/` folder is not thrown away. 7,282 lines of pure rules with table-driven specs is exactly the part of a program a conversion keeps whole, and its own ESLint rule (D-57: no Angular, no storage imports) already made it portable. The estimate for what remains after phase 2 is the `domain/` rules plus the screens, with the services between them replaced by held views: in the 70–90% range the other conversions landed in, measured as statements at the end, not promised now.

## The truth table

Written before any code (`DESIGN.md`). The three indices are `(space, variable, instance)`. `<dev>` is the writing device's identity (`w:<guid>`, the member's `ns`); `<mission>` is the mission id the scribe who started it minted.

| truth | owner | indices | latest or distinct | readers | trigger family | derived instead? |
| --- | --- | --- | --- | --- | --- | --- |
| Mission: name, event, op period, start, end, default lat/lon | the scribe who started it | `(mission, settings, <mission>)` | latest | every device | `(mission, settings, *)`: a new mission appears, an op period changes | |
| Ranger | the device that imported or typed that ranger | `(roster, <mission>, <uid>)` | latest per uid | every device; Entry autocomplete; map labels | `(roster, <mission>, *)` | |
| Ranger photo | same device as the ranger | `(photos, <mission>, <uid>)` | latest | Rangers grid, map | on demand, not held | |
| Radio log entry | the device whose scribe logged it | `(log, <mission>.<opPeriod>, <dev>.<seq>)` | distinct: one cell per entry, never overwritten; a correction is a new cell naming `revises` | every device's Radio Log, Map, Summary; the post's 309 writer | `(log, <mission>.<opPeriod>, *)`: fires per entry, in write order | |
| Message number | the device that logged the entry | in the entry's bag: `{station, seq}`; station is the device's letter, seq its own count | | | | no clash detection: `<dev>.<seq>` is unique by construction; the printed `A007` form is unchanged |
| Handled mark | whoever handled it | `(handled, <mission>, <entry-id>)` | latest | Radio Log, 309 | `(handled, <mission>, *)` | "later `at` wins" becomes write order |
| Position of a ranger | the device that logged the entry carrying it | from the entry | | Map | the log family | **derived**: latest entry per ranger (`domain/map-entry-filter.ts selectMapEntries`), computed when drawn |
| Overdue / needs attention | | | | Map, Rangers, Radio Log | | **derived** from the newest entry's time and `domain/overdue.ts` bands, computed when drawn, never stored |
| Place (mission location) | the device that typed it | `(places, <mission>, <uid>)` | latest | Map, Entry | `(places, <mission>, *)` | |
| AAR / 214 note | the device that typed it | `(notes, <mission>, <dev>.<seq>)` | distinct | After-action, 214 export | `(notes, <mission>, *)` | |
| 213 message | the device that composed it | in the log entry's bag (`generates213`, `message213`, `recipients213`) | | Messages | the log family | |
| Who is present at the mission | | | | Summary, the post's board view | | **derived**: alive members whose facts carry `<mission>` |
| Station letter | the device | its own facts: `{station: "A"}` | | the entry writer | | a device-level fact announced with the member, not mission data |
| Mission journal | the device | local, as today | | the device's Log page | | not in the RAM: it is this device's record of what this device did |
| The 309 file, the 213 PDFs | the post's node | a file on the node, written from its held read | | printing | | the durable record (`[LINGER_BY_FAMILY_V1]` keeps the cells while the post is up; the file is what outlives it) |

Two rules from the reference process decide the shape:

- **One writer per location.** A radio log entry is written once by the device that heard it; nobody edits it. A correction is a new entry. That is already how a radio log works on paper, and it is why `revisedAt` and `mergeEntries` go away together.
- **Contested truth is state, not a fight.** Message-number clashes existed because two devices each counted from 1. With `<dev>.<seq>` as the instance there is nothing to contest; the station letter is still shown and printed, but it no longer has to be unique for the log to be whole.

## The participants

Three, each 100–250 lines of its own, the screens and `domain/` rules shared by all.

| participant | runs on | writes | holds reads on | acts on triggers |
| --- | --- | --- | --- | --- |
| **Scribe** | a laptop or phone at a post, as a member of the region (the EOC app) or on the post's Wi-Fi | its own log entries, handled marks, rangers and places it typed, mission settings if it started the mission | `(log, <mission>.<op>, *)`, `(roster, <mission>, *)`, `(places, <mission>, *)`, `(handled, <mission>, *)`, `(mission, settings, *)` | redraws the grid row or marker the trigger named; nothing else |
| **Field** | a phone in the field, member of the region | its own entries, including its own position | the same families, so it sees the mission it is reporting into | the same |
| **Post** | the node at the post | the 309 and 213 files from what it reads; the read-only board page it serves | `(log, <mission>.<op>, *)`, `(handled, <mission>, *)` | appends the entry to the 309 file as it lands |

There is no fourth participant. No merger, no publisher, no server, no claim. The Board is the Post participant's served page, and "who is viewing" is not tracked because nothing needs it.

How the joining ++ libraries enter: a Scribe or Field device that is not at a post logs in at its level on each RAM host it lists (for the demo, a Field level with no password and a Scribe level with one), and connects on its own tunnel at that level's session port. That is the EOC app's login, used as is; RangerTrak adds no join code of its own.

## The phases

### Step 0: the ++ changes for registration

The demo's devices are not at a post; they join a region. Joining by invitation is already in the tree (r36: `member/.../Invite.kt`, the Windows bridge a19, the Android app, and an invited leaf addressed by its edge). What is not is levels and login, and the system RAM manager's choice at merge: a configured WAN RAM host if it answers, databasehost-control (the highest IP) if not. Those come first and are proven on their own before RangerTrak touches them. From `opt/fdf/docs/ORGANIZATIONS.md`:

| deliverable | where | proven by |
| --- | --- | --- |
| Levels and login: the admin's own levels (examples: Watcher, Player, Host, Viewer, Commenter, Editor, Admin), each with a login port and a session port, credentials encrypted in tuple space; login on the generic WireGuard credential returns the device's own peer and the level's session port | the credential manager on each RAM host; `login(level)` in the member library | a netns test: two devices logging in at once, a level with a password and one without, each ending on its own tunnel at its level's session port |
| The system RAM manager at merge: every node, independently, checks the configured WAN RAM hosts in order when the LAN connects to the Internet; registered and connected if one answers, databasehost-control (highest IP) if none does. System RAM only; services keep their own RAM managers | `internet_tunnels_v3/region.py` (the merge, `my_channels()`), the member library (`Invite.kt`, `Leaf.kt`) | a netns test: WAN RAM host up, it is the RAM; cut it, the highest IP is; restore it, the reconnect's merge moves back |
| `API-PLUS-PLUS.md` gains a section: joining, levels and login | `ribbit/docs/API-PLUS-PLUS.md` | the book's Part XII¾ picks it up at the next build |

### Phase 1: mechanical, to learn what it does

1. Build stock; run `ng test`, `test:board`, `tools/e2e.js`; record pass/fail and wall time. Baseline the two-scribe case the suite doesn't have: two devices, Report Packet from one, merge on the other, with clashes.
2. `RANGERTRAK-SUITE-MAP.md`: 137 spec files and the e2e scenarios, each applicable or not and why.
3. `study-*.md`: message numbering and clash handling (`domain/msg-no.ts`, D-68); merge (`mergeEntries`, `revisedAt`, `handled` by later `at`); the overdue bands; the Board protocol; the roster join (`uid`/`id`/`callsign`, D-42); storage and migrations.
4. Mechanical replacement, one service at a time, suite after each: `RecordStore.setItem(whole log)` becomes a cell write per entry; `RadioLogService`'s in-memory array becomes a held view on the log family; `RangerService`, `MissionLocationService`, `MissionService` the same. The screens don't know.
5. Bad data: two scribes typing at once, a phone with a wrong clock, an entry edited on two devices, a device killed mid-entry, 5,000 entries. What stock does with each is the contract where the suite is silent.
6. `QUESTION-3.md`: every red, with its decision.

Phase 1 ends when the two-scribe baseline runs on the memory with no packet, no merge and every applicable spec green.

### Phase 2: greenfield on the memory

1. `DESIGN.md`: the truth table above, final.
2. The three participants, one at a time, red first: the entry cell and the held log view; the roster; places; handled; the Post's 309 writer; the served board page.
3. Delete, in this order, with the spec map saying which specs retire with each: Report Packet, hand-over, merge, Board, backup/restore/safety copy, service worker, tab lock, `RecordStore`, migrations.
4. The suite, unmodified, the bad data, and the bench after each step.

Checkpoint files in this directory: `RANGERTRAK-SUITE-MAP.md`, `study-*.md`, `QUESTION-3.md`, `DESIGN.md`, `RESULTS.md`, `README.md`, `VERSION`.

## The multi-site demo

Three posts, one region, one field phone. Each post is a node with its own Wi-Fi (the installed nodes from the October gates, or three namespaces on one machine for the rehearsal, the way `netns-region-loop-test.sh` does it). The region host is a small machine every post reaches; it holds admission, not data. Scribes at posts A and B are laptops on their post's Wi-Fi: no join, DHCP clients. The scribe at post C logs in at the Scribe level and the field phone at the Field level, each ending on its own tunnel to the RAM host.

The region host is the only thing in the middle, and it holds no entry; pull it and every post keeps working on its own Wi-Fi.

What Cornelison does, in order, at his own keyboard:

1. Types an entry at post A. It is on B's and C's Radio Log and Map before he looks up; the phone shows it too.
2. Logs an entry on the phone from the parking lot. It appears at all three posts, numbered as the phone's station, no packet, no merge, no clash dialog.
3. Pulls post B's uplink. B's scribe keeps typing; A and C keep each other; B's entries are B's alone for the moment.
4. Plugs B back in. B's entries are on every screen within seconds of the tunnel's handshake, because each device's held read resumes past the last id it saw. Nothing was restored.
5. Prints the 309 at post C. Every entry from every post and the phone is on it, in write order, with the marks.
6. Opens the board page on a phone on post A's Wi-Fi. Same cells, read-only, no server was started.

What is deliberately not in the demo: anything requiring the Internet (geocoding stays off, coordinates only), and anything RangerTrak removed in the conversion, which is the point of step 6.

## What is measured and reported

Side by side against stock, same driver, on at least two machines with traffic between them, as the reference process requires (`RESULTS.md`, tied to a `VERSION`):

| measured | stock RangerTrak | on FrogNet |
| --- | --- | --- |
| Entry typed at one post to visible at another | a Report Packet: minutes, by hand | time from the write to the trigger on the other device, in ms |
| Two scribes logging at once, 500 entries each | merge, clash report, renumbering dialog | one log in write order; clash count must be 0 by construction |
| Post loses and regains its uplink | nothing moves until a file does | time from the handshake to the last missing entry on screen |
| The suite | `ng test`, `tools/e2e.js`: pass counts and wall time | the same, with the retired specs listed and why |
| What was removed | | statements and files: the table in *What is thrown away*, counted, not estimated |
| What remains | | `domain/` unchanged, screens unchanged, the three participants with their line counts |

Qualification (the contract holds: the suite, the forms, the coordinates) and characterization (what the demo's hardware sustains: entries per second, devices per post) are reported separately.

Not claimed: anything about the Internet-dependent features (geocoding, Leaflet's online tiles); phone battery under the member's tunnel (not measured until it is); more than the demo's three posts; behaviour with the region host destroyed rather than cut (the October gates tested the cut, not the destruction). A reader who wants more than this has the checkpoint files and the suite and can run them.
