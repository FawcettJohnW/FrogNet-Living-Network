# v0.37 RED — map-server configuration as ETR-owned held truth

Base: v0.36 GREEN (a79817bed7b3a09af110bef6cfd397e15d5d3906d025274aea69b9d23511186a).

Characterized from the control (lispconfig.py `lisp_map_server_command` 3766, lisp.py `class lisp_ms` 15514):
- A map-server entry: address (or dns-name), ms-name (default "all"), authentication-type sha1|sha2,
  authentication-key (key-id and password; a key without a type defaults to SHA-256), proxy-reply,
  merge-registrations, refresh-registrations, want-map-notify (all default no), site-id (integer, default 0),
  encryption-key (not in scope).
- xTR-ID is not map-server configuration: the control draws one random 64-bit value for the first map-server
  and reuses it for all (lisp_get_control_nonce), so it is the ETR's identity. It stays an argument.
- The password stays process-private in the ETR (same rule as Map-Server site keys since v0.7).

Red:
- `test_etr_map_register_built_from_held_map_server_configuration`: configure a map-server on the ETR, observe
  it held, read it back without a password, build the register from held truth only (map_server=address) and
  require the same bytes as the explicit-argument build; after delete, building for it must fail.
  Against v0.36: 48 tests, 1 error (`unsupported operation: etr_map_server.add`).
  Raw: artifacts/ribbit-local-ms-config-red-v0.37.txt.
- tools/check_ms_secret.cpp: an independent RAM reader that requires the published map-server cell to carry
  the address and no password field (run in the green's independent-process qualification).
- No implementation source changed.
