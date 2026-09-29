# v0.24 multi-record Map-Register — GREEN core slice

- v0.23 red retained: two-record Register failed solely on one-record parser restriction.
- IPv4 and IPv6 Map-Register decoders now parse all Record Count records and reject malformed/trailing packets before mutation.
- Wire registration performs a validation-only pass over every record before applying any record.
- Packet HMAC is verified once; authenticated multi-record packets require a single consistent site key across their records.
- Each mapping record is then applied independently through the existing registration contract.
- Map-Notify behavior follows RFC 9301: one Notify copies all records from the accepted Register and recomputes per-message authentication.
- New tests cover mixed prefix lengths, distinct registrations, malformed later record atomic rejection, authorization failure atomic rejection, refresh rejection, multi-record withdrawal, and authenticated two-record Notify.
- Existing refresh, merge/non-merge, TTL-0/source authorization, governance, and database_mapping code retained.
- Existing single-record Dino-derived Map-Register and Map-Reply fixtures remain byte-for-byte identical.
- Local conformance: 37 PASS, 1 explicit SKIP.
- Clean FNW1 conformance: 37 PASS, 1 explicit SKIP.
- Independent-process held resolver/site, DDT, and map-cache tests PASS.
- Held expiry test no longer sleeps; it waits for observed resolution state.
