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
frognet_monitor/trace.py — File-based trace logging.

Curses owns the terminal; all debug output goes to /tmp/frognet_monitor.log.
"""

import time

_TRACE_PATH = "/tmp/frognet_monitor.log"
_trace_fh = None


def trace(msg: str) -> None:
    global _trace_fh
    if _trace_fh is None:
        try:
            _trace_fh = open(_TRACE_PATH, "a")
        except OSError as e:
            # If we can't open the log, silently drop.
            # Do NOT mask with 2>/dev/null — just don't crash the UI.
            return
    try:
        _trace_fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        _trace_fh.flush()
    except OSError:
        pass
