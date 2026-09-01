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
frognet_monitor/draw_dashboard.py — Dashboard rendering.

Status logic:
  Echo transits the full semantic path (proxy → daemon → remote daemon → Apache).
  Echo OK = ONLINE.  Echo FAIL = DOWN.  No separate daemon probe.
"""

import curses
import time

from .config import (CP_GREEN, CP_RED, CP_YELLOW, CP_CYAN, CP_DIM,
                     CP_HEADER, CP_TITLE, CP_SELECT,
                     MODE_DASHBOARD, MODE_SENSORS, MODE_JSON)
from .formatting import fmt_bytes, fmt_bps, fmt_ratio, safe_get, num
from .identity import LOCAL_DOMAIN, LOCAL_IP
from .merge import merge_state, fmt_since
from .ui_helpers import safe_addstr


def draw_title(win, row: int) -> int:
    ts = time.strftime("%H:%M:%S")
    bar = "═" * 78
    safe_addstr(win, row, 1, bar, curses.color_pair(CP_TITLE) | curses.A_BOLD)
    row += 1
    safe_addstr(win, row, 1,
                f"  FrogNet v5.0   {LOCAL_DOMAIN} ({LOCAL_IP})   {ts}",
                curses.color_pair(CP_TITLE) | curses.A_BOLD)

    # [MERGE_IS_VISIBLE_V1] On the TITLE line, not in the footer with the help
    # text. A merge degrades this node while it runs, so every latency and
    # hit-rate figure below is suspect for its duration -- the warning belongs
    # where the eye lands before it reads the numbers, not underneath them.
    #
    # Yellow, matching [DBHOST_IS_MARKED_V1]: this screen's green/red axis is
    # health, and "a merge is running" is neither a fault nor an all-clear.
    _merge, _since, _note = merge_state()
    if _merge:
        _m = "  ⟳ MERGE IN PROGRESS (%s)" % fmt_since(_since)
        safe_addstr(win, row, 46, _m[:32],
                    curses.color_pair(CP_YELLOW) | curses.A_BOLD)
    elif _merge is None:
        # Unknown is not the same as clear. Say which.
        safe_addstr(win, row, 46, "  ⟳ MERGE STATE UNKNOWN"[:32],
                    curses.color_pair(CP_RED) | curses.A_BOLD)
    row += 1
    safe_addstr(win, row, 1, bar, curses.color_pair(CP_TITLE) | curses.A_BOLD)
    return row + 2


def draw_peer_header(win, row: int) -> int:
    # Column positions — defined once, used everywhere
    #   2    15    23    30    37    44   49     58     68
    safe_addstr(win, row, 2, "PEER", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 15, "STATUS", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 23, "RTT", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 30, "P50", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 37, "P95", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 44, "HIT", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 49, "SAVED", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 58, "EFF", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    safe_addstr(win, row, 68, "ACTUAL", curses.color_pair(CP_HEADER) | curses.A_BOLD)
    row += 1
    safe_addstr(win, row, 2, "─" * 76, curses.color_pair(CP_DIM))
    row += 1
    # [DBHOST_IS_MARKED_V1] Say what the marker means, on the screen, in the
    # colour it uses. A highlight nobody can decode is decoration.
    safe_addstr(win, row, 2, "*", curses.color_pair(CP_YELLOW) | curses.A_BOLD)
    safe_addstr(win, row, 4, "= databasehost", curses.color_pair(CP_DIM))
    return row + 1


# Column positions — shared by header, peer rows, local row
_C = dict(NAME=2, STAT=15, RTT=23, P50=30, P95=37,
          HIT=44, SAVED=49, EFF=58, ACT=68)
_ROW_WIDTH = 76


def _fmt_rtt(ms):
    """Format an RTT value and pick its color."""
    if ms > 0:
        text = f"{ms:.0f}ms"
        cp = CP_GREEN if ms < 100 else (CP_YELLOW if ms < 1000 else CP_RED)
    else:
        text, cp = "—", CP_DIM
    return text, cp


def format_peer_cols(name, echo, rtt_ms, p50_ms, p95_ms,
                     hit_rate, bytes_saved, eff_bps, actual_bps,
                     link_quality_peer=None):
    """Return (col_texts, col_colors) for a peer row.

    [LINK_POTENTIAL_PING_V1 2026-05-25] STATUS column upgraded from
    binary ONLINE/DOWN to a 6-state indicator that uses the
    SemanticProxy.LinkQuality saturation_ratio when available.

    State decision order (first match wins):

      echo FAIL                               → DOWN     (red)
      no link_quality_peer dict               → ONLINE   (green, legacy)
      sample_count == 0 in current window     → IDLE     (dim)
      success_rate < 0.8                      → FLAKY    (red)
      saturation_ratio_p95 >= 4.0             → SLOW     (yellow, bright)
      saturation_ratio_p95 >= 1.5             → DEGRADED (yellow, dim)
      saturation_ratio_p95 is None
        (no baseline yet) and p95_ms >= 1000  → DEGRADED (absolute fallback)
      else                                    → ONLINE   (green)

    The ratio-based thresholds (1.5, 4.0) compare *observed* RPC RTT
    to the connect-time-measured potential.  A 10Mbit link at its
    capacity has the SAME ratio as a 1Gbit link at its capacity —
    both look saturated.  That's the whole point: thresholds are
    per-peer and learned, not fleet-wide and arbitrary.

    The absolute-fallback (1000ms) only fires when the proxy has
    talked to a peer but the ladder probe never completed
    successfully — e.g. brand new connection that hasn't seen its
    first RPC, or persistent ladder failure.  In that case we still
    color a clearly-slow link yellow so it's visible to the operator.
    """
    e_status, _ = echo

    # 1. DOWN — echo failed, peer is not on the network at all.
    if e_status != "OK":
        s_text, s_cp = "DOWN", CP_RED
    elif not isinstance(link_quality_peer, dict):
        # 2. No link-quality data — proxy hasn't emitted yet, or this
        # is a peer we don't have a record for.  Default to ONLINE
        # since echo OK is enough to know the host is reachable.
        s_text, s_cp = "ONLINE", CP_GREEN
    else:
        lq = link_quality_peer
        sample_count = lq.get("sample_count", 0) or 0
        success_rate = lq.get("success_rate", 0.0) or 0.0
        sat_p95 = lq.get("saturation_ratio_p95")  # may be None
        if sample_count == 0:
            # 3. IDLE — proxy hasn't talked to this peer recently.
            # Not unhealthy; just quiet.  Use DIM to differentiate
            # from a "definitely fine" green and from problems.
            s_text, s_cp = "IDLE", CP_DIM
        elif success_rate < 0.8:
            # 4. FLAKY — RPCs are timing out or being reset.  This
            # is the most-actionable red state for an operator.
            s_text, s_cp = "FLAKY", CP_RED
        elif sat_p95 is not None and sat_p95 >= 4.0:
            # 5. SLOW — observed RTT is 4x+ what the link should
            # achieve.  Heavy queueing; usable but laggy.
            s_text, s_cp = "SLOW", CP_YELLOW
        elif sat_p95 is not None and sat_p95 >= 1.5:
            # 6. DEGRADED — noticeable extra latency, link is loaded
            # but still working well enough.
            s_text, s_cp = "DEGRADED", CP_YELLOW
        elif sat_p95 is None and (p95_ms or 0) >= 1000:
            # 7. DEGRADED via absolute fallback — no baseline yet
            # but p95 is clearly slow.  Less precise than the
            # ratio-based decision but still informative.
            s_text, s_cp = "DEGRADED", CP_YELLOW
        else:
            s_text, s_cp = "ONLINE", CP_GREEN

    rtt_text, rtt_cp = _fmt_rtt(rtt_ms)
    p50_text, p50_cp = _fmt_rtt(p50_ms)
    p95_text, p95_cp = _fmt_rtt(p95_ms)

    # Hit rate
    if hit_rate > 0:
        hit_text = f"{hit_rate * 100:.0f}%"
        hit_cp = CP_GREEN if hit_rate > 0.5 else (CP_YELLOW if hit_rate > 0.1 else CP_RED)
    else:
        hit_text, hit_cp = "—", CP_DIM

    # Bytes saved
    if bytes_saved > 0:
        saved_text = fmt_bytes(bytes_saved)
        saved_cp = CP_GREEN
    else:
        saved_text, saved_cp = "—", CP_DIM

    # Effective throughput
    eff_text = fmt_bps(eff_bps)
    eff_cp = CP_GREEN if eff_bps > 0 else CP_DIM

    # Actual throughput
    act_text = fmt_bps(actual_bps)
    act_cp = CP_CYAN if actual_bps > 0 else CP_DIM

    return (
        (name, s_text, rtt_text, p50_text, p95_text,
         hit_text, saved_text, eff_text, act_text),
        (0, s_cp, rtt_cp, p50_cp, p95_cp,
         hit_cp, saved_cp, eff_cp, act_cp),
    )


def _draw_cols(win, row, cols, colors, selected, is_local=False,
               is_dbhost=False, is_control=False):
    """Shared column renderer for peer and local rows.

    [DBHOST_IS_MARKED_V1] is_dbhost paints the NAME column YELLOW and marks it
    with a leading '*'. Deliberately NOT green or red: those two are the health
    axis on this screen (up / down) and reusing either would make "this is the
    databasehost" indistinguishable from "this peer is fine" or "this peer is
    broken". Role is a different axis from health and needs a different colour.

    It also outranks the cyan local marker. If this box IS the databasehost,
    which role it holds is the more consequential of the two facts -- "that is
    me" is already obvious from the name.

    The marker is a character as well as a colour, so it survives a mono
    terminal, a screenshot, and anyone who cannot separate the two hues.

    [CONTROL_IS_MARKED_V1] The control host is an INDEPENDENT role: a node can
    hold both, either, or neither, and every election reads its candidate data
    from the control host rather than from the floating data host. So this is not
    a precedence question -- both facts have to be on the row at once.

    The NAME field is 13 columns and the name itself takes 11, which leaves the
    same two-character marker slot the dbhost mark already lives in. Rather than
    move every column right, the two roles share one glyph:

        '*'  databasehost only          (floats, highest IP)
        '+'  control host only          (deterministic, does not float)
        '#'  both roles on one node

    Colour follows the same rule as above -- yellow is the role axis, and a node
    holding either role gets it, because the operator's question is "which box
    is carrying a role" before it is "which role"."""
    (name, s_text, rtt_text, p50_text, p95_text,
     hit_text, saved_text, eff_text, act_text) = cols
    (_, s_cp, rtt_cp, p50_cp, p95_cp,
     hit_cp, saved_cp, eff_cp, act_cp) = colors
    C = _C
    # [CONTROL_IS_MARKED_V1] One glyph carries both roles; see the docstring.
    _glyph = ("#" if (is_dbhost and is_control)
              else "*" if is_dbhost
              else "+" if is_control
              else " ")

    if selected:
        max_y, max_x = win.getmaxyx()
        safe_addstr(win, row, 1, " " * min(_ROW_WIDTH, max_x - 2),
                    curses.color_pair(CP_SELECT))
        sc = curses.color_pair(CP_SELECT)
        # [DBHOST_IS_MARKED_V1] The select bar owns the colour, so the role has
        # to survive as text: '▸*' rather than '▸ '. A selected databasehost was
        # otherwise indistinguishable from any other selected row.
        safe_addstr(win, row, C['NAME'],
                    f"▸{_glyph}{name:<11}",
                    sc | curses.A_BOLD)
        safe_addstr(win, row, C['STAT'], f"{s_text:<8}",   sc)
        safe_addstr(win, row, C['RTT'],  f"{rtt_text:<7}",  sc)
        safe_addstr(win, row, C['P50'],  f"{p50_text:<7}",  sc)
        safe_addstr(win, row, C['P95'],  f"{p95_text:<7}",  sc)
        safe_addstr(win, row, C['HIT'],  f"{hit_text:<5}",  sc)
        safe_addstr(win, row, C['SAVED'], f"{saved_text:<9}", sc)
        safe_addstr(win, row, C['EFF'],  f"{eff_text:<10}", sc)
        safe_addstr(win, row, C['ACT'],  f"{act_text:<9}",  sc)
    else:
        # [DBHOST_IS_MARKED_V1] Role before locality; see the docstring.
        if is_dbhost or is_control:
            name_cp = curses.color_pair(CP_YELLOW) | curses.A_BOLD
        elif is_local:
            name_cp = curses.color_pair(CP_CYAN)
        else:
            name_cp = curses.A_NORMAL
        _mark = f"{_glyph} "
        safe_addstr(win, row, C['NAME'], f"{_mark}{name:<11}",  name_cp)
        safe_addstr(win, row, C['STAT'], f"{s_text:<8}",
                    curses.color_pair(s_cp) | curses.A_BOLD)
        safe_addstr(win, row, C['RTT'],  f"{rtt_text:<7}",  curses.color_pair(rtt_cp))
        safe_addstr(win, row, C['P50'],  f"{p50_text:<7}",  curses.color_pair(p50_cp))
        safe_addstr(win, row, C['P95'],  f"{p95_text:<7}",  curses.color_pair(p95_cp))
        safe_addstr(win, row, C['HIT'],  f"{hit_text:<5}",  curses.color_pair(hit_cp))
        safe_addstr(win, row, C['SAVED'], f"{saved_text:<9}", curses.color_pair(saved_cp))
        safe_addstr(win, row, C['EFF'],  f"{eff_text:<10}", curses.color_pair(eff_cp))
        safe_addstr(win, row, C['ACT'],  f"{act_text:<9}",  curses.color_pair(act_cp))
    return row + 1


def draw_peer_row(win, row, cols, colors, selected=False, is_dbhost=False,
                  is_control=False):
    """Draw a single peer row."""
    return _draw_cols(win, row, cols, colors, selected, is_local=False,
                      is_dbhost=is_dbhost, is_control=is_control)


def draw_local_row(win, row, name, engine=None, selected=False,
                   is_dbhost=False, is_control=False):
    """Draw the local node row with aggregate engine stats.

    [DBHOST_IS_MARKED_V1] This box can BE the databasehost, and that is exactly
    the case where the operator most needs to know -- every other node in the
    pond is reading and writing through this machine.

    [CONTROL_IS_MARKED_V1] Likewise for the control host, and for the same
    reason: every election on the pond reads its candidates from it."""
    hit_text, saved_text = "—", "—"
    p50_text, p95_text = "—", "—"
    eff_text, act_text = "—", "—"
    hit_cp, saved_cp = CP_DIM, CP_DIM
    p50_cp, p95_cp = CP_DIM, CP_DIM
    eff_cp, act_cp = CP_DIM, CP_DIM

    if engine and isinstance(engine, dict):
        hr = num(engine.get("same_rate", 0))
        bs = num(engine.get("bytes_saved", 0))
        if hr > 0:
            hit_text = f"{hr * 100:.0f}%"
            hit_cp = CP_GREEN if hr > 0.5 else CP_YELLOW
        if bs > 0:
            saved_text = fmt_bytes(bs)
            saved_cp = CP_GREEN
        rtt = engine.get("rtt", {})
        if isinstance(rtt, dict):
            p50 = num(rtt.get("p50_ms", 0))
            p95 = num(rtt.get("p95_ms", 0))
            if p50 > 0:
                p50_text = f"{p50:.0f}ms"
                p50_cp = CP_GREEN if p50 < 100 else (CP_YELLOW if p50 < 1000 else CP_RED)
            if p95 > 0:
                p95_text = f"{p95:.0f}ms"
                p95_cp = CP_GREEN if p95 < 100 else (CP_YELLOW if p95 < 1000 else CP_RED)
        et = engine.get("effective_throughput", {})
        if isinstance(et, dict):
            eb = num(et.get("effective_bps", 0))
            ab = num(et.get("actual_bps", 0))
            if eb > 0:
                eff_text = fmt_bps(eb)
                eff_cp = CP_GREEN
            if ab > 0:
                act_text = fmt_bps(ab)
                act_cp = CP_CYAN

    cols = (name, "(local)", "—", p50_text, p95_text,
            hit_text, saved_text, eff_text, act_text)
    colors = (0, CP_DIM, CP_DIM, p50_cp, p95_cp,
              hit_cp, saved_cp, eff_cp, act_cp)
    return _draw_cols(win, row, cols, colors, selected, is_local=True,
                      is_dbhost=is_dbhost, is_control=is_control)


def draw_internet(win, row, result):
    s, ms = result
    safe_addstr(win, row, 2, f"  {'Internet':<12}", curses.A_NORMAL)
    if s == "OK":
        safe_addstr(win, row, 17, f"{'OK':<10}", curses.color_pair(CP_GREEN))
        safe_addstr(win, row, 27, f"{ms}ms", curses.color_pair(CP_GREEN))
    else:
        safe_addstr(win, row, 17, f"{'FAIL':<10}", curses.color_pair(CP_RED))
    return row + 1


def draw_engine(win, row, engine, daemon):
    """Draw the Semantic Engine panel — the at-a-glance view."""
    row += 1
    safe_addstr(win, row, 2, "Semantic Engine", curses.A_BOLD)
    row += 1
    safe_addstr(win, row, 2, "─" * 76, curses.color_pair(CP_DIM))
    row += 1

    if not engine or not isinstance(engine, dict):
        safe_addstr(win, row, 2, "No engine data yet", curses.color_pair(CP_DIM))
        return row + 1

    # --- Headlines ---
    comp = num(engine.get("compression_ratio", 0))
    amp = num(engine.get("amplification", 0))
    same = num(engine.get("same_rate", 0))
    tpl = num(engine.get("template_coverage", 0))
    err = num(engine.get("error_rate", 0))
    lru = num(engine.get("lru_hit_rate", 0))

    comp_cp = CP_GREEN if comp > 0.5 else (CP_YELLOW if comp > 0 else CP_DIM)
    safe_addstr(win, row, 2, "Compress:  ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 13, fmt_ratio(comp), curses.color_pair(comp_cp) | curses.A_BOLD)
    safe_addstr(win, row, 21, f" ({amp:.1f}x)", curses.color_pair(comp_cp))
    safe_addstr(win, row, 32, "SAME rate: ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 43, fmt_ratio(same), curses.color_pair(CP_GREEN if same > 0.5 else CP_DIM))
    safe_addstr(win, row, 52, "Templates: ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 63, fmt_ratio(tpl), curses.color_pair(CP_GREEN if tpl > 0.9 else CP_YELLOW))
    row += 1

    # Error + LRU + Coalescing on one line
    err_cp = CP_GREEN if err < 0.02 else (CP_YELLOW if err < 0.1 else CP_RED)
    safe_addstr(win, row, 2, "Errors:    ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 13, fmt_ratio(err), curses.color_pair(err_cp))
    miss_n = int(num(engine.get("miss_count", 0)))
    err_n = int(num(engine.get("error_count", 0)))
    safe_addstr(win, row, 21, f"({miss_n} miss + {err_n} err)", curses.color_pair(CP_DIM))

    safe_addstr(win, row, 43, "LRU: ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 48, fmt_ratio(lru), curses.color_pair(CP_GREEN if lru > 0.8 else CP_YELLOW))

    coal = int(num(engine.get("coalesce_hits", 0)))
    if coal > 0:
        safe_addstr(win, row, 57, f"Coalesced: {coal}", curses.color_pair(CP_CYAN))
    row += 2

    # --- Wire bytes ---
    bw = num(engine.get("bytes_would", 0))
    ba = num(engine.get("bytes_actual", 0))
    bs = num(engine.get("bytes_saved", 0))

    safe_addstr(win, row, 2, "Wire:      ", curses.color_pair(CP_DIM))
    safe_addstr(win, row, 13, f"would={fmt_bytes(bw)}", curses.A_BOLD)
    safe_addstr(win, row, 30, f"actual={fmt_bytes(ba)}", curses.A_BOLD)
    safe_addstr(win, row, 47, f"saved={fmt_bytes(bs)} ({fmt_ratio(comp)})",
                curses.color_pair(CP_GREEN) | curses.A_BOLD)
    row += 1

    # Effective throughput
    et = safe_get(engine, "effective_throughput", default={})
    if isinstance(et, dict):
        eff_bps = num(et.get("effective_bps", 0))
        if eff_bps > 0:
            eff_link = et.get("effective_link", "?")
            act_link = et.get("actual_link", "?")
            et_amp = num(et.get("amplification", 0))
            safe_addstr(win, row, 2, "Throughput:", curses.color_pair(CP_DIM))
            safe_addstr(win, row, 13, f"effective={eff_link}",
                        curses.color_pair(CP_GREEN) | curses.A_BOLD)
            safe_addstr(win, row, 38, f"wire={act_link}", curses.A_NORMAL)
            if et_amp > 0:
                amp_cp = CP_GREEN if et_amp > 10 else (CP_YELLOW if et_amp > 2 else CP_DIM)
                safe_addstr(win, row, 55, f"{et_amp:.0f}x amplification",
                            curses.color_pair(amp_cp) | curses.A_BOLD)
            row += 1

    # RTT
    rtt_data = safe_get(engine, "rtt", default={})
    if isinstance(rtt_data, dict) and num(rtt_data.get("avg_ms", 0)) > 0:
        r_avg = num(rtt_data.get("avg_ms", 0))
        r_p50 = num(rtt_data.get("p50_ms", 0))
        r_p95 = num(rtt_data.get("p95_ms", 0))
        safe_addstr(win, row, 2, "RTT:       ", curses.color_pair(CP_DIM))
        rtt_cp = CP_GREEN if r_p95 < 100 else (CP_YELLOW if r_p95 < 1000 else CP_RED)
        safe_addstr(win, row, 13,
                    f"avg={r_avg:.0f}ms  p50={r_p50:.0f}ms  p95={r_p95:.0f}ms",
                    curses.color_pair(rtt_cp))
        row += 1
    row += 1

    # --- Per-link utilization ---
    links = safe_get(engine, "links", default={})
    if isinstance(links, dict) and links:
        safe_addstr(win, row, 2, "Links:", curses.color_pair(CP_DIM))
        row += 1
        for nh in sorted(links.keys()):
            ls = links[nh]
            if not isinstance(ls, dict):
                continue
            wo = fmt_bytes(num(ls.get("wire_out", 0)))
            wi = fmt_bytes(num(ls.get("wire_in", 0)))
            rpcs = int(num(ls.get("rpc_count", 0)))
            rate = ls.get("wire_rate", "")
            safe_addstr(win, row, 4, f"{nh:<18}", curses.A_NORMAL)
            safe_addstr(win, row, 23, f"out={wo:<9} in={wi:<9} {rpcs} RPCs",
                        curses.color_pair(CP_CYAN))
            if rate:
                safe_addstr(win, row, 62, f" {rate}", curses.color_pair(CP_DIM))
            row += 1
        row += 1

    # --- Daemon execution stats ---
    if daemon and isinstance(daemon, dict):
        dt = safe_get(daemon, "totals", default={})
        if isinstance(dt, dict) and num(dt.get("total_requests", 0)) > 0:
            safe_addstr(win, row, 2, "Daemon:    ", curses.color_pair(CP_DIM))
            dreqs = int(num(dt.get("total_requests", 0)))
            davg = num(dt.get("exec_avg_ms", 0))
            dp95 = num(dt.get("exec_p95_ms", 0))
            safe_addstr(win, row, 13,
                        f"{dreqs} reqs  exec avg={davg:.1f}ms  p95={dp95:.1f}ms",
                        curses.A_NORMAL)
            dcoal = int(num(dt.get("coalesce_hits", 0)))
            if dcoal > 0:
                safe_addstr(win, row, 57, f"coalesced={dcoal}",
                            curses.color_pair(CP_CYAN))
            row += 1

    return row


def draw_footer(win, row, mode, note="", ok=True):
    row += 1
    try:
        with open("/proc/uptime") as f:
            up_secs = int(float(f.read().split()[0]))
        days = up_secs // 86400
        hours = (up_secs % 86400) // 3600
        mins = (up_secs % 3600) // 60
        up_str = f"up {days}d {hours}h {mins}m" if days else f"up {hours}h {mins}m"
    except Exception:
        up_str = ""
    try:
        with open("/proc/loadavg") as f:
            load = f.read().split()[:3]
        load_str = f"load {' '.join(load)}"
    except Exception:
        load_str = ""

    safe_addstr(win, row, 2, f"{up_str}  {load_str}", curses.color_pair(CP_DIM))
    row += 1

    if mode == MODE_DASHBOARD:
        help_text = "↑↓=select  Enter=sensors  r=refresh  M=merge  q=quit"
    elif mode == MODE_SENSORS:
        help_text = "↑↓=select  Enter=view JSON  r=refresh  Esc/←=back"
    else:
        help_text = "↑↓=scroll  r=refresh  Esc/←=back"

    safe_addstr(win, row, 2, help_text, curses.color_pair(CP_DIM))
    # [MERGE_IS_VISIBLE_V1] The result of the last M press, if it is recent.
    # A key that appears to do nothing is worse than one that is not offered:
    # a refused launch has a reason and the operator should see it.
    if note:
        safe_addstr(win, row, 2 + len(help_text) + 3, note[:40],
                    curses.color_pair(CP_YELLOW if ok else CP_RED)
                    | curses.A_BOLD)
    return row + 1
