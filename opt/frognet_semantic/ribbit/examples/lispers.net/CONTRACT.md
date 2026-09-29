# Ribbit-LISP executable contract

## Authority

1. Normative LISP requirements and agreed acceptance tests define required behavior.
2. `lispers.net` is the control implementation and a source of edge cases, not the specification.
3. A control behavior not supported by the contract is characterization until promoted deliberately.
4. Tests assert externally observable behavior. They must not require Dino's cache, IPC, lock, process, timer, or storage architecture.

## Conformance families

| Family | First executable coverage | Next topology coverage |
|---|---|---|
| System/API | system shape, authentication/error behavior | concurrent independent queries |
| Map cache / resolution | list/get shape, exact EID query | miss→request→reply→forward; expiry |
| ETR database mappings | CRUD round trip | registration and re-registration |
| Map resolvers | CRUD round trip | resolver failover |
| Map servers | CRUD round trip | registration auth / refresh |
| RLOC sets | record construction and persistence | priority/weight selection |
| Reachability | — | probe loss/recovery and forwarding transition |
| Site cache | list/get shape | registration visibility/expiry |
| DDT | configuration CRUD | iterative referral/delegation resolution |
| Pub/Sub | — | subscribe/change/notification behavior |
| Mobility | — | mapping movement and traffic continuity |
| NAT traversal | configuration | observed NAT traversal behavior |
| Multicast | — | join/forward behavior |
| Data plane | — | encapsulate/decapsulate and sustained forwarding |

The `—` entries are intentionally not fabricated. They require a runnable topology or a normative packet-level fixture.

## First green-field executable slice

The first Ribbit-LISP implementation covers an implementation-neutral subset:

- system presence/status shape
- map-resolver add/get/delete
- map-cache add/list/get/delete
- IPv4/IPv6 prefix parsing in the domain engine
- longest-prefix selection independent of control implementation internals
- instance-ID isolation for otherwise identical EID prefixes
- database-mapping add/delete (storage semantics present; wire registration is not yet claimed)

Passing these tests does **not** establish Map-Request/Map-Reply wire conformance, registration,
expiry, probing, forwarding, DDT, Pub/Sub, mobility, NAT traversal, or multicast. Those remain open.

## Registration -> resolution slice (v0.4)
Source-derived control requirements implemented/tested so far:
- A valid registration makes its RLOC set resolvable for the registered EID prefix.
- A later non-refresh registration replaces that registration's RLOC set.
- TTL 0 deregisters when the registration source is one of the registered RLOCs.
- TTL 0 from a source outside the registered RLOC set is ignored.
- A refresh cannot change an already-registered RLOC set.
- Merge registrations from independent xTR IDs for the same site/prefix contribute to the resolved RLOC set.
- Replacing one merged xTR registration changes only that xTR's contribution.

Not yet claimed complete: authentication/CGA, allowed-RLOC policy, AMS, site-id change behavior, Map-Notify/subscribers, registration expiry, multicast/RLE merge, proxy-reply policy, wire Map-Register/Map-Reply encoding.

## Registration lifetime (control-derived)
- T-bit requested: EID record TTL is minutes unless bit 31 is set, in which case low 31 bits are seconds.
- T-bit not requested: control registration timeout is 180 seconds.
- Refresh updates the registration's last-seen lifetime without permitting locator-set change.
- Expired registrations must not resolve.

## Wire slice now executable
- IPv4/IPv6, one EID record per Map-Register/Map-Request.
- Ordinary unicast EID/RLOC AFIs.
- Multiple locator records.
- Positive Map-Reply preserving request nonce and locator attributes.
- Unmapped IPv4 request returns authoritative, zero-locator, native-forward negative Map-Reply with 15-minute TTL, matching the control's site-not-found behavior.

## Site authorization
- A registration for an unknown/unconfigured site is not accepted.
- Exact configured site prefix is accepted subject to authentication/policy.
- A more-specific is accepted only when the configured parent permits accept-more-specifics.
- Authorization failure must not mutate registration truth.

## Map-Register authentication / Map-Notify
- Algorithms exercised: SHA-1 (20-byte HMAC) and SHA-256 (32-byte HMAC), matching control constants and zero-auth-field hashing procedure.
- Site key-id selects the configured password.
- Authentication failure occurs before registration state mutation.
- Want-Map-Notify accepted registration produces Map-Notify preserving nonce/EID records and authenticating with the site key.

## DDT state slice
- Delegations are independently addressed truths by IID/group/prefix.
- Lookup is longest-prefix over the requested EID and returns the selected referral set.
- IID namespaces are isolated.
- Wire Map-Referral and recursive Map-Resolver behavior are not yet claimed.

## Security boundary and lispers.net authentication framing (v0.7)
- Site existence and authorization policy are shared truths. HMAC passwords are not.
- A site cell may publish prefix, accept-more-specifics policy, and key-id; it MUST NOT publish key material.
- The map-server obtains HMAC secrets from map-server-local/private configuration. The FNW1-backed implementation therefore authenticates without reading a password from shared RAM.
- TTL-zero deregistration is source-authorized. Wire ingress MUST supply the packet sender address to registration processing; an absent sender is not authorization. A source outside the current registered RLOC set is ignored.
- The executable authentication framing in this checkpoint follows the lispers.net oracle: byte 12 is key-id, byte 13 selects the algorithm, the authentication length is the control's full digest length (20 bytes SHA-1, 32 bytes SHA-256), and HMAC is computed with the authentication field zeroed. This is a control-compatibility claim, NOT a claim of generic RFC 6833 interoperability.
- Nonce handling is likewise control-compatible: the current codec copies the eight nonce bytes in and emits those same native-order bytes out. Byte preservation is tested; no host-independent numeric nonce interpretation is claimed yet.

## Registration addressing / packet-path reads (v0.7)
- Registration and site-policy RAM variables are partitioned by IID, group, and prefix length. A resolver/authorizer no longer reads the complete IID/group table and filters unrelated prefix lengths.
- This checkpoint still performs RAM reads on a Map-Request. The held-read-maintained resolver view required by Programming Ribbit doctrine is NOT YET claimed. The per-prefix-length change removes whole-table scans but is not the final packet-path architecture.

## Review corrections carried into v0.7
- `site_cache.list` is withdrawn from Ribbit capabilities until implemented; its test skips honestly.
- Non-merge registration replaces prior merged xTR contributions for the same prefix.
- Conformance assertions compare parsed values rather than substrings of serialized JSON.
- Registration expiry has executable tests that cross the expiry deadline and verify disappearance without a reaper.

## Negative Map-Reply wire contract (v0.27)
- RFC 9301 is authoritative for this slice; no negative wire format is synthesized from memory.
- A Map-Request miss outside any configured site authority returns a zero-locator Natively-Forward negative Map-Reply with a 15-minute TTL.
- A Map-Request that matches a configured authoritative site but has no active registration returns a zero-locator Natively-Forward negative Map-Reply for the matching configured site prefix with a 1-minute TTL.
- The behavior is implemented for IPv4 and IPv6.
- Site-authority classification uses the held site-policy participant. A warmed request does not add a direct RAM read.
- Policy-denied and authentication-failure negative actions are defined by RFC 9301 but are not claimed by this checkpoint because the current Map-Request ingress surface does not yet carry those policy/authentication failure conditions.

## ETR direct Map-Reply (v0.33)
- `wire.etr_request4`: answers an IPv4 Map-Request from the ETR's own held database mapping (best-matching
  EID-prefix), never from Map-Server registration truth or the ITR map-cache. One record, authoritative,
  No-Action, record TTL 1440, R bit per locator (control: lisp_etr_process_map_request / lisp_build_map_reply).
- `wire.etr_request6` (v0.34): the IPv6 counterpart, same contract.
- Not yet claimed: ETR miss behaviour; L bit; ETR Map-Register generation from database mapping.
- The local backend keeps ETR database mappings (`etr_db`) separate from Map-Server registrations (`db`).

## Observed convergence (v0.35)
- `map_cache.wait` optional `rloc` (present and carrying this locator) and optional `timeout_s` ("timeout" if
  not observed in time). Other `*.wait` operations are unchanged; the same extension applies if a test needs to
  observe a replacement rather than a presence.

## ETR Map-Register (v0.36)
- `wire.etr_register4`: the ETR's Map-Register for one map-server, built from its held database mapping
  (IID 0, IPv4); byte-identical to the control's lisp_build_map_register for the same inputs.
- Map-Server decoders accept the xTR-ID/site-ID trailer (I bit).
- Not yet claimed: periodic sending, map-server configuration as truth, IPv6/IID records, packet splitting.

## Map-server configuration (v0.37)
- `etr_map_server.add/get/delete/wait`: ETR-owned, published as public policy only (`etr-map-server` cells);
  password process-private. `wire.etr_register4 map_server=<address>` builds from held truth only.
- Not yet claimed: periodic sending; DNS-named map-servers; ms-name selection; encryption keys.

## Periodic registration (v0.38)
- `etr_registrar.start/stop/sent/wait`: a participant whose clock is a bounded held read on the map-server
  configuration; periodic sends with refresh semantics, immediate register on a published active map-server,
  UDP to the map-server, send truth published as its own cells. FNW1 only.
- Not yet claimed: ETR consumption of Map-Notify; packet splitting; IPv6/IID records; DNS map-servers; NAT.

## ETR Map-Notify (v0.39)
- `wire.etr_notify4`: verify a Map-Notify against the held map-server configuration and the process-private
  key, answer with the control's Map-Notify-Ack (record count 0, records copied), byte-identical to the control.
- Not yet claimed: receiving notifies on the registrar's socket; retransmissions; (S,G) notifies.

## Registrar control socket (v0.40)
- One UDP control socket per registrar: registers sent from it, notifies received on it and acknowledged from it,
  verified against held truth. Per-map-server failures are recorded truths; the registrar does not die on them.
- Not yet claimed: notify retransmission handling; (S,G); IPv6 control socket.

## Multi-record atomicity (v0.41)
- A multi-record Map-Register is authorized once, against one site-policy snapshot; no partial application.
- (v0.41 recorded "open mode" as a divergence pending decision. It was a bug; fixed in v0.42.)

## No site, no registration (v0.42)
- A registration is accepted only when a configured site authorizes it; an empty site table authorizes nothing,
  including after the last site is deleted. (Bug fix: the rule stated above since v0.6 was not enforced when no
  site existed.)

## Native registration (v0.43)
- ETR identity; ETR database truth under the identity; the Map-Server governor holds each ETR's database variable in
  place, applies site policy, publishes governed registration and decisions; ETRs observe decisions. No packets.
- Not yet claimed: liveness of governed registrations; re-governance on site-policy change; multiple governors.

## Liveness of native registrations (v0.44)
- A native registration resolves only while its ETR's single liveness cell (etr-live/<xtr_id>) is fresh; death is
  derived from time, nothing is written. UDP-path registrations are unaffected.

## Re-governance (v0.45)
- A native registration resolves only while a site currently authorizes it (derived at read from the held site
  policy). Not claimed: decision refresh on site change; re-governance of UDP-path registrations.
