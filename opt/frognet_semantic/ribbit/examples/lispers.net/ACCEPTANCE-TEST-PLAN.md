# LISP control plane: acceptance test, lispers.net vs Ribbit-LISP
Draft 1, 2026-09-27. There was no acceptance test for either implementation; this is it. Every test runs the SAME
stimulus against both systems and records, per system: PASS / FAIL (with the evidence), and the measurements.
A test that finds one system wrong is a finding about that system, not a reason to drop the test.

## 1. Systems under test
| | A: lispers.net | B: Ribbit-LISP |
|---|---|---|
| build | Dino's released 0.643, build/latest-py3/lispers.net.tgz, installed exactly as build/Dockerfile installs it | the Ribbit-LISP package at the version printed in the run's banner |
| roles | lisp-core + lisp-ms + lisp-mr (map-server = yes, map-resolver = yes) | tools/lisp-boundary (UDP front) + ram-server (RAM host, vendor API) |
| UDP | 4342 | the port given (4342 when not sharing a machine with A) |
| configuration | lisp.config (header line required), one site per test fixture, force-proxy-reply = yes | the same sites through site.add (same prefixes, key-ids, keys, accept-more-specifics) |

Configuration parity is itself test P3: both systems are configured from ONE fixture file, never by hand.

## 2. Principles
1. Same stimulus, same checks, both systems. The harness never branches on which system it is talking to except to
   configure it and to start/stop it.
2. Every answer is decoded and checked field by field. Timing is recorded only for answers that passed their checks.
3. Oracles are independent of both systems where possible: RFC 9301 field layout, Dino's own encoders (byte oracle),
   Dino's own client tools (lig/rig) as a second client, and hand-computed expectations.
4. Failure injection must leave each system able to serve the next valid message; "the process died" is a result,
   recorded with its evidence (log, traceback), and the system is restarted before the next test.
5. Every run records: both versions, host, wire (loopback / LAN / Internet RTT), seeds, and the full fixture.

## 3. Levels
### P -- preconditions (both must pass before anything else runs)
- P1 A installs cleanly: Dino's lispers.net-test-install.py reports every module present.
- P2 B builds and its own gates pass: tools/qualify.sh STAGES="build local fnw1 oracle handler boundary".
- P3 configuration parity: the same fixture yields the same site set on both (A: its API site list; B: held sites).
- P4 liveness: each system answers one valid Map-Request for an address outside every site (negative Map-Reply).
- P5 acceptance-surface closure: the externally reachable control-plane surface of the frozen lispers.net version --
  every UDP message type and flag it handles, every lispapi call, every lisp.config command -- is inventoried, and
  each entry maps to at least one test id above. Every branch reachable from external input is exercised, or
  classified unreachable / dead / platform-specific with evidence. Reported separately, per system: command-surface
  coverage, function coverage, line coverage, branch coverage (lispers.net: coverage.py over its .py sources run
  under the suite; Ribbit: gcov). Command parity must not hide an unexercised failure path, and line coverage must not
  pass for behavioural completeness.

### L1 -- first principles: one message, one client, field by field
| id | stimulus | expected (both) | source |
|---|---|---|---|
| L1.1 | authenticated Map-Register, 1 record, 1 RLOC, M bit set | Map-Notify, same nonce, same record, valid HMAC over the notify with the site key | RFC 9301 5.6, 5.7 |
| L1.2 | same, M bit clear | no Map-Notify; registration applied (L1.4 resolves it) | RFC 9301 5.6 |
| L1.3 | ECM Map-Request for an EID inside the registered prefix, ITR-RLOC = client | Map-Reply to the inner UDP source port, same nonce, EID-prefix/mask as registered, locator set exact (address, priority, weight, R bit), TTL as registered | RFC 9301 5.4, 5.8 |
| L1.4 | ECM Map-Request after L1.2 | as L1.3 | |
| L1.5 | ECM Map-Request, EID inside a site, nothing registered | negative Map-Reply, 1-minute TTL, the site prefix | RFC 9301 5.4, lispers.net behaviour |
| L1.6 | ECM Map-Request, EID outside every site | negative Map-Reply, action natively-forward | RFC 9301 5.4 |
| L1.7 | bare (non-ECM) Map-Request to the map-resolver port | recorded as found; not an interoperability requirement | observation |
| L1.8 | IPv6 EID versions of L1.1-L1.6 | as IPv4 | RFC 9301 |
| L1.9 | byte identity: B's Map-Reply / Map-Notify / Map-Register bytes against Dino's encoders for the same inputs | identical | existing: Dino byte oracle |
| L1.10 | Dino's lig queries the registered EID on each system | lig prints the registered locator set | independent client |

### L2 -- semantics: the registration state machine
| id | stimulus | expected | existing coverage |
|---|---|---|---|
| L2.1 | refresh (same locator set) | still resolves, lifetime extended | conformance: refresh_extends_own_registration_lifetime |
| L2.2 | change locator set | new set resolves, old never again | conformance: registration_replaces_locator_set |
| L2.3 | withdraw: TTL 0 from a registered RLOC | negative after; Map-Notify if M set | conformance: zero_ttl_deregisters |
| L2.4 | withdraw from a NON-registered source | ignored, state unchanged | conformance: zero_ttl_from_non_rloc_is_ignored |
| L2.5 | TTL expiry without refresh | resolves before, negative after, no reaper needed | conformance: ttl_seconds_encoding_expires_without_reaper |
| L2.6 | more-specific inside an accept-more-specifics site | accepted | conformance: accept_more_specifics_authorizes_child |
| L2.7 | more-specific without accept-more-specifics | rejected, no state | conformance: more_specific_rejected_without_ams |
| L2.8 | prefix outside every site | rejected, no state | conformance: unknown_site_registration_rejected |
| L2.9 | two xTRs, merge semantics | union of their sets; one replacing its own changes only its own | conformance: merge_* |
| L2.10 | multi-record register | all records applied, one Map-Notify carrying all | wire: two_record / multirecord_notify |
| L2.11 | longest-prefix match across nested registrations | the longest wins | conformance: longest_prefix_match |
| L2.12 | instance-id isolation | a registration in one IID never answers another | conformance: iid_isolation |
| L2.13 | site shutdown / delete while registered | resolution stops; restore resumes | (B: native_regovernance; A: via its API) |
The conformance suite runs against A through its API (--adapter control) and against B (--adapter command) -- the
same 51 tests; each result is reported per system.

### L2C -- causality and time (put / get ordering, per participant and across participants)
| id | stimulus | expected |
|---|---|---|
| L2C.1 | put, then immediately get, same participant | the get sees the put (ruled: put() completes when this participant observes it) |
| L2C.2 | put, then the same participant's held view (resolver.wait with zero timeout) | the view has it |
| L2C.3 | put in one process, get in another | visible within the stated convergence bound |
| L2C.4 | registration expires, immediate get | negative, with no reaper running |
| L2C.5 | withdraw, immediate get, same participant | negative |

### L3 -- authentication and authorization
| id | stimulus | expected |
|---|---|---|
| L3.1 | HMAC-SHA-256-128, correct key | accepted |
| L3.2 | HMAC-SHA-1-96, correct key | accepted (A 0.643 on Python 3 is KNOWN to crash its Map-Server here -- recorded as FAIL for A) |
| L3.3 | wrong key | rejected, no state change, no Map-Notify |
| L3.4 | wrong key-id | rejected |
| L3.5 | no authentication (alg 0) on a keyed site | rejected |
| L3.6 | one bit flipped in the authentication data / in the body | rejected |
| L3.7 | multi-record register with one unauthorized record | whole register rejected, nothing applied (atomic) |
| L3.8 | replay of an accepted register | recorded (RFC leaves nonce replay handling to the implementation) |
| L3.9 | the matrix: {HMAC-SHA-1-96, HMAC-SHA-256-128} x {valid, wrong key, malformed auth fields}; after EACH, a valid register on the same system | per cell as above; the follow-up register is always accepted (failure containment) |
| L3.10 | multi-record register, the bad record FIRST / MIDDLE / LAST, bad = unauthorized, invalid, malformed | Ribbit: nothing applied (packet atomicity, a deliberate deviation); lispers.net: recorded as it behaves (partial application before a later decode failure) |

### L4 -- failure and boundary conditions (robustness: every case must leave the system serving)
After EVERY case below, a valid L1.3 exchange must succeed; a system that stops answering FAILS that case.
- L4.1 truncation: each valid message (Register, ECM Request) cut at every byte offset
- L4.2 wrong type values 0 and 9-15; ECM whose inner packet is not IPv4/UDP or not a Map-Request
- L4.3 record count larger / smaller than the records present; locator count mismatch
- L4.4 unknown AFI in EID or locator; mask > 32 (IPv4) / > 128 (IPv6); mask 0
- L4.5 authentication length disagreeing with the algorithm; zero-length auth with alg set
- L4.6 zero records; maximum records that fit one 1500-byte datagram; maximum locators per record
- L4.7 TTL extremes: 0, 1 second, 0x7fffffff minutes; seconds-encoding bit set/clear
- L4.8 oversize datagram (> 1500, up to 64 KiB)
- L4.9 a flood of malformed packets interleaved with valid traffic: the valid traffic's answers stay correct and timely
- L4.10 restart of the map-server while registrations exist: what is retained, how long until re-registered state
       resolves again (A: in-process state; B: RAM host transient by design -- both recorded)
- L4.11 B only: RAM host unreachable / restarted mid-run -- boundary behaviour, recovery time
- L4.12 A only (hazard found): starting A kills every process with "lisp-" in its command line; recorded, and the
       harness always starts A before B

### L5 -- performance and scale (only answers that passed their checks are timed)
- L5.1 latency per message class (register, refresh, change, withdraw, resolve positive / in-site negative /
       outside negative): median, p90, p99, max -- 1 client
- L5.2 the same under N parallel xTRs and M parallel ITRs, N,M in {1, 4, 16, 64}
- L5.3 head-of-line: resolution latency while registrations stream at full rate
- L5.4 table size: resolution and registration cost at 10, 100, 1 000, 10 000 registered prefixes
- L5.5 convergence: registration accepted -> resolvable through a different resolver (A: same MS; B: another
       participant through the memory)
- L5.6 bytes on the wire per operation class and per refresh interval
- L5.7 sustained load: 10 minutes at 50% of the measured maximum; memory and CPU of each system's processes

### L6 -- deployment conditions
- L6.1 same machine (loopback)
- L6.2 LAN
- L6.3 Internet: clients on AI-Host, servers on streamingfrog (both A and B there, one at a time)

### L2D -- deregistration
- dynamic withdrawal (TTL 0 from a registered RLOC), static withdrawal, expiry -- each with and without want-map-notify.
  Static deregistration by timeout is a finding for Dino's judgment, not a defect; the test records it.

## 7. Contract rulings the tests depend on
- Read-your-own-write (L2C.1, L2C.2, L2C.5): RULED by John 2026-09-27 -- put() is complete when this participant's own
  consequences of the write are observable (its own held views have applied it), not merely when shared RAM accepted
  it. No synchronization with other participants is implied (L2C.3 keeps its convergence bound).
- Map-Notify-Ack record count: Ribbit reproduces lispers.net byte for byte (interoperability is the contract).
- Multi-record registration: one transaction in Ribbit (deliberate deviation), per-record in lispers.net.

## 4. Existing tests reused
- Ribbit-LISP conformance suite (51 tests; command adapter for B, control adapter -- lispers.net's own API -- for A)
- Dino byte oracle (tools/test_control_wire_oracle.py) -- L1.9
- tests/test_wire_contract.py builders (reg4, req4, auth builders) -- the stimulus library
- tools/test_boundary_hol.py -- L5.3
- tools/stress_multirecord_atomic.py -- L3.7 under load
- tools/measure_*_bytes.py -- L5.6
- lispers.net-test-install.py (Dino) -- P1
- Dino's lig / rig -- L1.10, second independent client
- tools/compare_lispers.py -- the parallel driver behind L5.1-L5.3

## 5. Output
One directory per run: the banner (both versions, wire, seeds), the fixture, every system log, and
ACCEPTANCE-REPORT.md: one row per test id, per system, the numbers for L5, and one classification per test:
  PARITY            both systems satisfy the external contract
  A FAIL            lispers.net violates the independent oracle (evidence attached)
  B FAIL            Ribbit violates it (evidence attached)
  KNOWN DIFFERENCE  an intentional semantic divergence, documented and justified (e.g. multi-record atomicity:
                    L3.7 / L3.10 expect whole-register rejection, which lispers.net does not do)
  OBSERVATION       behaviour for which no normative requirement is asserted (e.g. L1.7, L3.8)

## 6. Recording: every measurement to tuple space, as it goes
Every timed sample the harness takes is written to a Ribbit memory as it goes, in cells whose ADDRESSES are
deterministic -- built only from what the measurement is, never from a clock or a random number -- so the same
measurement lands at the same address on every run of it, and any later analysis can select by address alone.

Address (a write to an existing address replaces that cell, so re-recording a run is idempotent):
    service  = lisp_acceptance
    variable = <test id>.<metric>                    e.g. L5.1.latency_us, L5.2.throughput_msg_s, L4.9.survived
    instance = <system>|<version>|<wire>|<condition>|r<run>|c<chunk>
      system     lispers.net | ribbit
      version    the version string the system reports (lispers.net: lisp-version.txt; Ribbit: RIBBIT_LISP_VERSION)
      wire       the configured wire label: loopback | lan | inet  (the measured RTT goes in the bag, not the address)
      condition  the test's parameters, keys sorted, key=value joined by ',' -- e.g. class=register,prefixes=8,xtrs=16
      run        the ordinal of this run in its series (same system|version|wire): 1 + the highest run already
                 recorded in run.manifest for that series, read once when the system's run starts; every condition
                 measured in that run carries the same run number
      chunk      samples are split into cells of at most 1 000; chunk 0, 1, ...
Bag (JSON object):
    schema 1, and every coordinate again as its own field (system, version, wire, condition as an object, run, chunk,
    test, metric) so analysis never parses the instance string; unit; n; samples -- integers in the unit, in the order
    they were taken; errors -- count and the first few, with evidence; clock (the timer used); host; measured_rtt_us.
Run manifest, one per run:  variable = run.manifest,  instance = <system>|<version>|<wire>|r<run>
    the banner, both versions, the fixture (and its sha256), host, wire, the measured RTT, the start date (in the
    bag -- a date is never part of an address).
Writes never fall inside a timed window: samples are held in memory during a phase and written, non-blocking,
between phases. The tuple space is the RAM host the run uses (John 2026-09-27), under its own service
lisp_acceptance. Implemented: tools/compare_lispers.py --record HOST:PORT --wire LABEL --version NAME=VERSION ...,
through tools/tuple-write (JSON lines -> Memory::write; also lists a variable's instances and reads a bag back).

## 8. Surface items and where they are tested (P5 closure)
| surface (lispers.net map-server / map-resolver) | test | status |
|---|---|---|
| Map-Request (bare, ECM, IPv4, IPv6, instance-id) | L1.3, L1.5-L1.8, L2.12 | PARITY |
| Map-Register (auth, merge, multi-record, limits, withdraw, expiry, LCAF locators, encryption) | L1.1-L1.2, L2.x, L3.x, L4.x, L2.14, L3.12 | PARITY / A FAIL as reported |
| Map-Notify-Ack | L1.12 | PARITY |
| Info-Request (NAT traversal) | L1.11 | PARITY |
| ECM with the D bit (DDT-originated), co-located MS+MR | L1.13 | OBSERVATION, same behaviour |
| Map-Referral, ms-authoritative-prefix, ddt-root, referral-cache | HW.DDT.1-5 (REAL-HARDWARE-TESTS.md) | two hosts |
| map-server-peer | HW.PEER.1 | two hosts |
| lisp site (add, delete, restore) | L2.13 and every fixture start | PARITY |
| encryption-keys (map-register-key) | L3.12 | PARITY |
| geo-coordinates, explicit-locator-path, replication-list-entry, json | L2.14.* | PARITY; json A FAIL |
| eid-crypto-hash (crypto-EIDs), require-signature, encrypt-json | -- | not testable for parity: depend on JSON locators, which lispers.net 0.643 drops |
| site options: shutdown, allowed-rloc, force-ttl, proxy-reply-action (drop, not-registered-yet), echo-nonce-capable, force-proxy-reply, pitr-proxy-reply-drop | L2.15.* | PARITY |
| allowed-prefix group-prefix -- multicast (S,G) | L2.16, L2.16b | Ribbit PASS; lispers.net A FAIL (non-RFC padded LCAF) |
| force-nat-proxy-reply (no xTR behind NAT) | L2.15.force-nat-proxy-reply | PARITY |
| lisp policy (match destination-eid / source-rloc; set-record-ttl, set-action drop, set-rloc-address; implied drop) and policy-name | L2.17.* | PARITY |
| proxy Map-Reply TTL (1440) | L1.14 | PARITY |
| geo-coordinates, explicit-locator-path, replication-list-entry, json objects, used by policy set-*-name | L2.18.* | PARITY, byte for byte |
