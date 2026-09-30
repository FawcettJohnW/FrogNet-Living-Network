# v0.33 RED — ETR Map-Request answered from held database_mapping (IPv4)

Base: v0.32 runtime-lock-free GREEN (archive SHA-256 24629d38946fde0881935b05ba4d9fb4bcc71d7d0248d1819074e3241d72b795).

- New test `test_etr_map_request_answers_from_held_database_mapping_ipv4` (tests/test_wire_contract.py).
- Contract, from RFC 9301 and the control's `lisp_etr_process_map_request` / `lisp_build_map_reply`
  (lispers.net lisp.py 7544, 7384): the ETR answers from its own database mapping with the best-matching
  EID-prefix; one record; authoritative; action No-Action; record TTL 1440; R bit on each locator.
- Held ETR mappings 198.18.0.0/16 -> 192.0.2.16 and 198.18.128.0/17 -> 192.0.2.17 (IID 0); a decoy
  Map-Server registration 198.18.200.0/24 -> 192.0.2.99 exists for the same target and must NOT be used.
- Request for 198.18.200.1/32 must return exactly one record: 198.18.128.0/17, locator 192.0.2.17, length 40,
  and zero request-time database-mapping RAM reads.
- `wire.etr_request4` added to the local runner's capability list.
- Result against unchanged v0.32 implementation: 44 tests, 1 error (`unsupported operation: wire.etr_request4`),
  1 explicit skip; every other test green. Raw output: artifacts/ribbit-local-etr-red-v0.33.txt.
- Not claimed: the L (local) bit — the control sets it only for RLOCs that are addresses of the answering
  host, which is host-dependent; not asserted. ETR database miss behaviour: not established.
- No implementation source changed in this checkpoint.
