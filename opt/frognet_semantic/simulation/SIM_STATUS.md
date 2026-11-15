# Simulator status (last full run 2026-10-13; updated 2026-11-11)

The simulator runs the real planner, committer, proxy and daemon over modelled or namespaced environments. Run it from
the checkout's root:

    cd opt/frognet_semantic
    PYTHONPATH=$PWD PYTHONUTF8=1 FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=$PWD/../../usr/local/bin python3 simulation/run_all.py

Its last run (`docs/cleanup-2026-10/t6a-logs/runall.txt` in the repository; the same result at T4b, T4c and T5):

| tier | what | result |
|---|---|---|
| 1 | model suite (`frognet_sim.py`: algorithm, topologies, lifecycle, churn, invariants) | PASS |
| 2 | the real planner and committer on 23 topologies | PASS |
| 2b | failure modes through the real planner and committer (reroute, black hole, partition) | PASS |
| R | regression against the learned baselines, and the failure-scenario replay | PASS |
| 4 | the real kernel in network namespaces | the provisioning plan only (dry run) |
| 5 | the proxy's semantic data plane | PASS, except collectives + DDP hooks: FAIL (`agent_workload` is not in this tree) |
| C | codex language compliance (JSON, XML, HTML, text) | PASS |
| T | physical transport and socket sets | PASS, except the aiconnect ramp oracle: FAIL |
| D | the discovery oracle suite | PASS |

The gate prints `GATE: FAIL (2 tier(s) failed)`: the two FAIL rows, the same two since T4b.

Since that run, `run_all.py` has changed in two ways. Neither has a recorded full run yet.

- **Both FAIL rows were removed from the gate on 2026-10-16** (`[PYTORCH_TIERS_PULLED_V1]`). They tested
  `agent_workload/`, which was not in this tree. `simulation/collective_tier.py` and
  `simulation/test_aiconnect_ramp_oracle.py` are kept, and run where `agent_workload/` is. The PyTorch work comes into
  the tree with `agent_workload/` (PR #15). The collectives tier still reads `agent_workload/ddpbench.py`, the duplicate
  dropped on 2026-09-15; the copy the campaigns run is `usr/local/frognet-ai/ddpbench.py`. The tier has to read that
  copy before both tiers go back into the gate.
- **TIER 3 is back, as FD&F discovery from memory** (`[S_SCALE_TIER_V1]`). It runs a real fdf-ram, an fdf-client per
  node and fdf_graph's plans at S=20, E=1, 2 and 5, and outputs the complete verification plan. It needs the fdf-ram,
  fdf-client and fdf-events binaries (`FDF_SIM_BIN`, or the day-claims package's, or rinab's `build/static`). The old
  internet rendezvous tier it replaces is described below.

What it covers and does not:

- `baselines/` holds 20 route-table baselines, every one stamped `"source": "hardware"`; tier R reproduces them. That
  is agreement with what hardware did, not prediction.
- Loss, jitter, bandwidth and outage are modelled in the transport tier over a single link; a topology edge cannot
  carry them.
- Merges run one at a time in a single process, so races are unreachable by construction.
- The internet rendezvous tier (TIER 3) and the old Python Communicator's media tier were removed with the code they
  tested (October 2026, `docs/cleanup-2026-10/T2.md`, `T3b.md`). FD&F regions are tested by the network-namespace
  tests in `opt/fdf/src/fdf-source/rinab/tools/` (README.md, "Running the gates").

The simulator's dated build notes from June 2026 are `opt/frognet_semantic/docs/history/SIM_STATUS-2026-06.md`.

## Tier 3, all of it (2026-11-14)

`s_scale_tier.py` (the star: S nodes, E egresses) now runs in the gate at S=200, E=1 5 20. `s_topo_tier.py` is the
rest of tier 3, every member a real fdf-client on a real fdf-ram, routes followed only in the verification plan:

- the 20 hardware topologies, converted to the facts fdf-facts writes (a segment's .1 holder gives the lease, every
  other member holds one); the oracle is the hardware's (every pair reached). 17 pass. Three do not, and the reason is
  in the model, not the harness: `topo_asym_three_sites` (a .1 holder whose only link is to a node that chose another
  parent is left with no link: [FOREST_V1] keeps one upward link per host), `topo_dual_wg_transit_bridge` and
  `topo_multilan_shared_wg_transit` (transit segments with no .1 holder: no lease, so no FD&F link).
- generated topologies with every path stated by the generator, hop for hop (trees, a mesh where the exit is chosen by
  speed not hops, a mixed forest), and their events: link down, egress lost, egress back, a child moving, churn
  (three rounds), a member's RAM link cut past its keepalive. All pass.
- slow links (`slowlink.py`: a user-space shaper, since this kernel has no qdiscs): every member's session over wan,
  wifi, 4g, geo, halow, with and without loss. The bearer test holds one member per profile for 120 s: every bearer
  down to geo (256 kb/s) and lossy HaLow holds its session; the jammed bearer (2 kb/s, 2 s outages) does not -- a
  member's participants read is 31 KB at 52 members.

`lisp_slow_tier.py` runs Ribbit-LISP against lispers.net (both unmodified) over simulated links: central, edge,
slow-edge. It needs the Ribbit-LISP package built and lispers.net unpacked (LISP_PKG, LISPERS_DIR).
