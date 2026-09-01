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
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex"))
from backgammon_codex import (
    BackgammonCodex, InMemoryTransient, InMemoryPerm, wire_codec,
    start_position, legal_moves, apply_move, do_roll, all_home, WHITE, BLACK, ELEMENT,
)
FAILS=[]
def check(n, ok, d=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {n}"+(f"  ({d})" if d else ""))
    if not ok: FAILS.append(n)

def base_state(turn=WHITE):
    return {"points":start_position(),"bar":{WHITE:0,BLACK:0},"off":{WHITE:0,BLACK:0},
            "turn":turn,"phase":"move","dice":[],"dice_remaining":[],"legal":[],
            "winner":None}

def t_start():
    print("\nU1: start position is the standard 15+15")
    p=start_position()
    w=sum(v for v in p if v>0); b=-sum(v for v in p if v<0)
    check("15 white checkers", w==15, str(w))
    check("15 black checkers", b==15, str(b))
    check("white on 24/13/8/6", p[24]==2 and p[13]==5 and p[8]==3 and p[6]==5)

def t_roll_and_legal():
    print("\nU2: roll produces dice and legal moves")
    st=base_state(); st["phase"]="roll"
    do_roll(st, 3, 1)
    check("dice set", st["dice"]==[3,1])
    check("non-doubles -> 2 dice remaining", st["dice_remaining"]==[3,1])
    check("legal moves exist from start with 3,1", len(st["legal"])>0)
    st2=base_state(); st2["phase"]="roll"; do_roll(st2,5,5)
    check("doubles -> 4 moves", st2["dice_remaining"]==[5,5,5,5])

def t_apply_basic():
    print("\nU3: applying a legal move updates board and consumes the die")
    st=base_state(); st["dice_remaining"]=[3,1]; st["legal"]=legal_moves(st)
    before=st["points"][6]
    ok=apply_move(st, 6, 1)          # white 6 -> 5
    check("move applied", ok)
    check("source decremented", st["points"][6]==before-1)
    check("destination has a white checker", st["points"][5]==1)
    check("die consumed", st["dice_remaining"]==[3])

def t_hit():
    print("\nU4: landing on an opponent blot sends it to the bar")
    st=base_state()
    st["points"][5]=-1               # black blot on 5
    st["dice_remaining"]=[1]; st["legal"]=legal_moves(st)
    ok=apply_move(st, 6, 1)          # white 6 -> 5, hitting
    check("move applied", ok)
    check("white now on 5", st["points"][5]==1)
    check("black sent to bar", st["bar"][BLACK]==1)

def t_bar_priority():
    print("\nU5: a checker on the bar must enter before any other move")
    st=base_state()
    st["bar"][WHITE]=1
    st["dice_remaining"]=[2,3]; st["legal"]=legal_moves(st)
    froms={m["from"] for m in st["legal"]}
    check("only bar-entry moves offered", froms=={0}, str(froms))
    # white enters on 25-die
    tos={m["to"] for m in st["legal"]}
    check("enters on 25-die (23 and 22)", tos=={23,22}, str(tos))

def t_bear_off():
    print("\nU6: bearing off when all checkers are home")
    st=base_state()
    st["points"]=[0]*25
    st["points"][6]=2; st["points"][3]=1   # 3 white, all in home (1..6)
    st["off"][WHITE]=12
    check("all_home true", all_home(st["points"],st["bar"],WHITE))
    st["dice_remaining"]=[6,3]; st["legal"]=legal_moves(st)
    ok=apply_move(st, 6, 6)                  # exact bear off from 6
    check("bear off from 6 with a 6", ok and st["off"][WHITE]==13)
    ok2=apply_move(st, 3, 3)                 # exact bear off from 3
    check("bear off from 3 with a 3", ok2 and st["off"][WHITE]==14)

def t_overflow_bearoff():
    print("\nU7: overflow bear-off only from the highest occupied point")
    st=base_state()
    st["points"]=[0]*25; st["points"][4]=1; st["off"][WHITE]=14
    st["dice_remaining"]=[6]; st["legal"]=legal_moves(st)
    ok=apply_move(st, 4, 6)                   # 6 > 4, no higher checker -> legal
    check("overflow bear off from highest point", ok and st["off"][WHITE]==15)
    check("game over + winner white", st["phase"]=="gameover" and st["winner"]==WHITE)

def t_no_legal_passes():
    print("\nU8: no legal move ends the turn")
    st=base_state(turn=WHITE)
    # wall: black owns 23,22,...   so white on 24 can't move with 1 or 2
    st["points"]=[0]*25; st["points"][24]=1
    st["points"][23]=-2; st["points"][22]=-2
    st["phase"]="roll"; do_roll(st,1,2)
    check("turn passed to black (no legal move)", st["turn"]==BLACK and st["phase"]=="roll")

def t_codex_flow_and_perm():
    print("\nI1: codex new/roll/move, perm-authority refault")
    t,p=InMemoryTransient(),InMemoryPerm()
    c=BackgammonCodex(t,p)
    st=c.new_game(gid="g1")
    check("new game white to roll", st["turn"]==WHITE and st["phase"]=="roll")
    c.roll("g1",3,1)
    st=c.get("g1"); check("after roll, phase move", st["phase"]=="move")
    mv=st["legal"][0]; c.move("g1",mv["from"],mv["die"])
    st=c.get("g1"); check("a die was consumed", len(st["dice_remaining"])<2 or st["phase"]=="roll")
    # perm refault: drop transient, reload
    t.drop(f"{ELEMENT}.g1")
    st=c.get("g1")
    check("refaulted from perm (game not lost)", st is not None and st["id"]=="g1")

def t_cube():
    print("\nI2: doubling cube offer/accept")
    t,p=InMemoryTransient(),InMemoryPerm(); c=BackgammonCodex(t,p)
    c.new_game(gid="g2")
    c.offer_double("g2"); st=c.get("g2")
    check("double offered", st["phase"]=="double-offered")
    c.accept_double("g2"); st=c.get("g2")
    check("cube now 2, owned by black", st["cube"]["value"]==2 and st["cube"]["owner"]==BLACK)

def t_presence_stale():
    print("\nI3: presence + stale-read")
    t,p=InMemoryTransient(),InMemoryPerm(); c=BackgammonCodex(t,p); c.new_game(gid="g3")
    c.touch_presence("g3","dad"); c.touch_presence("g3","mom")
    check("two viewers", c.who_is_here("g3")==["dad","mom"])
    check("backwards read is stale", BackgammonCodex.is_stale_read(100,200) is True)

def t_full_game():
    print("\nI4: a full forced game runs to a legal win without errors")
    import random as r; r.seed(7)
    t,p=InMemoryTransient(),InMemoryPerm(); c=BackgammonCodex(t,p); c.new_game(gid="gf")
    turns=0
    while True:
        st=c.get("gf")
        if st["phase"]=="gameover": break
        if st["phase"]=="roll": c.roll("gf")
        st=c.get("gf")
        guard=0
        while st["phase"]=="move" and st["legal"]:
            mv=r.choice(st["legal"]); c.move("gf",mv["from"],mv["die"]); st=c.get("gf")
            guard+=1
            if guard>50: break
        turns+=1
        if turns>4000: break
    st=c.get("gf")
    check("game terminated with a winner", st["phase"]=="gameover" and st["winner"] in (WHITE,BLACK),
          f"{turns} turns, winner={st.get('winner')}")
    check("winner bore off all 15", st["off"][st["winner"]]==15)

def t_wire():
    print("\nI5: real codec -- a move ships a DIFF, per-reader deltas")
    codec,handler=wire_codec()
    if codec is None: check("real codec available", False, "tree not importable"); return
    t,p=InMemoryTransient(),InMemoryPerm(); c=BackgammonCodex(t,p)
    c.new_game(gid="gw"); c.roll("gw",3,1)
    s1=json.dumps(c.get("gw"),sort_keys=True)
    mv=c.get("gw")["legal"][0]; c.move("gw",mv["from"],mv["die"])
    s2=json.dumps(c.get("gw"),sort_keys=True)
    frag=handler.learn_request_template(s1)
    def enc(ref,body):
        dyn=handler.extract_request_dynamic(body,frag)
        diff,nref,_=codec.encode_request_diff(opcode=0xB6,url_vals=[],json_vals=dyn,
            type_map=frag["type_map"],reference=ref,tokens=frag["tokens"],compress=True)
        full=codec.encode_request(opcode=0xB6,url_vals=[],json_vals=dyn,
            type_map=frag["type_map"],tokens=frag["tokens"],compress=True)
        return diff,nref,full
    _,refA,_=enc(None,s1)
    diffA,_,fullA=enc(refA,s2)
    _,_,fullB=enc(None,s2)
    check("caught-up delta < fresh full", len(diffA)<len(fullB), f"A={len(diffA)} B={len(fullB)}")
    check("move travels as a DIFF", len(diffA)<len(fullA), f"diff={len(diffA)} full={len(fullA)}")

def main():
    print("="*68); print("Family Backgammon -- engine + codex tests"); print("="*68)
    for fn in (t_start,t_roll_and_legal,t_apply_basic,t_hit,t_bar_priority,t_bear_off,
               t_overflow_bearoff,t_no_legal_passes,t_codex_flow_and_perm,t_cube,
               t_presence_stale,t_full_game,t_wire):
        fn()
    print("\n"+"="*68)
    if FAILS: print(f"RESULT: {len(FAILS)} FAIL -- {FAILS}"); return 1
    print("RESULT: ALL PASS"); return 0

if __name__=="__main__": raise SystemExit(main())
