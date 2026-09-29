# Complexity and coverage -- Ribbit-LISP v0.63 vs lispers.net 0.643, under the 78-test acceptance suite

How measured. Complexity: lizard, per function (the same rules and files as LIZARD-COMPARISON.md; Ribbit's 19 files are
ribbit_cpp/ without the two generated Unicode tables). Coverage: one full run of tools/acceptance.py (78 tests,
v0.63, loopback) with both systems instrumented -- Ribbit's RAM server and front built with gcov (their counts
merged: both compile the same sources), lispers.net under coverage.py with every lisp-* process recording. Each
lizard function is joined with the lines it spans: a function RAN if a line inside it executed (for Python, not
counting the def line, which executes when the module is imported); STATEMENTS RUN is executed / executable lines
inside functions; COMPLEXITY RUN weights each function by its CCN.

Scope differs and must be read with it:
- lispers.net whole is its entire implementation; this suite starts only its map-server / map-resolver processes, so
  its ITR, ETR, RTR, DDT-node and tools code cannot run. The "surface" row is the part the Ribbit Map-Server replaces
  (functions reachable from lispers.net's map-server/map-resolver entry points; a name-based call graph, approximate).
- Ribbit's 19 files include machinery that is not LISP (FNWP, the semantic codec, the frogram client, EBR, the
  memory) -- run by every operation, so covered heavily -- and API operations this ACCEPTANCE suite never calls
  (map-cache, DDT, map-resolver, ETR-registrar, and the test waits): those are exercised by the conformance
  suites (qualify: local, fnw1), not by acceptance, so their coverage here is not their coverage.

| code | functions | functions run | statements run | complexity run (CCN-weighted) |
|---|---:|---:|---:|---:|
| Ribbit-LISP v0.63 (19 files) | 576 | 401 (70%) | 65% | 80% |
| lispers.net 0.643, whole (26 files) | 1139 | 296 (26%) | 28% | 31% |
| lispers.net 0.643, map-server/map-resolver surface | 370 | 167 (45%) | 36% | 51% |

| code | complexity band | functions | functions run | statements run |
|---|---|---:|---:|---:|
| Ribbit-LISP v0.63 (19 files) | CCN 1-5 | 390 | 254 (65%) | 71% |
| Ribbit-LISP v0.63 (19 files) | CCN 6-15 | 145 | 111 (77%) | 69% |
| Ribbit-LISP v0.63 (19 files) | CCN 16-50 | 39 | 34 (87%) | 71% |
| Ribbit-LISP v0.63 (19 files) | CCN > 50 | 2 | 2 (100%) | 37% |
| lispers.net 0.643, whole (26 files) | CCN 1-5 | 788 | 178 (23%) | 29% |
| lispers.net 0.643, whole (26 files) | CCN 6-15 | 269 | 90 (33%) | 28% |
| lispers.net 0.643, whole (26 files) | CCN 16-50 | 70 | 24 (34%) | 27% |
| lispers.net 0.643, whole (26 files) | CCN > 50 | 12 | 4 (33%) | 34% |
| lispers.net 0.643, map-server/map-resolver surface | CCN 1-5 | 241 | 100 (41%) | 36% |
| lispers.net 0.643, map-server/map-resolver surface | CCN 6-15 | 102 | 54 (53%) | 34% |
| lispers.net 0.643, map-server/map-resolver surface | CCN 16-50 | 23 | 10 (43%) | 27% |
| lispers.net 0.643, map-server/map-resolver surface | CCN > 50 | 4 | 3 (75%) | 65% |

## Engine::dispatch() under acceptance

52 top-level branches (v0.63). 2 are one line (the condition and the call on the same line), so line coverage cannot
say whether they ran -- one of them is wire.request4/6, which every Map-Request in the suite uses. Of the other 50,
the acceptance suite entered 9: wire.register_notify, registration.put, resolution.get, site.add, site.delete,
ms.encryption_key, ms.policy, ms.named_locator, resolver.wait_site. The remaining 41 -- map-cache, database-mapping,
DDT, map-resolver configuration, the ETR registrar and liveness roles, and the test/diagnostic waits and stats -- are
the API's other surfaces, which the conformance suites exercise and the acceptance suite, by design, does not.

Two readings follow directly:
- dispatch()'s CCN 548 is mostly branches this acceptance suite does not execute: the 9-11 operations it drives are a
  minority of the 52 (DISPATCH-DISSECTION-v0.63.md classifies all of them). Some of the rest are deployed code (the
  ETR roles, the map-cache) that a Map-Server-only run does not reach; some are test surface; the conformance suites
  are where they are covered.
- At equal suite, Ribbit's code is exercised far more thoroughly than lispers.net's surface: 65% of statements in
  its functions against 36% on lispers.net's map-server/map-resolver surface.
