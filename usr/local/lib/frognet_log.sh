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
# /usr/local/lib/frognet_log.sh
#
# Shared logging primitives for the FrogNet merge pipeline.  Source
# this at the top of every bash script that participates in a merge:
#
#   . /usr/local/lib/frognet_log.sh
#   flog_init "mergeHostsAndResolv"
#
# After init, the following are available:
#
#   flog_info   <key=val>...    # state transitions, decisions, outcomes
#   flog_debug  <key=val>...    # branch inputs, intermediate values
#   flog_trace  <key=val>...    # raw tool output, voluminous intermediates
#   flog_warn   <key=val>...    # something unexpected but non-fatal
#   flog_error  <key=val>...    # definitely bad
#   flog_stage  <stage>         # mark stage transition - pairs with flog_stage_end
#   flog_stage_end <stage> [rc=N extra=val ...]
#                                # closes the stage, emits dur_ms
#   flog_dump   <label> <path>  # copy a file into the per-merge debug dir
#   flog_dump_cmd <label> <cmd> # run cmd, capture stdout+stderr into debug dir
#   flog_kv     <k> <v>         # escape a value for a key=val log line
#
# Levels: ERROR=1 WARN=2 INFO=3 DEBUG=4 TRACE=5.  Default INFO.
#   FROGNET_DEBUG=1 -> raise to DEBUG
#   FROGNET_TRACE=1 -> raise to TRACE (implies DEBUG)
#
# Merge ID: propagated through FROGNET_MERGE_ID env var.  Set by
# runMerge.bash at merge start.  If unset (script run standalone for
# debugging), we generate one and export it so child processes share
# the same debug dir.

# Prevent double-sourcing
[[ -n "${_FLOG_SOURCED:-}" ]] && return 0
_FLOG_SOURCED=1

# ---- Level setup ----------------------------------------------------------
_FLOG_ERROR=1
_FLOG_WARN=2
_FLOG_INFO=3
_FLOG_DEBUG=4
_FLOG_TRACE=5

_flog_level=$_FLOG_INFO
[[ "${FROGNET_DEBUG:-0}" == 1 ]] && _flog_level=$_FLOG_DEBUG
[[ "${FROGNET_TRACE:-0}" == 1 ]] && _flog_level=$_FLOG_TRACE

# ---- Merge ID and debug dir -----------------------------------------------
if [[ -z "${FROGNET_MERGE_ID:-}" ]]; then
    FROGNET_MERGE_ID="$(date +%Y%m%dT%H%M%S)-$$"
    export FROGNET_MERGE_ID
fi

FROGNET_DEBUG_DIR="/run/frognet/debug/${FROGNET_MERGE_ID}"
export FROGNET_DEBUG_DIR
mkdir -p "$FROGNET_DEBUG_DIR" 2>/dev/null || true

# Refresh the "latest" symlink so operators can `tail -F
# /run/frognet/debug/latest/trace.log` without knowing the merge ID.
# Only the process that owns this merge does this (runMerge sets
# FROGNET_MERGE_OWNER=1 before forking children).
if [[ "${FROGNET_MERGE_OWNER:-0}" == 1 ]]; then
    ln -sfn "$FROGNET_DEBUG_DIR" /run/frognet/debug/latest 2>/dev/null || true
fi

# ---- Tag + stage timing ---------------------------------------------------
_FLOG_TAG="${_FLOG_TAG:-frognet}"
declare -A _FLOG_STAGE_T0 2>/dev/null || true

_flog_ts()     { date '+%Y-%m-%dT%H:%M:%S.%3N' 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S'; }
_flog_now_ms() { date '+%s%3N' 2>/dev/null || echo $(( $(date '+%s') * 1000 )); }

# _flog_emit <numeric-level> <LEVELNAME> <message...>
# Format matches production runMerge output:
#   [2026-07-23T12:31:00.579] runMerge INFO pid=12931 STAGE=enter merge_id=...
_flog_emit() {
    local lvl="$1" name="$2"; shift 2
    (( lvl > _flog_level )) && return 0
    local line
    line="[$(_flog_ts)] ${_FLOG_TAG} ${name} pid=$$ $*"
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "${FROGNET_DEBUG_DIR}/trace.log"
    return 0
}

# ---- Public API -----------------------------------------------------------

# flog_init <tag> - name this script's stream, announce entry.
flog_init() {
    _FLOG_TAG="${1:-$_FLOG_TAG}"
    _flog_emit $_FLOG_INFO INFO \
        "STAGE=enter pid=$$ merge_id=${FROGNET_MERGE_ID} debug_dir=${FROGNET_DEBUG_DIR}"
}

flog_error() { _flog_emit $_FLOG_ERROR ERROR "$*"; }
flog_warn()  { _flog_emit $_FLOG_WARN  WARN  "$*"; }
flog_info()  { _flog_emit $_FLOG_INFO  INFO  "$*"; }
flog_debug() { _flog_emit $_FLOG_DEBUG DEBUG "$*"; }
flog_trace() { _flog_emit $_FLOG_TRACE TRACE "$*"; }

# flog_kv <k> <v> - emit one escaped key=val pair.
flog_kv() {
    local k="${1:-key}" v="${2:-}"
    case "$v" in
        *[[:space:]]*|*'"'*) v="\"${v//\"/\\\"}\"" ;;
    esac
    _flog_emit $_FLOG_INFO INFO "${k}=${v}"
}

# flog_stage <stage> - open a timed stage.
flog_stage() {
    local st="${1:-stage}"
    _FLOG_STAGE_T0["$st"]="$(_flog_now_ms)"
    _flog_emit $_FLOG_INFO INFO "STAGE=${st} status=start"
}

# flog_stage_end <stage> [rc=N extra=val ...] - close it, emit dur_ms.
flog_stage_end() {
    local st="${1:-stage}"; shift || true
    local t0="${_FLOG_STAGE_T0[$st]:-}" dur=""
    [[ -n "$t0" ]] && dur=" dur_ms=$(( $(_flog_now_ms) - t0 ))"
    _flog_emit $_FLOG_INFO INFO "STAGE=${st} status=done${dur}${*:+ $*}"
    unset "_FLOG_STAGE_T0[$st]" 2>/dev/null || true
}

# flog_dump <label> <path> - copy a file into the per-merge debug dir.
flog_dump() {
    local label="${1:-dump}" path="${2:-}"
    if [[ -z "$path" || ! -e "$path" ]]; then
        _flog_emit $_FLOG_DEBUG DEBUG \
            "dump label=${label} path=${path:-<none>} status=absent"
        return 0
    fi
    if [[ -n "${FROGNET_DEBUG_DIR:-}" ]]; then
        cp -a -- "$path" "${FROGNET_DEBUG_DIR}/${label}"
        _flog_emit $_FLOG_DEBUG DEBUG \
            "dump label=${label} src=${path} file=${FROGNET_DEBUG_DIR}/${label}"
    fi
    return 0
}

# flog_dump_cmd <label> <cmd...> - run a command, capture stdout+stderr.
# Diagnostics must NEVER fail the caller, so this always returns 0.
flog_dump_cmd() {
    local label="${1:-cmd}"; shift || true
    local rc=0 out
    out="$("$@" 2>&1)" || rc=$?
    if [[ -n "${FROGNET_DEBUG_DIR:-}" ]]; then
        printf '%s\n' "$out" > "${FROGNET_DEBUG_DIR}/${label}.txt"
    fi
    _flog_emit $_FLOG_DEBUG DEBUG "dump_cmd label=${label} rc=${rc} cmd=$*"
    return 0
}
