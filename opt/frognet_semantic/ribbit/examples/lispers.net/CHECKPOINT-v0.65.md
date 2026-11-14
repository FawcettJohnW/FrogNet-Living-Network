# CHECKPOINT v0.65 -- held by the session (2026-11-11)

John 2026-11-11: "make it all memory and role based ... This is our flagship."

What went, and what replaced it:

| Was (message/time based) | Now (memory based) | Tag |
|---|---|---|
| ETR heartbeat rewrote `etr-live/<xtr_id> {alive_until}` every `interval_s`; resolvers derived death from the clock | the ETR writes `etr-live/<xtr_id>` once; its session holds it; the memory removes it when the session ends and every held read on `etr-live` is handed the removal. Alive = the cell is there | [LIVE_WHILE_ITS_SESSION_IS_V1] |
| `etr_liveness.stop` wrote `etr-live-control {stop}` to wake the heartbeat | removes the ETR's own cell | same |
| lisp-service heartbeat thread rewrote every capability tuple every `heartbeat_s` with the next `beat` | each tuple written once, held by the session | [CAPABILITIES_HELD_V1] |
| lisp-service slept `startup_wait_s`, then one plain read per family | each role holds a read on the capabilities it uses and handles each as it arrives or goes; the service also holds a read on its own first tuple and stops when it goes | same |
| lisp-service main loop polled `failure()` every 100 ms; signals by handler flag | sleeps on the Engine's event word; one thread takes SIGINT/SIGTERM in `sigwait` | [WAIT_ON_THE_EVENT_WORD_V1] |
| a thread needing a resolver view another thread was arming spun in `yield()` | sleeps on the node's `ready` word; a builder that fails marks it -1 and the waiters throw | [ARMING_WAITS_ON_THE_WORD_V1] |

Death detection is the memory's: a process exit closes the session at once; a host that stops answering is dropped
by the RAM host's sockets (keepalive idle 2 s, interval 1 s, 3 probes, user timeout 5 s -- ram_host.cpp).

Refused, by name, rather than ignored: `etr_liveness.start` with `interval_s`/`lifetime_s`; a lisp-service
configuration with `heartbeat_s`, `startup_wait_s` or an ETR `liveness`. Losing the memory now exits lisp-service
with 3 (was 0).

Not changed: the LISP UDP front and registrar (they speak to conventional LISP routers -- the protocol boundary);
`expires_at` on UDP-path registrations (the protocol's record TTL).

Qualification 20261111-193822 (RUNS=1, x86-64, local): local OK, fnw1 OK, independent 10/10 + cross-process PASS,
handler PASS, stress 0 partial, client PASS, boundary PASS, read-your-write PASS, fail-loudly PASS, service PASS
(15 checks, incl. "the map-server's held read was handed etr-b's capability as gone"). Oracle 4/4 after installing
`future` (20261111 rerun). RUNS=3 of local/independent/service/failloud: all 3/3. Liveness steady state: 0 requests,
0 B in 3 s.
