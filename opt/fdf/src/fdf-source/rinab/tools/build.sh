#!/bin/bash
# build.sh -- [FDF_RAM_ON_PLATFORM_V1] fdf-ram, FD&F's RAM manager: the Ribbit platform's host (ribbit::RamHost,
# opt/frognet_semantic/ribbit/cpp) with the region (rina_region.cpp, daf_region.cpp, ctl_host.cpp) as its operations
# (RAM-MANAGER-ON-PLATFORM.md). It replaces rina-host (John, 2026-11-02: "We should not be running rina_host." "Move
# fully to the RAM manager model."). The clients (build_static_clients.sh, build_rlite.sh, build_arm64.sh,
# build_windows_client.sh) are built from the same platform.
set -e -o pipefail
cd "$(dirname "$0")/.."
# [FDF_RAM_ON_PLATFORM_V1] one source for the host AND every client: the platform (its frogram, memory and host). The
# patched v0.63 copy (platform/ribbit-v0.63 + tools/apply_*.py) is no longer built: a client on it and fdf-ram on the
# platform do not speak the same FNW1 (found 2026-11-03: every member's session closed by its first held reply).
PL=../../../../frognet_semantic/ribbit/cpp; CXX="g++ -O2 -pthread -D_GNU_SOURCE"; W="-Wall -Wextra -Wno-unused-parameter"
mkdir -p build/obj; F=build/fdfram; mkdir -p $F; rm -rf build/platform
$CXX -std=c++20 -I$PL/include -c $PL/src/frogram.cpp -o build/obj/frogram.o      # the client library (with lf_ebr.o)
$CXX -std=c++20 -I$PL/include -c $PL/src/lf_ebr.cpp -o build/obj/lf_ebr.o
$CXX -std=c++20 $W -Werror -Isrc -Isrc/door -I$PL/include -c src/rina_region.cpp -o $F/rina_region.o
$CXX -std=c++20 -Isrc -Isrc/door -I$PL/include -c src/ctl_host.cpp -o $F/ctl_host.o
$CXX -std=c++20 $W -Werror -Isrc -Isrc/door -I$PL/include -c src/daf_region.cpp -o $F/daf_region.o   # [DAF_MODEL_V1]
$CXX -std=c++20 $W -Werror -Isrc -Isrc/door -I$PL/include -c src/fdf_ram.cpp -o $F/fdf_ram.o
$CXX -std=c++20 -I$PL/include -c $PL/src/ram_host.cpp -o $F/p_ram_host.o
cp build/obj/frogram.o $F/p_frogram.o; cp build/obj/lf_ebr.o $F/p_lf_ebr.o
# [HOST_ALLOCATOR_V1] the host links jemalloc when the machine has it (apt install libjemalloc2): measured on rr 3,000 +
# perf 20,000, host CPU -15% (181 -> 155 ticks) and rr +16..27% against glibc malloc, which was 38% of the host's
# instructions (callgrind). Without it the host links glibc malloc and says so. NO_JEMALLOC=1 forces glibc.
JE=$(ldconfig -p 2>/dev/null | awk '/libjemalloc\.so\.2 /{print $NF; exit}' || true)   # (awk's early exit is ldconfig's SIGPIPE: not a failure)
if [ -n "$JE" ] && [ "${NO_JEMALLOC:-0}" != 1 ]; then ALLOC="$JE"; echo "host allocator: jemalloc ($JE)"; else ALLOC=""; echo "host allocator: glibc malloc (apt install libjemalloc2 for jemalloc)"; fi
FO="$F/fdf_ram.o $F/rina_region.o $F/daf_region.o $F/ctl_host.o $F/p_ram_host.o $F/p_lf_ebr.o $F/p_frogram.o"
$CXX -o build/fdf-ram $FO -llz4 -lcrypto -lresolv $ALLOC   # [MR_FROM_THE_AREA_NAME_V1] -lresolv: SRV lookup
mkdir -p build/bin && cp build/fdf-ram build/bin/fdf-ram     # the one the scripts run
rm -f build/rina-host build/bin/rina-host build/bin/rina-host-static build/static/rina-host build/obj/ram_server_rina.o build/ram_server_rina.cpp   # [FDF_RAM_ON_PLATFORM_V1] rina-host is gone
# [DAF_SIM_AGENT_V1] one simulated node's FNW1 session, for the simulator's DAF discovery tier
$CXX -std=c++20 $W -Werror -I$PL/include -Isrc examples/sim/daf_agent.cpp build/obj/frogram.o build/obj/lf_ebr.o -llz4 -lcrypto -lresolv -o build/bin/daf-agent
echo "BUILD OK: build/fdf-ram (and build/bin/fdf-ram, build/bin/daf-agent)"
