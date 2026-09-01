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
"""Oracle: a LIVE streaming session must NOT be reaped when its one-shot create tuple
ages out of the discovery window. Catches the 30s mid-call reap -> EADDRINUSE thrash."""
import threading, time, socket, struct, sys
import media_stream as M, frognet_mediahost_server as S
from call_media import pack_av, CallReceiver
S.REAP_IDLE_GRACE_S = 1.0
class Mem:
    def __init__(s): s.rows=[]; s.l=threading.Lock()
    def put(s,svc,var,scope,value,addr=None):
        with s.l:
            s.rows=[r for r in s.rows if not(r["s"]==svc and r["v"]==var and r["sc"]==scope)]
            s.rows.append({"s":svc,"v":var,"sc":scope,"val":value,"ts":time.time()})
    def get_one(s,svc,var,scope,fresh_s=0):
        with s.l:
            for r in s.rows:
                if r["s"]==svc and r["v"]==var and r["sc"]==scope: return r["val"]
        return None
    def get(s,svc,var,fresh_s=0):
        now=time.time()
        with s.l:
            return [{"scope":r["sc"],"value":r["val"]} for r in s.rows
                    if r["s"]==svc and r["v"]==var and (fresh_s==0 or now-r["ts"]<=fresh_s)]
def run():
    backend=Mem()
    M.TupleControl(backend,"C1",addr="10.0.0.5").request_create(session_id="C1",codec="vp8")
    srv=S.MediaHostServer(addr="127.0.0.1"); srv._backend=lambda: backend
    threading.Thread(target=srv.run,kwargs={"interval":0.2},daemon=True).start(); time.sleep(0.4)
    ci=M.TupleControl(backend,"C1").conn_info(); tx,rx=ci["tx"],ci["rx"]
    gv=[]
    rxc=CallReceiver(lambda seq,ts,key,a,v: gv.append(v) if v else None)
    rxc.open(ci["addr"], rx); time.sleep(0.3)
    _LEN=struct.Struct("!I"); up=socket.create_connection((ci["addr"],tx))
    with backend.l:
        for r in backend.rows:
            if r["v"]=="create": r["ts"] -= 100      # age the create tuple out
    broke=False
    for i in range(40):
        f=pack_av(i,i*33,i==0,b"A",f"V{i}".encode())
        try: up.sendall(_LEN.pack(len(f))+f)
        except OSError: broke=True; break
        time.sleep(0.05)
    time.sleep(0.4)
    alive="C1" in srv.sessions
    srv.close(); up.close()
    if broke or not alive or len(gv)<20:
        print(f"FAIL: broke={broke} alive={alive} video={len(gv)}"); print("ORACLE RED"); return 1
    print("PASS: live session survived create-tuple staleness (no mid-call reap)")
    print(f"PASS: {len(gv)} frames delivered through the aged-out window")
    print("ORACLE GREEN"); return 0
if __name__=="__main__": sys.exit(run())
