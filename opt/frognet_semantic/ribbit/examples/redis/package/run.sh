#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run.sh -- the Redis region beside stock Redis 7.2.11, on this machine and across machines. Everything prints to the
# terminal; each stage's raw output is also kept under ribbit/examples/redis/results/<run>/.
#
#   ./run.sh build                       build stock Redis and the region here (installs make, g++, tcl if missing)
#   ./run.sh node                        acceptance on this machine: suite, concurrency, throughput, failure tests
#   STAGES="bench scale" ./run.sh node   only the throughput stages (REPEAT=5 runs each, interleaved; K = cores)
#   ./run.sh lan host                    on the machine that holds the memory (memory :7490, stock :7491, box :7492)
#   ./run.sh lan node <HOST_IP> <NAME>   on every other machine
#   ./run.sh lan coordinate <N> [SECS]   on the host machine, once the N nodes are up
set -u
cd "$(dirname "$(readlink -f "$0")")"; TOP=$PWD
export REDIS_SRC=$TOP/redis-7.2.11
case "${1:-}" in
build)
  need=(); for t in make:make g++:g++ tclsh:tcl python3:python3 pip3:python3-pip; do command -v ${t%%:*} >/dev/null || need+=(${t##*:}); done
  if [ ${#need[@]} -gt 0 ]; then echo "== installing ${need[*]}"; apt-get install -y -q "${need[@]}" || { echo "FAIL: apt-get install ${need[*]}"; exit 1; }; fi
  python3 -c "import lizard" 2>/dev/null || { echo "== installing lizard (the metrics stage counts lines and complexity with it)"; pip3 install -q --root-user-action=ignore --break-system-packages lizard || { echo "FAIL: pip3 install lizard"; exit 1; }; }
  echo "== stock Redis 7.2.11"; make -C "$REDIS_SRC" -j"$(nproc)" || { echo "FAIL: stock Redis did not build"; exit 1; }
  echo "== Redis on the memory (the box and the memory)"; bash "$TOP/ribbit/examples/redis/tools/build.sh" || { echo "FAIL: the box did not build"; exit 1; }
  echo "== built: $("$REDIS_SRC/src/redis-server" --version 2>/dev/null || "$REDIS_SRC/src/redis-server.stock" --version) / region $(cat ribbit/examples/redis/VERSION)"
  ;;
node) shift; exec bash "$TOP/ribbit/examples/redis/tools/accept.sh" "$@" ;;
lan)  shift; exec bash "$TOP/ribbit/examples/redis/tools/lan.sh" "$@" ;;
*) sed -n '4,14p' "$0"; exit 2 ;;
esac
