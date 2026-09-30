# v0.36 ETR Map-Register from held database_mapping (IPv4) — GREEN

Base: v0.35 GREEN. Red: CHECKPOINT-v0.36-red.md (characterization of the control's ETR Map-Register).

## Behaviour
- `wire.etr_register4`: builds the ETR's Map-Register for one map-server from the held ETR database mapping
  (IID 0, active IPv4 entries, held-table order). Map-server configuration is passed as arguments: key-id,
  alg (sha1/sha256/none), password, want-map-notify, merge, proxy-reply, refresh, xTR-ID, site-ID, nonce,
  optional record TTL (default 3 = LISP_REGISTER_TTL). The password never enters shared memory.
- Packet exactly as the control's lisp_build_map_register: I and T bits always, P/M/merge/refresh from
  configuration, authoritative records, R bit, L bit clear, xTR-ID/site-ID trailer, HMAC over the whole packet.
- Map-Server decoders (IPv4 and IPv6) now accept the I-bit xTR-ID/site-ID trailer (previously rejected as
  trailing data), so an ETR's register is accepted end to end; the xTR-ID is carried on each decoded record.

## Qualification
- Dino byte oracle: `PASS ribbit etr_register4 == control lisp_build_map_register` — byte-identical to a
  packet built with the control's own classes and lisp_compute_auth, HMAC included; register4 and reply4
  fixtures unchanged (artifacts/dino-byte-oracle-v0.36.txt).
- Local 46 PASS / 1 explicit SKIP; clean FNW1 46 PASS / 1 explicit SKIP.
- Independent processes: the ETR process builds the Map-Register from its held view with zero request-time
  database reads; a separate Map-Server process authenticates and registers it and resolves the prefix
  (tools/test_held_etr_request.py). All other held tests and cross_process_ram PASS.
- Runtime lock-free probe YES; zero participant locks, atomic shared_ptr, Memory::remove, lines over 200;
  package_check PASS.

## Performance (artifacts/performance-v0.36.txt; 1-core sandbox, FNW1, warmed, JSON-lines interface cost)
- wire.etr_register4 (2 records, HMAC-SHA-1): median 26.88 us per Map-Register
- wire.etr_request4 21.02, wire.request4 25.47, wire.etr_request6 25.82 us/request (v0.35: 20.67, 25.06,
  25.52): within run-to-run noise; database_mapping_reads 0.

## Not claimed
- Sending: when and to whom. The control sends on a 60 s timer (first after 5 s) to each configured
  map-server; this slice builds the packet on request. Map-server configuration as published truth.
- IPv6 and non-zero IID records (LCAF); the 20-record / MTU split into several packets; NAT/RTR records;
  decentralized (decent) map-server selection; encryption (ekey).
- Deregistration: the control sends TTL-0 registers only for dynamic EIDs, not when a static
  database-mapping is removed — nothing to implement for static mappings; dynamic EIDs are out of scope.
