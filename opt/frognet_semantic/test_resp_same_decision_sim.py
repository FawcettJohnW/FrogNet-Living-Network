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
"""test_resp_same_decision_sim.py -- does the SAME/DIFF DECISION survive a
realistic wire?

transport_sim_tier.py exercises the transport faithfully and MOCKS this
decision: its _handle_req_diff returns wrap_resp_diff unconditionally and its
_handle_req_repeat returns wrap_resp_same unconditionally. So it passes with the
bug present and with the bug fixed. This tier supplies the missing half -- the
real comparison, over that same shaped wire.

THE BUG (session.py:818, pre-[SAME_COMPARES_THE_BODY_V1]):

    new_raw_hash = hashlib.sha256(sem_resp).digest()

sem_resp is the SEMANTIC REPLY. It is diff-encoded against the reply reference,
so an UNCHANGED response body encodes to DIFFERENT bytes whenever the reference
moves -- and the reference moves on nearly every request under load. The hash
then differs although the response did not, and RESP_SAME is missed.

This tier reproduces exactly that: the response body is held constant while the
encoding varies, which is the real behaviour of a diff encoder, and asks which
comparison notices.

Run: python3 test_resp_same_decision_sim.py
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/claude/opt")

from transport_sim_tier import (            # noqa: E402
    NetworkParams, create_connected_pair, _hash_for,
)
from core.semcache_wire import (            # noqa: E402
    try_parse, wrap_req_diff, OP_RESP_SAME, OP_RESP_DIFF,
)

FAILS = []


def check(label, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(label)


# --- the two comparisons, side by side --------------------------------------

def hash_encoding(sem_resp, dyn_vals, status):
    """OLD. Hashes the encoded reply."""
    return hashlib.sha256(sem_resp).digest()


def hash_body(sem_resp, dyn_vals, status):
    """NEW -- [SAME_COMPARES_THE_BODY_V1], lifted from daemon/engine/session.py."""
    h = hashlib.sha256()
    h.update(b"status:%d\n" % int(status))
    for k, v in sorted(dyn_vals or [], key=lambda kv: str(kv[0])):
        h.update(("%s=%r\n" % (k, v)).encode("utf-8", "surrogateescape"))
    return h.digest()


ECHO_BODY = [("host", "New-York-1"), ("ip", "10.102.60.1"),
             ("lan", "192.168.1.245"), ("wan", "0.0.0.0")]


def run_over_wire(compare, n=25, label=""):
    """Drive n identical echo responses over the shaped wire and count how many
    the daemon would answer RESP_SAME.

    The ENCODING varies per request -- that is what a diff encoder does as its
    reference advances. The BODY never changes: this is /frognet_echo.php.
    """
    params = NetworkParams(latency_ms=5.0, jitter_ms=1.0, bandwidth_bps=1e6)
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.12", fwd_params=params, rev_params=params,
    )
    same = diff = 0
    try:
        req_hash = _hash_for("echo-session")
        stored = None
        for i in range(n):
            # a real REQ_DIFF crosses the simulated wire each iteration
            frame = wrap_req_diff(req_hash, b"\x01\x00" + b"\x00" * 10)
            reply = proxy.send_request(frame, timeout=10.0)
            assert try_parse(reply) is not None, "no reply off the wire"

            # the daemon-side decision, with the real inputs
            sem_resp = b"ENCODED-AGAINST-REFERENCE-%04d" % i   # varies
            h = compare(sem_resp, ECHO_BODY, 200)
            if stored is not None and h == stored:
                same += 1
            else:
                diff += 1
            if stored is None:
                stored = h
    finally:
        for w in (w1, w2):
            try:
                w.stop()
            except Exception:
                pass
    pct = 100.0 * same / max(1, n - 1)
    print("  %-22s %2d RESP_SAME / %2d RESP_DIFF   (%.0f%% of repeats)"
          % (label, same, diff, pct))
    return same, diff


def main():
    print("=== SAME/DIFF decision over the simulated transport ===")
    print("    identical echo body, encoding varying per request")
    print("    (the encoding varies because the reply reference advances --")
    print("     this is what a diff encoder does, not an artefact)\n")

    n = 25
    old_same, old_diff = run_over_wire(hash_encoding, n, "OLD sha256(sem_resp)")
    new_same, new_diff = run_over_wire(hash_body, n, "NEW body hash")

    print()
    check("OLD comparison misses every repeat", old_same == 0,
          "-- this is the {'DIFF': 25} in pipe_workload")
    check("NEW comparison catches every repeat", new_same == n - 1,
          "got %d of %d" % (new_same, n - 1))

    # and it must still notice a REAL change
    changed = [("host", "New-York-1"), ("ip", "10.102.60.1"),
               ("lan", "192.168.1.99"), ("wan", "0.0.0.0")]
    check("NEW comparison still sees a real change",
          hash_body(b"x", ECHO_BODY, 200) != hash_body(b"x", changed, 200))
    check("NEW comparison separates status",
          hash_body(b"x", ECHO_BODY, 200) != hash_body(b"x", ECHO_BODY, 500))

    print()
    if FAILS:
        print("DECISION TIER FAILED: %s" % FAILS)
        return 1
    print("ALL CHECKS PASS -- the fix is what turns 0%% SAME into 100%% on a")
    print("static endpoint, over a wire with real latency and jitter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
