# FrogNet Family Hub — the launcher (pluggable unit)

The home screen of the Family Suite. A standalone stdlib-Tk app that **discovers
installed modules by beacon and launches them** — the first concrete piece of the
dashboard (primer 05).

## Run
```
python3 app/family_hub.py                       # scans /etc/frognet_bundles
python3 app/family_hub.py --bundles <root>      # dev / custom bundles root
```
It scans the bundles root, reads each module's `bundle.json` beacon (module/title/hub/
app), groups by hub (Communications / Family / Games / …), and opens a module's
standalone app on tap.

## Beacon manifest (every module ships one)
```json
{ "module": "...", "title": "...", "hub": "games|family-management|communications|...",
  "app": "app/<entry>.py", "summary": "...", "freshness": { "<field>": "<class>" } }
```
This file-based discovery is the **local-install stand-in for the network beacon** (an
`installed_family_plugin` sensor in the transient DB). Same shape, so moving to mesh-wide
discovery later swaps the *source*, not the launcher: install a module → its beacon
appears → its tile appears; stop it → the beacon goes stale → the tile greys.

## Where it sits in the gestalt
- **Discovery = beacon** (here: `bundle.json`; on the mesh: a transient-DB sensor).
- **Grouping = hub** (an axis on the beacon).
- **Launch = open the module's standalone app** (delivery standard: stdlib Tk desktop /
  native Android; never a browser as the client).
- The **served data plane** each module uses moves memory not messages — see
  `family-backgammon/dev/convergence_sim.py` (proven: SAME/DIFF/FULL, client holds the
  structure and reconstructs from deltas). The hub launches apps; the apps converge state.

Designed-vs-built: the launcher + beacon discovery are **built and verified**
(discovery/grouping tested headless). The richer dashboard (live tiles, action-writes,
network beacons, signed third-party modules) is the designed roadmap in primer 05.
