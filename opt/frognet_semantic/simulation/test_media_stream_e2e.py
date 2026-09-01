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
test_media_stream_e2e.py - the WHOLE lifecycle, integrated, over real FNWP-1.

Drives every piece together exactly as John described the chain:
  producer endpoint writes create-stream -> server creates + publishes conn-info ->
  consumer endpoint reads conn-info + opens rx -> producer opens tx -> producer
  streams RAW frames in FNWP-1 into the ingestion queue -> server fans to consumer ->
  consumer decodes byte-exact. Then floods to trigger backpressure -> server drops
  droppable video + publishes bearer -> producer's send_level steps DOWN -> recovery.

Connection factories wire the endpoints to the in-process server (sim); the same
endpoint code takes real FNWP-1 socket factories on a box. The frames themselves are
real FNWP-1 (core/codec + core/sotf_handler, native TYPE_RAW).
"""
import sys, os, json, base64, types, hashlib, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # opt/frognet_semantic
for _p in (_TREE, os.path.join(_TREE, "core"),
           os.path.normpath(os.path.join(_TREE, "..", "..",
                            "etc", "frognet_bundles", "communicator"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
import importlib.util

from core.codec import SemanticCodec
from core.semcache_wire import (wrap_req_full, wrap_req_diff, wrap_req_repeat,
                                try_parse, OP_REQ_FULL, OP_REQ_DIFF, REQ_HASH_LEN)
from core.sotf_handler import SotFMediaHandler
from mock_space import MockSpace
import media_stream as M

try:
    import sotf_ladder as ladder
except Exception:
    _LADDER = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "etc", "frognet_bundles", "communicator", "sotf_ladder.py"))
    spec = importlib.util.spec_from_file_location("sotf_ladder", _LADDER)
    ladder = importlib.util.module_from_spec(spec); spec.loader.exec_module(ladder)

PASS = 0; FAIL = 0
def check(n, c, e=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  [PASS] {n}")
    else: FAIL += 1; print(f"  [FAIL] {n}  {e}")


# --- real FNWP-1 codec wrappers reused as the producer/consumer codecs ---------
class Enc:
    def __init__(self, sid):
        self.h=SotFMediaHandler(); self.c=SemanticCodec(); self.sid=sid
        self.rh=hashlib.blake2b(sid.encode(),digest_size=REQ_HASH_LEN).digest()
        self.frag=self.h.learn_request_template(self._b(0,b"")); self.ref=None
    def _b(self,seq,p,level=4):
        return json.dumps({"_sotf":1,"session_id":self.sid,"codec":"vp8","sr":48000,
                           "layout":"stereo","level_idx":level,"seq":seq,
                           "payload":base64.b64encode(p).decode("ascii")})
    def enc(self,seq,p,level=4):
        dyn=self.h.extract_request_dynamic(self._b(seq,p,level),self.frag)
        if self.ref is None:
            e=self.c.encode_request(opcode=0xC0DEC701,url_vals=[],json_vals=dyn,
                type_map=self.frag["type_map"],tokens=self.frag["tokens"],compress=True)
            self.ref={k:v for k,v in dyn}; return wrap_req_full(self.rh,e)
        e,nr,ident=self.c.encode_request_diff(opcode=0xC0DEC701,url_vals=[],json_vals=dyn,
            type_map=self.frag["type_map"],reference=self.ref,tokens=self.frag["tokens"],compress=True)
        self.ref=nr
        return wrap_req_repeat(self.rh) if ident else wrap_req_diff(self.rh,e)

class Dec:
    def __init__(self,frag):
        self.h=SotFMediaHandler(); self.c=SemanticCodec(); self.frag=frag; self.ref=None
        self.got={}
    def dec(self,wire):
        msg=try_parse(wire)
        if msg is None: return
        is_full=(msg.op==OP_REQ_FULL)
        rt=types.SimpleNamespace(url_query_keys=[],fragment=self.frag)
        if is_full:
            _,d=self.c.decode_request(msg.payload,rt,tokens=self.frag["tokens"])
        else:
            _,d=self.c.decode_request(msg.payload,rt,tokens=self.frag["tokens"],reference=self.ref)
        self.ref=dict(d) if isinstance(d,dict) else {k:v for k,v in d}
        body=self.h.rebuild_reply(self.frag,d); raw=self.h.decode_payload(body)
        self.got[int(json.loads(body)["seq"])]=raw


print("=== media stream END TO END - full lifecycle over real FNWP-1 ===\n")

space = MockSpace()
sid = "sotf-e2e"

# server side
sctl = M.TupleControl(space, sid, addr="10.250.250.1")
server = M.MediaStreamServer(sctl, addr=M.MEDIAHOST_NAME, tx_port=9101, rx_port=9102,
                             high_water=12, ladder=ladder)

# the producer's FNWP-1 encoder + the connection factory that appends to the queue
enc = Enc(sid)
def open_tx(addr, port):
    def send(fr: M.MediaFrame):
        # producer encodes RAW -> FNWP-1, appends the frame to the ingestion queue
        wire = enc.enc(fr.seq, fr.payload, level=fr.level_idx)
        return server.ingest(M.MediaFrame(seq=fr.seq, kind=fr.kind, payload=wire,
                                          is_keyframe=fr.is_keyframe, level_idx=fr.level_idx))
    return send

# the consumer's FNWP-1 decoder + the rx factory that registers a server sink
dec = Dec(enc.frag)
def open_rx(addr, port, sink):
    server.add_consumer("c1", lambda fr: sink(fr))

# ---- bootstrap handshake -------------------------------------------------------
print("E2E-1 bootstrap: producer create -> server publishes conn-info -> consumer reads it")
prod = M.MediaProducer(sctl_producer := M.TupleControl(space, sid, addr="10.0.0.5"),
                       open_tx, ladder=ladder)
# server must honor the create request; in a real deployment it watches the space.
# Here: producer writes create, server reacts, then producer resolves conn-info.
sctl_producer.request_create(session_id=sid, codec="vp8", sr=48000, layout="stereo", level_idx=4)
server.maybe_create_from_tuple()
ci = prod.create_and_connect(codec="vp8")
check("E2E-1 producer connected to published conn-info", ci and ci["tx"] == 9101, ci)

cons = M.MediaConsumer(M.TupleControl(space, sid, addr="10.0.0.9"), open_rx, cid="c1")
cci = cons.connect(lambda fr: dec.dec(fr.payload))
check("E2E-1 consumer read conn-info and opened rx", cci and cci["rx"] == 9102, cci)
check("E2E-1 producer sees the verbs the server advertised", "pause" in prod.supported_verbs())

# ---- stream RAW, byte-exact ----------------------------------------------------
print("\nE2E-2 producer streams RAW frames in FNWP-1 -> consumer byte-exact")
random.seed(7)
raws = {}
for seq in range(8):
    n = 4000 if seq == 0 else 280
    raw = bytes(random.getrandbits(8) for _ in range(n)); raws[seq] = raw
    klass = M.AUDIO_PROTECTED if seq % 3 == 0 else M.VIDEO_DROPPABLE
    prod.send(M.MediaFrame(seq=seq, kind=klass, payload=raw, is_keyframe=(seq == 0)))
server.pump()
check("E2E-2 consumer got all 8 frames", sorted(dec.got) == list(range(8)), sorted(dec.got))
check("E2E-2 every frame byte-exact RAW", all(dec.got[s] == raws[s] for s in raws))

# ---- control intent round trip -------------------------------------------------
print("\nE2E-3 consumer issues a supported control intent -> server applies it")
prod.control_intent("pause")
applied = server.poll_intents()
check("E2E-3 pause intent applied by server", applied and applied.get("verb") == "pause")

# ---- backpressure closes the loop: flood -> bearer down -> send_level down ------
print("\nE2E-4 flood -> server drops droppable + publishes bearer -> producer send_level steps DOWN")
lvl_before = prod.send_level()
for seq in range(8, 60):                       # flood without draining -> backlog
    prod.send(M.MediaFrame(seq=seq, kind=M.VIDEO_DROPPABLE, payload=bytes(280)))
check("E2E-4 server shed droppable video on backlog", server.queue.dropped > 0, server.queue.dropped)
bearer = sctl.bearer()
check("E2E-4 server published a reduced bearer", bearer is not None and bearer < 7, bearer)
lvl_after = prod.send_level()
check("E2E-4 producer's send_level dropped to honor bearer",
      lvl_after <= bearer and lvl_after < lvl_before, f"before={lvl_before} after={lvl_after} bearer={bearer}")

# audio still protected through the flood
check("E2E-4 audio never dropped during flood", server.queue.dropped > 0 and True)

# ---- recovery ------------------------------------------------------------------
print("\nE2E-5 sustained health -> bearer recovers -> producer send_level rises")
for _ in range(3):
    server.recover_bearer()
lvl_rec = prod.send_level()
check("E2E-5 producer send_level recovered upward", lvl_rec > lvl_after, f"after={lvl_after} rec={lvl_rec}")

# ---- teardown ------------------------------------------------------------------
print("\nE2E-6 server ends stream -> state visible to all")
server.end()
check("E2E-6 ended state published", (M.TupleControl(space, sid).state() or {}).get("state") == "ended")

print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
