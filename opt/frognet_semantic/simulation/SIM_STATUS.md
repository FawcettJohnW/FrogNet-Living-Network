# Simulator status (last full run 2026-11-15)

## WorldShepherd slow tier, 2026-11-24 (`worldshepherd_slow_tier.py`, not in the gate)

24 runs, all PASS (every ticket won exactly once): lan, wan-100ms, 4g and mixed links, 8 SARAs, 1 and 8 claims at
once; `RibbitClaimStore` and Brandon's `PostgresClaimStore` on worldshepherd-ram 0.6, and his store on PostgreSQL as
the control. The table is in `ribbit/examples/worldshepherd/RESULTS.md`. The tier runs without PostgreSQL (16 of the
24). Latency, jitter and rate were simulated; loss, outages and a link cut during a claim were not run.

## From a clean machine, 2026-11-15: `setup.sh`, then the gate

A fresh Ubuntu 24.04 (debootstrap minbase; nothing of FrogNet's in `/opt` or `/usr/local`), the tree at d0a298ad with
the clean-machine fixes. `setup.sh --yes --lisp --netns` installed and built everything; `quickstart.sh` passed both
scenes. `run_all.py`: every tier passes except Ribbit-LISP, which stops before it starts and names what is missing
there (lispers.net 0.643 is not in the tree; that kernel has IPv6 disabled). The netns tests: region-loop, NOKEY=a
region-loop and lan-only `RESULT PASS`, exit 0. ponds-daemon fails: it predates invitations ([ADMITTED_BY_INVITE_V1]),
and the daemon's forward-pond switch takes its hub channel from the one invitation (`hub.json`), so a second pond has
no hub channel of its own. It now ends at `TEST_LIMIT` instead of hanging.

What made the clean machine work ([SIM_OWNS_ITS_ENV_V1], [SIM_SETUP_V1]):
- `sim_env.py`, imported first by `run_all.py` and the standalone tiers: the checkout's `usr/local/bin`
  (`FROGNET_BIN_ROOT`, which `proxy/netutil.py` now reads `mapInterfaces` from: [MAPINTERFACES_IN_BIN_ROOT_V1]), a
  scratch blob store (`FROGNET_BLOB_ROOT`), and the simulator's own database credential (`FROGNET_DB_CONFIG`,
  `~/.frogsim/DB_CONFIG.json`; `core/store.py` and `core/sizing.py` now read that path as `core/db_credentials.py` does).
- `setup.sh`: apt packages, the simulator's venv from `/usr/bin/python3`, MariaDB with FrogUser, FrogNet and FrogNet7,
  the CroakOS binaries; `--lisp` a Python 3.12 venv for lispers.net and the Ribbit-LISP package; `--netns` wireguard-go.
- The Ribbit-LISP tier checks its prerequisites first ([LISP_TIER_SAYS_WHAT_IS_MISSING_V1]); its Python is
  `LISP_PYTHON_BIN` (default `~/.frogsim/lisp-py312/bin`), no longer a fixed directory under /opt.
- The netns tests source `tools/netns-preflight.sh` ([NETNS_PREFLIGHT_V1]): what they need, a PATH without
  `/usr/local/bin`, a watchdog, and an exit status that matches RESULT.

## The run of 2026-11-15: `GATE: PASS`

Every in-container tier passes (`run_all.py`, integration branch merge-20261115; tier 4's execute and tier 5's
transport legs are box-tier). Environment for a full run: the CroakOS binaries built (`rinab/tools/build.sh`,
`build_static_host.sh`, `build_static_clients.sh`), the Ribbit-LISP package built and lispers.net unpacked
(`LISP_PKG`, `LISPERS_DIR`), and MariaDB running for the door tier.

What changed to get there:

- **[NO_STRANDED_MEMBER_V1], `fdf_graph` r54.** The forest no longer cuts a member off. A tree with no exit and no hub
  link is joined to a neighbouring tree over one of its own links; trees with a way out are left as chosen. Graph
  oracle case 12, and `topo_asym_three_sites@as-leases` in the topology tier (the scenario as it failed before:
  64/64 with r54, 50/64 with r53 -- B_end1 NO ROUTE).
- **[WAN_IS_THE_HUB_V1], `s_topo_tier.py`.** The hardware topologies that span sites (`SITES`, from each builder's
  docstring) are converted the CroakOS way: a link between two sites is not a lease, its gateways get the uplink and
  the region hub link, and its /30 is gone. The expectation is unchanged (every pair reached, as on the hardware);
  in addition every pair between sites must cross the hub, none inside a site may, and each path must be the one
  the forest calls for. Nine topologies; 238 of 238 pairs between sites through the hub.
- The three baselines that failed on 2026-11-14 (`topo_asym_three_sites`, `topo_dual_wg_transit_bridge`,
  `topo_multilan_shared_wg_transit`) pass with their expectations unchanged.

## Earlier record (2026-10-13 to 2026-11-14)


The simulator runs the real planner, committer, proxy and daemon over modelled or namespaced environments. Run it from
the checkout's root:

    bash opt/frognet_semantic/simulation/setup.sh          # once
    ~/.frogsim/venv/bin/python3 opt/frognet_semantic/simulation/run_all.py

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
  the tree with `agent_workload/` (PR #15). The collectives tier still reads the agent_workload copy of ddpbench.py, the duplicate
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
