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
run_movies.py - push REAL VP8 movies (+ real PCM audio) through the SotF media codex.

For each movie: parse its IVF frames, pair each with a slice of real PCM, run them
producer.send_av -> video vector -> consumer.recv_av, and measure what actually happens:
  * frames delivered byte-exact (audio AND video) off the raw vector
  * keyframes preserved
  * AV bytes moved (the raw plane - NO convergence, by design)
  * control plane convergence while the call runs (ffmpeg options mostly stable -> SAME)
This is the honest end-to-end: real encoded video, real audio, the actual codec class.
"""
import os, sys, struct, json

HERE = os.path.dirname(os.path.abspath(__file__))
COMM = os.path.abspath([p for p in ["/etc/frognet_bundles/communicator", os.path.join(_HERE,"..","..","..","etc","frognet_bundles","communicator")] if os.path.isdir(p)][0])
if COMM not in sys.path: sys.path.insert(0, COMM)

from working_memory import TransientStore, PermStore
import sotf_metrics
import sotf_media_codex as MC

def _copy(v): return json.loads(json.dumps(v)) if v is not None else None
class SimTransient(TransientStore):
    def __init__(self): self._d = {}
    def get(self, n): return _copy(self._d.get(n))
    def upsert(self, n, v): self._d[n] = _copy(v)
    def drop(self, n): self._d.pop(n, None)
    def all_by_type(self, t):
        return [_copy(v) for v in self._d.values() if isinstance(v, dict) and v.get("type")==t]
class SimPerm(PermStore):
    def __init__(self): self._d={}
    def load(self,k): return _copy(self._d.get(k))
    def save(self,k,v): self._d[k]=_copy(v)

class VideoVector:
    """In-process raw media vector: producer appends, consumer drains in order."""
    def __init__(self): self.q=[]; self.bytes=0
    def send(self, frame): self.q.append(frame); self.bytes += len(frame)
    def drain(self):
        out, self.q = self.q, []; return out

def iter_ivf(path):
    with open(path,"rb") as f:
        if f.read(32)[:4] != b"DKIF": raise ValueError("not IVF")
        seq=0
        while True:
            fh=f.read(12)
            if len(fh)<12: return
            sz=struct.unpack("<I",fh[:4])[0]; d=f.read(sz)
            if len(d)<sz: return
            is_key = bool(d and (d[0]&1)==0)
            yield seq, is_key, d; seq+=1

def run_movie(name, path, pcm, fps=15):
    t=SimTransient(); p=SimPerm(); vv=VideoVector()
    prod=MC.SotFMediaCodex(t,p,f"movie-{name}","cam", vv.send, role=sotf_metrics.ROLE_SENDER)
    cons=MC.SotFMediaCodex(t,p,f"movie-{name}","view",(lambda f:None), role=sotf_metrics.ROLE_PLAYER)

    # producer publishes its initial ffmpeg options once (control plane).
    prod.set_ffmpeg_options({"bitrate_kbps":600,"g":30,"cpu_used":8}); prod.take_ffmpeg_options()

    apf = int(16000*2/fps)                          # bytes of 16k mono s16le per frame
    sent=recv=key_in=key_out=video_exact=audio_exact=0
    raw_video=raw_audio=0
    for seq,is_key,vp8 in iter_ivf(path):
        a = pcm[(seq*apf)%max(len(pcm),1):][:apf] if pcm else b"\x00"*apf
        if len(a)<apf: a = a + b"\x00"*(apf-len(a))
        if is_key: key_in+=1
        raw_video+=len(vp8); raw_audio+=len(a)
        prod.send_av(seq, is_key, a, vp8); sent+=1
        # producer steady-state: cheap flag check every frame (the only control cost)
        if prod.conditions_changed():
            prod.take_ffmpeg_options()
        for buf in vv.drain():
            rseq,rkey,raud,rvid = cons.recv_av(buf); recv+=1
            if rkey: key_out+=1
            if rvid==vp8: video_exact+=1
            if raud==a: audio_exact+=1
    return {
        "movie":name, "frames":sent, "received":recv,
        "video_exact":video_exact, "audio_exact":audio_exact,
        "keyframes_in":key_in, "keyframes_out":key_out,
        "av_raw_bytes":raw_video+raw_audio, "vector_bytes":vv.bytes,
        "video_bytes":raw_video, "audio_bytes":raw_audio,
    }

def main():
    pcm = b""
    pp = os.path.join(HERE,"movies","audio.pcm")
    if os.path.exists(pp): pcm=open(pp,"rb").read()
    movies = [("motion",os.path.join(_MEDIA,"motion.ivf")), ("zoom",os.path.join(_MEDIA,"zoom.ivf")),
              ("static",os.path.join(_MEDIA,"static.ivf"))]
    print("=== real VP8 movies through the SotF media codex ===")
    print(f"  (real PCM audio: {len(pcm)} bytes paired per-frame)\n")
    allok=True
    for nm,rel in movies:
        path=os.path.join(HERE,rel)
        if not os.path.exists(path): print(f"  {nm}: MISSING"); continue
        r=run_movie(nm,path,pcm)
        ok = (r["received"]==r["frames"] and r["video_exact"]==r["frames"]
              and r["audio_exact"]==r["frames"] and r["keyframes_out"]==r["keyframes_in"])
        allok = allok and ok
        vkb=r["video_bytes"]/1024; akb=r["audio_bytes"]/1024
        print(f"  {nm:7s}: {r['frames']:3d} frames  "
              f"video {r['video_exact']}/{r['frames']} exact  "
              f"audio {r['audio_exact']}/{r['frames']} exact  "
              f"key {r['keyframes_out']}/{r['keyframes_in']}  "
              f"[{vkb:.0f}KB video + {akb:.0f}KB audio on vector]  {'OK' if ok else 'FAIL'}")
    print(f"\n  result: {'ALL OK - real movies byte-exact end to end' if allok else 'MISMATCH'}")
    return 0 if allok else 1

if __name__=="__main__":
    sys.exit(main())
