#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build-ribbit.sh -- the Ribbit side of Meshtastic on Ribbit, from the platform's own sources (cpp/):
#   bin/meshtastic-ram       the memory the nodes share (answers /meshtastic-api)
#   lib/libmtribbit.a        what the firmware links: mt_ribbit (packets on the RAM) and the platform under it
# Compiler output goes to the terminal. Ends with "build-ribbit PASS" or stops at the first error.
set -e -o pipefail
cd "$(dirname "$0")/.."
EX=$(pwd); P=$(cd ../../cpp && pwd)
mkdir -p bin lib obj
CXX=${CXX:-g++}
F="-std=c++20 -O2 -pthread -fPIC -Wall -Wextra -Wno-unused-parameter -Wno-free-nonheap-object -I$P/include -I$EX/ribbit"
source "$P/ensure_build_deps.sh"; ribbit_ensure_build_deps
for s in frogram ribbit_space ram_host lf_ebr; do
  echo "  compile $s.cpp"; $CXX $F -c "$P/src/$s.cpp" -o "obj/$s.o"
done
# the VERSION line goes into the library so every node's log says what it carries (mtr_front_version)
echo "  compile mt_ribbit.cpp"; $CXX $F "-DMTR_FRONT_VERSION=\"$(cat VERSION)\"" -c ribbit/mt_ribbit.cpp -o obj/mt_ribbit.o
rm -f lib/libmtribbit.a; ar rcs lib/libmtribbit.a obj/mt_ribbit.o obj/ribbit_space.o obj/frogram.o obj/ram_host.o obj/lf_ebr.o
echo "  link meshtastic-ram"; $CXX $F ribbit/meshtastic_ram.cpp obj/ram_host.o obj/lf_ebr.o obj/frogram.o -o bin/meshtastic-ram -llz4 -lcrypto
echo "  link mtr-probe (harness)"; $CXX $F harness/mtr_probe.cpp lib/libmtribbit.a -o bin/mtr-probe -llz4 -lcrypto
# [MT_APP_LIBRARY_V1] the app-facing library: mesh_app on libmtribbit, with nanopb and the Meshtastic protobufs it decodes
NP=/home/claude/mesh/fwb/.pio/libdeps/native/Nanopb
if [ -d "$NP" ]; then
  G="-std=c++20 -O2 -fPIC -w -DPB_ENABLE_MALLOC=1 -DPB_VALIDATE_UTF8=1 -I$NP -I$EX/front/src/mesh/generated -I$EX/ribbit -I$P/include"
  for s in pb_common pb_decode pb_encode; do $CXX $G -x c++ -c "$NP/$s.c" -o "obj/np_$s.o"; done
  for s in mesh portnums telemetry channel config module_config device_ui field_metadata xmodem; do
    $CXX $G -c "$EX/front/src/mesh/generated/meshtastic/$s.pb.cpp" -o "obj/pb_$s.o"; done
  echo "  compile mesh_app.cpp"; $CXX $G -Wall -Wextra -Wno-unused-parameter -c ribbit/mesh_app.cpp -o obj/mesh_app.o
  rm -f lib/libmeshapp.a; ar rcs lib/libmeshapp.a obj/mesh_app.o obj/np_*.o obj/pb_*.o obj/mt_ribbit.o obj/ribbit_space.o obj/frogram.o obj/ram_host.o obj/lf_ebr.o
  echo "  link mesh-app-demo"; $CXX $G harness/mesh_app_demo.cpp lib/libmeshapp.a -o bin/mesh-app-demo -llz4 -lcrypto -lpthread
else
  echo "  (nanopb not at $NP: mesh_app not built)"
fi
echo "  link mtx (coordination cells)"; $CXX $F harness/mtx.cpp obj/ribbit_space.o obj/frogram.o obj/ram_host.o obj/lf_ebr.o -o bin/mtx -llz4 -lcrypto
echo "build-ribbit PASS ($(bin/meshtastic-ram --version 2>/dev/null | head -1))"
