# Acceptance report

lispers.net 0.643 (A) vs Ribbit-LISP v0.63-bounded (B), wire network: harness 10.250.250.1, systems 10.250.250.100, 2026-09-28 11:31

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
  - A: reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x01', 'records': [{'ttl': 15, 'action': 1, 'eid': '203.0.113.9', 'mask': 32, 'locs': [], 'iid': 0}]}
  - B: reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x01', 'records': [{'ttl': 15, 'action': 1, 'eid': '203.0.113.9', 'mask': 32, 'locs': [], 'iid': 0}]}
- **L1.1** (PARITY)
  - A: type 4, 76 B, HMAC valid
  - B: type 4, 76 B, HMAC valid
- **L1.2** (PARITY)
  - A: notify none; reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x02', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.12.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}]}
  - B: notify none; reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x02', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.12.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}]}
- **L1.3** (PARITY)
  - A: record {'ttl': 1440, 'action': 0, 'eid': '198.18.13.0', 'mask': 24, 'locs': ['10.250.250.1', '198.51.100.13'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 0, 'eid': '198.18.13.0', 'mask': 24, 'locs': ['10.250.250.1', '198.51.100.13'], 'iid': 0}
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
  - A: notify True, reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x03', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.21.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}]}
  - B: notify True, reply {'nonce': b'\n\xcc\x00L\xbf\xa0\x00\x03', 'records': [{'ttl': 1440, 'action': 0, 'eid': '198.18.21.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}]}
- **L2.2** (PARITY)
  - A: resolves to ['10.250.250.1', '198.51.100.22']
  - B: resolves to ['10.250.250.1', '198.51.100.22']
- **L2.3** (PARITY)
  - A: notify True, resolves to []
  - B: notify True, resolves to []
- **L2.4** (PARITY)
  - A: resolves to ['198.51.100.24']
  - B: resolves to ['198.51.100.24']
- **L2.5** (PARITY)
  - A: resolved before: ['10.250.250.1']; stopped resolving after 45.4 s (TTL 3 s)
  - B: resolved before: ['10.250.250.1']; stopped resolving after 3.3 s (TTL 3 s)
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
  - A: notify False -- STOPPED SERVING after this test; evidence: 110 byte to lisp-core-pkt succeeded / 09/28/26 11:26:56.158: ms: [1mReceive[0m 64 bytes [1mfrom 10.250.250.1[0m 23895, packet: 30000901 0acc004c bfa00001 01010014 3ab10088 3c767ce8 933eab0f 1b638ff4 0a144b0d 00000003 01180000 00000001 c6123400 01640000 00050001 0afafa01 | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:26:56.158 ---------- / traceback.print_last(file=fd) failed
  - B: notify True
- **L3.3** (A FAIL)
  - A: notify True, resolves to []
  - B: notify False, resolves to []
- **L3.4** (A FAIL)
  - A: notify False, resolves to None -- STOPPED SERVING after this test; evidence: h packet value: a26176f7ef619f3bfd3835530d797ba7ab819b574907563899c20a28ae4dd30e / 09/28/26 11:27:15.064: ms:   Authentication [1mfailed[0m for dynamic EID-prefix [1m[92m[0]198.18.54.0/24[0m[0m, key-id 9 / 09/28/26 11:27:15.064: ms: Send Map-Notify to ack Map-Register | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:27:15.064 ---------- / traceback.print_last(file=fd) failed
  - B: notify False, resolves to []
- **L3.5** (A FAIL)
  - A: notify True, resolves to ['10.250.250.1']
  - B: notify False, resolves to []
- **L3.6** (A FAIL)
  - A: byte 20: notify True, resolves []; byte 50: notify True, resolves []
  - B: byte 20: notify False, resolves []; byte 50: notify False, resolves []
- **L3.7** (KNOWN DIFFERENCE)
  - A: notify True, first record resolves to ['10.250.250.1']
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
  - A: STOPPED SERVING -- STOPPED SERVING after this test; evidence: 0a0bf4c00cc0a, key/alg-id: 1/2 (sha1), auth-len: 32, xtr-id: 0x0, site-id: 0 / 09/28/26 11:27:43.410: ms:   EID-record -> record-ttl: 3 mins, rloc-count: 1, action: no-action, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.64.0/40[0m[0m[0m[0m | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:27:43.410 ---------- / traceback.print_last(file=fd) failed
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
  - A: notify valid; record {'ttl': 1440, 'action': 0, 'eid': '2001:db8:acce:18::', 'mask': 64, 'locs': ['10.250.250.1'], 'iid': 0}; unregistered []
  - B: notify valid; record {'ttl': 1440, 'action': 0, 'eid': '2001:db8:acce:18::', 'mask': 64, 'locs': ['10.250.250.1'], 'iid': 0}; unregistered []
- **L2.9** (PARITY)
  - A: notify A False B False; union ['198.51.100.130', '198.51.100.131']; after A replaced its own ['198.51.100.131', '198.51.100.132']
  - B: notify A True B True; union ['198.51.100.130', '198.51.100.131']; after A replaced its own ['198.51.100.131', '198.51.100.132']
- **L2.13** (PARITY)
  - A: registered True -> ['10.250.250.1']; site deleted (good) -> ['10.250.250.1']; restored (good), re-registered True -> ['10.250.250.1']
  - B: registered True -> ['10.250.250.1']; site deleted (good) -> ['10.250.250.1']; restored (good), re-registered True -> ['10.250.250.1']
- **L2.12** (PARITY)
  - A: notify iid7 True, iid0 True; iid 7 -> ['198.51.100.7'] (record iid 7); iid 0 -> ['10.250.250.1']
  - B: notify iid7 True, iid0 True; iid 7 -> ['198.51.100.7'] (record iid 7); iid 0 -> ['10.250.250.1']
- **L1.11** (PARITY)
  - A: {'ms_port': 0, 'etr_port': 46740, 'global': '10.250.250.1', 'private': 'acceptance-etr'}
  - B: {'ms_port': 0, 'etr_port': 39740, 'global': '10.250.250.1', 'private': 'acceptance-etr'}
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
  - A: right key: notify True, resolves ['10.250.250.1']; wrong key: notify False, resolves []; unknown key-id: notify False, resolves []
  - B: right key: notify True, resolves ['10.250.250.1']; wrong key: notify False, resolves []; unknown key-id: notify False, resolves []
- **L2.15.shutdown** (A FAIL)
  - A: notify True; resolves []
  - B: notify False; resolves []
- **L2.15.allowed-rloc** (A FAIL)
  - A: allowed: notify True resolves ['10.250.250.1']; other: notify True resolves []
  - B: allowed: notify True resolves ['10.250.250.1']; other: notify False resolves []
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
  - A: record {'ttl': 1440, 'action': 3, 'eid': '198.27.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 3, 'eid': '198.27.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
- **L2.15.pitr-drop** (PARITY)
  - A: record {'ttl': 1440, 'action': 3, 'eid': '198.28.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 3, 'eid': '198.28.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
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
  - A: record {'ttl': 1440, 'action': 0, 'eid': '198.33.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
  - B: record {'ttl': 1440, 'action': 0, 'eid': '198.33.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}
- **L2.17.set-record-ttl** (PARITY)
  - A: matching {'ttl': 10, 'action': 0, 'eid': '198.30.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}; not matching {'ttl': 1440, 'action': 4, 'eid': '198.30.2.0', 'mask': 24, 'locs': [], 'iid': 0}
  - B: matching {'ttl': 10, 'action': 0, 'eid': '198.30.1.0', 'mask': 24, 'locs': ['10.250.250.1'], 'iid': 0}; not matching {'ttl': 1440, 'action': 4, 'eid': '198.30.2.0', 'mask': 24, 'locs': [], 'iid': 0}
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
  - A: answers to B's register at A's RLOC 10.250.250.1: []
  - B: answers to B's register at A's RLOC 10.250.250.1: [(4, '400000010acc004cbfa0000201020020f89926ee7042228db965dc1140c778c5bd4b624a28644a60943dd1475cab74bc000000030118000000000001c61287000164000000050001c63364870123456789abcdef0123456789ab0b0b0000000000000000')]
- **L2C.1** (PARITY)
  - A: resolves to ['10.250.250.1']
  - B: resolves to ['10.250.250.1']
- **L3.8** (OBSERVATION)
  - A: first notified, replay notified
  - B: first notified, replay notified
- **L3.9** (A FAIL)
  - A: SHA-1/valid: no notify THEN STOPPED SERVING -- STOPPED SERVING after this test; evidence:  124-out-of-124 byte to lisp-core-pkt succeeded / 09/28/26 11:29:28.356: ms: Map-Notify with nonce 0x200a0bf4c00cc0a retry limit reached for ETR [1m[91m[0]198.51.100.135[0m[0m / 09/28/26 11:29:28.356: ms: Dequeue Map-Notify from retransmit queue, key is: 200a0bf4c00cc0a | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:29:24.822 ---------- / traceback.print_last(file=fd) failed
  - B: SHA-1/valid: notified; SHA-1/wrong key: no notify; SHA-1/bad length: no notify; SHA-256/valid: notified; SHA-256/wrong key: no notify; SHA-256/bad length: no notify
- **L3.10.first.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.first.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence: 0a0bf4c00cc0a, key/alg-id: 1/2 (sha1), auth-len: 32, xtr-id: 0x0, site-id: 0 / 09/28/26 11:29:49.432: ms:   EID-record -> record-ttl: 3 mins, rloc-count: 1, action: no-action, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.90.0/40[0m[0m[0m[0m | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:29:49.432 ---------- / traceback.print_last(file=fd) failed
  - B: notify False; valid records applied: [False, False]
- **L3.10.middle.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.middle.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence:  09/28/26 11:30:14.293: ms:     Changed RLOC-set, Map-Notifying old RLOC-set / 09/28/26 11:30:14.293: ms:   EID-record -> record-ttl: 3 mins, rloc-count: 1, action: no-action, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.91.0/40[0m[0m[0m[0m | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:30:14.294 ---------- / traceback.print_last(file=fd) failed
  - B: notify False; valid records applied: [False, False]
- **L3.10.last.unauthorized** (KNOWN DIFFERENCE)
  - A: notify True; valid records applied: [True, True]
  - B: notify False; valid records applied: [False, False]
- **L3.10.last.invalid** (A FAIL)
  - A: notify False; valid records applied: [False, False] -- STOPPED SERVING after this test; evidence:  09/28/26 11:30:39.177: ms:     Changed RLOC-set, Map-Notifying old RLOC-set / 09/28/26 11:30:39.177: ms:   EID-record -> record-ttl: 3 mins, rloc-count: 1, action: no-action, non-auth, map-version: 0, afi: 1, [iid]eid/ml: [1m[92m[1m[92m[0]198.18.92.0/40[0m[0m[0m[0m | [lisp-traceback.log] ---------- Exception occurred: 09/28/26 11:30:39.177 ---------- / traceback.print_last(file=fd) failed
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
  - A: notify True; resolves to ['10.250.250.1']
  - B: notify True; resolves to ['10.250.250.1']
- **L4.10** (OBSERVATION)
  - A: after restart: not retained ([])
  - B: after restart: retained, resolves to ['10.250.250.1']
- **L4.11** (OBSERVATION)
  - A: not applicable
  - B: not applicable


# Performance (same run, same build) -- lispers.net 0.643 vs Ribbit-LISP v0.63-bounded

perf v2 (package v0.63-bounded). Load generator 10.250.250.1; monitors: control, ram; warmup 5s, measured 20s per run.

## Throughput and latency (load generator)

| system | workload | clients | state | ops/s | p50 ms | p95 ms | p99 ms | errors | unanswered |
|---|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 357 | 2.84 | 3.31 | 3.96 | 0 | 0 |
| lispers.net | reg | 4 | 1000 | 793 | 4.84 | 6.06 | 6.72 | 0 | 0 |
| lispers.net | reg | 16 | 1000 | 769 | 20.23 | 24.95 | 28.68 | 0 | 0 |
| lispers.net | reg | 64 | 1000 | 718 | 85.21 | 149.33 | 203.02 | 0 | 0 |
| lispers.net | req | 1 | 1000 | 334 | 2.91 | 3.45 | 4.39 | 0 | 0 |
| lispers.net | req | 4 | 1000 | 516 | 7.61 | 9.44 | 10.69 | 0 | 0 |
| lispers.net | req | 16 | 1000 | 484 | 31.87 | 41.22 | 44.93 | 0 | 0 |
| lispers.net | req | 64 | 1000 | 484 | 130.20 | 155.14 | 168.19 | 0 | 0 |
| lispers.net | mixed | 1 | 1000 | 334 | 2.97 | 3.53 | 4.35 | 0 | 0 |
| lispers.net | mixed | 4 | 1000 | 491 | 8.06 | 9.30 | 10.91 | 0 | 0 |
| lispers.net | mixed | 16 | 1000 | 491 | 32.79 | 36.41 | 39.50 | 0 | 0 |
| lispers.net | mixed | 64 | 1000 | 495 | 132.27 | 142.80 | 153.47 | 0 | 0 |
| lispers.net | req-host | 16 | 1 | 938 | 16.15 | 22.28 | 26.50 | 0 | 0 |
| lispers.net | req-host | 16 | 100 | 877 | 17.87 | 20.47 | 23.51 | 0 | 0 |
| lispers.net | req-host | 16 | 1000 | 508 | 29.81 | 40.77 | 46.46 | 0 | 0 |
| lispers.net | req-host | 16 | 10000 | 93 | 175.18 | 206.91 | 244.12 | 0 | 0 |
| lispers.net | req-nested | 16 | 1 | 924 | 16.59 | 22.10 | 25.73 | 0 | 0 |
| lispers.net | req-nested | 16 | 100 | 821 | 19.07 | 23.66 | 28.29 | 0 | 0 |
| lispers.net | req-nested | 16 | 1000 | 486 | 31.06 | 42.17 | 46.83 | 0 | 0 |
| lispers.net | req-nested | 16 | 10000 | 114 | 136.90 | 181.30 | 204.74 | 0 | 0 |
| ribbit | reg | 1 | 1000 | 39 | 25.10 | 29.18 | 34.45 | 0 | 0 |
| ribbit | reg | 4 | 1000 | 161 | 24.39 | 27.52 | 32.85 | 0 | 0 |
| ribbit | reg | 16 | 1000 | 591 | 26.28 | 32.71 | 45.18 | 0 | 0 |
| ribbit | reg | 64 | 1000 | 1565 | 38.06 | 63.01 | 74.59 | 0 | 0 |
| ribbit | req | 1 | 1000 | 891 | 1.02 | 1.55 | 2.10 | 0 | 0 |
| ribbit | req | 4 | 1000 | 3014 | 1.21 | 1.97 | 2.51 | 0 | 0 |
| ribbit | req | 16 | 1000 | 9398 | 1.61 | 2.44 | 2.99 | 0 | 0 |
| ribbit | req | 64 | 1000 | 20407 | 2.94 | 4.51 | 5.89 | 0 | 0 |
| ribbit | mixed | 1 | 1000 | 161 | 1.26 | 25.96 | 27.73 | 0 | 0 |
| ribbit | mixed | 4 | 1000 | 581 | 1.36 | 25.66 | 30.45 | 0 | 1 |
| ribbit | mixed | 16 | 1000 | 2326 | 1.89 | 27.75 | 32.74 | 0 | 0 |
| ribbit | mixed | 64 | 1000 | 5125 | 2.67 | 62.24 | 83.08 | 0 | 0 |
| ribbit | req-host | 16 | 1 | 9210 | 1.63 | 2.54 | 3.15 | 0 | 0 |
| ribbit | req-host | 16 | 100 | 9213 | 1.62 | 2.55 | 3.23 | 0 | 0 |
| ribbit | req-host | 16 | 1000 | 9044 | 1.66 | 2.58 | 3.20 | 0 | 0 |
| ribbit | req-host | 16 | 10000 | 9123 | 1.65 | 2.53 | 3.12 | 0 | 0 |
| ribbit | req-nested | 16 | 1 | 9346 | 1.60 | 2.47 | 3.06 | 0 | 0 |
| ribbit | req-nested | 16 | 100 | 9272 | 1.61 | 2.53 | 3.15 | 0 | 0 |
| ribbit | req-nested | 16 | 1000 | 9063 | 1.66 | 2.52 | 3.07 | 0 | 0 |
| ribbit | req-nested | 16 | 10000 | 9164 | 1.63 | 2.53 | 3.17 | 0 | 0 |

## Work per successful operation -- CPU-seconds per 1M operations, by component

control = the systems' machine (lispers.net's lisp-* processes, or Ribbit's front); shared memory = the RAM server's machine; load = the generator.

| system | workload | clients | state | control: lispers.net | control: ribbit-front | shared memory: ram-server | system total | load-gen |
|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 1772.9 | 0.0 | 12.6 | 1772.9 | 45.7 |
| lispers.net | reg | 4 | 1000 | 1552.6 | 0.0 | 4.4 | 1552.6 | 51.6 |
| lispers.net | reg | 16 | 1000 | 1587.0 | 0.0 | 4.6 | 1587.0 | 65.4 |
| lispers.net | reg | 64 | 1000 | 1650.3 | 0.0 | 4.2 | 1650.3 | 69.3 |
| lispers.net | req | 1 | 1000 | 2022.2 | 0.0 | 10.5 | 2022.2 | 33.5 |
| lispers.net | req | 4 | 1000 | 2399.6 | 0.0 | 4.8 | 2399.6 | 45.1 |
| lispers.net | req | 16 | 1000 | 2559.9 | 0.0 | 6.2 | 2559.9 | 60.9 |
| lispers.net | req | 64 | 1000 | 2585.8 | 0.0 | 7.2 | 2585.8 | 64.9 |
| lispers.net | mixed | 1 | 1000 | 2029.1 | 0.0 | 7.5 | 2029.1 | 42.7 |
| lispers.net | mixed | 4 | 1000 | 2511.7 | 0.0 | 8.1 | 2511.7 | 68.8 |
| lispers.net | mixed | 16 | 1000 | 2514.8 | 0.0 | 9.2 | 2514.8 | 62.3 |
| lispers.net | mixed | 64 | 1000 | 2510.4 | 0.0 | 8.1 | 2510.4 | 55.4 |
| lispers.net | req-host | 16 | 1 | 1579.3 | 0.0 | 2.7 | 1579.3 | 48.4 |
| lispers.net | req-host | 16 | 100 | 1653.6 | 0.0 | 3.4 | 1653.6 | 45.7 |
| lispers.net | req-host | 16 | 1000 | 2458.2 | 0.0 | 4.9 | 2458.2 | 56.9 |
| lispers.net | req-host | 16 | 10000 | 11373.2 | 0.0 | 37.7 | 11373.2 | 72.3 |
| lispers.net | req-nested | 16 | 1 | 1617.5 | 0.0 | 3.8 | 1617.5 | 53.7 |
| lispers.net | req-nested | 16 | 100 | 1751.7 | 0.0 | 3.7 | 1751.7 | 53.3 |
| lispers.net | req-nested | 16 | 1000 | 2574.8 | 0.0 | 5.1 | 2574.8 | 63.6 |
| lispers.net | req-nested | 16 | 10000 | 9394.2 | 0.0 | 30.7 | 9394.2 | 69.7 |
| ribbit | reg | 1 | 1000 | 0.0 | 2340.2 | 3503.8 | 5844.0 | 116.2 |
| ribbit | reg | 4 | 1000 | 0.0 | 1067.0 | 1296.5 | 2363.5 | 76.5 |
| ribbit | reg | 16 | 1000 | 0.0 | 748.2 | 997.5 | 1745.7 | 85.1 |
| ribbit | reg | 64 | 1000 | 0.0 | 679.9 | 815.1 | 1495.0 | 74.2 |
| ribbit | req | 1 | 1000 | 0.0 | 154.9 | 3.9 | 158.8 | 28.5 |
| ribbit | req | 4 | 1000 | 0.0 | 77.3 | 1.2 | 78.5 | 31.7 |
| ribbit | req | 16 | 1000 | 0.0 | 43.2 | 0.4 | 43.6 | 47.4 |
| ribbit | req | 64 | 1000 | 0.0 | 29.1 | 0.2 | 29.3 | 72.4 |
| ribbit | mixed | 1 | 1000 | 0.0 | 586.8 | 664.4 | 1251.2 | 47.9 |
| ribbit | mixed | 4 | 1000 | 0.0 | 314.0 | 327.7 | 641.7 | 43.6 |
| ribbit | mixed | 16 | 1000 | 0.0 | 224.6 | 227.2 | 451.8 | 48.0 |
| ribbit | mixed | 64 | 1000 | 0.0 | 189.9 | 167.6 | 357.6 | 61.3 |
| ribbit | req-host | 16 | 1 | 0.0 | 43.7 | 0.4 | 44.1 | 52.2 |
| ribbit | req-host | 16 | 100 | 0.0 | 42.2 | 0.3 | 42.6 | 49.9 |
| ribbit | req-host | 16 | 1000 | 0.0 | 45.6 | 0.3 | 45.9 | 50.8 |
| ribbit | req-host | 16 | 10000 | 0.0 | 46.8 | 0.4 | 47.2 | 48.9 |
| ribbit | req-nested | 16 | 1 | 0.0 | 41.1 | 0.3 | 41.5 | 55.0 |
| ribbit | req-nested | 16 | 100 | 0.0 | 41.8 | 0.4 | 42.2 | 57.3 |
| ribbit | req-nested | 16 | 1000 | 0.0 | 45.8 | 0.5 | 46.3 | 51.4 |
| ribbit | req-nested | 16 | 10000 | 0.0 | 46.4 | 0.6 | 47.0 | 56.8 |

## Machines -- CPU utilization and network per operation

| system | workload | clients | state | control busy % | shared-memory busy % | load busy % | control B/op | shared-memory B/op | control pk/op | shared-memory pk/op | ram-server RSS MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 17.0 | 5.5 | 5.4 | 279 | 168 | 2.02 | 1.05 | 51 |
| lispers.net | reg | 4 | 1000 | 32.4 | 6.1 | 3.7 | 277 | 74 | 2.01 | 0.45 | 51 |
| lispers.net | reg | 16 | 1000 | 32.3 | 3.8 | 7.5 | 277 | 59 | 2.01 | 0.37 | 51 |
| lispers.net | reg | 64 | 1000 | 31.1 | 3.2 | 5.2 | 276 | 84 | 2.00 | 0.51 | 51 |
| lispers.net | req | 1 | 1000 | 18.2 | 2.8 | 3.4 | 215 | 163 | 2.02 | 1.05 | 51 |
| lispers.net | req | 4 | 1000 | 33.5 | 2.3 | 3.2 | 214 | 83 | 2.02 | 0.53 | 51 |
| lispers.net | req | 16 | 1000 | 33.1 | 3.0 | 7.8 | 214 | 139 | 2.02 | 0.80 | 51 |
| lispers.net | req | 64 | 1000 | 33.0 | 3.9 | 9.0 | 213 | 158 | 2.01 | 0.89 | 51 |
| lispers.net | mixed | 1 | 1000 | 18.4 | 3.9 | 4.8 | 228 | 225 | 2.03 | 1.26 | 51 |
| lispers.net | mixed | 4 | 1000 | 32.9 | 4.2 | 11.2 | 227 | 121 | 2.02 | 0.73 | 51 |
| lispers.net | mixed | 16 | 1000 | 32.5 | 4.3 | 6.0 | 227 | 184 | 2.02 | 1.07 | 51 |
| lispers.net | mixed | 64 | 1000 | 32.7 | 4.9 | 3.6 | 226 | 94 | 2.01 | 0.56 | 51 |
| lispers.net | req-host | 16 | 1 | 39.2 | 2.6 | 4.7 | 213 | 40 | 2.01 | 0.27 | 51 |
| lispers.net | req-host | 16 | 100 | 39.0 | 2.8 | 3.9 | 213 | 48 | 2.01 | 0.30 | 51 |
| lispers.net | req-host | 16 | 1000 | 33.2 | 3.2 | 5.3 | 214 | 117 | 2.02 | 0.73 | 51 |
| lispers.net | req-host | 16 | 10000 | 27.5 | 5.6 | 7.1 | 225 | 745 | 2.10 | 4.37 | 51 |
| lispers.net | req-nested | 16 | 1 | 39.7 | 3.3 | 4.5 | 214 | 46 | 2.01 | 0.28 | 52 |
| lispers.net | req-nested | 16 | 100 | 38.6 | 3.1 | 4.5 | 213 | 50 | 2.01 | 0.32 | 52 |
| lispers.net | req-nested | 16 | 1000 | 33.4 | 4.0 | 6.1 | 215 | 106 | 2.02 | 0.64 | 52 |
| lispers.net | req-nested | 16 | 10000 | 28.2 | 3.1 | 3.3 | 220 | 408 | 2.06 | 2.46 | 52 |
| ribbit | reg | 1 | 1000 | 4.4 | 15.2 | 14.9 | 3394 | 5367 | 18.52 | 30.24 | 56 |
| ribbit | reg | 4 | 1000 | 6.6 | 15.4 | 6.5 | 2173 | 2266 | 9.41 | 10.14 | 56 |
| ribbit | reg | 16 | 1000 | 14.4 | 35.9 | 14.4 | 1804 | 1612 | 6.15 | 4.88 | 57 |
| ribbit | reg | 64 | 1000 | 31.4 | 68.9 | 11.8 | 1725 | 1475 | 4.39 | 2.53 | 59 |
| ribbit | req | 1 | 1000 | 4.7 | 3.5 | 3.8 | 213 | 54 | 2.01 | 0.34 | 59 |
| ribbit | req | 4 | 1000 | 9.8 | 2.9 | 5.3 | 212 | 15 | 2.00 | 0.09 | 59 |
| ribbit | req | 16 | 1000 | 14.2 | 2.4 | 13.8 | 212 | 4 | 2.00 | 0.03 | 59 |
| ribbit | req | 64 | 1000 | 18.3 | 3.6 | 43.1 | 212 | 2 | 2.00 | 0.01 | 59 |
| ribbit | mixed | 1 | 1000 | 4.3 | 10.1 | 6.4 | 862 | 955 | 5.39 | 5.61 | 59 |
| ribbit | mixed | 4 | 1000 | 7.8 | 16.7 | 4.4 | 699 | 518 | 3.76 | 2.18 | 59 |
| ribbit | mixed | 16 | 1000 | 17.8 | 31.2 | 8.8 | 604 | 389 | 3.02 | 1.14 | 59 |
| ribbit | mixed | 64 | 1000 | 29.4 | 47.9 | 16.5 | 544 | 322 | 2.55 | 0.56 | 59 |
| ribbit | req-host | 16 | 1 | 13.4 | 3.5 | 23.2 | 212 | 9 | 2.00 | 0.05 | 61 |
| ribbit | req-host | 16 | 100 | 13.0 | 3.5 | 20.3 | 212 | 9 | 2.00 | 0.05 | 64 |
| ribbit | req-host | 16 | 1000 | 14.0 | 2.6 | 21.1 | 212 | 10 | 2.00 | 0.06 | 66 |
| ribbit | req-host | 16 | 10000 | 14.3 | 6.2 | 16.6 | 212 | 5 | 2.00 | 0.03 | 73 |
| ribbit | req-nested | 16 | 1 | 12.5 | 3.7 | 16.5 | 212 | 4 | 2.00 | 0.03 | 92 |
| ribbit | req-nested | 16 | 100 | 12.9 | 3.8 | 21.3 | 212 | 7 | 2.00 | 0.04 | 108 |
| ribbit | req-nested | 16 | 1000 | 14.4 | 4.1 | 14.8 | 211 | 7 | 1.99 | 0.04 | 125 |
| ribbit | req-nested | 16 | 10000 | 14.5 | 6.2 | 20.8 | 212 | 6 | 2.00 | 0.04 | 145 |
