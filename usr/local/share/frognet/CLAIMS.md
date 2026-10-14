# CLAIMS.md — what is shown, and by what

Written for a sceptical reader, human or machine. Each claim names the test or
log in this tree that shows it, or says that nothing here shows it. Paths are
repository paths; `docs/cleanup-2026-10/` holds the October 2026 cleanup's gate
records (`T*.md`) and their logs.

If a claim you care about is not in this file, treat it as **argued** until you
find the test that shows it.

Revision 2026-10-13.

---

## Shown by a test or a log in this tree

| Claim | Shown by |
|---|---|
| Two LAN-only nodes find each other, name each other, route to each other's /24 and elect the same database host, with no WireGuard and nothing outside the LAN | `opt/fdf/src/fdf-source/rinab/tools/netns-lan-only-test.sh`, 19/19 (`docs/cleanup-2026-10/t5-logs/lanonly.txt`); two installed Ubuntu 24.04 machines (containers), `docs/cleanup-2026-10/t4b-logs/final2-h1-state.txt`, `final2-h2-state.txt`, `final2-g2-cross.txt` |
| A clean install on Ubuntu 24.04 x86-64 passes its own checks and comes up LAN-only after the reboot | `docs/cleanup-2026-10/t5b-logs/g1-install.txt`, `t5-logs/g1-after-reboot.txt`, `t6a-logs/walk-lan-only.txt` |
| Nodes at two sites reach each other through a region's hub (3/3 pings each way) | `docs/cleanup-2026-10/t4b-logs/g3-n1-after-reboot.txt`, `g3-n2-after-reboot.txt`; `netns-region-loop-test.sh` (`t5-logs/region.txt`) |
| The region recovers from a loss of the region host's network (55 s between disconnect and reconnect in the log) with no manual step; pings both ways again 20 s after | `docs/cleanup-2026-10/t4b-logs/g3-outage.txt` |
| A region needs no internet: the region host can be any address the members reach | `fdf-region-create` takes that address (`opt/fdf/bin/fdf-region-create`); the gates used a private 172.30.0.10 (`t4b-logs/g3-region-create.txt`). Not shown on a network with no internet uplink at all |
| A node with no WireGuard key makes its own, announces it, and brings its hub link up | `netns-region-loop-test.sh` with `NOKEY=a` (`docs/cleanup-2026-10/t5-logs/nokey.txt`) |
| One node in two ponds, its forward pond switched without a restart | `netns-ponds-daemon-test.sh` (`docs/cleanup-2026-10/t5-logs/ponds.txt`) |
| A member reaches into another pond as that pond's configuration allows (the Kotlin bridge and fdf-tun) | `netns-reach-test.sh`, 70 checks (`docs/cleanup-2026-10/t5-logs/reach.txt`) |
| An upgrade (`--preserve`) keeps the node's identity and key and removes what the tree retired | `docs/cleanup-2026-10/t4c-logs/g4-before-upgrade.txt`, `g4-after-upgrade.txt`, `g4-usr-local-bin-*.txt` |
| An uninstall leaves no FrogNet file and puts the machine's name resolution back | `docs/cleanup-2026-10/t4b-logs/g5-m2-uninstall.txt`, `g5-m2-leftovers.txt` |
| The shipped runtime has no rendezvous-service code and no second mechanism behind a failed one | `docs/cleanup-2026-10/static-no-broker.sh` and `static-no-fallback.sh`, 0 violations (`t5b-logs/static-*.txt`) |
| The simulator's offline engine reproduces 20 route-table baselines recorded on hardware | the 20 files in `opt/frognet_semantic/simulation/baselines/` are stamped `"source": "hardware"`; `simulation/run_all.py` TIER R passes (`docs/cleanup-2026-10/t5-logs/runall.txt`) |
| The real planner and committer converge on the simulator's topologies | `simulation/run_all.py` TIER 2, "real-engine 23 topologies" PASS (`t5-logs/runall.txt`) |
| The discovery and election logic meets its oracles | `python3 -m discovery selfcheck` 14/14 and `discovery/run_discovery_oracles.py` 84/84 (`docs/cleanup-2026-10/t5b-logs/selfcheck.txt`, `oracles.txt`) |

## Recorded elsewhere; not shown by anything in this tree

These are recordings and figures published on the website. Nothing in this tree
reproduces them, and the cleanup did not re-run them.

| Claim | Where it is recorded |
|---|---|
| A two-way HD video call sustains under 150 kbit/s over 900 MHz HaLow (22 fps measured) | `/demonstrations.html` §01 |
| A call continues across a deliberate network partition | `/demonstrations.html` |
| A value written in one city is read in another with no application messaging | `/index.html`, `/demonstrations.html` |
| The media ladder steps down and recovers under contested RF | `/simulator.html` |
| Compression against ordinary traffic: 2.3× worst case, 9.6× on churning telemetry, 8,741× on a static blob | the tool that produced them ships, `usr/local/bin/pipe_workload.py`; no recorded run is in this tree |
| An unchanged exchange is a 21-byte FNWP-1 frame, 25 on the wire | spec §11.10; no test in this tree asserts the size |
| Did-vs-would bandwidth on a live node, about 89% reduction | `/demonstrations.html` |
| Kart race and a Communicator call on John's hardware, a phone among them | the Croakus Part 0.6; `opt/fdf/src/frognet-android/docs/ANDROID-HANDOFF.md` (John's report). The Communicator stage of the region-loop test is skipped in the gates |

## Argued from the architecture, not measured

These follow from the design and are stated as reasoning. They are not
experimental results.

- That the programming model removes categories of application code: coordination,
  discovery, retry, session management (spec §10.4).
- That contention cannot arise where each writer owns its key. This is a property
  of the model, and **nothing in the fabric enforces it**: the store accepts a
  second writer to any key (spec §10.4).
- That the write ceiling moves with the machine holding the role (spec §17.3).
  The mechanism is specified; the ceiling has not been measured.
- That a node runs correctly in a hypervisor guest. Not tested. Privileged systemd
  containers on one host are what the October 2026 gates used.

## Not shown, and stated as such

| | |
|---|---|
| **Raspberry Pi after the cleanup** | John's Pi nodes ran FrogNet in the field before October 2026. The cleaned tree has not been installed on a Pi; the aarch64 `.run` was built, not run |
| **Size** | The largest region in the gates had three members, the largest LAN-only pond two nodes. Nothing larger was measured |
| **Prediction** | The simulator reproduces what hardware did. No run has yet predicted something a real machine then confirmed |
| **Concurrency** | The simulator runs merges one at a time in a single process, so a green suite is not evidence about races |
| **Loss and jitter on a topology** | Modelled in the transport tier over a single link only |
| **Independent verification** | Every claim here was produced and checked by one author. Nobody outside has reproduced any of it |
| **A second implementation by someone else** | None exists |
| **The phone app after the cleanup** | No APK was built. The cleanup did not change the app's sources (`git diff --stat 42465dd -- opt/fdf/src/frognet-android/app` is empty) |
| **The Communicator servers on a node** (`fdf-comms.sh`) | Not run on a node in the gates |

## Known limits

- **One serialization point** is both the coherence guarantee and the write
  ceiling.
- **No change notification.** Deciding which changes matter is the application's
  job.
- **No cross-value atomicity.** `get_all` is not a snapshot.
- **In a region, a /24 has one owner**; a second claimant is refused
  (`opt/fdf/bin/fdf-hub`). `docs/OPERATING.md` section 6 lists the rest.
- **Four `PENDING SOURCE` values** the specification could not locate in source
  and declines to invent: discovery cadence (§6), the reassignment-callback
  registration API (§10), the template disuse interval (§11), association retry
  policy (§13).
- **Two of three falsification challenges carry no oracle** (§18.5).

## How to disprove any of it

Install it (`docs/OPERATING.md`) on machines you control, run the gates
(`README.md`, "Running the gates"), and post what failed.
