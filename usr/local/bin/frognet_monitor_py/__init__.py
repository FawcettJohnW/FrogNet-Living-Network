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
frognet_monitor — FrogNet Network Info Center v5.0

Dashboard:
  - Per-peer: semantic echo, RTT (avg/p95), cache hit rate, bytes saved
  - Cache telemetry: request/response type matrix, wire savings
  - Internet canary

Interactive:
  - ↑/↓ or j/k to highlight a peer row
  - Enter/→  opens sensor list for that node
  - Enter/→  on a sensor opens JSON detail overlay
  - Esc/← /q backs out one level
  - q from dashboard quits

Architecture:
  All probes go through the proxy on port 80.
  Echo success proves the remote daemon is alive (full semantic path).
  NO direct sockets to daemon port 9009.  EVER.
"""
