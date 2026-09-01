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
test_transit_closure_repro.py - reproduce the NY-1-can't-reach-Seattle-chain bug
against the REAL broker (frognet_broker_v4), no hardware.

Models the real topology's transit semantics:
  guest chain  Seattle2 -> SeattleThree -> Seattle6 -> Seattle5 -> (tunnel) -> NY-1
Each node advertises transit for its DIRECT guests only (what the lease-based
discover_transit_subnets produces). Then we ask the real broker, via my-channels,
what NY-1 learns as remote_subnets for its Seattle5 tunnel. The bug: NY-1 never
learns 10.130.130 / 10.120.120 because the broker composes remote_subnets =
peer.owned + peer.transit (single hop), with NO transitive closure.
"""
import os, sys, asyncio, tempfile, types, secrets, importlib
BROKER_DIR = os.environ.get("FROGNET_BROKER_DIR",
                            "/home/claude/v25/opt/frognet_semantic/broker")

def _stub_web():
    import types as T
    he = type("HTTPException", (Exception,),
              {"__init__": lambda self, status_code=400, detail="":
               (setattr(self,"status_code",status_code),
                setattr(self,"detail",detail),
                Exception.__init__(self, f"{status_code}:{detail}"))[0] if False else None})
    class HTTPException(Exception):
        def __init__(self, status_code=400, detail=""):
            self.status_code=status_code; self.detail=detail
            super().__init__(f"{status_code}:{detail}")
    fa=T.ModuleType("fastapi")
    class FastAPI:
        def __init__(s,*a,**k): pass
        def _d(s,*a,**k):
            def w(fn): return fn
            return w
        get=post=patch=delete=put=on_event=_d
        def add_middleware(s,*a,**k): pass
    fa.FastAPI=FastAPI; fa.Header=lambda default=None,*a,**k: default
    fa.HTTPException=HTTPException; fa.Request=object; fa.Response=object
    sys.modules["fastapi"]=fa
    fr=T.ModuleType("fastapi.responses")
    class JSONResponse:
        def __init__(s,content=None,status_code=200,**k):
            s.content=content; s.status_code=status_code
    fr.JSONResponse=JSONResponse; sys.modules["fastapi.responses"]=fr
    pd=T.ModuleType("pydantic")
    class BaseModel:
        def __init__(s,**kw):
            for k,v in kw.items(): setattr(s,k,v)
    pd.BaseModel=BaseModel; sys.modules["pydantic"]=pd
    uv=T.ModuleType("uvicorn"); uv.run=lambda *a,**k: None; sys.modules["uvicorn"]=uv

def load_broker():
    _stub_web()
    sys.path.insert(0, BROKER_DIR)
    b=importlib.import_module("frognet_broker_v4")
    from pathlib import Path
    b.DB_PATH=Path(tempfile.mkdtemp())/"b.db"
    ns=types.SimpleNamespace()
    for m in ("ensure_namespace","create_wg_interface","add_wg_peer","add_route",
              "setup_port_forward","teardown_port_forward","delete_wg_interface"):
        setattr(ns,m,lambda *a,**k: None)
    ns._run=lambda *a,**k:""; ns._veth_ips=lambda *a,**k:("10.255.255.1","10.255.255.2")
    ns.get_public_ip=lambda:"203.0.113.9"
    ns._wg_genkey=lambda:(secrets.token_hex(16),secrets.token_hex(16))
    b.ns=ns; b._check_admin_token=lambda *a,**k:None
    sp=types.SimpleNamespace()
    sp.run=lambda *a,**k: types.SimpleNamespace(returncode=0,stdout="",stderr="")
    sp.PIPE=sp.DEVNULL=None; sp.CalledProcessError=Exception
    b.subprocess=sp; b.init_db()
    return b

def main():
    b=load_broker()
    POND="ratpond"
    asyncio.run(b.create_pond(b.PondCreate(name=POND,node_base="10.0.0.1"),
                              authorization="admin"))
    chain=[("Seattle2","10.120.120"),("SeattleThree","10.130.130"),
           ("Seattle6","10.160.160"),("Seattle5","10.250.250"),
           ("New-York-1","10.102.60")]
    pk={}
    for name,s3 in chain:
        pk[name]=f"PK-{name}-{secrets.token_hex(3)}"
        asyncio.run(b.register_node(b.RegisterRequest(
            pond=POND,pubkey=pk[name],subnet=f"{s3}.0/24",node_name=name,mac=f"M-{name}")))
    db=b.get_db()
    for name in pk: b._bump_last_seen(db, b._get_node_by_pubkey(db,pk[name])["id"])
    db.commit()

    # SINGLE-HOP transit advertisement (what the lease method yields): each node
    # advertises only the /24 of the DIRECT guest one hop downstream.
    #   Seattle5 serves Six           -> transit 10.160.160
    #   Seattle6 serves Three         -> transit 10.130.130
    #   SeattleThree serves Two       -> transit 10.120.120
    async def adv(name, subs):
        await b.update_subnets(b.UpdateSubnetsRequest(pubkey=pk[name], transit_subnets=subs))
    asyncio.run(adv("Seattle5",     ["10.160.160.0/24"]))
    asyncio.run(adv("Seattle6",     ["10.130.130.0/24"]))
    asyncio.run(adv("SeattleThree", ["10.120.120.0/24"]))

    # What does NY-1 learn for its Seattle5 tunnel?
    db=b.get_db()
    ny=b._get_node_by_pubkey(db,pk["New-York-1"])
    chans=b._compute_aggregate(db, ny)
    s5=[c for c in chans if c["node_name"]=="Seattle5"]
    learned=set()
    for c in s5:
        learned |= set(c.get("wg_config",{}).get("remote_subnets",[]))
    print("NY-1 learns via Seattle5 tunnel remote_subnets =", sorted(learned))
    ok=True
    def check(c,m):
        nonlocal ok; ok=ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")
    check("10.250.250.0/24" in learned, "NY-1 learns Seattle5 owned 250")
    check("10.160.160.0/24" in learned, "NY-1 learns 160 (Seattle5's direct transit)")
    # THE BUG: these should be reachable but are NOT advertised to NY-1
    print("  --- the bug (these FAIL, proving single-hop transit can't chain) ---")
    check("10.130.130.0/24" in learned, "NY-1 learns 130 (two hops down)")
    check("10.120.120.0/24" in learned, "NY-1 learns 120 (three hops down)")
    return 0 if ok else 1

if __name__=="__main__":
    sys.exit(main())
