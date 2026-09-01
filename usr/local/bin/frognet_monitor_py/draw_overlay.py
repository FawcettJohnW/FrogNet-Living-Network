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
frognet_monitor/draw_overlay.py — Overlay windows for sensor list and JSON detail.
"""

import curses
from typing import List, Tuple

from .config import CP_SELECT, CP_OVERLAY
from .ui_helpers import safe_addstr


def overlay_dims(stdscr) -> Tuple[int, int]:
    """Box (height, width) — ONE source of truth for overlay geometry, shared by
    draw_overlay_box and the scroll math in main_loop so page size is never
    guessed or hardcoded."""
    max_y, max_x = stdscr.getmaxyx()
    box_h = min(max_y - 4, max(12, max_y * 3 // 4))
    box_w = min(max_x - 4, max(60, max_x * 4 // 5))
    return box_h, box_w


def overlay_content_height(stdscr) -> int:
    """Number of content rows visible inside the box (box_h minus the two
    border rows). This is the page size for scrolling."""
    return overlay_dims(stdscr)[0] - 2


def clamp_scroll(scroll: int, total: int, height: int) -> int:
    """Bound a free-scroll offset to [0, total-height]. Used for the JSON
    overlay (no cursor). height<=0 or total<=height => pinned to 0."""
    if height <= 0 or total <= height:
        return 0
    return max(0, min(scroll, total - height))


def follow_cursor(scroll: int, cursor: int, total: int, height: int) -> int:
    """Adjust a scroll offset so `cursor` stays inside the visible window, then
    bound it. Used for the sensor list (cursor-driven scroll)."""
    if height <= 0 or total <= height:
        return 0
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + height:
        scroll = cursor - height + 1
    return max(0, min(scroll, total - height))


def draw_overlay_box(stdscr, title: str, content_lines: List[str],
                     scroll_offset: int, selected_idx: int = -1,
                     selectable: bool = False) -> Tuple[int, int]:
    """Draw a centered overlay box.  Returns (visible_height, total_lines).

    If selectable, highlights selected_idx row.
    """
    max_y, max_x = stdscr.getmaxyx()

    box_h, box_w = overlay_dims(stdscr)
    top = max(1, (max_y - box_h) // 2)
    left = max(1, (max_x - box_w) // 2)

    try:
        overlay = curses.newwin(box_h, box_w, top, left)
    except curses.error:
        return (0, len(content_lines))

    overlay.erase()
    overlay.bkgd(' ', curses.color_pair(CP_OVERLAY))

    try:
        overlay.border()
    except curses.error:
        pass

    # Title bar
    title_str = f" {title} "
    tx = max(1, (box_w - len(title_str)) // 2)
    try:
        overlay.addnstr(0, tx, title_str, box_w - 2,
                        curses.color_pair(CP_OVERLAY) | curses.A_BOLD)
    except curses.error:
        pass

    # Content area
    content_h = box_h - 2
    content_w = box_w - 4
    total = len(content_lines)

    # Clamp scroll
    if scroll_offset > max(0, total - content_h):
        scroll_offset = max(0, total - content_h)
    if scroll_offset < 0:
        scroll_offset = 0

    for i in range(content_h):
        line_idx = scroll_offset + i
        if line_idx >= total:
            break
        display = content_lines[line_idx][:content_w]

        if selectable and line_idx == selected_idx:
            attr = curses.color_pair(CP_SELECT) | curses.A_BOLD
            padded = display.ljust(content_w)
            try:
                overlay.addnstr(1 + i, 2, padded, content_w, attr)
            except curses.error:
                pass
        else:
            attr = curses.color_pair(CP_OVERLAY)
            try:
                overlay.addnstr(1 + i, 2, display, content_w, attr)
            except curses.error:
                pass

    # Scroll indicator
    if total > content_h:
        pct = int((scroll_offset / max(1, total - content_h)) * 100)
        indicator = (f" {scroll_offset + 1}-"
                     f"{min(scroll_offset + content_h, total)}/{total} ({pct}%) ")
        try:
            overlay.addnstr(box_h - 1, max(1, box_w - len(indicator) - 2),
                            indicator, box_w - 2,
                            curses.color_pair(CP_OVERLAY) | curses.A_DIM)
        except curses.error:
            pass

    overlay.noutrefresh()
    return (content_h, total)
