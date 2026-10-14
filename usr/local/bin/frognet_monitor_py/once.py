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
frognet_monitor/once.py -- `frognet_monitor --once`: one frame as plain text on stdout, no terminal control, then exit.
The same snapshot read (getSnapshot.php [ONE_MONITOR_TRUTH_V1]) and the same probes as the dashboard, the same words.
Exit 0 with a frame, 1 when the snapshot did not answer (the frame then says so).
"""
import time

from . import discovery
from .draw_dashboard import title_text, node_lines
from .draw_ponds import pond_lines
from .probes import probe_all


def frame_lines(snap, results):
    """The frame, pure: (snapshot summary, probe results) -> [str]. Marks: * databasehost, + control host, # both."""
    out = [title_text(snap) + "   " + time.strftime("%Y-%m-%d %H:%M:%S")]
    out += [t for t, _ in node_lines(snap)]
    if not snap:
        return out
    raw = snap["raw"]
    el = raw.get("elections") or {}
    out.append("")
    out.append("  %-2s%-14s %-12s %-8s %-15s %s" % ("", "MEMBER", "STATUS", "ROLE", "IP", "SOURCE"))
    for m in raw.get("members") or []:
        ip = m.get("ip", "")
        db, ctl = ip == el.get("databasehost"), ip == el.get("databasehost_control")
        mark = "#" if db and ctl else "*" if db else "+" if ctl else " "
        if m.get("self") is True:
            st = "SELF"
        elif m.get("role") == "leaf":
            st = "DEVICE " + ("ONLINE" if m.get("alive") is True else "DOWN")
        else:
            e = (results.get(ip) or {}).get("echo", ("FAIL", 0))
            st = ("ONLINE %dms" % e[1]) if e[0] == "OK" else ("DOWN " + e[0])
            if m.get("alive") is not True:
                st += " (region: down)"
        out.append("  %-2s%-14s %-12s %-8s %-15s %s" % (mark, str(m.get("name", ""))[:14], st, m.get("role", ""), ip,
                                                       m.get("source", "")))
    if not raw.get("members"):
        out.append("  (no members: %s)" % (raw.get("sources") or {}).get("members", ""))
    pl = pond_lines(raw)
    if pl:
        out.append("")
        out += ["  " + t for t, _ in pl]
    out.append("")
    out.append("  elections  " + ("  ".join("%s=%s" % kv for kv in sorted(el.items())) or "none in /etc/hosts"))
    inet = (results.get("__internet__") or {}).get("result", ("FAIL", 0))
    out.append("  internet   " + ("OK %dms" % inet[1] if inet[0] == "OK" else "FAIL"))
    return out


def run_once() -> int:
    snap = discovery.fetch_snapshot()
    results = probe_all(discovery.hosts_of(snap)) if snap else {}
    print("\n".join(frame_lines(snap, results)), flush=True)
    return 0 if snap else 1
