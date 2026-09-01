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
comms_ui.py -- Communicator presentation MODEL. Pure logic, no tkinter, no cv2.

The old shell had one fixed layout that never changed: roster, calls, stats, video
stage, demo sliders and chat were all on screen at once whether you were idle or in
a call, and the controls silently no-op'd when there was no call to apply them to.
There was no navigation because there were no states.

This module holds the states and everything derived from them, so the Tk file is
only paint. Everything here is deterministic and oracle-drivable headless:

  Screen        -- LOBBY / RINGING / CALL / ENDED, and the transitions between them
  RingTracker   -- which incoming calls should ring, and what a decline means
  LinkPreset    -- the demo link conditions, as named instruments with a PREDICTED rung
  ladder_cells  -- the L7..L0 SotF ladder as renderable cells (the demo's headline)
  call_label    -- a call described by WHO is on it, not by a hex session fragment

Nothing here imports the media stack; sotf_ladder is policy (a pure table) and is the
one dependency, because the ladder is what the whole demonstration is about.

ASCII only. This runs on Windows consoles too, and cp1252 has already cost this
project a debugging session.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import sotf_ladder as L


# -- screens ------------------------------------------------------------------
# A screen is a MODE, not a window. The Tk shell shows/hides chrome per mode; the
# transitions below are the only legal ways to move between them.
LOBBY = "lobby"       # not in a call: the flock, open calls, chat, preflight
RINGING = "ringing"   # someone has invited me and I have not answered
CALL = "call"         # fnav leg is up: ladder headline, tiles, link instrument
ENDED = "ended"       # the leg finished (hangup or drop); says why, offers the way back

SCREENS = (LOBBY, RINGING, CALL, ENDED)


class Screen:
    """The Communicator's mode, and the only transitions that may change it.

    Every transition returns the new mode, so a caller can compare and repaint only
    on change. Illegal transitions are no-ops rather than exceptions: this drives a
    UI, and a stray event (a late tuple, a double-click on Hang up) must not take
    the window down. `reason` carries the human sentence the ENDED screen shows --
    the old shell dropped this on the floor and left the user staring at an idle
    window with no explanation.
    """

    def __init__(self):
        self.mode = LOBBY
        self.session: Optional[str] = None      # session we are in, or being rung by
        self.caller: Optional[str] = None       # display name of whoever is ringing
        self.reason: str = ""                   # why the last call ended
        self.host: Optional[str] = None         # relay we are pointed at, for display
        self.port: Optional[int] = None

    # -- outbound -------------------------------------------------------------
    def dialing(self, session: str) -> str:
        """We originated a call. Goes straight to CALL: the originator is not rung."""
        if self.mode in (LOBBY, ENDED):
            self.mode, self.session, self.reason = CALL, session, ""
        return self.mode

    # -- inbound --------------------------------------------------------------
    def ring(self, session: str, caller: str) -> str:
        """An invite arrived. Only rings from LOBBY/ENDED -- never interrupts a call."""
        if self.mode in (LOBBY, ENDED):
            self.mode, self.session, self.caller = RINGING, session, caller
        return self.mode

    def accept(self) -> str:
        if self.mode == RINGING:
            self.mode, self.caller, self.reason = CALL, None, ""
        return self.mode

    def decline(self) -> str:
        if self.mode == RINGING:
            self.mode, self.session, self.caller = LOBBY, None, None
        return self.mode

    # -- in call --------------------------------------------------------------
    def connected(self, host: str, port: int) -> str:
        self.host, self.port = host, port
        return self.mode

    def hangup(self) -> str:
        if self.mode == CALL:
            self.mode, self.reason = ENDED, "You hung up."
            self.session = None
        return self.mode

    def dropped(self, reason: str = "The connection dropped.") -> str:
        """The fnav leg died on its own (relay reset, peer gone). fnav does not
        reconnect, so this is terminal for the leg -- say so plainly."""
        if self.mode in (CALL, RINGING):
            self.mode, self.reason = ENDED, reason
            self.session = None
        return self.mode

    def back_to_lobby(self) -> str:
        if self.mode == ENDED:
            self.mode, self.reason = LOBBY, ""
        return self.mode

    # -- queries the shell paints from ---------------------------------------
    @property
    def in_call(self) -> bool:
        return self.mode == CALL

    def shows(self, panel: str) -> bool:
        """Which chrome belongs on screen in this mode. This is the navigation: the
        window is a different instrument in each mode instead of one crowded board."""
        return panel in SCREEN_PANELS.get(self.mode, ())


# Panels per mode. `stage` is the video area; `link` is the demo instrument; the
# lobby owns discovery (flock/calls/preflight) and the call owns the ladder headline.
SCREEN_PANELS: Dict[str, Sequence[str]] = {
    LOBBY:   ("flock", "calls", "preflight", "chat", "link"),
    RINGING: ("flock", "calls", "chat", "ring"),
    CALL:    ("stage", "ladder", "link", "stats", "chat", "incall_controls"),
    ENDED:   ("flock", "calls", "chat", "ended", "link"),
}


# -- ring tracking ------------------------------------------------------------
class RingTracker:
    """Decides which visible calls should ring me, and remembers what I answered.

    Two things the old shell got wrong, both fixed here:

      1. It marked a session as rung BEFORE showing the popup and never cleared it
         on decline, so declining a call meant it could never ring again for the
         life of that session -- the caller could redial forever in silence.
      2. Decline was a pure UI dismiss: the caller was told nothing.

    Here, declining forgets the session so a fresh invite rings again. The shell
    pairs that with ControlPlane.leave_call(session), which rewrites the call tuple
    without me -- an existing API, no new protocol. Because `should_ring` requires
    me in `members`, dropping out is what stops the re-ring; being re-added is what
    restarts it. Declining is therefore visible to the caller as membership, which
    is the honest UnREST way to say no: change the shared memory, don't send a
    message about it.
    """

    def __init__(self):
        self.rung: Set[str] = set()

    def should_ring(self, invitations: Iterable[Dict[str, Any]],
                    me_id: str, busy: bool) -> List[Dict[str, Any]]:
        """Live invitations naming me that I have not already been rung for.

        [MEMBERSHIP_IS_SELF_ASSERTED_V1] This took the CALL rows and rang on
        finding my own id in a members list. Membership was asserted by whoever
        wrote last, on everyone's behalf, so a node that remembered me from a
        call I had already left put me back into it on its next heartbeat -- and
        this rule could not tell that from an invitation, because they were the
        same row. Measured 2026-08-10: leave a call, return to the lobby, and be
        rung within seconds by a peer that cannot address anyone in particular.

        An invitation is now its own fact with a maker, renewed by the inviter
        and retired when they stop. Reading it needs no [NO_SELF_INVITE_V1]
        guard: invite() will not address me, so a row naming me came from
        somebody else by construction.

        `busy` (already in a call, or already ringing) suppresses everything.
        """
        live = {i.get("session") for i in invitations if i.get("session")}
        self.rung &= live                       # a call that ended can ring again
        if busy:
            return []
        out = []
        for i in invitations:
            sid = i.get("session")
            if not sid or sid in self.rung:
                continue
            if i.get("invitee", me_id) != me_id or not i.get("from"):
                continue
            self.rung.add(sid)
            out.append(i)
        return out

    def declined(self, session: str) -> None:
        """Forget it, so a later invite to the same session rings again."""
        self.rung.discard(session)

    def answered(self, session: str) -> None:
        self.rung.add(session)


# -- naming -------------------------------------------------------------------
def display_names(roster: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    return {p.get("id"): p.get("name") or p.get("id") for p in roster if p.get("id")}


def call_label(call: Dict[str, Any], names: Dict[str, str], me_id: str,
               max_names: int = 2) -> str:
    """Describe a call by WHO is on it. The old shell printed `sid[:8]` and a count
    -- 'a3f91c02 . 2' -- which tells an audience nothing and tells a user less."""
    members = [m for m in (call.get("members") or []) if m != me_id]
    if not members:
        # [AN_ANNOUNCED_CALL_IS_SOMEWHERE_TO_GO_V1] An announced call with
        # nobody on it yet is an OFFER, not a leftover. "empty call" read as
        # something broken and made the row look unjoinable; it is the normal
        # state between announcing and the first join.
        host = call.get("host") or ""
        return "open call%s" % (" on %s" % host if host else "")
    shown = [names.get(m, m) for m in members[:max_names]]
    label = ", ".join(shown)
    extra = len(members) - len(shown)
    if extra > 0:
        label += " +%d" % extra
    return label


def call_is_mine(call: Dict[str, Any], me_id: str) -> bool:
    return me_id in (call.get("members") or [])


# -- the demo link instrument -------------------------------------------------
class LinkPreset:
    """One named link condition. `kbps` 0 means unthrottled.

    Presets exist because a slider is unusable in front of a room: you cannot hit a
    number while talking, and the audience cannot read where you put it. A named
    button you can press without looking, that announces what it is about to do, is
    the difference between a demo and a fumble.
    """

    def __init__(self, name: str, kbps: int, jitter_ms: int, blurb: str):
        self.name, self.kbps, self.jitter_ms, self.blurb = name, kbps, jitter_ms, blurb

    @property
    def bps(self) -> int:
        return 0 if self.kbps <= 0 else self.kbps * 1000

    def __repr__(self):
        return "LinkPreset(%r, %d kbps, %d ms)" % (self.name, self.kbps, self.jitter_ms)


# Budgets are read against fnav.RUNG_VIDEO (L7 1.2M / L6 600k / L5 300k), so the
# PREDICTED rung below is derived from the real ladder table, not guessed.
PRESETS: List[LinkPreset] = [
    LinkPreset("Open water", 0, 0, "no limit -- the fabric as it is"),
    LinkPreset("Home DSL", 700, 20, "a normal household uplink"),
    LinkPreset("Congested", 320, 60, "a shared link under load"),
    LinkPreset("Contested", 110, 180, "a bad radio link -- voice survives"),
]


def expected_rung(bps: int, rung_video: Dict[int, Dict[str, Any]],
                  ceiling_idx: int = L.MAX_IDX) -> int:
    """The rung this uplink budget can carry, from the REAL per-rung bitrates.

    Used to say what is about to happen BEFORE it happens ('this drops us to DUET
    360p') so the audience watches a prediction come true instead of watching a
    number change and being told afterwards it meant something. If no video rung
    fits the budget, video sheds and we land on the top audio rung the ceiling
    allows -- which is exactly what fnav's bearer does under sustained drops.
    """
    if bps <= 0:
        return ceiling_idx
    for idx in sorted(rung_video, reverse=True):
        if idx <= ceiling_idx and rung_video[idx].get("bitrate", 0) <= bps:
            return idx
    return min(ceiling_idx, 4)          # L4 SOLO: audio only, video shed


# -- the ladder, as renderable cells -----------------------------------------
ACTIVE, AVAILABLE, SHED, BLOCKED = "active", "available", "shed", "blocked"


def ladder_cells(send_idx: Optional[int], ceiling_idx: int,
                 allowed: Optional[Set[int]] = None,
                 low: int = 3, high: int = L.MAX_IDX) -> List[Dict[str, Any]]:
    """The ladder as cells to paint, richest first.

    This is the headline of the whole demonstration and the old shell rendered it as
    a 13px line of monospace text in a 240px sidebar. As cells it can be a full-width
    track that a room can read from the back:

      active    -- the rung we are sending right now
      shed      -- above the current rung: capability we have but the wire will not carry
      available -- at or below the current rung, still within the ceiling
      blocked   -- above the ceiling: this machine cannot source it (no camera, --no-audio)

    The floor (L0/L1) is always available by ladder invariant, which is the point
    worth saying out loud: the call does not die, the medium changes.
    """
    cells = []
    for idx in range(high, low - 1, -1):
        lv = L.BY_IDX[idx]
        if idx > ceiling_idx or (allowed is not None and idx not in allowed
                                 and idx not in L.ALWAYS):
            state = BLOCKED
        elif send_idx is None:
            state = AVAILABLE
        elif idx == send_idx:
            state = ACTIVE
        elif idx > send_idx:
            state = SHED
        else:
            state = AVAILABLE
        cells.append({"idx": idx, "code": lv["code"], "name": lv["name"],
                      "kind": lv["kind"], "state": state})
    return cells


def rung_caption(send_idx: Optional[int], rung_video: Dict[int, Dict[str, Any]]) -> str:
    """One short line the room can read: what we are sending, in plain terms."""
    if send_idx is None:
        return "not connected"
    lv = L.BY_IDX.get(send_idx)
    if lv is None:
        return "L%s" % send_idx
    if send_idx in rung_video:
        p = rung_video[send_idx]
        gray = " grayscale" if p.get("gray") else ""
        return "%s %s -- %dx%d%s video + audio" % (lv["code"], lv["name"],
                                                   p["w"], p["h"], gray)
    if lv["kind"] == L.AUDIO:
        return "%s %s -- audio only, video shed" % (lv["code"], lv["name"])
    if lv["kind"] == L.TEXT:
        return "%s %s -- text floor" % (lv["code"], lv["name"])
    return "%s %s -- presence only" % (lv["code"], lv["name"])


# -- deferred controls --------------------------------------------------------
class PendingControls:
    """Controls set before a call exists, applied when one starts.

    In the old shell every knob guarded on `if self.call is not None`, so moving a
    slider in the lobby updated its label and did nothing else. The label said
    '700 kbps' and the wire was wide open. At demo time that is a silent failure
    with a confident readout -- the worst kind. Here the setting is always recorded,
    always reflected, and applied either immediately (in a call) or on entry.
    """

    # [ASPECT_IS_CHOSEN_V1] Frame shape ABOVE L5, operator-selected, widescreen
    # by default. Below L5 the ladder goes 4:3 regardless.
    ASPECTS = ("16:9", "4:3")

    def __init__(self, codec: str = "auto", quality: str = "Auto",
                 aspect: str = "16:9",
                 bps: int = 0, jitter_ms: int = 0):
        # [CODEC_DEFAULT_IS_AUTO_V1] "vp8" was the default, and _apply_pending stamps
        # this over the Call's codec_id immediately after construction -- so the
        # auto-probe would select H.264/libx264 as the best encoder the box can run,
        # announce it, and then be overwritten by software VP8 before the first frame.
        # VP8 software at 720p is several times the CPU of x264: measured on hardware,
        # 5-19 fps where the box should manage its full rate. A default that names a
        # codec is a decision nobody made; "auto" means the probe stands.
        self.codec, self.quality = codec, quality
        self.aspect = aspect if aspect in self.ASPECTS else self.ASPECTS[0]
        self.bps, self.jitter_ms = bps, jitter_ms

    # [BULLFROG_V1] Auto follows MAX_IDX; the named caps are explicit rungs and
    # a new top rung needs its own entry or it is unreachable from the menu.
    QUALITY_CAP = {"Auto": L.MAX_IDX, "1080p": 8, "720p": 7,
                   "480p": 6, "360p": 5}

    def cap(self) -> int:
        return self.QUALITY_CAP.get(self.quality, L.MAX_IDX)

    def armed(self) -> bool:
        """True if anything is set away from wide-open -- the shell badges this so a
        throttle armed in the lobby cannot be forgotten and blamed on the network."""
        return bool(self.bps) or bool(self.jitter_ms) or self.quality != "Auto"

    def summary(self) -> str:
        if not self.armed():
            return "link open, quality auto"
        bits = []
        if self.bps:
            bits.append("%d kbps" % (self.bps // 1000))
        if self.jitter_ms:
            bits.append("%d ms jitter" % self.jitter_ms)
        if self.quality != "Auto":
            bits.append("capped %s" % self.quality)
        return ", ".join(bits)


# -- preflight ----------------------------------------------------------------
def ceiling_from_devices(have_cam: bool, have_mic: bool,
                         no_video: bool = False, no_audio: bool = False) -> int:
    return L.ceiling(have_cam, have_mic, no_video, no_audio)


def preflight_verdict(have_cam: bool, have_mic: bool,
                      no_video: bool = False, no_audio: bool = False) -> str:
    """Plain sentence about what this box can source, shown BEFORE dialling.

    The most likely demo failure is a wrong camera index, and the old shell's only
    remedy was to quit and relaunch from a terminal with a different --cam. Saying
    the ceiling out loud in the lobby turns that into something you notice while
    nobody is watching.
    """
    c = ceiling_from_devices(have_cam, have_mic, no_video, no_audio)
    lv = L.BY_IDX[c]
    if c >= 5:
        return "camera + mic ready -- can source %s %s" % (lv["code"], lv["name"])
    if c >= 3:
        return "audio only -- no camera, ceiling %s %s" % (lv["code"], lv["name"])
    return "text floor only -- no mic, ceiling %s %s" % (lv["code"], lv["name"])


# -- shared link conditions ---------------------------------------------------
class SharedLink:
    """The call's link condition, as read from the one tuple that holds it.

    There is nothing to arbitrate here. The link is a single row on the elected data
    host; every participant reads that row and applies it to its own uplink. Whoever
    wrote last wrote last -- the store decided that, not this class. This is only a
    record of what we have already applied, so the shell can tell a changed value
    from an unchanged one and avoid re-pushing the same numbers at fnav on every
    poll, and so the badge can name whoever set it.

    What crosses the wire is the SETTING. The shaping stays local, because a sender
    can only throttle the uplink it owns.
    """

    def __init__(self, me_id: str):
        self.me_id = me_id
        self.bps = 0
        self.jitter_ms = 0
        self.preset = ""
        self.set_by = ""
        self.set_by_name = ""

    def set_local(self, bps: int, jitter_ms: int, preset: str) -> None:
        """Record what we just published, so reading it back is not a change."""
        self.bps, self.jitter_ms, self.preset = int(bps), int(jitter_ms), preset or ""
        self.set_by, self.set_by_name = self.me_id, ""

    def take(self, remote: Optional[Dict[str, Any]]) -> bool:
        """Adopt the tuple. True if the numbers differ from what we have applied."""
        if not remote:
            return False
        bps = int(remote.get("bps", 0) or 0)
        jit = int(remote.get("jitter_ms", 0) or 0)
        changed = (bps != self.bps or jit != self.jitter_ms)
        self.bps, self.jitter_ms = bps, jit
        self.preset = remote.get("preset", "") or ""
        self.set_by = remote.get("set_by", "")
        self.set_by_name = remote.get("set_by_name", "") or self.set_by
        return changed

    def attribution(self) -> str:
        if not self.set_by or self.set_by == self.me_id:
            return ""
        return "set by %s" % (self.set_by_name or self.set_by)


# -- bearer hysteresis, mirrored from fnav.Bearer.sample ----------------------
# Read from fnav.py:617-631, not remembered. The controller reacts to a sustained
# trend, never one sample, and caps how often it may move:
#   stressed   = backlog >= 8 or dropped > 0   (SotF keeps no queue, so: dropped > 0)
#   step DOWN  after 3 consecutive stressed samples
#   step UP    after 5 consecutive clean samples
#   rate cap   at most one step per 1.0 s, in either direction
# The asymmetry IS the hysteresis: it retreats on 3 and advances on 5, so a link
# that is flapping settles low rather than oscillating through the user's picture.
BEARER_BAD_STEPS = 3
BEARER_GOOD_STEPS = 5
BEARER_STEP_CAP_S = 1.0


class LinkHistory:
    """Bounded time series behind the diagnostics plot.

    Sampled off the same 80ms UI tick that paints the tiles, so it costs nothing
    extra and cannot drift from what the window is showing. Fixed capacity: a
    diagnostic window left open for an hour must not grow without bound.
    """

    # [DIAG_IS_UNGATED_V1] The diagnostics record what the wire did, per sample, with
    # nothing filtered, substituted or suppressed. AUDIO is here because below L5 it is
    # the ONLY thing on the wire: a history carrying video counters alone goes flat at
    # L4 and the screen reads as "stopped" while a call is running perfectly.
    #
    # v_kbps / v_drops  video only, zero when video is shed -- and a real zero is a
    #                   reading, not a gap. It is recorded and it is drawn.
    # a_kbps            audio bytes on the wire (WireStats already computes it; nothing
    #                   read it before).
    # a_sheds           audio frames the wire REFUSED, as a per-sample delta off the
    #                   cumulative SotFDataPlane.audio_sheds counter. Below L5 this is
    #                   the only congestion signal there is. Read non-destructively:
    #                   take_audio_sheds() CLEARS the pending count the bearer steers
    #                   on, so the diagnostics must never call it.
    FIELDS = ("rung", "bearer", "kbps", "drops", "throttle_kbps", "bad", "good",
              "a_kbps", "a_sheds")

    def __init__(self, capacity: int = 900):        # ~72 s at one sample / 80 ms
        self.capacity = capacity
        self.t: List[float] = []
        self.rows: List[Dict[str, Any]] = []

    def add(self, now: float, **kw) -> None:
        self.t.append(now)
        self.rows.append({k: kw.get(k) for k in self.FIELDS})
        if len(self.t) > self.capacity:
            drop = len(self.t) - self.capacity
            del self.t[:drop]
            del self.rows[:drop]

    def __len__(self):
        return len(self.t)

    def window(self, seconds: float, now: Optional[float] = None):
        """(times, rows) within the last `seconds`."""
        if not self.t:
            return [], []
        end = self.t[-1] if now is None else now
        start = end - seconds
        i = 0
        for i in range(len(self.t)):
            if self.t[i] >= start:
                break
        return self.t[i:], self.rows[i:]

    def series(self, field: str, seconds: float = 60.0):
        ts, rows = self.window(seconds)
        return ts, [r.get(field) for r in rows]

    def step_events(self, seconds: float = 60.0) -> List[Dict[str, Any]]:
        """Every rung change in the window: when it happened and which way."""
        ts, rows = self.window(seconds)
        out = []
        prev = None
        for t, r in zip(ts, rows):
            cur = r.get("rung")
            if cur is None:
                continue
            if prev is not None and cur != prev:
                out.append({"t": t, "from": prev, "to": cur,
                            "dir": "down" if cur < prev else "up"})
            prev = cur
        return out

    def hysteresis_lag(self, seconds: float = 60.0) -> Dict[str, Any]:
        """How long the controller waited before each move -- the number the graph
        exists to show. For a step DOWN: seconds from the start of the CURRENT run of
        stressed samples to the step. For a step UP: seconds from the last stressed
        sample to the step.

        The run of stressed samples restarts at every rung change, not only when the
        drops stop. Without that reset, a second step down during one long congestion
        episode is measured from the onset of the whole episode, so the readout drifts
        upward the longer the link stays bad -- it reported a 9.12 s "lag before
        stepping down" on a controller whose rate cap is 1.0 s, which is not a lag at
        all but the age of the congestion. After a step the controller starts counting
        again from zero, so the measurement has to as well.
        """
        ts, rows = self.window(seconds)
        downs, ups = [], []
        first_stressed = None
        last_stressed = None
        prev = None
        for t, r in zip(ts, rows):
            drops = r.get("drops") or 0
            if drops > 0:
                if first_stressed is None:
                    first_stressed = t
                last_stressed = t
            else:
                first_stressed = None
            cur = r.get("rung")
            if cur is not None and prev is not None and cur != prev:
                if cur < prev and first_stressed is not None:
                    downs.append(t - first_stressed)
                elif cur > prev and last_stressed is not None:
                    ups.append(t - last_stressed)
                # The controller resets its counters on a step, so both clocks we
                # measure against reset too -- in BOTH directions. Fixing this only
                # for the down path left the up path drifting the same way: three
                # steps climbing back out of one clean period read 8.2 / 9.2 / 10.2 s
                # because every one of them was still measured from the same last
                # drop, instead of each from the step before it.
                first_stressed = t if drops > 0 else None
                last_stressed = t
            if cur is not None:
                prev = cur
        return {"down_lags": downs, "up_lags": ups,
                "down_mean": (sum(downs) / len(downs)) if downs else None,
                "up_mean": (sum(ups) / len(ups)) if ups else None}


# -- ungated readouts ---------------------------------------------------------
# [DIAG_IS_UNGATED_V1] A readout must distinguish "nothing was measured" from "the
# measurement was nothing". `%s` on a .get(k, 0) collapses the two, and every screen
# in this program was doing it. num() renders a measured value -- INCLUDING zero --
# and renders absence as MISSING, which is short enough to sit in a legend and
# obviously not a number.
MISSING = "--"


def num(v, fmt="%g"):
    """A measured value, or MISSING. Zero is a measurement and prints as 0."""
    if v is None:
        return MISSING
    try:
        return fmt % (v,)
    except (TypeError, ValueError):
        return str(v)


def hold_legend(holds):
    """[MEDIAHOLD_IS_MEMORY_V1] One line naming what the media host is holding.

    Empty list means the relay published nothing -- which is NOT the same as "no
    backlog" and must not read as it. No relay, no answer.
    """
    if holds is None:
        return "backlog " + MISSING + " (no MediaHold in memory)"
    backed = [h for h in holds
              if h.get("awaiting_keyframe") or h.get("held_keyframe_bytes")]
    if not backed:
        return "backlog none  (%d viewer%s reporting)" % (
            len(holds), "" if len(holds) == 1 else "s")
    bits = []
    for h in backed:
        who = h.get("name") or h.get("addr", "?")
        _aim, _got = h.get("frames_aimed"), h.get("frames_delivered")
        _rate = ("  %s/%s frames delivered" % (num(_got), num(_aim))
                 if _aim else "")
        bits.append("%s: %s held %sB, %s shed%s  [%s]"
                    % (who,
                       "awaiting keyframe" if h.get("awaiting_keyframe") else "holding",
                       num(h.get("held_keyframe_bytes")),
                       num(h.get("inter_frames_shed")),
                       _rate,
                       h.get("reason") or "?"))
    return "backlog  " + "   ".join(bits)


def wire_legend(s):
    """The one-line console legend: what is on the wire right now.

    Video AND audio, SEND AND RECEIVE. It carried outbound video only, so on an
    audio-only rung -- which is a working call -- every figure on it read zero and
    the screen looked dead, and at no rung at all could it say how fast anything was
    ARRIVING. The fields come straight from WireStats.snapshot(); a key the roll has
    not filled yet is absent, not zero, and prints as MISSING.
    """
    s = s or {}
    return ("send %s fps  %s KB/s video  %s KB/s audio   "
            "recv %s fps  %s KB/s video  %s KB/s audio%s   drops %s/s"
            % (num(s.get("v_fps_sent")), num(s.get("v_kbps")),
               num(s.get("a_kbps")),
               num(s.get("v_fps_recv")), num(s.get("v_rx_kbps")),
               num(s.get("a_rx_kbps")),
               rx_size_legend(s.get("rx_dims")),
               num(s.get("v_drop_fps"))))


def rx_size_legend(dims):
    """[RX_DIMS_ARE_MEASURED_V1] The size of the picture actually ARRIVING, per
    sender.

    The legend carried rates and drops for both directions but never the
    incoming geometry, so a far end that had walked down to 360p was
    indistinguishable on screen from one holding 720p -- the KB/s moves, but the
    KB/s also moves when the scene gets simpler, and nothing said which.

    None means the decoder was not asked or did not answer; that is absent data
    and prints as MISSING, not as "no video". {} means it answered and is
    holding nothing, which is a different fact and says so.
    """
    if dims is None:
        return "  size --"
    if not dims:
        return "  size (none)"
    return "  size " + " ".join("%s %dx%d" % (src, w, h)
                                for src, (w, h) in sorted(dims.items()))


def plot_points(ts: Sequence[float], vals: Sequence[Optional[float]],
                x0: int, y0: int, w: int, h: int,
                vmin: float, vmax: float,
                t0: Optional[float] = None, t1: Optional[float] = None):
    """Map a series onto canvas pixels. Pure arithmetic so the plot geometry is
    oracle-checkable without a display: y grows downward, None values break the line
    into segments rather than being drawn as zero (a shed rung is absent data, not a
    reading of nought)."""
    if not ts:
        return []
    t0 = ts[0] if t0 is None else t0
    t1 = ts[-1] if t1 is None else t1
    span = (t1 - t0) or 1.0
    rng = (vmax - vmin) or 1.0
    segs: List[List[tuple]] = []
    cur: List[tuple] = []
    for t, v in zip(ts, vals):
        if v is None:
            if cur:
                segs.append(cur)
                cur = []
            continue
        x = x0 + (t - t0) / span * w
        y = y0 + h - (max(vmin, min(vmax, float(v))) - vmin) / rng * h
        cur.append((x, y))
    if cur:
        segs.append(cur)
    return segs


# -- device selection ---------------------------------------------------------
class DeviceChoice:
    """One selectable device, already labelled for a list box."""

    def __init__(self, index, name, detail="", is_default=False, usable=True):
        self.index, self.name, self.detail = index, name, detail
        self.is_default, self.usable = is_default, usable

    def label(self) -> str:
        bits = [self.name]
        if self.detail:
            bits.append("(%s)" % self.detail)
        if self.is_default:
            bits.append("- system default")
        return " ".join(bits)

    def __repr__(self):
        return "DeviceChoice(%r, %r)" % (self.index, self.name)


def rank_cameras(probes: Sequence[Dict[str, Any]]) -> List[DeviceChoice]:
    """Turn raw cv2 probe results into a chooser list.

    `probes` are dicts {index, ok, w, h} from opening each cv2 index, which is the
    same way a call opens the camera -- fnav._list_cameras is explicit that cv2
    indices are authoritative and /dev/video* is not a reliable map, because one
    physical camera can expose several nodes. It also states the rule applied here:
    where a camera appears at more than one index, prefer the LOWEST index that
    reports a resolution. Unusable indices are still listed, greyed, so a user who
    expected a camera at index 2 can see it was found and rejected rather than
    wondering whether we looked.
    """
    out: List[DeviceChoice] = []
    best = None
    for p in sorted(probes, key=lambda q: q.get("index", 0)):
        ok = bool(p.get("ok"))
        w, h = int(p.get("w") or 0), int(p.get("h") or 0)
        detail = ("%dx%d" % (w, h)) if (ok and w and h) else (
            "opened, no frame" if p.get("opened") else "did not open")
        usable = ok and w > 0 and h > 0
        if usable and best is None:
            best = p.get("index")
        out.append(DeviceChoice(p.get("index"), "Camera %s" % p.get("index"),
                                detail, is_default=(p.get("index") == best),
                                usable=usable))
    return out


def audio_choices(devices: Sequence[Dict[str, Any]], kind: str,
                  default_index: Optional[int] = None) -> List[DeviceChoice]:
    """Filter a PortAudio device table down to inputs or outputs.

    sounddevice reports every host API's view of every device, so the raw list is
    long and full of entries with no channels in the direction you want. Selecting
    on max_input_channels / max_output_channels is what makes it a usable list.
    """
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    out = []
    for i, d in enumerate(devices):
        if int(d.get(key, 0) or 0) <= 0:
            continue
        rate = int(round(float(d.get("default_samplerate", 0) or 0)))
        detail = "%d ch" % int(d.get(key)) + (", %d Hz" % rate if rate else "")
        out.append(DeviceChoice(i, str(d.get("name", "device %d" % i)), detail,
                                is_default=(i == default_index)))
    return out


# -- microphone level meter ---------------------------------------------------
METER_FLOOR_DB = -60.0          # below this the bar reads empty
QUIET_DB, HOT_DB = -40.0, -3.0  # too quiet to hear; close enough to clip


def rms_to_db(rms: float) -> float:
    """Linear RMS (0..1) to dBFS, floored so silence is a number and not -inf."""
    if rms <= 0:
        return METER_FLOOR_DB
    import math
    return max(METER_FLOOR_DB, 20.0 * math.log10(min(1.0, rms)))


def meter_fraction(rms: float) -> float:
    """0..1 bar fill. Mapped in dB, not linearly: a linear bar spends almost its
    whole length in the top few dB and looks dead for normal speech, which is
    exactly the confusion a level meter exists to remove."""
    db = rms_to_db(rms)
    return max(0.0, min(1.0, (db - METER_FLOOR_DB) / (0.0 - METER_FLOOR_DB)))


def meter_state(rms: float) -> str:
    """'silent' | 'quiet' | 'good' | 'hot' -- what to tell the user in words."""
    db = rms_to_db(rms)
    if db <= METER_FLOOR_DB + 1.0:
        return "silent"
    if db < QUIET_DB:
        return "quiet"
    if db >= HOT_DB:
        return "hot"
    return "good"


def meter_advice(state: str) -> str:
    return {"silent": "no signal -- is this the right microphone?",
            "quiet": "very quiet -- try speaking up or another input",
            "good": "sounds good",
            "hot": "close to clipping -- move back or lower the gain"}.get(state, "")


# -- saved settings vs devices actually present -------------------------------
def resolve_saved(saved: Optional[Dict[str, Any]],
                  cameras: Sequence[DeviceChoice],
                  inputs: Sequence[DeviceChoice],
                  outputs: Sequence[DeviceChoice]) -> Dict[str, Any]:
    """Reconcile stored settings against the hardware that is here NOW.

    Settings live in the tuple plane and outlive the box's USB arrangement: a webcam
    is unplugged, a headset is on a different index this boot, PortAudio renumbers.
    A saved index that no longer resolves must fall back to a working default AND
    say so, rather than silently selecting nothing and failing at call time -- which
    is the failure the whole preflight screen exists to prevent. Audio is matched by
    NAME first and index second, because names survive renumbering and indices do
    not.
    """
    notes: List[str] = []
    saved = saved or {}

    def pick_cam():
        want = saved.get("cam")
        usable = [c for c in cameras if c.usable]
        if want is not None:
            for c in usable:
                if c.index == int(want):
                    return c.index
            if usable:
                notes.append("saved camera %s is not here; using camera %s"
                             % (want, usable[0].index))
        return usable[0].index if usable else None

    def pick_audio(kind, rows):
        want_name = saved.get("%s_name" % ("in" if kind == "input" else "out"))
        want_idx = saved.get("in_dev" if kind == "input" else "out_dev")
        if want_name:
            for r in rows:
                if r.name == want_name:
                    return r.index
        if want_idx is not None:
            for r in rows:
                if r.index == int(want_idx):
                    notes.append("saved %s matched by index, not name" % kind)
                    return r.index
        if want_name or want_idx is not None:
            notes.append("saved %s device is not here; using the default" % kind)
        for r in rows:
            if r.is_default:
                return r.index
        return rows[0].index if rows else None

    return {"cam": pick_cam(),
            "in_dev": pick_audio("input", inputs),
            "out_dev": pick_audio("output", outputs),
            "codec": saved.get("codec") or "vp8",
            "quality": saved.get("quality") or "Auto",
            "notes": notes}
