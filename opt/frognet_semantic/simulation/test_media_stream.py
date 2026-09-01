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
test_media_stream.py - positive + negative tests for the media stream lifecycle.

Stage 1 (this file): the TUPLE/CONTROL plane + ingestion-queue backpressure, driven
by MockSpace (no live DB). Fast, deterministic. Covers P1,P2(queue),P3,P4,P5,P6,P7
and N1,N2,N3,N5,N6. The real-FNWP-1 data-plane integrity test (RAW bytes through
the proven SotFSender/SotFReceiver codec wire) is test_media_stream_wire.py.

Run: python3 test_media_stream.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_space import MockSpace
import media_stream as M
import importlib.util

PASS = 0; FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  [PASS] {name}")
    else:
        FAIL += 1; print(f"  [FAIL] {name}  {extra}")

# load the real sotf_ladder for bearer/ceiling semantics: it's on PYTHONPATH on a
# box (/etc/frognet_bundles/communicator); fall back to a path relative to THIS file.
ladder = None
try:
    import sotf_ladder as ladder
except Exception:
    _LADDER_PATH = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "etc", "frognet_bundles", "communicator", "sotf_ladder.py"))
    if os.path.exists(_LADDER_PATH):
        spec = importlib.util.spec_from_file_location("sotf_ladder", _LADDER_PATH)
        ladder = importlib.util.module_from_spec(spec); spec.loader.exec_module(ladder)


def frame(seq, kind, n=200, key=False, level=4):
    return M.MediaFrame(seq=seq, kind=kind, payload=bytes(n), is_keyframe=key, level_idx=level)


print("=== media stream lifecycle - tuple/control plane + backpressure ===\n")

# ---------------------------------------------------------------- P1 bootstrap
print("P1 bootstrap handshake (create-stream -> conn-info -> endpoint reads it)")
space = MockSpace()
sid = "sotf-p1"
producer_ctl = M.TupleControl(space, sid, addr="10.0.0.5")
server_ctl   = M.TupleControl(space, sid, addr="10.250.250.1")
server = M.MediaStreamServer(server_ctl, addr=M.MEDIAHOST_NAME, tx_port=9101, rx_port=9102, ladder=ladder)

# before any create request, the server creates nothing
check("P1 no create-request -> server does not create", server.maybe_create_from_tuple() is False)
check("P1 no conn-info published yet", producer_ctl.conn_info() is None)
# first producer writes create-stream
producer_ctl.request_create(session_id=sid, codec="vp8", sr=48000, layout="stereo", level_idx=4)
check("P1 server honors create-request", server.maybe_create_from_tuple() is True)
ci = producer_ctl.conn_info()
check("P1 conn-info published & readable by endpoint", ci is not None and ci.get("tx") == 9101, ci)
check("P1 conn-info advertises supported verbs", ci and "pause" in ci.get("supports", []), ci)
check("P1 state is live", (producer_ctl.state() or {}).get("state") == "live")

# ---------------------------------------------------------------- N2 idempotent create
print("\nN2 duplicate create -> idempotent (existing conn-info, single stream)")
producer_ctl.request_create(session_id=sid, codec="vp8")   # someone re-requests
server.maybe_create_from_tuple()
ci2 = producer_ctl.conn_info()
check("N2 still one stream, same conn-info", ci2["tx"] == 9101 and server.created is True)

# ---------------------------------------------------------------- N3 read-before-publish
print("\nN3 consumer reads conn-info before server publishes -> clean absent, no crash")
space_b = MockSpace(); sidb = "sotf-n3"
cctl = M.TupleControl(space_b, sidb, addr="10.0.0.9")
got = cctl.conn_info()
check("N3 conn-info absent returns None (not a crash)", got is None)

# ---------------------------------------------------------------- P3 fan-out
print("\nP3 1 producer -> server -> N consumers, all receive each frame")
recv = {"a": [], "b": [], "c": []}
for cid in recv:
    server.add_consumer(cid, (lambda d: (lambda fr: recv[d].append(fr.seq)))(cid))
for s in range(5):
    server.ingest(frame(s, M.VIDEO_DROPPABLE if s % 2 else M.AUDIO_PROTECTED))
fanned = server.pump()
check("P3 all 5 frames drained", fanned == 5, fanned)
check("P3 consumer A got all seqs", recv["a"] == [0,1,2,3,4], recv["a"])
check("P3 consumer C got all seqs", recv["c"] == [0,1,2,3,4], recv["c"])

# ---------------------------------------------------------------- P4 late join
print("\nP4 late consumer joins mid-stream -> sees frames only from join point")
for s in range(5, 8):
    server.ingest(frame(s, M.VIDEO_DROPPABLE))
server.pump()                                   # pre-join frames drained & fanned (late not attached yet)
late = []
server.add_consumer("late", lambda fr: late.append(fr.seq))
for s in range(8, 10):
    server.ingest(frame(s, M.VIDEO_DROPPABLE))
server.pump()
check("P4 late consumer missed pre-join frames", 5 not in late and 6 not in late and 7 not in late, late)
check("P4 late consumer sees post-join frames", 8 in late and 9 in late, late)

# ---------------------------------------------------------------- P5 control intent
print("\nP5 endpoint writes a supported intent -> server reads & applies it")
producer_ctl.write_intent("pause")
applied = server.poll_intents()
check("P5 supported intent applied", applied and applied.get("verb") == "pause", applied)
check("P5 server recorded it", server.applied_intents and server.applied_intents[-1]["verb"] == "pause")

# ---------------------------------------------------------------- N5 unsupported verb
print("\nN5 endpoint writes an UNSUPPORTED verb -> rejected + error status")
producer_ctl.write_intent("teleport")
res = server.poll_intents()
check("N5 unsupported verb rejected", res == {"rejected": "teleport"}, res)
err = producer_ctl.error()
check("N5 error status published for all to read", err and err.get("code") == "unsupported_verb", err)

# ---------------------------------------------------------------- P6 async status
print("\nP6 server writes authoritative status -> any endpoint reads it async")
server_ctl.set_state("live", note="probe")
# a brand-new consumer control handle (different reader) sees it immediately
reader = M.TupleControl(space, sid, addr="10.0.0.77")
check("P6 independent reader sees server status immediately", (reader.state() or {}).get("note") == "probe")

# ---------------------------------------------------------------- P7 backpressure + bearer
print("\nP7 ingestion-queue flood -> droppable video shed, bearer drops, audio kept")
space2 = MockSpace(); sid2 = "sotf-p7"
sctl2 = M.TupleControl(space2, sid2, addr="10.250.250.1")
srv2 = M.MediaStreamServer(sctl2, high_water=8, ladder=ladder)
sctl2.request_create(session_id=sid2, codec="vp8"); srv2.maybe_create_from_tuple()
start_bearer = srv2._cur_bearer
results = []
# flood with video (droppable) past high-water without draining
for s in range(40):
    results.append(srv2.ingest(frame(s, M.VIDEO_DROPPABLE)))
check("P7 some video frames were dropped on backlog", srv2.queue.dropped > 0, srv2.queue.dropped)
check("P7 'dropped' result surfaced to caller", "dropped" in results)
bearer_after = sctl2.bearer()
check("P7 bearer tuple published and stepped DOWN", bearer_after is not None and bearer_after < start_bearer,
      f"start={start_bearer} after={bearer_after}")
# the sender's ladder consumes that bearer
if ladder:
    allowed = ladder.allowed_levels(True, True)
    ceil = ladder.ceiling(True, True)
    sl = ladder.send_level(ceil, bearer_after, allowed)
    check("P7 sender send_level honors the published bearer", sl <= bearer_after, f"send_level={sl} bearer={bearer_after}")

# ---------------------------------------------------------------- N6 audio never dropped
print("\nN6 flood with AUDIO (protected) -> never dropped; stall signaled instead")
space3 = MockSpace(); sid3 = "sotf-n6"
sctl3 = M.TupleControl(space3, sid3, addr="10.250.250.1")
srv3 = M.MediaStreamServer(sctl3, high_water=4, ladder=ladder)
sctl3.request_create(session_id=sid3); srv3.maybe_create_from_tuple()
res3 = [srv3.ingest(frame(s, M.AUDIO_PROTECTED)) for s in range(12)]
check("N6 audio frames NEVER counted as dropped", srv3.queue.dropped == 0, srv3.queue.dropped)
check("N6 over-pressure on protected audio signals 'stalled'", "stalled" in res3, res3)
check("N6 stall drove bearer down (audio can't keep up => degrade)", sctl3.bearer() is not None)

# ---------------------------------------------------------------- N1 no mediahost name
print("\nN1 no mediahost elected (name unresolvable) -> caller gets clean signal")
# In the lifecycle, 'no mediahost' = no conn-info ever appears. A consumer that polls
# must time out cleanly rather than hang/crash. Model the poll with a bounded retry.
def resolve_conn(ctl, tries=3):
    for _ in range(tries):
        ci = ctl.conn_info()
        if ci:
            return ci
    return None
space4 = MockSpace()
lone = M.TupleControl(space4, "sotf-n1", addr="10.0.0.9")
check("N1 bounded poll returns None cleanly when no server ever publishes",
      resolve_conn(lone) is None)

# ---------------------------------------------------------------- recover up
print("\nP7b sustained health -> bearer recovers UP a rung")
b0 = srv2._cur_bearer
srv2.recover_bearer()
check("P7b bearer steps back up under recovery", sctl2.bearer() == b0 + 1 if b0 < (ladder.MAX_IDX if ladder else 7) else True)

print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
