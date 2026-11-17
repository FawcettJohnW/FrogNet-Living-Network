# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# pysoft.py: the Python Homa driver's socket, two of them in one process, on whatever answers IPPROTO_HOMA here.
import sys, time, threading
sys.path.insert(0, '/home/claude/src/frognet/opt/frognet_semantic')
from core import homa_socket as h
srv = h.HomaSocket(server_port=4555); cli = h.HomaSocket()
def serve():
    while True:
        m = srv.recv()
        srv.send_reply(m.peer, m.id, m.payload[::-1])
threading.Thread(target=serve, daemon=True).start()
for size in (10, 1000, 60000, 300000, 999000):
    body = bytes((i * 7) & 255 for i in range(size)); ts = []
    for n in range(20):
        t = time.time(); rid = cli.send_request(("127.0.0.1", 4555), body, n); m = cli.recv(); ts.append(time.time() - t)
        assert m.id == rid and m.cookie == n and m.payload == body[::-1], (size, n, m.id, rid, m.cookie, len(m.payload))
    ts.sort(); print("size %7d: 20 round trips exact, median %.2f ms" % (size, ts[10] * 1000), flush=True)
