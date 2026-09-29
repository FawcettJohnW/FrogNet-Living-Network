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
call_media.py - the Communicator A/V call on the CANONICAL media stack.

WHY THIS EXISTS
  The Communicator's calls historically spawned `frognet_communicator.py` as a
  subprocess and shipped frames over a BLOCKING `sendall` socket (`_send_env`).
  That transport stalls on a slow link instead of shedding, has no congestion
  signal, and is invisible to the SotF degrade/recover ladder. The production
  media stack already implements the better design and is fully built:

    sotf_media_backing.MediaSocketSender   - non-blocking, keyframe-aware shed,
                                             `depth()` is the predictive backlog signal
    sotf_media_backing.MediaSocketReceiver - server-side accept + length-prefixed read
    media_stream.MediaProducer             - reads the server's `bearer` tuple and caps
                                             send level via sotf_ladder (backpressure)
    media_stream.MediaStreamServer         - ingestion queue -> drain -> fan to consumers
    sotf_ladder_control                    - TUNE/DOWNGRADE/UPGRADE off drop-rate + lag

  This module is the THIN BINDING that moves the call onto that stack:
    - capture/encode (the surviving half of frognet_communicator: unpinned-framerate
      dshow/v4l2 capture, _pump, _iter_ivf_stream) feeds CallSender.send_video/audio
    - CallSender wraps a MediaProducer; its open_tx is a MediaSocketSender adapter
    - CallReceiver wraps a consumer rx (MediaSocketReceiver) feeding decoded PPM tiles
    - the ladder is wired so the server's bearer tuple steps the send level: at a lower
      rung we FORWARD FEWER FRAMES (drop droppable video before send) - the camera keeps
      capturing at native rate; only the SEND cadence changes.

  The transport never inspects the payload (media_stream MediaFrame.payload is "RAW
  bytes - what rides FNWP-1 as TYPE_RAW"; InPlane/queue "make NO claim about what they
  are"). So the existing _pack_raw(seq,ts,key,audio,video) body rides opaque inside the
  frame payload, unchanged end to end. We are changing HOW bytes are carried, not WHAT.

STATUS: IN-FLIGHT. Built against the real signatures (read from source 2026-06-16):
  MediaFrame(seq, kind, payload, level_idx); kind in {AUDIO_PROTECTED, VIDEO_DROPPABLE}
  TupleControl.bearer() -> Optional[int]; MediaProducer(control, open_tx, ladder, ...)
  MediaProducer.send_level() -> rung capped by bearer; .send(MediaFrame)
  MediaSocketSender(host, port, maxq, on_drop).send(bytes, is_key); .depth(); .close()
  MediaSocketReceiver(host, port, on_frame).start()
Not box-proven yet; the cross-LAN sim oracle gates it (see test_call_media_oracle.py).
"""
from __future__ import annotations

import struct
import threading
import time
from typing import Callable, Optional

# Canonical stack - imported lazily-tolerant so this module can be unit-probed even
# where the full bundle isn't on the path (the oracle injects fakes).
try:
    from media_stream import (
        MediaFrame, MediaProducer, TupleControl,
        AUDIO_PROTECTED, VIDEO_DROPPABLE,
    )
except Exception:  # pragma: no cover - exercised only outside the bundle
    MediaFrame = MediaProducer = TupleControl = None
    AUDIO_PROTECTED, VIDEO_DROPPABLE = "AUDIO_PROTECTED", "VIDEO_DROPPABLE"

try:
    from sotf_media_backing import MediaSocketSender, MediaSocketReceiver
except Exception:  # pragma: no cover
    MediaSocketSender = MediaSocketReceiver = None

try:
    import sotf_ladder
except Exception:  # pragma: no cover
    sotf_ladder = None


# -- the opaque payload boundary ----------------------------------------------
# Same body the blocking engine packed; carried opaque inside MediaFrame.payload.
# Kept byte-identical so the far side's existing _unpack_raw still applies.
_RAW_HDR = struct.Struct("!IQBII")     # seq, ts, key, alen, vlen  (matches frognet_communicator)


def pack_av(seq: int, ts: int, key: bool, audio: bytes, video: bytes) -> bytes:
    return (_RAW_HDR.pack(seq, ts, 1 if key else 0, len(audio), len(video))
            + audio + video)


def unpack_av(payload: bytes):
    seq, ts, key, alen, vlen = _RAW_HDR.unpack_from(payload, 0)
    off = _RAW_HDR.size
    audio = payload[off:off + alen]; off += alen
    video = payload[off:off + vlen]
    return seq, ts, bool(key), audio, video


# -- open_tx factory: adapt MediaProducer's frame send onto MediaSocketSender --
def make_open_tx(on_drop: Optional[Callable[[], None]] = None,
                 maxq: int = 120,
                 _sender_cls=None):
    """Return an `open_tx(addr, port) -> send(MediaFrame)` factory for MediaProducer.

    MediaSocketSender ships raw `bytes` and sheds the oldest droppable frame when the
    queue fills; its depth() is the predictive backlog the ladder reads. We adapt the
    producer's MediaFrame onto it: keyframe-ness drives the queue's keep-keyframes shed.
    """
    cls = _sender_cls or MediaSocketSender

    def open_tx(addr: str, port: int):
        sender = cls(addr, port, maxq=maxq, on_drop=on_drop)

        def send(fr) -> None:
            # fr is a MediaFrame. Audio is protected (key-ish: never shed); video is
            # droppable. A VIDEO keyframe must survive the shed, so flag it is_key.
            is_key = (getattr(fr, "kind", None) == AUDIO_PROTECTED) or bool(
                getattr(fr, "is_key", False))
            sender.send(fr.payload, is_key=is_key)

        send._sender = sender   # exposed so the call can read depth()/close()
        return send

    return open_tx


# -- CallSender - capture/encode feeds this; it owns rung selection + send -----
class CallSender:
    """Wraps a MediaProducer for one outbound call leg. Capture/encode calls
    `feed_video`/`feed_audio`; this decides, per the ladder + the server's bearer,
    whether to forward this frame and at what rung. Lower rung => forward fewer
    droppable (video) frames. The camera is untouched; only send cadence changes."""

    def __init__(self, control, node: str = "producer",
                 have_camera: bool = True, have_mic: bool = True,
                 on_drop: Optional[Callable[[], None]] = None,
                 _producer_cls=None, _open_tx=None):
        self._seq = 0
        self._depth_drops = 0
        self.have_camera = have_camera
        self.have_mic = have_mic
        prod_cls = _producer_cls or MediaProducer
        open_tx = _open_tx or make_open_tx(on_drop=self._count_drop)
        self._ext_on_drop = on_drop
        self.producer = prod_cls(control, open_tx, ladder=sotf_ladder,
                                 have_camera=have_camera, have_mic=have_mic, node=node)
        self._tx = None

    def _count_drop(self):
        self._depth_drops += 1
        if self._ext_on_drop:
            self._ext_on_drop()

    def connect(self, **params):
        info = self.producer.create_and_connect(**params)
        # the open_tx send closure carries the live MediaSocketSender for depth()
        self._tx = getattr(self.producer, "_send", None)
        return info

    def send_level(self) -> int:
        return self.producer.send_level()

    def depth(self) -> int:
        s = getattr(self._tx, "_sender", None)
        return s.depth() if s is not None else 0

    def feed_audio(self, ts: int, audio: bytes) -> None:
        # Audio is CONTINUOUS/protected - always forwarded, never shed by rung.
        if not audio:
            return
        self._seq += 1
        fr = MediaFrame(seq=self._seq, kind=AUDIO_PROTECTED,
                        payload=pack_av(self._seq, ts, False, audio, b""),
                        level_idx=self.send_level())
        self.producer.send(fr)

    def feed_video(self, ts: int, key: bool, video: bytes) -> None:
        # Video is DROPPABLE. At a rung below the video floor, do not forward video at
        # all (audio-only rungs L2/L3). Otherwise forward; the queue sheds under backlog
        # and the bearer will have already stepped the rung down to reduce inflow.
        if not video:
            return
        lvl = self.send_level()
        if sotf_ladder is not None and not _rung_carries_video(lvl):
            return                      # rung dropped below video - send fewer frames
        self._seq += 1
        fr = MediaFrame(seq=self._seq, kind=VIDEO_DROPPABLE,
                        payload=pack_av(self._seq, ts, key, b"", video),
                        level_idx=lvl)
        setattr(fr, "is_key", bool(key))
        self.producer.send(fr)

    def publish_metrics(self):
        self.producer.publish_metrics(own=True)

    def close(self):
        s = getattr(self._tx, "_sender", None)
        if s is not None:
            s.close()


def _rung_carries_video(level_idx: int) -> bool:
    """True if the rung carries video. Mirrors sotf_ladder LEVELS (L4..L6 video).
    Falls back to >=4 if the ladder's table isn't importable."""
    try:
        lv = sotf_ladder.BY_IDX.get(level_idx)
        if lv is not None and "video" in lv:
            return bool(lv["video"])
    except Exception:
        pass
    return level_idx >= 4


# -- CallReceiver - rx feeds decoded frames to the tiles ----------------------
class CallReceiver:
    """Wraps a consumer rx for one inbound leg. The server fans MediaFrames to us;
    we unpack the opaque AV body and hand (audio, video) to the render sink. Uses
    MediaSocketReceiver (server side) or an injected open_rx for the client side."""

    def __init__(self, on_av: Callable[[int, int, bool, bytes, bytes], None]):
        self.on_av = on_av
        self._recv = None

    def _on_frame_bytes(self, payload: bytes) -> None:
        try:
            seq, ts, key, audio, video = unpack_av(payload)
        except Exception:
            return
        self.on_av(seq, ts, key, audio, video)

    def open(self, addr: str, port: int, _receiver_cls=None):
        cls = _receiver_cls or MediaSocketReceiver
        self._recv = cls(addr, port, on_frame=self._on_frame_bytes)
        self._recv.start()
        return self._recv

    def close(self):
        r = self._recv
        if r is not None and hasattr(r, "close"):
            try:
                r.close()
            except Exception:
                pass
