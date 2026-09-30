# v0.34 RED — IPv6 ETR Map-Request answered from held database_mapping

Base: v0.33 GREEN (5bc5ef4b1ee07a7e4b85135ed125d972205911a28df22832c31fdaaaf96fe25c).
- New test `test_etr_map_request_answers_from_held_database_mapping_ipv6`: held ETR mappings
  2001:db8:e7::/48 -> 2001:db8:ffff::48 and 2001:db8:e7:8000::/49 -> 2001:db8:ffff::49 (IID 0); a decoy
  Map-Server registration 2001:db8:e7:c800::/56 for the same target must NOT be used. Request for
  2001:db8:e7:c801::1/128 must return exactly one record: the /49, locator 2001:db8:ffff::49, TTL 1440,
  authoritative, R bit, length 64, zero request-time database-mapping reads.
- `wire.etr_request6` added to the local runner's capabilities.
- Result against unchanged v0.33: 45 tests, 1 error (`unsupported operation: wire.etr_request6`), 1 skip.
  Raw: artifacts/ribbit-local-etr6-red-v0.34.txt. No implementation source changed.
