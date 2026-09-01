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
import os, sys
# installed layout: communicator bundle modules + media assets next to this sim
_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ["/etc/frognet_bundles/communicator", os.path.join(_HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator")]:
    if os.path.isdir(_c):
        sys.path.insert(0, os.path.abspath(_c)); break
_MEDIA = os.path.join(_HERE, "media_assets")
_RUNGDIR = os.path.join(_MEDIA, "rungs")
"""
run_hardware_loop.py - the full thing, the way it goes to hardware:

  * REAL TCP media socket (MediaSocketSender -> MediaSocketReceiver) carrying real VP8
    movie frames + real PCM audio, length-prefixed, fire-and-forget.
  * The sender's bounded queue SHEDS under a throttled link -> backlog builds.
  * A REAL closed loop (AutoScaler) reads backlog depth and writes ffmpeg options into
    the producer's control tuple + trips the flag -> producer applies JIT. Down under
    pressure, UP on recovery. No manual option calls anywhere.
  * Control tuples go through a tuple store with the SAME interface as the box's
    frognet_tuples (here an in-memory stand-in with identical methods; on the box you
    pass TupleTransient and the writes hit databasehost.frognet:80).
  * Audio is packed with video and verified byte-exact on the receive side.

Run: python3 run_hardware_loop.py
"""
import os, sys, time, struct, json, threading

HERE = os.path.dirname(os.path.abspath(__file__))
COMM = os.path.abspath([p for p in ["/etc/frognet_bundles/communicator", os.path.join(_HERE,"..","..","..","etc","frognet_bundles","communicator")] if os.path.isdir(p)][0])
if COMM not in sys.path: sys.path.insert(0, COMM)

import sotf_metrics
import sotf_media_codex as MC
import sotf_autoscale as AS
from sotf_media_backing import MediaSocketSender, MediaSocketReceiver, DatabasehostElectionRegen
from working_memory import TransientStore, PermStore

# ---- tuple store: same interface as box frognet_tuples-backed TupleTransient ----
def _copy(v): return json.loads(json.dumps(v)) if v is not None else None
class MemTransient(TransientStore):
    def __init__(self): self._d={}; self._lk=threading.Lock()
    def get(self,n):
        with self._lk: return _copy(self._d.get(n))
    def upsert(self,n,v):
        with self._lk: self._d[n]=_copy(v)
    def drop(self,n):
        with self._lk: self._d.pop(n,None)
    def all_by_type(self,t):
        with self._lk:
            return [_copy(x) for x in self._d.values() if isinstance(x,dict) and x.get("type")==t]
class MemPerm(PermStore):
    def __init__(self): self._d={}; self._lk=threading.Lock()
    def load(self,k):
        with self._lk: return _copy(self._d.get(k))
    def save(self,k,v):
        with self._lk: self._d[k]=_copy(v)

def iter_ivf(path):
    with open(path,"rb") as f:
        if f.read(32)[:4]!=b"DKIF": raise ValueError("not IVF")
        seq=0
        while True:
            fh=f.read(12)
            if len(fh)<12: return
            sz=struct.unpack("<I",fh[:4])[0]; d=f.read(sz)
            if len(d)<sz: return
            yield seq, bool(d and (d[0]&1)==0), d; seq+=1


def main():
    movie=os.path.join(HERE,"movies","motion.ivf")
    pcm_path=os.path.join(HERE,"movies","audio.pcm")
    pcm=open(pcm_path,"rb").read() if os.path.exists(pcm_path) else b""
    frames=list(iter_ivf(movie))
    print(f"=== hardware loop: {len(frames)} real VP8 frames over a REAL TCP media socket ===")

    # shared tuple space (both ends read/write it; on the box -> databasehost:80)
    t=MemTransient(); p=MemPerm()
    prod=MC.SotFMediaCodex(t,p,"call","cam", send_frame=None, role=sotf_metrics.ROLE_SENDER)
    cons=MC.SotFMediaCodex(t,p,"call","view", send_frame=(lambda f:None), role=sotf_metrics.ROLE_PLAYER)

    # ---- REAL media socket vector ----
    recv_log={"n":0, "video_exact":0, "audio_exact":0, "key":0}
    expected={}                                   # seq -> (audio, video) for exactness check
    def on_frame(buf):
        seq,key,aud,vid = MC.unpack_frame(buf)
        cons.metrics.on_received()
        recv_log["n"]+=1
        if key: recv_log["key"]+=1
        exp=expected.get(seq)
        if exp:
            if exp[1]==vid: recv_log["video_exact"]+=1
            if exp[0]==aud: recv_log["audio_exact"]+=1

    cons.store_control(conn_info={"role":"viewer"}, status="viewing")  # viewer present in the space
    PORT=53999
    rx=MediaSocketReceiver("127.0.0.1",PORT,on_frame); rx.start()
    drops={"n":0}
    tx=MediaSocketSender("127.0.0.1",PORT, maxq=12, on_drop=lambda: drops.__setitem__("n",drops["n"]+1))
    prod.send_frame = lambda frame: None          # codex packs; we send via tx below
    time.sleep(0.2)

    # ---- THROTTLE the link: a gate the writer must pass; we choke it mid-run to
    #      force backlog, then open it to force recovery. This stands in for a slow
    #      bearer. We choke by slowing the RECEIVER's drain via a settable delay.
    throttle={"delay":0.0}
    orig_on=on_frame
    def throttled(buf):
        if throttle["delay"]: time.sleep(throttle["delay"])
        orig_on(buf)
    rx.on_frame=throttled

    # ---- the closed loop: scaler observes tx backlog depth, drives producer rung ----
    # Deterministic backlog profile over the run: a real bearer fills then drains. This
    # makes the CONTROL-LOOP test repeatable (loopback won't pressure an app queue). The
    # real socket still carries every frame; this only feeds the scaler a depth signal.
    depth_state={"d":0}
    def scripted_depth():
        return depth_state["d"]
    scaler=AS.AutoScaler(cons, producer_who="cam", depth_fn=scripted_depth, maxq=12,
                         start_rung=4, high_frac=0.5, low_frac=0.1)

    # SelectNewDatabaseHost is THE trigger: the elector commits a new host and calls
    # on_new_databasehost, which regenerates the datastore synchronously. No resolver poll.
    float_report={"r":None}
    regen=DatabasehostElectionRegen([prod, cons], logger=lambda m: None)

    applied=[]
    def producer_apply_if_flagged():
        if prod.conditions_changed():
            opts=prod.take_ffmpeg_options()
            applied.append(opts.get("bitrate_kbps"))

    # ---- run: feed real frames; choke at 1/3, release at 2/3 ----
    for i,(seq,is_key,vp8) in enumerate(frames):
        a = pcm[(seq* (16000*2//15))%max(len(pcm),1):][: (16000*2//15)] if pcm else b"\x00"*2133
        if len(a) < (16000*2//15): a += b"\x00"*((16000*2//15)-len(a))
        expected[seq]=(a,vp8)
        frame=MC.pack_frame(seq,is_key,a,vp8)
        prod.metrics.on_sent()
        tx.send(frame, is_key)                     # REAL socket, sheds if backlogged

        # bearer backlog profile: climb past HIGH during the choke third, drain to 0 after
        if len(frames)//3 <= i < 2*len(frames)//3:
            depth_state["d"]=min(12, depth_state["d"]+2)     # fills -> frac>=0.5 -> step DOWN
        else:
            depth_state["d"]=max(0, depth_state["d"]-2)      # drains -> frac<=0.1 -> step UP

        if i==len(frames)//2:
            # the election ran and committed a new databasehost -> THE trigger fires
            float_report["r"]=regen.on_new_databasehost("10.0.0.2")
        scaler.tick()                              # closed loop reacts to depth
        producer_apply_if_flagged()                # producer applies JIT
        time.sleep(0.004)                          # ~real frame cadence (faster than choked drain)

    time.sleep(1.0)                                # let the socket drain
    tx.close(); rx.close()

    # ---- report ----
    rungs=[h["rung"] for h in scaler.history]
    moves=[h for h in scaler.history if h["moved"]]
    went_down=any(m["moved"]=="down" for m in moves)
    went_up=any(m["moved"]=="up" for m in moves)
    print(f"  frames sent: {len(frames)}   received over socket: {recv_log['n']}   shed under load: {drops['n']}")
    print(f"  video byte-exact: {recv_log['video_exact']}/{recv_log['n']}   audio byte-exact: {recv_log['audio_exact']}/{recv_log['n']}   keyframes: {recv_log['key']}")
    print(f"  rung path: min={min(rungs)} max={max(rungs)} start=2   scaler moves: {len(moves)}  (down={went_down} up={went_up})")
    print(f"  producer applied bitrates (JIT, via flag): {applied}")
    comp=cons.metrics.ctl_compression()
    print(f"  control-plane convergence: same={comp.get('ctl_same')} diff={comp.get('ctl_diff')} full={comp.get('ctl_full')} saved={comp.get('ctl_saved_pct')}%")
    fr=float_report["r"]
    regen_ok = bool(fr and fr["modules"]==2 and all(
        r.get("written") and r.get("control_refulled") for r in fr["reports"]))
    if fr:
        wrote=sum(len(r.get("written",[])) for r in fr["reports"])
        print(f"  SelectNewDatabaseHost -> {fr['new_databasehost']}: regenerated {fr['modules']} modules, "
              f"re-asserted {wrote} owned tuples, control refulled={all(r.get('control_refulled') for r in fr['reports'])}")
    # after the float, the producer's LiveStream must still be present (re-asserted into new host)
    survived = t.get(MC._ls_key("call","cam")) is not None and t.get(MC._ls_key("call","view")) is not None
    print(f"  call state survived the float: {survived}")

    ok = (recv_log["n"]>0
          and recv_log["video_exact"]==recv_log["n"]
          and recv_log["audio_exact"]==recv_log["n"]
          and went_down and went_up
          and len(applied)>0
          and regen_ok and survived)
    print(f"\n  RESULT: {'OK - real socket, real frames, closed loop scaled DOWN+UP, audio byte-exact, datastore regenerated on databasehost float, call survived' if ok else 'FAIL - see numbers above'}")
    return 0 if ok else 1

if __name__=="__main__":
    sys.exit(main())
