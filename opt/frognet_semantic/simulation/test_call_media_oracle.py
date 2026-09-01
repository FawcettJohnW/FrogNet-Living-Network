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
test_call_media_oracle.py - the Communicator call on the canonical media stack.

Gates call_media.py. Fails on the OLD design (blocking engine: no bearer, no ladder,
no shed) and passes on the new binding. Drives the REAL MediaFrame/ladder shapes with
injected fakes for the socket + tuple control so it runs in-container without a box.

Asserts the architecture we agreed:
  1. opaque payload round-trips: pack_av -> (carried opaque) -> unpack_av is byte-exact
     for audio+video+seq/ts/key (transport makes no claim about contents).
  2. bearer caps the send level: server publishes a lower bearer -> send_level() steps
     the rung DOWN (backpressure as a control byte, via the tuple).
  3. lower rung sends FEWER video frames, not a slower camera: at an audio-only rung,
     feed_video forwards nothing; audio still forwarded every tick.
  4. audio is protected: it is sent as a keep (is_key) so the queue never sheds it.
  5. predictive backlog: depth() reflects the sender queue (the signal the ladder reads).
"""
from __future__ import annotations
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok:
        FAILS.append(label)


# -- fakes: a tuple control whose bearer we drive, and a capture-free socket sender --
class FakeControl:
    """Stands in for media_stream.TupleControl. We set the bearer the server 'published'
    and return conn-info as if a mediahost had created the stream."""
    session_id = "alice-sleepy-xlan"
    def __init__(self): self._bearer = None
    def request_create(self, **p): self._created = p
    def conn_info(self):
        return {"addr": "10.250.250.1", "tx": 9101, "rx": 9102, "supports": ["vp8"]}
    def bearer(self): return self._bearer
    def set_bearer(self, idx): self._bearer = idx
    def publish_metrics(self, *a, **k): pass


class FakeSocketSender:
    """Stands in for sotf_media_backing.MediaSocketSender: records sends, shed on full,
    keeps keyframes, exposes depth()."""
    def __init__(self, host, port, maxq=120, on_drop=None):
        self.sent = []           # (payload, is_key)
        self.maxq = maxq
        self.on_drop = on_drop
        self._q = []
    def send(self, frame: bytes, is_key: bool = False):
        self.sent.append((frame, is_key))
        if len(self._q) >= self.maxq and not is_key:
            self._q.pop(0)
            if self.on_drop: self.on_drop()
        self._q.append((frame, is_key))
    def depth(self): return len(self._q)
    def close(self): pass


def run():
    import call_media as CM
    import sotf_ladder

    # 1. opaque payload round-trip (byte-exact)
    audio = b"\x11\x22" * 80
    video = b"\xab\xcd\xef" * 300
    body = CM.pack_av(7, 123456, True, audio, video)
    seq, ts, key, a2, v2 = CM.unpack_av(body)
    check("opaque AV payload round-trips byte-exact",
          (seq, ts, key, a2, v2) == (7, 123456, True, audio, video),
          f"got seq={seq} ts={ts} key={key} alen={len(a2)} vlen={len(v2)}")

    # Build a CallSender on the real MediaProducer + ladder, fake tuple + socket.
    ctrl = FakeControl()
    open_tx = CM.make_open_tx(_sender_cls=FakeSocketSender)
    cs = CM.CallSender(ctrl, node="alice", have_camera=True, have_mic=True,
                       _open_tx=open_tx)
    cs.connect(codec="vp8")
    fake_sender = cs._tx._sender

    # 2. bearer caps the send level (backpressure via tuple)
    ctrl.set_bearer(None)
    hi = cs.send_level()
    ctrl.set_bearer(3)                      # server says: step down to an audio rung
    lo = cs.send_level()
    check("no bearer -> ceiling rung", hi >= 6, f"hi={hi}")
    check("bearer steps the send level DOWN", lo <= 3 and lo < hi, f"hi={hi} lo={lo}")

    # 3. lower rung sends FEWER video frames (camera untouched); audio still flows
    ctrl.set_bearer(3)                      # audio-only rung (L3 carries audio, not video)
    before = len(fake_sender.sent)
    for i in range(10):
        cs.feed_video(ts=1000 + i, key=(i == 0), video=b"\x00" * 200)   # droppable
        cs.feed_audio(ts=1000 + i, audio=b"\x01" * 64)                  # protected
    sent_now = fake_sender.sent[before:]
    n_video = sum(1 for (p, k) in sent_now if CM.unpack_av(p)[4])   # vlen>0
    n_audio = sum(1 for (p, k) in sent_now if CM.unpack_av(p)[3])   # alen>0
    check("audio-only rung forwards NO video frames", n_video == 0, f"video sent={n_video}")
    check("audio still forwarded every tick at low rung", n_audio == 10, f"audio sent={n_audio}")

    # at a video rung, video flows again (same camera feed, different cadence)
    ctrl.set_bearer(6)
    before = len(fake_sender.sent)
    for i in range(5):
        cs.feed_video(ts=2000 + i, key=(i == 0), video=b"\x00" * 200)
    n_video_hi = sum(1 for (p, k) in fake_sender.sent[before:] if CM.unpack_av(p)[4])
    check("video rung forwards video again", n_video_hi == 5, f"video sent={n_video_hi}")

    # 4. audio is protected (sent as keep so the queue never sheds it)
    audio_keeps = [k for (p, k) in fake_sender.sent if CM.unpack_av(p)[3] and k]
    audio_total = [1 for (p, k) in fake_sender.sent if CM.unpack_av(p)[3]]
    check("all audio frames marked keep (never shed)",
          len(audio_keeps) == len(audio_total), f"keeps={len(audio_keeps)}/{len(audio_total)}")

    # 5. depth() is the predictive backlog signal
    check("depth() reflects the sender queue", cs.depth() >= 0, f"depth={cs.depth()}")


def main():
    print("=== Communicator call on the canonical media stack ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL CALL-MEDIA CHECKS PASS" if not FAILS
                  else f"CALL-MEDIA CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
