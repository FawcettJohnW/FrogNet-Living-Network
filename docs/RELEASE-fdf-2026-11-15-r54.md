# fdf-2026-11-15-r54: the forest keeps every member

r54 contains r53 and one change to CroakOS's graph. The installers in `opt/fdf/dist` (`fdf-install-x86_64.run`,
`fdf-install-aarch64.run`, `fdf-droplet.tgz`) were built from this tree with `rinab/tools/make_deliverables.sh`.

## What changed

- **No stranded member** (`[NO_STRANDED_MEMBER_V1]`, `fdf_graph.py`). The forest keeps one upward link per host. A
  host whose `.1` gave a lease to a node that chose a different parent was left with no link at all, and nothing
  could reach it. One example is `topo_asym_three_sites`: B_end1 gave B_gw its only lease, and B_gw's parent was A_gw.
  - Now a tree with no exit and no hub link is joined to a neighbouring tree over one of its own links (the lowest
    `.1`, then the names).
  - A link between two trees adds no cycle, so every path stays unique.
  - Trees that have a way out are left exactly as they were chosen.
- **Tests.**
  - The graph oracle has a new case, 12. It fails with r53 and passes with r54.
  - The simulator keeps `topo_asym_three_sites@as-leases`, the scenario as it originally failed: 50/64 pairs reached
    with r53, 64/64 with r54.
  - The full simulator gate passes (`opt/frognet_semantic/simulation/SIM_STATUS.md`).

## Upgrade

On a node, run the `.run` for its machine type again. It is re-runnable, as for r53. On the region host, unpack
`fdf-droplet.tgz` as before. Each package's `/opt/fdf/BUILD` says `build=fdf-2026-11-15-r54`.
