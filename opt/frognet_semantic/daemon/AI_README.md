# AI_README — the origin half of the semantic path

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model (the holistic invariant, the checksum,
and what to do before reporting a defect); this file lists only the hazards in this directory.

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

## The semantic wire on Homa (`[HOMA_TRANSPORT_V1]`, 2026-11-16)

With `homa` in `/etc/frognet/semantic_transport` (`core/homa_transport.py`), `daemon_main` starts
`engine/server_homa.MsgDaemonServer` instead of `DaemonServer`. Read the section of the same name in
`proxy/AI_README.md` first: it has the bus contract and the rule that the driver, not the engine, keeps wire order.
The hazards on this side:

- **`_MsgSession.__init__` does not call `SemanticSession.__init__`** (which needs two sockets). It sets the same
  fields by hand. A field added to `SemanticSession.__init__` must be added there too.
- **Every request is answered through the session's reply queue**, including the ones the driver answers itself
  (HELLO, RTT_PING, a malformed frame, an oversize reply). Replies are numbered as they leave that queue; a reply
  sent around it leaves a hole and the proxy can hand up nothing after it.
- **A session ends when liveness says its peer is gone** (`core/homa_liveness.py`, `[LIVENESS_IS_A_HELD_READ_V1]`),
  not when Homa says anything: a Homa server is told nothing when a client goes away. The peer is named by the .1
  in its HELLO. An ended session answers each request it still owed with the driver's ended message (`FNHE` + why):
  answering is the only way a Homa server lets go of a request (the module never times out one that is in service).
  A message that arrives later for an ended session gets the same answer.
- **`remove_writer` is told only when the MACHINE left** (it is out of the hosts block), as the TCP driver tells it
  when a machine's last socket closes. A proxy that merely stopped has not left the network. Telling it then would
  remove everything that machine wrote, its own daemon's capability tuple included, and every other proxy would
  call that daemon gone.
- **`end()` runs on the liveness thread while the receiving thread may be in `arrive()`.** `ended` is set first and
  each side lets go of what it finds in `_dest` after that. Do not clear `_early` from `end()`.
- **One thread receives for every peer**, and frames are handed to the engine on it, in order, as the TCP reader
  thread does per session.
