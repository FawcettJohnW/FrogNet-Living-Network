history: superseded by docs/OPERATING.md

# HANDOFF: FD&F / Mode B (rinab) — state as of 2026-10-06 (D5 complete: name through LISP, LAN join, Map-Resolver by capability)

Read this first. Everything referred to here is in this tarball (rinab/), and the last verified build is this tree.

## What this is

FD&F is John Fawcett's merge of LISP (lispers.net), RINA (rlite) and FrogNet.

- RINA and LISP do discovery and routing; FrogNet's own discovery and routing are retired.
- A DIF is, in reality, a shared RAM: a Ribbit region holds the DIF's management state.
- FrogNet's tools (Ribbit, FNWP, BLDC, proxy/daemon) ride on top as just another transport.
- Mode B is the build: rlite's own programs (rinaperf, rina-echo-async, iporinad, rlite-ctl) run unchanged against
  librina_region.so, which talks to a RINA region resident in a RAM host (the v0.63 platform plus build-time patches).

**Design docs** (Claude Docs, John's account): "Ribbit and FD&F — Design" and "FD&F merge — where we are" (the second
contains the locked proof plan: obligations, D1–D5, experiments 1–9, build sequence).

## John's rulings (binding)

- **DATA TRAVELS THROUGH THE SHARED SPACE, never member to member** (2026-10-07): firewall rules forbid member-to-member;
  connections are up only. The participant list comes only from the shared space, and all communication between
  participants goes through it. LISP does discovery, RINA does naming. Supersedes build item 1's member-to-member data
  path and the NAT relay link (see RESULTS.md, RULING 2026-10-07).

- **Map-resolver election: deterministic on capability, identity as the tie-breaker** (2026-10-06):
  `[MR_RANKED_BY_CAPABILITY_V1]`, src/rina_elect.hpp (cores, then memory, then lowest identity).
- **No control messages over UDP:** LISP mapping lives in the region and is read and written over the session.

- **A restarted (or changed) host is announced by neighbour propagation** (2026-10-06). Built as
  `[HOST_ANNOUNCES_ITSELF_V1]` / `[HOST_CHANGE_BY_NEIGHBOUR_PROPAGATION_V1]`.
- **Every host needs RINA_REGION_FILE** (the region's Permanent records); run.sh and the tools set one per run.

- **The Map-Resolver comes from a capability probe;** a machine with no LAN neighbour is unity by definition (elects
  itself). Built as `[MR_IS_A_CAPABILITY_V1]` / `[MR_ELECTED_FROM_CAPABILITY_V1]`.

- **No fallbacks** — "they mask real errors". No timed re-asks, no heartbeats, no pings, no silent drops.
- **Don't copy the messaging architecture.** Claude owns the API, the RAM server (consolidating commands is allowed)
  and the schema (must stay tuple-shaped), within the RFC and shared-RAM best practices. Prove each step in the sandbox
  and the simulator.
- **Correctness over speed;** still as fast and efficient as possible. Preallocate: high-water buffers, no allocation
  where the data's shape is known.
- **Self-joining, self-healing and the LISP integrations are critical.**
- **Liveness is the session.** Credit is state (the receiver's truth in the region), not messages.
- **Work style:** small tasks, copious artifacts, test as you go, don't stop until a real blocker. Never claim a result
  without checking that the test exercised it (see the rinaperf -i lesson below).
- **Tuple lifetime:** an envelope can carry a Permanent flag (stored offline, restored at once on restart); other
  tuples survive by re-assertion.
- **RAM hosts per island:** databasehost_control by arithmetic, databasehost by deterministic election (address
  computed, never stored); the databasehost change notification follows FrogNet's host_reset reconcile; neighbour
  propagation never forwards an announcement already seen.
- **AI-Host** is a quad-core i7 with 64 GB.

## Layout

- **src/**
  - rina_region.cpp: the region (ops: alloc, accept, wait, send, credit, taken, taken_now, routed, rmt_view, node,
    ctl, ...)
  - rina_memory.hpp: the region's own lock-free memory (InputSpace, FlowDir, records)
  - librina_region.cpp: rlite's API over the region and the data plane
  - rina_node.cpp: the machine agent, installed as rlite-uipcps; it is the machine's RMT
  - rmt.hpp: PDU and addressing
  - machine_id.hpp: machine identity
  - rlite_ctl*.cpp, ctl_host.*: rlite-ctl
- **tools/**: build.sh (host and platform patches, applied in order), build_rlite.sh (library, agent, rlite-ctl and
  rlite's programs), apply_*.py (every platform patch, each tagged), quick-bench.sh, malloc-sites.sh, mcount.c,
  perf-count.sh, cg-*.sh, loop-test.sh
- **run.sh RLITE_ROOT [FIRST LAST]:** rlite's 30 integration tests against an isolated host. `TESTS='a.sh b.sh'` runs
  other scripts the same way (the experiments).
- **bench.sh RLITE_ROOT [ROUNDS]:** rlite against Mode B on the same machine (ONLY_REGION=1 skips rlite).
- **lisp-mapping/:** lisp-up.sh LISPERS_ROOT (unmodified lispers.net as MS/MR), lisp.config, run-lispers.py,
  FINDINGS-FOR-DINO.md.
- **experiments/:** expD5-one-name.sh, expD5-lan-join.sh, expD5-lisp-name.sh, exp4-readdress.sh, exp5a-member-dies.sh, exp6-split-merge.sh, exp5a-diag.sh; RESULTS.md has every
  result and correction.
- **Write-ups:** STEP1-DATA-PLANE.md, STEP2-MACHINE-IDENTITY.md, WIRE-REVIEW.md (everything before), evidence/,
  templates/rina.prime (the primed template store; regenerate when request shapes change).

## Build and run (sandbox paths)

```
cd rinab
tools/build.sh
tools/build_rlite.sh /path/to/rlite-6ac276b
./run.sh /path/to/rlite-6ac276b               # rlite's 30 tests
TESTS=$PWD/experiments/exp6-split-merge.sh ./run.sh /path/to/rlite-6ac276b 1 1
lisp-mapping/lisp-up.sh /path/to/lispers.net    # then, D5 through LISP:
HOST_LISTEN=<this machine's address> LISP_MS=<it> LISP_MR=<it> LISP_AREA=rina-area.test LISP_KEY=fdf-area-key \
  LISP_LOG=/path/to/lispers.net/lisp/logs/lisp-ms.log TESTS=$PWD/experiments/expD5-lisp-name.sh ./run.sh /path/to/rlite-6ac276b 1 1
RINA_RMT_STATS=/tmp/rmt.txt ...               # each agent's route log and forwarding counts
```

## Where it stands (restart, 2026-10-07; the plan is the Claude doc "FD&F — plan and tests (restart, 2026-10-07)")

FD&F is a transport: individual machines, each connected up to one RAM manager; discovery by capability announcement;
LISP locates, RINA names; NO routing, nothing between machines but through the RAM manager.

| Item | Status | Check |
|---|---|---|
| 1 Discovery (announce, participants, alive) | done | tools/discovery-check.sh (0 packets between machines) |
| 2 Transport interface (src/transport.hpp) and FD&F's answer (src/fdf_transport.hpp) | done | build/bin/transport-conformance |
| 3 The RAM manager registers its own LISP mapping | done | tools/selfreg-check.sh |
| 3 Name-only joining | BLOCKED: where a new machine finds the Map-Resolver (John's ruling) | — |
| Machines follow a moved RAM manager via the mapping | done | tools/mapwait-check.sh, tools/move-check.sh |
| 4 Strip: propagation, LAN join, library and agent member-to-member, relay, routes, routed op, old experiments | done (4a–4e) | rlite 30/30 after each |
| 5 BLDC-1 dictionary for FD&F (send + current ops) | open | — |
| 7 FD&F dashboard + chat web page | open | — |
| 8 Records: retire the old plan document | open | — |

Flow data goes through the RAM manager (rlite 30/30; 1.03 requests per transaction; about 520 µs per transaction).

## Lessons (don't repeat)

- **The plan doc is the order, not this file's list.** "FD&F merge — where we are" (Claude Docs) holds the locked build
  sequence; read it, and check each experiment against its definition there (D5's LAN case is rlite 0025's broadcast
  enrollment, which names no neighbour).
- **lispers.net logs to logs/lisp-<name>.log only if that file does not exist when it starts;** lisp-up.sh empties
  logs/ for that reason (a stale log made expD5-lisp-name count 0 lookups).
- **If lispers.net is down, `rina-lisp register` waits forever** (no retransmit yet: open question). Check
  `ss -ulnp | grep 4342` before a LISP run. Long runs: start run.sh with setsid in the background and poll; the tool
  stops any single command at 300 s.
- **Mapping-mode runs:** `HOST_LISTEN=192.0.2.2 MAP_AREA=rina-area.test MAP_KEY=<key> TESTS="…expD5-region-mapping.sh
  …exp5b-host-moves.sh" ./run.sh <rlite> 1 2` (unset RINA_MAP_RESOLVER). host-moves adds and removes 192.0.2.3 on eth0.
- **NAT runs are standalone** (`experiments/exp-nat.sh`, `exp-nat-third.sh`, not under run.sh); they need iptables
  (`apt-get install iptables`) and build/. tools/nat-check.sh first proves the kernel gives real NAT.
- **Work you don't remember in this tree is your own,** from earlier turns or from background runs (setsid) still going.
  Check `pgrep -af run.sh`, the newest runs/ and file timestamps before starting a run, and read what is there.
- **Deleting a network namespace does not kill its processes;** a test that measures the host must end them itself.
- **`pkill -f PATTERN` / `pgrep -f` kills the shell running it when PATTERN is in its own command line.** Put the kill
  in a script.
- **lispers.net logs carry ANSI colour codes:** strip them (`sed 's/\x1b\[[0-9;]*m//g'`) before grepping.
- **A Map-Notify is not acceptance:** verify its HMAC.

- **Verify from the extracted tarball every time:** it caught the intermittent 5b-full loss that 8 working-tree runs
  had passed. When a failure is rare, force its order by NAMED fault injection used only by its own experiment
  (`[FAULT_REATTACH_DELAY_V1]`), and A/B the same injection on the builds before and after the fix.
- **rinaperf on an UNRELIABLE flow ends its own test after ONE lost ping:** the server stops 10 s after the last ping
  it received, before the client's 10 s timeout (RP_DATA_WAIT_MSECS). Use `-g 0` where the claim is "nothing beyond the
  interruption", and fail on "Stopping after" / transmitted < count, never on the exit code alone.
- **rinaperf `-i` is in MICROseconds.** rinaperf waits 10 s for a lost reply, the server closes after 10 s idle, and
  it exits 0 even after giving up: pass criteria must count Timeouts.
- **`pkill` is not namespace-scoped:** kill by `ip netns pids <ns>`.
- **An AF_PACKET socket reports ENETDOWN when one of its devices goes down:** a device event, not a socket failure.
- **pid 1's netns cannot be read here:** the machine identity always appends the inode.

## Open, in order

1. Item 3: John's ruling on where a machine finds the Map-Resolver with only the area's name.
2. Item 5: BLDC-1 dictionary (`send`, announce, participants, map ops) on both ends; bytes per message beside Python BLDC-1.
3. Item 7: fdf-participants behind a page (the fln_dashboard copy) and a chat page writing <App>.<QueueName>.<submitter>.
4. Reuse one mapping session per machine (every Client() opens a fresh lookup session today).
5. Item 8: retire the old plan document.
