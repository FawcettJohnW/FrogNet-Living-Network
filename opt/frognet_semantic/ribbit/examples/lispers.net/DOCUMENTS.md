# Every document in this package, and what it is

## Start here

| document | what it is |
|---|---|
| `README-EXPERIMENT.md` | the experiment in two pages: question, design, what was held equal, measurement, results, how to challenge it |
| `RIBBIT-LISP-REPORT.md` | the full account. **Section 5 outlines every change, component by component in the order the code is organized:** what lispers.net does there, what changed, and what it measured |
| `README.md` | installing, building and running the three roles, including the RAM host at your own address and port |

## The changes

| document | what it is |
|---|---|
| `RIBBIT-LISP-REPORT.md`, section 5 | every change, by component (the summary) |
| `CHECKPOINT-BOUNDARY-NEVER-WAITS.md` | the chronological log of every change from v0.53 to v0.63: the ruling that prompted it, what changed, how it was measured, the before and after |
| `CHECKPOINT-v0.21.md` ... `CHECKPOINT-v0.45.md` | the earlier history, one file per version; a `-red` file records the failing test that came first, its pair the fix |
| `CHECKPOINT-*.md` (named) | the earlier steps by subject: the semantic codec (S1-S3), FNW1 engines, instance references, the front (LISP-HANDLER), no fallbacks, RAM rows |
| `AUDIT-v0.18.md`, `AUDIT-v0.22.md` | audits of the code at those versions |
| `PORT-LOG.md`, `WORKLOG.md` | running logs of the semantic-engine port and of the work |
| `REBUILD.txt` | what changed in this issue, and whether anything must be rebuilt |

## The programming model and the platform

In the Ribbit tree, `../../docs/` (they describe the platform every example shares): `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`
(the rules Ribbit code follows), `RAM-HOST-LOCKING-DESIGN.md` (the memory: row-level locks and no other lock),
`TRANSPORT-ARCHITECTURE.md` (how participants reach a RAM host), and `SEMANTIC-ENGINE-PORT-PLAN.md`,
`S3-CHARACTERIZATION.md`, `S4-S5-SPEC.md`, `RAM-ANSWER-HANDLER-SPEC.md` (FNW1, the semantic wire, and its port to C++).
`CONTRACT.md`, here, is the Ribbit-LISP executable contract.

## Testing and results

| document | what it is |
|---|---|
| `ACCEPTANCE-TEST-PLAN.md` | the acceptance test's plan and the closure of each item |
| `REAL-HARDWARE-TESTS.md` | the two-host DDT, peering and lig tests, and how to run on hardware |
| `README-CONFORMANCE-SUITE.md` | the conformance suite the qualification stages run |
| `evidence/2026-09-28/` | the reported hardware run: acceptance, performance, timeline, chart, the matched audit, coverage and complexity, the v0.63 dispatcher dissection |
| `evidence/loopback-runs/` | full acceptance runs on one machine for v0.61, v0.62 and v0.63, and the run with coverage instrumentation |
| `FINDINGS-FOR-DINO.md` | the findings about lispers.net 0.643, each with its stimulus and log |

## Analysis

| document | what it is |
|---|---|
| `docs/analysis/LIZARD-COMPARISON.md` | lizard on both code bases, whole and by scope, before the matched audit |
| `docs/analysis/DISPATCH-DISSECTION-v0.60.md` | the v0.60 `Engine::dispatch()` taken apart branch by branch (v0.63's is in `evidence/2026-09-28/`) |
| `docs/analysis/SURFACE-lispers.md` | lispers.net's map-server/map-resolver surface: what the tests reach and what they do not |
| `docs/analysis/REGISTER-COST-v0.54.md` | one Map-Register taken apart at v0.54, before the register-path changes |

## Historical working notes (kept for the record; superseded by the documents above)

`RUN-ON-A-REAL-MACHINE.md` (v0.52), `OUTPUTS-INDEX.md` (2026-09-27), `NEXT.md`, `HANDOFF-S3.md`, and in `docs/history/`:
`step4-pickup-FINDINGS.md`, `lispers-compare-NOTES.md`, `read-your-write-STATUS.md`.
