# v0.39 RED — ETR consumes a Map-Notify and acknowledges it

Base: v0.38 GREEN (3abf0672ef2b0473fb75feca95bd5d06f1ec25627fe3ea307a75998b36827dc5).

Characterized from the control (lisp.py `lisp_process_map_notify` 10918, `lisp_send_map_notify_ack` 9667,
`lisp_map_notify.encode/decode` 4227):
- The ETR finds the map-server by the notify's source address; if the notify is authenticated (alg or auth-len
  non-zero) it verifies HMAC with that map-server's password and drops the notify on failure or when no
  map-server matches. An unauthenticated notify is accepted without a map-server match.
- It answers with a Map-Notify-Ack: type 5, the notify's nonce, key-id, alg and auth-len, the notify's
  EID-records copied (everything after the auth field — including an xTR-ID trailer, which the control's
  Map-Server copies into the notify from the register), record count 0 (the control sets it to 0 while still
  copying the records), HMAC over the whole packet with the same password.
- It counts notifies received per map-server. (S,G) records are forwarded to the ITR — out of scope.

Red: `test_etr_consumes_map_notify_and_acks_it` (ETR register -> our Map-Server notify -> ETR ack; checks
type, record count, nonce/key/alg, copied records, HMAC; tampered notify -> auth-failed; unknown source ->
unknown-map-server). Against v0.38: 49 tests, 1 error (`unsupported operation: wire.etr_notify4`).
Dino byte oracle extended with the control's own ack for the same notify; fails against v0.38.
No implementation source changed.
