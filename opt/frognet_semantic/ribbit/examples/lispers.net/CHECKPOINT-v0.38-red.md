# v0.38 RED — periodic ETR registration as a participant

Base: v0.37 GREEN (7f331908f994c3c0c272053644257c950033c5216676cb8208279b643f2f1644).

Characterized from the control (lisp-etr.py):
- `lisp_process_register_timer` sends Map-Registers to every map-server every LISP_MAP_REGISTER_INTERVAL
  (60 s) with refresh=True; the first send comes from a 5 s trigger timer that calls the same function.
- `lisp_etr_map_server_command`: a newly configured map-server gets an immediate register (refresh=False).
- `lisp_build_map_register` creates a fresh lisp_map_register per call, so each send run's first packet has
  nonce 0xaabbccdddfdfdf01 and increments per packet within the run.
- The register is a UDP datagram to the map-server (lisp_send_map_register); the refresh bit is set only when
  the call is periodic AND the map-server has refresh-registrations.

Red (independent processes over FNW1; intervals shortened): tools/test_etr_registrar.py — start a registrar on
the ETR, receive its UDP Map-Registers on two loopback map-server addresses, check timer vs trigger refresh
semantics and the control nonce, hand each datagram to an independent Map-Server process which must accept and
register it, and have a third process observe the registrar's published send truth (no password).
Against unchanged v0.37: `unsupported operation: etr_registrar.start`
(artifacts/etr-registrar-red-v0.38.txt). No implementation source changed.
