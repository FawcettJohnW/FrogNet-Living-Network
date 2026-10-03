#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""comms_feed.py -- the unattended feed in Python (requirement 1): no UI; a camera, a video file or a moving test
picture, a microphone or a tone, published to the lobby as *NAME until stopped.

  comms_feed.py --ram HOST:PORT --media HOST:PORT --name NAME [--camera DEV | --video FILE | --synthetic WxH]
                [--size WxH] [--mic N] [--tone HZ] [--seconds N] [--bandwidth BPS] [--jitter MS]"""
import argparse, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import AvSource, SyntheticSource  # noqa: E402
from frogcomms.audio import Mic, ToneSource  # noqa: E402


def hostport(s):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--ram", required=True); a.add_argument("--media", required=True); a.add_argument("--name", required=True)
    a.add_argument("--camera"); a.add_argument("--video"); a.add_argument("--synthetic", default="1280x720")
    a.add_argument("--size"); a.add_argument("--mic", type=int); a.add_argument("--tone", type=float, default=0)
    a.add_argument("--seconds", type=int, default=0); a.add_argument("--bandwidth", type=int, default=0)
    a.add_argument("--jitter", type=int, default=0); a.add_argument("--fps", type=int, default=24)
    o = a.parse_args()
    (rh, rp), (mh, mp) = hostport(o.ram), hostport(o.media)
    w, h = map(int, (o.size or o.synthetic).split("x"))
    src = AvSource("camera", o.camera, w, h, o.fps) if o.camera else AvSource("file", o.video, w, h, o.fps) if o.video \
        else SyntheticSource(w, h)
    mic = Mic(o.mic) if o.mic is not None else ToneSource(o.tone) if o.tone else None
    session = "%012x" % random.getrandbits(48)
    room = None
    try:
        room = Room(rh, rp, "*" + o.name, "cam=1,mic=%d,unattended=1" % (1 if mic else 0))
        room.offer_call(session, mh, mp); room.join_call(session, mh, mp)
        print("[feed] *%s is live: session=%s media=%s:%d" % (o.name, session, mh, mp), flush=True)
    except Exception as e:                                   # streaming goes on; the lobby will not show it -- said
        print("[feed] could not announce (%s) -- streaming anyway, but *%s will NOT appear in the lobby" % (e, o.name), flush=True)
    call = Call(mh, mp, o.name, session, src, o.fps, mic, None)
    call.start()
    if o.bandwidth: call.set_throttle(o.bandwidth); print("[feed] link: upload paced to %d kb/s" % (o.bandwidth // 1000), flush=True)
    if o.jitter: call.set_jitter(o.jitter); print("[feed] link: up to %d ms of jitter per frame" % o.jitter, flush=True)
    t0, last = time.time(), (0, 0)
    try:
        while not o.seconds or time.time() - t0 < o.seconds:
            time.sleep(5)
            s = call.stats
            print("[feed] L%d %dx%d sent %d frames (%d dropped), %d audio" % (s.rung, s.send_w, s.send_h, s.v_sent, s.v_dropped, s.a_sent), flush=True)
    except KeyboardInterrupt:
        pass
    call.stop()
    if room:
        room.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                   # [A_FAILURE_SAYS_WHAT_FAILED_V1]
        print("comms_feed: %s" % e, file=sys.stderr); sys.exit(1)
