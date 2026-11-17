#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# try.sh -- soft Homa on real machines with John Ousterhout's own load generator (HomaModule util/cp_node), unchanged.
# Everything prints to this terminal. Nothing is installed outside this directory tree and /etc/hosts is not written.
#
#   try.sh build                                    build the shim, the memory and his tools (incremental)
#   try.sh ram [this-machine's-address]             run the memory here; it prints the commands for the other machines
#   try.sh server RAM-ADDRESS                       his cp_node as a server, on soft Homa
#   try.sh client RAM-ADDRESS SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]
#                                                   his cp_node as a client. WORKLOAD: a size in bytes, or his w1..w5
#                                                   (default 100). OUTSTANDING: requests in flight (default 8).
# The memory's port is SOFTHOMA_TRY_PORT (default 9131).
set -u
HERE=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
HOMA=${HOMA:-$(cd "$HERE/../../.." && pwd)/HomaModule}
PORT=${SOFTHOMA_TRY_PORT:-9131}
die() { echo "try.sh: $*" >&2; exit 1; }
built() { [ -x "$HERE/build/softhoma-ram" ] && [ -f "$HERE/build/libsofthoma.so" ] && [ -x "$HOMA/util/cp_node" ] || die "not built yet: $0 build"; }
# this machine's address as the others reach it: FrogNet's own answer where there is one, else the address the
# kernel would send from to reach $1
me() {
    local a=""
    command -v getEth0Address >/dev/null 2>&1 && a=$(getEth0Address 2>/dev/null | tr -d '[:space:]')
    [ -z "$a" ] && [ -n "${1:-}" ] && a=$(ip -4 route get "$1" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
    [ -n "$a" ] || die "cannot tell this machine's address; give it: $0 ram ADDRESS"
    echo "$a"
}
case "${1:-}" in
build)
    [ -f "$HOMA/homa.h" ] || die "HomaModule is not at $HOMA (set HOMA=/path/to/HomaModule)"
    if [ -f "$HERE/../../cpp/ensure_build_deps.sh" ]; then . "$HERE/../../cpp/ensure_build_deps.sh"; ribbit_ensure_build_deps; fi
    make -C "$HERE" HOMA="$HOMA" -j"$(nproc)" || die "the shim did not build"
    # His tools are written for x86: the cycle counter is the RDTSC instruction and the crash handler prints the x86
    # program counter. On any other machine those two places are changed in THIS copy of his source, and it is said
    # here. Nothing else of his is touched; on x86 nothing is.
    case "$(uname -m)" in
    x86_64|i?86) ;;
    *)  if ! grep -q "soft Homa try.sh" "$HOMA/util/test_utils.h"; then
            sed -i -e '1i #include <time.h>   /* soft Homa try.sh */' -e 's|^\t__asm__ __volatile__("rdtsc" : "=a" (lo), "=d" (hi));$|\t{ struct timespec ts; clock_gettime(CLOCK_MONOTONIC, \&ts); uint64_t ns = (uint64_t) ts.tv_sec * 1000000000ull + (uint64_t) ts.tv_nsec; lo = (uint32_t) ns; hi = (uint32_t) (ns >> 32); }   /* soft Homa try.sh: this machine has no RDTSC instruction */|' "$HOMA/util/test_utils.h"
            grep -q "no RDTSC instruction" "$HOMA/util/test_utils.h" || die "could not give his rdtsc() a clock for $(uname -m) in $HOMA/util/test_utils.h"
            echo "try.sh: $(uname -m) has no RDTSC: his util/test_utils.h rdtsc() now reads CLOCK_MONOTONIC in nanoseconds (his code calibrates it against the clock itself)"
        fi
        case "$(uname -m)" in aarch64|arm64) PC=pc ;; arm*) PC=arm_pc ;; *) PC="" ;; esac
        if grep -q 'uc->uc_mcontext.gregs\[REG_RIP\]' "$HOMA/util/cp_node.cc"; then
            [ -n "$PC" ] || die "his cp_node.cc crash handler prints the x86 program counter and this script does not know the register's name on $(uname -m)"
            sed -i "s|uc->uc_mcontext.gregs\[REG_RIP\]|uc->uc_mcontext.$PC|" "$HOMA/util/cp_node.cc"
            echo "try.sh: his util/cp_node.cc crash handler now prints this machine's program counter (uc_mcontext.$PC) in place of x86's"
        fi ;;
    esac
    make -C "$HOMA/util" server homa_test cp_node || die "his tools did not build"
    echo; echo "built. On the machine that holds the memory:  $0 ram"
    ;;
ram)
    built; IP=${2:-$(me)} || exit 1
    echo "the memory is at $IP:$PORT. On the other machines:"
    echo "    server:  $0 server $IP"
    echo "    client:  $0 client $IP SERVER-ADDRESS      (the server prints its address)"
    echo
    exec "$HERE/build/softhoma-ram" "$IP:$PORT"
    ;;
server)
    built; RAM=${2:-}; [ -n "$RAM" ] || die "usage: $0 server RAM-ADDRESS"
    IP=$(me "$RAM") || exit 1
    echo "his cp_node server on soft Homa at $IP. On the client machine:"
    echo "    $0 client $RAM $IP"
    echo
    exec env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" \
        "$HOMA/util/cp_node" server --protocol homa --first-port 4000 --port-threads 2
    ;;
client)
    built; RAM=${2:-}; SRV=${3:-}; [ -n "$RAM" ] && [ -n "$SRV" ] || die "usage: $0 client RAM-ADDRESS SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]"
    IP=$(me "$RAM") || exit 1
    echo "his cp_node client on soft Homa at $IP, server $SRV, workload ${4:-100}, ${5:-8} outstanding. One line a second; Ctrl-C ends it."
    # With no --gbps his client computes a rate of 0, an interval of infinity, and adds it to a uint64_t (cp_node.cc
    # "next_start += interval_dist(rand_gen)*cycles_per_second"). That conversion is undefined: x86 gives 0, so it
    # sends at once, which is the mode everyone runs; ARM gives the largest number, so it sends ONE request and waits
    # forever. Off x86 it is therefore given a rate nothing reaches, through his own option, and his source is left
    # alone: the next start is always in the past, as on x86.
    GBPS=""
    case "$(uname -m)" in x86_64|i?86) ;; *) GBPS="--gbps 1000000"
        echo "try.sh: $(uname -m): his client is given --gbps 1000000 (send at once; without it his client sends one request and stops on this machine, see the comment in $0)" ;; esac
    echo
    exec env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" SOFTHOMA_NODE1="$SRV" \
        "$HOMA/util/cp_node" client --protocol homa --workload "${4:-100}" --client-max "${5:-8}" --ports 1 \
        --first-port 4000 --first-server 1 --server-nodes 1 $GBPS
    ;;
*)  sed -n '5,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
