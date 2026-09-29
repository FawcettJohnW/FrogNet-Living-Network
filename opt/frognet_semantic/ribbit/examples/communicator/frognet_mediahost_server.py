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
frognet_mediahost_server.py - the SotF A/V media host (the SERVER John designed).

It sits and waits. For each call it does exactly the lifecycle from FROGNET_NOW.md Sec.1:

  1. A client WRITES a create-stream tuple into the session space.
  2. This server SEES the tuple and creates the per-session objects.
  3. It ALLOCATES a per-session A/V connector (its own port - sessions can't share one)
     and PUBLISHES the connection info to tuple space (conn_info = addr + this session's
     tx/rx ports). No 9000 first-contact dial: the port is read FROM THE TUPLE.
  4. Other clients read that port from the tuple and connect their dedicated A/V socket.
  5. When the participants are connected, the call runs.
  6. Clients write A/V on their sockets; control rides tuple space.
  7. The server reads ALL the producer channels and places each input where the output
     needs it. Audio is interleaved in the same FNWP-1 frame form (seq,key,audio,video).

Built ENTIRELY on existing pieces - no new architecture:
  media_stream.MediaStreamServer  - create-from-tuple, publish_conn_info, IngestionQueue,
                                     ingest(), pump()/fan-out, backpressure (the DATA logic)
  media_stream.TupleControl        - the control plane (tuple read/write)
  sotf_media_backing.MediaSocketReceiver - the dedicated A/V tx socket (real accept loop:
                                     accepts EVERY producer concurrently, per-conn reader)
  sotf_media_backing.MediaSocketSender   - the dedicated A/V rx socket per consumer
  sotf_media_codec.unpack_frame/pack_frame - the interleaved FNWP-1 audio+video frame
  frognet_avhost.resolve_host      - this box is the elected media-host role

The A/V plane is SEPARATE from proxy/daemon :9009 and from :80 control - A/V only.

STATUS: DESIGNED + wired on the proven pieces. The concurrent-accept receiver is PROVEN
in-container. Live end-to-end belongs on media_stream_rungs.py R0->R3 (directive 12).
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

import frognet_tuples as T
from media_stream import (
    TupleControl, MediaStreamServer, MediaFrame, MEDIAHOST_NAME,
)
from sotf_media_backing import MediaSocketReceiver, MediaSocketSender

from sotf_media_codex import pack_frame, unpack_frame   # codex interleaved frame (!IBII)
# The DEPLOYED client (call_media.CallSender) packs the uplink with pack_av (!IQBII:
# seq,ts,key,alen,vlen). The server must unpack the SAME format the client sends - read
# the real client packer, do not assume the codex header. Both are interleaved FNWP-1;
# they differ only by the ts field. call_media is authoritative for what's on the wire.
from call_media import unpack_av, unpack_av_src, pack_av

try:
    import sotf_ladder
except Exception:                                       # pragma: no cover
    sotf_ladder = None

# ---- logging: ride the standard frognet.media tree (FROGNET_LOG_LEVEL), AND always
# write a file so there's something to read on the box no matter how this is launched.
import logging as _logging
import os as _os

def _make_log():
    try:
        from frognet_log import get_logger
        lg = get_logger("media.host")
    except Exception:
        lg = _logging.getLogger("frognet.media.host")
        if not lg.handlers:
            h = _logging.StreamHandler()
            h.setFormatter(_logging.Formatter("[frognet:media.host] %(levelname)s %(message)s"))
            lg.addHandler(h)
    # default to INFO so the path is visible without setting an env var; FROGNET_LOG_LEVEL
    # can still raise/lower it.
    lvl = _os.environ.get("FROGNET_LOG_LEVEL", "INFO").upper()
    lg.setLevel(getattr(_logging, lvl, _logging.INFO))
    # add a FILE handler once, in addition to whatever stderr handler exists
    if not any(isinstance(h, _logging.FileHandler) for h in lg.handlers):
        for path in ("/var/log/frognet/mediahost.log", "/tmp/frognet-mediahost.log"):
            try:
                d = _os.path.dirname(path)
                if d and not _os.path.isdir(d):
                    _os.makedirs(d, exist_ok=True)
                fh = _logging.FileHandler(path)
                fh.setFormatter(_logging.Formatter(
                    "%(asctime)s [media.host] %(levelname)s %(message)s"))
                lg.addHandler(fh)
                lg._frognet_logpath = path
                break
            except Exception:
                continue
    return lg

log = _make_log()


# Per-session A/V port allocation. The elected media host owns a pool; each new call
# session gets its OWN tx/rx pair so sessions never share a connector. The base is the
# A/V plane (NOT 80, NOT 9009). One slot = one (tx, rx) pair.
AV_PORT_BASE = 9100
PORTS_PER_SESSION = 2
# A session leaves the discovery window when its one-shot create tuple ages out (~30s),
# but the call may still be live. Only reap after this much idle time (no frames, no
# consumers) so a live call is never torn down mid-stream.
REAP_IDLE_GRACE_S = 20.0


class _PortPool:
    def __init__(self, base: int = AV_PORT_BASE):
        self.base = base
        self._used: Dict[str, int] = {}        # session -> slot index
        self._lock = threading.Lock()

    def acquire(self, session: str) -> tuple:
        with self._lock:
            if session in self._used:
                slot = self._used[session]
            else:
                used = set(self._used.values())
                slot = next(i for i in range(4096) if i not in used)
                self._used[session] = slot
            tx = self.base + PORTS_PER_SESSION * slot
            return tx, tx + 1

    def release(self, session: str):
        with self._lock:
            self._used.pop(session, None)


class _SessionServer:
    """One call session: its own A/V port pair, its own MediaStreamServer, its own tx
    receiver and per-consumer rx senders. Reads ALL producer channels into the one
    ingestion queue; pumps the drained frames out to every consumer (the single output
    point). Audio rides interleaved in the same frame - never separated from the wire."""

    def __init__(self, session: str, backend, addr: str, tx: int, rx: int):
        self.session = session
        self.addr = addr
        self.tx = tx
        self.rx = rx
        self.control = TupleControl(backend, session, addr=addr)
        self.server = MediaStreamServer(self.control, addr=addr,
                                        tx_port=tx, rx_port=rx, ladder=sotf_ladder)
        self._rx_listener: Optional[MediaSocketReceiver] = None      # producers -> here
        self._dl_listener: Optional["_DownlinkListener"] = None       # consumers <- here
        self._stop = threading.Event()
        self._pump_t: Optional[threading.Thread] = None

    def start(self):
        # 2+3: create per-session objects, publish conn_info (the ports) to the tuple.
        self.server.maybe_create_from_tuple()
        self._n_in = 0; self._n_ingest = 0; self._n_bcast = 0
        self._last_activity = time.time()    # frame-in or consumer-connect; liveness clock
        # 6 (uplink): bind the dedicated A/V tx socket. The accept loop takes EVERY
        # producer concurrently; each frame is appended to the one ingestion queue.
        self._rx_listener = MediaSocketReceiver("0.0.0.0", self.tx,
                                                on_frame=self._on_producer_frame)
        self._rx_listener.start()
        # 6 (downlink): the server LISTENS on the rx port. Each consumer CONNECTS to it
        # (per the open_rx contract: consumer = rx, drains server output). Every accepted
        # connection becomes a fan target; the pump writes drained frames to all of them.
        self._dl_listener = _DownlinkListener("0.0.0.0", self.rx, on_accept=self._on_consumer)
        self._dl_listener.start()
        # drain+fan loop: the single output point -> all connected rx sockets.
        self._pump_t = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump_t.start()
        log.info("[DIAG-SESS] session=%s START uplink_listen=0.0.0.0:%d "
                 "downlink_listen=0.0.0.0:%d", self.session, self.tx, self.rx)

    def _on_consumer(self, peeraddr):
        self._last_activity = time.time()
        log.info("[DIAG-DL] session=%s CONSUMER connected from %s (downlink formed) "
                 "consumers=%d", self.session, peeraddr, self._dl_listener.count())

    def _on_producer_frame(self, payload: bytes):
        """A producer wrote an interleaved FNWP-1 frame on its A/V socket. The deployed
        client packs with pack_av (!IQBII: seq,ts,key,alen,vlen). Unpack with the SAME
        (unpack_av) - audio+video stay interleaved; we do NOT split the wire. Append to
        the one ingestion queue. MediaStreamServer.ingest() sheds droppable video on
        backlog, protects audio, and publishes backpressure."""
        seq = is_key = audio = video = None
        src = ""
        try:
            src, seq, ts, is_key, audio, video = unpack_av_src(payload)
        except Exception as e:
            log.warning("[DIAG-IN] session=%s UNPACK FAILED len=%d err=%r",
                        self.session, len(payload), e)
        self._n_in += 1
        self._last_activity = time.time()
        if self._n_in == 1:
            log.info("[DIAG-IN] session=%s FIRST producer frame src=%s seq=%s key=%s "
                     "alen=%d vlen=%d total_len=%d", self.session, src, seq, is_key,
                     len(audio or b""), len(video or b""), len(payload))
        elif self._n_in % 100 == 0:
            log.info("[DIAG-IN] session=%s producer frames=%d (last src=%s vlen=%d)",
                     self.session, self._n_in, src, len(video or b""))
        fr = MediaFrame(seq=seq or 0,
                        kind="video" if (video and not audio) else "audio",
                        payload=payload,
                        level_idx=(self.server._cur_bearer))
        try:
            setattr(fr, "is_key", bool(is_key))
        except Exception:
            pass
        res = self.server.ingest(fr)
        self._n_ingest += 1
        if self._n_ingest == 1:
            log.info("[DIAG-INGEST] session=%s FIRST ingest result=%s qdepth=%d",
                     self.session, res, self.server.queue.depth())
        if res in ("dropped", "stalled") and self._n_ingest % 50 == 0:
            log.warning("[DIAG-INGEST] session=%s res=%s qdepth=%d (backpressure)",
                        self.session, res, self.server.queue.depth())

    def _read_leg_requests(self):
        """Map client addr -> requested rung, from the leg_request tuples.

        [NO_MASKED_CONTRACT_V1] The bare `except Exception: return {}` this
        replaces was the most expensive kind of fallback: {} is a LEGITIMATE
        answer meaning "no leg has requested a rung", which is true most of the
        time. So a control plane that could not answer at all was
        indistinguishable from a session where nobody had asked for anything.

        Measured: TupleControl has no all_leg_requests() -- it is called here and
        defined nowhere. Every pass raised AttributeError, logged one warning
        into a log nobody greps, and returned {}. Per-leg rung honoring has never
        once worked, and test_leg_request_oracle.py exercises three methods that
        do not exist.

        A missing method is not a missing leg. It means this server's control
        plane does not implement the contract the server requires, and that is a
        fault at startup, not a per-frame warning. Raise it.
        """
        fn = getattr(self.control, "all_leg_requests", None)
        if fn is None:
            raise AttributeError(
                "TupleControl.all_leg_requests() is missing: this media host "
                "requires it to honour per-leg rung requests. Returning an empty "
                "map here would be indistinguishable from a session in which no "
                "leg has asked for anything, which is how this went unnoticed.")
        out = {}
        for req in fn():
            addr = req.get("addr")
            if addr is not None:
                out[addr] = int(req["leg_bearer"])
        return out

    def _read_media_speeds(self):
        """[MEDIASPEED_V1] {peer_addr: bps} for this session. 0/absent is
        unrestricted, and an unrestricted session is genuinely {} -- but a
        backend that cannot answer raises rather than reporting the same thing."""
        return self.control.all_media_speeds()

    def _legs_without_video(self, leg_rungs):
        """The set of downlink conns whose leg requested an audio-only rung (<=L4: no video).
        Video frames are dropped for these conns at the server - fully honoring the client's
        downgrade request - while audio still flows. Matched by conn peer IP."""
        import sotf_leg_adapt as _A
        if not leg_rungs:
            return None
        drop = set()
        dl = self._dl_listener
        with dl._lock:
            peer_of = dict(dl._peer_of)
        for conn, peer in peer_of.items():
            ip = peer[0] if isinstance(peer, (tuple, list)) else peer
            rung = leg_rungs.get(ip)
            if rung is not None and not _A.carries_video(rung):
                drop.add(conn)
        return drop or None

    def _frame_has_video(self, fr):
        """True if this frame carries a video payload (vlen>0)."""
        try:
            src, seq, ts, is_key, audio, video = unpack_av_src(fr.payload)
            return bool(video)
        except Exception:
            return False

    def _pump_loop(self):
        # The single output point: drain the ingestion queue and broadcast each frame to
        # every connected downlink consumer. Non-blocking per socket (drop-don't-block) AND
        # per-leg: a consumer that requested an audio-only rung gets video dropped for it.
        import sotf_leg_adapt as _A
        last_leg_read = 0.0
        last_health = time.time()
        leg_rungs = {}                 # viewer -> requested rung (cached)
        while not self._stop.is_set():
            now = time.time()
            # closed-loop recovery: tick health ~1/s whether idle or busy, so the bearer
            # recovers UP after a sustained clean period (down is handled on drop in ingest).
            if now - last_health >= 1.0:
                last_health = now
                try:
                    self.server.health_tick()
                except Exception as e:
                    log.warning("[RECOVER] health_tick failed session=%s err=%r", self.session, e)
            frames = self.server.queue.drain(64)
            if not frames:
                time.sleep(0.005)          # idle backoff; busy when frames flow
                continue
            # refresh per-leg requests at most ~1/s (tuple read is not free; legs change slowly)
            if now - last_leg_read >= 1.0:
                last_leg_read = now
                leg_rungs = self._read_leg_requests()
                # [MEDIASPEED_V1] refresh the artificial per-consumer caps on the
                # same pass that reads the per-leg rungs.
                if self._dl_listener is not None:
                    self._dl_listener.set_speed_caps(self._read_media_speeds())
            for fr in frames:
                nconsumers = self._dl_listener.count()
                drop_for = self._legs_without_video(leg_rungs) if self._frame_has_video(fr) else None
                self._dl_listener.broadcast(fr.payload, drop_for=drop_for)
                self.server.metrics.on_sent_on()
                self._n_bcast += 1
                if self._n_bcast == 1:
                    log.info("[DIAG-OUT] session=%s FIRST broadcast seq=%s consumers=%d "
                             "len=%d", self.session, fr.seq, nconsumers, len(fr.payload))
                    if nconsumers == 0:
                        log.warning("[DIAG-OUT] session=%s broadcasting with NO consumers "
                                    "connected - downlink not formed yet", self.session)
                elif self._n_bcast % 100 == 0:
                    log.info("[DIAG-OUT] session=%s broadcast=%d consumers=%d",
                             self.session, self._n_bcast, nconsumers)

    def close(self):
        self._stop.set()
        try:
            if self._rx_listener:
                self._rx_listener.close()
        except Exception:
            pass
        try:
            if self._dl_listener:
                self._dl_listener.close()
        except Exception:
            pass


import socket as _socket
import struct as _struct
import select as _select

_DL_LEN = _struct.Struct("!I")     # same length framing the producer uses / client reads


class _DownlinkListener:
    """Server side of the downlink: LISTEN on the rx port; each consumer CONNECTS to it
    (open_rx contract: consumer = rx, drains server output). broadcast() writes one
    length-framed frame to every connected consumer, non-blocking - a slow/dead consumer
    is dropped, never blocks the pump or the other consumers."""

    def __init__(self, host: str, port: int, on_accept=None):
        self.host = host
        self.port = port
        self.on_accept = on_accept
        self._srv = None
        self._conns = []
        # [MEDIASPEED_V1] per-consumer artificial caps and their token buckets.
        # Empty means every leg is unrestricted, which is the normal case.
        self._speed_caps = {}                       # peer_addr -> bps
        self._buckets = {}                          # peer_addr -> (tokens, last_t)
        self._capped_drops = 0
        self._peer_of = {}                  # conn -> (ip, port), for per-leg request lookup
        self._wouldblock_drops = 0          # frames dropped because a leg's buffer was full
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self):
        self._srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        self._t.start()

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, peer = self._srv.accept()
            except OSError:
                return
            conn.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
            conn.setblocking(False)                 # non-blocking: a slow consumer never
                                                    # blocks the pump or other consumers
            with self._lock:
                self._conns.append(conn)
                self._peer_of[conn] = peer          # remember who, for per-leg honoring
                                                    # and for [MEDIASPEED_V1] caps
            if self.on_accept:
                try:
                    self.on_accept(peer)
                except Exception:
                    pass

    def set_speed_caps(self, caps):
        """[MEDIASPEED_V1] {peer_addr: bps} -- an ARTIFICIAL cap per consumer.

        A client's link is symmetric, so the one number the client publishes
        covers both legs: it throttles its own uplink, and this throttles the
        host's downlink TO it. Independent of every other consumer.

        Absent or 0 is UNRESTRICTED and costs nothing -- no bucket is created and
        the frame goes out on the existing whole-frame-or-drop path."""
        with self._lock:
            self._speed_caps = dict(caps or {})
            for addr in list(self._buckets):
                if addr not in self._speed_caps:
                    self._buckets.pop(addr, None)   # cap lifted -> bucket gone

    def _over_cap(self, peer, nbits):
        """Token bucket for one consumer. True == over budget, drop this frame.

        Identical shaping to the sender's own throttle, so the two legs behave
        the same way under the same number: a capped wire SHEDS, it does not
        queue, and the ladder reads the shedding as congestion."""
        if not peer:
            return False
        addr = peer[0] if isinstance(peer, tuple) else peer
        bps = self._speed_caps.get(addr, 0)
        if bps <= 0:
            return False
        now = time.time()
        # Start EMPTY, not full. A full bucket permits an initial burst of one
        # bucket-depth -- measured 192 kbps in the first second against a
        # 100 kbps cap -- which is exactly the overshoot this exists to prevent.
        # Depth is one second of budget, so it still absorbs normal jitter.
        tokens, last = self._buckets.get(addr, (0.0, now))
        tokens = min(float(bps), tokens + (now - last) * bps)
        if tokens < nbits:
            self._buckets[addr] = (tokens, now)
            return True
        self._buckets[addr] = (tokens - nbits, now)
        return False

    def broadcast(self, payload: bytes, drop_for=None):
        """Write one length-framed frame to every consumer, NON-BLOCKING, whole-frame-or-drop.
        For each consumer we check writability with a zero-timeout select: if its send buffer
        has room, send the whole frame; if the buffer is FULL (its downlink is congested), DROP
        the whole frame for that consumer only - never block the pump, never leave a partial
        frame on the wire (which would corrupt the length-framed stream). A dead socket is
        reaped. `drop_for` skips conns whose leg shouldn't get this rung's frame (per-leg)."""
        framed = _DL_LEN.pack(len(payload)) + payload
        with self._lock:
            conns = list(self._conns)
        dead = []
        for c in conns:
            if drop_for and c in drop_for:
                continue                            # per-leg: this rung not for this leg
            # [MEDIASPEED_V1] artificial per-consumer cap, before the writability
            # probe: a capped leg sheds even when its buffer has room, which is
            # the whole point -- we are simulating a slower link, not a full one.
            if self._speed_caps and self._over_cap(self._peer_of.get(c),
                                                   len(framed) * 8):
                self._capped_drops += 1
                continue
            try:
                # zero-timeout writability probe: room in the send buffer?
                _, wr, _ = _select.select([], [c], [], 0)
                if not wr:
                    self._wouldblock_drops += 1     # buffer full -> congested leg -> drop whole frame
                    continue
                c.sendall(framed)                   # room exists; whole frame goes atomically
            except OSError:
                dead.append(c)
        if dead:
            with self._lock:
                for c in dead:
                    if c in self._conns:
                        self._conns.remove(c)
                    self._peer_of.pop(c, None)
                    try:
                        c.close()
                    except OSError:
                        pass

    def count(self):
        with self._lock:
            return len(self._conns)

    def close(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except OSError:
            pass
        with self._lock:
            for c in self._conns:
                try:
                    c.close()
                except OSError:
                    pass
            self._conns.clear()


class MediaHostServer:
    """Sits and waits. Watches tuple space for ANY create-stream, spins up a
    _SessionServer (per-session port + objects + sockets) for each, reaps ended ones."""

    def __init__(self, addr: str = None,
                 dbhost: str = "databasehost.frognet"):
        # conn_info must carry the server's REAL reachable FrogNet IP, not the role NAME
        # "mediahost.frognet" - clients read the IP+port from the tuple and dial it. If no
        # addr is forced, resolve this node's own 10/8 address via frognet_tuples.my_ip().
        if addr is None:
            try:
                addr = T.my_ip()
            except Exception:
                addr = MEDIAHOST_NAME
        self.addr = addr
        self.dbhost = dbhost
        self.pool = _PortPool()
        self.sessions: Dict[str, _SessionServer] = {}
        self._stop = threading.Event()

    def _backend(self):
        dbhost = self.dbhost
        class _B:
            def put(self, service, var, scope, value, addr=None):
                return T.put(service, var, scope, value, dbhost=dbhost)
            def get(self, service, var, fresh_s=0):
                return T.get(service, var, dbhost=dbhost, fresh_s=fresh_s)
            def get_one(self, service, var, scope, fresh_s=0):
                for r in T.get(service, var, dbhost=dbhost, fresh_s=fresh_s):
                    if r.get("scope") == scope:
                        return r.get("value")
                return None
        return _B()

    def _discover_sessions(self, backend):
        """Every live session = scope of a fresh create tuple under 'mediastream'."""
        sids = []
        for r in backend.get("mediastream", "create", fresh_s=30):
            scope = r.get("scope", "")
            if scope.startswith("session:"):
                sid = scope.split(":", 1)[1]
                if sid and sid not in sids:
                    sids.append(sid)
        return sids

    def run(self, interval: float = 1.0):
        backend = self._backend()
        logpath = getattr(log, "_frognet_logpath", "(stderr only)")
        log.info("[DIAG-SRV] mediahost UP addr=%s dbhost=%s logfile=%s - watching tuples",
                 self.addr, self.dbhost, logpath)
        print(f"[mediahost] SERVER up addr={self.addr} dbhost={self.dbhost} - "
              f"watching tuple space for calls  (log: {logpath})", flush=True)
        while not self._stop.is_set():
            try:
                live = set(self._discover_sessions(backend))
                for sid in live:
                    if sid not in self.sessions:
                        tx, rx = self.pool.acquire(sid)
                        s = _SessionServer(sid, backend, self.addr, tx, rx)
                        s.start()
                        self.sessions[sid] = s
                        log.info("[DIAG-SRV] session=%s NEW tx=%d rx=%d conn_info published",
                                 sid, tx, rx)
                        print(f"[mediahost] session={sid} NEW -> tx={tx} rx={rx} "
                              f"conn_info published", flush=True)
                for sid in list(self.sessions):
                    if sid not in live:
                        sess = self.sessions[sid]
                        # The create tuple is a ONE-SHOT intent, not a heartbeat - it ages
                        # out of the discovery window (~30s) while the call is still live.
                        # Do NOT reap on tuple-staleness alone: only reap a session that is
                        # also genuinely idle (no consumers, no recent producer frames).
                        idle = time.time() - sess._last_activity
                        has_consumers = (sess._dl_listener.count() > 0
                                         if sess._dl_listener else False)
                        if has_consumers or idle < REAP_IDLE_GRACE_S:
                            continue                 # still live - keep it
                        self.sessions.pop(sid).close()
                        self.pool.release(sid)
                        log.info("[DIAG-SRV] session=%s ended -> reaped (idle=%.1fs)", sid, idle)
                        print(f"[mediahost] session={sid} ended -> reaped", flush=True)
            except Exception as e:
                log.error("[DIAG-SRV] loop error: %r", e)
            time.sleep(interval)

    def close(self):
        self._stop.set()
        for s in self.sessions.values():
            s.close()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="FrogNet SotF A/V media host")
    ap.add_argument("--addr", default=None,
                    help="IP to publish in conn_info (default: this node's my_ip 10/8)")
    ap.add_argument("--dbhost", default="databasehost.frognet",
                    help="DATA host the media tuples live on (NOT _control)")
    args = ap.parse_args(argv)
    srv = MediaHostServer(addr=args.addr, dbhost=args.dbhost)
    try:
        srv.run()
    except KeyboardInterrupt:
        srv.close()


if __name__ == "__main__":
    main()
