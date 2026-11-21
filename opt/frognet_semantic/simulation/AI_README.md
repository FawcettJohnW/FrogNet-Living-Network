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


## `wire_between_machines_tier.py`: what it models, and what it must not (17 Nov 2026)

- Roles are placed by the node's own rules, not by hand: the ControlRam is the highest .1
  (`usr/local/bin/frognet_dbhost_elector.py`).
- A machine leaving is a merge: it is out of every remaining hosts block and its role goes to a machine that is
  there. Do not model "a machine restarts and every hosts file still names it"; John called that not realistic.
- `--transport` takes a whole setting, lines joined by commas (`'chorus,databasehost_control.frognet tcp'`). Run
  `tcp` first as the control, and run every word the setting accepts.
- One read answered 503 "REQ_MISS from daemon; exhausted retries" is the semantic engine's, on either driver. The
  tier counts it and says so; it does not judge it. Anything else fails the pair.
- Batteries so far: (a) concurrent reads and writes from every machine to every other; (b) the SystemRam's machine
  leaves, the role moves, the machine returns. Still to do for John's definition of passing: the door scenarios,
  large frames, the 20 hardware topologies, shaped links.

- `--topology` takes `trial3`, any topology with a hardware baseline (`simulation/baselines/topo_*.json`), or `all`.
  A simulator topology is built from the simulator's own description: `frognet_sim.py`'s builder for the devices
  and segments, the baseline's `committed_routes` for the routes (with `onlink`, the node's identity as `src`, and
  the peer answering for addresses it routes onward, which is what a tunnel does). Do not draw a topology by hand.
- The simulator's one database server stands for every machine: the tier raises its `max_connections`.
- Known and not excused: the TCP driver refuses requests for about 0.4 s while a new session's return connection
  is being set up ("reader dead"). It fails `auto` and sometimes `tcp`. See the Homa record, section 7, item 0c.

- Every network event in `wire_between_machines_tier.py` runs the merge (`the_merge`): the flush of
  `core/not_frognet` marks that `runMerge.bash` begins with, then `frognet_sim.discover_routes` over the topology as
  it is now. Routes, hosts blocks and roles come from it. Do not write a hosts file or install a route by hand.
  Each machine has its own sentinel directory.

- `--links <profile|mixed>` shapes every machine's uplink with `simulation/shaped_link.py` (slowlink.py on each TCP
  connection to another machine). A machine is "up" or "back" when its daemon says it is listening, not when its
  proxy answers.
- Batteries now: (a) fan-out, (c) large frames each way and a short frame during a long one, (d) the door tier's
  four scenarios with `simulation/door_origin.py` as the origin's door at /dc/ram.php, (b) the SystemRam's machine
  leaving and returning with the merge run at each event.
- A large frame is many fields: one string field over 65,535 bytes is refused by the codec. JSON is compared by
  value (canonical form), because the wire carries values.
- OPEN, the engine's: (c) fails in its second direction at 60 fields because the two machines' template stores hold
  different field orders for one opcode and the reply is decoded by position. See the Homa record, section 7 0d.

## `roles_exercise_tier.py` and `roles_exercise/`: the protocol layer demonstration (21 Nov 2026)

Seven Roles on three machines of `wire_between_machines_tier`, each Role with a memory of its own
(`<role>ram.frognet`; the tier writes their role lines through `W.MORE_MEMORIES`) and a channel setting of its own.
`--step memories | protocols | pages` test the parts; `--run 1|2|3|4|all` runs the exercise on TCP, Chorus, OpenQASM
and mixed with changes, and compares the exercise records byte for byte. The design is named in the tier's
docstring; the record is `ribbit/examples/homa/EXPLORATION-LOG.md`, 21 Nov.

Traps, each found by a run that failed:

- **Read a tuple by its name, or hold a read.** Every proxy caches broad reads of its own machine's api.php for up to
  10 s (`proxy/local_read_cache.py`); a write of a NEW name does not empty one. A raw read of a whole service just
  after a write can answer without it. A hold's rows are not cached.
- **A tuple is its writer's.** Two programs writing one name are two rows (the address each wrote from is part of
  what the tuple is). `role_service.index` takes the row for the later step. The Instructor writes into a Role's
  tuple as that tuple's writer.
- **A service's waits are `Role.wait`; its looks are `Role.look`.** Nothing in `roles_exercise/` sleeps, polls, names
  a protocol or names a key, and the tier fails the build if it does (R25). The only pauses are the tier's own.
- **A Role's facts for a step are that step's tuples**, because the record is read back from memory at the end.
  Anything that must be in the record for every step is written per step.
- **Nothing that enters the record is floating point** (the circuit simulator rounds its one probability to parts in
  a million). `ts` and `step`-less timings are left out of the exercise record by `record.clean`.
- **The record is collected at each site for the memories that site holds** (a whole memory across a 4,800-baud link
  takes minutes).
- `--steps N` runs N of the 60 steps, with run 4's changes at the same fractions: for the slow link.
- `FROGNET_ROLES_SAY=1` makes each service say what each wait is for; a stalled run then names the tuple.
