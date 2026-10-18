# FrogNet Kart — a Ribbit demo in two regions, with calls through the real Communicator

A top-down kart racer whose multiplayer is Ribbit shared memory. It maps a second, independent region for the call
strip at the same time. One application, two independently governed memories, composed on screen.

- **race region** (`race/kart/<id>`): each kart writes its pose and reads everyone else's.
- **comm region** (`comm/tile/<id>`): each player publishes a tile (presence, status, whom they are calling).
  The two regions know nothing about each other; the app maps both.

## Robots and the autopilot `[KART_AUTODRIVE_V1]`

`--bots N` adds N robot karts that this game owns and drives, named `<name>-bot1` … `<name>-botN`, each in its own
colour. A robot is a kart cell like any other: every player on every machine sees it the same way it sees a person's
kart, and nothing anywhere knows it is a robot except its owner. Robots follow the track in their own lane at their
own pace, wander across the road a little, and step aside for a kart just ahead.

`--autodrive` puts your own kart on the same autopilot from the start; `P` turns it on and off during play.
`--headless` runs with no window and no sound, for a machine that is only there to put karts on the track:

    python kartdemo.py --name nyc --race-host H:8799 --bots 4 --autodrive --headless

## The memory is memory (DESIGN.md is the truth table)

The game is a multithreaded program whose threads are on different machines. Each game writes its own truths at its
own tuples, once, and never waits; it reads everyone else's from the region, and held reads are its callbacks.

- `[KART_ON_THE_SESSION_V1]` **One permanent FNW1 session per region**, through this tree's own C++ session client
  (`kartram.cpp`: a C interface over `frogram::Session`, built as `libkartram.so` / `kartram.dll`). A write is posted:
  it goes out and the game carries on. There is no plain-HTTP path and no second way in.
- `[KART_WRITES_ON_CHANGE_V1]` **Written when it changes.** A kart carries its heading, speed and turn rate; every
  reader draws it carried forward along that arc, and the owner computes the same thing and writes again only when
  its truth is more than 2 px or 0.05 rad from it, or its lap changes. A tile is written when what it says changes.
  Nothing is written to stay fresh.
- `[KART_HELD_READS_V1]` **Held reads, no polling.** Each family is read once, then held: the region answers when
  something is written to it, with only what was written.
- `[OWNED_CELL_GOES_WITH_THE_SESSION_V1]` **Gone is gone.** Every tuple the game writes is its session's. When the
  game's session ends (quit, crash, kill -9, the machine drops) the region writes each of them `{}`, every other
  game's hold fires, and the karts and tile vanish. No timeout decides who is still here.
- FD&F's member list is re-read when FD&F's held participant wait answers (on the game's own FD&F session), not once a second.

What that changed, measured by `tools/kart-check.sh` (three machines, 11 karts):

| | before (2026-10-17) | now |
|---|---|---|
| writes per kart | 60/s (every frame), plus a tile every 0.15 s | 2.6 to 4.0/s; the tile only on a change |
| reads | every kart every 40 ms and every tile every 200 ms, changed or not | held: one answer per change |
| connections | a new TCP connection with a plain-HTTP request per call | one FNW1 session per region |
| a player who leaves | drawn until its cells are 4 s old | gone everywhere ~100 ms after its session ends |

The frame rate is 62 fps either way; before this, writing on the frame loop had cost 27 fps at 30 ms and 10 fps at
80 ms to the region (fixed 2026-10-17, measured through a delay relay: this kernel has no netem).

`[KART_REPORT_V1]` A region whose session breaks is said once, with the cause; the game opens a new session a second
later, says "working again", and writes its own truths again. `ribbit.py` raises `RibbitError` rather than returning
nothing (`[RIBBIT_PY_SAYS_WHY_V1]`).

## What is real and what is not

| piece | state |
|---|---|
| the race (karts, laps) over the race region | real |
| the tiles in the game's call strip | **synthetic**: a moving colour pattern, no camera |
| a call | **real**, in a separate window: the Call button on another player's tile launches `comms-app` (the desktop Communicator) against the Communicator's servers `[KART_CALLS_V1]`; the callee's game sees the call in the caller's tile and opens its own `comms-app`, where the call rings |
| who may call | with `--fdf`, only machines that are FD&F members `[FDF_GATED_V1]`; the bottom line says whether this machine is one |

Real camera and audio happen only in the Communicator window, never inside the game's tile.

## Files

- `kartdemo.py` — the game, the robots and autopilot, the call strip, the Call buttons and the FD&F lookup.
- `ribbit.py` — the game's memory in Python: one session per region over `libkartram` (ctypes).
- `kartram.cpp`, `build_kartram.sh` — that library: `libkartram.so` (Linux x86-64, needs libcrypto.so.3 and glibc 2.38
  or later, i.e. Ubuntu 24.04) and, with `--windows`, `kartram.dll` (64-bit, static).
- `DESIGN.md` — the truth table.
- `test_owned_cells.py` — the region's owned-cell checks (run by `tools/kart-check.sh`).
- `kartdemo.original.py` — the version before calls and FD&F, kept for comparison.

## What has to exist first (not in this directory)

- **The race region server.** `rina-host` from `rinab/tools/build.sh`, started by `/opt/fdf/bin/fdf-kart-ram.sh`
  (port 8799, the `/ram.php` front). It is in the droplet package (`fdf-droplet.tgz`); there is no `kart-ram`
  program.
- **The Communicator's servers.** `comms-ram` (8800) and `comms-media` (8994), from the Communicator tree,
  started by `fdf-services.sh` on the droplet or `fdf-comms.sh` on a node.
- **comms-app.** Built from `communicator-r3-full.zip` (`comms/WINDOWS.md` on Windows). Without it, the Call button
  prints `kartdemo: cannot start the Communicator (...)` and the race carries on.
- **pygame** on each player's machine, and `libkartram.so` (Linux) or `kartram.dll` (Windows) beside `ribbit.py`.
- **The region at r21 or later** (`fdf-2026-10-18-r21`): own-cell writes need it. An older `rina-host` takes the
  writes but never removes a departed player's karts.

## Run

On the droplet, in one terminal (the race on 8799, FD&F on 8840, the Communicator on 8800/8994):

    /opt/fdf/bin/fdf-services.sh start

Each player, located through FD&F:

    pip install pygame
    python kartdemo.py --name red --fdf 165.232.153.144:8840 --comms-app <path to comms-app> --comms-args "--camera video=<name> --mic 1"

Or with the addresses given directly (no FD&F, no member check):

    python kartdemo.py --name red --race-host H:8799 --comms-ram H:8800 --comms-media H:8994 --comms-app <path>

Controls: arrow keys or WASD; `P` toggles the autopilot. `--comm-host H:P` puts the tile region on another server, to show that the two regions
are independent. `--laps N` (default 3).

## Options

| option | default | what |
|---|---|---|
| `--name` | a random colour | the player |
| `--fdf H:P` | none | FD&F's RAM server: where the race and the Communicator are come from its `fdf.where` cells (`race`, `comms`), and who is a member from its participants. kartdemo starts this machine's `fdf-client` if it is not running (`--fdf-client`, else beside the script, else `/opt/fdf/bin`). It exits when FD&F does not say where the race is and no `--race-host` was given. |
| `--race-host H:P` | from FD&F, else 127.0.0.1:8799 | the race region |
| `--comm-host H:P` | the race host | the tile region |
| `--comms-ram H:P` | from FD&F, else race host:8800 | the Communicator's memory |
| `--comms-media H:P` | from FD&F, else race host:8994 | the Communicator's media server |
| `--comms-app PATH` | `comms-app.exe` / `comms-app` on PATH | the Communicator program |
| `--comms-args "…"` | none | more options for comms-app, e.g. `--camera video=<name> --mic 1` or `--synthetic 640x360` |
| `--bots N` | 0 | robot karts this game owns and drives |
| `--autodrive` | off | your own kart starts on the autopilot (`P` toggles it) |
| `--headless` | off | no window, no sound |

The game is not in any installed package: run it from this directory. `fdf-kart-ram.sh`'s header names
`/opt/fdf/kart/kartdemo.py`, which no package installs.

## Proven: the memory model, across machines (2026-10-18, `tools/kart-check.sh`, build fdf-2026-10-18-r21)

Each machine its own network namespace on one shared network, the region on the "droplet": red (3 robots), blue
(1 robot), and "nyc", a machine that only runs 4 robots. 10 of 10 checks pass, on the shipped files (fdf-droplet.tgz's rina-host, kart-r21-linux.tgz's game):

- every game sees every other machine's karts (7, 9 and 6: 11 minus its own); every game at 61.7 to 62.0 fps;
- 2.6 to 4.0 writes/s per kart; all 11 karts lap on the road (2.2 to 3.4 laps in 25 s, at most 50.5 px off the centre-line, the road 62 px each side);
- 0 packets between two player machines (tcpdump on the shared network): everything is through the region;
- red's game killed -9: its 4 karts and its tile are `{}` in the region 98 ms later, and blue and nyc stop drawing them;
- the region killed -9 and started again: both games say FAILING, then "working again", and their 7 karts are back;
- the owned-cell checks;
- with `--fdf` (FD&F's RAM manager on the droplet, each machine its own hostname): blue and nyc start their own
  fdf-client and draw only each other's karts, not red's (red runs without FD&F); nyc's fdf-client killed -9, and blue
  stops drawing nyc's karts at FD&F's next answer (within 2 s);
- a player's path dies silently for 12 s (its link down: no reset): its game stays at 61+ fps, says FAILING when the
  session's 5 s keepalive gives up, opens a new session when the path is back, and its karts are in the region again.

Red/green: against the r20 region (no owned cells) check 6 fails (red's karts still there 4 s later, still drawn by
both other games) and check 8 fails; against r21 both pass.

**Not run:** on Windows (`kartram.dll` is built, not run), and across real machines.

## Proven: robots, autopilot, writer threads (2026-10-17; the writer threads are replaced by the above)

Against `rina-host` built from this tree by `tools/build.sh` (build fdf-2026-10-12-r20), on one Linux machine, all
players headless:

- Two games, red with 5 robots and blue on the autopilot with 2 robots, sampled from the region every 0.25 s for 40 s:
  every robot and blue lapped continuously, 3.4 to 4.5 laps each, never stopped, and the farthest any of them got
  from the centre-line was 50 px on a road 62 px wide each side.
- The frame-rate table above.
- The region stopped mid-race and started again: each of the five region calls said `FAILING` with
  `Connection refused`, then `working again`; the game held 61 fps throughout.
- **Not run:** across real machines, and on Windows.

## Proven (rinab/experiments/RESULTS.md, "kart calls")

- The call flow: two players under SDL's dummy driver, a stand-in comms-app that logs its arguments, alice pressing
  Call on bob through the test switch `KART_TEST_CALL`. alice launched `--name alice --auto-join bob`, bob's game
  launched `--name bob`, and the tiles read alice "calling bob" and bob "in a call". The evidence it cites
  (`evidence/kart-calls/`) is not in this package.
- fdf-comms.sh with stand-in binaries: comms-ram first, then comms-media; with none present, it says so.
- **Not run there:** the real comms-app, comms-ram and comms-media, and a real click.
