# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""Audio: the wire format (16 kHz mono s16le, 20 ms blocks, Opus 24 kb/s), the mixer, sources and devices."""
import math, threading, time
from collections import OrderedDict, deque

import av
import numpy as np

AUDIO_RATE, BLOCK_MS = 16000, 20
BLOCK_SAMPLES = AUDIO_RATE * BLOCK_MS // 1000      # 320
BLOCK_BYTES = BLOCK_SAMPLES * 2                     # 640
FMT_PCM, FMT_OPUS = 0, 1
OPUS_BITRATE = 24000


def pack_audio(fmt, data):
    return bytes([fmt]) + data


class Opus:
    """[AUDIO_IS_OPUS_V1] one encoder and one decoder per direction. A packet that cannot be decoded is refused."""

    def __init__(self):
        e = self.enc = av.CodecContext.create("libopus", "w")
        e.sample_rate, e.layout, e.format, e.bit_rate = AUDIO_RATE, "mono", "s16", OPUS_BITRATE
        e.open()
        self.n = 0
        self.decs = {}

    def encode(self, pcm):
        """one 20 ms block of s16le -> Opus packets (one, normally)"""
        f = av.AudioFrame.from_ndarray(np.frombuffer(pcm, np.int16).reshape(1, -1), format="s16", layout="mono")
        f.sample_rate = AUDIO_RATE
        f.pts = self.n; self.n += BLOCK_SAMPLES
        return [bytes(p) for p in self.enc.encode(f)]

    def decode(self, src, pkt):
        # PyAV's Opus decoder decodes at Opus's native 48 kHz whatever rate is asked for; the wire is 16 kHz s16 mono,
        # so every decoded frame goes through a resampler (found 2026-10-02: a 440 Hz tone arrived at 146.7 Hz, 1/3).
        d = self.decs.get(src)
        if d is None:
            d = self.decs[src] = (av.CodecContext.create("libopus", "r"),
                                  av.AudioResampler(format="s16", layout="mono", rate=AUDIO_RATE))
        out = b""
        for f in d[0].decode(av.Packet(pkt)):
            for r in d[1].resample(f):
                out += r.to_ndarray().reshape(-1).astype(np.int16).tobytes()
        return out


class Mixer:
    """fnphone_pa's mixer (as the C++ Mixer): one buffer per remote source, a playout cushion per source, the backlog
    capped at 2x the cushion (at least 240 ms); trims and pads COUNTED ([MIXER_DAMAGE_IS_COUNTED_V1])."""

    def __init__(self, block_bytes=BLOCK_BYTES, jitter_bytes=AUDIO_RATE * 120 // 1000 * 2, max_buffer_bytes=0):
        self.block, self.jitter = block_bytes, jitter_bytes
        self.max = max_buffer_bytes or max(AUDIO_RATE * 240 // 1000 * 2, jitter_bytes * 2)
        self.bufs, self.started = OrderedDict(), {}
        self.trimmed = self.trims = self.pads = self.pad_bytes = self.dry = self.out_bytes = 0
        self.mu = threading.Lock()

    def feed(self, src, pcm):
        with self.mu:
            if src not in self.bufs:
                self.bufs[src], self.started[src] = bytearray(), False
            b = self.bufs[src]
            b += pcm
            if len(b) > self.max:
                drop = len(b) - self.max
                del b[:drop]
                self.trimmed += drop; self.trims += 1

    def pull_block(self):
        chunks, short = [], 0
        with self.mu:
            for src, b in self.bufs.items():
                if not self.started[src]:
                    if len(b) < self.jitter:
                        continue                         # still filling the cushion -> silence
                    self.started[src] = True
                if len(b) >= self.block:
                    chunks.append(bytes(b[:self.block])); del b[:self.block]
                elif b:
                    short += self.block - len(b); chunks.append(bytes(b)); b.clear()
                else:
                    self.dry += 1
            if short:
                self.pads += 1; self.pad_bytes += short
        acc = np.zeros(self.block // 2, np.int32)
        for c in chunks:
            a = np.frombuffer(c, np.int16)
            acc[:len(a)] += a
        out = np.clip(acc, -32768, 32767).astype(np.int16).tobytes()
        self.out_bytes += len(out)
        return out


class ToneSource:
    def __init__(self, hz=440.0, amp=8000):
        self.hz, self.amp, self.n = hz, amp, 0
        self.t0 = None

    def next(self):
        if self.t0 is None:
            self.t0 = time.monotonic()
        due = self.t0 + (self.n / BLOCK_SAMPLES) * BLOCK_MS / 1000.0
        if due > time.monotonic():
            time.sleep(due - time.monotonic())           # a tone arrives in real time, as a microphone does
        i = np.arange(self.n, self.n + BLOCK_SAMPLES)
        self.n += BLOCK_SAMPLES
        return (self.amp * np.sin(2 * math.pi * self.hz * i / AUDIO_RATE)).astype(np.int16).tobytes()


def true_rate(measured, nominal):
    """[ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] the nominal rate if the measurement is within 10% of it, else the nearest
    standard rate (the eMeet C950 says 44100 and delivers ~16000)."""
    if measured <= 0 or nominal <= 0 or abs(measured - nominal) <= 0.10 * nominal:
        return nominal
    return min((8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000), key=lambda r: abs(r - measured))


class Resampler:
    """A streaming linear resampler for mono s16 (the C++ Resampler)."""

    def __init__(self, rate_in, rate_out):
        self.step, self.pos, self.last = rate_in / rate_out, 0.0, None

    def push(self, x):
        out = []
        for s in np.asarray(x, np.int16):
            s = int(s)
            if self.last is None:
                self.last = s
                continue
            while self.pos < 1.0:
                out.append(int(self.last + (s - self.last) * self.pos))
                self.pos += self.step
            self.pos -= 1.0
            self.last = s
        return np.array(out, np.int16)


class Mic:
    """A microphone through sounddevice, opened at ITS OWN rate and resampled to 16 kHz; what it really delivers is
    measured (0.5-2.5 s after start) and, if it is not what it claims, resampled from that."""

    def __init__(self, device=None):
        import sounddevice as sd
        info = sd.query_devices(device, "input")
        self.rate = float(info["default_samplerate"])
        self.rs = Resampler(self.rate, AUDIO_RATE)
        self.acc, self.q, self.cv = np.zeros(0, np.int16), deque(maxlen=25), threading.Condition()
        self.t0, self.win, self.settled = time.monotonic(), 0, False
        self.captured = self.overflowed = 0
        self.stream = sd.InputStream(device=device, samplerate=self.rate, channels=1, dtype="int16",
                                     blocksize=int(self.rate * BLOCK_MS / 1000), callback=self._cb)
        self.stream.start()
        print("[audio] mic %s at %d Hz, resampled to %d Hz" % (info["name"], self.rate, AUDIO_RATE), flush=True)

    def _cb(self, indata, frames, t, status):
        if status.input_overflow:
            self.overflowed += 1
        el = time.monotonic() - self.t0
        if not self.settled:
            if el >= 0.5:
                self.win += frames
            if el >= 2.5:
                self.settled = True
                measured = self.win / (el - 0.5)
                real = true_rate(measured, self.rate)
                msg = "[audio] mic measured: %d samples/s from a device that says %d" % (measured, self.rate)
                if real != self.rate:
                    print(msg + " -- it does not deliver what it says: resampling from %d" % real, flush=True)
                    self.rs, self.rate = Resampler(real, AUDIO_RATE), real
                else:
                    print(msg + " (as it says)", flush=True)
        self.acc = np.concatenate([self.acc, self.rs.push(indata[:, 0])])
        with self.cv:
            while len(self.acc) >= BLOCK_SAMPLES:
                if len(self.q) == self.q.maxlen:
                    self.overflowed += 1                 # 0.5 s behind: counted, newest wins
                self.q.append(self.acc[:BLOCK_SAMPLES].tobytes()); self.acc = self.acc[BLOCK_SAMPLES:]
                self.captured += 1
            self.cv.notify()

    def next(self):
        with self.cv:
            if not self.cv.wait_for(lambda: self.q, 0.5):
                return None
            return self.q.popleft()

    def close(self):
        self.stream.stop(); self.stream.close()


class Speaker:
    """A speaker through sounddevice at ITS OWN rate, fed 16 kHz blocks resampled up."""

    def __init__(self, device=None):
        import sounddevice as sd
        info = sd.query_devices(device, "output")
        self.rate = float(info["default_samplerate"])
        self.rs = Resampler(AUDIO_RATE, self.rate)
        self.q, self.mu = deque(), threading.Lock()
        self.underruns = self.dropped = 0
        self.stream = sd.OutputStream(device=device, samplerate=self.rate, channels=1, dtype="int16",
                                      blocksize=int(self.rate * BLOCK_MS / 1000), callback=self._cb)
        self.stream.start()
        print("[audio] speaker %s at %d Hz, resampled from %d Hz" % (info["name"], self.rate, AUDIO_RATE), flush=True)

    def play(self, block):
        y = self.rs.push(np.frombuffer(block, np.int16))
        with self.mu:
            if len(self.q) + len(y) > self.rate * 0.5:
                self.dropped += 1
                return
            self.q.extend(y.tolist())

    def _cb(self, outdata, frames, t, status):
        with self.mu:
            n = min(frames, len(self.q))
            out = [self.q.popleft() for _ in range(n)]
        if n < frames:
            out += [0] * (frames - n); self.underruns += 1
        outdata[:, 0] = np.array(out, np.int16)

    def close(self):
        self.stream.stop(); self.stream.close()


def devices():
    """A listing for --list-devices."""
    import sounddevice as sd
    return "\n".join("%d: %s  (in %d, out %d, %d Hz)" % (i, d["name"], d["max_input_channels"], d["max_output_channels"],
                     d["default_samplerate"]) for i, d in enumerate(sd.query_devices()))
