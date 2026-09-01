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
media_stream.py - SotF media stream LIFECYCLE over FNWP-1 (the orchestration layer).

This is the production stream lifecycle John specified, built on the pieces that
already exist (codec/handler/ladder/tuples/name). It does NOT reinvent transport or
codec - it binds them:

  DATA  plane: pure RAW frames in FNWP-1, producer->ingestion-queue->server->consumers.
               (sim wire = transport_sim_tier.create_connected_pair; codec = the
               proven SotFSender/SotFReceiver path in sotf_video_stream_test.)
  CONTROL plane: the GENERAL tuple space (frognet_tuples / MockSpace in sim) carries
               create-stream, connection-info, endpoint control intents, and the
               server's AUTHORITATIVE async status (state/bearer/error).

Two target semantics (John's distinction):
  databasehost.frognet = general space: T.put a fact, everyone reads it.
  mediahost.frognet     = a specific consumer's INGESTION QUEUE: binary appended to
                          one service's intake. Targeted, not converged-for-all.

Backpressure: the ingestion queue is bounded. On backlog we DROP droppable frames
(video LATEST_DROPPABLE) but NEVER audio (CONTINUOUS/protected), and the server
publishes a `bearer` tuple; the sender's sotf_ladder.send_level reads it and steps
the rung DOWN. The media payload never touches the tuple space; control never touches
the binary wire.

The TUPLE BACKEND is injected (real frognet_tuples on a box, MockSpace in the sim),
so the lifecycle is testable without a live DB.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import sotf_metrics  # per-node counters + deterministic slow-node detection

# -- logging spine: ride frognet_log/frognet_trace (the tree-wide switch) when
#    present; fall back to stdlib so the bundle still runs standalone. Operational
#    state at INFO, chatty/per-frame at DEBUG/TRACE (off by default), faults at
#    WARNING/ERROR with context. One env var (FROGNET_LOG_LEVEL) controls it all.
try:
    from frognet_log import get_logger          # type: ignore
    log = get_logger("media.stream")
except Exception:  # pragma: no cover - standalone fallback
    import logging as _logging
    log = _logging.getLogger("frognet.media.stream")
    if not log.handlers:
        _h = _logging.StreamHandler()
        _h.setFormatter(_logging.Formatter("[frognet:%(name)s] %(levelname)s %(message)s"))
        log.addHandler(_h)
    log.setLevel(__import__("os").environ.get("FROGNET_LOG_LEVEL", "WARNING").upper()
                 if __import__("os").environ.get("FROGNET_LOG_LEVEL", "WARNING").upper()
                 != "TRACE" else 5)

try:
    from frognet_trace import trace_event        # type: ignore
except Exception:  # pragma: no cover
    def trace_event(event, **kv):                 # TRACE-level structured event
        if log.isEnabledFor(5):
            log.log(5, "EVENT name=%s %s", event,
                    " ".join("%s=%s" % kv2 for kv2 in kv.items()))

# ---- freshness classes for the ingestion queue drop policy -------------------
# Mirrors media_codex / AVCodex FRESHNESS: audio is the protected gate, video sheds.
AUDIO_PROTECTED = "audio"          # CONTINUOUS - never dropped on backlog
VIDEO_DROPPABLE = "video"          # LATEST_DROPPABLE - dropped first under pressure

SERVICE = "mediastream"            # tuple service namespace
MEDIAHOST_NAME = "mediahost.frognet"   # floating elected name (frognet_service_hosts)


def session_scope(session_id: str) -> str:
    return f"session:{session_id}"


def host_scope(ip: str, pid: int = 1) -> str:
    return f"host:{ip}:{pid}"


# ============================================================================
# Media frame as it rides the ingestion queue (kind = freshness class)
# ============================================================================
@dataclass
class MediaFrame:
    seq: int
    kind: str            # AUDIO_PROTECTED or VIDEO_DROPPABLE
    payload: bytes       # RAW bytes - what rides FNWP-1 as TYPE_RAW
    is_keyframe: bool = False
    level_idx: int = 4


# ============================================================================
# Ingestion queue - bounded; drops DROPPABLE frames on backlog, never PROTECTED.
# This is the structure behind mediahost.frognet: append-and-the-server-drains.
# ============================================================================
class IngestionQueue:
    """Bounded intake for one stream. append() never blocks the producer; when the
    queue is at/over its high-water mark it sheds the OLDEST droppable (video) frame
    to make room, and reports a drop so the server can publish backpressure. Audio
    (protected) is never shed - a gap is audible. If the queue is full of protected
    frames, append reports `stalled` (the real signal that even audio can't keep up,
    which forces a rung drop rather than silent loss)."""

    def __init__(self, high_water: int = 32):
        self.high_water = high_water
        self._dq: List[MediaFrame] = []
        self._lock = threading.Lock()
        self.appended = 0
        self.dropped = 0
        self.stalled = 0

    def depth(self) -> int:
        with self._lock:
            return len(self._dq)

    def append(self, fr: MediaFrame) -> str:
        """Returns 'ok' | 'dropped' (a droppable frame was shed to fit) | 'stalled'
        (queue full of protected frames; nothing droppable to shed)."""
        with self._lock:
            if len(self._dq) < self.high_water:
                self._dq.append(fr)
                self.appended += 1
                return "ok"
            # over high-water: try to shed the oldest DROPPABLE frame
            for i, q in enumerate(self._dq):
                if q.kind == VIDEO_DROPPABLE:
                    del self._dq[i]
                    self.dropped += 1
                    self._dq.append(fr)
                    self.appended += 1
                    log.warning("queue_drop seq=%d kind=%s depth=%d high_water=%d "
                                "shed_seq=%d (backlog -> droppable shed, bearer should step down)",
                                fr.seq, fr.kind, len(self._dq), self.high_water, q.seq)
                    return "dropped"
            # nothing droppable to shed (all protected/audio) - refuse, signal stall
            self.stalled += 1
            log.error("queue_stall seq=%d kind=%s depth=%d high_water=%d "
                      "(queue full of PROTECTED frames; even audio can't keep up)",
                      fr.seq, fr.kind, len(self._dq), self.high_water)
            return "stalled"

    def drain(self, n: int = 1) -> List[MediaFrame]:
        with self._lock:
            out = self._dq[:n]
            del self._dq[:n]
            return out

    def drain_all(self) -> List[MediaFrame]:
        with self._lock:
            out = self._dq[:]
            self._dq.clear()
            return out


# ============================================================================
# Control/status namespace helpers - the tuple plane (general space).
# A tuple backend is injected: real frognet_tuples on a box, MockSpace in sim.
# ============================================================================
# Server-owned status verbs (only the server writes these; everyone reads):
ST_STATE = "state"          # {"state": "live"|"created"|"ended", ...}
ST_CONN = "conn_info"       # {"addr","tx","rx","supports":[...]} published on create
ST_BEARER = "bearer"        # {"level_idx": int} backpressure -> sender ladder
ST_ERROR = "error"          # {"code","msg"}
ST_METRICS = "stream_metrics"   # per-node counters (server aggregate + endpoint self-observed)
# Endpoint-owned control intents (endpoints write; server reads):
CTL_CREATE = "create"       # {"session_id","codec","sr","layout","level_idx"} first producer
CTL_INTENT = "intent"       # {"verb": "pause"|"resume"|"seek"|"rewind"|"restart", ...}
CTL_MEDIASPEED = "MediaSpeed"   # {"bps": int, "addr": str} per (session, viewer)
                                # An ARTIFICIAL cap on one client's link. Under
                                # normal conditions the bandwidth is discovered
                                # and the ladder finds its own level; this exists
                                # to constrain it on purpose. Absent or bps==0
                                # means UNRESTRICTED -- no bucket, no cost.
                                #
                                # A client's link is symmetric, so ONE number
                                # covers both legs: the client applies it to its
                                # own uplink (camera -> host) and the media host
                                # applies it to that client's downlink
                                # (host -> client). The two legs are independent
                                # of every other user and stream.


class TupleControl:
    """Thin facade over the tuple backend for one session. Mirrors the
    frognet_tuples surface (put/get_one). The backend must expose:
        put(service, var, scope, value, addr=...)
        get_one(service, var, scope, fresh_s=...) -> value|None
        get(service, var, fresh_s=...) -> [rows]
    Both MockSpace (sim) and a frognet_tuples adapter satisfy this."""

    def __init__(self, backend: Any, session_id: str, addr: str = "10.0.0.1"):
        self.b = backend
        self.session_id = session_id
        self.scope = session_scope(session_id)
        self.addr = addr

    # server writes ---------------------------------------------------------
    def publish_conn_info(self, addr: str, tx: int, rx: int, supports: List[str]):
        self.b.put(SERVICE, ST_CONN, self.scope,
                   {"addr": addr, "tx": tx, "rx": rx, "supports": list(supports)},
                   addr=self.addr)

    def set_state(self, state: str, **extra):
        self.b.put(SERVICE, ST_STATE, self.scope, {"state": state, **extra}, addr=self.addr)

    def set_error(self, code: str, msg: str):
        self.b.put(SERVICE, ST_ERROR, self.scope, {"code": code, "msg": msg}, addr=self.addr)

    def publish_bearer(self, level_idx: int):
        # bearer is per-(session) here for the sim; on a box it is SD:bearer.<host>
        self.b.put(SERVICE, ST_BEARER, self.scope, {"level_idx": int(level_idx)}, addr=self.addr)

    # endpoint writes -------------------------------------------------------
    def request_create(self, **params):
        self.b.put(SERVICE, CTL_CREATE, self.scope, dict(params), addr=self.addr)

    def write_intent(self, verb: str, **extra):
        self.b.put(SERVICE, CTL_INTENT, self.scope, {"verb": verb, **extra}, addr=self.addr)

    # [MEDIASPEED_V1] endpoint writes its own cap; the host reads everyone's ----
    def set_media_speed(self, viewer: str, bps: int, addr: Optional[str] = None):
        """Publish this viewer's artificial link cap for this session.

        Scoped per (session, viewer) because the constraint belongs to a client,
        not to a call -- and a viewer is in one call at a time, so per-viewer and
        per-call coincide. addr is carried because the media host matches its
        downlink consumers by peer IP.
        """
        self.b.put(SERVICE, CTL_MEDIASPEED,
                   "%s:viewer:%s" % (self.scope, viewer),
                   {"bps": int(bps or 0), "addr": addr or self.addr, "viewer": viewer},
                   addr=self.addr)

    def media_speed(self, viewer: str) -> int:
        """This viewer's cap, or 0 for unrestricted."""
        row = self.b.get_one(SERVICE, CTL_MEDIASPEED,
                             "%s:viewer:%s" % (self.scope, viewer))
        return int((row or {}).get("bps") or 0)

    def all_media_speeds(self) -> Dict[str, int]:
        """{peer_addr: bps} for every viewer in THIS session that has published a
        cap. Rows with bps==0 are omitted -- unrestricted is the absence of a
        bucket, not a bucket of infinite size.

        This does NOT swallow a backend failure. A backend that cannot answer is
        not a session with no caps: returning {} for both is how a broken control
        plane becomes indistinguishable from an unconstrained one, and that
        confusion has cost real days. The caller decides what to do with the
        exception; it does not get a plausible empty answer instead.
        """
        prefix = "%s:viewer:" % self.scope
        out: Dict[str, int] = {}
        for r in self.b.get(SERVICE, CTL_MEDIASPEED):
            if not str(r.get("scope", "")).startswith(prefix):
                continue
            v = r.get("value") or {}
            bps = int(v.get("bps") or 0)
            addr = v.get("addr")
            if bps > 0 and addr:
                out[addr] = bps
        return out

    # readers ---------------------------------------------------------------
    def conn_info(self) -> Optional[Dict[str, Any]]:
        return self.b.get_one(SERVICE, ST_CONN, self.scope)

    def state(self) -> Optional[Dict[str, Any]]:
        return self.b.get_one(SERVICE, ST_STATE, self.scope)

    def bearer(self) -> Optional[int]:
        v = self.b.get_one(SERVICE, ST_BEARER, self.scope)
        return int(v["level_idx"]) if v and "level_idx" in v else None

    def error(self) -> Optional[Dict[str, Any]]:
        return self.b.get_one(SERVICE, ST_ERROR, self.scope)

    def create_request(self) -> Optional[Dict[str, Any]]:
        return self.b.get_one(SERVICE, CTL_CREATE, self.scope)

    def intent(self) -> Optional[Dict[str, Any]]:
        return self.b.get_one(SERVICE, CTL_INTENT, self.scope)

    # metrics: one row per node within the session (server=aggregate authority,
    # endpoints=self-observed). Same plane, same backend; read back for the monitor.
    def publish_metrics(self, node: str, snapshot: Dict[str, Any], own: bool):
        self.b.put(SERVICE, ST_METRICS, f"{self.scope}:metrics:{node}", snapshot,
                   addr=self.addr, own=own)

    def all_metrics(self, fresh_s: int = 30) -> List[Dict[str, Any]]:
        rows = self.b.get(SERVICE, ST_METRICS, fresh_s=fresh_s)
        out = []
        for r in rows:
            sc = r.get("scope", "")
            if sc.startswith(f"{self.scope}:metrics:"):
                out.append(r.get("value", {}))
        return out


# ============================================================================
# MediaStreamServer - the ffmpeg server. Watches for create-stream, allocates the
# ingestion queue + publishes connection-info, drains+fans frames to consumers,
# is the AUTHORITATIVE async status writer, applies backpressure (drop + bearer).
#
# The actual FNWP-1 binary connections are injected as a `fanout` callable so this
# stays transport-agnostic and unit-testable; the sim wires it to the real
# SotFSender/SotFReceiver path, the box wires it to proxy/daemon.
# ============================================================================
SUPPORTED_VERBS = ("pause", "resume", "seek", "rewind", "restart")  # what THIS server advertises


@dataclass
class _Consumer:
    cid: str
    sink: Callable[[MediaFrame], None]   # deliver a frame to this consumer (rx path)


class MediaStreamServer:
    def __init__(self, control: TupleControl, addr: str = MEDIAHOST_NAME,
                 tx_port: int = 9101, rx_port: int = 9102, high_water: int = 32,
                 ladder=None, node: str = "ffmpeg"):
        self.control = control
        self.addr = addr
        self.tx_port = tx_port
        self.rx_port = rx_port
        self.queue = IngestionQueue(high_water=high_water)
        self.consumers: Dict[str, _Consumer] = {}
        self._lock = threading.Lock()
        self.created = False
        self.ended = False
        self._cur_bearer = (ladder.MAX_IDX if ladder else 7)
        self.ladder = ladder
        self.applied_intents: List[Dict[str, Any]] = []
        # backpressure thresholds
        self._bp_drop_floor = 1     # any drop pushes bearer down a rung
        # metrics: the server is the AUTHORITATIVE aggregate writer (injected, sent_on, fps)
        self.node = node
        self.metrics = sotf_metrics.StreamMetrics(
            control.session_id, node, sotf_metrics.ROLE_SERVER)

    # -- lifecycle: honor a create-stream tuple, publish connection-info -----
    def maybe_create_from_tuple(self) -> bool:
        """Idempotent: if a create request exists and we haven't created yet,
        create the stream and publish conn-info. Returns True if (now) created."""
        if self.created:
            return True
        req = self.control.create_request()
        if not req:
            return False
        self._create(req)
        return True

    def _create(self, params: Dict[str, Any]):
        with self._lock:
            if self.created:
                return
            self.created = True
        self.control.publish_conn_info(self.addr, self.tx_port, self.rx_port,
                                       list(SUPPORTED_VERBS))
        self.control.set_state("live", codec=params.get("codec", "vp8"))
        log.info("stream_create session=%s addr=%s tx=%d rx=%d codec=%s verbs=%s",
                 self.control.session_id, self.addr, self.tx_port, self.rx_port,
                 params.get("codec", "vp8"), ",".join(SUPPORTED_VERBS))

    # -- consumers (rx side) -------------------------------------------------
    def add_consumer(self, cid: str, sink: Callable[[MediaFrame], None]):
        with self._lock:
            self.consumers[cid] = _Consumer(cid, sink)

    def remove_consumer(self, cid: str):
        with self._lock:
            self.consumers.pop(cid, None)

    # -- data plane: producer appends to the ingestion queue (tx side) -------
    def ingest(self, fr: MediaFrame) -> str:
        """Producer appends a RAW frame. Returns the queue result; on drop/stall the
        server applies backpressure (publishes a reduced bearer)."""
        res = self.queue.append(fr)
        if res != "stalled":
            self.metrics.on_injected()      # accepted into the ingestion queue
        if res in ("dropped", "stalled"):
            self._apply_backpressure(res)
        return res

    def _apply_backpressure(self, why: str):
        # step the advertised bearer DOWN one rung and publish it; the sender's
        # send_level reads it and degrades. 'stalled' (even audio can't keep up) is
        # more severe - drop two rungs.
        step = 2 if why == "stalled" else 1
        lo = (self.ladder.MIN_IDX if self.ladder else 0)
        prev = self._cur_bearer
        self._cur_bearer = max(lo, self._cur_bearer - step)
        self.control.publish_bearer(self._cur_bearer)
        log.warning("backpressure_bearer session=%s why=%s bearer=%d<-%d step=%d "
                    "(sender send_level should follow down)",
                    self.control.session_id, why, self._cur_bearer, prev, step)

    def recover_bearer(self):
        """Sustained health: step the advertised bearer back UP one rung."""
        hi = (self.ladder.MAX_IDX if self.ladder else 7)
        if self._cur_bearer < hi:
            prev = self._cur_bearer
            self._cur_bearer += 1
            self.control.publish_bearer(self._cur_bearer)
            log.info("bearer_recover session=%s bearer=%d<-%d",
                     self.control.session_id, self._cur_bearer, prev)

    # -- drain + fan-out to consumers (server emits RAW out) -----------------
    def pump(self, max_frames: int = 64) -> int:
        """Drain up to max_frames and fan each to every consumer. Returns count fanned."""
        frames = self.queue.drain(max_frames)
        n = 0
        with self._lock:
            consumers = list(self.consumers.values())
        for fr in frames:
            for c in consumers:
                try:
                    c.sink(fr)
                except Exception as e:
                    log.error("fanout_error session=%s consumer=%s seq=%d err=%r",
                              self.control.session_id, c.cid, fr.seq, e)
            self.metrics.on_sent_on()        # one frame emitted toward players (aggregate)
            trace_event("pump_fan", session=self.control.session_id, seq=fr.seq,
                        kind=fr.kind, consumers=len(consumers))
            n += 1
        return n

    def publish_metrics(self, own: bool = False):
        """Publish the server's authoritative aggregate (injected/sent_on/fps). own=False
        so the aggregate ages by ts rather than being reaped when a transient writer exits."""
        self.control.publish_metrics(self.node, self.metrics.snapshot(), own=own)

    # -- control intents from endpoints (server reads, applies if supported) -
    def poll_intents(self) -> Optional[Dict[str, Any]]:
        it = self.control.intent()
        if not it:
            return None
        verb = it.get("verb")
        if verb not in SUPPORTED_VERBS:
            self.control.set_error("unsupported_verb", f"{verb!r} not supported")
            log.warning("intent_rejected session=%s verb=%r supported=%s",
                        self.control.session_id, verb, ",".join(SUPPORTED_VERBS))
            return {"rejected": verb}
        self.applied_intents.append(it)
        self.control.set_state("live", last_intent=verb)
        log.info("intent_applied session=%s verb=%s", self.control.session_id, verb)
        return it

    def end(self):
        self.ended = True
        self.control.set_state("ended")
        log.info("stream_end session=%s injected=%d sent_on=%d queue_dropped=%d queue_stalled=%d",
                 self.control.session_id, self.metrics.snapshot().get("injected", 0),
                 self.metrics.snapshot().get("sent_on", 0), self.queue.dropped, self.queue.stalled)

    # -- autonomous watch loop: a deployed server reacts to the tuple space on its
    #    own - honoring create-stream, polling intents, recovering bearer - without
    #    a caller driving maybe_create/poll_intents by hand. Runs in a thread; stop()
    #    ends it. tick() is the single-step the tests drive deterministically.
    def tick(self):
        self.maybe_create_from_tuple()
        if self.created and not self.ended:
            self.poll_intents()
        self.publish_metrics()

    def watch(self, stop_event: "threading.Event", interval_s: float = 0.2,
              pump: bool = False):
        while not stop_event.is_set() and not self.ended:
            self.tick()
            if pump:
                self.pump()
            stop_event.wait(interval_s)

    def start_watch(self, interval_s: float = 0.2, pump: bool = False) -> "threading.Event":
        ev = threading.Event()
        threading.Thread(target=self.watch, args=(ev, interval_s, pump), daemon=True).start()
        return ev


# ============================================================================
# Endpoints - what a producer/consumer runs. Each resolves the floating media
# name, reads the published connection-info from the tuple space, then opens its
# binary FNWP-1 connection(s): producer = tx (append to ingestion queue), consumer
# = rx (drain server output). Connection factories are injected so the SAME endpoint
# logic runs in the sim (in-process server) and on a box (real FNWP-1 sockets).
#
#   open_tx(addr, port) -> send(MediaFrame)          # producer's transmit
#   open_rx(addr, port, sink: Callable[[MediaFrame]]) -> None   # consumer's receive
# ============================================================================
class MediaHostUnavailable(RuntimeError):
    pass


def resolve_conn_info(control: TupleControl, tries: int = 5,
                      sleep_s: float = 0.0) -> Dict[str, Any]:
    """Read the server's published connection-info from the session space. Addressing
    is the floating name mediahost.frognet (already in /etc/hosts); conn-info carries
    the ports + supported verbs. Bounded poll: a missing server (no election / no
    create yet) returns cleanly via MediaHostUnavailable, never hangs."""
    for _ in range(max(1, tries)):
        ci = control.conn_info()
        if ci:
            return ci
        if sleep_s:
            time.sleep(sleep_s)
    log.error("conn_info_timeout session=%s tries=%d "
              "(no mediahost election or no create-stream yet -> producers/consumers cannot dial)",
              control.session_id, tries)
    raise MediaHostUnavailable(
        f"no connection-info for session {control.session_id} "
        f"(is a mediahost elected and the stream created?)")

class MediaProducer:
    """A producer of a stream. Ensures the create-stream request exists, resolves
    conn-info, opens the tx connection, streams frames. Reads the server's bearer
    tuple and (with the ladder) caps its send level - backpressure as a control byte."""

    def __init__(self, control: TupleControl, open_tx: Callable[[str, int], Callable],
                 ladder=None, have_camera=True, have_mic=True, node: str = "producer"):
        self.control = control
        self.open_tx = open_tx
        self.ladder = ladder
        self.have_camera = have_camera
        self.have_mic = have_mic
        self._send = None
        self.conn_info: Optional[Dict[str, Any]] = None
        self.node = node
        self.metrics = sotf_metrics.StreamMetrics(
            control.session_id, node, sotf_metrics.ROLE_SENDER)

    def create_and_connect(self, **params) -> Dict[str, Any]:
        self.control.request_create(session_id=self.control.session_id, **params)
        self.conn_info = resolve_conn_info(self.control)
        self._send = self.open_tx(self.conn_info["addr"], self.conn_info["tx"])
        log.info("producer_connect session=%s node=%s addr=%s tx=%d",
                 self.control.session_id, self.node, self.conn_info["addr"],
                 self.conn_info["tx"])
        return self.conn_info

    def supported_verbs(self) -> List[str]:
        return list((self.conn_info or {}).get("supports", []))

    def send_level(self) -> int:
        """Highest rung to send now = ceiling (capability) capped by the server's
        published bearer (backpressure)."""
        if not self.ladder:
            return 7
        allowed = self.ladder.allowed_levels(self.have_camera, self.have_mic)
        ceil = self.ladder.ceiling(self.have_camera, self.have_mic)
        bearer = self.control.bearer()
        if bearer is None:
            return ceil
        return self.ladder.send_level(ceil, int(bearer), allowed)

    def send(self, fr: MediaFrame) -> Any:
        if self._send is None:
            raise RuntimeError("producer not connected (call create_and_connect first)")
        try:
            res = self._send(fr)
        except Exception as e:
            self.metrics.on_error()          # transmit failure on this producer
            log.error("producer_send_error session=%s node=%s seq=%d kind=%s err=%r",
                      self.control.session_id, self.node, fr.seq, fr.kind, e)
            raise
        self.metrics.on_sent()               # this producer transmitted a frame (its tx rate)
        trace_event("producer_send", session=self.control.session_id, node=self.node,
                    seq=fr.seq, kind=fr.kind, bytes=len(fr.payload))
        return res

    def publish_metrics(self, own: bool = True):
        self.control.publish_metrics(self.node, self.metrics.snapshot(), own=own)

    def control_intent(self, verb: str, **extra):
        self.control.write_intent(verb, **extra)


class MediaConsumer:
    """Producer or pure watcher on the receive side. Resolves conn-info and opens
    the rx connection, delivering decoded frames to a sink."""

    def __init__(self, control: TupleControl,
                 open_rx: Callable[[str, int, Callable], None],
                 cid: str = "consumer"):
        self.control = control
        self.open_rx = open_rx
        self.cid = cid
        self.conn_info: Optional[Dict[str, Any]] = None
        self.metrics = sotf_metrics.StreamMetrics(
            control.session_id, cid, sotf_metrics.ROLE_PLAYER)
        self._last_seq: Optional[int] = None

    def _meter(self, sink: Callable[[MediaFrame], None]) -> Callable[[MediaFrame], None]:
        """Wrap the rx sink so this player counts what it actually receives, and
        infers drops from sequence gaps (a missing seq = a frame the rx never got).
        Player-owned and deterministic - the server's sent_on vs this received is the
        slow-consumer signal."""
        def wrapped(fr: MediaFrame):
            if self._last_seq is not None and fr.seq > self._last_seq + 1:
                gap = fr.seq - self._last_seq - 1
                self.metrics.on_dropped(gap)
                trace_event("player_seqgap", session=self.control.session_id,
                            player=self.cid, missing=gap, at_seq=fr.seq)
            self._last_seq = fr.seq
            self.metrics.on_received()
            sink(fr)
        return wrapped

    def connect(self, sink: Callable[[MediaFrame], None]) -> Dict[str, Any]:
        self.conn_info = resolve_conn_info(self.control)
        self.open_rx(self.conn_info["addr"], self.conn_info["rx"], self._meter(sink))
        log.info("consumer_connect session=%s player=%s addr=%s rx=%d",
                 self.control.session_id, self.cid, self.conn_info["addr"],
                 self.conn_info["rx"])
        return self.conn_info

    def publish_metrics(self, own: bool = True):
        self.control.publish_metrics(self.cid, self.metrics.snapshot(), own=own)


def read_stream_dashboard(control: TupleControl, fresh_s: int = 30,
                          slack: float = sotf_metrics.DEFAULT_SLACK):
    """Read every node's metric tuple for this session and build the dashboard +
    deterministic slow-node verdict. Used by stream_monitor; works against MockSpace
    (sim) or a FrognetTuplesBackend (box) - same call."""
    values = control.all_metrics(fresh_s=fresh_s)
    return sotf_metrics.read_dashboard_from_values(control.session_id, values, slack)


# ============================================================================
# Adapter: drive the real frognet_tuples module through the same backend surface
# (put / get_one / get / get_all) the lifecycle expects. On a box, construct
# FrognetTuplesBackend(frognet_tuples) and hand it to TupleControl. In the sim, use
# MockSpace. SAME lifecycle code both places.
# ============================================================================
class FrognetTuplesBackend:
    def __init__(self, T, dbhost: str = "databasehost.frognet"):
        self.T = T
        self.dbhost = dbhost

    def put(self, service, var, scope, value, dbhost=None, addr="", own=True, timeout=4.0):
        return self.T.put(service, var, scope, value, dbhost=dbhost or self.dbhost,
                          own=own, timeout=timeout)

    def get_all(self, service, dbhost=None, fresh_s=0, timeout=4.0):
        return self.T.get_all(service, dbhost=dbhost or self.dbhost,
                              fresh_s=fresh_s, timeout=timeout)

    def get(self, service, var, dbhost=None, fresh_s=0, timeout=4.0):
        return self.T.get(service, var, dbhost=dbhost or self.dbhost,
                          fresh_s=fresh_s, timeout=timeout)

    def get_one(self, service, var, scope, fresh_s=0):
        want = f"SD:{var}.{scope}"
        for r in self.get_all(service, fresh_s=fresh_s):
            if r.get("name") == want:
                return r.get("value")
        return None
