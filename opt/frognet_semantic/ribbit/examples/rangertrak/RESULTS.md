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
