#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/homa_transport.py -- [HOMA_TRANSPORT_V1] [CARRIER_PER_HOP_V1] which carrier each hop of the FNW1 semantic wire
runs on.

John, 2026-11-16: the kernel module "can be detected and used if present"; "Highest static ip is control ram, where
the capability tuples live. Reach it over whatever transport is loaded"; the rule is "deterministic and simple".
Design: ribbit/examples/homa/DESIGN.md, section 5.

One word says how a node chooses. It is read once at start from /etc/frognet/semantic_transport
(FROGNET_SEM_TRANSPORT_FILE names another) or from FROGNET_SEM_TRANSPORT in the process's own environment:

    auto      (no file and no variable) the carrier is a property of each hop:
                - this end detects what it can open: kernel Homa if an IPPROTO_HOMA socket opens (that IS the
                  capability; lsmod would miss a Homa built into the kernel), and TCP, which every node has;
                - a daemon listens on every carrier it has, and says which in its capability tuple in the ControlRam
                  (core/homa_liveness.py). A daemon with TCP only writes nothing: it is as it has always been;
                - a proxy opens each session on a carrier both ends have. A peer whose tuple has not been read is
                  known to have TCP and nothing more. Among the carriers both have, the LOWEST MEASURED ROUND TRIP
                  wins and nothing is preferred by kind (docs/spec/spec-05-routing.md 4.1, [PURE_RTT_V1]); a carrier
                  not yet measured to that peer is taken first, in the walk's order, so that it is measured by being
                  used; equal measurements keep the walk's order. TCP's measurement is the unloaded round trip its
                  own connect ladder records (proxy_metrics link_baseline); Homa's is the least round trip its
                  sessions have seen.
                - a session is never moved. When the choice for a peer is no longer the carrier of its session, the
                  session is retired the first time a request finds it with nothing outstanding, and the next one
                  opens on the choice. Nothing stands by as a spare.
              On a node where no Homa socket opens, "auto" is "tcp": nothing new runs.
    tcp       TCP only, and nothing here runs.
    homa      Homa only, on the kernel module. A harness's word, and a node's when every peer is the same.
    homasim   Homa only, on core/homa_sim.py (the Homa simulator). A harness's word.

Any other word stops the process with the word in the message.

[NO_FALLBACK_V1] Choosing a carrier at the start of a session from what both ends have is not a fallback. A session
whose carrier fails ends with the reason; nothing carries on underneath it. A node told "homa" whose Homa socket will
not open stops with the kernel's reason (core/homa_socket.HomaUnavailable).
"""
from __future__ import annotations

import os
import threading
from typing import Dict, Optional, Tuple

TRANSPORT_FILE = os.environ.get("FROGNET_SEM_TRANSPORT_FILE", "/etc/frognet/semantic_transport")
VALID = ("auto", "tcp", "homa", "homasim")
WALK = ("homa", "tcp")            # the walk's order: equal or unmeasured carriers are taken in this order
BUILD = "homa-carrier-v1-20261116k"
_chosen = None
_here: Optional[Tuple[str, ...]] = None
_lock = threading.Lock()
_rtt: Dict[Tuple[str, str], float] = {}      # (peer's .1, carrier) -> the least round trip measured to it, seconds
_picked: Dict[str, str] = {}                  # peer's .1 -> the carrier last chosen for it (for the log, said on change)


def semantic_transport() -> str:
    global _chosen
    if _chosen is not None:
        return _chosen
    word, src = os.environ.get("FROGNET_SEM_TRANSPORT", "").strip().lower(), "FROGNET_SEM_TRANSPORT"
    if not word:
        try:
            with open(TRANSPORT_FILE) as f:
                word, src = f.read().strip().lower(), TRANSPORT_FILE
        except FileNotFoundError:
            word, src = "auto", "no %s (the default)" % TRANSPORT_FILE
    if word not in VALID:
        raise SystemExit("[SEM-TRANSPORT] %s says %r; the semantic wire's transport is one of %s"
                         % (src, word, ", ".join(VALID)))
    _chosen = word
    print("[SEM-TRANSPORT] [HOMA_TRANSPORT_V1] %s semantic wire transport=%s (from %s)" % (BUILD, word, src), flush=True)
    return _chosen


def on_homa_driver() -> bool:
    """True when this node was told Homa only (over the module or over the simulator)."""
    return semantic_transport() in ("homa", "homasim")


def _kernel_homa_opens() -> Tuple[bool, str]:
    """Whether an IPPROTO_HOMA socket opens here, and what the kernel said if not. A test replaces this function."""
    from core import homa_socket
    try:
        homa_socket.HomaSocket().close()
        return True, "an IPPROTO_HOMA socket opened"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def carriers_here() -> Tuple[str, ...]:
    """What this end can carry the wire on, decided once at start and said in the log."""
    global _here
    with _lock:
        if _here is None:
            mode = semantic_transport()
            if mode == "tcp":
                _here = ("tcp",)
            elif mode in ("homa", "homasim"):
                _here = ("homa",)
            else:
                ok, why = _kernel_homa_opens()
                _here = ("homa", "tcp") if ok else ("tcp",)
                print("[SEM-TRANSPORT] [CARRIER_PER_HOP_V1] carriers here: %s (kernel Homa: %s)"
                      % (", ".join(_here), why), flush=True)
        return _here


def note_rtt(node: str, carrier: str, seconds: float) -> None:
    """A round trip measured to `node` on `carrier`. The least ever seen is kept: it only goes down, so the choice
    settles."""
    if seconds > 0:
        with _lock:
            k = (node, carrier)
            if k not in _rtt or seconds < _rtt[k]:
                _rtt[k] = seconds


def _tcp_rtt(host: str) -> Optional[float]:
    """TCP's unloaded round trip to `host`, as its own connect ladder measured it ([LINK_POTENTIAL_PING_V1]), or
    None if no TCP session to it has been opened in this process. Read, never written."""
    from proxy import proxy_metrics as M
    with M._lock:
        b = (M._CACHE_STATS.get(host) or {}).get("link_baseline")
        us = float(b.get("intercept_us") or 0.0) if b else 0.0
    return us / 1e6 if us > 0 else None


def _peer_carriers(node: str) -> Optional[Tuple[str, ...]]:
    """What the daemon at `node` says it listens on (its capability tuple in the ControlRam), or None if that tuple
    has not been read. A test replaces this function."""
    from proxy import transport_homa as TH
    return TH._get_mux().liveness.carriers_of(node)


def carrier_for(host: str) -> str:
    """The carrier a NEW session to `host` opens on. See the module docstring for the rule."""
    mode = semantic_transport()
    if mode == "tcp":
        return "tcp"
    if mode in ("homa", "homasim"):
        return "homa"
    mine = carriers_here()
    if "homa" not in mine:
        return "tcp"
    from core.homa_liveness import node_of
    node = node_of(host)
    theirs = _peer_carriers(node)
    common = [c for c in WALK if c in mine and c in (theirs if theirs is not None else ("tcp",))]
    if not common:
        raise ConnectionError("peer %s carries the semantic wire on %s and this node on %s: no carrier in common"
                              % (node, ", ".join(theirs or ()), ", ".join(mine)))
    t = _tcp_rtt(host)
    if t is not None:
        note_rtt(node, "tcp", t)
    with _lock:
        unmeasured = [c for c in common if (node, c) not in _rtt]
        pick = unmeasured[0] if unmeasured else min(common, key=lambda c: (_rtt[(node, c)], WALK.index(c)))
        if _picked.get(node) != pick:
            _picked[node] = pick
            print("[SEM-TRANSPORT] [CARRIER_PER_HOP_V1] sessions to %s open on %s (it has: %s; this node: %s; measured: %s)"
                  % (node, pick, "its tuple has not been read, so tcp" if theirs is None else ", ".join(theirs),
                     ", ".join(mine),
                     ", ".join("%s %.3f ms" % (c, _rtt[(node, c)] * 1000) if (node, c) in _rtt else "%s not yet" % c
                               for c in common)), flush=True)
    return pick


def worker_idle(w) -> bool:
    """Whether a session has nothing outstanding (either driver's worker)."""
    out = getattr(w, "_out", None)
    if out is not None:
        return not out
    return not w._pending and w.q.empty()


def daemon_server(host: str, port: int, resolver, tcp_server_class):
    """The daemon's listener: one per carrier this end has. Same run() as DaemonServer."""
    mine = carriers_here()
    if mine == ("tcp",):
        return tcp_server_class(host=host, port=port, resolver=resolver)
    from daemon.engine.server_homa import MsgDaemonServer
    if mine == ("homa",):
        return MsgDaemonServer(host=host, port=port, resolver=resolver)

    class _EveryCarrier:
        def __init__(self):
            self.tcp = tcp_server_class(host=host, port=port, resolver=resolver)
            self.homa = MsgDaemonServer(host=host, port=port, resolver=resolver)

        def _homa(self):
            try:
                self.homa.run()
            finally:
                print("[SEM-TRANSPORT] [CARRIER_PER_HOP_V1] the daemon's Homa listener has ENDED; sessions on Homa to "
                      "this node end with it. Its TCP listener is a separate carrier and is not touched.", flush=True)

        def run(self):
            threading.Thread(target=self._homa, daemon=True, name="daemon-homa-listener").start()
            self.tcp.run()
    return _EveryCarrier()


def open_homa_socket(who: str, bind_ip: str = "0.0.0.0", server_port=None):
    """The socket the Homa driver runs on, as configured: the kernel module's, or the simulator's. Logs what it is."""
    if semantic_transport() == "homasim":
        from core import homa_sim
        s = homa_sim.HomaSimSocket(bind_ip=bind_ip, server_port=server_port)
        print(homa_sim.startup_line(who, s), flush=True)
        return s
    from core import homa_socket
    s = homa_socket.HomaSocket(bind_ip=bind_ip, server_port=server_port)
    print(homa_socket.startup_line(who), flush=True)
    return s
