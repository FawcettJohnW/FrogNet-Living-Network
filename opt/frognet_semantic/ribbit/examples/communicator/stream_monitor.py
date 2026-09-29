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
stream_monitor.py - live readout of a SotF media stream's metrics, the way
frognet_monitor reads the network. Reads the per-node metric tuples for ONE
session off the transient (the server's authoritative aggregate + each endpoint's
self-observed counts) and renders them on a refresh, flagging slow producers and
consumers DETERMINISTICALLY.

On a box it reads the real transient via frognet_tuples (talks to
databasehost.frognet). In the sim hand it a MockSpace. Same reader either way.

Usage:
  # box (real transient):
  python3 stream_monitor.py --session sotf-1234
  python3 stream_monitor.py --session sotf-1234 --interval 1.0 --dbhost databasehost.frognet
  # one-shot (no refresh loop), e.g. for scripting:
  python3 stream_monitor.py --session sotf-1234 --once

Reads only; writes nothing. Ctrl-C to stop.
"""
from __future__ import annotations

import argparse
import sys
import time

import media_stream as M

_CLEAR = "\033[2J\033[H"


def _backend(dbhost: str):
    """Real transient backend via frognet_tuples (box). Fails clearly if the
    module / api.php isn't reachable, rather than pretending."""
    import frognet_tuples as T  # noqa
    return M.FrognetTuplesBackend(T, dbhost=dbhost)


def render(control: M.TupleControl, fresh_s: int, slack: float) -> str:
    dash = M.read_stream_dashboard(control, fresh_s=fresh_s, slack=slack)
    body = dash.render()
    state = control.state() or {}
    bearer = control.bearer()
    err = control.error()
    head = "stream %s   state=%s   bearer=%s%s" % (
        control.session_id, state.get("state", "?"),
        ("rung " + str(bearer)) if bearer is not None else "-",
        ("   ERROR %s: %s" % (err.get("code"), err.get("msg"))) if err else "")
    slow = dash.slow_nodes()
    foot = ("SLOW: " + ", ".join("%s (%s)" % (n.node, n.reason) for n in slow)) if slow \
           else "all nodes keeping pace"
    return "%s\n%s\n%s\n%s" % (head, "-" * max(len(head), 48), body, foot)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stream_monitor",
                                 description="Live SotF media-stream metrics monitor.")
    ap.add_argument("--session", required=True, help="stream session id")
    ap.add_argument("--interval", type=float, default=1.0, help="refresh seconds")
    ap.add_argument("--fresh-s", type=int, default=30,
                    help="drop metric rows older than this many seconds")
    ap.add_argument("--slack", type=float, default=0.15,
                    help="fps fraction below the server rate before a node is 'slow'")
    ap.add_argument("--dbhost", default="databasehost.frognet")
    ap.add_argument("--once", action="store_true", help="render once and exit")
    args = ap.parse_args(argv)

    try:
        backend = _backend(args.dbhost)
    except Exception as e:
        print("cannot reach the transient (%s): %s" % (args.dbhost, e), file=sys.stderr)
        print("on a box this needs frognet_tuples + a reachable databasehost.frognet.",
              file=sys.stderr)
        return 2

    control = M.TupleControl(backend, args.session)
    if args.once:
        print(render(control, args.fresh_s, args.slack))
        return 0
    try:
        while True:
            out = render(control, args.fresh_s, args.slack)
            sys.stdout.write(_CLEAR + out + "\n")
            sys.stdout.flush()
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
