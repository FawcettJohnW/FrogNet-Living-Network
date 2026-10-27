
### Standard graphs, symmetric links (chain, star, ring, branch; 4-11 nodes, 1-6 members) (600 graphs)

| construction | near-optimal (<=1.10; all reached) | cost/opt p50 | p90 | max | a tree | all members reached | duplicate copies | worst stretch p50 | max | build hop-msgs p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| Day, R1 (one next hop per node) | 531 (88%) | 1.000 | 1.136 | 1.889 | 600 | 600 | 0 | 1.000 | 1.000 | 12 |
| Day, R2 (per-member choice of equal paths) | 530 (88%) | 1.000 | 1.136 | 2.200 | 597 | 600 | 6 | 1.000 | 1.000 | 12 |
| requester's shortest-path tree (from shared state) | 531 (88%) | 1.000 | 1.136 | 1.889 | 600 | 600 | 0 | 1.000 | 1.000 | 0 |
| FrogNet, RAM host at the source | 167 (28%) | 1.455 | 2.280 | 4.306 | 0 | 600 | 0 | 1.000 | 1.000 | 12 |
| FrogNet, RAM host at the best node | 364 (61%) | 1.000 | 1.692 | 2.333 | 0 | 600 | 0 | 1.000 | 21.000 | 8 |
| FrogNet, RAM host at the worst node | 3 (0%) | 2.682 | 5.000 | 39.000 | 0 | 600 | 0 | 4.400 | 75.000 | 18 |
| exact optimum | 600 (100%) | 1.000 | 1.000 | 1.000 | 600 | 600 | 0 | 1.000 | 3.556 | 0 |

### Standard graphs, asymmetric links (600 graphs)

| construction | near-optimal (<=1.10; all reached) | cost/opt p50 | p90 | max | a tree | all members reached | duplicate copies | worst stretch p50 | max | build hop-msgs p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| Day, R1 (one next hop per node) | 446 (74%) | 1.000 | 1.500 | 6.000 | 600 | 600 | 0 | 1.000 | 14.000 | 12 |
| Day, R2 (per-member choice of equal paths) | 445 (74%) | 1.000 | 1.556 | 6.000 | 595 | 600 | 10 | 1.000 | 14.000 | 12 |
| requester's shortest-path tree (from shared state) | 507 (84%) | 1.000 | 1.235 | 1.875 | 600 | 600 | 0 | 1.000 | 1.000 | 0 |
| FrogNet, RAM host at the source | 165 (28%) | 1.500 | 2.444 | 4.160 | 0 | 600 | 0 | 1.000 | 1.000 | 12 |
| FrogNet, RAM host at the best node | 354 (59%) | 1.000 | 1.667 | 2.706 | 0 | 600 | 0 | 1.000 | 41.000 | 10 |
| FrogNet, RAM host at the worst node | 4 (1%) | 2.740 | 5.095 | 54.000 | 0 | 600 | 0 | 4.000 | 75.000 | 18 |
| exact optimum | 600 (100%) | 1.000 | 1.000 | 1.000 | 600 | 600 | 0 | 1.000 | 5.000 | 0 |

### Source position: 30 branching graphs (8 nodes), every node in turn the source (240 graphs)

| construction | near-optimal (<=1.10; all reached) | cost/opt p50 | p90 | max | a tree | all members reached | duplicate copies | worst stretch p50 | max | build hop-msgs p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| Day, R1 (one next hop per node) | 138 (58%) | 1.000 | 1.500 | 6.000 | 240 | 240 | 0 | 1.000 | 6.000 | 12 |
| Day, R2 (per-member choice of equal paths) | 136 (57%) | 1.000 | 1.562 | 6.000 | 237 | 240 | 8 | 1.000 | 6.000 | 12 |
| requester's shortest-path tree (from shared state) | 178 (74%) | 1.000 | 1.273 | 1.667 | 240 | 240 | 0 | 1.000 | 1.000 | 0 |
| FrogNet, RAM host at the source | 58 (24%) | 1.500 | 2.267 | 3.704 | 0 | 240 | 0 | 1.000 | 1.000 | 12 |
| FrogNet, RAM host at the best node | 112 (47%) | 1.111 | 1.379 | 2.167 | 0 | 240 | 0 | 1.000 | 19.000 | 10 |
| FrogNet, RAM host at the worst node | 0 (0%) | 3.241 | 5.273 | 26.000 | 0 | 240 | 0 | 5.000 | 47.000 | 20 |
| exact optimum | 240 (100%) | 1.000 | 1.000 | 1.000 | 240 | 240 | 0 | 1.000 | 3.667 | 0 |

### One-way links: branching graphs with ~30% of links one direction only (strongly connected) (42 graphs)

| construction | near-optimal (<=1.10; all reached) | cost/opt p50 | p90 | max | a tree | all members reached | duplicate copies | worst stretch p50 | max | build hop-msgs p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| Day, R1 (one next hop per node) | 4 (10%) | 1.174 | 1.625 | 2.000 | 42 | 12 | 0 | 1.000 | 8.000 | 19 |
| Day, R2 (per-member choice of equal paths) | 4 (10%) | 1.174 | 1.625 | 2.000 | 42 | 12 | 0 | 1.000 | 8.000 | 19 |
| requester's shortest-path tree (from shared state) | 30 (71%) | 1.000 | 1.293 | 1.500 | 42 | 42 | 0 | 1.000 | 1.000 | 0 |
| FrogNet, RAM host at the source | 4 (10%) | 1.667 | 2.286 | 3.278 | 0 | 42 | 0 | 1.000 | 1.000 | 18 |
| FrogNet, RAM host at the best node | 16 (38%) | 1.263 | 1.609 | 1.897 | 0 | 42 | 0 | 1.000 | 7.500 | 14 |
| FrogNet, RAM host at the worst node | 0 (0%) | 3.417 | 4.545 | 8.000 | 0 | 42 | 0 | 5.800 | 24.000 | 30 |
| exact optimum | 42 (100%) | 1.000 | 1.000 | 1.000 | 42 | 42 | 0 | 1.000 | 3.333 | 0 |

### Counterexample families: cost / optimum

| family | k | Day, R1 (one next hop per node) | Day, R2 (per-member choice of equal paths) | requester's shortest-path tree (from shared state) | FrogNet, RAM host at the source | FrogNet, RAM host at the best node | FrogNet, RAM host at the worst node | duplicates (Day R2) |
|---|---|---|---|---|---|---|---|---|
| trunk | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.100 | 0 |
| trunk | 2 | 1.538 | 1.538 | 1.538 | 1.538 | 1.000 | 1.538 | 0 |
| trunk | 3 | 2.000 | 2.000 | 2.000 | 2.000 | 1.000 | 2.000 | 0 |
| trunk | 4 | 2.353 | 2.353 | 2.353 | 2.353 | 1.000 | 2.353 | 0 |
| trunk | 5 | 2.632 | 2.632 | 2.632 | 2.632 | 1.000 | 2.632 | 0 |
| trunk | 6 | 2.857 | 2.857 | 2.857 | 2.857 | 1.000 | 2.857 | 0 |
| trunk | 7 | 3.043 | 3.043 | 3.043 | 3.043 | 1.000 | 3.043 | 0 |
| trunk | 8 | 3.200 | 3.200 | 3.200 | 3.200 | 1.000 | 3.200 | 0 |
| asym | 1 | 4.500 | 4.500 | 1.000 | 1.000 | 1.000 | 2.500 | 0 |
| asym | 2 | 4.500 | 4.500 | 1.000 | 1.000 | 1.000 | 1.875 | 0 |
| asym | 3 | 4.500 | 4.500 | 1.000 | 1.000 | 1.000 | 1.667 | 0 |
| asym | 4 | 4.500 | 4.500 | 1.000 | 1.000 | 1.000 | 1.750 | 0 |
| ties | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| ties | 2 | 1.000 | 2.000 | 1.000 | 1.750 | 1.000 | 1.750 | 2 |
| ties | 3 | 1.000 | 2.000 | 1.000 | 2.333 | 1.000 | 2.333 | 3 |
| ties | 4 | 1.000 | 2.000 | 1.000 | 2.800 | 1.000 | 2.800 | 4 |
| ties | 5 | 1.000 | 2.000 | 1.000 | 3.182 | 1.000 | 3.182 | 5 |
| ties | 6 | 1.000 | 2.000 | 1.000 | 3.500 | 1.000 | 3.500 | 6 |

Proposition 1 (converging paths form a tree): R1 non-trees 0 of 1482; R2 non-trees 16 of 1500.
Proposition 2 (the tree reaches every member): one-way graphs where Day R1 misses a member: 30 of 42; the requester's tree: 0 of 42.
Proposition 3 (near-optimal, <=1.10) for Day R1 on symmetric standard graphs: 531 of 600; worst 1.889.
Proposition 3 (near-optimal, <=1.10) for Day R1 on asymmetric standard graphs: 446 of 600; worst 6.000.
