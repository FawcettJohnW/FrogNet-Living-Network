# v0.25 multi-record Map-Register — GREEN

- Extends v0.24 core green with two-record IPv6 coverage and multi-record TTL-0 partial-source-authorization atomic rejection.
- IPv4 and IPv6 Map-Register parse every Record Count record before state mutation and reject malformed/trailing packets.
- Every record is semantically validated before any record is applied.
- Original packet authentication is verified once; all authenticated records must resolve to the same packet key.
- Mapping records retain independent TTL, prefix length, locator set, refresh validation, withdrawal authorization, and existing governance behavior.
- RFC 9301 Map-Notify contract used: one Notify copies all records from the accepted Register and recomputes per-message authentication.
- Existing merge/non-merge conformance remains green.
- `database_mapping` retained unchanged; its contract remains open as instructed.
- Negative Map-Reply contract remains a separate next task; no new negative wire format was synthesized.
- Zero `Memory::remove` calls remain in ribbit_lisp.cpp.
- Saved Dino-derived single-record Map-Register and Map-Reply fixtures remain byte-for-byte identical.
- Local conformance: 39 PASS, 1 explicit SKIP.
- Clean FNW1 conformance: 39 PASS, 1 explicit SKIP.
- Independent-process held resolver/site, DDT, and map-cache tests PASS.
- No convergence sleeps were added; held-state tests wait for observed state. The prior fixed expiry sleep was removed.
- package_check PASS; no Python bytecode packaged.
