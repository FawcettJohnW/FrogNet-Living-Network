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
call_mediahost.py - the call mediahost: receive uplinks, MIX, fan per-destination.

WHY THIS EXISTS
  The Communicator call's host is CONTENT-AWARE (the frognet_mediahost.MediaHost
  design), not the opaque MediaStreamServer relay. Each client sends audio+video on
  the FNWP-1 wire (opaque _pack_raw body); the host must:
    1. RECEIVE each source's uplink (one MediaSocketReceiver per source),
    2. DEMUX audio+video out of each frame (call_media.unpack_av),
    3. MIX ALL feeds together into ONE program PER DEMANDED RUNG - NOT per recipient.
       (>2-party directive: per-recipient minus-self filtering forces a distinct encode
       per recipient - too expensive. Mux everyone once per rung; the recipient handles
       its own self-tile from local capture. amix/xstack of ALL sources via
       frognet_mediahost.build_mix_filtergraph given every label, not "others".)
    4. ENCODE each demanded rung's all-feeds program ONCE (cost = #distinct rungs, not
       #recipients), via frognet_mediahost.rung_output_args,
    5. SEND DOWN to each destination over an INDEPENDENT sender thread - one
       MediaSocketSender per destination - so one slow client never head-of-line
       blocks the others. Each destination attaches to the shared program for ITS rung;
       per-destination backpressure steps a recipient's rung (it then reads a lower
       shared program), degrading only itself.

  The transport stays a dumb pipe at the FRAMING layer (length-prefixed FNWP-1); the
  HOST is where bytes are decoded/mixed/re-encoded. That's the only place content
  awareness lives. Uplink contract is UNCHANGED from the client migration (call_media):
  opaque AV up; the host unpacks because it negotiated the codec.

  This BRIDGES the pieces that exist but weren't wired to live sockets:
    sotf_media_backing.MediaSocketReceiver  - accept a source's push, frame callback
    sotf_media_backing.MediaSocketSender    - per-destination, own writer thread + shed
    frognet_mediahost.build_mix_filtergraph  - per-recipient minus-self amix/xstack
    frognet_mediahost.rung_output_args       - encode the mix at one ladder rung
    frognet_mediahost.compute_plan           - who is in whose minus-self program
    media_stream.MediaStreamServer           - REUSED only for its per-(session) bearer
                                               backpressure accounting + conn-info publish
  MediaHost._start_pipeline used a PULL model (ffmpeg opens tcp://who.uplink.frognet)
  and self-described as not launched in-container. This module uses the PUSH model the
  client actually speaks (MediaSocketReceiver), feeding ffmpeg via stdin pipes.

STATUS: IN-FLIGHT. Built against real signatures read from source 2026-06-16. The mix
ffmpeg launch is gated behind a SINK injection so the sim proves the wiring (routing,
per-destination independence, backpressure) without a real ffmpeg/camera in container.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

# Tolerant imports so the sim can inject fakes and run outside the bundle.
try:
    from sotf_media_backing import MediaSocketReceiver, MediaSocketSender
except Exception:  # pragma: no cover
    MediaSocketReceiver = MediaSocketSender = None

try:
    from frognet_mediahost import build_mix_filtergraph, rung_output_args, compute_plan
except Exception:  # pragma: no cover
    build_mix_filtergraph = rung_output_args = compute_plan = None

try:
    import call_media as CMED
except Exception:  # pragma: no cover
    CMED = None


# -- one destination = one independent downlink (its own thread, shed, bearer) --
class _Destination:
    """A single recipient's downlink. Owns its own MediaSocketSender (which has its
    own writer thread + shed-don't-stall queue), so a slow client here cannot block
    any other destination. Carries this recipient's demanded rung + its own bearer."""

    def __init__(self, recipient: str, addr: str, port: int,
                 level_idx: int, _sender_cls=None):
        self.recipient = recipient
        self.level_idx = level_idx
        self.drops = 0
        self.sent = 0
        cls = _sender_cls or MediaSocketSender
        self.sender = cls(addr, port, maxq=120, on_drop=self._on_drop)

    def _on_drop(self):
        self.drops += 1

    def send(self, mixed_frame: bytes, is_key: bool = False) -> None:
        # Independent: this call only ever touches THIS destination's sender/queue.
        self.sender.send(mixed_frame, is_key=is_key)
        self.sent += 1

    def depth(self) -> int:
        return self.sender.depth()

    def drop_rate(self) -> float:
        tot = self.sent + self.drops
        return (self.drops / tot) if tot else 0.0

    def close(self):
        try:
            self.sender.close()
        except Exception:
            pass


# -- the mixer seam: ONE all-feeds program per demanded RUNG, shared by all -----
# John's directive (>2 party): do NOT filter minus-self per recipient - that forces a
# distinct encode per recipient (N encodes for N people), too expensive. Instead mux ALL
# feeds together ONCE per demanded rung and send that shared program to every recipient at
# that rung. Encode cost = number of distinct demanded rungs, NOT number of recipients.
# Self-view is the CLIENT's job (it renders its own tile from local capture); the host does
# not exclude anyone.
class _RungProgram:
    """The all-feeds mixed program at ONE ladder rung, shared by every recipient who
    demands that rung. `encode` is injected so the sim runs without ffmpeg; production
    passes an ffmpeg encoder built from rung_output_args(level_idx, vout, aout, port) fed
    the xstack/amix of ALL sources (build_mix_filtergraph given every label, not others)."""

    def __init__(self, level_idx: int,
                 encode: Optional[Callable[[Dict[str, Tuple[bytes, bytes]], int], Tuple[bytes, bool]]] = None):
        self.level_idx = level_idx
        self._encode = encode or _fake_mix_encode

    def mix(self, latest: Dict[str, Tuple[bytes, bytes]]) -> Tuple[bytes, bool]:
        """latest: who -> (audio, video) most-recent decoded frame for EVERY source.
        The program is ALL of them muxed - no minus-self, no per-recipient variation."""
        return self._encode(dict(latest), self.level_idx)


def _fake_mix_encode(program: Dict[str, Tuple[bytes, bytes]], level_idx: int) -> Tuple[bytes, bool]:
    """Sim encoder: deterministic all-feeds program bytes so the oracle can assert that
    every recipient gets EVERY source at their rung, with ONE encode per rung. Real path
    replaces this with ffmpeg (libvpx/Opus, the xstack/amix of all sources)."""
    import struct
    blob = bytearray()
    blob += struct.pack("!BB", level_idx, len(program))
    for who in sorted(program):
        a, v = program[who]
        wb = who.encode()
        blob += struct.pack("!BII", len(wb), len(a), len(v)) + wb + a + v
    is_key = any(v for (_a, v) in program.values())
    return bytes(blob), is_key


# -- the host: receivers in, mix, per-destination senders out ------------------
class CallMediaHost:
    """One session's mediahost. Sources push uplinks (registered via add_source);
    recipients are destinations (registered via set_plan from compute_plan). Each tick,
    the latest decoded AV per source is mixed minus-self per recipient and sent down that
    recipient's own sender thread. Backpressure is PER DESTINATION."""

    def __init__(self, session: str, encode_factory: Optional[Callable] = None,
                 _receiver_cls=None, _sender_cls=None):
        self.session = session
        self._receiver_cls = _receiver_cls
        self._sender_cls = _sender_cls
        self._encode_factory = encode_factory      # (level_idx)->encode fn (per RUNG now)
        self._lock = threading.Lock()
        self.sources: Dict[str, "object"] = {}      # who -> MediaSocketReceiver
        self.latest: Dict[str, Tuple[bytes, bytes]] = {}   # who -> (audio, video)
        self.dests: Dict[str, _Destination] = {}    # recipient -> _Destination
        self.programs: Dict[int, _RungProgram] = {}  # level_idx -> shared all-feeds program
        self.bearer: Dict[str, int] = {}            # recipient -> current advertised rung

    # -- uplink: a source pushes opaque AV; we demux and stash the latest --------
    def add_source(self, who: str, host: str, port: int):
        cls = self._receiver_cls or MediaSocketReceiver

        def on_frame(payload: bytes, _who=who):
            try:
                _seq, _ts, _key, audio, video = (CMED.unpack_av(payload) if CMED
                                                 else _unpack_fallback(payload))
            except Exception:
                return
            with self._lock:
                pa, pv = self.latest.get(_who, (b"", b""))
                # audio-only frame keeps last video; video-only keeps last audio
                self.latest[_who] = (audio or pa, video or pv)

        rx = cls(host, port, on_frame=on_frame)
        rx.start()
        with self._lock:
            self.sources[who] = rx

    # -- plan: from compute_plan(streams, watchers). We use demanded_levels (the set of
    #    distinct rungs) to build ONE shared all-feeds program per rung, and a destination
    #    per recipient bound to the rung that recipient demands. NOT per-recipient mixing. -
    def set_plan(self, plan: dict, dest_addr_port: Callable[[str], Tuple[str, int]]):
        with self._lock:
            # one shared program per DEMANDED RUNG (encode cost = #rungs, not #recipients)
            for level in plan.get("demanded_levels", []):
                level = int(level)
                if level not in self.programs:
                    enc = self._encode_factory(level) if self._encode_factory else None
                    self.programs[level] = _RungProgram(level, encode=enc)
            # a destination per recipient, bound to the rung it demands
            for rec, info in plan["recipients"].items():
                level = int(info["level"])
                if rec not in self.dests:
                    addr, port = dest_addr_port(rec)
                    self.dests[rec] = _Destination(rec, addr, port, level,
                                                   _sender_cls=self._sender_cls)
                    self.bearer[rec] = level
                else:
                    self.dests[rec].level_idx = level
            # drop destinations no longer in the plan
            for rec in list(self.dests):
                if rec not in plan["recipients"]:
                    self.dests.pop(rec).close()
                    self.bearer.pop(rec, None)
            # drop rung programs no longer demanded
            demanded = {int(l) for l in plan.get("demanded_levels", [])}
            for lvl in list(self.programs):
                if lvl not in demanded:
                    self.programs.pop(lvl, None)

    # -- tick: encode each demanded rung's all-feeds program ONCE, then fan the program
    #    for each recipient's current rung down that recipient's own sender thread. --------
    def pump(self) -> int:
        with self._lock:
            latest = dict(self.latest)
            programs = dict(self.programs)
            dests = dict(self.dests)
            bearer = dict(self.bearer)
        # encode ONCE per demanded rung (shared)
        encoded: Dict[int, Tuple[bytes, bool]] = {}
        for lvl, prog in programs.items():
            encoded[lvl] = prog.mix(latest)
        fanned = 0
        for rec, d in dests.items():
            lvl = bearer.get(rec, d.level_idx)
            frame_key = encoded.get(lvl)
            if frame_key is None:
                # recipient's rung has no program (e.g. just stepped); fall back to the
                # nearest available lower rung so it still gets media.
                avail = [l for l in encoded if l <= lvl]
                if not avail:
                    continue
                frame_key = encoded[max(avail)]
            mixed, is_key = frame_key
            d.send(mixed, is_key=is_key)        # touches ONLY this destination
            fanned += 1
        return fanned

    # -- per-destination backpressure: step THIS recipient's bearer on its drops --
    def apply_backpressure(self, publish_bearer: Callable[[str, int], None],
                           min_idx: int = 0, drop_thresh: float = 0.10):
        """For each destination whose drop-rate crossed the threshold, step ITS bearer
        down one rung and publish per-recipient. One slow client degrades only itself."""
        with self._lock:
            dests = dict(self.dests)
        for rec, d in dests.items():
            if d.drop_rate() > drop_thresh and self.bearer.get(rec, 7) > min_idx:
                self.bearer[rec] -= 1
                publish_bearer(rec, self.bearer[rec])

    def close(self):
        with self._lock:
            for rx in self.sources.values():
                try: rx.close()
                except Exception: pass
            for d in self.dests.values():
                d.close()


def _unpack_fallback(payload: bytes):
    import struct
    hdr = struct.Struct("!IQBII")
    seq, ts, key, alen, vlen = hdr.unpack_from(payload, 0)
    off = hdr.size
    a = payload[off:off + alen]; off += alen
    v = payload[off:off + vlen]
    return seq, ts, bool(key), a, v
