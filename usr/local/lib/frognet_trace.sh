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
##############################################################
# /usr/local/lib/frognet_trace.sh
#
# [INSTRUMENTATION_V2 2026-06-01] Comprehensive bash tracing.
#
# Sourced by every FrogNet bash script.  Provides:
#   _trace_enter <funcname> [k=v ...]
#   _trace_exit  <funcname> [k=v ...]
#   _trace_event <event>     [k=v ...]
#   _trace_cmd   <label> -- <cmd> [args...]
#       Logs CMD-BEGIN, runs cmd capturing rc + stdout + stderr,
#       logs CMD-END with rc + duration + first line of each.
#
# All output goes through flog_info so it lands in the same log
# stream as everything else.  Each line is prefixed with [TRACE]
# to make grepping easy.
##############################################################

# Avoid double-sourcing.
[[ -n "${_FROGNET_TRACE_LOADED:-}" ]] && return 0
_FROGNET_TRACE_LOADED=1

# Depends on flog_info from frognet_log.sh.  If frognet_log.sh
# hasn't been sourced yet, fall back to plain echo to stderr.
if ! declare -f flog_info >/dev/null 2>&1; then
    flog_info() { echo "$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ) INFO $*" >&2; }
fi

_trace_enter() {
    local fn="$1"; shift
    flog_info "TRACE" "phase=ENTER func=$fn" "$@"
}

_trace_exit() {
    local fn="$1"; shift
    flog_info "TRACE" "phase=EXIT  func=$fn" "$@"
}

_trace_event() {
    local ev="$1"; shift
    flog_info "TRACE" "phase=EVENT name=$ev" "$@"
}

# _trace_cmd <label> -- <cmd> <args...>
# Logs the command, executes it capturing rc/stdout/stderr,
# logs result.  Returns the cmd's rc.  Stdout of the cmd is
# echoed (so callers using $() still work).
_trace_cmd() {
    local label="$1"; shift
    [[ "$1" == "--" ]] && shift
    local _t0 _t1 _out _err _rc _outfile _errfile
    _outfile="$(mktemp)"
    _errfile="$(mktemp)"
    flog_info "TRACE-CMD" "phase=BEGIN" "label=$label" "argv=$(printf '%q ' "$@")"
    _t0="$(date +%s%N)"
    "$@" >"$_outfile" 2>"$_errfile"
    _rc=$?
    _t1="$(date +%s%N)"
    _out="$(head -c 200 "$_outfile" 2>/dev/null | tr '\n' '|')"
    _err="$(head -c 200 "$_errfile" 2>/dev/null | tr '\n' '|')"
    flog_info "TRACE-CMD" "phase=END" "label=$label" \
        "rc=$_rc" "dur_ms=$(( (_t1 - _t0) / 1000000 ))" \
        "stdout_head=${_out:-empty}" "stderr_head=${_err:-empty}"
    # Re-emit stdout so $(_trace_cmd ...) captures it
    cat "$_outfile"
    rm -f "$_outfile" "$_errfile"
    return $_rc
}
