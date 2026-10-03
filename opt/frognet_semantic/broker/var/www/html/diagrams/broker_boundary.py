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
broker_boundary.py — products page, Broker section (dark instrument panel).

The broker introduces nodes across the internet and hands out WireGuard
tunnels. Cross-internet, the encrypted tunnels meet at the broker, which
relays ciphertext it cannot read and holds no authoritative state. On one LAN
it is never in the data path at all.
"""
from frognet_svg import (P, DISP, SANS, MONO, svg, rect, line, circle, text,
                         path, write)

W, H = 880, 360


def _pond(x, y, w, h, title, nodes):
    b = [rect(x, y, w, h, fill=P["petrol"], stroke=P["hair"], sw=1.4, rx=8)]
    b.append(text(x + 14, y + 24, title, size=12, fill=P["mist_dim"],
                  font=MONO, weight=500, ls=1))
    # internal mesh
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            b.append(line(*nodes[i], *nodes[j], P["hair"], 1.2))
    for cx, cy in nodes:
        b.append(circle(cx, cy, 8, fill=P["abyss"], stroke=P["green"], sw=2))
        b.append(circle(cx, cy, 2.6, fill=P["green"]))
    return "".join(b)


def _tunnel(x1, x2, y, label):
    b = [rect(x1, y - 7, x2 - x1, 14, fill=P["petrol2"],
              stroke=P["green_deep"], sw=1.3, rx=7)]
    # hatch to read as an encrypted pipe
    xx = x1 + 10
    while xx < x2 - 6:
        b.append(line(xx, y - 6, xx + 6, y + 6, P["green_deep"], 1,
                      opacity=0.5))
        xx += 11
    # lock glyph mid-pipe
    mx = (x1 + x2) / 2
    b.append(rect(mx - 7, y - 4, 14, 11, fill=P["abyss"],
                  stroke=P["green"], sw=1.2, rx=2))
    b.append(path(f"M {mx-3.5} {y-4} v-3 a 3.5 3.5 0 0 1 7 0 v3",
                  stroke=P["green"], sw=1.2))
    b.append(text(mx, y - 16, label, size=10.5, fill=P["green2"], font=MONO,
                  anchor="middle", ls=0.5))
    return "".join(b)


def build():
    b = []
    b.append(text(40, 34, "THE BROKER · TRUST BOUNDARY", size=12,
                  fill=P["mist_dim"], font=MONO, weight=500, ls=2))
    b.append(text(40, 54, "It relays what it cannot read.", size=17,
                  fill="#ffffff", font=DISP, weight=600))

    my = 150
    b.append(_pond(40, 90, 200, 130, "Seattle · pond",
                   [(90, 135), (190, 120), (140, 185)]))
    b.append(_pond(640, 90, 200, 130, "New York · pond",
                   [(690, 120), (790, 135), (740, 185)]))

    # broker box
    bx, bw = 390, 100
    b.append(rect(bx, my - 34, bw, 68, fill=P["petrol2"], stroke=P["green"],
                  sw=1.6, rx=6))
    b.append(text(bx + bw / 2, my - 6, "broker", size=16, fill="#ffffff",
                  font=DISP, weight=600, anchor="middle"))
    b.append(text(bx + bw / 2, my + 14, "rendezvous", size=11,
                  fill=P["mist_dim"], font=MONO, anchor="middle"))

    # tunnels meeting at the broker
    b.append(_tunnel(240, bx, my, "WireGuard · encrypted"))
    b.append(_tunnel(bx + bw, 640, my, "WireGuard · encrypted"))

    # trust boundary
    b.append(rect(bx - 26, my - 56, bw + 52, 112, fill="none",
                  stroke=P["coral"], sw=1.4, rx=10, dash="4 5"))
    b.append(text(bx + bw / 2, my + 74,
                  "sees ciphertext only · holds no truth · rebuilt from polls",
                  size=11.5, fill=P["coral"], font=SANS, anchor="middle"))

    # LAN footnote strip
    fy = 320
    b.append(line(40, fy - 22, 840, fy - 22, P["hair"], 1))
    b.append(circle(300, fy, 7, fill=P["abyss"], stroke=P["green"], sw=2))
    b.append(circle(430, fy, 7, fill=P["abyss"], stroke=P["green"], sw=2))
    b.append(line(308, fy, 422, fy, P["green"], 2))
    b.append(text(300, fy - 16, "node", size=10, fill=P["mist_dim"],
                  font=MONO, anchor="middle"))
    b.append(text(430, fy - 16, "node", size=10, fill=P["mist_dim"],
                  font=MONO, anchor="middle"))
    b.append(text(460, fy - 2, "On one LAN the broker is never in the path.",
                  size=12.5, fill=P["mist"], font=SANS, weight=500))
    b.append(text(460, fy + 15,
                  "Lose the broker service and every link keeps carrying traffic.",
                  size=12, fill=P["mist_dim"], font=SANS))

    return svg(W, H, "\n".join(b), bg=P["abyss"])


if __name__ == "__main__":
    write("broker-boundary.svg", build())
