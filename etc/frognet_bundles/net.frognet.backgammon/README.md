# Family Backgammon — UnREST bundle (pluggable unit)

A **build-native** bundle (no OSS backend — public-domain rules; the substrate is the
backend). The whole game is one shared element; a move is a DIFF; the codex ships the
**legal moves inside the state**, so the UI highlights from authoritative data and never
re-implements the rules. Authority = **perm** (a table is resumable — it survives a host
re-election and resumes on any box).

## Run it — standalone desktop app (no browser, no box)
Pure-stdlib Python + Tk. Runs on Windows, macOS, and Linux with nothing to install
(Windows and macOS python.org builds include Tk; on Linux install your distro's
`python3-tk`).
```
python3 app/bg_app.py                                   # play now, offline, hot-seat
python3 app/bg_app.py --connect family-backgammon.frognet   # join a table on the mesh
python3 app/bg_app.py --connect 10.111.11.1:80 --table kitchen
```
Same app, two backends: **local** (embeds the codex — instant, offline) or **net** (a
thin UnREST client to a codex on the mesh; the fabric does the codec). Tap a checker
(legal sources glow), tap a highlighted point to move; Roll on your turn; Double offers
the cube; bear off by tapping the glowing tray when all your checkers are home.

## Android app
Native Kotlin client in `android/` — a custom-view board, not a browser. Open the
`android/` folder in Android Studio, Gradle sync, Run. Edit `BackgammonClient(base=…)`
to point at the well-known name (on the mesh) or a host for testing. See
`docs/ANDROID_STUDIO.md`.

## Quick visual preview in a browser (dev only)
If you just want to eyeball it without Tk, a same-origin dev server serves a browser
build of the same board and codex:
```
python3 dev/serve_web_test.py        # http://127.0.0.1:8888/
```
The browser path is for previewing only — the real clients are the desktop app and the
Android app.

## Doctrine data plane — convergence on the served path (proven in sim)
`dev/convergence_sim.py` proves the served path **moves memory, not whole structures**,
on the real `SemanticCodec`: the resident template/scaffold crosses **once** per fresh
client (~693 B), then a move to a caught-up client is a **~105–161 B DIFF vs the 832 B
full state**, no change is a **0-byte SAME**, and the client **reconstructs full state
from deltas** (asserted equal to authoritative every step). Run it:
```
FROGNET_SEMANTIC=/path/to/frognet_semantic python3 dev/convergence_sim.py
```
`ConvergenceChannel` (FrogNet-side: per-client reference → SAME/DIFF/FULL) and
`ClientReplica` (app-side: hold the structure, decode deltas) are the reference pattern.
**Remaining work to be fully doctrine-complete:** the shipping Tk/Android/web clients
still full-state poll the dev server; they adopt the `ClientReplica` pattern to move
deltas instead of whole documents. The engine, codex, and the convergence itself are
proven; wiring it through the three clients is the open step.

## Test / baseline
```
python3 tests/test_backgammon_codex.py     # engine + codex, incl. a full game to a win
./run_baseline.sh                          # tests + verb smoke over HTTP, self-cleaning
```
Tests run with in-memory stores; on the box swap in the api.php transient + perm stores.
The real-codec integration test (I5) runs if your FrogNet tree is importable
(`FROGNET_SEMANTIC=/path ./run_baseline.sh`).

## Install (single tar)
```
sudo tar xzf family-backgammon_bundle.tar.gz -C /     # → /etc/frognet_bundles/family-backgammon
sudo /etc/frognet_bundles/family-backgammon/install_family_backgammon.sh
```

## Design intent
Not a web grid — a real set on a table: walnut frame, felt field, inlaid oxblood/ivory
points, beveled bone vs ebony checkers, pipped dice, a brass doubling cube. The board is
the hero; controls stay quiet. (I could not screenshot the JS-rendered board in the build
sandbox — no headless browser / SVG rasterizer — so eyeball it via the dev server; the
geometry and data flow are verified.)

## Codex verbs  (`/unrest/family-backgammon/<verb>?g=<table>`)
- `GET  get`              → full state `{points,bar,off,turn,phase,dice,dice_remaining,legal,cube,winner,version,ts}`
- `POST new_game`         → fresh table
- `POST roll`             → roll dice (doubles → 4 moves); computes `legal`
- `POST move {from,die}`  → apply one checker move (from=0 means the bar); consumes the die
- `POST offer_double` / `accept_double` / `decline_double`
- `POST presence {who}`   → `{who_is_here:[…]}`

## New over a stock backgammon app
- **Async / correspondence:** the table persists (perm); play live when both are online,
  or a move now and a move tomorrow — same element, different cadence.
- **Mesh-wide spectating:** anyone reads the state element; no server connection, no slot.
- **Resumable tables:** survives a host re-election / a box reboot — the field-team
  downtime scenario. Relocatable-brain rules apply (halt-don't-fork on split; the client
  self-detects a backwards-time read).
- **Cross-app:** the table sits in the same shared memory as the family calendar/wall.

## Box wiring TODO (VERIFY-AGAINST-INSTALL — don't invent)
1. TransientStore → api.php (confirm timestamp field + upsert params on the live endpoint).
2. PermStore → the perm host under its well-known name.
3. WSGI bridge: wire `codex/wsgi.py` verbs → `BackgammonCodex`; enable the Apache suffix.
4. Register `family-backgammon.frognet` via the box's standard path (renewing lease).
5. Per-player identity (currently shared-board hot-seat-over-the-network; act for the side
   on turn). Per-seat identity is the natural refinement.

## Rules notes (honest)
Standard rules implemented: movement, hitting to the bar, mandatory bar re-entry, bearing
off (exact + overflow from the highest point), doubles = 4 moves, turn passes when no
legal move, doubling cube offer/accept/decline. **Simplification:** the engine does not
enforce the "must use both dice / maximize pip usage" rule — it lets you play any legal
move and ends the turn when none remain. Fine for family play; note it before any
tournament use.

## Conventions
No `pipefail`. "Open source" only. Resolve names from the hosts model, never hardwire IPs.
Client is plain HTTP; the fabric does the codec. Wire-bound timestamps are strings; the
game uses a version+ts; ticks/versions are int-safe.
