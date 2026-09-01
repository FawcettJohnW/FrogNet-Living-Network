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
frognet_monitor/config.py — Constants and configuration.
"""

# --- Probe timing ---
ECHO_TIMEOUT = 5          # seconds per echo probe
INET_TIMEOUT = 5          # seconds for internet canary
REFRESH_SEC = 3           # seconds between probe cycles
DB_REFRESH_SEC = 15       # minimum seconds between database fetches
ECHO_STAGGER_SEC = 1.0    # delay between staggered echo probes

# --- UI modes ---
MODE_DASHBOARD = 0
MODE_SENSORS = 1
MODE_JSON = 2

# --- Color pair indices ---
CP_NORMAL = 0
CP_GREEN = 1
CP_RED = 2
CP_YELLOW = 3
CP_CYAN = 4
CP_DIM = 5
CP_HEADER = 6
CP_TITLE = 7
CP_SELECT = 8
CP_OVERLAY = 9
