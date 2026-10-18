# FrogNet Kart: shared memory creating and managing games

This is a demonstration of capability, not of a game. The kart racing is what the memory manages: a lobby,
matchmaking, a race server started on some machine, everyone finding it, and results and records kept across runs.
None of it has a server of its own. There is one shared memory everyone maps (the main memory), a memory a race server
brings with it (the race memory), and programs that write their own truths and act on the others'. `DESIGN.md` is the
truth table and the rules.

## What happens

1. **The lobby.** Every game maps the main memory and writes itself into the lobby, `(kart, lobby, <player>)`: its
   machine, its robots, whether its machine can host a race. It waits until `--min-players` players are free and
   ready, or someone presses Start.
2. **A race is started on a machine.** The game that starts it writes a start intent in its own lobby tuple, naming
   the race, the roster and the host: itself if its machine can host, else the first player whose machine can. If two
   intents appear, the lowest race id wins, by the same rule in every game. The named host's game starts
   `kart_server.py` on its own machine.
3. **The race server is filled, then announced.** It starts its own memory (a rina-host on a port the system picks),
   reads the racers' records from the main memory, and writes the race into its memory: the grid (fastest player by
   record in front), the racers' records, the start time. Only then does it write `(kart, server, <race>)` into the
   main memory, with its address and port. Nobody sees a race server that is not ready.
4. **Everyone sees it happen and goes there.** Each racer's game holds a read on `(kart, server, *)`. When its race
   appears, it maps the race memory, takes its grid slots, counts down and races. The karts' truths live in the race
   memory; the call tiles stay in the main memory.
5. **Before it exits, the race server writes back.** When every kart has finished or is gone (or it is told to stop),
   it writes the result and each racer's updated record into the main memory as permanent tuples, says in its own
   memory that the race is over, writes its server tuple `{}`, stops its memory and exits. Every game is back in the
   lobby, showing the result and the records.
6. **Kept across runs.** Results and records are `[PERMANENT_CELL_V1]` tuples: the main memory files them before
   publishing them and writes them back when it starts again. The next race server is filled from them.

Gone is gone. A game that quits or dies takes its lobby tuple, its tile and its karts with its session
(`[OWNED_CELL_GOES_WITH_THE_SESSION_V1]`). A race server killed outright takes its server tuple with its session and
its race memory with it; every game returns to the lobby and no result is written for that race.

## Run

Each player (`pip install pygame`; `libkartram.so` or `kartram.dll` beside `ribbit.py`):

    python kartdemo.py --name red --fdf <droplet>:8840          # the main memory is FD&F's RAM manager; members only
    python kartdemo.py --name red --memory <host>:8799          # the main memory is any rina-host, e.g. the droplet's kart region
    python kartdemo.py --name red --solo --bots 3 --min-players 1   # alone: the game starts its own main memory

`--solo` keeps the main memory in `~/.frognet-kart/main.perm`, so a solo player's records survive across runs. With
`--memory`, the rina-host's own `RINA_REGION_FILE` keeps them (`fdf-kart-ram.sh` gives the droplet's kart region
`/opt/fdf/var/kart.perm`); with `--fdf`, FD&F's.

In the lobby: Start (or S / Enter). Racing: arrows / WASD; P toggles the autopilot.

A machine **can host** a race when it is Linux and has `rina-host` beside `kartdemo.py`, in `/opt/fdf/bin`, or given
by `--rina-host`. The Linux player tarball carries one. Windows plays and calls but does not host: someone else's
machine does.

## Options

| option | default | what |
|---|---|---|
| `--name` | a random colour | the player |
| `--fdf H:P` | | the main memory is FD&F's RAM manager; only FD&F members are in the lobby (the game starts this machine's `fdf-client` if it is not a member: `--fdf-client`, else beside the script, else `/opt/fdf/bin`) |
| `--memory H:P` | | the main memory is that rina-host |
| `--solo` | | start a main memory on this machine |
| `--min-players N` | 2 | a race starts by itself when N players are free and ready (a player back from a race is ready 10 s later) |
| `--laps N` | 3 | laps in a race this game starts |
| `--bots N` | 0 | robot karts this game owns and drives in its races |
| `--autodrive` | off | your own kart starts on the autopilot (P toggles it) |
| `--headless` | off | no window, no sound |
| `--rina-host PATH` | beside the script, else `/opt/fdf/bin` | what a race server on this machine runs as its memory |
| `--comms-ram`, `--comms-media`, `--comms-app`, `--comms-args` | from FD&F, else the main memory's host:8800 / :8994 | the Communicator, for the Call buttons (`[KART_CALLS_V1]`) |

## Ports

Nothing new on the droplet. The main memory is one that already runs (FD&F's on 8840, or the kart region on 8799). A
race server listens on whatever port its machine's system gives it (`--listen 0.0.0.0:0`; the host says the port it
bound, `[BOUND_PORT_SAID_V1]`), and writes it into the main memory. Players reach the hosting machine on that port.

## The memory is memory

- `[KART_ON_THE_SESSION_V1]` one permanent FNW1 session per memory and service, through this tree's C++ session client
  (`kartram.cpp` -> `libkartram.so` / `kartram.dll`, ctypes in `ribbit.py`). Writes are posted, never waited for. The
  one exception is the race server's write-back as it exits (`write_landed`): it must know its last truths are in the
  memory before its session goes.
- `[KART_HELD_READS_V1]` each service is read once, then held; the memory answers when something is written.
- `[KART_WRITES_ON_CHANGE_V1]` a kart is written when it departs from where readers draw it (its heading, speed and turn
  rate carried forward); a lobby tuple or tile when it changes. Nothing is written to stay fresh.

## Files

- `kartdemo.py` — the game: lobby, start rules, hosting, racing, robots, the call strip.
- `kart_server.py` — the race server: its memory, its filling, the race's lap times, the write-back.
- `ribbit.py` — the memory in Python over `libkartram` (ctypes).
- `kartram.cpp`, `build_kartram.sh` — that library: `libkartram.so` (Linux x86-64; libcrypto.so.3, glibc 2.38+,
  i.e. Ubuntu 24.04) and, with `--windows`, `kartram.dll` (64-bit, static).
- `DESIGN.md` — the truth table and the rules.
- `test_owned_cells.py` — the region's owned- and permanent-cell checks.
- `kartdemo.original.py` — the first version, kept for comparison.

## Proven (2026-10-18, `tools/kart-check.sh`, on the shipped r22 files)

Each machine its own network namespace on one shared network; the main memory a rina-host on the "droplet". red
(3 robots, can host), blue (1 robot, cannot host), nyc (4 robots, can host). 11 of 11 checks pass:

1. the lobby is in the main memory; two players do not start a race at `--min-players 3`;
2. blue (which cannot host) starts it when it joins: the intent names nyc, the race server runs on nyc's machine, and
   the main memory says where (its address and a port its system picked);
3. every game takes its slots from the race memory's grid; each sees the other machines' karts (11 in all); 61.5 to
   61.9 fps; 2.7 to 3.5 writes/s per kart;
4. all 11 karts lap on the road (sampled from the race memory; at most 42.7 px off the centre-line, the road 62);
5. machines talk only to the main memory and to the race host's race ports: 8,004 machine-to-machine IP packets, none
   anywhere else;
6. red's game killed -9 mid-race: its 4 karts are `{}` in the race memory 43 ms later and the other games stop
   drawing them; the race goes on;
7. the race ends by itself: the server writes the result (11 karts, 7 finished) and 3 records back, permanent, and
   every game is back in the lobby;
8. the main memory killed -9 and started again from its file: the result and the 3 records are back;
9. race two starts by itself; its server read the 3 records and put the fastest player's kart in slot 0; the server
   killed -9: its tuple is gone, its race memory with it, every game is back in the lobby, and no result is written;
10. through FD&F: red cannot join FD&F, so blue's Start makes a roster of blue and nyc only; the race runs on nyc's
    FD&F address and FD&F's memory keeps the result;
11. the owned- and permanent-cell checks (7 of 7), red on the previous region for the permanent ones.

Solo, on one machine: four runs of the game, five races; each run's main memory wrote the earlier runs' records and
results back in at start (`[PERMANENT_CELL_V1] 5 permanent tuple(s) written back`), and the lobby showed red's 5 races.

**Not run:** on Windows (the DLL is built, not run), and on your machines.
