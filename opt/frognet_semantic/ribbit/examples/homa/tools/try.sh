#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# try.sh -- soft Homa on real machines with John Ousterhout's own load generator (HomaModule util/cp_node), unchanged.
# Everything prints to this terminal. Nothing is installed outside this directory tree and /etc/hosts is not written.
#
#   try.sh build                                    build the Homa API interface, the memory and his tools (incremental)
#   try.sh ram [this-machine's-address]             run the memory here; it prints the commands for the other machines
#   try.sh server RAM-ADDRESS                       his cp_node as a server, on soft Homa
#   try.sh client RAM-ADDRESS SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]
#                                                   his cp_node as a client. WORKLOAD: a size in bytes, or his w1..w5
#                                                   (default 100). OUTSTANDING: requests in flight (default 8).
#   try.sh tcpserver                                the control: his cp_node as a server on TCP, no soft Homa, no memory
#   try.sh tcpclient SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]
#                                                   his cp_node as a client on TCP, straight to the server
#   try.sh servers RAM-ADDRESS                      both of his servers at once, soft Homa and TCP, for compare
#   try.sh compare RAM-ADDRESS SERVER-ADDRESS [SECONDS]
#                                                   on the client: soft Homa and TCP in turn in one session, at each
#                                                   message size, SECONDS each (default 8); prints a table and keeps
#                                                   it and every run's output under results/
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
    make -C "$HERE" HOMA="$HOMA" -j"$(nproc)" || die "the Homa API interface did not build"
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
    exec env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1 \
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
    exec env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" SOFTHOMA_NODE1="$SRV" SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1 \
        "$HOMA/util/cp_node" client --protocol homa --workload "${4:-100}" --client-max "${5:-8}" --ports 1 \
        --first-port 4000 --first-server 1 --server-nodes 1 $GBPS
    ;;
tcpserver)
    built
    echo "his cp_node server on TCP (the control: no soft Homa). His server takes connections only from 10.x, 172.16-31.x and 192.168.x."
    echo "On the client machine:   $0 tcpclient THIS-MACHINE'S-ADDRESS"
    echo
    exec "$HOMA/util/cp_node" server --protocol tcp --first-port 4000 --port-threads 2
    ;;
tcpclient)
    built; SRV=${2:-}; [ -n "$SRV" ] || die "usage: $0 tcpclient SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]"
    echo "his cp_node client on TCP straight to $SRV, workload ${3:-100}, ${4:-8} outstanding. One line a second; Ctrl-C ends it."
    GBPS=""
    case "$(uname -m)" in x86_64|i?86) ;; *) GBPS="--gbps 1000000"; echo "try.sh: $(uname -m): his client is given --gbps 1000000, as for soft Homa" ;; esac
    echo
    # the Homa API interface is loaded only to answer his name for the server (node1); a TCP socket is not its business
    exec env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_NODE1="$SRV" \
        "$HOMA/util/cp_node" client --protocol tcp --workload "${3:-100}" --client-max "${4:-8}" --ports 1 \
        --first-port 4000 --first-server 1 --server-nodes 1 $GBPS
    ;;
servers)
    built; RAM=${2:-}; [ -n "$RAM" ] || die "usage: $0 servers RAM-ADDRESS"
    IP=$(me "$RAM") || exit 1
    echo "his cp_node servers at $IP: one on soft Homa (memory at $RAM:$PORT), one on TCP. Ctrl-C ends both."
    echo "On the client machine:   $0 compare $RAM $IP"
    echo
    "$HOMA/util/cp_node" server --protocol tcp --first-port 4000 --port-threads 2 & TCPSRV=$!
    trap 'kill $TCPSRV 2>/dev/null' EXIT
    env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1 \
        "$HOMA/util/cp_node" server --protocol homa --first-port 4000 --port-threads 2
    ;;
compare)
    built; RAM=${2:-}; SRV=${3:-}; SECS=${4:-8}; [ -n "$RAM" ] && [ -n "$SRV" ] || die "usage: $0 compare RAM-ADDRESS SERVER-ADDRESS [SECONDS]"
    IP=$(me "$RAM") || exit 1
    OUT="$HERE/results/compare-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT" || die "cannot make $OUT"
    T="$OUT/TABLE.txt"
    GBPS=""; case "$(uname -m)" in x86_64|i?86) ;; *) GBPS="--gbps 1000000" ;; esac
    say() { echo "$*" | tee -a "$T"; }
    say "soft Homa against TCP, his cp_node unchanged, one session. $(date '+%Y-%m-%d %H:%M:%S %Z')"
    say "client $IP ($(uname -srm)), server $SRV, memory $RAM:$PORT, $SECS s a run, his client ${GBPS:-with no --gbps}"
    say "$(strings "$HERE/build/libsofthoma.so" | grep -m1 -o 'softhoma-v[0-9]*-[0-9a-z]*'), HomaModule $(cut -c1-8 "$HOMA/COMMIT" 2>/dev/null)"
    say "link before: $(ping -c 5 -i 0.2 -q "$SRV" 2>&1 | tail -1)"
    say ""
    say "$(printf '%-9s %-4s %-9s %10s %10s %10s %10s' size out carrier 'Kops/s' 'P50 ms' 'P99 ms' 'P99.9 ms')"
    one() {   # carrier size outstanding round
        local f="$OUT/$1-$2-o$3-r$4.txt"
        if [ "$1" = soft ]; then
            env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_RAM="$RAM:$PORT" SOFTHOMA_IP="$IP" SOFTHOMA_NODE1="$SRV" SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1 \
                timeout "$SECS" "$HOMA/util/cp_node" client --protocol homa --workload "$2" --client-max "$3" --ports 1 --first-port 4000 --first-server 1 --server-nodes 1 $GBPS >"$f" 2>&1
        else
            env LD_PRELOAD="$HERE/build/libsofthoma.so" SOFTHOMA_NODE1="$SRV" \
                timeout "$SECS" "$HOMA/util/cp_node" client --protocol tcp --workload "$2" --client-max "$3" --ports 1 --first-port 4000 --first-server 1 --server-nodes 1 $GBPS >"$f" 2>&1
        fi
        # throughput: the median of his per-second lines (after the first two when there are more than three); round trips: his last line (they are
        # over the whole run)
        local kops last
        kops=$(grep "clients:" "$f" | awk '{l[NR]=$0} END{s=(NR>3)?3:1; for(i=s;i<=NR;i++) print l[i]}' | sed 's/.*clients: \([0-9.]*\) Kops.*/\1/' | sort -n | awk '{a[NR]=$1} END{if(NR) print a[int((NR+1)/2)]; else print "none"}')
        last=$(grep "clients:" "$f" | tail -1)
        if [ -z "$last" ] || grep -q "FATAL\|NOT THERE\|MEMORY IS GONE" "$f"; then
            say "$(printf '%-9s %-4s %-9s %s' "$2" "$3" "$1" "FAILED: $(grep -m1 'FATAL\|NOT THERE\|MEMORY IS GONE\|rror' "$f" | cut -c1-110) (see $f)")"; return
        fi
        say "$(echo "$last" | sed 's/.*P50 \([0-9.]*\) P99 \([0-9.]*\) P99.9 \([0-9.]*\).*/\1 \2 \3/' | awk -v s="$2" -v o="$3" -v c="$1" -v k="$kops" '{printf "%-9s %-4s %-9s %10s %10.2f %10.2f %10.2f", s, o, c, k, $1/1000, $2/1000, $3/1000}')"
    }
    for spec in ${SOFTHOMA_TRY_SPECS:-100:8 1000:8 10000:8 100000:1 240000:1 1000000:1}; do
        size=${spec%%:*}; o=${spec##*:}
        one soft "$size" "$o" 1; one tcp "$size" "$o" 1; one tcp "$size" "$o" 2; one soft "$size" "$o" 2    # each carrier once first, once second
    done
    say ""
    say "link after:  $(ping -c 5 -i 0.2 -q "$SRV" 2>&1 | tail -1)"
    echo; echo "the table is in $T ; every run's own output is beside it"
    ;;
*)  sed -n '5,22p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
