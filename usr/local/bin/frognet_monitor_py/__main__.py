#!/opt/frognet_semantic/venv/bin/python3
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
Entry point: python3 -m frognet_monitor_py [--once]
  (no argument)  the curses dashboard
  --once         one frame as plain text on stdout, then exit (once.py) [ONE_MONITOR_TRUTH_V1]
"""

import sys


def run():
    if "--once" in sys.argv[1:]:
        from .once import run_once
        sys.exit(run_once())
    import curses
    from .main_loop import main
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
