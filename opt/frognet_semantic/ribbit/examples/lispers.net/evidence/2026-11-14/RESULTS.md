# Ribbit-LISP v0.75 against lispers.net 0.643 over the Internet -- results of 2026-11-14

This page stands on its own. Every number is from `acceptance-v0.82-run.txt` in this folder (the run's whole
terminal output) or from `MATCHED-AUDIT.md` (the code audit), and the line each comes from can be found by
searching for it there.

## The arrangement

- **Load generator and harness:** New-York-2 (NY2), a FrogNet node in New York, public address 71.125.44.88.
- **Both map-servers:** Seattle7 (10.170.170.1), a FrogNet node in Seattle. lispers.net 0.643 answers on port
  4342; the Ribbit-LISP front answers on port 14342. Each system is measured alone, the other stopped.
- **Ribbit's memory:** the RAM host `lisper-ram`, on a 2-core DigitalOcean droplet in San Francisco
  (streamingfrog.com:17283). lispers.net keeps its state in its own process.
- **Paths:** NY2 reaches Seattle7 through the RAM host, as FrogNet's WAN design requires (no node-to-node tunnel).
  The harness measured a 152.6 ms round trip from NY2 to Seattle7 through it. Every operation below therefore pays
  roughly 100 ms of network before either system does any work.
- **Load:** closed loop. Each client sends a request and waits for its answer before sending the next, so
  throughput cannot exceed clients divided by round-trip time. On this path the ceiling is about 1,450--1,500
  operations a second, whatever the server.
- **Builds:** Ribbit-LISP `v0.75-front-decides`, harness `acceptance.py v0.82-front-decides`, lispers.net 0.643
  from its release under Python 3.12.

## Behaviour: 78 tests

| | lispers.net 0.643 | Ribbit-LISP v0.75 |
|---|---|---|
| PARITY (both pass, same answer) | 53 | 53 |
| A FAIL (lispers.net does not do what the test expects) | 14 | 0 |
| KNOWN DIFFERENCE | 4 | -- |
| OBSERVATION | 7 | 7 |

lispers.net stopped serving seven times and the harness restarted it each time: after SHA-1 authentication
(`byte_swap_64`, a TypeError), a wrong key-id (a KeyError building the Map-Notify) and malformed or invalid records
(`add_cache`). Ribbit-LISP kept serving through every test. After a restart of the map-server Ribbit-LISP still
resolved its registrations; lispers.net had lost them.

## Throughput, operations a second (p50 ms in brackets)

| clients | lispers.net reg | Ribbit reg | lispers.net lookup | Ribbit lookup | lispers.net mixed | Ribbit mixed |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 (104) | 9 (102) | 7 (112) | 6 (102) | 6 (111) | 6 (103) |
| 4 | 30 (106) | 32 (102) | 33 (113) | 30 (103) | 27 (114) | 33 (102) |
| 16 | 125 (108) | 151 (103) | 86 (176) | 137 (103) | 86 (173) | 136 (103) |
| 64 | 186 (346) | 555 (107) | 87 (703) | 497 (106) | 95 (691) | 556 (106) |
| 256 | 0 | 1,357 (173) | 0 | 1,402 (175) | 0 | 1,391 (172) |
| 1,024 | 0 | 1,474 (682) | 0 | 1,468 (669) | 0 | 1,479 (668) |
| 2,048 | 0 | 184 (723) | 0 | 247 (677) | 0 | 30 (1,042) |

"0" means no request was answered within the one-second limit. From 256 clients up lispers.net answered nothing.

Peak against peak: lookups 1,468 against 87 (**17X**), registrations 1,474 against 186 (**7.9X**), mixed 1,479
against 95 (**15.6X**). At 64 clients, where lispers.net peaks: lookups 5.7X, registrations 3.0X, mixed 5.9X.

At one client both systems take the same time per operation: that time is the network.

**2,048 clients.** All three Ribbit modes fell, each with a reset of NY2's session to the RAM host. The RAM host's
log shows NY2's session at a 428 ms round trip at the reset; Seattle7's session was unaffected. NY2's uplink is a
USB network device (`cdc_ncm`), and at 1,024 clients it was already carrying about 1,500 operations a second each
way plus the harness's own sessions. The 2,048 rows measure NY2's link, not either map-server.

## CPU, seconds per million operations

| | lispers.net (its process) | Ribbit front | Ribbit RAM host |
|---|---:|---:|---:|
| lookups, 64 clients | 13,088 | 96 | 4 |
| lookups, 1,024 clients | -- | 88 | 1 |
| registrations, 64 clients | 6,424 | 1,011 | 554 |
| registrations, 1,024 clients | -- | 885 | 392 |

Per lookup that is about 13 ms of CPU for lispers.net and about 0.1 ms for Ribbit's front and RAM host together,
roughly **140X less**.

## As the table grows (16 clients, lookups)

| registrations | lispers.net host prefixes | Ribbit host prefixes | lispers.net nested | Ribbit nested |
|---:|---:|---:|---:|---:|
| 1 | 140 (105) | 138 (102) | 134 (104) | 138 (103) |
| 100 | 128 (105) | 136 (103) | 118 (111) | 138 (102) |
| 1,000 | 110 (117) | 149 (103) | 117 (111) | 125 (103) |
| 10,000 | FAILED | 128 (102) | -- | -- |
| 6,877 (nested) | -- | -- | 46 (352) | 126 (103) |

- **10,000 host prefixes:** 6 of 25 sampled EIDs still did not resolve on lispers.net 85.5 s after the preload.
  On Ribbit all 25 resolved 2.9 s after it.
- **Nested prefixes:** the generator's nested set stops at 6,877 registrations for both systems. The sampled EIDs
  resolved 2.2 s after the preload on Ribbit and 60.7 s after it on lispers.net.
- **CPU per lookup** at the largest table: lispers.net 23,550 s per million (nested), Ribbit's front 119 and its RAM
  host 24. Ribbit's front stays between 105 and 128 s per million from 1 to 10,000 registrations.

## Code for the same job (matched audit, v0.75)

| | functions | NLOC | total CCN |
|---|---:|---:|---:|
| lispers.net 0.643, application (Python) | 360 | 6,463 | 2,237 |
| Ribbit-LISP v0.75, application (C++) | 195 | 2,067 | 1,318 |
| Ribbit-LISP v0.75 with the whole FrogNet platform charged to it | 985 | 8,326 | 5,495 |

The application is **68% smaller** in NLOC (3.1 times) and **41% lower** in total cyclomatic complexity. With the
whole FrogNet platform charged to it, Ribbit-LISP is larger than lispers.net; the platform is shared by every
Ribbit conversion and is not specific to LISP. Python and C++ lines are not the same unit; CCN compares more
directly. The audit's source check passed: all 26 lispers.net source files compile to the release's bytecode.

## Limits

- One run, one arrangement: one load generator, one map-server machine, one Internet path, one 2-core RAM host.
- The load is closed loop, so above about 1,000 clients the path, not the servers, sets the ceiling.
- One Ribbit front was measured. Several fronts sharing one registration database is how v0.75 is built; it has
  not been measured with two.
- The 28 September 2026 run (one host, Ribbit-LISP v0.63, `../2026-09-28/RESULTS.md`) is a different
  arrangement. Its numbers are not comparable row for row with these.
