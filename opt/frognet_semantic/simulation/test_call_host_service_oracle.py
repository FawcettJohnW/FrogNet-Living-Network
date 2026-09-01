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
test_call_host_service_oracle.py - the runnable mediahost orchestration (items 1+2).

Gates call_host_service.py. Drives the REAL frognet_mediahost.compute_plan + a real
TupleControl-shaped backend with injected fakes for sockets and the MixEncoder, so it
runs in-container without ffmpeg/real sockets. Asserts:

  1. CREATE -> conn_info published (item 2): on a create-stream request the service
     publishes conn_info {addr, tx, rx, supports} so the client's resolve_conn_info finds
     it. Until this, the client blocks.
  2. ONE streaming encoder per demanded rung (not per recipient); sources route their
     demuxed A/V into the encoder(s) (the streaming feed, faked).
  3. MIXED program fans to per-destination downlinks - recipients at a rung receive the
     mixed bytes over their OWN sender (independent).
  4. PER-DESTINATION backpressure: a slow downlink steps ITS bearer; others untouched.
  5. ONE UP / ONE DOWN: each participant has exactly one uplink receiver and one downlink
     sender regardless of party count.
"""
from __future__ import annotations
import os, sys, struct

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok:
        FAILS.append(label)


class FakeBackend:
    """TupleControl backend: put/get_one/get with scope filtering."""
    def __init__(self): self.rows = []
    def put(self, svc, var, scope, value, addr=None):
        self.rows = [r for r in self.rows if not (r[0]==svc and r[1]==var and r[2]==scope)]
        self.rows.append((svc, var, scope, value)); return True
    def get(self, svc, var, dbhost=None, fresh_s=0):
        return [{"scope": r[2], "value": r[3]} for r in self.rows if r[0]==svc and r[1]==var]
    def get_one(self, svc, var, scope, fresh_s=0):
        for r in self.rows:
            if r[0]==svc and r[1]==var and r[2]==scope: return r[3]
        return None


class FakeReceiver:
    def __init__(self, host, port, on_frame): self.on_frame = on_frame; self.closed=False
    def start(self): pass
    def close(self): self.closed=True


class FakeSender:
    def __init__(self, host, port, maxq=120, on_drop=None):
        self.delivered=[]; self.on_drop=on_drop; self.block=False; self._q=[]; self.maxq=maxq
    def send(self, frame, is_key=False):
        if self.block:
            if len(self._q)>=self.maxq:
                if self.on_drop: self.on_drop()
                return
            self._q.append(frame); return
        self.delivered.append(frame)
    def depth(self): return len(self._q)
    def close(self): pass


class FakeEncoder:
    """Stands in for MixEncoder: records fed frames; on feed, emits a 'mixed' chunk via
    on_mixed so the fan path is exercised."""
    instances=[]
    def __init__(self, level_idx, sources, on_mixed, **kw):
        self.level_idx=level_idx; self.sources=list(sources); self.on_mixed=on_mixed
        self.fed=[]; FakeEncoder.instances.append(self)
    def start(self): return True
    def feed(self, who, kind, data):
        self.fed.append((who, kind, data))
        # emit a mixed chunk tagged with this rung so the oracle can verify routing
        self.on_mixed(struct.pack("!B", self.level_idx) + b"MIX")
    def close(self): pass


def _pack(seq, ts, key, audio, video):
    return struct.Struct("!IQBII").pack(seq, ts, 1 if key else 0, len(audio), len(video)) + audio + video


def run():
    import call_host_service as S
    from media_stream import TupleControl
    from frognet_mediahost import compute_plan

    who = ["alice", "bob", "carol"]
    backend = FakeBackend()
    control = TupleControl(backend, "s", addr="10.250.250.1")

    # request_create so maybe_create fires
    control.request_create(codec="vp8")

    svc = S.CallHostService("s", control, addr="10.250.250.1",
                            _receiver_cls=FakeReceiver, _sender_cls=FakeSender,
                            _encoder_cls=FakeEncoder,
                            dest_addr_port=lambda rec: ("10.250.250.1", 9202))

    # 1. create -> conn_info published
    check("maybe_create returns True on a create request", svc.maybe_create())
    ci = control.conn_info()
    check("conn_info published on create (item 2)",
          ci is not None and ci.get("tx") and ci.get("rx") and "vp8" in ci.get("supports", []),
          f"conn_info={ci}")

    # plan: all three stream + watch at rung 6
    streams = [{"type":"stream","session":"s","who":w} for w in who]
    watchers = [{"type":"watch","session":"s","who":w,"ladder_level":6} for w in who]
    plan = compute_plan(streams, watchers)

    for w in who:
        svc.add_source(w, "0.0.0.0", svc.tx_port)
    svc.set_plan(plan)

    # 2. one encoder per demanded rung (one rung here)
    check("one streaming encoder per demanded rung (not per recipient)",
          len(svc.encoders) == 1 and 6 in svc.encoders, f"encoders={sorted(svc.encoders)}")

    # 5. one up / one down per participant
    check("one uplink receiver per participant", len(svc.sources) == 3, f"sources={len(svc.sources)}")
    check("one downlink sender per participant", len(svc.dests) == 3, f"dests={len(svc.dests)}")

    # 2+3. a source pushes AV up -> routed to encoder -> mixed fans to all dests
    rx = {w: svc.sources[w].on_frame for w in who}
    for i, w in enumerate(who):
        rx[w](_pack(i, 1000, True, audio=f"a{w}".encode(), video=f"v{w}".encode()))
    enc = svc.encoders[6]
    check("sources route demuxed A/V into the encoder", len(enc.fed) > 0, f"fed={len(enc.fed)}")
    fanned_each = all(len(d.sender.delivered) > 0 for d in svc.dests.values())
    check("mixed program fans to every destination's own sender", fanned_each,
          f"delivered={ {w: len(svc.dests[w].sender.delivered) for w in who} }")

    # 4. per-destination backpressure: block bob's downlink, push more, bob bearer steps
    svc.dests["bob"].sender.block = True
    svc.dests["bob"].sender.maxq = 1
    for t in range(40):
        for w in who:
            rx[w](_pack(100+t, 2000+t, t%10==0, audio=b"a", video=b"v"))
    svc.apply_backpressure(drop_thresh=0.0)
    bob_bearer = svc.bearer["bob"]
    check("slow downlink steps ITS bearer down", bob_bearer < 6, f"bob bearer={bob_bearer}")
    check("healthy downlinks' bearers untouched",
          svc.bearer["alice"] == 6 and svc.bearer["carol"] == 6,
          f"alice={svc.bearer['alice']} carol={svc.bearer['carol']}")
    # bearer published per session
    bv = control.bearer()
    check("bearer published to the tuple space", bv is not None, f"bearer={bv}")


def main():
    print("=== runnable mediahost: create->conn_info, mix fan, per-dest backpressure ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL HOST-SERVICE CHECKS PASS" if not FAILS
                  else f"HOST-SERVICE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
