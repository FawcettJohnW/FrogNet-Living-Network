# FROGNET_NOW — adaptive board poll cadence (2026-06-20)

## Problem
An idle backgammon table polled the shared `state` tuple every 1.2s whether or
not anyone was playing. With nobody at the board, that was still ~50 reads/min
per open board to the databasehost (semantic REQ_REPEAT -> RESP_SAME each time).
The local read cache can't absorb these — they're remote-origin (the board lives
on another node), and caching a remote board would serve a stale game.

## Fix — poll on the tuple's own state
The state tuple already carries `phase` and `seats` (presence lives inside it
too), so it says whether anyone is playing. New `poll_cadence.py`:
  active(st)  = phase=="play"  OR  (phase=="lobby" AND seats non-empty)
  next_interval(st, fast, idle) = fast if active else idle
- bg_board.py and game_app.py now reschedule with next_interval() instead of a
  fixed 1200ms, off the last state they read.
- Idle (empty lobby, gameover, or a read-miss) backs off to 8s; a live or
  seated table stays at 1.2s; a join snaps it back to fast on the next cycle.
- Tunable: FROGNET_BOARD_POLL_MS (1200), FROGNET_BOARD_IDLE_POLL_MS (8000).

## Effect
Idle open board: ~50 reads/min -> ~8 reads/min (~85% cut), with no loss of
in-play responsiveness. Watching an empty table no longer hammers the data host.

## Proof
test_poll_cadence_oracle.py: empty lobby/gameover/read-miss -> idle; seated
lobby + in-play -> fast; idle backed off >5x; join snaps back. The predicate bug
where a finished game (seats still filled) stayed fast was caught and fixed here.

## Deploy (every node that runs the board clients)
Ship the whole games-common/ bundle. No service restart — picked up next time a
board is launched. Clear any __pycache__ in the bundle dir.

## NOTE — possible duplicate
A second copy of these client modules exists under
etc/frognet_bundles/communicator/ (game_tuple_client.py, games_lobby.py there).
Whichever the launcher actually runs is the one that must carry this change.
Confirm which path the Games hub launches before assuming this is live.
