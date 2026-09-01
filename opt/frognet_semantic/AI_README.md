# AI_README — the engine

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

The FrogNet engine. Subdirectories do the work; the files at this level are
notes, config and entry points.

| | |
|---|---|
| `core/` | memory, codec, wire identity, handlers, sizing |
| `discovery/` | the walk, routing, election, and 81 oracles |
| `proxy/` `daemon/` | the two halves of the semantic path — FNWP-1 on :9009 |
| `internet_tunnels_v3/` | broker registration, WireGuard peering |
| `simulation/` | the harness — real code over modelled environments |
| `broker/` | `full_broker.tgz`; the broker ships as an archive, and its source is inside it |

Read in this order if you are new: `README_FIRST.md` (working posture),
`FROGNET_PRIMER_load_me_first.md` (the architecture), `DOCTRINE.txt` (the tags).

## Traps

- **`FROGNET_NOW_*.md` files are design notes, some superseded.**
  `FROGNET_NOW_guid_identity.md` is marked SUPERSEDED at the top and its deploy
  order would put identity where a reinstall destroys it. Check the header before
  following any of them.
- **The broker source is not loose in the tree.** It is inside
  `broker/full_broker.tgz`. Searching the tree for broker behaviour and finding
  nothing does not mean it is not implemented.
- **`_fix_meta/` is a forensic record**, not live code. Its README and diffs
  explain two election fixes and are worth reading; nothing there executes.
