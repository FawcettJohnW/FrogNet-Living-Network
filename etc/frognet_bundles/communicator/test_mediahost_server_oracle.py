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
"""Oracle for frognet_mediahost_server: the A/V media-host lifecycle.
Lifecycle: create-tuple -> per-session port -> conn_info in tuple -> read-from-tuple;
server reads ALL producer channels concurrently and fans to the single downlink output;
audio+video interleaved FNWP-1 and separable. Directive 12: catches regression.
"""
import threading, time, socket, struct, sys
import media_stream as M
from call_media import pack_av, CallReceiver
import frognet_mediahost_server as S

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
    ctl.request_create(session_id="CALL1",codec="vp8",sr=48000,layout="stereo",level_idx=7)
    srv=S.MediaHostServer(addr="127.0.0.1"); srv._backend=lambda: backend
    threading.Thread(target=srv.run,kwargs={"interval":0.1},daemon=True).start()
    time.sleep(0.5)

    # ORACLE 1: conn_info published to tuple with a per-session A/V port (read FROM TUPLE)
    ci=ctl.conn_info()
    if not ci: fails.append("conn_info not published to tuple")
    elif ci["tx"]<S.AV_PORT_BASE: fails.append("conn_info tx not a per-session A/V port")

    if ci:
        tx,rx=ci["tx"],ci["rx"]
        # consumer connects to the server rx (the real downlink)
        got=[]
        rxc=CallReceiver(lambda seq,ts,key,a,v: got.append((a,v)))
        rxc.open("127.0.0.1", rx); time.sleep(0.4)

        # ORACLE 2: TWO concurrent producers, server reads BOTH channels
        _LEN=struct.Struct("!I")
        def prod(name,n):
            s=socket.create_connection(("127.0.0.1",tx))
            for i in range(n):
                f=pack_av(i,i*33,i==0,f"{name}A{i}".encode(),f"{name}V{i}".encode())
                s.sendall(_LEN.pack(len(f))+f); time.sleep(0.02)
            time.sleep(0.2); s.close()
        threads=[threading.Thread(target=prod,args=(n,4)) for n in ("alice","bob")]
        for t in threads: t.start()
        for t in threads: t.join()
        time.sleep(0.8)

        names={(a or v).decode().split('A')[0].split('V')[0] for a,v in got}
        if not ({"alice","bob"} <= names):
            fails.append(f"server did not fan BOTH producers to the one output: {names}")
        # ORACLE 3: audio+video interleaved & separable; ORACLE 4: single output got frames
        if not all((a and v) for a,v in got):
            fails.append("audio/video not interleaved+separable at output")
        if len(got) < 8:
            fails.append(f"not all frames reached the single output: {len(got)}")
    srv.close()

    if fails:
        for f in fails: print("FAIL:", f)
        print("ORACLE RED"); return 1
    print("PASS: create-tuple -> per-session port -> conn_info in tuple -> read-from-tuple")
    print("PASS: server read BOTH producer channels, fanned to one downlink output")
    print("PASS: audio+video interleaved FNWP-1, separable at output")
    print("PASS: single output delivered all frames")
    print("ORACLE GREEN"); return 0

if __name__=="__main__":
    sys.exit(run())
