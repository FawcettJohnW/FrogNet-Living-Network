# Findings about lispers.net from the Ribbit-LISP conversion

Things the conversion found in lispers.net's own behaviour. Each is sourced to the file and line, and was read from
the code, not assumed. Nothing here changes lispers.net. For each item we offer to file an issue and/or a pull request
if Dino wants one.

## 1. Multi-record Map-Register is processed record by record, not all-or-nothing

lisp.py `lisp_process_map_register`, the record loop at about lines 10262–10420:

- A record whose EID-prefix has no site: "Site not found", then `continue`. That record is skipped, and the records
  around it are still processed.
- A record that fails authentication (checked per record, with that record's site key): `continue`, the same effect.
- A record or RLOC-record that fails to decode: `return`. Records processed before it stay applied.

The consequence is that a Map-Register carrying several records can be partly applied: the earlier records are
registered and a later one is refused, or the packet is truncated partway through.

Ribbit-LISP applies a multi-record Map-Register all-or-nothing. It validates every record, including authorization
and decoding, before applying any, and since v0.41 it authorizes against one snapshot of site policy. This is a
deliberate, recorded difference. Offer: an issue, and if wanted a pull request that validates all records before
applying any.

## 2. The Map-Notify-Ack carries EID-records with a record count of 0

lisp.py `lisp_send_map_notify_ack` (line 9667): the ack copies the Map-Notify's EID-records but sets `record_count =
0`. RFC 9301 gives the Map-Notify-Ack the Map-Notify's format. Ribbit-LISP matches lispers.net byte for byte here
(v0.39), for interoperability, and records the difference. Offer: an issue and/or a one-line pull request.

## 3. Deregistration is sent only for dynamic EIDs

lisp-etr.py: a TTL-0 Map-Register is built only when a dynamic EID goes away (`lisp_build_map_register(..., 0, eid,
...)`, line 1869). Removing a static `database-mapping` sends nothing, so the Map-Server keeps the registration until
it times out. Recorded for discussion; this may be intended.

## (Ribbit side, not Dino's) Read-your-own-write on the held resolver view -- found 2026-09-26, open
`registration.put` writes RAM and returns; `resolution.get` in the same process answers from the held resolver view,
which applies that write only when its held read wakes. A put followed at once by a get can miss the write.
Reproduced with a site configured, 1-second TTL, put then get: 9 of 40 misses on v0.52, 5 of 40 on v0.51 -- present
before the engines went into the session. It is what makes `test_ttl_seconds_encoding_expires_without_reaper` fail now
and then (it reads straight after the put; the other registration tests call `resolver.wait` first). Not yet ruled:
whether a mutating call on a participant waits until its own held views have applied it before returning.

## lispers.net 0.643 on Python 3: an HMAC-SHA-1-96 Map-Register kills the Map-Server process -- found 2026-09-27
Reproduced against the released build (build/latest-py3/lispers.net.tgz, installed as build/Dockerfile does) on x86-64:
one authenticated Map-Register with alg-id 1 (HMAC-SHA-1-96), key-id 1, sent to UDP 4342 of a Map-Server whose site
has that key. lisp-ms raises and exits; no Map-Notify, and every later Map-Register is lost until lispers.net is
restarted:
    File "lisp-ms.py", line 1524, in <module>
    File "lisp.py", line 7298, in lisp_parse_packet
    File "lisp.py", line 10230, in lisp_process_map_register
    File "lisp.py", line 4165, in decode
    File "lisp.py", line 6773, in lisp_concat_auth_data
    File "lisp.py", line 11592, in byte_swap_64
    TypeError: unsupported operand type(s) for &: 'bytes' and 'int'
Cause: for alg-id 1, decode() unpacks "QQI" and sets auth4 = b"" (bytes); lisp_concat_auth_data tests
`if (auth4 != "")` -- under Python 3, b"" != "" is True -- so byte_swap_64(b"") runs on bytes. The same register
with HMAC-SHA-256-128 (alg-id 2) is accepted and Map-Notified in about 1 ms. Suggested fix: compare with b"" (or test
len()) for auth1..auth4, as the packing produces bytes in Python 3. Also: one malformed-or-unexpected packet ends the
Map-Server process -- the main receive loop has no per-packet guard.

## Acceptance test findings on lispers.net 0.643 (2026-09-27; tools/acceptance.py; ACCEPTANCE-REPORT.md)
Each was found by sending the same Map-Register / ECM Map-Request to lispers.net and to Ribbit-LISP, the site keyed
(key-id 1, HMAC-SHA-256 unless stated), force-proxy-reply on.
- L3.2  HMAC-SHA-1-96 with the correct key: lisp-ms exits (the b"" != "" bug above); nothing is registered after it.
- L3.4  a Map-Register with a key-id the site does not have: lisp-ms exits (traceback from lisp_process_map_register).
- L4.4  a record with an unknown AFI, or an IPv4 mask > 32: lisp-ms exits -- also when that record is the first,
        the middle or the last of a three-record Map-Register (L3.10).
        In all three, one datagram ends the Map-Server until restart -- the receive loop has no per-packet guard.
- L3.3  wrong key: the registration is NOT applied, but a Map-Notify comes back anyway.
- L3.6  one bit flipped in the authentication data, or in the body: not applied, Map-Notify comes back anyway.
- L3.5  NO authentication (alg-id 0) for a prefix in a keyed site: accepted, applied, and Map-Notified.
- L4.10 registrations do not survive a restart of lispers.net (for discussion).
- L2.5  a 3-second registration keeps resolving for up to a minute (44 s measured): registrations are expired by a sweep
        every LISP_SITE_TIMEOUT_CHECK_INTERVAL (60 s), not at their TTL. (For discussion, not necessarily a defect.)
- L3.7  a two-record Map-Register whose second record is unauthorized applies the first (per-record semantics; Ribbit
        applies none -- a deliberate difference, not a defect on either side).
- (configuration) lisp.config whose first line is "# lispers.net lisp.config file" -- accepted at start -- is destroyed
  the first time the API changes the configuration: lisp_write_last_changed_date keeps the header up to its ':' and
  there is none, so the line becomes just the date; the next start then declares the file corrupt and runs no roles.
  lispers.net's own example header ("..., last changed: <date>") has the ':'.

- L2.14 a Map-Register whose locator is a JSON LCAF record, encoded by lispers.net's own lisp_rloc_record.encode
        (AFI-list with 198.51.100.9 and a type-14 JSON LCAF): the map-server logs "Authentication passed" and then
        neither registers it nor Map-Notifies; geo-coordinates, explicit-locator-path and replication-list-entry
        locators encoded the same way are registered and returned byte for byte.

- Crypto-EIDs (lisp eid-crypto-hash) cannot work end to end on this build: lisp_verify_cga_sig reads the signature
  from a JSON locator in the crypto-EID's Map-Register, and lisp_lookup_public_key reads the public key from a JSON
  locator registered under "hash-<hex>" -- and the map-server drops Map-Registers that carry a JSON locator (L2.14.json).
- DDT negative prefixes (lisp_find_negative_mask_len), now observed: as a map-server-only DDT authority for
  198.18.0.0/15, lispers.net answers a DDT Map-Request for 198.19.7.9 (inside the authoritative prefix, in no site; a
  site 198.19.0.0/24 exists) with NOT-AUTHORITATIVE, TTL 0, prefix 198.19.0.0/21 -- a prefix that covers the site
  198.19.0.0/24. The mask kept is the INDEX of the first bit where the EID differs from the site (21); one more (/22,
  198.19.4.0/22) excludes it. Also: the action is NOT-AUTHORITATIVE inside an authoritative prefix, where
  DELEGATION-HOLE would be expected. Ribbit-LISP reproduces both for parity.

- L2.16 multicast (S,G) EIDs: lcaf_encode_sg / lcaf_decode_sg pack the Multicast Info LCAF with
  struct "BBBBHIHBB" -- NATIVE byte order and alignment (no "!"), so Python inserts two pad bytes after the 16-bit
  length to align the 32-bit instance-id (struct.calcsize gives 16, not 14). Every (S,G) EID lispers.net writes has two
  bytes RFC 8060 does not have, and an RFC 8060 (S,G) sent to it is misread ("unknown-afi:50717", site not found).
  lispers.net-to-lispers.net works because both ends share the mistake (L2.16b). Suggested fix: "!BBBBHIHBB" with the
  htons/htonl calls dropped. Ribbit-LISP reads both layouts and answers in the one it was asked in.

- The JSON LCAF (type 14) lispers.net encodes states a length 2 bytes longer than what follows: for a 27-byte JSON
  string the LCAF length field is 33 (json length + 6) while 31 bytes follow (2 length + 27 + 2). Seen in its own
  lisp_rloc_record.encode output and in its policy set-json-name answers (L2.18.json). A decoder that trusts the length
  reads 2 bytes past the LCAF -- a plausible cause of the dropped JSON-locator Map-Registers above (not yet proven).

## From the first run across a network (Pi -> AI-Host, broker on streamingfrog, 2026-09-28)
Every result of the loopback run reproduced. Reading the evidence closely also showed:
- L2.15.shutdown / L2.15.allowed-rloc: a register to a site configured `shutdown = yes`, and a register whose RLOC is
  not in the site's allowed-rloc set, are rejected (nothing resolves) -- but lispers.net still sends a Map-Notify
  acknowledging each. The same pattern as L3.3 / L3.6.
- L2.9b merged Map-Notify: for a merge register lispers.net builds a Map-Notify with the merged RLOC-set and "sends"
  it to every registered xTR, but the first transmission never reaches lisp-core (lisp-core logs no IPC for it); only
  retransmissions go out, 2 s later -- and the retransmit queue is keyed by nonce, so of the Map-Notifies for one
  register only the one to the LAST xTR is ever retransmitted. The other xTRs of the merged set never hear of it.
  (Observed in lisp-ms.log / lisp-core.log.)
