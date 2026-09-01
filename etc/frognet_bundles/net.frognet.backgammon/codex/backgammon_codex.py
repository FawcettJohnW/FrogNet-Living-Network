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
backgammon_codex.py -- UnREST codex + authoritative engine for Family Backgammon.

Build-native (Tier 1): no OSS package to wrap -- public-domain rules. The substrate is
the backend; this file is the rules + the codex. The whole game is ONE shared element
(`game.backgammon.<id>`); a move rewrites it; the codec sends each reader only their
delta. Authority = perm (a game is resumable; it survives host re-election). The element
carries the computed `legal` moves so the UI highlights from authoritative data and
never re-implements the rules.

Board model:
  points: list index 1..24 (0 unused), signed int. +n = n WHITE checkers, -n = n BLACK.
  WHITE moves 24 -> 1, bears off below 1, home = points 1..6.
  BLACK moves 1 -> 24, bears off above 24, home = points 19..24.
  bar/off: counts per color.
Verbs: new_game, roll, move, can_move(internal), offer_double, accept_double,
decline_double, presence. Stores injected (api.php transient + perm on the box).
"""
from __future__ import annotations
import os, sys, json, time, random, uuid
from typing import Any, Dict, List, Optional, Tuple

for _c in ("/tmp/ft/opt/frognet_semantic",):
    if os.path.isdir(os.path.join(_c, "core")):
        sys.path.insert(0, _c); break

ELEMENT = "game.backgammon"      # + ".<id>"
WHITE, BLACK = "w", "b"

# ---------------------------------------------------------------- stores
class TransientStore:
    def get(self, name): raise NotImplementedError
    def upsert(self, name, value): raise NotImplementedError
class PermStore:
    def load(self, gid): raise NotImplementedError
    def save(self, gid, state): raise NotImplementedError
class InMemoryTransient(TransientStore):
    def __init__(self): self._d={}
    def get(self,n): return self._d.get(n)
    def upsert(self,n,v): self._d[n]=json.loads(json.dumps(v))
    def drop(self,n): self._d.pop(n,None)
class InMemoryPerm(PermStore):
    def __init__(self): self._d={}
    def load(self,gid): return json.loads(json.dumps(self._d[gid])) if gid in self._d else None
    def save(self,gid,state): self._d[gid]=json.loads(json.dumps(state))

# ---------------------------------------------------------------- engine
def start_position() -> List[int]:
    p=[0]*25
    p[24]=2;  p[13]=5;  p[8]=3;  p[6]=5      # WHITE (positive)
    p[1]=-2; p[12]=-5; p[17]=-3; p[19]=-5    # BLACK (negative)
    return p

def _sign(color): return 1 if color==WHITE else -1
def _own(v,color): return v>0 if color==WHITE else v<0
def _opp_blot(v,color): return (v==-1) if color==WHITE else (v==1)

def all_home(points, bar, color) -> bool:
    if bar[color]>0: return False
    if color==WHITE:
        return not any(points[p]>0 for p in range(7,25))
    return not any(points[p]<0 for p in range(1,19))

def dest_point(color, frm, die) -> int:
    # returns destination point, or 0/25 sentinel meaning "bear off" (off-board)
    return frm-die if color==WHITE else frm+die

def is_bear_off(color, frm, die) -> bool:
    d=dest_point(color,frm,die)
    return d<1 if color==WHITE else d>24

def legal_moves(state) -> List[Dict[str,int]]:
    """All single-checker moves playable with a die currently in dice_remaining."""
    color=state["turn"]; pts=state["points"]; bar=state["bar"]
    dice=list(dict.fromkeys(state["dice_remaining"]))  # unique die values
    out=[]
    def add(frm,die,to,hit,bear):
        out.append({"from":frm,"die":die,"to":to,"hit":bool(hit),"bear":bool(bear)})
    # must enter from bar first
    if bar[color]>0:
        for die in dice:
            entry = 25-die if color==WHITE else die
            v=pts[entry]
            if _own(v,color) or v==0 or _opp_blot(v,color):
                add(0,die,entry,_opp_blot(v,color),False)
        return out
    home_ready = all_home(pts,bar,color)
    rng = range(1,25)
    for frm in rng:
        if not _own(pts[frm],color): continue
        for die in dice:
            if is_bear_off(color,frm,die):
                if not home_ready: continue
                # exact bear off, or overflow only if no checker on a higher pip
                pip = frm if color==WHITE else 25-frm
                if die==pip:
                    add(frm,die,(0 if color==WHITE else 25),False,True)
                elif die>pip:
                    higher = any(_own(pts[q],color) for q in (
                        range(frm+1,7) if color==WHITE else range(19,frm)))
                    if not higher:
                        add(frm,die,(0 if color==WHITE else 25),False,True)
            else:
                to=dest_point(color,frm,die)
                v=pts[to]
                if _own(v,color) or v==0 or _opp_blot(v,color):
                    add(frm,die,to,_opp_blot(v,color),False)
    return out

def apply_move(state, frm, die) -> bool:
    """Apply a single legal move keyed by (from,die). Returns True if applied."""
    legals=legal_moves(state)
    chosen=next((m for m in legals if m["from"]==frm and m["die"]==die), None)
    if chosen is None: return False
    color=state["turn"]; pts=state["points"]; bar=state["bar"]; off=state["off"]
    s=_sign(color)
    # remove from source
    if frm==0:
        bar[color]-=1
    else:
        pts[frm]-=s
    if chosen["bear"]:
        off[color]+=1
    else:
        to=chosen["to"]
        if chosen["hit"]:
            pts[to]=0
            opp=BLACK if color==WHITE else WHITE
            bar[opp]+=1
        pts[to]+=s
    # consume die
    state["dice_remaining"].remove(die)
    # win?
    if off[color]==15:
        state["phase"]="gameover"; state["winner"]=color
        state["legal"]=[]; return True
    # turn end if no dice or no legal moves
    if not state["dice_remaining"] or not legal_moves(state):
        _end_turn(state)
    else:
        state["legal"]=legal_moves(state)
    return True

def _end_turn(state):
    state["turn"]=BLACK if state["turn"]==WHITE else WHITE
    state["dice"]=[]; state["dice_remaining"]=[]
    state["phase"]="roll"; state["legal"]=[]

def do_roll(state, d1=None, d2=None):
    if state["phase"]!="roll": return False
    d1=d1 or random.randint(1,6); d2=d2 or random.randint(1,6)
    state["dice"]=[d1,d2]
    state["dice_remaining"]=[d1,d1,d1,d1] if d1==d2 else [d1,d2]
    state["phase"]="move"
    state["legal"]=legal_moves(state)
    if not state["legal"]:          # rolled but nothing to play
        _end_turn(state)
    return True

# ---------------------------------------------------------------- codex
class BackgammonCodex:
    def __init__(self, transient: TransientStore, perm: PermStore):
        self.t=transient; self.p=perm
    def _now(self): return str(int(time.time()*1000))
    def _key(self, gid): return f"{ELEMENT}.{gid}"

    def new_game(self, gid=None, white="white", black="black") -> Dict[str,Any]:
        gid=gid or uuid.uuid4().hex[:8]
        st={"element":self._key(gid),"id":gid,"version":1,"ts":self._now(),
            "points":start_position(),"bar":{WHITE:0,BLACK:0},"off":{WHITE:0,BLACK:0},
            "turn":WHITE,"phase":"roll","dice":[],"dice_remaining":[],"legal":[],
            "cube":{"value":1,"owner":None},"winner":None,
            "players":{WHITE:white,BLACK:black}}
        self._commit(gid,st); return st

    def _live(self, gid):
        st=self.t.get(self._key(gid))
        if st is None:                      # cold/relocated host -> refault from perm
            st=self.p.load(gid)
            if st is not None: self.t.upsert(self._key(gid),st)
        return st

    def _commit(self, gid, st):
        st["version"]+=1; st["ts"]=self._now()
        self.p.save(gid,st)                 # perm authority first (resumable)
        self.t.upsert(self._key(gid),st)    # then the live cache

    def get(self, gid): return self._live(gid)

    def roll(self, gid, d1=None, d2=None):
        st=self._live(gid)
        if st and do_roll(st,d1,d2): self._commit(gid,st)
        return st

    def move(self, gid, frm, die):
        st=self._live(gid)
        if st and st["phase"]=="move" and apply_move(st,frm,die): self._commit(gid,st)
        return st

    def offer_double(self, gid):
        st=self._live(gid)
        if st and st["phase"]=="roll" and st["cube"]["owner"] in (None,st["turn"]):
            st["phase"]="double-offered"; st["cube"]["offered_by"]=st["turn"]; self._commit(gid,st)
        return st
    def accept_double(self, gid):
        st=self._live(gid)
        if st and st["phase"]=="double-offered":
            st["cube"]["value"]*=2
            st["cube"]["owner"]=BLACK if st["cube"]["offered_by"]==WHITE else WHITE
            st["cube"].pop("offered_by",None); st["phase"]="roll"; self._commit(gid,st)
        return st
    def decline_double(self, gid):
        st=self._live(gid)
        if st and st["phase"]=="double-offered":
            st["phase"]="gameover"; st["winner"]=st["cube"]["offered_by"]; self._commit(gid,st)
        return st

    def touch_presence(self, gid, who):
        k=self._key(gid)+".presence"
        el=self.t.get(k) or {"seen":{}}
        el["seen"][who]=self._now(); self.t.upsert(k,el)
    def who_is_here(self, gid, window_ms=60000):
        el=self.t.get(self._key(gid)+".presence") or {"seen":{}}
        now=int(self._now())
        return sorted(w for w,ts in el["seen"].items() if now-int(ts)<=window_ms)

    @staticmethod
    def is_stale_read(read_ts, watermark): return int(read_ts)<int(watermark)

# ---------------------------------------------------------------- wire (real codec)
def wire_codec():
    try:
        import types, zlib
        if "mysql" not in sys.modules:
            m=types.ModuleType("mysql"); mc=types.ModuleType("mysql.connector")
            me=types.ModuleType("mysql.connector.errors")
            class E(Exception): pass
            me.Error=me.DatabaseError=me.InterfaceError=me.OperationalError=E
            mc.connect=lambda *a,**k:None; mc.errors=me; mc.Error=E
            pl=types.ModuleType("mysql.connector.pooling"); pl.MySQLConnectionPool=object
            mc.pooling=pl; m.connector=mc
            for n,mod in (("mysql",m),("mysql.connector",mc),
                          ("mysql.connector.errors",me),("mysql.connector.pooling",pl)):
                sys.modules[n]=mod
        if "lz4" not in sys.modules:
            lz4=types.ModuleType("lz4"); fr=types.ModuleType("lz4.frame")
            fr.compress=lambda d: zlib.compress(d,1); fr.decompress=lambda d: zlib.decompress(d)
            lz4.frame=fr; sys.modules["lz4"]=lz4; sys.modules["lz4.frame"]=fr
        from core.codec import SemanticCodec
        from core.json_handler import JsonFormatHandler
        return SemanticCodec(), JsonFormatHandler()
    except Exception:
        return None, None
