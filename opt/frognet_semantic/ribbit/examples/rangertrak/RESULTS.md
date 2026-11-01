# RangerTrak on FrogNet: results

Each run is tied to the RangerTrak commit it ran on and to this tree's build. Numbers are measured, not estimated.

## Phase 1, step 1: stock, as it is (2026-10-31)

RangerTrak `b6745f49` (SteadyEOC/RangerTrak, `fix(map): E-266 offline save keeps going past failed tiles`), cloned
outside this tree. One machine (the session's container: 2 cores, Node 22.23.4, npm 10.9.9, Chromium 153 headless).

What was changed to run it here, and only this:

- `xlsx`: the stock `package.json` pins `https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz`; that host is refused from
  this container (403), so the install used `xlsx@0.18.5` from the npm registry. Roster and places import from
  spreadsheets go through it; any red there below is to be read with that in mind.
- `tools/e2e.js` launches Chrome itself; as root in a container Chrome needs `--no-sandbox`, given by a wrapper named in
  `CHROME_BIN`. The driver is unmodified.

| Suite | Command | Result | Wall |
| --- | --- | --- | --- |
| Unit (Karma, 137 spec files; 40 of them `src/app/domain/`) | `ng test --watch=false --browsers=ChromeHeadlessCI` | 1440 of 1440 SUCCESS | 119 s (78 s in the browser) |
| Board server | `npm run test:board` | 77 of 77 | 4 s |
| Worker | `npm run test:worker` | 41 of 41 | 1 s |
| Regional demos | `npm run test:regional` | pass (rc 0) | 1 s |
| Build | `npm run build` | rc 0 | 40 s |
| End to end, default set | `node tools/e2e.js` against `tools/serve-dist.js` | 366 of 371 | 332 s |

The five e2e reds, on stock, before anything is converted (the contract where the driver is red already):

- clicking the status-cluster pill navigates to Entry: expected `/`, got `/rangers`
- ...and reopens the welcome panel: expected true, got false
- ...and cleared the dismissed flag: expected null, got `"true"`
- clicking the readiness dot inside the pill still goes to Mission, not hijacked: expected `/mission`, got `/rangers`
- E-207 / D-62 (Simple view hides unused advanced controls): the harness threw `TypeError: Illegal invocation`

Size, as counted here: 47,506 lines of TypeScript outside the specs; 7,282 of them in `src/app/domain/`.

Not yet run: `e2e --full`, the two-scribe baseline (Report Packet from one device, merge on the other, with clashes).

## The app's pages, first cut (2026-10-31)

`opt/fdf/src/fdf-source/fdf/www/rt` (`log.html`, `rt.js`, `rules.js`), [RT_PAGES_V1]: the mission, the Entry form and the Radio Log.
`test_rt_pages.py` there: a real rina-host, two posts (two web servers, each a node's `ram.php`), three devices in
Chromium (scribe A at post A, scribe B at post B, a field phone on post A):

| Check | Result |
| --- | --- |
| A mission started at A on B's list | 0.13 s |
| An entry typed at A on B's Radio Log | 209 ms |
| The phone's entry on A and B, numbered as the phone's station (`F001`) | 189 ms |
| Message numbers, one count per device, in write order | `A001 F001 B001 A002` |
| Two scribes logging at once, 25 each | 54 on each screen, 54 distinct numbers, the same order on both, 7.5 s |
| B reloads | all 54 back from the RAM; its station still B |
| A handled mark set at B, on A | ✓ 188 ms, → 89 ms |
| A ranger added at B, in A's callsign list | 58 ms; B's roster shows when it was last heard |
| The ICS-309 at A | 54 entries in write order, number, mark, callsign with the ranger's name |
| The map at B | each callsign at its newest position; a new position logged at A moves B's marker in 236 ms |
| Post B's uplink cut (its system space on its own LAN RAM manager) | B keeps logging, its 27 own entries on its own RAM; A does not see B's new ones |
| B's uplink back (the region's RAM again) | B's cut-off entries on A in 7.1-7.5 s; A's entry from the cut on B; 58 rows on both |
| The board on a fresh phone at A, nothing set up | all 58, newest first, every team's last word; read-only |

How the cut is survived: each answer from `ram.php` says which RAM manager the node is on; a page that sees it change
reads everything there and writes this device's own entries again where they are missing (memory is never moved; each
owner writes its own). A held read lasts at most 8 s, the longest a page stays on a RAM manager its node has left.

Stock RangerTrak's equivalent of the second row is a Report Packet: a file, carried by hand, merged on the other device.
Not yet in the app: offline basemap areas, Messages (213), Summary, After-action (214), the 213 form, the post's 309 file,
spreadsheet roster import.

## Step 1 and step 2: the app's RAM on a droplet, and the pages following it (2026-11-01)

One machine (the session's container, 2 cores). Every member is real: its own session (`fdf-client`, restarted when it
ends, as its unit does) and its own `fdf-app serve`, both reaching the network RAM at the member's one address for it.
A split is emulated only as the fabric presents one: behind that address, another RAM (`rinab/tools/netcut.py`).

`rinab/tools/test_fdf_app.py`: four members, the app on three (RAMs general and hospitals).

| Check | Result |
| --- | --- |
| Each RAM served by the member the selection names (c), answering there | general and hospitals on c |
| The strongest member, which has no app | never serves |
| Every member with the app maps the RAMs the same (`app-where.json`) | a, b, c: both on c |
| c's session ends | both on b in 172 / 315 ms; c stops serving |
| c's session back | both on c again in 246 / 364 ms |
| b cut off | b writes its configuration and capability on its side's RAM and serves both there, 1.07 s after; a and c still on c |
| b back | b maps both to c again and stops serving |

`fdf/www/rt/test_rt_pages.py`: two posts, each a member as above; the radio log's services in the app's general RAM
(apps.json), served by the selection's member (A); `ram.php` reads the post's `app-where.json`.

| Check | Result |
| --- | --- |
| The general RAM before any page works | A's droplet, on both posts |
| Checks 1 to 10 (as above) | all pass; A's entry on B in 225 ms, the phone's in 260 ms, B's map moved in 194 ms |
| B cut off | the general RAM on B's side is B's droplet 1.03 s after; B logs there and keeps its 27 own entries; A does not see them |
| B back | the general RAM is A's again on B 0.02 s after; both cut-off entries on A 0.5 s later; 58 rows on both |
| The board on a fresh phone at A | all 58, newest first |

## Step 3: positions are each device's own (2026-11-01)

`(rt-pos, <mission>, <dev>)`, written by the device as its GPS moves (from the Radio Log and Map pages), watched by it
so that after a RAM move its last fix is written again. The map is a query over those cells; how long since each was
heard is the map page's own clock. A position in a log entry stays the scribe's record and is not on the map.

| Check (`test_rt_pages.py`) | Result |
| --- | --- |
| B's map (scribe A and the phone give a position; B's browser gives none) | 2 devices, 2 markers |
| The phone walks | its marker moves on B's map in 232 ms; nothing logged (54 entries before and after) |
| During the cut, B's map | no device from A's side |
| B back | both devices on B's map again |
| Checks 1 to 9, 11, 12 | pass |

## Step 4: the truth-table corrections (2026-11-01)

| Correction | How | Check (`test_rt_pages.py`) |
| --- | --- | --- |
| Station ID = the device's hostname | `ram.php?op=whoami`: the lease the post gave the device's address; nothing typed | each device's station is its lease name (`laptop-a`, `laptop-b`, `phone-f`); numbers `laptop-a-001`, `phone-f-001` |
| Handled marks per handler | `(rt-handled, <mission>, <entry>.<dev>)`; the mark shown is the newest across handlers | B marks ✓ then →, A marks !: ! shown on B in 110-154 ms; the RAM holds one cell per handler |
| Own truth kept on the device | every write also in browser storage; on a RAM change every family the device owns is read there and what is missing written again | B reloads during the cut: its 28 own entries back on its side; B's position on its side's map |
| The mission the device is on | the device's own, opened on load whatever that side's RAM holds | the reload during the cut |
| 309 written in write order, once each | `rt-post309` on each post: held reads on `rt-log`, appended by id once, a new RAM read from the start | A's file 57 rows in A's order, each once, after a restart before the cut; B's 57, each once, its cut entries first |
| Overdue on the page's own clock | the board and the map redraw their bands on a timer, with no write | |

In the first run of this step a handled mark reached A in 8.1 s instead of ~100 ms. The cause was found in step 5
(below): the post's web server ran out of workers, because each page held one request per family.

## Step 5: team RAMs, messages, direct messages (2026-11-01)

The app's RAMs are general and hospitals, each served by the droplet its own selection picks. `rt-team` is mapped to
`"team"`: a team's messages go to `<app>.<team>`. Announcements (`rt-msg`) and direct messages (`rt-dm`, in the
addressee's variable, by station ID) are in the general RAM. Each device writes its own directory cell (`rt-who`). The
device's team is its own fact, set on the Radio Log page.

| Check 14 (`test_rt_pages.py`) | Result |
| --- | --- |
| A announces | on B and the phone in 0.21-0.33 s |
| The phone sends B a direct message | on B in 0.04-0.13 s; not in A's direct list |
| B writes to its team (hospitals) | on the phone (hospitals) in 0.06-0.17 s; the cell is in the hospitals RAM, none in the general RAM |

**Found on the way: one web server worker per held read.** The first run passed, but slowly: 2.5, 5.2 and 6.5 s. The
pages' own log ([DIAG-WATCH]) showed the phone's write asked at :31 and answered 5194 ms later. Post A's server log
showed it accepted by a worker only at :36: every worker was held by a page's held reads, one per family, 4-6 per
page. Fix: `ram.php?op=watch` holds all of a page's families in one request, and `rt.js` keeps one held request per
page; a page adding or dropping a family ends it, and the door frees its worker when the page has gone. After the fix,
all checks pass and the times are in the table.

A direct message needs no check: it is in the addressee's own variable, `(rt-dm, <mission>.<station>)`, which only
the addressee reads; the sender keeps its copy as its own truth. A team's RAM is checked by its droplet (step 6).

## Step 7: the 213, the Summary and the 214 (2026-11-01)

| Screen | What | Check |
| --- | --- | --- |
| ICS-213 | opt-in per entry on the Entry form (to, subject, message, in the scribe's entry); `print213.html` fills the form's fields as RangerTrak's `ics213FieldsFromReport` does, with the full number; the reply block and signatures stay blank. The post writes each asked-for 213 once (`rt-post309`, `rt/213/`) | B logs one; A prints it (To `IC, EOC`, Subject, Message, mm/dd/yyyy, 24-hour, `26-0142 / OP 2 / laptop-b-029`); post A's file has it |
| After-action / ICS-214 | `(rt-notes, <mission>, <dev>.<ms>)`: 214 lines (a time, logged late if need be, and a line) and after-action notes (area, about the incident or the app); `print214.html` | A's two lines on B's After-action in 0.07 s; A's ICS-214 in time order, not entry order |
| Summary | `rules.js summarizeMission`, RangerTrak's `domain/mission-summary.ts` on these cells: totals, time buckets, status, team, call sign, scribe, needs attention, notes by area; whole log or a window | B's Summary: every entry, the flagged ones as needing attention, the note under Communications |

`test_rules.mjs`: RangerTrak's own summary cases and the 213 field mapping, 22 of 22. The cases that need fields
these cells do not carry (rangerUid, tactical calls, source, late entries, revisions, per-mission status lists) are not
ported. The summary leaves those totals out rather than show a zero it did not measure.

## Step 6: roles, login, the droplet's check, the client's focus (2026-11-01)

The admin sets up roles. A role is activated at an individual's login, or a client takes it as its default
(Team vs EOC). The network's levels play no part. The droplet answers logins and gives a grant (HMAC under the app's
key). Every droplet checks it on every read and write (`rina-host`, `RINA_APP_ACCESS`, `tools/apply_app_access.py`).
The device keeps its grant and writes its access record again after every RAM change.

| Check | Result |
| --- | --- |
| `test_fdf_app.py` 7: the access definition in each droplet's configuration tuple | roles, users with verifiers; no password and no key in the network RAM |
| `test_fdf_app.py` 8: login and the check | pat's password gives hospitals; a wrong one nothing; `command` (a default role) needs no password; `hospitals` does. Pat: general and hospitals RAMs written. Command's grant on hospitals, no grant, and a forged grant: refused |
| `test_fdf_app.py` 8: relocated to b | pat's grant from c works on b's hospitals RAM, with no new login |
| `test_rt_pages.py` 0: B and the phone log in (kim, lee: hospitals) | the role shown beside the station |
| `test_rt_pages.py` 11: the devices' access records | B's written again on its side's network RAM during the cut; all three on the region's |
| `test_rt_pages.py` 14: the hospitals RAM without a hospitals grant | refused: "log in first (no grant)" |
| `test_rt_pages.py` 16: the client's focus | an EOC client (default role, no password) opens on the Summary; no role on the Radio Log; kim on the hospitals messages |

Not yet: the admin app is the `fdf-app config` command line, not a screen.
