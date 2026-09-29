# The experiment

What was tested, how, and what came out. The full account, component by component, is `RIBBIT-LISP-REPORT.md`;
how to run everything yourself is `README.md`.

## The question

A distributed control plane is conventionally built as processes that each hold their own state and exchange
messages to keep one another informed. FrogNet's proposition is that the state can instead live in shared memory
that every participant reads and writes directly, even across the Internet, and that much of the machinery the
conventional shape needs then has nothing to do.

The experiment asks three questions of one real system:

1. **Behaviour.** Does a control plane built on shared memory do the same job, on the wire, as a mature
   conventional one?
2. **Performance.** What does it cost, in throughput, latency and CPU, when the shared memory is on the Internet?
3. **Structure.** How much code does the same job take?

## The subject and the control

- **Control:** lispers.net 0.643, Dino Farinacci's LISP implementation, unmodified -- the Python 3 release, run
  with its own `RUN-LISP`. LISP was chosen because its map-server is a textbook shared-state problem (sites,
  registrations and keys that every request consults), and because lispers.net is the reference implementation by
  the protocol's inventor, not a strawman.
- **Subject:** Ribbit-LISP v0.63, a C++ implementation of the same map-server and map-resolver, written against
  lispers.net as the reference, with all of its state in FrogNet shared memory on a separate host.
- **The job,** fixed before measuring: the map-server and map-resolver -- registration with authentication and
  encryption, requests (ECM and bare), notifications, Info-Request, DDT referrals as an authority, sites and their
  options, policies, named locators, registration state and expiry, and their configuration. Other LISP roles,
  pub-sub, crypto-EIDs and operator surfaces are outside the job on both sides.

## What was held equal, and what was not

Held equal: the host both systems ran on (one at a time), the test fixture (sites, keys, prefixes), the packets,
the load generator, the tests, and the complexity tool and its settings.

Not equal, and to be read with the results:

- **The programming model** -- the variable under test.
- **The language:** C++ against Python. This is a confound; section 10 of the report discusses what the results can
  and cannot separate.
- **Where the state lives:** lispers.net's in its own process; Ribbit's on an Internet host, a 2-core DigitalOcean
  droplet, reached over the open Internet on every state change.

## How each question was measured

**Behaviour -- a differential test (`tools/acceptance.py`).** 78 tests, each sending the same packets to both
systems and comparing their answers with each other and with the RFCs. Each test is labelled PARITY (both correct
and identical), A FAIL (lispers.net does not do what is expected), B FAIL (Ribbit does not), KNOWN DIFFERENCE (two
defensible readings of the RFC) or OBSERVATION (the RFCs do not settle it). When lispers.net stops answering, its
log is kept as evidence and it is restarted.

**Performance -- the same harness, same systems, same run.** Map-Registers, Map-Requests and a mix, at 1, 4, 16 and
64 concurrent closed-loop clients; lookups against 1, 100, 1,000 and 10,000 registrations held, each step from a
fresh start; 5 seconds of warm-up and 20 measured per point. Monitors on every machine record CPU per operation,
machine load and network bytes at the edges of each measured run, so the cost is counted where it is paid --
including on the RAM host.

**Structure -- a matched audit (`tools/matched_audit.py`).** Seven rules, fixed in the script before measuring:
the same job, the same exclusions with reasons, reachability from each system's own entry points, Ribbit's test
backend excluded, Ribbit's FrogNet platform reported in its own row, one complexity tool, and the lispers.net
source verified against the release's bytecode (26 of 26 files) before anything is counted. Every function on
both sides is listed, in or out, with its reason.

**The arrangement.** A test machine (Raspberry Pi 5) sends the traffic; a systems machine runs both systems; the
RAM host is a third machine on the Internet. Every connection goes up to the RAM host; the machines coordinate
through it.

## How the protocol was arrived at

The harness was corrected twice during the hardware runs, and the reported numbers are from the first run with
both corrections:

- the monitors' run-start snapshots were being read after the run ended, when they had already been overwritten;
- scale steps were inheriting the previous step's registrations, which inflated lispers.net's table in the nested
  sweep.

The load generator was also spread over several processes, after one Python process was found using about one
core at the highest load. The reported run is one complete campaign; nothing in it is taken from another run.

## What came out

| | lispers.net 0.643 | Ribbit-LISP v0.63 |
|---|---|---|
| behaviour, 78 tests | 14 failures | 0 failures; 53 byte-for-byte parity |
| lookups/s, 64 clients (p50) | 484 (130 ms) | 20,407 (2.9 ms) |
| lookups/s at 10,000 registrations | 93 | 9,123 |
| CPU per lookup, 1 -> 10,000 registrations | 1.6 -> 11.4 ms | 44 -> 47 µs |
| registers/s, 1 / 64 clients | 357 / 718 | 39 / 1,565 |
| application code for the job: functions / NLOC / total CCN | 360 / 6,463 / 2,237 | 175 / 1,886 / 1,175 |
| with the whole FrogNet platform charged to it | -- | 568 / 4,972 / 3,283 |

In one sentence: the shared-memory implementation does the same job, answers lookups one to two orders of
magnitude faster and without cost growth as the table grows, pays one Internet round trip per register but
overlaps them, and takes about half the functions and half the total complexity for the job -- more than
lispers.net's if the whole reusable platform is charged to this one application.

## How to challenge it

- **Behaviour:** `evidence/2026-09-28/ACCEPTANCE-REPORT.md` has every stimulus and both systems' answers; re-run
  any test with `--only`.
- **Performance:** `perf.json` holds every counter from every machine for every run; the timeline shows each
  machine's CPU second by second.
- **Structure:** dispute any line of `lispers-functions.csv` or `ribbit-functions.csv`, or any rule in
  `matched_audit.py`, and re-run it; the numbers follow.
- **All of it:** `README.md` runs the whole experiment on your own machines, with your own RAM host.
- **On a clean network:** every reported run was made on machines that have had FrogNet installed for a long time.
  Running the three roles on configurations that have never seen FrogNet is the test this experiment has not had
  yet; `README.md` section 8 says what to send back.
