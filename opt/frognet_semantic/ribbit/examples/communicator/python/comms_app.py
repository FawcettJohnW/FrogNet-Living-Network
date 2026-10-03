#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""comms_app.py -- the FrogNet Communicator window, in Python (PySide6). The same window as the C++ comms-app.

  comms_app.py --ram HOST:PORT --media HOST:PORT --name NAME [--camera DEV | --video FILE | --synthetic WxH]
               [--size WxH] [--fps N] [--no-audio] [--mic N] [--speaker N] [--list-devices] [--bandwidth BPS]
               [--auto-join NAME] [--screenshot FILE --after SECONDS] [--show-controls] [--diagnostics]"""
import argparse, math, os, random, sys, time
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPointF  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,  # noqa: E402
                               QPushButton, QSlider, QStyle, QVBoxLayout, QWidget)
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import AvSource, SyntheticSource  # noqa: E402
from frogcomms import fnav as F  # noqa: E402

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
            tr = g.fontMetrics().boundingRect(QRect(0, 0, 400, 200), Qt.AlignLeft, self.overlay).adjusted(-8, -6, 8, 6)
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
    def __init__(self, o):
        super().__init__()
        self.o = o
        rh, rp = o.ram.rsplit(":", 1); self.media_host, mp = o.media.rsplit(":", 1); self.media_port = int(mp)
        self.room = Room(rh, int(rp), o.name, "video,audio")
        self.call = None; self.session = ""; self.pending = None; self.answered = set()
        self.gain = 1.0; self.seen = -1; self.age_redraw = 0; self.ticks = 0
        self.last_seq = self.self_seq = 0; self.rx = (0, 0, 0)
        self.prev = {}; self.t_last = time.monotonic()
        self.hist = deque(); self.t_start = time.monotonic(); self.d_prev = None; self.d_last = time.monotonic()
        self.last_picture = time.monotonic()
        self._build()
        self.tick = QTimer(self); self.tick.timeout.connect(self._tick); self.tick.start(1000)
        self.frame_t = QTimer(self); self.frame_t.timeout.connect(self._paint_frame); self.frame_t.start(40)
        self.sampler = QTimer(self); self.sampler.timeout.connect(self._sample); self.sampler.start(500)

    def _build(self):
        self.setWindowTitle("FrogNet Communicator")
        self.setStyleSheet(
            "QWidget{background:#f6f7f9;color:#1d2024;font-size:13px}QFrame#bar{background:#ffffff;border-bottom:1px solid #dfe2e6}"
            "QListWidget{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:4px}"
            "QListWidget::item{padding:6px;border-radius:6px}QListWidget::item:selected{background:#e6effc;color:#1d2024}"
            "QPushButton{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px 12px}"
            "QPushButton:hover{background:#eef1f5}QPushButton#hang{background:#fbe9e9;color:#a32020;border-color:#efc4c4}"
            "QPushButton#answer{background:#e7f5ec;color:#16703a;border-color:#bfe3cc}"
            "QLabel#tile{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:6px 8px}"
            "QLabel#title{font-weight:600;font-size:14px}QLabel#muted{color:#6b7280;font-size:12px}"
            "QLineEdit{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px}")
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
        self.mid.addLayout(tiles); body.addLayout(self.mid, 1)
        right = QVBoxLayout(); self.chat_title = QLabel("Chat"); self.chat_title.setObjectName("muted"); right.addWidget(self.chat_title)
        self.chat = QListWidget(); self.chat.setMinimumWidth(220); self.chat.setWordWrap(True); right.addWidget(self.chat, 1)
        self.say = QLineEdit(); self.say.setPlaceholderText("Start a call to chat"); self.say.setEnabled(False); right.addWidget(self.say)
        self.say.returnPressed.connect(self._send_chat)
        body.addLayout(right); top.addLayout(body, 1); self.resize(1180, 720)
        self._update_tiles()

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
            p = self.pending
            self.start_call(p.session, p.media_host, p.media_port, p.offered_by)

    def hang_up(self):
        if self.view.isFullScreen():
            self.toggle_fullscreen()
        if not self.call:
            return
        self.call.stop(); self.call = None; self.session = ""
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
        changed = self.room.version != self.seen; self.seen = self.room.version
        try:
            ros = self.room.roster(); self.status.setText("Connected")
        except Exception as e:
            self.status.setText("RAM unreachable: %s" % e); return
        self.age_redraw += 1
        if changed or self.age_redraw >= 5:
            self.age_redraw = 0; self._redraw_lobby(ros)
        if not self.call:
            for o in self.room.invitations():
                if o.session not in self.answered:
                    self.pending = o; self.answered.add(o.session)
                    self.answer_b.setText("Answer " + o.offered_by); self.answer_b.show()
        if self.call and changed:
            self._read_chat()
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
                self.last_picture = time.monotonic(); self.view.no_video = ""
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
        self.view.overlay = ("L%d · %dx%d · %.1f fps\nvideo %.0f kb/s\naudio %.0f blocks/s\nshed %d · keyreq %d"
                             % (lvl, w, h, fps, down, abps, shed, s.keyreqs)) if w else ""
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

    def _auto_test(self):                                   # unattended testing: join a named participant, screenshot
        if self.o.auto_join and not self.call:
            for i in range(self.lobby.count()):
                n = self.lobby.item(i).data(Qt.UserRole) or ""
                if n in (self.o.auto_join, "*" + self.o.auto_join):
                    self.lobby.setCurrentRow(i); self.call_selected(); break
            if not self.call and self.pending:
                self.answer()
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
    o = a.parse_args()
    if o.list_devices:
        from frogcomms.audio import devices
        print(devices()); return 0
    if not (o.ram and o.media and o.name):
        a.error("--ram, --media and --name are required")
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
