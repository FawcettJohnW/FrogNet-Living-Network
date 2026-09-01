# UnREST Game Origin — Install Instructions

Unlike the format-handler codices (`json` / `xml` / `sotf_media` / the `game`
codec), this is **not** a codec in the forwarding path. It is an **origin**: a
`_game` request terminates at the proxy, the handler runs the game's rules against
the node's working memory, writes the new board back, and answers. Nothing is
forwarded. The board lives in the proxy node that hosts the table — hot in the
transient cache, resumable in perm — so a node that floats away is rebuilt from
perm by the next holder (the table was never in the host).

This is the slow / turn-based case. There is no stream, no delivery ladder, no
convergence — a move lands, the board changes, it sits until the next move.

## What this lays down

```
opt/frognet_semantic/
  core/
    game_origin.py              # the authority — drop into your core/
  tests/
    test_game_origin.py         # proves: shared memory, host-float refault, turn rule
```

`game_origin.py` resolves `working_memory` (communicator bundle) and the game
codices (their bundles) off `$FROGNET_BUNDLES` (default `/etc/frognet_bundles`), so
on a Host it imports the real codex without copying it.

## Hook into `proxy/proxy_main.py`

Two edits — an import + instance, and a terminate-and-serve branch at the very top of
`proxy_dispatch`, BEFORE the async gate / HAM / semantic routing. A `_game` request
must never reach the forwarding logic; it is answered here from memory.

### Edit 1 — import and instantiate one origin per proxy process

Near the other proxy imports:

```python
from core.game_origin import GameOrigin, looks_like_game

# One authority per node. On the box, pass the api.php-backed transient + the perm
# store instead of the in-memory defaults; the codices do not change.
GAME_ORIGIN = GameOrigin()
```

### Edit 2 — terminate-and-serve at the top of `proxy_dispatch`

Right after the missing-`target_ip` guard, before the async gate:

```python
    if not target_ip:
        return send_error_reply(self, 400, "Missing/invalid Host header", ...)

    # --- UnREST game origin: answer from working memory, do not forward ---------
    raw_path = ctx["raw_path"]
    body     = ctx["body"]
    if raw_path.split("?", 1)[0] == "/game" or (body and looks_like_game(
            body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body))):
        code, resp = GAME_ORIGIN.serve(body)
        data = resp.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass
        finally:
            self.close_connection = True
        return
    # ---------------------------------------------------------------------------
```

That's the whole hook. The request is recognized (path `/game` or a `_game` body),
the origin mutates memory and returns the board, and dispatch ends — no HAM, no
semantic forward.

## Routing note

A player's `_game` request reaches the proxy on whatever node it connects to, but the
board lives on the node hosting the table. Two honest options:

1. **Co-located play** (simplest, proven): all players at a table point their client
   at the SAME host (`--origin <table-host>`). Every request lands on that node's
   origin, one working memory, done. This is what the test exercises.
2. **Routed play**: the origin on a non-hosting node forwards the `_game` request to
   the table host (a single hop, decided like any frognet target) so players can
   connect anywhere. This reintroduces a hop and is a later step — start with (1).

## Verify

```
FROGNET_BUNDLES=/etc/frognet_bundles python3 opt/frognet_semantic/tests/test_game_origin.py
```

Expected: two players converge on one board with no messages between them; wiping the
transient (host float) rebuilds the identical board from perm; an off-turn move is
refused and leaves the board unchanged.

## Client

`net.frognet.backgammon/app/bg_app.py --origin <table-host> --table kitchen --who alice`
writes moves into the shared board and reads it back. (`--space` plays the same game
over the api.php tuple substrate; `--connect` is the legacy HTTP path; no flag is
offline hot-seat. Same UI, four transports — the spectrum from messages to memory.)
