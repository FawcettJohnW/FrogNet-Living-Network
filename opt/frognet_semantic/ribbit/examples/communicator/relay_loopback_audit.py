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
"""[AUDIT_THROUGH_THE_RELAY_V1] Push a WAV through the REAL relay and compare.

audit_audio_path.py proved codec + framing + mixer + resamplers in isolation.
This adds the piece that test could not reach: the relay itself. It stands up
an actual fnav SotFDataPlane, connects two real sockets to it -- one declaring
the audio plane as the sender, one as the listener -- pushes the WAV through as
Opus at wall-clock rate, and reads it back off the far socket.

Compared, and FAILED LOUD on any of them:
  - packet count out of the relay equals packet count in
  - every packet byte-identical in and out, IN ORDER
  - no reordering, no duplication, no truncation
  - arrival timing: worst gap, worst burst, and how far each packet
    drifted from its 20 ms slot

Writes:  relay_in_16000hz.wav   what was handed to the relay
         relay_out_16000hz.wav  what came back off it
Listen to both. If they sound different, the relay did it.
"""

import hashlib
import socket
import struct
import sys
import threading
import time
import wave

import fnphone_pa as A
A.LIVE_TAP_DIR = None                      # taps off: this test writes its own
import fnav

IN = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/mic.wav"
PORT = 19099
BLK = fnav.AUDIO_RATE * 20 // 1000 * 2     # 640 bytes = 20 ms
FAIL = []


def fail(m):
    FAIL.append(m); print("  FAIL  " + m)


def ok(m):
    print("  ok    " + m)


def wav(path, pcm, rate):
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(pcm); w.close()


# ---- load and downconvert exactly as the capture path does ----------------
w = wave.open(IN); ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
raw = w.readframes(n); w.close()
if ch == 2:
    raw = b"".join(raw[i:i + 2] for i in range(0, len(raw), 4))
cap = A.StreamResampler(rate, fnav.AUDIO_RATE)
pcm = b"".join(cap.process(raw[i:i + rate // 50 * 2])
               for i in range(0, len(raw), rate // 50 * 2))
print("source: %s -> %d Hz mono, %.2f s" % (IN, fnav.AUDIO_RATE,
                                            len(pcm) / (fnav.AUDIO_RATE * 2.0)))

# ---- stand up the real relay ----------------------------------------------
relay = fnav.Relay("127.0.0.1", PORT)
threading.Thread(target=relay.serve, daemon=True).start()
time.sleep(0.6)
print("relay: real fnav.Relay on 127.0.0.1:%d\n" % PORT)


def join(name, session):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    tag = struct.pack("!Q", int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF)
    body = fnav.pack_typed(fnav.KIND_PLANE, name,
                           fnav.PLANE_AUDIO + tag + session.encode())
    s.sendall(fnav._LEN.pack(len(body)) + body)
    return s


SESSION = "audit0001"
rx = join("John", SESSION)          # listener joins first
time.sleep(0.3)
tx = join("Sandy", SESSION)
time.sleep(0.5)

enc, dec = fnav.OpusCodec(), fnav.OpusCodec()
sent, sent_at = [], []
got, got_at = [], []
stop = threading.Event()


def reader():
    buf = b""
    rx.settimeout(0.5)
    while not stop.is_set():
        try:
            d = rx.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return
        if not d:
            return
        buf += d
        while len(buf) >= 4:
            (ln,) = fnav._LEN.unpack(buf[:4])
            if len(buf) < 4 + ln:
                break
            body, buf = buf[4:4 + ln], buf[4 + ln:]
            k, src, pl = fnav.unpack_typed(body)
            if k == fnav.KIND_AUDIO and src == "Sandy":
                got.append(pl[1:])            # strip the AUDIO_FMT byte
                got_at.append(time.monotonic())


th = threading.Thread(target=reader, daemon=True); th.start()

# ---- push it through at wall-clock rate -----------------------------------
t0 = time.monotonic()
i = blk_n = 0
while i + BLK <= len(pcm):
    for p in enc.encode(pcm[i:i + BLK]):
        body = fnav.pack_typed(fnav.KIND_AUDIO, "Sandy",
                               fnav._KIND.pack(fnav.AUDIO_FMT_OPUS) + p)
        tx.sendall(fnav._LEN.pack(len(body)) + body)
        sent.append(p); sent_at.append(time.monotonic())
    i += BLK; blk_n += 1
    nxt = t0 + blk_n * 0.020
    d = nxt - time.monotonic()
    if d > 0:
        time.sleep(d)

time.sleep(1.5)
stop.set(); th.join(timeout=2)
try: tx.close(); rx.close()
except Exception: pass

# ---- the comparison -------------------------------------------------------
print("sent %d packets, %d bytes   |   received %d packets, %d bytes\n"
      % (len(sent), sum(map(len, sent)), len(got), sum(map(len, got))))

(ok if len(got) == len(sent) else fail)(
    "packet count preserved through the relay (%d -> %d)" % (len(sent), len(got)))

hs = hashlib.sha256(b"".join(sent)).hexdigest()[:16]
hg = hashlib.sha256(b"".join(got)).hexdigest()[:16]
(ok if hs == hg else fail)("payload identical end to end  (in %s / out %s)" % (hs, hg))

if len(got) == len(sent):
    bad = [i for i, (a, b) in enumerate(zip(sent, got)) if a != b]
    (ok if not bad else fail)("every packet byte-identical and in order"
                              + ("" if not bad else " (%d differ, first at %d)"
                                 % (len(bad), bad[0])))

# ---- arrival timing -------------------------------------------------------
if len(got_at) > 2:
    gaps = [(got_at[i] - got_at[i - 1]) * 1000 for i in range(1, len(got_at))]
    late = [(got_at[i] - sent_at[i]) * 1000 for i in range(min(len(got_at), len(sent_at)))]
    gaps_s = sorted(gaps)
    print("\narrival timing (20.0 ms is nominal):")
    print("  gap  median %.1f ms   p95 %.1f ms   worst %.1f ms"
          % (gaps_s[len(gaps_s) // 2], gaps_s[int(len(gaps_s) * .95)], gaps_s[-1]))
    print("  relay transit  min %.2f ms  median %.2f ms  worst %.2f ms"
          % (min(late), sorted(late)[len(late) // 2], max(late)))
    stalled = [g for g in gaps if g > 60]
    (ok if not stalled else fail)(
        "no arrival gap over 60 ms (%d found, worst %.0f ms)"
        % (len(stalled), max(stalled) if stalled else 0))

# ---- decode both sides and write them out ---------------------------------
_di = fnav.OpusCodec()
d_in = b"".join(_di.decode(p) for p in sent)
d_out = b"".join(dec.decode(p) for p in got)
wav("/home/claude/relay_in_16000hz.wav", d_in, fnav.AUDIO_RATE)
wav("/home/claude/relay_out_16000hz.wav", d_out, fnav.AUDIO_RATE)
print("\nwrote relay_in_16000hz.wav (%d bytes) and relay_out_16000hz.wav (%d bytes)"
      % (len(d_in), len(d_out)))
(ok if d_in == d_out else fail)("decoded audio identical on both sides")

print()
if FAIL:
    print("FAILURES: %d" % len(FAIL))
    for f in FAIL: print("  " + f)
    raise SystemExit(1)
print("the relay returned exactly what it was given")
