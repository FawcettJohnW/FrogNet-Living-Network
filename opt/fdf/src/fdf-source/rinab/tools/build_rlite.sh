#!/bin/bash
# build_rlite.sh RLITE_ROOT -- rlite's side on the region: rlite-ctl (one call per command), librina-api.so (rina/api.h:
# one call per conversation, one held read per program), rlite-uipcps (the machine agent), and rlite's own programs
# compiled UNCHANGED against it. Needs build/ from tools/build.sh.
set -e
U=$1; [ -d "$U/include/rina" ] || { echo "usage: build_rlite.sh RLITE_ROOT"; exit 2; }
cd "$(dirname "$0")/.."; H=$PWD; P=build/platform; mkdir -p build/bin build/lib build/gen
F="-std=c++20 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -D_GNU_SOURCE -I$P/ribbit_cpp -Isrc"
L="build/obj/frogram.o -llz4 -lcrypto -lresolv"   # [MR_FROM_THE_AREA_NAME_V1] -lresolv: SRV lookup
cp build/rina-host build/bin/rina-host
g++ $F src/rlite_ctl_client.cpp $L -o build/bin/rlite-ctl
g++ $F tests/peek.cpp $L -o build/bin/peek                                  # a debugging aid: one variable of the region, as held
g++ $F src/fdf_client.cpp $L -o build/bin/fdf-client                             # [FDF_WINDOWS_CLIENT_V1] the Linux member (facts, maps)
g++ $F src/fdf_events.cpp $L -o build/bin/fdf-events                           # [FDF_EVENTS_V1] region changes as lines, for non-FNWP programs
g++ $F src/fdf_participants.cpp $L -o build/bin/fdf-participants                # [FDF_DISCOVERY_V1] FD&F's getHosts
g++ $F tests/transport_conformance.cpp $L -o build/bin/transport-conformance     # [TRANSPORT_INTERFACE_V1] T2
g++ $F src/ram_tool.cpp $L -o build/bin/ram                                       # [FDF_CHAT_V1] Ribbit tuple get/send
g++ $F tests/coalesce_held.cpp $L -o build/bin/coalesce-held                 # [HOST_COALESCE_V1] proof: identical held reads, one execution
g++ $F tests/client_coalesce.cpp $L -o build/bin/client-coalesce               # [CLIENT_COALESCE_LOCKLESS_V1] proof
g++ $F tests/prime_dedupe.cpp $L -o build/bin/prime-dedupe                   # [PRIMED_TEMPLATES_V1] one exemplar per template
g++ $F tests/map_prefix.cpp $L -o build/bin/map-prefix                     # [MAP_PREFIX_LOCATOR_SET_V1] proof: prefix + locator set, session-bound, map-notify
g++ $F tests/net_config_test.cpp $L -o build/bin/net-config-test               # [NET_CONFIG_IS_A_TUPLE_V1] proof: published, read-only to members
g++ $F tests/rmem_probe.cpp $L -o build/bin/rmem-probe                     # [RINA_MEMORY_V1] a debugging aid
g++ $F -fPIC -shared -I"$U/include" src/librina_region.cpp $P/ribbit_cpp/frogram.cpp -llz4 -lcrypto -lresolv -o build/lib/librina-api.so
g++ $F src/rina_node.cpp $L -o build/bin/rlite-uipcps
g++ -std=c++20 -O2 -Wall -Wextra -Werror -Isrc tests/elect_test.cpp -o build/bin/elect-test        # [MR_RANKED_BY_CAPABILITY_V1] the election rule
g++ $F src/rina_lisp.cpp $L -o build/bin/rina-lisp                     # [AREA_NAME_IS_A_LISP_EID_V1] the host registers its area name
for p in rinaperf rina-echo-async; do gcc -O2 -g -I"$U/include" "$U/user/tools/$p.c" -Lbuild/lib -lrina-api -Wl,-rpath,"$H/build/lib" -lpthread -lm -o build/bin/$p; done
protoc --cpp_out=build/gen -I"$U/user/tools" "$U/user/tools/iporina.proto"
protoc --cpp_out=build/gen -I"$U/user/libs" "$U/user/libs/CDAP.proto"
gcc -O2 -D_GNU_SOURCE -I"$U/include" -c "$U/common/utils.c" -o build/gen/rl_utils.o
g++ -std=c++17 -O2 -D_GNU_SOURCE -I"$U/include" -I"$U/user/tools" -Ibuild/gen "$U/user/tools/iporinad.cpp" "$U/user/tools/fdfwd.cpp" \
    "$U/user/libs/cdap.cpp" build/gen/iporina.pb.cc build/gen/CDAP.pb.cc build/gen/rl_utils.o -Lbuild/lib -lrina-api -Wl,-rpath,"$H/build/lib" \
    $(pkg-config --cflags --libs protobuf) -lpthread -o build/bin/iporinad
gcc -O2 -I"$U/include" ribbit-tests/fanout.c -Lbuild/lib -lrina-api -Wl,-rpath,"$H/build/lib" -lpthread -o build/bin/fanout
echo "built: $(ls build/bin | tr '\n' ' ') build/lib/librina-api.so"
