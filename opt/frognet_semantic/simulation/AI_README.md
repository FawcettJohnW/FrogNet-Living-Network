# AI_README — the harness

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model (the holistic invariant, the checksum,
and what to do before reporting a defect); this file lists only the hazards in this directory.

## What is here

The simulator. It runs the **real** planner, committer, proxy and daemon over
modelled or namespaced environments — it does not model FrogNet, it executes it.

| | |
|---|---|
| `run_all.py` | the tiers, in order |
| `live_engine.py` | topology convergence over the real engine |
| `netns_backend.py` | real kernel, network namespaces (Linux, root) |
| `transport_sim_tier.py` | the bearer model: latency, jitter, bandwidth, outage, MTU |
| `baselines/` | 20 topologies, all recorded `source: hardware` |
| `failure_scenarios/` | captured real faults, replayed offline |

Read `SIM_STATUS.md` before claiming anything about coverage.

**A transport driver's tests pass here only one way** (John, 2026-11-17, of the Homa driver): "The Homa tests in the
simulator need to activate the driver between machines on the topology and use that as the transport for a full
battery of tests. That is passing." One proxy and one daemon in one process (`homa_door_tier.py`) and a driver's own
oracles are steps toward that and are not it. The record of bringing a transport up, and what the simulator needed
for it, is `ribbit/examples/homa/EXPLORATION-LOG.md` (section 9).
`wire_between_machines_tier.py` is the tier for it: each machine is a network namespace built as a node (its own
address, hosts file, database, `api.php` on 8080, the real `proxy_main` on 80 and `daemon_main` on 9009, the node's
nat rules and tools), the network is brought up as a network comes up, and the battery runs from every machine to
every other on the transport given. Run it with `--transport tcp` first: that is the control.

## Traps

- **The simulator runs from a checkout, not from a node.** `sim_env.py` is imported first ([SIM_OWNS_ITS_ENV_V1]):
  the checkout's `usr/local/bin`, a scratch blob store, and `~/.frogsim/DB_CONFIG.json`. A tier that opens a path
  under /opt/frognet_semantic or /usr/local/bin passes on a machine with a node installed and fails on a clean one;
  that is how the clean-machine run of 2026-11-15 failed. `setup.sh` makes the machine ready; test there.
- **Read a result as the whole machine's.** Tier 3 (`s_scale_tier.py`, `s_topo_tier.py`) runs real fdf-ram and
  fdf-client processes: members write their facts, every member derives the graph, and every path is walked in the
  route tables the planner wrote. A pass there is formation, discovery, routing, selection and recovery together.
- **Expectations are not adjusted to make a tier pass.** When a hardware baseline fails, compare expected and actual
  against the intended CroakOS behaviour, then fix the implementation or convert the baseline faithfully. The hardware
  topologies that span sites run through the region hub (`SITES`, `[WAN_IS_THE_HUB_V1]`); the case that exposed the
  stranded member stays as a regression (`AS_LEASES`, `[NO_STRANDED_MEMBER_V1]`).
- **Agreement is not prediction.** All 20 topologies have hardware-recorded
  baselines and the offline engine reproduces them byte for byte. No run has yet
  predicted something a real box then confirmed. State the first, never the
  second.
- **A topology edge cannot carry bearer parameters.** Loss, jitter, bandwidth and
  outage exist in the transport tier over a single link. You cannot attach
  600 kbps to the New York leg of a five-site pond. That join is the missing
  piece.
- **No concurrency.** Merges run one at a time in a single process, so every race
  is unreachable by construction. A green run is not evidence about races.
- **`[SIM_TMP_MUST_NOT_MATCH_THE_MERGE_GLOB_V1]`** — the harness temp prefix must
  not sit under `runMerge.bash`'s `rm -rf /tmp/frognet*`. It did once, and a
  merge firing mid-run deleted the harness's own isolation directory.
