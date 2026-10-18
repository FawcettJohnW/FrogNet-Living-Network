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

## Every kart writes from its own thread `[KART_WRITER_THREAD_V1]`

Each kart a game owns (yours and each robot) is its own participant: it writes its own cell from its own thread,
always the newest pose, one write in flight. The frame loop never waits on the network. Before this, the game wrote
its pose on the frame loop, so the frame rate fell with the distance to the region:

| round trip to the region | before (fps) | now (fps) |
|---|---|---|
| local | 61 | 61 |
| 30 ms | 27 | 61 |
| 80 ms | 10 | 61 (with 3 robots and a second player) |

Measured here through a TCP relay adding the delay each way (this kernel has no netem). The tile is written off the
frame loop as well. Other karts are drawn at their last written pose carried forward along their heading for the
time since it was read (at most 0.3 s): rendering between reads, not reconciliation.

`[KART_REPORT_V1]` A region call that fails is said once when it starts failing, with the cause, and once when it
works again; `ribbit.py` raises `RibbitError` rather than returning nothing (`[RIBBIT_PY_SAYS_WHY_V1]`).

## What is real and what is not

| piece | state |
|---|---|
| the race (karts, laps) over the race region | real |
| the tiles in the game's call strip | **synthetic**: a moving colour pattern, no camera |
| a call | **real**, in a separate window: the Call button on another player's tile launches `comms-app` (the desktop Communicator) against the Communicator's servers `[KART_CALLS_V1]`; the callee's game sees the call in the caller's tile and opens its own `comms-app`, where the call rings |
| who may call | with `--fdf`, only machines that are FD&F members `[FDF_GATED_V1]`; the bottom line says whether this machine is one |

Real camera and audio happen only in the Communicator window, never inside the game's tile.

## Files

- `kartdemo.py` — the game, the robots and autopilot, the call strip, the Call buttons and the FD&F lookup (446 lines).
- `ribbit.py` — the pure-Python Ribbit client (HTTP `/ram.php`), stdlib only (54 lines).
- `kartdemo.original.py` — the version before calls and FD&F, kept for comparison.

## What has to exist first (not in this directory)

- **The race region server.** `rina-host` from `rinab/tools/build.sh`, started by `/opt/fdf/bin/fdf-kart-ram.sh`
  (port 8799, the `/ram.php` front). It is in the droplet package (`fdf-droplet.tgz`); there is no `kart-ram`
  program.
- **The Communicator's servers.** `comms-ram` (8800) and `comms-media` (8994), from the Communicator tree,
  started by `fdf-services.sh` on the droplet or `fdf-comms.sh` on a node.
- **comms-app.** Built from `communicator-r3-full.zip` (`comms/WINDOWS.md` on Windows). Without it, the Call button
  prints `kartdemo: cannot start the Communicator (...)` and the race carries on.
- **pygame** on each player's machine.

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

## Proven: robots, autopilot, writer threads (2026-10-17)

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
