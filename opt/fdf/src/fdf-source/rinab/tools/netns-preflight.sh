#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# netns-preflight.sh -- [NETNS_PREFLIGHT_V1] sourced first by every netns-*-test.sh, with H set to rinab and T to the
# test's scratch directory (created). It:
#   - checks everything the test needs before anything starts, and names each missing one;
#   - puts wireguard-go (build/wg, made by tools/build_wireguard_go.sh or simulation/setup.sh) in $T/bin;
#   - sets PATH to $T/bin, the simulator's venv (simulation/setup.sh) and the system's bin directories, NOT
#     /usr/local/bin: the tests bind the tree's usr/local over /usr/local, so a python3 there would vanish inside the
#     node's namespace (reconcile_tunnels rc=127);
#   - starts a watchdog: past TEST_LIMIT seconds (default 900) the test's processes are ended and it says so. A wait on
#     something that never comes ends the run instead of hanging it.
# A test calls netns_result at the end: it prints RESULT and exits with it (0 only when nothing failed).
# usage, in a test:  NEEDS="php"  . "$(dirname "$0")/netns-preflight.sh"
FROGSIM_HOME=${FROGSIM_HOME:-$HOME/.frogsim}
_miss=""
[ "$(id -u)" = 0 ] || _miss="$_miss\n  - root (the tests make network namespaces)"
for _c in ip wg curl ping $NEEDS; do command -v $_c >/dev/null || _miss="$_miss\n  - $_c"; done
_py=$FROGSIM_HOME/venv/bin/python3
[ -x "$_py" ] || _miss="$_miss\n  - the simulator's Python, $_py (bash opt/frognet_semantic/simulation/setup.sh makes it)"
case "$(readlink -f "$_py" 2>/dev/null)" in /usr/local/*) _miss="$_miss\n  - $_py is built on a Python under /usr/local, which the tests hide inside each node: remake it from /usr/bin/python3 (setup.sh does)";; esac
_wg=$H/build/wg/wireguard-go-$(uname -m)
[ -x "$_wg" ] || _miss="$_miss\n  - wireguard-go, $_wg (bash tools/build_wireguard_go.sh, or setup.sh)"
if [ -n "$_miss" ]; then printf "FAIL  setup: missing:$_miss\nRESULT FAIL: setup\n"; exit 1; fi
mkdir -p $T/bin; cp "$_wg" $T/bin/wireguard-go
export PATH=$T/bin:$FROGSIM_HOME/venv/bin:/usr/sbin:/usr/bin:/sbin:/bin
TEST_LIMIT=${TEST_LIMIT:-900}
( trap 'kill $_s 2>/dev/null; exit 0' TERM; sleep $TEST_LIMIT & _s=$!; wait $_s || exit 0
  tr '\0' ' ' < /proc/$$/cmdline 2>/dev/null | grep -q "netns-.*-test" || exit 0     # the test already ended
  echo "FAIL  the test ran past ${TEST_LIMIT} s (TEST_LIMIT): ended"; kill -TERM $$ 2>/dev/null; pkill -TERM -P $$ 2>/dev/null ) &
_WATCHDOG=$!
trap 'exit 124' TERM
netns_result() { kill $_WATCHDOG 2>/dev/null; echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")${1:+ ($1)}"; [ $FAILS = 0 ]; exit $?; }
