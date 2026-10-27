# Hospital capacity manager (example)

The vehicle for the DAF-model prototype: John Day's distributed-application model (RINA Reference Model, Part 2) built
on Ribbit as the `op=daf` region of the FD&F RAM host. The design is `opt/fdf/docs/FDF-TECHNICAL-DESIGN.md`,
Appendix S.

- `hcm_board.cpp` -- the board. It enrolls in the incident's DAF, holds one subscription to the names `facilities`
  (the roster, persistent) and `receiving` (hospitals connected and taking patients), and serves a page and
  `/state.json`: each hospital connected or offline, its trauma capability and capacity, where the next trauma patient
  goes, and the incident's events.
- `hcm_incident.cpp` -- the scripted incident: nine steps that exercise every section of the model, each checked
  against what the system answers and what the board shows.

Run it (after `tools/build.sh`), from `opt/fdf/src/fdf-source/rinab/`:

    tools/hospital-demo.sh 3                 # three runs in a row; exit 0 only if all pass
    WATCH=1 tools/hospital-demo.sh 1         # one run, 4 s between steps; open http://127.0.0.1:8077/

No program here uses a hospital's address: they read names, and the names are resolved by their rules.
