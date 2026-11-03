# The RAM manager on the Ribbit platform (fdf-ram), replacing rina-host

**John, 2026-11-02:** "We should not be running rina_host." "YES! Move fully to the RAM manager model."

rina-host is FD&F's own copy of the v0.63 RAM server, patched at build time (`tools/build.sh`, 30-odd `apply_*.py`)
and carrying the region (`src/fdf_region.cpp`, `src/daf_region.cpp`, `src/ctl_host.cpp`). The platform
(`opt/frognet_semantic/ribbit/cpp`, `ribbit::RamHost`) took FD&F's host work on 2026-10-17 ([PLATFORM_FROM_FDF_V1]) but
not the region hooks. The RAM manager becomes a platform vendor, `fdf-ram`: the platform's host, with FD&F's endpoint,
FD&F's write contract and the region as its operations. Every RAM FD&F runs (a region's, an app's, the kart's) is
`fdf-ram`; one that holds no region simply never gets a region call.

## What rina-host's server gives the region, and what the platform gives instead

| rina-host (server patch / call) | what it is | on the platform |
|---|---|---|
| `op=rina`, `op=daf` (inline), `op=rina_block` ([REGION_FAST_PATH_V1]) | the region's calls | `handles`/`operation`; `inline_operation` for rina and daf |
| `rina_defer` / `rina_answer` ([DEFERRED_ANSWER_V1]) | a region call answered later | `ribbit::defer_answer` / `ribbit::answer_later` |
| `rina_session_id` ([SESSION_IS_LIVENESS_V1]) | the session of this call | `ribbit::current_session()` |
| `rina_session_peer`, `rina_session_down` ([SESSION_PEER_V1]) | a session's address; its end | `session_up(id, peer)`, `session_down(id)` |
| `[REGION_COALESCE_PER_SESSION_V1]` | identical region calls from two sessions are two calls | new: `coalesces(op)` (default true); fdf-ram says false for rina, rina_block, daf |
| `own:true` → `rina_own_cell` ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]) | a write that goes with its session | the platform's `own_flag_decides()` (already FD&F's contract) |
| `permanent:true` → `rina_permanent_cell` ([PERMANENT_CELL_V1]) | durable, filed before it is published, written back at start | new: `keeps_permanent()` + `permanent(s, v, i, bag)` (the platform refuses `permanent` without it) |
| `(pond, config)` refused ([NET_CONFIG_IS_THE_RAM_MANAGERS_V1]) | written only by the RAM manager from net.json | new: `refuse_write(s, v, i)` ("" allows) |
| `rina_host_listening` ([HOST_ANNOUNCES_ITSELF_V1], [BOUND_PORT_SAID_V1]) | at listening: DAF file, permanent cells back, net config, LISP self-registration | new: `listening(bound)` |
| `rina_resident_memory`, `_wait`, `_wait_raw`, `_get_raw`, `_get_raw_id` ([RINA_RESIDENT_V1]) | the memory itself, in this process | adapters over `RamHost::memory()` (`ramm::Memory`) |
| `op=lisp` ([VENDOR_API_V1]) | v0.63's lispers.net example op | not carried: nothing in FD&F calls it (FD&F's LISP mapping is in the region, `lisp_area.hpp`) |
| `--api PATH` | the endpoint | `api_path()` = `/FawcettInnovations.RINA.ram_interface.php`; `--api` accepted when it is that |
| `RINA_REGION_FILE`, `RINA_NET_CONFIG`, `RINA_MAP_AREA`, `RINA_HOST_ROLE`, `RIBBIT_TEMPLATES` | the region's settings | unchanged |

Nothing else in the region code changes: it is written against `frogram::MemoryApi` and the calls above.

## The descriptor leak (found on StreamingFrog, 2026-11-02)

rina-host 6 days up: 1,021 sockets held, ~110 of them still TCP connections; the rest were sessions shut down at their
end and never closed, because something still held the `Session` (`~Session` closes them). The platform frees a session
the same way, so the leak is chased in the platform host, with the session's holders logged at its end
([SESSION_HOLDERS_SAID_V1]) and a test that ends sessions in each state. The RAM manager units also get
`LimitNOFILE=65536`.

## Callers switched (2026-11-03)

On 2026-11-02 this section said the callers were switched; they were not (found 2026-11-03, John: "Why is rina_region
still here?"): `fdf-app`, `fdf-org`'s spaces, `fdf-region-create`'s unit, the kart and mapping scripts and every test
still started `rina-host`, and nothing built `fdf-ram`. Now: `tools/build.sh`, `build_static_host.sh` and
`build_arm64.sh` build `fdf-ram` and remove rina-host; every caller in `fdf/bin`, the bundles, the kart, the simulator
tiers and the tests runs `fdf-ram` (the kart's `--rina-host PATH` is `--fdf-ram PATH`); the update tarball removes
`/opt/fdf/bin/rina-host` and points each existing `fdf-ram-AREA.service` at `fdf-ram`.

The region's sources were renamed the same day (John: "Yes on the rename if the file is actually used"; it is: `fdf-ram`
links it and answers `op=rina` and `op=rina_block` with `fdf_region_call` / `fdf_region_block`): `src/fdf_region.cpp`,
`src/door/fdf_region.h`, `src/fdf_region_client.hpp` (namespace `fdf_region`, used by `fdf-client`, `fdf-events`,
`fdf-participants`, `rina-node`, `rina-lisp`, the DAF client). The wire's op names (`rina`, `rina_block`) are unchanged:
every door, Python library and Kotlin member speaks them. `src/librina_region.cpp` keeps its name: it is rlite's
`librina-api` (rina/api.h) on the region, rlite's name.
