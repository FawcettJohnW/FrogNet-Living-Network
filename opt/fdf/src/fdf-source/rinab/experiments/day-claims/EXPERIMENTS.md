# Experiments on John Day's claims, run on FrogNet shared RAM

Each experiment records the passage, the architectural prediction, our interpretation, and the result that would
contradict it. The **property** under test is kept apart from the **messaging procedure** Day uses to explain it: the
procedure is how a message-passing implementation would arrive at the answer; the property is what the answer must be.
Here the procedure is replaced by participants writing their own facts into one shared memory and computing from what
they read. A result about the property is a result about his architecture; it says nothing about his procedure.

## Sources and how they are cited

- **Ch12**: Day, *Chapter 12, Fundamentals of Data Transfer II*, draft of 2026-10-13 (PDF he sent John).
- **Ch1**: *Chapter 1, Symmetry Requires an Invariant*, draft of 2026-10-13. **Ch1-App**: its appendix, *The Symmetry
  of Semaphores*, 2026-09-11.
- **Ch3**: *Chapter 3, Broken Invariances*, draft of 2026-10-13. **Ch3-A**: Appendix A (ARPANET, CYCLADES and the
  Internet compared), 2026-09-03. **Ch3-B**: Appendix B (TCP's design), 2026-09-11.
- **HG**: *Abner Doubleday Comes to Networking*, the hourglass note, 2019 revised 2023.
- **P2-1**: RINA Reference Model Part 2-1. Line numbers refer to `RINARefModelPart2-1-230419.txt` (pandoc of his file).

Page numbers are the PDF's. Quotations are verbatim; anything else is marked *(paraphrase)*.

Simulator results (one machine, loopback, scripted topology) are labelled **sim**. Physical-network results are
labelled **phys**.

---

## E1. Whatevercast selection

**Passage.** P2-1 lines 432–437 and 459–470 *(paraphrase)*: a name of a distributed application is a whatevercast
name, a set plus a rule that selects from the set when the name is resolved; a DAF has at least three — all members,
a member to join through (nearest or designated), and a member that serves requests — and may have more, each with its
own rule. Ch12 §2.4 *(paraphrase)*: the same definition at the IPC level; anycast, multicast and unicast are special
cases of one mechanism; nearest is a rule.

**Prediction.** One mechanism (a set plus a rule applied at resolution) supports all, one, nearest, designated and
capability-based selection, and stays correct as membership and capabilities change during operation.

**Our interpretation.** "Set" = the members joined to the name, each with the facts it wrote itself. "Rule" = a pure
function of the set's facts (and, for nearest, of the asker's position). "Correct" = the answer equals the rule applied
to the set as it is after the last completed change. "Nearest" is read two ways and both are tested: (a) the region's
built-in `nearest` (distance between reported coordinates — geographic); (b) nearest in the network, by shortest-path
cost over the measured adjacency facts the members publish. (b) is computed by the asking participant from what it
reads; nothing in the region knows a topology.

**Property vs procedure.** Property: the selected members. Procedure not tested: directory forwarding, the Flow
Allocator's search, any message exchange that carries the rule to the members.

**Contradicting results.**
1. A rule that cannot be expressed as set + rule on the same mechanism (needs its own registry, protocol or state).
2. A resolution, issued after a change completed, that answers from the set before the change (a stale decision).
3. A held read on the name that does not wake on a change, or wakes and then resolves to a stale answer.
4. A selected member that is not in the set, fails the rule's filter, or is not live when `live` is asked for.

**Measured.** Wrong selections, stale selections, missed and spurious wakes, duplicate effects; then resolve latency
(p50/p99), change-to-wake latency, bytes on the wire per resolve.

---

## E2. Group distribution tree

**Passage.** Ch12 §2.8.1, p. 16: "Some responses will go through the same border routers, and they will create a
spanning tree! Whoa! It might not be perfectly optimal spanning tree, but because the responses follow the current
optimal routing, it's going to be very close." HG p. 10 gives the same construction for a single-sender group.

**Prediction.** The tree formed by the responses' paths is close in cost to the optimal tree for the group.

**Our interpretation.** The responses travel from each member back to the requester along the member's shortest path
to it and merge where those paths meet. The resulting tree is the union of the members' shortest paths to the
requester — on a symmetric network, the shortest-path tree (SPT) rooted at the requester; on an asymmetric network,
the reverse-path tree, whose data still flows requester → members over the reverse direction of each edge. "Optimal"
is the minimum Steiner tree spanning the requester and the members (on directed costs, the minimum Steiner
arborescence rooted at the requester). "Very close" is not given a number by Day; we report the ratio and let him
judge.

**Property vs procedure.** Property: the tree (its edges, its cost, its paths). Procedure not tested: Create Flow
Request/Response messages. Here each participant derives the tree from the membership and the adjacency facts it reads.

**References, independently checked.** Optimal cost by Dreyfus–Wagner dynamic programming, cross-checked on every
graph small enough by exhaustive enumeration of Steiner-node subsets (minimum spanning tree / minimum arborescence of
each induced subgraph). The two methods must agree on every instance or the instance is reported as a reference
failure. Written in Python, apart from the C++ participants.

**Topologies.** Chains, stars, rings, branching graphs (random trees plus extra edges), and asymmetric graphs
(a→b ≠ b→a), with seeds recorded.

**Contradicting result.** SPT/reverse-path cost well above the Steiner optimum on the topologies Day had in mind.
Because "very close" has no number, the finding states the measured ratio distribution and where it is worst.

**Correctness checks before cost.** Each participant's tree spans the requester and every member, is a tree, uses only
edges that exist, and is identical across participants reading the same state; after a topology change every
participant converges to the oracle's tree.

**Measured.** Cost ratio to optimum, path stretch per member (tree path / shortest path), convergence time after a
change, bytes on the wire, RAM manager load.

---

## S. Discovery against memory, as the network grows

**Statement under test.** Day's construction gets the data path from discovery: messages sent out, the path they took
believed and reversed. FD&F gets it from memory: membership (RINA), location (LISP) and resolving routes (FrogNet) as
shared state, each delivery resolved forward from it. E2 showed the consequences on small graphs (reach, stretch,
duplicates, construction messages); S measures them as N grows.

**Arms.** (1) Memory: the FD&F region (rina-host, fdf-facts, fdf-client, the real tunnel daemon, fdf-hub), every node
publishing its rows and reading the graph -- this IS FrogNet's discovery: FD&F replaced the old crawl, which does not
run in region mode. (2) Day's construction, in the graph model (no RINA stack runs here): the messages his discovery
sends to build and rebuild the same paths on the same graphs.

**Graphs.** The same generated graphs in every arm: sites behind gateways, closed LANs behind sites, tunnels relayed
by the hub with a delay per uplink. N = 4, 8, 16, 32, 64 and up to what the bed holds; seeds recorded.

**Events.** Cold start; a node joins; a node's uplink fails; it comes back.

**Measures.**
1. *Time to full resolution:* from the event until the LAST node holds a correct route to every reachable /24 and
   resolves every name (checked by real packets and getent on every node), wall-clock.
2. *Route efficiency:* every pair's route taken (both directions, from the kernel) against the lowest-delay route in
   the graph; measured RTT against its floor.
3. *Graph efficiency with growth:* messages and bytes per node per event, and (1) and (2), against N.

**Contradicting result.** Memory's per-node cost (CPU per event, region size) growing with E when only the routes
change; or memory's routes less efficient than Day's construction's on the same graphs.

**Correctness before speed.** A run counts only when every route is on its expected next hop and every name resolves
on every node; a pair the graph cannot reach must be reported unreachable.

---

## Claims from Chapters 1 and 3, their appendices, and the hourglass note

Across these documents Day keeps asking one question: does a property follow from the structure, or only from an
implementation choice? Ch1 p. 6 says that in his model multihoming, mobility, multicast and security "fell out for
free, i.e., the capabilities were inherent to the structure". Ch3 and HG trace the opposite case, properties the
Internet has, or lacks, because of a choice that broke an invariance. The experiments below are grouped that way. E3
to E8 test properties he says follow from structure. E9 to E12 test properties he attributes to a choice. E13 turns
his distinction on our own claim. Each experiment states what structure predicts and which result would show the
property comes from a choice instead.

For every experiment the change under test is made in two ways that keep the structure and differ in policy, and once
in a way that keeps the policy and breaks the structure. A property that survives the first and fails the second follows
from the structure. A property that changes with the policy comes from the choice.

All of these are defined and not yet run.

### Properties Day says follow from structure

#### E3. The group's center

**Passage.** Ch12 §2.3: "<Has the problem of dynamically determining the center of gravity of the group been
solved?>"

**Prediction.** A center can be chosen from the group's own state, and moving it as membership changes costs less than
it saves.

**How tested here.** Built on E2's references: the center-rooted shared tree against each source's SPT and the exact
optimum, as members join and leave. The root is a fact in the region, so moving it is one write.

**Contradicting result.** No center choice computed from the shared state beats a fixed root over a membership
sequence once the cost of moving the root is counted.

#### E4. Multihoming comes from routing to the node

**Passage.** Ch3 p. 7: "multihoming committed a network architecture to routing to the node, not the interface."
Ch3 p. 8: "multihoming is about delivering packets, not sending them. Solve the first, the second is solved for
free." Ch3 p. 9: "An address names the entity that processes the address."

**Prediction.** If what a route leads to is the node, a node with two attachments stays reachable when one fails, and
nothing that names the node changes. No multihoming mechanism is needed.

**Our interpretation.** In FrogNet a site with two egress points is a multihomed destination. The node's facts (its
name, its subnet) are written once; the attachments are separate facts. Structure: routes are computed to the
subnet/node. Policy: which attachment is preferred (planner hysteresis, `SWITCH_MIN_ABS_MS`, `SWITCH_MIN_REL`).

**How tested here.** `tier4_egress.py` (the second egress), on the TIER 4 bed and on the living network. Fail the
preferred attachment, restore it, and repeat with the planner's thresholds changed. Then the structure-breaking arm:
routes written to an interface address instead of the subnet.

**Measured.** Pairs that lose reach, time to the last pair restored, facts rewritten (expected: only the failed
attachment's own), flows that break.

**Contradicting result.** Reach is lost or a node-naming fact must change in the routes-to-node arm. Or recovery
depends on the preference policy beyond its timing, which would mean the multihoming comes from the policy.

#### E5. Mobility is multihoming over time

**Passage.** Ch1 p. 8, note 17: "it was discovered that mobility was inherent to the model, nothing additional were
required". Ch3-A p. 3 records mobility as needing "home and foreign routers, tunnels, and specialized protocols" in
the Internet but "no additional protocols" in CYCLADES.

**Prediction.** A node that moves is a node that gains a new attachment and loses the old one. The same mechanism as E4
covers it, with nothing mobility-specific.

**How tested here.** On the TIER 4 bed the relay is roaming-safe (`wg_bearer_relay.py`): move a node from one site to
another, make-before-break and break-before-make. On the living network, move a client between Seattle and New York.

**Measured.** As E4, plus whether any fact other than the attachment facts changes, and whether any component handles
the move other than the ones that handle E4's failure.

**Contradicting result.** The move needs state or code that E4's failure does not use, such as a home address,
a forwarding agent or a binding update.

#### E6. Reach without a global address space

**Passage.** Ch1 p. 6: "we found that a global network address space was not required yet all applications could be
reached." HG p. 7: "While a global address space is convenient, it isn't necessary in order to reach all
destinations."

**Prediction.** With addresses unique only within each scope, every application that can be named is reached.

**Our interpretation.** Closed LANs behind different sites given the *same* private subnet (10.20.k.0/24 reused), with
names resolved in the region. Structure: names resolve to a member in a scope, and the scope is part of the path.
The structure-breaking arm: one flat routing table that must hold every subnet once.

**How tested here.** `netns-scale-test.sh` with overlapping subnets per site, on the bed and on two sites of the living
network.

**Measured.** Pairs reached by name, pairs unreachable, which component failed for each.

**Contradicting result.** A pair reached when the subnets are unique but not when they overlap, in the scoped arm.

#### E7. Addresses belong to the layer: renumbering one scope

**Passage.** HG p. 7: "Addresses are used to deliver data to another member of the layer for layers with more than two
members. They should be used entirely within the layer. Hence, we can observe that addresses belong to layers, not to
protocols." Ch3 p. 9: "An address names the entity that processes the address."

**Prediction.** Renumbering one scope changes nothing outside it: no name, no flow above it, no fact in another scope.

**How tested here.** Renumber one site's LAN while flows cross it, then a site's tunnel addresses. Diff the region
before and after, and watch flows by name.

**Measured.** Facts changed outside the renumbered scope (expected: none), flows broken, time to every pair restored.

**Contradicting result.** A fact outside the scope must change, or a flow addressed by name breaks.

#### E8. Only a repeating structure scales

**Passage.** HG p. 9: "it is well-know that only a repeating structure scales." Ch3-A p. 3: "Repeating layers to
enable scaling, otherwise nothing. It is inherent to the architecture."

**Prediction.** One flat scope's per-member cost grows with N. Nested scopes, each the same structure, hold it bounded.

**How tested here.** Extends S. The same 200 to N subnets, once as one region and once as regions of regions, each inner
region a member of the outer.

**Measured.** Per-member CPU per change, the state a member reads, time to the last member correct, against N.

**Contradicting result.** The flat region's per-member cost stays flat over the range the beds reach (so nesting is
not what bounds it), or the nested arm grows as fast as the flat one.

### Properties Day attributes to an implementation choice

#### E9. Instability from a controller, or from delay in the loop?

**Passage.** Ch3 p. 15: "the delay in updating routers from a controller may lead to instabilities. The routers will
have reported local conditions, the controller will calculate routes based on that information and send it back to the
routers. By that time, local conditions at some routers may have changed significantly that the update is totally wrong
and will cause instabilities in network traffic."

**Why it matters here.** FD&F keeps state in one place, as a controller does, but every member computes its own routes
from it, as distributed routing does. Day blames centralization. The experiment separates who computes the routes from
how long the loop takes.

**Arms.** (1) FD&F as it is: facts written, each daemon computes. (2) A controller: one process reads the same facts,
computes everyone's routes, writes them back. (3) Distributed routing with no shared state: the rlite policies as
modelled in S. Each arm at the same loop delay (hub legs 10 to 35 ms, then WAN delays), with link conditions that
change faster than, at, and slower than the loop.

**Measured.** Route flaps per change, time spent on a route worse than the current best, oscillations (A→B→A), and the
same with the planner's hysteresis set to zero.

**Contradicting result.** Arms 1 and 2 show the same instability at the same loop delay. That would mean the delay in
the loop causes it, not where the routes are computed. A second result to watch: instability that disappears with
hysteresis would mean a policy fixes it, not a structure.

#### E10. Two-step forwarding: next hop, then line

**Passage.** Ch3 p. 16: "the forwarding process can be a two-step process: first determine the next hop, then
determine what line to the next hop. ... if the preferred line to the next hop goes down, the router could choose an
alternate without doing a routing update."

**Prediction.** With two lines to the same next hop, losing one is repaired locally, with no change to the shared
state and no route computed anywhere else.

**How tested here.** TIER 4 bed: two parallel bearers between a node and the hub (two relayed legs with different
delays). Fail the preferred one. Arm 1 keeps the next hop and switches the line locally. Arm 2 treats each line as its
own route, so the failure is a topology change.

**Measured.** Writes to the region, daemons woken, packets lost, time to restored.

**Contradicting result.** Arm 1 cannot recover without a write, or recovers no faster than arm 2.

#### E11. Management for stability, not dynamic change

**Passage.** Ch3 p. 16: "Monitoring, collecting data on the network's operation, is fundamentally a centralized
function. However, this is management, not control." Ch3 p. 17: "The management configuration should be trying to
ensure stability, not dynamic change."

**Prediction.** In steady operation the configuration does not move. Only events move it, and monitoring can be
gathered centrally without entering the control loop.

**How tested here.** S already showed nothing moves in the region at rest. Add a monitoring reader (`fdf-facts --watch` on every
fact) and measure whether it changes any member's timing. Then run a day on the living network and count configuration
writes not caused by an event.

**Contradicting result.** Writes at rest without an event, or the monitor's reads measurably delaying members' wakes.

#### E12. The narrow waist and fragmentation

**Passage.** HG p. 8: "separating IP broke fragmentation/reassembly. It has never worked." HG p. 7: "If this layer has
a procedure for creating a flow, then the layer can chose the protocol". HG p. 9: "The separation of IP from TCP to
create the 'narrow waist' was a poor design choice."

**Why it matters here.** FrogNet's tunnels carry IP inside UDP, so an inner packet that is too large fragments the
outer one. That puts the claim into our own data path.

**How tested here.** TIER 4 bed: a path whose MTU drops mid-path, ICMP "fragmentation needed" blocked (as Day notes
PMTUD usually is), and loss on the fragmented leg. Kernel TCP across a WireGuard tunnel.

**Measured.** Reassembly failures and fragment memory at the receiver (`/proc/net/snmp`, `ipfrag_*`), goodput, stalls.

**Contradicting result.** No stranded partial reassemblies and no goodput loss beyond the fragment loss rate itself.
The other arm, fragmentation inside a sequenced protocol as CYCLADES split it, does not exist on this system. It would
have to be written first.

### Our claim, tested against his distinction

#### E13. Shared memory on a computing system

**Passage.** Ch1 p. 8: "The only difference is the distinction between a processing system (shared memory can be used
for synchronization) and a computing system (without shared memory and distributed methods must be used for
synchronization)." Ch1 p. 8, the third meta-principle: "Always try to prove our conclusions wrong."

**Why it matters here.** "Day uses discovery; FD&F uses memory" presents a processing-system abstraction to members
that sit on a computing system. Day's distinction predicts that the memory is itself built by distributed methods,
and holds only where those methods hold.

**How tested here.** Partition members from the region host, one at a time and by site, for longer than any timer. Then
partition the region host itself. Watch what each member still resolves, whether it acts on stale state, and how it
recovers.

**Measured.** Wrong routes installed during and after the partition, time to correct after heal, held reads that wake
wrongly or never.

**Contradicting result for our claim.** A member acting on stale state as if current, or failing to recover on heal
without a restart. Either would show that the property comes from the region staying reachable, not from the
structure, and the report must say so.

### Claims this system does not test

- **Watson's three timers** (Ch3 p. 11: synchronization is "to enforce upper bounds on 3 times: Maximum Packet
  Lifetime, MPL, Maximum Time to Send an Ack, A, and Maximum time to Exhaust Retries, R", and "3-way handshake is not why
  it works"). Those bounds live in the kernel's TCP. Nothing FrogNet builds changes them, so a run would test Linux, not
  the claim.
- **Data transfer and feedback "barely interact"** (Ch3-B p. 1) and **PDU types against control bits** (Ch3-B). There
  is no data-transfer protocol of ours to split.
- **Symmetry of P and V** (Ch1-App). A property of the operation's definition, not of a running network.
- **Multicast as a set plus a rule** (HG p. 9: "a multicast group is a set of unicast addresses with a rule") is E1, and
  its tree is E2.
