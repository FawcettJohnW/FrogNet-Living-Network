# v0.31 held database-mapping contract — GREEN

Base: v0.29 lock-free held green; v0.30 preserved the non-vacuous red test.

- `database_mapping` is no longer write-only.
- The ETR database-mapping surface now has held `get` and `wait` operations.
- RAM-backed database mappings are held as an immutable atomic snapshot per IID/group, with one watcher as the sole writer.
- `database_mapping.get` performs longest-prefix match, respects IID/group isolation, ignores inactive current-state truth, and falls back to a less-specific active mapping after withdrawal.
- `database_mapping.wait` waits on the participant's `database-mapping-applied|IID|GROUP` FrogNet Memory truth, not a private synchronization primitive.
- Warm database-mapping lookup performs zero direct RAM reads, independently verified across processes.
- Local conformance: 43 PASS / 1 explicit SKIP.
- Clean FNW1 conformance: 43 PASS / 1 explicit SKIP.
- Independent held resolver/site, DDT, map-cache, and database-mapping tests: PASS.
- Participant lock-free invariant preserved; `Memory::remove()` remains zero.

This establishes the database-mapping read/held-state contract. It does not yet claim ETR Map-Register emission or direct ETR Map-Reply ingress behavior; those can now consume an established authoritative local mapping truth rather than a write-only cell.
