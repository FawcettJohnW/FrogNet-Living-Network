# v0.39 ETR consumes a Map-Notify and acknowledges it — GREEN

Base: v0.38 GREEN. Red: CHECKPOINT-v0.39-red.md (49 tests, 1 error; byte oracle failing).

## Behaviour
- `wire.etr_notify4 hex source` -> {"result":"good","ack_hex":...} | {"result":"auth-failed"} |
  {"result":"unknown-map-server"}.
- Authenticated notify: the map-server is found by the source address in the held map-server view, the key
  in the process-private key snapshot; HMAC verified; failure or no match drops it (control behaviour). An
  unauthenticated notify is acknowledged without a match (control).
- Map-Notify-Ack: type 5, record count 0 (as the control sends it), notify's nonce/key-id/alg/auth-len, the
  notify's records (and xTR-ID trailer) copied, HMAC over the whole ack with the same key.
- FNW1: the ETR publishes its own `etr-notify-received|<ms>` count (control: map_notifies_received).
- (S,G) forwarding to the ITR is not implemented.

## Qualification
- Dino byte oracle: `PASS ribbit etr_notify4 ack == control lisp_send_map_notify_ack` for a notify produced
  by our Map-Server from the ETR's own register; the three earlier checks still PASS.
- Local 48 PASS / 1 SKIP; clean FNW1 48 PASS / 1 SKIP.
- Independent processes: a separate Map-Server process produces the notify for the ETR's register, the ETR
  verifies and acknowledges it, the Map-Server process verifies the ack's HMAC. All other held tests,
  the registrar test and cross_process_ram PASS.
- Runtime lock-free probe YES; zero participant locks/CVs, atomic shared_ptr, Memory::remove, lines over 200;
  package_check PASS.

## Performance (artifacts/performance-v0.39.txt; 1-core sandbox, JSON-lines interface cost)
- wire.etr_notify4, FNW1: median 84.06 us/notify; local backend (no shared-memory write): 63.51 us. The
  difference is the published count write; the rest is two HMACs and a larger JSON payload each way.
- Request paths, register build and registrar trigger latency unchanged within noise.

## Not claimed
- The registrar receiving notifies on its UDP socket and sending the ack (the wire transform exists; the
  socket loop is the next slice); notify retransmission handling; (S,G) notifies; Map-Notify-Ack on the
  Map-Server side.
- Note for the register: the control sends the Map-Notify-Ack with record count 0 while carrying records;
  RFC 9301 gives the ack the Map-Notify's format. We match the control.
