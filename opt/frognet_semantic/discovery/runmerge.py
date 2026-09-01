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
"""
runmerge.py - the runMerge controller, ported from runMerge.bash.

ONE invocation = ONE merge pass. runMerge:
  lock (flock) -> setup_iptables -> clear runAgain -> set mergePending
  -> flush_discovery_cache -> mergeHostsAndResolv -> converge_decision -> cleanup

The converge recursion (fork-if-changed) is COMMENTED OUT in the current bash
(lines 95-101), so a pass never forks regardless of sync_required/runAgain. We
preserve that: converge_decision is logged, no fork.

`runAgain` is set when a concurrent runMerge attempt hits the held lock and bails
(flock -n fails -> touch runAgain). That's a cross-process signal, not derivable
from a single in-process merge, so the simulator injects it
(concurrent_attempt). The oracle run had runAgain=1 (a concurrent attempt bailed
during the pass).
"""
from __future__ import annotations

from .orchestrate import merge

MAX_MERGE_DEPTH = 10


def run_merge(disc, kernel, seeds, upstream_seed, *, local, bringup_peers,
              prior_names=None, depth=0, concurrent_attempt=False,
              logger=lambda s: None,
              setup_iptables=lambda: 0, flush_cache=lambda: 0):
    """Run one merge pass. Returns the merge result dict plus converge fields."""
    logger(f"ENTER depth={depth} argv_count=0")
    logger("lock_acquired lockfile=/var/run/runMerge.lock")
    logger(f"setup_iptables rc={setup_iptables()}")
    logger("cleared_runAgain")
    logger("merge_pending_set file=/etc/sentinels/mergePending")
    logger(f"flush_discovery_cache rc={flush_cache()}")
    # [NOT_FROGNET_V1] A merge re-evaluates the whole view, so the per-session
    # non-FrogNet cache is cleared here: every previously-skipped host gets one
    # fresh probe in case it joined FrogNet since the last merge.
    # [FAILFAST_V1] Unguarded: not_frognet's contract ("one fresh probe per
    # merge") depends on this flush. Proceeding un-flushed silently kept
    # recovered nodes poisoned; if flush fails, the merge must fail loudly.
    from core.not_frognet import flush as _nf_flush
    logger(f"flush_not_frognet cleared={_nf_flush()}")
    # [HOPS_CLEAR_EVERY_MERGE_V1] Stamp the merge boundary so caches living in
    # OTHER processes (the proxy's decision._HOPS) can clear themselves. Same
    # merge-top event as the not_frognet flush above; unguarded for the same
    # reason -- a cache that silently outlives its run is how a latched SLOW
    # mode survived every merge until a restart.
    import os as _os, time as _time
    _gen = _os.path.join(_os.environ.get("FROGNET_SENTINEL_DIR", "/etc/sentinels"),
                         "merge_generation")
    _os.makedirs(_os.path.dirname(_gen), exist_ok=True)
    with open(_gen, "w") as _f:
        _f.write("%.6f\n" % _time.time())
    logger(f"merge_generation_stamped file={_gen}")

    out = merge(disc, kernel, seeds, upstream_seed,
                local=local, bringup_peers=bringup_peers,
                prior_names=prior_names, logger=logger)

    sync_required = 1 if out["sync_required"] else 0

    # [RUNAGAIN_ON_MUTATION_V1] A merge that changed any /24 WINNER has moved the
    # mesh: this node's /24 changes are exactly what peers react to, and their
    # reactions change what this node sees next pass. So re-run whenever a /24
    # winner was added/replaced this pass (route_table_mutated, scoped to /24s in
    # routes.rtmut - /32 probes/aliases and /30 transit excluded, and a reap of a
    # non-winner excluded per REAP_NOT_CONVERGENCE_V1 - so a settled mesh leaves
    # it False). Also re-run on the legacy concurrent-lock bail.
    # [HOSTS_NAME_CHANGE_RUNAGAIN_V1] A peer's authoritative name superseding a
    # deprecated /etc/hosts name is also a mesh move: re-run so the corrected
    # name re-propagates. A run that changes no /24 winner and no name is clean.
    routes_mutated = bool(getattr(disc.r, "route_table_mutated", False))
    name_changed = bool(out.get("host_name_changed"))
    run_again = 1 if (concurrent_attempt or routes_mutated or name_changed) else 0
    # [MUTATED_DEST_TRACE_V1] Name the exact /24(s) that moved, so a non-converging
    # chain points straight at the churning dest instead of being inferred.
    mutated_dests = sorted(getattr(disc.r, "mutated_slash24", set()))
    logger(f"converge_decision sync_required={sync_required} runAgain={run_again} "
           f"depth={depth} cap={MAX_MERGE_DEPTH} "
           f"slash24_mutated={int(routes_mutated)} name_changed={int(name_changed)} "
           f"concurrent={int(concurrent_attempt)} "
           f"slash24_dests={mutated_dests}")
    # [RUNAGAIN_ON_MUTATION_V1] Surface the decision to the bash wrapper via the
    # sentinel it already reads (/etc/sentinels/runAgain). The wrapper sets this
    # only on a concurrent-lock bail; we additionally set it on a /24 mutation so
    # the wrapper re-invokes for another convergence pass (bounded by
    # MAX_MERGE_DEPTH on the bash side); clear it on a clean pass.
    try:
        import os as _os
        _os.makedirs("/etc/sentinels", exist_ok=True)
        if run_again:
            open("/etc/sentinels/runAgain", "w").close()
        else:
            try:
                _os.remove("/etc/sentinels/runAgain")
            except FileNotFoundError:
                pass
    except OSError as _e:
        logger(f"runAgain sentinel write failed: {_e}")
    # recursion fork is commented out in current bash -> never forks
    logger("completed")

    out["converge"] = dict(sync_required=sync_required, run_again=run_again,
                           depth=depth, cap=MAX_MERGE_DEPTH, forked=False)
    return out
