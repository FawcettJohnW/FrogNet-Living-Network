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
test_media_stream_transport.py - bind the lifecycle ENDPOINT to the REAL FNWP-1
socket transport (transport_sim_tier.connect_pair: proxy<->daemon over simulated
sockets with shaping), and prove RAW-in-FNWP-1 arrives byte-exact at the far daemon.

This closes the "real socket" gap: earlier data-plane tests used in-process codec
calls; here the producer's tx connection IS a real SimulatedProxyWorker shipping
frames across a shaped wire to a SimulatedDaemonSession that decodes them. The
control/bootstrap (conn-info, create) still rides the tuple space (MockSpace).

T1 clean wire   - every RAW frame byte-exact at the daemon; FULL then DIFF.
T2 shaped wire  - under latency, still byte-exact (keyframes RELIABLE retry, deltas DROPPABLE).

Run: PYTHONPATH=<tree>/opt/frognet_semantic python3 test_media_stream_transport.py
"""
import sys, os, hashlib, random, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # opt/frognet_semantic
for _p in (_TREE, os.path.join(_TREE, "simulation"), os.path.join(_TREE, "core"),
           os.path.normpath(os.path.join(_TREE, "..", "..",
                            "etc", "frognet_bundles", "communicator"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import sotf_video_stream_test as svt          # SotFSender/SotFReceiver/VideoFrame, connect_pair
from transport_sim_tier import NetworkParams, REQ_HASH_LEN
from core.sotf_handler import SotFMediaHandler
from mock_space import MockSpace
import media_stream as M

PASS = 0; FAIL = 0
def check(n, c, e=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  [PASS] {n}")
    else: FAIL += 1; print(f"  [FAIL] {n}  {e}")


def run_over_wire(label, params):
    """Stand up a real connect_pair; the MediaProducer's open_tx ships frames through
    the SotFSender on that proxy; the SotFReceiver on the daemon decodes them. The
    lifecycle bootstrap (create -> conn-info) runs on the tuple space."""
    space = MockSpace()
    sid = f"sotf-tx-{label}"
    session_init = {"session_id": sid, "codec": "vp8", "sr": 48000,
                    "layout": "stereo", "level_idx": 4}
    # the sim's handler builds the fragment connect_pair/SotFSender expect
    sim_handler = svt.SotFMediaHandler()
    fragment = sim_handler.extract_template(session_init)
    req_hash = hashlib.blake2b(sid.encode(), digest_size=REQ_HASH_LEN).digest()

    proxy, daemon, w1, w2 = svt.connect_pair(
        local_gw="10.20.21.5", mode="sim",
        fwd_params=params, rev_params=params,
        default_fragment=fragment)
    receiver = svt.SotFReceiver(daemon, fragment, req_hash)
    sender = svt.SotFSender(proxy, sid, fragment, req_hash)

    # lifecycle bootstrap on the tuple space
    sctl = M.TupleControl(space, sid, addr="10.250.250.1")
    server_meta = M.MediaStreamServer(sctl, addr=M.MEDIAHOST_NAME, tx_port=9101, rx_port=9102)
    M.TupleControl(space, sid, addr="10.0.0.5").request_create(session_id=sid, codec="vp8")
    server_meta.maybe_create_from_tuple()

    # the producer's tx connection IS the real proxy: open_tx ships over the socket wire
    def open_tx(addr, port):
        def send(fr: M.MediaFrame):
            vf = svt.VideoFrame(seq=fr.seq, pts=fr.seq, payload=fr.payload,
                                is_keyframe=fr.is_keyframe)
            return sender.send_frame(vf)        # encode RAW -> FNWP-1 -> real socket
        return send

    prod = M.MediaProducer(M.TupleControl(space, sid, addr="10.0.0.5"), open_tx)
    ci = prod.create_and_connect(codec="vp8")
    assert ci["tx"] == 9101

    # stream RAW frames across the real wire
    random.seed(11)
    sent = {}
    for seq in range(10):
        n = 3500 if seq == 0 else 260
        raw = bytes(random.getrandbits(8) for _ in range(n)); sent[seq] = raw
        prod.send(M.MediaFrame(seq=seq, kind=M.VIDEO_DROPPABLE if seq else M.AUDIO_PROTECTED,
                               payload=raw, is_keyframe=(seq == 0)))
    time.sleep(0.2)   # let the daemon drain the wire
    proxy.close(); daemon.stop()

    got = receiver.received_bytes
    delivered = [s for s in sent if s in got]
    exact = [s for s in delivered if got[s] == sent[s]]
    return sent, got, delivered, exact


print("=== media stream TRANSPORT binding - endpoint over real FNWP-1 sockets ===\n")

print("T1 clean wire: RAW byte-exact at the far daemon, FULL->DIFF")
sent, got, delivered, exact = run_over_wire("clean", NetworkParams(latency_ms=1.0, bandwidth_bps=1e9))
check("T1 all frames delivered across the socket wire", len(delivered) == len(sent),
      f"{len(delivered)}/{len(sent)}")
check("T1 every delivered frame byte-exact RAW", len(exact) == len(delivered) and len(exact) == len(sent),
      f"exact={len(exact)} delivered={len(delivered)}")

print("\nT2 shaped wire (120ms latency): still byte-exact (RELIABLE keyframe, DROPPABLE deltas)")
sent2, got2, delivered2, exact2 = run_over_wire("shaped", NetworkParams(latency_ms=120.0, bandwidth_bps=2e6))
check("T2 keyframe (seq 0, RELIABLE) delivered byte-exact", 0 in [s for s in exact2], exact2)
check("T2 delivered frames all byte-exact", len(exact2) == len(delivered2) and len(delivered2) > 0,
      f"exact={len(exact2)} delivered={len(delivered2)}")

print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
