# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# pyclose.py: a server socket closes with a request outstanding; the requester is told. Then the port is bound again.
import sys, time, threading
sys.path.insert(0, '/home/claude/src/frognet/opt/frognet_semantic')
from core import homa_socket as h
srv = h.HomaSocket(bind_ip="127.0.0.9", server_port=4556); cli = h.HomaSocket()
rid = cli.send_request(("127.0.0.9", 4556), b"x" * 100, 7); m = srv.recv(); print("server got", len(m.payload), "from", m.peer, flush=True)
t = time.time(); srv.close()
try:
    r = cli.recv(); print("UNEXPECTED reply", r)
except h.RpcFailed as e:
    print("requester told in %.1f ms: id ok=%s cookie=%s errno=%s" % ((time.time() - t) * 1000, e.rpc_id == rid, e.cookie, e.errno), flush=True)
srv2 = h.HomaSocket(bind_ip="127.0.0.9", server_port=4556)
rid = cli.send_request(("127.0.0.9", 4556), b"y" * 10, 8); m = srv2.recv(); srv2.send_reply(m.peer, m.id, b"ok"); print("after rebind:", cli.recv().payload, flush=True)
try:
    h.HomaSocket(bind_ip="127.0.0.9", server_port=4556); print("UNEXPECTED second bind succeeded")
except Exception as e:
    print("second bind of a live port:", str(e)[:90], flush=True)
