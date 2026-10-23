# The converging tree, and the cost of learning the network

*2026-10-23 · John Fawcett*

§2.8.1 of the Chapter 12 draft (2026-10-13) says the converging Create Flow Responses "will create a spanning tree",
and that "because the responses follow the current optimal routing, it's going to be very close" to optimal. Tested
against the exact optimum, each part holds only under a condition the text does not state:

1. **It is a tree** only with one deterministic next hop per node. With a per-flow choice among equal paths, two
   members on a shared node each receive the message twice, at twice the cost.
2. **Used outward from the requester, it reaches every member** only if each link a response used works both ways.
   With some one-way links it missed members in 30 of 42 graphs.
3. **It is near-optimal** only with symmetric costs and no cheaper shared path that members' own shortest paths
   bypass. A shared trunk makes the ratio as large as one likes; asymmetric costs made it 4.5× for one member.

All three come from one source: the data path is the reverse of the path a response happened to take. A requester that
reads the topology as state and resolves each member's path forward needs none of the three conditions (Appendix A has
a graph small enough to check by hand for each finding).

The second result is about learning the network. Under rlite's policies, modelled on one 200-subnet network, every cost
of knowing the network follows its physical shape: from 2 to 101 member hops to the farthest member, 199 to 9,901
transfers per change, and a refresh of 169 KB to 31.6 MB every 30 s for as long as the network exists. In a shared
region, measured on the real FrogNet daemon, each member publishes only its own facts, the region holds about 200 KB at
every egress count tried, and nothing moves at rest. Only the routes computed from that state reflect the physical
network, so an algorithm written against it does not have to change with the topology (Appendix B; physical numbers
marked **[physical: …]**).

---

## Appendix A: the tree, in detail

The passage (§2.8.1, p. 16):

> The Create_Flow Responses are sent back to the IPC Process that generated the Request. Some responses will go
> through the same border routers, and they will create a spanning tree! Whoa!
>
> It might not be perfectly optimal spanning tree, but because the responses follow the current optimal routing, it's
> going to be very close.

Two claims: the converging responses form a tree, and because each follows current optimal routing the tree is very
close to optimal. Tested as written: each member's response takes its routed path to the requester, and the tree is the
union of those paths. "Optimal" is the cheapest tree connecting the requester to every member (it spans the group, not
the whole network: a Steiner tree, on directed costs the minimum Steiner arborescence rooted at the requester), computed
exactly by Dreyfus–Wagner and cross-checked by exhaustive search on every instance. Costs below are one message, summed
over the links it crosses. The passage does not say in which direction the group's data then uses the tree; finding 2
applies when data flows from the requester outward along it.

### 1. Converging paths form a tree only with one next hop per node

    r ─3─ x ─3─ z ─1─ m1
    r ─3─ y ─3─ z ─1─ m2

Requester r reaches z through x or through y at equal cost; members m1 and m2 hang off z.

| Next-hop rule | Links used | Cost | Copies each member receives |
| --- | --- | --- | --- |
| one deterministic next hop per node | r–x–z, z–m1, z–m2 | 8 | 1 |
| each response chooses among equal paths (per flow) | r–x–z and r–y–z, z–m1, z–m2 | 16 | 2 |
| optimum | r–x–z, z–m1, z–m2 | 8 | 1 |

With a per-flow choice, m1's response goes through x and m2's through y: z joins both branches, and every member
receives the message twice. This holds for any number of members on z (the cost stays 2×). Across 1,500 generated
graphs, 16 were not trees under the per-flow rule.

**Condition needed:** destination-based forwarding with one deterministic next hop per node.

### 2. Used outward from the requester, the tree reaches every member only if each link it used works both ways

    r ⇄ a ⇄ m      (cost 1 each way)
    m → r          (cost 1, one way only)

(Built by hand.) Member m's shortest path to r is the one-way link m → r, so its response arrives that way. Data must come back over
r → m, which does not exist: m is not reached. Resolving the path forward from the requester gives r → a → m, cost 2.

In 42 strongly connected graphs with about 30% of links one-way, the reversed paths missed at least one member in 30;
forward resolution reached every member in all 42.

**Condition needed:** every link a response used must carry data in the other direction.

### 3. Near-optimal is not a property of the construction

Shared trunk, symmetric links: r has a direct link of cost 10 to each of k members, and a trunk of cost 9 to a hub h
that reaches each member at cost 2.

    r ─10─ m_i            (each member's own shortest path: 10 < 9 + 2)
    r ─9── h ─2── m_i     (the group's cheapest tree: 9 + 2k < 10k)

| k | Converging tree | Optimum | Ratio |
| --- | --- | --- | --- |
| 2 | 20 | 13 | 1.54 |
| 3 | 30 | 15 | 2.00 |
| 8 | 80 | 25 | 3.20 |

The converging tree costs 10k and the optimum 9 + 2k, so the ratio tends to direct cost / hub-to-member cost (here 5).
Lowering the hub-to-member cost, while keeping each member's direct link its own shortest path, makes the ratio as
large as one likes.

Asymmetric costs, one member: two ways from r to m, one cheap toward r (1 + 1) and dear away from it (9 + 9), the other
the reverse (5 + 5 toward, 2 + 2 away). The response takes the cheap way toward r; data then pays its dear side:
**18 against an optimum of 4 (4.5×)**, for every number of such members.

On 600 generated graphs with symmetric links the tree was within 10% of optimal in 88%, worst 1.89×; with asymmetric
costs, 74%, worst 6.0×.

**Condition needed:** symmetric costs, and members whose individually shortest paths do not bypass a cheaper shared
path. Even then "very close" needs a number.

### What follows for the tree

The three conditions have one source: the data path is taken by reversing the path a response happened to travel. A
requester that reads the topology as state and resolves each member's path forward needs none of them: in the same
graphs its shortest-path tree reached every member, including all 42 one-way graphs, and delivered no duplicates in any
of the 1,500. It is not optimal either: within 1.9× on the generated graphs, but the shared trunk defeats it as it
defeats the converging tree (3.2× at k = 8). The exact tree is computable from the same state; which tree to build is
policy.


## Appendix B: the cost of learning the network, in detail

One network of 200 subnets, its egress level given 1, 2, 5, 10, 20, 50 and 100 egress points. In both cases what each
member contributes is the same: its own interfaces, addresses and leases. The difference is how the others come to know
it.

| | rlite's policies, modelled | Shared region (FD&F) |
| --- | --- | --- |
| How a member's facts reach the others | enrollment, then flooding: each member forwards each new object to every neighbour but the sender; every member's own objects re-sent every 30 s | the member writes its facts once into the region; each reader is woken on change and reads it there |
| What every member holds | the whole link-state database, directory and address table | nothing beyond its own facts: the region holds one copy |
| Implementation | rlite `user/uipcps` as written (link-state routing, fully replicated directory, distributed address allocation); sizes from rlite's own `.proto` encodings | the FD&F region and the FrogNet tunnel daemon, unchanged |
| Obtained by | computation on the graphs | runs: every node in its own network namespace, real kernel routes, real WireGuard |
| Physical layouts | chain, binary tree, star (hub), partial mesh (ring plus chords, degree 4), full mesh | star through the hub only (a FrogNet region's physical layout) |
| Where | the model | sandbox (one machine, 2 CPUs) and **[physical: machines, sites, WAN legs]** |

Two things differ between the columns at once, the coordination mechanism and, for four of the five layouts, the
physical shape. The comparison is between deployments; it does not attribute every difference to one cause.

### rlite's policies, modelled

With N members and L adjacencies:

    transfers per change        = 2L − N + 1
    refresh messages per 30 s   = Σ over members v of  deg(v) · ⌈(deg(v) + 2) / 10⌉
    time to the farthest member = Σ over the hops on its path of (link delay + processing at the member)

At 200 subnets, from the smallest to the largest value across E = 1 to 100:

| Physical layout | Worst-case member hops | Transfers per change | Refresh per 30 s | Objects moved at cold start | State held across all members |
| --- | --- | --- | --- | --- | --- |
| chain | 2 to 101 | 199 | 1.26 MB down to 169 KB | 159 k | 4.4 MB |
| binary tree | 2 to 14 | 199 | 1.26 MB down to 171 KB | 159 k | 4.4 MB |
| star (hub) | 2 to 4 | 200 | 1.27 MB down to 214 KB | 160 k | 4.4 MB |
| partial mesh | 2 to 27 | 199 to 401 | 1.26 MB down to 250 KB | 159 k to 300 k | 4.4 to 5.9 MB |
| full mesh | 2 to 3 | 199 to 9,901 | 1.26 MB up to 31.6 MB | 159 k to 55 M | 4.4 to 62 MB |

Every term is a property of the physical graph, and the refresh is paid for as long as the network exists. These are
rlite's policy choices, not the only ones RINA allows: where the RIB lives is itself policy.

### The shared region, measured

A change is two logical operations, a write to the region and a wake of each reader; each crosses whatever links lie
between that member and the region host. No member relays another's facts, and at rest nothing is sent.

    time to the farthest reader = leg(writer) + max over readers of leg(reader) + processing

| E | Resolved (every route right, every name) | Cold start to last node correct | Change to last daemon (p50) | One daemon's CPU per change (p50) | All daemons' CPU per change (p50) | Region state (one copy) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | yes | 13.9 s | 211 ms | 275 ms | 275 ms | 181 KB |
| 2 | yes | 14.8 s | 276 ms | 185 ms | 375 ms | 181 KB |
| 5 | yes | 21.3 s | 348 ms | 140 ms | 680 ms | 182 KB |
| 10 | yes | 36.7 s | 417 ms | 85 ms | 935 ms | 184 KB |
| 20 | yes | 73.0 s | 697 ms | 85 ms | 1,725 ms | 189 KB |
| 50 | yes | 219 s | 1,532 ms | **[physical]** | **[physical]** | 203 KB |
| 100 | **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** |

Sandbox numbers, one machine with 2 CPUs running every node. As E grows, one daemon's CPU per change falls from 275 to
85 ms while the elapsed time to the last daemon rises from 211 to 697 ms; the CPU of all daemons together reaches
1,725 ms per change at E = 20, about 860 ms on two CPUs. That is consistent with the rise being the daemons queuing for
the machine's two CPUs; the physical runs, one node per machine, measure it directly. The region's mapping table is
16.5 KB at every E; the participant rows grow by each egress point's own rows. CPU figures come from a second run of the
same build on the same bed, which measured them.

On your machines:

| E | Machines / sites | Cold start to last node correct | Change to last daemon (p50) | One daemon's CPU per change (p50) | Region state |
| --- | --- | --- | --- | --- | --- |
| **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** |

![rlite's policies, modelled, across five physical layouts, against the shared region, at 200 subnets](S-discovery-vs-memory-S200.png)

### What the comparison shows

Under rlite's policies the cost of knowing the network follows its physical shape: hops, transfers, refresh and the
state every member carries all change with it. In the shared region, what an algorithm reads is the same set of facts
whatever the shape; only the routes computed from it reflect the physical network. An algorithm written against that
state does not have to change with the topology.


## Appendix C: limits, how to reproduce it, sources

- The passage is quoted from the draft of 2026-10-13; it does not say which way data uses the tree, so finding 2 is
  stated for data flowing from the requester outward.
- rlite's side is computed from its policies as written, not run.
- The chart's delay panel assumes 10 ms per WAN link, 0.1 ms per LAN link and 1 ms of processing per member that
  handles a change, for both columns.
- The shared region was measured on the hub layout only; its state does not depend on the layout by construction, but
  other layouts were not run.
- The sandbox's elapsed times include contention for 2 CPUs. E = 100 did not run in the sandbox: the machine ran out of
  memory.

Shared region, on any x86-64 Linux machine as root:

    cd / && tar xvzf - < /tmp/scale-test.tgz
    /opt/fdf/experiments/scale/scale-series

The tree experiments and the rlite model: `e2_compare.py`, `e2_ref.py`, `s_day_model.py` and `s_plot.py` in
`experiments/day-claims/`.

Sources:

- [rlite](https://github.com/rlite/rlite): `user/uipcps/uipcp-normal-lower-flows.cpp`, `uipcp-normal-enroll.cpp`,
  `uipcp-normal.cpp`, `uipcp-gpb/BaseRIB.proto`, `user/libs/CDAP.proto`
- [FrogNet Living Network](https://github.com/FawcettJohnW/FrogNet-Living-Network), branch `daf-model`:
  `opt/fdf/src/fdf-source/rinab/experiments/day-claims/` (`EXPERIMENTS.md`, `RESULTS-E2-COMPARE.md`,
  `runs/e2-compare-20261023/`, `runs/scale-20261023/`)
