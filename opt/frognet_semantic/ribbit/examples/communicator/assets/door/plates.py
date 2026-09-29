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
"""Build eight MST3K-style bulkhead doors.

The last pass put an emblem inside the logo's medallion. That kept the branding but
made eight nearly identical rings -- and MST3K's doors work precisely because they
are NOT identical: each is its own object, a different cut of metal with a different
frame, and the graphic on it is huge and bright against dark. So these are doors.

  - a dark plate with a heavy frame and corner bolts
  - a distinct cut per rung, cycling square / rounded / octagonal / porthole, so no
    two consecutive doors read the same
  - the emblem in LIGHT ink, large and centred, because the whole sequence happens
    against a dark portal and black-on-white flashed
  - a signage strip carrying the rung, the way a bulkhead is stencilled
  - the accent brightening as the ladder climbs: L0 is nearly dead metal, L7 is lit.
    Diving through them should feel like coming up into the light.
  - a visible seam down the split axis, so the parting reads as a door and not as an
    image being torn
"""
import cv2, numpy as np, math, sys
sys.path.insert(0, "/home/claude/emblem")
import emblems

R = 920                                   # render size; leaves are cut down from this
PLATE = (34, 30, 15)                      # BGR, a shade off the water palette
DEEP = (24, 21, 11)
BOLT = (78, 104, 92)


def _ramp(i):
    """Accent per rung: dim at the floor, bright at the top."""
    f = i / 7.0
    cold = np.array([88, 118, 62], float)          # BGR, muted reed
    hot = np.array([160, 209, 127], float)         # BGR, lily
    c = cold + (hot - cold) * (f ** 0.8)
    return tuple(int(v) for v in c)


def _cut_mask(i):
    """The door's outline. Four shapes, cycled, so neighbours never match."""
    m = np.zeros((R, R), np.uint8)
    pad = 26
    shape = i % 4
    if shape == 0:                                  # square plate
        cv2.rectangle(m, (pad, pad), (R - pad, R - pad), 255, -1)
    elif shape == 1:                                # rounded plate
        r = 120
        cv2.rectangle(m, (pad + r, pad), (R - pad - r, R - pad), 255, -1)
        cv2.rectangle(m, (pad, pad + r), (R - pad, R - pad - r), 255, -1)
        for cx, cy in ((pad + r, pad + r), (R - pad - r, pad + r),
                       (pad + r, R - pad - r), (R - pad - r, R - pad - r)):
            cv2.circle(m, (cx, cy), r, 255, -1)
    elif shape == 2:                                # octagonal
        c = 210
        pts = [(pad + c, pad), (R - pad - c, pad), (R - pad, pad + c),
               (R - pad, R - pad - c), (R - pad - c, R - pad), (pad + c, R - pad),
               (pad, R - pad - c), (pad, pad + c)]
        cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
    else:                                           # porthole in a plate
        cv2.rectangle(m, (pad, pad), (R - pad, R - pad), 255, -1)
    return m


def plate(i, orient):
    acc = _ramp(i)
    im = np.zeros((R, R, 3), np.uint8)
    im[:, :] = DEEP
    mask = _cut_mask(i)
    im[mask > 0] = PLATE

    # frame: three rules, tightening inward
    for k, (inset, th) in enumerate(((30, 10), (54, 4), (86, 2))):
        m2 = _cut_mask(i)
        m2 = cv2.erode(m2, np.ones((inset, inset), np.uint8))
        cnts, _ = cv2.findContours(m2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        col = acc if k == 0 else tuple(int(v * (0.55 if k == 1 else 0.35)) for v in acc)
        cv2.drawContours(im, cnts, -1, col, th, cv2.LINE_AA)

    # corner bolts
    for cx, cy in ((118, 118), (R - 118, 118), (118, R - 118), (R - 118, R - 118)):
        cv2.circle(im, (cx, cy), 17, BOLT, -1, cv2.LINE_AA)
        cv2.circle(im, (cx, cy), 7, DEEP, -1, cv2.LINE_AA)

    if i % 4 == 3:                                  # the porthole cut gets its ring
        cv2.circle(im, (R // 2, R // 2 - 20), 322, acc, 8, cv2.LINE_AA)

    # the emblem, light on dark, large
    em = emblems.DRAW[i]()
    ink = cv2.resize((em[:, :, 0] < 128).astype(np.uint8) * 255, (620, 620),
                     interpolation=cv2.INTER_AREA)
    y0, x0 = R // 2 - 340, R // 2 - 310
    roi = im[y0:y0 + 620, x0:x0 + 620]
    a = (ink.astype(float) / 255.0)[:, :, None]
    light = np.array([214, 236, 220], float)        # BGR bone
    roi[:] = (roi * (1 - a) + light * a).astype(np.uint8)

    # signage strip, stencilled like a bulkhead
    sy = R - 214
    cv2.rectangle(im, (150, sy), (R - 150, sy + 96), DEEP, -1)
    cv2.rectangle(im, (150, sy), (R - 150, sy + 96), acc, 3, cv2.LINE_AA)
    label = "L%d  %s" % (i, emblems.NAMES[i])
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.5, 3)
    cv2.putText(im, label, (R // 2 - tw // 2, sy + 64), cv2.FONT_HERSHEY_DUPLEX,
                1.5, acc, 3, cv2.LINE_AA)

    # the seam the door parts on
    if orient == "v":
        cv2.line(im, (R // 2, 0), (R // 2, R), DEEP, 6)
        cv2.line(im, (R // 2, 0), (R // 2, R), tuple(int(v * .5) for v in acc), 2)
    else:
        cv2.line(im, (0, R // 2), (R, R // 2), DEEP, 6)
        cv2.line(im, (0, R // 2), (R, R // 2), tuple(int(v * .5) for v in acc), 2)
    return im
