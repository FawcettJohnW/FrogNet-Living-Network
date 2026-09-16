#!/usr/bin/env python3
"""gate.py -- the primary says go, everyone else waits for it.

    gate.py --role primary  --runid mesh34 --arm 0 --store host:port --tree T
    gate.py --role follower --runid mesh34 --arm 0 --store host:port --tree T
    gate.py --role barrier  --runid ddp800 --arm 0 --rank R --world W \
            --store host:port --tree T

The primary writes one cell and returns. A follower blocks until that cell
exists, using the store's own blocking read -- one request held open, not a
poll -- and returns when it appears.

A barrier is symmetric: every rank writes its OWN cell and then waits for
all W of them. There is no primary, so no rank can start before the rest
have arrived, and a rank that never arrives is named by the ones that did.

WHY THIS AND NOT A KEYPRESS
Four terminals pressed by hand start seconds apart at best, and the barrier
then waits for the slowest: a measured campaign spent 116 seconds in its
first barrier for no reason but launch skew. It also means a run cannot be
left alone.

WHY A TUPLE AND NOT A BROADCAST
The same reason the collectives use one. The primary publishes a fact -- arm
N is starting -- and whoever needs it reads it. It does not need to know who
is listening, they do not need to be listening yet when it writes, and a
follower that arrives late still finds it. That is the whole model, applied
to the one piece of the harness that was still doing it the other way.

WHY BARRIER EXISTS ALONGSIDE PRIMARY/FOLLOWER
primary/follower is a START signal: rank 0 decides, the rest follow. That is
right for netbench1's arms, which are short and begin together.

The DDP arms run over an hour and finish far apart, so by the next arm the
ranks are minutes apart and "rank 0 is ready" says nothing about the other
three. Under primary/follower the absent rank was discovered by PyTorch's
own rendezvous timing out after 1800s with "3/4 clients joined" -- half an
hour to learn something that was knowable immediately.

So: every rank publishes its arrival and reads for W arrivals. The store's
blocking read does the waiting, min_rows=W is the condition, and when the
wait runs out the rows that ARE present say exactly which rank is missing.
"""
import argparse
import os
import sys
import time

#: [HOLD_INSIDE_THE_RPC_BUDGET_V1] Seconds to hold one blocking read open.
#: Must stay under the proxy's RPC budget in front of the store (15 s), or
#: the proxy kills the read and reports it as a store failure.
GATE_HOLD_S = float(os.environ.get("FROGNET_GATE_HOLD_S", "10"))


def _arrivals(rows, var):
    """Ranks that have published under this arm. Reads the rank out of the
    value, falling back to the scope suffix -- the scope is what makes each
    rank's cell distinct, so it always carries the number even if a value
    was written by an older build."""
    seen = set()
    for r in rows:
        if r.get("var") != var:
            continue
        v = r.get("value") or {}
        n = v.get("rank")
        if n is None:
            scope = str(r.get("scope") or r.get("name") or "")
            if ".r" in scope:
                tail = scope.rsplit(".r", 1)[1]
                if tail.isdigit():
                    n = int(tail)
        if n is not None:
            seen.add(int(n))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True,
                    choices=["primary", "follower", "barrier"])
    ap.add_argument("--runid", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--pypath", default="/etc/frognet_bundles/communicator")
    ap.add_argument("--wait", type=float, default=1800.0,
                    help="how long a follower waits before giving up")
    ap.add_argument("--rank", type=int, default=None,
                    help="this rank's number (barrier only)")
    ap.add_argument("--world", type=int, default=None,
                    help="how many ranks must arrive (barrier only)")
    a = ap.parse_args()

    if a.role == "barrier" and (a.rank is None or a.world is None):
        ap.error("--role barrier needs --rank and --world")

    for p in ([a.pypath] if os.path.isdir(a.pypath) else []) + [a.tree]:
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("FROGNET_DIAG", "0")
    os.environ.setdefault("FROGNET_SENTINEL_DIR", "/tmp/netbench1.sentinel")
    os.makedirs("/tmp/netbench1.sentinel", exist_ok=True)
    from core import frognet_tuples as T

    service = "nb1.%s.gate" % a.runid
    var = "arm%s" % a.arm

    if a.role == "primary":
        # [A_GATE_THAT_DID_NOT_OPEN_MUST_NOT_SAY_IT_DID_V1]
        #
        # This ignored the result of the put and printed "open" regardless.
        # When the write failed -- a duplicate against a gate cell left by an
        # earlier campaign that reused this run id -- the followers were
        # released anyway, by the OLD cell, and started before this rank was
        # ready. The one thing the gate exists to prevent.
        #
        # A failed write is a failed gate. Say so and stop.
        ok = T.put(service, var, var, {"t": time.time()},
                   dbhost=a.store, own=False)
        if ok is False:
            print("gate: could not publish %s/%s -- the store refused the "
                  "write, so this arm was never opened. Followers may be "
                  "released by a cell from an earlier run with this id: use "
                  "a run id you have not used before." % (service, var),
                  file=sys.stderr)
            return 2
        print("  gate: arm %s open" % a.arm)
        return 0

    if a.role == "barrier":
        # Publish this rank's arrival under its OWN scope, so W ranks make W
        # rows. Same failure rule as the primary: a write that did not land
        # is a barrier this rank never entered, and continuing would let the
        # others proceed W-1 strong while this one runs anyway.
        scope = "%s.r%d" % (var, a.rank)
        ok = T.put(service, var, scope, {"rank": a.rank, "t": time.time()},
                   dbhost=a.store, own=False)
        if ok is False:
            print("gate: rank %d could not publish its arrival at %s/%s -- "
                  "the store refused the write. This rank never entered the "
                  "barrier; the others will report it missing. Use a run id "
                  "you have not used before." % (a.rank, service, scope),
                  file=sys.stderr)
            return 2

        # One held-open request per pass, bounded by the store, exactly as
        # the follower does. min_rows is W because W arrivals is the
        # condition -- the store returns as soon as they exist.
        # [HOLD_INSIDE_THE_RPC_BUDGET_V1] The per-request hold was 30s. The
        # proxy in front of the store gives an RPC 15s and then reports the
        # whole thing as a store failure:
        #
        #   StoreBroken ... HTTP 503: RAW RPC failed: TimeoutError(
        #     'RPC to 10.199.199.1 failed after 1 attempts (15.1s, budget 15.0s)')
        #
        # Measured directly: a plain read of the same store returns 200 in
        # 26 ms, and the identical URL with wait_s=30&min_rows=4 returns 503
        # at 15.2 s. The store was doing exactly what it was asked. The hold
        # simply outlived the budget of the thing carrying it.
        #
        # So hold for less than the budget. The outer --wait deadline is
        # unchanged, so the barrier still waits as long as it was told to;
        # it just asks again more often while it does.
        deadline = time.time() + a.wait
        seen = set()
        while time.time() < deadline:
            left = deadline - time.time()
            rows = T.get_all(service, dbhost=a.store,
                             wait_s=min(left, GATE_HOLD_S), min_rows=a.world)
            seen = _arrivals(rows, var)
            if len(seen) >= a.world:
                print("  gate: arm %s -- all %d ranks present" %
                      (a.arm, a.world))
                return 0
        missing = sorted(set(range(a.world)) - seen)
        print("gate: arm %s barrier timed out after %.0fs. present=%s "
              "missing=%s -- those ranks never reached this arm. Look at "
              "them, not here." % (a.arm, a.wait, sorted(seen), missing),
              file=sys.stderr)
        return 2

    # A follower asks once and lets the store hold the question open. The
    # loop exists because the store bounds its own wait, not because this is
    # polling: each pass is one request that was held for its full wait.
    deadline = time.time() + a.wait
    while time.time() < deadline:
        left = deadline - time.time()
        rows = T.get_all(service, dbhost=a.store,
                         wait_s=min(left, GATE_HOLD_S), min_rows=1)
        for r in rows:
            if r.get("var") != var:
                continue
            # [THE_RUN_ID_IS_THE_CAMPAIGN_V1]
            #
            # There was a freshness test here: ignore a cell written before
            # this process started, to defend against a run id reused from
            # an earlier campaign.
            #
            # It rejected valid gates. A follower's start time has nothing
            # to do with when the primary opened the arm -- the primary is
            # normally AHEAD -- so any node whose script started after rank
            # 0 published discarded the cell and asked again. With a row
            # present, min_rows was satisfied and the read returned at once,
            # so it span at a hundred requests a second against the store
            # for as long as it was left running.
            #
            # The run id already carries what that test was reaching for. A
            # campaign uses ids it has never used, runme.sh increments them,
            # and a cell under this id belongs to this campaign by
            # construction. Nothing further to check.
            print("  gate: arm %s open" % a.arm)
            return 0
    print("gate: arm %s never opened after %.0fs -- the primary did not "
          "reach it." % (a.arm, a.wait), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
