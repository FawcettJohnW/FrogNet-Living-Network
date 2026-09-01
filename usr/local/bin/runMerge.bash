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
# /usr/local/bin/runMerge.bash  —  PYTHON CUTOVER WRAPPER
#
# Single authoritative merge controller. Preserves the original controller
# contract (lock, merge-id, setup_iptables, flush cache, sentinels, converge
# decision) and the daemon fork interface, but the MERGE GUTS are now the
# Python port:  bring-up + discovery + promote + /etc/hosts commit are done by
#   PYTHONPATH=/opt/frognet_semantic <venv>/python3 -m discovery.live
#
# RC-1: the python merge now covers the FULL tail — fixDefaultRoute (Mode A/B +
# forced override) and manageResolv (resolv.conf) are ported and run inside
# `discovery.live`. The bash legacy tail is therefore OFF by default
# (FROGNET_MERGE_LEGACY_TAIL=0); set it to 1 only as an escape hatch to re-run
# the old bash fixDefaultRoute/manageResolv alongside the python merge.
#
# Cutover gate: this replaces sync_interfaces.sh/mergeHostsAndResolv.bash as the
# merge path. The deciding pre-bare-metal test remains the on-box differential
# (python vs bash, same node — diff `ip r` + /etc/hosts).

[[ -f /usr/local/lib/frognet_trace.sh ]] && . /usr/local/lib/frognet_trace.sh

# [PYCACHE_PURGE_V1] Remove stale bytecode BEFORE anything runs. A leftover
# __pycache__/*.pyc from a prior build makes Python load the OLD bytecode instead
# of the updated .py on disk — so a freshly deployed fix silently does not run.
# Purge every merge, up front, across all FrogNet code roots.
for _pcroot in /opt/frognet_semantic /usr/local/bin /etc/frognet_bundles; do
    [[ -d "$_pcroot" ]] || continue
    find "$_pcroot" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
    find "$_pcroot" -type f -name '*.pyc' -delete 2>/dev/null
done
if [[ -z "${FROGNET_MERGE_ID:-}" ]]; then
    export FROGNET_MERGE_ID="$(date +%Y%m%dT%H%M%S)-$$"
    export FROGNET_MERGE_OWNER=1
fi
. /usr/local/lib/frognet_log.sh
flog_init "runMerge"

# [MERGE_RUNS_FROM_ROOT_V1] This script deletes whole directory trees --
# /tmp/frognet*, /var/run/frognet/*, /etc/sentinels/* -- so it must not be
# RUNNING from one. An operator who launches a merge (or anything that forks
# one) from inside a matching directory has their cwd removed underneath them,
# and every subsequent fork in the merge's own process tree emits
#
#   shell-init: error retrieving current directory: getcwd: cannot access
#               parent directories: No such file or directory
#
# hundreds of times, interleaved with the merge log. Observed on a node with the
# operator sitting in /tmp/frognet-fixes/tree while `rm -rf /tmp/frognet*` ran.
# Harmless -- the merge completes rc=0 -- but it buries the log it writes.
#
# This script uses no relative paths, no $PWD and no $0 directory, so pinning to
# / costs nothing and makes the condition unreachable from here.
cd / || true


FROGNET_MERGE_DEPTH="${FROGNET_MERGE_DEPTH:-0}"
MAX_MERGE_DEPTH="${MAX_MERGE_DEPTH:-10}"
PYBIN="${FROGNET_PYBIN:-/opt/frognet_semantic/venv/bin/python3}"
[[ -x "$PYBIN" ]] || PYBIN="/usr/bin/python3"
LEGACY_TAIL="${FROGNET_MERGE_LEGACY_TAIL:-0}"

flog_info "ENTER" "depth=$FROGNET_MERGE_DEPTH" "argv_count=$#" "pybin=$PYBIN"

# ---- lock (hard re-entrancy guard) --------------------------------------
LOCKFILE="/var/run/runMerge.lock"
exec 9>"$LOCKFILE" || { flog_error "lock_open_failed" "lockfile=$LOCKFILE"; exit 1; }
if ! flock -n 9; then
    flog_warn "BAIL" "reason=lock_held" "depth=$FROGNET_MERGE_DEPTH"
    mkdir -p /etc/sentinels; : > /etc/sentinels/runAgain
    flog_info "runAgain_touched" "reason=for_running_instance"
    exit 0
fi
flog_info "lock_acquired" "lockfile=$LOCKFILE"

# [TUNNEL_AUTOSWITCH_V1] Automatic Internet<->LAN tunnel switch, at the top of
# every merge. Replaces frognet_internet_watch.sh and the manual make-gateway /
# unmake-gateway scripts: nothing promotes or demotes a node by hand anymore.
# Enrollment (setup_lillypad_v4 -> tunnel-setup register + WG creds) is a
# separate, one-time concern and is NOT touched here.
#
# The 2x2, evaluated each merge:
#     online  = the broker is reachable (NOT "has Internet" -- a node can reach
#               the broker over the LAN transit mesh). Reuses the daemon's own
#               broker client via `internet_tunnels_v3 broker-reachable`.
#     tunnels = any wgN interface exists in the kernel.
#
#     offline + tunnels present  -> frognet_nuke_tunnels --keep-config
#                                   (go LAN-only; drop live tunnels, KEEP creds
#                                    so a later bring-up needs no re-enrolment)
#     online  + no tunnels        -> bring-up (rejoin: build the broker's channels)
#     online  + tunnels present   -> no-op (steady state)
#     offline + no tunnels        -> no-op (steady LAN-only)
#
# Idempotent by construction: only the two transition quadrants act. Cheap on the
# common no-change pass (one broker probe + one iface check).
flog_stage tunnel_autoswitch
_PYBIN_AS="${FROGNET_PYBIN:-/opt/frognet_semantic/venv/bin/python3}"
[[ -x "$_PYBIN_AS" ]] || _PYBIN_AS="/usr/bin/python3"

if ip -o link show type wireguard 2>/dev/null | grep -q '^[0-9]'; then
    _AS_TUNNELS=1
else
    _AS_TUNNELS=0
fi

if ( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic "$_PYBIN_AS" -m internet_tunnels_v3 broker-reachable ) >/dev/null 2>&1; then
    _AS_ONLINE=1
else
    _AS_ONLINE=0
fi

flog_info "autoswitch" "online=$_AS_ONLINE" "tunnels=$_AS_TUNNELS"

if [[ "$_AS_ONLINE" -eq 0 && "$_AS_TUNNELS" -eq 1 ]]; then
    flog_info "autoswitch" "action=nuke_keep_config" "reason=offline_with_tunnels"
    /usr/local/bin/frognet_nuke_tunnels --keep-config 2>&1 | while IFS= read -r _l; do flog_info "autoswitch_nuke" "$_l"; done || true
elif [[ "$_AS_ONLINE" -eq 1 && "$_AS_TUNNELS" -eq 0 ]]; then
    flog_info "autoswitch" "action=bringup" "reason=online_no_tunnels"
    ( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic "$_PYBIN_AS" -m internet_tunnels_v3 bring-up-only ) 2>&1 | while IFS= read -r _l; do flog_info "autoswitch_bringup" "$_l"; done || true
else
    flog_info "autoswitch" "action=none" "reason=steady_state"
fi
flog_stage_end tunnel_autoswitch "rc=$?"

flog_stage setup_iptables
/etc/setup_iptables
flog_stage_end setup_iptables "rc=$?"

SENT_DIR="/etc/sentinels"
rm -rf /etc/sentinels/*
rm -rf /tmp/frognet*

# [WIPE_THEN_REOPEN_V1] The wipe below is deliberate: a merge inherits nothing
# from a previous run. But /var/run is a symlink to /run, so it also removes
# /run/frognet/debug/<TS>-<PID> -- the directory flog_init created at line 36 and
# is currently writing trace.log into. Every flog_* call after this point then
# failed its redirect:
#
#   /usr/local/lib/frognet_log.sh: line 82:
#     /run/frognet/debug/20260824T065035-2073849/trace.log: No such file or directory
#
# Harmless to the merge -- bash reports the failed redirect and carries on, which
# is why STAGE=identity_preflight still prints -- but it is noise on every merge
# and it costs the trace from this line onward.
#
# So: keep the wipe, then re-create THIS run's directory. Resolved by glob on the
# pid suffix rather than by reaching into the log library's internals, so it does
# not break if flog_init's variable names change. If the glob finds nothing the
# behaviour is exactly what it is today.
_flog_dir="$(ls -1d /run/frognet/debug/*-$$ 2>/dev/null | head -1)"
rm -rf /var/run/frognet/*
[[ -n "$_flog_dir" ]] && mkdir -p "$_flog_dir"

MERGE_PENDING="$SENT_DIR/mergePending"
RUN_AGAIN="$SENT_DIR/runAgain"
SYNC_REQUIRED="$SENT_DIR/sync_required"
mkdir -p "$SENT_DIR"; chmod -R 777 "$SENT_DIR" 2>/dev/null || true

flog_dump_cmd "pre_merge_routes" ip -4 route show
flog_dump_cmd "pre_merge_hosts"  cat /etc/hosts

rm -f "$RUN_AGAIN"; flog_info "cleared_runAgain"
# sync_required is written by the merge (live.py) ONLY on a pass that changes a
# route or the topology; absent means this pass was clean. It is the raw per-pass
# "changed" input to the propagate gate below, NOT the loop control (that is
# runAgain). Clear it at the pass top so a stale flag from a prior pass cannot
# make a clean pass look dirty.
rm -f "$SYNC_REQUIRED"
: > "$MERGE_PENDING"; flog_info "merge_pending_set" "file=$MERGE_PENDING"

cleanup() { rc=$?; flog_info "cleanup" "rc=$rc"; rm -f "$MERGE_PENDING"; flog_info "EXIT" "rc=$rc"; }
trap cleanup EXIT INT TERM

flog_stage flush_discovery_cache
/usr/local/bin/frognet_discovery_cache.sh flush 2>/dev/null || true
flog_stage_end flush_discovery_cache "rc=$?"

# [FLUSH_HAS_A_CALLER_V1] The not_frognet negative cache is merge-scoped and
# NOTHING WAS CLEARING IT. core/not_frognet.py's docstring claimed this happened
# in the flush_discovery_cache stage above -- but that stage runs
# frognet_discovery_cache.sh, whose whole body is `DELETE FROM TransitPeerCache;`,
# an unrelated MySQL table. A mark was therefore permanent for the life of the
# proxy process.
#
# Measured on Seattle3, 2026-08-08: one legitimate ECONNREFUSED at 09:02:09
# during a two-second restart of Seattle5 marked 10.250.250.1 -- the elected
# databasehost -- and it was never dialled again. 28 consecutive worker
# retirements over four minutes, zero reconnect attempts, every sensor upsert
# 503, capability tuple never published, databasehost floated.
#
# This clears the SENTINEL FILE from the merge's own interpreter. Every other
# process sees it gone on its next is_marked(), because _reload_locked() stats
# the file per call -- no proxy restart, which is the entire point of the disk
# backing.
#
# NOT `|| true`. A flush that failed and said nothing is how this was missed for
# weeks: the whole class of bug here is a clearing step that quietly does not
# clear. rc is recorded and a failure is loud.
flog_stage flush_not_frognet
_NF_OUT="$(cd /opt/frognet_semantic && "$PYBIN" -m core.not_frognet flush 2>&1)"
_NF_RC=$?
if [[ $_NF_RC -ne 0 ]]; then
    flog_info "flush_not_frognet_FAILED" "rc=$_NF_RC" "out=${_NF_OUT}"
    echo "[runMerge] FLUSH FAILED: the not_frognet negative cache was NOT cleared" \
         "(rc=$_NF_RC): ${_NF_OUT}" >&2
    echo "[runMerge] every peer marked non-FrogNet stays marked until this" \
         "succeeds or the proxy restarts" >&2
else
    flog_info "flush_not_frognet" "${_NF_OUT}"
fi
flog_stage_end flush_not_frognet "rc=$_NF_RC"

# ---- identity preflight (front gate) ------------------------------------
# The interface that bears this node's identity must be up and carrying .1/.2
# per the CONFIGURED mode (wired -> eth0Name, AP -> wlan0/1). If not, the node
# cannot function and the merge FAILS LOUDLY here, before any route work —
# instead of the old silent degrade. Soft stand-alone mode is the explicit
# exception and synthesizes the interface. See discovery.identity_preflight.
flog_stage identity_preflight
( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic "$PYBIN" -m discovery.identity_preflight )
pf_rc=$?
flog_stage_end identity_preflight "rc=$pf_rc"
if [[ "$pf_rc" -ne 0 ]]; then
    flog_error "ABORT" "reason=identity_preflight_failed" "rc=$pf_rc"
    echo "runMerge: ABORT — identity interface preflight failed; node cannot bear its identity. See IDENTITY-PREFLIGHT above, or enable soft mode: frognet-soft-standalone enable" >&2
    exit "$pf_rc"
fi

# ---- THE MERGE: python port (bring-up + discovery + promote + hosts) -----
flog_stage discovery_merge_py
( cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic "$PYBIN" -m discovery.live )
merge_rc=$?
flog_stage_end discovery_merge_py "rc=$merge_rc"

# ---- legacy tail not yet ported: default route + dnsmasq forwarders ------
if [[ "$LEGACY_TAIL" == "1" ]]; then
    flog_stage legacy_fixDefaultRoute
    [[ -x /usr/local/bin/fixDefaultRoute ]] && /usr/local/bin/fixDefaultRoute
    flog_stage_end legacy_fixDefaultRoute "rc=$?"
    flog_stage legacy_manageResolv
    [[ -x /usr/local/bin/manageResolv.bash ]] && /usr/local/bin/manageResolv.bash
    flog_stage_end legacy_manageResolv "rc=$?"
fi

flog_dump_cmd "post_merge_routes" ip -4 route show
flog_dump_cmd "post_merge_hosts"  cat /etc/hosts

# sync_required present => this pass changed something (feeds chain_dirty for the
# propagate-on-converged gate). It does NOT control the loop; runAgain does.
_sr=0; [[ -f "$SYNC_REQUIRED" ]] && _sr=1
_ra=0; [[ -f "$RUN_AGAIN"     ]] && _ra=1

# [PROPAGATE_ON_CONVERGED_V1] Track whether ANY pass in the re-invoke chain
# changed something. The chain carries this forward via FROGNET_MERGE_DIRTY; a
# pass marks it dirty if it set sync_required (host/route delta this pass). We
# do NOT propagate per-pass — an intermediate pass that is about to re-run would
# announce a half-converged state, and the final clean pass changes no /24 by
# definition so a per-pass test would miss it. Instead: propagate exactly once,
# on the CONVERGED pass (runAgain=0), iff the chain was dirty.
_dirty="${FROGNET_MERGE_DIRTY:-0}"
if [[ "$_sr" -eq 1 ]]; then _dirty=1; fi

flog_info "converge_decision" "changed_this_pass=$_sr" "runAgain=$_ra" "merge_rc=$merge_rc" \
          "depth=$FROGNET_MERGE_DEPTH" "cap=$MAX_MERGE_DEPTH" "chain_dirty=$_dirty"

/usr/local/bin/makeHostJson.bash rebuild
# [RUNAGAIN_ON_MUTATION_V1] A merge that changed a /24 sets runAgain (Python
# converge_decision -> $RUN_AGAIN). The mesh rarely converges in one pass — this
# node's /24 changes are what peers react to, and their reactions change what
# this node sees next pass. Re-invoke until a pass changes no /24 (runAgain
# cleared) or we hit MAX_MERGE_DEPTH. Carry chain_dirty forward so the converged
# pass knows whether the overall merge changed anything worth announcing.
if [[ "$_ra" -eq 1 ]]; then
    if [[ "$FROGNET_MERGE_DEPTH" -lt "$MAX_MERGE_DEPTH" ]]; then
        _next=$(( FROGNET_MERGE_DEPTH + 1 ))
        flog_info "runAgain_reinvoke" "next_depth=$_next" "cap=$MAX_MERGE_DEPTH" \
                  "chain_dirty=$_dirty" "reason=changed_24_not_yet_converged"
        # release the lock (close fd 9) before re-invoking so the child acquires it
        rm -f "$MERGE_PENDING" 2>/dev/null || true
        exec 9>&-
        FROGNET_MERGE_DEPTH="$_next" FROGNET_MERGE_DIRTY="$_dirty" exec "$0" "$@"
    else
        flog_warn "runAgain_capped" "depth=$FROGNET_MERGE_DEPTH" "cap=$MAX_MERGE_DEPTH" \
                  "chain_dirty=$_dirty" "reason=max_merge_depth_reached_stopping"
        # fall through: capped is treated as converged-enough; announce if dirty.
    fi
fi

# [PROPAGATE_ON_CONVERGED_V1] We reach here only on the converged (or depth-
# capped) pass — the happy run. Tell peers once, iff the overall merge changed
# something. Neighbor-scoped (direct LAN next-hops + WG tunnel peers); each
# receiver re-propagates; dedup by event id.
if [[ "$_dirty" -eq 1 ]]; then
    flog_info "PROP" "converged=YES" "chain_dirty=1" "action=propogateNotification"
    /usr/local/bin/propogateNotification
else
    flog_info "PROP" "converged=YES" "chain_dirty=0" "reason=no_change_in_merge_chain"
fi

# [DEFERRED_BROKER_SETUP_V1] Retry an unfinished broker enrolment.
#
# A node configured as a gateway at install time may not have reached the broker
# yet -- no upstream, DNS not up, broker down. The install writes the config
# anyway and leaves enrolment pending; every merge retries it. runMerge fires on
# any network change, which is exactly when a previously unreachable broker is
# likely to have become reachable. There is no timer: the dnsmasq dhcp-script and
# the NetworkManager up/down and connectivity-change hooks all land here.
#
# Cheap in the common case: one file test. frognet-pond-bootstrap.sh writes the
# sentinel only once enrolment completed AND the tunnel daemon is running, so
# this stops firing on its own. Backgrounded so a slow broker never stalls a merge.
if [[ ! -f /etc/frognet/pond_bootstrap_done \
      && -x /usr/local/bin/frognet-pond-bootstrap.sh ]] \
   && grep -q '^BROKER_URL=..*' /etc/frognet/tunnel.conf 2>/dev/null; then
    flog_info "BROKER_SETUP" "state=pending" "action=retry_pond_bootstrap"
    /usr/local/bin/frognet-pond-bootstrap.sh >/dev/null 2>&1 &
fi

flog_info "completed"
exit 0
