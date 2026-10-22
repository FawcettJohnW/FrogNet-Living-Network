# Experiments on John Day's claims, run on FrogNet shared RAM

Each experiment records the passage, the architectural prediction, our interpretation, and the result that would
contradict it. The **property** under test is kept apart from the **messaging procedure** Day uses to explain it: the
procedure is how a message-passing implementation would arrive at the answer; the property is what the answer must be.
Here the procedure is replaced by participants writing their own facts into one shared memory and computing from what
they read. A result about the property is a result about his architecture; it says nothing about his procedure.

## Sources and how they are cited

- **Ch12** — Day, *Chapter 12, Fundamentals of Data Transfer II*, draft dated 2026-10-13 (.docx he sent John). Not
  present in this workspace. The two verbatim fragments below were quoted from it in the 2026-10-16 reading; every
  other Ch12 statement here is a paraphrase from that reading and is marked *(paraphrase)*. Verbatim passages are to
  be inserted from the .docx before any finding goes to Day.
- **P2-1** — RINA Reference Model Part 2-1. Line numbers refer to `RINARefModelPart2-1-230419.txt` (pandoc of his file).

Simulator results (one machine, loopback, scripted topology) are labelled **sim**. Physical-network results are
labelled **phys**. None of the results so far are phys.

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

**Passage.** Ch12 §2.8.1 *(paraphrase, one word verbatim)*: the Create Flow Responses converge through the border
routers and form a spanning tree that is "very close" to optimal; he says this was a surprise and gives no numbers.

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

## E3–E6 (later)

- **E3 Group center.** Ch12 §2.3, verbatim: "<Has the problem of dynamically determining the center of gravity of the
  group been solved?>". Built on E2's references (the center-rooted shared tree versus the per-source SPT).
- **E4 Mobility as multihoming. E5 Scoped renumbering. E6 Routing stability.** Passages to be taken from the .docx.
