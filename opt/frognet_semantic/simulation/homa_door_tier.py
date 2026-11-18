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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
homa_door_tier.py -- the simulator's door tier (door_semantic_tier.py: an app's calls to a FrogNet door through the
REAL proxy and the REAL daemon, each node on its own template store) with the one thing that tier substitutes, the
socket hop, replaced by the REAL Homa driver on soft Homa.

door_semantic_tier hands each frame straight to the daemon's session. Here the proxy's engine is left its own
_get_worker, the node is told `softhoma`, and every frame crosses proxy/transport_homa.py -> soft Homa (the Homa API
interface the driver opens itself, on a memory process this tier starts) -> daemon/engine/server_homa.py. The door,
the scenarios and the checks are door_semantic_tier's, unchanged: every answer must equal the door's and the door must
be given exactly what the app sent. In addition every scenario must have run on soft Homa, on a session of its own,
with no TCP worker made.

Stood in for: where soft Homa's memory is (on a node, the machine databasehost.frognet names; here the memory this
tier starts) and the two ends' liveness (the ControlRam's capability tuples, which this tier has no network for).

Needs soft Homa built (make -C ribbit/examples/homa HOMA=<HomaModule checkout>) and what door_semantic_tier needs
(MariaDB, the simulator's database and that name + "7"). Without them it FAILS and names what is missing.
"""
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILT = os.path.join(ROOT, "ribbit", "examples", "homa", "build")
LIB, RAM = os.path.join(BUILT, "libsofthoma.so"), os.path.join(BUILT, "softhoma-ram")
PORT = int(os.environ.get("HOMA_DOOR_TIER_MEMORY_PORT", "9171"))
MEMORY = "127.0.0.1:%d" % PORT
ORIGIN, ASKING = "10.77.77.1", "10.77.77.2"                     # the two nodes, by FrogNet addresses (names in the memory)


class _Liveness:
    """The calls the driver makes on its liveness, answering nothing: no peer leaves in this tier."""
    on_hosts_change = None

    def watch(self, ip): pass
    def withdraw(self): pass
    def carriers_of(self, ip): return None
    def stats(self): return {}


def main():
    # [WE_OWN_THE_WIRE_V1] soft Homa is Homa's frames on a TCP connection the driver holds open (core/homa_frames.py):
    # nothing is built, loaded or started for it
    os.environ["FROGNET_SEM_TRANSPORT"] = "softhoma"
    os.environ.pop("LD_PRELOAD", None)
    sys.path.insert(0, HERE)
    import door_semantic_tier as D
    ran = []                                                    # per scenario: what the driver carried

    def hop(TS, origin, origin_store):
        from core import homa_frames as HF, homa_transport as HT
        from proxy import transport_homa as TH
        from daemon.engine import server_homa as SH
        class OnThisHost(HF.HomaFramesOnTcp):                   # STOOD IN FOR: the origin node's address. One host is both
            def send_request(self, dest, payload, cookie):      # nodes here, so the connection to 10.77.77.1 is made to
                return super().send_request(("127.0.0.1", dest[1]), payload, cookie)     # this host; the frames are the same
        TH._client_carrier = lambda: OnThisHost()
        TH._peer_liveness = lambda on_gone, on_event: _Liveness()
        tcp = []
        init = TS._DaemonWorker.__init__
        TS._DaemonWorker.__init__ = lambda self, *a, **k: (tcp.append(a), init(self, *a, **k))[1]
        session_init = SH._MsgSession.__init__

        def with_origin_store(self, *a, **k):                   # the origin node's daemon reads the origin node's store
            session_init(self, *a, **k)
            self.engine.loader._store = origin_store
        SH._MsgSession.__init__ = with_origin_store
        srv = SH.MsgDaemonServer(ORIGIN, TS._DAEMON_PORT, None)
        srv._http_client = origin
        srv._peer_liveness = lambda: _Liveness()
        srv._server_carrier = lambda: HF.HomaFramesOnTcp(bind_ip="127.0.0.1", server_port=TS._DAEMON_PORT)
        srv._writer_gone = lambda ip: None
        threading.Thread(target=srv.run, daemon=True, name="homa-door-daemon").start()
        t_end = time.time() + 20
        while srv._carrier is None and time.time() < t_end:    # the listener thread opens its port
            time.sleep(0.02)
        if srv._carrier is None:
            raise RuntimeError("the daemon's Homa listener did not open on %s" % ORIGIN)

        def end():
            mux = TH._get_mux()
            w = [x.stats() for x in TS._WORKERS.values()]
            ran.append({"soft": HT._soft_homa_loaded(), "carrier": type(mux.carrier).__name__,
                        "frames": sum(x.get("sent", 0) for x in w), "replies": sum(x.get("replies", 0) for x in w),
                        "sessions": len(srv._sessions), "homa_requests": mux.carrier.n_req_out,
                        "homa_replies": mux.carrier.n_rep_in, "tcp_workers": len(tcp),
                        "preloaded": "LD_PRELOAD" in os.environ})
            srv.stop()
            mux.carrier.close()
            SH._MsgSession.__init__ = session_init
            TS._DaemonWorker.__init__ = init
        return end

    D.SOCKET_HOP = hop
    D.TARGET_IP = ORIGIN
    try:
        fails = D.run(ROOT)
    finally:
        pass
    for name, r in zip([n for n, _s in D.SCENARIOS], ran):
        ok = (r["soft"] and r["carrier"] == "OnThisHost" and r["frames"] > 0 and r["frames"] == r["replies"]
              and r["homa_requests"] >= r["frames"] and r["sessions"] == 1 and r["tcp_workers"] == 0 and not r["preloaded"])
        print(" %s  %s: on soft Homa %s, %d frames and %d replies in %d Homa requests on %d session, %d TCP workers"
              % ("ok   " if ok else "WRONG", name, r["soft"], r["frames"], r["replies"], r["homa_requests"], r["sessions"],
                 r["tcp_workers"]))
        if not ok:
            fails.append("%s: it did not run as one session on soft Homa: %s" % (name, r))
    if len(ran) != len(D.SCENARIOS):
        fails.append("%d of %d scenarios reached the end of their run" % (len(ran), len(D.SCENARIOS)))
    print("RESULT", "PASS" if not fails else "FAIL: %d wrong" % len(fails))
    for f in fails:
        print("   " + f[:300])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
