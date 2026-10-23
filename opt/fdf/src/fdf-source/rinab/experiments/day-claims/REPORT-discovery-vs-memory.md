# Discovery versus Memory

*2026-10-23 · John Fawcett*

Your method learns the network by discovery; FD&F learns it from memory. Discovery's cost changes with every physical
topology and never stops. With memory, the state an algorithm reads is the same whatever the topology, and only the
routes reflect the physical network, so the algorithm itself can be topology-free.

Fields marked **[physical: …]** are filled from the hardware runs.

![Discovery under your method across five physical topologies, against memory, at 200 subnets](S-discovery-vs-memory-S200.png)

## What was run

One network of 200 subnets, its egress level given 1, 2, 5, 10, 20, 50 and 100 egress points, and five physical shapes
for that level. Every other subnet sits on an egress point's LAN.

| | Your method | Memory (FD&F) |
| --- | --- | --- |
| How the network is learned | discovery: enrollment, flooding of link state and names, periodic refresh | each member writes its own facts once into a shared region; each reader takes what changed |
| Implementation | rlite's link-state routing, fully replicated directory and distributed address allocation, as written | the FD&F region and the FrogNet tunnel daemon, unchanged |
| How it was obtained | computed on the same graphs from rlite's policies; message sizes are rlite's own protobuf encodings | run: every node in its own network namespace, real kernel routes, real WireGuard |
| Physical shapes | chain, binary tree, star (hub), partial mesh (ring plus chords, degree 4), full mesh | star through the hub (a FrogNet region's physical layout) |
| Where | the model | sandbox (one machine, 2 CPUs) and **[physical: machines, sites, WAN legs]** |

A run counts only when every route on every node is on its expected next hop and every name resolves, checked by real
packets.

## Discovery under your method

Every term of discovery's cost is a property of the physical graph. With N members and L N-1 links, rlite's policies
give:

    transfers per change        = 2L − N + 1
    refresh messages per 30 s   = Σ over members v of  deg(v) · ⌈(deg(v) + 2) / 10⌉
    time to the farthest member = Σ over the hops on its path of (link delay + processing at the member)

The first follows from each member forwarding a new object to every neighbour except the sender. The second is each
member re-sending its own objects to every neighbour every 30 s, at most 10 objects per CDAP message. The third is the
delay of each link the flood crosses plus the processing at each member that relays it.

At 200 subnets, from the smallest to the largest value across E = 1 to 100:

| Physical shape | Worst-case member hops | Transfers per change | Refresh per 30 s | Objects moved at cold start | State held by the network |
| --- | --- | --- | --- | --- | --- |
| chain | 2 to 101 | 199 | 1.26 MB down to 169 KB | 159 k | 4.4 MB |
| binary tree | 2 to 14 | 199 | 1.26 MB down to 171 KB | 159 k | 4.4 MB |
| star (hub) | 2 to 4 | 200 | 1.27 MB down to 214 KB | 160 k | 4.4 MB |
| partial mesh | 2 to 27 | 199 to 401 | 1.26 MB down to 250 KB | 159 k to 300 k | 4.4 to 5.9 MB |
| full mesh | 2 to 3 | 199 to 9,901 | 1.26 MB up to 31.6 MB | 159 k to 55 M | 4.4 to 62 MB |

The refresh is paid every 30 s for as long as the network exists. State held by the network is every member's full copy
of the link-state database, directory and address table.

## Memory

Under memory a change is one write to the region and one wake to each reader: two steps, whatever the graph between
them. Time to the farthest reader is the writer's leg to the RAM host plus that reader's leg, plus its processing. No
member relays it, and nothing moves at rest.

    time to the farthest reader = leg(writer) + max over readers of leg(reader) + processing

| E | Resolved (every route right, every name) | Cold start to last node correct | Change to last daemon (p50) | Daemon CPU per change (p50) | Region state (one copy) |
| --- | --- | --- | --- | --- | --- |
| 1 | yes | 13.9 s | 211 ms | 275 ms | 181 KB |
| 2 | yes | 14.8 s | 276 ms | 185 ms | 181 KB |
| 5 | yes | 21.3 s | 348 ms | 140 ms | 182 KB |
| 10 | yes | 36.7 s | 417 ms | 85 ms | 184 KB |
| 20 | yes | 73.0 s | 697 ms | 85 ms | 189 KB |
| 50 | yes | 219 s | 1,532 ms | **[physical]** | 203 KB |
| 100 | **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** |

These are sandbox numbers: one machine with 2 CPUs runs every node, so every daemon a change wakes competes for those
two CPUs, and the elapsed times grow with E from that alone. The region's mapping table is 16.5 KB at every E; the
participant rows grow only by each egress point's own rows. Daemon CPU comes from a second run of the same build on the same bed, which measured it.

On your machines:

| E | Machines / sites | Cold start to last node correct | Change to last daemon (p50) | Daemon CPU per change (p50) | Region state |
| --- | --- | --- | --- | --- | --- |
| **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** | **[physical]** |

## Your three propositions on the converging tree

From the same work, on small graphs with an exact optimum (Dreyfus–Wagner, cross-checked by exhaustive search):

1. **Converging paths form a tree** only with one deterministic next hop per node. With a per-flow choice among equal
   paths, 16 of 1,500 graphs were not trees, and members received two copies.
2. **The tree reaches every member** only if every link a response used can carry data back. With about 30% of links
   one-way, it missed members in 30 of 42 graphs; resolving forward from state reached all 42.
3. **Near-optimal is not an invariant.** On symmetric links it was within 10% in 88% of graphs. A shared trunk makes it
   as bad as you like, and asymmetric costs made it 6×.

Each condition comes from reversing the path a discovery message took. Memory reads the facts and resolves each
delivery forward, so it needs none of them.

## What this means for the algorithm

Discovery belongs to the topology: change the shape and every cost above changes with it, and part of that cost is paid
forever. Memory takes discovery out of the algorithm's way. Membership, location and the routes that resolve are state
every member can read, so an algorithm written against that state is written once, for every topology. What is left to
get right is the algorithm itself: what the tree should be, and where its root belongs.

## Limits, and how to reproduce it

- Your side is computed from rlite's policies as written, not run. The rlite source and the compiled message
  definitions are named below.
- The delay figures in the chart assume 10 ms per WAN link, 0.1 ms per LAN link and 1 ms of processing per member that
  handles a change, the same assumption for both sides.
- The memory side's sandbox times include CPU contention on a 2-CPU machine. The physical runs remove it.
- E = 100 did not run in the sandbox: the machine ran out of memory.

Memory side, on any x86-64 Linux machine as root:

    cd / && tar xvzf - < /tmp/scale-test.tgz
    /opt/fdf/experiments/scale/scale-series

Your side: `s_day_model.py` and `s_plot.py` in `experiments/day-claims/`, against rlite's `user/uipcps` and its own
`.proto` files.

Sources:

- [rlite](https://github.com/rlite/rlite), `user/uipcps/uipcp-normal-lower-flows.cpp`, `uipcp-normal-enroll.cpp`,
  `uipcp-normal.cpp`, `uipcp-gpb/BaseRIB.proto`, `user/libs/CDAP.proto`
- [FrogNet Living Network](https://github.com/FawcettJohnW/FrogNet-Living-Network), branch `daf-model`:
  `opt/fdf/src/fdf-source/rinab/experiments/day-claims/` (`EXPERIMENTS.md`, `RESULTS-E2-COMPARE.md`,
  `runs/scale-20261023/`)
