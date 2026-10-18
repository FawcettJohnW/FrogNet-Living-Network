# FrogNet Kart: the truth table

The game is a multithreaded program whose threads happen to be on different machines. Each game writes its own
truths at its own locations, once, and never waits. It reads everyone else's truths from the memory, and held reads
(triggers) on tuple families are its callbacks. Nothing in the game asks another machine for anything.

| truth | its one writer | tuple (service, variable, instance) | latest / distinct | readers | trigger family | when it is written |
|---|---|---|---|---|---|---|
| a kart: x, y, heading, speed, lap, colour, robot, owner | the game that drives it (a player's own kart and each of its robots) | (`race`, `kart`, *kart name*) | latest | every game | (`race`, `kart`, *) | when the kart departs from where readers will draw it (dead reckoning) |
| a player's tile: status, whom it is calling, the call id, machine | that player's game | (`comm`, `tile`, *player*) | latest | every game | (`comm`, `tile`, *) | when one of those changes |
| a kart or tile is gone | the region, when the writer's session ends (`[OWNED_CELL_GOES_WITH_THE_SESSION_V1]`) | the same tuple, written `{}` | latest | every game | the same family | once, when the session ends |
| who is an FD&F member | FD&F (each machine's fdf-client) | FD&F's own participants | | every game run with `--fdf` | FD&F's held participant wait (`watch_participants`, then `op=rina_block` `wait`), on the game's own FD&F session | by FD&F |
| where the race and the Communicator are | the droplet's fdf-services.sh | (`fdf`, `where`, `race` / `comms`) | latest | every game, once at start | none | by fdf-services.sh |

## What this removes

- **Liveness writes.** A tile no longer re-writes itself every 0.15 s and a kart no longer writes every frame to stay
  "fresh". Readers no longer filter by age (`fresh_s`). A participant is there while its session is up; when the
  session ends the region writes `{}` at every tuple that session owns, the readers' triggers fire, and it is gone.
  Gone is gone: no timeout decides it.
- **Polling.** The race reader read every kart every 40 ms and the tile reader every tile every 200 ms, whether
  anything had changed or not. Now each reads the family once, holds a read for anything written after the newest id
  it holds, and applies only what changed to the view it keeps.
- **Waiting on writes.** A write is `post`ed on the session: it goes out and the game carries on. No reply is waited
  for, so no writer threads are needed.
- **A connection per call.** Every write and read was a new TCP connection with a plain-HTTP request. The game now
  holds one FNW1 session per region (the permanent socket, semantically compressed), through the tree's own C++
  session client (`kartram.cpp`, a C interface over `frogram::Session`).
- **FD&F polling.** The member list was asked for once a second; it is now re-read only when FD&F's held participant wait answers, on the game's own session to FD&F.

## Write rule for a kart (pay for change)

Readers draw a kart at its last written position carried forward along its written heading at its written speed and turn rate,
for the time since they received it. The owner computes the same thing and writes again only when its true pose is
more than 2 px from that, its heading more than 0.05 rad from where they draw it, or its
lap has changed. A kart standing still writes nothing; a kart on a straight writes almost nothing.
