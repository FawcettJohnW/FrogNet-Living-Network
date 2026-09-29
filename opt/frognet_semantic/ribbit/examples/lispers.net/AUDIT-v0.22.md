# Remaining RAM-read audit after v0.22

Consumer/request lookup paths now held:
- registration + registration governance resolution
- public site policy authorization
- DDT delegation LPM
- ITR map-cache LPM/list
- map-resolver configuration get

Semantic physical removal:
- none in ribbit_lisp.cpp; withdrawals/deletes publish inactive/current truth.

Remaining direct `ram->read` calls are classified as:
1. held-view bootstrap reads: required once per participant/surface;
2. held watcher reads with `after` watermarks: held reads, not polling;
3. `publish_known_length`: writer-side exact read used only to make monotonic manifest publication idempotent;
4. `registration.put`: writer-side inspection of the registration /N used to enforce source-authorized TTL-0, refresh, merge replacement, and prior-truth semantics.

No remaining direct RAM read is on a warmed resolution, authorization, DDT lookup, map-cache lookup/list, or map-resolver get path.
