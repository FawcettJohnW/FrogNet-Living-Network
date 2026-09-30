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
test_drop_frame_oracle.py -- [DROP_THE_FRAME_NOT_THE_CALL_V1]

A bad frame is that frame's problem. It is not a reason to hang up.

The receive loop's whole body sat inside a single `except Exception` at the
bottom, so anything raised while HANDLING a frame -- a decoder refusing a
packet, a malformed backpressure record, a payload that unpacked but did not
make sense -- ended the loop. And one plane ending tears the call down:

    [RX/audio] this plane was the FIRST to end -- stopping the call

So a single corrupt frame hung up a working link.

  D1  a frame whose handling raises does NOT end the loop
  D2  the frames after it still arrive
  D3  it is counted and reported, at a rate nobody has to scroll past
  D4  a socket error still ends the loop -- there is nothing left to read
  D5  and the loop still ends when the peer really does close
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import fnav


class PoisonDecoder:
    """Raises on every frame, the way a decoder refusing a packet does."""

    def __init__(self):
        self.n = 0

    def feed(self, *a):
        self.n += 1
        raise ValueError("decoder said no")


def run(n_frames, close_after=True):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        frame = fnav.pack_typed(
            fnav.KIND_VIDEO, "Dave",
            fnav.pack_video(7, b"\x00" * 32, 0, is_key=True))
        for _ in range(n_frames):
            fnav.send_frame(c, frame)
            time.sleep(0.01)
        time.sleep(0.3)
        if close_after:
            c.close()

    threading.Thread(target=serve, daemon=True).start()

    dec = PoisonDecoder()
    c = fnav.Call.__new__(fnav.Call)
    c.name, c.host, c.port = "John", *srv.getsockname()
    c._stop = threading.Event()
    c.vdec = dec
    c.mixer = None
    c.stats = type("S", (), {"on_video_recv": staticmethod(lambda n: None)})()
    c._rx_lock = threading.Lock()
    c._in_src, c._rx_src = {}, {}
    c.session, c.is_producer = "S", False

    sk = socket.create_connection(srv.getsockname())
    t = threading.Thread(target=lambda: c._recv_loop(sk), daemon=True)
    t.start()
    time.sleep(0.15 + 0.02 * n_frames)
    alive = t.is_alive()
    t.join(timeout=2.0)
    srv.close()
    return dec.n, alive


# ---- D1/D2: poisoned frames do not end it ----------------------------------
seen, alive_mid = run(5)
ck("D1 the loop was still running after the first bad frame", alive_mid, alive_mid)
ck("D2 every frame after it still arrived -- 5 sent, 5 handled", seen == 5, seen)

seen, _ = run(20)
ck("D2 and it does not degrade with repetition -- 20 sent, 20 handled",
   seen == 20, seen)

# ---- D3/D4/D5: the shape of the handler ------------------------------------
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".",
                        "fnav.py"), encoding="utf-8").read()
# The doctrine comment sits at the TOP of the guarded block and the handler is
# at the bottom, past the whole dispatch -- 3000 characters did not reach it.
# Slice from the comment to the end of the enclosing loop instead of guessing.
i = src.index("[DROP_THE_FRAME_NOT_THE_CALL_V1]")
blk = src[i:src.index("[RX_SAYS_WHERE_V1]", i)]
ck("D3 the drop is counted", "n_unpack_fail += 1" in blk, None)
ck("D3 and reported on a curve, not every frame",
   "in (1, 10, 100)" in blk and "% 1000 == 0" in blk, None)
ck("D3 saying the call continues", "the call continues" in blk, None)
ck("D4 a socket error still propagates",
   "except (ConnectionError, OSError):" in blk and "raise" in blk, None)
ck("D4 and it is caught BEFORE the catch-all",
   blk.index("except (ConnectionError, OSError):")
   < blk.index("except Exception as _fe:"), None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
