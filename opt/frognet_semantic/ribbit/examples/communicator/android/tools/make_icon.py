#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""make_icon.py OUT.png -- the launcher icon: a frog's face on a rounded green square (drawn, not copied)."""
import sys
from PIL import Image, ImageDraw

S = 4  # supersampling
N = 192 * S
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, N - 1, N - 1], radius=44 * S, fill=(20, 58, 38, 255))
d.ellipse([30 * S, 62 * S, 162 * S, 168 * S], fill=(46, 158, 91, 255))            # the head
for cx in (66, 126):                                                                 # the eyes
    d.ellipse([(cx - 24) * S, 34 * S, (cx + 24) * S, 82 * S], fill=(46, 158, 91, 255))
    d.ellipse([(cx - 15) * S, 43 * S, (cx + 15) * S, 73 * S], fill=(245, 250, 246, 255))
    d.ellipse([(cx - 7) * S, 51 * S, (cx + 7) * S, 65 * S], fill=(16, 32, 24, 255))
d.arc([62 * S, 98 * S, 130 * S, 140 * S], start=20, end=160, fill=(16, 52, 32, 255), width=5 * S)   # the smile
img.resize((192, 192), Image.LANCZOS).save(sys.argv[1], optimize=True)
print(sys.argv[1])
