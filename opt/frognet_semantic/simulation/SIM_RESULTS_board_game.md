# Simulating the UnREST Board Game Over the Wire — What It Proves

This record accompanies `sim_board_game_over_wire.py`. It exists so the result
survives a compacted context and can be read on its own by a programmer learning the
UnREST pattern. It is the simulator doing what it is *for*: proving an application over
the transport, under conditions you'd otherwise need a lab of radios to reproduce.

## What was run

A complete two-player backgammon game — open the table, both players seat, white rolls
3-1 and plays it out, black answers 6-4 — played over the real simulated transport
(`transport_sim_tier.WireMedium`), once per network profile. The dice are fixed, so the
resulting board position is deterministic; the only thing changing between runs is the
wire. Then a separate test floats the table host to a different node mid-game.

The transport is the production simulator's `WireMedium`, dialed with `NetworkParams`
straight from its own documented profiles: one-way latency, jitter, a bandwidth ceiling,
and periodic outages that close the wire for a set duration.

## Captured output

```
[Clean LAN]        latency=0.5ms  bw=1e9 B/s    outage_p=0     -> 0.02s, 0 outages
[Satellite (GEO)]  latency=300ms  bw=256k B/s   outage_p=0     -> 6.01s, 0 outages
[HaLow 900MHz]     latency=20ms   bw=600k B/s   outage_p=0     -> 0.42s, 0 outages
[Jammed RF]        latency=100ms  bw=4k B/s     outage_p=0.25  -> 4.21s, 1 outage

   Clean LAN        board identical to Clean LAN: True
   Satellite (GEO)  board identical to Clean LAN: True
   HaLow 900MHz     board identical to Clean LAN: True
   Jammed RF        board identical to Clean LAN: True

[Host float]  board before float ts=…612 version=5
              board after  float ts=…612 version=5   identical: True
```

The GEO link took 6 seconds against the LAN's 0.02; the jammed link took 4 and ate a
real 1.5-second outage. Every final board was byte-for-byte the same position.

## What it proves, and why it matters to how you write the game

**1. Impairment costs time, never correctness.** Over a reliable transit, a slow or
briefly-severed link delays bytes; it does not lose a move or corrupt the board. The
GEO run is sixteen seconds of latency slower and produces the identical game. The jammed
run survives an outage mid-play and produces the identical game. There is no run in which
the board came out wrong, because there is no mechanism by which a delayed read could
make it wrong — the board is a value in shared memory, and a read returns the current
value whenever it arrives.

**2. The game wrote none of the machinery that would normally be required.** Look at the
player's entire network surface:

```python
link.move(frm, die)     # write my move into the shared board
board = link.get()       # read the board
```

No retry loop. No acknowledgment. No sequence numbers. No dedup. No reconnect. No "is the
host up." No port. The backgammon engine plus those two lines is the whole networked
program. A message-based version of this game over the same jammed link would need a
retransmit timer, an ack scheme, duplicate suppression, and ordering — a small protocol
with its own bugs — just to not corrupt the board when the wire blinks. The memory model
deletes all of it, because there is no message to lose, only a value to read.

**3. The host can move and the player does nothing.** In the float test the node holding
the table vanishes mid-game and a different node takes over. The two nodes share one perm
store — the resumable authority — and keep separate live caches, which is the float in
miniature. The new node comes up cold, the player reads the board on a fresh link, and
the board is identical down to the timestamp: no new commit happened, because nothing
changed — the new host simply re-read the same authoritative memory. The player "found
the IP that moved" by doing nothing. In a live mesh, discovery re-resolves the host the
same way, beneath the application, which never learns the address changed.

## The standing lesson

This is the payoff of the whole UnREST pattern, made concrete and tested under fire: the
networking is the layer *below* the application, written and hardened once, so the
application is just the application. The simulator is how you prove that without a box —
dial the wire to its worst and watch the game stay a game. When you build the next
UnREST application, this is the bar: if your app code contains a retransmit timer or a
reconnect handler, you have pulled the network's job up into the app. Push it back down.
The app reads and writes a shared value; the wire underneath can be as bad as a jammer
makes it, and the only thing that changes is how long the read takes.

## To run it yourself

```
FROGNET_BUNDLES=/path/to/etc/frognet_bundles \
  python3 opt/frognet_semantic/simulation/sim_board_game_over_wire.py
```

Dial the profiles in `PROFILES` to taste; add an outage probability and watch the game
shrug it off.
