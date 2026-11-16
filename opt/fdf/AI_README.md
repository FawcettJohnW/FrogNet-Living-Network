# AI_README — CroakOS, the implementation of FD&F

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model: the holistic invariant, the checksum, and
what to do before reporting a defect. This file lists only the hazards in this tree.

## What is here

| | |
|---|---|
| `src/fdf-source/rinab/` | the C++ (fdf-ram, the region's RAM manager, and the clients), its netns tests, `tools/make_deliverables.sh` |
| `src/fdf-source/fdf/` | the scripts and Python: `bin/` (fdf-hub, fdf-facts, fdf-app, fdf-region-create, fdf-region-join, fdf-invite, fdf-leave), `lib/python/fdf_graph.py` (the graph every node derives) |
| `docs/` | `APP-SETUP-AND-OPERATION.md` (governs how an app runs through a split and a merge), `FDF-TECHNICAL-DESIGN.md` |
| `dist/` | the installers, built by `make_deliverables.sh`; `/opt/fdf/BUILD` in each says its build |
| `src/frognet-android/`, `windows/`, `src/openeoc-app/` | the Android member, the Windows bridge, OpenEOC's desktop and phone app |

## Traps

- **FD&F is the architecture; CroakOS is the implementation.** The code keeps the `fdf` name in paths and commands; do
  not rename them.
- **Nothing here is permanent central infrastructure.** The region host's only special job is attaching members; the hub
  relays and terminates tunnels and keeps no memory (`[HUB_HAS_NO_MEMORY_V1]`); every RAM, the SystemRam and each
  AppRam, is a role whose holder the selection names and moves.
- **Sites never tunnel to each other.** Between sites, traffic goes member → hub → member. The simulator holds the
  hardware topologies to this (`[WAN_IS_THE_HUB_V1]` in `opt/frognet_semantic/simulation/s_topo_tier.py`).
- **The forest keeps one upward link per host, and strands nobody** (`[FOREST_V1]`, `[NO_STRANDED_MEMBER_V1]` in
  `fdf_graph.py`). `fdf_graph_oracle.py` holds it to the agreed topologies; run it after any change to the graph.
- **The build tag lives in six places** that `make_deliverables.sh` checks: `rinab/src/fdf_version.hpp`, `fdf-facts`,
  `fdf-hub`, `fdf_graph.py`, `opt/frognet_semantic/internet_tunnels_v3/region.py` and `opt/frognet_semantic/BUILD`.
- **Every wait ends on an event**, never a timer (`[EVENTS_NOT_TIMERS_V1]`). A sleep or retry loop standing in for an
  event is a defect: name the event that should end it.
