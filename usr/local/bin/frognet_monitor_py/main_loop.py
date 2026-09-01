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
frognet_monitor/main_loop.py — Main curses event loop.

Orchestrates probes, DB fetches, drawing, and input handling.

Architecture:
  All network I/O runs in background threads.  The main loop ONLY
  draws and handles input — it never blocks on network calls.
  Background results are swapped into AppState atomically (single
  attribute assignment) so the draw path always sees consistent data.
"""

import curses
import threading
import time

from .config import (MODE_DASHBOARD, MODE_SENSORS, MODE_JSON,
                     REFRESH_SEC, DB_REFRESH_SEC)
from .discovery import discover_hosts
from .draw_dashboard import (draw_title, draw_peer_header, format_peer_cols,
                             draw_peer_row, draw_local_row, draw_internet,
                             draw_engine, draw_footer)
from .draw_overlay import (draw_overlay_box, overlay_content_height,
                          clamp_scroll, follow_cursor)
from .formatting import num, pretty_json
from .identity import (LOCAL_LOWER, LOCAL_IP, databasehost_ip,
                       databasehost_control_ip)
from .merge import start_merge
from .probes import probe_all
from .sensors import (fetch_engine_json, fetch_daemon_json, fetch_peer_sensors,
                      fetch_link_quality_json,
                      fetch_sensors_for_host, fetch_sensor_detail)
from .state import AppState
from .ui_helpers import init_colors


# ---- Background I/O workers ----

def _bg_probe_cycle(st):
    """Run discovery + probes in background.  Writes results atomically."""
    hosts = discover_hosts()
    results = probe_all(hosts)
    # Atomic swap — main loop reads these; single assignment = safe
    st.hosts = hosts
    st.results = results
    st.clamp_peer_cursor()
    st.probe_running = False


def _bg_db_fetch(st):
    """Run DB sensor fetches in background.  Writes results atomically."""
    from .trace import trace
    from .identity import LOCAL_LOWER, LOCAL_IP
    engine = fetch_engine_json()
    daemon = fetch_daemon_json()
    # [LINK_POTENTIAL_PING_V1 2026-05-25] Pull this node's
    # SemanticProxy.LinkQuality sensor so the per-peer status indicator
    # can use saturation_ratio.  Fetched alongside the others to keep
    # the DB-roundtrip count predictable.
    link_quality = fetch_link_quality_json()
    # Build peer IP list from discovered hosts, excluding self
    peer_ips = [ip for ip, name in st.hosts
                if name.lower() != LOCAL_LOWER and ip != LOCAL_IP]
    peer_data = fetch_peer_sensors(peer_ips)
    trace(f"[DB_FETCH] peer_data keys={list(peer_data.keys())} "
          f"host_ips={peer_ips}")
    st.engine = engine
    st.daemon = daemon
    st.peer_data = peer_data
    st.link_quality = link_quality
    st.db_fetch_running = False


def main(stdscr):
    init_colors()
    curses.curs_set(0)
    stdscr.timeout(200)

    st = AppState()

    while True:
        now = time.monotonic()

        # Kick off probe cycle in background (if not already running)
        if (st.mode == MODE_DASHBOARD
                and not st.probe_running
                and now - st.last_probe >= REFRESH_SEC):
            st.last_probe = now
            st.probe_running = True
            threading.Thread(target=_bg_probe_cycle, args=(st,),
                             daemon=True).start()

        # Kick off DB fetch in background (if not already running)
        if not st.db_fetch_running and now - st.last_db_fetch >= DB_REFRESH_SEC:
            st.last_db_fetch = now
            st.db_fetch_running = True
            threading.Thread(target=_bg_db_fetch, args=(st,),
                             daemon=True).start()

        # --- Draw dashboard (always, as background) ---
        stdscr.erase()
        row = 0
        row = draw_title(stdscr, row)
        row = draw_peer_header(stdscr, row)

        for idx, (ip, name) in enumerate(st.hosts):
            is_self = (name.lower() == LOCAL_LOWER or ip == LOCAL_IP)
            selected = (st.mode == MODE_DASHBOARD and idx == st.peer_cursor)

            # [DBHOST_IS_MARKED_V1] Which peer the whole pond is reading and
            # writing through. Resolved from /etc/hosts (TTL-cached in
            # identity.py), because the role floats and the file is what every
            # other component routes by.
            # [NO_FALLBACK_V1] _hosts_lookup RAISES if /etc/hosts cannot be
            # read -- it will not pretend the name is absent, because "no
            # databasehost elected" and "the monitor cannot read the file" look
            # identical on screen and are entirely different problems. The
            # decision to carry on without markers is made HERE, explicitly and
            # once per pass, not buried in the reader.
            try:
                _dbip = databasehost_ip()
                _ctlip = databasehost_control_ip()
            except OSError:
                _dbip = _ctlip = None      # already reported by the resolver
            _is_db = bool(_dbip) and ip == _dbip
            # [CONTROL_IS_MARKED_V1] Independent of the above: a node can be
            # both, one, or neither.
            _is_ctl = bool(_ctlip) and ip == _ctlip

            if is_self:
                row = draw_local_row(stdscr, row, name, engine=st.engine,
                                     selected=selected, is_dbhost=_is_db,
                                     is_control=_is_ctl)
            else:
                r = st.results.get(ip, {})
                echo = r.get("echo", ("FAIL", 0))

                # Per-peer cache stats from SemanticCache.Peer.* sensors
                rtt_ms = 0.0
                p50_ms = 0.0
                p95_ms = 0.0
                hit_rate = 0.0
                bytes_saved = 0
                eff_bps = 0.0
                actual_bps = 0.0
                pd = st.peer_data.get(ip, {})
                if isinstance(pd, dict) and pd:
                    rtt = pd.get("rtt", {})
                    if isinstance(rtt, dict):
                        try:
                            rtt_ms = float(rtt.get("avg_ms", 0))
                        except (TypeError, ValueError):
                            pass
                        try:
                            p50_ms = float(rtt.get("p50_ms", 0))
                        except (TypeError, ValueError):
                            pass
                        try:
                            p95_ms = float(rtt.get("p95_ms", 0))
                        except (TypeError, ValueError):
                            pass
                    try:
                        hit_rate = float(pd.get("cache_hit_rate", 0))
                    except (TypeError, ValueError):
                        pass
                    try:
                        bytes_saved = int(pd.get("bytes_saved", 0))
                    except (TypeError, ValueError):
                        pass
                    et = pd.get("effective_throughput", {})
                    if isinstance(et, dict):
                        try:
                            eff_bps = float(et.get("effective_bps", 0))
                        except (TypeError, ValueError):
                            pass
                        try:
                            actual_bps = float(et.get("actual_bps", 0))
                        except (TypeError, ValueError):
                            pass

                # Fall back to echo RTT if no semantic RTT
                if rtt_ms == 0 and echo[0] == "OK" and echo[1] > 0:
                    rtt_ms = float(echo[1])

                # [LINK_POTENTIAL_PING_V1 2026-05-25] Pull this peer's
                # entry from the link-quality snapshot if present.
                # None when the proxy hasn't talked to this peer in
                # the current window or the snapshot hasn't loaded.
                # format_peer_cols handles None gracefully.
                lq_peer = None
                if isinstance(st.link_quality, dict):
                    lq_peers = st.link_quality.get("peers")
                    if isinstance(lq_peers, dict):
                        lq_peer = lq_peers.get(ip)

                cols, colors = format_peer_cols(name, echo, rtt_ms, p50_ms,
                                                p95_ms, hit_rate, bytes_saved,
                                                eff_bps, actual_bps,
                                                link_quality_peer=lq_peer)
                row = draw_peer_row(stdscr, row, cols, colors,
                                    selected=selected, is_dbhost=_is_db,
                                    is_control=_is_ctl)

        row += 1
        inet = st.results.get("__internet__", {}).get("result", ("FAIL", 0))
        row = draw_internet(stdscr, row, inet)
        row = draw_engine(stdscr, row, st.engine, st.daemon)
        _note = ""
        _nok = True
        if getattr(st, "merge_note", ""):
            # The note is a reply to a keystroke, so it goes away on its own.
            # A stale message beside a live dashboard reads as current state.
            if time.monotonic() - st.merge_note_at <= MERGE_NOTE_S:
                _note, _nok = st.merge_note, st.merge_note_ok
            else:
                st.merge_note = ""
        draw_footer(stdscr, row, st.mode, note=_note, ok=_nok)

        # Flush stdscr BEFORE overlays so overlays paint on top
        stdscr.noutrefresh()

        # --- Draw overlays ---
        if st.mode == MODE_SENSORS:
            _draw_sensor_overlay(stdscr, st)
        elif st.mode == MODE_JSON:
            _draw_json_overlay(stdscr, st)

        curses.doupdate()

        # --- Input handling ---
        try:
            ch = stdscr.getch()
        except curses.error:
            continue

        if ch == -1:
            continue

        if st.mode == MODE_DASHBOARD:
            _handle_dashboard_input(ch, st)
            if ch in (ord('q'), ord('Q')):
                break
        elif st.mode == MODE_SENSORS:
            _handle_sensor_input(ch, st)
        elif st.mode == MODE_JSON:
            _handle_json_input(ch, st)


# --- Overlay drawing helpers ---

def _draw_sensor_overlay(stdscr, st):
    if st.sensor_loading:
        lines = ["Loading sensors..."]
    elif not st.sensors:
        lines = ["No sensors found for this host.",
                 "",
                 "The host may not be reporting sensor data,",
                 "or it may not be reachable from this node."]
    else:
        lines = []
        prefix = st.sensor_host_name + "."
        for i, s in enumerate(st.sensors):
            sname = s.get("SensorName", "?")
            stype = s.get("SensorType", "")
            short = sname
            if short.startswith(prefix):
                short = short[len(prefix):]
            line = f"  {short}"
            if stype:
                line += f"  ({stype})"
            lines.append(line)

    content_h = overlay_content_height(stdscr)
    st.overlay_visible_h = content_h
    if st.sensors:
        # One line per sensor, so line index == sensor_cursor. Scroll follows
        # the cursor so the selected row is always on screen.
        st.sensor_scroll = follow_cursor(st.sensor_scroll, st.sensor_cursor,
                                         len(lines), content_h)
    else:
        st.sensor_scroll = 0

    title = f"Sensors: {st.sensor_host_name} ({st.sensor_host_ip})"
    draw_overlay_box(stdscr, title, lines,
                     scroll_offset=st.sensor_scroll,
                     selected_idx=st.sensor_cursor if st.sensors else -1,
                     selectable=bool(st.sensors))


def _draw_json_overlay(stdscr, st):
    content_h = overlay_content_height(stdscr)
    st.overlay_visible_h = content_h
    # Free-scroll (no cursor): bound the stored offset against the real page
    # height and write it back so input never drifts past the ends.
    st.json_scroll = clamp_scroll(st.json_scroll, len(st.json_lines), content_h)

    age = time.monotonic() - st.json_fetched_at if st.json_fetched_at else 0
    refresh_ind = " ⟳" if st.json_refreshing else ""
    title = f"{st.json_title}  ({age:.0f}s ago{refresh_ind})  r=refresh"
    draw_overlay_box(stdscr, title, st.json_lines,
                     scroll_offset=st.json_scroll,
                     selected_idx=-1, selectable=False)


# --- Input handlers ---

MERGE_NOTE_S = 6.0


def _handle_dashboard_input(ch, st):
    if ch in (curses.KEY_UP, ord('k')):
        st.peer_cursor = max(0, st.peer_cursor - 1)
    elif ch in (curses.KEY_DOWN, ord('j')):
        st.peer_cursor = min(st.peer_count - 1, st.peer_cursor + 1)
    elif ch in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord('l')):
        host = st.selected_host()
        if host:
            ip, name = host
            st.sensor_host_ip = ip
            st.sensor_host_name = name
            st.sensor_cursor = 0
            st.sensor_scroll = 0
            st.sensor_loading = True
            st.mode = MODE_SENSORS

            def _load():
                st.sensors = fetch_sensors_for_host(name)
                st.sensor_loading = False
                st.clamp_sensor_cursor()
            threading.Thread(target=_load, daemon=True).start()
    elif ch == ord('r'):
        st.last_probe = 0.0
        st.last_db_fetch = 0.0
    elif ch == ord('M'):
        # [MERGE_IS_VISIBLE_V1] Capital M only. Lowercase letters on this
        # screen are navigation (j/k/l/h/g/r/q) and a merge is not something to
        # start with a mistyped cursor key.
        #
        # [SENTINEL_IS_A_CLAIM_NOT_A_LOCK_V1] A second press while the refusal
        # is still on screen forces it. The sentinel is a claim a dead merge can
        # leave behind, so it must not be able to block this key forever -- but
        # starting a second merge on top of a live one should take two presses.
        _forcing = bool(st.merge_note) and not st.merge_note_ok \
            and (time.monotonic() - st.merge_note_at) <= MERGE_NOTE_S
        ok, note = start_merge(force=_forcing)
        st.merge_note = note
        st.merge_note_ok = ok
        st.merge_note_at = time.monotonic()


def _handle_sensor_input(ch, st):
    if ch in (27, curses.KEY_LEFT, ord('h'), ord('q')):
        st.mode = MODE_DASHBOARD
        st.sensors = []
    elif ch in (curses.KEY_UP, ord('k')):
        st.sensor_cursor = max(0, st.sensor_cursor - 1)
    elif ch in (curses.KEY_DOWN, ord('j')):
        st.sensor_cursor = min(len(st.sensors) - 1, st.sensor_cursor + 1)
    elif ch in (curses.KEY_PPAGE,):
        page = max(1, st.overlay_visible_h)
        st.sensor_cursor = max(0, st.sensor_cursor - page)
    elif ch in (curses.KEY_NPAGE,):
        page = max(1, st.overlay_visible_h)
        st.sensor_cursor = min(len(st.sensors) - 1, st.sensor_cursor + page)
    elif ch in (curses.KEY_HOME, ord('g')):
        st.sensor_cursor = 0
    elif ch in (curses.KEY_END, ord('G')):
        st.sensor_cursor = max(0, len(st.sensors) - 1)
    elif ch in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord('l')):
        sensor = st.selected_sensor()
        if sensor:
            sname = sensor.get("SensorName", "?")
            detail = fetch_sensor_detail(sname)
            if detail:
                st.json_title = sname
                st.json_lines = pretty_json(detail).splitlines()
            else:
                st.json_title = sname
                st.json_lines = ["Failed to fetch sensor detail.",
                                 "",
                                 "Raw cached data:",
                                 ""] + pretty_json(sensor).splitlines()
            st.json_scroll = 0
            st.json_sensor_name = sname
            st.json_fetched_at = time.monotonic()
            st.json_refreshing = False
            st.mode = MODE_JSON
    elif ch == ord('r'):
        st.sensor_loading = True

        def _reload():
            st.sensors = fetch_sensors_for_host(st.sensor_host_name)
            st.sensor_loading = False
            st.clamp_sensor_cursor()
        threading.Thread(target=_reload, daemon=True).start()


def _handle_json_input(ch, st):
    if ch in (27, curses.KEY_LEFT, ord('h'), ord('q')):
        st.mode = MODE_SENSORS
        st.json_lines = []
        st.json_sensor_name = ""
    elif ch == ord('r'):
        if st.json_sensor_name and not st.json_refreshing:
            st.json_refreshing = True

            def _json_manual_refresh():
                detail = fetch_sensor_detail(st.json_sensor_name)
                if detail:
                    old_scroll = st.json_scroll
                    st.json_lines = pretty_json(detail).splitlines()
                    st.json_scroll = old_scroll
                st.json_fetched_at = time.monotonic()
                st.json_refreshing = False
            threading.Thread(target=_json_manual_refresh, daemon=True).start()
    elif ch in (curses.KEY_UP, ord('k')):
        st.json_scroll = max(0, st.json_scroll - 1)
    elif ch in (curses.KEY_DOWN, ord('j')):
        st.json_scroll += 1
    elif ch in (curses.KEY_PPAGE,):
        st.json_scroll = max(0, st.json_scroll - max(1, st.overlay_visible_h))
    elif ch in (curses.KEY_NPAGE,):
        st.json_scroll += max(1, st.overlay_visible_h)
    elif ch in (curses.KEY_HOME, ord('g')):
        st.json_scroll = 0
    elif ch in (curses.KEY_END, ord('G')):
        st.json_scroll = max(0, len(st.json_lines) - max(1, st.overlay_visible_h))
