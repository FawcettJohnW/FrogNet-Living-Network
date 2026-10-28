# FrogNet Kart: shared memory creating and managing games

This demonstrates a capability. It is not a game demo. The kart racing is what the shared memory manages:
- a lobby, chat, and parameters agreed by a service;
- a machine chosen to serve each game, and the game's memory started there and filled;
- everyone finding the game;
- results and records kept across runs.

The players and the service never ask each other anything. Each one writes its own truths into the memory and acts on
the others' truths. `DESIGN.md` has the truth table and the service's rules.

## Who is in it

- **The kart memory and the kart service.** This is where everyone connects first. On the droplet,
  `fdf-kart-ram.sh` runs both: the kart memory (a rina-host on 8799, kept in `/opt/fdf/var/kart.perm`) and
  `kart_service.py` beside it. The service is the lobby's intelligence. It reads the lobby and agrees the
  parameters (the laps most players want). It decides when a game launches: when all free players are ready, and at
  least `KART_MIN_PLAYERS` of them; or when a ready player presses Start. It also decides where each game runs.
- **Game-serving machines** (`kart_host.py`). Each one writes that it can serve a game's memory and where racers reach
  it. A **dedicated** one is set up with `fdf-kart-host.sh`. A **player's** game also runs a host agent for its own
  machine when that machine can host (Linux, with a rina-host). The service picks a dedicated machine if one is up,
  else a player's machine: the one serving the fewest races, then by name. If the chosen machine cannot start the
  race server, its agent says why, and the service names the next machine and says so in the chat.
- **Players' games** (`kartdemo.py`). Each one writes its player into the lobby (machine, robots, the laps wanted,
  ready), plus its chat lines and its call tile. It reads the service's decisions and the race servers.

## What happens

1. Everyone maps the kart memory. Players chat and set the laps they want and Ready. The lobby shows the agreed laps,
   the service's state, the game-serving machines and the records.
2. The service launches a game. It writes `(kart, launch, <race>)` naming a machine, and says so in the chat.
3. The named machine's agent starts `kart_server.py`. The race server:
   - starts the game's memory (a rina-host on a port the system picks);
   - fills it from the kart memory with the grid (fastest record in front), the racers' records and the start time;
   - only then writes `(kart, server, <race>)` with its address.
4. Every racer's game sees the server tuple, maps the game's memory, takes its grid slots and races.
5. Before it exits, the race server writes the result and each racer's record back into the kart memory, as
   permanent tuples. It then ends its game memory. The racers are back in the lobby, not ready, and the service posts
   the winner.
6. Results and records are `[PERMANENT_CELL_V1]` tuples. The kart memory files them and writes them back when it
   starts again.

When a participant is gone, its truths go with it. A game that quits takes its lobby tuple, tile and karts with its
session. A host agent takes its game-serving machine. A race server killed outright takes its server tuple, and its
racers return to the lobby (`[OWNED_CELL_GOES_WITH_THE_SESSION_V1]`).

## Run

On the droplet, `fdf-services.sh` starts `fdf-kart-ram.sh` (the kart memory and the service). `KART_MIN_PLAYERS`
sets the auto-launch count (default 2). A dedicated game-serving machine (any Linux node with the node tarball) runs:

    fdf-kart-host.sh <droplet>:8799 [<droplet>:8840]     # with FD&F: racers reach it at the address it announced

Each player needs `pip install pygame`, with `libkartram.so` or `kartram.dll` beside `ribbit.py`:

    python kartdemo.py --name red --fdf <droplet>:8840     # FD&F says where the kart memory is (fdf.where race); members only
    python kartdemo.py --name red --kart <droplet>:8799    # straight to the kart memory
    python kartdemo.py --name red --solo --bots 3          # alone: the game starts a kart memory and service on this machine

`--solo` keeps the kart memory in `~/.frognet-kart/main.perm`, so a solo player's records survive across runs.

Controls:
- **In the lobby:** type and press Enter to chat; Ready; Start (once ready); − / + to set the laps you want.
- **Racing:** arrows or WASD; P toggles the autopilot.

## Options

| option | default | what |
|---|---|---|
| `--name` | a random colour | the player |
| `--fdf H:P` | | FD&F's RAM manager. It says where the kart memory is, and only FD&F members are in the lobby. If this machine is not a member, the game starts its `fdf-client`, found from `--fdf-client`, else beside the script, else `/opt/fdf/bin`. |
| `--kart H:P` | | the kart memory |
| `--solo` | | start a kart memory and a kart service on this machine |
| `--min-players N` | 1 | `--solo` only: the service launches by itself at N ready players |
| `--laps N` | 3 | the laps you want; the service agrees on the laps most players want |
| `--bots N` | 0 | robot karts this game owns and drives |
| `--autodrive` | off | your own kart starts on the autopilot (P toggles it) |
| `--headless` | off | no window, no sound |
| `--rina-host PATH` | beside the script, else `/opt/fdf/bin` | the rina-host this machine serves games with. If it is given and missing, this machine does not host. |
| `--comms-ram`, `--comms-media`, `--comms-app`, `--comms-args` | from FD&F, else the kart memory's host:8800 / :8994 | the Communicator, for the Call buttons (`[KART_CALLS_V1]`) |

## Ports

On the droplet, the kart memory uses 8799, which the kart region already had. The service opens no port. A game's
memory listens on a port picked by the serving machine's system. That machine's rina-host logs the port
(`[BOUND_PORT_SAID_V1]`) and its race server writes it into the kart memory. Racers must be able to reach the
serving machine on that port. A dedicated host behind a firewall needs its ports open to the players.

## The memory is memory

- **`[KART_ON_THE_SESSION_V1]`** Each participant has one permanent FNW1 session per memory, through this tree's C++
  session client (`kartram.cpp`, built as `libkartram.so` / `kartram.dll`, used through ctypes in `ribbit.py`).
  Writes are posted and never waited for. One exception: the race server's write-back as it exits.
- **`[KART_HELD_READS_V1]`** Each participant follows its whole service with one held read. The memory answers when
  something is written, and that answer is the callback. The service decides inside that callback.
- **`[KART_WRITES_ON_CHANGE_V1]`** Truths are written when they change. Nothing is written to stay fresh.

## Files

- `kart_service.py`: the kart service, the lobby's intelligence.
- `kart_host.py`: a game-serving machine's agent.
- `kart_server.py`: the race server. It handles the game's memory, its filling, lap times and the write-back.
- `kartdemo.py`: the game. It covers the lobby screen, chat, racing, robots and the call strip.
- `kartmem.py`: one participant's view of one memory: a session, a held read on its service, reconnects.
- `ribbit.py`: the memory in Python over `libkartram` (ctypes).
- `kartram.cpp`, `kartram_glibc.cpp`, `kartram.map`, `build_kartram.sh`: that library.
  - `libkartram.so`: Linux x86-64; needs libcrypto.so.3 and glibc 2.35+ (Ubuntu 22.04 and later).
  - `arm64/libkartram.so` (`--arm64`): the same for arm64.
  - `kartram.dll` (`--windows`): 64-bit, static.
- `DESIGN.md`: the truth table and the service's rules.
- `test_owned_cells.py`: the region's checks for owned and permanent cells.
- `kartdemo.original.py`: the first version, kept for comparison.

## Proven

Run on 2026-10-19 with `tools/kart-check.sh`, against the shipped fdf-2026-10-19-r23 files: the droplet tarball's
rina-host and fdf-client, and the player tarball's game.

Setup:
- Each machine is its own network namespace on one shared network.
- The "droplet" runs the kart memory and the kart service (`--min-players 3`).
- red: 3 robots, wants 2 laps, can host.
- blue: 1 robot, wants 2 laps, cannot host.
- nyc: 4 robots, wants 4 laps, can host.
- kbox4: a dedicated game-serving machine, which joins during race one.

11 of 11 checks pass:

1. The lobby (three players) and chat (blue's line, the service's lines) are in the kart memory.
2. The service agreed 2 laps: two players want 2, one wants 4.
3. With no dedicated machine, the service launches race one on a player machine, kbox1. Its race server runs there,
   and the kart memory says where.
4. In race one, every game takes its slots from the race memory and sees the other machines' karts (11 in all).
   Frame rate was 61.7 to 61.9 fps, at 2.8 to 3.5 writes/s per kart.
5. Race one ends by itself. The result (11 karts) and 3 records are written back, permanent, and the service posts
   the winner.
6. Once a dedicated machine is up, race two runs on it (kbox4), even though two player machines could host.
7. The dedicated machine is replaced by one whose race server cannot start. The service launches race three there.
   The agent says "its race server exited (1) before it was up", and the service launches the race on kbox1 and
   says so in the chat.
8. Players talk only to the kart memory and to the race hosts' race ports: 22,649 machine-to-machine IP packets,
   none anywhere else.
9. The kart memory is killed -9 and restarted from its file. 6 permanent tuples are written back. The service and
   every game reconnect by themselves, and the lobby and the agreed tuple are whole again.
10. Through FD&F, `fdf.where race` says where the kart memory is. red cannot join FD&F and is not in the lobby. blue
    and nyc are, and they race on nyc's machine at its FD&F address.
11. The owned- and permanent-cell checks pass (7 of 7).

Also run:
- **Solo:** the kart memory, the service and the host agent end with the game, even when it is killed -9.
- **arm64:** `arm64/libkartram.so` under qemu-aarch64 opens a session, posts a write and reads it back.

**Not run:** `kartram.dll` on Windows (it is built, not run), and anything on your machines.
