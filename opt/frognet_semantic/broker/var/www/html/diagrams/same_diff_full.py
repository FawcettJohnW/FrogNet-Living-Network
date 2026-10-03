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
same_diff_full.py — products page, UnREST section (light dossier).

The web asks for the whole answer every time. UnREST teaches the template once
(FULL), then only the difference crosses: DIFF for what changed, a 16-byte SAME
when nothing did. Steady-state traffic lands ~16x lighter on the wire.
"""
from frognet_svg import (P, DISP, SANS, MONO, svg, rect, line, text, path,
                         write)

W, H = 880, 300
BAR_X = 210                 # where bars begin
SCALE = 0.135              # px per byte


def _blocks(x, y, specs, h=30):
    """specs: list of (bytes, fill, stroke, label_or_None)."""
    out, cx = [], x
    for by, fill, stroke, lab in specs:
        w = max(4, by * SCALE)
        out.append(rect(cx, y, w, h, fill=fill, stroke=stroke, sw=1.2, rx=2))
        if lab and w > 34:
            out.append(text(cx + w / 2, y + h / 2 + 4, lab, size=10.5,
                            fill="#ffffff", font=MONO, weight=500,
                            anchor="middle"))
        cx += w + 5
    return "".join(out), cx


def build():
    b = []
    b.append(text(30, 34, "UNREST · SAME / DIFF / FULL", size=12,
                  fill=P["ink_dim"], font=MONO, weight=500, ls=2))
    b.append(text(30, 54, "Ask once, then send only the difference.",
                  size=17, fill=P["ink"], font=DISP, weight=600))

    # Row A — conventional
    ya = 92
    b.append(text(30, ya + 20, "Conventional", size=13.5, fill=P["ink"],
                  font=SANS, weight=600))
    b.append(text(30, ya + 38, "every reply is the whole answer", size=11.5,
                  fill=P["ink_dim"], font=SANS))
    specs_a = [(480, P["coral"], P["coral"], None)] * 8
    mk, end_a = _blocks(BAR_X, ya, specs_a)
    b.append(mk)
    b.append(text(end_a + 12, ya + 20, "≈ 3,840 B", size=13,
                  fill=P["coral"], font=MONO, weight=600))

    # Row B — UnREST
    yb = 182
    b.append(text(30, yb + 20, "UnREST", size=13.5, fill=P["ink"],
                  font=SANS, weight=600))
    b.append(text(30, yb + 38, "template taught once, then diffs", size=11.5,
                  fill=P["ink_dim"], font=SANS))
    specs_b = ([(480, P["green_deep"], P["green_deep"], "FULL")]
               + [(64, P["green"], P["green"], None)] * 2
               + [(16, P["lily"], P["lily"], None)] * 8)
    mk, end_b = _blocks(BAR_X, yb, specs_b)
    b.append(mk)
    b.append(text(end_b + 12, yb + 20, "≈ 256 B steady state", size=13,
                  fill=P["green_deep"], font=MONO, weight=600))

    # the win, called out
    b.append(text(BAR_X, 262, "~16x lighter on the wire once the template is known",
                  size=12.5, fill=P["ink_dim"], font=SANS, italic=True))

    # legend
    lx = 555
    for i, (c, lab) in enumerate([
            (P["green_deep"], "FULL · 480 B, taught once"),
            (P["green"], "DIFF · what changed"),
            (P["lily"], "SAME · 16 B")]):
        yy = 40 + i * 18
        b.append(rect(lx, yy - 9, 13, 11, fill=c, rx=2))
        b.append(text(lx + 20, yy, lab, size=11.5, fill=P["ink_dim"],
                      font=SANS))

    return svg(W, H, "\n".join(b), bg=P["paper"])


if __name__ == "__main__":
    write("same-diff-full.svg", build())
