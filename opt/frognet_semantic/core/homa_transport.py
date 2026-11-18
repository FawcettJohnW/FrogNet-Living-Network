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
    softhoma  Homa only, and it must be soft Homa: Homa's calls on shared memory (ribbit/examples/homa), loaded into
              this process. A node told "softhoma" without it stops.
    homasim   Homa only, on core/homa_sim.py (the Homa simulator). A harness's word.

[SOFT_HOMA_CARRIER_V1] Kernel Homa and soft Homa answer the same calls and are not the same wire, so they are two
carriers with two names: "homa" and "softhoma". One process has one of them: whichever answers IPPROTO_HOMA in it
(homa_name()). The Homa driver is the same for both. Each end says which it has in its capability tuple, so a kernel
node and a soft node find TCP in common and nothing else. A node told "homa" in whose process soft Homa is loaded
stops: the word for that is "softhoma".

Any other word stops the process with the word in the message.

[A_WORD_PER_RAM_CONNECTION_V1] John, 2026-11-17, asked whether "softhoma" only is a mode to support or a test's word:
"Both. It is a Setting per ram connection, which may be on different machines". So the first line is this node's
word, and each line after it is a RAM's role name and the word for this node's connection to that RAM:

    auto
    databasehost.frognet softhoma
    databasehost_control.frognet tcp

(FROGNET_SEM_TRANSPORT holds the same lines with commas between them.) A RAM is named by its role, never by an
address: where it is NOW is the one address this node's hosts file gives that name (the rule of
core/homa_socket.soft_memory_now, [ONE_INPUT_ONE_RULE_V1]), read each time a session is opened, so the word follows
the RAM when the role moves. A session is to a machine. Its word is the word of the RAM that is on that machine, and
this node's word when no named RAM is there. Two named RAMs on one machine with different words: that machine's
session cannot have both, so it is not opened and the reason is the error (nothing is preferred).

A daemon listens on every carrier any word of its setting names (carriers_listened()), and says so in its capability
tuple: a node that reaches a RAM on a carrier is a node a RAM can be reached at on that carrier when the role comes
to it, and every node has the same setting. So "softhoma" with "databasehost_control.frognet tcp" is: this node
opens soft Homa sessions to every machine but the ControlRam's, a TCP session to the ControlRam's, and its daemon
listens on both. A RAM's word is auto, tcp, homa or softhoma (homasim is a harness's word for a whole process).

[NO_FALLBACK_V1] Choosing a carrier at the start of a session from what both ends have is not a fallback. A session
whose carrier fails ends with the reason; nothing carries on underneath it. A node told "homa" whose Homa socket will
not open stops with the kernel's reason (core/homa_socket.HomaUnavailable).
"""
from __future__ import annotations

import os
import threading
from typing import Dict, Optional, Tuple

TRANSPORT_FILE = os.environ.get("FROGNET_SEM_TRANSPORT_FILE", "/etc/frognet/semantic_transport")
VALID = ("auto", "tcp", "homa", "softhoma", "homasim")
WALK = ("homa", "softhoma", "tcp")    # the walk's order: equal or unmeasured carriers are taken in this order
BUILD = "homa-carrier-v4-20261117u"
_chosen = None
_ram_words: Dict[str, str] = {}              # [A_WORD_PER_RAM_CONNECTION_V1] RAM's role name -> the word for this node's connection to it
_here: Dict[str, Tuple[str, ...]] = {}       # word -> what this end carries the wire on under that word
_lock = threading.Lock()
_rtt: Dict[Tuple[str, str], float] = {}      # (peer's .1, carrier) -> the least round trip measured to it, seconds
_picked: Dict[str, str] = {}                  # peer's .1 -> the carrier last chosen for it (for the log, said on change)


def semantic_transport() -> str:
    global _chosen
    if _chosen is not None:
        return _chosen
    text, src = os.environ.get("FROGNET_SEM_TRANSPORT", "").strip().lower().replace(",", "\n"), "FROGNET_SEM_TRANSPORT"
    if not text:
        try:
            with open(TRANSPORT_FILE) as f:
                text, src = f.read().strip().lower(), TRANSPORT_FILE
        except FileNotFoundError:
            text, src = "auto", "no %s (the default)" % TRANSPORT_FILE
    lines = [l.split() for l in text.splitlines() if l.strip()]
    word = lines[0][0] if lines and len(lines[0]) == 1 else " ".join(lines[0]) if lines else ""
    if word not in VALID:
        raise SystemExit("[SEM-TRANSPORT] %s says %r; the semantic wire's transport is one of %s"
                         % (src, word, ", ".join(VALID)))
    rams: Dict[str, str] = {}
    for l in lines[1:]:                      # [A_WORD_PER_RAM_CONNECTION_V1]
        if len(l) != 2 or l[1] not in VALID or l[1] == "homasim" or l[0] in rams:
            raise SystemExit("[SEM-TRANSPORT] %s says %r after its first line; each line there is a RAM's role name and "
                             "one of %s, and a RAM is named once" % (src, " ".join(l), ", ".join(VALID)))
        rams[l[0]] = l[1]
    _ram_words.clear()
    _ram_words.update(rams)
    _chosen = word
    print("[SEM-TRANSPORT] [HOMA_TRANSPORT_V1] %s semantic wire transport=%s (from %s)%s"
          % (BUILD, word, src, "".join("; [A_WORD_PER_RAM_CONNECTION_V1] the connection to %s: %s" % kv
                                       for kv in sorted(rams.items()))), flush=True)
    for w in sorted(set(rams.values())):
        carriers_here(w)                     # a RAM's word this process cannot keep stops it here, at the start
    return _chosen


def _rams_at(host: str) -> Tuple[str, ...]:
    """The named RAMs (roles with a word of their own) that this node's hosts file puts at `host` NOW. A test replaces
    this function."""
    if not _ram_words:
        return ()
    import sys
    from core import frognet_tuples
    d = os.path.dirname(os.path.abspath(frognet_tuples.__file__))       # [CORE_ON_THE_PATH_V1] as homa_liveness does
    if d not in sys.path:
        sys.path.append(d)
    import frognet_held
    at: Dict[str, set] = {}
    with open(frognet_held.HOSTS) as f:
        for line in f:
            p = line.split("#", 1)[0].split()
            for name in p[1:]:
                if name.lower() in _ram_words:
                    at.setdefault(name.lower(), set()).add(p[0])
    return tuple(sorted(n for n, a in at.items() if a == {host}))        # a role with two addresses is at neither


def word_for(host: str) -> str:
    """[A_WORD_PER_RAM_CONNECTION_V1] The word for a session to `host`: the word of the named RAM that is there now,
    else this node's word."""
    mode = semantic_transport()
    rams = _rams_at(host)
    words = sorted(set(_ram_words[r] for r in rams))
    if len(words) > 1:
        raise ConnectionError("the RAMs %s are both at %s and this node's connections to them are set to %s: one "
                              "session to that machine cannot be both" % (", ".join(rams), host, " and ".join(words)))
    return words[0] if words else mode


def on_homa_driver() -> bool:
    """True when this node was told Homa only (over the module or over the simulator)."""
    return semantic_transport() in ("homa", "softhoma", "homasim")


def _soft_homa_loaded() -> Optional[str]:
    """[WE_OWN_THE_WIRE_V1] Whether the Homa this node's driver runs on is soft Homa (Homa's frames on TCP connections
    the driver holds open, core/homa_frames.py), and if so its build: only when a word of the node's setting says
    softhoma. It needs nothing but TCP, so it could always be had; it is not taken unless asked for. A node left on
    auto with no Homa in its kernel is on TCP exactly as it was (John, 17 Nov: "There should be no risk to existing
    systems at all")."""
    words = {semantic_transport()} | set(_ram_words.values())
    if "softhoma" in words:
        from core import homa_frames
        return homa_frames.BUILD
    return None



def homa_name() -> str:
    """The name of the Homa this process has: "softhoma" where it was told of a soft Homa library, else "homa"."""
    return "softhoma" if _soft_homa_loaded() else "homa"


def _kernel_homa_opens() -> Tuple[bool, str]:
    """Whether an IPPROTO_HOMA socket opens here, and what the kernel said if not. A test replaces this function."""
    from core import homa_socket
    try:
        homa_socket.HomaSocket().close()
        return True, "an IPPROTO_HOMA socket opened"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def carriers_here(mode: Optional[str] = None) -> Tuple[str, ...]:
    """What this end can carry the wire on under `mode` (this node's word when not given), decided once at start and
    said in the log."""
    if mode is None:
        mode = semantic_transport()
    with _lock:
        if mode not in _here:
            if mode == "tcp":
                _here[mode] = ("tcp",)
            elif mode == "homasim":
                _here[mode] = ("homa",)
            elif mode in ("homa", "softhoma"):
                soft = _soft_homa_loaded()
                if mode == "homa" and soft:
                    raise SystemExit("[SEM-TRANSPORT] this node was told homa, and it was also told of a soft Homa library "
                                     "(%s): the Homa its driver would open is soft Homa. The word for that is softhoma." % soft)
                _here[mode] = (mode,)
            else:
                soft = _soft_homa_loaded()
                # [SOFT_HOMA_IS_HAD_WHEN_ITS_LIBRARY_IS_V1] found between machines, 17 Nov: "auto" decided what this end
                # has by opening a socket at start. A node starts before the merge has named the SystemRam, so soft
                # Homa's memory cannot be named yet, the socket did not open, and "auto" was tcp for the life of the
                # process on every node. Soft Homa is had when this node was told of its library; where its memory is
                # comes and goes with the role, and the listener follows it. Kernel Homa is still had when its socket opens.
                ok, why = (True, "a word of this node's setting says softhoma") if soft else _kernel_homa_opens()
                _here[mode] = (homa_name(), "tcp") if ok else ("tcp",)
                print("[SEM-TRANSPORT] [CARRIER_PER_HOP_V1] carriers here: %s (%s: %s)"
                      % (", ".join(_here[mode]), "soft Homa %s" % soft if soft else "kernel Homa", why), flush=True)
        return _here[mode]


def carriers_listened() -> Tuple[str, ...]:
    """[A_WORD_PER_RAM_CONNECTION_V1] What this node's daemon listens on: every carrier any word of its setting names
    (the node's word and each RAM's), in the walk's order."""
    words = [semantic_transport()] + sorted(set(_ram_words.values()))
    have = set(c for w in words for c in carriers_here(w))
    return tuple(c for c in WALK if c in have)


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


def _peer_in_soft_memory(host: str) -> bool:
    """[A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1] Whether `host`'s daemon has its Homa listener in soft Homa's memory where
    that memory is NOW. After the role moves, each node arrives in the new memory when its own hosts file changes, so
    a peer can be behind this node. The library answers from its standing read on the capability cells; it asks the
    memory only about a peer it has not heard of. A test replaces this function."""
    from core import homa_socket
    return homa_socket.soft_peer_there((host, int(os.environ.get("FROGNET_DAEMON_PORT", "9009"))), 0)


def carrier_for(host: str) -> str:
    """The driver a NEW session to `host` opens on: "homa" (the Homa driver, on kernel Homa or soft Homa, whichever
    this process has) or "tcp". See the module docstring for the rule."""
    mode = word_for(host)                   # [A_WORD_PER_RAM_CONNECTION_V1] the RAM's word if a named RAM is there now
    if mode == "tcp":
        return "tcp"
    if mode in ("homa", "softhoma", "homasim"):
        carriers_here(mode)                 # a word this process cannot keep stops it here
        return "homa"                       # (told softhoma only: a peer not in the memory is the session's to say, transport_homa._MsgWorker)
    mine = carriers_here(mode)
    if mine == ("tcp",):
        return "tcp"
    from core.homa_liveness import node_of
    node = node_of(host)
    theirs = _peer_carriers(node)
    common = [c for c in WALK if c in mine and c in (theirs if theirs is not None else ("tcp",))]
    if "softhoma" in common and not _peer_in_soft_memory(host):
        # [A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1] it has soft Homa but is not in the memory where that is now: for this
        # session the two ends do not have soft Homa in common
        common.remove("softhoma")
        with _lock:
            if _picked.get(node) not in (None, "tcp"):
                print("[SEM-TRANSPORT] [CARRIER_PER_HOP_V1] %s is not in soft Homa's memory where it is now: sessions "
                      "to it do not open on softhoma until it is" % node, flush=True)
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
    return "tcp" if pick == "tcp" else "homa"


def worker_idle(w) -> bool:
    """Whether a session has nothing outstanding (either driver's worker)."""
    out = getattr(w, "_out", None)
    if out is not None:
        return not out
    return not w._pending and w.q.empty()


def daemon_server(host: str, port: int, resolver, tcp_server_class):
    """The daemon's listener: one per carrier this end has. Same run() as DaemonServer."""
    mine = carriers_listened()
    if mine == ("tcp",):
        return tcp_server_class(host=host, port=port, resolver=resolver)
    from daemon.engine.server_homa import MsgDaemonServer
    if len(mine) == 1:
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
    if _soft_homa_loaded():                        # [WE_OWN_THE_WIRE_V1] Homa's frames on connections the driver holds open
        from core import homa_frames, frognet_tuples
        try:
            src = frognet_tuples.my_ip()
        except Exception as e:
            src = ""
            print("[HOMA] %s: this node's own address could not be said (%s: %s); soft Homa's connections leave by "
                  "whatever address the kernel picks" % (who, type(e).__name__, e), flush=True)
        s = homa_frames.HomaFramesOnTcp(bind_ip=bind_ip, server_port=server_port, src=src)
        print(homa_frames.startup_line(who, s), flush=True)
        return s
    from core import homa_socket
    s = homa_socket.HomaSocket(bind_ip=bind_ip, server_port=server_port)
    print(homa_socket.startup_line(who), flush=True)
    return s
