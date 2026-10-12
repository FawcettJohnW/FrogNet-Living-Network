# FD&F experiments: results as run

Each experiment is a script in experiments/, run by `TESTS=$PWD/experiments/<x>.sh ./run.sh RLITE_ROOT 1 1`, exactly
as rlite's own tests run. `RINA_RMT_STATS=FILE` collects each agent's route log (which link or locator, when) and its
forwarding counts.

## Correction (2026-10-06): the first runs of experiments 4 and 5a proved nothing

rinaperf's `-i` is in microseconds (`rinaperf -h`: "number of microseconds to wait after each SDU is sent"). Both
scripts passed `-i 30`, so their 100 pings were over in about 3 ms, long before the event at 1 s, and both "passed"
vacuously. The tell was experiment 5a's own log: after the member died, the agent derived 0 routes, yet it had
forwarded every PDU with none lacking a route. Both scripts now pass `-i 30000` (30 ms, 3 s of pings).

## Experiment 4: a machine changes its locator mid-flow (exp4-readdress.sh): PASS

- **Configured before the run:** two namespaces, one veth link, addresses .4 (red) and .52 (green); /etc/hosts names
  for the two normal IPCPs (used by rlite's shim-udp4 registration); a shim-udp4 DIF and a normal DIF, red enrolled
  through green.
- **The event:** at about 1 s, red's address moves from 10.10.10.4 to 10.10.10.40. /etc/hosts still says .4.
- **Result:** red re-addressed at 637.402. Green's route to red moved to the new locator at 637.411, 9 ms later, with
  red's own statement in the region as the source (DNS was not asked). 0 of 100 pings were lost; the application saw
  nothing.

## Experiment 5a: a member dies mid-flow (exp5a-member-dies.sh): FAILS, open

- **Configured before the run:** three namespaces a, b, c; veth links ab, bc, ac, each with a shim-eth DIF; one normal
  DIF; enrollments b→a, c→b, and a lower flow c→a. Nothing about routes.
- **The event:** at about 1 s, b's agent is killed (only b's, found by its namespace's pids; `pkill` is not scoped to a
  namespace), b's links go down, and the a–c link comes up.
- **What healed:** b died at 750.740. Both a and c re-derived a direct route over a–c by 750.783, 43 ms later, from
  the region's signal alone, with nothing reconfigured.
- **What failed:** the ping stalled after about 35 of 100.
  - a forwarded 41 pings and c delivered 40, so pings reached c over the new route.
  - c forwarded only 35 echoes back, all from before b died.
  - No route was missing anywhere.
  - So after the heal, c's server stopped echoing, or its echoes never reached c's agent. Under investigation.

---

## Experiment 5a, run to the bottom (2026-10-06)

**Three causes, found one under the other:**

1. **The test's interval was 1,000 times too short** (rinaperf's `-i` is in microseconds). Fixed: `-i 30000`.
2. **A real bug: the agent's link thread exited silently on a device going down.** An AF_PACKET socket reports
   ENETDOWN once when a device goes down, and the thread returned on any error without a word. In 5a, b's death took
   down c's end of the b–c link, so c's agent heard nothing on any link from then on. Fixed
   (`[LINK_DOWN_IS_NOT_A_SOCKET_ERROR_V1]`): ENETDOWN is a device event (the machine states it and routes are
   re-derived); any other receive error is said, and the agent ends with status 3.
3. **rinaperf ends the session after one lost reply.** Its client waits 10 s (RP_DATA_WAIT_MSECS) for a lost reply;
   its server gives up after 10 s with no data and closes the flow. The one ping in flight when b died is lost (an
   unreliable flow, as in rlite), both sides go quiet for 10 s, and the server closes first. The server's own counters
   show it: data flow arrived 42, handed over 34, 8 failed with EPIPE (errno 32: the program had closed the flow).

**What is established:**

- **The DIF heals by itself.** b died at 029.391. a and c re-derived a direct a–c route within about 40 ms, from the
  region's signal alone, with nothing reconfigured.
- **After the heal, pings crossed the new link and reached c's server:** c's agent delivered 9 more.
- **Losses are counted, never silent:** a dropped 1 PDU for lack of a route, in the window between b's death and a–c
  coming up.

**The pass criterion was too weak, and has been tightened.** rinaperf exits 0 even after giving up on 9 consecutive
timeouts, so "rc 0" passed a dead session. The experiment now fails unless at most 2 pings are lost (the one in flight
and one in the route-less window) and rinaperf never gives up.

**Open: the in-flight PDU.** With an unreliable flow, the PDU in flight on a dying path is lost, and rinaperf (as an
application) cannot survive one loss. This is the case for reliable flows: the receiver's contiguous count is already
its stated truth in the region. A sender holding its unacknowledged PDUs could re-send those past that count when its
flow's route changes — on the event, with no timer and no re-ask — so a path change loses nothing the receiver has not
stated it took. That is the next piece.

---

## Experiment 5a with reliable flows: PASS (2026-10-06)

**`[RELIABLE_FLOWS_V1]` and `[RESEND_ON_ROUTE_CHANGE_V1]`.** A flow asking max SDU gap 0 (rinaperf `-g 0`) is
reliable, and there is no timer and no re-ask anywhere:

- **The sender** keeps each SDU, in a ring of 4,096 published slots, until its receiver states that it took it.
- **The receiver** hands SDUs over strictly in sequence, holds any that arrive early, and drops duplicates (counted).
- **The trigger is an event.** When a machine's agent has re-derived its routes, it tells the region (op `routed`),
  and the region tells that machine's programs (an event in each one's own input space).
- **The re-send.** Each program reads its receivers' stated counts (op `taken_now`) and re-sends, on its reliable
  flows, every SDU not yet stated taken.
- **Region records:** a program's record now carries its machine (`1;ns=<machine>;`).

**Result:**

- **Time:** b died at 798.502 and the routes moved to the a–c link; the whole run took 4.28 s.
- **Loss:** **0 of 100 pings lost**, and rinaperf never gave up.
- **Before reliable flows:** the same event cost the session (rinaperf's 10 s waits).

**Cost, noted for refinement.** A route-change event re-sends everything not yet stated taken, and receivers state
their count only once per quarter window (1,024 SDUs). So a short flow re-sends its whole history, and re-sends during
the gap before the new link came up found no route: about 100 per agent, counted, not silent. Two refinements:
receivers state their count on the same event before senders read it; and a program re-sends only once its machine
has a route to that peer.

**rlite's 30 tests:** 30/30 after this change.

---

## Experiment 5a with reliable flows: PASS (2026-10-06)

**Reliable flows (`[RELIABLE_FLOWS_V1]`).** A flow asked for with max_sdu_gap 0 (rinaperf `-g 0`) is reliable:

- The sender keeps each SDU it sends in a ring of the window.
- The receiver hands SDUs over strictly in sequence, holding any early ones, and drops duplicates, which it counts.
- The receiver states its contiguous count in the region every 64 SDUs (unreliable flows: every quarter window, for
  credit only).

**Re-send on route change (`[RESEND_ON_ROUTE_CHANGE_V1]`).**

- When a machine's agent re-derives its routes, it tells the region (op `routed`).
- The region puts an event in the input space of each program on that machine.
- Each program re-sends, on its reliable flows, every kept SDU past the count its receiver has stated (op
  `taken_now`).
- The trigger is the event: no timer, no re-ask.
- A lost PDU is recovered because the loss and the route change are the same event.

**Result:** 100 pings at 30 ms, b killed at about 1 s, the a–c link brought up. **0 of 100 lost**, the run finished
in 4.3 s, and the application saw nothing. rlite's 30 tests: 30/30 after the change.

**Cost (open: a correct but wasteful re-send).**

- In the run, agents forwarded about 300 PDUs for 100 pings and 100 echoes, and dropped 144 and 215 for lack of a
  route.
- Why: b dies after about 34 pings, before any 64-SDU statement. The heal produces several route re-derivations, each
  re-sending everything unstated, and some arrive while no route exists yet.
- Duplicates are dropped and counted at the receiver; nothing is delivered twice.
- **To do:** re-send only when a route to that flow's peer exists, and only what was sent before the change.

---

## Re-send made precise (`[PRECISE_RESEND_V1]`)

**The change.**

- When an agent reports re-derived routes, it says what it can reach: "dif:machine>next machine", one per reachable
  machine.
- A program re-sends on a reliable flow only when a route to that flow's peer exists and its next machine differs from
  the last one it was told. A change elsewhere, or a moment with no route at all, re-sends nothing.

**A bug found on the way:** the agent reported every re-derivation twice (the call had been added twice), which
doubled the re-sends.

**Experiment 5a again:** 0 of 100 lost.

| | before | after |
|---|---|---|
| PDUs dropped for lack of a route | 144 / 215 | 1 / 1 |
| PDUs forwarded per side | about 300 | about 170: 100 pings or echoes, plus one re-send of the ~34 sent before b died, which the receiver had not yet stated |

**rlite's 30 tests:** 30/30.

## Experiment 6: a DIF splits and reconnects (exp6-split-merge.sh): PASS

- **Configured before the run:** four namespaces in a line a–b–c–d, veth links ab, bc, cd with a shim-eth DIF each,
  one normal DIF; enrollments b→a, c→b, d→c. Nothing about routes.
- **The event:** two reliable pings run at once, a→d across the coming split and a→b inside one half. At about 1 s
  the b–c link goes down, and the DIF is two islands. At about 2 s it comes back. Nothing else is done: there is no
  reconciling or merging step. Each agent re-derives its routes from the truths then available, and reliable flows
  re-send what their receivers have not stated they took.

**Result** (split at 831.009, reconnected at 832.038):

| ping | lost | rtt min / avg / max | |
|---|---|---|---|
| a→d, across the split | 0 of 100 | 0.100 / 10.46 / 1,023 ms | the one ping caught by the split was held for the 1 s the islands were apart, re-sent and answered when they reconnected |
| a→b, inside one island | 0 of 100 | 0.068 / 0.278 / 7.8 ms | the split did not touch it |

**Agents:** c, alone between the islands, relayed 273 PDUs and delivered none locally. A PDU was dropped for lack of
a route once, at a, during the split.

---

## D5, first part: a machine is given only its area's name (expD5-one-name.sh): PASS

**`[ONE_NAME_BOOTSTRAP_V1]`** (src/rina_region_client.hpp). Where an area's region is:

- `RINA_RAM` ("host:port") if a deployment names it outright;
- otherwise the area's one well-known name, `RINA_AREA`, resolved in the network the region is reached from, at the
  area's port (`RINA_AREA_PORT`, default 8840);
- neither set is an error, said, and never guessed.

**The experiment.**

- `RINA_RAM` is removed from every program and agent the test starts; `RINA_AREA=rina-area.test`.
- **Configured before the run:** the area's name (one /etc/hosts line, where the region is reached from); two
  namespaces, one veth link, a shim-eth DIF, a normal DIF, enrollment y→x. **Not configured:** the region's address
  or port.
- **Result:** the agents, rlite-ctl and rinaperf all found the region from the name. Enrollment over shim-eth and a
  reliable ping across the link: 0 of 20 lost, rtt min / avg / max 0.076 / 0.111 / 0.343 ms. Each agent relayed
  22–23 PDUs.

**Next for D5:**

- **On a LAN with nothing configured at all,** a new machine asks its links (an Ethernet broadcast frame, ethertype
  0xD1F0) and any member's agent answers with the area's name.
- **On the Internet,** the name resolves through LISP's mapping rather than a hosts file or DNS.

---

## D5, second part: a machine with NOTHING configured joins on a LAN (expD5-lan-join.sh): PASS

**`[LAN_JOIN_V1]`, `[LAN_JOIN_ANSWERS_NAME_V1]`** (src/rina_node.cpp, src/rina_region_client.hpp). An agent with neither
`RINA_RAM` nor `RINA_AREA` asks every up link (Ethernet broadcast, ethertype 0xD1F0, type 4), and asks again on each
link-up event from netlink; there is no timer. Any member's agent answers unicast (type 5) with the area's NAME. That
machine's programs ask their own agent over the abstract socket `rina-area`.

**First run: FAIL (timeout 120 s), the test's fault, not the code's.** x was started with only `RINA_RAM=127.0.0.1:…`.
By `[LAN_JOIN_ANSWERS_NAME_V1]` a member that knows only a loopback locator does not answer. y's `rlite-ctl` then
waited on its agent forever, as the no-fallbacks ruling requires. Fix `[LAN_JOIN_TEST_MEMBER_KNOWS_NAME_V1]`: x is
told the area's name, exactly as in expD5-one-name; y is still told nothing. Log: evidence/d5/lan-join-FAIL-misconfigured.log.

**The run (2026-10-06, sandbox).**

- **Configured:** x is told `RINA_AREA=rina-area.test`. y has `RINA_RAM` and `RINA_AREA` removed from its agent and
  every program. One veth link joins them.
- **On the wire** (tcpdump in the log): y sends one broadcast type 4. x answers type 5 to y, unicast, with
  `rina-area.test:8840`. `asks=1 answers=1`.
- **Then:** y creates its IPCPs, enrolls in x's DIF over shim-eth, and a reliable ping runs.

| run | lost | rtt min / avg / max |
|---|---|---|
| alone | 0 of 20 | 0.066 / 0.117 / 0.225 ms |
| paired with one-name | 0 of 20 | 0.083 / 0.130 / 0.298 ms (one-name in the same run: 0.074 / 0.110 / 0.170) |

**rlite's 30 tests with this build:** 30/30 (evidence/suite-1-15-after-lanjoin.tsv, suite-16-30-after-lanjoin.tsv).

**Open, found here.** When no member can answer, a new machine waits silently. Under `-d` the agent's stderr is
/dev/null, so the member's single "not answering" line is lost. The waiting is correct; that nobody can see why is a
gap. The member's refusal should be stated somewhere visible, without adding a timeout.

**Tightened to the plan's definition (`[LAN_JOIN_BROADCAST_ENROLL_V1]`).** The locked D5 says "on a LAN, by broadcast
enrollment (rlite test 0025)", and the run above still named the neighbour (`ipcp-enroll y.n ndif ethdif x.n`). y now
enrolls with no neighbour named, as 0025 does. PASS: asks=1 answers=1, 0 of 20 lost, rtt 0.077 / 0.126 / 0.576 ms
(evidence/d5/lan-join-broadcast-enroll.log). On y nothing is configured about the area or its members; the only things
set on y are its own IPCPs and the link.

**What D5 still lacks against the plan:** the name resolves through /etc/hosts, a stand-in. The plan has LISP supply
the one name per area, so D5 is not complete until the name resolves through LISP mapping.

---

## D5 through LISP: the area's name resolves only through the mapping system (expD5-lisp-name.sh): PASS

**`[AREA_NAME_IS_A_LISP_EID_V1]`** (src/lisp_area.hpp, src/rina_lisp.cpp, src/rina_region_client.hpp, src/rina_node.cpp,
run.sh). This replaces the /etc/hosts stand-in in D5.

- **The name is an EID.** The area's one well-known name is a LISP distinguished-name EID: AFI 17, the name and a NUL,
  mask-len 8 × length, as lispers.net encodes it.
- **The host registers it.** At start the region host sends one Map-Register: name → its RLOC, with the P bit (proxy
  reply), the M bit (want a Map-Notify), HMAC-SHA-256, and the T bit with a 24 h record TTL. With the T bit the
  Map-Server expires the registration by its own TTL, so there is no periodic re-register; the host re-registers only
  when the host changes (`[REGISTER_ONCE_WITH_ITS_OWN_TTL_V1]`).
- **A machine resolves it.** Every program and agent sends an ECM-encapsulated Map-Request for the name to the
  Map-Resolver and connects to the RLOC in the Map-Reply, at the area's port.
- **LISP is the only path when a Map-Resolver is known.** It comes from the member's answer or `RINA_MAP_RESOLVER`, and
  then hosts and DNS are not consulted, with no fallback to them. A negative Map-Reply is an error, said.
- **A member's LAN answer carries the Map-Resolver** (`name:port@map-resolver`), so a new machine still needs nothing
  configured.

**The mapping system is unmodified lispers.net** (commit in lisp-mapping/LISPERS-COMMIT), run as Map-Server and
Map-Resolver by lisp-mapping/lisp-up.sh with lisp-mapping/lisp.config: one site allowing `'rina-area.test'`.

- **One environment shim:** this sandbox kernel has no IPv6 and lispers.net binds `0::0`. lisp-mapping/run-lispers.py
  selects lispers.net's own IPv4-only listen path (`lisp_is_raspbian`) at run time. No lispers.net file is changed.

**The run (2026-10-06, sandbox; evidence/d5-lisp/).**

- **Configured before the run:** the host registered `'rina-area.test'` → 192.0.2.2 (Map-Notify 1.2 ms, HMAC
  verified). x is told `RINA_AREA` and `RINA_MAP_RESOLVER`. y has `RINA_RAM`, `RINA_AREA` and `RINA_MAP_RESOLVER`
  removed from its agent and every program. One veth link joins them.
- **Not configured anywhere:** the region's address or port. `getent hosts rina-area.test` finds nothing, and the test
  fails if it does.
- **On the link:** y broadcasts its ask; x answers `rina-area.test:8840@192.0.2.2`.
- **y joins with nothing configured:** it creates its IPCPs and enrolls by broadcast (no neighbour named, as rlite
  0025). Its agent and programs resolve the name through lispers.net.

| | result |
|---|---|
| reliable ping y→x | 0 of 20 lost, rtt 0.086 / 0.147 / 0.508 ms |
| LISP lookups the Map-Server answered during the run | 15: one per program opening its session (each rlite-ctl call, each agent, rinaperf) |
| register (host start) | Map-Notify in 0.9–1.9 ms across runs |
| resolve alone (rina-lisp resolve) | Map-Reply in 1.3–1.6 ms |
| unregistered name | negative Map-Reply (action 1), reported as an error |

**Security checks.**

- **Wrong key, same RLOC:** refused.
- **Wrong key pointing the name at 10.9.9.9:** refused; the name still resolves to 192.0.2.2.
- **lispers.net acknowledges a failed authentication with a Map-Notify anyway.** Its log says "Authentication failed"
  and then "Send Map-Notify to ack Map-Register". So a Map-Notify's arrival proves nothing.
  `[MAP_NOTIFY_MUST_BE_SIGNED_WITH_OUR_KEY_V1]`: the registrar verifies the Map-Notify's HMAC with its own key, and a
  mismatch is a refusal, said. My first version took any Map-Notify as success; the wrong-key test caught it.

**Regressions.**

- **rlite's 30 tests: 30/30** with the default loopback host and no LISP (evidence/suite-1-15-after-lisp-name.tsv,
  suite-16-30-after-lisp-name.tsv).
- **expD5-one-name and expD5-lan-join (hosts stand-in): PASS,** 0/20 lost each.
- **Found by the regression:** run.sh is `set -u`, and an unset `LISP_MS` aborted it. Fixed with `${LISP_MS:-}`.
- These ran before `[MAP_NOTIFY_MUST_BE_SIGNED_WITH_OUR_KEY_V1]`, which touches only rina-lisp (not used by them).
  expD5-lisp-name was rerun after it.

**Open, for a ruling.**

- **Transport loss.** LISP control runs over UDP. With no retransmission timer, a lost Map-Register or Map-Request
  waits forever (said once, never hidden). This is a transport-timer question, recorded separately per the proof plan.
- **The Map-Resolver's address is configured** on members (x). It is infrastructure-wide, not area topology, the way a
  DNS resolver is. Is that acceptable bootstrap knowledge alongside "one well-known name per area", or must it come
  from DHCP or anycast?
- **Lookup work is one per program session.** A machine could ask once and serve its programs, as the LAN answer
  already does. Recorded, not changed: it is a performance choice, not a correctness one.

**Reproduced from a clean build (2026-10-06 13:45 and 13:58, a second session's own runs).**

| run | result | lookups answered by lispers.net | lost | rtt min / avg / max |
|---|---|---|---|---|
| clean build, original harness | PASS | 15 | 0 of 20 | 0.075 / 0.119 / 0.346 ms |
| reconciled harness, logs cleared | PASS | 15 | 0 of 20 | 0.075 / 0.137 / 0.815 ms |

Also with this build: rlite 30/30, expD5-one-name PASS, expD5-lan-join PASS. A wrong-key Map-Register was refused
(exit 1), and the name still resolved to 192.0.2.2. Evidence: evidence/d5-lisp/expD5-lisp-name-reproduced.log,
suite-reproduced.tsv.

**Harness defect found by the reproduction (`[LISPERS_LOGS_START_EMPTY_V1]`).** One run reported FAIL with
`lisp_lookups=0` while its ping passed. lispers.net redirects its stdout to logs/lisp-<name>.log only if that file does
not exist at start (`lisp_process_logfile`). With a leftover log it kept logging to stdout (logs/ms.out, which showed 18
proxy replies for the name during the run), and the experiment counted lookups in the stale file. The test was right to
fail: it could not see the lookups. lisp-mapping/lisp-up.sh now empties logs/ itself rather than relying on
lisp-down.sh having run.

---

## D5: the Map-Resolver comes from a capability probe (expD5-mr-capability.sh): PASS

**John's rulings (2026-10-06).** The Map-Resolver's address comes from a capability probe, not configuration. A
machine with no LAN neighbour is unity by definition: every election over one member returns that member.

**What was built.**

- **The probe, `[MR_IS_A_CAPABILITY_V1]`** (src/lisp_area.hpp `probe_resolver`, src/rina_node.cpp). At start each
  agent sends one Map-Request for its area's name to each of its own non-loopback RLOCs. The pass is a positive
  mapping: measured signal, as FrogNet's probe measures `mysql_running`, not "installed". A pass is stated in the
  machine's own record as `cap.mr=<rloc>`. The probe's wait is bounded at 1 s as part of the measurement (FrogNet's
  probe runs under `timeout=10`). It is one Map-Request and one verdict, never retried.
- **The election, `[MR_ELECTED_FROM_CAPABILITY_V1]`.** Every member derives the resolver from the same records on each
  region signal, in the same pass that derives its routes: among machines stating `cap.mr`, the lowest machine
  identity. It is computed and never stored. A lone machine elects itself if its own probe passed.
- **The answer.** A member's LAN answer is `name:port@<elected resolver>`. `RINA_MAP_RESOLVER` is gone from every
  machine in this experiment.
- **`[AGENT_LOGS_KEPT_V1]`.** With `RINA_AGENT_LOG=<dir>` (run.sh sets it to the run's directory), a daemonized
  agent's stderr goes to `agent.<machine>.log`. This closes the open item from the LAN join: a member's refusal to
  answer is now visible.
- **run.sh.** In the LISP runs (`LISP_AREA` set) the run's own agent, where the host and the mapping system are, is
  told the area's name. That makes it the area's first member.

**The experiment.** A chain root — x — y. root is the run's own agent, in the network with the host and lispers.net,
and is told only `RINA_AREA`. x and y are told nothing: `RINA_RAM`, `RINA_AREA` and `RINA_MAP_RESOLVER` are removed
from their agents and programs. The test fails if `RINA_MAP_RESOLVER` is set anywhere, or if the system resolver knows
the name.

| step | evidence (evidence/d5-mr/) |
|---|---|
| root's probe | `map-resolver probe on 192.0.2.2 for 'rina-area.test': PASS` |
| the election | all three agents: `map-resolver elected: 192.0.2.2` |
| x joins from root, y joins from x | both: `joined the area at rina-area.test:8840@192.0.2.2` |
| on the x–y link | x answers `rina-area.test:8840@192.0.2.2` |
| y enrolls by broadcast, pings x | 0 of 20 lost, rtt 0.134 / 0.164 / 0.325 ms, 15 LISP lookups |

**First run: FAIL, the test's fault.** The capture on the x–y link took 2 frames, and both were asks: x asks its own
links too. So the answer was never captured, and the test failed on missing evidence while everything else had worked
(evidence/d5-mr/FAIL-capture-too-short.log). The capture now takes 6 frames.

**Regressions on this build:** rlite 30/30 (evidence/suite-1-15-after-mr-capability.tsv, suite-16-30-…), and
expD5-one-name, expD5-lan-join and expD5-lisp-name all PASS with 0/20 lost.

**Seen in practice, still open (question to John, with his reaper question).** lispers.net died between two steps,
and the next run hung in `rina-lisp register`, waiting for a Map-Notify that could not come. It printed one "waiting"
line, and only the tool's time limit ended it. No reaper reaches a request that was never answered. The proposed cure
is a transport retransmit: resend the same nonce while unanswered, each resend counted and logged.

**Choice to confirm.** The tie-break among several capable machines is the lowest machine identity. FrogNet's
database-host election scores capability; the map-resolver capability is yes or no, so identity gives the
deterministic order.

---

## Experiment 5b, part A: the region keeps its Permanent records in a file (`[REGION_FILE_V1]`): PASS

**Why first.** In 5b the region host dies and restarts. Everything a participant owns returns by re-assertion (parts B
and C). A DIF's policy and parameters belong to no member, so nobody would re-assert them. Per the tuple-lifetime
ruling they are Permanent: stored offline and restored at once on restart. Before this, nothing in the region was
durable (E4's file covered the older v0.63 tuple memory, which the region no longer uses).

**Inventory of the region's records, by owner** (who must restore each after a restart):

| owner | records | returns by |
|---|---|---|
| the DIF | `pol|` policy, `par|` parameters | the region file (this part) |
| the machine (agent; written by rlite-ctl) | `node|`, `ipcp|`, `adj|`, `nreg|` | re-assertion by the agent (part B) |
| the program (library) | `proc|`, `sess`, `app|`, `flow|`, `fl`, `ibh|` | re-assertion by the library (part C) |

**What was built.**

- **`[PERMANENT_ENVELOPE_V1]`** (src/ctl_host.hpp). The writer decides lifetime: rlite-ctl's `pol|` and `par|`
  writes carry `"p":1` in their envelope.
- **`[REGION_FILE_V1]`** (src/rina_region.cpp). The region memory appends every Permanent write to its file (one
  `write()` per record, O_APPEND) and `fdatasync`s before publishing it. At host start, not first use, the file is
  replayed (last value per record wins; an empty value is a deletion), compacted by write-new-then-rename, and
  reopened for appending.
- **No silent degradation.** No `RINA_REGION_FILE` means the host does not start. A damaged line, or a write that
  cannot be made durable, is said and fatal.
- **Callers.** run.sh sets `RINA_REGION_FILE=$R/region.perm`, one per run. Every tool that starts a host defaults it
  to a fresh /tmp file.

**The check** (tools/region-file-check.sh; evidence/5b/region-file-check.txt):

1. Create IPCP n1 in DIF d1, then `dif-policy-mod d1 addralloc static`. The file holds the `pol|d1|addralloc` record
   and a `par|` deletion.
2. `kill -9` the host, then restart it on the same file. It reports `1 Permanent record(s) restored`.
3. `ipcps-show` is empty: the IPCP is its owner's to re-assert, and does not come back by itself.
4. Recreate n1 (standing in for the owner's re-assertion). `dif-policy-list d1` shows `[static]`, which nobody
   re-wrote.

Result: PASS.

**Regressions:** rlite 30/30 with a region file in every run (evidence/suite-1-15-after-region-file.tsv,
suite-16-30-…); one run's file reached 3.9 kB. tools/rr-count.sh 300 runs on its default file.

---

## Experiment 5b, part B: machines survive the region host and re-attach by neighbour propagation (exp5b-host-restart.sh): PASS

**John's ruling (2026-10-06):** what tells members a restarted host is back is a host-change announcement by
neighbour propagation (FrogNet's propagateNotification).

**What was built.**

- **`[HOST_ANNOUNCES_ITSELF_V1]`** (tools/apply_host_announce.py, a tagged build-time patch; the hook
  `rina_host_listening` is in src/rina_region.cpp). Once the host is LISTENING (not before, or a member reconnecting
  on the announcement would be refused), it sends one datagram to its own machine's agent (abstract socket
  `rina-host-change`). The datagram carries the host's start time, which is the announcement's sequence number, and
  its listen address. If no agent hears it, the host says so.
- **`[HOST_CHANGE_BY_NEIGHBOUR_PROPAGATION_V1]`** (src/rina_node.cpp). The agent that hears its own host announces
  on all its links: Ethernet broadcast, ethertype 0xD1F0, type 6, carrying `<seq> <origin machine>`.
  - A machine that has seen that origin's sequence number, or a newer one, drops it: not applied, not forwarded.
  - A machine seeing it first applies it: if it has no live session (checked, not assumed), it re-attaches. It
    forwards to its other links ONLY if applying it changed something. The origin always sends.
  - So an announcement crosses each link at most twice and ends by itself, with no timer and no re-ask.
- **`[AGENT_SURVIVES_THE_REGION_V1]`.** Losing the region no longer kills the agent (it used to `_exit(2)`). It says
  so, keeps the routes it last derived (flows keep moving member to member), and waits. The session is now
  replaceable: `attach()` is one path for the first attach and every re-attach. The old session object is not freed,
  because threads may still hold it; that is a few hundred bytes per host change, recorded.
- **`[AGENT_REASSERTS_V1]`** (region ops `mine` and `reassert`).
  - **The copy.** On every topology signal the agent refreshes its copy of what the machine owns: its IPCPs
    (`ipcp|<machine>|*`), their registrations in lower DIFs (`nreg|*|<its ipcp>`), and the adjacencies it is an end
    of.
  - **Re-attach.** It writes them back verbatim before stating its node. Addresses are kept, so the node's rejoin
    re-claims the same ones.
  - **Ownership.** The region checks ownership of every record in a `reassert` (EPERM, naming the record, otherwise),
    so a machine can write back only its own truths.

**The experiment.** A chain root — x — y. root is the run's own agent, on the host's machine. A shim-eth DIF and a
normal DIF run on x–y, and y enrolls by broadcast. Then:

1. A ping y → x works. The test's rinaperf server is ended (programs do not survive the host until part C).
2. `kill -9` the host.
3. Restart it, exactly as run.sh starts it, on the same region file.
4. Nothing else is done: no rlite-ctl command, no re-enrollment.

| | result |
|---|---|
| after the kill | all three agents: "the region is gone; waiting for a host-change announcement" |
| the announcement | host → root's agent (origin) → x → y; each applied it once and forwarded |
| re-attached | all three, 68 ms after the host was started; x and y each re-asserted 4 records |
| IPCPs | x.n 337348803 and y.n 912879210 before and after, with shims and DIFs identical |
| a new flow y → x, no re-enrollment | 0 of 20 lost, rtt 0.155 / 0.191 / 0.265 ms |

Evidence: evidence/5b/ (the test log, the three agents' logs, the restarted host's log).

**Regressions:** rlite 30/30 (evidence/suite-1-15-after-5b-agent.tsv, suite-16-30-…). expD5-one-name,
expD5-lan-join, exp5a and exp6 all PASS.

**Unexplained, recorded.** In one batch (one-name, lan-join, exp4, exp5a, exp6), experiment 4 lost 9 of 100 pings
(92 s; each lost ping costs rinaperf's 10 s). Its criterion only asks that pings resume, so it showed PASS. It was not
reproduced: exp4 alone lost 0 of 100 (3.6 s), and the same three-test order again lost 0 of 100 (3.5 s). It is not
attributed to part B. Next: exp4 fails on any lost ping, and it is run repeatedly on this build and on the build before
part B.

---

## Correction: experiment 4 was passing runs whose flow had died (found 2026-10-06 while checking the 9/100 loss)

**What was wrong.** exp4 checked only rinaperf's exit code. rinaperf exits 0 even after it gives up ("Stopping
after 9 consecutive timeouts"), which is the lesson already in HANDOFF, and exp4 did not apply it. Every "9 of 100
lost, 92 s" run was a flow that **died for good** at the re-address: 40 transmitted, 32 received, then rinaperf
stopped. The recorded "exp4: 0/100 lost" was a single good run.

| build | runs of exp4 (×8 each) that died |
|---|---|
| before 5b part B (fdf-modeb-d5-mr.tgz) | 5 of 8 |
| current (fdf-modeb-5b-b + this) | 2 of 8 |

It predates part B. Every death showed exactly 9 timeouts, rinaperf's give-up count.

**Fixed in the test (`[EXP4_COUNTS_WHAT_RINAPERF_DID_V1]`).** exp4 now fails if rinaperf gave up or transmitted
fewer than 100. It will FAIL on this build until the cause is fixed.

**Instrumented run (experiments/exp4-diag.sh; evidence/exp4-loss/).** Every layer on one clock: rinaperf lines,
tcpdump on both veth ends, `ip monitor` in both namespaces, and each agent's route log and counters. In the dying run
(green = …212, red = …264):

- **Kernel.** `.4` deleted at 50.917, `.40` added at 50.925, and red's neighbour entry for green was flushed with it.
- **Routing is not the cause.** Both agents re-derived within 7 ms: red routes via `.52` at 130.925, green via `.40`
  at 130.926. In the gap red dropped one PDU for lack of a route (`no_route=1`), which is ping 34. The flow is
  unreliable, so that single loss is the expected interruption.
- **Green's agent is not the cause.** It delivered every PDU red forwarded (red `forwarded=43`, green
  `delivered_here=43`). Red's later pings reach green's wire every 10 s from `.40`.
- **Green never answers them.** Green's agent forwarded only 34 PDUs in all. The loss is ABOVE green's agent: green's
  rinaperf server, or the library's reply path.
- **A stale locator on green.** At 134.96–137.0, 4 s after green's agent had moved to `.40`, something on green sent
  to red's OLD `.4` (three ARPs, all unanswered). At 140.904 green sent one 58-byte PDU to `.40`.

**Not yet established:** what on green still held `.4`, and why green's server's replies never reached its agent.
Hypothesis to test, not a finding: the library's per-flow peer endpoint (`link_peer`, `[MEMBER_TO_MEMBER_DATA_V1]`), a
copy of the peer's locator taken once at allocation. Next: instrument green's library reply path (every SDU the server
writes, where the library sends it, every error) and run exp4-diag until one dies.

**Root cause found (same day), and experiment 4 re-run: PASS 8/8.**

- **The data path was not at fault.** The 58-byte PDU green sent at 140.904 is a DATA PDU on rinaperf's control flow,
  carrying its 32-byte result: rinaperf's server ENDING THE TEST.
- **The chain.**
  1. Red has no IPv4 address on the link for about 7 ms (old address deleted, new one added). There is no route in
     that gap, so one PDU is dropped and counted (`no_route=1`).
  2. On an unreliable flow that ping is lost for good.
  3. rinaperf's server stops 10 s after the last ping it received (`RP_DATA_WAIT_MSECS 10000`, rinaperf.c). That
     always falls before the client's 10 s timeout on the lost ping, so the server ends the test and every later ping
     goes to a closed flow. rinaperf does this on any single lost ping, including on rlite.
  4. The intermittency is whether a 30 ms ping lands in the 7 ms gap.
- **Withdrawn lead.** The ARPs for `.4` at 134.96–137.0 are unicast, one second apart: the kernel's own probing of a
  recently used neighbour entry (5 s delay, then 3 probes). Nothing on green held the old locator.
- **The test measured rinaperf, not the claim.** `[EXP4_RELIABLE_FLOW_V1]`: the ping flow is now reliable (`-g 0`),
  as in 5a and 6. The claim is that a move costs only the interruption, with nothing beyond it.
- **`[EXP4_SHOWS_THE_GAP_WAS_HIT_V1]`:** exp4 prints each agent's `no_route` count, so a run shows whether a PDU fell
  into the gap at all.

| exp4, reliable flow, strict criterion (×8) | result |
|---|---|
| runs passing | 8 of 8, 0 of 100 lost in every run, 3.9 s each |
| runs where a PDU fell into the gap | 1 (r1: `gap_drops=1`, 0 lost: re-sent and delivered) |
| the instrumented run that also hit it | 100 of 100 received; the hit ping's rtt 9.19 ms against ~0.2 ms for the rest, which is the interruption |

So the re-send on route change (`[RESEND_ON_ROUTE_CHANGE_V1]`) recovers a PDU lost while the moving machine has no
address, even though the next machine is the same before and after.

Evidence: evidence/exp4-reliable/ (eight logs, and the instrumented hit's rinaperf and route log).
evidence/exp4-loss/ keeps the unreliable run that showed rinaperf ending the test.

**Also fixed:** exp4-diag.sh waited on its own capture jobs (a bare `wait`) and hung until run.sh's 120 s limit;
`[EXP4_DIAG_WAITS_FOR_ITS_EVENT_V1]` makes it wait for the re-address only. Unchanged lesson: never `pkill -f` a pattern
that is in your own command line (it killed the working shell twice more here).

---

## Experiment 5b, part C, and 5b in full: programs live through the host (exp5b-full.sh): PASS

**`[LIBRARY_REASSERTS_V1]`** (src/librina_region.cpp, src/rina_node.cpp; region ops `pmine` and `preassert`).

- **Loss is said.** The library's held wait used to END SILENTLY when its session dropped ("the session is gone: so
  is everything it carried"). It now says so, keeps the flows' data moving (that path is member to member, through
  the agent), and asks its machine's agent, with a type-5 PDU, to tell it when the host is back.
- **The agent answers.** At once if it holds a live session (checked by stating its node, not assumed); otherwise
  after its next successful attach. The answer is a type-4 PDU on the program's own data endpoint. This runs off the
  agent's RMT thread, so a region call never stalls the data plane.
- **The library re-attaches:** a new session, hello (liveness and inboxes), then what the program owns, written back
  verbatim:
  - its registrations: `app|*` whose inbox is its own;
  - its flows' records: `fl|`, and `flow|` where it is the client or the server end;
  - its flow list: `pfl:<proc>|*`.
- **The copy stays current.** The program's copy is refreshed after every register, unregister, accept and positive
  verdict: control calls, never per SDU.
- **Ownership is checked.** The region checks it on every record (EPERM, naming the record, otherwise).
- **No back door.** The dead session stays in place until `reattach` replaces it, so no stray call can open a session
  that skipped re-assertion.

**The experiment.** A chain root — x — y, with shim-eth and normal DIFs on x–y. x runs a rinaperf server registered
BEFORE the event. y runs a RELIABLE ping (200 pings, 30 ms apart). One second in, `kill -9` the host; half a second
later restart it on the same region file. Nothing else is done.

| | result |
|---|---|
| the ping that was running | 200 transmitted, 200 received; worst rtt 2.79 ms; it never gave up |
| the server | the same process (by pid) started before the kill, still running |
| a NEW client after the restart, to that server by name | 20 of 20 (its registration was re-asserted) |
| re-attached | both programs (re-asserting 6 and 7 records) and all three agents |

The first run failed on the test: `rinaperf -w` daemonizes, so `$!` was not the server. The test now takes the server's
pid from the namespace. Evidence: evidence/5b-full/.

**`[HOST_RESTARTS_IN_A_RUN_V1]`** (run.sh). A test that replaces the host used to make every later test in the same
run report HOST_GONE. run.sh now follows the restarted host: 5b-host-restart then 5b-full, back to back, both PASS.

## The map-resolver election ranks capability (`[MR_RANKED_BY_CAPABILITY_V1]`)

**John's ruling (2026-10-06):** deterministic on capability, with identity as the tie-breaker.

- **What is stated.** Each capable machine states `cap.mr=<rloc>@<cores>@<mem_mb>`. These are STATIC measures of the
  machine, so the election does not flap; live load is deliberately not an input.
- **The rule.** More cores wins, then more memory, then the lowest machine identity. It is one pure function,
  src/rina_elect.hpp, used unchanged by the agent.
- **Tested on synthetic records** (tests/elect_test.cpp, built as build/bin/elect-test; the sandbox's namespaces share
  one kernel, so they cannot differ in capability). All 6 cases pass: none capable; one capable; more cores beats a
  lower identity; same cores and more memory wins; equal capability goes to the lowest identity; a lone machine
  elects itself. Evidence: evidence/mr-rank/.

**Regression on this build (all PASS):** rlite 30/30. exp4 (reliable; its gap was hit and recovered: `gap_drops=1`, 0
lost), exp5a, exp6, expD5-one-name, expD5-lan-join, exp5b-host-restart, exp5b-full, expD5-lisp-name, and
expD5-mr-capability (both agents' probes passed; all elect 192.0.2.2).

**Next (John, 2026-10-06: "why are we sending control messages over UDP at all").** Only because the area-name lookup
was pointed at lispers.net's wire protocol, which is UDP 4342. The design has LISP mapping IN THE REGION: registering
is writing your own record, resolving is reading one, over the session (TCP; liveness is the session). Next the
area-name mapping moves into a mapping region held by the elected map-resolver host, keeping LISP's semantics
(authenticated registration, its own TTL, a negative answer). lispers.net and UDP stay only at the edge, for interop
with real-Internet LISP, and as the oracle the region's answers are checked against. The UDP-loss question then does
not arise.

---

## Correction: exp5b-full could lose a reliable flow at the restart — root cause found and fixed (`[RESEND_ON_HOST_CHANGE_V1]`)

**Found by verifying from the extracted tarball.** One run of exp5b-full failed there: the running reliable ping got 51
of 58, then rinaperf gave up. Every program and agent had re-attached correctly. It was intermittent: 8 runs on the
working tree had passed.

**Mechanism.**

1. **A window with no route.** At re-attach an agent re-derives its routes at once. If its peer has not yet re-asserted
   its records, it finds no route for about 6–11 ms and drops what arrives for lack of one. A passing run's route log
   shows it (evidence via `[EXP5B_SHOWS_ITS_ROUTES_V1]`): `derived 0 routes` twice at the restart, `no_route=1`.
2. **The re-send's trigger is lost.** On a reliable flow, the route returning normally triggers a re-send
   (`[RESEND_ON_ROUTE_CHANGE_V1]`). But a route event pushed while the program has no session is gone.
3. **The next event is suppressed too.** The library remembers each flow's last next machine ("via"). Because the
   disappearance was missed, the next route event looks like "same path, nothing lost" and is skipped. Nothing is
   re-sent, and the receiver holds every later SDU behind the missing one, for good.

**Fix: the re-arm step of the reconcile.** On re-attaching, the library forgets each reliable flow's last path and
re-sends at once everything kept that the receiver has not stated it took. After a restart that is the whole window,
because the region's statements went with the old host. The receiver drops duplicates. A PDU that finds no route yet
is dropped and counted by the agent, and the next route event re-sends it, since "via" was reset.

**Proof by fault injection.** `[FAULT_REATTACH_DELAY_V1]`: `RINA_FAULT_REATTACH_DELAY_MS`, used ONLY by
experiments/exp5b-late-program.sh, holds both programs' re-attach back 500 ms. That forces the failing order: the routes
change while the programs are detached. A ping every 2 ms, 3000 pings. The same injection was applied to both builds.

| build | exp5b-late-program |
|---|---|
| before the fix (fdf-modeb-5b-full.tgz + the injection only) | **4 of 4 FAIL**: about 680 sent, 7 short, rinaperf gives up, as in the original failure |
| after the fix | **6 of 6 PASS**: 3000 of 3000 each; logs show about 690 SDUs re-sent per program |

exp5b-full on the fixed build: **8 of 8 PASS** (200/200 each). A stress variant without the injection
(exp5b-stress.sh, a ping every 2 ms) passed even on the build before the fix: ping rate is not the trigger, the order
of re-attachment is. Evidence: evidence/5b-resend/.

**Regression on the fixed build (all PASS):** rlite 30/30. exp4 (reliable), exp5a, exp6, expD5-one-name,
expD5-lan-join, exp5b-host-restart, exp5b-full, expD5-lisp-name, expD5-mr-capability.

**Follow-up, recorded.** After a restart a sender re-sends its whole window (about 690 SDUs here), because what each
receiver took is gone from the region. A receiver's count of what it took is its own truth. If receivers re-asserted it
on re-attach, senders would re-send only what is unconfirmed.

---

## D5: LISP's mapping in a region, over the session; no control messages over UDP (`[AREA_MAPPING_IN_THE_REGION_V1]`): PASS

**John (2026-10-06): "why are we sending control messages over UDP at all."** We were only because the area-name lookup
had been pointed at lispers.net's wire protocol (UDP 4342). The design has LISP's mapping in a region: registering is
writing your own record, resolving is reading one, over the session. That session is TCP, and its liveness is the
session, so a lost-packet question cannot arise.

**What was built.**

- **The mapping region.** The map-resolver machine runs a rina-host in the mapping role (`RINA_HOST_ROLE=mapping`) on
  its RLOC at TCP 4342, LISP's control port number carried over TCP. It is not an area host, so it does not announce
  itself. Its records live in its own region file.
- **LISP's semantics, kept.**
  - A site per name, with its authentication key.
  - A registration must carry the site's key (EPERM otherwise, with the mapping unchanged).
  - A registration lives by its own TTL, checked when it is read: no timer, no reaper.
  - An unknown or expired name gets a negative answer (ENOENT), said, never a fallback.
- **Clients.** `map_lookup` / `probe_mapping` (src/rina_region_client.hpp) replace the UDP Map-Request. The agent's
  capability probe and the client library both use them. `rina-lisp map-site | map-register | map-resolve` is the CLI.
- **run.sh `MAP_AREA` / `MAP_KEY`.** The run's machine becomes the map-resolver: it runs the mapping host, adds the
  site, and the area host's registration is made over the session.

**The check (tools/map-region-check.sh): PASS.** Every case right:

- the site; registration with the right key; resolve to the RLOC;
- a wrong key refused, with the mapping unchanged;
- an unknown name answered negatively;
- a 1-second registration expired after 1.5 s;
- the registration back after `kill -9` and restart;
- 0 UDP 4342 packets.

Evidence: evidence/d5-region-mapping/map-region-check.txt.

**The experiment (experiments/expD5-region-mapping.sh): PASS.** The chain root — x — y, as expD5-mr-capability, with
no lispers.net and nothing listening on UDP 4342 (the test fails if anything is). Results:

- root's probe passed against the mapping region, and all three elected 192.0.2.2;
- x and y joined from link answers carrying `rina-area.test:8840@192.0.2.2`;
- the mapping region's own log counts 15 sessions served;
- y enrolled by broadcast and pinged x: 0 of 20 lost.

The test also sends ONE deliberate UDP packet to 4342 (`[EXP_CAPTURE_HAS_A_CONTROL_V1]`), so the capture's "nothing
else" is proven able to see. It recorded exactly 1, that control.

**Superseded:** expD5-lisp-name and expD5-mr-capability resolve over lispers.net's UDP wire, which the client no longer
uses. They are kept as the record of that path (the edge, and the oracle), and are not in the regression.

**Regression on this build (all PASS):** rlite 30/30, exp4, exp5a, exp6, expD5-one-name, expD5-lan-join,
exp5b-host-restart, exp5b-full and expD5-region-mapping.

**Open, in this item's scope.**

1. **The area host does not yet register itself.** run.sh makes the registration. On a host change (a restart at a new
   RLOC, a float), the new host must re-register its name in the mapping region itself, over the session.
2. **The mapping region's answers have not been compared with lispers.net's** for the same registrations. That oracle
   comparison is still to do.
3. **The mapping region does not yet answer real-Internet LISP** (UDP Map-Request/Map-Register from other xTRs): the
   edge.

---

## 5b with a move: the host returns at a different RLOC, registers itself, and is found there (exp5b-host-moves.sh): PASS

**`[AREA_HOST_REGISTERS_ITSELF_V1]`.**

- **One request.** `rina_region::map_register()` (src/rina_region_client.hpp) is the one authenticated registration
  request (HMAC-SHA-256 of name|rloc|ttl|ts with the site's key; its own TTL; ts orders registrations). The CLI
  (`rina-lisp map-register`) and the agent both use it. tools/map-region-check.sh still passes through it.
- **The host's agent registers it.** When the agent on the host's machine hears its host come up, it registers the
  area's name → the host's announced listen address with the ELECTED map-resolver, over the session. It does this
  BEFORE re-attaching, so the re-attach, by name, finds the host where it now is. The key is `RINA_MAP_KEY`, the
  site's secret, configured on the host's machine. If any of the name, the address, an elected resolver or the key is
  missing, that is said and nothing is registered.
- **Agents resolve through the elected resolver.** Unless the area is named outright (`RINA_RAM`), an agent resolves
  its area's name through the elected map-resolver; before any election, through itself if its own probe passed
  (unity).
- **run.sh, mapping mode.** The run's agent is given the NAME only (no `RINA_RAM`), so it reaches the area through the
  mapping like every other member. The host's FIRST registration is still made by run.sh: at start the host comes up
  before the agent, so no agent hears it.

**`[MEMBER_OFFERS_THE_AREA_V1]`: a self-joining hole found here and fixed.**

- **The hole.** A LAN ask is ONE broadcast, asked again only on a link event. A neighbour whose agent was not yet
  serving (not started, or still joining) could not hear it, and the asker waited forever. Started together, x and y
  hit this: y asked before x knew the area.
- **The fix.** A machine that BECOMES a member offers the area once, by broadcast on each link, as soon as it is
  serving. Whoever asked earlier takes the offer; whoever asks later gets an answer. Every ordering is covered, with
  no timer and no re-ask.
- **Not taken.** A first attempt remembered the askers instead. It still lost an ask made between joining and serving,
  so it was replaced by the offer.

**The experiment** (mapping mode: `HOST_LISTEN=192.0.2.2 MAP_AREA=rina-area.test MAP_KEY=…`). A chain root — x — y.
x's and y's agents are told NOTHING and started together; the programs have no `RINA_RAM`. x runs a server; y runs a
reliable ping (200 pings, 30 ms apart). One second in, `kill -9` the area host at `192.0.2.2:8840` and restart it on
the same region file at `192.0.2.3:8840`, an address added for the test.

| | result |
|---|---|
| the mapping before → after | `192.0.2.2` → `192.0.2.3` |
| who registered the move | the host machine's agent, over the session (`registered 'rina-area.test' -> 192.0.2.3`) |
| the reliable ping across the move | 200 of 200, never gave up |
| a new client to the same server, by name | 20 of 20; the server is the same process |
| re-attached by name to the moved host | all three agents and both programs |

**Host descriptors.** The first runs reported 20 open descriptors in the moved host (5 elsewhere). Measured, not
guessed (`[EXP5B_MOVE_SHOWS_HOST_FDS_V1]`): with x's and y's processes ended, the moved host holds 8, and its only TCP
peers are three sessions from root's agent, which is alive. The 20 were processes the test left running (deleting a
namespace does not kill them; the `rinaperf -w` daemon kept its sessions). The test now ends them itself. Not a leak.

**Regression on this build (all PASS):** rlite 30/30; exp4, exp5a, exp6, expD5-one-name, expD5-lan-join,
exp5b-host-restart, exp5b-full; in mapping mode, expD5-region-mapping and exp5b-host-moves.

Evidence: evidence/5b-host-moves/.

---

## The mapping region against the incumbent, lispers.net (`[MAPPING_MATCHES_LISPERS_V1]`)

The same registrations and lookups go to unmodified lispers.net (its own UDP wire, reached ONLY by these check tools,
as the oracle) and to FD&F's mapping region (over the session). lispers.net runs from lisp-mapping/lisp-up.sh.

**tools/map-oracle-check.sh: 7 of 7 agree, PASS** (evidence/mapping-oracle/oracle-check.txt; run twice, same both
times).

| case | lispers.net | mapping region |
|---|---|---|
| register the name, right key | registered | registered |
| resolve it | 192.0.2.40 | 192.0.2.40 |
| register with a WRONG key | refused | refused |
| resolve after the wrong key | 192.0.2.40 | 192.0.2.40 |
| re-register at a new RLOC (a move) | registered | registered |
| resolve after the move | 192.0.2.41 | 192.0.2.41 |
| resolve a name nobody registered | negative | negative |

**tools/map-expiry-compare.sh: expiry DIFFERS, and the region is the stricter**
(evidence/mapping-oracle/expiry-x3.txt). The same name, registered at the same moment with a 5 s TTL in both, asked
once a second until both answer negative:

| run | lispers.net first negative | mapping region first negative |
|---|---|---|
| 1 | 31.5 s | 5.1 s |
| 2 | 50.7 s | 5.1 s |
| 3 | 58.8 s | 5.1 s |
| 4 | 58.8 s | 5.1 s |

- **lispers.net** removes expired registrations from a 60 s timer (`LISP_SITE_TIMEOUT_CHECK_INTERVAL = 60`,
  lisp-ms.py `lisp_timeout_sites`). Until that timer comes round it keeps answering with the expired RLOC: here 26–54 s
  past the TTL, and by its source up to 60 s.
- **The region** checks the TTL when the name is read: no timer, and never answers past the TTL.

Recorded for the Dino findings: an expired mapping is served for up to a minute.

---

## LISP NAT traversal: design, and step 1 (a machine finds out it is behind NAT)

**Design:** experiments/DESIGN-NAT.md. The plan's item is "an RTR as the RLOC behind NAT". LISP keeps a NAT binding
open with an Info-Request every 15 s over UDP, which conflicts with two rulings (no timers, no UDP control). FD&F
instead uses FrogNet's dial-out:

- the NATed machine opens a TCP session UP to an elected RTR, a reachable member;
- its record states the RTR as its RLOC;
- the RTR relays;
- liveness is the session.

**The sandbox can do real NAT** (tools/nat-check.sh: PASS). In namespaces y (10.20.0.2) — natbox (MASQUERADE) — x
(10.30.0.2): y reaches x; x sees the connection come from 10.30.0.1; x cannot reach y's own address.

**Step 1, `[SESSION_PEER_V1]`.**

- **The hook.** A tagged build-time patch (tools/apply_session_peer.py) makes the host tell the region, once at
  "session up", the address each session arrives FROM.
- **The region.** It keeps that address per session and drops it when the session ends. Op `seen` answers "your
  session arrives from …". `rina-lisp seen HOST PORT` asks it.
- **tools/nat-seen-check.sh: PASS.** With the host at 10.30.0.2:8840:

| asked from | the host's answer | that machine's own addresses |
|---|---|---|
| y, behind NAT | 10.30.0.1 | 10.20.0.2 (not held) → behind NAT |
| x, outside | 10.30.0.2 | 10.30.0.2 (held) → directly reachable |

That is measured, never configured. Evidence: evidence/nat/.

**Next steps (DESIGN-NAT.md):**

2. Each agent states `nat=<seen>` when its seen address is not its own.
3. The RTR role: a probe, a capability, and an election (src/rina_elect.hpp).
4. A NATed machine opens a TCP RMT link up to the RTR.
5. Route derivation sends to a NATed machine via its RTR.
6. exp-nat.sh.

**Step 2, `[NAT_IS_MEASURED_V1]`: PASS.** On every attach the agent asks the host `seen`. If the answer is not one of
its own addresses, it states `nat=<seen>` in its own node record, before stating its node. It is measured, never
configured, and said when it changes.

tools/nat-agent-check.sh puts agents on both sides of the NAT, each attached to the host outside. The region then holds
exactly one `nat=`, the NATed agent's `nat=10.30.0.1`; the agent outside states none (evidence/nat/nat-agent-check.txt).

Regression: rlite 30/30 (evidence/suite-1-15-after-nat-step2.tsv, suite-16-30-…).

**Step 3, `[RTR_IS_A_CAPABILITY_V1]`: PASS.**

- **The capability.** A machine the host sees at one of its OWN, non-loopback addresses is directly reachable. It
  states `cap.rtr=<that address>@<cores>@<mem_mb>`. A NATed machine states none.
- **The election.** It is the same pure function as the map-resolver's, now one rule for any role
  (`rina_elect::role(node, key)`): capability first, then the lowest identity. It returns the winner's identity too,
  which NATed machines will route through.
- **Unit test.** tests/elect_test.cpp: 9 of 9, including three RTR cases.
- **tools/nat-rtr-check.sh.** The region holds `cap.rtr=10.30.0.2` (x, outside) and `nat=10.30.0.1` (y); both agents
  log `RTR elected: 10.30.0.2|<x's identity>` (evidence/nat/nat-rtr-check.txt).

**Steps 4–5, `[RTR_LINK_V1]`, and the experiment: PASS.**

- **The link.** A NATed machine links UP to the elected RTR over TCP, port 0x0d1f: a connection it opens itself, so
  the NAT keeps the binding while it is open. It says which machine it is. Frames are a 4-byte length plus the PDU.
- **forward().** A NATed machine sends every PDU not for itself up its link. The RTR sends a PDU for a machine that
  has a link to it down that link, before any route. What arrives on a link is forwarded like any other PDU, so local
  delivery and relaying are unchanged.
- **Liveness is the connection.** Its end is said at both ends. The NATed machine dials again on that event, and on
  each topology signal if it has no link. There is no timer.

**experiments/exp-nat.sh** (run on its own; its host is on x, outside). y (10.20.0.2) sits behind a natbox
(MASQUERADE; nothing new gets in) from x (10.30.0.2). A normal DIF runs over a shim-udp4, as in rlite test 0030. x and
y are given the area host's address only: nothing about NAT, the RTR, or y's reachability.

| | result |
|---|---|
| y finds it is behind NAT | `nat=10.30.0.1` (measured); x states `cap.rtr=10.30.0.2`; both elect x |
| the link | y dialled before x listened (refused, said), then linked up on the next topology signal |
| y → x, reliable | 20 of 20, rtt 0.085 / 0.162 / 0.922 ms |
| x → y, reliable: only down y's own link | 20 of 20, rtt 0.093 / 0.158 / 0.688 ms |
| counters | y: `tcp_up=45 tcp_rx=45`; x: `tcp_down=45 tcp_rx=45`; no_route 0, send_errors 0 |
| natbox's outside wire | 0 packets addressed to y's private net; 106 on the link port; 0 UDP |

Evidence: evidence/nat/exp-nat/.

**Not yet:**

- **A third machine** outside, reaching y THROUGH the RTR. Its route must take the RTR as the next machine for y; y's
  record would state `via=<rtr>`.
- **More than one NATed machine**, and NATed to NATed, both through the RTR.

**Reproduced (a second session, 2026-10-07):** tools/nat-rtr-check.sh PASS, and experiments/exp-nat.sh PASS:

- y → x 20/20 and x → y 20/20;
- 0 packets addressed to y's private net;
- no UDP;
- counters: 45 frames up and 45 down the link.

**A question it raised, answered (`[RTR_LINK_SAYS_WHY_V1]`).** y's log showed its link "ended" twice and "linked up"
twice, while x's showed one link arriving: was there a drop mid-run? Every link line now carries a wall-clock stamp
(ms), the connection's two ports, and the exact reason it ended: end of stream, read error and errno, an oversized
frame, a lost dial race, or a connection closed before it said which machine it is. That last case used to close
silently. The instrumented run (evidence/nat/exp-nat-link-why/), in order:

| time | what happened |
|---|---|
| 62.739 | y links up on 43614; x logs the same connection arriving. This is the one link, and it carried all 45 frames |
| 63.682 | that link ends: "the other end closed it" (teardown, which stops x's agent first). y redials at once, as designed (43624) |
| 63.682 | the kernel completes the redial from x's listen backlog, but x's agent is exiting and never accepts it, so it is never logged |
| 63.687 | the redial ends: "connection reset by peer" |
| after | further dials are refused, and the region has no RTR left |

No link dropped during the run. Both ends were teardown, and x's single arrival is the redial its exiting agent never
took.

**A third machine reaches y THROUGH the RTR (experiments/exp-nat-third.sh): PASS.**

- **Topology.** One outside LAN (a bridge in its own namespace) holds the natbox's outside leg (10.30.0.1), x
  (10.30.0.2, also the host's machine) and z (10.30.0.3). y (10.20.0.2) is behind the natbox.
- **The third party is never assumed.** Both x and z are RTR-capable, so the test reads the elected RTR and uses the
  OTHER outside machine as the third party. Every PDU between it and y must cross the RTR.
- **Nothing about NAT, the RTR or reachability is configured.** No code changed for this: the DIF's multi-hop routes
  take z to y via x, and x sends a PDU for y down y's link before any route.

| | result |
|---|---|
| elected RTR / third party | x (10.30.0.2) / z |
| y → z, reliable | 20 of 20 |
| z → y, reliable | 20 of 20, rtt 0.213 / 0.305 / 1.351 ms |
| z's counters | forwarded 67 PDUs; no link to y; `tcp_*` 0 |
| x's counters (the RTR) | forwarded 89, 45 of them down y's link, 44 up from it |
| outside wire, natbox leg | 0 packets addressed to y's private net |

**Recorded, not glossed.**

1. **The RTR changed once at start.** z (.3) was elected first, before x had stated its capability; y linked up to z.
   When x's record arrived, x won on identity (equal capability), and y moved its link to x. z logged y's link
   ending after 0 frames. That is the election being deterministic on the records present at each moment.
2. **x counted one `not_ours`:** a PDU that reached x and that x could place nowhere. It fits the moment of the
   switch, but that is not established.

Evidence: evidence/nat/exp-nat-third/.

**x's one `not_ours`, explained (`[NOT_OURS_SAYS_WHAT_V1]`).** The agent now says, once per (type, link), what a frame
not for it was. Rerun of exp-nat-third (PASS again; evidence/nat/exp-nat-third-notours.out): `type 5, 15 bytes, packet
type 1 (broadcast), on o-nx`. That is z's offer of the area (`[MEMBER_OFFERS_THE_AREA_V1]`), heard by x on the shared
outside LAN. A member that already knows its area ignores another member's offer, correctly. It was never a PDU, and
nothing was lost. The RTR switch at start (z, then x) repeated identically across three runs: deterministic on the
records present.

**Regression on this build (all PASS):**

- rlite 30/30 (evidence/suite-1-15-after-nat.tsv, suite-16-30-after-nat.tsv);
- exp4, exp5a, exp6, expD5-one-name, expD5-lan-join, exp5b-host-restart, exp5b-full
  (evidence/suite-experiments-after-nat.tsv);
- in mapping mode, expD5-region-mapping and exp5b-host-moves (evidence/suite-mapping-after-nat.tsv);
- tools/nat-rtr-check.sh, experiments/exp-nat.sh and experiments/exp-nat-third.sh (evidence/nat/).

---

## Experiment 3: FrogNet's 20 discovery topologies under FD&F (build item 4): every pair reached, 554 of 554

**The plan.** "FrogSim's 20 topologies under FD&F. Measured: every-pair reachability; route count; messages per change.
Fails if: any pair can't reach another that FrogNet's discovery reaches."

**The topologies are FrogNet's own, not re-typed.**

- **Source.** FrogNet's public repo (FawcettJohnW/FrogNet-Living-Network, commit in experiments/FROGNET-COMMIT),
  `opt/frognet_semantic/simulation/frognet_sim.py`, whose Section 2 runs 20 topologies.
- **The export.** tools/export_frognet_topos.py imports that file unchanged and calls its 20 builders in the order its
  `main()` runs them; it asserts the list against the source. It writes each topology's nodes and L2 segments to
  experiments/exp3-topologies.json: 104 nodes, 89 segments, the largest 11 nodes and 12 segments.
- **FrogNet's own baseline.** `frognet_sim.py` run unchanged: `TOPOLOGY RESULT: PASS`, `ALL TESTS PASS`
  (evidence/exp3/frognet-sim-baseline.txt).

**The rig (tools/topo-rig.py, `[EXP3_TOPOLOGY_RIG_V1]`).**

- **Machines.** Each node is a network namespace with its own agent, told only where the area's host is.
- **Segments.** Each segment is a Linux bridge: a real shared Ethernet, LAN or WireGuard segment alike.
- **DIFs.** One shim-eth DIF per segment, and one normal DIF over all of them.
- **Joining.** Node 0 is the first member. The others enroll breadth-first, each through a segment shared with a
  member already in (broadcast enrollment, as rlite 0025), then take an adjacency to every member on every segment
  they share (`ipcp-lower-flow-alloc`, as rlite 0028). So the paths are the topology's, not a tree's.
- **The oracle.** FrogNet's own criterion, everyone reaches everyone: every ordered pair pings by name.
- **Route counts** are taken from each agent's route log as it stood while the pairs were checked, before teardown,
  which re-derives.

**Result: 554 of 554 ordered pairs reached, in all 20 topologies** (evidence/exp3/mode-b.jsonl; the full table,
beside FrogNet's convergence cycles, is evidence/exp3/SIDE-BY-SIDE.md). In every topology every node holds exactly
n−1 routes, one to each other node. For the largest (multi-LAN, /30 full mesh between 4 gateways; 11 nodes) that is
110 of 110 pairs and 10 routes per node.

**Not yet measured: the plan's "messages per change"** against FrogNet's crawl (E3 measured the crawl's side: merges,
getHosts, echo and rtt per added node). Next: add one node to each topology and count what Mode B sends for it,
beside the crawl's counts from FrogNet's own simulator.

**CORRECTION (John, 2026-10-07: "And you are sure this is using fd&f").** It is not. The run above does NOT pass
experiment 3.

- **What the rig did.** It gave every machine the host's address (`RINA_RAM`). It issued every enrollment and every
  extra adjacency itself (`ipcp-enroll … <named neighbour>`, `ipcp-lower-flow-alloc` to each named member), in an
  order computed from the topology file.
- **What that proves.** That is topology knowledge leaked into configuration, which the harness rule says is not a
  pass. What 554/554 shows is narrower: Mode B routes correctly over adjacencies it is HANDED.
- **FD&F's discovery, as John stated it.** Each machine connects to the shared RAM. It writes its capability tuple and
  its own node-specific facts into its tuple space. It runs tuple queries on the capability matrix to discover the
  other machines. It sets up the dedicated socket to each.
- **Next.** The rig gives each machine its links and the way to the shared RAM, nothing else; no rlite-ctl at all.

---

## RULING (John, 2026-10-07): data travels through the shared space, never member to member

**John:**

- "LISP for discovery and the other thing [RINA] for naming, but the list of participants in the shared space can only
  come from the shared space, and communication between you and those others happens only through the shared space."
- "The data no longer runs member to member. It cannot without breaking firewall rules. It must travel through [the
  shared space]."

This is consistent with his earlier rule that connections are up only: every participant connects UP to the RAM host,
on a neutral machine.

**What it supersedes.**

- **Build item 1** ("the RINA data path back in RINA", `[MEMBER_TO_MEMBER_DATA_V1]` / `[RMT_OVER_DIF_LINKS_V1]`):
  agents carrying SDUs over UDP and Ethernet between members.
- **The NAT relay link** (`[RTR_LINK_V1]`). A machine behind NAT already reaches the shared space by its own outbound
  session, so no relay is needed.
- **DESIGN-DISCOVERY.md's "dedicated socket"** between neighbours. The participants come from the shared space's
  tuples, and reaching them is through it.

**What is already in place for it.**

- **The region.** Its `send` op still writes each SDU into the peer program's event stream, in a credit-bounded ring,
  with the by-reference form (`[SDU_BY_REFERENCE_V1]`).
- **The library's receiving half.** It still handles arriving SDUs (`"k":"s"`) and returned credit (`"c"`).
- **What changed.** Only the SENDING half was switched to member to member.

**Open, to settle with John.** Two things still talk outside the shared space:

- the LAN join's broadcast ask and offer (how a machine first finds the shared space);
- a restarted host's announcement by neighbour propagation (his own ruling, 2026-10-06).

---

## START OVER (John, 2026-10-07: "You are turning this into a huge project")

**The whole system, as John stated it:**

1. **One RAM manager, reachable by all participants.** Its scope is wherever they are: one machine, a LAN, or the
   Internet.
2. **Each machine connects up to it,** writes its capability tuple, and finds the other participants by querying the
   tuples.
3. **Web apps do RAM get and send from PHP or JavaScript.** Page input goes in as `<App>.<QueueName>.<submitter>`.
4. **rlite and RINA programs use the flow API** (custom handling; rlite is not HTTP). Its data goes through the RAM
   manager too.

Everything built between machines (the agents' routing and forwarding, the NAT relay, broadcast joining, host-change
propagation, the topology rig) is out of scope.

**Step 4 done (`[DATA_THROUGH_THE_RAM_MANAGER_V1]`, src/librina_region.cpp `sender`).** Each SDU a program writes is
ONE posted request on its own connection to the RAM manager (the region's `send`). The RAM manager writes it into the
peer program's event stream, where the library already took it from. The receiving side and the region side were
unchanged.

| tools/rr-count.sh 300 | member to member (before) | through the RAM manager (now) |
|---|---|---|
| requests per transaction, each program | 0.02 / 0.05 | 1.03 / 1.04 |
| latency per transaction | 22,264 ns | 520,050 ns |
| transactions completed | 300 | 300 |

rlite's 30 tests: 30/30 (evidence/suite-1-15-ram-data.tsv, suite-16-30-ram-data.tsv; evidence/ram-data-rr300.txt).

---

## Plan item 1, step 1: discovery by capability announcement (`[FDF_DISCOVERY_V1]`): PASS

New plan: the Claude doc "FD&F — plan and tests (restart, 2026-10-07)".

- **The RAM side** (src/rina_region.cpp). Op `announce` stores a machine's announcement (name, IPs, capability) as
  `ann|<machine>`. Op `participants` returns every announcement, with `alive` = its machine's session is up (its node
  record, cleared when the session ends).
- **The machine side** (src/rina_node.cpp). On every attach the agent announces:
  - its name is its hostname;
  - its IPs are its own non-loopback IPv4 addresses;
  - its capability is FrogNet's capability probe output, verbatim (`RINA_CAPABILITY_PROBE`; run once, kept).
  - It re-announces on every address change.
  - No probe, or one that fails, is said and nothing is announced: no announcement without capability.
- **The tool** (`fdf-participants`, FD&F's getHosts): a JSON array with `ip` and `hostname` (getHosts.php's keys,
  so pages built on it keep working), plus `ips`, `alive`, `ns` and the capability's cores, CPU benchmark and memory.
- **The check** (tools/discovery-check.sh): three machines, each its own network namespace, hostname and address,
  told only where the RAM manager is.
  - All three are listed with the right name, IP and capability, all alive.
  - machine-2 leaves; it is listed not alive, and the other two alive. PASS.
  - Evidence: evidence/discovery/.
- **Not yet in this check:**
  - joining by the area's name alone (item 3);
  - a wire capture showing no packet between machines;
  - machines on separate networks behind NAT (T1 and T7 in full).

**Step 2: no packet between machines (`[FDF_NO_MEMBER_TO_MEMBER_V1]`): PASS.**

- **The rule.** discovery-check captures the shared network for the whole run and fails on any packet between two
  machine addresses.
- **The mistake the capture caught.** At first it saw 3 packets in all. The machines were reaching the RAM manager
  through the test's own network namespace (`RINA_RAM_NETNS=/proc/$$/ns/net`), so their sessions never crossed their
  own network, and a capture with nothing to see proves nothing.
- **The fix.** Each machine now reaches it from its own network (`RINA_RAM_NETNS=/proc/self/ns/net`).
- **Result: 110 packets on the shared network, 0 between two machines.** Discovery, the participants list and
  liveness all pass as before (evidence/discovery/discovery-check.txt, wire.pcap).

## Plan item 2: the transport interface, in writing, and FD&F answering it (T2): PASS

- **src/transport.hpp (`[TRANSPORT_INTERFACE_V1]`)** holds DEFINITIONS ONLY: `Participant` (id, name, ips,
  capability, alive), `Socket` (call, post) and `Transport` (locate, connect, announce, participants, capability,
  change). No definition carries a route, a path or a neighbour.
- **src/fdf_transport.hpp** is FD&F's implementation of it:

| Definition | FD&F's implementation |
|---|---|
| locate | `rina_region::Client::locate()`: RINA_RAM outright, or the area's name through LISP |
| connect | `rina_region::Client(where)`, the FNWP session |
| announce | op `announce` |
| participants | op `participants` |
| capability | `participants`, one machine's entry |
| change | op `watch_participants`, then a held wait |

- **Supporting changes.**
  - `rina_region::Client` was split into `locate()` and `Client(where)`; `Client()` = `Client(locate())`, so every
    existing caller is unchanged.
  - **CHANGE (`[FDF_PARTICIPANTS_CHANGE_V1]`):** each watcher gets one event in its input space on every
    announcement, machine session up, or machine session down. A watcher is dropped when its own session ends.
- **tests/transport_conformance.cpp (build/bin/transport-conformance) uses ONLY the interface.** All six answer
  correctly; change returned after a re-announcement made while it was waiting. PASS
  (evidence/discovery/transport-conformance.txt).
- **Regression after the client split:** discovery-check PASS (0 packets between machines); rr-count 300 completes.

**Regression after items 1–2:** rlite 30/30 (evidence/suite-1-15-after-interface.tsv, suite-16-30-…).

## Plan item 3: OPEN QUESTION for John (blocks name-only joining)

- **The problem.** A machine is to be configured with the area's name only and to find the RAM manager through LISP.
  A LISP lookup goes to a Map-Resolver, and with the LAN broadcast retired, nothing tells a new machine where the
  Map-Resolver is.
- **Why the earlier ruling doesn't reach it.** The 10-06 ruling (Map-Resolver from a capability probe, elected) works
  for machines already in the shared space. A machine outside it cannot take part in that election before it has
  found the space, which is the very lookup it cannot make yet.
- **Candidates:**
  1. One configured Map-Resolver address per deployment, like a DNS resolver. That is two facts: the area's name and
     the resolver.
  2. The area's name resolves through DNS to the Map-Resolver, and LISP does the rest.
  3. A well-known anycast address for Map-Resolvers, LISP's own practice.
- **Meanwhile.** Items 4, 5 and 7 do not depend on it and go ahead. The RAM manager's own registration (the other
  half of item 3) does not depend on it either.

## Plan item 3, the RAM manager's half: it registers its own LISP mapping (`[RAM_MANAGER_REGISTERS_ITSELF_V1]`): PASS

- **What it does.** When the RAM manager is listening (a start, a restart, a move), it registers the area's name ->
  its listen address with the mapping region, over the session, using the shared `rina_region::map_register`. Its
  configuration, on its own machine, is `RINA_MAP_AREA`, `RINA_MAP_RESOLVER` and `RINA_MAP_KEY`. Anything missing
  is said, and nothing is registered. The mapping host itself registers nothing.
- **Nothing else registers.** run.sh's registration and the host's agent's registration are now redundant; both go
  under item 4.
- **tools/selfreg-check.sh: PASS** (evidence/discovery/selfreg-check.txt).

| step | the RAM manager's log | the mapping answers |
|---|---|---|
| start at 127.0.0.2 | registered 'rina-area.test' -> 127.0.0.2 | 127.0.0.2 (3.7 ms) |
| kill -9, restart at 127.0.0.3 | registered 'rina-area.test' -> 127.0.0.3 | 127.0.0.3 (4.3 ms) |

**Design point for item 4.** With neighbour propagation gone, how does a member that lost its session learn that the
RAM manager is back, without a timer? The answer that fits the rulings: it waits on the MAPPING. The mapping region is
itself shared space, so a member holds a read on the area's registration, and the RAM manager's re-registration (above)
is the event. It re-resolves and reconnects then. To build: op `map_wait` in the mapping region.

## Waiting on the mapping replaces neighbour propagation (`[MAP_WATCH_V1]`): PASS

- **The RAM side.** In the mapping region, op `map_watch {name, proc}` registers a watcher. Every successful
  `map_register` of that name pushes one event into each watcher's input space. A watcher is dropped when its session
  ends.
- **The client side.** `rina_region::map_wait(name, mr)` opens a session to the mapping region (the new
  `Client(where)`), watches, holds one wait, then returns the address the name now resolves to. The CLI is
  `rina-lisp map-wait NAME MR`.
- **The mistake the first run caught.** It FAILED with "unknown op map_watch": the op sat inside a block entered only
  for map_site / map_register / map_request. Fixed by adding map_watch to that condition.
- **tools/mapwait-check.sh: PASS** (evidence/discovery/mapwait-check.txt). The watcher waited while the RAM manager
  was at 127.0.0.2. After kill -9 and a restart at 127.0.0.3, it returned by itself with 127.0.0.3, 60 ms after the
  kill. The RAM manager's own re-registration was the event; nothing polled.
- **Next.** The agent uses this on losing its session (re-attach on the mapping event), and neighbour propagation
  goes.

## Machines follow a moving RAM manager through the mapping, by themselves (tools/move-check.sh): PASS

- **The agent.** On losing its session, the agent waits on the mapping (`rejoin_on_mapping`). It sets the watch FIRST,
  then tries to re-attach, then holds the wait, so a registration made between a failed attempt and the wait cannot be
  missed. It re-attaches by name when the RAM manager registers; anything missing (`RINA_AREA`, `RINA_MAP_RESOLVER`)
  is said.
- **The check.** Three machines (own namespace, hostname, address), CONFIGURED with only `RINA_AREA=rina-area.test`
  and `RINA_MAP_RESOLVER=10.60.0.1` (candidate 1 of item 3's open question).
  - The RAM manager registers itself at 10.60.0.1; the machines join by name and announce. All three are listed
    alive.
  - kill -9; it restarts at 10.60.0.2 on the same region file and registers itself again.
  - All 3 machines re-attached by themselves, 73 ms after the kill, re-announced, and are listed alive at the new
    address.
  - Wire: 61 packets, 0 between two machines.
- **PASS** (evidence/discovery/move-check.txt, evidence/discovery/move/). This is the replacement for host-change
  neighbour propagation, so propagation can now go (item 4).

## Item 4a: host-change neighbour propagation removed

- **Removed.** From the agent: the announcement send / receive / forward (`[HOST_CHANGE_BY_NEIGHBOUR_PROPAGATION_V1]`),
  the listener for its own host's announcement, the agent-side registration of its host
  (`[AREA_HOST_REGISTERS_ITSELF_V1]`, now the RAM manager's own), and the type-6 frame handling. From the host: the
  datagram to its local agent. `rina_host_listening` now only registers the RAM manager's own mapping.
- **After it:** move-check PASS (3 of 3 re-attached by name on the re-registration, 135 ms after the kill; 0 packets
  between machines), selfreg-check PASS, discovery-check PASS (0 between machines).
- **Examined, not glossed: the wire.** move-check's wire shows 877 packets, against 61 in the first run.
  - **The first run's count is no baseline.** Its capture shows no traffic at all to the new address 10.60.0.2,
    though its machines did re-attach there, so that capture was incomplete.
  - **This run, measured.** 33 sessions to the mapping (4342), 11 connection attempts to the old address
    (10.60.0.1:8840, refused after the kill), 9 to the new one.
  - **The agents are not retrying.** Their logs show each machine made at most ONE failed attempt (refused, to the
    old address), then re-attached once.
  - **Where the 33 come from.** Every `rina_region::Client()` opens a fresh lookup session to the mapping, and an
    agent creates several clients over a start, an outage and a re-attach.
  - **Open:** reuse one mapping session per machine, or the address `locate()` already found.

## Item 4b: the LAN-join broadcast ask and the member's offer removed

- **Removed.** The agent's broadcast ask on its links (`[LAN_JOIN_V1]` type 4), its answer to others' asks, the
  member's offer on becoming a member (`[MEMBER_OFFERS_THE_AREA_V1]`), and the type-4 receive path.
- **Kept.** The machine-LOCAL socket where its own programs ask their agent for the area, which never leaves the
  machine.
- **A machine with nothing configured now refuses, plainly:** "no area: give this machine the area's name
  (RINA_AREA) and the mapping region (RINA_MAP_RESOLVER), or RINA_RAM; it no longer asks its links".
- **After it:** move-check PASS (3 of 3 re-attached, 128 ms; 0 packets between machines), discovery-check PASS (0
  between machines).

## Item 4c, the library's half: no member-to-member send left

- **Re-sends.** The re-send after a RAM manager change (`[RESEND_ON_HOST_CHANGE_V1]`) now posts through the RAM
  manager, as every SDU does.
- **Removed.** The re-send on route change (`[RESEND_ON_ROUTE_CHANGE_V1]`) and the `"rt"` event that drove it: no
  routing, so no route changes. Also `send_pdu` (an SDU handed to the agent to forward member to member), which now
  has no caller.
- **Kept: machine-local only.** A program's subscription to its own agent, and its agent's wake.
- **rlite 30/30** (evidence/suite-1-15-after-4c-lib.tsv, suite-16-30-…).

(John, on the restart: the earlier work wasn't a failure; its tests showed what worked, and that is what made it
clear most of it shouldn't exist. Retire it, keep the evidence.)

## Item 4c, the agent's half: the agent is local-only (`[AGENT_IS_LOCAL_ONLY_V1]`)

- **Removed:** the RMT's Ethernet and UDP links (`g_pkt`, `g_udp`, `rx_links`); routes (`Hop`, `Routes`,
  `derive_routes`' route building, `rloc_toward` and helpers); forwarding between machines; the NAT relay
  (`TcpLink`, `rtr_listen`, `dial_rtr`, `link_for_nat`); and NAT and RTR detection (`nat=`, `cap.rtr=`).
- **Kept:** attach, state, announce, re-assert, waiting on the mapping, and the machine-local socket for programs'
  subscriptions. `on_topology` keeps only the map-resolver election (item 3, still open).
- **rina_node.cpp: 784 lines -> 370.**
- **After it:** discovery-check PASS, move-check PASS (3 of 3 re-attached, 182 ms), selfreg-check PASS; 0 packets
  between machines in each.
- **Wire, A/B on the same check:** before the strip 393 and 109 packets, after it 89 and 89. The count varies from
  run to run; the strip added nothing (evidence/discovery/wire-ab-4c.txt).

**rlite after 4c (agent local-only): 30/30** (evidence/suite-1-15-after-4c.tsv, suite-16-30-after-4c.tsv); transport-conformance PASS.

## Item 4d: the RAM side's routing op removed

- Op `routed` (route-change events to programs) is removed: it had no caller after 4c.
- Kept: `taken_now` (the re-send after a RAM manager change uses it) and `rmt_view` (`on_topology` reads the node records through it).
- After it: the host builds clean; discovery-check PASS, with 0 packets between machines.

## Item 4e: experiments and tools built on the old design retired

- Moved to experiments/retired/ (17) and tools/retired/ (7), each folder with a README saying why; kept for their evidence, not run.
- Experiments retired: exp4, exp4-diag, exp5a, exp5a-diag, the exp5b set (host-restart, full, host-moves, late-program, stress), exp6, exp-nat, exp-nat-third, and expD5 one-name, lan-join, lisp-name, mr-capability, region-mapping.
- Tools retired: the topology rig and its exporter, exp3-run, and the four NAT checks.
- The current checks: tools/discovery-check.sh, selfreg-check.sh, mapwait-check.sh, move-check.sh, map-region-check.sh, map-oracle-check.sh, region-file-check.sh, and build/bin/transport-conformance; plus rlite's 30 tests.

## Item 5, step 1: an FD&F dictionary for BLDC-1, recorded from real traffic (`[FDF_PRIME_V1]`)

- **Recorded, not hand-written.** Learning runs (`RIBBIT_TEMPLATES_RECORD`) on the flow path (rr-count), the
  discovery path (discovery-check) and the mapping path (move-check), merged with rina.prime by tools/build-prime.py.
  One exemplar per (path, op, field shape), so a program's control hello and its data-set hello are two templates.
- **templates/fdf.prime: 20 exemplars**, covering every FD&F message in use: alloc, accept, hello (two shapes),
  node, wait, fork, ctl, register, mine, pmine, send, announce, participants, map_site, map_register, map_request,
  map_watch. Evidence: evidence/bldc/learn-*.prime.
- **Measured on the data set** (rr-count 300; evidence/bldc/wire-send-fdf-prime.txt):

| dictionary | raw | full | diff | bytes out |
|---|---|---|---|---|
| rina.prime | 1 | 1 | 300 | 32,708 |
| fdf.prime | 0 | 2 | 301 | 33,079 |

- **Correcting what I said earlier.** "The first `send` goes in full because it isn't primed" was wrong.
  - `raw` is a message with no template; `full` is a templated message carrying all its values.
  - The first message of each instance must be full, because there is nothing earlier to diff against; priming
    cannot remove that.
  - Priming removed the one raw message. For the data path that is one message per session, so it makes no
    measurable difference.
- **Next lever:** the ~108 bytes each diffed `send` costs. Break those bytes down, and measure FrogNet's Python
  BLDC-1 on the same messages.

## Item 5, step 2: a flat single-SDU `send`, so BLDC-1 diffs its fields (`[FLAT_SEND_V1]`)

- **The finding** (evidence/bldc/send-frame-breakdown.md). Each diffed `send` carried the whole `sdus` array:
  BLDC-1 treats an array as ONE atomic value, so the unchanged flow id (~20 bytes) went with every SDU.
- **The change.** The library posts one SDU as `{"op":"send","fid","q","d"}`. The RAM side accepts that flat form
  beside the array form, through the same handling.
- **The dictionary.** templates/fdf.prime was re-recorded with the flat `send` (21 exemplars).

| rr-count 300, data set | array send | flat send |
|---|---|---|
| bytes per send frame | 111–117 | 87–88 |
| bytes out | 33,079 | 25,546 (−23%) |
| transactions completed | 300 | 300 |

- **rlite 30/30** with the flat send (evidence/suite-1-15-flat-send.tsv, suite-16-30-…; evidence/bldc/).
- **Still to do for item 5:** the same messages through FrogNet's Python BLDC-1, side by side.

## Item 5, step 3: FD&F's `send` beside FrogNet's Python BLDC-1 (tools/bldc-compare.py)

- **The method.** FrogNet's Python BLDC-1 (core.codec.SemanticCodec and core.semcache_wire, driven as its own
  ribbit/cpp/tests/test_fnwp_oracle.py drives them, origin injected as the proxy does) encodes 300 `send`s with the
  flow id and data of the recorded exemplar and `q` counting up (evidence/bldc/python-bldc1-send.txt).
- **Like for like.** Our TCP payload starts with a 4-byte length; the FNW1 frame follows. The table compares FNW1
  frames.

| per steady-state send frame (FNW1) | FD&F C++ (rr-count) | Python BLDC-1 (same shape) |
|---|---|---|
| flat {fid,q,d} | 83–84 bytes | 76 bytes |
| array {sdus:[[fid,q,d]]} | 107–113 bytes | 153–155 bytes |

- **Caveat: the inputs are NOT yet identical.** Python's run holds the data constant (only `q` changes), while our
  frames' diffs appear to carry a second changed field (rinaperf's data varies per packet). An exact comparison drives
  both with the same captured sequence.

**Step 3, exact inputs (`[SEND_TRACE_V1]`).**

- **Capturing the real sends.** `RINA_SEND_TRACE=FILE` (library instrumentation) records every posted send body.
  rr-count 300 posted 605 sends over 2 flows, with the data different on every one (305 distinct;
  evidence/bldc/send-trace.jsonl). So a diff carries `q` AND `d`, which explains the caveat above.
- **Replaying them.** tools/bldc-replay.py runs FrogNet's Python BLDC-1 over those exact bodies, one reference per
  flow (evidence/bldc/python-replay.txt).

| steady-state send frame (FNW1) | FD&F C++ | FrogNet Python BLDC-1 |
|---|---|---|
| mean | 83.4 bytes (n=599; evidence/bldc/cpp-send-mean.txt) | 79.8 bytes (n=603) |
| range | 83–84 | 74–113 |

- **Result.** FD&F's C++ BLDC-1 is 3.6 bytes heavier per send on average (about 4.5%), and steadier.
- **What makes it inexact.** The two sides are the same workload but separate runs: the C++ capture is from the
  flat-send run, the trace from a run with the trace on.
- **Not yet examined:** where the 3.6 bytes come from, which needs the two frames decoded field by field.

## Item 7, the dashboard: FD&F's participants on a web page, live, through its own server only (`[FDF_DASHBOARD_V1]`): PASS

- **www/fdf/getParticipants.php** is FD&F's getHosts.php: the RAM get happens on the page's own server, over that
  machine's FNWP connection (`fdf-participants`), with the same keys (ip, hostname) plus ips, alive and capability.
- **www/fdf/waitParticipants.php?gen=N** is a long-poll. `fdf-participants --wait N` holds on the transport's
  CHANGE in the RAM manager and returns only when the participant set changes, with the new generation. No timer.
- **www/fdf/fdf_dashboard.html**, modelled on fln_dashboard, shows a status line and a participant table (machine,
  addresses, alive, cores, CPU benchmark, memory). Its first view comes from getParticipants, and after that it
  loops on waitParticipants. It reads only its own server; there are no routes or edges to draw.
- **tools/dashboard-check.sh: PASS** (evidence/web/), run under PHP 8.3's built-in server with 4 workers (one held
  long-poll must not block the rest).
  - The page is served (HTTP 200), and the first view lists the participants.
  - The long-poll was HELD with no change, then returned generation 1 when a participant announced.
- **Not tested here:** the page rendered in a real browser (no browser in the sandbox). The check exercises exactly
  the requests the page makes.

## Item 7, the chat: page input as <App>.<QueueName>.<submitter>, a service, and a page, all through the shared space (`[FDF_CHAT_V1]`): PASS

- **No FD&F handler is involved: plain Ribbit.** The RAM manager's own `write` / `read` (`read` with `after` and a
  held wait) are reached by a new CLI, `ram` (src/ram_tool.cpp), over the machine's FNWP connection, located by the
  transport.
- **www/fdf/chat_send.php** (POST from, text) writes `chat.inbox.<from>`: the page input, as
  <App>.<QueueName>.<submitter>.
- **tools/chat-service.py**, the chat app's service, holds a read on the queue `chat.inbox` (every submitter) after
  the last cell it handled, and appends each message to `chat.room.<n>` as {from, text, at}.
- **www/fdf/chat_wait.php?after=N** is the room's messages newer than N, held until one exists. **www/fdf/chat.html**
  has a form, and a room view looping on chat_wait. Both pages read only their own server.
- **tools/chat-check.sh: PASS** (evidence/web/chat-check.txt).
  - The page is served (HTTP 200), and the room read was HELD with no message.
  - alice's post (chat.inbox.alice) reached the held read via the service as #1 from alice, "hello from alice".
  - bob's post made the room #1 alice, #2 bob, in order.
- **One bound that is Ribbit's, not FD&F's.** Ribbit's held read returns empty at its maximum wait (`WAIT_MAX_S`),
  and the service and page then hold the next read. That bound is in the RAM manager's own `read`; neither adds a
  timer.

## Item 5, step 4: on the exact same sends, FD&F's C++ BLDC-1 is byte-identical to FrogNet's Python BLDC-1

- **The method** (tools/bldc-exact.py). The 605 `send` bodies FD&F posted (evidence/bldc/send-trace.jsonl) go
  through FrogNet's Python BLDC-1 AND through FD&F's C++ FNWP engine. The C++ side is FrogNet's own
  ribbit/cpp/tests/fnwp_driver.cpp, built against THIS tree's patched platform (build/platform/ribbit_cpp), with the
  canonical value form of FrogNet's test_fnwp_oracle.py. Each side chains its own reference per flow.
- **Result** (evidence/bldc/exact-cpp-vs-python.txt): **605 of 605 frames byte-identical.** Steady state: Python
  79.8 bytes (74–113), C++ 79.8 bytes (74–113).
- **Correction to step 3.** The 3.6-byte gap (83.4 vs 79.8) was NOT the encoder: it compared two different runs,
  whose data differed. Frame for frame, the structure was always the same: a 25-byte header, 13 bytes of template
  and location, the diff body, and a 22-byte origin trailer.
- **Item 5 is done.**
  - The dictionary covers every FD&F message.
  - The flat send cut 23% of the data path's bytes.
  - The engine matches the incumbent byte for byte.

## The "33 mapping connections" item, examined: near the minimum, and the theory behind it was wrong

- **Built anyway** (`[LOCATE_CACHED_V1]`). `locate()` keeps its answer per process. It is forgotten when a connection
  to it fails, and before any re-attach after losing the RAM manager (the agent's rejoin, a program's reattach), so a
  moved RAM manager is always re-resolved. move-check PASS (3 of 3 re-attached, 124 ms; 0 packets between machines).
- **The count did not change** (36 connections to 4342). The mapping host's own log explains why: 18 SESSIONS, and
  every FNWP session opens two TCP connections (request socket and data socket).
- **The 18:**
  - 1 site setup;
  - 2 registrations by the RAM manager (start, and after the move);
  - about 5 per machine: the initial lookup, the watch, one failed attempt at the old address, and the lookups after
    the move.
- **Conclusion.** That is close to the minimum for the event. My earlier theory ("every Client() opens a fresh
  lookup session") was wrong; the agent creates few clients. The cache stays: it is harmless and saves repeat
  lookups for a machine's programs.

**Regression after the locate cache:** rlite 30/30 (evidence/suite-1-15-locate-cache.tsv, suite-16-30-…).

## Item 3 resolved: completely hands off; the Map-Resolver comes from the area's name (`[MR_FROM_THE_AREA_NAME_V1]`): PASS

**John (2026-10-07):** "how does lispers do it now?", then "it must be hands off. Completely automatic."

- **How lispers.net does it, from its source** (docs/lisp-config-commands.txt; lisp.py `lisp_mr`): the Map-Resolver
  is always configured.
  - `address = x.x.x.x`, one or more.
  - `dns-name = mr1.lispers.net`: every A record becomes a Map-Resolver, used in turn; re-resolved on use when more
    than 30 s have passed.
  - LISP-Decent (`decentralized-pull-xtr-modulus` + `-dns-suffix`): the EID is hashed modulo N, and the
    Map-Resolver is DNS name `<n>.<suffix>`.
  - Nothing finds a Map-Resolver without being told an address, a name or a suffix.
- **FD&F, hands off: the one thing a machine is given is the area's name.** The area's mapping system is the DNS SRV
  record `_lisp-mr._tcp.<area>`, lispers.net's dns-name method derived from the area name instead of configured.
  - Targets come in SRV order: priority, then weight. The next target is tried only when one cannot be reached
    (RFC 2782), and each failure is said.
  - It is looked up on use, never on a schedule.
  - No record is said; nothing falls back.
  - `rina_region::srv_lookup()` / `map_resolvers()` give one rule for the client's `locate()`, the agent's rejoin
    (its watch), and the RAM manager's self-registration (at every map-resolver of the area).
  - An explicit `RINA_MAP_RESOLVER` remains possible for a deployment that wants one; it is configuration, not a
    fallback.
- **tools/handsoff-check.sh: PASS** (evidence/handsoff/).
  - **Configured.** Each machine: `RINA_AREA` ONLY. The RAM manager: the area's name and the site's key (to sign its
    own registration). Published once when the area was created: `_lisp-mr._tcp.rina-area.test` SRV ->
    mr.rina-area.test, A 10.60.0.1.
  - The RAM manager found the mapping through SRV and registered itself. All three machines did the same, resolved
    the RAM manager, joined, announced, and were listed alive.
  - kill -9; the RAM manager restarts at 10.60.0.2 and re-registers itself. All 3 machines re-attached by
    themselves, 276 ms after the kill, and are listed alive at the new address.
  - Wire: 0 packets between machines; 24 SRV queries in all.
- **What remains a one-time act:** publishing the SRV record when an area is created, as a domain publishes its mail
  servers. Making even that automatic (the mapping host updating DNS itself, RFC 2136) is a possible next step.

## Item 3 resolved again, by RULE: the Map-Resolver's address is derived from the area's name (`[MR_ADDRESS_FROM_NAME_V1]`): PASS

- **John (2026-10-07).** "I was trying to remove [DNS]. Can we create a deterministic rule", then option B: an
  address derived from the name. This SUPERSEDES the earlier DNS SRV design (`[MR_FROM_THE_AREA_NAME_V1]`, the
  "Item 3 resolved" section above). Its lookup code is removed, and its check is in tools/retired/handsoff-check.sh.
- **The rule** (experiments/DESIGN-MR-RULE.md). `h = SHA-256(area name)`. In the sandbox the address is
  198.18.0.0/15 + h's first 17 bits (never .0 or .255); in deployment it is fd46:4446::/32 + h's first 96 bits (the
  prefix is a placeholder for John). The machine serving the area's mapping binds that address (anycast).
- **Two independent implementations agree** on 5 of 5 names: C++ `rina-lisp mr-address`, and tools/mr-address.py
  (evidence/item3/mr-address-crosscheck.txt). rina-area.test -> 198.18.82.28.
- **One rule everywhere.** `rina_region::map_resolvers()` returns `RINA_MAP_RESOLVER` if set (an override), else the
  derived address. The client's `locate()`, the agent's rejoin and the RAM manager's self-registration all use it.
- **tools/rule-check.sh: PASS** (evidence/item3/rule-check.txt).
  - The machines are given ONLY `RINA_AREA=rina-area.test`.
  - The network has a route to the reserved prefix (anycast routing; it knows no area).
  - Each machine computed 198.18.82.28. The RAM manager registered itself there, and all 3 machines joined and
    announced.
  - The RAM manager moved (10.60.0.1 -> 10.60.0.2); 3 of 3 re-attached by themselves, 310 ms after the kill.
  - Wire: 566 packets, 0 between two machines, **0 DNS packets**.
- **Research behind the choice:** evidence/item3/lispers-map-resolver.md (lispers.net always configures its
  Map-Resolver: an address, a DNS name, or LISP-Decent's DNS suffix), evidence/item3/rina-name-resolution.md (RINA's
  lowest layer resolves names by DNS or ARP), and evidence/item3/deterministic-rule-options.md.

**Regression after the rule:** rlite 30/30 (evidence/suite-1-15-rule.tsv, suite-16-30-rule.tsv).

## The "site key" is a password, and is called one (John, 2026-10-07)

- **The name.** `RINA_MAP_KEY` became `RINA_AREA_PASSWORD` in the RAM manager's code, its messages, and every check
  script. The HMAC-SHA-256 signing is unchanged. LISP's own term ("site key", lisp_area.hpp) is left where it
  describes LISP itself.
- **Made once, when the area is created** (`head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'`: 64 hex
  characters). It is set in /opt/fdf/fdf.env on the mapping machine and on any machine that may serve as the RAM
  manager. Members never read it: neither the agent nor the library references it.
- **The node scripts** take it from fdf.env instead of an argument (`fdf-mapping.sh IFACE`,
  `fdf-ram-manager.sh IFACE [ROUTER]`). Without it they stop and say where to set it.
- **Checks.** rule-check PASS after the rename (3 of 3 re-attached, 269 ms). The node dry run with a generated
  password: the mapping took it, and the RAM manager registered with it.
- **The wrong password is refused, EPERM** (evidence/node-dryrun/wrong-password-refused.txt).
  - My first attempt at that test was invalid: fdf-ram-manager.sh sources fdf.env, which overrode the wrong password
    I passed with the real one.
  - The valid test ran rina-host directly with the wrong password.

## The interface arguments removed from the node scripts (John: "And the interface? What is that for?" ... "Yes")

- **fdf-mapping.sh** (no arguments) binds the derived Map-Resolver address on `lo`. That is the anycast convention:
  the network's route to 198.18.0.0/15 brings traffic to the machine.
- **fdf-ram-manager.sh [ROUTER]** listens on, and registers, the source address of this machine's own route to the
  mapping system.
  - When the mapping is on THIS machine (the route is `local`, and its src would be the anycast address itself), it
    uses the default route's source address, or else the first global address not on lo.
  - ROUTER is only the route to 198.18.0.0/15, when the network does not route it yet.
- **Dry run from the tarball** (evidence/node-dryrun/dry3-*), all PASS:
  - fdf-mapping.sh bound 198.18.82.28 on lo.
  - The RAM manager co-located on mapnode picked 10.70.0.1, not the anycast address, and registered it.
  - A member joined.
  - The RAM manager was moved to m2 with only ROUTER; it picked 10.70.0.12 and registered it, and the member
    re-attached by itself.

## Direct mode: one RAM server everyone is told about, e.g. a droplet (`[FDF_DIRECT_V1]`): PASS

- **The use (John):** a machine with two interfaces (FrogNet and DHCP to the Internet) and a RAM server on his droplet, which the members connect to.
- **fdf-ram-server.sh** runs the RAM server on 0.0.0.0:8840, with no mapping system, no password and no registration (it says once that the name is not registered).
- **fdf-member.sh,** when `RINA_RAM` is set, skips the route to the reserved prefix and connects straight to it.
- **Dry run from the package** (evidence/node-dryrun/direct-*): the member connected to 10.80.0.1:8840 directly, announced itself, and was listed alive.
- **Limit:** with no mapping system, members do not follow a RAM server restart; restart them.

## The kart demo (John's kartdemo.zip) runs against the RAM manager: a plain-HTTP front (`[HTTP_FRONT_V1]`): PASS

- **The demo.** A pygame racer with two regions: race (`race.kart.<name>`, each kart's pose) and comm
  (`comm.tile.<name>`, each player's call tile). Its Ribbit client, ribbit.py, is pure Python over plain HTTP
  (ram.php: POST ?op=write, GET ?op=read), and its read reply is named `cells`.
- **The gap.** The RAM manager spoke only FNW1: an HTTP request got no answer. FrogNet's own ram_host is FNW1-only
  too; the demo's "kart-ram" is not in FrogNet's repository.
- **The extension** (tools/apply_http_front.py, applied last at build). A connection whose first four bytes are "GET "
  or "POST" is one HTTP request: read, run through the SAME api() as FNW1, answered as HTTP/1.1, closed. A read's
  rows are also given as `cells`. FNW1 connections are untouched; both share one port.
- **Checks** (evidence/kart/):
  - HTTP write and read work; an FNW1 client on the same port is answered.
  - The demo's ribbit.py, unmodified, writes and reads.
  - Two instances of kartdemo.py, unmodified (red, blue; SDL dummy driver, 8 s each): both karts' live poses are in
    the race region, both call tiles are in the comm region, and neither instance printed an error.
  - rule-check PASS after the change.
- **Deployment.** The kart demo has its own region (`fdf-kart-ram.sh`: port 8799, `--api /ram.php`) beside FD&F's
  RAM server on the droplet. Each application has its own memory.
- **Not shown here:** the game rendered or played by a person (no display in the sandbox).

## A native Windows FD&F client (`[FDF_WINDOWS_CLIENT_V1]`): PASS (under Wine)

- **John (2026-10-07).** "those are all now just calls to get and set shared memory" ... "the fdf client does that
  socket thing, by itself, for that region" ... "This has some interesting implications." Design:
  experiments/DESIGN-WINDOWS-CLIENT.md.
- **src/fdf_client.cpp**, one portable program (Linux and Windows):
  - it connects one FNWP session to the region's RAM manager;
  - it writes its node record, so it is alive while the session lasts;
  - it announces its computer name, IPv4s and capability (cores and memory, from the OS);
  - it holds a read until the session ends, then says why and exits.
- **Made portable** without changing Linux behaviour:
  - `[FUTEX_PORTABLE_V1]` (tools/apply_futex_portable.py): the session client's 5 untimed waits and 2 helpers go
    through a shim. Linux keeps the futex syscall; Windows uses WaitOnAddress / WakeByAddressAll.
  - `[WINDOWS_PORTABLE_V1]` (tools/apply_windows_portable.py) covers the three POSIX-only spots this tree's own
    patches added: the dictionary reader (getline; Linux still loads the 21 exemplars), O_CLOEXEC, and mkdir's mode.
  - src/win/openssl/: SHA-256 and HMAC-SHA-256 over Windows' own CNG (bcrypt, documented since Vista). Windows build
    only.
- **tools/build_windows_client.sh** cross-builds with mingw-w64 (lz4 from GitHub, static) to build/win/fdf-client.exe:
  a PE32+ x86-64 console program, statically linked, with no DLLs (3.7 MB).
- **Checks.**
  - Linux build of the same source: listed alive, then not alive when killed.
  - The .exe under Wine against a real RAM manager: "VM joined the region ... announced". `fdf-participants` on
    Linux lists VM alive (the capitals are GetComputerName's; the memory figure is GlobalMemoryStatusEx's). After the
    exe stopped, VM is listed not alive. Evidence: evidence/windows-client/.
- **Not yet run:** on real Windows hardware.

**Regression after the portability patches:** rlite 30/30 (evidence/suite-1-15-portable.tsv, suite-16-30-portable.tsv).

## The old map-resolver probe and election removed from the agent (superseded by the name rule)

- **Removed:**
  - the agent's capability probe (`[MR_IS_A_CAPABILITY_V1]`), which printed "map-resolver probe ... connect
    failed" on every member;
  - the `cap.mr` statement;
  - the election (`[MR_ELECTED_FROM_CAPABILITY_V1]`, `on_topology`);
  - in attach, the override that set `RINA_MAP_RESOLVER` from the elected or probed resolver, which BYPASSED the
    name rule whenever an election had produced anything.
- **What stays.** Topology signals still re-assert this machine's records (`refresh_mine`). rina_node.cpp: 370 lines
  -> 327.
- **rule-check: PASS** (evidence/item3/rule-check-after-mr-strip.txt): 3 of 3 re-attached in 301 ms; 0 packets
  between machines; 0 DNS. There are no probe lines in the agents' logs, and the wire for the same check fell from
  767–869 packets to 239, because the probes had been opening sessions.

**Regression after the map-resolver strip:** rlite 30/30 (evidence/suite-1-15-mr-strip.tsv, suite-16-30-mr-strip.tsv).

## FIRST RUN ON REAL HARDWARE (John, 2026-10-07): the kart demo races across Windows and Linux: "It is working"

- **The setup.**
  - AI-Host (10.250.250.100) runs `fdf-ram-server.sh` (FD&F, 8840) and `fdf-kart-ram.sh` (the kart region, 8799).
  - The players are a Windows machine (kartdemo.py from fdf-windows.zip, players John and Alice) and FrogNetHost
    (Raspberry Pi, kartdemo.py over remote X).
- **Found on the way:**
  - An old `kart-ram` held port 8799 and did not answer plain HTTP; it was stopped.
  - The players first pointed at 10.250.250.1, not AI-Host's 10.250.250.100.
  - libjemalloc2 was missing on AI-Host (apt).
  - `cat >` replaced FrogNetHost's fdf.env (restore it from the tarball, then append with `>>`).
  - The prebuilt FD&F member is x86-64; FrogNetHost (arm64) needs it built there.
  - A RAM server restart leaves direct-mode members detached until restarted.

## Calls from the race through the real Communicator (`[KART_CALLS_V1]`; John: "a Call button by each user's name ... it opens the communicator to that user")

- **kart/kartdemo.py** (the original is kept as kart/kartdemo.original.py).
  - **The button.** Each other live player's tile draws a Call button.
  - **Calling.** A click launches `comms-app --ram <race>:8800 --media <race>:8994 --name <me> --auto-join <them>`,
    and my tile publishes `"calling": "<them>"`.
  - **Being called.** A game that sees a live tile calling it launches `comms-app` WITHOUT --auto-join (once per
    caller), so the call rings in its window and the player answers. The callee does not auto-join because both
    windows auto-joining would each call the other.
  - **New flags:** --comms-ram, --comms-media, --comms-app.
- **opt/fdf/bin/fdf-comms.sh** runs the Communicator's memory (comms-ram, 8800) and media server (comms-media, 8994)
  on the race machine, from the Communicator's build (COMMS_BIN).
- **Checks:**
  - The call flow, with a stand-in comms-app that logs its arguments, two players under the SDL dummy driver, and
    alice pressing Call on bob (test switch KART_TEST_CALL). alice launched `--name alice --auto-join bob`; bob's
    game launched `--name bob` (no auto-join); the tiles read alice "calling bob", bob "in a call"
    (evidence/kart-calls/).
  - fdf-comms.sh, with stand-in binaries, started comms-ram, waited for it, then comms-media; with none, it says so.
- **Not run here:** the real comms-app, comms-ram and comms-media (not built in this sandbox), and a real click.

## The real Communicator, from John's bundle (frognet-communicator-bundle, the 2026-10-06 version): builds, and the call path works

- **The bundle.** It is the 2026-10-06 version (docker/, COMMS_SERVERS_ONLY); all 111 files match its MANIFEST.txt.
  It is NOT what is checked in: GitHub HEAD is f4977bc (2026-10-03), whose communicator has no docker/.
- **build.sh: all four built** (comms-app, comms-feed, comms-media, comms-ram; evidence/comms/build.log).
  - Its first run stopped at `apt-get update`: a broken third-party apt source in this sandbox (NodeSource, 403)
    makes `set -e` stop the script before it compiles anything.
  - That is the same weakness found on 2026-10-03, still in this bundle. With that source moved aside, it built.
- **fdf-comms.sh with the real binaries:** comms-ram on 0.0.0.0:8800, comms-media on 0.0.0.0:8994.
- **The call path, real comms-app** (QT_QPA_PLATFORM=offscreen, no audio, synthetic video, screenshots;
  evidence/comms/alice.png, bob.png):
  - bob was started as his game answers (no --auto-join), and alice as her game calls (`--auto-join bob`).
  - alice's window is in the call with bob, sending 1280x720 at 987 kb/s and waiting for his keyframe.
  - bob's window shows alice "in a call" and a green "Answer alice" button: the call rings in his window, as
    designed.
- **Not shown:** a person pressing Answer, and video both ways.

**Servers-only build (as the bundle's Dockerfile does it), proven here:** cmake -DCOMMS_SERVERS_ONLY=ON, targets comms-ram comms-media: built (evidence/comms/servers-only-build.log).

## A static rina-host, for Ubuntu 22.04 (StreamingFrog, the droplet) (`[STATIC_HOST_V1]`)

- **The failure (John's StreamingFrog run).** The packaged rina-host, built on Ubuntu 24.04, failed on 22.04:
  "GLIBC_2.38 not found", "GLIBCXX_3.4.32 not found". The Communicator built fine there with its own build.sh.
- **The fix** (tools/build_static_host.sh). rina-host is linked fully static from the same objects (jemalloc, lz4,
  libcrypto, libstdc++ and glibc inside), so it runs on any x86-64 Linux. The package now ships it as
  /opt/fdf/bin/rina-host, used by fdf-ram-server.sh, fdf-kart-ram.sh, fdf-ram-manager.sh and fdf-mapping.sh.
- **Check:** the static rina-host served an FD&F member: fdf-client joined it, and fdf-participants listed it alive.
- **Not covered by this change:** the other packaged binaries (the member agent, fdf-participants, fdf-client) are
  still dynamic, built on 24.04. On 22.04 they need building there, or the same static treatment. The droplet only
  needs rina-host.

**--comms-args** (pass-through to every comms-app the game launches, e.g. a camera and mic, or a smaller test pattern): checked with the logging stand-in (evidence/kart-calls/comms-args.txt).

**Caller first, as in the game** (real comms-app, alice --auto-join bob, then bob 3 s later): alice "in call ... with bob"; bob rings ("Answer alice") (evidence/comms/bob-caller-first.png).

**Bug found on real hardware, fixed: a callee answered a caller only once per game.**

- **The symptom (John):** from Linux, Call on Alice opened Linux's window; Alice's never opened.
- **The cause.** Each game remembered callers it had answered and never forgot them. Linux's earlier calls (the ones
  that died on the 8994 timeout) had put Linux on Alice's list.
- **First fix, insufficient.** Forgetting a caller once its tile stopped calling did not work: in the test, back-to-back
  calls were never seen to stop.
- **The fix.** Each call carries its own id (`call_id`, the time Call was pressed); the callee opens a window once per
  NEW id.
- **Test** (evidence/kart-calls/repeated-calls.txt): five calls from alice in 15 s, against a stand-in that exits after
  3 s. bob's game opened a window for 4. The fifth came while bob's window was still open, where a real comms-app
  rings anyway.

## fdf-services.sh start|stop|status: the droplet's services together

- **What it runs.** FD&F (8840), the race (8799) and the Communicator (8800, 8994).
- **How.** Each runs in its own process group (id in /opt/fdf/var/<name>.pgid), its output to the starting terminal.
  stop ends each group, so the Communicator's servers end with the script that started them.
- **Checks** (evidence/services/):
  - start: all four ports listening;
  - start again: "already running", no duplicates;
  - stop: all DOWN, no rina-host, comms-ram or comms-media left;
  - a port held by something else: "kart: NOT started -- port(s) 8799 already in use by: python3", the rest started;
  - status names each port's listener (rina-host, comms-ram, comms-media).

## The game now depends on FD&F, so FD&F's part is provable (`[FDF_GATED_V1]`, `[FDF_WHERE_V1]`)

- **John:** "Is there any way I can prove that this is using FDF instead of anything else?" Before this change, no:
  the game and the Communicator connected to typed addresses, and stopping the FD&F client changed nothing.
- **What changed:**
  - **Where things are.** fdf-services.sh start publishes `fdf.where.race` (host, port) and `fdf.where.comms` (ram,
    media) in the FD&F region (the droplet's public address, or FDF_PUBLIC). The game, given only
    `--fdf <droplet>:8840`, reads them.
  - **Who is here.** The game reads FD&F's participants list (over the RAM server's HTTP front). Each kart and tile
    carries its machine's FD&F name (COMPUTERNAME on Windows, the hostname on Linux, as the FD&F clients announce).
    The game draws only members' karts and tiles, offers Call only between members, and shows a status line.
- **The proof, three phases** (two machines alicebox and bobbox, each running fdf-client; screenshots and logs in
  evidence/kart-calls/p1-*, p2-*, p3-*):
  1. Both members: alice's screen shows "FD&F: alicebox is a member (2 alive)", bob's kart, bob's tile with Call; her
     call reaches bob.
  2. ONLY bobbox's FD&F client stopped (FD&F lists bobbox not alive; bob's game still racing): bob vanishes from
     alice's screen ("1 alive", no kart, no tile, no Call); no call is made; bob's screen reads "bobbox is NOT a
     member" with no Call buttons.
  3. bobbox's client started again: bob is back and alice's call goes through.
- **fdf-services.sh** published both locations, read back from the region (evidence/services/publish.out).

## REAL HARDWARE (John, 2026-10-07): the Communicator through the droplet, from the race

- **Setup.** StreamingFrog (the droplet, 165.232.153.144) runs FD&F (8840), the race (8799) and the Communicator's
  memory and media server (8800, 8994), via fdf_service. Two Communicators run on the Windows laptop (a private
  192.168 address behind NAT, unable to ping the far side); both games are up.
- **Measured by John:**

| | |
|---|---|
| video | 850x480 at 24 fps on both, between 400 and 800 kb/s; no lag; the resolution switches automatically |
| audio | sounds good |
| total throughput | under 2 Mbps (Windows Task Manager) |

- **What it shows.** Machines that cannot reach each other are in a live audio/video call, every connection going
  outward to the droplet and everything between them passing through its RAM and media servers.

**The game starts the FD&F client itself (`[FDF_AUTOCLIENT_V1]`; John: "This should be automatic ... check and start it
if it's not there").**

- **What it does.** With --fdf, after the first look at FD&F's participants, a game whose machine is not a member
  starts the FD&F client: fdf-client.exe next to kartdemo.py on Windows (in its own console window), fdf-client
  beside it or in /opt/fdf/bin on Linux, or --fdf-client <path>. It keeps running after the game: it is the
  machine's membership.
- **Test** (evidence/kart-calls/autoclient-*):
  - no clients before;
  - game 1 printed "vm was not an FD&F member: started .../fdf-client 127.0.0.1:8840" (1 client);
  - game 2 on the same machine started none (still 1 client), and its screen reads "FD&F: vm is a member (1 alive)",
    showing game 1's player with Call.

## Quiet in direct mode (`[QUIET_DIRECT_V1]`; John: "unregistered messages from FDF again")

- **The direct-mode registration message.** With no area configured (fdf-ram-server.sh, the droplet), rina-host no
  longer prints "the area's name is NOT registered: RINA_MAP_AREA is not set": no registration is intended. It still
  says so when an area IS set but its password is missing or the registration fails.
- **The exit counters.** [KNOWN_SHAPE_V1] and [HOST_COALESCE_V1] print only with RINA_DIAG=1.
- **Checks:**
  - started and stopped as fdf-ram-server.sh does: only the restore line and the startup line;
  - rule-check PASS (registration with an area still works: 3 of 3 re-attached, 0 between machines, 0 DNS;
    evidence/item3/rule-check-quiet.txt).

**The game restarts its FD&F client whenever it has ended** (John: Windows red after the droplet's services were restarted). A restart of the FD&F server ends every client's session; the game now starts the client again (at most every 10 s) whenever the machine is not a member and its client is not running. Test: game with its client; FD&F server killed and restarted; the game started the client again unprompted ("started" twice) and FD&F lists the machine alive (evidence/kart-calls/client-restart.out).
