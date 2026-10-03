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
import os
import sys
import time
import threading
import uuid

_DEBUG = os.environ.get("FROGNET_DEBUG", "0") == "1"
_PID = os.getpid()
_LOCK = threading.RLock()

def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def trace(msg: str):
    """Always-on operational logging. Session lifecycle, errors, warnings."""
    with _LOCK:
        sys.stderr.write(
            f"[{_ts()}] [PID {_PID}] {msg}\n"
        )
        sys.stderr.flush()

def debug(msg: str):
    """Verbose logging, gated by FROGNET_DEBUG=1."""
    if not _DEBUG:
        return
    with _LOCK:
        sys.stderr.write(
            f"[{_ts()}] [PID {_PID}] {msg}\n"
        )
        sys.stderr.flush()

def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]
