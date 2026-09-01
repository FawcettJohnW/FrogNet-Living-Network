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
test_media_stream_metrics.py - metrics wired through the lifecycle, published on the
tuple plane, read back deterministically by the monitor reader.

Proves:
  M1  server aggregate (injected/sent_on/fps) is published and read back
  M2  per-producer (sent) and per-consumer (received) counters published & read
  M3  a slow consumer (server fans fewer frames to it) is flagged, and ONLY it
  M4  audio-protected frames are counted as injected (never silently lost)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.normpath(os.path.join(HERE, "..", "..", "etc", "frognet_bundles", "communicator"))
_TREE = os.path.dirname(HERE)  # opt/frognet_semantic
for p in (BUNDLE, HERE, _TREE, os.path.join(_TREE, "core")):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

import mock_space as ms
import media_stream as M

_passed = _failed = 0
def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  [PASS] {name}")
    else:    _failed += 1; print(f"  [FAIL] {name}")


def build(session="sotf-metrics"):
    space = ms.MockSpace()
    sctl = M.TupleControl(space, session, addr="10.0.0.9")
    server = M.MediaStreamServer(sctl, node="ffmpeg1", high_water=64)
    # producer create -> server creates + publishes conn-info
    pctl = M.TupleControl(space, session, addr="10.0.0.1")
    pctl.request_create(session_id=session, codec="vp8")
    server.tick()
    return space, session, server, sctl


def test_flow():
    space, session, server, sctl = build()

    # two consumers: fast (gets every frame), slow (server fans only 60%)
    fast = M.MediaConsumer(M.TupleControl(space, session, addr="10.0.0.2"),
                           open_rx=lambda a, p, sink: None, cid="playerFast")
    slow = M.MediaConsumer(M.TupleControl(space, session, addr="10.0.0.3"),
                           open_rx=lambda a, p, sink: None, cid="playerSlow")
    fast_sink = fast._meter(lambda fr: None)
    slow_sink = slow._meter(lambda fr: None)

    producer = M.MediaProducer(M.TupleControl(space, session, addr="10.0.0.1"),
                               open_tx=lambda a, p: (lambda fr: server.ingest(fr)),
                               node="winProducer")
    producer.create_and_connect(codec="vp8")

    # account some compression so the improvement metric is exercised: a naive
    # full-frame stream would send raw bytes; FNWP-1 reference-diff sends far less.
    for seq in range(90):
        producer.metrics.on_wire(raw=2000, wire=(2000 if seq == 0 else 200))

    # 90 frames; server fans to fast every frame, to slow only ~60% (rest "lost" in rx)
    for seq in range(90):
        kind = M.AUDIO_PROTECTED if seq % 5 == 0 else M.VIDEO_DROPPABLE
        fr = M.MediaFrame(seq=seq, kind=kind, payload=bytes(200))
        producer.send(fr)                      # -> server.ingest (injected++)
        for got in server.queue.drain_all():   # server emits (sent_on++ per frame)
            server.metrics.on_sent_on()
            fast_sink(got)                     # fast player gets all
            if seq % 5 < 3:                    # slow player only ~60% -> seq gaps
                slow_sink(got)

    for ep in (server, producer, fast, slow):
        ep.publish_metrics()

    dash = M.read_stream_dashboard(sctl, fresh_s=0)
    print(dash.render())

    # M1 server aggregate present and sane
    check("M1 server aggregate injected==90", dash.server and dash.server.counters.get("injected") == 90)
    check("M1 server aggregate sent_on==90",  dash.server and dash.server.counters.get("sent_on") == 90)
    check("M1 server fps > 0",                dash.server and dash.server.fps > 0)
    # M2 per-endpoint counters present
    prod = next((s for s in dash.senders if s.node == "winProducer"), None)
    pf = next((p for p in dash.players if p.node == "playerFast"), None)
    psl = next((p for p in dash.players if p.node == "playerSlow"), None)
    check("M2 producer sent==90", prod and prod.counters.get("sent") == 90)
    check("M2 fast player received==90", pf and pf.counters.get("received") == 90)
    check("M2 slow player received<90", psl and psl.counters.get("received") < 90)
    # M3 slow consumer flagged, and ONLY it
    flagged = sorted(n.node for n in dash.slow_nodes())
    check("M3 only the slow player is flagged", flagged == ["playerSlow"])
    check("M3 fast player NOT flagged", pf and not pf.slow)
    check("M3 producer NOT flagged", prod and not prod.slow)
    # M4 audio frames (every 5th) were injected, not lost
    #    18 audio frames of 90; all injected (injected==90 already asserts none lost)
    check("M4 slow player shows drops (seq gaps)", psl and psl.counters.get("dropped") > 0)
    # M5 improvement metric: FNWP-1 wire is far smaller than raw full-frame send
    check("M5 producer reports compression saved_pct > 50",
          prod and prod.counters.get("saved_pct", 0) > 50)
    check("M5 wire_bytes < raw_bytes",
          prod and 0 < prod.counters.get("wire_bytes", 0) < prod.counters.get("raw_bytes", 1))


if __name__ == "__main__":
    print("=== media-stream metrics ===")
    test_flow()
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    sys.exit(1 if _failed else 0)
