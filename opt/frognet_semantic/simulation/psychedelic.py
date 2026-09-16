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
simulation/psychedelic.py -- the worker that stops imitating a collective.

[THE_APPLICATION_CONSUMES_STATE_IT_DOES_NOT_SYNCHRONISE_V1]

Phase I's worker was deliberately conservative: compute, publish, observe,
reconcile EVERY step, continue. That shape was inherited from the collective
it replaced, and it was kept on purpose so the substitution could be measured
without also changing the algorithm.

This drops it. The loop is:

    while training:
        local      = compute()
        publish(local)
        candidates = observe()              # descriptors; no bytes
        selected   = policy(me, candidates) # the whole of the decision
        for c in selected:
            incorporate(local, materialise(c))

`policy` returns SELECTIONS, not communication instructions. Nothing here
requires that every worker contribute to every logical step, that useful
state be the same age, or that the choice between parameter and gradient
reconciliation be global.

THE ARMS

  every_peer   The intentionally stupid policy: take every acceptable peer,
               every step. This is Phase I's behaviour expressed in the
               native loop, and it exists so the architecture change can be
               measured with the mathematics held still.

  fresh_only   The first deleted assumption. A peer whose generation has not
               moved since this worker last incorporated from it is not
               consulted at all. Not "fetched and found identical" -- the
               SAME reply already avoids the bytes -- but never selected, so
               no descriptor is acted on and no round trip is made.

               This is the smallest honest attack on "reconcile every step",
               and it needs nothing that is not already in the descriptor:
               the producer's generation, and what this worker last took
               from it. No sketch, no lineage, no new mechanism.

WHAT IS MEASURED

Time to quality, and the lifecycle in full: offered, observed, selected,
materialised, incorporated, avoided, wasted, bytes. `avoided` is the
quantity a selective policy exists to move, and it counts any candidate that
cost no bytes -- including one the policy could have taken and chose not to.
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)
for _p in (_TREE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TARGET = None
DIM = 4


# --------------------------------------------------------------------------
# policies -- the whole of Phase II lives here
# --------------------------------------------------------------------------

def solo(me, candidates):
    """[A_STUDY_WITHOUT_A_CONTROL_MEASURES_ITS_OWN_KNOBS_V1] Take nothing.

    The control this study was missing. Without it, "publishing less
    improves the loss" reads as a result of the publication policy, when the
    arm that publishes least is simply the arm closest to not communicating
    at all. If solo is the best row, the fixture is telling us reconciliation
    HURTS at this scale -- which is a fact about the workload, not about any
    policy, and every other row must be read against it.
    """
    return []


def every_peer(me, candidates):
    """Intentionally stupid: every acceptable peer, every step.

    Phase I's behaviour, written as a selection rather than a collective. It
    is the control: if the native loop changes the result on its own, this
    arm shows it before any assumption is deleted.
    """
    return [c for c in candidates if c.acceptable and c.usable]


def fresh_only(me, candidates):
    """[THE_APPLICATION_CONSUMES_STATE_IT_DOES_NOT_SYNCHRONISE_V1]
    Skip a peer that has not moved since I last took from it.

    The first inherited assumption deleted. `me.last_accepted` is what this
    worker last incorporated from each producer; a candidate at the same
    generation carries nothing this worker does not already hold, so it is
    not selected -- no request, no round trip, no descriptor acted on.

    This is not SAME suppression. SAME avoids the BYTES after asking. This
    avoids the asking, using only facts already in the descriptor.
    """
    out = []
    for c in candidates:
        if not (c.acceptable and c.usable):
            continue
        gen = c.contribution.generation
        if gen is not None and me.last_accepted.get(c.worker) == gen:
            continue                       # nothing new from this producer
        out.append(c)
    return out


def by_participation(threshold=1.5):
    """[A_WORKER_KNOWS_ITS_OWN_PARTICIPATION_V1] Choose the state KIND that
    degrades gracefully, from a fact the worker already has.

    The drift sweep found this. Across 36 cells and 3 repetitions:

        gradient reconciliation produced a model worse than its own worst
        worker in three cells, all at 2000 steps, at participation 0.40,
        0.54, 0.58 and one at 1.69 -- against 3.00 possible

        parameter reconciliation never did it, in any cell, including ones
        where its participation was as low as 0.47

        at HIGH participation, gradient reconciliation was the better arm by
        4-5x: 0.034 against 0.151 for parameters, with solo at 0.166

    The cleanest instance was 2000/d0/grad/lag8 rep 3: every worker reached
    0.021-0.039 individually, better than anything else in the sweep, and
    their average came out at 0.175 -- worse than solo. Four workers each in
    an excellent but DIFFERENT basin, and the mean of basins is not a basin.
    The same cell with participation 3.00 instead of 0.58 gave the best
    result in the sweep.

    So the two kinds differ in VARIANCE, not just in mean: gradients are
    better and occasionally catastrophic, parameters are worse and never
    are. Choosing between them is choosing a risk profile, and the worker
    can see which regime it is in -- `accepted_peers / peers_possible` is
    already counted, needs no new mechanism, and costs nothing to read.

    Below the threshold this consumes parameter state; above it, gradient
    state. `threshold` defaults to 1.5 peers because that is between the
    highest pathological observation (1.69, an outlier) and the lowest
    healthy one (2.72) -- and it is a knob, so an arm that moves it is
    measuring the knob unless the control moves with it.
    """
    def choose(me, candidates):
        usable = [c for c in candidates if c.acceptable and c.usable]
        me.prefer_parameters = (me.recent_participation is not None
                                and me.recent_participation < threshold)
        return usable
    choose.__name__ = "by_participation_%g" % threshold
    return choose


def every_k(k):
    """[IS_THE_FACT_WORTH_ANYTHING_V1] Consume on every kth round, blind.

    The control for `fresh_only`. That policy avoids consumption by KNOWING
    a producer has not moved -- a fact that exists only because the state is
    observable without asking for it. This one avoids the same work by
    counting rounds, which needs no shared state at all and could be written
    against a collective.

    If the two match at equal volume, freshness carries no information and
    the RAM-native fact bought nothing over a timer. If content-based wins,
    it is the knowing that helped, not the doing-less.
    """
    def choose(me, candidates):
        if me.step % k:
            return []
        return [c for c in candidates if c.acceptable and c.usable]
    choose.__name__ = "every_%d" % k
    return choose


POLICIES = {"solo": solo, "every_peer": every_peer,
            "fresh_only": fresh_only,
            "by_participation": by_participation(1.5),
            "every_2": every_k(2), "every_3": every_k(3),
            "every_4": every_k(4), "every_5": every_k(5)}

#: [COUNT_THE_WORK_NOT_DONE_V1] The counterfactual every arm is scored
#: against: what the inherited loop would have consumed. Named once, here,
#: so no arm can quietly be measured against a different baseline.
BASELINE = every_peer


# --------------------------------------------------------------------------
# publication policies -- the same question on the producer side
# --------------------------------------------------------------------------

def publish_always(state, last_published, eps):
    """Every step, unconditionally. Inherited from the collective, where
    every worker had to contribute to every operation."""
    return True


def publish_when_moved(state, last_published, eps):
    """[A_PRODUCER_OFFERS_STATE_IT_DOES_NOT_REPORT_FOR_DUTY_V1]
    Offer only what is new.

    A producer whose parameters have barely moved since it last published is
    offering the same state again. In the collective it had no choice: an
    absent rank stalls the operation. Here nobody is waiting, so it can
    simply not publish, and the cell keeps saying what is still true.

    The decision uses ONLY the producer's own two vectors -- what it holds
    and what it last offered. No consumer, no peer, no sketch, no distance
    to anyone else. That is what makes it available now: it needs nothing
    the drift sweep is investigating.

    Note what this does NOT save. The reader still finds a current cell and
    still may fetch it; what disappears is the store WRITE and the
    republication of a state nobody could distinguish from the last one.
    Control-plane writes are the cost plane_separation measures as constant
    per node -- this makes some of them not happen at all.
    """
    if last_published is None:
        return True
    import torch
    denom = float(last_published.norm()) or 1.0
    return float((state - last_published).norm()) / denom > eps


PUBLISH = {"always": publish_always, "when_moved": publish_when_moved}


# --------------------------------------------------------------------------

class Me:
    """What a policy knows about the worker it is deciding for."""
    __slots__ = ("worker", "step", "generation", "last_accepted",
                 "last_observed", "incorporation_history",
                 "recent_participation", "prefer_parameters")

    def __init__(self, group, step):
        self.worker = group.worker
        self.step = step
        self.generation = group.generation(step)
        self.last_accepted = group._last_accepted
        self.last_observed = group._last_observed
        # [PROVENANCE_BEFORE_CLEVERNESS_V1] per producer: the round it was
        # last incorporated in, and what that ROUND moved.
        self.incorporation_history = group._incorporation_history
        # [A_WORKER_KNOWS_ITS_OWN_PARTICIPATION_V1] Peers accepted per
        # reduction so far. Already counted; nothing new is measured to get
        # it. None before the first reduction -- not zero, which would read
        # as "nobody participated" rather than "not yet known".
        st = group.stats
        self.recent_participation = (
            (st["accepted_peers"] / st["reduced"]) if st["reduced"] else None)
        self.prefer_parameters = False


def run_arm(policy_name, world, steps, lr, lag, seed_target,
            publish_name="always", eps=0.0, state_kind="wts",
            threshold=1.5):
    """One arm, N workers as threads over a shared list_store."""
    import torch
    from agent_workload.tuplespace import list_store, policy as P
    from agent_workload.tuplespace.reduce_by_read import ReduceGroup
    from agent_workload.tuplespace.tensor_plane import StateGone

    choose = POLICIES[policy_name]
    offer = PUBLISH[publish_name]
    results, stats, t0 = {}, {}, time.time()
    published = {"n": 0, "skipped": 0}
    last_pub = {}
    last_kind, switches = {}, []

    with list_store.installed(my_ip="10.90.0.1"):
        accept = (lambda mine, theirs, age_s:
                  theirs is not None and mine is not None
                  and abs(mine - theirs) <= lag)
        # [NEVER_TWO_THINGS_IN_ONE_CELL_V1] Parameters and gradients are
        # different states, so they are different cells. Not one cell with a
        # kind field: a worker that switched what it published used to
        # change what its cell MEANT, and peers still reading it averaged
        # their parameters with its gradients -- dimensionally identical,
        # semantically nonsense, silently accepted. Four switches in the
        # first four steps of a 1500-step run cost 0.34 -> 0.50.
        #
        # With two names there is nothing to detect and nothing to refuse.
        # A consumer reading `params` cannot receive gradients because
        # gradients were never in that cell. Switching is a change of which
        # cell this worker writes and reads; the one it stops writing simply
        # goes stale, which presence already handles.
        gs, ms, ops, gens = {}, {}, {}, {}
        for r in range(world):
            w = "w%d" % r
            gs[w] = {"wts": ReduceGroup("params", w, accept=accept),
                     "grad": ReduceGroup("grads", w, accept=accept)}
            torch.manual_seed(17)
            ms[w] = torch.nn.Sequential(
                torch.nn.Linear(DIM, 32), torch.nn.Tanh(),
                torch.nn.Linear(32, 32), torch.nn.Tanh(),
                torch.nn.Linear(32, 1))
            ops[w] = torch.optim.SGD(ms[w].parameters(), lr=lr)
            gens[w] = torch.Generator().manual_seed(1000 + r)

        def flat(m):
            return torch.cat([p.detach().flatten() for p in m.parameters()])

        def unflat(m, v):
            i = 0
            with torch.no_grad():
                for p in m.parameters():
                    k = p.numel()
                    p.copy_(v[i:i + k].reshape(p.shape))
                    i += k

        batches = [16, 32, 48, 64]
        # [PRODUCERS_MUST_ADVANCE_AT_DIFFERENT_RATES_V1] Worker r publishes
        # once every (r+1) rounds. Without this every worker advances every
        # round, so no producer is ever UNMOVED when a consumer looks and
        # `fresh_only` can never fire -- the first run of this study showed
        # exactly that, with both arms identical and zero avoided. A policy
        # that skips unmoved producers needs producers that can be unmoved.
        publish_every = [r + 1 for r in range(world)]
        for step in range(steps):
            for r in range(world):
                w = "w%d" % r
                if step % publish_every[r]:
                    continue          # this worker is still computing
                # --- compute -------------------------------------------
                x = torch.randn(batches[r % 4], DIM, generator=gens[w])
                y = x @ seed_target
                y = torch.sin(y) + 0.3 * y
                loss = ((ms[w](x).squeeze() - y) ** 2).mean()
                ops[w].zero_grad()
                loss.backward()

                # [THE_ACTUATOR_IS_A_SEPARATE_VARIABLE_V1] Which state this
                # worker offers and consumes THIS round. Fixed for the
                # control arms; chosen per round by the adaptive one. The
                # decision (participation says conditions changed) and the
                # response (reconcile a different quantity) are two
                # variables, so the fixed arms exist to hold the second one
                # still while only the adaptive arm moves it.
                # The cell this round belongs to is chosen before anything
                # is written, so a worker only ever touches one of them.
                me = Me(gs[w]["wts"], step)
                kind = state_kind
                if state_kind == "adaptive":
                    kind = ("wts" if (me.recent_participation is not None
                                      and me.recent_participation < threshold)
                            else "grad")
                    prev = last_kind.get(w)
                    if prev is not None and prev != kind:
                        # The switch itself is an event, with the fact that
                        # caused it and the step it happened on -- causal
                        # chronology, not just an endpoint.
                        switches.append({
                            "kind": "state_kind_switch", "worker": w,
                            "step": step,
                            "participation_at_decision":
                                me.recent_participation,
                            "from": prev, "to": kind,
                            "threshold": threshold})
                    last_kind[w] = kind
                g = gs[w][kind]
                me = Me(g, step)

                if kind == "grad":
                    local = torch.cat([
                        (p.grad if p.grad is not None
                         else torch.zeros_like(p)).flatten()
                        for p in ms[w].parameters()])
                else:
                    ops[w].step()
                    local = flat(ms[w])

                # --- publish (or decline to) ---------------------------
                if offer(local, last_pub.get(w), eps):
                    g.publish(local, step=step)
                    last_pub[w] = local.clone()
                    published["n"] += 1
                else:
                    published["skipped"] += 1

                # --- observe -------------------------------------------
                obs = P.observe(g, step=step)

                # --- decide --------------------------------------------
                selected = choose(me, obs.candidates)
                sel = set(id(e) for e in selected)
                # [COUNT_THE_WORK_NOT_DONE_V1] What the BASELINE would have
                # done with each of these same candidates, recorded beside
                # what this policy did. One run then reports its own saving
                # instead of it being differenced out of two.
                base = set(id(e) for e in BASELINE(me, obs.candidates))
                for e in obs.candidates:
                    if id(e) not in sel:
                        obs.declined(e, "not selected by %s" % policy_name,
                                     would_have=(id(e) in base))

                # --- materialise + incorporate -------------------------
                taken = [local]
                for e in selected:
                    t1 = time.perf_counter()
                    try:
                        taken.append(e.contribution.tensor())
                    except StateGone as exc:
                        obs.gone(e, getattr(exc, "reason", ""),
                                 (time.perf_counter() - t1) * 1000)
                        continue
                    except (ConnectionError, TimeoutError, OSError) as exc:
                        obs.unreachable(e, str(exc)[:120],
                                        (time.perf_counter() - t1) * 1000)
                        continue
                    obs.took(e, (time.perf_counter() - t1) * 1000,
                             would_have=(id(e) in base))
                if len(taken) > 1:
                    before = local
                    merged = torch.stack(taken).mean(0)
                    if kind == "grad":
                        i = 0
                        for pm in ms[w].parameters():
                            k = pm.numel()
                            pm.grad = merged[i:i + k].reshape(pm.shape).clone()
                            i += k
                        ops[w].step()
                    else:
                        unflat(ms[w], merged)
                    # [PROVENANCE_BEFORE_CLEVERNESS_V1] What the round did,
                    # stamped on every producer that was part of it. Free --
                    # both vectors are already in hand.
                    obs.incorporated(float((merged - before).norm()))

                elif kind == "grad":
                    # Nothing acceptable to mix in. The worker still applies
                    # its OWN gradient -- declining to consume is not
                    # declining to learn.
                    ops[w].step()

                st = stats.setdefault(w, {"events": []})
                st["events"].extend(obs.events)

        for r in range(world):
            w = "w%d" % r
            results[w] = flat(ms[w])
        # merge both cells' counters so participation reporting is unchanged
        for w in list(gs):
            gs[w] = gs[w]["wts"]
    return results, stats, time.time() - t0, published, switches


def main(argv=None):
    import torch
    from agent_workload.tuplespace import policy as P
    global TARGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", type=int, default=4)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--lr", type=float, default=0.1)
    ap.add_argument("--lag", type=int, default=64)
    ap.add_argument("--arms", default="every_peer,fresh_only",
                    help="consume[:publish[:eps[:kind]]]  kind = wts | "
                         "grad | adaptive")
    ap.add_argument("--threshold", type=float, default=1.5,
                    help="peers-per-reduction below which the adaptive arm "
                         "prefers parameter state")
    ap.add_argument("--eps", type=float, default=0.002,
                    help="relative move a producer must have made since it "
                         "last published before it publishes again")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    TARGET = torch.tensor([2.0, -3.0, 0.5, 1.0])
    g = torch.Generator().manual_seed(4242)
    xv = torch.randn(4096, DIM, generator=g)
    yv = xv @ TARGET
    yv = torch.sin(yv) + 0.3 * yv

    def loss_of(vec):
        torch.manual_seed(17)
        m = torch.nn.Sequential(torch.nn.Linear(DIM, 32), torch.nn.Tanh(),
                                torch.nn.Linear(32, 32), torch.nn.Tanh(),
                                torch.nn.Linear(32, 1))
        i = 0
        with torch.no_grad():
            for p in m.parameters():
                k = p.numel()
                p.copy_(vec[i:i + k].reshape(p.shape))
                i += k
            return float(((m(xv).squeeze() - yv) ** 2).mean())

    print("=" * 78)
    print("PSYCHEDELIC FROGTORCH -- the worker stops imitating a collective")
    print("=" * 78)
    print("  %d workers, %d steps, lr=%s, acceptance window %d generations"
          % (a.world, a.steps, a.lr, a.lag))
    print("  loop: compute -> publish -> observe -> DECIDE -> materialise")
    print("  every_peer : take every acceptable peer, every step (Phase I,")
    print("               written as a selection). The control.")
    print("  fresh_only : skip a producer that has not moved since I last")
    print("               took from it. No request, no round trip -- this is")
    print("               not SAME suppression, which avoids the bytes AFTER")
    print("               asking.")
    print()
    # [THE_FIRST_ARM_PAYS_THE_WARM_UP_V1] There is no wall column. Whichever
    # arm ran first paid torch import, allocator warm-up and store setup:
    # the same two arms measured 9.5s/1.1s in one order and 1.9s/1.0s in the
    # other, with identical results. A column that reports arm order as if
    # it were policy cost is worse than no column.
    print("  %-20s %10s %10s %9s %8s %8s %9s"
          % ("consume/kind", "loss best", "loss worst", "loss(mn)",
             "incorp", "switches", "averaging"))

    rows = []
    for spec in [x.strip() for x in a.arms.split(",") if x.strip()]:
        parts = spec.split(":")
        name = parts[0]
        pub = parts[1] if len(parts) > 1 else "always"
        eps = float(parts[2]) if len(parts) > 2 else a.eps
        kind = parts[3] if len(parts) > 3 else "wts"
        res, stats, wall, published, switches = run_arm(
            name, a.world, a.steps, a.lr, a.lag, TARGET, pub, eps, kind,
            a.threshold)
        ev = [e for st in stats.values() for e in st["events"]]
        f = P.funnel(ev)
        ws = [res["w%d" % r] for r in range(a.world)]
        ls = [loss_of(w) for w in ws]
        mean = torch.stack(ws).mean(0)
        row = {"policy": name, "publish": pub, "eps": eps, "kind": kind,
               "switches": switches, "n_switches": len(switches),
               "loss_best": min(ls), "loss_worst": max(ls),
               "loss_of_mean": loss_of(mean), "wall_s": wall,
               "published": published["n"],
               "publishes_skipped": published["skipped"], "funnel": f}
        rows.append(row)
        label = "%s/%s" % ("all" if name == "every_peer" else name, kind)
        print("  %-20s %10.5f %10.5f %9.5f %8d %8d %9s"
              % (label, row["loss_best"], row["loss_worst"],
                 row["loss_of_mean"], f["incorporated"], len(switches),
                 "WORSE" if row["loss_of_mean"] > row["loss_worst"]
                 else "ok"))

    print()
    print("  `publish` is store WRITES actually made -- a producer that has")
    print("  barely moved since it last published is offering the same state")
    print("  again, and in a collective it had no choice. `offered` is")
    print("  candidates observed; `avoided` is candidates that")
    print("  cost no bytes, INCLUDING ones the policy could have taken and")
    print("  chose not to. A selective policy exists to move that number")
    print("  without moving loss.")
    print("=" * 78)
    if a.json:
        with open(a.json, "w") as f_:
            json.dump(rows, f_, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
