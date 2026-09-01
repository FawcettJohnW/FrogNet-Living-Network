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
"""[RECV_VIDEO_NOT_GATED_ON_TX_V1] oracle.

Regression: a receive-only client (--no-video, no camera) used to leave self.vdec
None because the spawn block gated decoder construction on `want_video` (a SEND-side
flag). Every inbound KIND_VIDEO frame then fell through _recv_loop's
`vdec is not None` test to SKIP -> recv stuck at 0, "connected, waiting for video".

This drives the REAL Call.run() setup path with the socket/threads neutralized and
asserts the decoder is built for a receive-only config.

OLD code: vdec stays None  -> FAIL
NEW code: vdec built always -> PASS
"""
import sys, threading, types

# sandbox has no PyAV; the real boxes do. VideoDecoder.__init__ only does
# `import av; self._av = av`, and we never decode here -- a bare module suffices.
# The codec PROBE (unconditional in __init__) does real encoding, so stub it.
_fake_av = types.ModuleType("av")
_fake_av.CodecContext = type("CodecContext", (), {"create": staticmethod(lambda *a, **k: None)})
sys.modules.setdefault("av", _fake_av)

import fnav

# neutralize the real-encode probe (needs av+numpy); irrelevant to the gate under test
fnav.VideoEncoder.probe_best_codec = staticmethod(lambda fps=24: fnav.CODEC_VP8)

FAILS = []
def check(cond, msg):
    print(("  ok  " if cond else "  FAIL") + "  " + msg)
    if not cond:
        FAILS.append(msg)


class _FakeSock:
    """Just enough socket surface for run() setup to pass through offline."""
    def __init__(self, *a, **k): pass
    def setsockopt(self, *a, **k): pass
    def connect(self, *a, **k): pass
    def settimeout(self, *a, **k): pass
    def sendall(self, *a, **k): pass
    def fileno(self): return -1
    def recv(self, *a, **k): return b""      # _recv_loop won't run: _stop preset
    def close(self, *a, **k): pass


def build_receive_only():
    """no_video=True (the viewer's flag), no_audio=True, no camera -> want_video False."""
    orig = fnav.socket.socket
    fnav.socket.socket = lambda *a, **k: _FakeSock()
    try:
        c = fnav.Call("10.0.0.1", 9000, "Seattle5",
                      no_video=True, no_audio=True, no_display=True)
        # short-circuit the post-setup idle loop so run() returns after spawning
        c._stop.set()
        c.run()
        return c
    finally:
        fnav.socket.socket = orig


def main():
    c = build_receive_only()
    # the contract: receiving video must not depend on sending it
    check(c.want_video is False, "receive-only config -> want_video is False (send disabled)")
    check(c.have_cam is False, "receive-only config -> have_cam is False (no TX)")
    check(c.vdec is not None,
          "receive-only client BUILDS a video decoder (vdec is not None)")

    # and the _recv_loop video branch is therefore live, not SKIP
    branch_live = (c.vdec is not None)
    check(branch_live,
          "_recv_loop KIND_VIDEO branch is reachable (vdec is not None) for a viewer")

    if FAILS:
        print(f"\nORACLE RED: {len(FAILS)} failure(s) -- receive-only video is gated off")
        sys.exit(1)
    print("\nORACLE GREEN: receive-only client decodes inbound video")


if __name__ == "__main__":
    main()
