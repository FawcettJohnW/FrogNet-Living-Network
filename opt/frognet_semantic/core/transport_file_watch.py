#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/transport_file_watch.py -- [THE_TRANSPORT_FILE_IS_WATCHED_V1] restart the daemon and the proxy when the
node's transport file no longer says what they read.

John, 22 Nov 2026: "There needs to be a file watcher on it that restarts the proxy and daemon when it changes."

A daemon or proxy reads /etc/frognet/semantic_transport once, when it starts (core/homa_transport.py), and writes
what it read to /run/frognet/semantic_transport.read. systemd watches the file (frognet-transport-watch.path: the
kernel wakes it when the file is written, replaced, made or removed; nothing polls) and runs this once for each
change. This compares the file as it is now with what the running processes read:

  the same        nothing is done (an editor saved it unchanged, or something else in /etc/frognet changed)
  different       the file is checked; if it is one a process can start on, frognet-daemon and frognet-proxy are
                  restarted, and they read it
  not startable   nothing is restarted. A daemon restarted onto a file it cannot read would stop and stay stopped;
                  the running ones are left on what they have, and the line says what is wrong with the file.

Every line it prints begins [SEM-TRANSPORT] and goes to the journal of frognet-transport-watch.service.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILD = "transport-file-watch-v1-20261122a"
RESTART = ["systemctl", "restart", "frognet-daemon", "frognet-proxy"]


def why_not_startable(text: str):
    """None when a process can start on this text; otherwise what is wrong with it, in the words a process would say.
    The rules are core/homa_transport.semantic_transport's own."""
    from core import homa_transport as HT
    lines = [l.split() for l in text.splitlines() if l.strip()]
    word = lines[0][0] if lines and len(lines[0]) == 1 else " ".join(lines[0]) if lines else ""
    if "softhoma" in [l[-1] for l in lines if l]:
        return "it says softhoma; that driver is called chorus now"
    if word not in HT.DRIVERS:
        return "its first line says %r; the transport is one of %s" % (word, ", ".join(sorted(HT.DRIVERS)))
    seen = set()
    for l in lines[1:]:
        if len(l) != 2 or l[1] not in HT.DRIVERS or l[1] == "homasim" or l[0] in seen:
            return ("it says %r after its first line; each line there is a RAM's role name and one of %s, and a RAM is "
                    "named once" % (" ".join(l), ", ".join(sorted(HT.DRIVERS))))
        seen.add(l[0])
    return None


def main() -> int:
    from core import homa_transport as HT
    now = HT.transport_file_text()
    try:
        with open(HT.TRANSPORT_READ) as f:
            read = f.read().strip()
    except FileNotFoundError:
        read = None
    if read == now:
        print("[SEM-TRANSPORT] %s: %s says what the daemon and the proxy read at start; nothing to do"
              % (BUILD, HT.TRANSPORT_FILE), flush=True)
        return 0
    wrong = why_not_startable(now)
    if wrong:
        print("[SEM-TRANSPORT] %s: %s changed and the daemon and the proxy are NOT restarted: %s. They stay on what "
              "they read at start. Correct the file and they will be restarted then."
              % (BUILD, HT.TRANSPORT_FILE, wrong), flush=True)
        return 0
    print("[SEM-TRANSPORT] %s: %s changed (%s); restarting frognet-daemon and frognet-proxy. It says now: %s"
          % (BUILD, HT.TRANSPORT_FILE,
             "they read: " + " / ".join(read.splitlines()) if read is not None else "no record of what they read",
             " / ".join(now.splitlines())), flush=True)
    r = subprocess.run(RESTART)
    if r.returncode:
        print("[SEM-TRANSPORT] %s: `%s` ended with %d; the next change to the file tries again"
              % (BUILD, " ".join(RESTART), r.returncode), flush=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
