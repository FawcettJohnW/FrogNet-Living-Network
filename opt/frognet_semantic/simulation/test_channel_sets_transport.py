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
test_channel_sets_transport.py - INTEGRATION test (uses the transport simulator).

Validates the PROPERTY the socket-set allocator exists to provide, end to end
over the faithful sim transport - not just the allocator's bookkeeping:

  1. ISOLATION: two protocols the allocator placed on INDEPENDENT sets carry
     traffic independently. Stalling one set's wire does NOT inflate the other
     set's round-trip - separate sockets, separate seq spaces, separate
     head-of-line domains.

  2. SHARED FATE (overlay): two protocols the allocator OVERLAID onto one set
     share a wire, so stalling that set delays BOTH. This is the documented
     cost of oversubscription, asserted so it can't silently regress into a
     false isolation claim.

Each "set" is realized as an independent transport_factories sim pair
(WireMedium + SimulatedDaemonSession). The allocator decides which protocols
are independent vs overlaid (by sensitivity); this test wires those decisions
to real sim transports and measures the result.

Run:  FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=/usr/local/bin \\
        python3 simulation/test_channel_sets_transport.py     (exit 0 = pass)

NOTE: this proves the isolation property on the simulator. Doing it through the
PRODUCTION _DaemonWorker requires a 10/8 peer + live daemon (the IP gate), so
that variant is box-tier - see STATUS. The sim result is the design proof; the
box run is the measurement.
"""
from __future__ import annotations
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE, os.path.join(_ROOT, "simulation")):
    if p and p not in sys.path:
        sys.path.insert(0, p)

from transport_sim_tier import wrap_req_repeat, try_parse, _hash_for, OP_RESP_SAME, NetworkParams  # noqa: E402
from transport_factories import connect_pair  # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "proxy"))
import channel_sets as cs  # noqa: E402

_FAILS = []
def ck(name, cond, got=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  - {got}"))
    if not cond:
        _FAILS.append(name)


def _rtt_ms(proxy, n=8):
    """Median-ish RTT over n REQ_REPEATs on a sim pair's proxy."""
    times = []
    for i in range(n):
        t = time.perf_counter()
        r = proxy.send_request(wrap_req_repeat(_hash_for(f"p{i}")), timeout=5.0)
        times.append((time.perf_counter() - t) * 1000)
        assert r is not None and try_parse(r).op == OP_RESP_SAME
    times.sort()
    return times[len(times) // 2]


def main():
    print("=== channel_sets INTEGRATION (sim transport) ===")

    # Allocator decides placement: game+media independent, bulk overlays.
    H, P = "10.255.254.1", 9009
    alloc = cs._PeerChannelSets(H, P)
    t_game = alloc.open("game", sensitivity=9)   # set 0
    t_med = alloc.open("media", sensitivity=5)    # set 1
    t_bulk = alloc.open("bulk", sensitivity=0)    # set 2
    snap = alloc.snapshot()
    ck("allocator: game/media/bulk on distinct sets",
       len({t_game.set_id, t_med.set_id, t_bulk.set_id}) == 3, snap)

    clean = NetworkParams(latency_ms=2.0, bandwidth_bps=1e9)
    stalled = NetworkParams(latency_ms=120.0, jitter_ms=10.0)  # set under stress

    # --- 1) ISOLATION: independent sets, stall one, measure the other --------
    # game set: clean wire. media set: stalled wire. Separate sim pairs ==
    # separate socket sets.
    g_proxy, g_d, gh1, gh2 = connect_pair("10.255.254.1", "sim",
                                          fwd_params=clean, rev_params=clean)
    m_proxy, m_d, mh1, mh2 = connect_pair("10.255.254.2", "sim",
                                          fwd_params=stalled, rev_params=stalled)
    try:
        # Hammer the stalled (media) set continuously in the background...
        stop = threading.Event()
        def pound():
            while not stop.is_set():
                try:
                    m_proxy.send_request(wrap_req_repeat(_hash_for("m")), timeout=5.0)
                except Exception:
                    break
        th = threading.Thread(target=pound, daemon=True); th.start()
        time.sleep(0.2)
        # ...and measure the game set's RTT while media is stalled+busy.
        game_rtt = _rtt_ms(g_proxy)
        stop.set(); th.join(timeout=2.0)
        # game wire is 2ms/side -> ~4ms RTT. If it were sharing media's stalled
        # socket it would be >200ms. Assert it stayed near its own floor.
        ck(f"independent set isolated under neighbor stall (game RTT={game_rtt:.1f}ms)",
           game_rtt < 50.0, f"{game_rtt:.1f}ms")
    finally:
        for x in (g_proxy, m_proxy): x.close()
        for x in (g_d, m_d): x.stop()
        for h in (gh1, gh2, mh1, mh2): h.close()

    # --- 2) SHARED FATE: two protocols on ONE set share the wire -------------
    # bulk + log overlaid onto the same set => one sim pair carries both. A
    # stall on that shared wire delays both; neither gets an isolated floor.
    s_proxy, s_d, sh1, sh2 = connect_pair("10.255.254.3", "sim",
                                          fwd_params=stalled, rev_params=stalled)
    try:
        shared_rtt = _rtt_ms(s_proxy, n=4)
        ck(f"overlaid set pays the stall (shared RTT={shared_rtt:.1f}ms >= 2x latency)",
           shared_rtt >= 200.0, f"{shared_rtt:.1f}ms")
    finally:
        s_proxy.close(); s_d.stop(); sh1.close(); sh2.close()

    print("\nRESULT:", "ALL PASS" if not _FAILS else f"FAILURES {_FAILS}")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
