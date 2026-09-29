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
call_host_service.py - the RUNNABLE call mediahost (items 1 + 2).

What it is: the process that makes a client's `create_and_connect` actually resolve. It
binds real sockets, accepts each participant's uplink, demuxes the opaque AV, drives the
streaming all-feeds MixEncoder (real ffmpeg), reads the single mixed program, and fans it
to each participant's downlink - and publishes `conn_info` per session so producers/
consumers can dial. Until this runs, the client blocks in resolve_conn_info.

Pieces it wires (all already built + proven in isolation):
  media_stream.TupleControl        - read create-stream, publish conn_info + bearer
  sotf_media_backing.MediaSocketReceiver - accept a participant's uplink push (per source)
  call_media.unpack_av             - demux opaque _pack_raw -> (audio, video)
  call_mix_encoder.MixEncoder      - streaming ffmpeg per rung: per-source FIFOs ->
                                     overlay-placement + amix -> ONE webm program out
  sotf_media_backing.MediaSocketSender   - per-destination downlink (own thread, shed)
  frognet_avhost.resolve_host      - which box is the elected mediahost (item 2)

ELECTION (item 2): the elected avhost box runs THIS service; on a create-stream tuple it
publishes conn_info {addr, tx, rx, supports} for that session. The client's CallSender
resolve_conn_info then reads it and dials. addr is the avhost address; tx is where
producers push; rx is where consumers attach for the mixed program.

ONE UP / ONE DOWN per client, constant regardless of party count: each participant pushes
one uplink and pulls one mixed downlink; the host bears the N-source mix. Downlink is a
single webm program (item 4 falls out here: the host emits webm, the client decodes webm).

STATUS: orchestration + lifecycle built; sim-proven via injected fakes (see
test_call_host_service_oracle.py). Real-socket bring-up is the hardware step.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

try:
    from media_stream import TupleControl
except Exception:  # pragma: no cover
    TupleControl = None

try:
    from sotf_media_backing import MediaSocketReceiver, MediaSocketSender
except Exception:  # pragma: no cover
    MediaSocketReceiver = MediaSocketSender = None

try:
    import call_media as CMED
except Exception:  # pragma: no cover
    CMED = None

try:
    import call_mix_encoder as MIX
except Exception:  # pragma: no cover
    MIX = None

try:
    from frognet_mediahost import compute_plan
except Exception:  # pragma: no cover
    compute_plan = None

try:
    import frognet_avhost
except Exception:  # pragma: no cover
    frognet_avhost = None


_AV_PORT_TX = 9101
_AV_PORT_RX = 9102


class _DestDownlink:
    """One participant's downlink: its own MediaSocketSender (own thread + shed) carrying
    the mixed program for that participant's rung. Independent - a slow one can't block
    others. Backpressure steps THIS participant's bearer."""

    def __init__(self, who: str, addr: str, port: int, level_idx: int,
                 on_drop: Callable[[], None], _sender_cls=None):
        self.who = who
        self.level_idx = level_idx
        self.drops = 0
        self.sent = 0
        cls = _sender_cls or MediaSocketSender
        self.sender = cls(addr, port, maxq=120, on_drop=self._drop)
        self._ext = on_drop

    def _drop(self):
        self.drops += 1
        self._ext()

    def send(self, mixed: bytes, is_key: bool = False):
        self.sender.send(mixed, is_key=is_key)
        self.sent += 1

    def drop_rate(self) -> float:
        t = self.sent + self.drops
        return (self.drops / t) if t else 0.0

    def depth(self) -> int:
        return self.sender.depth()

    def close(self):
        try: self.sender.close()
        except Exception: pass


class CallHostService:
    """One session's running mediahost. Drives the streaming all-feeds mix and the per-
    participant downlinks; publishes conn_info. Reconcile-style: stateless re-read of the
    plan each tick, so a float (this process dies, another elects) rebuilds the same."""

    def __init__(self, session: str, control,
                 addr: str = "mediahost.frognet",
                 tx_port: int = _AV_PORT_TX, rx_port: int = _AV_PORT_RX,
                 ladder=None,
                 _receiver_cls=None, _sender_cls=None, _encoder_cls=None,
                 dest_addr_port: Optional[Callable[[str], Tuple[str, int]]] = None):
        self.session = session
        self.control = control
        self.addr = addr
        self.tx_port = tx_port
        self.rx_port = rx_port
        self.ladder = ladder
        self._receiver_cls = _receiver_cls
        self._sender_cls = _sender_cls
        self._encoder_cls = _encoder_cls or (MIX.MixEncoder if MIX else None)
        self._dest_addr_port = dest_addr_port or (lambda who: (self.addr, self.rx_port))
        self._lock = threading.Lock()
        self._uplink = None                         # ONE listener per session (binds tx once)
        self.sources: Dict[str, object] = {}        # who -> registered (roster membership)
        self.latest: Dict[str, Tuple[bytes, bytes]] = {}
        self.encoders: Dict[int, object] = {}       # level_idx -> MixEncoder
        self.dests: Dict[str, _DestDownlink] = {}   # who -> downlink
        self.bearer: Dict[str, int] = {}
        self.created = False
        self._uplink = None                          # ONE receiver for the whole session
        self._stop = threading.Event()

    # -- ONE uplink listener per session: binds tx_port ONCE, accepts all producers,
    #    demuxes each frame by its embedded source id (NOT one listener per source -
    #    that double-binds the port -> 'Address already in use'). ------------------
    def ensure_uplink(self):
        """Bind the ONE uplink listener for this session, exactly once. Idempotent."""
        if self._uplink is not None:
            return
        cls = self._receiver_cls or MediaSocketReceiver

        def on_frame(payload: bytes):
            # NOTE: the client's pack_av carries NO source id, so a single shared listener
            # cannot yet attribute frames per-participant. Until the uplink frame is tagged
            # with 'who' (client wire-format change), feed under one logical source. This is
            # correct for 1-up and unblocks the call; multi-feed demux is the next step.
            try:
                _s, _t, _k, audio, video = (CMED.unpack_av(payload) if CMED
                                            else _unpack_fallback(payload))
            except Exception:
                return
            who = "_uplink"
            with self._lock:
                pa, pv = self.latest.get(who, (b"", b""))
                self.latest[who] = (audio or pa, video or pv)
            for enc in list(self.encoders.values()):
                lvl = getattr(enc, "level_idx", 6)
                if video and MIX and MIX.rung_carries_video(lvl):
                    enc.feed(who, "V", video)
                if audio and MIX and MIX.rung_carries_audio(lvl):
                    enc.feed(who, "A", audio)

        rx = cls("0.0.0.0", self.tx_port, on_frame=on_frame)
        rx.start()
        self._uplink = rx

    def maybe_create(self) -> bool:
        """Per-tick idempotent entry: bind the listener once, publish conn_info once.
        Returns True once the session is live (conn_info published)."""
        self.ensure_uplink()
        if self.created:
            return True
        req = self.control.create_request()
        if not req:
            return False
        self.control.publish_conn_info(self.addr, self.tx_port, self.rx_port,
                                       ["vp8", "opus"])
        self.control.set_state("live", codec=req.get("codec", "vp8"))
        self.created = True
        return True

    # -- uplink: register a participant as a source. Does NOT bind - the ONE uplink
    #    listener for the whole session is owned by ensure_uplink() and binds tx_port
    #    exactly once. Binding here (per source, per tick) is what caused repeated
    #    'Address already in use'. This is pure roster membership now. ---------------
    def add_source(self, who: str, host: str = "0.0.0.0", port: int = 0):
        self.ensure_uplink()                 # idempotent: binds once, publishes conn_info
        with self._lock:
            self.sources[who] = self._uplink  # share the single listener

    # -- plan: build one streaming encoder per demanded rung + a downlink per dest -
    def set_plan(self, plan: dict):
        sources = list(plan.get("sources", []))
        with self._lock:
            for level in plan.get("demanded_levels", []):
                level = int(level)
                if level not in self.encoders and self._encoder_cls is not None:
                    enc = self._encoder_cls(level, sources, on_mixed=self._mixed_for(level))
                    enc.start()
                    self.encoders[level] = enc
            for rec, info in plan["recipients"].items():
                level = int(info["level"])
                if rec not in self.dests:
                    a, p = self._dest_addr_port(rec)
                    self.dests[rec] = _DestDownlink(
                        rec, a, p, level, on_drop=lambda: None,
                        _sender_cls=self._sender_cls)
                    self.bearer[rec] = level
                else:
                    self.dests[rec].level_idx = level
            for rec in list(self.dests):
                if rec not in plan["recipients"]:
                    self.dests.pop(rec).close()
                    self.bearer.pop(rec, None)

    def _mixed_for(self, level_idx: int):
        """Return an on_mixed(bytes) that fans this rung's mixed program to every
        destination currently at this rung - over each destination's OWN sender."""
        def on_mixed(chunk: bytes):
            with self._lock:
                dests = [d for d in self.dests.values()
                         if self.bearer.get(d.who, d.level_idx) == level_idx]
            for d in dests:
                d.send(chunk, is_key=False)
        return on_mixed

    # -- per-destination backpressure: a slow downlink steps ITS bearer -----------
    def apply_backpressure(self, drop_thresh: float = 0.10, min_idx: int = 0):
        with self._lock:
            dests = dict(self.dests)
        for rec, d in dests.items():
            if d.drop_rate() > drop_thresh and self.bearer.get(rec, 7) > min_idx:
                self.bearer[rec] -= 1
                self.control.publish_bearer(self.bearer[rec])   # per-session bearer tuple

    def close(self):
        self._stop.set()
        with self._lock:
            for rx in self.sources.values():
                try: rx.close()
                except Exception: pass
            for enc in self.encoders.values():
                try: enc.close()
                except Exception: pass
            for d in self.dests.values():
                d.close()


def run_service(session: str, backend, addr: str = "mediahost.frognet",
                dbhost: str = "databasehost.frognet",   # mediastream is DATA: data host, NEVER _control
                interval: float = 1.0, ladder=None):
    """Reconcile loop entrypoint. Reads create/watch tuples, publishes conn_info, computes
    the plan, drives the mix + downlinks. Stateless across ticks (float-safe)."""
    control = TupleControl(backend, session, addr=addr)
    svc = CallHostService(session, control, addr=addr, ladder=ladder)
    while not svc._stop.is_set():
        try:
            if svc.maybe_create():
                streams, watchers = _read_streams_watchers(backend, session, dbhost)
                if compute_plan is not None:
                    plan = compute_plan(streams, watchers)
                    # ensure each source has an uplink receiver bound
                    for who in plan.get("sources", []):
                        if who not in svc.sources:
                            svc.add_source(who, "0.0.0.0", svc.tx_port)
                    svc.set_plan(plan)
                    svc.apply_backpressure()
        except Exception as e:  # never break the loop
            import sys
            sys.stderr.write(f"[call_host] reconcile error: {e!r}\n")
        time.sleep(interval)
    svc.close()


def _read_streams_watchers(backend, session, dbhost):
    """Read SD:stream.* / SD:watch.* for this session (mirrors frognet_mediahost)."""
    streams, watchers = [], []
    try:
        for r in backend.get("mediastream", "stream", dbhost=dbhost, fresh_s=30):
            v = r.get("value", {})
            if v.get("session") == session:
                streams.append(v)
        for r in backend.get("mediastream", "watch", dbhost=dbhost, fresh_s=30):
            v = r.get("value", {})
            if v.get("session") == session:
                watchers.append(v)
    except Exception:
        pass
    return streams, watchers


def _unpack_fallback(payload: bytes):
    import struct
    hdr = struct.Struct("!IQBII")
    seq, ts, key, alen, vlen = hdr.unpack_from(payload, 0)
    off = hdr.size
    a = payload[off:off + alen]; off += alen
    v = payload[off:off + vlen]
    return seq, ts, bool(key), a, v
