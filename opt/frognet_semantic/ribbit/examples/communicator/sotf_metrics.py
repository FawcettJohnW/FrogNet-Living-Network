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
sotf_metrics.py - SotF media-stream metrics over the tuple/status plane.

The media frames ride the binary FNWP-1 connections (ingestion queue tx, server
emit rx); metrics ride the SAME control/status plane everything else uses - the
transient tuple space, keyed by session. The ffmpeg server is the authoritative
writer of the AGGREGATE; each endpoint writes only the counts it alone observes.
Every producer/consumer reads all of them async (one tuple write, N readers), so
a dashboard can localize a slow node DETERMINISTICALLY by comparing each node's
FrogNet frame rate against the server's authoritative inject / sent-on rate.

Counters (exactly the set asked for):
  SERVER  (aggregate, server-written):  injected, sent_on, fps
  SENDER  (per producer, self-written):  sent, dropped, error, fps
  PLAYER  (per consumer, self-written):  received, dropped, fps

Slow-node rule (deterministic, no heuristics):
  stream_fps          = server inject fps (the rate frames actually enter)
  a SENDER is slow  if its sent fps < stream_fps * (1 - SLACK)  OR drop/err rising
  a PLAYER is slow  if its received fps < server sent_on fps * (1 - SLACK)
                       OR its dropped rising
SLACK absorbs sampling jitter; the comparison is against measured counts in the
shared space, so two observers reading the same tuples reach the same verdict.

Tuple shape (one row per node per session):
  service = "mediahost"
  var     = "stream_metrics"
  scope   = session_scope(session_id) + ":" + node   (one row per node)
  value   = {role, node, fps, <role counters>, ts}
The default backend is frognet_tuples; tests inject an in-memory backend with the
same put / get_all surface, so this runs without a live api.php.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

SERVICE = "mediahost"
VAR = "stream_metrics"

ROLE_SERVER = "server"
ROLE_SENDER = "sender"
ROLE_PLAYER = "player"

# fraction below the reference rate a node may drift before it's "slow"
DEFAULT_SLACK = 0.15
# rolling window over which FrogNet fps is measured
DEFAULT_FPS_WINDOW_S = 3.0


# -- rolling frame-rate meter -------------------------------------------------
class FpsMeter:
    """FrogNet frames/sec over a sliding wall-clock window. mark() once per frame
    that crosses (injected at the server, sent by a producer, received by a
    player). fps() is deterministic given the same marks and clock."""

    def __init__(self, window_s: float = DEFAULT_FPS_WINDOW_S,
                 clock: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self._clock = clock
        self._marks: Deque[float] = deque()

    def mark(self, n: int = 1) -> None:
        t = self._clock()
        for _ in range(max(1, n)):
            self._marks.append(t)
        self._evict(t)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._marks and self._marks[0] < cutoff:
            self._marks.popleft()

    def fps(self) -> float:
        now = self._clock()
        self._evict(now)
        if len(self._marks) < 2:
            return float(len(self._marks)) / self.window_s if self._marks else 0.0
        span = max(self._clock() - self._marks[0], 1e-6)
        return len(self._marks) / span


# -- per-node metrics ---------------------------------------------------------
@dataclass
class StreamMetrics:
    """Counters for ONE node in a session. role decides which fields are live.
    The node bumps counters as frames cross; publish() snapshots them to a tuple."""
    session_id: str
    node: str
    role: str
    window_s: float = DEFAULT_FPS_WINDOW_S
    clock: Callable[[], float] = time.monotonic

    # server aggregate
    injected: int = 0          # frames accepted into the ingestion queue (all producers)
    sent_on: int = 0           # frames emitted from the server toward players (aggregate)
    # sender (producer)
    sent: int = 0              # frames this producer transmitted
    dropped: int = 0           # frames this node dropped (producer: at inject; player: toward it)
    error: int = 0             # transmit errors on this producer
    # player (consumer)
    received: int = 0          # frames this player received
    # the improvement metric (SENDER): bytes actually put on the wire (FNWP-1 with
    # reference-diff) vs the raw frame bytes a naive full-frame stream would send.
    wire_bytes: int = 0        # sum of FNWP-1 frame sizes actually transmitted
    raw_bytes: int = 0         # sum of raw payload sizes (what full-frame send costs)
    # CONTROL-plane convergence (this is where SAME/DIFF is real - stable JSON, not AV):
    ctl_full: int = 0
    ctl_diff: int = 0
    ctl_same: int = 0
    ctl_wire: int = 0          # bytes the control writes actually put on the wire
    ctl_raw: int = 0           # bytes a naive re-send of the full bag would cost

    _fps: FpsMeter = field(init=False, repr=False)

    def __post_init__(self):
        self._fps = FpsMeter(self.window_s, self.clock)

    # --- crossings (mark fps on the frame this node is responsible for) ---
    def on_injected(self, n: int = 1):   # SERVER: a frame entered the queue
        self.injected += n; self._fps.mark(n)

    def on_sent_on(self, n: int = 1):    # SERVER: a frame left toward players
        self.sent_on += n

    def on_sent(self, n: int = 1):       # SENDER: transmitted a frame
        self.sent += n; self._fps.mark(n)

    def on_dropped(self, n: int = 1):    # SENDER or PLAYER: shed a droppable frame
        self.dropped += n

    def on_error(self, n: int = 1):      # SENDER: transmit error
        self.error += n

    def on_received(self, n: int = 1):   # PLAYER: got a frame
        self.received += n; self._fps.mark(n)

    def on_wire(self, raw: int, wire: int):  # SENDER: per-frame throughput accounting
        self.raw_bytes += int(raw); self.wire_bytes += int(wire)

    def on_ctl(self, kind: str, wire: int, raw: int):  # CONTROL: convergence accounting
        if kind == "full": self.ctl_full += 1
        elif kind == "diff": self.ctl_diff += 1
        else: self.ctl_same += 1
        self.ctl_wire += int(wire); self.ctl_raw += int(raw)

    def ctl_compression(self):
        if self.ctl_raw <= 0: return {}
        r = self.ctl_wire / self.ctl_raw
        return {"ctl_full": self.ctl_full, "ctl_diff": self.ctl_diff, "ctl_same": self.ctl_same,
                "ctl_wire": self.ctl_wire, "ctl_raw": self.ctl_raw,
                "ctl_saved_pct": round((1.0 - r) * 100.0, 2)}

    def compression(self) -> Dict[str, Any]:
        """The improvement vs a naive full-frame stream: how much smaller the FNWP-1
        wire is than the raw bytes. ratio = wire/raw (lower is better); saved_pct =
        1-ratio. Empty until on_wire has data."""
        if self.raw_bytes <= 0:
            return {}
        ratio = self.wire_bytes / self.raw_bytes
        return {"wire_bytes": self.wire_bytes, "raw_bytes": self.raw_bytes,
                "wire_ratio": round(ratio, 4), "saved_pct": round((1.0 - ratio) * 100.0, 2)}

    def fps(self) -> float:
        return round(self._fps.fps(), 2)

    # --- snapshot the live counters for the wire ---
    def snapshot(self) -> Dict[str, Any]:
        v: Dict[str, Any] = {"role": self.role, "node": self.node, "fps": self.fps()}
        if self.role == ROLE_SERVER:
            v.update(injected=self.injected, sent_on=self.sent_on)
        elif self.role == ROLE_SENDER:
            v.update(sent=self.sent, dropped=self.dropped, error=self.error)
            v.update(self.compression())     # wire_bytes/raw_bytes/wire_ratio/saved_pct
        elif self.role == ROLE_PLAYER:
            v.update(received=self.received, dropped=self.dropped)
        v.update(self.ctl_compression())   # control-plane SAME/DIFF gain (any role)
        return v


# -- tuple-space backend (injectable; default is frognet_tuples) --------------
class _FrognetTuplesBackend:
    """Thin adapter over frognet_tuples so the metrics layer stays decoupled and
    testable. Lazy import: importing this module never requires a live api.php."""

    def __init__(self, dbhost: Optional[str] = None):
        import frognet_tuples as T  # type: ignore
        self._T = T
        self._dbhost = dbhost or T.DEFAULT_DBHOST

    def put(self, scope: str, value: Dict[str, Any], own: bool) -> bool:
        return self._T.put(SERVICE, VAR, scope, value, dbhost=self._dbhost, own=own)

    def get_all(self, fresh_s: int) -> List[Dict[str, Any]]:
        rows = self._T.get(SERVICE, VAR, dbhost=self._dbhost, fresh_s=fresh_s)
        return [r.get("value", {}) for r in rows]


def _scope(session_id: str, node: str) -> str:
    # one tuple row per node within the session; mirrors session_scope + node
    return f"sotf:{session_id}:{node}"


def publish(metrics: StreamMetrics, backend, own: bool = True) -> bool:
    """Snapshot this node's counters into its per-node session tuple. The server
    publishes with own=False (the aggregate must outlive a transient writer and
    age by ts); endpoints publish their own with own=True so cleanup reaps them."""
    return backend.put(_scope(metrics.session_id, metrics.node), metrics.snapshot(), own)


# -- dashboard: read every node's tuple, localize slow nodes deterministically -
@dataclass
class NodeView:
    node: str
    role: str
    fps: float
    counters: Dict[str, Any]
    slow: bool = False
    reason: str = ""


@dataclass
class Dashboard:
    session_id: str
    server: Optional[NodeView]
    senders: List[NodeView]
    players: List[NodeView]

    def slow_nodes(self) -> List[NodeView]:
        return [n for n in (self.senders + self.players) if n.slow]

    def render(self) -> str:
        lines = [f"session {self.session_id}"]
        if self.server:
            c = self.server.counters
            lines.append("  SERVER   fps=%-6.2f injected=%-7d sent_on=%-7d"
                         % (self.server.fps, c.get("injected", 0), c.get("sent_on", 0)))
        for s in self.senders:
            c = s.counters
            comp = ""
            if c.get("raw_bytes", 0):
                comp = "  wire=%dB raw=%dB saved=%.1f%%" % (
                    c.get("wire_bytes", 0), c.get("raw_bytes", 0), c.get("saved_pct", 0.0))
            lines.append("  SENDER   %-14s fps=%-6.2f sent=%-7d dropped=%-6d error=%-4d%s%s"
                         % (s.node, s.fps, c.get("sent", 0), c.get("dropped", 0),
                            c.get("error", 0), comp,
                            ("   <-- SLOW: " + s.reason) if s.slow else ""))
        for p in self.players:
            c = p.counters
            lines.append("  PLAYER   %-14s fps=%-6.2f received=%-7d dropped=%-6d%s"
                         % (p.node, p.fps, c.get("received", 0), c.get("dropped", 0),
                            ("   <-- SLOW: " + p.reason) if p.slow else ""))
        return "\n".join(lines)


def read_dashboard(session_id: str, backend, fresh_s: int = 30,
                   slack: float = DEFAULT_SLACK) -> Dashboard:
    return read_dashboard_from_values(session_id, backend.get_all(fresh_s), slack)


def read_dashboard_from_values(session_id: str, rows: List[Dict[str, Any]],
                               slack: float = DEFAULT_SLACK) -> Dashboard:
    """Build the dashboard + slow-node verdict from already-fetched metric snapshots
    (each is a StreamMetrics.snapshot() dict). Pure: same rows + slack -> same verdict,
    which is why two observers reading the same tuples agree."""
    server = None
    senders: List[NodeView] = []
    players: List[NodeView] = []
    for v in rows:
        if not isinstance(v, dict):
            continue
        role = v.get("role")
        nv = NodeView(node=v.get("node", "?"), role=role,
                      fps=float(v.get("fps", 0.0)), counters=v)
        if role == ROLE_SERVER:
            server = nv
        elif role == ROLE_SENDER:
            senders.append(nv)
        elif role == ROLE_PLAYER:
            players.append(nv)

    # Deterministic slow-node verdict, measured against the server's authoritative
    # rates. No server -> no reference, leave everyone unflagged (can't be sure).
    if server is not None:
        inject_fps = server.fps                                   # rate frames enter
        senton_fps = inject_fps                                    # server relays ~1:1
        for s in senders:
            if s.fps < inject_fps * (1.0 - slack):
                s.slow = True
                s.reason = "sent fps %.1f < inject %.1f" % (s.fps, inject_fps)
            elif int(s.counters.get("error", 0)) > 0:
                s.slow = True
                s.reason = "%d transmit errors" % int(s.counters.get("error", 0))
        for p in players:
            if p.fps < senton_fps * (1.0 - slack):
                p.slow = True
                p.reason = "recv fps %.1f < sent_on %.1f" % (p.fps, senton_fps)
            elif int(p.counters.get("dropped", 0)) > 0 and senton_fps > 0:
                # receiving at rate but still dropping -> rx can't keep up
                p.slow = True
                p.reason = "%d drops at rate" % int(p.counters.get("dropped", 0))
    return Dashboard(session_id, server, senders, players)


def frognet_tuples_backend(dbhost: Optional[str] = None):
    """Default production backend. Lazy - only touches frognet_tuples when called."""
    return _FrognetTuplesBackend(dbhost)
