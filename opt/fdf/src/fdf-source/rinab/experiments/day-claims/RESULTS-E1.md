# E1 results: whatevercast selection

All results **sim**: one machine (2 cores, Linux 6.18), loopback, the real `rina-host` and real clients. No physical-network
result yet. Definition, passage, prediction and contradicting results: `EXPERIMENTS.md` E1.

## Runs

| Build | Run | Result |
|---|---|---|
| `fdf-2026-10-16-r21` (`daf-model` 3e836fb, rina-host 564e7948fc7c6ac5) | `runs/e1-20261022T230712Z` (12 events), `e1-20261022T231330Z`, `e1-20261022T231340Z` | **FAIL**, deterministic: 9 of 11 checks, seed 1 |
| r21 + fix 1 (rina-host acf270ced01c4b58) | `runs/e1-20261022T231529Z` (12 events) | 10 of 11: wrong selections gone, designation wake still fails |
| `fdf-2026-10-22-r22` (fixes 1 and 2, rina-host 6d2fdb7cfe0e93fb) | `runs/e1-20261022T231722Z`, `e1-20261022T231846Z`, `e1-20261022T232026Z` (seeds 1-3, 300 events each) | **PASS**, 11 of 11 on every seed |

Regression on r22: `tools/daf-check.sh` all suites pass, both phases (kill -9 and restart between them);
`tools/hospital-demo.sh 3` passes 3 of 3.

## What E1 found

### Finding 1: the property holds; the transport gave a wrong answer (fixed)

**Question.** Does one mechanism, a set plus a rule applied at resolution, select correctly under all, one, nearest
(geographic and network), designated and capability rules while membership and capabilities change?

**Result on r21.** After event 8 of seed 1, a long-lived session resolving `svc` with rule `one` ranked on capacity got
`m00` at rev 537; the oracle, and a fresh session asking the same request at the same rev, got `m08`. Wrong in 5 of 12
events, then every event after.

**Cause (the transport, not the region).** Build-time patch `tools/apply_same_as_last.py` ([SAME_AS_LAST_ANSWER_V1])
answers SAME when an answer equals the last answer at that request location, and does not move the response reference,
stating that the reference "is already those fields". The older request-hash SAME path moves the reference to its own
answer and did not move the last answer. Two different requests at one location (rule `one` ranked on capacity, and
rule `one` ranked on cpu: same shape, same DAF and name) then desynchronised the two ends:

| ev | request | server reply | server reference | last answer | client reference |
|---|---|---|---|---|---|
| 6 | capacity → m08 | DIFF | m08 | m08 | m08 |
| 6 | cpu → m00 | DIFF | m00 | m00 | m00 |
| 7 | capacity, unchanged | SAME (request hash) | m08 | **m00** | m08 |
| 7 | cpu, unchanged | SAME (same as last) | **m08** | m00 | m00 |
| 8 | capacity → m08, rev 537 | DIFF vs m08: only rev | m08 | m08 | client applies to m00 → **m00, rev 537** |

Read from the raw call trace (`runs/e1-20261022T231340Z/raw-1.jsonl`, `E1_TRACE_CALLS=1`).

**Fix** ([SAME_KEEPS_LAST_ANSWER_V1], in the same patch file): the request-hash SAME records its answer as the last
answer. **Superseded in r24** by [SAME_MOVES_BOTH_REFERENCES_V1] (RESULTS-E2.md, finding 4): this was one path of a
general defect; every server SAME now moves the reference as the client's does. E1 re-run on r24: 3 seeds, 11 of 11. Only the FD&F RINA host builds this patch; `ribbit/cpp`, the Redis example and the v0.63 platform do not contain
[SAME_AS_LAST_ANSWER_V1].

**Not yet done.** `repro_stale_diff` variant f, a hand-built sequence meant to drive the same steps, does not reproduce
it on r21. The E1 trace above does, deterministically. Why variant f misses is not explained.

### Finding 2: a designation change did not wake a held read on the name (fixed)

`designate` changes what a `designated` name resolves to, but the name's version (`name_rev`) and the variables a held
read parks on covered only the name's entries and its members' enrollment. Resolution followed the designation at once;
the version stood still (297 → 297) and a held read never woke. The name's rule (`n|D`) had the same gap.

**Fix** ([NAME_REV_COVERS_ANSWER_V1], `src/daf_region.cpp`): one list, `name_vars`, gives every variable a name's answer
is computed from (entries, enrollment, designation, rule); `name_rev` folds the same four; held reads on a name, on a
subscription and on a script's `on_change` park on that list.

### Finding 3: session setup waits 50 ms about one time in six (not fixed; not E1's property)

A member's session is three sockets. When the host accepts RETURN or DATA before the request socket's HELLO has
registered, `ram_server.cpp` (v0.63 platform, lines 635-643 of the built copy) sleeps 50 ms under a mutex and looks again.
Measured: p50 1.0-1.1 ms, max 51-52 ms, 16 of 92 arrivals over 10 ms across three seeds. It is in the platform every
example builds from, so it is reported, not patched here.

## Measurements on r22 (sim)

| | seed 1 | seed 2 | seed 3 |
|---|---|---|---|
| wrong / stale selections (300 changes × 7 rules) | 0 | 0 | 0 |
| missed wakes | 0 | 0 | 0 |
| lost operations (concurrent writers, 3 s) | 0 | 0 | 0 |
| rule violations (concurrent readers) | 0 | 0 | 0 |
| resolve p50 / p99 | 0.325 / 0.722 ms | 0.310 / 0.759 ms | 0.310 / 0.713 ms |
| bytes per resolve out / in | 75 / 137 | 75 / 147 | 75 / 122 |
| change → wake p50 / p99 | 0.31 / 2.03 ms | 0.29 / 2.50 ms | 0.32 / 2.47 ms |
| killed member leaves the name, p50 | 1.9 ms | 2.0 ms | 2.1 ms |

Change → wake runs from the moment the change is issued (a revive: from its session being up). The earlier figures in
`runs/e1-20261022T231722Z` measured from the writer's reply and came out negative; they are superseded.

## Against Day's prediction

**Confirms**, on this implementation: all, one, designated, nearest and capability-based selection are one mechanism
(a set of member-written facts plus a rule applied at resolution), and every answer matched the rule applied to the set
after the last completed change. Network-nearest needed nothing in the region: the asking participant computed it from
the adjacency facts the members wrote.

**Condition**: geographic `nearest` is built into the region; network-nearest is the asker's computation. Day's
"nearest" is not specified as either; both are tested.

**Caveat for the report to Day**: the Ch12 passages in `EXPERIMENTS.md` are paraphrases; the .docx is not in this
workspace. Verbatim text must replace them before this goes to him.
