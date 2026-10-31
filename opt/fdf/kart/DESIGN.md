# FrogNet Kart: shared memory creating and managing games

This is not a game demonstration. It shows shared memory doing what a game company's servers do: a lobby, chat,
agreeing a game's parameters, choosing a machine to serve the game, starting it there, everyone finding it, the game,
and the results kept across runs.

There are three kinds of participant and two memories:

- **The kart service** (`kart_service.py`) is where the lobby's intelligence is. It runs beside the **kart memory**
  (the kart region, a rina-host: on the droplet `fdf-kart-ram.sh` starts both), which is where everyone connects
  first. It reads the players' truths and writes its decisions: the agreed parameters, the lobby's state, and which
  machine serves each game.
- **Game-serving machines** (`kart_host.py`) say they can serve a game's memory, and start a race server when the
  service names them. A network can have dedicated ones; a player's game that can host runs one for its own machine.
- **Players' games** (`kartdemo.py`) write their own truths (here, chat lines, the parameters they want, ready), read
  the service's, and race in the game's memory when the service has one up for them.

Every participant writes its own truths at its own tuples, once, and never waits. Everyone else reads them from the
memory; held reads on tuple families are the callbacks. Nobody asks anybody anything: the service decides by reading,
and acts by writing.

## The kart memory (service `kart`)

| truth | its one writer | tuple (service, variable, instance) | kept | readers |
|---|---|---|---|---|
| I am here: name, machine, robots, ready, the laps I want | that player's game | (`kart`, `lobby`, *player*) | with its session | the service; every game |
| a chat line: text, when | its writer (a player, or the service) | (`kart`, `chat`, *writer*`#`*n mod 40*) | until overwritten | every game |
| this machine can serve a game's memory: kind (`dedicated` or `player`), address, rina-host | that machine's host agent | (`kart`, `gamehost`, *machine*) | with its session | the service |
| the agreed parameters and the lobby's state (waiting, launching, why) | the service | (`kart`, `agreed`, `lobby`) | with its session | every game |
| launch this race here: race, roster, parameters, the machine chosen | the service | (`kart`, `launch`, *race*) | with its session; `{}` when done | the named machine's host agent; every game |
| what became of a launch: starting, or failed and why | the host agent the launch named | (`kart`, `hosting`, *race*) | with its session | the service |
| a race server is running: address, port, race, roster, laps, start time | that race server | (`kart`, `server`, *race*) | with its session | every game; the service |
| a player's record; a race's result | the race's server, before it exits | (`kart`, `record`, *player*), (`kart`, `result`, *race*) | **permanent** | every game; the next race servers |
| a player's call tile | that player's game | (`comm`, `tile`, *player*) | with its session | every game |

## The game's memory (a rina-host the race server starts on the chosen machine, on a port its system picks)

As before: (`race`, `meta`, `info`), (`race`, `grid`, *kart*), (`race`, `record`, *player*) written by the race server
before it is announced; (`race`, `kart`, *kart*) by the game that drives each kart.

## The service's rules

1. **Agreed parameters.** Laps: the value most players in the lobby want (a tie goes to the shorter race). The
   service writes them, with the lobby's state, in (`kart`, `agreed`, `lobby`) whenever either changes.
2. **When a game launches.** When every player in the lobby who is not racing is ready and there are at least
   `--min-players` of them; or when a ready player presses Start, which launches the ready players now.
3. **Where it runs.** The service inspects the game-serving machines: a `dedicated` one if any is up (the one serving
   the fewest running races, then by name), else a player machine that can host (the same order). None: it says so
   in its state and in the chat, and launches nothing.
4. **Launching.** It writes (`kart`, `launch`, *race*) naming the machine and says so in the chat. The named
   machine's host agent writes (`kart`, `hosting`, *race*) `starting` and starts the race server, which fills the
   game's memory and only then writes (`kart`, `server`, *race*); every racer's game sees it and goes. If the agent
   writes `failed`, or its session ends before the server is up, the service names the next machine (said, never
   silently). When the server is up, the service writes the launch `{}`.
5. **Afterwards.** The race server writes the result and the records back, permanent, before it exits, and writes
   its server tuple `{}`. The racers are back in the lobby, not ready; the service posts the winner in the chat.

## What each game writes, and when

Its lobby tuple when ready or the laps it wants change; a chat line when its player sends one; its tile when it
changes; a kart when it departs from where readers draw it (heading, speed and turn rate carried forward, 2 px /
0.05 rad). Nothing is written to stay fresh: a participant is there while its session is up.
