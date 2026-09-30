# Ribbit-LISP conformance + control characterization

This directory is deliberately implementation-neutral. The same behavioral tests target Dino Farinacci's `lispers.net` control and Ribbit-LISP.

## Control, non-destructive

```sh
./run_conformance.py --adapter control \
  --source /path/to/lispers.net-master \
  --host CONTROL_HOST --user USER --password PASSWORD --port 8080
```

Use `--http` if the control API is HTTP rather than HTTPS.

## Isolated control mutation tests

Only on a disposable/known test configuration:

```sh
LISP_ALLOW_MUTATION=1 LISP_TEST_RESOLVER=192.0.2.254 \
./run_conformance.py --adapter control ...
```

The test uses `try/finally` to remove its fixture.

## Ribbit adapter contract

A Ribbit executable may initially expose a tiny JSON-lines shim. It receives:

```json
{"operation":"system.get","args":{}}
```

and returns:

```json
{"ok":true,"result":{}}
```

Run it with:

```sh
./run_conformance.py --adapter command \
  --command '/path/to/ribbit-lisp-test-adapter' \
  --capabilities system.get,map_cache.list
```

This shim is a test boundary, not a proposed production protocol.

## Optional control telemetry

Apply `artifacts/control-instrumentation.patch` to a clean control tree. Set `LISP_METRICS_FILE=/tmp/lisp-metrics.jsonl` for the API/core processes. With the variable unset, telemetry is disabled. The patch records REST client elapsed time/bytes, IPC send bytes/segments, IPC lock wait, and IPC reply wait/bytes.

`tools/characterize_source.py /path/to/lispers.net-master` records static structural counts for active `lisp/` and `apps/` source only.

## v0.3 RAM-backed green-field slice

Build the Ribbit-LISP engine:

```sh
cd ribbit_cpp
g++ -std=c++17 -O2 -pthread ribbit_lisp.cpp frogram.cpp -o ribbit-lisp
```

Build/run the reference C++ FrogNet RAM server from the FrogNet source, then target it with:

```sh
LISP_ALLOW_MUTATION=1 ./run_conformance.py --adapter command \
  --command './ribbit_cpp/ribbit-lisp --ram 127.0.0.1 18788' \
  --capabilities system.get,map_cache.list,map_resolver.add,map_resolver.get,map_resolver.delete,map_cache.add,map_cache.get,map_cache.delete
```

`ribbit_cpp/ram_probe.cpp` verifies held-read wakeup plus FNW1 semantic counters. `tools/cross_process_ram.py` verifies shared visibility and replacement across two independent Ribbit-LISP processes.


## v0.7 full Ribbit conformance command
```sh
g++ -std=c++17 -O2 -pthread ribbit_cpp/ribbit_lisp.cpp ribbit_cpp/frogram.cpp -lcrypto -o ribbit_cpp/ribbit-lisp
PYTHONDONTWRITEBYTECODE=1 LISP_ALLOW_MUTATION=1 python run_conformance.py --adapter command --command './ribbit_cpp/ribbit-lisp' --capabilities system.get,map_cache.list,map_cache.get,map_cache.add,map_cache.delete,map_resolver.add,map_resolver.get,map_resolver.delete,database_mapping.add,database_mapping.delete,registration.put,registration.delete,resolution.get,wire.register4,wire.request4,wire.register6,wire.request6,wire.auth_verify,wire.register4_notify,site.add,site.delete,ddt.add,ddt.get,ddt.delete
```
`site_cache.list` is intentionally absent: it is not implemented.

`wire.etr_request4` (v0.33): ETR Map-Request answered from held database mapping. See CHECKPOINT-v0.33.md.
