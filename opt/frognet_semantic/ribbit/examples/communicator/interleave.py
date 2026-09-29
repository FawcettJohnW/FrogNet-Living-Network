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
"""[CELL_V1] One ordered wire carrying fixed-size cells, audio and video mixed.

A cell is MSS-sized. Its header is a map of what is inside it -- a count, then
one entry per payload giving kind and length -- followed by the payloads packed
end to end with no delimiters, because the map already said where each one ends.
Same shape the semantic engine puts on the request/reply wire: structure up
front, data behind it, nothing wasted on framing the data twice.

The map is what lets audio ride in the same cell as video without waiting for it.
A cell going out with 900 bytes of video segment in it has room for a 60 byte
Opus frame that queued a millisecond ago, and the map says so in three bytes.

CELL_BYTES is 1368 because that is what ss reports as mss on every tunnel path
(pmtu 1420, WireGuard overhead off the top). Not a guess.

[NO_FALLBACK_V1] A count that cannot fit the cell, a length sum that does not
equal the payload bytes exactly, a continuation for a frame that was never
started, a kind nobody registered -- every one of these raises. None is clamped
to something safe, defaulted, or skipped. A fallback here would produce a cell
that parses and a picture that decodes and no line in any log, and the fault
would surface weeks later as an artifact instead of now as an error.

ORDERING: this assumes the bearer delivers bytes in order with no gaps, which is
what makes CONT unambiguous -- there is one open frame per kind, so "continue"
can only mean the one thing and needs no frame id to say which.
"""

import collections
import select
import socket
import struct
import threading
import time

from fnav import (
    _LEN, KIND_AUDIO, KIND_VIDEO,
    pack_typed, unpack_typed, unpack_video, video_is_key,
)

# ss: mss:1368 pmtu:1420 on every tunnel leg. The cell is one segment.
CELL_BYTES = 1368

# Header: one byte of entry count, then count x (kind byte, !H length).
_CELL_HDR = struct.Struct("!B")
_ENTRY = struct.Struct("!BH")
_HDR_BYTES = _CELL_HDR.size
_ENTRY_BYTES = _ENTRY.size          # 3

# Entry kind byte: low 6 bits are the kind (covers all six fnav kinds), high two
# say where this payload sits in its frame.
KIND_MASK = 0x3F
E_CONT = 0x40                        # continues the open frame of this kind
E_LAST = 0x80                        # completes it

# A frame the sender gave up on part-way. Zero length, CONT set, LAST clear is
# the abort: "drop the video frame you are holding." It needs no reference --
# there is only one open frame per kind.
E_ABORT = E_CONT

# Ceiling on entries. 1368 bytes of 55-byte Opus with 3-byte entries tops out
# around 23; nothing real approaches it. The encoder never emits more and the
# decoder refuses more.
MAX_ENTRIES = 15


class CellProtocolError(Exception):
    """[NO_FALLBACK_V1] The cell is not what it claims to be."""


class WireDead(Exception):
    """The socket will not take bytes and is not going to."""


def pack_cell(entries) -> bytes:
    """entries: sequence of (kind_byte, payload). Returns one cell body."""
    n = len(entries)
    if n == 0:
        raise CellProtocolError("refusing to pack an empty cell")
    if n > MAX_ENTRIES:
        raise CellProtocolError("cell has %d entries, ceiling is %d"
                                % (n, MAX_ENTRIES))
    head = [_CELL_HDR.pack(n)]
    body = []
    for kind_byte, payload in entries:
        if len(payload) > 0xFFFF:
            raise CellProtocolError("payload of %d bytes exceeds the length field"
                                    % len(payload))
        head.append(_ENTRY.pack(kind_byte, len(payload)))
        body.append(payload)
    cell = b"".join(head) + b"".join(body)
    if len(cell) > CELL_BYTES:
        raise CellProtocolError("packed cell is %d bytes, ceiling is %d"
                                % (len(cell), CELL_BYTES))
    return cell


def unpack_cell(cell: bytes):
    """Return [(kind_byte, payload), ...]. Raises on anything inconsistent."""
    if len(cell) < _HDR_BYTES:
        raise CellProtocolError("cell is %d bytes, header alone is %d"
                                % (len(cell), _HDR_BYTES))
    (n,) = _CELL_HDR.unpack_from(cell, 0)
    if n == 0:
        raise CellProtocolError("cell declares zero entries")
    if n > MAX_ENTRIES:
        raise CellProtocolError("cell declares %d entries, ceiling is %d"
                                % (n, MAX_ENTRIES))
    need = _HDR_BYTES + n * _ENTRY_BYTES
    if len(cell) < need:
        raise CellProtocolError("cell declares %d entries needing %d header "
                                "bytes, cell is %d" % (n, need, len(cell)))
    kinds = []
    total = 0
    off = _HDR_BYTES
    for _ in range(n):
        kind_byte, ln = _ENTRY.unpack_from(cell, off)
        off += _ENTRY_BYTES
        kinds.append((kind_byte, ln))
        total += ln
    avail = len(cell) - need
    # Exactly, not at least. A cell whose lengths do not account for every
    # payload byte is a cell the sender and receiver disagree about, and
    # trimming to fit is the fallback that hides it.
    if total != avail:
        raise CellProtocolError("cell lengths sum to %d, payload area is %d"
                                % (total, avail))
    out = []
    for kind_byte, ln in kinds:
        out.append((kind_byte, cell[off:off + ln]))
        off += ln
    return out


class CellWire:
    """[CELL_V1] Audio and video producers, one drain thread, one socket.

    Each iteration builds ONE cell: every audio frame waiting goes in first,
    then as much of the head video frame as still fits. So audio is never behind
    more than one cell of video, and video pays nothing for the privilege --
    it uses whatever room audio left.
    """

    WRITE_DEADLINE_S = 5.0
    AUDIO_QUEUE_MAX = 32
    VIDEO_QUEUE_MAX = 4

    def __init__(self, sock, src: str, cell_bytes: int = CELL_BYTES):
        self.sock = sock
        self.src = src
        self.cell_bytes = int(cell_bytes)
        self.sock.setblocking(False)

        self._wake = threading.Condition()
        self._audio = collections.deque()
        self._video = collections.deque()      # deque of bytearray, each a frame
        self._video_started = False            # is the head frame part-way out
        self._stop = False
        self._thread = None

        self.audio_sent = 0
        self.audio_dropped_backlog = 0
        self.video_frames_sent = 0
        self.video_frames_aborted = 0
        self.video_frames_dropped_backlog = 0
        self.cells_sent = 0
        self.audio_wait_max_s = 0.0
        self.dead = False

    # -- producers ---------------------------------------------------------

    def put_audio(self, opus_payload: bytes):
        if not opus_payload:
            raise CellProtocolError("refusing to queue an empty audio payload")
        with self._wake:
            if len(self._audio) >= self.AUDIO_QUEUE_MAX:
                self._audio.popleft()
                self.audio_dropped_backlog += 1
            self._audio.append((time.monotonic(), opus_payload))
            self._wake.notify()

    def put_video(self, video_payload: bytes):
        if not video_payload:
            raise CellProtocolError("refusing to queue an empty video payload")
        with self._wake:
            if len(self._video) >= self.VIDEO_QUEUE_MAX:
                if self._video_started and len(self._video) == 1:
                    raise CellProtocolError(
                        "cannot drop a video frame already part-way onto the wire")
                self._video.popleft()
                self.video_frames_dropped_backlog += 1
            self._video.append(bytearray(video_payload))
            self._wake.notify()

    # -- wire --------------------------------------------------------------

    def start(self):
        if self._thread is not None:
            raise RuntimeError("CellWire already started")
        self._thread = threading.Thread(target=self._drain, daemon=True,
                                        name="cell-drain")
        self._thread.start()

    def stop(self):
        with self._wake:
            self._stop = True
            self._wake.notify_all()

    def _send_all(self, cell: bytes):
        """Write one cell whole. Completes or raises -- never part-way."""
        buf = _LEN.pack(len(cell)) + cell
        view = memoryview(buf)
        off = 0
        deadline = time.monotonic() + self.WRITE_DEADLINE_S
        while off < len(buf):
            try:
                off += self.sock.send(view[off:])
                continue
            except (BlockingIOError, InterruptedError):
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.dead = True
                raise WireDead("socket took %d of %d cell bytes then stopped "
                               "for %.1fs" % (off, len(buf), self.WRITE_DEADLINE_S))
            select.select([], [self.sock], [], remaining)

    def _build(self):
        """One cell's worth of entries, or None if there is nothing to send."""
        with self._wake:
            audio = list(self._audio)
            self._audio.clear()
            head = self._video[0] if self._video else None

        entries = []
        room = self.cell_bytes - _HDR_BYTES
        waits = []

        for queued_at, payload in audio:
            cost = _ENTRY_BYTES + len(payload)
            if cost > room or len(entries) >= MAX_ENTRIES - 1:
                # Does not fit this cell. Put it back at the FRONT, in order.
                with self._wake:
                    self._audio.appendleft((queued_at, payload))
                continue
            entries.append((KIND_AUDIO | E_LAST, payload))
            waits.append(time.monotonic() - queued_at)
            room -= cost

        if head is not None and room > _ENTRY_BYTES and len(entries) < MAX_ENTRIES:
            take = min(len(head), room - _ENTRY_BYTES)
            chunk = bytes(head[:take])
            del head[:take]
            kind = KIND_VIDEO
            if self._video_started:
                kind |= E_CONT
            if not head:
                kind |= E_LAST
            entries.append((kind, chunk))
            with self._wake:
                if head:
                    self._video_started = True
                else:
                    self._video.popleft()
                    self._video_started = False
                    self.video_frames_sent += 1

        if not entries:
            return None
        for w in waits:
            if w > self.audio_wait_max_s:
                self.audio_wait_max_s = w
        return entries

    def _drain(self):
        while True:
            with self._wake:
                while not self._stop and not self._audio and not self._video:
                    self._wake.wait(0.100)
                if self._stop:
                    return
            entries = self._build()
            if entries is None:
                continue
            self._send_all(pack_cell(entries))
            self.cells_sent += 1
            self.audio_sent += sum(1 for k, _p in entries
                                   if (k & KIND_MASK) == KIND_AUDIO)

    def abort_video(self):
        """Give up on the video frame currently part-way onto the wire."""
        with self._wake:
            if not self._video_started:
                return
            self._video.popleft()
            self._video_started = False
        self._send_all(pack_cell([(KIND_VIDEO | E_ABORT, b"")]))
        self.video_frames_aborted += 1

    def report(self) -> str:
        return ("  [CELL] cells %d | audio sent %d, backlog-dropped %d, worst "
                "queue wait %.0f ms | video sent %d, aborted %d, "
                "backlog-dropped %d"
                % (self.cells_sent, self.audio_sent, self.audio_dropped_backlog,
                   self.audio_wait_max_s * 1000.0, self.video_frames_sent,
                   self.video_frames_aborted, self.video_frames_dropped_backlog))


class CellReader:
    """[CELL_V1] Cells in, whole payloads out.

    Audio entries are complete frames and dispatch immediately. Video entries
    accumulate into the one open video frame and are handed over on E_LAST.
    """

    def __init__(self):
        self._video = None            # bytearray of the open frame, or None
        self.audio_out = 0
        self.video_out = 0
        self.video_aborted = 0

    def feed(self, cell: bytes):
        """Return [(kind, payload), ...] of whatever completed in this cell."""
        done = []
        for kind_byte, payload in unpack_cell(cell):
            kind = kind_byte & KIND_MASK
            cont = bool(kind_byte & E_CONT)
            last = bool(kind_byte & E_LAST)

            if kind == KIND_AUDIO:
                if cont or not last:
                    raise CellProtocolError(
                        "audio entry is fragmented: an Opus frame is whole or "
                        "it is not audio")
                self.audio_out += 1
                done.append((KIND_AUDIO, payload))
                continue

            if kind != KIND_VIDEO:
                raise CellProtocolError("unregistered kind %d in cell" % kind)

            if cont and not last and not payload:
                if self._video is None:
                    raise CellProtocolError(
                        "abort for a video frame that was never started")
                self._video = None
                self.video_aborted += 1
                continue

            if cont:
                if self._video is None:
                    raise CellProtocolError(
                        "video continuation with no frame open")
                self._video += payload
            else:
                if self._video is not None:
                    raise CellProtocolError(
                        "video frame started while %d bytes of the previous "
                        "one are still open" % len(self._video))
                self._video = bytearray(payload)

            if last:
                out = bytes(self._video)
                self._video = None
                self.video_out += 1
                done.append((KIND_VIDEO, out))
        return done


class AudioFIFO:
    """[CELL_V1] Handoff between the receive loop and the decoder.

    NOT a second jitter buffer -- the mixer already is one, with a 120 ms
    cushion and a 240 ms cap. Two elastic stages in series make it impossible
    to say which one ate a lump. The consumer drains this flat out; depth
    sitting above a frame or two means the DECODER is behind, not the network.
    """

    def __init__(self, maxlen=64):
        self._q = collections.deque()
        self._cv = threading.Condition()
        self._maxlen = int(maxlen)
        self.depth_max = 0
        self.dropped = 0
        self.pushed = 0

    def push(self, src, payload):
        with self._cv:
            if len(self._q) >= self._maxlen:
                self._q.popleft()
                self.dropped += 1
            self._q.append((src, payload))
            self.pushed += 1
            if len(self._q) > self.depth_max:
                self.depth_max = len(self._q)
            self._cv.notify()

    def pop(self, timeout=0.100):
        with self._cv:
            if not self._q and not self._cv.wait(timeout):
                return None
            if not self._q:
                return None
            return self._q.popleft()

    def report(self):
        return ("  [AFIFO] pushed %d, dropped %d, deepest %d"
                % (self.pushed, self.dropped, self.depth_max))
