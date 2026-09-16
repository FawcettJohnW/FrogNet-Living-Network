#!/usr/bin/env python3
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
core/stat_schema.py -- what a counter means, declared where it is defined.

[A_STAT_DECLARES_ITS_OWN_AGGREGATION_V1]

There were two sources of truth. Counters were created in
reduce_by_read.py's stats dict; the rule for combining them across workers
lived in frogtorch_mesh.py as a frozenset called ADDITIVE_STATS. Adding a
counter in one file without editing the other made it silently invisible in
every study -- which is exactly what happened to `superseded`: it was
incremented correctly on every supersession and reported as None, because the
study did not recognise the name and refused to guess.

Refusing to guess was right. Needing to guess was the defect.

Every counter is declared here once, with the only thing a study cannot
work out for itself: what combining it across workers MEANS. A sum of four
workers' maxima is not a maximum -- that was `lag_max` reporting the accept
cap times the world size as though it were an observation. A max of four
counts is not a count.

This module imports nothing but the standard library. It is read by the
tuplespace modules, which pull in torch lazily, and by the study and oracle
layers, which must not pull in torch at all merely to learn what a counter
means.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: How a counter combines across workers.
SUM = "sum"                  # counts of things that happened
MAX = "max"                  # an extremum one worker observed
MIN = "min"
LAST = "last"                # a current value, not a history
PER_WORKER = "per_worker"    # kept as a list; the spread is the point
UNAGGREGATED = "unaggregated"   # combining it would invent a meaning

KINDS = (SUM, MAX, MIN, LAST, PER_WORKER, UNAGGREGATED)


class Stat:
    __slots__ = ("name", "kind", "initial", "description")

    def __init__(self, name: str, kind: str, initial: Any = 0,
                 description: str = ""):
        if kind not in KINDS:
            raise ValueError("%s: unknown aggregation kind %r; one of %s"
                             % (name, kind, ", ".join(KINDS)))
        self.name = name
        self.kind = kind
        self.initial = initial
        self.description = description


def _s(*args, **kw) -> Stat:
    return Stat(*args, **kw)


#: The reduction group's counters. Order is documentation: what was offered,
#: what was accepted, what was refused and why, and what it cost.
REDUCE_STATS: List[Stat] = [
    _s("published", SUM, 0, "descriptors this worker offered"),
    _s("reduced", SUM, 0, "reductions this worker performed"),
    _s("accepted", SUM, 0,
       "contributions used, INCLUDING this worker's own"),
    _s("accepted_peers", SUM, 0,
       "contributions used from other workers -- the participation number"),
    _s("peers_possible", SUM, 0,
       "peers whose cell was present and could have contributed"),
    _s("rejected_stale", SUM, 0,
       "contributions the acceptance policy declined; not a departure"),
    _s("rejected_missing", SUM, 0, "descriptors with no payload at all"),
    _s("superseded", SUM, 0,
       "selected states the producer had moved past before the fetch; "
       "the peer answered correctly, so this is not unreachable"),
    _s("peer_gaps", SUM, 0,
       "peers that became unusable and later returned"),
    _s("descriptors_seen", SUM, 0, "cells read from the space"),
    _s("retired_seen", SUM, 0,
       "contributions flagged as departing; advisory, not liveness"),
    _s("unreachable", SUM, 0, "peers whose data plane could not be reached"),
    _s("lag_sum", SUM, 0, "sum of |lag| over accepted contributions"),
    _s("lag_n", SUM, 0, "how many lags went into lag_sum"),
    _s("lag_max", MAX, 0,
       "the largest |lag| any ONE worker saw. Summing this across workers "
       "reports the accept cap times the world size as an observation"),
    _s("ahead", SUM, 0, "accepted contributions from a peer further along"),
    _s("behind", SUM, 0, "accepted contributions from a peer further back"),
]

#: The data plane's counters.
PLANE_STATS: List[Stat] = [
    _s("states_offered", SUM, 0, "immutable states advertised"),
    _s("states_materialised", SUM, 0,
       "states whose bytes were actually made, because someone asked"),
    _s("materialise_s", SUM, 0.0,
       "seconds spent serialising and hashing on demand"),
    _s("bytes_offered", SUM, 0, "bytes a reader could have taken"),
    _s("bytes_sent", SUM, 0, "bytes that crossed"),
    _s("same_replies", SUM, 0, "requests answered without sending bytes"),
    _s("data_replies", SUM, 0, "requests that sent bytes"),
]

_BY_NAME: Dict[str, Stat] = {}
for _group in (REDUCE_STATS, PLANE_STATS):
    for _st in _group:
        if _st.name in _BY_NAME and _BY_NAME[_st.name].kind != _st.kind:
            raise ValueError("stat %r declared twice with different kinds"
                             % _st.name)
        _BY_NAME[_st.name] = _st


def kind_of(name: str) -> Optional[str]:
    """How this counter combines, or None if it was never declared.

    None means "this study has never heard of it", and the caller must NOT
    guess. Reporting it per worker is honest; summing it is how `lag_max`
    became a bound reported as an observation.
    """
    st = _BY_NAME.get(name)
    return st.kind if st is not None else None


def initial(stats: Iterable[Stat]) -> Dict[str, Any]:
    """A fresh counter dict for one group."""
    return {s.name: s.initial for s in stats}


def combine(per_worker: Iterable[Dict[str, Any]]):
    """Fold each worker's counters according to their declared kind.

    Returns (combined, unknown) -- `unknown` holds every name that has no
    declaration, as a list of the raw per-worker values. Nothing is dropped
    and nothing is invented.
    """
    combined: Dict[str, Any] = {}
    unknown: Dict[str, List[Any]] = {}
    for row in per_worker:
        for name, value in (row or {}).items():
            k = kind_of(name)
            if k is None:
                unknown.setdefault(name, []).append(value)
                continue
            if k == SUM:
                combined[name] = combined.get(name, 0) + value
            elif k == MAX:
                combined[name] = (value if name not in combined
                                  else max(combined[name], value))
            elif k == MIN:
                combined[name] = (value if name not in combined
                                  else min(combined[name], value))
            elif k == LAST:
                combined[name] = value
            elif k in (PER_WORKER, UNAGGREGATED):
                combined.setdefault(name, []).append(value)
    return combined, unknown


def describe() -> str:
    lines = ["%-20s %-12s %s" % ("stat", "combines", "meaning")]
    for st in REDUCE_STATS + PLANE_STATS:
        lines.append("%-20s %-12s %s" % (st.name, st.kind, st.description))
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
