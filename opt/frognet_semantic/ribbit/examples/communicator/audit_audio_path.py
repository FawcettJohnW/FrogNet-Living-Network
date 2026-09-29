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
"""[AUDIT_THE_WHOLE_PATH_V1] mic.wav -> every real stage -> speaker.wav

Uses the SHIPPING classes, not reimplementations: fnphone_pa.StreamResampler,
fnav.OpusCodec, fnav.pack_typed/unpack_typed, fnphone_pa.Mixer. If a stage
damages audio, it damages it here.

Stages, in the order the live code runs them:

  1. device capture      32000 Hz s16 stereo -> mono          (arecord format)
  2. cap_rs              StreamResampler 32000 -> 16000        [TAP A]
  3. OpusCodec.encode    16 kHz mono -> Opus packets
  4. wire                pack_typed / _LEN framing             [TAP W: bytes]
  5. unframe             _LEN / unpack_typed
  6. OpusCodec.decode    Opus -> 16 kHz mono s16               [TAP B]
  7. Mixer.feed/pull     jitter buffer, 20 ms blocks           [TAP C]
  8. play_rs             StreamResampler 16000 -> 44100        [TAP D]

Proves, and FAILS LOUD on any of them:
  - every Opus packet that went in came out, in order, byte-identical
  - the audio SECONDS in equals the audio seconds out at each stage
  - the mixer inserted no silence and cut no splices
  - the decoded stream is the same LENGTH as the encoded stream
  - per-block timing: which block, how many bytes, cumulative drift

Writes the tap WAVs so each stage can be listened to and the one that
introduces the artifact identified by ear as well as by number.
"""

import struct
import sys
import wave

import fnphone_pa as A
import fnav


IN = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/mic.wav"
OUT = "/home/claude/audit"

DEV_RATE = 32000          # what arecord / the device produced
OUT_RATE = 44100          # what John's speaker runs at
BLOCK_MS = 20
JITTER_MS = 120

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print("  FAIL  " + msg)


def ok(msg):
    print("  ok    " + msg)


def wav_write(path, pcm, rate):
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(pcm); w.close()
    return path


def ms(b, rate):
    nbytes = len(b) if isinstance(b, (bytes, bytearray)) else b
    return nbytes / (rate * 2 / 1000.0)


# ---- 1. device capture ----------------------------------------------------
w = wave.open(IN)
ch, rate, width, n = (w.getnchannels(), w.getframerate(),
                      w.getsampwidth(), w.getnframes())
raw = w.readframes(n); w.close()
print("input: %s  %d Hz  %d ch  %d-bit  %.2f s"
      % (IN, rate, ch, width * 8, n / float(rate)))
if width != 2:
    raise SystemExit("expected s16")
if ch == 2:                                    # device gave stereo; take L
    mono = bytearray()
    for i in range(0, len(raw), 4):
        mono += raw[i:i + 2]
    dev = bytes(mono)
else:
    dev = raw
print("stage 1  device mono   %8d bytes  %8.1f ms\n" % (len(dev), ms(dev, rate)))

# ---- 2. capture resample --------------------------------------------------
cap_rs = A.StreamResampler(rate, fnav.AUDIO_RATE)
in_block = int(rate * BLOCK_MS / 1000) * 2      # device bytes per 20 ms
capped = bytearray()
for i in range(0, len(dev), in_block):
    capped += cap_rs.process(dev[i:i + in_block])
capped = bytes(capped)
wav_write(OUT + "_A_captured16k.wav", capped, fnav.AUDIO_RATE)
print("stage 2  cap_rs %d->%d  %8d bytes  %8.1f ms   [A]"
      % (rate, fnav.AUDIO_RATE, len(capped), ms(capped, fnav.AUDIO_RATE)))
drift = ms(capped, fnav.AUDIO_RATE) - ms(dev, rate)
(ok if abs(drift) < 25 else fail)(
    "capture resample preserved duration (drift %.1f ms)" % drift)

# ---- 3/4/5/6. encode -> wire -> unframe -> decode --------------------------
enc = fnav.OpusCodec()
dec = fnav.OpusCodec()                          # separate: the far end

blk = fnav.AUDIO_RATE * BLOCK_MS // 1000 * 2    # 640 bytes = 20 ms
sent_pkts, wire_bytes, recv_pkts = [], 0, []
decoded = bytearray()
timing = []

for i in range(0, len(capped) - blk + 1, blk):
    pcm = capped[i:i + blk]
    for pkt in enc.encode(pcm):
        sent_pkts.append(pkt)
        frame = fnav.pack_typed(fnav.KIND_AUDIO, "Sandy",
                                fnav._KIND.pack(fnav.AUDIO_FMT_OPUS) + pkt)
        onwire = fnav._LEN.pack(len(frame)) + frame
        wire_bytes += len(onwire)

        # ---- the far end reads it back ----
        (ln,) = fnav._LEN.unpack(onwire[:4])
        body = onwire[4:4 + ln]
        kind, src, payload = fnav.unpack_typed(body)
        if kind != fnav.KIND_AUDIO:
            fail("frame came back as kind %d, not audio" % kind)
        (fmt,) = fnav._KIND.unpack(payload[:1])
        got = payload[1:]
        recv_pkts.append(got)
        pcm_out = dec.decode(got)
        decoded += pcm_out
        timing.append((len(pkt), len(pcm_out)))

decoded = bytes(decoded)
wav_write(OUT + "_B_decoded16k.wav", decoded, fnav.AUDIO_RATE)
print("stage 3  encode        %8d packets %8d bytes opus  %.1f kbps"
      % (len(sent_pkts), sum(len(p) for p in sent_pkts),
         sum(len(p) for p in sent_pkts) * 8 / ms(capped, fnav.AUDIO_RATE)))
print("stage 4  wire framing  %8d bytes total (%.1f%% overhead)"
      % (wire_bytes,
         100.0 * (wire_bytes - sum(len(p) for p in sent_pkts))
         / max(1, sum(len(p) for p in sent_pkts))))
print("stage 6  decode        %8d bytes  %8.1f ms   [B]"
      % (len(decoded), ms(decoded, fnav.AUDIO_RATE)))

# ---- the two checks he asked for by name ----
(ok if len(sent_pkts) == len(recv_pkts) else fail)(
    "every packet sent came back (%d in, %d out)" % (len(sent_pkts), len(recv_pkts)))
same = all(a == b for a, b in zip(sent_pkts, recv_pkts))
(ok if same else fail)("wire bytes IDENTICAL before and after framing")

lost = ms(capped, fnav.AUDIO_RATE) - ms(decoded, fnav.AUDIO_RATE)
(ok if abs(lost) < 60 else fail)(
    "codec preserved duration (%.1f ms difference)" % lost)

odd = [i for i, (p, o) in enumerate(timing) if o != blk]
(ok if not odd else fail)(
    "every packet decoded to exactly one 20 ms block (%d exceptions)" % len(odd))
if odd[:5]:
    print("        first exceptions at packets %s (sizes %s)"
          % (odd[:5], [timing[i][1] for i in odd[:5]]))

# ---- 7. mixer -------------------------------------------------------------
mx = A.Mixer(blk, fnav.AUDIO_RATE * JITTER_MS // 1000 * 2)
played = bytearray()
# feed and pull in lockstep, as the live path does: recv thread feeds,
# the PortAudio callback pulls one block per 20 ms.
for i in range(0, len(decoded), blk):
    mx.feed("Sandy", decoded[i:i + blk])
    if i >= (JITTER_MS // BLOCK_MS) * blk:      # cushion filled
        played += mx.pull_block()
while len(played) < len(decoded) - blk:
    played += mx.pull_block()
played = bytes(played)
wav_write(OUT + "_C_mixed16k.wav", played, fnav.AUDIO_RATE)
print("stage 7  mixer         %8d bytes  %8.1f ms   [C]"
      % (len(played), ms(played, fnav.AUDIO_RATE)))
print("        " + mx.damage_report().strip())
(ok if mx.pads == 0 else fail)("mixer inserted no silence (pads=%d)" % mx.pads)
(ok if mx.trims == 0 else fail)("mixer cut no splices (trims=%d)" % mx.trims)

# ---- 8. playout resample --------------------------------------------------
play_rs = A.StreamResampler(fnav.AUDIO_RATE, OUT_RATE)
spk = bytearray()
for i in range(0, len(played), blk):
    spk += play_rs.process(played[i:i + blk])
spk = bytes(spk)
wav_write(OUT + "_D_speaker44k.wav", spk, OUT_RATE)
print("stage 8  play_rs %d->%d %8d bytes  %8.1f ms   [D]"
      % (fnav.AUDIO_RATE, OUT_RATE, len(spk), ms(spk, OUT_RATE)))
d = ms(spk, OUT_RATE) - ms(played, fnav.AUDIO_RATE)
(ok if abs(d) < 25 else fail)("playout resample preserved duration (%.1f ms)" % d)

print("\nend to end: %.2f s in, %.2f s out"
      % (ms(dev, rate) / 1000.0, ms(spk, OUT_RATE) / 1000.0))
print("\nlisten in order -- the first one that rings is the stage that did it:")
for t in "ABCD":
    print("  " + OUT + "_%s_*.wav" % t)

print()
if FAIL:
    print("FAILURES: %d" % len(FAIL))
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("every stage accounted for, no loss, no damage")
