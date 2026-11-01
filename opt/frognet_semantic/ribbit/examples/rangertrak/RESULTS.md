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

`opt/fdf/www/rt` (`log.html`, `rt.js`, `rules.js`), [RT_PAGES_V1]: the mission, the Entry form and the Radio Log.
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

Stock RangerTrak's equivalent of the second row is a Report Packet: a file, carried by hand, merged on the other device.
Not yet in the app: Rangers, Map, Messages (213), Summary, After-action (214), the printed forms, the handled marks,
the post's 309 file, the read-only board page.
