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
[INSTRUMENTATION_V2 2026-06-01] frognet trace helper — broker edition.

Drop into /opt/frognet_broker_v4/ alongside frognet_broker_v4.py so any
instrumented sibling module (broker_namespace_v4.py, etc.) can find
`trace_enter` / `trace_event` / their underscore-prefixed aliases at
import time.

All four names point at the same emitters.  No dependency on the host
logging config — works whether or not uvicorn / FastAPI / the broker
have initialized logging.  Writes to stderr (which systemd captures
into the unit's journal).
"""
import sys
import time
import threading

_TRACE_LOCK = threading.Lock()


def _kv_format(d):
    parts = []
    for k, v in d.items():
        if isinstance(v, str):
            v = (v.replace("\\", "\\\\")
                  .replace("\n", "\\n")
                  .replace("\r", "\\r")
                  .replace("\t", "\\t"))
        parts.append("%s=%s" % (k, v))
    return " ".join(parts)


def _emit(prefix, payload):
    line = "[FROGNET-TRACE] t=%.6f tid=%d %s %s\n" % (
        time.time(),
        threading.get_ident(),
        prefix,
        payload,
    )
    with _TRACE_LOCK:
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except Exception:
                pass


def trace_enter(funcname, **kv):
    _emit("ENTER func=%s" % funcname, _kv_format(kv))


def trace_exit(funcname, **kv):
    _emit("EXIT  func=%s" % funcname, _kv_format(kv))


def trace_event(event, **kv):
    _emit("EVENT name=%s" % event, _kv_format(kv))


# Aliases — different instrumenter generations have used both naming
# conventions; export both so it doesn't matter which one a given
# instrumented file imports.
_trace_enter = trace_enter
_trace_exit  = trace_exit
_trace_event = trace_event
