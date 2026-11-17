# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# movemem.py: the Homa socket on soft Homa, memory A then memory B, in one process (no engine)
import os, sys, threading, time
sys.path.insert(0, "/home/claude/src/frognet/opt/frognet_semantic")
from core import homa_socket as H
where = {"at": sys.argv[1]}
H.soft_memory_now = lambda: (where["at"], "10.77.77.2")
def pair(tag):
    srv = H.HomaSocket("10.77.77.1", 4020); cli = H.HomaSocket()
    def serve():
        try:
            while True:
                m = srv.recv()
                srv.send_reply(m.peer, m.id, b"re:" + m.payload)
        except H.CarrierClosed as e:
            print(tag, "server ended:", e, flush=True)
    threading.Thread(target=serve, daemon=True).start()
    cli.send_request(("10.77.77.1", 4020), b"hello " + tag.encode(), 7)
    try:
        m = cli.recv(); print(tag, "client got", m.payload, flush=True)
    except Exception as e:
        print(tag, "client failed:", type(e).__name__, e, flush=True)
    return srv, cli
a = pair("A")
where["at"] = sys.argv[2]
print("told:", H.soft_memory_tell("test move"), flush=True)
time.sleep(0.3)
for x in a: x.close()
b = pair("B")
time.sleep(0.3)
