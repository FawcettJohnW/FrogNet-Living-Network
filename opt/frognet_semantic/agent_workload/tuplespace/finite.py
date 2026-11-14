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
agent_workload/tuplespace/finite.py -- where the first non-finite value came
from.

[FIRST_NON_FINITE_V1]

A gradient run that ends with a non-finite model tells you only that it
happened. There are four places it can begin, and they mean entirely different
things:

    the producer          a worker's own backward pass overflowed. Numerical
                          instability in the training, nothing to do with
                          coordination.
    reconciliation        every contribution arrived finite and the reduction
                          of them was not. Evidence against mixing stale
                          contributions.
    the optimizer step    the update was finite and applying it was not.
                          Evidence about step size against staleness.
    the transport         a tensor was finite when its producer serialised it
                          and was not when the reader deserialised it. A
                          defect in this code, and it invalidates any
                          architectural reading of the run.

Only the middle two support a claim about the coordination model. So the
question a run has to answer is not "did it diverge" but "at which of those
four boundaries did the first non-finite value appear, and what was true one
step earlier".

This module is the latch. Every stage records what it saw; the first
observation that is not finite freezes itself and the preceding ring, and the
run keeps going, because whether it recovers is also evidence.

Everything is recorded, not only what the current hypothesis says matters. The
hypothesis is the thing that might be wrong.

  FROGNET_FINITE_WATCH=0     off
  FROGNET_FINITE_RING=N      observations kept before the transition (20)
"""

from __future__ import annotations

import collections
import math
import os
import time
from typing import Any, Deque, Dict, List, Optional

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag

_ON = os.environ.get("FROGNET_FINITE_WATCH", "1") not in ("0", "", "no", "off")

try:
    RING = int(os.environ.get("FROGNET_FINITE_RING", "20"))
except ValueError:
    RING = 20


def on() -> bool:
    return _ON


def num(x: Any) -> Any:
    """A float that survives JSON. inf and nan are not JSON numbers, and a
    report that cannot be written is not a report."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return "nan"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    return f


def tstat(t) -> Dict[str, Any]:
    """Everything cheap that is knowable about a tensor's values.

    `norm` is the real norm and is itself inf or nan when the tensor is --
    that is the answer, not a problem with the measurement. `finite_norm` is
    the norm of the entries that ARE finite, which is what says how large the
    tensor had grown before it overflowed. Both, because one without the other
    loses the magnitude regime.

    [THE_MEASUREMENT_MUST_NOT_OVERFLOW_BEFORE_THE_DATA_DOES_V1]
    Computed in float64 regardless of the tensor's dtype. In float32 a
    parameter vector whose largest entry is 1e20 has every element finite and
    a SUM OF SQUARES that is not: the norm overflows at 3.4e38 and reports
    inf. A first-transition table then prints `norm=inf` beside `finite=True`
    and the reader has to know which column to disbelieve. float64 carries to
    1.8e308, so the norm reaching inf now means the data did.

    `absmax_finite` is the scale indicator to read near an overflow: it is the
    largest magnitude actually present among the finite entries, and it does
    not depend on the accumulation width at all.
    """
    import torch
    try:
        with torch.no_grad():
            f = t.detach().to("cpu").reshape(-1).double()
            mask = torch.isfinite(f)
            n_fin = int(mask.sum())
            n = int(f.numel())
            fin = f[mask] if n_fin else f[:0]
            return {
                "n": n,
                "finite": n_fin == n,
                "dtype": str(t.dtype),
                "n_nan": int(torch.isnan(f).sum()),
                "n_inf": int(torch.isinf(f).sum()),
                "norm": num(f.norm()),
                "finite_norm": num(fin.norm()) if n_fin else None,
                "min": num(f.min()) if n else None,
                "max": num(f.max()) if n else None,
                "absmax_finite": num(fin.abs().max()) if n_fin else None,
            }
    except Exception as e:                             # pragma: no cover
        # A diagnostic must not become the failure it was added to explain.
        return {"stat_error": "%s: %s" % (type(e).__name__, e),
                "finite": None}


class FiniteWatch:
    """One latch per worker, shared by every stage that handles a tensor.

    One object rather than one per module so the stages are ordered against
    each other. "the peer's tensor was finite when read and the reduction of
    it was not" is a statement about two records in one sequence, and it
    cannot be made from two independent logs.
    """

    __slots__ = ("who", "ring", "first", "seen", "stages", "_n")

    def __init__(self, who: str, ring: int = 0):
        self.who = str(who)
        self.ring: Deque[Dict[str, Any]] = collections.deque(
            maxlen=max(1, ring or RING))
        #: The frozen first transition: the record, and what preceded it.
        self.first: Optional[Dict[str, Any]] = None
        #: How many non-finite observations in total, by stage.
        self.seen = 0
        self.stages: Dict[str, int] = {}
        self._n = 0

    def observe(self, stage: str, step: Optional[int] = None,
                tensor=None, **fields) -> bool:
        """Record one stage's view. Returns True if this observation is not
        finite.

        `tensor` is measured here rather than by the caller, so every stage
        reports the same quantities in the same way.
        """
        if not _ON:
            return False
        rec: Dict[str, Any] = {"i": self._n, "stage": stage, "step": step,
                               "t": round(time.time(), 6), "who": self.who}
        self._n += 1
        if tensor is not None:
            rec.update(tstat(tensor))
        rec.update(fields)
        self.ring.append(rec)

        bad = rec.get("finite") is False
        if not bad:
            return False
        self.seen += 1
        self.stages[stage] = self.stages.get(stage, 0) + 1
        if self.first is None:
            preceding = [dict(r) for r in list(self.ring)[:-1]]
            self.first = {"stage": stage, "step": step, "who": self.who,
                          "record": dict(rec), "preceding": preceding}
            diag("DIAG-NONFINITE", "FIRST NON-FINITE", who=self.who,
                 stage=stage, step=step, record=rec,
                 preceding_kept=len(preceding))
            for r in preceding:
                diag("DIAG-NONFINITE", "ring", who=self.who, rec=r)
        else:
            diag("DIAG-NONFINITE", "non-finite (after first)", who=self.who,
                 stage=stage, step=step, record=rec)
        return True

    def summary(self) -> Dict[str, Any]:
        """What the run has to report even when nothing went wrong."""
        return {"who": self.who, "non_finite_seen": self.seen,
                "by_stage": dict(self.stages), "first": self.first}


#: Stage names, in the order a training step touches them. Named here so a
#: reader of the report does not have to grep for where a string came from.
STAGE_PARAM_PRE = "param.pre"
STAGE_GRAD_LOCAL = "grad.local"
STAGE_PUBLISH = "publish"
STAGE_PLANE_PUBLISH = "plane.publish"
STAGE_PLANE_RECV = "plane.recv"
STAGE_PEER_READ = "peer.read"
STAGE_SELF_CONTRIB = "self.contrib"
STAGE_REDUCE_OUT = "reduce.out"
STAGE_PARAM_POST = "param.post"

#: Which side of the causal boundary each stage sits on. `producer` and
#: `transport` do not support an architectural reading; `reconcile` and
#: `optimizer` do.
STAGE_CLASS = {
    STAGE_PARAM_PRE: "producer",
    STAGE_GRAD_LOCAL: "producer",
    STAGE_PUBLISH: "producer",
    STAGE_PLANE_PUBLISH: "producer",
    STAGE_PLANE_RECV: "transport",
    STAGE_PEER_READ: "transport",
    STAGE_SELF_CONTRIB: "producer",
    STAGE_REDUCE_OUT: "reconcile",
    STAGE_PARAM_POST: "optimizer",
}


def classify(stage: str) -> str:
    return STAGE_CLASS.get(stage, "unknown")


def first_of(watches: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Earliest first-transition across several workers' summaries.

    Ordered by step and then by wall clock. Steps are comparable because the
    workers are running the same loop; the clock breaks ties within a step and
    is on one host here. Across hosts it is the monotonic delta that
    correlates, not this.
    """
    firsts = [w["first"] for w in watches if w and w.get("first")]
    if not firsts:
        return None
    return sorted(firsts, key=lambda f: ((f.get("step") if f.get("step")
                                          is not None else 1 << 62),
                                         f["record"].get("t", 0.0)))[0]
