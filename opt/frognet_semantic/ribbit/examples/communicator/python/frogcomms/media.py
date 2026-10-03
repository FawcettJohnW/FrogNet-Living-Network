# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""Video: the size ladder, VP8 through PyAV with the same settings as the C++ encoder, and picture sources."""
import threading, time
from dataclasses import dataclass
from fractions import Fraction

import av
import numpy as np

from . import fnav as F


@dataclass(frozen=True)
class Size:
    w: int
    h: int
    bitrate: int
    gray: bool
    level: int


# index 0 = largest; the bottom four are 4:3 and gray, as fnav does below L5 (src/media/transcode.cpp)
SIZES = [Size(1920, 1080, 3000000, False, 8), Size(1280, 720, 1200000, False, 7), Size(854, 480, 600000, False, 6),
         Size(640, 360, 300000, True, 5), Size(480, 360, 250000, True, 5), Size(320, 240, 150000, True, 5),
         Size(160, 120, 60000, True, 5)]


def size_for_rung(rung, src_w, src_h):
    if rung < 5:
        return -1                                   # L4 and below: no video
    i = 8 - rung
    while i < 3 and (SIZES[i].w > src_w or SIZES[i].h > src_h):
        i += 1
    return i


def ceiling_for(w, h):                              # [EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1]
    for r in range(8, 4, -1):
        if SIZES[8 - r].w <= w and SIZES[8 - r].h <= h:
            return r
    return 5


class Encoder:
    """VP8 at one size: realtime, cpu-used 8, no lag, a keyframe at least every 2 s, the budget enforced by VBV
    ([VBV_OR_THE_BUDGET_IS_A_WISH_V1]: maxrate = bitrate, bufsize = bitrate / 2)."""

    def __init__(self, size, fps):
        self.size, self.n = size, 0
        c = self.ctx = av.CodecContext.create("libvpx", "w")
        c.width, c.height, c.pix_fmt = size.w, size.h, "yuv420p"
        c.bit_rate = size.bitrate
        c.framerate = fps
        c.time_base = Fraction(1, fps)
        c.gop_size = fps * 2                         # [KEYFRAME_INTERVAL_IS_BOUNDED_V1]
        c.options = {"deadline": "realtime", "cpu-used": "8", "lag-in-frames": "0",
                     "maxrate": str(size.bitrate), "bufsize": str(size.bitrate // 2)}
        c.open()

    def encode(self, frame, force_key):
        """frame: an av.VideoFrame of any size; returns VIDEO payloads ([level][codec][packet])."""
        f = frame.reformat(width=self.size.w, height=self.size.h, format="yuv420p")
        if self.size.gray:                            # [SMALLEST_WIRE_WINS_V1] flat chroma at the bottom sizes
            a = f.to_ndarray()
            a[self.size.h:] = 128
            f = av.VideoFrame.from_ndarray(a, format="yuv420p")
        f.pts = self.n; self.n += 1
        if force_key:                                 # [KEYFRAME_ON_REQUEST_V1] advisory: the packet says
            f.pict_type = av.video.frame.PictureType.I
        return [F.pack_video(self.size.level, bytes(p), F.CODEC_VP8, p.is_keyframe) for p in self.ctx.encode(f)]


class Decoder:
    def __init__(self):
        self.ctx = av.CodecContext.create("vp8", "r")

    def decode(self, packet):
        """av.VideoFrames decoded from one VP8 packet."""
        return self.ctx.decode(av.Packet(packet))


# -- picture sources: next() -> av.VideoFrame ------------------------------------------------------------------
class SyntheticSource:
    """A moving shape on a gradient (the C++ SyntheticSource's role): a picture with no camera."""

    def __init__(self, w=1280, h=720):
        self.w, self.h, self.n = w, h, 0
        y, x = np.mgrid[0:h, 0:w]
        self.base = ((x * 60 // w) + (y * 40 // h) + 40).astype(np.uint8)

    def next(self):
        self.n += 1
        a = np.empty((self.h * 3 // 2, self.w), np.uint8)
        a[:self.h] = self.base
        cx, cy, r = int(self.w * (0.3 + 0.4 * ((self.n % 240) / 240))), self.h // 2, self.h // 8
        y, x = np.ogrid[0:self.h, 0:self.w]
        a[:self.h][(x - cx) ** 2 + (y - cy) ** 2 <= r * r] = 200
        a[self.h:] = 128
        return av.VideoFrame.from_ndarray(a, format="yuv420p")


class AvSource:
    """A video file (in order, looped) or a camera (v4l2 / dshow). A camera is LIVE: its own thread decodes every
    frame and keeps only the newest ([A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]: reading in order let a backlog,
    seconds of delay, build up whenever the camera outran the sender)."""

    def __init__(self, kind, url, w, h, fps):
        self.w, self.h, self.kind = w, h, kind
        self.live = kind in ("camera", "live-file")
        opts = {}
        fmt = None
        if kind == "camera":
            import sys
            fmt = "dshow" if sys.platform == "win32" else "v4l2"
            opts = {"video_size": "%dx%d" % (w, h), "framerate": str(fps)}
            opts["vcodec" if sys.platform == "win32" else "input_format"] = "mjpeg"   # [ASK_THE_CAMERA_FOR_MJPEG_V1]
        self.c = av.open(url, format=fmt, options=opts)
        self.stream = self.c.streams.video[0]
        print("[source] %s %s: %s %dx%d -> sent as %dx%d" % ("camera" if kind == "camera" else "file", url,
              self.stream.codec_context.name, self.stream.codec_context.width, self.stream.codec_context.height, w, h), flush=True)
        self._newest, self._seq, self._taken = None, 0, 0
        self.superseded, self._stop = 0, False
        self._cv = threading.Condition()
        self._frames = self._decode_forever()
        if self.live:
            self._t = threading.Thread(target=self._capture, daemon=True); self._t.start()

    def _decode_forever(self):
        while True:
            for f in self.c.decode(self.stream):
                yield f
            if self.kind == "camera":
                return
            self.c.seek(0)                           # a file loops

    def _capture(self):
        for f in self._frames:
            if self._stop:
                return
            with self._cv:
                if self._seq > self._taken:
                    self.superseded += 1
                self._newest, self._seq = f, self._seq + 1
                self._cv.notify()
        print("[source] the camera stopped delivering frames", flush=True)

    def next(self):
        if not self.live:
            return next(self._frames).reformat(width=self.w, height=self.h, format="yuv420p")
        with self._cv:
            self._cv.wait_for(lambda: self._seq > self._taken, 0.2)
            if self._newest is None:
                return None
            self._taken = self._seq
            return self._newest.reformat(width=self.w, height=self.h, format="yuv420p")

    def close(self):
        self._stop = True
        self.c.close()
