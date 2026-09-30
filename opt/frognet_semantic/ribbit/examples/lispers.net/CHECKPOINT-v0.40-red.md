# v0.40 RED — Map-Notify over the registrar's control socket

Base: v0.39 GREEN (204fa277f522f02e9dfeaf646d7c955cb6840d317d60345957f3997cbfe3f257).

Characterized from the control (lisp-etr.py 1668-1677): the ETR binds ONE ephemeral UDP socket
(lisp_ephem_socket on 0.0.0.0:lisp_ephem_port), sends its Map-Registers from it (lisp_send_sockets[0]), and the
map-server's Map-Notify comes back to that socket (the map-server answers to the register's source port).
The ack goes to the map-server's control port (lisp_send_map_notify_ack -> LISP_CTRL_PORT).

Red: tools/test_etr_registrar_notify.py — the map-server's UDP side is the test; the notify is produced by an
independent Ribbit-LISP Map-Server process from the register the registrar actually sent; it is sent back to
the register's source address/port; the ack must arrive on the map-server control port from the same socket,
HMAC-valid; a tampered notify must not be acknowledged; another process observes the counts; stop is observed.
Against v0.39 the register arrives but no ack does (v0.39 sends from a throwaway socket and never listens):
artifacts/etr-registrar-notify-red-v0.40.txt. No implementation source changed.
