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
"""Oracle: the downlink must FORM and DELIVER. Catches the inverted connect/listen
regression (server connecting out + client listening = downlink never forms, no error,
no media). Fails on old behavior, passes on new. Directive 12."""
import threading, time, socket, struct, sys
import media_stream as M, frognet_mediahost_server as S
from call_media import pack_av, CallReceiver

class Mem:
    def __init__(s): s.rows=[]; s.l=threading.Lock()
    def put(s,svc,var,scope,value,addr=None):
        with s.l:
            s.rows=[r for r in s.rows if not(r["s"]==svc and r["v"]==var and r["sc"]==scope)]
            s.rows.append({"s":svc,"v":var,"sc":scope,"val":value})
    def get_one(s,svc,var,scope,fresh_s=0):
        with s.l:
            for r in s.rows:
                if r["s"]==svc and r["v"]==var and r["sc"]==scope: return r["val"]
        return None
    def get(s,svc,var,fresh_s=0):
        with s.l: return [{"scope":r["sc"],"value":r["val"]} for r in s.rows if r["s"]==svc and r["v"]==var]

def run():
    fails=[]
    backend=Mem()
    ctl=M.TupleControl(backend,"CALL1",addr="10.0.0.5")
    ctl.request_create(session_id="CALL1",codec="vp8")
    srv=S.MediaHostServer(addr="127.0.0.1"); srv._backend=lambda: backend
    threading.Thread(target=srv.run,kwargs={"interval":0.1},daemon=True).start()
    time.sleep(0.5)
    ci=ctl.conn_info()
    if not ci: print("FAIL: no conn_info"); print("ORACLE RED"); return 1
    tx,rx=ci["tx"],ci["rx"]

    gv=[]; ga=[]
    rxc=CallReceiver(lambda seq,ts,key,a,v:(gv.append(v) if v else None, ga.append(a) if a else None))
    rxc.open("127.0.0.1", rx); time.sleep(0.4)

    # ORACLE A: the downlink CONNECTION FORMED (consumer registered at server)
    if srv.sessions["CALL1"]._dl_listener.count() < 1:
        fails.append("downlink never formed (consumer did not connect to server rx)")

    _LEN=struct.Struct("!I")
    up=socket.create_connection(("127.0.0.1",tx))
    for i in range(6):
        f=pack_av(i,i*33,i==0,f"A{i}".encode(),f"V{i}".encode())
        up.sendall(_LEN.pack(len(f))+f); time.sleep(0.03)
    time.sleep(0.8)

    # ORACLE B: media DELIVERED both tracks down the downlink
    if len(gv) < 4: fails.append(f"video did not flow to consumer ({len(gv)})")
    if len(ga) < 4: fails.append(f"audio did not flow to consumer ({len(ga)})")
    srv.close(); up.close()

    if fails:
        for f in fails: print("FAIL:", f)
        print("ORACLE RED"); return 1
    print("PASS: downlink connection formed (consumer dialed server rx, server listens+fans)")
    print("PASS: video delivered to consumer tile")
    print("PASS: audio delivered to consumer sink")
    print("ORACLE GREEN"); return 0

if __name__=="__main__":
    sys.exit(run())
