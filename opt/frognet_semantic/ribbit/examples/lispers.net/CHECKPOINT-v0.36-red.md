# v0.36 RED — ETR Map-Register generated from held database_mapping (IPv4)

Base: v0.35 GREEN (7c97ae8300c91beaca1459e0a567417ffa5b051bce5a4ec5ec6d7e7361ef7690).

Characterized from the control (lispers.net lisp-etr.py `lisp_build_map_register` 480,
`lisp_build_map_register_records` 386; lisp.py `lisp_map_register.encode` 4023, `encode_xtr_id` 4173,
`lisp_compute_auth` 9392):
- One Map-Register per configured map-server carrying every database-mapping as an EID-record; header flags
  xTR-ID present (0x02000000) and use-TTL-for-timeout (0x800) always; proxy-reply (0x08000000),
  merge (0x400) and map-notify (0x100) from the map-server's configuration; refresh (0x1000) only on the
  periodic timer when the map-server asks for refresh.
- Nonce starts at 0xaabbccdddfdfdf00 and increments per packet (packed native, as the codec already treats it).
- Each EID-record: authoritative, record TTL LISP_REGISTER_TTL = 3 unless the database-mapping configures
  its own; each locator R bit set, L bit = locator is a local address (host-dependent; not reproduced).
- Trailer: 128-bit xTR-ID and 64-bit site-ID, big-endian. HMAC (key-id, alg from the map-server) over the
  whole packet with the auth field zeroed, using the map-server's password.
- Periodic send every 60 s (LISP_MAP_REGISTER_INTERVAL), first after 5 s; deregistration (record TTL 0) is
  sent ONLY for dynamic EIDs in the control — not for static database-mapping removal.

Red tests:
- `test_etr_map_register_from_held_database_mapping_is_accepted_by_map_server`: build the ETR's Map-Register
  from its held database mapping, check header/auth/trailer, hand it to the Map-Server path, which must
  authenticate it and register both prefixes. Against v0.35: 47 tests, 1 error
  (`unsupported operation: wire.etr_register4`). Raw: artifacts/ribbit-local-etr-register-red-v0.36.txt.
- Dino byte oracle extended: tools/control_wire_oracle.py `etr_register4` builds the packet with the
  control's own classes and `lisp_compute_auth`; tools/test_control_wire_oracle.py requires ours to be
  byte-identical. Against v0.35: fails (unsupported operation).
- No implementation source changed.
