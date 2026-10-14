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
| `discovery/` | the walk, routing, election, and the oracles `discovery/run_discovery_oracles.py` runs (84) |
| `proxy/` `daemon/` | the two halves of the semantic path — FNWP-1 on :9009 |
| `internet_tunnels_v3/` | the tunnel daemon: region mode (the FD&F RAM manager and hub link, WireGuard) or LAN-only (no WireGuard) |
| `simulation/` | the harness — real code over modelled environments |

Read in this order if you are new: `README_FIRST.md` (working posture), the
repository's `README.md` and `AI_READ_FIRST.md` (the architecture), `DOCTRINE.txt`
(the tags).

## Traps

- **`docs/history/` holds dated notes** (`FROGNET_NOW.md`,
  `FROGNET_NOW_guid_identity.md`, `FROGNET_PRIMER_load_me_first.md`). They
  record what was true on their date; their deploy steps are not current. The
  files of the same names at this level are one-line pointers.
- **A node runs in one of two modes**, LAN-only or region. The switch is one
  file on the node, `/opt/fdf/var/join.env` (RINA_RAM, written by
  `fdf-region-join`, removed by `fdf-leave`); the tunnel
  daemon logs its mode at start. The internet rendezvous service of earlier
  notes was removed in October 2026 (`docs/cleanup-2026-10/T3a.md`, `T3b.md`);
  notes that describe it are history. The operator guide is `docs/OPERATING.md`.
