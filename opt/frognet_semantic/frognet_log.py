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
frognet_log.py - single logging spine for the FrogNet source tree.

Goal: one place to turn output off/down. Everything that used to `print(...)`
or `sys.stderr.write(...)` for diagnostics routes through a logger under the
"frognet" tree, so a single env var (or call) controls verbosity everywhere.

Levels (low -> high): TRACE(5) < DEBUG < INFO < WARNING < ERROR.
  FROGNET_LOG_LEVEL = TRACE|DEBUG|INFO|WARNING|ERROR   (default WARNING = quiet)
  FROGNET_TRACE     = 1|true|yes  -> force TRACE on (function-enter tracing)

Usage in operational modules:
    from frognet_log import get_logger
    log = get_logger(__name__)
    log.info("COMMIT installs=%d", n)        # was print(...)
    log.debug("...")                          # chatty detail
Trace shim (frognet_trace.py) emits at TRACE through this same tree, so the
existing `from frognet_trace import trace_enter` call sites keep working and
gain a toggle.
"""
import logging
import os
import sys

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_ROOT = "frognet"
_configured = False


def _env_level() -> int:
    if os.environ.get("FROGNET_TRACE", "").lower() in ("1", "true", "yes", "on"):
        return TRACE
    name = os.environ.get("FROGNET_LOG_LEVEL", "WARNING").upper()
    if name == "TRACE":
        return TRACE
    return getattr(logging, name, logging.WARNING)


def _configure():
    global _configured
    if _configured:
        return
    root = logging.getLogger(_ROOT)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("[frognet:%(name)s] %(levelname)s %(message)s"))
        root.addHandler(h)
    root.setLevel(_env_level())
    root.propagate = False
    _configured = True


def get_logger(name: str = _ROOT) -> logging.Logger:
    _configure()
    if name == _ROOT or name.startswith(_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")


def set_level(level) -> None:
    """Programmatic override: set_level('DEBUG') or set_level(logging.INFO)."""
    _configure()
    if isinstance(level, str):
        level = TRACE if level.upper() == "TRACE" else getattr(logging, level.upper())
    logging.getLogger(_ROOT).setLevel(level)


def trace_enabled() -> bool:
    _configure()
    return logging.getLogger(_ROOT).isEnabledFor(TRACE)
