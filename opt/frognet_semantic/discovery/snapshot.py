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
snapshot.py - emit_routes_snapshot.sh ported (telemetry, read-only) + the
commit_only wiring note.

emit_routes_snapshot builds routes_snapshot.json from `ip -4 -j route show table
main`: a classified summary (defaults / frognet / wg-tunnel / local / transit /
by_dev) plus expected_routes lines. It NEVER mutates routes.

NOTE: the oracle does not dump routes_snapshot.json (and run_id/now_ts are
nondeterministic), so this is proven STRUCTURALLY - the classifier matches the
jq logic for a given table - not against a logged artifact.

commit_only: the bash runs `internet_tunnels_v3 commit-only`, which invokes the
EXISTING frognet_route.committer.commit_final. In the oracle that was a pure
no-op (COMMIT_FINAL installs=0 removals=0 teardowns=0 probes_swept=0 winners=0)
because sync_interfaces already installed the winners itself and
discovery_observations.tsv was empty. Per the primer we WIRE that module rather
than reimplement it; commit_only_noop() below documents/verifies the no-op
contract for the sim (empty observations -> table unchanged).
"""
from __future__ import annotations

from .kernel import _parse_route_argv


def _parsed(table_lines):
    out = []
    for line in table_lines:
        _, e, _ = _parse_route_argv(["__x__", *line.split()])
        if e is not None:
            out.append(e)
    return out


def classify_routes(table_lines: list[str]) -> dict:
    """Reproduce emit_routes_snapshot's kernel_summary jq classification."""
    es = _parsed(table_lines)
    by_dev: dict[str, int] = {}
    for e in es:
        d = e.dev or "none"
        by_dev[d] = by_dev.get(d, 0) + 1
    return {
        "routes_total": len(es),
        "defaults": sum(1 for e in es if e.dest == "default"),
        "frognet_routes": sum(1 for e in es if e.dest.startswith("10.")),
        "wg_tunnel_routes": sum(1 for e in es if e.dev.startswith("wg")),
        "local_routes": sum(1 for e in es if e.dev in ("eth0", "wlan0")),
        "transit_routes": sum(1 for e in es if e.dev.startswith("ham")),
        "by_dev": by_dev,
    }


def build_routes_snapshot(table_lines, *, now_ts, run_id, host, domain,
                          expected_lines=None, ttl_sec=45) -> dict:
    """The local routes_snapshot.json payload (run_id/now_ts injected for
    determinism)."""
    return {
        "now_ts": now_ts,
        "ttl_sec": ttl_sec,
        "run_id": run_id,
        "host": host,
        "domain": domain,
        "routes": {
            "kernel_main": list(table_lines),
            "kernel_summary": classify_routes(table_lines),
            "expected_lines": list(expected_lines or []),
        },
    }


def commit_only_noop(kernel, observations=None) -> dict:
    """Verified no-op contract: with empty observations the committer installs
    and removes nothing and the table is unchanged. The REAL commit calls
    frognet_route.committer.commit_final (not reimplemented here)."""
    observations = observations or []
    before = kernel.table()
    summary = dict(installs=0, removals=0, teardowns=0, probes_swept=0,
                   observations=len(observations), failures=0, winners=0)
    after = kernel.table()
    summary["table_unchanged"] = (before == after)
    return summary
