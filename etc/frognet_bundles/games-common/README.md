# FrogNet Games — five UnREST multiplayer games

Five turn-based, multiplayer games that all work the same way: the whole game is **one
tuple in shared memory**, the moves live inside that tuple, and the in-proxy origin
applies them. Because the moves are in the tuple, **replay and watchers are free** — a
watcher is anyone who reads the tuple, and replay is folding the move list that is already
there. Memory, not messages.

| Game        | Players | Mechanic                          | Hidden info | Client |
|-------------|---------|-----------------------------------|-------------|--------|
| Backgammon  | 2       | dice + checkers, doubling cube    | none        | bg_app.py (own ops) |
| Connect Four| 2       | drop, four-in-a-row               | none        | generic |
| Reversi     | 2       | flank & flip                      | none        | generic |
| Hearts      | 4       | trick-taking, scoring, shoot moon | your hand   | generic |
| Liar's Dice | 2–6     | bid & bluff on dice               | your dice   | generic |

Backgammon is the original and its own special case — it predates the shared base and
keeps its own ops (roll/move/double) and its own client, `bg_app.py`. The other four are
the generic GameCodex family: one `game_base.py` gives them seating, the turn gate,
history, replay, watchers, presence, and host-float resilience, and one `game_client.py`
plays, watches, or replays any of them. All five register in one origin and ride the same
`/game` path.

Hidden-info games redact per viewer (`view(state, who)`), so a live watcher can't see a
player's hand or dice — but a replay of a finished game reveals everything.

## What's in the tarball

```
etc/frognet_bundles/
  games-common/
    game_base.py        # the shared base: store pattern, turn authority, history,
                        #   replay (fold), watchers (open read), per-viewer redaction
    game_client.py      # ONE client for every game: play / watch / replay (Tk + --cli)
    test_games.py       # self-test: plays each game, proves replay/watcher/authority
    install.sh          # verify + wire-up notes
    README.md           # this file
  net.frognet.connectfour/  bundle.json  codex/connectfour_codex.py  app/play.py
  net.frognet.reversi/      bundle.json  codex/reversi_codex.py      app/play.py
  net.frognet.hearts/       bundle.json  codex/hearts_codex.py       app/play.py
  net.frognet.liarsdice/    bundle.json  codex/liarsdice_codex.py    app/play.py
opt/frognet_semantic/core/
  game_origin.py        # the origin, now registering all five games
```

Each game is ~80–220 lines of RULES. Everything else — seating, the turn gate, history,
replay, presence, the working-memory store that lets the host float — is in `game_base.py`
and is identical for every game. A new game is one file: `initial_setup`, `legal_actions`,
`apply_action`, and optionally `view` to hide secrets.

## Install

```
sudo tar xzf frognet_games.tar.gz -C /          # lays down the bundles + game_origin.py
cd /etc/frognet_bundles/games-common && python3 test_games.py   # verify: ALL GAMES PASS
```

Then two wire-up steps:

1. **Origin hook** (once per node that hosts tables). `game_origin.py` is dropped into
   `opt/frognet_semantic/core/`; if you haven't already wired the origin into the proxy,
   apply the two-edit terminate-and-serve hook from `GAME_ORIGIN_INSTALL.md` so a `/game`
   request is answered from working memory instead of forwarded. The four games register
   themselves in `GAME_CODICES`; nothing else to change.

2. **Announce them** so they appear in the Communicator's Games tab. The beacon service
   reads each `bundle.json` and plants a presence beacon:
   ```
   python3 /etc/frognet_bundles/communicator/frognet_beacon_service.py \
       --dbhost databasehost.frognet --interval 300
   ```
   (or `--once` to test). Discovery is an associative read of those beacons — present a
   game on the network, and every Communicator sees it.

## Play, watch, replay

The Communicator launches a game's `app/play.py`, which hands the generic client its game
id. Standalone:

```
# play a seat
python3 game_client.py --connect <table-host> --game hearts --table kitchen --who alice

# watch (read-only; no seat; hidden info stays hidden)
python3 game_client.py --connect <table-host> --game hearts --table kitchen --who bystander --watch

# replay a finished game (folds the move history in the tuple)
python3 game_client.py --connect <table-host> --game hearts --table kitchen --replay --cli
```

Joining a table is reading its tuple; taking a turn is one write the origin validates;
watching is reading; replay is folding the history you already read. The client wrote no
retry, no ack, no reconnect — over reliable transit a read just takes as long as the link
takes, and the board is always correct. The networking is the layer below the game.

## Prove it over the wire

The package ships the simulator harnesses that run the games through the real transport
tier under impairment, multiplayer, with watchers and replay and a host float:

```
FROGNET_BUNDLES=/etc/frognet_bundles \
  python3 /opt/frognet_semantic/simulation/sim_games_over_wire.py     # the 4 generic games
FROGNET_BUNDLES=/etc/frognet_bundles \
  python3 /opt/frognet_semantic/simulation/sim_board_game_over_wire.py # backgammon
```

Both assert the outcome is identical on every wire (Clean LAN through Jammed RF): impairment
costs time, not correctness, and turn authority, hidden info, replay, and host-float all hold
across the link.

## Add your own game

1. Drop `net.frognet.<yourgame>/codex/<yourgame>_codex.py` subclassing `GameCodex`.
2. Implement `NAME`, `MIN_PLAYERS`, `MAX_PLAYERS`, `initial_setup`, `legal_actions`,
   `apply_action`, and `view` if it has secrets.
3. Register it in `game_origin.GAME_CODICES` with `_bundle_loader(...)`.
4. Add a `bundle.json` and an `app/play.py` shim (`main(default_game="yourgame")`).

Replay, watchers, presence, host-float resilience, and the generic client all work for it
the moment it exists, because they were never the game's job.
