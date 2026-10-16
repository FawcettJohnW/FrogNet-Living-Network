#!/bin/bash
# build.sh -- the RINA host on the Redis server model: the platform's ram_server.cpp (v0.63, vendored untouched) +
# the Redis host's platform patches + [SESSION_IS_LIVENESS_V1]/[RINA_RESIDENT_V1] + one include and one dispatch block.
set -e -o pipefail
cd "$(dirname "$0")/.."
PLAT=platform/ribbit-v0.63; P=build/platform; CXX="g++ -O2 -pthread"; W="-Wall -Wextra -Wno-unused-parameter"
mkdir -p build/obj
rm -rf "$P" && cp -r "$PLAT" "$P"
python3 tools/apply_reply_proof.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_same_miss.py $P/ribbit_cpp
python3 tools/apply_no_answer_hash.py $P/ribbit_cpp/fnwp_engine.hpp      # after same_miss: it needs CacheE.status/body
python3 tools/apply_engine_exact_shortcuts.py $P/ribbit_cpp
python3 tools/apply_alloc_once.py $P/ribbit_cpp
python3 tools/apply_template_precompute.py $P/ribbit_cpp
python3 tools/apply_fire_keys.py $P/ribbit_cpp/ram_memory.hpp
python3 tools/apply_publish_order.py $P/ribbit_cpp/ram_memory.hpp   # [PUBLISH_IN_ID_ORDER_V1] a follower never skips a write
python3 tools/apply_ebr_readers.py $P/ribbit_cpp/ebr.hpp
python3 tools/apply_coalesce.py $P/ribbit_cpp
python3 tools/apply_wire_order.py $P/ribbit_cpp
python3 tools/apply_client_lockless.py $P/ribbit_cpp/frogram.cpp
python3 tools/apply_refused_why.py $P/ribbit_cpp/frogram.cpp
python3 tools/apply_client_applier.py $P/ribbit_cpp/fnwp_engine.hpp $P/ribbit_cpp/frogram.cpp $P/ribbit_cpp/frogram.hpp
python3 tools/apply_primed_templates.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_client_coalesce_lockless.py $P/ribbit_cpp/frogram.cpp
python3 tools/apply_client_landing_lockless.py $P/ribbit_cpp/frogram.cpp
python3 tools/apply_call_text.py $P/ribbit_cpp/frogram.hpp $P/ribbit_cpp/frogram.cpp
python3 tools/apply_futex_portable.py $P/ribbit_cpp/frogram.cpp          # [FUTEX_PORTABLE_V1] Windows client
python3 tools/apply_client_known_shape.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_engine_once.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_long_leaf.py $P/ribbit_cpp/semtpl.hpp              # [LONG_LEAF_ONE_PASS_V1]
python3 tools/apply_session_hook.py $P/ramsrv/ram_server.cpp
python3 tools/apply_host_fanout.py $P/ramsrv/ram_server.cpp
python3 tools/apply_engine_lockless.py $P/ribbit_cpp/fnwp_engine.hpp $P/ramsrv/ram_server.cpp
python3 tools/apply_decode_alloc.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_same_as_last.py $P/ribbit_cpp/fnwp_engine.hpp
python3 tools/apply_session_order.py $P/ramsrv/ram_server.cpp
python3 tools/apply_host_coalesce.py $P/ramsrv/ram_server.cpp
python3 tools/apply_deferred_answer.py $P/ramsrv/ram_server.cpp
python3 tools/apply_work_moves.py $P/ramsrv/ram_server.cpp
python3 tools/apply_region_session_key.py $P/ramsrv/ram_server.cpp   # [REGION_COALESCE_PER_SESSION_V1]
python3 tools/apply_host_announce.py $P/ramsrv/ram_server.cpp
python3 tools/apply_session_peer.py $P/ramsrv/ram_server.cpp
python3 tools/apply_http_front.py $P/ramsrv/ram_server.cpp         # [HTTP_FRONT_V1] last: plain HTTP on the same port
python3 tools/apply_windows_portable.py $P/ribbit_cpp                      # [WINDOWS_PORTABLE_V1] last: the Windows client
python3 tools/apply_keepalive.py $P/ribbit_cpp/frogram.cpp $P/ramsrv/ram_server.cpp   # [KEEPALIVE_20S_V1] both ends: silent loss = dead at 20 s
python3 tools/apply_keepalive_5s.py $P/ribbit_cpp/frogram.cpp $P/ramsrv/ram_server.cpp   # [KEEPALIVE_5S_V1] after the 20 s block it replaces
python3 tools/apply_session_epoch.py $P/ramsrv/ram_server.cpp
python3 tools/apply_net_config.py $P/ramsrv/ram_server.cpp $P/ribbit_cpp/ram_memory.hpp   # [NET_CONFIG_IS_THE_RAM_MANAGERS_V1]
tools/make_host.sh $P/ramsrv/ram_server.cpp build/ram_server_rina.cpp
$CXX -std=c++17 -c $P/ribbit_cpp/frogram.cpp -o build/obj/frogram.o
$CXX -std=c++20 $W -Werror -Isrc -Isrc/door -I$P/ribbit_cpp -c src/rina_region.cpp -o build/obj/rina_region.o
$CXX -std=c++20 -Isrc -Isrc/door -I$P/ribbit_cpp -c src/ctl_host.cpp -o build/obj/ctl_host.o
$CXX -std=c++20 $W -Werror -Isrc -Isrc/door -I$P/ribbit_cpp -c src/daf_region.cpp -o build/obj/daf_region.o   # [DAF_MODEL_V1]
$CXX -std=c++20 -I$P/ribbit_cpp -I$P/ramsrv -Isrc/door -c build/ram_server_rina.cpp -o build/obj/ram_server_rina.o
# [HOST_ALLOCATOR_V1] the host links jemalloc when the machine has it (apt install libjemalloc2): measured on rr 3,000 +
# perf 20,000, host CPU -15% (181 -> 155 ticks) and rr +16..27% against glibc malloc, which was 38% of the host's
# instructions (callgrind). Without it the host links glibc malloc and says so. NO_JEMALLOC=1 forces glibc.
JE=$(ldconfig -p 2>/dev/null | awk '/libjemalloc\.so\.2 /{print $NF; exit}')
if [ -n "$JE" ] && [ "${NO_JEMALLOC:-0}" != 1 ]; then ALLOC="$JE"; echo "host allocator: jemalloc ($JE)"; else ALLOC=""; echo "host allocator: glibc malloc (apt install libjemalloc2 for jemalloc)"; fi
$CXX -o build/rina-host build/obj/ram_server_rina.o build/obj/rina_region.o build/obj/daf_region.o build/obj/ctl_host.o build/obj/frogram.o -llz4 -lcrypto -lresolv $ALLOC   # [MR_FROM_THE_AREA_NAME_V1] -lresolv: SRV lookup
mkdir -p build/bin && cp build/rina-host build/bin/rina-host     # the one the scripts run
# [DAF_SIM_AGENT_V1] one simulated node's FNW1 session, for the simulator's DAF discovery tier
$CXX -std=c++20 $W -Werror -O2 -pthread -D_GNU_SOURCE -I$P/ribbit_cpp -Isrc examples/sim/daf_agent.cpp build/obj/frogram.o -llz4 -lcrypto -lresolv -o build/bin/daf-agent
echo "BUILD OK: build/rina-host (and build/bin/rina-host, build/bin/daf-agent)"
