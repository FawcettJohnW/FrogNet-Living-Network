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
agent_workload/tuplespace/policy.py -- mechanism below, training policy above.

[REDUCE_IS_A_POLICY_NOT_THE_PRIMITIVE_V1]

`reduce(name, tensor, step)` was the application-facing abstraction, and it
is collective-shaped: one call, every acceptable peer, one reduced result,
every step, synchronously. It cannot say "this peer needs nothing", "that one
should send a delta", "I should take another local step because nothing newer
exists". It cannot even say "do not read" -- the read happens, then the
filter runs, then a full reduction is produced.

The primitives underneath were always the right ones:

    publish(state)          offer what I hold
    contributions()         what exists, descriptors only, no bytes
    assess(...)             what is true about each candidate
    Contribution.tensor()   materialise the one I chose
    <policy>                what to do with it

`mean_of_current` is a policy over those, and it reproduces what `reduce()`
did. That is the seam: the mechanism below does not change when the policy
above does.

WHY Eligibility HAS FIVE FIELDS AND NOT ONE BOOLEAN

Every collapse we found today was two of these facts wearing each other's
clothes. A peer that had not published for a while was reported as departed
when the acceptance policy declined it. A producer that had advanced was
reported as an unreachable data plane. A worker that announced departure was
excluded from reduction as though it were absent. So they are separate
fields, and a policy that wants one boolean has to say which facts it is
collapsing and take responsibility for it:

    present     its cell is in the space
    fresh       the cell has been refreshed within PRESENCE_STALE_S
    departing   it says it intends to leave -- advisory, never presence
    usable      it has a payload this reader can actually fetch
    acceptable  this consumer's policy will mix it in

`acceptable` is the only one that is about the CONSUMER. The rest are about
the world.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag

import time

from .reduce_by_read import PRESENCE_STALE_S, ReduceGroup
from .tensor_plane import StateGone


class Eligibility:
    """What is true about one candidate, before anybody decides anything.

    Nothing here materialises a tensor. Every field is answered from the
    descriptor, which is the point: a policy gets to reason before it spends.
    """

    __slots__ = ("contribution", "present", "fresh", "departing", "usable",
                 "acceptable", "lag", "why",
                 # [PROVENANCE_BEFORE_CLEVERNESS_V1]
                 "producer_progress", "consumer_progress",
                 "last_accepted_generation", "generations_since_incorporated",
                 "rounds_since_incorporated", "effect_of_round_last_taken",
                 "state_kind")

    def __init__(self, c, mine, accept, presence_stale_s, group=None,
                 round_no=None):
        self.contribution = c
        self.present = True                  # it is in the set we just read
        self.fresh = (c.age_s is None or c.age_s <= presence_stale_s)
        self.departing = bool(c.retired)
        self.usable = bool((c.locator or {}).get("host")) or \
            c.values is not None
        self.lag = ((mine - c.generation)
                    if (mine is not None and c.generation is not None)
                    else None)
        self.acceptable = bool(accept(mine, c.generation, c.age_s))
        self.why = None

        # [PROVENANCE_BEFORE_CLEVERNESS_V1] Facts a policy might use, made
        # visible before any policy is made clever with them. Age alone is
        # known NOT to answer "is this worth consuming" -- state with more
        # measured drift has behaved well and state with less has produced a
        # pathological aggregate -- so recording lag and stopping there would
        # encode an assumption there is already evidence against.
        #
        # `effect_of_round_last_taken` is named at the length it is because
        # the short name would be a lie: an incorporation is a mean over
        # several producers, so the movement it caused cannot be attributed
        # to one of them. What is recorded is the effect of the ROUND in
        # which this producer was last among those taken.
        self.producer_progress = c.generation
        self.consumer_progress = mine
        self.state_kind = c.payload_mode
        la = (group._last_accepted.get(c.worker) if group is not None
              else None)
        self.last_accepted_generation = la
        self.generations_since_incorporated = (
            (c.generation - la) if (la is not None and c.generation is not None)
            else None)
        hist = getattr(group, "_incorporation_history", None) or {}
        h = hist.get(c.worker)
        self.rounds_since_incorporated = (
            (round_no - h["round"]) if (h and round_no is not None) else None)
        self.effect_of_round_last_taken = h["effect"] if h else None

    @property
    def worker(self):
        return self.contribution.worker

    def as_dict(self) -> Dict[str, Any]:
        return {"peer": self.worker, "generation": self.contribution.generation,
                # [PROVENANCE_BEFORE_CLEVERNESS_V1]
                "producer_progress": self.producer_progress,
                "consumer_progress": self.consumer_progress,
                "state_kind": self.state_kind,
                "last_accepted_generation": self.last_accepted_generation,
                "generations_since_incorporated":
                    self.generations_since_incorporated,
                "rounds_since_incorporated": self.rounds_since_incorporated,
                "effect_of_round_last_taken": self.effect_of_round_last_taken,
                "incarnation": self.contribution.incarnation,
                "present": self.present, "fresh": self.fresh,
                "departing": self.departing, "usable": self.usable,
                "acceptable": self.acceptable, "lag": self.lag,
                "age_s": self.contribution.age_s}


def assess(group: ReduceGroup, step: Optional[int] = None,
           contributions=None) -> List[Eligibility]:
    """Every candidate and what is true about it. Reads the space once.

    Costs one store read and no tensor bytes at all -- that separation is
    what makes a policy able to decline before it spends.
    """
    mine = group.generation(step)
    cs = group.contributions() if contributions is None else contributions
    # [A_POLICY_STILL_OWES_THE_COUNTERS_V1] Presence is judged once per
    # observation, and reduce() did it here. Routing a study through this
    # layer without it silently turned every departure report into "none":
    # the killed worker was still killed, and nobody was left watching.
    # [PRESENCE_IS_NOT_USABILITY_V1] Observation is recorded BEFORE the
    # policy decides, for any cell that was there. reduce() did this inside
    # its own loop, so the policy path left _last_observed empty and the
    # sweep below had no peers it could notice the absence of -- the killed
    # worker was still killed and every departure report said "none".
    for c in cs:
        if c.worker != group.worker and c.generation is not None:
            group._last_observed[c.worker] = max(
                group._last_observed.get(c.worker, c.generation), c.generation)
    group._sweep_presence(cs, mine)
    return [Eligibility(c, mine, group.accept, PRESENCE_STALE_S)
            for c in cs if c.worker != group.worker]


def record(e: "Eligibility", *, selected: bool, materialised: bool,
           incorporated: bool, outcome: str, reason: str = "",
           bytes_moved: int = 0, materialise_ms: float = 0.0,
           would_have: Optional[bool] = None) -> Dict[str, Any]:
    """[A_LIFECYCLE_RECORD_IS_NOT_A_STATUS_FIELD_V1] One candidate, one event.

    The report wants a funnel -- present, acceptable, materialised,
    incorporated -- and a funnel is a presentation. The record underneath
    keeps the facts orthogonal, because they answer different questions and
    one of them is not about the peer at all: `acceptable` is a statement
    about THIS consumer's policy, and collapsing it into a status field is
    exactly how a rejection became a departure this morning.

    So there is no status enum. There are the facts as observed, the three
    things the consumer did or did not do, the outcome with its reason, and
    what it cost. The funnel is derived at report time from these, and any
    two of the facts can be true at once -- present AND departing, usable AND
    unacceptable -- without one overwriting the other.
    """
    # [COUNT_THE_WORK_NOT_DONE_V1] `would_have` is what the BASELINE policy
    # would have done with this same candidate, recorded beside what this
    # policy actually did. Without it a saving can only be inferred by
    # differencing two arms, which compares two runs; with it, one run says
    # how much of what it was offered it declined to take, and the figure is
    # immune to seed, order and arm-to-arm drift.
    #
    # None means no baseline was supplied -- not False. A candidate nobody
    # asked a counterfactual about has not been shown to be a saving.
    d = e.as_dict()
    d.update(would_have=would_have,
             selected=selected, materialised=materialised,
             incorporated=incorporated, outcome=outcome, reason=reason,
             bytes=bytes_moved, materialise_ms=round(materialise_ms, 4),
             observed_at=round(time.time(), 6))
    return d


def funnel(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The four-transition view, DERIVED from the records rather than stored.

    `acceptable` is counted among candidates that were present, and so on --
    the funnel is an ordering imposed for reading, and the record does not
    depend on that ordering being the true one.
    """
    out = {
        "present": sum(1 for x in events if x.get("present")),
        "acceptable": sum(1 for x in events if x.get("acceptable")),
        "materialised": sum(1 for x in events if x.get("materialised")),
        "incorporated": sum(1 for x in events if x.get("incorporated")),
        # [ONE_FIELD_MEANS_ONE_THING_V1] Named exactly as analyze.funnel
        # names them. They were `rejected_before_materialisation` and
        # `materialised_not_incorporated` here and `avoided` and `wasted`
        # there -- one quantity, two names, in two modules that a reader
        # moves between. The same shape as `finished` against `completed`.
        # [AVOIDED_MEANS_NOT_FETCHED_V1] Any candidate that cost no
        # bytes, whatever the reason. This counted only the UNACCEPTABLE
        # ones, so a policy that declines a candidate it could have taken --
        # which is the entire point of choosing -- reported avoiding
        # nothing. The quantity is "work not done", not "work the acceptance
        # window forbade".
        "avoided": sum(1 for x in events
                       if not x.get("materialised")),
        # [COUNT_THE_WORK_NOT_DONE_V1] Of the candidates the baseline would
        # have consumed, how many this policy did not -- and what fraction.
        "would_have_taken": sum(1 for x in events if x.get("would_have")),
        "avoided_vs_baseline": sum(
            1 for x in events
            if x.get("would_have") and not x.get("materialised")),
        "wasted": sum(1 for x in events
                      if x.get("materialised")
                      and not x.get("incorporated")),
        "bytes_moved": sum(x.get("bytes") or 0 for x in events),
        "materialise_ms": round(sum(x.get("materialise_ms") or 0.0
                                    for x in events), 4),
    }
    # [COUNT_THE_WORK_NOT_DONE_V1] The saving as a fraction of what the
    # baseline would have consumed. None when no counterfactual was
    # supplied -- a policy nobody scored against a baseline has not been
    # shown to save anything, and 0.0 would claim it was measured.
    out["avoided_pct_of_baseline"] = (
        round(100.0 * out["avoided_vs_baseline"] / out["would_have_taken"], 1)
        if out["would_have_taken"] else None)
    return out


class Observation:
    """[BOOKKEEPING_BELONGS_TO_OBSERVATION_V1] One look at the space, and
    everything that follows from having looked.

    A policy decides. It does not also owe the accounting, and the reason
    this class exists is that five separate obligations were silently
    inherited and dropped when `reduce()` was demoted:

        the group's counters      participation and drift printed as 0.00
        the presence sweep        every departure report said "none"
        _last_observed            restoring the sweep changed nothing,
                                  because it had no peers to miss
        _last_accepted            the second departure column stayed empty
        the lag set               measured over declined candidates, giving
                                  a mean of 59 and a max of 500 against a
                                  window of 64

    None of those announced itself. Four were invisible in the studies' own
    output. So the rule "a policy replaces the decision, not the
    bookkeeping" is no longer a convention a policy author has to remember:
    observing does the observing bookkeeping here, and a policy reports what
    it decided through `took`, `declined`, `gone` and `unreachable` -- which
    is the only way to update anything.

    A policy that forgets to report simply records nothing, which shows up
    as zero candidates rather than as plausible wrong numbers.
    """

    __slots__ = ("group", "step", "mine", "candidates", "events", "_taken",
                 "_taken_from")

    def __init__(self, group: ReduceGroup, step: Optional[int] = None,
                 contributions=None):
        self.group = group
        self.step = step
        self.mine = group.generation(step)
        cs = group.contributions() if contributions is None else contributions
        # Everything below is owed by LOOKING, not by deciding.
        for c in cs:
            if c.worker != group.worker and c.generation is not None:
                group._last_observed[c.worker] = max(
                    group._last_observed.get(c.worker, c.generation),
                    c.generation)
        group._sweep_presence(cs, self.mine)
        peers = [c for c in cs if c.worker != group.worker]
        group.stats["reduced"] += 1
        group.stats["peers_possible"] += len(peers)
        group.stats["accepted"] += 1          # the caller's own contribution
        self.candidates = [Eligibility(c, self.mine, group.accept,
                                       PRESENCE_STALE_S, group=group,
                                       round_no=step) for c in peers]
        self.events: List[Dict[str, Any]] = []
        self._taken = 0
        self._taken_from: List[str] = []

    def took(self, e: "Eligibility", materialise_ms: float = 0.0,
             would_have: Optional[bool] = None):
        """This candidate was materialised and incorporated."""
        st = self.group.stats
        st["accepted"] += 1
        st["accepted_peers"] += 1
        self._taken += 1
        self._taken_from.append(e.worker)
        c = e.contribution
        if c.generation is not None:
            self.group._last_accepted[e.worker] = c.generation
        # [STALENESS_IS_A_DISTANCE_NOT_A_DIRECTION_V1] over what was USED.
        if e.lag is not None:
            st["lag_sum"] += abs(e.lag)
            st["lag_n"] += 1
            st["lag_max"] = max(st["lag_max"], abs(e.lag))
            if e.lag > 0:
                st["behind"] += 1
            elif e.lag < 0:
                st["ahead"] += 1
        self.events.append(record(e, selected=True, materialised=True,
                                  incorporated=True, outcome="incorporated",
                                  bytes_moved=c.wire_bytes or 0,
                                  materialise_ms=materialise_ms,
                                  would_have=would_have))

    def declined(self, e: "Eligibility", reason: str = "policy declined",
                 would_have: Optional[bool] = None):
        """The consumer chose not to take it. Not a departure, not a fault."""
        key = ("rejected_missing" if not e.usable else "rejected_stale")
        self.group.stats[key] += 1
        self.events.append(record(e, selected=False, materialised=False,
                                  incorporated=False, outcome="rejected",
                                  reason=reason, would_have=would_have))

    def gone(self, e: "Eligibility", reason: str = "",
             materialise_ms: float = 0.0):
        """Selected, then superseded before the bytes arrived. The peer
        answered correctly; this is not unreachable."""
        self.group.stats["superseded"] += 1
        self.events.append(record(e, selected=True, materialised=False,
                                  incorporated=False, outcome="superseded",
                                  reason=reason,
                                  materialise_ms=materialise_ms))

    def unreachable(self, e: "Eligibility", reason: str = "",
                    materialise_ms: float = 0.0):
        """The peer's data plane could not be reached."""
        self.group.stats["unreachable"] += 1
        self.events.append(record(e, selected=True, materialised=False,
                                  incorporated=False, outcome="unreachable",
                                  reason=reason,
                                  materialise_ms=materialise_ms))

    def incorporated(self, effect: float) -> None:
        """[PROVENANCE_BEFORE_CLEVERNESS_V1] What the round DID, recorded
        against every producer that was part of it.

        `effect` is how far this worker's own state moved as a result of the
        incorporation -- a number the caller already has and which costs
        nothing extra. It is attributed to the ROUND, not to any one
        producer, because a mean over several cannot be decomposed. A policy
        that later wants per-producer consequence has to earn it with a
        mechanism that can actually measure it.
        """
        hist = getattr(self.group, "_incorporation_history", None)
        if hist is None:
            hist = {}
            self.group._incorporation_history = hist
        for w in self._taken_from:
            hist[w] = {"round": self.step, "effect": float(effect),
                       "n_in_round": len(self._taken_from)}

    def report(self) -> Dict[str, Any]:
        return {"events": self.events, "n_used": self._taken + 1,
                "used": [x for x in self.events if x["incorporated"]],
                "skipped": [x for x in self.events if not x["incorporated"]],
                "funnel": funnel(self.events)}


def observe(group: ReduceGroup, step: Optional[int] = None,
            contributions=None) -> Observation:
    """Look once. Returns the candidates and owns everything else."""
    return Observation(group, step, contributions)


def mean_of_current(group: ReduceGroup, tensor, step: Optional[int] = None,
                    choose: Optional[Callable] = None,
                    require_peers: bool = False):
    """The compatibility policy: publish, take every acceptable peer's
    current state, average them with mine.

    [BOOKKEEPING_BELONGS_TO_OBSERVATION_V1] Rewritten as a policy that only
    DECIDES. It reads candidates, chooses, and reports each outcome; every
    counter, the presence sweep and the observation records are the
    Observation's. What remains here is the decision and nothing else, which
    is what a Phase II policy should have to write.

    [NO_PEERS_IS_A_DECISION_NOT_A_DETAIL_V1]
        require_peers=True   the COLLECTIVE contract: a step without peers
                             is a failure. STRICTER than reduce(), which
                             raised only when nothing at all was kept -- and
                             a caller passing my_tensor always has its own
                             contribution, so reduce() never failed for want
                             of peers. It returned the mean of one.
        require_peers=False  what reduce() actually did, and the faithful
                             setting for routing an existing study here.
    """
    import torch
    group.publish(tensor, step=step)
    obs = observe(group, step=step)
    cands = obs.candidates
    if choose is not None:
        cands = [e for e in cands if choose(e)]
        for e in obs.candidates:
            if e not in cands:
                obs.declined(e, "not chosen by this policy")

    taken = [tensor]
    for e in cands:
        if not e.acceptable:
            obs.declined(e)
            continue
        if not e.usable:
            obs.declined(e, "no payload to fetch")
            continue
        t0 = time.perf_counter()
        try:
            t = e.contribution.tensor()
        except StateGone as exc:
            obs.gone(e, getattr(exc, "reason", ""),
                     (time.perf_counter() - t0) * 1000)
            continue
        except (ConnectionError, TimeoutError, OSError) as exc:
            obs.unreachable(e, str(exc)[:120],
                            (time.perf_counter() - t0) * 1000)
            continue
        taken.append(t)
        obs.took(e, (time.perf_counter() - t0) * 1000)

    if require_peers and len(taken) == 1:
        raise RuntimeError(
            "reduce(%s) has nothing to reduce at generation %s: %d "
            "candidate(s) observed, none acceptable and usable."
            % (group.name, group.generation(step), len(obs.candidates)))
    report = obs.report()
    out = torch.stack(taken).mean(0)
    diag("DIAG-POLICY", "mean_of_current", worker=group.worker,
         name=group.name, n_used=report["n_used"], **report["funnel"])
    return out, report


def keep_computing_if_nothing_new(group: ReduceGroup, tensor,
                                  step: Optional[int] = None):
    """A policy `reduce()` cannot express: offer my state, look, and if
    nothing acceptable exists, return my own tensor unchanged rather than
    failing.

    Included because it is the smallest thing that demonstrates the seam --
    the mechanism below is identical, and only the decision above differs.
    """
    group.publish(tensor, step=step)
    cands = [e for e in assess(group, step=step) if e.acceptable and e.usable]
    if not cands:
        return tensor, {"events": [], "used": [], "skipped": [],
                        "n_used": 1, "continued_locally": True,
                        "funnel": funnel([])}
    out, report = mean_of_current(group, tensor, step=step)
    report["continued_locally"] = False
    return out, report
