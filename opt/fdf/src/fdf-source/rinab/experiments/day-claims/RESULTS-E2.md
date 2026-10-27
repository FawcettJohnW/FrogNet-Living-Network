# E2 results: group distribution tree

All results **sim** (one machine, 2 cores, loopback). Definition, passage and contradicting result: `EXPERIMENTS.md` E2.
The passage is a paraphrase of Ch12 §2.8.1 (the .docx is not in this workspace); verbatim text must replace it before
any of this goes to Day.

## Part 1. The references (graph structure only, no shared RAM)

`e2_ref.py`. Optimum = minimum Steiner arborescence rooted at the requester, computed two independent ways:
Dreyfus-Wagner (written here) and exhaustive Edmonds over every Steiner-node set (networkx). **The two agree on all
1,200 instances and on two hand-worked cases** (`ref-selfcheck.txt`).

One reference fault found and fixed on the way: networkx 3.7 `minimum_spanning_arborescence` reported "no arborescence"
for graphs that have one (chain, 10 nodes, seed 12, asymmetric: 7→6→5→4→3 and 7→8→9 exist). The exhaustive method now
uses `maximum_branching` on BIG − cost and checks the result spans ([E2_EDMONDS_BY_MAX_BRANCHING_V1]).

Trees compared, data costed requester → members:
- **reverse-path (responses)**: each node's next hop toward the requester on its own shortest path; the members' paths
  merge where they meet. This is our reading of the tree Day says the Create Flow Responses form.
- **shortest-path (requester)**: the requester's shortest path to each member, what a requester reading the topology
  would build.

Evidence: `runs/e2-ref-20261022/instances.jsonl`, `report.md`.

| links | topology | n | tree | optimal | cost/opt p50 | p90 | max | worst member stretch | ties other way: p90 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| symmetric | ring | 150 | shortest-path | 95 | 1.000 | 1.320 | 1.889 | 1.000 | 1.320 | 1.889 |
| symmetric | ring | 150 | reverse-path | 95 | 1.000 | 1.320 | 1.889 | 1.000 | 1.320 | 1.889 |
| symmetric | branch | 150 | shortest-path | 103 | 1.000 | 1.250 | 1.647 | 1.000 | 1.217 | 1.800 |
| symmetric | branch | 150 | reverse-path | 103 | 1.000 | 1.250 | 1.647 | 1.000 | 1.217 | 1.800 |
| asymmetric | ring | 150 | shortest-path | 83 | 1.000 | 1.476 | 1.875 | 1.000 | 1.500 | 1.875 |
| asymmetric | ring | 150 | reverse-path | 68 | 1.050 | 1.750 | 3.182 | 3.667 | 1.800 | 3.182 |
| asymmetric | branch | 150 | shortest-path | 96 | 1.000 | 1.258 | 1.833 | 1.000 | 1.278 | 1.833 |
| asymmetric | branch | 150 | reverse-path | 48 | 1.143 | 1.833 | 6.000 | 14.000 | 1.800 | 3.750 |

Chains and stars: every tree is optimal in all 600 instances, symmetric and asymmetric. A tree-shaped graph has only one
subtree spanning the group, so they check correctness, not quality.

- Symmetric graphs: the reverse-path tree **is** the shortest-path tree, 600 of 600.
- Asymmetric graphs: reverse-path costs more than shortest-path in 103 of 600, less in 26.
- The tie rule changes the reverse-path cost in 55 of 1,200. The 6.0 maximum is partly a tie: root 2, member 3; the
  member's paths to the root tie at 8 (direct, or via node 1); taking node 1 makes the data run 2→1→3 at 3 + 9 = 12
  against the direct 2→3 at 2. With ties broken the other way the asymmetric-branch maximum is 3.75.

## Part 2. The participants, on shared RAM

`e2_tree.cpp`. Each node is a member on its own session and writes only its own measurement of its own out-links (under
`topo`); the group (requester, members) joins `grp`. Every participant holds a read on `topo` and, on every wake,
computes both trees from what it read. The driver changes one link's cost per step (by the session that owns it; on a
symmetric graph both ends write) and checks every participant against the reference for that step.

**r24, `runs/e2-20261022T234014Z`: 160 scenarios (20 per topology × symmetry), 960 steps (initial + 5 changes each);
every participant's reverse-path and shortest-path trees equal the reference at every step (960 of 960); 0 timeouts.**

| | p50 | p99 | max |
|---|---|---|---|
| convergence: change issued → every participant has computed the new reference tree | 5.46 ms | 21.66 ms | 32.16 ms |

By size (p50): 4 nodes 3.65 ms, 7 nodes 4.84 ms, 9 nodes 7.55 ms, 11 nodes 13.35 ms. Convergence time grows with the
number of participants: 11 nodes is 22 sessions computing on 2 cores. Treat this as a ceiling set by the sandbox, not a
property; it needs a multi-machine run.

Wire: 4,796 bytes per change, all participants' sessions together (two reads and a held read per participant per wake).

## What E2 found in the implementation (fixed)

**Finding 4: wrong answers and session failures from the response-reference bookkeeping (r23 → r24).** On r23, 37 of 960
steps never converged. Every participant's read of `topo` threw `frogram: pair_heuristic`, and the participants died
silently: the harness swallowed the exception. That silence is now a failed check. Traced with a diagnostic client
(`[DIAG-E2-DECODE]`, scratch build):
the two reads `resolve name=topo` and `resolve name=grp` share one response reference (their template has no location
fields). The client moves its reference on every RESP_SAME ([SAME_MOVES_REFERENCE_V1]); the server moved its own on only
one of its three SAME paths. After a SAME from a non-moving path, the next DIFF was encoded against one reference and
applied to the other. The result was either a delivered body with grp's name and topo's members, or, when the wrong
list had two entries, a thrown `pair_heuristic`. E1's wrong-member answers were the same defect through another path.
`[SAME_KEEPS_LAST_ANSWER_V1]` (r22) repaired that path's bookkeeping, not the rule.

Fix, `tools/apply_same_moves_reference.py` ([SAME_MOVES_BOTH_REFERENCES_V1]): every server SAME moves the server's
reference exactly as the client's SAME moves its own (same extraction calls), and a body the client could not hold is
never answered SAME. The RAW-cache path no longer changes the RAM-answer template before deciding SAME.

**Finding 5: a session's teardown freed one coalescing entry twice (r22 → r23).** At session end a held read's pending
call was finished by the sender ("connection gone") and by the reader's ring sweep; both removed and retired the same
coalescing entry. Seen as a segfault in `Session::Impl::finish`; AddressSanitizer: heap-use-after-free in `co_home`,
freed by the other finisher. Fix in `tools/apply_client_coalesce_lockless.py` ([CO_REMOVE_ONCE_V1]): the entry is
claimed by an atomic exchange; the loser has nothing to remove. ASan run clean after.

## Against Day's prediction

**Confirms, with a condition.** On symmetric links the tree the responses form is the shortest-path tree; it is optimal
in 63% of rings and 69% of branching graphs here, within 1.32 of the optimum at p90 and 1.89 at worst. Whether that
is "very close" is Day's call; we report the numbers.

**Condition (asymmetric links):** the responses follow each member's path *to* the requester, while data flows the
other way. With different costs per direction the reverse-path tree is worse than both the optimum (p90 1.83, worst
6.0, 3.75 without the tie) and the requester's own shortest-path tree, which costs less in 103 of 600 asymmetric
instances. A member's path can be stretched 14×. A requester that computes the tree from shared topology does not have
this problem.

**Property vs procedure:** these trees were derived by participants from shared state, not by exchanging Create Flow
messages. The result is about the tree, not his procedure.
