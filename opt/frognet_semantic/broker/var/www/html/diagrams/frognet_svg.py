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
frognet_svg.py — shared SVG primitives + the FrogNet "Instrument Dossier" palette.

No third-party dependencies. Every diagram script builds a plain SVG string
from these helpers and writes it to ../assets/diagrams/<name>.svg.

Palette and fonts mirror site.css exactly so the generated art sits inside the
site's dark instrument panels and light engineering dossier without a seam.
"""

from html import escape as _esc

# ---- palette (mirrors site.css :root) --------------------------------------
P = {
    # instrument (dark)
    "abyss": "#091416", "abyss2": "#0b191c",
    "petrol": "#0d2226", "petrol2": "#10292e",
    "hair": "#1b3a40",
    "mist": "#cdd9d7", "mist_dim": "#7b938f", "mist_faint": "#536763",
    # dossier (light)
    "paper": "#f3efe6", "paper2": "#ebe5d8", "paper3": "#e3dccb",
    "rule": "#d7cfbd", "rule2": "#c8bfa9",
    "ink": "#15201e", "ink2": "#33403d", "ink_dim": "#5d6a66",
    # signals
    "green": "#3DBA6A", "green2": "#54cf80", "green_deep": "#1f6b43",
    "green_ink": "#06210f",
    "lily": "#4faa52",
    "coral": "#d6593a",
}

# fonts (fall back gracefully when the SVG is loaded in isolation via <img>)
DISP = "'Space Grotesk', system-ui, sans-serif"      # display / headings
SANS = "'IBM Plex Sans', system-ui, sans-serif"      # body
MONO = "'IBM Plex Mono', ui-monospace, monospace"    # data / labels


def _fmt(v):
    """Trim floats to short strings so output is stable and small."""
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


def svg(width, height, body, bg=None, pad_style=""):
    """Wrap body in an <svg> root. Optional solid background rect."""
    rect = (f'<rect width="{width}" height="{height}" fill="{bg}"/>'
            if bg else "")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'font-family="{SANS}" role="img"{pad_style}>\n{rect}\n{body}\n</svg>\n'
    )


def rect(x, y, w, h, fill="none", stroke="none", sw=1, rx=0, opacity=None,
         dash=None):
    a = [f'x="{_fmt(x)}"', f'y="{_fmt(y)}"',
         f'width="{_fmt(w)}"', f'height="{_fmt(h)}"']
    if rx:
        a.append(f'rx="{_fmt(rx)}"')
    a.append(f'fill="{fill}"')
    if stroke != "none":
        a.append(f'stroke="{stroke}"')
        a.append(f'stroke-width="{_fmt(sw)}"')
    if dash:
        a.append(f'stroke-dasharray="{dash}"')
    if opacity is not None:
        a.append(f'opacity="{_fmt(opacity)}"')
    return f'<rect {" ".join(a)}/>'


def line(x1, y1, x2, y2, stroke, sw=1, dash=None, cap="butt", opacity=None):
    a = [f'x1="{_fmt(x1)}"', f'y1="{_fmt(y1)}"',
         f'x2="{_fmt(x2)}"', f'y2="{_fmt(y2)}"',
         f'stroke="{stroke}"', f'stroke-width="{_fmt(sw)}"',
         f'stroke-linecap="{cap}"']
    if dash:
        a.append(f'stroke-dasharray="{dash}"')
    if opacity is not None:
        a.append(f'opacity="{_fmt(opacity)}"')
    return f'<line {" ".join(a)}/>'


def polyline(pts, stroke, sw=2, fill="none", dash=None, cap="round",
             join="round"):
    d = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
    a = [f'points="{d}"', f'fill="{fill}"', f'stroke="{stroke}"',
         f'stroke-width="{_fmt(sw)}"', f'stroke-linecap="{cap}"',
         f'stroke-linejoin="{join}"']
    if dash:
        a.append(f'stroke-dasharray="{dash}"')
    return f'<polyline {" ".join(a)}/>'


def path(d, stroke="none", fill="none", sw=1, dash=None, cap="round"):
    a = [f'd="{d}"', f'fill="{fill}"']
    if stroke != "none":
        a += [f'stroke="{stroke}"', f'stroke-width="{_fmt(sw)}"',
              f'stroke-linecap="{cap}"']
    if dash:
        a.append(f'stroke-dasharray="{dash}"')
    return f'<path {" ".join(a)}/>'


def circle(cx, cy, r, fill="none", stroke="none", sw=1):
    a = [f'cx="{_fmt(cx)}"', f'cy="{_fmt(cy)}"', f'r="{_fmt(r)}"',
         f'fill="{fill}"']
    if stroke != "none":
        a += [f'stroke="{stroke}"', f'stroke-width="{_fmt(sw)}"']
    return f'<circle {" ".join(a)}/>'


def text(x, y, s, size=13, fill=P["ink"], font=SANS, weight=400,
         anchor="start", ls=None, italic=False, upper=False):
    a = [f'x="{_fmt(x)}"', f'y="{_fmt(y)}"',
         f'font-family="{font}"', f'font-size="{_fmt(size)}"',
         f'fill="{fill}"', f'font-weight="{weight}"',
         f'text-anchor="{anchor}"']
    if ls is not None:
        a.append(f'letter-spacing="{_fmt(ls)}"')
    if italic:
        a.append('font-style="italic"')
    body = _esc(s.upper() if upper else s)
    return f'<text {" ".join(a)}>{body}</text>'


def star(cx, cy, r, fill):
    """Small 5-point star used to mark an elected host."""
    import math
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.42
        ang = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d = "M" + " L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts) + " Z"
    return path(d, fill=fill)


def arrow_marker(idc, color):
    """A reusable arrowhead marker <defs> block."""
    return (
        f'<defs><marker id="{idc}" markerWidth="9" markerHeight="9" '
        f'refX="7" refY="4.5" orient="auto">'
        f'<path d="M0 0 L8 4.5 L0 9 Z" fill="{color}"/></marker></defs>'
    )


def write(name, markup, out_dir="../assets/diagrams"):
    """Write an SVG string to the site's diagram asset folder."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, name)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(markup)
    print(f"wrote {dest}  ({len(markup):,} bytes)")
    return dest
