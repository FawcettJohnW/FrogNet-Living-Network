#!/usr/bin/env python3
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
core/frognet_ram.py -- network RAM for code that is not a service.

[A_HANDLER_KNOWS_RAM_NOT_A_HOSTNAME_V1]

A semantic handler is an object. FORMAT_HANDLERS imports and instantiates
with no web server, no proxy and no daemon, so a handler can be driven from a
script, a shell wrapper or an oracle. What it must NOT do is reach shared
state by a different route than a service does, because then the fact it
reads is not the fact everyone else has.

frognet_tuples already routes correctly: DEFAULT_DBHOST is
databasehost_control.frognet, a NAME resolved through hosts_only rather than
the resolver, and nothing in it binds to localhost. So this module adds no
storage mechanism. It adds one entry point with the defaults a short-lived
process needs, and it makes the caller say which of two things it means.

THE DEFAULT THAT BITES

    T.put(service, var, scope, value)          # own=True

own=True registers the tuple for atexit deletion, so a service's presence
disappears when the service stops. That is right for presence and wrong for
published state, and the difference is invisible at the call site. A handler
run from a script is a process that exits immediately: with the default it
would delete what it had just written, on the way out, silently.

So `upsert` has no default. EPHEMERAL means "this fact is my being here" and
dies with the process. DURABLE means "this fact is about the world" and
outlives it, ageing out of the store on its envelope like any other. The
caller states which, every time.

    from core import frognet_ram as RAM
    RAM.read("frogtorch", "params")
    RAM.upsert("frogtorch", "params", scope, value, RAM.DURABLE)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Runnable as a script (handle_tensor and friends invoke it directly), so the
# tree has to be importable whether or not the caller set PYTHONPATH.
_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TREE not in sys.path:
    sys.path.insert(0, _TREE)

from core import frognet_tuples as T  # noqa: E402

#: The two meanings of a write, named so a reader of the call site can see
#: which was intended without knowing what `own` does.
EPHEMERAL = "ephemeral"     # my presence; atexit removes it
DURABLE = "durable"         # a fact about the world; survives this process

_LIFETIMES = (EPHEMERAL, DURABLE)


def host(dbhost: Optional[str] = None) -> str:
    """The authoritative store, resolved by the tuple layer, not by callers.

    A handler knows "network RAM". Where that currently lives is the tuple
    layer's business -- databasehost floats, is re-elected, and a caller that
    pinned an address would be reading a host that used to be authoritative.
    """
    return dbhost or T.DEFAULT_DBHOST


def read(service: str, var: str, dbhost: Optional[str] = None,
         fresh_s: int = 0, timeout: float = 4.0) -> List[Dict[str, Any]]:
    """Every scope's value of one variable, with the store's envelope.

    Rows carry ts_env and age_s from the store's own clock -- [ENVELOPE_TS_V1]
    -- so a handler that needs to know how old a fact is asks the row, never
    its own clock and never the payload.
    """
    return T.get(service, var, dbhost=host(dbhost), fresh_s=fresh_s,
                 timeout=timeout)


def read_one(service: str, var: str, scope: str,
             dbhost: Optional[str] = None,
             fresh_s: int = 0) -> Optional[Dict[str, Any]]:
    """One scope's row, or None. The row, not the value: a caller that wants
    to decide anything about currency needs the envelope that came with it."""
    for r in read(service, var, dbhost=dbhost, fresh_s=fresh_s):
        if r.get("scope") == scope:
            return r
    return None


def upsert(service: str, var: str, scope: str, value: Dict[str, Any],
           lifetime: str, dbhost: Optional[str] = None,
           timeout: float = 4.0) -> bool:
    """Write or refresh one cell. `lifetime` is required, not defaulted.

    Returns False on failure and says so loudly through the tuple layer --
    [PUT_FAILURE_IS_LOUD_V1] -- rather than raising, because callers already
    depend on that. A handler that ignores the return value has decided a
    write it never confirmed is a fact, which is the failure with no symptom.
    """
    if lifetime not in _LIFETIMES:
        raise ValueError(
            "lifetime must be %s or %s, got %r -- a write whose lifetime is "
            "guessed either deletes published state at exit or leaves "
            "presence behind after the process is gone"
            % (EPHEMERAL, DURABLE, lifetime))
    return T.put(service, var, scope, value, dbhost=host(dbhost),
                 timeout=timeout, own=(lifetime == EPHEMERAL))


def main(argv=None) -> int:
    """Read or write network RAM from a shell, for handlers and oracles.

      frognet_ram.py read  <service> <var> [scope]
      frognet_ram.py write <service> <var> <scope> <json> <ephemeral|durable>

    Reads print one JSON row per line, envelope included. `--dbhost H`
    overrides the store for a test space; without it the authoritative host
    is used, which is the point.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    db = None
    if "--dbhost" in argv:
        i = argv.index("--dbhost")
        db = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        print(main.__doc__)
        return 2
    op = argv.pop(0)
    if op == "read":
        if len(argv) < 2:
            print(main.__doc__)
            return 2
        rows = read(argv[0], argv[1], dbhost=db)
        if len(argv) > 2:
            rows = [r for r in rows if r.get("scope") == argv[2]]
        for r in rows:
            print(json.dumps(r, sort_keys=True))
        return 0 if rows else 1
    if op == "write":
        if len(argv) != 5:
            print(main.__doc__)
            return 2
        service, var, scope, blob, lifetime = argv
        ok = upsert(service, var, scope, json.loads(blob), lifetime,
                    dbhost=db)
        print("ok" if ok else "FAILED")
        return 0 if ok else 1
    print(main.__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
