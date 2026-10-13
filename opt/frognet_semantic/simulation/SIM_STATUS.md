# Simulator status (2026-10-13)

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
