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
frognet_monitor/draw_ponds.py -- [PONDS_ARE_LISTED_V1] the Ponds section: every pond this machine is in, from the
node snapshot's `ponds` (getSnapshot.php [ONE_MONITOR_TRUTH_V1]; a participant pond's members read live from that
pond's RAM manager). The peer table above is the FORWARD pond (it routes: the snapshot's `members`); this section adds
the others, so one screen shows every network the machine takes part in:

  Ponds
   * pondA      forward      node       3 members   config reach none, map refused
     pondB      participant  reacher    4 members   reach all as 10.252.0.1 (aliased 1)
       b          node      ONLINE   10.8.8.1
       c          node      ONLINE   10.7.7.1 -> 100.64.0.1
     pondC      participant  REFUSED: 10.7.7.0/24 collides with ...

A participant pond's members are listed (name, role, ONLINE/DOWN, address there -> its alias here), not probed: this
machine routes none of them unless it reaches that pond, and then only what the reach says.
"""
import curses
import ipaddress

from .config import CP_GREEN, CP_RED, CP_YELLOW, CP_CYAN, CP_DIM, CP_HEADER
from .ui_helpers import safe_addstr


def _alias_of(ip, alias):
    """the address an IP of the other network has HERE, through the reach's 1:1 aliases, or ''"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    for al, real in (alias or {}).items():
        rn, an = ipaddress.ip_network(real), ipaddress.ip_network(al)
        if a in rn:
            return str(an.network_address + (int(a) - int(rn.network_address)))
    return ""


def _ago(ts):
    import time
    try:
        d = int(time.time() - float(ts))
    except (TypeError, ValueError):
        return "-"
    return "%ds" % d if d < 120 else "%dm" % (d // 60) if d < 7200 else "%dh" % (d // 3600)


def telem_lines(p):
    """[TELEMETRY_IS_A_TUPLE_V1] the pond's (pond, telem) tuples, one line per device: what it is, where, its battery,
    its network, how long ago it said so. The tuple is the device's own: a device that left has none."""
    out = []
    for t in p.get("telem") or []:
        loc = t.get("loc") or {}
        where = ("%.5f,%.5f +-%.0fm" % (loc["lat"], loc["lon"], loc.get("acc_m") or 0)) if "lat" in loc and "lon" in loc else "no position"
        b = t.get("battery") or {}
        batt = ("batt %d%%%s" % (b["pct"], "+" if b.get("charging") else "")) if "pct" in b else ""
        net = (t.get("net") or {}).get("type", "")
        env = t.get("env") or {}
        e = ("  %.1f hPa" % env["pressure_hpa"]) if "pressure_hpa" in env else ""
        line = "     telem %-12s %-8s %s  %s  %s%s  (%s ago)" % (str(t.get("name", "?"))[:12], str(t.get("kind", ""))[:8], where, batt, net, e, _ago(t.get("at")))
        out.append((line, CP_CYAN))
    return out


def pond_lines(data):
    """[(text, color_pair)] for the section, from the snapshot (getSnapshot.php's object). Pure: the tests call it too.
    The forward pond's entry carries no members: they are the snapshot's `members` (the peer table), counted here."""
    out = []
    ponds = (data or {}).get("ponds") or []
    if not ponds:
        return out
    top = [m for m in (data or {}).get("members") or [] if isinstance(m, dict)]
    out.append(("Ponds", CP_HEADER))
    for p in ponds:
        mark = "*" if p.get("forward") else " "
        if p.get("forward"):
            me = next(({"role": m.get("role")} for m in top if m.get("self") is True), {})
            n = len([m for m in top if m.get("alive") is True])
        else:
            me = p.get("me") or {}
            n = len([m for m in p.get("members") or [] if m.get("alive")])
        head = " %s %-10s %-12s %-9s %2d members " % (mark, p.get("area", "?")[:10], "forward" if p.get("forward") else "participant",
                                                       (me.get("role") or "-")[:9], n)
        if p.get("error"):
            out.append((head + " " + p["error"], CP_RED)); continue
        if p.get("refused"):
            out.append((head + " REFUSED: " + p["refused"], CP_RED)); continue
        r = p.get("reach")
        if r:
            al = r.get("alias") or {}
            out.append((head + " reach %s as %s%s" % (r.get("reach"), r.get("as"), " (aliased %d)" % len(al) if al else ""), CP_GREEN))
        else:
            c = p.get("config")
            out.append((head + (" config reach %s, map %s" % (c.get("reach"), c.get("map")) if c else " publishes no config"),
                        CP_DIM if not p.get("forward") else 0))
        out.extend(telem_lines(p))                    # [TELEMETRY_IS_A_TUPLE_V1] its devices' own reports
        if p.get("forward"):
            continue                                  # its members are the peer table above
        for m in p.get("members") or []:
            if m.get("me"):
                continue
            ip = m.get("ip") or ""
            here = _alias_of(ip, (r or {}).get("alias")) if r else ""
            line = "     %-12s %-8s %-7s %s%s" % (m.get("name", "?")[:12], m.get("role", "")[:8], "ONLINE" if m.get("alive") else "DOWN",
                                                  ip, (" -> " + here) if here else "")
            out.append((line, CP_GREEN if m.get("alive") else CP_RED))
    return out


def draw_ponds(win, row, data):
    lines = pond_lines(data)
    if not lines:
        return row
    row += 1
    for text, cp in lines:
        safe_addstr(win, row, 2, text, curses.color_pair(cp) | (curses.A_BOLD if cp == CP_HEADER else 0))
        row += 1
    return row
