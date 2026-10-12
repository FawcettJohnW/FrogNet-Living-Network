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
split_and_merge.py — why page (light dossier).

A pond of nodes splits into two islands that each keep working and elect their
own host, then rejoins with no reconciliation when the path returns.
Observation 01: networks partition — as a matter of course, not as an error.
"""
from frognet_svg import (P, DISP, SANS, MONO, svg, rect, line, circle,
                         text, path, star, write)

W, H = 880, 330
PANEL_W = 250
PANELS_X = [25, 315, 605]
NY = 88                      # node area top

# node positions relative to a panel origin
NODES = {
    "A": (38, 34), "B": (94, 108), "C": (58, 168),     # left cluster
    "D": (212, 34), "E": (156, 108), "F": (190, 168),  # right cluster
}
LEFT, RIGHT = ("A", "B", "C"), ("D", "E", "F")
INTRA = [("A", "B"), ("B", "C"), ("A", "C"),
         ("D", "E"), ("E", "F"), ("D", "F")]
CROSS = [("B", "E"), ("A", "D")]


def _node(px, cx, cy, host=False):
    out = [circle(px + cx, NY + cy, 8.5,
                  fill=P["paper"], stroke=P["green_deep"], sw=2)]
    if host:
        out.append(star(px + cx, NY + cy, 6.5, P["green_deep"]))
    else:
        out.append(circle(px + cx, NY + cy, 3, fill=P["green_deep"]))
    return "".join(out)


def _edge(px, a, b, color=P["rule2"], dash=None, sw=1.6):
    ax, ay = NODES[a]
    bx, by = NODES[b]
    return line(px + ax, NY + ay, px + bx, NY + by, color, sw, dash=dash)


def _panel(px, kicker, caption, edges, hosts, break_line=False):
    b = [text(px + 4, 64, kicker, size=11.5, fill=P["ink_dim"], font=MONO,
              weight=600, ls=2)]
    for a, b_ in edges:
        b.append(_edge(px, a, b_))
    if break_line:
        cx = px + 125
        # jagged fracture line down the middle
        offs = [10, -12, 9, -10, 11, -9, 8]
        yy, i = 84, 0
        d = f"M {cx} {yy}"
        while yy < 268:
            yy += 26
            d += f" L {cx + offs[i % len(offs)]} {yy}"
            i += 1
        b.append(path(d, stroke=P["coral"], sw=1.8, dash="1 5"))
    for k, (cx, cy) in NODES.items():
        b.append(_node(px, cx, cy, host=(k in hosts)))
    b.append(text(px + 4, 292, caption, size=12.5, fill=P["ink_dim"],
                  font=SANS))
    return "".join(b)


def build():
    b = []
    b.append(text(25, 34, "OBSERVATION 01 · NETWORKS PARTITION", size=12,
                  fill=P["ink_dim"], font=MONO, weight=500, ls=2))
    b.append(text(25, 54, "A split is normal weather, not an outage.",
                  size=17, fill=P["ink"], font=DISP, weight=600))

    b.append(_panel(PANELS_X[0], "BEFORE",
                    "One pond. One elected host.",
                    INTRA + CROSS, {"B"}))
    b.append(_panel(PANELS_X[1], "SPLIT",
                    "Each island works, elects its own.",
                    INTRA, {"B", "E"}, break_line=True))
    b.append(_panel(PANELS_X[2], "MERGED",
                    "Path returns — rejoined, no sync step.",
                    INTRA + CROSS, {"B"}))

    # arrows between panels
    for x in (PANELS_X[1] - 33, PANELS_X[2] - 33):
        b.append(text(x, 170, "→", size=22, fill=P["rule2"], font=SANS,
                      anchor="middle"))

    # host legend
    b.append(star(640, 312, 6, P["green_deep"]))
    b.append(text(654, 316, "elected host (DatabaseHost)", size=11.5,
                  fill=P["ink_dim"], font=SANS))

    return svg(W, H, "\n".join(b), bg=P["paper"])


if __name__ == "__main__":
    write("split-and-merge.svg", build())
