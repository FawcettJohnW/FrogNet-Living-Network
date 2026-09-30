# Acceptance report

lispers.net 0.643 (A) vs Ribbit-LISP v0.62-own-writes (B), wire loopback, 2026-09-28 13:48

| test | classification | A | B | what |
|---|---|---|---|---|
| P4 | PARITY | PASS | PASS | liveness: a Map-Request outside every site is answered negatively |
| L1.1 | PARITY | PASS | PASS | authenticated Map-Register (M set) -> Map-Notify: same nonce, HMAC valid with the site key |
| L1.2 | PARITY | PASS | PASS | Map-Register with M clear: no Map-Notify, and the registration resolves |
| L1.3 | PARITY | PASS | PASS | ECM Map-Request for a registered EID: exact EID-prefix, mask and locator set |
| L1.5 | PARITY | PASS | PASS | ECM Map-Request inside an accept-more-specifics site, nothing registered: negative for the EID itself (/32), TTL 1, natively-forward |
| L1.5b | PARITY | PASS | PASS | ECM Map-Request inside a site that does NOT accept more-specifics, nothing registered: negative with the site's prefix |
| L1.6 | PARITY | PASS | PASS | ECM Map-Request outside every site: negative, natively-forward |
| L1.7 | OBSERVATION | PASS | PASS | a bare (non-ECM) Map-Request to the map-resolver port |
| L2.1 | PARITY | PASS | PASS | refresh (same locator set) keeps resolving |
| L2.2 | PARITY | PASS | PASS | change of locator set: the new set resolves, the old never again |
| L2.3 | PARITY | PASS | PASS | withdraw (TTL 0) from a registered RLOC: Map-Notify, then negative |
| L2.4 | PARITY | PASS | PASS | withdraw from a source that is not a registered RLOC is ignored |
| L2.5 | PARITY | PASS | PASS | TTL expiry without refresh (3-second TTL): resolves before; how long until it stops resolving |
| L2.6 | PARITY | PASS | PASS | a more-specific inside an accept-more-specifics site is accepted |
| L2.7 | PARITY | PASS | PASS | a more-specific without accept-more-specifics is rejected, no state |
| L2.8 | PARITY | PASS | PASS | a prefix outside every site is rejected, no state |
| L2.10 | PARITY | PASS | PASS | two-record register: both applied, one Map-Notify |
| L2.11 | PARITY | PASS | PASS | longest-prefix match across nested registrations |
| L3.1 | PARITY | PASS | PASS | HMAC-SHA-256-128 with the correct key is accepted |
| L3.2 | A FAIL | FAIL | PASS | HMAC-SHA-1-96 with the correct key is accepted |
| L3.3 | A FAIL | FAIL | PASS | wrong key: rejected, no Map-Notify, no state |
| L3.4 | A FAIL | FAIL | PASS | wrong key-id: rejected |
| L3.5 | A FAIL | FAIL | PASS | no authentication on a keyed site: rejected |
| L3.6 | A FAIL | FAIL | PASS | one bit flipped in the authentication data / in the body: rejected |
| L3.7 | KNOWN DIFFERENCE | FAIL | PASS | two-record register with the second record unauthorized: nothing applied |
| L4.1 | PARITY | PASS | PASS | truncation: a valid Map-Register cut at every byte offset; after each, a valid register still works |
| L4.2 | PARITY | PASS | PASS | wrong type values (0, 9-15) and an ECM whose inner packet is not a Map-Request; still serving after |
| L4.3 | PARITY | PASS | PASS | record count larger than the records present, and locator count mismatch; still serving after |
| L4.4 | A FAIL | FAIL | PASS | unknown AFI and mask > 32; still serving after |
| L4.5 | PARITY | PASS | PASS | authentication length disagreeing with the algorithm; still serving after |
| L4.8 | PARITY | PASS | PASS | an oversize datagram (8 KiB of garbage after a valid header); still serving after |
| L4.9 | PARITY | PASS | PASS | a flood of 500 malformed packets interleaved with valid registers: every valid one answered |
| L1.8 | PARITY | PASS | PASS | IPv6 EIDs: register -> Map-Notify (HMAC valid); ECM (inner IPv6) request -> exact record; unregistered -> negative |
| L2.9 | PARITY | PASS | PASS | merge semantics: two xTRs register one prefix with merge -> the union; one replacing its own changes only its own |
| L2.13 | PARITY | PASS | PASS | site deleted while registered, then restored: a new register is accepted and resolves (what resolves in between is recorded) |
| L2.12 | PARITY | PASS | PASS | instance-id isolation: the same prefix registered in iid 0 and iid 7, each resolves only its own |
| L1.11 | PARITY | PASS | PASS | Info-Request (NAT traversal) -> Info-Reply: same nonce, the global ETR RLOC and port it came from, the private name |
| L1.12 | PARITY | PASS | PASS | Map-Notify-Ack (authenticated, for a registered prefix) is consumed: no answer, still serving |
| L1.13 | OBSERVATION | PASS | PASS | DDT-originated Map-Request to the map-server -> Map-Referral: MS-ACK for a registered EID, MS-NOT-REGISTERED inside a site, and outside every site |
| L2.14.geo | PARITY | PASS | PASS | an LCAF geo locator (encoded by lispers.net's own encoder) is registered and returned byte for byte |
| L2.14.elp | PARITY | PASS | PASS | an LCAF elp locator (encoded by lispers.net's own encoder) is registered and returned byte for byte |
| L2.14.rle | PARITY | PASS | PASS | an LCAF rle locator (encoded by lispers.net's own encoder) is registered and returned byte for byte |
| L2.14.json | A FAIL | FAIL | PASS | an LCAF json locator (encoded by lispers.net's own encoder) is registered and returned byte for byte |
| L3.12 | PARITY | PASS | PASS | encrypted Map-Registers (lisp encryption-keys): the right key -> accepted and resolves; a wrong key or an unknown key-id -> rejected; still serving |
| L2.15.shutdown | A FAIL | FAIL | PASS | site shutdown = yes: a register is rejected, nothing resolves |
| L2.15.allowed-rloc | A FAIL | FAIL | PASS | allowed-rloc: a register with an allowed RLOC is accepted, one with another RLOC is not |
| L2.15.force-ttl | PARITY | PASS | PASS | force-ttl 30: the Map-Reply's record TTL is 30 seconds (seconds encoding), registered and unregistered |
| L2.15.not-registered-yet | PARITY | PASS | PASS | proxy-reply-action not-registered-yet: an unregistered EID gets a negative reply for the EID itself, action 7, TTL 1 |
| L2.15.echo-nonce | PARITY | PASS | PASS | echo-nonce-capable: the Map-Reply has the E bit |
| L2.15.drop | PARITY | PASS | PASS | force-proxy-reply no, proxy-reply-action drop: a registered EID is answered with action drop, TTL 1440, its locators |
| L2.15.pitr-drop | PARITY | PASS | PASS | pitr-proxy-reply-drop: a PITR's Map-Request for a registered EID is answered with action drop, TTL 1440, its locators |
| L2.16 | A FAIL | FAIL | PASS | multicast (S,G): a Map-Register for (198.29.1.0/24, 224.1.1.1/32) in a site with a group-prefix is accepted; a Map-Request for (S,G) returns its locators |
| L2.16b | OBSERVATION | PASS | PASS | multicast (S,G) in lispers.net's own (non-RFC, padded) layout: what each system does with it |
| L1.14 | PARITY | PASS | PASS | a positive proxy Map-Reply carries TTL 1440 (registered with a 3-minute TTL) |
| L2.15.force-nat-proxy-reply | PARITY | PASS | PASS | force-nat-proxy-reply (no xTR behind NAT): a registered EID is proxy-replied with its locators, TTL 1440 |
| L2.17.set-record-ttl | PARITY | PASS | PASS | policy: a matching Map-Request gets set-record-ttl 10; one no clause matches gets the implied drop (action 4, no locators) |
| L2.17.set-action-drop | PARITY | PASS | PASS | policy set-action drop: action 4 (policy-denied), no locators |
| L2.17.set-rloc-address | PARITY | PASS | PASS | policy matching the requesting RLOC with set-rloc-address: the reply's locator is the set address |
| L2.18.geo | PARITY | PASS | PASS | policy set-rloc-address + set-geo-name: the answer's locator is the AFI-list LCAF lispers.net's encoder builds for that address and object |
| L2.18.elp | PARITY | PASS | PASS | policy set-rloc-address + set-elp-name: the answer's locator is the AFI-list LCAF lispers.net's encoder builds for that address and object |
| L2.18.rle | PARITY | PASS | PASS | policy set-rloc-address + set-rle-name: the answer's locator is the AFI-list LCAF lispers.net's encoder builds for that address and object |
| L2.18.json | PARITY | PASS | PASS | policy set-rloc-address + set-json-name: the answer's locator is the AFI-list LCAF lispers.net's encoder builds for that address and object |
| L2.9b | OBSERVATION | PASS | PASS | merged Map-Notify: a merge register from a second xTR is acknowledged to EVERY registered xTR RLOC with the merged locator set |
| L2C.1 | PARITY | PASS | PASS | causality: registered through one client, resolved at once through another |
| L3.8 | OBSERVATION | PASS | PASS | replay: the same accepted Map-Register sent again |
| L3.9 | A FAIL | FAIL | PASS | the authentication matrix: {SHA-1, SHA-256} x {valid, wrong key, auth length wrong}; a valid SHA-256 register after each is accepted |
| L3.10.first.unauthorized | KNOWN DIFFERENCE | FAIL | PASS | three-record register, the first record unauthorized: nothing applied |
| L3.10.first.invalid | A FAIL | FAIL | PASS | three-record register, the first record invalid: nothing applied |
| L3.10.middle.unauthorized | KNOWN DIFFERENCE | FAIL | PASS | three-record register, the middle record unauthorized: nothing applied |
| L3.10.middle.invalid | A FAIL | FAIL | PASS | three-record register, the middle record invalid: nothing applied |
| L3.10.last.unauthorized | KNOWN DIFFERENCE | FAIL | PASS | three-record register, the last record unauthorized: nothing applied |
| L3.10.last.invalid | A FAIL | FAIL | PASS | three-record register, the last record invalid: nothing applied |
| L4.6a | PARITY | PASS | PASS | a Map-Register with zero records: no Map-Notify; still serving |
| L4.6b | PARITY | PASS | PASS | the most records that fit a 1500-byte datagram (40): all applied, one Map-Notify |
| L4.6c | PARITY | PASS | PASS | the most locators in one record that fit a 1500-byte datagram (100): all returned |
| L4.7 | PARITY | PASS | PASS | TTL extreme: 0x7fffffff minutes is accepted and resolves |
| L4.10 | OBSERVATION | PASS | PASS | restart of the map-server with live registrations: what is retained |
| L4.11 | OBSERVATION | PASS | PASS | the RAM host is lost mid-run (Ribbit only; lispers.net holds its state in its own process) |

## Evidence

- **P4** (PARITY)
  - A: reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x01', 'records': [{'ttl': 15, 'action': 1, 'eid': '203.0.113.9', 'mask': 32, 'locs': [], 'iid': 0}]}
  - B: reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x01', 'records': [{'ttl': 15, 'action': 1, 'eid': '203.0.113.9', 'mask': 32, 'locs': [], 'iid': 0}]}
- **L1.1** (PARITY)
  - A: type 4, 76 B, HMAC valid
  - B: type 4, 76 B, HMAC valid
- **L1.2** (PARITY)
  - A: notify none; reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x02', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.12.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}]}
  - B: notify none; reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x02', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.12.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}]}
- **L1.3** (PARITY)
  - A: record {'ttl': 1440, 'action': 0, 'eid': '198.18.13.0', 'mask': 24, 'locs': ['192.0.2.2', '198.51.100.13'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 0, 'eid': '198.18.13.0', 'mask': 24, 'locs': ['192.0.2.2', '198.51.100.13'], 'iid': 0}
- **L1.5** (PARITY)
  - A: record {'ttl': 1, 'action': 1, 'eid': '198.18.250.9', 'mask': 32, 'locs': [], 'iid': 0}
  - B: record {'ttl': 1, 'action': 1, 'eid': '198.18.250.9', 'mask': 32, 'locs': [], 'iid': 0}
- **L1.5b** (PARITY)
  - A: record {'ttl': 1, 'action': 1, 'eid': '198.19.0.0', 'mask': 24, 'locs': [], 'iid': 0}
  - B: record {'ttl': 1, 'action': 1, 'eid': '198.19.0.0', 'mask': 24, 'locs': [], 'iid': 0}
- **L1.6** (PARITY)
  - A: record {'ttl': 15, 'action': 1, 'eid': '203.0.113.77', 'mask': 32, 'locs': [], 'iid': 0}
  - B: record {'ttl': 15, 'action': 1, 'eid': '203.0.113.77', 'mask': 32, 'locs': [], 'iid': 0}
- **L1.7** (OBSERVATION)
  - A: not answered
  - B: answered type 2
- **L2.1** (PARITY)
  - A: notify True, reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x03', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.21.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}]}
  - B: notify True, reply {'nonce': b'\n\xcc\x00\x03\xb3\xe0\x00\x03', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.21.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}]}
- **L2.2** (PARITY)
  - A: resolves to ['192.0.2.2', '198.51.100.22']
  - B: resolves to ['192.0.2.2', '198.51.100.22']
- **L2.3** (PARITY)
  - A: notify True, resolves to []
  - B: notify True, resolves to []
- **L2.4** (PARITY)
  - A: resolves to ['198.51.100.24']
  - B: resolves to ['198.51.100.24']
- **L2.5** (PARITY)
  - A: resolved before: ['192.0.2.2']; stopped resolving after 50.4 s (TTL 3 s)
  - B: resolved before: ['192.0.2.2']; stopped resolving after 3.2 s (TTL 3 s)
- **L2.6** (PARITY)
  - A: notify True
  - B: notify True
- **L2.7** (PARITY)
  - A: notify False, resolves to []
  - B: notify False, resolves to []
- **L2.8** (PARITY)
  - A: notify False, resolves to []
  - B: notify False, resolves to []
- **L2.10** (PARITY)
  - A: notify True
  - B: notify True
- **L2.11** (PARITY)
  - A: resolves to ['198.51.100.41']
  - B: resolves to ['198.51.100.41']
- **L3.1** (PARITY)
  - A: notify True
  - B: notify True
- **L3.2** (A FAIL)
  - A: notify False -- STOPPED SERVING after this test; evidence: 4e 00000003 01180000 00000001 c6123400 01640000 00050001 c0000202 | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10230, in lisp_process_map_register / File "lisp.py", line 4165, in decode / File "lisp.py", line 6773, in lisp_concat_auth_data / File "lisp.py", line 11592, in byte_swap_64 / TypeError: unsupported operand type(s) for &: 'bytes' and 'int'
  - B: notify True
- **L3.3** (A FAIL)
  - A: notify True, resolves to []
  - B: notify False, resolves to []
- **L3.4** (A FAIL)
  - A: notify False, resolves to None -- STOPPED SERVING after this test; evidence:  EID-prefix [1m[92m[0]198.18.54.0/24[0m[0m, key-id 9 / 09/28/26 13:44:55.947: ms: Send Map-Notify to ack Map-Register | [lisp-traceback.log] Traceback (most recent call last): / File "lisp-ms.py", line 1524, in <module> / File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10628, in lisp_process_map_register / File "lisp.py", line 9633, in lisp_build_map_notify / KeyError: 9
  - B: notify False, resolves to []
- **L3.5** (A FAIL)
  - A: notify True, resolves to ['192.0.2.2']
  - B: notify False, resolves to []
- **L3.6** (A FAIL)
  - A: byte 20: notify True, resolves []; byte 50: notify True, resolves []
  - B: byte 20: notify False, resolves []; byte 50: notify False, resolves []
- **L3.7** (KNOWN DIFFERENCE)
  - A: notify True, first record resolves to ['192.0.2.2']
  - B: notify False, first record resolves to []
- **L4.1** (PARITY)
  - A: 75 truncations; still serving
  - B: 75 truncations; still serving
- **L4.2** (PARITY)
  - A: still serving
  - B: still serving
- **L4.3** (PARITY)
  - A: still serving
  - B: still serving
- **L4.4** (A FAIL)
  - A: STOPPED SERVING -- STOPPED SERVING after this test; evidence: n, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.64.0/40[0m[0m[0m[0m | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10305, in lisp_process_map_register / File "lisp.py", line 15174, in add_cache / File "lisp.py", line 11666, in add_cache / File "lisp.py", line 12352, in zero_host_bits / ValueError: negative shift count
  - B: still serving
- **L4.5** (PARITY)
  - A: still serving
  - B: still serving
- **L4.8** (PARITY)
  - A: still serving
  - B: still serving
- **L4.9** (PARITY)
  - A: 0 of 50 valid registers unanswered
  - B: 0 of 50 valid registers unanswered
- **L1.8** (PARITY)
  - A: notify valid; record {'ttl': 1440, 'action': 0, 'eid': '2001:db8:acce:18::', 'mask': 64, 'locs': ['192.0.2.2'], 'iid': 0}; unregistered []
  - B: notify valid; record {'ttl': 1440, 'action': 0, 'eid': '2001:db8:acce:18::', 'mask': 64, 'locs': ['192.0.2.2'], 'iid': 0}; unregistered []
- **L2.9** (PARITY)
  - A: notify A False B False; union ['198.51.100.130', '198.51.100.131']; after A replaced its own ['198.51.100.131', '198.51.100.132']
  - B: notify A True B True; union ['198.51.100.130', '198.51.100.131']; after A replaced its own ['198.51.100.131', '198.51.100.132']
- **L2.13** (PARITY)
  - A: registered True -> ['192.0.2.2']; site deleted (good) -> ['192.0.2.2']; restored (good), re-registered True -> ['192.0.2.2']
  - B: registered True -> ['192.0.2.2']; site deleted (good) -> ['192.0.2.2']; restored (good), re-registered True -> ['192.0.2.2']
- **L2.12** (PARITY)
  - A: notify iid7 True, iid0 True; iid 7 -> ['198.51.100.7'] (record iid 7); iid 0 -> ['192.0.2.2']
  - B: notify iid7 True, iid0 True; iid 7 -> ['198.51.100.7'] (record iid 7); iid 0 -> ['192.0.2.2']
- **L1.11** (PARITY)
  - A: {'ms_port': 0, 'etr_port': 49449, 'global': '192.0.2.2', 'private': 'acceptance-etr'}
  - B: {'ms_port': 0, 'etr_port': 50241, 'global': '192.0.2.2', 'private': 'acceptance-etr'}
- **L1.12** (PARITY)
  - A: answer none; still serving True
  - B: answer none; still serving True
- **L1.13** (OBSERVATION)
  - A: 198.18.141.9: no Map-Referral; answered with type(s) [2]; 198.18.251.9: no Map-Referral; answered with type(s) [2]; 203.0.113.141: no Map-Referral; answered with type(s) [2]
  - B: 198.18.141.9: no Map-Referral; answered with type(s) [2]; 198.18.251.9: no Map-Referral; answered with type(s) [2]; 203.0.113.141: no Map-Referral; answered with type(s) [2]
- **L2.14.geo** (PARITY)
  - A: notify True; locator returned byte-identical to lispers.net's encoding
  - B: notify True; locator returned byte-identical to lispers.net's encoding
- **L2.14.elp** (PARITY)
  - A: notify True; locator returned byte-identical to lispers.net's encoding
  - B: notify True; locator returned byte-identical to lispers.net's encoding
- **L2.14.rle** (PARITY)
  - A: notify True; locator returned byte-identical to lispers.net's encoding
  - B: notify True; locator returned byte-identical to lispers.net's encoding
- **L2.14.json** (A FAIL)
  - A: notify False; locator returned none
  - B: notify True; locator returned byte-identical to lispers.net's encoding
- **L3.12** (PARITY)
  - A: right key: notify True, resolves ['192.0.2.2']; wrong key: notify False, resolves []; unknown key-id: notify False, resolves []
  - B: right key: notify True, resolves ['192.0.2.2']; wrong key: notify False, resolves []; unknown key-id: notify False, resolves []
- **L2.15.shutdown** (A FAIL)
  - A: notify True; resolves []
  - B: notify False; resolves []
- **L2.15.allowed-rloc** (A FAIL)
  - A: allowed: notify True resolves ['192.0.2.2']; other: notify True resolves []
  - B: allowed: notify True resolves ['192.0.2.2']; other: notify False resolves []
- **L2.15.force-ttl** (PARITY)
  - A: registered TTL 0x8000001e, unregistered TTL 0x8000001e (want 0x8000001e)
  - B: registered TTL 0x8000001e, unregistered TTL 0x8000001e (want 0x8000001e)
- **L2.15.not-registered-yet** (PARITY)
  - A: record {'ttl': 1, 'action': 7, 'eid': '198.25.99.9', 'mask': 32, 'locs': [], 'iid': 0}
  - B: record {'ttl': 1, 'action': 7, 'eid': '198.25.99.9', 'mask': 32, 'locs': [], 'iid': 0}
- **L2.15.echo-nonce** (PARITY)
  - A: E bit True
  - B: E bit True
- **L2.15.drop** (PARITY)
  - A: record {'ttl': 1440, 'action': 3, 'eid': '198.27.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 3, 'eid': '198.27.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
- **L2.15.pitr-drop** (PARITY)
  - A: record {'ttl': 1440, 'action': 3, 'eid': '198.28.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 3, 'eid': '198.28.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
- **L2.16** (A FAIL)
  - A: notify False; reply {'afi': 16387, 'locs': [], 'records': 1}
  - B: notify True; reply {'afi': 16387, 'locs': ['198.51.100.216'], 'records': 1}
- **L2.16b** (OBSERVATION)
  - A: register in lispers.net's layout: notified
  - B: register in lispers.net's layout: notified
- **L1.14** (PARITY)
  - A: record TTL 1440
  - B: record TTL 1440
- **L2.15.force-nat-proxy-reply** (PARITY)
  - A: record {'ttl': 1440, 'action': 0, 'eid': '198.33.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 0, 'eid': '198.33.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}
- **L2.17.set-record-ttl** (PARITY)
  - A: matching {'ttl': 10, 'action': 0, 'eid': '198.30.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}; not matching {'ttl': 1440, 'action': 4, 'eid': '198.30.2.0', 'mask': 24, 'locs': [], 'iid': 0}
  - B: matching {'ttl': 10, 'action': 0, 'eid': '198.30.1.0', 'mask': 24, 'locs': ['192.0.2.2'], 'iid': 0}; not matching {'ttl': 1440, 'action': 4, 'eid': '198.30.2.0', 'mask': 24, 'locs': [], 'iid': 0}
- **L2.17.set-action-drop** (PARITY)
  - A: record {'ttl': 1440, 'action': 4, 'eid': '198.31.1.0', 'mask': 24, 'locs': [], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 4, 'eid': '198.31.1.0', 'mask': 24, 'locs': [], 'iid': 0}
- **L2.17.set-rloc-address** (PARITY)
  - A: locator ff00ff0000010001c63364e8
  - B: locator ff00ff0000010001c63364e8
- **L2.18.geo** (PARITY)
  - A: locator as lispers.net encodes it
  - B: locator as lispers.net encodes it
- **L2.18.elp** (PARITY)
  - A: locator as lispers.net encodes it
  - B: locator as lispers.net encodes it
- **L2.18.rle** (PARITY)
  - A: locator as lispers.net encodes it
  - B: locator as lispers.net encodes it
- **L2.18.json** (PARITY)
  - A: locator as lispers.net encodes it
  - B: locator as lispers.net encodes it
- **L2.9b** (OBSERVATION)
  - A: answers to B's register at A's RLOC 192.0.2.2: []
  - B: answers to B's register at A's RLOC 192.0.2.2: [(4, '400000010acc0003b3e000020102002018cb009db3874eb89dd6aa97e19d59808529710ebb21cd83f0f9409a616fd0e4000000030118000000000001c61287000164000000050001c63364870123456789abcdef0123456789ab0b0b0000000000000000')]
- **L2C.1** (PARITY)
  - A: resolves to ['192.0.2.2']
  - B: resolves to ['192.0.2.2']
- **L3.8** (OBSERVATION)
  - A: first notified, replay notified
  - B: first notified, replay notified
- **L3.9** (A FAIL)
  - A: SHA-1/valid: no notify THEN STOPPED SERVING -- STOPPED SERVING after this test; evidence: Dequeue Map-Notify from retransmit queue, key is: 200e0b30300cc0a | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10230, in lisp_process_map_register / File "lisp.py", line 4165, in decode / File "lisp.py", line 6773, in lisp_concat_auth_data / File "lisp.py", line 11592, in byte_swap_64 / TypeError: unsupported operand type(s) for &: 'bytes' and 'int'
  - B: SHA-1/valid: notified; SHA-1/wrong key: no notify; SHA-1/bad length: no notify; SHA-256/valid: notified; SHA-256/wrong key: no notify; SHA-256/bad length: no notify
- **L3.10.first.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.first.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence: n, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.90.0/40[0m[0m[0m[0m | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10305, in lisp_process_map_register / File "lisp.py", line 15174, in add_cache / File "lisp.py", line 11666, in add_cache / File "lisp.py", line 12352, in zero_host_bits / ValueError: negative shift count
  - B: notify False; valid records applied: [False, False]
- **L3.10.middle.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.middle.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence: n, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.91.0/40[0m[0m[0m[0m | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10305, in lisp_process_map_register / File "lisp.py", line 15174, in add_cache / File "lisp.py", line 11666, in add_cache / File "lisp.py", line 12352, in zero_host_bits / ValueError: negative shift count
  - B: notify False; valid records applied: [False, False]
- **L3.10.last.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.last.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence: n, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.92.0/40[0m[0m[0m[0m | [lisp-traceback.log] File "lisp.py", line 7298, in lisp_parse_packet / File "lisp.py", line 10305, in lisp_process_map_register / File "lisp.py", line 15174, in add_cache / File "lisp.py", line 11666, in add_cache / File "lisp.py", line 12352, in zero_host_bits / ValueError: negative shift count
  - B: notify False; valid records applied: [False, False]
- **L4.6a** (PARITY)
  - A: notify False
  - B: notify False
- **L4.6b** (PARITY)
  - A: notify True; 0 of 40 not resolving
  - B: notify True; 0 of 40 not resolving
- **L4.6c** (PARITY)
  - A: notify True; 100 locators returned
  - B: notify True; 100 locators returned
- **L4.7** (PARITY)
  - A: notify True; resolves to ['192.0.2.2']
  - B: notify True; resolves to ['192.0.2.2']
- **L4.10** (OBSERVATION)
  - A: after restart: not retained ([])
  - B: after restart: retained, resolves to ['192.0.2.2']
- **L4.11** (OBSERVATION)
  - A: not applicable
  - B: listener after the kill: none; register while the host is gone: not answered; request: answered; front restarted with a new host
