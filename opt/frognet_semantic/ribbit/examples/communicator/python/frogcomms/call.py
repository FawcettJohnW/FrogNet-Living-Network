# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""A call through the media server: two planes (audio, video), the bearer and the bottom walk on the send side,
reassembly, decoding and the mixer on the receive side. The same wire and the same rules as src/comms/call.cpp."""
import random, string, threading, time
from collections import deque

from . import fnav as F
from .audio import BLOCK_BYTES, BLOCK_MS, FMT_OPUS, AUDIO_RATE, Mixer, Opus, pack_audio
from .media import SIZES, Decoder, Encoder, ceiling_for, size_for_rung


class Stats:                                         # cumulative; each reader diffs
    def __init__(self):
        for k in ("v_sent v_dropped v_bytes keys_sent keyreqs v_recv v_rx_bytes decode_errors a_sent a_shed a_recv "
                  "a_refused a_bytes a_rx_bytes down_lag_ms down_steps up_lag_ms up_steps").split():
            setattr(self, k, 0)
        self.rung = self.send_w = self.send_h = self.ceiling = self.stressed = self.clean = self.sec_drops = 0


class Picture:
    def __init__(self, frame, level, seq):
        self.frame, self.level, self.seq = frame, level, seq
        self.w, self.h = frame.width, frame.height


class DelayLine:
    """[JITTER_IS_THE_LINK_V1] the receive side of the jitter: a reader drains the socket continuously and stamps each
    frame with arrival + random 0..N ms, never before the frame ahead of it (TCP does not reorder)."""

    def __init__(self, plane, call):
        self.p, self.call = plane, call
        self.q, self.cv, self.closed = deque(), threading.Condition(), False
        self.t = threading.Thread(target=self._read, daemon=True); self.t.start()

    def _read(self):
        last = time.monotonic()
        try:
            while not self.call._stop:
                st, body = self.p.recv(0.2)
                if st == F.CLOSED:
                    break
                if st != F.FRAME:
                    continue
                j = self.call.jitter_ms
                release = max(last, time.monotonic() + (random.randint(0, j) / 1000.0 if j > 0 else 0.0))
                last = release
                with self.cv:
                    if len(self.q) < 4000:
                        self.q.append((release, body))
                    self.cv.notify()
        except F.ConnectionError_:
            pass
        with self.cv:
            self.closed = True; self.cv.notify_all()

    def get(self, timeout_s):
        deadline = time.monotonic() + timeout_s
        with self.cv:
            while True:
                now = time.monotonic()
                if self.q and self.q[0][0] <= now:
                    return self.q.popleft()[1]
                if not self.q and self.closed:
                    raise F.ConnectionError_("closed")
                if now >= deadline:
                    return None
                self.cv.wait(min(deadline, self.q[0][0] if self.q else deadline) - now)


class Call:
    def __init__(self, host, port, name, session, source=None, fps=24, mic=None, speaker=None, cushion_ms=120):
        self.name, self.session, self.fps = name, session, fps
        self.src, self.mic, self.spk = source, mic, speaker
        self.mixer = Mixer(BLOCK_BYTES, AUDIO_RATE * cushion_ms // 1000 * 2)
        self.opus = Opus()
        self.stats = Stats()
        self.jitter_ms, self.throttle_bps, self.gain = 0, 0, 1.0
        self._stop, self._want_key, self._audio_shed = False, False, 0
        self._pics, self._self, self._pmu = {}, None, threading.Lock()
        tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8)).encode()   # one teardown tag, both planes

        def open_plane(kind):
            p = F.Plane(F.Plane.connect(host, port, 8.0))     # [A_HANG_IS_THE_WORST_REPORT_V1]
            p.send(F.pack_typed(F.KIND_PLANE, name, kind + tag + session.encode()), F.CONTROL)
            return p
        self.vplane = open_plane(F.PLANE_VIDEO)
        self.aplane = open_plane(F.PLANE_AUDIO)              # [TWO_PLANES_V1]
        self._threads = []

    # -- controls ------------------------------------------------------------------------------------------------
    def set_throttle(self, bps): self.throttle_bps = max(0, int(bps))
    def set_jitter(self, ms): self.jitter_ms = max(0, int(ms))
    def set_volume(self, g): self.gain = min(1.5, max(0.0, float(g)))

    def latest(self, src):
        with self._pmu:
            return self._pics.get(src)

    def self_view(self):
        with self._pmu:
            return self._self

    def sources(self):
        with self._pmu:
            return list(self._pics)

    def start(self):
        self.vdl, self.adl = DelayLine(self.vplane, self), DelayLine(self.aplane, self)
        for fn, on in ((self._video_tx, self.src is not None), (self._video_rx, True), (self._audio_tx, self.mic is not None),
                       (self._audio_rx, True), (self._playout, self.spk is not None)):
            if on:
                t = threading.Thread(target=fn, daemon=True); t.start(); self._threads.append(t)

    def stop(self):
        if self._stop:
            return
        self._stop = True
        self.vplane.shutdown_both(); self.aplane.shutdown_both()
        for t in self._threads:
            t.join(timeout=3)
        self.vplane.close(); self.aplane.close()

    # -- audio ---------------------------------------------------------------------------------------------------
    def _audio_tx(self):                                     # 20 ms blocks, Opus, format byte first
        try:
            while not self._stop:
                pcm = self.mic.next()
                if pcm is None:
                    continue
                for pkt in self.opus.encode(pcm):
                    body = F.pack_typed(F.KIND_AUDIO, self.name, pack_audio(FMT_OPUS, pkt))
                    if self.aplane.send(body, F.AUDIO) == F.WHOLE:
                        self.stats.a_sent += 1; self.stats.a_bytes += len(body)
                    else:
                        self.stats.a_shed += 1; self._audio_shed += 1
        except F.ConnectionError_:
            pass

    def _audio_rx(self):
        try:
            while not self._stop:
                body = self.adl.get(0.2)
                if body is None:
                    continue
                try:
                    kind, src, p = F.unpack_typed(body)
                except F.FrameError:
                    continue
                if kind == F.KIND_AUDIO_BACKPRESSURE:
                    self.stats.a_refused += 1
                    continue
                if kind != F.KIND_AUDIO or not p:
                    continue
                self.stats.a_recv += 1; self.stats.a_rx_bytes += len(body)   # [RECEIVE_IS_MEASURED_V1] before the mixer
                if p[0] != FMT_OPUS:
                    continue                                 # [AUDIO_IS_OPUS_V1] refused, not guessed
                try:
                    self.mixer.feed(src, self.opus.decode(src, p[1:]))
                except Exception:                            # one packet's problem only
                    continue
        except F.ConnectionError_:
            pass

    def _playout(self):
        import numpy as np
        nxt = time.monotonic()
        while not self._stop:
            b = self.mixer.pull_block()
            if self.gain != 1.0:                             # the listener's volume, clipped, never wrapped
                a = np.frombuffer(b, np.int16).astype(np.float32) * self.gain
                b = np.clip(a, -32768, 32767).astype(np.int16).tobytes()
            self.spk.play(b)
            nxt += BLOCK_MS / 1000.0
            time.sleep(max(0.0, nxt - time.monotonic()))

    # -- video ---------------------------------------------------------------------------------------------------
    def _video_tx(self):
        first = self.src.next()
        while first is None and not self._stop:
            first = self.src.next()
        if first is None:
            return
        sw, sh = first.width, first.height
        bearer = F.Bearer(ceiling_for(sw, sh), 0, time.time())
        enc, enc_size = None, -2
        bucket, bucket_t = 0.0, time.time()
        sent_times = deque()
        WARMUP_FRAMES, WARMUP_S = 15, 2.0
        rung_frames, rung_t0 = 0, time.time()
        stress_from = clean_from = -1.0
        BOTTOM_FIRST, BOTTOM_LAST, BOTTOM_STEP_S, WINDOW_S, BOTTOM_CLEAN_WINDOWS = 3, 6, 2.0, 2.0, 3
        bottom = bottom_ok = win_refused = 0
        bottom_at, win_t0 = 0.0, time.time()
        in_bottom = shed = False
        frame = first
        st = self.stats
        try:
            while not self._stop:
                deadline = time.monotonic() + 1.0 / self.fps            # [NO_CATCH_UP_BURST_V1]
                if frame is None:
                    frame = self.src.next()
                if frame is None:
                    continue
                with self._pmu:
                    self._self = Picture(frame, 0, (self._self.seq + 1) if self._self else 1)
                # [BOTTOM_RUNG_SHRINKS_V1] below L5 the picture gets smaller, audio only after 160x120 is refused
                if shed:
                    size = -1
                elif in_bottom or bottom > 0:
                    size = BOTTOM_FIRST + bottom
                else:
                    size = size_for_rung(bearer.idx, sw, sh)
                st.rung = bearer.idx if shed else 5 if (in_bottom or bottom > 0) else bearer.idx
                dropped = 0
                if size >= 0:
                    if size != enc_size:
                        enc, enc_size = Encoder(SIZES[size], self.fps), size
                    st.send_w, st.send_h = SIZES[size].w, SIZES[size].h
                    force, self._want_key = self._want_key, False
                    for vp in enc.encode(frame, force):
                        key = F.unpack_video(vp)[1]
                        if self.jitter_ms:
                            time.sleep(random.randint(0, self.jitter_ms) / 1000.0)
                        bps = self.throttle_bps
                        if bps:                                  # DEMO: a smaller wire -- frames over budget are shed
                            t = time.time()
                            bucket = min(bps / 8.0, bucket + (t - bucket_t) * bps / 8.0); bucket_t = t
                            if len(vp) > bucket and not key:
                                dropped += 1; st.v_dropped += 1
                                continue
                            bucket -= len(vp)
                        if self.vplane.send_video(self.name, vp, key):
                            st.v_sent += 1; st.v_bytes += len(vp); st.keys_sent += int(key)
                            sent_times.append(time.time()); rung_frames += 1
                        else:
                            dropped += 1; st.v_dropped += 1; self._want_key = self._want_key or key
                else:
                    enc, enc_size, st.send_w, st.send_h = None, -2, 0, 0
                frame = None
                t = time.time()
                while sent_times and t - sent_times[0] > 1.0:
                    sent_times.popleft()
                fps_sent = float(len(sent_times)) if (size >= 0 and rung_frames >= WARMUP_FRAMES and t - rung_t0 >= WARMUP_S) else None
                win_refused += dropped
                if t - win_t0 >= WINDOW_S:
                    bottom_ok = 0 if win_refused else bottom_ok + 1
                    win_refused, win_t0 = 0, t
                before = bearer.idx
                if (dropped or bearer.bad_reads or bearer.sec_drops) and stress_from < 0:
                    stress_from = t
                ashed, self._audio_shed = self._audio_shed, 0
                bearer.sample(t, 0, dropped, fps_sent, float(self.fps), ashed)
                st.ceiling, st.stressed, st.clean, st.sec_drops = bearer.ceiling, bearer.bad_reads, bearer.clean_secs, bearer.sec_drops
                if bearer.clean_secs and clean_from < 0:
                    clean_from = t
                if bearer.idx < before:
                    if stress_from >= 0:
                        st.down_lag_ms += int((t - stress_from) * 1000); st.down_steps += 1
                    stress_from = clean_from = -1.0
                elif bearer.idx > before:
                    if clean_from >= 0:
                        st.up_lag_ms += int((t - clean_from) * 1000); st.up_steps += 1
                    stress_from = clean_from = -1.0
                else:
                    if not (dropped or bearer.bad_reads or bearer.sec_drops):
                        stress_from = -1.0
                    if not bearer.clean_secs:
                        clean_from = -1.0
                r = bearer.idx
                if shed:
                    if r >= 5:
                        shed, in_bottom, bottom, bottom_at, bottom_ok = False, True, BOTTOM_LAST - BOTTOM_FIRST, t, 0
                        bearer.resume_at(5, t)
                elif r < 5:
                    if not in_bottom:
                        in_bottom, bottom_at = True, t
                    elif dropped and t - bottom_at >= BOTTOM_STEP_S:
                        if BOTTOM_FIRST + bottom < BOTTOM_LAST:
                            bottom += 1; bottom_at = t; bottom_ok = 0
                        else:
                            shed, in_bottom, bottom = True, False, 0   # 160x120 refused too: audio only
                elif r > 5 and bottom > 0:
                    if bottom_ok >= BOTTOM_CLEAN_WINDOWS and t - bottom_at >= BOTTOM_STEP_S:
                        bottom -= 1; bottom_at = t; bottom_ok = 0
                        if bottom == 0:
                            in_bottom = False; bearer.resume_at(5, t)
                    else:
                        bearer.resume_at(5, t)                   # hold the ladder at L5 while the picture grows
                elif r >= 5 and bottom == 0:
                    in_bottom = False
                if bearer.idx != before:                         # [RATE_IS_MEASURED_AT_THIS_RUNG_V1]
                    sent_times.clear(); rung_frames, rung_t0 = 0, t
                time.sleep(max(0.0, deadline - time.monotonic()))
        except F.ConnectionError_:
            pass

    def _video_rx(self):
        reasm, decs, st = F.Reassembler(), {}, self.stats
        try:
            while not self._stop:
                body = self.vdl.get(0.2)
                if body is None:
                    continue
                try:
                    kind, src, p = F.unpack_typed(body)
                except F.FrameError:
                    continue
                if kind == F.KIND_KEYREQ:                       # [KEYFRAME_ON_REQUEST_V1]
                    self._want_key = True; st.keyreqs += 1
                    continue
                payload = p if kind == F.KIND_VIDEO else reasm.feed(src, p) if kind == F.KIND_VSEG else None
                if payload is None:
                    continue
                try:
                    level, key, codec, packet = F.unpack_video(payload)
                except F.FrameError:
                    continue
                st.v_recv += 1; st.v_rx_bytes += len(payload)   # [RX_IS_PER_SOURCE_V1] counted before decoding
                d = decs.get(src) or decs.setdefault(src, Decoder())
                try:
                    frames = d.decode(packet)
                except Exception:                               # one frame's problem only
                    st.decode_errors += 1
                    continue
                for f in frames:
                    with self._pmu:
                        old = self._pics.get(src)
                        self._pics[src] = Picture(f, level, (old.seq + 1) if old else 1)
        except F.ConnectionError_:
            pass
