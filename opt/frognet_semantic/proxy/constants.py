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
proxy/constants.py

Central constants and enums for FrogNet proxy.
"""

from __future__ import annotations

import os
import socket
from enum import Enum
from typing import Optional

DEBUG = os.environ.get("FROGNET_DEBUG", "0").strip() == "1"

def debug(msg: str) -> None:
    if DEBUG:
        print(msg, flush=True)

# Semantic mode:
#  0 = semantic disabled (forward fast only)
#  1 = train-on-miss (learn from REAL pass-through when template missing)
#  2 = always-train (learn opportunistically on successful REAL)
_raw_mode = (os.environ.get("FROGNET_SEMANTIC", "").strip() or None)
if _raw_mode is None:
    SEM_MODE: Optional[int] = None
else:
    try:
        SEM_MODE = int(_raw_mode)
    except ValueError:
        SEM_MODE = None

def decide_semantic_mode() -> int:
    if SEM_MODE in (0, 1, 2):
        return int(SEM_MODE)
    return 1  # default train-on-miss

DAEMON_PORT_DEFAULT = int(os.environ.get("FROGNET_DAEMON_PORT", "9009"))

# [REFLECT_PROBE_V1] Reflect-vhost port (loop/reflection detector). User-
# exposed and env-overridable, like the daemon port and broker endpoint.
REFLECT_PORT_DEFAULT = int(os.environ.get("FROGNET_REFLECT_PORT", "18432"))

DEMO_RUN_ID_HEADER = "X-FrogNet-RunID"
HDR_ORIGIN_LOCAL = "X-FrogNet-Origin-Local"
HDR_CONTROL_PLANE = "X-FrogNet-Control-Plane"

# Socket marking used for recursion avoidance in your environment
SO_MARK = getattr(socket, "SO_MARK", 36)

# Telemetry config (proxy emits SemanticProxy.Bytes)
_TELEM_ENV = os.environ.get("FROGNET_TELEMETRY", "").strip().lower()
TELEMETRY_ENABLED_DEFAULT = _TELEM_ENV in ("1", "true", "yes", "on")
# [METRICS_INTERVAL_CONSISTENT_V1] 30s, matching the daemon's
# start_daemon_flusher(interval=30.0) at daemon_main.py:54. This was 5.0, so the
# proxy emitted sensor batches to the elected database host six times more often
# than the daemon for the same fleet, and after [NODE_HEARTBEAT_TS_V1] it would
# also have heartbeat at 5s -- a read plus a write per cycle per node against the
# DB host. One cadence for both processes: same emission rate, same
# database-change detection window.
TELEMETRY_INTERVAL_DEFAULT = float(os.environ.get("FROGNET_TELEMETRY_INTERVAL", "30.0"))

# Concurrency guard (prevents FD death spirals under request storms)
MAX_ACTIVE_REQUESTS = int(os.environ.get("FROGNET_PROXY_MAX_ACTIVE", "256"))

# Semantic protocol
SEM_PROTO_V2 = 2
SEM_HDR_V1_LEN = 8


class DecisionPath(str, Enum):
    LOCAL = "LOCAL"
    FAST = "FAST"
    SEMANTIC = "SEMANTIC"
    HAM = "HAM"
    FAIL_CLOSED = "FAIL_CLOSED"


class DecisionReason(str, Enum):
    KERNEL_LOCAL = "KERNEL_LOCAL"
    NEXT_HOP_SEMANTIC = "NEXT_HOP_SEMANTIC"
    FORWARD_FAST = "FORWARD_FAST"
    FAIL_CLOSED = "FAIL_CLOSED"
