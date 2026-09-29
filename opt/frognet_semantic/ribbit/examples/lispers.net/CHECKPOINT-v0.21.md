# v0.21 held map-resolver — GREEN

- map_resolver.get no longer performs request-time RAM reads.
- map-resolver truth is held by a dedicated view; writes wake the held reader.
- map_resolver.delete publishes active:false current state instead of physical removal.
- FNW1 convergence uses map_resolver.wait; writer publication remains asynchronous.
- Three merge tests were corrected to wait for exact observed RLOC truth instead of relying on scheduling luck.
- Local conformance: 31 PASS, 1 explicit SKIP.
- Clean FNW1 conformance: 31 PASS, 1 explicit SKIP.
