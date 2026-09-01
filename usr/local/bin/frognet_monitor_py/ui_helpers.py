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
frognet_monitor/ui_helpers.py — Curses primitives.
"""

import curses

from .config import (CP_GREEN, CP_RED, CP_YELLOW, CP_CYAN, CP_DIM,
                     CP_HEADER, CP_TITLE, CP_SELECT, CP_OVERLAY)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(CP_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_RED, curses.COLOR_RED, -1)
    curses.init_pair(CP_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_CYAN, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(CP_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_TITLE, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_SELECT, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(CP_OVERLAY, curses.COLOR_WHITE, curses.COLOR_BLUE)


def safe_addstr(win, y, x, text, attr=0):
    """Write text to curses window, clipping to bounds.  Never throws."""
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    avail = max_x - x - 1
    if avail <= 0:
        return
    try:
        win.addnstr(y, x, text, avail, attr)
    except curses.error:
        pass
