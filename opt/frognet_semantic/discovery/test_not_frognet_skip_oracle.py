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
test_not_frognet_skip_oracle.py - [NOT_FROGNET_SKIP_V1]

A host proven NOT a FrogNet node earlier in a session must not be re-probed by
the walk for the rest of that session (the negative cache flush()es at each merge
top). The walk already WRITES the cache via _nf_mark; this proves it now READS it
via is_marked and skips, so the 5 convergence passes in one merge stop re-paying
the probe budget on the same stray LAN clients.

REGRESSION (against the write-only walk): pass 2 re-probes the marked host -> FAIL.
GUARD: a protected FrogNet role (.1) is never cached, so it is never skipped.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import not_frognet
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

NF = "10.250.250.50"        # an on-segment stray client (LAN, not .1/.2)
DOT1 = "10.250.250.1"       # a protected FrogNet role


class CountingVerify(FakeVerify):
    """FakeVerify that records every measure_or_loop target so the oracle can
    assert whether a probe actually fired."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []
    def measure_or_loop(self, target, dev):
        self.calls.append((target, dev))
        return super().measure_or_loop(target, dev)


def _disc(verify):
    k = FakeKernel()
    k.seed("10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    return Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={}, self_identity="10.250.250.1", verify=verify)


def main():
    ok = True
    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    # isolate the cache in a temp sentinel and start clean (== merge-top flush)
    not_frognet.SENTINEL = tempfile.mktemp(suffix="_not_frognet.tsv")
    not_frognet.flush()

    print("=== a proven non-FrogNet host is not re-probed this session ===")

    # PASS 1: probe the stray client; :9009 REFUSED (definitive) -> marked
    # NOT_FROGNET. [NF_CONTRACT_V1] only structural absence marks.
    v1 = CountingVerify(refused={NF})
    _disc(v1).walk("eth0", NF, 1)
    check((NF, "eth0") in v1.calls, "pass 1 probes the unknown host once")
    check(not_frognet.is_marked(NF), "pass 1 marks it NOT_FROGNET in the cache")

    # PASS 2: a fresh walk (== the next convergence pass / a fresh process)
    v2 = CountingVerify(refused={NF})
    _disc(v2).walk("eth0", NF, 1)
    check((NF, "eth0") not in v2.calls,
          "pass 2 SKIPS the proven non-FrogNet host (no re-probe)  <-- the bypass")

    print("=== guard: a TIMEOUT must NEVER mark (not_frognet.py contract) ===")
    not_frognet.flush()
    TMO = "10.250.250.77"
    vt = CountingVerify(dead={TMO})            # dead == no answer == timeout-like
    _disc(vt).walk("eth0", TMO, 1)
    check((TMO, "eth0") in vt.calls, "timeout host is probed")
    check(not not_frognet.is_marked(TMO),
          "timeout host is NOT marked (slow real node must not be poisoned)")

    print("=== guard: a protected FrogNet role (.1) is never cached or skipped ===")
    not_frognet.mark(DOT1, "should_be_refused")          # protected -> refused
    check(not not_frognet.is_marked(DOT1), ".1 is never marked non-FrogNet")
    v3 = CountingVerify(dead={DOT1})
    _disc(v3).walk("eth0", DOT1, 1)
    # .1 is the node's served identity; it still gets walked (not skipped). The
    # on-segment branch only fires for non-.1, so .1 takes the normal path - the
    # point is simply that is_marked never silenced it.
    check(not not_frognet.is_marked(DOT1), ".1 still not in the cache after a walk")

    try:
        os.remove(not_frognet.SENTINEL)
    except OSError:
        pass

    print()
    print("ALL NOT_FROGNET_SKIP CHECKS PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
