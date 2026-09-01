# AI_README — the origin half of the semantic path

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

The daemon: reads FNWP-1 frames, executes against the origin, replies.

| | |
|---|---|
| `engine/session.py` | reader thread, writer thread, 256-worker pool |
| `pool_resizer.py` | runtime DB pool sizing via `core/sizing.py` |

## Traps

- **`REQ_REPEAT` always re-executes against the backend.** "Repeat" describes the
  request, not the work. The daemon runs the origin request again and compares
  the new response to the cached one to decide SAME or DIFF. Answering a repeat
  from cache without re-executing is not conforming.
- **Replies are drained in arrival order by one writer thread.** There is no
  shared send lock and no flush-polling loop. A previous attempt to add
  per-request flushing produced a 400-deep pending queue and was reverted.
- **`[NO_FALLBACK_V1]`: sizing raises rather than inventing.** If the CPU count
  cannot be read, `_default_db_pool` raises. `core/sizing.py` used to invent 4
  for the same condition and no longer does. Do not add a default.
- **The 45s per-frame read timeout is deliberate** — it covers an HF radio worst
  case, not a hung connection.
