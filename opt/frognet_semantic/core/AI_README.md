# AI_README — memory, codec, handlers

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

The parts every node runs. 37 modules, no subdirectories.

| | |
|---|---|
| `frognet_tuples.py` | the Memory API: `put`, `get`, `get_all`, the scope constructors |
| `codec.py` | BLDC-1 — template learning, diffing, the wire types |
| `semcache_id.py` | the SAME identifier: HMAC-SHA-256 truncated to 16 bytes |
| `frognet_secret.py` | the shared secret the identifier is keyed on |
| `database_handler.py` `game_role.py` | two role handlers, two very different scoring functions |
| `sizing.py` | DB concurrency from three ceilings |
| `store.py` `not_frognet.py` | the negative cache and its `[ONE_STATE_V1]` rules |

## Traps

- **`database_handler.py`'s scoring is static by doctrine.**
  `[DBHOST_STATIC_RANK_V1]` forbids any term that moves with load. If you are
  adding a term, it must be a property of the hardware or its configuration.
  The docstring above `score()` was stale for a long time and named the three
  inputs the doctrine forbids — read the code, not the docstring.
- **Two role handlers, deliberately different.** `database_handler.score()`
  ranks on measured hardware. `game_role.score()` returns a flat constant, on
  purpose: hosting a game needs no special hardware, and a load-weighted score
  would let jitter flap the role every pass. Do not "fix" the flat one.
- **`frognet_secret.py` falls back to a published constant** when the secret file
  is missing. It now prints CRITICAL on every derivation. Do not quiet that.
- **The three-coordinate key composes into two columns.**
  `SensorName = "SD:" + var + "." + scope`, `SensorType = service`. The `SD:`
  prefix marks a tuple reapable; observed sensor data carries no prefix and is
  never reaped.
