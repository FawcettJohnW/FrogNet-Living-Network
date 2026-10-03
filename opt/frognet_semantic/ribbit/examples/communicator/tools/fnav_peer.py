#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""fnav_peer.py FNAV_DIR PORT MODE -- an fnav.py peer for the C++ data-plane test, using fnav.py's own send_frame
and recv_frame.
  echo   receive frames and send each body straight back, until the connection closes
  stall  accept, read nothing for 0.35 s (longer than the sender's 250 ms to finish, shorter than finish plus
         padding), then read frames and report each: OK <len> or ABORTED"""
import socket, sys, time
sys.path.insert(0, sys.argv[1])
import fnav  # noqa: E402

port, mode = int(sys.argv[2]), sys.argv[3]
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
if mode == "stall":
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)   # a small window, so the sender's buffer fills
srv.bind(("127.0.0.1", port)); srv.listen(1)
print("READY", flush=True)
c, _ = srv.accept()
if mode == "echo":
    c.setblocking(False)
    n = 0
    try:
        while True:
            body = fnav.recv_frame(c)
            while True:
                try:
                    fnav.send_frame(c, body); break
                except BlockingIOError:
                    time.sleep(0.001)
            n += 1
    except (ConnectionError, OSError):
        pass
    print("ECHOED", n, flush=True)
else:
    time.sleep(0.35)
    c.setblocking(False)
    try:
        while True:
            try:
                body = fnav.recv_frame(c); print("OK", len(body), flush=True)
            except fnav.AbortedFrame:
                print("ABORTED", flush=True)
    except (ConnectionError, OSError):
        print("END", flush=True)
