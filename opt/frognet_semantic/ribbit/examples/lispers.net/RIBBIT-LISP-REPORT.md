# Ribbit-LISP: lispers.net's map-server and map-resolver on FrogNet shared memory

John Fawcett, Fawcett Innovations LLC -- report for package v0.63-bounded, 2026-09-28

This report describes what was built, component by component in the order the code is organized; how lispers.net
does the same job; what changed and why; how both were tested; what the results were; and how far the results can
be read. A two-page description of the experiment is `README-EXPERIMENT.md`; the installation and run
instructions are in `README.md`.

---

## 1. Summary

Ribbit-LISP is a C++ implementation of the LISP map-server and map-resolver (RFC 9300, RFC 9301 and the LCAF,
DDT and NAT-traversal pieces lispers.net implements), written against lispers.net 0.643 as the reference. Its
state -- sites, registrations, keys, policies, named locators -- lives in FrogNet shared memory on a separate
host (in the runs reported here a 2-core DigitalOcean droplet on the open Internet). The map-server front that
receives LISP packets holds no private copy of that state and takes no locks around it.

Measured on one run (2026-09-28; a Raspberry Pi 5 load generator, both systems on one host, Ribbit's memory on
the droplet):

| | lispers.net 0.643 | Ribbit-LISP v0.63 |
|---|---|---|
| behaviour: 78 tests | 14 failures | 0 failures; 53 byte-for-byte parity with lispers.net |
| lookups/s, 64 clients (p50) | 484 (130 ms) | 20,407 (2.9 ms) |
| lookups/s at 10,000 registrations, 16 clients | 93 | 9,123 |
| CPU per lookup, 1 -> 10,000 registrations | 1.6 -> 11.4 ms | 44 -> 47 µs |
| registers/s, 1 / 64 clients | 357 / 718 | 39 / 1,565 |
| application code for the same job (matched audit) | 360 functions, 6,463 NLOC, total CCN 2,237 | 175 functions, 1,886 NLOC, total CCN 1,175 |
| ... with the whole FrogNet platform charged to it | -- | 568 functions, 4,972 NLOC, total CCN 3,283 |

The reading of these numbers, their limits, and what they do not show are in section 9.

---

## 2. The experiment

**Control.** lispers.net 0.643, Dino Farinacci's implementation, unmodified: the Python 3 release
(`lispers.net-release-py3-0.643.tgz`), run with its own `RUN-LISP` and `lisp.config`. The source measured by the
audit (the `lisp/` directory of the lispers.net repository) compiles to bytecode identical to that release: 26 of
26 files, checked by `tools/matched_audit.py` every time it runs.

**Subject.** Ribbit-LISP v0.63-bounded, this package.

**The job.** The map-server and map-resolver as the acceptance suite exercises them: Map-Register with
authentication (HMAC-SHA-1-96, HMAC-SHA-256-128) and encryption (ChaCha20); Map-Request, ECM-encapsulated and
bare; Map-Notify and Map-Notify-Ack; Info-Request (NAT traversal); the Map-Referral a map-server sends as DDT
authority; sites and their options; policies; named locators (geo-coordinates, ELP, RLE, JSON); registration
state, merge semantics and expiry; the configuration that sets these up. Not in the job, on either side: the ITR,
ETR and RTR roles and the data plane, a map-resolver following referrals as a DDT client, crypto-EID signatures,
pub-sub, and operator surfaces (show, debug, API, web UI).

**The question.** Whether a LISP control plane written against shared memory -- state as cells in a memory every
participant reads and writes directly, instead of state held privately in a process and moved between processes by
messages -- behaves the same on the wire, how it performs, and how much code the same job takes.

---

## 3. How lispers.net does the job

lispers.net runs as several Python processes on one host, started by `RUN-LISP`:

- **lisp-core** owns the LISP control port (UDP 4342). `lisp_core_dispatch_packet` receives every control packet
  and relays it to the process that handles it over local IPC (`lisp_ipc`: datagram sockets named after the
  process, packets segmented up to 9000 bytes, with retries and short sleeps). lisp-core also reads `lisp.config`
  and relays each configuration command to the role processes, and serves the web UI and the API.
- **lisp-ms** (map-server) and **lisp-mr** (map-resolver) each run a main loop that receives relayed packets and
  commands and dispatches them: `lisp_parse_packet` for packets, `lispconfig.lisp_process_command` for commands.
- **State lives inside the process that owns it.** Sites and registrations are Python objects (`lisp_site`,
  `lisp_site_eid`) in the map-server's `lisp_sites_by_eid`, a `lisp_cache` (a dictionary by mask length plus a
  sorted list). A lookup is served from that process's memory; another process that needs the state must be sent
  it or must ask.
- **Time-driven work is done by timers.** `lisp_timeout_sites` runs every 60 seconds
  (`LISP_SITE_TIMEOUT_CHECK_INTERVAL`) and expires registrations whose TTL has passed. Map-Notifies that expect
  an acknowledgement are queued and retransmitted every 2 seconds (`LISP_MAP_NOTIFY_INTERVAL`).
- **Packet codecs** are Python classes in `lisp.py` (`lisp_map_register`, `lisp_map_request`, `lisp_eid_record`,
  `lisp_rloc_record`, ...) with `encode` and `decode` methods. `lisp_process_map_register` and
  `lisp_process_map_request` carry the map-server's semantics; `lispconfig.py` parses `lisp.config` and holds the
  named-locator commands.

This is the conventional shape of a network control plane, and a mature one: lispers.net implements far more than
this job (every LISP role, DDT, NAT traversal with RTRs, pub-sub, crypto-EIDs, telemetry), and the parts of it
measured here are a fraction of the whole.

---

## 4. How Ribbit-LISP is organized

| code | role | lispers.net counterpart |
|---|---|---|
| `ribbit_cpp/lisp_engine.hpp` | the Engine: LISP state as cells, every map-server operation, the wire codecs | `lisp.py` codecs and processing; `lisp-ms.py` site state and timers; `lispconfig.py` |
| `ribbit_cpp/lisp_handler.hpp` | the front: UDP control port, Map-Requests answered in place, Map-Registers sent to the memory | `lisp-core.py` dispatch; `lisp-ms.py` / `lisp-mr.py` main loops |
| `tools/lisp_boundary.cpp` | the front as a program (configuration as JSON lines on stdin); used by the tests | `RUN-LISP` + `lisp.config` |
| `tools/lisp_service.cpp` | the deployable service: one program, configured by a file, roles registered as capabilities | `RUN-LISP`, `lisp enable`, `lisp.config` peer lists |
| `tools/ramsrv/ram_server.cpp` | the RAM host: FNW1 sessions, the memory, and the LISP operations run inside it | none (lispers.net's state is in its processes) |
| `ribbit_cpp/ram_memory.hpp` | the memory: rows, per-row locks, waits, the write window | `lisp_cache`, the process heap |
| `ribbit_cpp/frogram.hpp/.cpp` | the client library: sessions to the RAM host, reads, writes, held reads | `lisp_ipc`, `lisp_send`, `lisp_receive` |
| `ribbit_cpp/ebr.hpp` | epoch-based reclamation for lock-free readers | the Python garbage collector |
| `ribbit_cpp/fnwp_*.hpp`, `semwire.hpp`, `semcodec.hpp`, `semtpl.hpp`, `dataplane.hpp` | FNW1, FrogNet's semantic wire: templates, SAME/DIFF, the data socket | none |
| `ribbit_cpp/ribbit_lisp.cpp` | a conformance command-line tool with an in-process backend, for tests only | -- |
| `tools/acceptance.py`, `tools/qualify.sh`, `tools/matched_audit.py`, ... | the tests (section 6) | -- |

The FrogNet parts (RAM host, memory, client library, EBR, FNW1) are the **platform**: they are not LISP-specific,
and the matched audit reports them in their own row.

Each design decision in the code carries a marker such as `[RESIDENT_REGION_V1]`; the sections below cite them so
a reader can find the code, and `CHECKPOINT-*.md` record when and why each was made.

---

## 5. Component by component

### 5.1 `ribbit_cpp/lisp_engine.hpp` -- the Engine

**What lispers.net does.** Registration state is a set of Python objects inside lisp-ms. `lisp_process_map_register`
decodes the packet record by record, authenticates it, finds the covering site in `lisp_sites_by_eid`, and updates
or creates a `lisp_site_eid`. `lisp_process_map_request` looks the EID up in the same structure and builds the
Map-Reply. Expiry is a 60-second timer sweep. Configuration commands build the site objects.

**What changed.** The Engine keeps no LISP state of its own. Every piece of state is a cell -- a value at a
(service, variable, instance) coordinate in the shared memory -- and every map-server operation reads and writes
cells:

- **The state model.** A site is a cell under `site|<iid>|<group>|<length>`, instance = its prefix; a registration
  is a cell under `registration|<iid>|<group>|<length>`, instance = its prefix (and xTR-id when merging); its
  governance is a cell beside it; small manifest cells list which prefix lengths exist, so a reader visits only
  lengths that are there. Keys are held in private rows the RAM host never serves to the network
  (`[RESIDENT_REGION_V1]`).
- **One call per operation (`[VENDOR_API_V1]`).** A mutating operation -- a whole Map-Register with its
  authentication, site policy, registration, governance and Map-Notify -- is one request to the LISP region's own
  interface on the RAM host, not a sequence of reads and writes across the network.
- **The operation runs in the memory (`[RESIDENT_REGION_V1]`).** On the RAM host, the same Engine code runs with
  the memory itself as its backend: it reads the rows it names under their shared locks and writes its own rows
  under their exclusive locks. There is no loopback session, no copied view, no Engine pool and no global lock.
  (Until v0.61 the RAM host ran each operation through a pool of Engines that were network clients of itself;
  replacing that is the largest single performance change in section 7.3.)
- **Registration semantics.** All records of a Map-Register are validated before any is applied, so a register
  either applies whole or not at all (lispers.net applies the valid records of a partly unauthorized register;
  section 8.3). A key on a site means only authenticated registers are accepted (`[KEYED_SITE_REQUIRES_AUTH_V1]`).
  Merge registrations are kept per xTR-id and resolved as the union (`[WIRE_XTR_ID_V1]`). A withdrawal is honoured
  only from a registered RLOC. Expiry is decided when the registration is read -- a registration past its TTL
  does not resolve -- so there is no sweeper timer.
- **Resolution (`[HELD_TUPLES_V1]`).** A lookup is a longest-prefix match: for each registered length, longest
  first, the one key the target falls under at that length is read in place. Its cost depends on the number of
  prefix lengths, not on the number of registrations.
- **The wire.** Map-Register and Map-Request codecs for IPv4 and IPv6 EIDs with locators of either family
  (`[REGISTER_ANY_AFI_V1]`, `[LOCATOR_ANY_AFI_V1]`); instance-IDs in LCAF type 2 (`[LCAF_INSTANCE_ID_V1]`); LCAF
  locators kept byte for byte (`[LCAF_LOCATOR_V1]`); named locators encoded exactly as lispers.net's own encoder
  encodes them (`[NAMED_LOCATORS_V1]`); map-server policies (`[MS_POLICY_V1]`); every site option lispers.net has
  -- shutdown, allowed-rloc, force-ttl, proxy-reply-action, echo-nonce-capable, force-proxy-reply,
  force-nat-proxy-reply, pitr-proxy-reply-drop (`[SITE_OPTIONS_V1]`); ChaCha20-encrypted Map-Registers
  (`[MAP_REGISTER_ENCRYPTION_V1]`); multicast (S,G) EIDs, LCAF type 9 (`[MULTICAST_SG_V1]`); the negative-reply
  prefix rules (`[NEGATIVE_PREFIX_V1]`); proxy Map-Replies with TTL 1440 (`[PROXY_REPLY_TTL_V1]`).

Most of these wire details were found by the acceptance suite: each marker's comment names the test (L1.5, L1.8,
L1.14, L2.9, L2.12, L2.14, L2.15, L2.16, L2.17, L2.18, L3.5, L3.12) that first showed Ribbit differing from
lispers.net.

**Results.** Every one of the 53 PARITY tests (section 8.1) passes byte for byte against lispers.net's answers,
including the named-locator and policy encodings. Lookup cost is flat from 1 to 10,000 registrations (section 7.2).

### 5.2 `ribbit_cpp/lisp_handler.hpp` -- the front

**What lispers.net does.** lisp-core receives on UDP 4342 and relays each control packet to lisp-ms or lisp-mr
over local IPC; that process decodes it, consults its own state and answers.

**What changed.** The front is one process that owns the control port and holds no state of its own:

- **Map-Requests are answered in place.** The front holds, for each instance and group, the registrations it has
  read, as tuples: each one held once behind its own atomic pointer, replaced in place when the cell changes
  (`[HELD_TUPLES_V1]`, `[ONE_SET_OF_VIEWS_PER_PARTICIPANT_V1]`). A lookup reads them lock-free and never goes to
  the RAM host. The front learns of changes by holding a read on the memory, which returns when something is
  written.
- **Map-Registers go to the memory.** Each is one call to the RAM host. Each sender gets its own strand, so one
  sender's registers stay in order while every sender's round trip overlaps every other's (`[REGISTER_STRANDS_V1]`);
  a Map-Request never waits behind a Map-Register (`[BOUNDARY_NEVER_WAITS_V1]`).
- **Read-your-own-write.** When the call returns, the cells it wrote come back with the reply and the front holds
  them at once (`[OWN_WRITES_HELD_V1]`), so a register followed immediately by a lookup through the same front
  sees the registration (L2C.1).
- **No locks (`[BOUNDARY_NO_LOCKS_V1]`).** Queues are lock-free; waiting is on futex words; held tuples are
  reclaimed by EBR.
- **The rest of the control plane.** ECM-encapsulated Map-Requests (`[ECM_MAP_REQUEST_V1]`); Info-Request ->
  Info-Reply (`[INFO_REQUEST_V1]`); Map-Notify-Ack consumed; Map-Referrals when configured as a DDT authority
  (`[DDT_REFERRAL_V1]`); Map-Notify sent to every registered xTR of a merged prefix (L2.9b).

**Results.** Lookups: 20,407/s at 64 clients, p50 2.9 ms, 29 µs of CPU each, with the RAM host untouched (0.2 CPU-s
per million lookups). Registers scale with clients: 39/s for one client (each is one Internet round trip), 1,565/s
for 64.

### 5.3 `tools/lisp_boundary.cpp` and `tools/lisp_service.cpp` -- the programs

**What lispers.net does.** `RUN-LISP` starts lisp-core, which starts the role processes; peers (map-servers,
map-resolvers) are written by hand in `lisp.config`; an ETR re-sends its whole Map-Register every 60 seconds.

**What changed.** `lisp-boundary` is the front as a program: `--ram HOST PORT --udp PORT`, configuration as JSON
operations on stdin; the acceptance and performance tests drive it. `lisp-service` is the deployable form: one
program, one JSON configuration (`/etc/lispers.d/lisp-service.config`), every role it plays registered in the
memory as a capability tuple (`[CAPABILITY_REGISTRATION_V1]`), so machines find each other by reading the memory
instead of a hand-written peer list. `tools/make_hw_configs.py` writes matching configurations for lispers.net and
Ribbit from the same fixture.

### 5.4 `tools/ramsrv/ram_server.cpp` and `ribbit_cpp/ram_memory.hpp` -- the RAM host (platform)

**What lispers.net does.** Nothing corresponding: its state is in its processes.

**What it is.** One program that serves FNW1 sessions and holds the memory:

- **Rows and locks (`[ROW_LOCKS_V1]`).** A cell is a row with its own multi-reader/single-writer lock; there is
  no other lock on the data. Tables of rows and variables are insert-only and lock-free to search.
- **Held reads.** A participant can read "whatever is written past id X", blocking until something is. Each
  variable keeps a fixed window of its last 1,024 writes (`[WRITE_WINDOW_V1]`), so a held read reads only the new
  writes; if the window no longer reaches back to X, the variable's rows answer instead.
- **The LISP region's operations run here (`[RESIDENT_REGION_V1]`, section 5.1).**
- **Bounded memory (v0.63).** A long-running RAM host must not grow: the write window replaced a log that grew
  with every write; EBR records are freed with their owner (`[EBR_OWNED_READERS_V1]`); the performance monitors
  overwrite their rows instead of adding one per second.

**Results.** Register cost at the RAM host, measured in development with one client and 0 / 1,000 / 5,000
registrations already held: 2.40 / 3.90 ms of CPU per register and 293 / 182 / stalled registers/s before v0.61;
0.57 / 0.60 / 0.63 ms and 757 / 725 / 717/s after. Memory over 120,000 registers to the same prefixes: flat at
17.1-17.5 MB after the first round. On the hardware run, 1,565 registers/s kept the 2-core droplet 69% busy: for
registers, the RAM host is the next limit.

### 5.5 `ribbit_cpp/frogram.hpp`, `frogram.cpp` -- the client library (platform)

A session to the RAM host carries many calls at once; writes, reads and held reads address cells by
(service, variable, instance). `[READ_YOUR_OWN_WRITE_V1]`: a call returns the ids of what it wrote, so a
participant can wait until its own view has them; `[AWAIT_ON_THE_MARK_V1]`: that wait sleeps on a futex, not a
poll. `[HIGH_SPEED_DATA_SOCKET_V1]`: large bodies travel on a separate data socket in fair segments so they do not
hold up small requests. `[RESIDENT_REGION_V1]`: the same interface is implemented by the memory itself, which is
how the Engine runs inside the RAM host.

### 5.6 `ribbit_cpp/ebr.hpp` -- reclamation (platform)

Readers of held tuples take no locks, so replaced tuples cannot be freed while a reader may hold them;
epoch-based reclamation frees them when no reader can. `[EBR_OWNED_READERS_V1]` (v0.63) ties each thread's record
to the EBR instance that made it, found by measuring the RAM host's memory end to end.

### 5.7 FNW1 -- `fnwp_*.hpp`, `semwire.hpp`, `semcodec.hpp`, `semtpl.hpp`, `dataplane.hpp` (platform)

FrogNet's semantic wire between participants and the RAM host: requests and replies are described by templates;
a repeated request or an unchanged answer travels as a reference (SAME) or as the fields that changed (DIFF)
instead of in full. Each piece is checked against FrogNet's Python reference implementation byte for byte by the
qualification oracles (section 6.1). lispers.net has no counterpart; its processes exchange raw packets over local
IPC.

### 5.8 `ribbit_cpp/ribbit_lisp.cpp` -- the conformance tool (tests only)

A command-line Engine with an in-process backend (no RAM host), used by the `local` qualification stage. The
in-process branches it needs inside the Engine are excluded from the matched audit (section 6.4).

---

## 6. How it was tested

Four independent kinds of evidence, each answering a different question.

### 6.1 Qualification -- `tools/qualify.sh` (does the code do what it says?)

Run on any single Linux machine; each stage starts a fresh local RAM host. The stages:

| stage | what it checks |
|---|---|
| build | every program compiles warning-free (`-Werror`); compiler output is shown, never hidden |
| local | the conformance suite against the Engine with its in-process backend |
| fnw1 | the same conformance suite through the RAM host over FNW1 |
| independent | each test on a fresh server, so no test depends on another's state |
| oracle | the byte oracle: Ribbit's packets compared with lispers.net's own encoders (needs `LISPERS_ROOT`) |
| semwire, semcodec, semtpl | FNW1's framing, codec and templates against FrogNet's Python reference (needs `FROGNET_SEMANTIC_ROOT`) |
| handler | the front: election, advertisement, codec, the UDP boundary, sites |
| fnwp, dataplane | the wire engines end to end over the RAM host; sessions and the data socket over real TCP |
| boundary | a Map-Request never waits behind Map-Registers, with a 22 ms wire |
| ryw | read-your-own-write: put -> get and delete -> get through one participant |
| memory | the RAM host's memory: row locks only; waits and wake-ups; the write window's exact answers, racing readers that never miss a write, memory flat under writes |
| failloud | the RAM host disappears: one answer naming it, a clean exit, no abort |
| service | two machines from one package: capability tuples, discovery, registration, heartbeat |
| stress | multi-record registers applied all-or-nothing under concurrency |
| lockfree | lock-free snapshot check and an EBR stress test (every object born is freed) |
| perf, bytes | development performance and bytes-on-the-wire measurements |

Qualification tests Ribbit against its own contract. It does not compare with lispers.net, except for the byte
oracle.

### 6.2 Acceptance -- `tools/acceptance.py` (does it behave like lispers.net on the wire?)

A differential test. The same packets are sent to lispers.net and to Ribbit, and their answers are compared with
each other and with what the RFCs require. Every test runs on both systems and gets one of these labels:

- **PARITY:** both behave as the RFCs require, and in every byte the test checks, identically.
- **A FAIL:** lispers.net does not do what the test expects; Ribbit does.
- **B FAIL:** Ribbit does not; lispers.net does.
- **KNOWN DIFFERENCE:** both are defensible readings of the RFC and they differ; the difference is recorded, not
  scored.
- **OBSERVATION:** the RFCs do not settle it; what each does is recorded.

When lispers.net stops answering after a test, the harness records its log as evidence, restarts it and carries
on. Tests are grouped as P4 (liveness), L1 (the protocol: register, notify, request, ECM, IPv6, Info-Request,
Map-Notify-Ack, DDT), L2 (registration semantics: refresh, change, withdraw, expiry, more-specifics, multi-record,
longest match, merge, instance-IDs, site deletion, LCAF locators, site options, (S,G), policies, named locators),
L2C (causality across clients), L3 (authentication, encryption, replay, multi-record atomicity) and L4 (robustness:
truncation at every byte, bad types, bad counts, bad AFIs and masks, oversize datagrams, a flood of malformed
packets, extremes, restart).

**The three-machine arrangement.** The test machine sends the traffic; the systems machine runs lispers.net and
Ribbit's front side by side; the RAM host runs on a third, neutral machine on the Internet. Every connection goes
up to the RAM host -- nothing connects into the test or systems machines. The harness and an agent on the systems
machine (which starts, configures and restarts both systems) coordinate through the RAM host itself.

### 6.3 Performance -- the second half of `tools/acceptance.py` (what does it cost?)

Run after the behaviour tests, on the same systems and the same arrangement.

- **Workloads:** `reg` (Map-Registers from a pool of 1,000 prefixes), `req` (ECM Map-Requests for registered EIDs),
  `mixed` (80% requests, 20% registers), each at 1, 4, 16 and 64 concurrent clients; and two scale sweeps -- host
  prefixes and nested prefixes -- with 1, 100, 1,000 and 10,000 registrations held, at 16 clients.
- **Protocol:** each client is a closed loop (send, wait for the answer, send again); 5 seconds of warm-up, 20
  seconds measured. Every scale step starts from a freshly started system, so no step inherits another's table.
  The load generator spreads its clients over several processes (4 on the Pi).
- **What is measured:** throughput, latency (p50, p95, p99), errors, unanswered requests; and on every machine,
  from monitors that publish their counters at the edges of each run: CPU seconds per million operations by
  process, machine busy %, and network bytes per operation. `perf.json` keeps every counter;
  `PERF-TIMELINE.html` plots each machine's CPU second by second.

### 6.4 Structure -- `tools/matched_audit.py` (how much code does the job take?)

A scope-matched complexity audit with its rules fixed in the script before anything was measured:

1. The job is as defined in section 2.
2. The same exclusions apply on both sides (other roles, features outside the job, operator surfaces), each with
   its reason.
3. Ribbit's in-process test backend is excluded.
4. What is in scope is found by reachability from each system's own entry points (static call graphs; Python
   method calls resolved by rapid type analysis).
5. Ribbit's FrogNet platform is reported in a second row, whole.
6. One tool (lizard) and one setting on both sides; Ribbit functions whose test-only branches were removed are
   recounted by lizard's own rule, checked against lizard beforehand.
7. The source measured must compile to the release's exact bytecode (26 of 26 files), or nothing is measured.

Every function on both sides is listed, in or out and why, in `lispers-functions.csv` and `ribbit-functions.csv`,
so any inclusion decision can be disputed line by line and the numbers recomputed.

### 6.5 Limits of the method

- One run on one LAN, with one Internet path to one RAM host. The numbers are this arrangement's, not general
  limits.
- Both systems ran on the same host, one at a time.
- The load generator is Python. At 64 clients it used about 1.5 of the Pi's 4 cores; it was not the limit of
  these runs, but it is not free.
- lispers.net expires registrations with a 60-second sweep, so expiry timing differs between the systems by
  design; L2.5 records how long each takes rather than scoring it.
- Coverage: the tests exercised about 36% of lispers.net's map-server/map-resolver code (statements) and 65% of
  Ribbit's code (`COMPLEXITY-AND-COVERAGE.md`).

---

## 7. Results -- performance (run of 2026-09-28)

Arrangement: Raspberry Pi 5 (FrogNetHost) -> AI-Host (Intel i7-7567U) running both systems -> Ribbit's memory on
streamingfrog.com (2-core DigitalOcean droplet). All numbers are from one run; `perf.json` holds them all.

### 7.1 Throughput and latency

| workload | clients | lispers.net ops/s (p50) | Ribbit ops/s (p50) |
|---|---:|---|---|
| lookups | 1 | 334 (2.9 ms) | 891 (1.0 ms) |
| lookups | 4 | 516 (7.6 ms) | 3,014 (1.2 ms) |
| lookups | 16 | 484 (31.9 ms) | 9,398 (1.6 ms) |
| lookups | 64 | 484 (130.2 ms) | 20,407 (2.9 ms) |
| registers | 1 | 357 (2.8 ms) | 39 (25.1 ms) |
| registers | 4 | 793 (4.8 ms) | 161 (24.4 ms) |
| registers | 16 | 769 (20.2 ms) | 591 (26.3 ms) |
| registers | 64 | 718 (85.2 ms) | 1,565 (38.1 ms) |
| mixed | 1 | 334 (3.0 ms) | 161 (1.3 ms) |
| mixed | 4 | 491 (8.1 ms) | 581 (1.4 ms) |
| mixed | 16 | 491 (32.8 ms) | 2,326 (1.9 ms) |
| mixed | 64 | 495 (132.3 ms) | 5,125 (2.7 ms) |

Errors: none. Unanswered: none, except one request in Ribbit's mixed 4-client run (one of about 11,600).

### 7.2 Lookups as the table grows (16 clients)

| registrations held | lispers.net host / nested | Ribbit host / nested | CPU per lookup (host): lispers.net / Ribbit |
|---:|---|---|---|
| 1 | 938 / 924 | 9,210 / 9,346 | 1,579 / 44 µs |
| 100 | 877 / 821 | 9,213 / 9,272 | 1,654 / 42 µs |
| 1,000 | 508 / 486 | 9,044 / 9,063 | 2,458 / 46 µs |
| 10,000 | 93 / 114 | 9,123 / 9,164 | 11,373 / 47 µs |

### 7.3 Where the work goes

| | lispers.net | Ribbit front | Ribbit RAM host |
|---|---|---|---|
| CPU per lookup, 64 clients | 2,586 µs | 29 µs | ~0 (0.2 CPU-s per million) |
| CPU per register, 1 client | 1,773 µs | 2,340 µs | 3,504 µs |
| CPU per register, 64 clients | 1,650 µs | 680 µs | 815 µs |
| bytes per lookup at the systems host | ~213 | ~212 | -- |
| bytes per register at the systems host, 64 clients | ~276 | ~1,725 | ~1,475 at the RAM host |
| systems host busy, lookups at 64 clients | 33% | 18% | -- |
| RAM host busy, registers at 64 clients | -- | -- | 69% |

Development measurement (container, not the hardware run): with a 24 ms round trip between front and RAM host,
registers went from 38/s (1 client) to 139/s (4 clients) and 343/s (16 clients, p50 two round trips) in v0.61, and
to 37 / 146 / 561 per second (16 clients, p50 28 ms: one round trip) in v0.62, once a register's own writes were
held from its reply instead of waiting for the next held read.

---

## 8. Results -- behaviour (78 tests)

PARITY 53, A FAIL 14, KNOWN DIFFERENCE 4, OBSERVATION 7, B FAIL 0. Ribbit failed no test. The full evidence --
every stimulus, both systems' answers, lispers.net's log where it stopped -- is in `ACCEPTANCE-REPORT.md`.

### 8.1 PARITY (53)

Both systems behave as required and identically in every byte checked: registration and notification with HMAC;
M-bit clear; ECM and IPv6 requests; the negative-reply prefix rules inside and outside sites; refresh, locator
change, withdrawal (and withdrawal from a non-registered source ignored), expiry; more-specifics accepted and
rejected; multi-record registers; longest-prefix match; merge semantics; instance-ID isolation; site deletion and
restoration; Info-Request/Info-Reply; Map-Notify-Ack; LCAF geo, ELP and RLE locators byte for byte; ChaCha20-
encrypted registers; the site options force-ttl, not-registered-yet, echo-nonce, drop, pitr-drop and
force-nat-proxy-reply; policies (set-record-ttl, set-action drop, set-rloc-address); named locators through policy
(geo, ELP, RLE, JSON) byte for byte; proxy-reply TTL; causality across clients; every robustness test in L4 that
lispers.net survived.

### 8.2 A FAIL (14): where lispers.net 0.643 does not do what the test expects

Stops serving (the map-server no longer answers; the harness restarts it):

- **L3.2, L3.9:** a correctly authenticated HMAC-SHA-1-96 Map-Register.
- **L3.4:** a Map-Register with an unknown key-id.
- **L4.4:** an EID record with an unknown AFI, or an IPv4 mask greater than 32.
- **L3.10.first/middle/last.invalid:** a three-record Map-Register with one malformed record.

Authentication and policy:

- **L3.3:** a Map-Register with the wrong key is answered with a Map-Notify (not applied).
- **L3.5:** a Map-Register with no authentication, for a site that has a key, is accepted and resolves.
- **L3.6:** a Map-Register with one bit flipped (in the authentication data, or in the body) is answered with a
  Map-Notify (not applied).
- **L2.15.shutdown, L2.15.allowed-rloc:** a register the site's configuration rejects is still answered with a
  Map-Notify.

Encodings:

- **L2.14.json:** a JSON LCAF locator is not registered.
- **L2.16:** an RFC 8060 multicast (S,G) register is not accepted. lispers.net encodes (S,G) with two pad bytes
  RFC 8060 does not have (L2.16b), so the two layouts do not interoperate. Ribbit currently accepts both.

`FINDINGS-FOR-DINO.md` has each with its stimulus and lispers.net's log.

### 8.3 KNOWN DIFFERENCE (4)

**L3.7, L3.10.first/middle/last.unauthorized:** a multi-record Map-Register in which one record is outside every
site. lispers.net applies the valid records and sends a Map-Notify; Ribbit applies none and sends none. Ribbit
treats a Map-Register as one unit, which is one reading of RFC 9301; lispers.net's is another. It is recorded,
not scored, and it is a question for lispers.net's author.

### 8.4 OBSERVATION (7)

- **L1.7:** a bare (non-ECM) Map-Request to the map-resolver port. lispers.net does not answer it; Ribbit answers.
- **L1.13:** a DDT-originated Map-Request to a combined map-server/map-resolver. Neither sends a Map-Referral; both
  answer with a Map-Reply. The DDT-authority configuration, where both send referrals, is covered by the two-host
  tests in `REAL-HARDWARE-TESTS.md`.
- **L2.9b:** a merge register from a second xTR. Ribbit sends the merged Map-Notify to every registered xTR;
  lispers.net's first send does not leave lisp-core, and only its 2-second retransmission reaches the last xTR.
- **L2.16b:** lispers.net's padded (S,G) layout. Both accept it.
- **L3.8:** the same accepted Map-Register replayed. Both accept it again.
- **L4.10:** a restart of the map-server with live registrations. lispers.net loses them; Ribbit keeps them,
  because they are in the memory, not in the process.
- **L4.11:** the RAM host lost mid-run. Not applicable in this arrangement.

---

## 9. Results -- structure

### 9.1 The matched audit (same job, same rules, `MATCHED-AUDIT.md`)

| | functions | NLOC | total CCN | average CCN | functions over CCN 15 |
|---|---:|---:|---:|---:|---:|
| lispers.net 0.643, application (Python) | 360 | 6,463 | 2,237 | 6.2 | 26 |
| Ribbit-LISP, application (C++) | 175 | 1,886 | 1,175 | 6.7 | 12 |
| Ribbit-LISP, application + the whole FrogNet platform | 568 | 4,972 | 3,283 | 5.8 | 43 |
| the FrogNet platform alone | 393 | 3,086 | 2,108 | 5.4 | 31 |

For the application: 51% fewer functions, 71% fewer lines, 47% less total cyclomatic complexity, and less than
half as many functions over CCN 15. The average complexity per function is not lower (6.7 against 6.2): the job
takes fewer functions, not simpler ones.

With the entire FrogNet platform charged to this one application, Ribbit has fewer lines (4,972 against 6,463)
but more functions and about 47% more total complexity. The platform is counted whole: the RAM host, FNW1, the
semantic codec and templates, the client library, the memory and EBR serve every FrogNet application, not only
this one. lispers.net's own infrastructure in `lisp.py` (IPC, sockets, timers) is counted in its application row,
because it is lispers.net's code; its Python runtime is not.

### 9.2 Coverage under the acceptance suite (`COMPLEXITY-AND-COVERAGE.md`)

The 78 tests ran 65% of the statements in Ribbit's code (its 19 C++ files, platform included) and 36% of the
statements in lispers.net's map-server/map-resolver code. Of the functions with CCN 16-50, 87% of Ribbit's ran and
43% of lispers.net's map-server/map-resolver functions.
`Engine::dispatch()`, the one very large Ribbit function (CCN 548 in v0.63), is mostly branches for other roles
and test operations; the matched audit counts only its in-scope branches (`DISPATCH-DISSECTION-v0.63.md`).

---

## 10. Interpretation

**What the evidence supports.**

- *Ribbit-LISP does the job.* 78 tests, no Ribbit failure, 53 of them byte for byte identical to lispers.net's
  answers, across a real network with the state on an Internet host.
- *Lookups do not pay for where the state lives.* A Map-Request is answered from tuples the front holds, read in
  place, lock-free: 29-47 µs of CPU, the same bytes on the wire as lispers.net, no traffic to the RAM host, and no
  cost growth from 1 to 10,000 registrations. lispers.net's lookup cost grows about 7x over the same range, and it
  stops scaling at about 500 lookups/s while its host still has capacity.
- *Registers pay one round trip, and overlap.* Each register is one call to the RAM host, so a single client is
  bounded by the round trip (39/s at about 25 ms). Many senders' round trips overlap, so throughput grows with
  concurrency (1,565/s at 64 clients) until the RAM host's CPU is the limit (69% busy on 2 cores). Per register,
  the CPU across front and RAM host at load (about 1.5 ms) is close to lispers.net's (about 1.65 ms). Registers
  are not where Ribbit saves work; lookups are.
- *The same job takes less application code.* Under rules fixed before measuring, and with the source verified as
  the release: 51% fewer functions and 47% less total complexity than lispers.net's code for the same job.
- *State outlives the process.* A restarted Ribbit front keeps every registration (L4.10), because the process
  never held them.

**What the evidence does not show.**

- *That this is architecture rather than language.* Ribbit is C++ and lispers.net is Python; lines are not the
  same unit, and C++ typically needs more lines than Python for the same logic. The register results are the
  clearest separation: where Ribbit pays a network round trip it is slower per operation for a single client,
  whatever the language; where it reads memory in place it is faster by orders of magnitude.
- *Which code is absent.* The audit shows how much code the job takes on each side, not which kinds of code are
  missing. The classification into LISP semantics, coordination and configuration is by name patterns, and too
  crude to carry that claim.
- *General limits.* These are one arrangement's numbers: one LAN, one Internet path, one 2-core RAM host. What
  bounds Ribbit's lookups beyond 20,407/s was not established; the systems host was 82% idle.
- *The platform's cost.* Charged whole to this one application, the FrogNet platform makes Ribbit larger in total
  complexity than lispers.net. Whether that is the right charge depends on how many applications share it.

---

## 11. Open items

- **L3.9, intermittent.** In one of four hardware runs, Ribbit's front stopped answering during the
  authentication matrix. It passed in the other three and in every container run. The cause was not established;
  the logs from that run were overwritten.
- **One unanswered request** in the mixed 4-client run (1 of about 11,600).
- **(S,G) layout.** Ribbit accepts both RFC 8060's layout and lispers.net's padded one. Whether to keep accepting
  the padded layout is open, pending which layout lispers.net intends (RFC 8060 is being revised as 8060bis).
- **Not yet run on hardware:** the map-resolver following referrals (HW.DDT.4-5), map-server peering
  (HW.PEER.1), and `lig` (HW.LIG.1).
- **Not implemented, and not testable for parity:** crypto-EID signatures, `require-signature` and `encrypt-json`
  -- lispers.net does not register JSON-locator registers, so there is no reference behaviour to match.

---

## 12. The files

The evidence for this report is in `evidence/2026-09-28/`:

| file | what it holds |
|---|---|
| `ACCEPTANCE-REPORT.md` | every test: stimulus, both answers, classification, evidence |
| `PERF-REPORT.md`, `perf.json`, `PERF-TIMELINE.html`, `performance-2026-09-28.png` | the performance run |
| `RESULTS.md` | a one-page summary of the run |
| `matched-audit/` | `MATCHED-AUDIT.md`, `lispers-functions.csv`, `ribbit-functions.csv` |
| `COMPLEXITY-AND-COVERAGE.md`, `DISPATCH-DISSECTION-v0.63.md` | coverage, and the dispatcher's composition |

`DOCUMENTS.md` lists every document in the package. In particular: `README.md` (installing and running
everything, including the RAM host),
`FINDINGS-FOR-DINO.md` (the lispers.net findings, each with its stimulus and log),
`../../docs/PROGRAMMING-RIBBIT-BEST-PRACTICES.md` (the programming rules the code follows), and `CHECKPOINT-*.md` (the
engineering log: every change, when and why).
