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
"""Draw the eight rung emblems, in the medallion's own idiom: bold black ink on
white, the same weight as the frog silhouette, so a door still reads as the mark."""
import cv2, numpy as np, math

INK = (0, 0, 0)
S = 660                      # emblem canvas; the medallion is ~658px in the source


def plate():
    return np.full((S, S, 3), 255, np.uint8)


def _line(im, a, b, t=14):
    cv2.line(im, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), INK, t, cv2.LINE_AA)


def _rect(im, x, y, w, h, t=14):
    cv2.rectangle(im, (int(x), int(y)), (int(x + w), int(y + h)), INK, t, cv2.LINE_AA)


def _frect(im, x, y, w, h):
    cv2.rectangle(im, (int(x), int(y)), (int(x + w), int(y + h)), INK, -1, cv2.LINE_AA)


def _circ(im, x, y, r, t=14):
    cv2.circle(im, (int(x), int(y)), int(r), INK, t, cv2.LINE_AA)


def _fcirc(im, x, y, r):
    cv2.circle(im, (int(x), int(y)), int(r), INK, -1, cv2.LINE_AA)


def _arcs(im, x, y, r0, n=3, a0=-50, a1=50, step=52, t=11):
    for i in range(n):
        cv2.ellipse(im, (int(x), int(y)), (int(r0 + i * step), int(r0 + i * step)),
                    0, a0, a1, INK, t, cv2.LINE_AA)


def _poly(im, pts, fill=True, t=12):
    p = np.array([[int(a), int(b)] for a, b in pts], np.int32)
    if fill:
        cv2.fillPoly(im, [p], INK, cv2.LINE_AA)
    else:
        cv2.polylines(im, [p], True, INK, t, cv2.LINE_AA)


# -- L0 PULSE: a big SOS -- the signal that is nothing but presence --------------
def l0():
    """The floor rung carries no content: it says only that somebody is there.

    SOS is the exact right emblem for that, and it is also literally what L0 is --
    a fixed token, not a message. Rendered as the Morse itself above the letters,
    big enough to read the instant the door lands, because this is the door that is
    held on screen before the descent starts."""
    im = plate()
    # ... --- ...   drawn large, dots square, dashes long
    y = 176
    x = 58
    dot, dash, gap, h = 46, 128, 30, 46
    for group, mark in ((3, "dot"), (3, "dash"), (3, "dot")):
        for _ in range(group):
            w = dot if mark == "dot" else dash
            _frect(im, x, y, w, h)
            x += w + gap
        x += 26
        if mark == "dot" and x > S:
            break
    # the three groups do not fit on one line at this weight; lay them on three rows
    im[:] = 255
    rows = [("dot", 3), ("dash", 3), ("dot", 3)]
    y = 92
    for mark, n in rows:
        w = dot if mark == "dot" else dash
        total = n * w + (n - 1) * gap
        x = (S - total) // 2
        for _ in range(n):
            _frect(im, x, y, w, h)
            x += w + gap
        y += h + 42
    cv2.putText(im, "SOS", (int(S * 0.13), int(S * 0.90)), cv2.FONT_HERSHEY_DUPLEX,
                5.4, INK, 22, cv2.LINE_AA)
    return im


# -- L1 BEACON: a ship, semaphore flags ---------------------------------------
def l1():
    im = plate()
    _poly(im, [(96, 430), (564, 430), (498, 520), (162, 520)])          # hull
    _frect(im, 300, 300, 30, 130)                                       # mast
    _line(im, (315, 300), (315, 232), 16)
    _fcirc(im, 315, 214, 26)                                            # signaller
    _line(im, (300, 262), (176, 168), 20)                               # arms
    _line(im, (330, 262), (452, 200), 20)
    _poly(im, [(176, 168), (176, 78), (256, 124)])                      # flags
    _poly(im, [(452, 200), (452, 110), (372, 156)])
    for i, y in enumerate((392, 356)):                                  # rigging
        _line(im, (315, y), (150 + i * 30, 430), 7)
        _line(im, (315, y), (510 - i * 30, 430), 7)
    return im


# -- L2 WHISPER: a telegraph key ----------------------------------------------
def l2():
    im = plate()
    _frect(im, 120, 452, 420, 46)                                       # base
    _frect(im, 200, 396, 40, 62)                                        # pivot post
    _poly(im, [(150, 336), (520, 296), (528, 340), (158, 380)])         # lever
    _fcirc(im, 520, 318, 44)                                            # knob
    cv2.circle(im, (520, 318), 18, (255, 255, 255), -1, cv2.LINE_AA)
    _frect(im, 366, 396, 90, 56)                                        # contact
    for i, (x, w) in enumerate(((120, 34), (176, 34), (232, 96), (352, 34),
                                (408, 96))):                            # dots + dashes
        _frect(im, x, 168, w, 26)
    return im


# -- L3 VOICE: an old-time radio (mono) ---------------------------------------
def l3():
    im = plate()
    pts = []
    for a in range(180, 361):                                           # cathedral arch
        pts.append((330 + 214 * math.cos(math.radians(a)),
                    300 + 214 * math.sin(math.radians(a))))
    pts += [(544, 520), (116, 520)]
    _poly(im, pts, fill=False, t=26)
    _circ(im, 330, 260, 108, 20)                                        # grille
    for i in range(-2, 3):
        _line(im, (330 + i * 38, 172), (330 + i * 38, 348), 12)
    _fcirc(im, 240, 434, 30)                                            # one dial: mono
    _frect(im, 316, 414, 130, 40)
    return im


# -- L4 SOLO: an 80s boombox (stereo) -----------------------------------------
def l4():
    im = plate()
    _rect(im, 70, 208, 520, 300, 24)                                    # body
    _circ(im, 178, 358, 88, 22)                                         # two speakers
    _circ(im, 482, 358, 88, 22)
    _fcirc(im, 178, 358, 26)
    _fcirc(im, 482, 358, 26)
    _rect(im, 268, 268, 124, 88, 16)                                    # cassette door
    _line(im, (288, 312), (372, 312), 10)
    for i in range(5):                                                  # transport keys
        _frect(im, 272 + i * 26, 400, 18, 46)
    _poly(im, [(200, 208), (200, 150), (460, 150), (460, 208)], fill=False, t=22)
    return im


# -- L5 DUET: an early TV (360p, grayscale) -----------------------------------
def l5():
    im = plate()
    _line(im, (330, 176), (150, 62), 16)                                # rabbit ears
    _line(im, (330, 176), (510, 62), 16)
    _fcirc(im, 150, 62, 18)
    _fcirc(im, 510, 62, 18)
    _rect(im, 92, 176, 476, 308, 26)                                    # cabinet
    cv2.ellipse(im, (300, 330), (150, 116), 0, 0, 360, INK, 20, cv2.LINE_AA)
    _fcirc(im, 500, 288, 26)                                            # two dials
    _fcirc(im, 500, 372, 26)
    _line(im, (150, 484), (118, 560), 20)                               # splayed legs
    _line(im, (510, 484), (542, 560), 20)
    return im


# -- L6 ENSEMBLE: 60s home movies (480p colour) -------------------------------
def l6():
    im = plate()
    _circ(im, 214, 178, 104, 22)                                        # two reels
    _circ(im, 430, 178, 104, 22)
    _fcirc(im, 214, 178, 22)
    _fcirc(im, 430, 178, 22)
    _rect(im, 128, 300, 390, 150, 24)                                   # body
    _poly(im, [(518, 330), (600, 300), (600, 450), (518, 420)])         # lens
    for i in range(4):                                                  # the beam
        _line(im, (600, 342 + i * 22), (648, 300 + i * 50), 8)
    _line(im, (168, 450), (168, 512), 20)
    _frect(im, 118, 502, 300, 26)
    return im


# -- L7 CHORUS: a modern TV (720p) --------------------------------------------
def l7():
    im = plate()
    _rect(im, 60, 168, 540, 300, 18)                                    # thin bezel
    _frect(im, 300, 468, 60, 62)                                        # stand
    _frect(im, 200, 530, 260, 28)
    for i in range(3):                                                  # signal, full
        cv2.ellipse(im, (150, 400), (60 + i * 52, 60 + i * 52), 0, -92, -2,
                    INK, 12, cv2.LINE_AA)
    return im


DRAW = [l0, l1, l2, l3, l4, l5, l6, l7]
NAMES = ["PULSE", "BEACON", "WHISPER", "VOICE", "SOLO", "DUET", "ENSEMBLE", "CHORUS"]
CAPTION = ["banging on a pipe", "semaphore", "telegraph", "radio",
           "boombox", "early TV", "home movies", "modern TV"]
