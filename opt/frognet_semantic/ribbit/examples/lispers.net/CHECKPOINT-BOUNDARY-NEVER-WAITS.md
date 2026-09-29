# [BOUNDARY_NEVER_WAITS_V1] + [ROUND_TRIPS_V1] -- v0.53 in progress, 2026-09-26

## John's rulings this step
- Nothing may stop behind a blocking call. Blocking only where the caller needs the answer, and then on its own thread.
- Compound operations become ONE call, in the LISP region's own API (the vendor owns its RAM interface): "Do that kind
  of thing throughout. This is the power of vendor-packaged APIs."
- Transient RAM host per run, pre-populated by John's RAM launcher before participants are let in (configuration --
  sites, keys, map-servers, DDT -- is the seed; soft state -- registrations, map-cache, liveness -- is never persisted).
  The launcher itself is NOT in this package or any upload yet: needed from John.

## Done, proven
### The Map-Server's UDP front never waits (ribbit_cpp/lisp_handler.hpp, tools/lisp_boundary.cpp)
- Receive thread: Map-Request answered on it from held views (own Engine); Map-Register handed to one of N register
  workers chosen by sender address:port (one xTR's registers stay in order; senders run in parallel). Each worker has
  its own Engine; configuration has its own Engine. No locks between participants, as before.
- A site's key is local to the process (site.add keeps it in site_keys, never in memory). configure() gives it to every
  worker as a job on the worker's own queue (Engine::adopt_site_key / forget_site_keys, lisp_engine.hpp): installed
  ahead of any register sent after site.add returns; still never written to memory.
- tools/lisp_boundary.cpp: the Map-Server UDP front as a program: lisp-boundary --ram HOST PORT --udp PORT [--workers N],
  configuration as JSON lines on stdin.
- Gate tools/test_boundary_hol.py (qualify stage `boundary`, 22 ms shaped wire, 16 senders x 5 Map-Registers, Map-Requests
  timed one at a time, every register must be applied):
    before: first Map-Request answered after > 5,000 ms; 80 registers applied in 15,317 ms   -> FAIL
    after : Map-Request max 0.81 ms, median 0.06 ms (idle 0.01); 80 registers applied in 3,875 ms -> PASS
- Regression on this tree: build PASS; local and fnw1 OK (skipped=1); handler 0 fail; independent all pass.

### Round-trip counting (frogram.cpp calls_this_thread, transport.stats "calls", tools/measure_round_trips.py)
Red today (loopback): site.add 2, map_cache.add 3, map_cache.delete 2, ddt.add 3, ddt.delete 2; the rest 1; held reads 0.
Over the Internet (fnw1 at streamingfrog, 22 ms RTT): registration.put 93 ms (4 RTT) although it makes ONE blocking
call -- the extra round trips are inside the call (REQ_MISS->FULL or RAW bootstrap suspected; not yet traced).
One Map-Register on a boundary worker costs ~190 ms (~9 RTT).

### Traced: registration.put in steady state is ONE round trip
Through the 22 ms shaped wire with FROGRAM_TRACE (artifacts/put-trace-22ms.log): four puts to prefixes of a length the
participant already holds, 22.9 / 22.8 / 22.8 / 22.7 ms -- one frame out, one back, each. The 93 ms median in the
streamingfrog fnw1 table is the TEST's pattern: most of its puts are the first at a new (iid, prefix length), which
costs publish_known_length's read + write of the length marker (2 more) plus arming the new length's held slot. So the
round trips to remove are the marker (the host decides "only if absent" inside the one call) and the first-arm reads;
and wire.register4_notify, ~190 ms (~9 RTT) per register on a boundary worker, is the one to take apart first.

## Next, in order
1. Count and trace wire.register4_notify (the boundary's register path) and take its round trips to one.
2. The vendor API: op=lisp.<operation> in the RAM host (tools/ramsrv/ram_server.cpp), the host running the same
   lisp_engine.hpp logic against its memory; every mutating LISP operation one call, reads stay on held views.
   Answers carry the id of the cell written so the caller's held view waits for it (closes the read-your-own-write
   finding in FINDINGS-FOR-DINO.md). Gates: measure_round_trips.py (every mutating op = 1) and the boundary stage
   (80 registers from 16 senders in a few hundred ms).
3. Every remaining blocking ram->write: does its caller need the answer? If not, write_nowait.
4. Full qualify local and against streamingfrog; package; checkpoint.
5. The pre-populated transient host with John's RAM launcher (needs the launcher).

## [VENDOR_API_V1] -- compound operations in ONE call (v0.53)
- RAM host (tools/ramsrv/ram_server.cpp): op=lisp (POST {"operation","args"}) runs the operation on the host's own
  Engine -- the same lisp_engine.hpp -- connected to itself over loopback, so every round trip the operation makes is
  local to the host. One host Engine (an Engine is single-threaded by design, and the site keys it verifies
  Map-Registers with are its own); it runs on its own thread, never on a socket thread.
- Client (ribbit_lisp.cpp): with --ram, the Map-Server-side mutating operations go to the host as one request each:
  site.add/delete, registration.put/delete, map_cache.add/delete, ddt.add/delete, map_resolver.add/delete,
  wire.register4, wire.register4_notify, wire.register6. Reads and waits stay on held views (0 round trips).
  ETR-side operations stay local (they use this participant's own keys), and so do database_mapping.* -- a database
  mapping is the ETR's own truth under its own identity; hosting it made native registration/liveness/regovernance
  fail (0/1 each) until it was taken back out.
- Gates: tools/measure_round_trips.py -- every mutating operation 1 round trip (was: site.add 2, map_cache.add 3,
  map_cache.delete 2, ddt.add 3, ddt.delete 2). fnw1 OK (skipped=1); local OK; independent all pass; handler 0 fail;
  boundary PASS.
- Through a 22 ms shaped wire, fnw1 medians: registration.put 89 -> 38 ms, map_cache.add 67 -> 35, ddt.add 67 -> 36,
  wire.register4 120 -> 40 (p90 228 -> 43), site.add 45 -> 36; but registration.delete 22 -> 36. Every hosted
  operation costs ~36 ms on that wire, not 22: ~14 ms beyond the one round trip, NOT yet explained (the host's own
  work is 0.4-1.7 ms on loopback). Suspects: the shaped relay delaying a reply written in two segments, or the first
  request of each new op=lisp body shape bootstrapping RAW. To be measured on the real wire to streamingfrog.

## Still open (mine)
- Lock gate: the boundary's worker queues and configure() take std::mutex in participant code (gate 0 -> 8).
  Replace with SPSC rings; configuration back to one caller.
- An Unreachable thrown on one of the adapter's threads aborts the process (rc=-6) and every later test reports the
  dead adapter: fail once, loudly, with the endpoint and cause.
- The 14 ms above.
- The package no longer ships built binaries: every machine builds its own.

## [READ_YOUR_OWN_WRITE_V1] -- done (John 2026-09-27: put() is complete when this participant observes it)
- RAM host: op=lisp reports every cell the operation wrote (frogram::log_writes_to on the host's thread).
- Participant: every held view records how far it has applied, per variable, in a lock-free table
  (Engine::held_read); a hosted operation returns only when this participant's own views have applied every cell it
  wrote (Engine::await_own). Views of variables this participant does not hold are not waited for; nothing waits on
  any other participant.
- Red: 6 of 80 put->get / delete->get pairs missed their own write. Green: 0 of 80, ten runs. qualify stage `ryw`.
- The "first put at a new length hangs" seen mid-way was my stale ram-server: the host links the LISP engine, and its
  build still had the held_read that called itself; a rebuild from the same sources cleared it.
- lisp_engine.hpp is now compiled into the RAM host, and the host's tests build with -Wall -Werror: its 33
  misleadingly indented one-liners were split onto their own lines (whitespace only -- token stream verified identical).
- Full run on this tree: build, local, fnw1, independent, handler, boundary, ryw, stress, fnwp (3 seeds, 2 endpoints),
  session over TCP all PASS. Lock gate still 8 (the boundary's queues) -- open.

## [ROW_LOCKS_V1] + [HOST_ENGINE_POOL_V1] -- built (v0.54-row-locks)
- ribbit_cpp/ram_memory.hpp replaces the RAM host's memory (one std::mutex + one condition_variable before).
  Each row: a std::shared_mutex (many readers or one writer) and its envelope (write time, exact-address triggers).
  No other lock: rows, variables and services are found through insert-only lock-free tables (CAS on a bucket list;
  nodes never unlinked -- a removed row is marked dead and revived on its next write), so nothing needs reclamation.
  Waits (John's design): a trigger per wait, built only when someone waits -- on the row's envelope for an exact
  address, on the shared space's pattern table (keyed by the fixed coordinates) otherwise; a write fires its row's
  triggers and the six patterns its coordinates can match. A trigger's condition variable sleeps on a mutex that
  belongs to that one waiter. Waiters register before they first evaluate, so no write between the two is missed.
- Vendor API: HOST_ENGINES (8) host Engines, an operation routed by what it addresses (iid|prefix, or its packet);
  each Engine has its own mutex held only while it runs one operation (an Engine is single-threaded by design); site
  keys handed to every Engine; every host Engine applies an operation's writes before its answer goes back.
- Gates: tools/test_ram_memory.cpp (qualify stage `memory`): lock gate (exactly one row lock and one per-trigger
  mutex declared in the memory), 8 writers x 400 writes over 64 rows of one variable with 4 readers and 4 parked
  waiters (exact rows, the variable, the service): no torn or invented read, ids unique and increasing per row, final
  state = serial replay, no lost wake, remove by id. 5 of 5 runs PASS.
  build, local, fnw1, ryw, independent, handler, boundary, stress, memory, fnwp (3 seeds x 2 endpoints), session over
  TCP, fan-in: all PASS.
- NOT improved: a Map-Register through lisp-boundary still costs 13-20 ms on one machine (lispers.net: ~2 ms),
  compare_lispers.py, 4 x 4. The engine pool did not move it; its cost is inside one register, not in queueing.
  Next: take one register apart hop by hop. Still open: withdrawal Map-Notify; the boundary's 8 locks.

## [BOUNDARY_NO_LOCKS_V1] + withdrawal -- v0.54
- lisp_handler.hpp: every lock gone. Worker queues are lock-free (Vyukov intrusive MPSC: producers append with one
  atomic exchange, the worker alone takes); the worker sleeps on a futex word each append bumps (a kernel wait queue,
  not a lock); configuration is one caller by contract. Lock gate: participant locks 0 (was 8).
- Withdrawal: a TTL-0 Map-Register with an EMPTY locator set was refused ("no rloc-set supplied") -- no deregistration
  and no Map-Notify; lispers.net accepts it, deregisters and Map-Notifies. Ribbit now does the same.
  compare_lispers.py against lispers.net 0.643: 0 wrong or missing answers (was 16), withdraw included.
- Regression: build, local, fnw1, ryw, independent, handler, stress, lockfree (gate 0), memory -- all PASS.
- Still: a register through lisp-boundary costs 16-23 ms on one machine vs lispers.net's ~2 ms. Next.

## [ONE_SET_OF_VIEWS_PER_PARTICIPANT_V1] + [APPLIED_CELL_HAS_ONE_WRITER_V1] -- v0.54
- A participant is a process and holds each resolver view once: Engine::share_views_of(owner); the view registry is
  an insert-only lock-free table armed by whichever thread first needs a view (the winner builds it, others wait for
  ready). lisp-boundary's 10 Engines and the RAM host's 8 Engines each share one set.
- An applied cell's address carries its one writer (host:pid:random drawn at start), so no participant's wait can
  be satisfied by another's progress.
- One Map-Register through lisp-boundary: 19 frames -> 9; 2.7-3.5 ms -> 1.9-2.7 ms sequential (lispers.net ~2 ms).
- compare_lispers.py (4 xTRs x 4 prefixes, 4 ITRs x 10, one core, both systems on it), medians ms, lispers / Ribbit:
  register 1.45 / 6.02 (was 16.5), refresh 1.10 / 12.59 (22.9), change 1.53 / 12.05 (21.1), withdraw 1.23 / 9.18
  (18.3), resolve 1.96 / 0.12, resolve after withdraw 1.69 / 0.08. 0 wrong or missing answers.
- Regression: build, local, fnw1, ryw, independent, handler, stress, lockfree (0), memory, boundary -- PASS.
- Remaining register cost: the prior is still read as the WHOLE per-length table (v0.46), which grows with the
  table -- refresh/change in the comparison read 16 rows each; and each view event still costs a re-read and one
  applied-cell write.

## v0.54, later: the open items from the requirements review
- [VENDOR_API_V1] in lisp-boundary: each Map-Register is ONE call to the region's API (wire.register4_notify on the
  RAM host); sites are configured through it, so the host's Engines hold the keys that verify registers.
- [READ_YOUR_OWN_WRITE_V1] for EVERY operation: Engine::call logs the cells an operation writes and returns only when
  this participant's own views have applied them -- local operations (database_mapping.*, the ETR side) as well as
  hosted ones; the log nests under an outer logger (the RAM host's executor).
- [FAIL_ONCE_LOUDLY_V1]: every Engine thread runs through spawn(); an exception on one is recorded, not
  std::terminate; the next operation throws it; ribbit-lisp answers once -- "RAM host H:P unreachable: <cause>" --
  and exits 3. Gate tools/test_fail_loudly.py (qualify stage failloud): PASS; the v0.51 client FAILS it (it keeps
  answering after the host is gone).
- [PRIOR_FROM_THE_HELD_VIEW_V1] (v0.46): registration.put finds its prior in the held registration view (no round
  trip) instead of reading the whole per-length table.
- compare_lispers.py, FULL size (16 xTRs x 8 prefixes, 16 ITRs x 30, all parallel, one core shared by both),
  medians ms lispers.net / Ribbit (Ribbit before these changes in brackets):
    register 8.34 / 21.75 (85.65)   refresh 8.14 / 21.23 (236.87)   change 8.28 / 22.39 (255.66)
    withdraw 8.00 / 14.17 (361.40)  resolve 7.27 / 0.97             resolve after withdraw 7.19 / 0.77
  throughput msg/s, Ribbit: register 474, refresh 492, change 476, withdraw 781, resolve 14 762 (lispers.net ~1 700)
  0 wrong or missing answers.
- Regression: build, local, fnw1, ryw, handler, independent, stress, boundary, failloud, lockfree 0, memory: PASS.

## Perf to tuple space (ACCEPTANCE-TEST-PLAN.md section 6) -- built
- tools/compare_lispers.py --record HOST:PORT --wire LABEL --version NAME=VERSION (per target): after each phase,
  its checked latency samples (integer microseconds, in the order taken) go to service lisp_acceptance, variable
  L5.2.latency_us, instance <system>|<version>|<wire>|<sorted k=v condition>|r<run>|c<chunk>; one run.manifest cell per
  system per run. Run ordinals come from what is already recorded -- no clock or random number in any address.
- tools/tuple-write: JSON lines -> Memory::write; lists a variable's instances; reads a bag back. Built by qualify.
- Proven: two recorded runs against lispers.net 0.643 and Ribbit -> manifests r1 and r2 for each system, 24 latency
  cells (2 systems x 6 phases x 2 runs), a bag read back with every coordinate, n = 16 samples, errors 0.

## [LISP_SERVICE_V1] -- the LISP service (John 2026-09-26 startup ruling; lispers.net's own startup read from its source)
- tools/lisp_service.cpp: one program per machine from the vendor package. Configuration (JSON, --config): name, RAM
  host/port/api, roles (map-server, map-resolver, etr, itr), udp, heartbeat_s, startup_wait_s, sites, governor, etr
  (xtr_id, database_mappings, liveness), itr (iids). Sequence: session (semantic + data socket) -> capability tuple
  (lisp / capability / <name>: roles, udp, participant, beat, version) -> roles (map-server: the UDP front, sites
  through the region's API, the governor; etr: identity, database mappings, liveness; itr: resolver views) -> wait
  startup_wait_s -> ONE plain read of every capability -> log the region -> heartbeat every heartbeat_s. SIGTERM stops
  it cleanly.
- Where lispers.net differs (its source): RUN-LISP -> lisp-core -> one process per `lisp enable` role, peers written
  by hand in lisp.config, a full Map-Register to every map-server every 60 s, configuration relayed over IPC.
- Gate tools/test_lisp_service.py (qualify stage service), 3 of 3 runs PASS: both machines find each other after the
  wait (roles, UDP front); heartbeats advance; an ITR's Encapsulated Map-Request to the map-server's UDP front is
  answered with the ETR's NATIVE registration (a cell governed by the map-server -- no Map-Register sent); the ETR
  stops cleanly; after its liveness lifetime the front stops answering with its locator.
- Configuration location, as lispers.net keeps ./lisp.config in its own directory: lisp-service.config in the
  directory the program is installed in, unless --config names another. --service runs it in the background as
  RUN-LISP runs lisp-core: detached, logs/lisp-service.log and logs/lisp-service.pid beside it. Tested: an installed
  copy started with --service returns at once, runs, logs there, and stops cleanly on SIGTERM (5 of 5 runs).

## [CAPABILITY_REGISTRATION_V1] + /etc/lispers.d (John 2026-09-27)
- "This is automatic registration of capabilities, not identification of peers": no per-machine tuple. Each role is
  its own capability tuple, addressed by what reaches it (capability|map-server / udp:A:P, capability|map-resolver /
  udp:A:P, capability|etr / xtr:<id> with its prefixes, capability|itr / participant:<id>). After the startup wait each
  role reads the capabilities it uses and handles them (ETR: map-servers; ITR: map-resolvers; map-server: the ETRs'
  prefixes). The heartbeat rewrites the capability tuples.
- Configuration: /etc/lispers.d/lisp-service.config unless --config. --service unchanged (logs/ beside the program).
- Gate (service stage), 3 of 3 runs PASS: capabilities registered as above and nothing per machine; each role read the
  capabilities it uses; heartbeat advances; native registration answered through the map-server's UDP front; the
  --service ETR, configured from /etc/lispers.d, stops cleanly; its locator withdrawn after its liveness lifetime.

## The acceptance test -- tools/acceptance.py (first full run 2026-09-27)
- One fixture configures both systems (lispers.net's lisp.config is generated from it; Ribbit's sites through the
  region API). Every test sends the same UDP LISP control messages to both; every answer is decoded and checked; a
  system that stops serving is recorded with its evidence and restarted; results and latencies go to the tuple space
  (acceptance.result, <test>.latency_us). Five-way classification; ACCEPTANCE-REPORT.md.
- Run: 31 tests -- PARITY 23, A FAIL 6, KNOWN DIFFERENCE 1, OBSERVATION 1; B FAIL 0.
- Ribbit defect it found and that is fixed: L3.5 -- an UNAUTHENTICATED Map-Register (alg-id 0) for a prefix in a keyed
  site was accepted ([KEYED_SITE_REQUIRES_AUTH_V1]: a site with any key now accepts only authenticated registers,
  on wire.register4, wire.register4_notify and wire.register6).
- lispers.net failures: FINDINGS-FOR-DINO.md.
- Not yet in the harness (plan ids): P1-P3, P5 (coverage closure), L1.8 (IPv6), L1.9 (byte oracle -- runs in
  qualify), L1.10 (lig), L2.9 (merge), L2.12 (iid isolation), L2.13 (site shutdown), L2C (causality -- ryw stage
  covers Ribbit), L2D static, L3.8-L3.10 matrix/positions, L4.6/L4.7/L4.10/L4.11, L5 (performance: compare_lispers.py),
  L6 (LAN / Internet).

## Acceptance, second pass (47 tests) -- PARITY 29, A FAIL 10, KNOWN DIFFERENCE 4, OBSERVATION 4, B FAIL 0
- Added: L1.8 IPv6 (register, ECM with an inner IPv6 header, positive and negative), L2C.1 causality across clients,
  L3.8 replay, L3.9 the SHA-1/SHA-256 x valid/wrong-key/bad-length matrix with a valid register after each, L3.10 the
  bad record first/middle/last x unauthorized/invalid, L4.6 zero records, 40 records, 100 locators, L4.7 TTL
  0x7fffffff, L4.10 restart with live registrations, L4.11 RAM host lost mid-run.
- Ribbit defects found and fixed: the Map-Server front refused IPv6 EID registers (it only called the IPv4 decoder)
  and did not answer ECMs with an inner IPv6 header; the IPv6 decoder and reply encoder required IPv6 locators
  ([REGISTER_ANY_AFI_V1], [LOCATOR_ANY_AFI_V1]; wire.register_notify dispatches on the records' AFI).
- Observations: lispers.net loses its registrations on restart; Ribbit's front restart keeps them (they are in the
  memory). With the RAM host gone, Ribbit's front still answers Map-Requests from its held views and does not accept
  registers.
- Still to add: P1-P3, P5 coverage closure, L1.10 (lig), L2.9 merge, L2.12 instance-id (LCAF), L2.13 site shutdown,
  L2D static deregistration, L5 inside the harness, L6 LAN/Internet.

## Acceptance, third pass -- 50 tests, one clean run: PARITY 32, A FAIL 10, KNOWN DIFFERENCE 4, OBSERVATION 4, B FAIL 0
- Added: L2.9 merge semantics (xTR-ID + merge flag), L2.12 instance-id isolation (LCAF type 2), L2.13 a site deleted
  and restored through each system's own control path (lispers.net: its lispapi; Ribbit: the region API).
- Ribbit defects found and fixed: [LCAF_INSTANCE_ID_V1] every wire path was fixed to instance 0 and refused LCAF EIDs
  (now read on registers and requests, authentication still over the original bytes, LCAF put back into replies);
  [WIRE_XTR_ID_V1] the wire path dropped the xTR-ID, so two xTRs' merge registrations replaced each other.
- Harness: a system counts as started when it answers a valid register (three attempts); lispers.net's lisp.config
  is rewritten from the fixture at every start (its API rewrites the file when L2.13 changes a site -- that made the
  second restart fail). tuple-write now fails once and loudly (exit 3) when the RAM host is not there.
- Observations: neither system withdraws registrations when their site is deleted; lispers.net does not Map-Notify
  the sender of a merge register.

## P5 coverage, in progress
- [COVERAGE_ON_TERM_V1] ram-server and lisp-boundary end on SIGTERM/SIGINT through a handler that calls __gcov_dump
  (weak; a coverage build links it with -Wl,-u,__gcov_dump) then _exit, so a coverage build writes its data; the
  acceptance harness stops Ribbit with SIGTERM (SIGKILL only if it does not end in 5 s). --ribbit-front selects the
  front binary (the coverage build).
- Next: the full acceptance run on the coverage build -> gcovr line/branch/function coverage of lisp_engine.hpp,
  lisp_handler.hpp, ram_memory.hpp; then lispers.net's side with coverage.py.

## P5 coverage -- first numbers (the 50-test acceptance run, loopback; both systems measured in the same run pattern)
- Ribbit (gcovr, throw branches excluded): lisp_engine.hpp lines 52% (1 034 / 1 982); lisp_handler.hpp 44%
  (103 / 232); ram_memory.hpp 84% (110 / 131); total lines 53.2%, functions 58.1%, branches 32.7%.
- lispers.net 0.643 (coverage.py, statements): lisp.py 23%, lisp-ms.py 30%, lisp-mr.py 21%, lisp-core.py 24%,
  lispconfig.py 26%; total 24% of 14 743 statements.
- These are whole-file numbers: both files hold much more than the map-server / map-resolver surface the suite drives
  (lisp.py carries the xTR, RTR, NAT and data plane; lisp_engine.hpp carries the ETR, ITR, governor and held views,
  which the qualify stages exercise). Closure per P5 needs the surface inventory: which functions are reachable from
  a map-server/map-resolver's external input, and which of those the suite runs. That is next.
- Artifacts: acceptance/coverage/ribbit-coverage*.html (per line), acceptance/coverage/lispers-html/ (per line),
  tools/coverage/ (the rc files and how to run it).
- Also: tuple-write fails once and loudly when its RAM host is absent (exit 3); the acceptance harness stops Ribbit
  with SIGTERM.

## P5: the lispers.net surface inventory (tools/coverage/surface_lispers.py -> acceptance/SURFACE-lispers.md)
- Entry points read from lispers.net's own source: lisp_parse_packet's handler per control-message type (map-server /
  map-resolver side: Map-Request, Map-Register, Map-Notify-Ack, Map-Referral, Info-Request, ECM; xTR side listed
  apart) and the map-server / map-resolver command tables (lisp_ms_commands, lisp_mr_commands). A static call graph
  from each gives the reachable functions; coverage.py's JSON says which ran. Under the 50-test suite: 315 reachable
  functions, 105 ran, 24% of reachable lines. Handlers NEVER driven: Map-Notify-Ack, Map-Referral, Info-Request;
  commands ms-authoritative-prefix, map-server-peer, eid-crypto-hash, encryption-keys, geo-coordinates,
  explicit-locator-path, replication-list-entry, json, ddt-root, referral-cache.
- Added from it: L1.11 Info-Request -> Info-Reply (NAT traversal), L1.12 Map-Notify-Ack consumed -- both PARITY.
- Ribbit gap it exposed, now closed: [INFO_REQUEST_V1] the front did not answer Info-Requests; it now answers exactly
  as lispers.net's lisp_process_info_request / lisp_info.encode do (MS port 0, ETR port and global RLOC = where the
  request came from, private RLOC = the hostname it carried, no RTRs).
- Remaining surface, each a feature Ribbit does not implement today: DDT referral on the map-resolver (Map-Referral,
  ddt-root, referral-cache, ms-authoritative-prefix), map-server peering (map-server-peer), crypto-EIDs
  (eid-crypto-hash), encryption keys, and the RLOC record types geo-coordinates, explicit-locator-path,
  replication-list-entry and json.

## DDT on the map-server (RFC 8111) -- first look
- L1.13 sends a DDT-originated Map-Request (ECM with the D bit) to each map-server. With the suite's topology
  (map-server and map-resolver in one lispers.net, ms-authoritative-prefix 198.18.0.0/15 now in the fixture) lispers.net
  answers with a Map-REPLY, not a Map-Referral -- as Ribbit does. Recorded as an OBSERVATION (parity of behaviour).
- lispers.net only produces Map-Referrals from a map-server or DDT node that is NOT also the map-resolver the ECM is
  routed to (lisp-core hands ECMs to lisp-mr; lisp_process_map_request sends the referral only in the lisp-ms process).
  Driving Map-Referral, ddt-root, referral-cache and ms-authoritative-prefix needs a second topology: a map-server-only
  lispers.net (map-resolver = no) as the DDT authority, and a map-resolver with ddt-root pointing at it -- two
  addresses on port 4342, so two hosts (or two network namespaces). Not possible in this container as it stands.

## LCAF locators, and the real-hardware set
- L2.14.{geo,elp,rle,json}: a Map-Register whose locator is an LCAF record encoded by lispers.net's OWN encoder
  (tools/coverage/lcaf_vectors_from_lispers.py runs lisp_rloc_record.encode from its source tree), then a Map-Request:
  the locator must come back byte for byte. Red: Ribbit refused them ("wire slice requires IPv4 RLOC").
  [LCAF_LOCATOR_V1]: a locator arriving as an LCAF AFI-list keeps its complete record (hex) through registration,
  memory, held views and resolution, and goes back out verbatim; its address is the AFI-list's own. Now geo, ELP and
  RLE are PARITY; JSON is A FAIL (lispers.net authenticates it, then drops it -- FINDINGS-FOR-DINO.md).
- REAL-HARDWARE-TESTS.md + tools/acceptance_hw.py: the tests that need two hosts -- DDT (HW.DDT.1-5: Map-Referral
  MS-ACK / MS-NOT-REGISTERED / NOT-AUTHORITATIVE from a map-server-only authority, a map-resolver following the
  referral, the referral cache), map-server peering (HW.PEER.1), Dino's lig (HW.LIG.1); plus the L6 LAN/Internet runs
  and P5 on hardware. Smoke-run here against the co-located lispers.net: every test executes. Known before running:
  Ribbit has no Map-Referral path on the wire (HW.DDT.1-3 red for Ribbit until it does).
- Regression after LCAF locators: build, local, fnw1, handler, oracle 4/4, ryw -- PASS.

## Encryption, DDT referral, crypto-EIDs -- and the full run (58 tests: PARITY 38, A FAIL 11, KNOWN DIFFERENCE 4,
## OBSERVATION 5, B FAIL 0)
- [MAP_REGISTER_ENCRYPTION_V1] lispers.net's `lisp encryption-keys { map-register-key = [id]key }`: an E-bit
  Map-Register is ChaCha20-decrypted (Bernstein's 64-bit nonce, eight ASCII '0's; key left-padded with '0' to 32;
  authentication over the plaintext) before anything else; keys configured through the region API (ms.encryption_key,
  every host Engine holds them, never in memory). L3.12 (right key / wrong key / unknown key-id): PARITY. The ChaCha20
  used by the test was checked identical to lispers.net's chacha.py.
- [DDT_REFERRAL_V1] a front configured as a DDT authority (front.ddt_authority, ms.authoritative_prefix; lisp-service
  does it for a map-server that is not the map-resolver, from "ms_authoritative_prefixes") answers a DDT-originated
  Map-Request with its Map-Reply AND a Map-Referral: MS-ACK (registered prefix, TTL 1440, incomplete), MS-NOT-REGISTERED
  (TTL 1), DELEGATION-HOLE (TTL 15, the least specific prefix around the EID holding no site), NOT-AUTHORITATIVE (host,
  TTL 0) -- following lispers.net's lisp_ms_send_map_referral; checked here against that reading; compared with
  lispers.net on hardware (HW.DDT.1-3b). In the suite's co-located topology neither system sends a referral (L1.13).
- Crypto-EIDs: not testable for parity -- lispers.net 0.643 drops every Map-Register carrying a JSON locator, and both
  the signature and the public key travel in JSON locators (FINDINGS-FOR-DINO.md).
- Test-data fix: L3.12's prefixes sat inside L4.9's flood range; moved.

## [SITE_OPTIONS_V1] -- lispers.net's site options (L2.15, 7 tests, all PARITY)
- A held site now carries shutdown, allowed-rloc, force-ttl, proxy-reply-action, echo-nonce-capable,
  force-proxy-reply and pitr-proxy-reply-drop (site.add writes them into the site cell; the held view parses them).
- Registration: a shut-down site authorizes nothing; with allowed-rlocs, a register whose locators are not all
  allowed finds no authorizing site.
- Answers (one path, answer_request, for IPv4 and IPv6, as lispers.net's lisp_ms_process_map_request): force-ttl ->
  seconds-encoded TTL on positive and negative answers; not-registered-yet -> the requested EID, action 7, TTL 1; a site
  that does not force proxy replies answers a PITR (P bit) with drop when pitr-proxy-reply-drop, or with its
  proxy-reply-action (drop / native-forward), TTL 1440, locators kept; echo-nonce-capable -> the E bit.
- Before: 5 of 7 B FAIL; after: 7 of 7 PARITY, each pinned to lispers.net's observed answer.
- Regression: build, local, fnw1, handler, oracle 4/4, ryw, independent, boundary, service, stress, memory, failloud,
  lock gate 0 -- PASS.

## [MULTICAST_SG_V1] -- (S,G) registrations under a site group-prefix (L2.16, L2.16b)
- The wire path reads RFC 8060 Multicast Info LCAFs (type 9): the source becomes the EID, the group "addr/mask";
  replies carry the (S,G) back. A site added with an address group-prefix is also listed in site-groups|<iid>, and an
  (S,G) is authorized, keyed and answered by the site of the longest group-prefix covering its group. A group that is
  not an address prefix keeps the API's exact-name meaning (test_held_resolver uses one -- it failed until that).
- lispers.net writes (S,G) with two pad bytes (native struct alignment -- FINDINGS-FOR-DINO.md); Ribbit reads that
  layout too and answers in the layout it was asked in. L2.16: Ribbit PASS, lispers.net A FAIL; L2.16b recorded.
- Regression: build, local, fnw1, independent, handler, boundary, service, stress, memory, failloud, lock gate 0 -- PASS.

## Proxy TTL, policies, NAT proxy (L1.14, L2.17.*, L2.15.force-nat-proxy-reply -- all PARITY)
- [PROXY_REPLY_TTL_V1] a proxy Map-Reply carries TTL 1440 whatever the registration's TTL, as lispers.net's map-server
  does (L1.14 found Ribbit returning the registration's 3 minutes). The Dino byte oracle's expected Map-Reply had
  assumed the registration's TTL; it now uses 1440 with the reason, and passes 4 of 4.
- [MS_POLICY_V1] lispers.net's `lisp policy` on the map-server: ms.policy writes a policy cell (match clauses on
  destination-eid and source-rloc; set-action process/drop, set-record-ttl, set-rloc-address); a site's policy_name
  applies it to proxy replies exactly as lisp_ms_process_map_request does -- a match applies its sets, no match is an
  implied drop (action 4, no locators). (A Map-Request here carries no source EID, so source-eid clauses pass, as in
  lispers.net's matcher.)
- force-nat-proxy-reply proxy-replies (with no xTR registered behind NAT, lispers.net's NAT path leaves the set as is).

## [NAMED_LOCATORS_V1] -- named locator objects and policy set-*-name (L2.18.*, 4 tests, PARITY byte for byte)
- ms.named_locator encodes lispers.net's geo-coordinates (from its geo-tag), explicit-locator-path, replication-list-entry
  and json objects exactly as lisp_geo.encode_geo / encode_lcaf do (including the JSON LCAF's length, which lispers.net
  overstates by 2 -- FINDINGS-FOR-DINO.md); a policy's set-geo/elp/rle/json-name puts them into the answer's locator as
  an AFI-list LCAF with the set address, with lispers.net's policy-locator header (255/0/255/0, R). L2.17.set-rloc-address
  now compares that header too.
- Coverage (72-test run): lispers.net 30% of statements, surface 34% of reachable lines, 137 of 315 reachable
  functions; Ribbit 65.5% lines, 72% functions, 44.6% branches. Undriven map-server/map-resolver entry points: only
  map-server-peer, ddt-root, referral-cache (two hosts), eid-crypto-hash (blocked), and the four named-object commands
  (driven by L2.18 from this run on).

## Hardware readiness
- tools/acceptance.py two-machine mode (L6): --agent-listen on the systems' machine (starts, configures, restarts and
  inspects lispers.net and Ribbit there), --agent HOST:PORT on the harness machine (drives it over one TCP connection,
  sends the LISP traffic itself). The restart and RAM-loss tests now go through methods (restart_keep_state,
  lose_ram_host) so they work through the agent. Tried here with two addresses: P4, L1.1, L1.11-L1.14, L2.13,
  L2.15.allowed-rloc, L2.17.set-rloc-address, L3.2, L4.10, L4.11 -- all as on one machine.
- tools/make_hw_configs.py writes the four DDT configurations (lispers.net AUTH/MR lisp.config, Ribbit AUTH/MR
  lisp-service.config) from the suite's fixture.
- DDT referrals: lispers.net 0.643, run here as a map-server-only authority from that configuration, sent Map-Referrals
  to the requester's 4342 with the peers as referral set, MS-ACK TTL 1440, MS-NOT-REGISTERED TTL 1 /32,
  NOT-AUTHORITATIVE TTL 0 (host /32 outside the authority; 198.19.0.0/21 plus peers inside it) -- its log shows each.
  Ribbit's [DDT_REFERRAL_V1] now does exactly that (peers via ms.peer / lisp-service ms_peers; referrals to 4342);
  acceptance_hw.py's HW.DDT.1-3b are pinned to those answers, listen on 4342, and pass for Ribbit as AUTH here.

## Connections up only; the RAM server on the neutral machine (John 2026-09-28)
- The two-machine mode no longer connects into the systems' machine. Agent (--agent --agent-space <RAM>) and harness
  (--agent-space <RAM>) both connect up to the neutral RAM host; commands and answers are cells in acceptance_ctl
  (<agent>|cmd, <agent>|res, instance <run>.<n>), each side holding a read (tuple-write gained a held read: "wait").
- Ribbit can use that RAM host as its memory (--ram-external), in either mode; L4.11 is then not applicable.
- Two faults found by running against a RAM host on a specific address, fixed: the RAM host's own Engines dialed
  127.0.0.1 (now the listen address, loopback only when it listens on every address); lisp-boundary streamed
  '{"ok":true,"result":' before a configuration call that then threw, leaving half a line (answer computed first now).
- Regression: build, local, fnw1, handler, oracle 4/4, boundary, service, independent -- PASS.

## First network run (Pi harness, AI-Host agent, streamingfrog broker) -- and what it exposed
- 76 tests, same classification as loopback: PARITY 54, A FAIL 12, KNOWN DIFFERENCE 4, OBSERVATION 6, B FAIL 0.
- Reading the evidence found three PARITY labels the tests were too loose to deserve:
  - L1.5: lispers.net answers an unregistered EID in an accept-more-specifics site for the EID itself (/32); Ribbit
    answered with the site prefix. [NEGATIVE_PREFIX_V1] fixes Ribbit (ams -> the EID; not ams -> the site prefix, as
    lisp_ms_process_map_request); L1.5 now checks the exact prefix, L1.5b the other kind of site. Two FNW1 contract
    tests had encoded the old reading with an ams site -- they now use a non-ams site, which is what they test.
  - L2.15.shutdown / allowed-rloc looked only at what resolves; lispers.net Map-Notifies those rejected registers.
    Tightened to the L3.3 rule (rejected = not applied and not acknowledged): now A FAIL.
  - L2.9 notify differs (lispers.net's merged Map-Notify goes out late, via retransmission): L2.9b records it.
- The report's wire label said "loopback" for a network run; it now names the harness and systems addresses.
- acceptance.py carries its own version; each test prints a start line, long waits and restarts say so; the agent
  checks tcsh, python and lispers.net's modules before starting anything.

## v0.61-register-path -- the register path, fixed (John 2026-09-28: "absolutely unacceptable. Fix these")
Measured first (one client, sequential registers, one front, one RAM server, registrations already held):
held 0 / 1,000 / 5,000 -> 293 / 182 / stalled reg/s; RAM server 2.40 / 3.90 ms CPU per register; front 0.87 / 1.47 ms.
- [RESIDENT_REGION_V1] The RAM server ran each region operation through a pool of 8 Engines that were clients of
  itself (loopback sessions, copied views of its own rows, a mutex per Engine held across the whole operation, then a
  wait for every Engine to see the write). Now each operation, on its own request thread, runs a resident Engine whose
  memory IS the server's memory (frogram::MemoryApi; ram_server.cpp ResidentMemory): it reads the rows it names under
  their shared locks and writes its own rows under their exclusive locks. No pool, no mutex, no loopback, no views, no
  waits. Keys (site, encryption) are in private rows (service "#lisp-secrets") the network API refuses to serve.
- [HELD_TUPLES_V1] A participant's held tables copied the WHOLE table to publish each arriving cell, and every lookup
  copied every held registration and governance entry into local containers. Now a held table (HeldSlot) holds each
  tuple once behind its own atomic pointer, grouped by its canonical prefix, insert-only; a new cell swings one
  pointer (the old tuple to the EBR). Resolution is a real longest-prefix match: for each length, longest first, the
  one key the target falls under, read in place. registration.put and resolver.wait read the one prefix they want.
- [WRITE_ORDER_LOG_V1] A held read ("what was written past id X") walked every row of the variable at the RAM
  server. Each variable now keeps a write-order log (one node per write, CAS-pushed, id taken inside the push so ids
  fall strictly along it); a held read walks only the writes past X.
- [REGISTER_STRANDS_V1] Map-Registers went to one of 10 workers by a hash of the sender and held it for the whole
  round trip. Now each sender has a strand (its registers stay in order) that runs on its own thread only while it
  has registers; every sender's round trip overlaps every other's.
- [AWAIT_ON_THE_MARK_V1] The read-your-own-write wait polled the applied mark every 20 us for the whole wait; it now
  sleeps on the mark's futex and the watch that advances the mark wakes it (only when someone waits).
After (same measurement): 757 / 725 / 717 reg/s -- flat; RAM server 0.57 / 0.60 / 0.63 ms; front 0.63 / 0.70 / 0.67 ms.
With a 24 ms round trip between front and RAM server (a delaying TCP relay; tc netem is not available here):
1 client 38 reg/s (p50 26 ms), 4 clients 139 (3.7x), 16 clients 343 (p50 48 ms). Next serialization, measured, not
yet fixed: at 16 clients the p50 is two round trips -- a register's own write reaches its view only on the view's
NEXT held read when one is already in flight, so the read-your-own-write wait pays up to one more round trip.
Regression: build, local, fnw1, handler, oracle 4/4, ryw, boundary, service, independent, memory (lock gate) -- PASS.
- Acceptance on v0.61 (loopback, full suite): 78 tests -- PARITY 53, A FAIL 14, KNOWN DIFFERENCE 4, OBSERVATION 7,
  B FAIL 0 (acceptance/ACCEPTANCE-REPORT-v0.61.md).

## v0.62-own-writes -- the held-read turnaround
- Measured on v0.61 (front <-> RAM server 24 ms round trip through a delaying relay): 16 clients 343 reg/s, p50 48 ms --
  two round trips. A register waited for its own write to come back through its view's held read; when that read was
  already on its way back, the write arrived only with the NEXT one.
- [OWN_WRITES_HELD_V1] John 2026-09-27: a put is complete when its own consequences are observable to the participant.
  The region operation now returns the cells it wrote (cells_json, beside written); the front holds them in its views
  at once -- the same tuples, by the same ids, that its watches bring later. HeldSlot became versioned and multi-writer
  (insert by compare-and-swap; a put replaces a tuple only with a newer cell id, so the same cell is held once and an
  older one never overwrites a newer). The front waits only for written cells it could not hold (a length its views do
  not hold yet).
- The first try sent the cells as a JSON array of objects; the semantic wire's reply template declined it
  ("pair_heuristic") and every client call failed -- 45 FNW1 errors. They now travel as JSON text, as the result does.
- After (same relay): 1 client 37 reg/s (p50 26.6 ms), 4 clients 145, 16 clients 567 (p50 28.1 ms) -- one round trip,
  near-linear. Held 0 / 1,000 / 5,000: 764 / 718 / 720 reg/s, CPU per register flat.
- Regression: build, local, fnw1, handler, oracle 4/4, ryw, boundary, service, independent -- PASS.
- Re-measured on the packaged build (2026-09-28): relay 24 ms RTT -- 1 client 37 reg/s p50 26.6 ms; 4 clients 146 p50
  27.4 ms; 16 clients 561 p50 28.3 ms. Held 0 / 1,000 / 5,000: 572 / 712 / 739 reg/s; front 0.53-0.67 ms and RAM
  server 0.53-0.60 ms CPU per register (flat).
- Acceptance on v0.62 (loopback, full suite, a clean run -- an earlier background run overlapped another process in its
  log and was discarded): 78 tests -- PARITY 53, A FAIL 14, KNOWN DIFFERENCE 4, OBSERVATION 7, B FAIL 0; every test the
  same classification as v0.61 (acceptance/ACCEPTANCE-REPORT-v0.62.md).

## v0.63-bounded -- a RAM server that stays up must not grow (John 2026-09-28: streamingfrog's v0.56 RAM server reached
## 2.85 GB in under 10 hours, left 20 MB free, and a build on that machine stalled in the kernel's page reclaim)
- [MONITOR_ROWS_V1] The performance monitors wrote a NEW row every second (and per run boundary); rows are never freed.
  Each monitor now owns two rows and overwrites them; the harness writes one boundary row; the harness holds a read on
  the sample rows during the run and keeps the history itself. Checked: after a run the RAM server holds perf|sample 2
  rows, perf|snap 2, perf|phase 1; the timeline still has every machine's samples.
- [WRITE_WINDOW_V1] v0.62's write-order log added a node per write and never freed one. Replaced by a fixed window of
  the last 1,024 writes per variable (position claimed by compare-and-swap, id taken inside the claim, slot published by
  stamping its position); a held read walks the window back to its id, and when the window no longer reaches that far
  (or a slot it needs was reused) the variable's rows answer. tools/test_write_window.cpp (now in the memory stage):
  exact answers inside and beyond the window, 4 racing readers over 160,000 writes never miss a final write, and
  400,000 further writes grow the process by 64 kB (v0.62's log: +12,460 kB -- the test fails it).
- [EBR_OWNED_READERS_V1] Found by measuring the RAM server end to end: ~35 bytes per register still leaked. Every Ebr
  registered a record per thread and never freed them, and the RAM server makes a resident Engine (with its own Ebr) per
  operation on its own thread. Records are now freed with their Ebr, and a thread tells Ebrs apart by serial (a later
  Ebr at a reused address is a different one).
- Measured (front + RAM server here, 16 clients, rounds of 10,000 registers to the same 500 EIDs): before the EBR fix the
  RAM server grew every round (16.4 -> 21.0 MB over 12 rounds); after, 17.1-17.5 MB from round 2 to round 12.
- Regression: build, local, fnw1, handler, oracle 4/4, ryw, boundary, service, independent, stress, memory (lock gate,
  write window), lock-free snapshot, EBR stress (born = dead) -- PASS.
- Acceptance on v0.63 (loopback, full suite, clean run): 78 tests -- PARITY 53, A FAIL 14, KNOWN DIFFERENCE 4,
  OBSERVATION 7, B FAIL 0; every test the same classification as v0.62 (acceptance/ACCEPTANCE-REPORT-v0.63.md).

## acceptance.py after v0.63 (Python only, no rebuild) -- from the first full hardware run
- [MONITOR_ROWS_V1] edge(): each monitor's snapshot read AT each boundary; read after the run (as before), the start
  snapshot had already been overwritten by the end one on the hardware (CPU columns empty for every run).
- [SCALE_FROM_FRESH_V1] every scale step starts from a freshly started system: on the hardware, lispers.net after the
  10,000-host step answered the next step (nested, 1 registration) at 95/s against 504 -- withdrawn entries still cost.
- [LOAD_PROCESSES_V1] --load-procs (default: the machine's cores): the clients spread over processes; one Python process
  held the Pi's load generator near one core (Ribbit 18,460 lookups/s at 64 clients used 1.1 cores). Latencies, errors
  and the children's measured CPU come back to the harness; load CPU = children + harness.
- The performance banner names this file's version (it said v0.60).
Checked here: a performance-only run through the monitors with 4 load processes -- no missing snapshot, every CPU column.

## v0.63-bounded, second issue -- documentation and the evidence
- README.md rewritten: installing, building and running the three roles, the RAM host at an address and port of
  the user's choosing (with the warning that it has no authentication of its own), the matched audit, running
  lisp-service outside the tests. The previous README (the conformance adapter) is README-CONFORMANCE-SUITE.md.
- RIBBIT-LISP-REPORT.md: the whole project, component by component as the code is organized -- lispers.net's way,
  the change, the result -- plus methodology, results (behaviour, performance, structure) and interpretation.
- tools/matched_audit.py (new): the scope-matched audit; the source is checked against the installed release's
  bytecode before anything is measured.
- evidence/2026-09-28/: the hardware run (acceptance, performance, timeline, chart) and the audit.
