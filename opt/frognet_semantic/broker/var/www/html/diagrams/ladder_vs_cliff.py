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
ladder_vs_cliff.py — the-claim page (dark instrument panel).

The SotF adaptive ladder degrades in clean steps and climbs back with no
re-dial; conventional UDP media holds, then falls off a cliff when the link
starves. One shared link, plotted as capacity changes over time.
"""
from frognet_svg import (P, DISP, SANS, MONO, svg, rect, line, polyline,
                         circle, text, path, write)

W, H = 880, 440
X0, X1 = 150, 830           # plot x-range
T = lambda t: X0 + (X1 - X0) / 100.0 * t

# quality levels (y)
HD, P480, P300, HB, ZERO = 95, 155, 215, 290, 345
LEVELS = [(HD, "HD · 720p"), (P480, "≈ 480p"), (P300, "≈ 300 Kbps"),
          (HB, "heartbeat"), (ZERO, "0 · call dropped")]


def build():
    b = []
    # title
    b.append(text(150, 40, "ONE LINK, STARVING AND RECOVERING", size=12,
                  fill=P["mist_dim"], font=MONO, weight=500, ls=2))
    b.append(text(150, 60, "The ladder holds the call. The cliff drops it.",
                  size=17, fill="#ffffff", font=DISP, weight=600))

    # starved band
    b.append(rect(T(37), 78, T(60) - T(37), ZERO - 78, fill=P["coral"],
                  opacity=0.08))
    b.append(text((T(37) + T(60)) / 2, 96, "LINK STARVED", size=11,
                  fill=P["coral"], font=MONO, weight=500, anchor="middle",
                  ls=1.5))

    # gridlines + level labels
    for y, lab in LEVELS:
        b.append(line(X0, y, X1, y, P["hair"], 1,
                      dash=None if y == ZERO else "2 5"))
        b.append(text(X0 - 12, y + 4, lab, size=11.5, fill=P["mist_dim"],
                      font=MONO, anchor="end"))

    # conventional (coral): flat, then cliff to zero
    coral = [(0, HD), (43, HD), (43, ZERO), (100, ZERO)]
    b.append(polyline([(T(t), y) for t, y in coral[:3]], P["coral"], 2.6))
    b.append(polyline([(T(t), y) for t, y in coral[2:]], P["coral"], 2.2,
                      dash="3 6"))
    b.append(circle(T(43), ZERO, 4.5, fill=P["coral"]))
    b.append(text(T(43) + 10, ZERO - 12, "call drops", size=12,
                  fill=P["coral"], font=SANS, weight=500))

    # frognet SotF (green): staircase down and back up
    stairs = [(0, HD), (26, HD), (26, P480), (37, P480), (37, P300),
              (47, P300), (47, HB), (60, HB), (60, P300), (70, P300),
              (70, P480), (79, P480), (79, HD), (100, HD)]
    b.append(polyline([(T(t), y) for t, y in stairs], P["green"], 3))
    # step dots
    for t, y in stairs:
        b.append(circle(T(t), y, 3, fill=P["green"]))

    # annotations
    b.append(text((T(47) + T(60)) / 2, HB - 12, "holds a heartbeat",
                  size=12, fill=P["green2"], font=SANS, weight=500,
                  anchor="middle"))
    b.append(text(T(74), P480 - 14, "climbs back — no re-dial", size=12,
                  fill=P["green2"], font=SANS, weight=500, anchor="middle"))

    # x-axis caption
    b.append(text((X0 + X1) / 2, 385,
                  "link capacity over time  →", size=12, fill=P["mist_faint"],
                  font=MONO, anchor="middle", ls=1))

    # legend
    lx, ly = 560, 40
    b.append(rect(lx, ly - 9, 22, 4, fill=P["green"], rx=1))
    b.append(text(lx + 30, ly - 3, "FrogNet — degrades, never drops",
                  size=12, fill=P["mist"], font=SANS))
    b.append(rect(lx, ly + 9, 22, 4, fill=P["coral"], rx=1))
    b.append(text(lx + 30, ly + 15, "Conventional UDP — falls off a cliff",
                  size=12, fill=P["mist"], font=SANS))

    return svg(W, H, "\n".join(b), bg=P["abyss"])


if __name__ == "__main__":
    write("ladder-vs-cliff.svg", build())
