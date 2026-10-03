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
"""Oracle: participants' streams stay SEPARATE on the one downlink (labeled by source),
so a client can render/mute each independently. Catches regression of the source-label
multiplexer (mix-down or unlabeled fan would fail this)."""
import threading, time, socket, struct, sys
import media_stream as M, frognet_mediahost_server as S
from call_media import pack_av_src, CallReceiver
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
    backend=Mem()
    M.TupleControl(backend,"C1",addr="10.0.0.5").request_create(session_id="C1",codec="vp8")
    srv=S.MediaHostServer(addr="127.0.0.1"); srv._backend=lambda: backend
    threading.Thread(target=srv.run,kwargs={"interval":0.2},daemon=True).start(); time.sleep(0.5)
    ci=M.TupleControl(backend,"C1").conn_info(); tx,rx=ci["tx"],ci["rx"]
    bysrc={}
    def on_av(src,seq,ts,key,audio,video):
        bysrc.setdefault(src,{"a":0,"v":0})
        if audio: bysrc[src]["a"]+=1
        if video: bysrc[src]["v"]+=1
    rxc=CallReceiver(on_av); rxc.open(ci["addr"],rx); time.sleep(0.4)
    _LEN=struct.Struct("!I")
    def prod(name, vid):
        s=socket.create_connection((ci["addr"],tx))
        for i in range(6):
            f=pack_av_src(name,i,i*33,i==0,b"AUD",(b"VID" if vid else b""))
            s.sendall(_LEN.pack(len(f))+f); time.sleep(0.02)
        time.sleep(0.3); s.close()
    ts=[threading.Thread(target=prod,args=a) for a in (("alice",True),("bob",False))]
    for t in ts: t.start()
    for t in ts: t.join()
    time.sleep(0.5); srv.close()
    ok = (bysrc.get("alice",{}).get("v",0)>=4 and bysrc.get("alice",{}).get("a",0)>=4
          and bysrc.get("bob",{}).get("a",0)>=4 and bysrc.get("bob",{}).get("v",0)==0)
    if not ok:
        print("FAIL: sources not kept separate:", bysrc); print("ORACLE RED"); return 1
    print("PASS: streams stay separate by source (alice a+v, bob a only)")
    print("PASS: client can address/mute each participant independently")
    print("ORACLE GREEN"); return 0
if __name__=="__main__": sys.exit(run())
