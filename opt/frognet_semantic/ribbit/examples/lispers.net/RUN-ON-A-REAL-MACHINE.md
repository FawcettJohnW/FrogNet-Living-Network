# Ribbit-LISP v0.52 -- stand-alone qualification

Runs on any Linux machine. It does not need to be a FrogNet node: the package carries everything the gates compare
against.

- `frognet_semantic_ref/` -- the FrogNet semantic engine's Python (`core/`, `proxy/`, `daemon/`) exactly as shipped in
  `frognet-world-20260926-wire-order.tgz`. Code only: no configuration, no credentials. The S1/S2/S3 oracles run the
  C++ against it.
- `third_party/lispers.net/` -- Dino Farinacci's `lisp/lisp.py`, unmodified, with his Apache 2.0 `LICENSE` and
  `NOTICE` (`SOURCE.txt` records where it came from and its sha256). The byte oracle runs the C++ against it.
- `tools/ramsrv/ram_server.cpp` -- the RAM server (FNWP engines in its socket loop; `--listen`, `--api`). The build
  stage compiles it; on another machine build it the same way (below).

## Requirements
```
sudo apt install -y build-essential libssl-dev liblz4-dev python3 python3-pip
pip install lz4 future mysql-connector-python        # add --break-system-packages on Debian 12 / Ubuntu 24.04
```
`mysql-connector-python` is only imported by the reference Python (no database is used). The UDP registrar tests bind
127.0.0.1-127.0.0.7: Linux routes all of 127/8 to loopback, macOS needs aliases.

## Run everything on this machine
```
unzip ribbit-lisp-v0.52-session-dataplane.zip && cd ribbit-lisp-conformance
tools/qualify.sh
```
Each stage starts its own fresh RAM server on 127.0.0.1:8788. Results: `results/<timestamp>/SUMMARY.txt` plus every
stage's raw output. Nothing is left running.

## Against a RAM server on another machine (the cross-Internet run)
The server must be v0.52 too: the client opens three sockets (requests, returns, data) and a pre-v0.52 server refuses
the data socket ("does not speak the full FNWP contract"). On the server machine, from the same package:
```
g++ -std=c++17 -O2 -pthread -Iribbit_cpp tools/ramsrv/ram_server.cpp ribbit_cpp/frogram.cpp -llz4 -lcrypto -o ribbit_cpp/ram-server
./ribbit_cpp/ram-server --listen 0.0.0.0:8789          # --api /Vendor.Product.ram_interface.php to rename the endpoint
```
Then on this machine:
```
RIBBIT_RAM_EXTERNAL=1 RIBBIT_RAM_HOST=streamingfrog.com RIBBIT_RAM_PORT=8789 tools/qualify.sh
```
With `RIBBIT_RAM_EXTERNAL=1` the script never starts, stops or restarts the server, so all stages share one memory;
the tests clean up after themselves.

## Variables
| Variable | Meaning |
|---|---|
| `STAGES` | subset of `build local fnw1 independent oracle semwire semcodec semtpl handler fnwp dataplane stress lockfree perf bytes` (default: all) |
| `RUNS` | repeats of each independent-process test (default 3) |
| `RIBBIT_RAM_HOST` / `RIBBIT_RAM_PORT` | the RAM server (default 127.0.0.1:8788) |
| `RAM_SERVER` | server binary the script may start (default `./ribbit_cpp/ram-server`, built by the build stage) |
| `RIBBIT_RAM_EXTERNAL=1` | use an already-running server |
| `FROGNET_SEMANTIC_ROOT` | override the Python reference (default `frognet_semantic_ref/`; a node's `/opt/frognet_semantic` also works) |
| `LISPERS_ROOT` | override the lispers.net tree (default `third_party/lispers.net/`) |
| `FROGRAM_TRACE` | `1` (stderr) or a file path: every FNWP frame in and out, both ends |

## What pass looks like
| Stage | Expected |
|---|---|
| build | `build PASS` |
| local, fnw1 | `OK (skipped=1)` (the one skip is the withdrawn `site_cache.list`) |
| independent | every test N/N; cross_process_ram both PASS |
| oracle | `4 of 4 PASS` |
| semwire / semcodec / semtpl | `RESULT PASS`, 0 differ; each mutant set all caught |
| handler | `RESULT PASS lisp_handler: 0 fail` |
| fnwp | PASS, seeds 1-3, on `/ram.php` and `/Fawcett.RibbitLISP.ram_interface.php` |
| dataplane | session over TCP PASS on both endpoints; fan-in PASS (small operations near idle latency while large ones transit; the small large-transfer finishes long before the big one) |
| stress | 600 x unauthorized, 0 partial applications, each run |
| lockfree | `pointer_atomic_lock_free=YES`, born == dead |

The mutant sets (semwire, semcodec, semtpl) take the longest: up to an hour each on a small machine.

## Relative numbers: unmodified lispers.net through the same suite ([PER_OPERATION_TIMING_V1])
`run_conformance.py` times every adapter call and prints a per-operation table (calls, errors, median, p90, p99, max,
total). The same tests, in the same order, run against lispers.net's own API (`--adapter control`) or against Ribbit
(`--adapter command`). `--timings FILE` writes the table as JSON; `--repeat N` runs the suite N times for more samples.

1. Start lispers.net as Dino documents it (`docs/how-to-install-linux.txt` in his tree: the apt and pip packages from
   `build/Dockerfile`, tcsh), with map-server, map-resolver, ITR and ETR enabled in `lisp.config`, and its API on
   **port 8800** (8080 is Apache on a FrogNet host). `RESTART-LISP` passes its first argument to `lisp-core` as the
   API port (SSL on any port; a leading `-` turns SSL off):
   ```
   cd ../lispers.net/lispers.net-master/lisp && ./RESTART-LISP 8800
   ```
2. The baseline (root has no password by default):
   ```
   LISP_ALLOW_MUTATION=1 python3 run_conformance.py --adapter control --source ../lispers.net/lispers.net-master \
       --host 127.0.0.1 --port 8800 --user root --password '' --repeat 5 --timings lispers.json
   ```
3. Ribbit, same suite, against the RAM server you are measuring:
   ```
   LISP_ALLOW_MUTATION=1 python3 run_conformance.py --adapter command \
       --command './ribbit_cpp/ribbit-lisp --ram streamingfrog.com 8789' \
       --capabilities "$(grep -o -- '--capabilities [^ ]*' run_ribbit_local.sh | cut -d' ' -f2)" \
       --repeat 5 --timings ribbit.json
   ```
4. Side by side (ratio = Ribbit median / lispers.net median):
   ```
   tools/compare_timings.py lispers.json ribbit.json
   ```
Only operations both adapters implement are compared; the rest are listed. The control adapter maps the suite's
operations onto lispapi's (map_cache, map_resolver, map_server, database_mapping, site_cache); the registration,
resolution and wire tests use operations lispapi has no call for and are skipped under it, so they have no baseline
here -- that comparison is the UDP one (Map-Register / Map-Request on the wire), not yet built.

## The LISP service ([LISP_SERVICE_V1], [CAPABILITY_REGISTRATION_V1])
`lisp-service [--config FILE] [--service]` is the one program a machine runs from the package. Its configuration is
`/etc/lispers.d/lisp-service.config` (JSON), unless `--config` names another file; `--service` runs it in the
background the way lispers.net's RUN-LISP runs lisp-core -- detached, output to `logs/lisp-service.log` beside the
program, pid in `logs/lisp-service.pid`.
It opens the session (the semantic socket and the high-speed data socket) and registers each role it plays as a
capability tuple of its own, addressed by what reaches it -- `lisp / capability|map-server / udp:A:P`,
`capability|map-resolver / udp:A:P`, `capability|etr / xtr:<id>`, `capability|itr / participant:<id>` -- never by
which machine offers it. It starts its roles, waits `startup_wait_s`, then each role reads, with one plain read, the
capabilities it uses (an ETR the map-servers, an ITR the map-resolvers, a map-server the ETRs' prefixes). The
heartbeat rewrites its capability tuples every `heartbeat_s`. SIGTERM or SIGINT stops it cleanly. The fields are
documented at the top of tools/lisp_service.cpp; tools/test_lisp_service.py writes two complete configurations.
