# E2 compared: Day's construction, FrogNet's, and the exact optimum

`e2_compare.py` / `e2_compare_report.py`; evidence `runs/e2-compare-20261023/` (compare.jsonl, report.md). Graph
analysis on the same inputs for every construction: members, source, directed link costs, topology snapshot. Not a
network measurement; the hardware run of the tree derivation itself is `runs/e2-phys-20261023T005453Z` (960 of 960 on
four machines, Seattle and New York). The Ch12 passage is still a paraphrase; the .docx is needed for the verbatim text.

## The constructions

| | what it is |
|---|---|
| Day, R1 | The converging Create Flow Responses: each member's response follows its shortest path to the requester; routing assumption R1, every node has ONE next hop toward the requester (equal costs broken the same way everywhere). Data flows back over the reverse links. |
| Day, R2 | The same, under R2: each member's response chooses independently among equal-cost next hops (per-flow / ECMP-like). |
| Requester's tree | A tree computed from the shared topology by the requester: its own shortest path to each member. A policy available to any shared-state implementation. |
| FrogNet | Shared RAM as it actually moves a message: the source writes it once into the RAM host; each member's held read brings one copy from the host. A shared link carries one copy per member whose read crosses it. Host at the source, at the best node, at the worst node. |
| Exact | The minimum Steiner arborescence rooted at the source (Dreyfus-Wagner; exhaustive Edmonds agrees on every instance). |

Cost = sum over links of link cost × copies of one message crossing it. **Near-optimal = cost ≤ 1.10 × exact.** A cost
ratio is counted only when every member is reached (a partial delivery costs less and is not a comparison).

## Proposition 1: converging paths form a tree

- **R1: always a tree.** 0 non-trees in 1,482 graphs. This holds by construction: each node has one next hop toward the
  requester, so every node has one parent in the reverse direction.
- **R2: not always.** 16 of 1,500 graphs are not trees, and members receive duplicate copies. Counterexample family
  `ties`: the requester reaches z through x or y at equal cost and k members hang off z. Members choosing independently
  make z receive the message from both x and y, so **every member receives 2 copies and the cost doubles (2.000×) for
  every k ≥ 2**.

**Condition needed:** destination-based forwarding with one deterministic next hop per node. Per-flow choice among
equal-cost paths breaks it.

## Proposition 2: that tree reaches every intended member

- With every link usable in both directions, every construction reached every member in every graph.
- **With one-way links it fails.** In 42 strongly connected branching graphs with about 30% of links one-way, Day's
  tree **missed at least one member in 30 of 42**: a response can travel a link that data cannot come back over. The
  requester's tree and FrogNet reached every member in all 42.

**Condition needed:** every link a response uses must be usable in the reverse direction.

## Proposition 3: that tree is near-optimal

| graphs | Day R1 near-optimal | Day R1 cost/opt p50 / p90 / worst | requester's tree p90 / worst | FrogNet best host p90 / worst | FrogNet host at source p90 / worst |
|---|---|---|---|---|---|
| symmetric, 600 | 531 (88%) | 1.000 / 1.136 / 1.889 | 1.136 / 1.889 | 1.692 / 2.333 | 2.280 / 4.306 |
| asymmetric, 600 | 446 (74%) | 1.000 / 1.500 / **6.000** | 1.235 / 1.875 | 1.667 / 2.706 | 2.444 / 4.160 |
| every source position, 240 | 138 (58%) | 1.000 / 1.500 / 6.000 | 1.273 / 1.667 | 1.379 / 2.167 | 2.267 / 3.704 |

**It does not follow from the structure.** Two counterexample families:

- **`trunk` (cheap shared trunk vs individually shortest paths), symmetric links.** The ratio grows without bound in k:
  1.54 at k=2, 2.00 at k=3, 2.63 at k=5, 3.20 at k=8, tending to direct-cost / branch-cost (5.0 here). Day's tree,
  the requester's tree and FrogNet with the host at the source are all equally far off; **FrogNet with its host on the
  trunk node is exactly optimal at every k**, because the host is the Steiner point.
- **`asym` (responses take the way that is cheap toward the requester, data pays the other direction).** Day's tree is
  4.5× optimal at every k. The requester's tree and FrogNet are optimal.

**Condition needed:** symmetric costs and few members sharing paths. Even then the bound is about 1.9× worst here, not
"very close" in general. Asymmetry alone makes it 6×.

## Day's against FrogNet's

**What FrogNet claims, and only that:** traffic takes the graph's best route that actually resolves. That holds in
every graph: each member's copy travelled the shortest path from the RAM host (stretch 1.0 from the host, every graph),
FrogNet delivered **no duplicates in any graph**, and it reached every member wherever a path existed, including **42 of
42 one-way graphs, where Day's tree reached all members in only 12**.

**What FrogNet does not claim:** an optimal distribution tree, or an optimal place for the RAM host. FrogNet builds no
tree (every member's held read brings its own copy from the host), and where the host sits is the network
administrator's decision, or a capability election at each level. Optimal placement in a dynamic network isn't
achievable unless each level elects its host on capabilities that say where it should be. The cost of one message by
host position is therefore **placement guidance, not a verdict**:

| host position | near-optimal cost (<=1.10), symmetric / asymmetric | cost/opt p90, symmetric / asymmetric | delay |
|---|---|---|---|
| at the source | 28% / 28% | 2.280 / 2.444 | shortest-path to every member (stretch 1.0) |
| at the best node for cost | 61% / 59% | 1.692 / 1.667 | detour through the host (worst stretch 21x / 41x) |
| at the worst node | 0% / 1% | 5.000 / 5.095 | worst stretch 75x |

- **Cost and delay pull the host in opposite directions.** At the source: shortest delay, more copies on shared links.
  Where the members' paths join (the Steiner point): least cost. In the `trunk` family a host on the trunk node is
  exactly optimal at every k, where every tree built from shortest paths (Day's included) is 1.5 to 3.2x.
- **Construction (model, hop-messages, p50):** FrogNet: each member's subscription to the host and its answer
  (12 at the source, 8-10 at the cost-best host); Day: a request out and a response back per member (12).

E3 changes accordingly: not "find the optimal center" but "what a capability-based election per level yields, against
the bounds above, as members and demand move".

## What this tells Day

1. Converging paths form a tree **if and only if forwarding is destination-based with one deterministic next hop**.
2. That tree reaches every member **only if every link the responses use can carry data the other way**.
3. Near-optimality is **not a property of the construction**. On symmetric links it is often within 10% (88%), but
   shared trunks make it unboundedly worse. Asymmetric costs make it 4.5–6× worse even for one member. "Very close"
   needs both conditions stated.
4. The tree's quality is a matter of **policy**: from the same shared state, an implementation can compute the
   requester's tree (never worse than 1.9x here) or the exact tree. FrogNet makes no optimality claim beyond routing on
   the best resolving path; its distribution cost follows where the RAM host is placed, which is the administrator's
   (or a per-level capability election's) choice.

## Not done yet

- **Recovery cost:** link and node failures, with the rebuild counted separately from steady state. (The shared-RAM
  rebuild was measured on hardware: a member departure moved its nodes in 8.8 s.)
- **Capacities:** a link capacity in copies, and a FrogNet host whose fan-out exceeds it.
- **Construction cost** above is a hop-message model, not a measurement of Day's messages.
