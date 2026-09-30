#!/usr/bin/env bash
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
# FrogNet Communicator - convenience wrappers.
#
#   source /etc/frognet_bundles/communicator/comms_functions.sh
#
#   runServerHeadless [BIND]                  # A/V relay (byte-fan). default bind 0.0.0.0:9000
#   runCaptureHeadless NAME [RELAY] [extra...]  # headless camera sender - NO window, no display
#   runClient NAME [RELAY] [extra...]           # windowed viewer - NEEDS a display
#
# Defaults:
#   RELAY  = $FN_RELAY  (10.28.28.1:9000)   - override per-call as the 2nd arg, or export FN_RELAY
#   capture uses --cam 0 --codec vp8        - override by passing --cam N / --codec ... as extra args
#                                             (argparse takes the last value, so extras win)
#
# The 2nd positional is taken as RELAY only when it does NOT start with '-', so you can write
#   runCaptureHeadless JWF --cam 1          (uses default relay, --cam 1 passed through)
#   runCaptureHeadless JWF 10.28.28.1:9000 --cam 1
#
# Override the bundle location before sourcing if it isn't the default:
#   COMM_DIR=/path/to/communicator source .../comms_functions.sh

: "${COMM_DIR:=/etc/frognet_bundles/communicator}"
: "${FN_RELAY:=10.28.28.1:9000}"
_FNAV="$COMM_DIR/fnav.py"
_FNLIVE="$COMM_DIR/communicator_live.py"

# pull an optional RELAY off $1 (only if it's not a flag); leaves the rest in $@
_fn_take_relay() {                    # usage: _fn_take_relay "$@"; echo "$_RELAY"; then shift accordingly
    _RELAY="$FN_RELAY"; _SHIFT=0
    if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then _RELAY="$1"; _SHIFT=1; fi
}

runServerHeadless() {
    local bind="${1:-0.0.0.0:9000}"
    echo "[runServerHeadless] relay on ${bind}" >&2
    python3 "$_FNAV" --serve "$bind"
}

runCaptureHeadless() {
    if [ -z "$1" ]; then
        echo "usage: runCaptureHeadless NAME [RELAY] [extra...]" >&2; return 2; fi
    local name="$1"; shift
    _fn_take_relay "$@"; [ "$_SHIFT" = 1 ] && shift
    echo "[runCaptureHeadless] name=${name} relay=${_RELAY} cam=0 codec=vp8 (headless)" >&2
    python3 "$_FNAV" --call "$_RELAY" --name "$name" \
            --no-display --cam 0 --codec vp8 "$@"
}

runClient() {
    if [ -z "$1" ]; then
        echo "usage: runClient NAME [RELAY] [extra...]   (needs a display)" >&2; return 2; fi
    local name="$1"; shift
    _fn_take_relay "$@"; [ "$_SHIFT" = 1 ] && shift
    echo "[runClient] name=${name} relay=${_RELAY} (windowed; requires a display)" >&2
    python3 "$_FNLIVE" --name "$name" --relay "$_RELAY" "$@"
}
