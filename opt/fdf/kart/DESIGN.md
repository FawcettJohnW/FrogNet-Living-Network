# FrogNet Kart: shared memory creating and managing games

This is not a game demonstration. It shows shared memory doing what a game company's servers do: a lobby, matchmaking,
starting a game server on some machine, everyone finding it, the game, and the results kept across runs. There is no
lobby server, no matchmaker, no master server and no database server: there is one shared memory everyone maps (the
**main memory**), a memory that a race server brings with it (the **race memory**), and programs that write their own
truths and act on the others'.

Every participant writes its own truths at its own tuples, once, and never waits. It reads the others' from the
memory; held reads (triggers) on tuple families are its callbacks. Nothing asks another machine for anything.

## The main memory

With `--fdf`, FD&F's RAM manager (the one the network already has). With `--memory H:P`, any rina-host. With `--solo`,
the game starts one on this machine, its file in `~/.frognet-kart/main.perm`, so what it keeps survives across runs.

| truth | its one writer | tuple (service, variable, instance) | kept | readers | trigger family |
|---|---|---|---|---|---|
| I am in the lobby: name, machine, robots, whether this machine can host a race, and my start intent if I made one | that player's game | (`kart`, `lobby`, *player*) | with its session | every game | (`kart`, `lobby`, *) |
| a race server is running: where (address, port), the race, its roster, laps, the start time | that race server | (`kart`, `server`, *race*) | with its session | every game | (`kart`, `server`, *) |
| a player's record: races, wins, best lap, last place, laps | the server of the player's latest race, before it exits | (`kart`, `record`, *player*) | **permanent** | every game (the board); each race server as it starts | (`kart`, `record`, *) |
| a race's result: the finishing order, times, best laps | that race's server, once, before it exits | (`kart`, `result`, *race*) | **permanent** | every game | (`kart`, `result`, *) |
| a player's call tile | that player's game | (`comm`, `tile`, *player*) | with its session | every game | (`comm`, `tile`, *) |

"With its session" is `[OWNED_CELL_GOES_WITH_THE_SESSION_V1]`: when the writer's session ends, the memory writes the
tuple `{}` and every hold on it fires. "Permanent" is `[PERMANENT_CELL_V1]`: the memory files it before publishing it
and writes it back when it starts again.

## The race memory (a rina-host the race server starts, on the hosting machine, on a port the system picks)

| truth | its one writer | tuple | readers |
|---|---|---|---|
| the race: id, laps, roster, the karts, the start time | the race server, before anyone is told it exists | (`race`, `meta`, `info`) | every racing game |
| each kart's starting slot (ordered by the players' best laps from their records) | the race server, the same | (`race`, `grid`, *kart*) | the kart's owner |
| each racer's record as the race began | the race server, the same | (`race`, `record`, *player*) | every racing game (shown on screen) |
| a kart: x, y, heading, speed, turn rate, lap | the game that drives it | (`race`, `kart`, *kart*) | every racing game; the race server (lap times) |

## Who starts a race, and where it runs

Determinism, not coordination: every game reads the same lobby and applies the same rules.

1. **A start intent.** A player presses Start, or the lobby holds at least `--min-players` players who are not in a
   running race, in which case the lowest-named of them is the one to say it. That game writes, in its OWN lobby
   tuple, `start = {race, host, roster, laps}`: the race id is the time in ms and its name; the roster is every lobby
   player not in a running race; the host is itself if its machine can host, else the first player in the roster
   whose machine can. If none can, it says so on screen and writes nothing.
2. **One intent wins.** If several are written, the one with the lowest race id is the race; every game computes the
   same answer.
3. **The host acts.** The game named host by the winning intent starts the race server on its machine, once per race.
   The server reads the racers' records from the main memory, starts its memory, writes the race into it (meta, grid,
   records) and only then writes `(kart, server, race)`. Nobody sees a race server that is not ready.
4. **Everyone else sees it happen.** Each game in the roster holds a read on `(kart, server, *)`; when its race
   appears it opens its sessions to the race memory and races. The intent's writer removes the intent once the race
   is up.
5. **Before it exits, the server writes back.** When every kart in the race has finished or is gone, or the server is
   told to stop, it writes the result and each racer's updated record to the main memory as permanent tuples, writes
   its `(kart, server, race)` `{}`, stops its memory and exits. Every game's hold fires and it is back in the lobby,
   showing the result. If the server is killed outright, its server tuple goes with its session and no result is
   written: gone is gone.

## What each game writes, and when (pay for change)

- Its lobby tuple when it joins and when its start intent changes; its tile when what the tile says changes.
- A kart when it departs from where readers draw it: they carry its last write forward along its heading at its
  speed and turn rate, and the owner writes again when its truth is 2 px or 0.05 rad from that, or its lap changes.
- Nothing to stay fresh: a participant is there while its session is up.
