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
