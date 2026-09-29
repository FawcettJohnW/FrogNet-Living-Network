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
comms_control.py -- Communicator CONTROL plane (BLDC/UnREST).

Doctrine: exchange MEMORY, not messages. Every control concern -- who is online, who is
on a call, where the call's media socket lives, and chat -- converges FULL -> DIFF -> SAME
through the UnREST substrate (substrate.Channel/Codex) and is persisted as transient tuples
(frognet_tuples). The DATA plane (raw audio/video bytes) is NOT here; it stays in fnav on
fire-and-forget FNWP-1. This module's only job is to tell a peer WHO is reachable, WHO is on
a call, and WHERE to point fnav -- then get out of the way.

Layering (so the real codec drops in unchanged):
  substrate.Codex/Channel  -- FULL/DIFF/SAME convergence + freshness (stand-in now; the real
                             BLDC-1 byte codec replaces substrate at rung 1, no change here)
  frognet_tuples (T)       -- the transient store the converged state lives in

Freshness, per UnREST:
  presence  LATEST_ONLY        -- continuously refreshed; stale == offline (no DELETE)
  call      LATEST_ONLY        -- current membership/ports; newest wins
  chat      LOSSLESS_EVENTUAL  -- every line must land, may arrive late
"""
from __future__ import annotations

import json
import logging
import sys
import socket
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import frognet_tuples as T

# [DIAGNOSTICS_GO_WHERE_THEY_CAN_BE_READ_V1] LOG, not print.
#
# The GUI detaches from the console it was launched from, so every print() in
# this module goes nowhere a person will look. Two turns were spent reading the
# ABSENCE of printed diagnostics as the code not reaching them; the log file had
# the answer all along and none of these lines were in it.
LOG = logging.getLogger("comms_control")

# [SAY_WHAT_WROTE_THIS_ROW_V1] one line per var, not per write
_SAID_WRITER = set()
from substrate import Freshness, Codex, Channel

SERVICE = "communicator"

# [DIAG-PRESENCE-V1] Off by default: this fires on every heartbeat for every
# concern and drowns the console. FROGNET_COMMS_DIAG=1 turns it on.
_DIAG = bool(__import__("os").environ.get("FROGNET_COMMS_DIAG"))
DBHOST = "databasehost.frognet"                 # application state -> ELECTED DATA HOST.
# NOT databasehost_control.frognet: _control is fabric discovery/coordination only
# (deterministic highest-.1). Presence/call/chat are shared application memory and must
# converge through the elected, capacity-clocked data host to be visible pond-wide -- a call
# tuple on one node's _control is not seen by a peer elsewhere, which is why join failed.
def _hosts_only_resolve(name):
    """[HOSTS_ONLY_V1] /etc/hosts, or nothing. No DNS fallback.

    core.hosts_only is the canonical implementation; it is not importable from a
    Windows client bundle, where /etc/hosts still exists and is still the only source
    this reads. Same rule either way: parse the file, refuse to fall through.
    """
    import os
    path = os.environ.get("FROGNET_HOSTS_PATH")
    # [CLIENT_USES_DNS_V1] A NODE resolves from /etc/hosts and nothing else. A
    # CLIENT resolves through DNS, and that is not a weakening of the rule -- it
    # is the rule applied where it means something.
    #
    # Hosts-only exists because on a node the local resolver LIES: resolv.conf
    # points at 127.0.0.1 first, and /etc/hosts said 10.250.250.1 while
    # gethostbyname in the same process returned 10.130.130.1. That address gets
    # pinned into the call tuple, so a wrong answer sends every participant to a
    # relay the starter never chose.
    #
    # A client has none of that. It has no FrogNet /etc/hosts entries at all --
    # mergeHostsAndResolv.bash never ran there -- and its DNS points at the LAN's
    # .1, which IS the nameserver for the .frognet zone. Asking it is asking the
    # same authority the node's hosts file was generated from. Refusing to ask
    # produced "no media host elected for this LAN" on a box where
    # `ping mediahost.frognet` answered from 10.250.250.1 immediately.
    #
    # A node is identified the way this codebase already identifies one: the
    # semantic tree is present.
    _IS_NODE = os.path.isdir("/opt/frognet_semantic")

    if not path and _IS_NODE:
        # core.hosts_only is the canonical implementation on a node.
        try:
            from core.hosts_only import resolve as _r
            return _r(name)
        except ImportError:
            pass
    if not path:
        path = (os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"),
                             "System32", "drivers", "etc", "hosts")
                if os.name == "nt" else "/etc/hosts")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and name in parts[1:]:
                return parts[0]

    # [CLIENT_USES_DNS_V1] Not in the hosts file. On a NODE that is final: the
    # resolver is not trustworthy there and absence is absence. On a CLIENT the
    # hosts file was never expected to carry .frognet names in the first place --
    # ask the nameserver, which is the LAN's .1.
    if not _IS_NODE:
        import socket as _s
        try:
            return _s.gethostbyname(name)
        except OSError as e:
            raise RuntimeError(
                "%r is not in %s and DNS could not resolve it either (%s). "
                "A client resolves .frognet through the LAN's .1, which serves "
                "that zone -- check this machine's DNS points at it."
                % (name, path, e))

    raise RuntimeError("[HOSTS_ONLY_V1] %r not in %s; refusing to fall through to DNS"
                       % (name, path))


class NoMediaHost(RuntimeError):
    """No media host is resolvable, so no call can be formed."""


MEDIA_NAME = "mediahost.frognet"          # kept for messages only; NOT resolved
# [MEDIAHOST_FROM_MEMORY_V1] Election data -- who holds a role -- is read from the
# CONTROL plane. The elected data host carries application state; it is not where
# the election lives and must never be asked.
CONTROL_DBHOST = "databasehost_control.frognet"
MEDIA_PORT = 9000
PRESENCE_FRESH_S = 30                            # presence older than this == offline
CALL_FRESH_S = 120
# [THE_CONTRACT_IS_FPS_AND_RESOLUTION_V1] the nominal rate a geometry is quoted
# at when a consumer publishes a size but no measured fps
REF_FPS = 24

# [A_MEASUREMENT_BELONGS_TO_THE_RUN_THAT_MADE_IT_V1] One id per process.
#
# A call is joined, not created -- [JOIN_BEFORE_YOU_CREATE_V1] -- so restarting
# a participant does NOT start a fresh call, and the row it wrote last time is
# still in the store under its name. Its measurements come back with it: what
# it was getting, whether it was struggling, and the size it once failed at.
#
# Measured 2026-08-12: a restarted publisher drove the call to the floor almost
# immediately, because the ceiling the previous run had recorded was still
# there and nothing had reason to lift it. Every new run is a fresh beginning,
# and this is how a reader can tell one run's measurements from another's.
RUN_ID = uuid.uuid4().hex[:8]
# [A_CEILING_MUST_BE_ABLE_TO_LIFT_V1] seconds of steady reporting before the
# ceiling lifts one step. In TIME, not poll cycles: a fast poller would lift it
# faster than a promotion can be judged, and the two race and oscillate.
#
# [SETTLE_BEFORE_YOU_CLIMB_V1] and comfortably LONGER than the sender-side
# settle. At 10s it was shorter, so a size that had just failed became a
# candidate again before the rate below it had even settled -- the call climbed
# straight back into it, failed, dropped, and did the same thing a few seconds
# later. Reported 2026-08-12 as "switching a lot".
#
# The asymmetry is deliberate and is the whole design: a failure is believed
# immediately and forgiven slowly. A link that has genuinely improved says so by
# still being steady three quarters of a minute later, which costs one step of
# picture quality for that time and nothing else.
# Strictly greater than Call.SETTLE_S, and the ordering is the point rather than
# either number. SETTLE_S is how long a rate that WORKS is left alone before
# this end proposes climbing; this is how long before a size that FAILED is
# considered again. Forgiving a failure faster than leaving a success alone
# would have the call re-testing known-bad sizes while still unsure of good
# ones. sim_converge X11 asserts the ordering; when SETTLE_S moved to 45 and
# this stayed there, it failed, which is what it is for.
CEILING_LIFT_S = 90.0


# -- codecs: one per converged concern, declaring field order + freshness -----
# RESIDENT_ONCE fields (id, session) are NOT offer()'d -- the substrate crosses them only via
# the FULL frame from the codec's resident dict. So they're passed at construction.
def _presence_codex(me_id: str) -> Codex:
    return Codex(store=None, method="POST", semantic_path="/comms/presence",
                 field_order=["id", "name", "status", "caps", "ts"],
                 freshness={"id": Freshness.RESIDENT_ONCE,
                            "name": Freshness.LATEST_ONLY,
                            "status": Freshness.LATEST_ONLY,
                            "caps": Freshness.LATEST_ONLY,
                            "ts": Freshness.CONTINUOUS},
                 resident={"id": me_id})

def _calls_codex(kind: str, who_field: str) -> Codex:
    """[FOUR_TUPLES_V1] A writer, its calls, a timestamp.

    `who_field` is "host" or "user" -- and ONLY that one is declared. One codex
    carrying both meant every host-scoped row declared a `user` it never offered
    and every user-scoped row declared a `host` it never offered, and each
    converged to None. Caught by sim_calls C11 on its first green run; it is the
    third time this exact shape has bitten, and the first time a test saw it.

    Everything LATEST_ONLY and everything offered on every write. NOTHING is
    RESIDENT_ONCE: a resident field's value comes from the codex's `resident`
    map, not from offer(), and declaring one without supplying it there
    converges it to None -- which published call rows carrying no member at all,
    twice, with no error at either end. A row this small has nothing to gain by
    withholding a field.
    """
    if who_field not in ("host", "user"):
        raise ValueError("who_field must be host or user, not %r" % who_field)
    return Codex(store=None, method="POST",
                 semantic_path="/comms/calls/%s" % kind,
                 field_order=[who_field, "calls", "ts"],
                 freshness={who_field: Freshness.LATEST_ONLY,
                            "calls": Freshness.LATEST_ONLY,
                            "ts": Freshness.CONTINUOUS})


def _mediacontrol_codex() -> Codex:
    """The call's media settings. All LATEST_ONLY, all offered every write.

    Same rule as the other four: nothing RESIDENT_ONCE. A resident field takes
    its value from the codex's `resident` map rather than from offer(), and
    declaring one without supplying it there converges it to None.
    """
    return Codex(store=None, method="POST", semantic_path="/comms/calls/media",
                 field_order=["session", "speed_bps", "geometry", "quality",
                              "set_by", "ts"],
                 freshness={"session": Freshness.LATEST_ONLY,
                            "speed_bps": Freshness.LATEST_ONLY,
                            "geometry": Freshness.LATEST_ONLY,
                            "quality": Freshness.LATEST_ONLY,
                            "set_by": Freshness.LATEST_ONLY,
                            "ts": Freshness.CONTINUOUS})


def _chat_codex() -> Codex:
    return Codex(store=None, method="POST", semantic_path="/comms/chat",
                 field_order=["from_id", "from_name", "text", "ts"],
                 freshness={"from_id": Freshness.LATEST_ONLY,
                            "from_name": Freshness.LATEST_ONLY,
                            "text": Freshness.LOSSLESS_EVENTUAL,
                            "ts": Freshness.CONTINUOUS})


# -- scopes -------------------------------------------------------------------
def _presence_scope(me_id: str) -> str:
    return T.role_scope(f"presence:{me_id}")

def _call_scope(session: str) -> str:
    return T.session_scope(session)

def _host_scope_stable() -> str:
    """host:<ip>, with no pid.

    T.host_scope() appends the pid so a row is unique per RUNNING INSTANCE and
    self-expiring. Wrong here: a media host that restarts must keep announcing
    into the same key, not leave its previous key behind for CALL_FRESH_S with
    calls in it that no longer exist.
    """
    return "host:%s" % T.my_ip()


def _callid_scope(session: str) -> str:
    """call:<id>. The CALL's own row.

    [MEDIACONTROL_BELONGS_TO_THE_CALL_V1] Media settings are a property of the
    call, not of a pair. MediaSpeed was scoped per viewer per session and
    MediaTreatment per producer per viewer, so "the speed" was N x M opinions
    that had to be reconciled -- and a client in the wrong session, or reading
    the wrong end of a pair, saw none of them.

    One row per call. Whoever changes it writes it, everyone in the call reads
    it, current value wins. That is the whole demonstration: move the slider and
    every picture changes, because the MEMORY changed. Nobody sent anything.
    """
    return "call:%s" % session


def _user_scope(name: str) -> str:
    """user:<name>. The NAME, not me_id.

    me_id is minted fresh every launch (fnav: name + uuid4().hex[:4]), so keying
    on it made a new row per run and left the old ones asserting until they aged
    -- forty identities in one roster. The name is stable by construction, the
    same on every platform, and needs no file.
    """
    return "user:%s" % name


def _chat_scope(session: str) -> str:
    return T.session_scope(f"chat:{session}")

def _link_scope(session: str) -> str:
    return T.session_scope(f"link:{session}")

def _treatment_scope(session: str, producer: str, viewer: str) -> str:
    """[SLOWEST_VIEWER_COMMANDS_V1] One row per VIEWER per producer per session.

    Scoped by viewer, not just by producer: every viewer writes what IT can
    take, and the producer serves the smallest. Scoping by producer alone meant
    two viewers overwrote each other and whichever wrote last won -- which is
    the opposite of the rule, since the fast viewer would silently erase the
    slow one's instruction.
    """
    return T.session_scope(f"treatment:{session}:{producer}:{viewer}")


def pick_media_treatment(rows, session: str, producer: str):
    """The geometry `producer` should serve: the SMALLEST any viewer asked for.

    [SLOWEST_VIEWER_COMMANDS_V1] The slowest machine sets the resolution. Not
    the freshest row, not an average, not the last writer -- the minimum by
    pixel count, because serving anything larger is sending a viewer something
    it has already said it cannot take.

    ONE GUARD, TWO READERS. ControlPlane reads through this and so does the fnav
    call engine, which talks to frognet_tuples directly the way it does for
    MediaSpeed. Separate copies of the row discipline drift, and one reader ends
    up accepting a row the other refuses.

    Returns the winning row, or None for "nobody has asked".
    """
    want = T.session_scope(f"treatment:{session}:{producer}:")
    best = None
    for r in rows:
        scope = str(r.get("scope", ""))
        if not scope.startswith(want):
            continue
        v = r.get("value", {})
        if not isinstance(v, dict):
            print("[TREAT] MALFORMED row at scope %s: value is %s, not an "
                  "object - skipped" % (scope, type(v).__name__), flush=True)
            continue
        if v.get("session") != session or v.get("producer") != producer:
            continue
        treat = v.get("treat")
        if not isinstance(treat, dict) or not treat:
            print("[TREAT] MALFORMED row at scope %s from addr=%s ts=%s: no "
                  "treat object - skipped, NOT read as an instruction (keys "
                  "present: %s)" % (scope, r.get("addr", "?"),
                                    v.get("ts", "?"),
                                    ", ".join(sorted(v)) or "none"), flush=True)
            continue
        try:
            px = int(treat["w"]) * int(treat["h"])
        except (KeyError, TypeError, ValueError):
            print("[TREAT] MALFORMED row at scope %s: treat has no usable w/h "
                  "(%s) - skipped" % (scope, sorted(treat)), flush=True)
            continue
        if best is None or px < best["px"]:
            best = {"session": session, "producer": producer,
                    "treat": dict(treat), "px": px,
                    "viewer": v.get("viewer", ""),
                    "set_by": v.get("set_by", ""),
                    "addr": v.get("addr", ""),
                    "ts": int(v.get("ts", 0) or 0)}
    return best


def _transcript_scope(session: str, line_id: str) -> str:
    return T.session_scope(f"transcript:{session}:{line_id}")


def _settings_scope(me_id: str) -> str:
    """Settings are keyed per (node, user), not per user alone.

    role_scope embeds this node's IP, which is exactly right here: a camera index or
    a PortAudio device name is a fact about THIS box. "Camera 1" on the laptop is not
    "camera 1" on the Pi, so settings that followed the identity across machines
    would be actively wrong. What the tuple plane buys is that the settings are
    shared MEMORY rather than a dotfile: any node can read what this user chose on
    this box, they survive a reinstall of the app, and the same convergence that
    carries presence carries them."""
    return T.role_scope(f"settings:{me_id}")


SETTINGS_FRESH_S = 86400        # a day; the heartbeat re-asserts long before this


class ControlPlane:
    """The Communicator's window onto the mesh. Owns the converged channels and the tuple
    reads/writes. UI and the fnav call engine sit ON this; they never touch tuples directly."""

    def __init__(self, me_id: str, me_name: str, caps: Optional[Dict[str, Any]] = None,
                 dbhost: str = DBHOST, relay: Optional[Tuple[str, int]] = None):
        self.me_id = me_id
        self.me_name = me_name
        # [THE_KEY_IS_THE_NAME_NOT_THE_DECORATION_V1] me_name is a DISPLAY
        # string and carries markers -- fnav's publisher passes "*" + name to
        # mark an unattended source. The call tuples are keyed on it, so the
        # marker went into the key: SD:CallsJoined.user:*Dave, which no reader
        # looking for "Dave" will ever match. Read out of the database
        # 2026-08-11.
        self.me_key = (me_name or "").lstrip("*").strip() or me_name
        self.caps = caps or {}
        self.dbhost = dbhost
        self.relay = relay            # explicit relay (host,port) override; used if election fails
        # [FOUR_TUPLES_V1] What THIS node asserts, one dict per row it owns.
        self._offered: Dict[str, Dict[str, Any]] = {}     # -> CallsAnnounce
        self._ever_offered: set = set()   # what THIS process may withdraw
        self._joined: Dict[str, Dict[str, Any]] = {}      # -> CallsJoined
        self._invited_by_me: Dict[str, Dict[str, Any]] = {}  # name -> sessions
        self._row_ch: Dict[Any, Channel] = {}   # (var, scope) -> Channel
        self._said = None                                 # last logged call set
        self._active_calls: Dict[str, Dict[str, Any]] = {}   # legacy, unused here
        # one convergence channel per concern, per UnREST (held reference + freshness)
        self._presence_ch = Channel(_presence_codex(me_id))
        self._chat_ch: Dict[str, Channel] = {}      # per-session
        self._call_ch: Dict[str, Channel] = {}      # MediaSpeed/link paths only
        self._settings: Dict[str, Any] = {}         # the bag this client holds

    # ---- PRESENCE -----------------------------------------------------------
    def announce(self, status: str = "online") -> None:
        """Write/refresh MY presence. Call at startup and on every tick to stay online;
        converges SAME (zero change) when nothing about me moved, FULL/DIFF when it did."""
        self._presence_ch.offer("name", self.me_name)
        self._presence_ch.offer("status", status)
        self._presence_ch.offer("caps", self.caps)
        self._presence_ch.offer("ts", int(time.time()))
        kind = self._publish(self._presence_ch, "presence", _presence_scope(self.me_id))
        return kind

    def roster(self, fresh_s: int = PRESENCE_FRESH_S) -> List[Dict[str, Any]]:
        """Everyone currently online: read presence tuples, freshness-filtered so a peer who
        stopped refreshing simply falls off (stale == gone, no DELETE)."""
        out = []
        for r in T.get(SERVICE, "presence", dbhost=self.dbhost, fresh_s=fresh_s):
            v = r.get("value", {})
            if v.get("id"):
                out.append({"id": v["id"], "name": v.get("name", v["id"]),
                            "status": v.get("status", "online"), "caps": v.get("caps", {})})
        return out

    def go_offline(self, status: str = "offline") -> None:
        self.announce(status=status)

    # ---- CALLS --------------------------------------------------------------
    # [FOUR_TUPLES_V1] Four facts, four writers, one row each.
    #
    #   CallsAnnounce/host:<ip>     what this host OFFERS, and where to connect
    #   CallsInProgress/host:<ip>   what this media host SEES carrying traffic
    #   CallsJoined/user:<name>     what this user SAYS they are in
    #   CallsInvitations/user:<name> what this user has been ASKED to join
    #
    # Every one is written by the single party with standing to know it, scoped
    # to that party, updated in place forever. Nobody writes a row about anybody
    # else, so nothing needs deleting, reaping, or reconciling: a writer that
    # stops is a fact that ages out.
    #
    # What this replaces, and why:
    #
    #   One row per CALL carrying a members LIST. Whoever wrote last renewed
    #   everyone's membership on their behalf, so a peer that remembered you put
    #   you back into a call you had left -- and the lobby, which rang on finding
    #   your name in a members list, rang you from a node that cannot address
    #   anyone in particular.
    #
    #   Then one row per (session, member) pair. Correct about who asserts what,
    #   and wrong in shape: a new scope for every pair, unbounded, needing a
    #   reaper, and keyed on a me_id minted fresh every launch -- forty dead
    #   identities in one roster.
    #
    # Identity is the NAME. It is stable across launches by construction, it is
    # the same on every platform, and there is no file to write. me_id goes back
    # to being what it always was: a handle for this process, in no tuple key.
    #
    # JOINED and INPROGRESS are two OBSERVERS, not two copies of one fact, and
    # neither corrects the other. A user in JOINED that INPROGRESS does not show
    # is a user who has joined something not yet carrying traffic. Reading both
    # is how you tell that from a user who is not there.

    def announce_call(self, session: str, host: str, port: int) -> bool:
        """Offer a call from THIS host. Idempotent; re-announcing refreshes."""
        self._offered[session] = {"host": host, "port": int(port)}
        self._ever_offered.add(session)
        return self._write_announce()

    def unannounce_call(self, session: str) -> bool:
        """Stop offering it. The row is rewritten without it."""
        self._offered.pop(session, None)
        return self._write_announce()

    def start_call(self, members: Optional[List[str]] = None) -> Dict[str, Any]:
        """Originate: mint a session, resolve the media host, offer it, join it.

        `members` are INVITED. Being invited is not being on a call, and the two
        were the same field for long enough to ring people who had left.
        """
        session = uuid.uuid4().hex[:12]
        host, port = self._resolve_media()
        self.announce_call(session, host, port)
        self.join_call_at(session, host, port)
        for who in [m for m in (members or []) if m and m != self.me_key]:
            self.invite(session, [who])
        return {"session": session, "host": host, "port": port}

    def join_call(self, session: str) -> Optional[Dict[str, Any]]:
        """Join an announced call: say so, and take the invitation down."""
        info = self.call_info(session)
        if not info:
            return None
        self.join_call_at(session, info["host"], info["port"])
        self._drop_invitation(session)
        return {"session": session, "host": info["host"], "port": info["port"]}

    def join_call_at(self, session: str, host: str, port: int) -> bool:
        """Assert that I am on this call. One fact, about me.

        [A_MEASUREMENT_BELONGS_TO_THE_RUN_THAT_MADE_IT_V1] And only the
        assertion: nothing measured is carried in.

        Joining an open call is the normal path, so a restarted participant
        lands on the call it was on before, under the same name, on top of the
        row it wrote last time. Carrying that row's measurements forward means a
        ceiling recorded by a process that no longer exists still holds the call
        down -- a restarted publisher drove straight to the floor, measured
        2026-08-12.

        A run states where it is and what it is doing. What it is GETTING it has
        not measured yet, and says so by not claiming anything until it has.
        """
        self._joined[session] = {"host": host, "port": int(port)}
        return self._write_joined()

    # [PRODUCER_LEADS_CONSUMERS_REPORT_V1] Two roles, not a negotiation
    # between peers.
    #
    # A PRODUCER sends and does not receive. It has no measurement of the link
    # and therefore no vote: it starts at the best its device can do and moves
    # only when a consumer reports. Treating it as a peer let a headless
    # publisher with no viewers cap itself on its own absent downlink --
    # "slowest consumer is Dave", measured 2026-08-11 -- and walk a good link
    # down to 160x120 on evidence that did not exist.
    #
    # A CONSUMER joins, reads what the producer says it is sending, tries to
    # take it, and reports two things only it can know: the rate it is actually
    # getting, and whether it is HAPPY with it.
    #
    # The producer reads every consumer's report:
    #   any consumer unhappy  -> step DOWN, to the worst
    #   every consumer happy  -> try UP one step
    # Down on the worst, up only on unanimity.

    def report_capability(self, session: str, w: int, h: int,
                          fps: float) -> bool:
        """[EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1] What this machine can do.

        Every participant, whatever its role -- a fact about this device and its
        camera, known only here. It is what a producer starts at, and what tells
        the others what is possible before anything has been measured.
        """
        if session not in self._joined:
            return False
        self._joined[session]["can_do"] = {
            "w": int(w), "h": int(h), "fps": round(float(fps or 0.0), 1)}
        return self._write_joined()

    def report_sending(self, session: str, w: int, h: int,
                       fps: float = 0.0, capped: bool = False) -> bool:
        """PRODUCER: what I am sending, for consumers to expect and judge."""
        if session not in self._joined:
            return False
        # [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] No role is stamped here.
        # ANY end that is sending publishes `sending`, and an end can both send
        # and receive -- stamping "producer" made a two-way participant's own
        # rate invisible to everybody, because writing `getting` then relabelled
        # it "consumer" and the two overwrote each other.
        #
        # The presence of the FACT is the role: `sending` means it is sending,
        # `getting` means it is receiving, and both means both.
        # `capped` says this size is not a choice: the uplink cannot carry more.
        # Without it a sender that lowered itself is climbed straight back up on
        # the grounds that no consumer complained, and oscillates.
        self._joined[session]["sending"] = {
            "w": int(w), "h": int(h), "fps": round(float(fps or 0.0), 1),
            "capped": bool(capped), "run": RUN_ID}
        return self._write_joined()

    def report_receiving(self, session: str, w: int, h: int, fps: float,
                         happy: bool, struggling: bool = False) -> bool:
        """CONSUMER: what I am getting, and whether it is good enough.

        `happy` is this consumer's own verdict, not a threshold somebody else
        applies to it. It is what lets a producer climb: only when EVERY
        consumer has said yes.
        """
        if session not in self._joined:
            return False
        # [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] no role stamp: the
        # presence of `getting` is what says this end is receiving, and an end
        # that also sends keeps its `sending` alongside it.
        # [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1] happy lets the network climb;
        # struggling makes it step down. A consumer that is neither is simply
        # not yet sure, and nobody moves on that.
        cur = dict(self._joined[session].get("getting") or {})
        # [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1] `struggling` is a
        # transient: it clears as soon as the rate comes down. `too_big` is the
        # DURABLE fact underneath it -- the smallest size this end has been
        # unable to keep up with.
        #
        # Without it the derivation had no memory: John struggles at 1280, the
        # call drops to 854, John recovers, the struggle report clears, 1280
        # becomes a candidate again, and it promotes straight back into a size
        # already known not to work. sim_converge measured ninety of those
        # cycles in five seconds.
        #
        # It lives in the STATE, not in any participant, so every end derives
        # the same ceiling and nobody keeps a history.
        too_big = int(cur.get("too_big") or 0)
        steady = float(cur.get("steady_since") or 0.0)
        if struggling and int(w or 0) > 0:
            px = int(w) * int(h)
            too_big = px if not too_big else min(too_big, px)
            steady = 0.0             # the credit does not survive a failure
        elif happy and too_big:
            # [A_CEILING_MUST_BE_ABLE_TO_LIFT_V1] Keeping up clears it, one step.
            #
            # This required happy AT OR ABOVE the failed size -- which the rate
            # is pinned below by that very ceiling, so that size is never sent,
            # so it can never be proven good. The ceiling was permanent by
            # construction. Measured 2026-08-11: one struggling report at
            # 854x480 pinned the call at 160x120 for the rest of its life, on a
            # gigabit link with zero drops and 23.4/24 fps the whole time.
            #
            # A consumer keeping up is evidence the link has room. Not proof
            # that the failed size works -- but enough to try the next one up,
            # which is exactly what a promotion is for. Raise the ceiling one
            # step per steady report; if that size fails again, `struggling`
            # puts it straight back.
            #
            # This is the difference between a ceiling and a scar.
            # Lifting on EVERY steady report lifts faster than the link can be
            # re-tested: the rate promotes into the newly-allowed size, fails,
            # and the whole thing oscillates -- sim_converge X6 caught exactly
            # that. A steady report arrives every couple of seconds; a promotion
            # needs several to be judged. So the ceiling lifts on a COUNT of
            # them, not on each one.
            # In TIME, not in reports. A report count is a count of poll
            # cycles, and a fast poller lifts the ceiling in a fraction of a
            # second -- faster than any promotion can be judged, so the rate
            # promotes into the newly-allowed size, fails, and oscillates.
            # sim_converge X6 caught that at a 20ms tick.
            #
            # The link has to be demonstrably steady for a while, on the wall
            # clock, whatever the poll rate happens to be.
            since = float(cur.get("steady_since") or 0.0)
            if not since:
                since = time.time()
            elif time.time() - since >= CEILING_LIFT_S:
                since = time.time()
                too_big = int(too_big * 2)
                if too_big >= 1920 * 1080:
                    too_big = 0      # above the top rung: no ceiling at all
            steady = since
        self._joined[session]["getting"] = {
            "w": int(w), "h": int(h), "fps": round(float(fps or 0.0), 1),
            "happy": bool(happy), "struggling": bool(struggling),
            "too_big": too_big, "steady_since": steady,
            # [A_MEASUREMENT_BELONGS_TO_THE_RUN_THAT_MADE_IT_V1]
            "run": RUN_ID}
        return self._write_joined()

    # [NO_SYNC_JUST_STATE_V1] There is no request tuple and no requested_rate.
    #
    # They existed for one revision: a consumer wrote "I want 640x360" and a
    # sender read it and complied. That is a negotiation with the word filed
    # off -- two parties, a message, and one waiting on the other to act.
    #
    # Each end publishes only what it knows about ITSELF: a producer what it is
    # sending, a consumer what it is getting and whether it is keeping up.
    # Every participant reads all of it and derives the same rate on its own.
    # Nobody is asked and nobody replies.

    def producer_sending(self, session: str) -> Optional[Dict[str, Any]]:
        """The LOWEST rate anybody on this call is sending at.

        [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] Every end that is sending
        publishes what it is sending, and every end reads the lowest of them.
        Nobody sends faster than the slowest sender on the call.

        This returned the first row with role=producer, so only one end's rate
        bounded anything and a second sender could sit well above it -- measured
        2026-08-11, one end at 640x360 while the other pushed 1920x1080 in the
        opposite direction on the same call.

        A sender that has settled somewhere has established that rate BY DOING
        IT, on the same relay and mostly the same path. There is no reason for
        anybody else to exceed it, and every reason not to: the bytes it costs
        come out of the same pipe the slower end is already struggling with.

        Ranked on pixels x fps, because the contract is both.
        """
        best = None
        for r in T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            c = (v.get("calls") or {}).get(session) or {}
            s_ = c.get("sending")
            if not isinstance(s_, dict):
                continue
            try:
                w, h = int(s_["w"]), int(s_["h"])
            except (KeyError, TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            fps = float(s_.get("fps") or 0.0)
            rate = w * h * (fps if fps > 0 else float(REF_FPS))
            if best is None or rate < best["rate"]:
                best = {"w": w, "h": h, "fps": fps, "rate": rate,
                        "producer": v.get("user", "")}
        return best

    def call_rate(self, session: str, ladder) -> Optional[Dict[str, Any]]:
        """[THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1] The rate, from state alone.

        Not "my current index, plus or minus one". A rate derived by stepping
        from a local index is a function of THIS end's history, so two ends that
        have seen different histories sit at different sizes -- measured
        2026-08-11 in sim_whole_call, two ends at 854x480 and one at 640x360,
        all reading the same rows.

        The answer is a pure function of what everyone has published:

            the largest size on the ladder at which NOBODY has reported
            struggling, and no bigger than the slowest sender is sending.

        Same rows in, same size out, whoever asks, with no memory and nothing
        to converge. `ladder` is passed in because what a size means in rungs is
        the media engine's business, not the control plane's.
        """
        rows = list(T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                          fresh_s=CALL_FRESH_S))

        def _px(g):
            return int(g["w"]) * int(g["h"])

        # Sizes anybody has reported trouble at. A consumer struggling at
        # 1280x720 says nothing good about 1920x1080 either, so trouble at a
        # size condemns every size at least that large.
        bad = []
        for r in rows:
            v = r.get("value", {})
            g = ((v.get("calls") or {}).get(session) or {}).get("getting")
            if isinstance(g, dict) and g.get("struggling") and g.get("w"):
                bad.append(_px(g))

        # And nobody sends above the slowest sender.
        cap = None
        for r in rows:
            v = r.get("value", {})
            s_ = ((v.get("calls") or {}).get(session) or {}).get("sending")
            if isinstance(s_, dict) and s_.get("w"):
                cap = _px(s_) if cap is None else min(cap, _px(s_))

        for g in ladder:                       # largest first
            if cap is not None and _px(g) > cap:
                continue
            if any(_px(g) >= b for b in bad):
                continue
            return dict(g)
        return dict(ladder[-1])                # everything condemned: the floor

    def call_state(self, session: str) -> Dict[str, Any]:
        """Everything anybody has published about this call, in one read.

        [ONE_DERIVATION_V1] Think of it as one program with many threads over
        shared memory, because that is what it is. Each thread writes only what
        it alone can know; every thread reads all of it; and the rate is a PURE
        FUNCTION of that state, computed identically everywhere.

        No messages. No requests. Nothing waiting on anybody. If two ends
        disagree about the rate it is because they read the store at different
        instants, and the next read fixes it -- there is nothing to reconcile
        because there was never an exchange.
        """
        senders, getters = [], []
        for r in T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            who = v.get("user") or ""
            c = (v.get("calls") or {}).get(session) or {}
            s_ = c.get("sending")
            if isinstance(s_, dict) and int(s_.get("w") or 0) > 0:
                senders.append({"user": who, "w": int(s_["w"]),
                                "h": int(s_["h"]),
                                "fps": float(s_.get("fps") or 0.0),
                                "capped": bool(s_.get("capped"))})
            g_ = c.get("getting")
            if isinstance(g_, dict):
                getters.append({"user": who, "w": int(g_.get("w") or 0),
                                "h": int(g_.get("h") or 0),
                                "fps": float(g_.get("fps") or 0.0),
                                "happy": bool(g_.get("happy")),
                                "struggling": bool(g_.get("struggling")),
                                "too_big": int(g_.get("too_big") or 0)})
        return {"senders": senders, "getters": getters}

    @staticmethod
    def derive_rate(state: Dict[str, Any], ladder: List[Dict[str, Any]],
                    current: Optional[Dict[str, Any]] = None
                    ) -> Optional[Dict[str, Any]]:
        """The call's rate, from the state alone. Pure: no clock, no self, no IO.

        [ONE_DERIVATION_V1] Every participant runs THIS over the same rows and
        lands on the same answer without being told. It is testable on its own
        precisely because it touches nothing.

        [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1] and the reason this took all
        day: a rule that promotes whenever everyone is currently happy will
        promote into a rate that has ALREADY been shown not to work, become
        unhappy, demote, become happy, and promote again. sim_converge caught
        exactly that -- 854 to 1280 to 854 to 640 to 854, ninety-odd times in
        five seconds, from a derivation with no memory of what had failed.

        The evidence a consumer publishes is not just "am I happy NOW" -- it is
        "I am getting w x h at f fps". A report of struggling AT A SIZE says
        that size does not work on that link. So:

          the ceiling is the smallest size at which anybody is struggling;
          nothing may promote to or above it.

        That is derived from the state itself, not remembered by any
        participant, so every end computes the same ceiling and no end has to
        keep a history. A link that genuinely improves says so by reporting
        happy at that size again, and the ceiling lifts with it.

        The rule, in order:
          1. nobody sending -> no rate; each end runs its own ladder
          2. the LOWEST sender bounds it
          3. anybody struggling -> one step below the bound
          4. everybody happy AND the step up is not a known-bad size -> promote
          5. otherwise the bound stands
        """
        def idx_of(g):
            px = int(g["w"]) * int(g["h"])
            for n, x in enumerate(ladder):
                if x["w"] * x["h"] <= px:
                    return n
            return len(ladder) - 1

        senders = state.get("senders") or []
        if not senders:
            return None
        slowest = min(senders,
                      key=lambda s: s["w"] * s["h"]
                      * (s["fps"] if s["fps"] > 0 else REF_FPS))
        i = idx_of(slowest)

        getters = state.get("getters") or []

        # [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1] The smallest size anybody
        # is struggling at is a ceiling: nothing at or above it may be chosen.
        # The durable ceiling: the smallest size ANY end has failed at.
        # idx_of takes a GEOMETRY and compares w*h. Handing it {"w": px,
        # "h": 1} made px the width and 1 the height, so the comparison was
        # against px*1 and every ceiling landed on the wrong rung -- the
        # oscillation survived with the fix in place. Compare pixel counts to
        # pixel counts.
        worst = None
        for g in getters:
            px = int(g.get("too_big") or 0)
            if px <= 0:
                continue
            n = len(ladder) - 1
            for k, x in enumerate(ladder):
                if x["w"] * x["h"] <= px:
                    n = k
                    break
            worst = n if worst is None else min(worst, n)

        if any(g["struggling"] for g in getters):
            i += 1
        elif getters and all(g["happy"] for g in getters):
            i -= 1
        elif any(s.get("capped") for s in senders):
            # [A_SENDER_IS_NOT_A_CONSUMER_OF_ITSELF_V1] A sender that has said
            # its own uplink is tight does not then get climbed on the grounds
            # that nobody has complained. Its constraint IS the complaint, and
            # it is about the one link no consumer can see.
            pass
        elif not getters:
            # [A_SENDER_BOUND_ALONE_CANNOT_CLIMB_V1] With no receive reports the
            # only input is what everyone is sending -- and that is where they
            # ALREADY are, so the answer is always "stay". The rate can then
            # only ratchet down, one keyframe walk at a time, and never comes
            # back. Measured 2026-08-11: both ends walked to 160x120, neither
            # ever published a `getting` row, and it sat there.
            #
            # Nobody reporting is not evidence of trouble. Absent a consumer
            # saying otherwise, try one step up and let the keyframes judge it
            # -- [WATCH_THE_KEYFRAMES_AFTER_A_PROMOTE_V1] -- which is the same
            # thing a lone sender does with its own ladder.
            #
            # Bounded by the failure ceiling below, so this cannot climb back
            # into a size already shown not to work.
            i -= 1

        if worst is not None:
            i = max(i, worst + 1)     # strictly smaller than what failed
        i = max(0, min(len(ladder) - 1, i))
        return dict(ladder[i])

    def consumer_feedback(self, session: str,
                          at: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Every consumer's report on this call, and the verdict.

        all_happy is False when there are NO consumers: a producer with nobody
        listening has no unanimity to climb on and holds, rather than walking
        itself up on an empty room.
        """
        reports, unhappy, slowest = [], [], None
        for r in T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            c = (v.get("calls") or {}).get(session) or {}
            g = c.get("getting")
            if not isinstance(g, dict):
                continue
            rep = {"user": v.get("user", ""), "w": int(g.get("w") or 0),
                   "h": int(g.get("h") or 0), "fps": float(g.get("fps") or 0.0),
                   "happy": bool(g.get("happy"))}
            # [A_REPORT_IS_ABOUT_A_SIZE_V1] A report says what it was getting
            # AND at what size. A report about a size that is no longer being
            # sent is about the PAST, and counting it steps again for something
            # already answered.
            #
            # Measured 2026-08-11 in sim_whole_call: one slow viewer reporting
            # every window walked the whole call 1920 -> 160 in three seconds,
            # one step per window, because each window re-counted a complaint
            # that had already been acted on.
            #
            # No timer and no memory: the size in the report either matches what
            # is being sent now or it does not. Pure function of the rows, which
            # is what lets every end derive the same answer without agreeing on
            # anything.
            rep["stale"] = bool(
                at and at.get("w")
                and (int(rep["w"]), int(rep["h"]))
                != (int(at["w"]), int(at["h"])))
            if rep["stale"]:
                reports.append(rep)
                continue
            rep["struggling"] = bool(g.get("struggling"))
            reports.append(rep)
            if rep["struggling"]:
                unhappy.append(rep)
            if slowest is None or rep["fps"] < slowest["fps"]:
                slowest = rep
        # [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1] The two directions read
        # DIFFERENT fields, and that is the whole point of having two.
        #
        #   unhappy   -> `struggling`: measured now, genuinely below the bar
        #   all_happy -> `happy`: earned over windows, has held
        #
        # Deriving all_happy from "nobody struggling" would let a consumer that
        # has not proven steady count toward unanimity, which is exactly the
        # flag the climb is supposed to wait for.
        _live = [r for r in reports if not r.get("stale")]
        return {"reports": reports, "unhappy": unhappy, "slowest": slowest,
                "consumers": len(reports), "current": len(_live),
                # A stale report is not a complaint and not a vote to climb.
                # Both directions wait for a report about the CURRENT size.
                "all_happy": bool(_live) and all(r["happy"] for r in _live)
                and len(_live) == len(reports)}

    def leave_call(self, session: str) -> None:
        """Stop asserting it. Nothing else to do and nobody else to tell.

        A client that crashes here does the same thing a little more slowly,
        which is the point: the tidy path and the crash path reach one place.
        """
        self._joined.pop(session, None)
        self._offered.pop(session, None)
        self._write_joined()
        self._write_announce()

    def refresh_calls(self) -> None:
        """Re-assert MY OWN rows. Called from the heartbeat."""
        if self._joined:
            self._write_joined()
        if self._offered:
            self._write_announce()
        if self._invited_by_me:
            self._write_invitations_for_others()

    # -- invitations ----------------------------------------------------------
    def invite(self, session: str, names: List[str]) -> Optional[List[str]]:
        """Ask people to a call. The row is written to the INVITEE's scope.

        Scoped to the invitee so reading is one row -- your own -- rather than a
        scan of everybody's rows for your own name. That scan is what let a
        stale membership assertion look exactly like a summons.
        """
        info = self.call_info(session)
        if not info:
            return None
        names = [n for n in (names or []) if n and n != self.me_key]
        for who in names:
            self._invited_by_me.setdefault(who, {})[session] = {
                "host": info["host"], "port": info["port"]}
        self._write_invitations_for_others()
        return names

    def invitations_for_me(self, fresh_s: int = CALL_FRESH_S
                           ) -> List[Dict[str, Any]]:
        """What I have been asked to join, from my own row."""
        want = _user_scope(self.me_key)
        out = []
        for r in T.get(SERVICE, "CallsInvitations", dbhost=self.dbhost,
                       fresh_s=fresh_s):
            if str(r.get("scope", "")) != want:
                continue
            for sid, inv in (r.get("value", {}).get("calls") or {}).items():
                if not isinstance(inv, dict) or not inv.get("host"):
                    continue
                out.append({"session": sid, "from": inv.get("from", ""),
                            "host": inv["host"],
                            "port": int(inv.get("port") or 0)})
        return out

    def decline(self, session: str) -> None:
        """Say no by taking it out of my own row. Same trace as accepting."""
        self._drop_invitation(session)

    def _drop_invitation(self, session: str) -> None:
        mine = self._my_invitations()
        if session not in mine:
            return
        mine.pop(session, None)
        self._write_invitations(self.me_key, mine)

    def _my_invitations(self) -> Dict[str, Any]:
        want = _user_scope(self.me_key)
        for r in T.get(SERVICE, "CallsInvitations", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            if str(r.get("scope", "")) == want:
                return dict(r.get("value", {}).get("calls") or {})
        return {}

    # -- reading --------------------------------------------------------------
    def list_calls(self, live_only: bool = False) -> List[Dict[str, Any]]:
        """Every call anybody is on, with who is on it and where to connect.

        [A_MEMBER_IS_PROOF_THE_CALL_EXISTS_V1] A call exists because somebody is
        on it. It does not stop existing because the process that originated it
        went away.

        This required a CallsAnnounce row, which only the ORIGINATOR writes and
        only while its process lives. So a caller whose originator had restarted
        -- or whose _offered map had been emptied by any of the paths that clear
        it -- vanished from every lobby while still sitting on the call, and
        nobody could join. Measured 2026-08-11: no joinable calls, with
        CallsJoined rows present the whole time.

        Membership proves the call. The announce row supplies WHERE, and where a
        member's own row already carries the host and port, that is enough on
        its own. A call is listed if anybody is on it, full stop.
        """
        offered: Dict[str, Dict[str, Any]] = {}
        ann_rows = 0
        for r in T.get(SERVICE, "CallsAnnounce", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            ann_rows += 1
            for sid, c in (r.get("value", {}).get("calls") or {}).items():
                if not isinstance(c, dict) or not c.get("host"):
                    continue
                offered.setdefault(sid, {"session": sid, "members": [],
                                         "host": c["host"],
                                         "port": int(c.get("port") or 0)})

        # Membership is the other, and sufficient, evidence.
        members = self._members_by_session()
        where = self._where_by_session()
        for sid, who in members.items():
            if sid not in offered:
                w = where.get(sid) or {}
                if not w.get("host"):
                    continue          # on a call with no known address: unjoinable
                offered[sid] = {"session": sid, "members": [],
                                "host": w["host"], "port": int(w.get("port") or 0)}
            offered[sid]["members"] = sorted(who)

        if not offered:
            if ann_rows or members:
                LOG.info("no joinable calls: %d announce row(s), %d session(s) "
                         "with members, but none carried a host", ann_rows,
                         len(members))
            else:
                LOG.info("no joinable calls: nothing on %s (fresh_s=%d) -- "
                         "nobody is on a call, or this is not the host they "
                         "write to", self.dbhost, CALL_FRESH_S)
            self._said = None
            return []

        if live_only:
            live = self.calls_in_progress()
            offered = {s: c for s, c in offered.items() if s in live}
        out = list(offered.values())
        _now = tuple(sorted((c["session"], tuple(c["members"])) for c in out))
        if _now != getattr(self, "_said", None):
            self._said = _now
            LOG.info("joinable calls: %s", ", ".join(
                "%s(%s)" % (c["session"][:8], ",".join(c["members"]) or "empty")
                for c in out) or "none")
        return out

    def _where_by_session(self) -> Dict[str, Dict[str, Any]]:
        """Host and port for a session, from the rows of the people ON it.

        [A_MEMBER_IS_PROOF_THE_CALL_EXISTS_V1] Every member records where it
        connected when it joined, so the address survives the originator
        leaving. Nobody has to still be announcing for a call to be joinable.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for r in T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            for sid, c in (v.get("calls") or {}).items():
                if isinstance(c, dict) and c.get("host") and sid not in out:
                    out[sid] = {"host": c["host"], "port": c.get("port") or 0}
        return out

    def _members_by_session(self) -> Dict[str, set]:
        out: Dict[str, set] = {}
        for r in T.get(SERVICE, "CallsJoined", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            who = v.get("user")
            if not who:
                continue
            for sid in (v.get("calls") or {}):
                out.setdefault(sid, set()).add(who)
        return out

    def calls_in_progress(self) -> Dict[str, Dict[str, Any]]:
        """What the media hosts SEE, keyed by session. Ground truth about
        traffic, written by the only party holding the sockets."""
        out: Dict[str, Dict[str, Any]] = {}
        for r in T.get(SERVICE, "CallsInProgress", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            v = r.get("value", {})
            for sid, c in (v.get("calls") or {}).items():
                if isinstance(c, dict):
                    out[sid] = dict(c, mediahost=v.get("host", ""))
        return out

    def report_in_progress(self, sessions: Dict[str, Dict[str, Any]]) -> bool:
        """Media host: publish what is actually carrying traffic here."""
        return self._write_row("CallsInProgress", _host_scope_stable(),
                               _calls_codex("inprogress", "host"),
                               {"host": T.my_ip(), "calls": dict(sessions)})

    def call_info(self, session: str) -> Optional[Dict[str, Any]]:
        for c in self.list_calls():
            if c["session"] == session:
                return c
        return None

    # -- the call's own media settings ---------------------------------------
    def set_media_control(self, session: str, speed_bps: int = 0,
                          geometry: str = "", quality: str = "") -> bool:
        """Write the CALL's media settings. Anyone in the call may.

        [MEDIACONTROL_BELONGS_TO_THE_CALL_V1] Not a request to a peer and not a
        per-viewer cap: the call's current setting, in one place, which every
        participant reads. speed_bps 0 means unconstrained; "" means leave that
        dimension alone.
        """
        return self._write_row("CallsMediaControl", _callid_scope(session),
                               _mediacontrol_codex(),
                               {"session": session,
                                "speed_bps": int(speed_bps or 0),
                                "geometry": geometry or "",
                                "quality": quality or "",
                                "set_by": self.me_key})

    def media_control(self, session: str,
                      fresh_s: int = CALL_FRESH_S) -> Optional[Dict[str, Any]]:
        """The call's media settings, or None if nobody has set any."""
        want = _callid_scope(session)
        for r in T.get(SERVICE, "CallsMediaControl", dbhost=self.dbhost,
                       fresh_s=fresh_s):
            if str(r.get("scope", "")) != want:
                continue
            v = r.get("value", {})
            if v.get("session") != session:
                continue
            return {"session": session,
                    "speed_bps": int(v.get("speed_bps") or 0),
                    "geometry": v.get("geometry") or "",
                    "quality": v.get("quality") or "",
                    "set_by": v.get("set_by") or "",
                    "ts": int(v.get("ts") or 0)}
        return None

    def media_share(self, session: str) -> Dict[str, Any]:
        """This sender's share of the call's pipe.

        [THE_PIPE_IS_SHARED_V1] A call's speed setting is the whole pipe, not
        each sender's allowance. With both ends sending, each one arming the
        full number puts twice the setting on the wire -- and the ladders then
        fight the congestion they jointly caused, each blaming its own link.

        Share = total / senders, where senders is the membership of the call
        from CallsJoined. Both ends read the same row and the same membership,
        so both compute the same number without exchanging anything. That is the
        point of keeping it in memory rather than negotiating it.

        A sender counts once whether or not it is currently sending video: a
        peer that is shedding will come back, and taking its share away the
        moment it stops means the other end grabs the pipe and the returning one
        has to fight for it. The division is by who is ON the call.

        total 0 means unconstrained, and unconstrained divided by anything is
        still unconstrained.
        """
        row = self.media_control(session)
        total = int((row or {}).get("speed_bps") or 0)
        members = []
        for c in self.list_calls():
            if c["session"] == session:
                members = c.get("members") or []
                break
        n = max(1, len(members))
        return {"total_bps": total, "senders": n, "members": sorted(members),
                "share_bps": (total // n) if total > 0 else 0,
                "geometry": (row or {}).get("geometry") or "",
                "set_by": (row or {}).get("set_by") or "",
                "ts": int((row or {}).get("ts") or 0)}

    # -- the four writers -----------------------------------------------------
    def _write_announce(self) -> bool:
        """The HOST's offers. Scoped per host, so merge -- do not overwrite.

        [ANNOUNCE_IS_THE_HOSTS_ROW_V1] More than one communicator can run on one
        machine, and the row is keyed by host. Writing only this process's own
        offers made the second process erase the first one's calls, and leaving
        a call erased every other call on the box. Caught by sim_calls the first
        time it ran, because four clients there share one address.

        Read the host's current row, drop only the sessions THIS process
        offered and no longer offers, add the ones it does. Read-modify-write is
        legitimate here in a way it never was for membership: this is our own
        host's row, describing our own host, and the sessions we did not write
        are still that host's offers.
        """
        want = _host_scope_stable()
        current = {}
        for r in T.get(SERVICE, "CallsAnnounce", dbhost=self.dbhost,
                       fresh_s=CALL_FRESH_S):
            if str(r.get("scope", "")) == want:
                current = dict(r.get("value", {}).get("calls") or {})
                break
        for sid in self._ever_offered - set(self._offered):
            current.pop(sid, None)
        current.update(self._offered)
        return self._write_row("CallsAnnounce", want,
                               _calls_codex("announce", "host"),
                               {"host": T.my_ip(), "calls": current})

    def _write_joined(self) -> bool:
        return self._write_row("CallsJoined", _user_scope(self.me_key),
                               _calls_codex("joined", "user"),
                               {"user": self.me_key,
                                "calls": dict(self._joined)})

    def _write_invitations(self, who: str, calls: Dict[str, Any]) -> bool:
        return self._write_row("CallsInvitations", _user_scope(who),
                               _calls_codex("invite:%s" % who, "user"),
                               {"user": who, "calls": dict(calls)})

    def _write_invitations_for_others(self) -> None:
        for who, calls in list(self._invited_by_me.items()):
            merged = {}
            for sid, c in calls.items():
                merged[sid] = dict(c, **{"from": self.me_name})
            self._write_invitations(who, merged)

    def _write_row(self, var: str, scope: str, codex, value) -> bool:
        """One row, one writer, whole value every time.

        [FOUR_TUPLES_V1] Every field is LATEST_ONLY and every field is offered on
        every write. Nothing is RESIDENT_ONCE here: a resident field takes its
        value from the codex's `resident` map, not from offer(), and declaring
        one without supplying it there converges it to None -- which published
        rows carrying no member at all, silently, twice. A row this small has
        nothing to save by holding a field back.
        """
        # [A_CHANNEL_BELONGS_TO_ONE_CODEX_V1] Key on (var, scope), not scope.
        #
        # Keyed on scope alone, a channel built for one codex was handed back
        # for another with the same scope string -- CallsAnnounce and
        # CallsInProgress share host:<ip>, and this instance's other channels
        # collide the same way. The offered fields then land in a structure that
        # does not declare them, and what reaches the store is the OTHER codex's
        # fields, all null. Read out of the database 2026-08-11:
        #
        #   SD:CallsAnnounce.host:10.102.60.1
        #     {"addr":null,"awaiting_keyframe":null,"cap_bps":null,
        #      "frames_aimed":null,"frames_delivered":null,...,"reason":<ts>}
        #
        # Backpressure fields, in a call row. No error at either end: the write
        # succeeded, the read succeeded, and the row carried nothing usable.
        key = (var, scope)
        ch = self._row_ch.get(key)
        if ch is None:
            ch = self._row_ch[key] = Channel(codex)
        for k, v in value.items():
            ch.offer(k, v)
        ch.offer("ts", int(time.time()))

        # [A_WRITER_CHECKS_ITS_OWN_OUTPUT_V1] What is about to be stored is
        # ch.reference, and it is built by the CODEX -- not by what was offered.
        # A field the codex does not declare is dropped; a field it declares and
        # nobody supplies is None. Both are silent, and both have shipped:
        #
        #   member missing from field_order          -> absent
        #   member RESIDENT_ONCE with no resident    -> None
        #
        # And read out of the live database 2026-08-11, a CallsJoined row
        # carrying session/members/host/port -- fields from a codex no current
        # file contains, written by a build nobody could identify:
        #
        #   SD:CallsJoined.user:*Dave
        #     {"host":null,"members":null,"port":null,"session":null,"ts":...}
        #
        # Whatever the cause, the WRITER can see it before the row leaves: it
        # knows what it offered and it can read what converged. Checked here so
        # a mismatch names itself at the moment it happens, on whatever version
        # is running, instead of being inferred days later from an empty lobby.
        # [SAY_WHAT_WROTE_THIS_ROW_V1] First write of each var, unconditionally,
        # on the console -- print, not LOG, because fnav's publisher configures
        # no logging and a diagnostic nobody sees is not one.
        #
        # A CallsAnnounce row was observed carrying MediaHold's field names with
        # "ts": null. _publish stamps ts on EVERY write, so that row did not come
        # from _publish -- and nothing else here writes that var. Which file, and
        # which codex, is therefore the question, and the answer has to travel
        # with the row rather than be reconstructed afterwards.
        if var not in _SAID_WRITER:
            _SAID_WRITER.add(var)
            import hashlib as _h
            def _fp(mod):
                try:
                    p = getattr(mod, "__file__", "?")
                    raw = open(p, "rb").read().replace(b"\r\n", b"\n")
                    return "%s=%s" % (p, _h.sha256(raw).hexdigest()[:8])
                except Exception as e:
                    return "%s=UNREADABLE(%s)" % (
                        getattr(mod, "__file__", "?"), type(e).__name__)
            import substrate as _sub
            print("[WRITER] %s scope=%s\n"
                  "         codex.path=%s\n"
                  "         codex.field_order=%s\n"
                  "         offering=%s\n"
                  "         comms_control %s\n"
                  "         substrate     %s"
                  % (var, scope,
                     getattr(codex, "semantic_path", "?"),
                     list(getattr(codex, "field_order", [])),
                     sorted(set(value) | {"ts"}),
                     _fp(sys.modules.get(__name__)), _fp(_sub)), flush=True)

        kind = self._publish(ch, var, scope)

        # The reference is built by flush(), which happens inside _publish -- so
        # this has to run AFTER. Checked before, it read an empty reference and
        # reported every write as broken; a guard that always fires is a guard
        # nobody reads.
        ref = dict(getattr(ch, "reference", None) or {})
        offered = set(value) | {"ts"}
        missing = sorted(k for k in offered if ref.get(k) is None)
        extra = sorted(set(ref) - offered)
        if missing or extra:
            print("[WRITER] %s %s WROTE A ROW THAT DOES NOT MATCH WHAT WAS OFFERED. "
                      "offered=%s converged=%s missing/None=%s undeclared "
                      "extra=%s -- the codex and the caller disagree; the row "
                  "in the store is unusable"
                  % (var, scope, sorted(offered), sorted(ref), missing, extra),
                  flush=True)
        return True

    def _resolve_media(self) -> Tuple[str, int]:
        """The media host for a call the LOCAL node originates, as an ADDRESS.

        [MEDIAHOST_FROM_MEMORY_V1] The media host is an ELECTION RESULT and lives in
        FrogNet Memory: frognet_avhost.reevaluate() scores the LAN's candidates at the
        end of a converged runMerge and puts {address, port} into the communicator
        `avhost` tuple on databasehost_control.frognet. That tuple is the authority.
        This reads it. Nothing else.

        It used to resolve the NAME mediahost.frognet out of /etc/hosts. That is a
        DIFFERENT source: the hosts file is written by commit_service_lines only once
        the role barrier is ready, so between a float and the next successful commit
        the file names the previous winner -- or, on a node whose barrier never
        cleared, a box that was never elected at all. Measured: New-York-1's hosts
        file mapped mediahost.frognet to New-York-1 itself while the elected media
        host was elsewhere, so two senders never met and neither end erred.

        Election data is read from databasehost_control.frognet, never from the
        elected data host. Both planes are consulted in a call -- control for WHO
        hosts the media, data for the call tuple itself -- and they are not
        interchangeable.

        [NO_MEDIAHOST_NO_CALL_V1] There is no fallback. If memory does not name a
        media host there is no media host, and a call that cannot be hosted is not
        started. Falling back to a second source is how the two ends of a call end up
        pointed at two different relays, each waiting for someone who is never going
        to arrive, with nothing logged anywhere.

        An explicit relay (--relay / ControlPlane(relay=...)) pins one and skips
        resolution entirely. That is an operator overriding the election on purpose,
        not a fallback.
        """
        if self.relay:
            return self.relay[0], int(self.relay[1])
        try:
            import frognet_avhost as _AV
        except Exception as e:
            raise NoMediaHost(
                "frognet_avhost is not importable (%s) -- the media-host election "
                "cannot be read, so there is no media host to name"
                % (type(e).__name__,))
        try:
            win = _AV.resolve_host(dbhost=CONTROL_DBHOST)
        except Exception as e:
            # A control plane that cannot answer is NOT a pond with no media host.
            # Say which it is: an unreachable store and an empty election look
            # identical to the caller otherwise.
            raise NoMediaHost(
                "could not read the media-host election from %s (%s: %s)"
                % (CONTROL_DBHOST, type(e).__name__, e))
        if not win or not win[0]:
            raise NoMediaHost(
                "no media host is elected on %s -- the `avhost` tuple is empty. "
                "Run `frognet_avhost.py reeval` on a node, and check that a node is "
                "registering the mediahost capability "
                "(frognet_register_candidate.sh mediahost)." % (CONTROL_DBHOST,))
        return (win[0], int(win[1] or MEDIA_PORT))

    # ---- CHAT ---------------------------------------------------------------
    def send_chat(self, session: str, text: str) -> str:
        """Chat is LOSSLESS_EVENTUAL: each line must land, so each message persists under its
        OWN scope (lines must not overwrite each other). The per-session channel still drives
        FULL/DIFF/SAME convergence of the line's fields."""
        ch = self._chat_ch.setdefault(session, Channel(_chat_codex()))
        ch.offer("from_id", self.me_id)
        ch.offer("from_name", self.me_name)
        ch.offer("text", text)
        ch.offer("ts", int(time.time() * 1000))      # ms: distinct ordering key per line
        msg_id = uuid.uuid4().hex[:10]
        return self._publish(ch, "chat", _chat_scope(f"{session}:{msg_id}"))

    def read_chat(self, session: str, fresh_s: int = 3600) -> List[Dict[str, Any]]:
        prefix = _chat_scope(session) + ":"          # session:<id>:  (per-message scopes)
        msgs = []
        for r in T.get(SERVICE, "chat", dbhost=self.dbhost, fresh_s=fresh_s):
            if not str(r.get("scope", "")).startswith(prefix):
                continue
            v = r.get("value", {})
            if v.get("text"):
                msgs.append({"from": v.get("from_name", v.get("from_id", "?")),
                             "text": v["text"], "ts": v.get("ts", 0)})
        return sorted(msgs, key=lambda m: m["ts"])

    # ---- MEDIA BACKPRESSURE (read from memory, written by the relay) --------
    def media_holds(self, fresh_s: int = 15) -> List[Dict[str, Any]]:
        """[MEDIAHOLD_IS_MEMORY_V1] What the media host is holding, and why.

        The relay publishes one MediaHold per viewer address: whether that viewer is
        waiting for a keyframe, how many bytes of keyframe it is sitting on, how many
        inter frames it has shed, the cap in force, and a NAMED reason. Nothing else
        in the system can see any of that -- a sender's own uplink is clean while the
        far end watches stills, so every screen it owns reads perfect health.

        This is a read of memory, not a subscription: a Communicator that started
        after the backlog began still sees it, and two of them see the same thing.

        fresh_s bounds it. A relay that stopped publishing has gone, and its last
        word about a viewer is not news about the present.
        """
        out = []
        for r in T.get(SERVICE, "MediaHold", dbhost=self.dbhost, fresh_s=fresh_s):
            v = r.get("value", {}) or {}
            if not v.get("addr"):
                continue
            out.append({"addr": v["addr"],
                        "name": v.get("name", ""),
                        "awaiting_keyframe": bool(v.get("awaiting_keyframe")),
                        "held_keyframe_bytes": int(v.get("held_keyframe_bytes", 0)),
                        "inter_frames_shed": int(v.get("inter_frames_shed", 0)),
                        "cap_bps": int(v.get("cap_bps", 0)),
                        "frames_aimed": int(v.get("frames_aimed", 0)),
                        "frames_delivered": int(v.get("frames_delivered", 0)),
                        "reason": v.get("reason", "")})
        return sorted(out, key=lambda h: h["addr"])

    # ---- LINK CONDITIONS (shared across the call) ---------------------------
    def set_link(self, session: str, bps: int, jitter_ms: int,
                 preset: str = "") -> bool:
        """Write the call's link condition. Every participant reads this row and
        applies it to its own uplink, so one person adjusting the link adjusts the
        call.

        A plain put. There is no channel and no FULL/DIFF/SAME here: convergence is
        the DISCOVERY mechanism, for state that is re-asserted continuously and must
        stay cheap on the wire. This is somebody pressing a button, occasionally.
        Wrapping it in a codec would buy nothing and would claim a relationship to
        discovery that it does not have.
        """
        return T.put(SERVICE, "link", _link_scope(session),
                     {"session": session, "bps": int(bps),
                      "jitter_ms": int(jitter_ms), "preset": preset or "",
                      "set_by": self.me_id, "set_by_name": self.me_name,
                      "ts": int(time.time())},
                     dbhost=self.dbhost, own=False)

    def set_media_speed(self, session: str, bps: int) -> bool:
        """[MEDIASPEED_V1] Publish MY artificial link cap for this call.

        set_link() writes the call's link condition, which every participant then
        applies TO ITS OWN UPLINK -- a sender can only throttle the leg it owns.
        That leaves the other half of a client's link, host -> me, unconstrained:
        the media host has no idea this client is meant to be slow.

        A client's link is symmetric, so one number covers both legs. This row is
        what the media host reads to cap the downlink it serves to me. Scoped per
        (session, viewer) because the constraint belongs to a CLIENT, not to a
        call -- and a viewer is in one call at a time, so the two coincide.

        bps == 0 means unrestricted, and the host creates no bucket at all.
        """
        return T.put(SERVICE, "MediaSpeed",
                     "%s:viewer:%s" % (_call_scope(session), self.me_id),
                     {"session": session, "viewer": self.me_id,
                      "bps": int(bps or 0), "addr": T.my_ip(),
                      "ts": int(time.time())},
                     dbhost=self.dbhost, own=False)

    def clear_stale_media_speeds(self) -> int:
        """[MEDIASPEED_V1] Drop every MediaSpeed row this ADDRESS left behind.

        Every launch mints a new me_id, so a client that has been restarted a few
        times leaves a trail of MediaSpeed rows -- all with the same addr, all
        still readable. The media host keys its caps by address, so those rows
        collapse onto one key and whichever the store returns last wins: a stale
        setting from a previous run silently overrides the current one, and
        moving the slider appears to do nothing.

        Cleared at startup, scoped to THIS host's own address so it is non-racy
        across nodes. Returns how many went.
        """
        ip = T.my_ip()
        if not ip.startswith("10."):
            return 0
        n = 0
        for row in T._values_raw(SERVICE, self.dbhost):
            nm = row.get("SensorName", "")
            if not nm.startswith("%sMediaSpeed." % T.SD_PREFIX):
                continue
            data = row.get("data") or {}
            if data.get("addr") != ip or row.get("SensorID") is None:
                continue
            if T._delete_by_id(self.dbhost, row["SensorID"]):
                n += 1
        if n:
            print("[MediaSpeed] cleared %d stale row(s) for %s" % (n, ip), flush=True)
        return n

    # -- MediaTreatment ------------------------------------------------------
    # [MEDIA_TREATMENT_IS_MEMORY_V1] The producer's desired encoder state, held
    # in the space rather than sent as a message.
    #
    # The bag is DYNAMIC and OPAQUE to this layer. It speaks the producer's own
    # vocabulary -- w, h, gray, bitrate, fps -- not ffmpeg's, because the encoder
    # behind it is PyAV today and may not be tomorrow. Only the producer and the
    # observer that writes for it need agree on the keys; anything this layer
    # does not recognise is carried through untouched rather than dropped, so a
    # newer observer can hand a newer producer fields this code has never heard
    # of and neither has to be redeployed.
    #
    # The one field that says this IS a treatment row is a non-empty `treat`
    # object. Same discipline as [LINK_ROW_MUST_BE_A_LINK_ROW_V1]: a row of the
    # wrong shape that happens to land in the right scope must not be read as an
    # instruction, and an absent instruction must not be manufactured from
    # defaults. No treat object => malformed => named and skipped.
    _TREATMENT_REQUIRED = ("treat",)

    def set_media_treatment(self, session: str, producer: str,
                            treat: Dict[str, Any], viewer: str = "",
                            set_by: str = "") -> bool:
        """Write the desired encoder state for `producer` in `session`.

        Written by whoever is watching the wire -- the consumer that is drowning,
        the media host, or the sender's own controller. `producer` is the party
        whose encoder this describes, not the party writing the row.

        `treat` is passed through verbatim. This layer neither validates nor
        completes it: a partial bag is a partial instruction, and the producer
        applies the keys it understands over whatever it is already running.
        """
        if not isinstance(treat, dict) or not treat:
            raise ValueError(
                "set_media_treatment: treat must be a non-empty dict; "
                "an empty bag is not 'no opinion', it is a malformed row")
        return T.put(SERVICE, "MediaTreatment",
                     _treatment_scope(session, producer,
                                      viewer or self.me_id),
                     {"session": session, "producer": producer,
                      "viewer": viewer or self.me_id,
                      "treat": dict(treat),
                      "set_by": set_by, "addr": T.my_ip(),
                      "ts": int(time.time())},
                     dbhost=self.dbhost, own=False)

    def read_media_treatment(self, session: str, producer: str,
                             fresh_s: int = CALL_FRESH_S
                             ) -> Optional[Dict[str, Any]]:
        """The desired encoder state for `producer`, or None if nobody has set one.

        None means NO STANDING INSTRUCTION and leaves the producer on whatever it
        is running. It does not mean "unconstrained" and must never be turned
        into a bag of defaults -- that is the [LINK_ROW_MUST_BE_A_LINK_ROW_V1]
        failure, where a defaulted field read as a real setting.

        Freshest row wins: every launch mints a new writer, so a producer can
        accumulate rows from observers that have since gone away. They age out by
        ts, and until they do the newest is the current instruction.
        """
        return pick_media_treatment(
            T.get(SERVICE, "MediaTreatment", dbhost=self.dbhost,
                  fresh_s=fresh_s),
            session, producer)

    # [LINK_ROW_MUST_BE_A_LINK_ROW_V1] The one field that says this IS a link
    # row. Anything else in the payload is optional; without this the row is not
    # a link setting and must not be read as one.
    _LINK_REQUIRED = ("bps",)

    def read_link(self, session: str,
                  fresh_s: int = CALL_FRESH_S) -> Optional[Dict[str, Any]]:
        """The call's current link condition, or None if nobody has set one.

        [LINK_ROW_MUST_BE_A_LINK_ROW_V1] A row was accepted as a link row on the
        strength of its `session` field alone, and every other field was then
        defaulted. `bps` defaulted to 0 -- and 0 is not "unknown" here, it is
        UNLIMITED. So a row of the wrong shape that happened to carry the right
        session read as "the user set this call to unlimited", which drives the
        slider to its 2000 stop and lifts the ladder ceiling to L7.

        Measured, 2026-08-08: a call-shaped payload
            {'host': None, 'members': None, 'port': None, 'session': '...'}
        was sitting under scope session:link:<session> with var='link'. It has no
        bps key. Every poll read it as unlimited and put the slider back to full,
        which looked exactly like the control being ignored.

        A missing required field is a MALFORMED ROW, not a value. It is named and
        skipped -- the caller gets None, which already means "nobody has set one"
        and leaves the local setting alone.
        """
        want = _link_scope(session)
        for r in T.get(SERVICE, "link", dbhost=self.dbhost, fresh_s=fresh_s):
            if str(r.get("scope", "")) != want:
                continue
            v = r.get("value", {})
            if not isinstance(v, dict):
                print("[LINK] MALFORMED row at scope %s: value is %s, not an "
                      "object - skipped" % (want, type(v).__name__), flush=True)
                continue
            if v.get("session") != session:
                continue
            missing = [k for k in self._LINK_REQUIRED if v.get(k) is None]
            if missing:
                # Do NOT default these. See the docstring: the default is
                # indistinguishable from a real setting and means the opposite
                # of "I do not know".
                print("[LINK] MALFORMED row at scope %s from addr=%s ts=%s: no %s "
                      "- skipped, NOT read as unlimited (keys present: %s)"
                      % (want, r.get("addr", "?"), v.get("ts", "?"),
                         ", ".join(missing), ", ".join(sorted(v)) or "none"),
                      flush=True)
                continue
            return {"session": session, "bps": int(v.get("bps") or 0),
                    "jitter_ms": int(v.get("jitter_ms", 0) or 0),
                    "preset": v.get("preset", ""),
                    "set_by": v.get("set_by", ""),
                    "set_by_name": v.get("set_by_name", ""),
                    "ts": int(v.get("ts", 0) or 0)}
        return None

    def clear_foreign_links(self, session: str) -> int:
        """[LINK_ROW_MUST_BE_A_LINK_ROW_V1] Delete rows in this session's link
        scope that are not link rows.

        The counterpart of clear_stale_media_speeds, for the same disease on the
        other tuple: a row of the wrong shape sitting in the right scope, read on
        every poll, overriding the live setting. read_link now refuses to read
        them, so this is housekeeping rather than a fix -- but a malformed row
        left in place is a malformed row that the NEXT reader may not be as
        careful about.

        Returns how many went.
        """
        want = _link_scope(session)
        n = 0
        for row in T._values_raw(SERVICE, self.dbhost):
            nm = row.get("SensorName", "")
            if not nm.startswith("%slink." % T.SD_PREFIX):
                continue
            if not nm.endswith(want):
                continue
            data = row.get("data") or {}
            if isinstance(data, dict) and data.get("bps") is not None:
                continue                      # a real link row; leave it
            if row.get("SensorID") is None:
                continue
            if T._delete_by_id(self.dbhost, row["SensorID"]):
                n += 1
                print("[LINK] deleted malformed row %s (keys: %s)"
                      % (nm, ", ".join(sorted(data)) if isinstance(data, dict)
                         else type(data).__name__), flush=True)
        return n

    # ---- TRANSCRIPT (what was said and typed, as shared memory) -------------
    def say(self, session: str, text: str, kind: str = "typed",
            speaker: str = "") -> bool:
        """Put a line of the conversation into the call's shared memory.

        `kind` is how the line arrived: "typed" (somebody wrote it) or "heard"
        (somebody's box transcribed incoming speech). One line per scope, because a
        line must not overwrite the one before it -- the same reason chat writes one
        scope per message.

        The transcript is not a message stream. Nobody is sent anything: the line is
        written into memory the call shares, and every participant -- including one
        who joins ten minutes late -- reads the same conversation from the same
        place. That is the whole point of doing it this way rather than fanning text
        out over the media socket, where a latecomer would get nothing and a node
        that blinked would lose the line permanently.
        """
        line_id = uuid.uuid4().hex[:10]
        return T.put(SERVICE, "transcript", _transcript_scope(session, line_id),
                     {"session": session, "text": text, "kind": kind,
                      "from_id": self.me_id,
                      "from_name": speaker or self.me_name,
                      "ts": int(time.time() * 1000)},
                     dbhost=self.dbhost, own=False)

    def read_transcript(self, session: str,
                        fresh_s: int = 3600) -> List[Dict[str, Any]]:
        """The whole conversation for this session, oldest first."""
        prefix = T.session_scope(f"transcript:{session}") + ":"
        out = []
        for r in T.get(SERVICE, "transcript", dbhost=self.dbhost, fresh_s=fresh_s):
            if not str(r.get("scope", "")).startswith(prefix):
                continue
            v = r.get("value", {})
            if v.get("text"):
                out.append({"from": v.get("from_name", v.get("from_id", "?")),
                            "from_id": v.get("from_id", ""),
                            "text": v["text"], "kind": v.get("kind", "typed"),
                            "ts": int(v.get("ts", 0) or 0)})
        return sorted(out, key=lambda m: m["ts"])

    # ---- SETTINGS (device + stream choices, as shared memory) ---------------
    def save_settings(self, **fields) -> bool:
        """Save this user's settings. The tuple holds ONE field: a JSON bag.

        Not a column per setting. A schema spelled out in the tuple's fields has to be
        edited here every time the application learns a new preference, so the control
        plane ends up knowing about camera indices, which is not its business. A bag
        means comms_control stores settings without knowing what a setting is.

        The write is the write. There is no read-modify-write, no merge arbitration,
        no keep-alive that reconciles against what is already there. If two writers
        race, one of them loses and the loser writes again next time somebody presses
        something. Nothing in FrogNet adapts by guarding a write -- it adapts by
        moving up or down the ladder, and that is the only adaptive mechanism there
        is. Machinery that exists to protect a write is machinery that has to be
        right, and it is not in the ladder's path.
        """
        self._settings.update({k: v for k, v in fields.items() if v is not None})
        return self._write_bag()

    def refresh_settings(self) -> None:
        """Re-write the bag so it does not age out of a transient store."""
        if self._settings:
            self._write_bag()

    def _write_bag(self) -> bool:
        return T.put(SERVICE, "settings", _settings_scope(self.me_id),
                     {"id": self.me_id, "ts": int(time.time()),
                      "settings": json.dumps(self._settings, sort_keys=True)},
                     dbhost=self.dbhost, own=False)

    def load_settings(self, fresh_s: int = None) -> Optional[Dict[str, Any]]:
        """Read the bag and HOLD it, so later partial saves write a whole bag.

        A client that has not loaded and then saves one field writes a bag with one
        field in it, and the rest is gone. That is not a bug to be defended against
        in the write path -- it is what happens when you write. The answer is for the
        client to know its own settings, which means loading them once at startup,
        deliberately and visibly, rather than having every save quietly turn into a
        read-modify-write.
        """
        bag = self.read_settings(fresh_s)
        if bag:
            self._settings = dict(bag)
        return bag

    def read_settings(self, fresh_s: int = None) -> Optional[Dict[str, Any]]:
        """Everything this user saved on this node, whatever it happens to be."""
        want = _settings_scope(self.me_id)
        fresh_s = SETTINGS_FRESH_S if fresh_s is None else fresh_s
        for r in T.get(SERVICE, "settings", dbhost=self.dbhost, fresh_s=fresh_s):
            if str(r.get("scope", "")) != want:
                continue
            v = r.get("value", {})
            if v.get("id") != self.me_id:
                continue
            return json.loads(v.get("settings") or "{}")
        return None

    # ---- host change: converged references go stale ------------------------
    def host_reset(self) -> None:
        """Media/db host moved -> drop held references so the next publish re-establishes a
        FULL frame instead of a DIFF against a reference the new host never saw."""
        self._presence_ch.hostReset()
        for ch in self._call_ch.values(): ch.hostReset()
        for ch in self._chat_ch.values(): ch.hostReset()

    # ---- the converge-then-persist step (the one place memory hits the store) ----
    def _publish(self, ch: Channel, var: str, scope: str) -> str:
        # [DIAG-PRESENCE-V1] see the note in the body; this wrapper exists only
        # to log every input and output of the write without touching its logic.
        """Flush the channel (FULL/DIFF/SAME per UnREST). On SAME, the held reference already
        matches -- nothing changed -- so we still refresh the tuple's ts (presence/call must
        stay fresh) but write the full converged value, not a partial. Returns last kind."""
        frames = ch.flush()
        last_kind = "same"
        for _frame, kind in frames:
            last_kind = kind
        # persist the converged reference (the authoritative current value) as the tuple
        value = dict(ch.reference or {})
        value["ts"] = int(time.time())

        # [DIAG-PRESENCE-V1] One-shot root cause for "the row never refreshes".
        # Everything the write depends on, logged before and after, because the
        # suspected cause is what might be wrong:
        #   me_id      - if this changes between calls, every announce INSERTS a
        #                new row instead of updating one, and no row's UpdatedAt
        #                ever advances. The Sensor table shows george-3df4,
        #                -3684, -d5d5, -9c44, -6d7a, -3528, -91ef, -12d7, -ff7c:
        #                nine rows for one client.
        #   scope/name - the exact upsert key. upsert_by_name matches on this.
        #   dbhost     - which plane. Presence is application data and belongs
        #                on the elected host, not _control.
        #   resolved   - what that name resolves to RIGHT NOW on this box.
        #   ok         - put()'s return, which this function currently discards,
        #                so a failing write is invisible.
        if not _DIAG:
            T.put(SERVICE, var, scope, value, dbhost=self.dbhost, own=False)
            return last_kind
        import socket as _s
        _name = T.var_name(var, scope)
        try:
            _res = _s.gethostbyname(self.dbhost)
        except Exception as _e:
            _res = "RESOLVE_FAIL:%r" % (_e,)
        print("[DIAG-PRESENCE] pre  me_id=%r var=%r scope=%r name=%r dbhost=%r "
              "resolved=%s kind=%s ts=%s value_keys=%s"
              % (getattr(self, "me_id", None), var, scope, _name, self.dbhost,
                 _res, last_kind, value.get("ts"), sorted(value.keys())),
              flush=True)
        _t0 = time.time()
        ok = T.put(SERVICE, var, scope, value, dbhost=self.dbhost, own=False)
        print("[DIAG-PRESENCE] post name=%r ok=%s dur_ms=%.1f"
              % (_name, ok, (time.time() - _t0) * 1000.0), flush=True)
        return last_kind
