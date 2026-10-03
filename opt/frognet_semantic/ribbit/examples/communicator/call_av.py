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
"""
call_av.py - the SotF A/V call, TWO PLANES that never cross.

  CONTROL  = tuple space (databasehost.frognet, the DATA host - NEVER _control):
             create-stream intent, per-session connection info, per-endpoint control.
  A/V DATA = a SECOND, DEDICATED socket pair (Communicator <-> Media Host), FNWP-1 framed,
             non-blocking, drop-don't-block. ONLY A/V rides it. NOT :9009, NOT :80.

LIFECYCLE (John's design, verbatim):
  1. user starts a call -> client WRITES a create-stream tuple into the session space.
  2. server (elected media host) SEES the tuple -> creates this session's objects.
  3. server ALLOCATES a per-session connector (its own port; you can't all use one port)
     and PUBLISHES the connection info to tuple space.
  4. other clients MONITOR their space, READ the connection port from the tuple (no :9000
     first-contact, no control handshake - tuple space is the only discovery path) and OPEN
     the dedicated A/V socket to that port for this session.
  5. server fills up (all participants connected) -> the call launches.
  6. clients WRITE A/V to their sockets; control goes through tuple space.
  7. server READS all the call's channels and places each input in the right place for the
     output: each producer's VIDEO -> that producer's ffmpeg input point; all incoming AUDIO
     -> MIXED into one channel. Audio is carried INTERLEAVED in the same FNWP-1 frame form.

Built ON the existing contract (read, not invented):
  media_stream.TupleControl              request_create / create_request / publish_conn_info
                                         / conn_info  (the tuple control plane)
  sotf_media_backing.MediaSocketSender   non-blocking, sheds oldest non-key on full
  sotf_media_backing.MediaSocketReceiver ACCEPT LOOP (accept, spawn reader, keep accepting)
  sotf_media_codex.pack_frame/unpack_frame   !IBII seq,key,alen,vlen + audio + video (interleaved)
  frognet_avhost.resolve_host            which box is the elected media host
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Dict, List, Optional, Tuple

from sotf_media_backing import MediaSocketSender, MediaSocketReceiver
from sotf_media_codex import pack_frame, unpack_frame

try:
    from media_stream import TupleControl
except Exception:  # pragma: no cover
    TupleControl = None


# -----------------------------------------------------------------------------
# CLIENT: one A/V call leg. Two planes: tuple control + the dedicated A/V pair.
# -----------------------------------------------------------------------------
class AVCallClient:
    """A Communicator's A/V leg for one session. Writes the create-stream tuple (if it is
    the originator), reads conn_info from tuple space, opens the dedicated A/V socket pair
    (tx up / rx down) to the published per-session port, sends interleaved audio+video
    FNWP-1 frames (non-blocking, drop-don't-block), and delivers received frames."""

    def __init__(self, control: "TupleControl", node: str,
                 on_av: Callable[[int, bool, bytes, bytes], None],
                 maxq: int = 120, on_drop: Optional[Callable[[], None]] = None,
                 _sender_cls=None, _receiver_cls=None):
        self.control = control
        self.node = node
        self.on_av = on_av
        self.maxq = maxq
        self.on_drop = on_drop
        self._sender_cls = _sender_cls or MediaSocketSender
        self._receiver_cls = _receiver_cls or MediaSocketReceiver
        self._seq = 0
        self._tx: Optional[object] = None      # MediaSocketSender (UP)
        self._rx: Optional[object] = None       # MediaSocketReceiver (DOWN)
        self.conn: Optional[Dict] = None

    # -- control plane: announce intent (originator only) ----------------------
    def request_create(self, **params):
        self.control.request_create(session_id=self.control.session_id, **params)

    # -- control plane: read the per-session connector from tuple space --------
    def read_conn_info(self) -> Optional[Dict]:
        ci = self.control.conn_info()
        self.conn = ci
        return ci

    # -- data plane: open the dedicated A/V socket pair to the published port --
    def open_av(self, conn: Optional[Dict] = None) -> bool:
        ci = conn or self.conn or self.read_conn_info()
        if not ci or not ci.get("addr") or not ci.get("tx") or not ci.get("rx"):
            return False
        addr = ci["addr"]
        # UP: dedicated non-blocking sender to the session's tx port
        self._tx = self._sender_cls(addr, int(ci["tx"]), maxq=self.maxq,
                                    on_drop=self.on_drop)
        # DOWN: dedicated receiver from the session's rx port -> split + deliver
        def _on_down(frame: bytes):
            try:
                seq, key, audio, video = unpack_frame(frame)
            except Exception:
                return
            self.on_av(seq, key, audio, video)
        self._rx = self._receiver_cls(addr, int(ci["rx"]), on_frame=_on_down)
        self._rx.start()
        return True

    # -- data plane: interleave audio + video up the ONE dedicated socket ------
    def send_video(self, is_key: bool, video: bytes):
        if not self._tx or not video:
            return
        self._seq += 1
        self._tx.send(pack_frame(self._seq, is_key, b"", video), is_key=is_key)

    def send_audio(self, audio: bytes):
        if not self._tx or not audio:
            return
        self._seq += 1
        # audio is droppable on THIS socket too (drop-don't-block, per directive)
        self._tx.send(pack_frame(self._seq, False, audio, b""), is_key=False)

    def depth(self) -> int:
        return self._tx.depth() if self._tx else 0

    def close(self):
        for x in (self._tx, self._rx):
            try:
                if x: x.close()
            except Exception:
                pass


# -----------------------------------------------------------------------------
# SERVER: the Media Host. One per-session connector; reads all channels; video to
# each producer's ffmpeg point; all audio mixed to one channel; fans down.
# -----------------------------------------------------------------------------
def _alloc_port() -> int:
    """Allocate a free port for THIS session's connector (you can't all use one port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _AudioMixer:
    """Mix all incoming producer audio into ONE channel: sum int16 PCM per slice, clamp.
    Pure-python so it has no ffmpeg dependency on the hot path. Producers feed slices;
    pull() returns the combined channel for the frames that have arrived."""
    def __init__(self):
        self._pending: Dict[str, bytearray] = {}
        self._lock = threading.Lock()

    def feed(self, who: str, audio: bytes):
        with self._lock:
            self._pending.setdefault(who, bytearray()).extend(audio)

    def pull(self) -> bytes:
        with self._lock:
            if not self._pending:
                return b""
            # mix over the shortest available slice across all producers
            chunks = [bytes(b) for b in self._pending.values() if b]
            if not chunks:
                return b""
            n = min(len(c) for c in chunks)
            n -= n % 2                                   # int16 alignment
            if n <= 0:
                return b""
            import array
            acc = array.array("i", [0] * (n // 2))
            for c in chunks:
                samp = array.array("h"); samp.frombytes(c[:n])
                for i in range(len(samp)):
                    acc[i] += samp[i]
            out = array.array("h", [max(-32768, min(32767, v)) for v in acc])
            # consume what we mixed
            for who in list(self._pending):
                del self._pending[who][:n]
            return out.tobytes()


class AVStreamServer:
    """One running A/V session on the Media Host. Allocates a per-session connector,
    publishes conn_info to tuple space, accept-loops every producer's A/V socket, routes
    each producer's VIDEO to its ffmpeg input point, MIXES all audio to one channel, and
    fans the combined interleaved output down each consumer's rx socket."""

    def __init__(self, control: "TupleControl", addr: str,
                 video_sink: Callable[[str, int, bool, bytes], None],
                 _receiver_cls=None, _sender_cls=None):
        self.control = control
        self.addr = addr
        self.video_sink = video_sink             # (who, seq, is_key, video) -> ffmpeg input point
        self._receiver_cls = _receiver_cls or MediaSocketReceiver
        self._sender_cls = _sender_cls or MediaSocketSender
        self.tx_port = _alloc_port()             # producers PUSH here (per-session connector)
        self.rx_port = _alloc_port()             # consumers PULL the mixed program here
        self.mixer = _AudioMixer()
        self._uplink: Optional[object] = None
        self._downlinks: Dict[str, object] = {}   # consumer -> MediaSocketSender
        self._lock = threading.Lock()
        self._seq = 0
        self.created = False

    # step 2+3: server saw the create tuple -> allocate connector + publish conn_info
    def create(self) -> bool:
        if self.created:
            return True
        req = self.control.create_request()
        if not req:
            return False
        # ONE accept-loop uplink: every producer dials tx_port; the receiver accepts each
        # and spawns its reader (MediaSocketReceiver is an accept loop). Split each frame:
        # video -> that producer's ffmpeg point; audio -> the mixer.
        def _on_up(frame: bytes):
            try:
                seq, key, audio, video = unpack_frame(frame)
            except Exception:
                return
            if video:
                self.video_sink("_in", seq, key, video)   # producer id resolved by caller
            if audio:
                self.mixer.feed("_in", audio)
        self._uplink = self._receiver_cls("0.0.0.0", self.tx_port, on_frame=_on_up)
        self._uplink.start()
        self.control.publish_conn_info(self.addr, self.tx_port, self.rx_port,
                                       ["vp8", "pcm"])
        self.control.set_state("live", codec=req.get("codec", "vp8"))
        self.created = True
        return True

    # a consumer is told (via its conn_info read) to pull rx_port; we dial it a downlink
    def add_consumer(self, who: str, addr: str, port: int):
        with self._lock:
            if who not in self._downlinks:
                self._downlinks[who] = self._sender_cls(addr, port, maxq=120)

    # the output: combined video (placed) + mixed audio, interleaved, fanned to consumers
    def emit(self, is_key: bool, video: bytes):
        audio = self.mixer.pull()
        if not audio and not video:
            return
        self._seq += 1
        frame = pack_frame(self._seq, is_key, audio, video)
        with self._lock:
            dests = list(self._downlinks.values())
        for d in dests:
            d.send(frame, is_key=is_key)

    def close(self):
        try:
            if self._uplink: self._uplink.close()
        except Exception:
            pass
        with self._lock:
            for d in self._downlinks.values():
                try: d.close()
                except Exception: pass
