# v0.40 Map-Notify over the registrar's control socket — GREEN

Base: v0.39 GREEN. Red: CHECKPOINT-v0.40-red.md (register arrived, no ack: v0.39 sent from a throwaway socket).

## Behaviour
- The registrar binds ONE UDP control socket (INADDR_ANY, ephemeral port unless `listen_port`), as the control's
  lisp_ephem_socket: Map-Registers are sent from it and the map-server's Map-Notify comes back to it.
- A second participant thread, the receiver, blocks in recvfrom on that socket. Each Map-Notify is verified
  against the held map-server view and the process-private key snapshot (the same function `wire.etr_notify4`
  now uses) and acknowledged to the map-server's control port from the same socket. It writes only its own
  cells: `etr-registrar-notify|<ms>` {acks, rejected}. Tampered or unknown-source notifies are counted, not acked.
- Stop wakes the receiver with an empty datagram to its own socket (no polling); the clock thread stops at its
  next wake and publishes "stopped".
- New observation ops: `etr_registrar.notified`, `etr_registrar.notify_wait`, `etr_registrar.state_wait`.

## Defect found and fixed (present since v0.38)
- A map-server published by ANOTHER process — so this ETR holds no key for it — made the registrar throw inside
  its thread, which terminated the whole ETR process (SIGABRT). Reproduced on the v0.39 binary:
  artifacts/registrar-keyless-crash-v0.39.txt ("alive ... False exit -6"). The batch run of the held tests found
  it (a previous test's map-servers were still published). Now a per-map-server build failure is recorded as a
  truth in `etr-register-sent|<ms>` {"error": ...} and the registrar carries on; the notify test covers it.
- Test isolation: the registrar tests now remove the map-servers and database mappings they created.

## Qualification
- tools/test_etr_registrar_notify.py: register from the registrar's socket; notify produced by an independent
  Map-Server process sent back to the register's source port; HMAC-valid ack received on the map-server control
  port from the same socket; tampered notify not acknowledged; keyless map-server recorded and the registrar keeps
  acknowledging; counts observed by another process; stop observed. 10/10 runs of the final code.
- test_etr_registrar (v0.38) 5/5; batch of all independent-process tests on one server twice: all PASS.
- Local 48 PASS / 1 SKIP; clean FNW1 48 PASS / 1 SKIP; Dino byte oracle 4 PASS; runtime lock-free probe YES;
  reclamation stress born=100001 dead=100001; zero participant locks/CVs, atomic shared_ptr, Memory::remove,
  lines over 200; package_check PASS.

## Performance (artifacts/performance-v0.40.txt; 1-core sandbox)
- Map-Notify -> Map-Notify-Ack over UDP, N=500: median 34 us, p90 60 us, max 3.6 ms (recvfrom, HMAC-SHA-256
  verify, ack build, HMAC, sendto; the count publication is a non-waiting write). No JSON interface involved.
- Everything else unchanged within noise.

## Not claimed
- Notify retransmission / duplicate suppression; (S,G) notifies; Map-Notify-Ack handling on the Map-Server side;
  IPv6 control socket; sending the Map-Server's own notifies over UDP.
