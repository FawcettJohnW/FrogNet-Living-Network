#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_reader.py - [S4_READER_V1] the application on one node of the layered run (IS-IS routes, shared RAM above them):
it consumes the shared state the way an FD&F application does. One held read, and nothing else: fdf-events holds the one
session and, on each participant change (and at "ready"), pulls the snapshot over THAT SAME session and hands it here as
"<kind>\t<ms>\t<participants-json>". There is no second channel -- no HTTP poll, no polling loop -- so a reader that
reconnects after a cut gets the authoritative set in the one round trip, and far-side readers line up by construction
instead of each catching a different partial snapshot of its own. [SNAPSHOT_OVER_HELD_SESSION_V1] One line out per event:

  {"t": <unix s>, "ev": "ready"|"p", "alive": [names...], "with_facts": [names whose facts fdf-facts has written],
   "read_ms": n, "read_bytes": n}
  {"t": ..., "ev": "gone", "why": "..."}          the session ended: the app's view is gone (it exits; restarted)

No timer and no retry loop here: an application that lost its session is restarted by its supervisor, as a unit is.
env: FDF_BIN
"""
import json
import os
import subprocess
import sys
import time


def say(d):
    sys.stdout.write(json.dumps(d) + "\n")
    sys.stdout.flush()


def main():
    ev = subprocess.Popen([os.path.join(os.environ["FDF_BIN"], "fdf-events")], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, bufsize=1)
    for line in ev.stdout:
        line = line.rstrip("\n")
        if line == "gone":
            break
        if line == "m":                             # a mapping changed, not the participant set: nothing to report
            continue
        kind, _, rest = line.partition("\t")        # "<kind>\t<ms>\t<participants-json>" from fdf-events' held session
        if kind not in ("ready", "p") or not rest:
            continue
        ms_s, _, body = rest.partition("\t")
        t0 = time.time()
        try:
            parts = json.loads(body).get("participants", [])
            ms = int(ms_s)
        except (ValueError, TypeError) as e:        # a malformed snapshot line: said, the next event carries the state
            say({"t": t0, "ev": kind, "error": str(e)[:200]})
            continue
        alive = sorted(p.get("name", "") for p in parts if p.get("alive"))
        with_facts = sorted(p.get("name", "") for p in parts if p.get("alive") and "addrs" in (p.get("facts") or {}))
        say({"t": t0, "ev": kind, "alive": alive, "with_facts": with_facts, "read_ms": ms, "read_bytes": len(body)})
    why = (ev.stderr.read() or "").strip().splitlines()
    say({"t": time.time(), "ev": "gone", "why": why[-1][:200] if why else ""})
    ev.kill()
    return 1


if __name__ == "__main__":
    sys.exit(main())
