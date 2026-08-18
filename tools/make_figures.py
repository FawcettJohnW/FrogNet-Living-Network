#!/usr/bin/env python3
"""
make_figures.py — generates every figure used by Magnum Croakus and the site.

    pip install pillow
    python3 make_figures.py                     # write any missing figure
    python3 make_figures.py --force             # redraw everything
    python3 make_figures.py --only fanout,two_planes
    python3 make_figures.py --out magnum-figures --scale 3
    python3 make_figures.py --list

Every figure is drawn from code, so the book is self-generating: no binary
asset has to be tracked, and a caption change never leaves a stale PNG behind.
Coordinates below are in POINTS at the size the docx places them; --scale
multiplies to pixels (3 = ~300dpi for a 6in-wide placement).

Palette matches the book shell: paper #f5f2e9, ink #1b1a16, accent #2c5a3c.
"""

import argparse
import math
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")

# ---------------------------------------------------------------- palette ----

PAPER = (245, 242, 233)
INK = (27, 26, 22)
BODY = (42, 40, 35)
MUTED = (111, 109, 100)
FAINT = (155, 152, 140)
HAIR = (220, 215, 201)
ACCENT = (44, 90, 60)
ACCENT_SOFT = (231, 236, 225)
ACCENT_MID = (188, 210, 191)
WHITE = (255, 255, 255)
WARN = (150, 84, 40)
WARN_SOFT = (243, 231, 219)

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "C:\\Windows\\Fonts",
    ".",
]
FONT_FILES = {
    "regular": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "Helvetica.ttc"],
    "bold": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"],
    "mono": ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "Courier New.ttf", "Menlo.ttc"],
    "monobold": ["DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf", "Courier New Bold.ttf"],
}
_font_cache = {}
_warned = set()


def font(kind, size):
    """Resolve a font once per (kind, size); degrade loudly, not silently."""
    key = (kind, int(size))
    if key in _font_cache:
        return _font_cache[key]
    for name in FONT_FILES.get(kind, FONT_FILES["regular"]):
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, int(size))
                    _font_cache[key] = f
                    return f
                except Exception:
                    pass
    if kind not in _warned:
        _warned.add(kind)
        print("  ! no TrueType '%s' font found — falling back to bitmap default" % kind)
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ------------------------------------------------------------------ canvas ---

class Fig:
    """A drawing surface in points. Every primitive scales on the way out."""

    def __init__(self, w_pt, h_pt, scale, bg=PAPER):
        self.s = scale
        self.w, self.h = w_pt, h_pt
        self.im = Image.new("RGB", (int(w_pt * scale), int(h_pt * scale)), bg)
        self.d = ImageDraw.Draw(self.im)

    # -- geometry helpers
    def p(self, v):
        return int(round(v * self.s))

    def xy(self, *v):
        return [int(round(c * self.s)) for c in v]

    # -- primitives
    def rect(self, x, y, w, h, fill=None, outline=HAIR, width=1, r=0):
        box = self.xy(x, y, x + w, y + h)
        wd = max(1, self.p(width))
        if r:
            try:
                self.d.rounded_rectangle(box, radius=self.p(r), fill=fill,
                                         outline=outline, width=wd)
                return
            except AttributeError:
                pass
        self.d.rectangle(box, fill=fill, outline=outline, width=wd)

    def line(self, x1, y1, x2, y2, fill=HAIR, width=1):
        self.d.line(self.xy(x1, y1, x2, y2), fill=fill, width=max(1, self.p(width)))

    def dash(self, x1, y1, x2, y2, fill=FAINT, width=1, on=4, off=3):
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        if dist <= 0:
            return
        ux, uy = dx / dist, dy / dist
        t = 0.0
        while t < dist:
            e = min(t + on, dist)
            self.line(x1 + ux * t, y1 + uy * t, x1 + ux * e, y1 + uy * e, fill, width)
            t = e + off

    def dot(self, cx, cy, r, fill=ACCENT, outline=None, width=1):
        self.d.ellipse(self.xy(cx - r, cy - r, cx + r, cy + r), fill=fill,
                       outline=outline, width=max(1, self.p(width)))

    def arrow(self, x1, y1, x2, y2, fill=ACCENT, width=1.2, head=5, dashed=False):
        ang = math.atan2(y2 - y1, x2 - x1)
        bx, by = x2 - head * 0.85 * math.cos(ang), y2 - head * 0.85 * math.sin(ang)
        (self.dash if dashed else self.line)(x1, y1, bx, by, fill, width)
        left = (x2 - head * math.cos(ang - 0.42), y2 - head * math.sin(ang - 0.42))
        right = (x2 - head * math.cos(ang + 0.42), y2 - head * math.sin(ang + 0.42))
        self.d.polygon([self.xy(x2, y2), self.xy(*left), self.xy(*right)], fill=fill)

    # -- text
    def tw(self, s, f):
        try:
            b = f.getbbox(s)
            return (b[2] - b[0]) / self.s
        except Exception:
            return len(s) * 0.5 * 10 / self.s

    def text(self, x, y, s, size=8, kind="regular", fill=BODY, anchor="lt", track=0):
        f = font(kind, size * self.s)
        w = self.tw(s, f)
        if track:
            # letter-spacing: draw per glyph
            total = w + track * (len(s) - 1)
            px = x - (total / 2 if anchor[0] == "m" else total if anchor[0] == "r" else 0)
            for ch in s:
                self.text(px, y, ch, size, kind, fill, "l" + anchor[1])
                px += self.tw(ch, f) + track
            return total
        if anchor[0] == "m":
            x -= w / 2
        elif anchor[0] == "r":
            x -= w
        yy = y
        if anchor[1] == "m":
            yy = y - size * 0.62
        elif anchor[1] == "b":
            yy = y - size * 1.15
        self.d.text(self.xy(x, yy), s, font=f, fill=fill)
        return w

    def cap(self, x, y, s, size=6.4, fill=MUTED, anchor="lt"):
        """Small uppercase label with tracking — the book's figure-label voice."""
        return self.text(x, y, s.upper(), size, "bold", fill, anchor, track=size * 0.13)

    def wrap(self, x, y, s, w, size=7.4, kind="regular", fill=BODY, lead=1.42):
        f = font(kind, size * self.s)
        words, line, yy = s.split(), "", y
        for word in words:
            probe = (line + " " + word).strip()
            if self.tw(probe, f) > w and line:
                self.text(x, yy, line, size, kind, fill)
                yy += size * lead
                line = word
            else:
                line = probe
        if line:
            self.text(x, yy, line, size, kind, fill)
            yy += size * lead
        return yy

    def save(self, path):
        self.im.save(path, "PNG", optimize=True)


def node(f, x, y, w, h, title, sub=None, accent=False, mono_title=False, r=2):
    """The standard box used across the figures."""
    f.rect(x, y, w, h, fill=ACCENT_SOFT if accent else WHITE,
           outline=ACCENT_MID if accent else HAIR, r=r)
    ty = y + (h / 2 - 5.5 if sub else h / 2)
    f.text(x + w / 2, ty, title, 8, "monobold" if mono_title else "bold",
           ACCENT if accent else INK, "mm")
    if sub:
        f.text(x + w / 2, ty + 10, sub, 6.6, "regular", MUTED, "mm")


# ------------------------------------------------------- book figures (6) ----

def fig_multi_transport(scale):
    """Part IV — transports multiplex inside one topology."""
    f = Fig(600, 268, scale)
    f.cap(0, 4, "one pond · one flat 10-net")
    y0 = 22
    f.rect(0, y0, 600, 96, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(10, y0 + 8, "ABOVE THE LINE — nothing here moves when a bearer changes",
           6.6, "bold", ACCENT)
    for i, (nm, ip) in enumerate([("SEA-1", "10.0.0.1"), ("SEA-2", "10.0.0.2"),
                                  ("NY-1", "10.0.1.1"), ("NY-2", "10.0.1.2")]):
        node(f, 24 + i * 140, y0 + 26, 116, 52, nm, ip, accent=True, mono_title=True)
        if i:
            f.line(24 + i * 140 - 24, y0 + 52, 24 + i * 140, y0 + 52, ACCENT_MID, 1.2)
    f.text(300, y0 + 86, "discovery · routing · elections · media · applications",
           6.8, "regular", MUTED, "mt")

    f.dash(0, 132, 600, 132, INK, 1.1, on=6, off=4)
    f.text(300, 136, "the line", 6.4, "bold", INK, "mt")

    bearers = [("eth1", "wire", "upstream on a cable"),
               ("wlan0", "Wi-Fi", "AP mode, projected SSID"),
               ("900 MHz", "radio", "clear-air point to point"),
               ("wg0", "tunnel", "WireGuard to the broker")]
    for i, (nm, kind, note) in enumerate(bearers):
        x = 12 + i * 146
        node(f, x, 158, 130, 46, nm, kind, mono_title=True)
        f.arrow(x + 65, 158, x + 65, 136, ACCENT, 1.1, 5)
        f.wrap(x, 212, note, 130, 6.4, "regular", MUTED)
    f.text(300, 248, "New transports plug in below the line. Nothing above the line moves.",
           7.6, "bold", INK, "mt")
    return f


def fig_two_planes(scale):
    """Part XIV — the careful mouth and the fast mouth."""
    f = Fig(600, 253, scale)
    f.cap(0, 2, "one handler · two planes")
    for i, (nm, tag, rows, accent) in enumerate([
        ("CONTROL PLANE", "exact · ordered · cheap",
         ["participants, rungs, presence", "held as shared memory",
          "survives with no frame moving"], True),
        ("DATA PLANE", "newest first · never waits",
         ["frames as pure payload", "raised like proxy/daemon links",
          "shed freely, nothing queued"], False)]):
        x = 12 + i * 300
        f.rect(x, 22, 276, 106, fill=ACCENT_SOFT if accent else WHITE,
               outline=ACCENT_MID if accent else HAIR, r=3)
        f.text(x + 14, 32, nm, 8.4, "bold", ACCENT if accent else INK)
        f.text(x + 14, 45, tag, 6.8, "regular", MUTED)
        for j, r in enumerate(rows):
            f.dot(x + 18, 66 + j * 15, 1.6, ACCENT if accent else MUTED)
            f.text(x + 26, 62 + j * 15, r, 7, "regular", BODY)
    f.text(300, 78, "same", 6.4, "bold", MUTED, "mm")
    f.text(300, 88, "socket", 6.4, "bold", MUTED, "mm")

    f.rect(12, 144, 576, 62, fill=WHITE, outline=HAIR, r=3)
    f.text(24, 152, "THE SEAM — a socket that must stay patient to listen, and a send that must never wait to speak",
           6.8, "bold", INK)
    steps = ["receive stays blocking", "before a frame: can the socket take one?",
             "yes → send it", "no → drop it, reach for a newer frame"]
    x = 24
    for i, s in enumerate(steps):
        w = f.tw(s, font("regular", 7 * scale)) + 16
        f.rect(x, 170, w, 24, fill=ACCENT_SOFT if i > 1 else WHITE, outline=HAIR, r=2)
        f.text(x + w / 2, 182, s, 7, "regular", BODY, "mm")
        if i < 3:
            f.arrow(x + w + 2, 182, x + w + 12, 182, FAINT, 1, 4)
        x += w + 14
    f.wrap(12, 214, "The naive move — a wholly non-blocking socket — breaks the receive loop. "
                    "Gate only the send on writability. The send never blocks because it is never "
                    "called when it would.", 576, 7, "regular", MUTED)
    return f


def fig_fanout(scale):
    """Part XIV — one uplink each, a personalized mix out."""
    f = Fig(600, 283, scale)
    f.cap(0, 2, "mediahost fan-out · nobody is sent themselves")
    host_x, host_y = 232, 112
    f.rect(host_x, host_y, 136, 58, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(host_x + 68, host_y + 22, "mediahost", 8.6, "monobold", ACCENT, "mm")
    f.text(host_x + 68, host_y + 38, "an elected role, not a server", 6.4, "regular", MUTED, "mm")

    pos = [(30, 34), (30, 190), (452, 34), (452, 190)]
    names = ["A", "B", "C", "D"]
    for i, (x, y) in enumerate(pos):
        node(f, x, y, 118, 52, "participant " + names[i], "one uplink", mono_title=False)
        cx = x + 118 if x < 300 else x
        tx = host_x if x < 300 else host_x + 136
        f.arrow(cx, y + 18, tx, host_y + 18, ACCENT, 1.1, 5)
        f.arrow(tx, host_y + 42, cx, y + 38, MUTED, 1.1, 5, dashed=True)
    f.text(300, 182, "up: one stream", 6.4, "bold", ACCENT, "mm")
    f.text(300, 192, "down: a mix built for that recipient", 6.4, "regular", MUTED, "mm")

    f.rect(12, 246, 576, 30, fill=WHITE, outline=HAIR, r=2)
    f.text(24, 256, "audio summed from the OTHERS  ·  video gridded from the OTHERS  ·  "
                    "one encode feeds every leg on that rung  ·  audio-only legs are never charged for pixels",
           6.8, "regular", BODY)
    return f


def fig_quality_ladder(scale):
    """Part XIV — seven rungs, framerate steps before resolution."""
    f = Fig(620, 372, scale)
    f.cap(0, 2, "the ladder · none of the rungs is silence")
    rungs = [("CHORUS", "720p color + mono audio", "camera + mic", 1.00),
             ("ENSEMBLE", "480p color + mono audio", "camera + mic", 0.72),
             ("DUET", "360p color + mono audio", "camera + mic", 0.52),
             ("VOICE", "mono audio only", "mic", 0.30),
             ("WHISPER", "plaintext text", "—", 0.18),
             ("BEACON", "token vocabulary", "—", 0.11),
             ("PULSE", 'presence — one byte: "alive"', "—", 0.05)]
    y = 26
    f.line(0, y - 4, 620, y - 4, HAIR, 1)
    f.cap(8, y - 16, "rung", 6.2, MUTED)
    f.cap(112, y - 16, "carries", 6.2, MUTED)
    f.cap(360, y - 16, "needs", 6.2, MUTED)
    f.cap(470, y - 16, "relative cost", 6.2, MUTED)
    for i, (nm, carries, needs, wgt) in enumerate(rungs):
        yy = y + i * 34
        f.rect(0, yy, 620, 32, fill=WHITE if i % 2 else PAPER, outline=None)
        f.text(8, yy + 16, nm, 8.4, "monobold", ACCENT, "lm")
        f.text(112, yy + 16, carries, 7.4, "regular", BODY, "lm")
        f.text(360, yy + 16, needs, 7.2, "regular", MUTED, "lm")
        f.rect(470, yy + 12, 142 * wgt, 8, fill=ACCENT if i < 3 else ACCENT_MID, outline=None)
        f.line(0, yy + 32, 620, yy + 32, HAIR, 1)
    yb = y + 7 * 34 + 12
    f.rect(0, yb, 620, 44, fill=ACCENT_SOFT, outline=ACCENT_MID, r=2)
    f.text(12, yb + 9, "WITHIN each video rung, framerate steps first:", 7.2, "bold", ACCENT)
    x = 300
    for j, fps in enumerate(["24 fps", "15 fps", "7 fps"]):
        f.rect(x, yb + 6, 62, 18, fill=WHITE, outline=ACCENT_MID, r=2)
        f.text(x + 31, yb + 15, fps, 7, "monobold", ACCENT, "mm")
        if j < 2:
            f.arrow(x + 64, yb + 15, x + 76, yb + 15, ACCENT, 1, 4)
        x += 78
    f.text(12, yb + 27, "only when 7 fps still will not fit does the resolution drop to the rung "
                        "below — and the framerate resets to 24", 7, "regular", BODY)
    f.text(310, 366, "the floor is not the floor of the network but the floor of presence",
           7.2, "bold", INK, "mb")
    return f


def fig_backpressure(scale):
    """Part XIV — the socket's refusal is the only congestion signal."""
    f = Fig(600, 324, scale)
    f.cap(0, 2, "backpressure · nothing estimates bandwidth")
    f.rect(12, 22, 576, 46, fill=WHITE, outline=HAIR, r=3)
    seq = ["send a frame", "socket refuses", "that IS the signal", "step down"]
    x = 26
    for i, s in enumerate(seq):
        w = f.tw(s, font("bold", 7.4 * scale)) + 20
        f.rect(x, 32, w, 26, fill=ACCENT_SOFT if i == 2 else WHITE,
               outline=ACCENT_MID if i == 2 else HAIR, r=2)
        f.text(x + w / 2, 45, s, 7.4, "bold" if i == 2 else "regular",
               ACCENT if i == 2 else BODY, "mm")
        if i < 3:
            f.arrow(x + w + 3, 45, x + w + 15, 45, ACCENT, 1, 4)
        x += w + 18
    f.text(578, 45, "no probe · no guesser · no model", 6.6, "regular", MUTED, "rm")

    # descent staircase
    f.cap(12, 84, "the descent is framerate-first")
    steps = [("720p", "24"), ("720p", "15"), ("720p", "7"), ("480p", "24"),
             ("480p", "15"), ("480p", "7"), ("360p", "24")]
    bx, by = 20, 104
    for i, (res, fps) in enumerate(steps):
        x = bx + i * 78
        h = 96 - i * 11
        f.rect(x, by + (96 - h), 66, h, fill=ACCENT_SOFT if res == "720p" else WHITE,
               outline=ACCENT_MID if res == "720p" else HAIR, r=2)
        f.text(x + 33, by + (96 - h) + 12, res, 7.6, "monobold", ACCENT, "mm")
        f.text(x + 33, by + (96 - h) + 25, fps + " fps", 6.8, "regular", MUTED, "mm")
        if i < len(steps) - 1:
            f.arrow(x + 68, by + 100, x + 76, by + 100, FAINT, 1, 4)
    f.text(20, by + 106, "hold the resolution, thin the motion — a stuttery sharp picture reads "
                         "better than a smooth blurred one", 7, "regular", BODY)

    f.rect(12, 234, 282, 74, fill=WHITE, outline=HAIR, r=3)
    f.text(24, 243, "HYSTERESIS", 6.8, "bold", ACCENT)
    f.rect(24, 258, 258, 16, fill=ACCENT_SOFT, outline=ACCENT_MID, r=2)
    f.text(153, 266, "the band between down and up", 6.8, "regular", ACCENT, "mm")
    f.wrap(24, 280, "Step down readily. Climb back only once the link has been calm for a while.",
           258, 6.8, "regular", MUTED)

    f.rect(306, 234, 282, 74, fill=WHITE, outline=HAIR, r=3)
    f.text(318, 243, "PER LEG, AGAINST ITS OWN BEARER", 6.8, "bold", ACCENT)
    for i, (nm, rung) in enumerate([("A", "720p"), ("B", "VOICE"), ("C", "720p")]):
        x = 318 + i * 88
        f.rect(x, 258, 78, 20, fill=WARN_SOFT if rung == "VOICE" else ACCENT_SOFT,
               outline=WARN if rung == "VOICE" else ACCENT_MID, r=2)
        f.text(x + 39, 268, nm + " · " + rung, 7, "monobold",
               WARN if rung == "VOICE" else ACCENT, "mm")
    f.wrap(318, 284, "One bad connection degrades only itself. It never drags the room down.",
           258, 6.8, "regular", MUTED)
    return f


def fig_keyframe_anchor(scale):
    """Ch. 48 — the frame that must not be dropped."""
    f = Fig(620, 248, scale)
    f.cap(0, 2, "shedding, unanchored, re-anchored")
    lane_y = 30
    f.rect(0, lane_y, 620, 58, fill=WHITE, outline=HAIR, r=3)
    f.text(10, lane_y + 8, "ANCHORED — normal service", 6.8, "bold", ACCENT)
    x = 12
    for i in range(14):
        key = (i % 7 == 0)
        w = 26 if key else 14
        f.rect(x, lane_y + 24, w, 24, fill=ACCENT if key else ACCENT_MID, outline=None, r=1)
        if key:
            f.text(x + w / 2, lane_y + 36, "K", 7.4, "monobold", WHITE, "mm")
        x += w + 5
    f.text(x + 8, lane_y + 36, "K = keyframe · the largest frame there is", 6.8, "regular", MUTED, "lm")

    f.arrow(310, lane_y + 62, 310, lane_y + 76, WARN, 1.2, 5)
    f.text(320, lane_y + 69, "the viewer starts shedding", 7, "bold", WARN, "lm")

    ly = 112
    f.rect(0, ly, 620, 76, fill=WARN_SOFT, outline=WARN, r=3)
    f.text(10, ly + 8, "UNANCHORED", 6.8, "bold", WARN)
    rules = [("inter frames", "dropped outright — arithmetic on a reference that does not exist"),
             ("a keyframe", "becomes the frame to send"),
             ("budget too small", "the keyframe is HELD — and a newer keyframe OVERWRITES it")]
    for i, (k, v) in enumerate(rules):
        f.text(14, ly + 24 + i * 16, k, 7, "monobold", WARN)
        f.text(112, ly + 24 + i * 16, v, 7, "regular", BODY)
    f.text(608, ly + 8, "no queue of stale keyframes", 6.6, "regular", MUTED, "rt")

    f.rect(0, 200, 620, 42, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(12, 208, "RE-ANCHORED", 6.8, "bold", ACCENT)
    f.text(12, 222, "Delivering a keyframe restores normal service. A held keyframe is allowed to "
                    "overdraw its budget — a bucket that only ever refuses would leave the viewer "
                    "unanchored forever.", 7, "regular", BODY)
    f.text(608, 222, "flag rides the high bit of the level field · the relay never decodes",
           6.6, "regular", MUTED, "rt")
    return f


# ------------------------------------------- audit additions · the book ------

def fig_wire_states(scale):
    """Part on the codec — SAME, DIFF, FULL/RAW, all LZ4."""
    f = Fig(620, 250, scale)
    f.cap(0, 2, "three wire states · every one of them LZ4-compressed")
    cols = [("SAME", "nothing changed", "essentially empty", "the steady state", 0.05, ACCENT),
            ("DIFF", "only changed fields", "a small parcel", "the ordinary case", 0.30, ACCENT),
            ("FULL / RAW", "the whole payload", "first exchange, or a body\nthe codec cannot model",
             "the exception", 1.00, MUTED)]
    for i, (nm, what, size, note, wgt, col) in enumerate(cols):
        x = 8 + i * 202
        f.rect(x, 24, 190, 128, fill=WHITE, outline=HAIR, r=3)
        f.text(x + 14, 34, nm, 9.4, "monobold", col)
        f.text(x + 14, 50, what, 7.2, "bold", INK)
        yy = f.wrap(x + 14, 64, size.replace("\n", " "), 162, 7, "regular", BODY)
        f.text(x + 14, yy + 2, note, 6.8, "regular", MUTED)
        f.rect(x + 14, 122, 162 * wgt, 16, fill=col, outline=None, r=2)
        f.text(x + 14 + max(20, 162 * wgt) + 6, 130, "on the wire", 6.4, "regular", MUTED, "lm")
    f.rect(8, 164, 604, 34, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(20, 173, "Both ends hold the same learned template, keyed on the path — so one template "
                    "serves every node. The wire carries the change.", 7.2, "regular", ACCENT)
    f.wrap(8, 210, "Compression is not the point. Stock LZ4 over an already-reduced payload is a "
                   "floor, not the mechanism — the mechanism is the diff. Even the largest case is "
                   "compressed, never sent whole and plain.", 604, 7.2, "regular", MUTED)
    return f


def fig_dead_air(scale):
    """The strongest payoff — capacity paid for and unused."""
    f = Fig(620, 214, scale)
    f.cap(0, 2, "dead air · the wire between exchanges")
    lanes = [("REQUEST / RESPONSE", "connection stood up, handshake, exchange, torn down",
              [(0, 34), (48, 22), (94, 30), (150, 18), (192, 26), (246, 20)], WARN),
             ("ONE STANDING WIRE", "a pool of workers, concurrency without more roads",
              None, ACCENT)]
    for i, (nm, sub, segs, col) in enumerate(lanes):
        y = 26 + i * 84
        f.text(0, y, nm, 7.6, "bold", col)
        f.text(0, y + 12, sub, 6.8, "regular", MUTED)
        ty = y + 26
        f.rect(0, ty, 560, 30, fill=WHITE, outline=HAIR, r=2)
        if segs:
            for sx, sw in segs:
                f.rect(sx * 2.0, ty, sw * 2.0, 30, fill=col, outline=None)
            f.text(568, ty + 15, "idle", 6.6, "regular", MUTED, "lm")
            used = sum(w for _, w in segs) * 2.0
            f.text(0, ty + 38, "≈%d%% of the wire carries nothing — capacity paid for and unused"
                   % round(100 - used / 560 * 100), 7, "bold", WARN)
        else:
            f.rect(0, ty, 560, 30, fill=col, outline=None)
            for k in range(1, 19):
                f.line(k * 29.5, ty, k * 29.5, ty + 30, (255, 255, 255), 0.6)
            f.text(568, ty + 15, "full", 6.6, "regular", ACCENT, "lm")
            f.text(0, ty + 38, "no dead air to reclaim — the wire is a fact, not a project",
                   7, "bold", ACCENT)
    f.wrap(0, 196, "Killing request/response and its dead air was the single greatest performance "
                   "win — larger than the size reduction. The two axes compound rather than add.",
           620, 7, "regular", MUTED)
    return f


def fig_discovery_walk(scale):
    """Part V — neighbours, children, grandchildren, on the .2 plane."""
    f = Fig(620, 256, scale)
    f.cap(0, 2, "runmerge · a clear pass is the only way out")
    ring = [("self", 300, 40, True), ("neighbour", 130, 104, False), ("neighbour", 470, 104, False),
            ("child", 60, 172, False), ("child", 210, 172, False),
            ("child", 400, 172, False), ("grandchild", 550, 172, False)]
    for nm, cx, cy, me in ring:
        w = 104 if not me else 92
        f.rect(cx - w / 2, cy, w, 34, fill=ACCENT_SOFT if me else WHITE,
               outline=ACCENT_MID if me else HAIR, r=2)
        f.text(cx, cy + 17, nm, 7.4, "monobold" if me else "bold",
               ACCENT if me else INK, "mm")
    for a, b in [((300, 74), (130, 104)), ((300, 74), (470, 104)),
                 ((130, 138), (60, 172)), ((130, 138), (210, 172)),
                 ((470, 138), (400, 172)), ((470, 138), (550, 172))]:
        f.arrow(a[0], a[1], b[0], b[1], ACCENT, 1, 4.5)
    f.text(300, 82, "three questions, over plain HTTP", 6.8, "bold", ACCENT, "mm")
    qs = ["who are you", "who do you know", "what is your default route"]
    for i, q in enumerate(qs):
        f.rect(140 + i * 116, 214, 108, 20, fill=WHITE, outline=HAIR, r=2)
        f.text(194 + i * 116, 224, q, 6.8, "regular", BODY, "mm")
    f.text(8, 224, "no DNS", 7.2, "bold", INK, "lm")
    f.text(8, 236, "a coordinated /etc/hosts,", 6.4, "regular", MUTED, "lm")
    f.text(8, 246, "complete on every node", 6.4, "regular", MUTED, "lm")
    f.text(612, 224, "runs on the reserved .2 addresses", 6.6, "regular", MUTED, "rm")
    f.text(612, 236, ".1 traffic is untouched; a .2 route is promoted", 6.6, "regular", MUTED, "rm")
    f.text(612, 246, "only when it must replace a .1", 6.6, "regular", MUTED, "rm")
    return f


def fig_election(scale):
    """Part on elections — capability, not seniority."""
    f = Fig(620, 226, scale)
    f.cap(0, 2, "one database per network · deterministically exactly one")
    cands = [("SEA-1", "mariadb ✓  mem 8G  ssd", 0.92, True),
             ("SEA-2", "mariadb ✓  mem 2G  sd", 0.54, False),
             ("NY-1", "mariadb ✓  mem 4G  ssd", 0.71, False),
             ("NY-2", "mariadb ✓  mem 1G  sd", 0.38, False)]
    for i, (nm, caps, score, win) in enumerate(cands):
        y = 26 + i * 34
        f.rect(0, y, 380, 30, fill=ACCENT_SOFT if win else WHITE,
               outline=ACCENT_MID if win else HAIR, r=2)
        f.text(10, y + 15, nm, 7.8, "monobold", ACCENT if win else INK, "lm")
        f.text(74, y + 15, caps, 6.8, "regular", MUTED, "lm")
        f.rect(252, y + 11, 116 * score, 8, fill=ACCENT if win else ACCENT_MID, outline=None, r=1)
    f.arrow(388, 72, 424, 72, ACCENT, 1.2, 5.5)
    f.rect(432, 40, 188, 66, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(526, 62, "DatabaseHost", 9, "monobold", ACCENT, "mm")
    f.text(526, 78, "the memory controller", 6.8, "regular", MUTED, "mm")
    f.text(526, 90, "one serialization point → one order", 6.4, "regular", MUTED, "mm")
    f.rect(0, 170, 620, 46, fill=WHITE, outline=HAIR, r=3)
    f.text(12, 179, "RECALCULATED ON EVERY MERGE, FROM THE PERSPECTIVE OF THE MACHINE",
           6.8, "bold", ACCENT)
    f.text(12, 194, "A team drives away and the database is theirs. They meet another team and one "
                    "of them holds it. They reach headquarters and somebody there does. No reader "
                    "changes a line — the producer was never addressed.", 7, "regular", BODY)
    return f


def fig_tuple_address(scale):
    """FrogNet Memory — three indices, any combination."""
    f = Fig(620, 242, scale)
    f.cap(0, 2, "the address is three coordinates, and you name them")
    schemes = [("a sensor fleet", ["name", "type", "location"]),
               ("process shared memory", ["program", "variable", "user"]),
               ("a machine's globals", ["host", "globals", "variable"])]
    for i, (nm, parts) in enumerate(schemes):
        y = 24 + i * 44
        f.text(0, y + 16, nm, 7.2, "regular", MUTED, "lm")
        x = 150
        for j, p in enumerate(parts):
            w = 108
            f.rect(x, y, w, 32, fill=ACCENT_SOFT, outline=ACCENT_MID, r=2)
            f.text(x + w / 2, y + 16, p, 7.6, "monobold", ACCENT, "mm")
            if j < 2:
                f.text(x + w + 8, y + 16, "/", 9, "monobold", FAINT, "mm")
            x += w + 20
        f.rect(x + 6, y, 96, 32, fill=WHITE, outline=HAIR, r=2)
        f.text(x + 54, y + 16, "value: JSON", 6.8, "regular", MUTED, "mm")
    f.line(0, 164, 620, 164, HAIR, 1)
    f.text(0, 172, "SEARCHABLE BY ANY ONE, ANY TWO, OR ALL THREE", 6.8, "bold", ACCENT)
    for i, (q, r) in enumerate([("all three", "one item"), ("any two", "a set"),
                                ("any one", "a larger set")]):
        x = i * 208
        f.rect(x, 188, 190, 30, fill=WHITE, outline=HAIR, r=2)
        f.text(x + 12, 203, q, 7.2, "monobold", ACCENT, "lm")
        f.text(x + 178, 203, "→  " + r, 7.2, "regular", BODY, "rm")
    f.text(0, 232, "No schema. No registry. The producer never learns the consumer exists.",
           7.2, "bold", INK)
    return f


def fig_address_scaling(scale):
    """Appendix K — per tunnel is quadratic; per node is linear."""
    f = Fig(620, 268, scale)
    f.cap(0, 2, "appendix K · the allocator, and why address space is the wrong lever")
    gw, gh, ox, oy = 250, 150, 20, 28
    f.rect(ox, oy, gw, gh, fill=WHITE, outline=HAIR, r=2)
    f.text(ox + gw / 2, oy - 8, "ALLOCATED PER TUNNEL — quadratic", 6.8, "bold", WARN, "mb")
    pts = [(n, (n * (n - 1)) / 2) for n in range(1, 12)]
    mx = pts[-1][1]
    prev = None
    for n, t in pts:
        x = ox + (n / 11) * gw
        y = oy + gh - (t / mx) * (gh - 10)
        if prev:
            f.line(prev[0], prev[1], x, y, WARN, 1.4)
        prev = (x, y)
    f.text(ox + 6, oy + 8, "tunnels grow as the square of the nodes", 6.4, "regular", MUTED)
    f.text(ox + gw - 6, oy + gh - 8, "nodes →", 6.4, "regular", FAINT, "rm")

    ox2 = 330
    f.rect(ox2, oy, gw, gh, fill=ACCENT_SOFT, outline=ACCENT_MID, r=2)
    f.text(ox2 + gw / 2, oy - 8, "ALLOCATED PER NODE — linear", 6.8, "bold", ACCENT, "mb")
    f.line(ox2, oy + gh, ox2 + gw, oy + gh - (gh - 10) * 0.42, ACCENT, 1.4)
    f.text(ox2 + 6, oy + 8, "one carrier address per node, one interface per pond",
           6.4, "regular", MUTED)
    f.text(ox2 + gw - 6, oy + gh - 8, "nodes →", 6.4, "regular", FAINT, "rm")

    y = 194
    f.line(0, y, 620, y, INK, 1)
    f.cap(0, y - 14, "buying nodes with address space returns a square root")
    rows = [("stop wasting half of each block", "2×", "1.4×"),
            ("use the whole reserved range", "5×", "2.2×"),
            ("hand transit ALL of 10/8 — plainly impossible", "256×", "16×")]
    for i, (chg, tun, nod) in enumerate(rows):
        yy = y + 8 + i * 18
        f.text(0, yy, chg, 7, "bold" if i == 2 else "regular", INK if i == 2 else BODY)
        f.text(430, yy, tun, 7, "monobold", MUTED)
        f.text(500, yy, nod, 7, "monobold", ACCENT if i == 2 else MUTED)
    f.text(0, y + 66, "The size of the address space is not what is wrong. Allocating anything at "
                      "all per tunnel is what is wrong.", 7.4, "bold", INK)
    f.text(430, y + 66, "56 → ~59,500 nodes", 7.4, "monobold", ACCENT)
    return f


def fig_airgap_broker(scale):
    """Security — the reference topology."""
    f = Fig(620, 232, scale)
    f.cap(0, 2, "the reference topology · one exposed port, and it is yours")
    for i, side in enumerate(["SEATTLE", "NEW YORK"]):
        x = 0 if i == 0 else 434
        f.rect(x, 26, 186, 116, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
        f.text(x + 93, 38, side + " POND", 7.4, "bold", ACCENT, "mm")
        for j, nm in enumerate(["node", "node", "gateway"]):
            f.rect(x + 12, 52 + j * 28, 162, 22,
                   fill=WHITE, outline=ACCENT_MID if nm == "gateway" else HAIR, r=2)
            f.text(x + 93, 63 + j * 28, nm, 7, "monobold" if nm == "gateway" else "regular",
                   ACCENT if nm == "gateway" else BODY, "mm")
        f.text(x + 93, 150, "no node is addressable from outside", 6.4, "regular", MUTED, "mm")
    f.rect(232, 44, 156, 66, fill=WHITE, outline=INK, r=3)
    f.text(310, 62, "BROKER", 8.6, "bold", INK, "mm")
    f.text(310, 78, "relays bytes it cannot read", 6.6, "regular", MUTED, "mm")
    f.text(310, 92, "18257/tcp · WireGuard", 6.8, "monobold", ACCENT, "mm")
    f.arrow(188, 78, 230, 78, ACCENT, 1.2, 5.5)
    f.arrow(432, 78, 390, 78, ACCENT, 1.2, 5.5)
    f.rect(232, 156, 156, 62, fill=WARN_SOFT, outline=WARN, r=3)
    f.text(310, 170, "AIR-GAPPED FILE DROP", 6.8, "bold", WARN, "mm")
    f.text(310, 186, "text into a watched directory", 6.6, "regular", BODY, "mm")
    f.text(310, 198, "validated inside · answer dropped back", 6.6, "regular", BODY, "mm")
    f.text(310, 210, "no open connection", 6.6, "monobold", WARN, "mm")
    f.dash(310, 112, 310, 154, FAINT, 1, on=5, off=4)
    f.text(0, 200, "WireGuard does not answer", 6.8, "bold", INK)
    f.text(0, 212, "a scan without credentials.", 6.8, "regular", MUTED)
    f.text(0, 224, "FrogNet implements no", 6.8, "regular", MUTED)
    f.text(620, 200, "Surface reduction, not", 6.8, "bold", INK, "rt")
    f.text(620, 212, "cryptography. WireGuard is", 6.8, "regular", MUTED, "rt")
    f.text(620, 224, "the encryption.", 6.8, "regular", MUTED, "rt")
    return f


def fig_scopes(scale):
    """The four scopes, nested."""
    f = Fig(600, 236, scale)
    f.cap(0, 2, "scope · where a value is visible")
    boxes = [("POND", "one network, complete with no tunnel and no broker in it", 0),
             ("CHORUS", "ponds federated across the internet through a broker", 1),
             ("LILLYPAD", "one node and what it holds", 2),
             ("SELF", "one process", 3)]
    x, y, w, h = 0, 24, 600, 200
    for i, (nm, note, depth) in enumerate(boxes):
        pad = i * 26
        f.rect(x + pad, y + pad, w - pad * 2, h - pad * 2,
               fill=ACCENT_SOFT if i == 0 else (WHITE if i % 2 else PAPER),
               outline=ACCENT_MID if i < 2 else HAIR, r=3)
        f.text(x + pad + 12, y + pad + 8, nm, 7.6, "bold", ACCENT if i < 2 else INK)
        f.text(x + pad + 76, y + pad + 9, note, 6.6, "regular", MUTED)
    return f


def fig_monitor_read(scale):
    """The deletion, drawn: 318 lines against a monitoring stack."""
    f = Fig(620, 244, scale)
    f.cap(0, 2, "a monitoring tool that never contacts the machines it monitors")
    f.rect(0, 24, 250, 92, fill=WARN_SOFT, outline=WARN, r=3)
    f.text(12, 33, "WHAT YOU WOULD NORMALLY DEPLOY", 6.6, "bold", WARN)
    for i, s in enumerate(["an agent on every machine", "a collector to run",
                           "a time-series backend", "a schema to version",
                           "a per-node polling protocol"]):
        f.text(20, 48 + i * 13, "·  " + s, 6.8, "regular", BODY)
    f.arrow(258, 70, 292, 70, ACCENT, 1.2, 5.5)
    f.rect(300, 24, 320, 92, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(312, 33, "WHAT THE MONITOR ACTUALLY DOES", 6.6, "bold", ACCENT)
    f.text(312, 50, "builds the name", 7, "regular", BODY)
    f.rect(312, 62, 296, 22, fill=WHITE, outline=ACCENT_MID, r=2)
    f.text(460, 73, "<domain>.SemanticCache.Peer.<ip>", 7.6, "monobold", ACCENT, "mm")
    f.text(312, 92, "and reads it. Learning about a peer does not involve the peer.",
           6.8, "regular", MUTED)
    y = 132
    f.line(0, y, 620, y, HAIR, 1)
    f.cap(0, y + 8, "lines of code")
    f.rect(0, y + 24, 560, 26, fill=ACCENT_MID, outline=None, r=2)
    f.text(10, y + 37, "whole tool — curses UI, drill-down, probes, formatting, colour",
           7, "regular", INK, "lm")
    f.text(570, y + 37, "1,945", 8, "monobold", INK, "lm")
    f.rect(0, y + 56, 560 * (318 / 1945), 26, fill=ACCENT, outline=None, r=2)
    f.text(560 * (318 / 1945) + 10, y + 69, "data layer — every read the entire tool performs",
           7, "bold", INK, "lm")
    f.text(570, y + 69, "318", 8, "monobold", ACCENT, "lm")
    f.text(0, y + 96, "That is not a smaller implementation of the same thing. "
                      "The thing stopped needing an implementation.", 7.4, "bold", INK)
    return f


def fig_three_arms(scale):
    """Programming Surface Studies — the method."""
    f = Fig(620, 238, scale)
    f.cap(0, 2, "three arms, one variable at a time")
    arms = [("ARM 1", "the existing architecture, as it is",
             "services, endpoints, ownership, retries, test doubles — modelled to "
             "understand it, not to criticise it", False),
            ("ARM 2", "the same architecture, over FrogNet",
             "application untouched, substrate replaced — isolates topology, routing "
             "and recovery, model held constant", True),
            ("ARM 3", "the same system, re-expressed through UnREST",
             "messages become shared state; only the programming surface changes", False)]
    for i, (n, t, note, mid) in enumerate(arms):
        x = i * 208
        f.rect(x, 24, 190, 122, fill=ACCENT_SOFT if mid else WHITE,
               outline=ACCENT_MID if mid else HAIR, r=3)
        f.text(x + 14, 34, n, 8.4, "bold", ACCENT if mid else INK)
        yy = f.wrap(x + 14, 50, t, 162, 7.4, "bold", INK)
        f.wrap(x + 14, yy + 4, note, 162, 6.8, "regular", MUTED)
        if mid:
            f.text(x + 95, 136, "the one most likely to come back flat", 6.4, "bold", ACCENT, "mm")
        if i < 2:
            f.arrow(x + 192, 85, x + 206, 85, FAINT, 1, 4.5)
    f.rect(0, 158, 620, 34, fill=WHITE, outline=HAIR, r=3)
    f.text(12, 167, "IDENTICAL ACROSS ALL THREE", 6.8, "bold", ACCENT)
    f.text(12, 180, "topology  ·  workload  ·  failures  ·  correctness checks", 7, "regular", BODY)
    f.text(0, 204, "Measured: engineering cost, not throughput.", 7.4, "bold", INK)
    f.text(0, 218, "Communication code, synchronisation logic, retries, message types, endpoints, "
                   "mock infrastructure. Classification rule published BEFORE the counts.",
           7, "regular", MUTED)
    return f


def fig_lamp_path(scale):
    """April 17 2026 — the write went 2,400 miles; the consequence came back."""
    f = Fig(620, 196, scale)
    f.cap(0, 2, "april 17, 2026 · nothing connected the switch to the lamp")
    f.rect(0, 30, 200, 84, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(100, 44, "DAN'S BENCH — NEW YORK", 6.8, "bold", ACCENT, "mm")
    f.rect(14, 56, 84, 44, fill=WHITE, outline=HAIR, r=2)
    f.text(56, 72, "he set a", 6.6, "regular", MUTED, "mm")
    f.text(56, 84, "sensor value", 7.2, "monobold", ACCENT, "mm")
    f.rect(104, 56, 84, 44, fill=WHITE, outline=HAIR, r=2)
    f.text(146, 72, "a lamp", 6.6, "regular", MUTED, "mm")
    f.text(146, 84, "came on", 7.2, "monobold", ACCENT, "mm")
    f.dash(98, 78, 104, 78, FAINT, 1, on=2, off=2)
    f.text(101, 106, "three feet apart · nothing between them", 6.2, "regular", MUTED, "mm")

    f.rect(420, 30, 200, 84, fill=WHITE, outline=INK, r=3)
    f.text(520, 44, "ELECTED DATABASE — SEATTLE", 6.8, "bold", INK, "mm")
    f.text(520, 68, "FrogNet Memory", 8.6, "monobold", ACCENT, "mm")
    f.text(520, 84, "one order, structurally", 6.6, "regular", MUTED, "mm")
    f.text(520, 100, "no cloud in the path · no broker in the story", 6.2, "regular", MUTED, "mm")

    f.arrow(202, 58, 418, 58, ACCENT, 1.3, 6)
    f.text(310, 50, "the write — 2,400 miles", 7, "bold", ACCENT, "mb")
    f.arrow(418, 92, 202, 92, MUTED, 1.3, 6)
    f.text(310, 100, "something that reads sensors noticed", 7, "regular", MUTED, "mt")

    f.rect(0, 132, 620, 56, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(14, 142, "Dan did not know he was setting a tuple.", 8.4, "bold", ACCENT)
    f.text(14, 158, "No endpoint. No subscription. No delivery guarantee. He was setting a sensor.",
           7.2, "regular", BODY)
    f.text(14, 172, "A surface has moved when competent people stop noticing the thing underneath it.",
           7.2, "bold", INK)
    return f


# -------------------------------------------------------------- registry -----

def fig_broker_rendezvous(scale):
    """Ch 20 - the broker is a rendezvous, not a party to anything."""
    f = Fig(600, 268, scale)
    f.cap(0, 2, "the only thing outside the memory")
    node(f, 24, 26, 150, 72, "NODE A", "behind NAT", accent=True)
    node(f, 426, 26, 150, 72, "NODE B", "behind NAT", accent=True)
    f.rect(216, 26, 168, 72, fill=WHITE, outline=HAIR, r=3)
    f.text(300, 44, "BROKER", 8.4, "bold", INK, "mm")
    f.text(300, 58, "host:18257", 6.8, "mono", MUTED, "mm")
    f.text(300, 72, "learns endpoints, hands them back", 6.2, "regular", MUTED, "mm")
    f.arrow(174, 52, 214, 52)
    f.arrow(426, 52, 386, 52)
    f.text(300, 112, "and then gets out of the way", 6.8, "bold", ACCENT, "mm")
    f.rect(24, 132, 552, 52, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.arrow(100, 158, 500, 158, width=1.6)
    f.text(300, 146, "WireGuard, direct, node to node", 7.4, "bold", ACCENT, "mm")
    f.text(300, 170, "every byte of memory crosses here - never the broker",
           6.6, "regular", BODY, "mm")
    f.rect(24, 196, 552, 56, fill=WHITE, outline=HAIR, r=3)
    f.text(36, 204, "WHAT IT IS NOT", 6.8, "bold", INK)
    for j, r in enumerate(["not an authority - it decides nothing about the network",
                           "not a relay - no data path runs through it",
                           "a dependency at join time, and only then"]):
        f.dot(42, 224 + j * 12, 1.6, MUTED)
        f.text(50, 220 + j * 12, r, 6.6, "regular", BODY)
    return f


def fig_handler_object(scale):
    """Ch 28 - one interface, a few virtual slots, everything else inherited."""
    f = Fig(600, 262, scale)
    f.cap(0, 2, "the handler is the thread body")
    f.rect(24, 26, 246, 120, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(38, 36, "THE INTERFACE", 8, "bold", ACCENT)
    for j, r in enumerate(["how to compress", "whether it holds a role",
                           "how to announce itself", "what to do on a value"]):
        f.dot(44, 60 + j * 16, 1.6, ACCENT)
        f.text(52, 56 + j * 16, r, 7, "mono", BODY)
    f.text(38, 126, "fill what you need. inherit the rest.", 6.6, "regular", MUTED)
    f.rect(318, 26, 258, 56, fill=WHITE, outline=HAIR, r=3)
    f.text(332, 36, "UnREST CORE", 7.6, "bold", INK)
    f.text(332, 52, "the DEFAULT behaviour of that interface", 6.6, "regular", BODY)
    f.text(332, 65, "SAME / DIFF / FULL, free, no subclass", 6.4, "mono", MUTED)
    f.rect(318, 90, 258, 56, fill=WHITE, outline=HAIR, r=3)
    f.text(332, 100, "UnREST UNLEASHED", 7.6, "bold", INK)
    f.text(332, 116, "override more: a media protocol, a game", 6.6, "regular", BODY)
    f.text(332, 129, "same interface, more slots filled", 6.4, "mono", MUTED)
    f.arrow(270, 60, 316, 54)
    f.arrow(270, 100, 316, 118)
    f.rect(24, 162, 552, 76, fill=WHITE, outline=HAIR, r=3)
    f.text(36, 170, "WHY THIS IS THE THREAD BODY", 6.8, "bold", INK)
    for j, r in enumerate([
            "nobody writes a message protocol between thread four and thread nine",
            "a handler reads what others wrote and writes what others will read",
            "the exchange is the substrate's problem, not the handler's"]):
        f.dot(42, 190 + j * 14, 1.6, ACCENT)
        f.text(50, 186 + j * 14, r, 6.8, "regular", BODY)
    return f


def fig_shotgun(scale):
    """Ch 31 - the same problem every streaming protocol has, stated plainly."""
    f = Fig(600, 250, scale)
    f.cap(0, 2, "one sender, receivers that are not alike")
    node(f, 234, 24, 132, 48, "SENDER", "one encoder", accent=True)
    caps = [("FAST", "gigabit", ACCENT), ("MIDDLING", "wifi, busy", MUTED),
            ("STARVED", "900 MHz", MUTED)]
    for i, (nm, sub, col) in enumerate(caps):
        x = 24 + i * 192
        f.arrow(300, 74, x + 84, 118, dashed=(i != 0))
        f.rect(x, 122, 168, 52, fill=WHITE, outline=HAIR, r=3)
        f.text(x + 14, 132, nm, 7.6, "bold", col)
        f.text(x + 14, 148, sub, 6.6, "mono", MUTED)
        f.text(x + 14, 160, "wants a different picture", 6.4, "regular", BODY)
    f.rect(24, 188, 552, 50, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(36, 196, "THE PROBLEM", 6.8, "bold", ACCENT)
    f.text(36, 212, "One stream cannot be three pictures. Shedding frames makes a "
           "slideshow, not a smaller picture -", 6.8, "regular", BODY)
    f.text(36, 224, "so the whole call runs at what the slowest end can take. "
           "Transcoding is the fix, and it is not built.", 6.8, "regular", BODY)
    return f


def fig_derivation(scale):
    """Ch 40f / XII-and-a-half - write what you know, read all, derive alone."""
    f = Fig(600, 264, scale)
    f.cap(0, 2, "nobody is told - everybody reads")
    for i, (nm, writes) in enumerate([("NODE A", "sending 854x480"),
                                      ("NODE B", "getting 23 fps, happy"),
                                      ("NODE C", "getting 4 fps, struggling")]):
        x = 12 + i * 192
        f.rect(x, 24, 176, 46, fill=WHITE, outline=HAIR, r=3)
        f.text(x + 12, 32, nm, 7.4, "bold", INK)
        f.text(x + 12, 48, writes, 6.6, "mono", BODY)
        f.arrow(x + 88, 70, 300, 92, dashed=True)
    f.rect(96, 96, 408, 40, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(300, 106, "SHARED MEMORY", 7.6, "bold", ACCENT, "mm")
    f.text(300, 122, "one row per participant - the only thing it alone knows",
           6.6, "regular", BODY, "mm")
    for i in range(3):
        f.arrow(300, 136, 100 + i * 192, 162)
    for i in range(3):
        x = 12 + i * 192
        f.rect(x, 166, 176, 40, fill=WHITE, outline=HAIR, r=3)
        f.text(x + 88, 176, "derive_rate()", 7, "mono", ACCENT, "mm")
        # NOT 854x480: node C reports struggling, so derive_rate steps DOWN one
        # rung. A figure that contradicts the function it illustrates is worse
        # than no figure.
        f.text(x + 88, 192, "640x360", 7.2, "bold", INK, "mm")
    f.rect(24, 218, 552, 32, fill=WHITE, outline=HAIR, r=3)
    f.text(300, 228, "one struggling report - all three step down, together",
           7, "bold", ACCENT, "mm")
    f.text(300, 240, "same rows, same function, same answer - and not one message",
           6.6, "regular", MUTED, "mm")
    return f


def fig_hello_world(scale):
    """Ch 45 - the Communicator is the smallest program touching every layer."""
    f = Fig(600, 250, scale)
    f.cap(0, 2, "a very complicated Hello, World")
    layers = [("the call", "presence, chat, A/V"),
              ("SotF ladder", "rungs, backpressure"),
              ("FrogNet Memory", "tuples, freshness, scope"),
              ("BLDC-1", "SAME / DIFF / FULL"),
              ("FNWP-1", "the wire"),
              ("bearers", "cable, wifi, radio, tunnel")]
    for i, (nm, sub) in enumerate(layers):
        y = 24 + i * 34
        acc = i in (0, 2)
        f.rect(24, y, 400, 28, fill=ACCENT_SOFT if acc else WHITE,
               outline=ACCENT_MID if acc else HAIR, r=2)
        f.text(38, y + 8, nm, 7.4, "bold", ACCENT if acc else INK)
        f.text(190, y + 9, sub, 6.6, "regular", BODY)
    f.rect(440, 24, 136, 198, fill=WHITE, outline=HAIR, r=3)
    f.text(452, 32, "WHAT IT FOUND", 6.8, "bold", INK)
    for j, r in enumerate(["a shim that broke", "on import", "", "a codex dropping",
                           "a field silently", "", "a store that", "half-answers",
                           "", "two kernel counters", "that do not subtract"]):
        if r:
            f.text(452, 50 + j * 14, r, 6.4, "regular", BODY)
    f.text(300, 232, "none of these are video bugs - they are platform facts the "
           "video exposed", 6.8, "bold", ACCENT, "mm")
    return f


def fig_resolv_chain(scale):
    """Ch 16 - a name from another pond, and how the query gets there."""
    f = Fig(600, 268, scale)
    f.cap(0, 2, "the local resolver first, peers next, the world last")
    f.rect(24, 26, 250, 60, fill=ACCENT_SOFT, outline=ACCENT_MID, r=3)
    f.text(38, 36, "A NODE IN BABox", 7.6, "bold", ACCENT)
    f.text(38, 54, "nslookup brokerhost.seattle5", 6.8, "mono", BODY)
    f.text(38, 68, "a name its own pond never heard of", 6.4, "regular", MUTED)
    rows = [("127.0.0.1", "own pond and .frognet", "no answer for this one", MUTED),
            ("10.250.250.1", "the machine at the far", "ANSWERS - 10.250.250.155", ACCENT),
            ("172.16.26.1", "the world", "never reached", MUTED)]
    for i, (ns, what, res, col) in enumerate(rows):
        y = 104 + i * 44
        acc = (col == ACCENT)
        f.rect(24, y, 552, 36, fill=ACCENT_SOFT if acc else WHITE,
               outline=ACCENT_MID if acc else HAIR, r=2)
        f.text(38, y + 12, "nameserver " + ns, 7.2, "mono", col)
        f.text(200, y + 8, what, 6.6, "regular", BODY)
        f.text(200, y + 20, "end of a tunnel" if i == 1 else "", 6.4,
               "regular", MUTED)
        f.text(400, y + 12, res, 6.8, "bold" if acc else "regular",
               ACCENT if acc else MUTED)
        if i < 2:
            f.arrow(300, y + 36, 300, y + 44)
    f.text(300, 250, "one second per miss - options timeout:1 attempts:1",
           6.6, "regular", MUTED, "mm")
    return f


FIGURES = {
    "resolv_chain": (fig_resolv_chain, "Ch 16 - resolving a name from another pond"),
    "broker_rendezvous": (fig_broker_rendezvous, "Ch 20 - the broker is a rendezvous, never a party"),
    "handler_object": (fig_handler_object, "Ch 28 - one interface, a few virtual slots"),
    "shotgun": (fig_shotgun, "Ch 31 - one stream, receivers that are not alike"),
    "derivation": (fig_derivation, "Ch 40f - write what you know, read all, derive alone"),
    "hello_world": (fig_hello_world, "Ch 45 - the smallest program touching every layer"),
    # referenced by build_magnum.js — these six are required for the docx build
    "multi_transport": (fig_multi_transport, "Part IV — transports multiplexed in one topology"),
    "two_planes": (fig_two_planes, "Part XIV — control plane and data plane, one socket"),
    "fanout": (fig_fanout, "Part XIV — mediahost fan-out, personalized per recipient"),
    "quality_ladder": (fig_quality_ladder, "Part XIV — seven rungs, framerate before resolution"),
    "backpressure": (fig_backpressure, "Part XIV — the socket's refusal is the signal"),
    "keyframe_anchor": (fig_keyframe_anchor, "Ch. 48 — the frame that must not be dropped"),
    # audit additions — book
    "wire_states": (fig_wire_states, "SAME / DIFF / FULL-RAW, all LZ4"),
    "dead_air": (fig_dead_air, "request/response idle wire vs one standing wire"),
    "discovery_walk": (fig_discovery_walk, "runmerge — neighbours, children, grandchildren"),
    "election": (fig_election, "capability scoring to one DatabaseHost"),
    "tuple_address": (fig_tuple_address, "three indices, any combination"),
    "address_scaling": (fig_address_scaling, "Appendix K — per tunnel vs per node"),
    "airgap_broker": (fig_airgap_broker, "reference topology and the air-gapped drop"),
    "scopes": (fig_scopes, "Pond / Chorus / Lillypad / Self"),
    "monitor_read": (fig_monitor_read, "the deletion — 1,945 lines against 318"),
    # audit additions — site
    "three_arms": (fig_three_arms, "Programming Surface Studies — the method"),
    "lamp_path": (fig_lamp_path, "April 17 2026 — the write and the consequence"),
}


def main():
    ap = argparse.ArgumentParser(description="Generate every FrogNet figure from code.")
    ap.add_argument("--out", default="magnum-figures", help="output directory")
    ap.add_argument("--scale", type=float, default=3.0, help="pixels per point (3 ≈ 300dpi)")
    ap.add_argument("--only", default="", help="comma-separated figure names")
    ap.add_argument("--force", action="store_true", help="redraw figures that already exist")
    ap.add_argument("--list", action="store_true", help="list figure names and exit")
    a = ap.parse_args()

    if a.list:
        for k, (_, note) in FIGURES.items():
            print("  %-18s %s" % (k, note))
        return 0

    names = [n.strip() for n in a.only.split(",") if n.strip()] or list(FIGURES)
    unknown = [n for n in names if n not in FIGURES]
    if unknown:
        sys.exit("unknown figure(s): %s\n(--list to see them all)" % ", ".join(unknown))

    os.makedirs(a.out, exist_ok=True)
    made = skipped = 0
    for n in names:
        path = os.path.join(a.out, n + ".png")
        if os.path.exists(path) and not a.force:
            skipped += 1
            continue
        fn = FIGURES[n][0]
        fig = fn(a.scale)
        fig.save(path)
        made += 1
        print("  drew %-18s %4d x %4d px" % (n + ".png", fig.im.width, fig.im.height))
    print("%d drawn, %d already present → %s" % (made, skipped, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
