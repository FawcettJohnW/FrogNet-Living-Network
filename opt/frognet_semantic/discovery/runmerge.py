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

    # [RUNAGAIN_ON_REAL_DELTA_V1] These three are now OBSERVATIONS, not votes.
    # They are recorded because they name what the pass did; they no longer
    # decide whether it runs again. The only thing that re-runs a merge is a
    # real before/after difference in the routing table, and that verdict is
    # reached in live.main() -- AFTER the fixDefault/manageResolv tail, which
    # writes routes of its own and therefore has to be inside the window.
    #
    # What this replaces: route_table_mutated is set when a write returns rc=0.
    # `ip route replace` on an already-correct route returns 0. A /24 that reap
    # removed and promote re-installed returns 0. Both latched runAgain on a
    # node whose table was identical at both ends of the pass -- and because a
    # dirty chain ends in propogateNotification, every one of those passes told
    # the whole mesh to merge too.
    #
    # [HOSTS_NAME_CHANGE_RUNAGAIN_V1] is likewise demoted: a corrected
    # /etc/hosts name is real, but it is not a routing change, so it no longer
    # re-runs the pass. It still reaches peers through the normal hosts commit.
    routes_mutated = bool(getattr(disc.r, "route_table_mutated", False))
    name_changed = bool(out.get("host_name_changed"))
    # [MUTATED_DEST_TRACE_V1] Name the exact /24(s) written, so a non-converging
    # chain points straight at the churning dest instead of being inferred.
    mutated_dests = sorted(getattr(disc.r, "mutated_slash24", set()))
    logger(f"merge_observations sync_required={sync_required} "
           f"depth={depth} cap={MAX_MERGE_DEPTH} "
           f"slash24_written={int(routes_mutated)} name_changed={int(name_changed)} "
           f"concurrent={int(concurrent_attempt)} "
           f"slash24_dests={mutated_dests}")
    logger("completed")

    out["routes_mutated"] = routes_mutated
    out["name_changed"] = name_changed
    out["concurrent_attempt"] = bool(concurrent_attempt)
    out["converge"] = dict(sync_required=sync_required, depth=depth,
                           cap=MAX_MERGE_DEPTH, forked=False)
    return out
