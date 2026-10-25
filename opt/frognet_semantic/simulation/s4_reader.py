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
it consumes the shared state the way an FD&F application does. One held read (fdf-events: "ready", then "p"/"m" per
change, "gone" when the session ends); after each line one participants read; one line out per event:

  {"t": <unix s>, "ev": "p", "alive": [names...], "with_facts": [names whose facts fdf-facts has written], "read_ms": n,
   "read_bytes": n}
  {"t": ..., "ev": "gone", "why": "..."}          the session ended: the app's view is gone (it exits; restarted)

No timer and no retry loop here: an application that lost its session is restarted by its supervisor, as a unit is.
env: RINA_RAM, FDF_BIN
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "/FawcettInnovations.RINA.ram_interface.php"
RAM = os.environ["RINA_RAM"]


def say(d):
    sys.stdout.write(json.dumps(d) + "\n")
    sys.stdout.flush()


def participants():
    req = urllib.request.Request(f"http://{RAM}{API}?op=rina", data=b'{"op":"participants"}', method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    return json.loads(raw).get("participants", []), len(raw)


def main():
    ev = subprocess.Popen([os.path.join(os.environ["FDF_BIN"], "fdf-events")], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, bufsize=1)
    for line in ev.stdout:
        line = line.strip()
        if line == "gone":
            break
        t0 = time.time()
        try:
            parts, size = participants()
        except Exception as e:                      # the session is up but the read failed: said, the next event reads
            say({"t": t0, "ev": line, "error": str(e)[:200]})
            continue
        alive = sorted(p.get("name", "") for p in parts if p.get("alive"))
        with_facts = sorted(p.get("name", "") for p in parts if p.get("alive") and "addrs" in (p.get("facts") or {}))
        say({"t": t0, "ev": line, "alive": alive, "with_facts": with_facts, "read_ms": int((time.time() - t0) * 1000),
             "read_bytes": size})
    why = (ev.stderr.read() or "").strip().splitlines()
    say({"t": time.time(), "ev": "gone", "why": why[-1][:200] if why else ""})
    ev.kill()
    return 1


if __name__ == "__main__":
    sys.exit(main())
