# v0.30 database-mapping held-view contract — RED

Base: v0.29 lock-free held green.

Authoritative role: an ETR database mapping is its authoritative EID-to-RLOC mapping used for ETR control-plane behavior. This slice first establishes the held lookup contract before registration emission.

Added non-vacuous conformance requiring:
- database_mapping.get longest-prefix match;
- IID isolation;
- withdrawal followed by fallback to the less-specific mapping;
- database_mapping.wait convergence on held participant state.

RED proof: 43 tests run, one error at the first database_mapping.wait call because v0.29 exposes database mapping only as write/delete state and has no held consumer.
