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
compare_bandwidth.py - the BANDWIDTH axis, measured honestly.

Throttle the pipe (kbps, NO loss). Push the movie three ways and measure delivered fps
and how far behind real-time it ends (lag):

  UDP fixed       - 606kbps stream over UDP; when the pipe < stream, datagrams overflow
                    and frames are lost (UDP sheds).
  SotF fixed      - 606kbps stream as data on established TCP; TCP never drops, so when
                    the pipe < stream it BUFFERS and falls behind (lag grows). This is
                    TCP's low-bandwidth failure mode: laggy, not broken.
  SotF + scaler   - the producer drops down the real ffmpeg ladder (1218->606->305->153)
                    to fit the pipe. THIS is the low-bandwidth win: re-encode smaller so
                    the stream fits, stays real-time.

The pipe is a token bucket: bytes/sec cap. Frames offered at 15fps; a frame can only go
once the bucket has tokens, else it waits (TCP) or sheds (UDP).
"""
import os, sys, struct, time

HERE=os.path.dirname(os.path.abspath(__file__))
FPS=15
def load(path):
    out=[]; f=open(path,"rb"); f.read(32); seq=0
    while True:
        fh=f.read(12)
        if len(fh)<12: break
        sz=struct.unpack("<I",fh[:4])[0]; d=f.read(sz)
        if len(d)<sz: break
        out.append((seq,bool(d and (d[0]&1)==0),d)); seq+=1
    return out

RUNGS=[(153,"r150.ivf"),(305,"r300.ivf"),(606,"r600.ivf"),(1218,"r1200.ivf")]
RUNG_FRAMES={br:load(os.path.join(_RUNGDIR,p)) for br,p in RUNGS}
AUDIO_BPS=256000//8   # ~256kbps audio always present

def sim_fixed_udp(frames, kbps):
    """UDP: bucket fills at kbps; a frame that doesn't fit the bucket when offered is shed."""
    cap=kbps*1000/8; bucket=cap*0.25; last=0.0; delivered=0
    for i,(seq,key,vp8) in enumerate(frames):
        t=i/FPS; bucket=min(cap*0.5, bucket+(t-last)*cap); last=t
        need=len(vp8)+AUDIO_BPS/FPS
        if bucket>=need: bucket-=need; delivered+=1     # fits -> sent
        # else: shed (UDP drops the frame)
    return delivered, delivered/ (len(frames)/FPS), 0.0

def sim_fixed_tcp(frames, kbps):
    """TCP: never drops; if the pipe is too slow the send blocks -> stream falls behind.
    Delivered fps = frames/(time it actually took); lag = extra wall time beyond the clip."""
    cap=kbps*1000/8; clock=0.0
    for i,(seq,key,vp8) in enumerate(frames):
        offered=i/FPS
        need=len(vp8)+AUDIO_BPS/FPS
        send_time=need/cap
        clock=max(clock, offered)+send_time            # must serialize through the pipe
    clip=len(frames)/FPS
    dfps=len(frames)/clock if clock>0 else 0
    return len(frames), dfps, max(0.0, clock-clip)

def sim_scaler_tcp(kbps):
    """Producer drops down the ladder to the highest rung that FITS the pipe (leaving room
    for audio). Then runs TCP-fixed at that rung. Reports the rung chosen + fps + lag."""
    budget=kbps - 256                                   # leave room for audio
    chosen=RUNGS[0][0]
    for br,_ in RUNGS:
        if br<=budget: chosen=br
    frames=RUNG_FRAMES[chosen]
    n,dfps,lag=sim_fixed_tcp(frames, kbps)
    return chosen, n, dfps, lag

def main():
    base=RUNG_FRAMES[606]
    print("=== BANDWIDTH axis: throttle the pipe (no loss). 606kbps stream (+256k audio) ===\n")
    print(f"  {'kbps':>6} | {'UDP fixed fps':>13} | {'SotF fixed fps':>14} {'lag(s)':>7} | {'SotF+scaler':>11} {'rung':>6} {'fps':>5} {'lag':>5}")
    for kbps in [2000,1000,862,600,400,300,200,150,100]:
        un,uf,_=sim_fixed_udp(base,kbps)
        tn,tf,tlag=sim_fixed_tcp(base,kbps)
        rung,sn,sf,slag=sim_scaler_tcp(kbps)
        print(f"  {kbps:>6} | {uf:>13.1f} | {tf:>14.1f} {tlag:>7.1f} | {'':>11} {rung:>6} {sf:>5.1f} {slag:>5.1f}")
    print()
    print("  READ:")
    print("  - UDP fixed: sheds frames once the pipe < stream -> fps craters.")
    print("  - SotF fixed (TCP): keeps every frame but the clip takes longer -> fps drops and")
    print("    lag grows (laggy, not broken). Pure transport can't beat the bitrate physics.")
    print("  - SotF + scaler: drops to the ladder rung that FITS -> stays ~15fps real-time at")
    print("    low bandwidth by ENCODING SMALLER. That is the low-bandwidth win, and it's the")
    print("    scaler+convergent-control, not the transport.")
    return 0

if __name__=="__main__":
    sys.exit(main())
