# AI_README — the harness

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

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

## Traps

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
