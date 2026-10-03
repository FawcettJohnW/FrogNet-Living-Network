# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""The fnav wire in Python (FNAV-SPEC.md): frames, the data plane, segmentation, the bearer.

  frame = [len u32 BE][kind u8][srclen u16 BE][src utf-8][payload]
  VIDEO = [level u32 BE: rung, high bit = keyframe][codec u8][codec packet]
  VSEG  = [frame_id u16 BE][index u16 BE][flags u8] + piece
A frame that cannot be finished is padded so that it ENDS with DE AD BE EF ([ABORT_PAD_ENDS_WITH_THE_SENTINEL_V1])."""
import errno, math, select, socket, struct, sys, threading, time

KIND_AUDIO, KIND_VIDEO, KIND_BACKPRESSURE, KIND_PLANE, KIND_KEYREQ, KIND_AUDIO_BACKPRESSURE, KIND_VSEG = range(7)
PLANE_AUDIO, PLANE_VIDEO = b"A", b"V"
KEYFRAME_BIT = 0x80000000
VSEG_LAST, VSEG_ABORT = 0x01, 0x02
VSEG_BYTES, VSEG_WHOLE_MAX = 1368, 8192
ABORT_SENTINEL = b"\xde\xad\xbe\xef"
CODEC_VP8, CODEC_H264_SW, CODEC_H264_HW = 0, 1, 2


class FrameError(Exception):
    """A malformed frame: refused, never read as a default."""


class ConnectionError_(Exception):
    """The connection is finished: closed, reset, or desynchronised."""


# -- the frame layer -------------------------------------------------------------------------------------------
def pack_typed(kind, src, payload):
    s = src.encode() if isinstance(src, str) else src
    if len(s) > 0xFFFF:
        raise FrameError("source name longer than 65535 bytes")
    return struct.pack(">BH", kind, len(s)) + s + payload


def unpack_typed(body):
    if not body:
        raise FrameError("empty frame")
    if len(body) < 3:
        raise FrameError("short frame: %d bytes" % len(body))
    kind, n = struct.unpack_from(">BH", body)
    if 3 + n > len(body):
        raise FrameError("source length %d past the end of the frame" % n)
    return kind, body[3:3 + n].decode("utf-8", "replace"), body[3 + n:]


def pack_video(level, packet, codec, key):
    return struct.pack(">IB", (level & 0x7FFFFFFF) | (KEYFRAME_BIT if key else 0), codec) + packet


def unpack_video(p):
    if len(p) < 5:
        raise FrameError("video payload shorter than its 5-byte header: %d" % len(p))
    raw, codec = struct.unpack_from(">IB", p)
    return raw & 0x7FFFFFFF, bool(raw & KEYFRAME_BIT), codec, p[5:]


def video_is_key(body):
    n = struct.unpack_from(">H", body, 1)[0]
    return bool(struct.unpack_from(">I", body, 3 + n)[0] & KEYFRAME_BIT)


def pack_seg(fid, idx, flags, piece):
    return struct.pack(">HHB", fid, idx, flags) + piece


def unpack_seg(p):
    if len(p) < 5:
        raise FrameError("segment payload shorter than its 5-byte header: %d" % len(p))
    fid, idx, flags = struct.unpack_from(">HHB", p)
    return fid, idx, flags, p[5:]


def with_length(body):
    return struct.pack(">I", len(body)) + body


def is_abort(body):
    return len(body) >= 4 and body[-4:] == ABORT_SENTINEL


def abort_pad(remaining):
    """The padding for the rest of a part-written frame, aligned so that it ENDS with the sentinel."""
    return bytes(ABORT_SENTINEL[3 - (i % 4)] for i in range(remaining))[::-1]


# -- segmentation ----------------------------------------------------------------------------------------------
class RateMeter:
    """Bytes the socket ACCEPTED per second: fnav.py _note_sent, exactly (a = 1 - exp(-dt / 2 s))."""
    HALFLIFE_S = 2.0

    def __init__(self):
        self.rate, self.last = 0.0, -1.0

    def on_accepted(self, nbytes, now):
        if self.last < 0:
            self.last, self.rate = now, 0.0
            return
        dt, self.last = now - self.last, now
        if dt <= 0:
            return
        a = 1.0 - math.exp(-dt / self.HALFLIFE_S)
        self.rate = (1.0 - a) * self.rate + a * (nbytes / dt)


def whole_frame_max(rate, sndbuf):
    """[THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] what the socket takes in one 20 ms audio block at its accepted rate."""
    if rate <= 0:
        return VSEG_BYTES
    return int(max(VSEG_BYTES, min(sndbuf // 2, rate * 0.020)))


class Reassembler:
    def __init__(self):
        self.cur = {}
        self.done = self.aborted = self.partial = self.gap = self.superseded = self.bad = 0

    def feed(self, src, p):
        """One VSEG payload; returns the completed VIDEO payload or None."""
        if len(p) < 5:
            self.bad += 1
            return None
        fid, idx, flags, piece = unpack_seg(p)
        c = self.cur.get(src)
        if flags & VSEG_ABORT:
            if c is not None:
                del self.cur[src]; self.aborted += 1
            return None
        if c is None or c[0] != fid:
            if idx != 0:
                self.partial += 1; self.cur.pop(src, None)
                return None
            if c is not None:
                self.superseded += 1
            c = self.cur[src] = [fid, 1, bytearray(piece)]
        else:
            if idx != c[1]:
                self.gap += 1; del self.cur[src]
                return None
            c[2] += piece; c[1] += 1
        if flags & VSEG_LAST:
            del self.cur[src]; self.done += 1
            return bytes(c[2])
        return None


# -- the data plane --------------------------------------------------------------------------------------------
WHOLE, DROPPED, ABORTED = "whole", "dropped", "aborted"
FRAME, ABORTED_IN, CLOSED, TIMEOUT = "frame", "aborted", "closed", "timeout"
AUDIO, KEYFRAME, VIDEO, CONTROL = "audio", "keyframe", "video", "control"


def _unsent(sock):
    """Bytes the kernel still holds (Linux SIOCOUTQ); None where that cannot be known (Windows)."""
    if not sys.platform.startswith("linux"):
        return None
    import fcntl, termios
    try:
        return struct.unpack("i", fcntl.ioctl(sock.fileno(), termios.TIOCOUTQ, b"\0\0\0\0"))[0]
    except OSError:
        return None


class Plane:
    """One fnav plane: a non-blocking TCP connection carrying audio or video. Whole frames only; the room is checked
    before the first byte; a part-written frame is finished within 250 ms or completed with abort padding."""
    SNDBUF = 1 << 16 if sys.platform == "win32" else 1 << 17
    AUDIO_RETRIES, KEY_RETRIES, RETRY_WAIT, FINISH_S = 2, 3, 0.008, 0.25

    def __init__(self, sock):
        self.s = sock
        self.s.setblocking(False)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.SNDBUF)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 0, 0))
        self.wlock = threading.Lock()
        self.rate = RateMeter()
        self.vseg_id = 0
        self.frames_sent = self.bytes_sent = self.dropped = self.aborted_out = 0
        self.frames_recv = self.bytes_recv = self.aborted_in = self.frames_shed = 0

    @staticmethod
    def connect(host, port, timeout_s=8.0):
        try:
            return socket.create_connection((host, port), timeout=timeout_s)   # [A_HANG_IS_THE_WORST_REPORT_V1]
        except OSError as e:
            raise ConnectionError_("connect %s:%d: %s" % (host, port, e))

    def shutdown_both(self):
        try:
            self.s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self):
        self.shutdown_both()
        self.s.close()

    def _writable(self, t):
        return bool(select.select([], [self.s], [], t)[1])

    def _readable(self, t):
        return bool(select.select([self.s], [], [], t)[0])

    def send_room(self):
        """[DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] conservative, as fnav._send_room: a shortage only when the
        queue is over three quarters of the cap; when the numbers disagree, believe the socket. None = unknown."""
        cap = self.s.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        used = _unsent(self.s)
        if used is None or cap <= 0 or used >= cap:
            return None
        return cap if used * 4 < cap * 3 else cap - used

    def _try_send(self, data):
        try:
            return self.s.send(data)
        except (BlockingIOError, InterruptedError):
            return 0
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return 0
            raise ConnectionError_("send: %s" % e)

    def send(self, body, kind=VIDEO):
        blob = with_length(body)
        tries = 1 + self.AUDIO_RETRIES if kind == AUDIO else self.KEY_RETRIES if kind == KEYFRAME else 1
        with self.wlock:
            sent = 0
            for t in range(tries):
                if t and not self._writable(self.RETRY_WAIT):
                    continue                                # [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]
                room = self.send_room()
                if room is not None and room < len(blob):
                    continue                                # not committed: nothing written
                sent = self._try_send(blob)
                if sent:
                    break
            if not sent:
                self.dropped += 1
                return DROPPED
            t0 = time.monotonic()                           # [WHOLE_FRAME_SEND_V1] committed: finish, bounded
            while sent < len(blob) and time.monotonic() - t0 < self.FINISH_S:
                if self._writable(0.01):
                    sent += self._try_send(blob[sent:])
            if sent == len(blob):
                self.frames_sent += 1; self.bytes_sent += len(blob)
                self.rate.on_accepted(len(blob), time.time())
                return WHOLE
            pad, off, t1 = abort_pad(len(blob) - sent), 0, time.monotonic()   # [ABORT_SENTINEL_V1]
            while off < len(pad):
                if time.monotonic() - t1 >= self.FINISH_S:
                    raise ConnectionError_("a %d byte frame: %d/%d written and the abort padding would not go either; "
                                           "the stream is desynced" % (len(body), sent, len(blob)))
                if self._writable(0.01):
                    off += self._try_send(pad[off:])
            self.aborted_out += 1
            return ABORTED

    def send_video(self, src, vp, key):
        """A VIDEO payload: whole if it fits whole_frame_max() at the accepted rate, else VSEG pieces."""
        kind = KEYFRAME if key else VIDEO
        if len(vp) <= whole_frame_max(self.rate.rate, self.SNDBUF * 2):
            if self.send(pack_typed(KIND_VIDEO, src, vp), kind) == WHOLE:
                return True
            self.frames_shed += 1
            return False
        self.vseg_id = (self.vseg_id + 1) & 0xFFFF
        fid, idx, off = self.vseg_id, 0, 0
        while off < len(vp):
            piece = vp[off:off + VSEG_BYTES]; off += len(piece)
            flags = VSEG_LAST if off >= len(vp) else 0
            if self.send(pack_typed(KIND_VSEG, src, pack_seg(fid, idx, flags, piece)), kind) != WHOLE:
                self.send(pack_typed(KIND_VSEG, src, pack_seg(fid, idx, VSEG_ABORT, b"")), AUDIO)   # one shed
                self.frames_shed += 1
                return False
            idx += 1
        return True

    def _read_exact(self, n, mid_frame):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.s.recv(n - len(buf))
            except (BlockingIOError, InterruptedError):
                self._readable(0.1)
                continue
            except OSError as e:
                raise ConnectionError_("recv: %s" % e)
            if not chunk:                                   # [RX_SAYS_WHERE_V1]
                if not mid_frame and not buf:
                    raise ConnectionError_("closed")
                raise ConnectionError_("peer closed mid-frame: %d/%d bytes (truncation, not a hang-up)" % (len(buf), n))
            buf += chunk
        return bytes(buf)

    def recv(self, timeout_s):
        """(state, body): FRAME, ABORTED_IN (an aborted frame, consumed), CLOSED (between frames), TIMEOUT."""
        if not self._readable(timeout_s):
            return TIMEOUT, b""
        try:
            hdr = self._read_exact(4, False)
        except ConnectionError_ as e:
            if str(e) == "closed":
                return CLOSED, b""
            raise
        n = struct.unpack(">I", hdr)[0]
        body = self._read_exact(n, True)                    # the declared length is ALWAYS consumed
        if is_abort(body):
            self.aborted_in += 1
            return ABORTED_IN, b""
        self.frames_recv += 1; self.bytes_recv += 4 + n
        return FRAME, body


# -- the bearer: John's rule, 2026-08-03 ([LADDER_RATE_V2]) ------------------------------------------------------
class Bearer:
    BAD_READS, FPS_DOWN_READS, SEC_DROPS, CLEAN_SECS, BACKLOG_DIRTY = 3, 3, 5, 3, 8
    FPS_DOWN, FPS_DOWN_FRAC, FULL_RATE_FRAC = 10.0, 0.75, 0.98
    HOLD_BASE_S, HOLD_MAX_S, HOLD_CLEAR_S = 15.0, 300.0, 60.0

    def __init__(self, ceiling, floor, now):
        self.idx, self.ceiling, self.floor = ceiling, ceiling, floor
        self.bad_reads = self.slow_reads = self.sec_drops = self.clean_secs = 0
        self.sec_fps_min = None
        self.sec_t0 = self.held_since = now
        self.fails, self.blocked = {}, {}
        self.reason = ""

    def sample(self, now, backlog, dropped, fps_sent, fps_target, audio_shed=0):
        dirty = dropped > 0 or backlog >= self.BACKLOG_DIRTY
        self.bad_reads = self.bad_reads + 1 if dirty else 0
        fps_floor = self.FPS_DOWN
        if fps_target:
            fps_floor = max(fps_floor, fps_target * self.FPS_DOWN_FRAC)
        self.slow_reads = self.slow_reads + 1 if (fps_sent is not None and fps_sent < fps_floor) else 0
        self.sec_drops += dropped
        if fps_sent is not None:
            self.sec_fps_min = fps_sent if self.sec_fps_min is None else min(self.sec_fps_min, fps_sent)
        sec_closed = sec_full_rate = None
        if now - self.sec_t0 >= 1.0:
            sec_closed, sec_full_rate = self.sec_drops, True
            if self.sec_fps_min is not None and fps_target:
                sec_full_rate = self.sec_fps_min >= fps_target * self.FULL_RATE_FRAC
            self.clean_secs = self.clean_secs + 1 if (sec_closed == 0 and sec_full_rate) else 0
            self.sec_drops, self.sec_fps_min, self.sec_t0 = 0, None, now
        reason = ""
        if audio_shed:
            reason = "audio shed (%d)" % audio_shed
        elif self.bad_reads > self.BAD_READS:
            reason = "%d consecutive reads with drops" % self.bad_reads
        elif sec_closed is not None and sec_closed > self.SEC_DROPS:
            reason = "%d drops in one second" % sec_closed
        elif self.slow_reads > self.FPS_DOWN_READS:
            reason = "below the frame-rate floor for %d reads" % self.slow_reads
        if reason:
            f = self.fails[self.idx] = self.fails.get(self.idx, 0) + 1
            self.blocked[self.idx] = now + min(self.HOLD_MAX_S, self.HOLD_BASE_S * 2.0 ** (f - 1))
            self.reason = reason
            self.bad_reads = self.slow_reads = self.clean_secs = self.sec_drops = 0
            self.sec_fps_min = None
            if self.idx > self.floor:
                self.idx -= 1; self.held_since = now
            return self.idx
        if self.clean_secs and now - self.held_since >= self.HOLD_CLEAR_S and self.idx in self.fails:
            del self.fails[self.idx]; self.blocked.pop(self.idx, None)
        if self.clean_secs >= self.CLEAN_SECS and self.idx < self.ceiling:
            target = self.idx + 1
            if now < self.blocked.get(target, 0.0):
                self.reason = "holding L%d" % target
            else:
                self.idx, self.held_since, self.clean_secs, self.reason = target, now, 0, ""
        elif sec_closed == 0 and sec_full_rate is False:
            self.reason = "holding: not at full frame rate"
        return self.idx

    def resume_at(self, idx, now):
        self.idx = idx
        self.bad_reads = self.slow_reads = self.sec_drops = self.clean_secs = 0
        self.sec_fps_min, self.sec_t0, self.held_since = None, now, now
        self.reason = "resumed at L%d after the bottom walk" % idx
