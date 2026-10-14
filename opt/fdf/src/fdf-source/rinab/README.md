# RINA on the Redis server model (Mode B, region build)

**Two things in this file, kept apart.** The layout, the build and the commands describe the code as it is in this
package (FD&F r19-a15). The measurements under "Historical measurements" were taken on the Mode B tree of 2026-10-05/06
(steps 1 to 2b), before FD&F's region, LISP mapping and ponds work; they are not measurements of r19-a15.

**Not in this package:** `PROFILE-1.md`, `VERIFIED.md` and `evidence/`. They were not in fdf-source-r16 or r19 as
handed over either. Without them the table below cannot be checked from this package; the commands under "Rebuild
and re-measure" produce the current numbers.

## Layout

- `platform/ribbit-v0.63`: the platform, vendored untouched (from redis-on-ribbit.tgz).
- `tools/build.sh`: builds `build/rina-host`. It applies the Redis host's four platform patches
  (apply_reply_proof/same_miss/coalesce/wire_order.py) plus `tools/apply_session_hook.py`
  (`[SESSION_IS_LIVENESS_V1]`, `[RINA_RESIDENT_V1]`, `[RAW_HELD_READ_V1]`, `[RAW_GET_V1]`), then glues in the
  region with `tools/make_host.sh` (one include, one dispatch block, and `[REGION_FAST_PATH_V1]` before the query parse), the way the Redis host is made.
- `src/rina_region.cpp`: the RINA region, resident in the host (`op=rina` inline, `op=rina_block` held). It contains
  `[CREDIT_ON_DEMAND_V1]`, `[CREDIT_BY_QUARTERS_V1]`, `[RAW_HELD_READ_V1]`, `[RAW_GET_V1]` and `[RESOLVE_ONLY_FOR_UDP4_V1]`.
- `src/librina_region.cpp`: rlite's `rina/api.h` on the region. Each program has one session and one held `wait`;
  sends are posted; credit is asked for only when out of it (`[CREDIT_ON_DEMAND_V1]`). `src/rina_region_client.hpp`: `[REGION_ANSWER_IS_DATA_V1]`, `[OPENSSL_NO_ATEXIT_V1]`.
- `src/rina_node.cpp`: the machine agent, installed as rlite-uipcps.
- `src/rlite_ctl.cpp`, `src/ctl_host.*`, `src/rlite_ctl_client.cpp`: rlite-ctl, one call per command, run in the
  region.
- `tools/build_rlite.sh RLITE_ROOT`: rlite's own programs (rinaperf, rina-echo-async, iporinad), unchanged, built
  against librina_region; plus `ribbit-tests/fanout.c`.
- `tools/run-suite.sh RLITE_ROOT OUT [FIRST LAST]`: rlite's integration tests (`ISOLATED=1`: the host in its own
  pid and mount namespaces).
- `tools/rr-count.sh [N]`: rinaperf rr, plus requests per transaction from the host's own log.
- `tools/cg-rr.sh [N] [OUT]`: instructions inside region calls (callgrind) during an rr of N.
- `tools/bench.sh`, `tools/cpu-split.sh`: the same-run comparison with Mode B as handed off
  (`OLD=<that tree>/ribbit/examples/rina/rlite`).

## Rebuild and re-measure

Needs g++ 11+, liblz4-dev, libssl-dev, protobuf-compiler, libprotobuf-dev, pkg-config, valgrind; root for
namespaces.

    cd rinab
    tools/build.sh
    tools/build_rlite.sh /path/to/rlite-6ac276b
    ISOLATED=1 tools/run-suite.sh /path/to/rlite-6ac276b /tmp/suite.tsv 1 15
    ISOLATED=1 tools/run-suite.sh /path/to/rlite-6ac276b /tmp/suite.tsv 16 30     # 30/30 expected
    tools/rr-count.sh 5000                                                         # 2.00 requests per program per transaction
    tools/cg-rr.sh 500 /tmp/cg.out                                                 # ~48 M instructions for 500 (96 k per transaction)
    tools/perf-count.sh 20000                                                      # every SDU arrives (credit by quarters)
    tools/alloc-stall.sh 400                                                       # no allocation slower than 1 s

## Historical measurements (Mode B, 2026-10-05/06; the files they cite are not in this package)

| number | file |
|---|---|
| 253 k instructions per transaction, ~3 requests per program (before step 1) | evidence/callgrind.out.host, requests-per-session.txt |
| 2.00 requests per program after credit on demand | evidence/after-credit-on-demand.txt |
| 158 k per transaction after step 2a | evidence/callgrind.out.step2a |
| 144 k per transaction after step 2b | evidence/callgrind.out.step2b |
| 30/30 isolated after step 2b | evidence/suite-isolated-after-step2b.tsv |
| ping, rr and perf against the handed-off Mode B, same run | evidence/bench-after-credit.txt |

Those profiles were taken on earlier states of these files. The changes they measured are marked by the tags above, and
no later step removed them. The code here has grown since: `src/rina_lisp.cpp` (the LISP mapping, see
`lisp-mapping/README.md`), the region and ponds work (`docs/`), and the netns tests in `tools/`. Their proofs are in
`experiments/RESULTS.md` and the docs/MERGE-*.md files, each with the test that shows it.

## One command

    sudo ./run.sh /path/to/rlite-6ac276b            # all 30; or: sudo ./run.sh /path/to/rlite 11 14

It checks (root, the rlite tree, the tools, nothing already running, the port free), builds, starts the host
isolated on PORT (default 8840) and checks it answers, then runs the tests one at a time. If a test is still running
after HANG_S seconds (default 15), it dumps the live host's threads with gdb. It prints every result, and for each
failure the end of that test's log and any host dump. Everything from a run is kept in `runs/<date-time>/`.

## Against rlite, on one machine

    sudo ./bench.sh /path/to/rlite-6ac276b [ROUNDS]

This builds rlite from a copy of your tree: its user programs, plus its kernel modules for the running kernel (needs
`linux-headers-$(uname -r)` and cmake). It loads the modules, runs rlite's own two-namespace topology with rinaperf,
then unloads them and runs the same topology and rinaperf against the region. It prints a side-by-side table (ping,
rr, perf, 16 parallel pings; median of rounds) and lines of code, and keeps everything in `bench-runs/<date-time>/`.
The region's count excludes the Ribbit platform, as rlite's excludes the Linux kernel.
