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

- **Steady-state cost: FrogNet loses unless the host is well placed.** FrogNet doesn't build a distribution tree. Every
  member's read brings its own copy from the host, so shared links carry several copies. Host at the source: near-optimal
  in 28% (symmetric) and 28% (asymmetric) of graphs, against Day R1's 88% and 74%. Host at the best node: 61% and 59%,
  better than Day on asymmetric links in the worst case (2.7× against 6×). Host at the worst node: up to 39–54×. **Where
  the RAM host sits decides FrogNet's distribution cost**, which is E3's question (the group's center), made concrete.
- **Delay:** with the host at the source, every member gets its copy at shortest-path delay (stretch 1.0). With the host
  elsewhere, the detour through it can be long (worst stretch 21× at the cost-best host). Cost and delay pull the host
  in different directions.
- **Duplicates: none for FrogNet in any graph.** Each member's read brings exactly one copy. Day R2 delivers duplicates
  where it isn't a tree.
- **Reachability under one-way links: FrogNet reached every member (42 of 42); Day's tree 12 of 42.**
- **Construction (model, hop-messages, p50):** Day builds with a request out and a response back per member (12 on
  symmetric graphs). FrogNet builds with each member's subscription to the host and its answer (12 with the host at the
  source, 8–10 at the best host). The requester's tree and the exact tree need no messages; they are computed from the
  shared state.

## What this tells Day

1. Converging paths form a tree **if and only if forwarding is destination-based with one deterministic next hop**.
2. That tree reaches every member **only if every link the responses use can carry data the other way**.
3. Near-optimality is **not a property of the construction**. On symmetric links it is often within 10% (88%), but
   shared trunks make it unboundedly worse. Asymmetric costs make it 4.5–6× worse even for one member. "Very close"
   needs both conditions stated.
4. The tree's quality is a matter of **policy**: from the same shared state, an implementation can compute the
   requester's tree (never worse than 1.9× here), the exact tree, or place a RAM host as the Steiner point.

## Not done yet

- **Recovery cost:** link and node failures, with the rebuild counted separately from steady state. (The shared-RAM
  rebuild was measured on hardware: a member departure moved its nodes in 8.8 s.)
- **Capacities:** a link capacity in copies, and a FrogNet host whose fan-out exceeds it.
- **Construction cost** above is a hop-message model, not a measurement of Day's messages.
