# v0.27 negative Map-Reply — GREEN

- Started from verified v0.25 multi-record green; v0.26 preserved the required red test.
- RFC 9301 section 8.3 used as the authoritative negative-reply contract.
- Existing v0.25 IPv4 no-match negative reply was recognized rather than replaced blindly.
- Generalized negative Map-Reply encoding to IPv4 and IPv6.
- No configured site match: zero locators, Natively-Forward, authoritative bit, 15-minute TTL.
- Configured authoritative site with no active registration: zero locators, Natively-Forward, authoritative bit, 1-minute TTL, configured site prefix returned.
- Authority lookup uses the existing held site-policy view; no packet-path RAM read was introduced.
- Positive Map-Reply encoder bodies are unchanged from v0.25. Existing Dino-derived byte-regression artifact remains applicable.
- Local conformance: 42 PASS, 1 explicit SKIP.
- Clean FNW1 conformance: 42 PASS, 1 explicit SKIP.
- Independent-process held resolver/site, DDT, and map-cache tests PASS on a freshly restarted FNW1 server.
- Convergence tests wait for observed held truth. Two pre-existing weak site cleanup waits and the held-resolver republish wait were strengthened; no writer was made synchronous and no convergence sleep was added.
- `database_mapping` retained unchanged.
- Zero `Memory::remove()` calls remain in ribbit_lisp.cpp.
- Policy-denied/auth-failure negative actions remain unclaimed until a real ingress contract exposes those conditions.
