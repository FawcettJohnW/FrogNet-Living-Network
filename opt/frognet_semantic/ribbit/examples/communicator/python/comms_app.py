#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""comms_app.py -- the FrogNet Communicator window, in Python (PySide6). The same window as the C++ comms-app.

  comms_app.py --ram HOST:PORT --media HOST:PORT --name NAME [--camera DEV | --video FILE | --synthetic WxH]
               [--size WxH] [--fps N] [--no-audio] [--mic N] [--speaker N] [--list-devices] [--bandwidth BPS]
               [--auto-join NAME] [--screenshot FILE --after SECONDS] [--show-controls] [--diagnostics]
               [--tab security] [--security "cipher NAME;key;protocol NAME;ram HOST:PORT"]

Two tabs, as in comms-app. Call: the lobby, the call, the chat. Security: [THE_CALL_IS_UNDER_THE_FRAME_V1] what the
Role's setting is now and what every end is running, and the owner's changes: the cipher, a new key, the protocol and
"Change RAM host" ([THE_MEMORY_MOVES_V1]). Each is one write of the Role's setting (frogcomms.channel.change)."""
import argparse, math, os, random, sys, threading, time
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPointF, Signal  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,  # noqa: E402
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSlider, QStyle, QTabWidget, QVBoxLayout, QWidget)
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import AvSource, SyntheticSource  # noqa: E402
from frogcomms import fnav as F  # noqa: E402
from frogcomms import channel, ram_at  # noqa: E402

# r5: the switches beside the picture, the overlay shows the channel, a call ends when the other leaves, --script
APP_BUILD = "comms-app-py-2026-11-26-r5"
K_STEPS, K_MIN, K_MAX = 1000, 32000.0, 4000000.0     # the bandwidth slider: 0..999 continuous, 1000 = unlimited


def bps_of(v):
    return 0 if v >= K_STEPS else int(K_MIN * (K_MAX / K_MIN) ** (v / (K_STEPS - 1)))


def pos_of(bps):
    x = math.log(bps / K_MIN) / math.log(K_MAX / K_MIN)
    return max(0, min(K_STEPS - 1, round(x * (K_STEPS - 1))))


def to_image(frame):
    """an av.VideoFrame as a QImage (RGB, owned)"""
    a = frame.to_ndarray(format="rgb24")
    return QImage(a.data, a.shape[1], a.shape[0], a.shape[1] * 3, QImage.Format_RGB888).copy()


class VideoView(QWidget):
    """[REAL_SIZE_UNLESS_TOO_BIG_V1] drawn at its own size, centred, scaled only DOWN (or up with Fit); a control bar
    on hover: pause, mute, volume, real size, 1:1/Fit, diagnostics, full screen. Keys F/Esc/Space/M/D."""

    def __init__(self):
        super().__init__()
        self.remote = self.self_img = None
        self.who = self.overlay = self.self_note = self.no_video = ""
        self.paused = self.fill = self.muted = self.pinned = False
        self.on_fullscreen = self.on_volume = self.on_diagnostics = None
        self.setMinimumSize(480, 270); self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self.bar = QWidget(self); self.bar.setObjectName("vbar")
        self.bar.setStyleSheet("QWidget#vbar{background:rgba(246,247,249,235);border-radius:8px}"
                               "QPushButton{background:transparent;border:none;color:#1d2024;padding:4px 8px}"
                               "QPushButton:hover{background:rgba(0,0,0,25);border-radius:6px}"
                               "QLabel{color:#3a3f45;background:transparent;font-size:12px}")
        l = QHBoxLayout(self.bar); l.setContentsMargins(8, 4, 8, 4); l.setSpacing(4)
        self.pause_b = self._button(QStyle.SP_MediaPause, "Pause (Space)", lambda: self.set_paused(not self.paused))
        self.mute_b = self._button(QStyle.SP_MediaVolume, "Mute (M)", lambda: self.set_muted(not self.muted))
        self.vol = QSlider(Qt.Horizontal); self.vol.setRange(0, 150); self.vol.setValue(100); self.vol.setFixedWidth(110)
        self.vol.valueChanged.connect(lambda _: self._push_volume())
        self.size_l = QLabel()
        self.fit_b = QPushButton("1:1"); self.fit_b.clicked.connect(self._toggle_fit)
        self.diag_b = self._button(QStyle.SP_FileDialogDetailedView, "Diagnostics (D)", lambda: self.on_diagnostics and self.on_diagnostics())
        self.full_b = self._button(QStyle.SP_TitleBarMaxButton, "Full screen (F, double-click)", lambda: self.on_fullscreen and self.on_fullscreen())
        for w in (self.pause_b, self.mute_b, self.vol):
            l.addWidget(w)
        l.addStretch()
        for w in (self.size_l, self.fit_b, self.diag_b, self.full_b):
            l.addWidget(w)
        self.hide_t = QTimer(self); self.hide_t.setSingleShot(True)
        self.hide_t.timeout.connect(lambda: (not self.paused and not self.pinned) and self.bar.hide())
        self.bar.hide()

    def _button(self, icon, tip, fn):
        b = QPushButton(); b.setIcon(self.style().standardIcon(icon)); b.setToolTip(tip); b.clicked.connect(fn)
        return b

    def _toggle_fit(self):
        self.fill = not self.fill; self.fit_b.setText("Fit" if self.fill else "1:1"); self.update()

    def _push_volume(self):
        if self.on_volume:
            self.on_volume(0.0 if self.muted else self.vol.value() / 100.0)

    def show_bar(self):
        self.bar.show(); self.bar.raise_(); self.hide_t.start(2500)

    def set_paused(self, p):
        self.paused = p
        self.pause_b.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay if p else QStyle.SP_MediaPause))
        self.show_bar(); self.update()

    def set_muted(self, m):
        self.muted = m
        self.mute_b.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted if m else QStyle.SP_MediaVolume))
        self._push_volume(); self.show_bar()

    def set_fullscreen_icon(self, fs):
        self.full_b.setIcon(self.style().standardIcon(QStyle.SP_TitleBarNormalButton if fs else QStyle.SP_TitleBarMaxButton))

    def resizeEvent(self, e):
        w = min(self.width() - 20, 640)
        self.bar.setGeometry((self.width() - w) // 2, self.height() - 52, w, 40)

    def mouseMoveEvent(self, e): self.show_bar()
    def mouseDoubleClickEvent(self, e): self.on_fullscreen and self.on_fullscreen()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_F and self.on_fullscreen: self.on_fullscreen()
        elif k == Qt.Key_Escape and self.isFullScreen() and self.on_fullscreen: self.on_fullscreen()
        elif k == Qt.Key_Space: self.set_paused(not self.paused)
        elif k == Qt.Key_M: self.set_muted(not self.muted)
        elif k == Qt.Key_D and self.on_diagnostics: self.on_diagnostics()
        else: super().keyPressEvent(e)

    def paintEvent(self, e):
        g = QPainter(self); g.setRenderHint(QPainter.SmoothPixmapTransform)
        g.fillRect(self.rect(), QColor(24, 26, 30))
        if self.remote is not None:
            s = self.remote.size()
            if self.fill or s.width() > self.width() or s.height() > self.height():
                s = s.scaled(self.size(), Qt.KeepAspectRatio)
            r = QRect((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s.width(), s.height())
            g.drawImage(r, self.remote)
            self.size_l.setText("%dx%d" % (self.remote.width(), self.remote.height()))
            if self.paused:
                g.fillRect(QRect(r.left(), r.top(), r.width(), 22), QColor(0, 0, 0, 140))
                g.setPen(Qt.white); g.drawText(QRect(r.left(), r.top(), r.width(), 22), Qt.AlignCenter, "Paused")
        elif self.no_video:                               # [NO_VIDEO_IS_A_STATE_V1] never a frozen last frame
            f = self.font(); f.setPointSize(20); f.setBold(True); g.setFont(f); g.setPen(QColor(225, 230, 235))
            g.drawText(self.rect().adjusted(0, -24, 0, -24), Qt.AlignCenter, "No video")
            f = self.font(); f.setPointSize(11); g.setFont(f); g.setPen(QColor(150, 155, 165))
            g.drawText(self.rect().adjusted(0, 24, 0, 24), Qt.AlignCenter, self.no_video)
        else:
            g.setPen(QColor(150, 155, 165))
            g.drawText(self.rect(), Qt.AlignCenter, "Not in a call" if not self.who else "Waiting for %s's picture" % self.who)
        if self.overlay:
            f = QFont("Monospace"); f.setStyleHint(QFont.Monospace); f.setPointSize(9); g.setFont(f)
            tr = g.fontMetrics().boundingRect(QRect(0, 0, 900, 600), Qt.AlignLeft, self.overlay).adjusted(-8, -6, 8, 6)
            tr.moveTo(10, 10)
            g.setPen(Qt.NoPen); g.setBrush(QColor(0, 0, 0, 150)); g.drawRoundedRect(tr, 6, 6)
            g.setPen(QColor(225, 230, 235)); g.drawText(tr.adjusted(8, 6, -8, -6), Qt.AlignLeft, self.overlay)
        if self.self_img is not None:
            w = max(120, self.width() // 5); h = w * self.self_img.height() // max(1, self.self_img.width())
            r = QRect(self.width() - w - 10, 10, w, h)
            g.drawImage(r, self.self_img)
            g.setPen(QColor(255, 255, 255, 180)); g.setBrush(Qt.NoBrush); g.drawRect(r)
            if self.self_note:
                f = self.font(); f.setPointSize(8); g.setFont(f)
                g.fillRect(QRect(r.left(), r.bottom() - 16, r.width(), 16), QColor(0, 0, 0, 140))
                g.drawText(QRect(r.left(), r.bottom() - 16, r.width(), 16), Qt.AlignCenter, self.self_note)


class DiagWindow(QWidget):
    """After comms_diag.py: the bearer, the WIRE figures, RUNG / THROUGHPUT / REFUSED on one axis, the law and the
    lag. [DIAG_IS_UNGATED_V1] a measured zero is a zero; no call is a gap."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FrogNet Communicator -- diagnostics"); self.resize(880, 760)
        self.setStyleSheet("QWidget{background:#ffffff;color:#1d2024}"
                           "QPushButton{background:#eef1f5;border:1px solid #c9ced5;border-radius:6px;padding:3px 10px}"
                           "QPushButton:checked{background:#dbe7fb;border-color:#8fb0e8}")
        self.hist, self.span = None, 60.0
        self.wire = ["", "", ""]; self.alarm = False; self.lag_text = ""
        top = QHBoxLayout(); top.setContentsMargins(12, 8, 12, 0)
        self.head = QLabel(); self.head.setStyleSheet("font-weight:600")
        top.addWidget(self.head); top.addStretch()
        self.spans = []
        for secs, label in ((30.0, "30s"), (60.0, "60s"), (120.0, "2m")):
            b = QPushButton(label); b.setCheckable(True); b.setChecked(secs == self.span)
            b.clicked.connect(lambda _=False, s=secs, b=b: self._span(s, b)); self.spans.append(b); top.addWidget(b)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.addLayout(top); v.addStretch()

    def _span(self, s, b):
        self.span = s
        for o in self.spans:
            o.setChecked(o is b)
        self.update()

    def paintEvent(self, e):
        g = QPainter(self); g.setRenderHint(QPainter.Antialiasing)
        MUTE, GRID, VID, AUD, RX, ROOF = (QColor(110, 117, 125), QColor(232, 235, 239), QColor(217, 100, 90),
                                          QColor(220, 150, 30), QColor(80, 120, 200), QColor(220, 150, 30))
        last = self.hist[-1] if self.hist else None
        self.head.setText("BEARER    rung L%d    ceiling L%d    stressed %d/%d    clean %d/%d" % (
            last["rung"], last["ceiling"], last["stressed"], F.Bearer.BAD_READS, last["clean"], F.Bearer.CLEAN_SECS)
            if last and last["live"] else "BEARER    no call running")
        big = QFont("Monospace"); big.setStyleHint(QFont.Monospace); big.setPointSize(11); g.setFont(big)
        wire = QRect(12, 44, self.width() - 24, 78); g.fillRect(wire, QColor(246, 247, 249))
        g.setPen(QColor(40, 44, 50))
        g.drawText(wire.adjusted(10, 6, -10, 0), Qt.AlignLeft | Qt.AlignTop, self.wire[0])
        g.drawText(wire.adjusted(10, 30, -10, 0), Qt.AlignLeft | Qt.AlignTop, self.wire[1])
        g.setPen(VID if self.alarm else MUTE)
        g.drawText(wire.adjusted(10, 54, -10, 0), Qt.AlignLeft | Qt.AlignTop, self.wire[2])
        if not self.hist:
            return
        now = self.hist[-1]["t"]; t0 = now - self.span
        x0, x1, PH, GAP = 60, self.width() - 20, 150, 38
        X = lambda t: x0 + (t - t0) / self.span * (x1 - x0)
        small = self.font(); small.setPointSize(8)

        def panel(y, title, unit):
            g.setFont(small); g.setPen(MUTE)
            g.drawText(x0, y - 6, title); g.drawText(QRect(x1 - 300, y - 18, 300, 14), Qt.AlignRight, unit)
            g.setPen(GRID); g.setBrush(Qt.NoBrush); g.drawRect(x0, y, x1 - x0, PH)
            for k in (1, 2, 3):
                g.drawLine(x0, y + PH * k // 4, x1, y + PH * k // 4)

        def series(y, vmax, col, key, step=False, style=Qt.SolidLine):
            pen = QPen(col, 2); pen.setStyle(style); g.setPen(pen)
            run = []

            def flush():
                if len(run) > 1:
                    g.drawPolyline(QPolygonF(run))
                run.clear()
            for s in self.hist:
                if s["t"] < t0:
                    continue
                if not s["live"]:
                    flush(); continue
                p = QPointF(X(s["t"]), y + PH - min(1.0, s[key] / vmax) * PH)
                if step and run:
                    run.append(QPointF(p.x(), run[-1].y()))
                run.append(p)
            flush()
        live = [s for s in self.hist if s["live"] and s["t"] >= t0]
        ymax_tp = max([1.0] + [max(s["tx_v"] + s["tx_a"], s["rx_v"] + s["rx_a"], s["budget"]) for s in live]) * 1.15
        ymax_ref = max(5.0, max([0.0] + [max(s["ref_v"], s["ref_a"]) for s in live]) * 1.2)
        y = 150
        panel(y, "RUNG", "L0 .. L8"); g.setFont(small); g.setPen(MUTE)
        for r in (0, 2, 4, 6, 8):
            g.drawText(QRect(x0 - 40, int(y + PH - r / 8 * PH) - 7, 34, 14), Qt.AlignRight, "L%d" % r)
        for roof in (True, False):
            pen = QPen(ROOF if roof else RX, 1 if roof else 2)
            if roof:
                pen.setStyle(Qt.DashLine)
            run, prev = [], -1
            for s in self.hist:
                if s["t"] < t0:
                    continue
                if not s["live"]:
                    if len(run) > 1:
                        g.setPen(pen); g.drawPolyline(QPolygonF(run))
                    run, prev = [], -1; continue
                r = s["ceiling"] if roof else s["rung"]
                p = QPointF(X(s["t"]), y + PH - r / 8 * PH)
                if run:
                    run.append(QPointF(p.x(), run[-1].y()))
                run.append(p)
                if not roof and prev >= 0 and r != prev:
                    g.setPen(QPen(VID if r < prev else QColor(60, 160, 100), 1, Qt.DotLine))
                    g.drawLine(QPointF(p.x(), y), QPointF(p.x(), y + PH))
                prev = r
            if len(run) > 1:
                g.setPen(pen); g.drawPolyline(QPolygonF(run))
        y += PH + GAP
        panel(y, "THROUGHPUT", "KB/s"); g.setFont(small); g.setPen(MUTE)
        g.drawText(QRect(x0 - 50, y - 7, 44, 14), Qt.AlignRight, "%.0f" % ymax_tp)
        series(y, ymax_tp, ROOF, "budget", True, Qt.DashLine); series(y, ymax_tp, VID, "tx_v")
        series(y, ymax_tp, AUD, "tx_a"); series(y, ymax_tp, RX, "rx_v", False, Qt.DashLine)
        g.setFont(small)
        for x, col, t in ((330, VID, "video sent"), (260, AUD, "audio sent"), (190, RX, "video received"), (95, ROOF, "budget")):
            g.setPen(col); g.drawText(x1 - x, y + PH + 13, t)
        y += PH + GAP
        panel(y, "REFUSED", "frames/s the wire refused"); g.setFont(small); g.setPen(MUTE)
        g.drawText(QRect(x0 - 50, y - 7, 44, 14), Qt.AlignRight, "%.0f" % ymax_ref)
        series(y, ymax_ref, VID, "ref_v"); series(y, ymax_ref, AUD, "ref_a")
        g.setPen(VID); g.drawText(x1 - 95, y + PH + 13, "video"); g.setPen(AUD); g.drawText(x1 - 50, y + PH + 13, "audio")
        y += PH + 30
        g.setFont(small); g.setPen(MUTE)
        g.drawText(QRect(x0, y, x1 - x0, 30), Qt.AlignLeft | Qt.TextWordWrap,
                   "step DOWN after %d stressed samples or %d drops in a second;  UP after %d clean seconds at full frame "
                   "rate;  a failed rung is held off %d s, doubling to %d s" % (F.Bearer.BAD_READS + 1, F.Bearer.SEC_DROPS + 1,
                   F.Bearer.CLEAN_SECS, F.Bearer.HOLD_BASE_S, F.Bearer.HOLD_MAX_S))
        g.setPen(QColor(40, 44, 50)); g.setFont(self.font())
        g.drawText(QRect(x0, y + 34, x1 - x0, 18), Qt.AlignLeft, self.lag_text)


class Window(QWidget):
    sec_done = Signal(str)                                  # the owner's change answered (from its own thread)
    sec_ends = Signal(str)                                  # every end's report, read off the window's thread

    def __init__(self, o):
        super().__init__()
        self.o = o
        self.sec_changes, self.ends_age, self.ends_busy, self.sec_steps = -1, 99, False, [x for x in (o.security or "").split(";") if x]
        rh, rp = o.ram.rsplit(":", 1); self.media_host, mp = o.media.rsplit(":", 1); self.media_port = int(mp)
        self.room = Room(rh, int(rp), o.name, "video,audio")
        self.call = None; self.session = ""; self.pending = None; self.answered = set()
        self.gain = 1.0; self.seen = -1; self.age_redraw = 0; self.ticks = 0
        # the owner's changes beside the picture; the script and what it measures between two of its steps
        self.shown_changes, self.changing, self.other_on, self.calls_made, self.with_ = -1, False, False, 0, ""
        self.steps = [x.strip() for x in (o.script or "").split(";") if x.strip()]
        self.step, self.step_begun, self.step_at = 0, False, None
        self.got_picture, self.prev_picture, self.pictures, self.mark_pictures, self.gap_max_ms, self.mark_at = False, 0.0, 0, 0, 0.0, time.monotonic()
        self.last_seq = self.self_seq = 0; self.rx = (0, 0, 0)
        self.prev = {}; self.t_last = time.monotonic()
        self.hist = deque(); self.t_start = time.monotonic(); self.d_prev = None; self.d_last = time.monotonic()
        self.last_picture = time.monotonic()
        self._build()
        self.tick = QTimer(self); self.tick.timeout.connect(self._tick); self.tick.start(1000)
        self.frame_t = QTimer(self); self.frame_t.timeout.connect(self._paint_frame); self.frame_t.start(40)
        self.sampler = QTimer(self); self.sampler.timeout.connect(self._sample); self.sampler.start(500)

    def _build(self):
        self.setWindowTitle("FrogNet Communicator  ·  " + APP_BUILD)
        self.setStyleSheet(
            "QWidget{background:#f6f7f9;color:#1d2024;font-size:13px}QFrame#bar{background:#ffffff;border-bottom:1px solid #dfe2e6}"
            "QListWidget{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:4px}"
            "QListWidget::item{padding:6px;border-radius:6px}QListWidget::item:selected{background:#e6effc;color:#1d2024}"
            "QPushButton{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px 12px}"
            "QPushButton:hover{background:#eef1f5}QPushButton#hang{background:#fbe9e9;color:#a32020;border-color:#efc4c4}"
            "QPushButton#answer{background:#e7f5ec;color:#16703a;border-color:#bfe3cc}"
            "QLabel#tile{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:6px 8px}"
            "QLabel#title{font-weight:600;font-size:14px}QLabel#muted{color:#6b7280;font-size:12px}"
            "QLineEdit{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px}"
            "QComboBox{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:5px 8px}"
            "QPlainTextEdit{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:6px;font-family:monospace;font-size:12px}"
            "QLabel#value{font-weight:600}"
            "QLabel#result{background:#fff8e6;border:1px solid #ecd9a3;border-radius:8px;padding:8px}"
            "QTabWidget::pane{border:0}"
            "QTabBar::tab{background:#eceef1;border:1px solid #dfe2e6;border-bottom:0;border-top-left-radius:8px;border-top-right-radius:8px;padding:7px 22px;margin-left:6px}"
            "QTabBar::tab:selected{background:#ffffff;font-weight:600}")
        top = QVBoxLayout(self); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(0)
        bar = QFrame(); bar.setObjectName("bar"); bl = QHBoxLayout(bar); bl.setContentsMargins(14, 8, 14, 8)
        t = QLabel("FrogNet Communicator"); t.setObjectName("title")
        self.status = QLabel(); self.status.setObjectName("muted")
        me = QLabel(self.o.name + "  (Python)"); me.setObjectName("muted")
        bl.addWidget(t); bl.addSpacing(10); bl.addWidget(self.status); bl.addStretch(); bl.addWidget(me)
        top.addWidget(bar)
        body = QHBoxLayout(); body.setContentsMargins(10, 10, 10, 10); body.setSpacing(10)
        left = QVBoxLayout(); lt = QLabel("In the room"); lt.setObjectName("muted"); left.addWidget(lt)
        self.lobby = QListWidget(); self.lobby.setMinimumWidth(200); left.addWidget(self.lobby, 1)
        self.call_b = QPushButton("Call"); left.addWidget(self.call_b); self.call_b.clicked.connect(self.call_selected)
        self.lobby.itemDoubleClicked.connect(lambda _: self.call_selected())
        self.answer_b = QPushButton(); self.answer_b.setObjectName("answer"); self.answer_b.hide(); left.addWidget(self.answer_b)
        self.answer_b.clicked.connect(self.answer)
        body.addLayout(left)
        self.mid = QVBoxLayout(); self.mid.setSpacing(8)
        self.view = VideoView(); self.mid.addWidget(self.view, 1)
        self.view.on_fullscreen = self.toggle_fullscreen
        self.view.on_volume = self._set_gain
        self.diag = DiagWindow()
        self.view.on_diagnostics = lambda: (setattr(self.diag, "hist", self.hist), self.diag.show(), self.diag.raise_())
        if self.o.show_controls:
            self.view.pinned = True; self.view.show_bar()
        ctl = QHBoxLayout(); self.hang_b = QPushButton("Hang up"); self.hang_b.setObjectName("hang"); self.hang_b.setEnabled(False)
        self.hang_b.clicked.connect(self.hang_up); ctl.addStretch(); ctl.addWidget(self.hang_b); ctl.addStretch(); self.mid.addLayout(ctl)

        def slider(name, mx):
            row = QHBoxLayout(); l = QLabel(name); l.setFixedWidth(90); l.setObjectName("muted")
            s = QSlider(Qt.Horizontal); s.setRange(0, mx); v = QLabel(); v.setFixedWidth(90); v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(l); row.addWidget(s, 1); row.addWidget(v); self.mid.addLayout(row)
            return s, v
        # [THE_SLIDER_IS_SMOOTH_V1] [THE_LINK_IS_SYMMETRIC_V1] [JITTER_IS_THE_LINK_V1]
        self.bw, self.bw_val = slider("Bandwidth", K_STEPS)
        self.bw.setValue(pos_of(self.o.bandwidth) if self.o.bandwidth else K_STEPS)
        self.bw_rest = QTimer(self); self.bw_rest.setSingleShot(True); self.bw_rest.timeout.connect(lambda: self.apply_bw(self.bw.value()))
        self.bw.valueChanged.connect(lambda v: (self.show_bw(v), self.bw_rest.start(200)))
        self.jit, self.jit_val = slider("Jitter", 500)
        self.jit.valueChanged.connect(lambda v: (self.jit_val.setText("%d ms" % v), self.call and self.call.set_jitter(v)))
        self.show_bw(self.bw.value()); self.jit_val.setText("0 ms")
        tiles = QHBoxLayout(); self.tiles = []
        for _ in range(4):
            l = QLabel(); l.setObjectName("tile"); tiles.addWidget(l, 1); self.tiles.append(l)
        self.mid.addLayout(tiles)
        # [SWITCH_IT_FROM_THE_CALL_V1] (John 2026-11-26: "While it is running I want to be able to switch ciphers, keys,
        # protocols, etc. from the app in real time.") The owner's changes beside the picture: the same one write the
        # Security tab makes, by the same code. A pick takes effect as it is picked.
        sw = QHBoxLayout(); sw.setSpacing(8)
        def small(t):
            l = QLabel(t); l.setObjectName("muted"); return l
        self.c_cipher = QComboBox(); self.c_cipher.addItems(["not sealed", "aes-256-gcm", "chacha20-poly1305"])
        self.c_cipher.textActivated.connect(self._set_cipher)
        self.c_key = QPushButton("New key"); self.c_key.clicked.connect(self._new_key)
        self.c_protocol = QComboBox(); self.c_protocol.addItems(["tcp", "chorus", "openqasm"])
        self.c_protocol.textActivated.connect(self._set_protocol)
        self.c_ram_to = QLineEdit(); self.c_ram_to.setPlaceholderText("RAM host:port"); self.c_ram_to.setMinimumWidth(150); self.c_ram_to.setMaximumWidth(190)
        self.c_ram_go = QPushButton("Move"); self.c_ram_go.clicked.connect(lambda: self._move_ram(self.c_ram_to.text()))
        self.c_ram_to.returnPressed.connect(lambda: self._move_ram(self.c_ram_to.text()))
        for w in (small("Cipher"), self.c_cipher, self.c_key, small("Protocol"), self.c_protocol, small("Memory"), self.c_ram_to, self.c_ram_go):
            sw.addWidget(w)
        sw.addStretch(); self.mid.addLayout(sw)
        self.c_result = QLabel("Cipher, key, protocol and where the memory is can be changed here while a call runs.")
        self.c_result.setObjectName("muted"); self.c_result.setWordWrap(True); self.mid.addWidget(self.c_result)
        body.addLayout(self.mid, 1)
        right = QVBoxLayout(); self.chat_title = QLabel("Chat"); self.chat_title.setObjectName("muted"); right.addWidget(self.chat_title)
        self.chat = QListWidget(); self.chat.setMinimumWidth(220); self.chat.setWordWrap(True); right.addWidget(self.chat, 1)
        self.say = QLineEdit(); self.say.setPlaceholderText("Start a call to chat"); self.say.setEnabled(False); right.addWidget(self.say)
        self.say.returnPressed.connect(self._send_chat)
        body.addLayout(right)
        self.tabs = QTabWidget()
        call_tab = QWidget(); call_tab.setLayout(body)
        self.tabs.addTab(call_tab, "Call"); self.tabs.addTab(self._build_security(), "Security")
        self.tabs.currentChanged.connect(lambda i: i == 1 and (setattr(self, "ends_age", 99), self._refresh_security()))
        top.addWidget(self.tabs, 1); self.resize(1180, 720)
        if self.o.tab == "security":
            self.tabs.setCurrentIndex(1)
        self._update_tiles()

    # -- the Security tab ------------------------------------------------------------------------------------------
    def _build_security(self):
        w = QWidget(); row = QHBoxLayout(w); row.setContentsMargins(18, 16, 18, 16); row.setSpacing(24)
        left = QVBoxLayout(); left.setSpacing(10)

        def head(t):
            l = QLabel(t); l.setObjectName("title"); return l

        def muted(t):
            l = QLabel(t); l.setObjectName("muted"); l.setWordWrap(True); return l
        left.addWidget(head("The channel now"))
        now = QGridLayout(); now.setHorizontalSpacing(14); now.setVerticalSpacing(6)
        self.s_now = {}
        for i, name in enumerate(("Role", "Cipher", "Key", "Protocol", "RAM host", "This program")):
            now.addWidget(muted(name), i, 0, Qt.AlignTop)
            v = QLabel(); v.setObjectName("value"); v.setWordWrap(True); v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            now.addWidget(v, i, 1); self.s_now[name] = v
        now.setColumnStretch(1, 1); left.addLayout(now); left.addSpacing(8)
        left.addWidget(head("Change it"))
        left.addWidget(muted("Each change is one write of the Role's setting. It carries a check made with this machine's secret, and every "
                             "end switches in its own stream: a call is not interrupted."))
        g = QGridLayout(); g.setHorizontalSpacing(10); g.setVerticalSpacing(8)
        self.s_cipher = QComboBox(); self.s_cipher.addItems(["not sealed", "aes-256-gcm", "chacha20-poly1305"])
        self.s_protocol = QComboBox(); self.s_protocol.addItems(["tcp", "chorus", "openqasm"])
        self.s_ram_to = QLineEdit(); self.s_ram_to.setPlaceholderText("host:port of another comms-ram that is running")
        cipher_go, key_go, protocol_go, ram_go = QPushButton("Set cipher"), QPushButton("New key"), QPushButton("Set protocol"), QPushButton("Change RAM host")
        cipher_go.clicked.connect(lambda: self._set_cipher(self.s_cipher.currentText()))
        key_go.clicked.connect(self._new_key)
        protocol_go.clicked.connect(lambda: self._set_protocol(self.s_protocol.currentText()))
        ram_go.clicked.connect(lambda: self._move_ram(self.s_ram_to.text()))
        g.addWidget(muted("Cipher"), 0, 0); g.addWidget(self.s_cipher, 0, 1); g.addWidget(cipher_go, 0, 2)
        g.addWidget(muted("Key"), 1, 0); g.addWidget(muted("A new random number in the setting: every sender computes a new key from it."), 1, 1); g.addWidget(key_go, 1, 2)
        g.addWidget(muted("Protocol"), 2, 0); g.addWidget(self.s_protocol, 2, 1); g.addWidget(protocol_go, 2, 2)
        g.addWidget(muted("RAM host"), 3, 0); g.addWidget(self.s_ram_to, 3, 1); g.addWidget(ram_go, 3, 2)
        g.setColumnStretch(1, 1); left.addLayout(g)
        self.s_result = QLabel("No change made from this window yet."); self.s_result.setObjectName("result"); self.s_result.setWordWrap(True)
        self.s_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(self.s_result); left.addStretch(); row.addLayout(left, 5)
        right = QVBoxLayout(); right.setSpacing(8); right.addWidget(head("Every end and what it is running"))
        self.s_ends = QPlainTextEdit(); self.s_ends.setReadOnly(True); right.addWidget(self.s_ends, 1); row.addLayout(right, 4)
        self.s_buttons = [cipher_go, key_go, protocol_go, ram_go]
        self.sec_done.connect(self._owner_done); self.sec_ends.connect(self._ends_read)
        return w

    def _said_both(self, t):
        self.s_result.setText(t); self.c_result.setText(t.replace("\n", "  ·  "))

    def _set_cipher(self, a):
        self._owner_change("cipher " + a, dict(unseal=True) if a == "not sealed" else dict(algorithm=a))

    def _new_key(self):
        self._owner_change("a new key", dict(new_key=True))

    def _set_protocol(self, p):
        self._owner_change("protocol " + p, dict(protocol=p))

    def _move_ram(self, text):
        to = text.strip()
        host, _, port = to.rpartition(":")
        if not host or not port.isdigit():
            self._said_both("Give the new RAM host as host:port."); return
        self._owner_change("the memory to " + to, dict(move_to=(host, int(port))))

    def _show_setting(self):
        """every picker shows the setting this program holds, whoever changed it and from where"""
        ch = channel.watching()
        if ch is None or ch.changes == self.shown_changes:
            return
        self.shown_changes = ch.changes
        f = ch.fields
        sealed = str(f.get("number", "0")) not in ("", "0")
        for b in (self.c_cipher, self.s_cipher):
            b.setCurrentText(f.get("protection", "") if sealed else "not sealed")
        for b in (self.c_protocol, self.s_protocol):
            b.setCurrentText(f.get("driver") or "tcp")

    def _channel_lines(self):
        """[THE_OVERLAY_SHOWS_THE_CHANNEL_V1] the channel's lines of the overlay"""
        ch = channel.watching()
        if ch is None:
            return "channel: none (start with --channel ROLE)"
        f = ch.fields
        sealed = str(f.get("number", "0")) not in ("", "0")
        host, port = ram_at.at()
        n = ch.totals(); c = n["calls"]
        t = ("channel %s%s\nprotocol %s · cipher %s%s\nmemory %s:%d\nunder the frame %d kB out · %d kB in\n"
             "switch calls sent: protocol %d · key/cipher %d · received: protocol %d · key/cipher %d\nsent again %d · not opened %d"
             % (ch.role, "" if ch.framed else " (planes NOT under the frame)", f.get("driver") or "tcp", f.get("protection", "") if sealed else "not sealed",
                " · key %s" % ch.key_tag if sealed else "", host, port, n["sent"] // 1000, n["received"] // 1000,
                c["protocol"][0], c["encryption"][0], c["protocol"][1], c["encryption"][1], n["sent_again"], n["not_opened"]))
        what, at, age = ch.last_change()
        if what:
            t += "\nlast: %s\n      at %s (%s ago) · %d change(s) taken" % (what, at, "%.0f s" % age if age < 90 else "%.0f min" % (age / 60), ch.changes)
        return t

    def _owner_change(self, what, change):
        """One change by the owner, off the window's thread: the write, then every end's report."""
        host, port = ram_at.at()
        ch = channel.watching()
        role = ch.role if ch else channel.ROLE
        self._said_both("Setting %s ..." % what)
        self.changing = True
        for b in self.s_buttons + [self.c_cipher, self.c_key, self.c_protocol, self.c_ram_go]:
            b.setEnabled(False)

        def run():
            try:
                d = channel.change(host, port, role, **change)
                said = "\n".join(["%s: %s" % (what, d["summary"])] + d["lines"] + (["%d line(s) of chat carried there" % d["chat_carried"]] if "move_to" in change else [])
                                 + ([] if d["checked"] else ["This machine holds no secret for the Role: the setting carries no check, and an end that holds one ignores it."]))
            except Exception as e:
                said = "Not set: %s" % e
            print("[security] " + said.replace("\n", " | "), flush=True)
            self.sec_done.emit(said)
        threading.Thread(target=run, daemon=True).start()

    def _owner_done(self, said):
        self._said_both(said)
        for b in self.s_buttons + [self.c_cipher, self.c_key, self.c_protocol, self.c_ram_go]:
            b.setEnabled(True)
        self.changing = False
        self.ends_age = 99; self._refresh_security()

    def _ends_read(self, text):
        if self.s_ends.toPlainText() != text:
            self.s_ends.setPlainText(text)
        self.ends_busy = False

    def _refresh_security(self):
        ch = channel.watching()
        if ch is None:
            self.s_now["Role"].setText("no channel"); return
        host, port = ram_at.at()
        f = ch.fields
        sealed = str(f.get("number") or "0") != "0"
        try:
            secret = channel.secret_line(ch.role, ch.secrets)
        except channel.Gone:
            secret = "this machine holds no secret for the Role (%s)" % ch.secrets
        cipher, protocol = (f.get("protection") if sealed else "not sealed"), f.get("driver") or "tcp"
        self.s_now["Role"].setText(ch.role); self.s_now["Cipher"].setText(cipher); self.s_now["Key"].setText("computed from the secret: " + secret)
        self.s_now["Protocol"].setText(protocol); self.s_now["RAM host"].setText("%s:%d" % (host, port))
        self.s_now["This program"].setText(ch.end + (", its planes under the frame" if ch.framed else ", its planes NOT under the frame (start it with --channel %s)" % ch.role)
                                           + ("\nIt IGNORED the last setting: " + ch.ignored if ch.ignored else ""))
        if ch.changes != self.sec_changes:                  # a setting was taken: the pickers show it
            self.sec_changes, self.ends_age = ch.changes, 99
            self.s_cipher.setCurrentText(cipher); self.s_protocol.setCurrentText(protocol)
        self.ends_age += 1
        if self.ends_age < 3 or self.ends_busy:             # the ends: read again every 3 s, and when a setting was taken
            return
        self.ends_age, self.ends_busy = 0, True
        role = ch.role

        def run():
            try:
                ends = channel.show(host, port, role)["ends"]
                text = "".join("%s\n    %s, %s%s\n    memory at %s%s\n" % (
                    e, r.get("driver"), "sealed with " + str(r.get("protection")) if r.get("sealed") else "not sealed", "" if r.get("framed", True) else ", not under the frame",
                    r.get("ram"), "\n    IGNORED: " + r["ignored"] if r.get("ignored") else "") for e, r in sorted(ends.items())) or "(no end reports)"
            except Exception as e:
                text = "could not read the ends: %s" % e
            self.sec_ends.emit(text)
        threading.Thread(target=run, daemon=True).start()

    # -- calls ---------------------------------------------------------------------------------------------------
    def _set_gain(self, g):
        self.gain = g
        if self.call:
            self.call.set_volume(g)

    def make_source(self):
        w, h = map(int, (self.o.size or "1280x720").split("x"))
        if self.o.video:
            return AvSource("file", self.o.video, w, h, self.o.fps)
        if self.o.camera:
            return AvSource("camera", self.o.camera, w, h, self.o.fps)
        w, h = map(int, self.o.synthetic.split("x"))
        return SyntheticSource(w, h)

    def start_call(self, session, host, port, with_):
        self.hang_up()
        self.session, self.with_ = session, with_
        self.calls_made += 1; self.other_on = False; self.got_picture = False; self._mark()
        self.last_picture = time.monotonic()
        mic = spk = None
        if self.o.audio:
            from frogcomms.audio import Mic, Speaker
            try: mic = Mic(self.o.mic)
            except Exception as e: print("[audio] mic: %s -- the call goes on without it" % e, flush=True)
            try: spk = Speaker(self.o.speaker)
            except Exception as e: print("[audio] speaker: %s -- the call goes on without it" % e, flush=True)
        src = None
        try:
            src = self.make_source()
        except Exception as e:                              # [A_DEVICE_IS_NOT_THE_CALL_V1]
            print("[video] %s -- the call goes on without sending video" % e, flush=True)
            self.status.setText("No camera: %s" % e)
        self.room.join_call(session, host, port)
        self.call = Call(host, port, self.o.name, session, src, self.o.fps, mic, spk)
        self.call.start(); self.call.set_volume(self.gain)
        self.apply_bw(self.bw.value()); self.call.set_jitter(self.jit.value())
        self.room.announce("in a call")
        self.hang_b.setEnabled(True); self.say.setEnabled(True); self.say.setPlaceholderText("Message " + with_)
        self.chat_title.setText("Chat with " + with_); self.view.who = with_; self.answer_b.hide()
        print("[app] in call %s with %s at %s:%d" % (session, with_, host, port), flush=True)

    def call_selected(self):
        it = self.lobby.currentItem()
        if not it:
            return
        name = it.data(Qt.UserRole)
        if not name or name == self.o.name:
            return
        if name.startswith("*"):
            return self.watch(name[1:])
        s = "%012x" % random.getrandbits(48)
        self.room.offer_call(s, self.media_host, self.media_port)
        self.room.invite(name, s, self.media_host, self.media_port)
        self.start_call(s, self.media_host, self.media_port, name)

    def watch(self, feed):
        on = self.room.calls_of(feed)
        for x in self.room.offers():
            if x.session in on:
                return self.start_call(x.session, x.media_host, x.media_port, "*" + feed)
        print("[app] *%s is in the lobby but offers no call yet" % feed, flush=True)

    def answer(self):
        if self.pending:
            p, self.pending = self.pending, None                 # answered once: it is not what rings next
            self.start_call(p.session, p.media_host, p.media_port, p.offered_by)

    def hang_up(self):
        if self.view.isFullScreen():
            self.toggle_fullscreen()
        if not self.call:
            return
        self.call.stop(); self.call = None
        try: self.room.leave_call(self.session)
        except Exception as e: print("[room] after the call: %s" % e, flush=True)
        self.session = ""; self.with_ = ""
        self.room.announce("online")
        self.hang_b.setEnabled(False); self.say.setEnabled(False); self.say.setPlaceholderText("Start a call to chat")
        self.chat.clear(); self.chat_title.setText("Chat")
        v = self.view; v.remote = v.self_img = None; v.overlay = v.who = v.no_video = ""; v.update()

    def toggle_fullscreen(self):
        if not self.view.isFullScreen():
            self.view.setParent(None); self.view.showFullScreen(); self.view.setFocus()
        else:
            self.view.showNormal(); self.mid.insertWidget(0, self.view, 1); self.view.show()
        self.view.set_fullscreen_icon(self.view.isFullScreen())

    def show_bw(self, v):
        b = bps_of(v)
        self.bw_val.setText("Unlimited" if b == 0 else "%.2f Mb/s" % (b / 1e6) if b >= 1000000 else "%d kb/s" % (b // 1000))

    def apply_bw(self, v):
        b = bps_of(v); self.show_bw(v)
        if self.call:
            self.call.set_throttle(b); self.room.set_media_speed(self.session, b)   # the link: both directions

    def _send_chat(self):
        if self.call and self.say.text():
            self.room.send_chat(self.session, self.say.text()); self.say.clear(); self._read_chat()

    def _read_chat(self):
        lines = self.room.read_chat(self.session)
        if len(lines) == self.chat.count():
            return
        self.chat.clear()
        for l in lines:
            it = QListWidgetItem("%s: %s" % (l.from_name, l.text))
            if l.from_name == self.o.name:
                it.setTextAlignment(Qt.AlignRight)
            self.chat.addItem(it)
        self.chat.scrollToBottom()

    # -- the clock -----------------------------------------------------------------------------------------------
    def _tick(self):
        if self.tabs.currentIndex() == 1:
            self._refresh_security()
        self._show_setting()
        changed = self.room.version != self.seen; self.seen = self.room.version
        try:
            ros = self.room.roster(); self.status.setText("Connected")
        except Exception as e:
            self.status.setText("RAM unreachable: %s" % e); return
        self.age_redraw += 1
        if changed or self.age_redraw >= 5:
            self.age_redraw = 0; self._redraw_lobby(ros)
        if not self.call:                                   # [RINGS_ONLY_WHILE_OFFERED_V1] as the C++ window: Answer shows only while it rings
            rg = self.room.ringing()
            if self.pending and not any(o.session == self.pending.session for o in rg):
                print("[app] %s is no longer calling (session %s)" % (self.pending.offered_by, self.pending.session), flush=True)
                self.pending = None; self.answer_b.hide()
            if not self.pending:
                for o in rg:
                    if o.session not in self.answered:
                        self.pending = o; self.answered.add(o.session)
                        print("[app] %s is calling (session %s)" % (o.offered_by, o.session), flush=True)
                        self.answer_b.setText("Answer " + o.offered_by); self.answer_b.show()
                        break
        if self.call and changed:
            self._read_chat()
        # [THE_CALL_ENDS_WHEN_THE_OTHER_LEAVES_V1] a call with one other person ends here when that person leaves it
        if self.call and self.with_ and not self.with_.startswith("*"):
            if self.session in self.room.calls_of(self.with_):
                self.other_on = True
            elif self.other_on:
                who = self.with_
                print("[app] %s left the call (session %s): this end hangs up" % (who, self.session), flush=True)
                self.hang_up(); self.status.setText(who + " hung up")
        self._update_tiles(); self._auto_test()

    def _redraw_lobby(self, ros):
        sel = self.lobby.currentItem().data(Qt.UserRole) if self.lobby.currentItem() else ""
        self.lobby.clear()
        people = [m for m in ros if m.name != self.o.name and not m.name.startswith("*")]
        feeds = [m for m in ros if m.name.startswith("*")]
        for title, group, feed in (("People", people, False), ("Feeds", feeds, True)):
            if not group:
                continue
            h = QListWidgetItem(title); h.setFlags(Qt.NoItemFlags); self.lobby.addItem(h)
            for m in group:
                it = QListWidgetItem("%s\n%s%s" % (m.name[1:] if feed else m.name, "Unattended feed · " if feed else "", m.status))
                it.setData(Qt.UserRole, m.name); self.lobby.addItem(it)
                if m.name == sel:
                    self.lobby.setCurrentItem(it)
        it = self.lobby.currentItem()
        if it:
            self.call_b.setText("Watch" if (it.data(Qt.UserRole) or "").startswith("*") else "Call")

    def _paint_frame(self):
        if not self.call:
            return
        src = next((s for s in self.call.sources() if s != self.o.name), None)
        if src and not self.view.paused:
            p = self.call.latest(src)
            if p and p.seq != self.last_seq:
                self.last_seq = p.seq; self.view.remote = to_image(p.frame); self.rx = (p.w, p.h, p.level)
                now = time.monotonic()
                if self.got_picture:
                    self.gap_max_ms = max(self.gap_max_ms, (now - self.prev_picture) * 1000)
                self.got_picture, self.prev_picture = True, now; self.pictures += 1
                self.last_picture = now; self.view.no_video = ""
        me = self.call.self_view()
        if me and me.seq != self.self_seq:
            self.self_seq = me.seq; self.view.self_img = to_image(me.frame)
        self.view.update()

    def _check_video(self):                                 # [NO_VIDEO_IS_A_STATE_V1]
        if not self.call or self.view.paused or time.monotonic() - self.last_picture < 2:
            return
        h = self.room.my_hold(self.session)
        why = ("Audio only -- your link is too slow for video" if h.present and h.audio_only else
               "Waiting for a keyframe" if h.present and h.unanchored else "Nothing is arriving")
        if self.view.no_video != why or self.view.remote is not None:
            self.view.remote = None; self.view.no_video = why; self.rx = (0, 0, 0); self.view.overlay = ""; self.view.update()

    def _update_tiles(self):
        self._check_video()
        now = time.monotonic(); dt = now - self.t_last; self.t_last = now
        if not self.call or dt <= 0:
            for l, t in zip(self.tiles, ("Upload", "Receiving", "Sending", "Audio")):
                l.setText(t + "\n—")
            return
        s = self.call.stats
        cur = dict(vb=s.v_bytes, vr=s.v_rx_bytes, vn=s.v_recv, ar=s.a_recv, ash=s.a_shed, vd=s.v_dropped)
        p = self.prev or cur; self.prev = cur
        up, down = (cur["vb"] - p["vb"]) * 8 / dt / 1000, (cur["vr"] - p["vr"]) * 8 / dt / 1000
        fps, abps = (cur["vn"] - p["vn"]) / dt, (cur["ar"] - p["ar"]) / dt
        shed, ashed = cur["vd"] - p["vd"], cur["ash"] - p["ash"]
        self.tiles[0].setText("Upload\n%.0f kb/s" % up)
        self.tiles[1].setText("Receiving\n%.0f kb/s · %.1f fps" % (down, fps))
        self.tiles[2].setText("Sending\nL%d · %dx%d" % (s.rung, s.send_w, s.send_h) if s.send_w else "Sending\nL%d · audio only" % s.rung)
        self.tiles[3].setText("Audio\n%.0f blocks/s%s" % (abps, " · %d shed" % ashed if ashed else ""))
        w, h, lvl = self.rx
        cap = self.call.throttle_bps
        hd = self.room.my_hold(self.session)
        served = "--" if not hd.present else "audio only" if hd.audio_only else "pass-through" if hd.passthrough else "transcoded to %dx%d" % (hd.w, hd.h)
        recv = "receiving L%d · %dx%d · %.1f fps · %.0f kb/s" % (lvl, w, h, fps, down) if w else "receiving no picture · %.0f kb/s" % down
        send = ("sending   L%d · %dx%d · %.0f kb/s (ceiling L%d)" % (s.rung, s.send_w, s.send_h, up, s.ceiling) if s.send_w
                else "sending   L%d · audio only · %.0f kb/s" % (s.rung, up))
        t = time.time()
        self.view.overlay = ("here %s.%03d\nwith %s · call #%d\n%s\n%s\naudio %.0f blocks/s · shed video %d audio %d · keyreq %d\nlink %s · jitter %d ms · server %s\n%s"
                             % (time.strftime("%H:%M:%S", time.localtime(t)), int(t % 1 * 1000), self.with_, self.calls_made, recv, send, abps, shed, ashed, s.keyreqs,
                                "unlimited" if cap == 0 else "%.2f Mb/s" % (cap / 1e6) if cap >= 1000000 else "%d kb/s" % (cap // 1000),
                                self.jit.value(), served, self._channel_lines()))
        self.view.self_note = "you · %.0f kb/s up" % up

    def _sample(self):                                      # half a second: one sample; no call = a gap
        now = time.monotonic(); dt = now - self.d_last; self.d_last = now
        sm = dict(t=now - self.t_start, live=False, rung=0, ceiling=0, stressed=0, clean=0, tx_v=0.0, tx_a=0.0, rx_v=0.0,
                  rx_a=0.0, fps_tx=0.0, fps_rx=0.0, ref_v=0.0, ref_a=0.0, budget=0.0)
        if self.call and dt > 0:
            s = self.call.stats
            c = [s.v_bytes, s.a_bytes, s.v_rx_bytes, s.a_rx_bytes, s.v_sent, s.v_recv, s.v_dropped, s.a_shed]
            if self.d_prev:
                d = [(a - b) / dt for a, b in zip(c, self.d_prev)]
                sm.update(live=True, tx_v=d[0] / 1000, tx_a=d[1] / 1000, rx_v=d[2] / 1000, rx_a=d[3] / 1000, fps_tx=d[4],
                          fps_rx=d[5], ref_v=d[6], ref_a=d[7], rung=s.rung, ceiling=s.ceiling, stressed=s.stressed,
                          clean=s.clean, budget=self.call.throttle_bps / 8000.0)
            self.d_prev = c
        else:
            self.d_prev = None
        self.hist.append(sm)
        while self.hist and self.hist[0]["t"] < sm["t"] - 125:
            self.hist.popleft()
        if not self.diag.isVisible():
            return
        if not sm["live"]:
            self.diag.wire = ["no call running", "", ""]; self.diag.alarm = False; self.diag.update(); return
        h = self.room.my_hold(self.session); s = self.call.stats
        served = "server --" if not h.present else "pass-through" if h.passthrough else "transcoded %dx%d" % (h.w, h.h)
        hold = "server --" if not h.present else "server delivered %.0f%%  %s  keys held %d" % (
            100.0 * h.delivered / h.aimed if h.aimed else 100.0,
            ("WAITING: keyframe held" if h.key_held else "WAITING for a keyframe") if h.unanchored else "anchored", h.keys_held)
        self.diag.wire = ["send   %5.1f fps  %7.1f KB/s video  %6.1f KB/s audio   %dx%d" % (sm["fps_tx"], sm["tx_v"], sm["tx_a"], s.send_w, s.send_h),
                          "recv   %5.1f fps  %7.1f KB/s video  %6.1f KB/s audio   %dx%d  %s" % (sm["fps_rx"], sm["rx_v"], sm["rx_a"], self.rx[0], self.rx[1], served),
                          "drops  %5.1f /s video  %5.1f /s audio     %s" % (sm["ref_v"], sm["ref_a"], hold)]
        self.diag.alarm = h.present and h.unanchored
        dn, upn = s.down_steps, s.up_steps
        self.diag.lag_text = "no rung changes since the call began" if not (dn or upn) else (
            "since the call began:  mean lag before stepping DOWN  %s     before stepping UP  %s" % (
                "%.2f s (%d steps)" % (s.down_lag_ms / 1000 / dn, dn) if dn else "--",
                "%.2f s (%d steps)" % (s.up_lag_ms / 1000 / upn, upn) if upn else "--"))
        self.diag.update()

    def _mark(self):
        self.mark_at, self.mark_pictures, self.gap_max_ms = time.monotonic(), self.pictures, 0.0

    def _run_script(self):
        """[THE_WHOLE_USE_AS_A_SCRIPT_V1] --script "step;step;...", as the C++ window's run_script: wait-for NAME, call NAME,
        answer, stay N, hang, wait-idle, cipher X, key, protocol X, ram HOST:PORT, bandwidth KBPS, jitter MS, shot FILE,
        quit. Each step says the pictures received since the step before and the longest time between two."""
        if self.step >= len(self.steps):
            return
        st = self.steps[self.step]; what, _, arg = st.partition(" ")
        now = time.monotonic()
        def in_room(n):
            return next((i for i in range(self.lobby.count()) if (self.lobby.item(i).data(Qt.UserRole) or "") == n), -1)
        note = ""
        if not self.step_begun:
            begun = True
            if what == "call":
                i = in_room(arg)
                if i < 0 or self.call: begun = False
                else: self.lobby.setCurrentRow(i); self.call_selected()
            elif what == "answer":
                if not self.pending or self.call: begun = False
                else: self.answer()
            elif what == "hang": self.hang_up()
            elif what in ("cipher", "key", "protocol", "ram"):
                if self.changing: begun = False
                elif what == "cipher": self.c_cipher.setCurrentText(arg); self._set_cipher(arg)
                elif what == "key": self._new_key()
                elif what == "protocol": self.c_protocol.setCurrentText(arg); self._set_protocol(arg)
                else: self.c_ram_to.setText(arg); self._move_ram(arg)
            elif what == "bandwidth": self.bw.setValue(pos_of(int(arg) * 1000) if int(arg) > 0 else K_STEPS); self.apply_bw(self.bw.value())
            elif what == "jitter": self.jit.setValue(int(arg))
            elif what == "shot": self._paint_frame(); self.view.update(); self.grab().save(arg)
            if self.step_at is None: self.step_at = now
            if begun:
                self.step_begun = True; print("[script] step %d of %d: %s" % (self.step + 1, len(self.steps), st), flush=True)
        took = now - self.step_at
        done = False
        if self.step_begun:
            if what == "wait-for": done = in_room(arg) >= 0
            elif what in ("call", "answer"):
                done = bool(self.call) and self.got_picture
                if done: note = "first picture %d ms after the step began" % (took * 1000)
            elif what == "stay": done = took >= float(arg)
            elif what == "wait-idle": done = not self.call
            elif what in ("cipher", "key", "protocol", "ram"):
                done = not self.changing
                if done: note = self.c_result.text()
            elif what == "quit":
                print("[script] finished: every step done", flush=True); self.hang_up(); self.step = len(self.steps); QApplication.exit(0); return
            else: done = True
        if done:
            print("[script] done %d: %s (%d ms)%s%s" % (self.step + 1, st, took * 1000,
                  "; pictures since the step before: %d in %d ms, longest between two %d ms" % (self.pictures - self.mark_pictures, (now - self.mark_at) * 1000, self.gap_max_ms)
                  if self.call or self.pictures != self.mark_pictures else "", "; " + note if note else ""), flush=True)
            self._mark(); self.step += 1; self.step_begun = False; self.step_at = None
        elif took > 45 and what != "stay":
            print("[script] FAILED at step %d: '%s' was not done in 45 s (%s)" % (self.step + 1, st,
                  "in a call, %s" % ("pictures arriving" if self.got_picture else "no picture yet") if self.call else "in no call"), flush=True)
            self.hang_up(); self.step = len(self.steps); QApplication.exit(3)

    def _auto_test(self):                                   # unattended testing: join a named participant, screenshot
        if self.o.auto_answer and not self.call and self.pending:
            self.answer()
        if self.steps:
            self._run_script()
        if self.o.auto_join and not self.call:
            for i in range(self.lobby.count()):
                n = self.lobby.item(i).data(Qt.UserRole) or ""
                if n in (self.o.auto_join, "*" + self.o.auto_join):
                    self.lobby.setCurrentRow(i); self.call_selected(); break
            if not self.call and self.pending:
                self.answer()
        if self.sec_steps and self.ticks >= 3 and self.s_buttons[0].isEnabled():   # unattended: the Security tab's own buttons, in order
            what, _, arg = self.sec_steps.pop(0).partition(" ")
            print("[app] pressing: %s %s" % (what, arg), flush=True)
            if what == "cipher":
                self.s_cipher.setCurrentText(arg); self.s_buttons[0].click()
            elif what == "key":
                self.s_buttons[1].click()
            elif what == "protocol":
                self.s_protocol.setCurrentText(arg); self.s_buttons[2].click()
            elif what == "ram":
                self.s_ram_to.setText(arg); self.s_buttons[3].click()
        if self.o.screenshot:
            self.ticks += 1
            if self.ticks >= self.o.after:
                self._paint_frame(); self.grab().save(self.o.screenshot)
                print("[app] screenshot %s" % self.o.screenshot, flush=True)
                if self.diag.isVisible():
                    d = os.path.splitext(self.o.screenshot); self.diag.grab().save(d[0] + "-diag" + d[1])
                    print("[app] screenshot %s-diag%s" % d, flush=True)
                self.hang_up(); QApplication.quit()

    def closeEvent(self, e):
        self.hang_up(); self.room.close(); self.diag.close(); super().closeEvent(e)


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--ram"); a.add_argument("--media"); a.add_argument("--name")
    a.add_argument("--camera"); a.add_argument("--video"); a.add_argument("--synthetic", default="1280x720")
    a.add_argument("--size"); a.add_argument("--fps", type=int, default=24); a.add_argument("--no-audio", dest="audio", action="store_false")
    a.add_argument("--mic", type=int); a.add_argument("--speaker", type=int); a.add_argument("--list-devices", action="store_true")
    a.add_argument("--bandwidth", type=int, default=0); a.add_argument("--auto-join", default="")
    a.add_argument("--screenshot", default=""); a.add_argument("--after", type=int, default=10)
    a.add_argument("--show-controls", action="store_true"); a.add_argument("--diagnostics", action="store_true")
    a.add_argument("--channel", default="")                # ROLE: this program's planes are under the frame, for that Role
    a.add_argument("--tab", default="")                    # security: open on the Security tab
    a.add_argument("--script", default="")                 # unattended: the whole use of the window, step by step (_run_script)
    a.add_argument("--auto-answer", action="store_true")   # a call that rings is answered
    a.add_argument("--security", default="")               # unattended testing: the Security tab's buttons pressed in order
    o = a.parse_args()
    if o.list_devices:
        from frogcomms.audio import devices
        print(devices()); return 0
    if not (o.ram and o.media and o.name):
        a.error("--ram, --media and --name are required")
    rh, rp = o.ram.rsplit(":", 1)
    channel.for_program(o.channel, o.name, rh, int(rp))     # [THE_CALL_IS_UNDER_THE_FRAME_V1] read before any plane is opened
    app = QApplication(sys.argv)
    w = Window(o); w.show()
    if o.diagnostics:
        w.view.on_diagnostics()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                   # [A_FAILURE_SAYS_WHAT_FAILED_V1]
        print("comms_app: %s" % e, file=sys.stderr); sys.exit(1)
