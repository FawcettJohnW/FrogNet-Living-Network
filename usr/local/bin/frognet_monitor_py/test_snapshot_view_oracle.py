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
"""test_snapshot_view_oracle.py -- [ONE_MONITOR_TRUTH_V1] what the Python monitor shows from a node snapshot: the
title (name, .1), mode, build, ponds (forward marked), hub link, the member rows with the databasehost/control marks,
and no broker vocabulary anywhere on the screen or in --once's frame. Uses the shared vectors' snapshot inputs, the
curses draw functions on a recording window, and once.frame_lines.

    PYTHONPATH=/usr/local/bin python3 -m frognet_monitor_py.test_snapshot_view_oracle
"""
import curses
import json
import os
import sys

VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frognet_monitor_shared", "parser_vectors.json")
FAILS = []


def check(cond, msg):
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


class Win:
    """A curses window that records what is written."""
    def __init__(self):
        self.rows = {}

    def getmaxyx(self):
        return (60, 120)

    def addnstr(self, y, x, text, n, attr=0):
        line = self.rows.get(y, "")
        line = line.ljust(x) + text[:n]
        self.rows[y] = line

    def text(self):
        return "\n".join(self.rows[k] for k in sorted(self.rows))


def main():
    curses.color_pair = lambda n: 0                  # no terminal here; the colour is not what is checked
    from frognet_monitor_py import discovery
    from frognet_monitor_py.draw_dashboard import draw_title
    from frognet_monitor_py.draw_ponds import draw_ponds
    from frognet_monitor_py.once import frame_lines
    cases = {c["name"]: c for c in json.load(open(os.path.normpath(VECTORS)))["snapshot"]["cases"]}
    region = discovery.parse_snapshot(cases["region node: forward pond, a participant pond, hub link, a device"]["input"])
    lan = discovery.parse_snapshot(cases["LAN-only node: no ponds, no hub, members from LAN discovery"]["input"])

    w = Win()
    r = draw_title(w, 0, region)
    draw_ponds(w, r, region["raw"])
    scr = w.text()
    check("N1 (10.81.81.1)" in scr, "title: the snapshot's name and .1")
    check("mode region" in scr and "build fdf-2026-10-12-r20 546df37+changes" in scr, "mode and build (with commit)")
    check("ponds  *test  other" in scr, "ponds, forward marked *")
    check("hub    wg0 17s" in scr, "hub link: iface and handshake age")
    check("Ponds" in scr and "* test" in scr and "forward" in scr and "2 members" in scr, "Ponds: the forward pond counts the snapshot's members")
    check("q            node     ONLINE  10.9.9.1" in scr, "Ponds: a participant pond lists its own members")

    f = "\n".join(frame_lines(region, {"10.82.82.1": {"echo": ("OK", 3)}}))
    check("+ N1             SELF" in f, "--once: self row, control-host mark")
    check("* N2             ONLINE 3ms" in f, "--once: databasehost mark, echo result")
    check("phone          DEVICE DOWN  leaf" in f, "--once: a device row from its region session")
    check("elections  databasehost=10.82.82.1  databasehost_control=10.81.81.1" in f, "--once: elections")

    w2 = Win()
    draw_title(w2, 0, lan)
    s2 = w2.text()
    check("mode lan-only" in s2 and "ponds  none, hub none (LAN-only: no /opt/fdf/var/join.env)" in s2, "LAN-only: no ponds, no hub, why")
    f2 = "\n".join(frame_lines(lan, {}))
    check("G2             DOWN FAIL    node     10.62.62.1      lan" in f2, "LAN-only: member from LAN discovery, source lan")

    w3 = Win()
    draw_title(w3, 0, None)
    check("getSnapshot.php did not answer" in w3.text(), "no snapshot: said, nothing in its place")

    allout = scr + f + s2 + f2 + w3.text()
    check("broker" not in allout.lower(), "no broker vocabulary on the screen or in the frame")
    print("%d failed" % len(FAILS))
    print("PASS" if not FAILS else "FAILED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
