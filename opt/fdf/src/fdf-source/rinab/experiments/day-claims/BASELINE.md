# Baseline: the DAF model (whatevercast names) before any change

Recorded 2026-10-22, before anything in the tree was changed.

## What was run

| Item | Value |
|---|---|
| Source | GitHub `FawcettJohnW/frognet-living-network`, branch `daf-model`, commit `3e836fb` (2026-10-16). `main` (308c8ac) does not contain the DAF model. |
| Build tag | `fdf-2026-10-16-r21` (src/fdf_version.hpp) |
| Build | `tools/build.sh`, unchanged. g++ 13.3.0, jemalloc, -O2. `build/rina-host` sha256 prefix 564e7948fc7c6ac5 |
| Machine | Simulator sandbox (Linux 6.18, 2 cores). Loopback only. **Not a physical-network result.** |
| Whatevercast implementation | `src/daf_region.cpp` section B: `define_name`, `join`, `unjoin`, `designate`, `resolve_core`, `wait what=name` |
| Client | `src/daf_client.hpp` |

## Results

| Suite | Result |
|---|---|
| `tools/daf-check.sh` (daf_names, daf_membership, daf_information, daf_reach; phase 1, kill -9 + restart, phase 2) | **54 of 54 checks pass**, all phases. Section B (whatevercast): 14 of 14. |
| `tools/hospital-demo.sh 3` | **3 of 3 runs pass** (19 checks per run) — with `FROGNET_BLOB_ROOT` set, see below |

The earlier report to John said 55 checks; the suites on `daf-model` HEAD print 54 (A4 B14 C7 D5 E5 F6 G4 H5 I4).

## One environment difference, traced

The first demo run failed 3 of 3 at step 3: the client threw `REQ_RAW answered with something other than RESP_RAW`.
Traced (diagnostic client, `[DIAG-RAWREPLY]`): the host answered the 512 KB `wait what=fd` with an FNW1 ERROR,
`encode: train_exception`; the host log said `[SEMTPL-TRAIN] learn FAILED: blob store: mkdir
/opt/frognet_semantic/blob_cache: No such file or directory`. The blob store root defaults to
`/opt/frognet_semantic/blob_cache` (semtpl.hpp `root()`), which exists on a FrogNet node and not in this sandbox.
With `FROGNET_BLOB_ROOT` pointed at a scratch directory, all runs pass. No code was changed.

## What the whatevercast code does today (read from source, not docs)

- A name is a set plus settings: rule (`all`, `one`, `designated`, `nearest`), lifetime (`session` / `persistent`),
  join policy via the check point.
- `one`: filter on members' facts, rank on one numeric fact, ties to the lowest apn. Capability selection is the filter.
- `nearest`: **Euclidean distance on the members' `x`,`y` facts from a caller-given point.** It is geographic, not
  network distance (no latency or hop facts are consulted).
- The caller may override the name's rule per resolve (`in["rule"]`).
- Liveness: `live:true` drops members whose session ended; session entries vanish with the session.
- A held read on a name (`wait what=name`) wakes on any change to its entries or its members' enrollment.
