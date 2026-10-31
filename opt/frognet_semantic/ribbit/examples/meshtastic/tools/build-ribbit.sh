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
echo "build-ribbit PASS ($(bin/meshtastic-ram --version 2>/dev/null | head -1))"
