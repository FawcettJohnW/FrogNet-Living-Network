# lispers.net comparison -- work in progress, 2026-09-27

## lispers.net 0.643 is running in the build container (Map-Server + Map-Resolver), installed the way its own
## build/Dockerfile installs it, started with its own RUN-LISP, API on 8800.
- lisp.config: map-server = yes, map-resolver = yes, root account, one site `ribbit-comparison`,
  authentication-key [1]compare-secret, allowed-prefix 0 / 198.18.0.0/16, accept-more-specifics. The file MUST start
  with the line "# lispers.net lisp.config file" or lispers.net declares it corrupt and ignores it.
- The container's kernel has no IPv6; lispers.net opens IPv6 sockets unless lisp_is_raspbian(). container-only-v4shim/
  (PYTHONPATH) puts it on that IPv4-only path. NOT for real machines -- they have IPv6.

## Found while getting it up -- matters for anyone running both on one machine
- lispers.net's STOP-LISP (run by RUN-LISP/RESTART-LISP) kills EVERY process whose command line contains "lisp-".
  That includes ribbit-lisp, lisp-boundary and any shell whose command mentions them or lispers.net's own files.
  Start lispers.net first, and never from a command line that names anything with "lisp-" in it.

## First probe (probe.py, results in lispers-logs-first-probe.txt)
- An authenticated Map-Register (key 1, HMAC-SHA-1) reached lisp-core and was handed to lisp-ms (logged); no
  Map-Notify came back to the sender, and lisp-ms logged nothing after receiving it. Not yet explained.
- A bare Map-Request to 4342 was routed to the ETR process, not the Map-Resolver: an ITR reaches a map-resolver
  with the Map-Request inside an Encapsulated Control Message (type 8, inner IP+UDP). The comparison has to send ECMs
  to lispers.net. (Ribbit's lisp-boundary answers bare Map-Requests -- to be checked against RFC behaviour.)

## Next
1. Why no Map-Notify (register flags/auth vs what lisp-ms expects).
2. ECM-wrapped Map-Requests.
3. The driver: many senders in parallel (registers, refreshes, withdrawals, requests for registered / unregistered /
   more-specific EIDs), identical traffic to lispers.net and to lisp-boundary, per-message latency, throughput, bytes.

## Progress, 05:34
- Register works with HMAC-SHA-256-128: authenticated Map-Register -> Map-Notify from lispers.net in 0.99 ms
  (probe2.py). HMAC-SHA-1-96 CRASHES lisp-ms (Python 3 bytes-vs-str bug) -- written up in FINDINGS-FOR-DINO.md.
  The comparison uses SHA-256 for both sides.
- lsstop.sh now kills ms/mr/core (the earlier pattern left stale lisp-ms processes that compete for its IPC socket).
- Next: Map-Requests as ECMs with our own address as ITR-RLOC and force-proxy-reply on the site (the Map-Server
  answers, as Ribbit's held view does); then the parallel driver against both.

## 05:40 -- compare_lispers.py runs against both (compare-first-run-container.txt; same machine, loopback-class wire)
lispers.net 0.643 (force-proxy-reply) vs Ribbit lisp-boundary + ram-server, 4 xTRs x 4 prefixes, 4 ITRs x 10:
  register 2.17 vs 17.93 ms   refresh 1.55 vs 19.84   change 1.93 vs 23.09   (Ribbit 8-13x SLOWER to register)
  resolve  2.20 vs 0.15 ms    resolve after withdraw 2.07 vs 0.24            (Ribbit ~10x FASTER to resolve)
  every answer checked: locator sets exact, negatives negative.
- Ribbit gap: a withdrawal (TTL 0, from a registered RLOC, want-map-notify) is APPLIED but gets NO Map-Notify;
  lispers.net Map-Notifies it. 16 errors in the run = those 16.
- lisp-boundary now takes ECM-encapsulated Map-Requests ([ECM_MAP_REQUEST_V1]); before, it rejected type 8 --
  which is how every real ITR asks a map-resolver.
- Why Ribbit's register costs ~18-23 ms on one machine is not yet traced: worker -> op=lisp -> the host's ONE Engine
  (serialized) -> loopback RAM calls. That is the number to take apart next.
