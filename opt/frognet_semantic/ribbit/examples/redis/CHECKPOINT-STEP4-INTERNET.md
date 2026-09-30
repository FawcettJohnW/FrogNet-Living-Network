# Redis across the Internet -- step 4 (2026-09-29), in progress
## Done, tested (RED on step 3 where it applies, GREEN now)
- [REGION_HELD_AUTH_V1] tests/run_trust.py: step 3 accepted a forged call claiming authed:true (+OK); now every call
  presents the id and random token the region issued, and authed/user come only from the region's record. T1-T5 GREEN.
- [REGION_CONFIG_IS_THE_HOSTS_V1] a host's Redis config comes from RIBBIT_REDIS_CONFIG, then CONFIG SET after AUTH;
  the config operation requires RIBBIT_REDIS_ADMIN (step 3 took {"requirepass":""} from anyone).
- [FRONT_RECONNECTS_V1] tests/run_reconnect.py: step 3's front stayed dead after the host came back (R4/R5 RED);
  now in-flight calls fail loudly and the next call opens a new session. R1-R5 GREEN.
- tools/latency_relay.py: a faithful one-way delay (shaped_relay.py sleeps per chunk: frames waited for each other,
  which looked like 2 round trips per call). At 24 ms RTT: 1 client 40/s p50 24.9 ms; 50 clients 1,742/s p50 27.4 ms;
  50 clients pipelined x16 26,600/s p50 27.8 ms (the round trip's own limits: ~2,000/s and ~33,000/s).
- tests/internet_wrapper.py: Redis's suite with front and host 24 ms apart: incr.tcl = control (31/1); hash.tcl 66 of
  73 reported, 1 red (the control's), cut by the sandbox's 295 s command limit, not by a failure.
- tests/run_two_fronts.py against a named host (RAM_ENDPOINT, keys prefixed per run): GREEN through the 24 ms path.
- tools/qualify.sh (build, oracles, suite, inet, deploy against RAM_ENDPOINT), RUN-ACROSS-THE-INTERNET.md.
- [DIAG-REGION] the front logs every failed region call whole (connection, op, body, error, wire counters).
## RESOLVED: the failure found by the deploy stage
Coalescing [CLIENT_COALESCES_V1] and wire-order turns [WIRE_ORDER_TURNS_V1] in the C++ client, as the Python client
does, and the revised [SAME_MISS_FROM_CACHE_V1] (nothing erased; the answer makes the SAME hit's move, in turn).
50 clients across 24 ms: 0 failures in 3 runs (was 97). list.tcl = control, 4,331 SAME misses resolved in turn.
Oracles (two-fronts, trust, reconnect, same-miss) GREEN. See client-equivalence.tgz / CLIENT-EQUIVALENCE.md.
