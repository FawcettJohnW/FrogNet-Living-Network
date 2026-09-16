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
"""store_preflight.py -- ask the store that will actually serve the campaign.

[THE_TREE_IS_NOT_WHAT_RUNS_V1 - John 2026-09-15]

Every tier in this directory asserts over the source tree on the machine the
tier runs on. That is the wrong tree for anything served by another machine,
and it has now cost two campaigns in one night:

  - collective_tier plane 5 read web/api.php out of AI-Host's tree, found
    [AN_OBJECT_IS_NOT_A_LIST_V1] and went green. The file answering requests
    is /var/www/html/api.php on the databasehost, which nobody had updated,
    so pub_n was still arriving as a list and ddp5702 came back with
    hook_stats_error: AttributeError: 'list' object has no attribute 'get'.

  - ddp5706 died at the gate on HTTP 502, "Daemon: http exec error:
    TimeoutError" -- the origin not answering in time. No tier can see
    mysqld on a box it cannot reach.

Both are ten seconds of question asked of the real store. Neither needs a
nine-hour campaign to ask it.

This REPORTS. It does not refuse and it does not gate: it prints what the
store did and exits 0 either way when run as a preflight, so a campaign never
fails to start because of something in here. Run with --strict from the
simulator, where a wrong answer IS a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEM = os.path.dirname(_HERE)
if _SEM not in sys.path:
    sys.path.insert(0, _SEM)


def probe(dbhost: str, service: str = "nb1.preflight.store",
          timeout: float = 20.0):
    """Round-trip one tuple whose value is an object keyed "0".."N-1".

    Returns (ok, findings) where findings is a list of (level, text).
    """
    from core import frognet_tuples as T

    out = []
    # Unreachable is not wrong. A store nobody can reach says nothing about
    # whether it round-trips correctly, and reporting it as a failure would
    # train everyone to ignore this.
    import socket as _sock
    from core.frognet_tuples import StoreUnreachable
    # "cannot reach" covers a refused connect and a name that does not
    # resolve. Neither says anything about whether the store round-trips, and
    # both are the normal case in a container or a netns.
    _NO_ROUTE = (StoreUnreachable, _sock.gaierror, ConnectionError)

    def _unreachable(e):
        """The name does not resolve or nothing is listening.

        frognet_tuples wraps the read path's failures as a bare StoreError,
        so the cause has to be unwrapped -- classifying by the outer type
        alone reports a container with no DNS as a broken store.
        """
        if isinstance(e, _NO_ROUTE):
            return True
        c = getattr(e, "__cause__", None)
        if isinstance(c, _NO_ROUTE):
            return True
        return "gaierror" in str(e) or "Connection refused" in str(e)
    # The payload is the exact shape that does not survive a PHP associative
    # round trip: keys "0".."N-1", contiguous from zero. This is a DDP hook's
    # pub_n, bucket index -> publish count.
    sent = {"pub_n": {"0": 5, "1": 7}, "list": [1, 2, 3], "step": 40}
    var = "probe.%d" % int(time.time())

    t0 = time.time()
    try:
        T.put(service, var, "preflight", sent, dbhost=dbhost, timeout=timeout)
    except Exception as e:
        if _unreachable(e):
            out.append(("SKIP", "%s is not reachable from here: %r"
                        % (dbhost, e)))
            return None, out
        out.append(("FAIL", "write to %s failed after %.1fs: %r"
                    % (dbhost, time.time() - t0, e)))
        return False, out
    w_ms = (time.time() - t0) * 1000.0

    t1 = time.time()
    try:
        # SensorName is SD:<var>.<scope>; the match is a prefix over the var
        # and a wildcard over the scope, exactly as torch_store does it. The
        # first version of this read dropped the SD: prefix, found nothing,
        # and reported a healthy store as broken -- a preflight that cries
        # wolf is worse than no preflight.
        rows = T.get_all(service, dbhost=dbhost,
                         name_like="%s%s.%%" % (T.SD_PREFIX, var),
                         timeout=timeout)
    except Exception as e:
        if _unreachable(e):
            out.append(("SKIP", "%s is not reachable from here: %r"
                        % (dbhost, e)))
            return None, out
        out.append(("FAIL", "read back from %s failed after %.1fs: %r"
                    % (dbhost, time.time() - t1, e)))
        return False, out
    r_ms = (time.time() - t1) * 1000.0

    out.append(("INFO", "write %.0f ms, read %.0f ms" % (w_ms, r_ms)))
    if w_ms > 5000 or r_ms > 5000:
        out.append(("WARN", "the store is answering slowly. ddp5706 died at "
                            "the gate on an origin TimeoutError; a get_all "
                            "this slow is the same condition earlier."))

    got = None
    for r in rows:
        if isinstance(r, dict) and r.get("value") is not None:
            got = r["value"]
            break
    if got is None:
        out.append(("FAIL",
                    "the row was written but did not read back under "
                    "SensorType=%s, SensorName like %s%s.%%: %r"
                    % (service, T.SD_PREFIX, var, rows)))
        return False, out

    ok = True
    pn = got.get("pub_n") if isinstance(got, dict) else None
    if isinstance(pn, dict):
        out.append(("PASS", "an object keyed \"0\"..\"N-1\" survives the "
                            "store: pub_n came back %s" % json.dumps(pn)))
    else:
        ok = False
        out.append(("FAIL",
                    "pub_n went out as %s and came back as %s (%s). "
                    "api.php on %s is flattening objects keyed \"0\"..\"N-1\" "
                    "into arrays -- it does NOT carry "
                    "[AN_OBJECT_IS_NOT_A_LIST_V1]. Every hook arm that calls "
                    "drift() will die with \"'list' object has no attribute "
                    "'get'\". Check /var/www/html/api.php on the machine "
                    "serving this name, which is not necessarily the machine "
                    "whose tree you just updated."
                    % (json.dumps(sent["pub_n"]), json.dumps(pn),
                       type(pn).__name__, dbhost)))

    lst = got.get("list") if isinstance(got, dict) else None
    if lst == [1, 2, 3]:
        out.append(("PASS", "and a real list is still a list"))
    else:
        ok = False
        out.append(("FAIL", "a genuine JSON array came back as %r -- the "
                            "round trip is wrong in the other direction too"
                    % (lst,)))
    return ok, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dbhost", default=os.environ.get(
        "FROGNET_DBHOST", "databasehost.frognet:80"))
    ap.add_argument("--service", default="nb1.preflight.store")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on a wrong answer. Off by default so "
                         "a campaign preflight reports and never blocks.")
    a = ap.parse_args(argv)

    print("=== STORE PREFLIGHT: %s ===" % a.dbhost)
    try:
        ok, findings = probe(a.dbhost, a.service, a.timeout)
    except Exception as e:
        print("  [SKIP] could not reach the store at all: %r" % (e,))
        print("         This says nothing about the store's correctness.")
        return 0
    for level, text in findings:
        print("  [%s] %s" % (level, text))
    if ok is False:
        # [SAY_WHICH_FAILURE_V1] The first version said "the hook arms will
        # fail, the no-hook and gloo arms will not" for EVERY failure. That is
        # true of a store that flattens objects and false of a store that is
        # down -- which is what it printed when the databasehost ran out of
        # MySQL connections, where every arm dies at the gate. A preflight
        # that draws the same conclusion from any evidence is not evidence.
        flat = any("flattening" in t for _, t in findings)
        if flat:
            print("\n  The hook arms of this campaign will fail at their "
                  "first step. The no-hook and gloo arms will not -- they "
                  "never call drift().")
        else:
            print("\n  The store did not answer correctly. EVERY arm fails "
                  "at the gate, gloo included: rank arrival is a store "
                  "write. This is not a hook problem.")
    if a.strict:
        return 1 if ok is False else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
