#!/bin/bash
# build_comms_static.sh arm64|x86_64 COMMS_SRC -- [COMMS_ON_A_NODE_V1] static comms-ram and comms-media for the node tarballs
# (no Qt, no window: the servers only). Deps (static, per arch): DEPS = opus, libvpx, portaudio (no host APIs), FFmpeg
# (vpx+opus, no programs). arm64: lz4 and openssl from ARM (rinab/build/arm-deps); x86_64: the build machine's static
# liblz4/libcrypto. DEPS: tools/build_comms_deps.sh. Output: rinab/build/comms-<arch>/{comms-ram,comms-media}, which
# make_deliverables.sh puts in /opt/fdf/comms (fdf-comms.sh's COMMS_BIN default)
set -eu; H=$(cd "$(dirname "$0")/.." && pwd); A=${1:?arm64|x86_64}; cd "${2:?the Communicator source: ribbit/examples/communicator}"; R=third_party/ribbit; O=$H/build/comms-$A
if [ "$A" = arm64 ]; then
  DEPS=${DEPS:-$H/build/comms-deps/arm64}; ARM=${ARM:-$H/build/arm-deps}; GXX=aarch64-linux-gnu-g++
  XI="-I$ARM/lz4/lib -I$ARM/openssl/include"; XL="$ARM/lz4/lib/liblz4.a $ARM/openssl/libcrypto.a"
else
  DEPS=${DEPS:-$H/build/comms-deps/x86_64}; GXX=g++; XI=""; XL="-llz4 -lcrypto"
fi
CXX="$GXX -O2 -pthread -std=c++17 -D__STDC_CONSTANT_MACROS -I$R/cpp/include -I$R/defaults/api -Isrc -I$DEPS/include $XI"
mkdir -p $O/obj
SRC="$R/cpp/src/frogram.cpp $R/cpp/src/ram_client.cpp $R/cpp/src/ram_host.cpp $R/defaults/api/tuples.cpp src/ram/comms_ram.cpp
     src/fnav/net.cpp src/fnav/frame.cpp src/fnav/plane.cpp src/fnav/segment.cpp src/fnav/bearer.cpp src/media/transcode.cpp
     src/media/server.cpp src/media/speed.cpp src/media/main.cpp src/comms/call.cpp src/comms/audio.cpp src/comms/room.cpp src/comms/devices.cpp"
pids=""; for s in $SRC; do o=$O/obj/$(echo $s | tr / _).o; $CXX -c $s -o $o & pids="$pids $!"; done; for p in $pids; do wait $p; done
ob() { for s; do printf '%s ' $O/obj/$(echo $s | tr / _).o; done; }
L="-L$DEPS/lib -lavdevice -lavfilter -lavformat -lavcodec -lswscale -lswresample -lavutil -lvpx -lopus -lportaudio $XL -lm -ldl -static"
$GXX -pthread -o $O/comms-ram $(ob src/ram/comms_ram.cpp $R/cpp/src/ram_host.cpp $R/cpp/src/frogram.cpp) $XL -ldl -static
$GXX -pthread -o $O/comms-media $(ob src/media/main.cpp src/media/server.cpp src/media/speed.cpp src/media/transcode.cpp src/fnav/net.cpp src/fnav/frame.cpp src/fnav/plane.cpp src/fnav/segment.cpp src/fnav/bearer.cpp src/comms/call.cpp src/comms/audio.cpp src/comms/room.cpp src/comms/devices.cpp $R/cpp/src/frogram.cpp $R/cpp/src/ram_client.cpp $R/defaults/api/tuples.cpp) $L
if [ "$A" = arm64 ]; then aarch64-linux-gnu-strip $O/comms-ram $O/comms-media; else strip $O/comms-ram $O/comms-media; fi
file $O/comms-ram $O/comms-media
