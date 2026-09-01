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
frognet_trace.py - function-enter/exit/event tracing, now TOGGLEABLE.

Backward compatible: the four public names (trace_enter/exit/event and their
underscore aliases) are unchanged, so every existing
`from frognet_trace import trace_enter` keeps working. The difference: output
is now gated and routed through the shared logger tree ("frognet.trace") at
TRACE level, OFF by default. Turn it on with:
    FROGNET_TRACE=1            (env)            or
    FROGNET_LOG_LEVEL=TRACE    (env)            or
    frognet_log.set_level('TRACE')  (programmatic)

When disabled, each call is a single isEnabledFor() check and returns - no
string formatting, no I/O. (Previously it unconditionally wrote every ENTER to
stderr, flooding any non-trace run.)
"""
import logging

try:
    from frognet_log import get_logger, TRACE
    _log = get_logger("trace")
except Exception:  # pragma: no cover - fallback if spine not importable
    TRACE = 5
    logging.addLevelName(TRACE, "TRACE")
    _log = logging.getLogger("frognet.trace")
    if not _log.handlers:
        _log.addHandler(logging.NullHandler())


def _kv(d):
    parts = []
    for k, v in d.items():
        if isinstance(v, str):
            v = (v.replace("\\", "\\\\").replace("\n", "\\n")
                  .replace("\r", "\\r").replace("\t", "\\t"))
        parts.append("%s=%s" % (k, v))
    return " ".join(parts)


def trace_enter(funcname, **kv):
    if _log.isEnabledFor(TRACE):
        _log.log(TRACE, "ENTER func=%s %s", funcname, _kv(kv))


def trace_exit(funcname, **kv):
    if _log.isEnabledFor(TRACE):
        _log.log(TRACE, "EXIT  func=%s %s", funcname, _kv(kv))


def trace_event(event, **kv):
    if _log.isEnabledFor(TRACE):
        _log.log(TRACE, "EVENT name=%s %s", event, _kv(kv))


_trace_enter = trace_enter
_trace_exit = trace_exit
_trace_event = trace_event
