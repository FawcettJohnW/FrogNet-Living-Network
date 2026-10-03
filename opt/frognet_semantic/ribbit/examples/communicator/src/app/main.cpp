#include <cstdio>
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms-app -- the FrogNet Communicator window (COMMUNICATOR-SPEC.md, "The main window").
//
//   comms-app --ram HOST:PORT --media HOST:PORT --name NAME
//             [--video FILE | --camera DEVICE | --synthetic WxH] [--size WxH] [--fps N]
//             [--no-audio] [--mic N] [--speaker N] [--list-devices] [--bandwidth BPS]
//             [--auto-join NAME] [--screenshot FILE --after SECONDS]      (unattended testing)
//
// Left: the lobby (people and unattended feeds). Centre: the call -- remote video with live stats, self-view,
// controls, the two link sliders -- bandwidth (this participant's link, both directions) and jitter -- the stats strip. Right: chat with the call.
#include "comms/call.hpp"
#include "fnav/bearer.hpp"
#include "comms/devices.hpp"
#include "comms/room.hpp"
extern "C" {
#include <libswscale/swscale.h>
#include <libavutil/pixfmt.h>
#include <libavutil/log.h>
}
#include <QApplication>
#include <QPolygonF>
#include <deque>
#include <functional>
#include <QStyle>
#include <QMouseEvent>
#include <QKeyEvent>
#include <QComboBox>
#include <QFrame>
#include <QHBoxLayout>
#include <QImage>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QPainter>
#include <QPushButton>
#include <QSlider>
#include <QTimer>
#include <QVBoxLayout>
#include <chrono>
#include <cmath>
#include <iostream>
#include <memory>
#include <random>

using namespace comms;

static std::pair<std::string, int> hostport(const std::string& s) {
    auto c = s.rfind(':');
    if (c == std::string::npos) throw std::runtime_error("expected HOST:PORT, got " + s);
    return {s.substr(0, c), std::stoi(s.substr(c + 1))};
}
static std::string new_session() {
    std::mt19937_64 r(std::random_device{}());
    char b[17]; snprintf(b, sizeof b, "%012llx", (unsigned long long)(r() & 0xFFFFFFFFFFFFull));
    return b;
}

// A Picture (yuv420p) as a QImage, through libswscale.
// [SCALER_WRITES_ALIGNED_ROWS_V1] The destination is a buffer whose row stride is a multiple of 64 bytes, cropped to
// the true width afterwards. Scaling straight into a QImage of an unaligned width (854 * 4 = 3416 bytes per row) left
// the last pixels of each row unwritten by the vector path -- uninitialised memory that, in a call, still held the
// viewer's OWN previous frames: a strip of the wrong stream's colours down the right edge (found 2026-10-01).
static QImage to_image(const Picture& p, SwsContext*& s) {   // s: this picture's cached converter
    if (!p.w || p.u.empty()) return QImage();
    const int stride_px = (p.w + 15) & ~15;
    QImage img(stride_px, p.h, QImage::Format_RGB32);
    img.fill(Qt::black);
    // [ONE_CONVERTER_PER_PICTURE_V1] built once and kept: a converter per frame cost CPU and, on the Pi, logged a line each time
    s = sws_getCachedContext(s, p.w, p.h, AV_PIX_FMT_YUV420P, p.w, p.h, AV_PIX_FMT_BGRA, SWS_BILINEAR, nullptr, nullptr, nullptr);
    if (!s) return QImage();
    const uint8_t* src[3] = {p.y.data(), p.u.data(), p.v.data()};
    int sl[3] = {p.w, (p.w + 1) / 2, (p.w + 1) / 2};
    uint8_t* dst[1] = {img.bits()};
    int dl[1] = {int(img.bytesPerLine())};
    sws_scale(s, src, sl, 0, p.h, dst, dl);
    return img.copy(0, 0, p.w, p.h);
}

// The call view: the remote picture, a stats overlay, the self-view in the corner, and standard video controls.
// [REAL_SIZE_UNLESS_TOO_BIG_V1] A picture is drawn at its own size, centred: a 160x120 is a small picture in the middle,
// not blown up to the window. It is only ever scaled DOWN, when it is bigger than the display -- unless "Fit" is on.
// The control bar appears when the mouse moves over the picture and hides after 2.5 s: pause (the display freezes; the
// call goes on), mute, volume, the picture's real size, 1:1 / Fit, full screen. Keys: F or double-click full screen,
// Esc leaves it, Space pause, M mute.
class VideoView : public QWidget {
public:
    QImage remote, self;
    QString who, overlay, self_note;
    QString no_video;                                     // non-empty: no picture is arriving; the reason
    bool paused = false, fill = false, muted = false;
    std::function<void()> on_fullscreen;                  // the window owns where the view lives
    std::function<void(float)> on_volume;                 // effective gain: 0 when muted
    std::function<void()> on_diagnostics;

    VideoView() {
        setMinimumSize(480, 270);
        setMouseTracking(true);
        setFocusPolicy(Qt::StrongFocus);
        bar_ = new QWidget(this);
        bar_->setObjectName("vbar");
        // light, so Qt's standard (dark) media icons read on every platform
        bar_->setStyleSheet("QWidget#vbar{background:rgba(246,247,249,235);border-radius:8px}"
                            "QPushButton{background:transparent;border:none;color:#1d2024;padding:4px 8px}"
                            "QPushButton:hover{background:rgba(0,0,0,25);border-radius:6px}"
                            "QLabel{color:#3a3f45;background:transparent;font-size:12px}");
        auto* l = new QHBoxLayout(bar_); l->setContentsMargins(8, 4, 8, 4); l->setSpacing(4);
        pause_ = button(QStyle::SP_MediaPause, "Pause (Space)", [this] { set_paused(!paused); });
        mute_ = button(QStyle::SP_MediaVolume, "Mute (M)", [this] { set_muted(!muted); });
        vol_ = new QSlider(Qt::Horizontal); vol_->setRange(0, 150); vol_->setValue(100); vol_->setFixedWidth(110);
        vol_->setToolTip("Volume");
        QObject::connect(vol_, &QSlider::valueChanged, [this](int) { push_volume(); });
        size_ = new QLabel;
        fit_ = new QPushButton("1:1"); fit_->setToolTip("Real size / fit to the display");
        QObject::connect(fit_, &QPushButton::clicked, [this] { fill = !fill; fit_->setText(fill ? "Fit" : "1:1"); update(); });
        full_ = button(QStyle::SP_TitleBarMaxButton, "Full screen (F, double-click)", [this] { if (on_fullscreen) on_fullscreen(); });
        diag_ = button(QStyle::SP_FileDialogDetailedView, "Diagnostics (D)", [this] { if (on_diagnostics) on_diagnostics(); });
        for (QWidget* w : std::initializer_list<QWidget*>{pause_, mute_, vol_}) l->addWidget(w);
        l->addStretch(); l->addWidget(size_); l->addWidget(fit_); l->addWidget(diag_); l->addWidget(full_);
        hide_ = new QTimer(this); hide_->setSingleShot(true);
        QObject::connect(hide_, &QTimer::timeout, [this] { if (!paused && !pinned) bar_->hide(); });
        bar_->hide();
    }
    bool pinned = false;                                  // testing: keep the bar visible
    void show_bar() { bar_->show(); bar_->raise(); hide_->start(2500); }
    void set_paused(bool p) { paused = p; pause_->setIcon(style()->standardIcon(p ? QStyle::SP_MediaPlay : QStyle::SP_MediaPause)); show_bar(); update(); }
    void set_muted(bool m) { muted = m; mute_->setIcon(style()->standardIcon(m ? QStyle::SP_MediaVolumeMuted : QStyle::SP_MediaVolume)); push_volume(); show_bar(); }
    void set_fullscreen_icon(bool fs) { full_->setIcon(style()->standardIcon(fs ? QStyle::SP_TitleBarNormalButton : QStyle::SP_TitleBarMaxButton)); }

protected:
    void resizeEvent(QResizeEvent*) override {
        int w = std::min(width() - 20, 640);
        bar_->setGeometry((width() - w) / 2, height() - 52, w, 40);
    }
    void mouseMoveEvent(QMouseEvent*) override { show_bar(); }
    void mouseDoubleClickEvent(QMouseEvent*) override { if (on_fullscreen) on_fullscreen(); }
    void keyPressEvent(QKeyEvent* e) override {
        switch (e->key()) {
            case Qt::Key_F: if (on_fullscreen) on_fullscreen(); break;
            case Qt::Key_Escape: if (isFullScreen() && on_fullscreen) on_fullscreen(); break;
            case Qt::Key_Space: set_paused(!paused); break;
            case Qt::Key_M: set_muted(!muted); break;
            case Qt::Key_D: if (on_diagnostics) on_diagnostics(); break;
            default: QWidget::keyPressEvent(e);
        }
    }
    void paintEvent(QPaintEvent*) override {
        QPainter g(this);
        g.setRenderHint(QPainter::SmoothPixmapTransform);
        g.fillRect(rect(), QColor(24, 26, 30));
        if (!remote.isNull()) {
            QSize s = remote.size();
            if (fill || s.width() > width() || s.height() > height()) s = s.scaled(size(), Qt::KeepAspectRatio);
            QRect r((width() - s.width()) / 2, (height() - s.height()) / 2, s.width(), s.height());
            g.drawImage(r, remote);
            size_->setText(QString("%1x%2").arg(remote.width()).arg(remote.height()));
            if (paused) {
                g.fillRect(QRect(r.left(), r.top(), r.width(), 22), QColor(0, 0, 0, 140));
                g.setPen(Qt::white); g.drawText(QRect(r.left(), r.top(), r.width(), 22), Qt::AlignCenter, "Paused");
            }
        } else if (!no_video.isEmpty()) {                   // [NO_VIDEO_IS_A_STATE_V1] never a frozen last frame
            QFont f = font(); f.setPointSize(20); f.setBold(true); g.setFont(f);
            g.setPen(QColor(225, 230, 235));
            g.drawText(rect().adjusted(0, -24, 0, -24), Qt::AlignCenter, "No video");
            QFont r = font(); r.setPointSize(11); g.setFont(r);
            g.setPen(QColor(150, 155, 165));
            g.drawText(rect().adjusted(0, 24, 0, 24), Qt::AlignCenter, no_video);
        } else {
            g.setPen(QColor(150, 155, 165));
            g.drawText(rect(), Qt::AlignCenter, who.isEmpty() ? "Not in a call" : "Waiting for " + who + "'s picture");
        }
        if (!overlay.isEmpty()) {                            // live per-stream stats
            QFont f("Monospace"); f.setStyleHint(QFont::Monospace); f.setPointSize(9); g.setFont(f);
            QRect tr = g.fontMetrics().boundingRect(QRect(0, 0, 400, 200), Qt::AlignLeft, overlay).adjusted(-8, -6, 8, 6);
            tr.moveTo(10, 10);
            g.setPen(Qt::NoPen); g.setBrush(QColor(0, 0, 0, 150)); g.drawRoundedRect(tr, 6, 6);
            g.setPen(QColor(225, 230, 235)); g.drawText(tr.adjusted(8, 6, -8, -6), Qt::AlignLeft, overlay);
        }
        if (!self.isNull()) {                                // self-view
            int w = std::max(120, width() / 5), h = w * self.height() / std::max(1, self.width());
            QRect r(width() - w - 10, 10, w, h);             // top right: the control bar owns the bottom
            g.drawImage(r, self);
            g.setPen(QColor(255, 255, 255, 180)); g.setBrush(Qt::NoBrush); g.drawRect(r);
            if (!self_note.isEmpty()) {
                QFont f = g.font(); f.setPointSize(8); g.setFont(f);
                g.fillRect(QRect(r.left(), r.bottom() - 16, r.width(), 16), QColor(0, 0, 0, 140));
                g.drawText(QRect(r.left(), r.bottom() - 16, r.width(), 16), Qt::AlignCenter, self_note);
            }
        }
    }
private:
    QPushButton* button(QStyle::StandardPixmap icon, const char* tip, std::function<void()> fn) {
        auto* b = new QPushButton; b->setIcon(style()->standardIcon(icon)); b->setToolTip(tip);
        QObject::connect(b, &QPushButton::clicked, fn);
        return b;
    }
    void push_volume() { if (on_volume) on_volume(muted ? 0.f : float(vol_->value()) / 100.f); }
    QWidget* bar_;
    QPushButton *pause_, *mute_, *fit_, *full_, *diag_;
    QSlider* vol_;
    QLabel* size_;
    QTimer* hide_;
};

// ---------------------------------------------------------------------------------------------------------------
// The diagnostics window -- after the Python Communicator's comms_diag.py: what the bearer is doing and why it is
// taking its time about it. Header: the bearer's state and the span. WIRE: the figures you read while something is
// wrong, large. Three panels on one time axis: RUNG (a step line under its ceiling), THROUGHPUT (KB/s on the wire,
// video and audio, against the bandwidth budget), REFUSED (frames the wire refused, video and audio -- the only
// congestion signal send-or-drop has). Footer: the law, and the measured lag before each step.
// [DIAG_IS_UNGATED_V1] A measured zero is drawn as a zero; a moment with no call is a gap. Never collapsed.
struct Sample {
    double t = 0;                               // seconds since the window opened
    bool live = false;                          // false = no call: a gap
    int rung = 0, ceiling = 0, stressed = 0, clean = 0;
    double tx_v = 0, tx_a = 0, rx_v = 0, rx_a = 0;   // KB/s
    double fps_tx = 0, fps_rx = 0, ref_v = 0, ref_a = 0, budget = 0;   // budget KB/s, 0 = none
};

class DiagWindow : public QWidget {
public:
    std::deque<Sample>* hist = nullptr;
    QString wire_send, wire_recv, wire_loss;
    bool alarm = false;                         // the media server is holding this viewer for a keyframe
    QString lag_text;                           // measured in the call, where the bearer runs
    double span = 60;
    DiagWindow() {
        setWindowTitle("FrogNet Communicator -- diagnostics");
        resize(880, 760);
        setStyleSheet("QWidget{background:#ffffff;color:#1d2024}"
                      "QPushButton{background:#eef1f5;border:1px solid #c9ced5;border-radius:6px;padding:3px 10px}"
                      "QPushButton:checked{background:#dbe7fb;border-color:#8fb0e8}");
        auto* top = new QHBoxLayout; top->setContentsMargins(12, 8, 12, 0);
        head_ = new QLabel; head_->setStyleSheet("font-weight:600");
        top->addWidget(head_); top->addStretch();
        for (auto sp : {std::make_pair(30.0, "30s"), std::make_pair(60.0, "60s"), std::make_pair(120.0, "2m")}) {
            auto* b = new QPushButton(sp.second); b->setCheckable(true); b->setChecked(sp.first == span);
            QObject::connect(b, &QPushButton::clicked, [this, b, sp] { span = sp.first; for (auto* o : spans_) o->setChecked(o == b); update(); });
            spans_.push_back(b); top->addWidget(b);
        }
        auto* v = new QVBoxLayout(this); v->setContentsMargins(0, 0, 0, 0);
        v->addLayout(top); v->addStretch();
    }
protected:
    void paintEvent(QPaintEvent*) override {
        QPainter g(this);
        g.setRenderHint(QPainter::Antialiasing);
        const QColor MUTE(110, 117, 125), GRID(232, 235, 239), VID(217, 100, 90), AUD(220, 150, 30), RX(80, 120, 200), ROOF(220, 150, 30);
        const Sample* last = (hist && !hist->empty()) ? &hist->back() : nullptr;
        head_->setText(last && last->live
            ? QString("BEARER    rung L%1    ceiling L%2    stressed %3/%4    clean %5/%6")
                  .arg(last->rung).arg(last->ceiling).arg(last->stressed).arg(fnav::Bearer::BAD_READS).arg(last->clean).arg(fnav::Bearer::CLEAN_SECS)
            : QString("BEARER    no call running"));
        // WIRE: large
        QFont big("Monospace"); big.setStyleHint(QFont::Monospace); big.setPointSize(11);
        g.setFont(big);
        QRect wire(12, 44, width() - 24, 78);
        g.fillRect(wire, QColor(246, 247, 249));
        g.setPen(QColor(40, 44, 50));
        g.drawText(wire.adjusted(10, 6, -10, 0), Qt::AlignLeft | Qt::AlignTop, wire_send);
        g.drawText(wire.adjusted(10, 30, -10, 0), Qt::AlignLeft | Qt::AlignTop, wire_recv);
        g.setPen(alarm ? VID : MUTE);
        g.drawText(wire.adjusted(10, 54, -10, 0), Qt::AlignLeft | Qt::AlignTop, wire_loss);
        if (!hist || hist->empty()) return;
        const double now = hist->back().t, t0 = now - span;
        const int x0 = 60, x1 = width() - 20, PH = 150, GAP = 38;
        auto X = [&](double t) { return x0 + (t - t0) / span * (x1 - x0); };
        QFont small = font(); small.setPointSize(8);
        auto panel = [&](int y, const QString& title, const QString& unit) {
            g.setFont(small); g.setPen(MUTE);
            g.drawText(QPoint(x0, y - 6), title); g.drawText(QRect(x1 - 300, y - 18, 300, 14), Qt::AlignRight, unit);
            g.setPen(GRID); g.setBrush(Qt::NoBrush); g.drawRect(x0, y, x1 - x0, PH);
            for (int k = 1; k < 4; ++k) g.drawLine(x0, y + PH * k / 4, x1, y + PH * k / 4);
        };
        // a series as polylines broken at gaps ([DIAG_IS_UNGATED_V1])
        auto series = [&](int y, double vmax, QColor col, double Sample::*field, bool step, Qt::PenStyle style = Qt::SolidLine) {
            QPen pen(col, 2); pen.setStyle(style); g.setPen(pen);
            QPolygonF run;
            auto flush = [&] { if (run.size() > 1) g.drawPolyline(run); else if (run.size() == 1) g.drawEllipse(run[0], 1.5, 1.5); run.clear(); };
            for (auto& sm : *hist) {
                if (sm.t < t0) continue;
                if (!sm.live) { flush(); continue; }
                double v = sm.*field;
                QPointF p(X(sm.t), y + PH - std::min(1.0, v / vmax) * PH);
                if (step && !run.isEmpty()) run << QPointF(p.x(), run.back().y());
                run << p;
            }
            flush();
        };
        double ymax_tp = 1, ymax_ref = 1;
        for (auto& sm : *hist) if (sm.live && sm.t >= t0) {
            ymax_tp = std::max({ymax_tp, sm.tx_v + sm.tx_a, sm.rx_v + sm.rx_a, sm.budget});
            ymax_ref = std::max({ymax_ref, sm.ref_v, sm.ref_a});
        }
        ymax_tp *= 1.15; ymax_ref = std::max(5.0, ymax_ref * 1.2);
        int y = 150;
        // RUNG, with the ceiling as its roof and a mark at every step
        panel(y, "RUNG", "L0 .. L8");
        g.setFont(small); g.setPen(MUTE);
        for (int r = 0; r <= 8; r += 2) g.drawText(QRect(x0 - 40, int(y + PH - r / 8.0 * PH) - 7, 34, 14), Qt::AlignRight, QString("L%1").arg(r));
        auto rung_series = [&](bool roof) {
            QPen pen(roof ? ROOF : RX, roof ? 1 : 2); if (roof) pen.setStyle(Qt::DashLine); g.setPen(pen);
            QPolygonF run; int prev = -1;
            auto flush = [&] { if (run.size() > 1) g.drawPolyline(run); run.clear(); };
            for (auto& sm : *hist) {
                if (sm.t < t0) continue;
                if (!sm.live) { flush(); prev = -1; continue; }
                int r = roof ? sm.ceiling : sm.rung;
                QPointF p(X(sm.t), y + PH - r / 8.0 * PH);
                if (!run.isEmpty()) run << QPointF(p.x(), run.back().y());
                run << p;
                if (!roof && prev >= 0 && r != prev) {                 // a step: marked
                    g.save(); g.setPen(QPen(r < prev ? VID : QColor(60, 160, 100), 1, Qt::DotLine));
                    g.drawLine(QPointF(p.x(), y), QPointF(p.x(), y + PH)); g.restore();
                }
                prev = r;
            }
            flush();
        };
        rung_series(true); rung_series(false);
        // THROUGHPUT
        y += PH + GAP;
        panel(y, "THROUGHPUT", "KB/s");
        g.setFont(small); g.setPen(MUTE); g.drawText(QRect(x0 - 50, y - 7, 44, 14), Qt::AlignRight, QString::number(ymax_tp, 'f', 0));
        series(y, ymax_tp, ROOF, &Sample::budget, true, Qt::DashLine);
        series(y, ymax_tp, VID, &Sample::tx_v, false);
        series(y, ymax_tp, AUD, &Sample::tx_a, false);
        series(y, ymax_tp, RX, &Sample::rx_v, false, Qt::DashLine);
        g.setFont(small);
        g.setPen(VID); g.drawText(x1 - 330, y + PH + 13, "video sent");
        g.setPen(AUD); g.drawText(x1 - 260, y + PH + 13, "audio sent");
        g.setPen(RX);  g.drawText(x1 - 190, y + PH + 13, "video received");
        g.setPen(ROOF); g.drawText(x1 - 95, y + PH + 13, "budget");
        // REFUSED
        y += PH + GAP;
        panel(y, "REFUSED", "frames/s the wire refused");
        g.setFont(small); g.setPen(MUTE); g.drawText(QRect(x0 - 50, y - 7, 44, 14), Qt::AlignRight, QString::number(ymax_ref, 'f', 0));
        series(y, ymax_ref, VID, &Sample::ref_v, false);
        series(y, ymax_ref, AUD, &Sample::ref_a, false);
        g.setPen(VID); g.drawText(x1 - 95, y + PH + 13, "video");
        g.setPen(AUD); g.drawText(x1 - 50, y + PH + 13, "audio");
        // footer: the law, and the lag measured in this window
        y += PH + 30;
        g.setFont(small); g.setPen(MUTE);
        g.drawText(QRect(x0, y, x1 - x0, 30), Qt::AlignLeft | Qt::TextWordWrap,
                   QString("step DOWN after %1 stressed samples or %2 drops in a second;  UP after %3 clean seconds at full frame rate;  "
                           "a failed rung is held off %4 s, doubling to %5 s")
                       .arg(fnav::Bearer::BAD_READS + 1).arg(fnav::Bearer::SEC_DROPS + 1).arg(fnav::Bearer::CLEAN_SECS)
                       .arg(int(fnav::Bearer::HOLD_BASE_S)).arg(int(fnav::Bearer::HOLD_MAX_S)));
        QString lag = lag_text;
        g.setPen(QColor(40, 44, 50)); g.setFont(font());
        g.drawText(QRect(x0, y + 34, x1 - x0, 18), Qt::AlignLeft, lag);
    }
private:
    QLabel* head_;
    std::vector<QPushButton*> spans_;
};

struct Options {
    std::string ram, media, name, video, camera, synthetic = "1280x720", size, auto_join, screenshot;
    int fps = 24, mic = -1, speaker = -1, after = 10;
    long bandwidth = 0;                                  // the link, both directions; 0 = unlimited
    bool show_controls = false;                          // testing: keep the video control bar visible
    bool diagnostics = false;                            // open the diagnostics window at start
    bool audio = true;
};

class Window : public QWidget {
public:
    Window(const Options& o) : opt_(o) {
        auto [rh, rp] = hostport(o.ram);
        auto [mh, mp] = hostport(o.media);
        media_host_ = mh; media_port_ = mp;
        room_ = std::make_unique<Room>(rh, rp, o.name, "video,audio");
        room_->announce("online");
        build();
        tick_ = new QTimer(this); connect(tick_, &QTimer::timeout, [this] { tick(); }); tick_->start(1000);
        frame_ = new QTimer(this); connect(frame_, &QTimer::timeout, [this] { paint_frame(); }); frame_->start(40);
    }
    ~Window() override { hang_up(); delete diag_; }

    void build() {
        setWindowTitle("FrogNet Communicator");
        setStyleSheet(
            "QWidget{background:#f6f7f9;color:#1d2024;font-size:13px}"
            "QFrame#bar{background:#ffffff;border-bottom:1px solid #dfe2e6}"
            "QListWidget{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:4px}"
            "QListWidget::item{padding:6px;border-radius:6px}"
            "QListWidget::item:selected{background:#e6effc;color:#1d2024}"
            "QPushButton{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px 12px}"
            "QPushButton:hover{background:#eef1f5}"
            "QPushButton#hang{background:#fbe9e9;color:#a32020;border-color:#efc4c4}"
            "QPushButton#answer{background:#e7f5ec;color:#16703a;border-color:#bfe3cc}"
            "QLabel#tile{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:6px 8px}"
            "QLabel#title{font-weight:600;font-size:14px}"
            "QLabel#muted{color:#6b7280;font-size:12px}"
            "QLineEdit{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px}");
        auto* top = new QVBoxLayout(this); top->setContentsMargins(0, 0, 0, 0); top->setSpacing(0);
        auto* bar = new QFrame; bar->setObjectName("bar");
        auto* bl = new QHBoxLayout(bar); bl->setContentsMargins(14, 8, 14, 8);
        auto* t = new QLabel("FrogNet Communicator"); t->setObjectName("title");
        status_ = new QLabel; status_->setObjectName("muted");
        bl->addWidget(t); bl->addSpacing(10); bl->addWidget(status_); bl->addStretch();
        me_ = new QLabel(QString::fromStdString(opt_.name)); me_->setObjectName("muted"); bl->addWidget(me_);
        top->addWidget(bar);

        auto* body = new QHBoxLayout; body->setContentsMargins(10, 10, 10, 10); body->setSpacing(10);
        // lobby
        auto* left = new QVBoxLayout;
        auto* lt = new QLabel("In the room"); lt->setObjectName("muted"); left->addWidget(lt);
        lobby_ = new QListWidget; lobby_->setMinimumWidth(200); left->addWidget(lobby_, 1);
        call_btn_ = new QPushButton("Call"); left->addWidget(call_btn_);
        connect(call_btn_, &QPushButton::clicked, [this] { call_selected(); });
        connect(lobby_, &QListWidget::itemDoubleClicked, [this] { call_selected(); });
        answer_ = new QPushButton; answer_->setObjectName("answer"); answer_->hide(); left->addWidget(answer_);
        connect(answer_, &QPushButton::clicked, [this] { answer(); });
        body->addLayout(left);

        // call
        auto* mid = new QVBoxLayout; mid->setSpacing(8);
        mid_ = mid;
        view_ = new VideoView; mid->addWidget(view_, 1);
        view_->on_fullscreen = [this] { toggle_fullscreen(); };
        view_->on_volume = [this](float g) { gain_ = g; if (call_) call_->set_volume(g); };
        view_->on_diagnostics = [this] { diag_->hist = &hist_; diag_->show(); diag_->raise(); };
        diag_ = new DiagWindow;
        t_start_ = std::chrono::steady_clock::now();
        sampler_ = new QTimer(this); QObject::connect(sampler_, &QTimer::timeout, [this] { sample(); }); sampler_->start(500);
        if (opt_.diagnostics) view_->on_diagnostics();
        if (opt_.show_controls) { view_->pinned = true; view_->show_bar(); }
        auto* ctl = new QHBoxLayout;
        hang_ = new QPushButton("Hang up"); hang_->setObjectName("hang"); hang_->setEnabled(false);
        connect(hang_, &QPushButton::clicked, [this] { hang_up(); });
        ctl->addStretch(); ctl->addWidget(hang_); ctl->addStretch();
        mid->addLayout(ctl);
        auto slider = [&](const char* name, int max, QLabel*& val, QSlider*& s) {
            auto* row = new QHBoxLayout;
            auto* l = new QLabel(name); l->setFixedWidth(90); l->setObjectName("muted");
            s = new QSlider(Qt::Horizontal); s->setRange(0, max);
            val = new QLabel; val->setFixedWidth(90); val->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
            row->addWidget(l); row->addWidget(s, 1); row->addWidget(val);
            mid->addLayout(row);
        };
        // DEMO link conditions (fnav set_throttle / set_jitter): the ladder discovers what the smaller wire carries.
        // [THE_SLIDER_IS_SMOOTH_V1] continuous and logarithmic, 32 kb/s .. 4 Mb/s, the far right unlimited. The label
        // follows the hand; the link is set once the slider rests for 200 ms (no stream of MediaSpeed writes).
        slider("Bandwidth", kSteps, bw_val_, bw_);
        bw_->setValue(opt_.bandwidth > 0 ? pos_of(opt_.bandwidth) : kSteps);
        bw_rest_ = new QTimer(this); bw_rest_->setSingleShot(true);
        connect(bw_rest_, &QTimer::timeout, [this] { apply_bw(bw_->value()); });
        connect(bw_, &QSlider::valueChanged, [this](int v) { show_bw(v); bw_rest_->start(200); });
        slider("Jitter", 500, jit_val_, jit_);
        // the link's jitter, BOTH directions ([JITTER_IS_THE_LINK_V1]): what you send and what you receive
        connect(jit_, &QSlider::valueChanged, [this](int v) { jit_val_->setText(QString("%1 ms").arg(v)); if (call_) call_->set_jitter(v); });
        apply_bw(bw_->value()); jit_val_->setText("0 ms");
        auto* tiles = new QHBoxLayout;
        for (QLabel** l : {&t_up_, &t_down_, &t_send_, &t_audio_}) { *l = new QLabel; (*l)->setObjectName("tile"); tiles->addWidget(*l, 1); }
        mid->addLayout(tiles);
        body->addLayout(mid, 1);

        // chat
        auto* right = new QVBoxLayout;
        chat_title_ = new QLabel("Chat"); chat_title_->setObjectName("muted"); right->addWidget(chat_title_);
        chat_ = new QListWidget; chat_->setMinimumWidth(220); chat_->setWordWrap(true); right->addWidget(chat_, 1);
        say_ = new QLineEdit; say_->setPlaceholderText("Start a call to chat"); say_->setEnabled(false); right->addWidget(say_);
        connect(say_, &QLineEdit::returnPressed, [this] {
            if (!call_ || say_->text().isEmpty()) return;
            room_->send_chat(session_, say_->text().toStdString()); say_->clear(); read_chat();
        });
        body->addLayout(right);
        top->addLayout(body, 1);
        resize(1180, 720);
        update_tiles();
    }

    std::unique_ptr<Source> make_source() {
        int w = 1280, h = 720;
        if (!opt_.size.empty()) sscanf(opt_.size.c_str(), "%dx%d", &w, &h);
        if (!opt_.video.empty()) return std::make_unique<AvInputSource>("file", opt_.video, w, h, opt_.fps);
        if (!opt_.camera.empty()) return std::make_unique<AvInputSource>("camera", opt_.camera, w, h, opt_.fps);
        sscanf(opt_.synthetic.c_str(), "%dx%d", &w, &h);
        return std::make_unique<SyntheticSource>(w, h);
    }

    void start_call(const std::string& session, const std::string& host, int port, const std::string& with) {
        hang_up();
        session_ = session; with_ = with;
        last_picture_ = std::chrono::steady_clock::now();
        std::unique_ptr<AudioSource> mic; std::unique_ptr<AudioSink> spk;
        if (opt_.audio) {
            try { mic = std::make_unique<PaMic>(opt_.mic); } catch (const std::exception& e) { std::cerr << "[audio] mic: " << e.what() << " -- the call goes on without it\n"; }
            try { spk = std::make_unique<PaSpeaker>(opt_.speaker); } catch (const std::exception& e) { std::cerr << "[audio] speaker: " << e.what() << " -- the call goes on without it\n"; }
        }
        room_->join_call(session, host, port);
        // [A_DEVICE_IS_NOT_THE_CALL_V1] a camera or file that will not open is reported and the call goes on receiving
        // only -- as a microphone that will not open already did. Found 2026-10-01 under WSL: the exception escaped a
        // Qt event handler and aborted the window.
        std::unique_ptr<Source> src;
        try { src = make_source(); }
        catch (const std::exception& e) {
            std::cerr << "[video] " << e.what() << " -- the call goes on without sending video\n";
            status_->setText(QString("No camera: ") + e.what());
        }
        call_ = std::make_unique<Call>(host, port, opt_.name, session, std::move(src), opt_.fps, std::move(mic), std::move(spk));
        call_->start();
        call_->set_volume(gain_);
        apply_bw(bw_->value());
        call_->set_jitter(jit_->value());
        room_->announce("in a call");
        hang_->setEnabled(true); say_->setEnabled(true); say_->setPlaceholderText("Message " + QString::fromStdString(with));
        chat_title_->setText("Chat with " + QString::fromStdString(with));
        view_->who = QString::fromStdString(with);
        answer_->hide();
        std::cerr << "[app] in call " << session << " with " << with << " at " << host << ":" << port << "\n";
    }

    void call_selected() {
        auto* it = lobby_->currentItem();
        if (!it) return;
        std::string name = it->data(Qt::UserRole).toString().toStdString();
        if (name.empty() || name == opt_.name) return;
        if (name[0] == '*') { watch(name.substr(1)); return; }
        std::string s = new_session();
        room_->offer_call(s, media_host_, media_port_);
        room_->invite(name, s, media_host_, media_port_);
        start_call(s, media_host_, media_port_, name);
    }

    bool watch(const std::string& feed) {                    // join the call an unattended feed is on
        std::vector<std::string> on = room_->calls_of(feed);
        for (auto& x : room_->offers())
            for (auto& s : on)
                if (x.session == s) { start_call(x.session, x.media_host, x.media_port, "*" + feed); return true; }
        std::cerr << "[app] *" << feed << " is in the lobby but offers no call yet\n";
        return false;
    }

    void answer() { if (pending_.session.size()) start_call(pending_.session, pending_.media_host, pending_.media_port, pending_.offered_by); }

    // half a second: one Sample, rates from counter differences; no call = a gap ([DIAG_IS_UNGATED_V1])
    void sample() {
        auto now = std::chrono::steady_clock::now();
        Sample sm; sm.t = std::chrono::duration<double>(now - t_start_).count();
        double dt = std::chrono::duration<double>(now - d_last_).count(); d_last_ = now;
        if (call_ && dt > 0) {
            CallStats& s = call_->stats;
            uint64_t c[8] = {s.v_bytes, s.a_bytes, s.v_rx_bytes, s.a_rx_bytes, s.v_sent, s.v_recv, s.v_dropped, s.a_shed};
            if (d_have_) {
                sm.live = true;
                sm.tx_v = (c[0] - d_[0]) / dt / 1000; sm.tx_a = (c[1] - d_[1]) / dt / 1000;
                sm.rx_v = (c[2] - d_[2]) / dt / 1000; sm.rx_a = (c[3] - d_[3]) / dt / 1000;
                sm.fps_tx = (c[4] - d_[4]) / dt; sm.fps_rx = (c[5] - d_[5]) / dt;
                sm.ref_v = (c[6] - d_[6]) / dt; sm.ref_a = (c[7] - d_[7]) / dt;
                sm.rung = s.rung; sm.ceiling = s.ceiling; sm.stressed = s.stressed; sm.clean = s.clean;
                sm.budget = call_->throttle() / 8.0 / 1000.0;
            }
            std::copy(c, c + 8, d_); d_have_ = true;
        } else d_have_ = false;
        hist_.push_back(sm);
        while (!hist_.empty() && hist_.front().t < sm.t - 125) hist_.pop_front();
        if (!diag_->isVisible()) return;
        if (!sm.live) { diag_->wire_send = "no call running"; diag_->wire_recv.clear(); diag_->wire_loss.clear(); diag_->alarm = false; diag_->update(); return; }
        auto num = [](double v, int w, int p) { return QString("%1").arg(v, w, 'f', p); };
        Room::Hold h = room_->my_hold(session_);
        QString served = !h.present ? QString("server --") : h.passthrough ? QString("pass-through") : QString("transcoded %1x%2").arg(h.w).arg(h.h);
        diag_->wire_send = QString("send   %1 fps  %2 KB/s video  %3 KB/s audio   %4x%5")
                               .arg(num(sm.fps_tx, 5, 1)).arg(num(sm.tx_v, 7, 1)).arg(num(sm.tx_a, 6, 1)).arg(int(call_->stats.send_w)).arg(int(call_->stats.send_h));
        diag_->wire_recv = QString("recv   %1 fps  %2 KB/s video  %3 KB/s audio   %4x%5  %6")
                               .arg(num(sm.fps_rx, 5, 1)).arg(num(sm.rx_v, 7, 1)).arg(num(sm.rx_a, 6, 1)).arg(rx_w_).arg(rx_h_).arg(served);
        QString hold = !h.present ? QString("server --")
            : QString("server delivered %1%  %2  keys held %3")
                  .arg(h.aimed ? 100.0 * double(h.delivered) / double(h.aimed) : 100.0, 0, 'f', 0)
                  .arg(h.unanchored ? (h.key_held ? "WAITING: keyframe held" : "WAITING for a keyframe") : "anchored")
                  .arg(h.keys_held);
        diag_->wire_loss = QString("drops  %1 /s video  %2 /s audio     %3").arg(num(sm.ref_v, 5, 1)).arg(num(sm.ref_a, 5, 1)).arg(hold);
        diag_->alarm = h.present && h.unanchored;
        {   // the hysteresis lag, measured in the call where the bearer runs (a half-second sampler cannot see it)
            CallStats& s = call_->stats;
            uint64_t dn = s.down_steps, up = s.up_steps;
            diag_->lag_text = (dn == 0 && up == 0) ? QString("no rung changes since the call began")
                : QString("since the call began:  mean lag before stepping DOWN  %1     before stepping UP  %2")
                      .arg(dn ? QString("%1 s (%2 steps)").arg(double(s.down_lag_ms) / 1000.0 / double(dn), 0, 'f', 2).arg(dn) : QString("--"))
                      .arg(up ? QString("%1 s (%2 steps)").arg(double(s.up_lag_ms) / 1000.0 / double(up), 0, 'f', 2).arg(up) : QString("--"));
        }
        diag_->update();
    }

    void toggle_fullscreen() {
        if (!view_->isFullScreen()) {                      // lift the view out into its own full-screen window
            view_->setParent(nullptr);
            view_->showFullScreen();
            view_->setFocus();
        } else {                                           // and put it back where it was
            view_->showNormal();
            mid_->insertWidget(0, view_, 1);
            view_->show();
        }
        view_->set_fullscreen_icon(view_->isFullScreen());
    }

    void hang_up() {
        if (view_ && view_->isFullScreen()) toggle_fullscreen();
        if (!call_) return;
        call_->stop(); call_.reset();
        session_.clear(); with_.clear();
        room_->announce("online");
        hang_->setEnabled(false); say_->setEnabled(false); say_->setPlaceholderText("Start a call to chat");
        chat_->clear(); chat_title_->setText("Chat");
        view_->remote = QImage(); view_->self = QImage(); view_->overlay.clear(); view_->who.clear(); view_->no_video.clear(); view_->update();
    }

    static long bps_of(int v) {                           // slider position -> bits/s; 0 = unlimited
        if (v >= kSteps) return 0;
        return long(kMin * std::pow(kMax / kMin, double(v) / double(kSteps - 1)));
    }
    static int pos_of(long bps) {
        double x = std::log(double(bps) / kMin) / std::log(kMax / kMin);
        return std::max(0, std::min(kSteps - 1, int(std::lround(x * double(kSteps - 1)))));
    }
    void show_bw(int v) {
        long bps = bps_of(v);
        bw_val_->setText(bps == 0 ? "Unlimited" : bps >= 1000000 ? QString("%1 Mb/s").arg(bps / 1e6, 0, 'f', 2) : QString("%1 kb/s").arg(bps / 1000));
    }
    void apply_bw(int v) {
        long bps = bps_of(v);
        show_bw(v);
        // [THE_LINK_IS_SYMMETRIC_V1] One number for this participant's link, applied to BOTH directions, as fnav's
        // MediaSpeed was ([MEDIASPEED_V1]): the uplink is paced to it (the sender's ladder discovers the smaller wire)
        // and it is written as this viewer's MediaSpeed (the media server serves this viewer at it). 0 = unlimited.
        if (call_) { call_->set_throttle(bps); room_->set_media_speed(session_, bps); }
    }

    void tick() {
        // lobby
        bool changed = room_->version() != seen_;               // [HOLD_THE_READ_V1] redraw only on a change
        seen_ = room_->version();
        std::vector<Member> ros;
        try { ros = room_->roster(); status_->setText("Connected"); }
        catch (const std::exception& e) { status_->setText(QString("RAM unreachable: ") + e.what()); return; }
        if (changed || ++age_redraw_ >= 5) { age_redraw_ = 0; redraw_lobby(ros); }
        // incoming calls
        if (!call_) {
            for (auto& o : room_->invitations())
                if (!answered_.count(o.session)) {
                    pending_ = o; answered_.insert(o.session);
                    answer_->setText("Answer " + QString::fromStdString(o.offered_by)); answer_->show();
                }
        }
        if (call_ && changed) read_chat();
        update_tiles();
        auto_test();
    }

    void redraw_lobby(const std::vector<Member>& ros) {
        QString sel = lobby_->currentItem() ? lobby_->currentItem()->data(Qt::UserRole).toString() : "";
        lobby_->clear();
        std::vector<Member> people, feeds;
        for (auto& m : ros) { if (m.name == opt_.name) continue; (m.name.size() && m.name[0] == '*' ? feeds : people).push_back(m); }
        auto add = [&](const Member& m, bool feed) {
            QString label = QString::fromStdString(feed ? m.name.substr(1) : m.name) + "\n" +
                            (feed ? QString("Unattended feed · ") : QString()) + QString::fromStdString(m.status);
            auto* it = new QListWidgetItem(label); it->setData(Qt::UserRole, QString::fromStdString(m.name));
            lobby_->addItem(it);
            if (it->data(Qt::UserRole).toString() == sel) lobby_->setCurrentItem(it);
        };
        if (!people.empty()) { auto* h = new QListWidgetItem("People"); h->setFlags(Qt::NoItemFlags); lobby_->addItem(h); }
        for (auto& m : people) add(m, false);
        if (!feeds.empty()) { auto* h = new QListWidgetItem("Feeds"); h->setFlags(Qt::NoItemFlags); lobby_->addItem(h); }
        for (auto& m : feeds) add(m, true);
        if (auto* it = lobby_->currentItem()) call_btn_->setText(it->data(Qt::UserRole).toString().startsWith("*") ? "Watch" : "Call");
    }

    void read_chat() {
        auto lines = room_->read_chat(session_);
        if (int(lines.size()) == chat_->count()) return;
        chat_->clear();
        for (auto& l : lines) {
            auto* it = new QListWidgetItem(QString::fromStdString(l.from_name + ": " + l.text));
            if (l.from_name == opt_.name) it->setTextAlignment(Qt::AlignRight);
            chat_->addItem(it);
        }
        chat_->scrollToBottom();
    }

    void paint_frame() {
        if (!call_) return;
        Picture p;
        auto srcs = call_->sources();
        std::string src;
        for (auto& s : srcs) if (s != opt_.name) { src = s; break; }
        if (!src.empty() && !view_->paused && call_->latest(src, p) && p.seq != last_seq_) {
            last_seq_ = p.seq; view_->remote = to_image(p, sws_remote_); rx_w_ = p.w; rx_h_ = p.h; rx_level_ = int(p.level);
            last_picture_ = std::chrono::steady_clock::now(); view_->no_video.clear();
        }
        Picture me;
        if (call_->self_view(me) && me.seq != self_seq_) { self_seq_ = me.seq; view_->self = to_image(me, sws_self_); }
        view_->update();
    }

    // [NO_VIDEO_IS_A_STATE_V1] 2 s without a picture in a call: drop the last frame and say why, from the media
    // server's own account of this viewer (MediaHold) where it gives one.
    void check_video() {
        if (!call_ || view_->paused) return;
        if (std::chrono::steady_clock::now() - last_picture_ < std::chrono::seconds(2)) return;
        Room::Hold h = room_->my_hold(session_);
        QString why = h.present && h.audio_only ? "Audio only -- your link is too slow for video"
                    : h.present && h.unanchored ? "Waiting for a keyframe"
                    : "Nothing is arriving";
        if (view_->no_video != why || !view_->remote.isNull()) {
            view_->remote = QImage(); view_->no_video = why; rx_w_ = rx_h_ = 0; view_->overlay.clear(); view_->update();
        }
    }

    void update_tiles() {
        check_video();
        auto now = std::chrono::steady_clock::now();
        double dt = std::chrono::duration<double>(now - t_last_).count();
        t_last_ = now;
        if (!call_ || dt <= 0) {
            t_up_->setText("Upload\n—"); t_down_->setText("Receiving\n—"); t_send_->setText("Sending\n—"); t_audio_->setText("Audio\n—");
            return;
        }
        CallStats& s = call_->stats;
        uint64_t vb = s.v_bytes, vr = s.v_rx_bytes, vn = s.v_recv, ar = s.a_recv, as = s.a_shed, vd = s.v_dropped;
        double up = (vb - p_vb_) * 8 / dt / 1000, down = (vr - p_vr_) * 8 / dt / 1000, fps = (vn - p_vn_) / dt, abps = (ar - p_ar_) / dt;
        uint64_t shed = vd - p_vd_, ashed = as - p_as_;
        p_vb_ = vb; p_vr_ = vr; p_vn_ = vn; p_ar_ = ar; p_as_ = as; p_vd_ = vd;
        t_up_->setText(QString("Upload\n%1 kb/s").arg(up, 0, 'f', 0));
        t_down_->setText(QString("Receiving\n%1 kb/s · %2 fps").arg(down, 0, 'f', 0).arg(fps, 0, 'f', 1));
        t_send_->setText(s.send_w ? QString("Sending\nL%1 · %2x%3").arg(int(s.rung)).arg(int(s.send_w)).arg(int(s.send_h))
                                  : QString("Sending\nL%1 · audio only").arg(int(s.rung)));
        t_audio_->setText(QString("Audio\n%1 blocks/s%2").arg(abps, 0, 'f', 0).arg(ashed ? QString(" · %1 shed").arg(ashed) : ""));
        view_->overlay = rx_w_ ? QString("L%1 · %2x%3 · %4 fps\nvideo %5 kb/s\naudio %6 blocks/s\nshed %7 · keyreq %8")
                                     .arg(rx_level_).arg(rx_w_).arg(rx_h_).arg(fps, 0, 'f', 1).arg(down, 0, 'f', 0)
                                     .arg(abps, 0, 'f', 0).arg(shed).arg(uint64_t(s.keyreqs))
                               : QString();
        view_->self_note = QString("you · %1 kb/s up").arg(up, 0, 'f', 0);
    }

    void auto_test() {                                        // unattended: join a named participant, screenshot
        if (!opt_.auto_join.empty() && !call_) {
            for (int i = 0; i < lobby_->count(); ++i) {
                QString n = lobby_->item(i)->data(Qt::UserRole).toString();
                if (n == QString::fromStdString(opt_.auto_join) || n == "*" + QString::fromStdString(opt_.auto_join)) {
                    lobby_->setCurrentRow(i); call_selected(); break;
                }
            }
            if (!call_ && !pending_.session.empty()) answer();
        }
        if (!opt_.screenshot.empty() && ++ticks_ >= opt_.after) {
            paint_frame();
            grab().save(QString::fromStdString(opt_.screenshot));
            if (diag_->isVisible()) {
                std::string d = opt_.screenshot; auto dot = d.rfind('.'); d.insert(dot == std::string::npos ? d.size() : dot, "-diag");
                diag_->grab().save(QString::fromStdString(d));
                std::cerr << "[app] screenshot " << d << "\n";
            }
            std::cerr << "[app] screenshot " << opt_.screenshot << "\n";
            hang_up();
            QApplication::quit();
        }
    }

private:
    static constexpr int kSteps = 1000;                   // positions 0..999 continuous, 1000 = unlimited
    static constexpr double kMin = 32000.0, kMax = 4000000.0;
    QTimer* bw_rest_ = nullptr;
    Options opt_;
    std::unique_ptr<Room> room_;
    std::unique_ptr<Call> call_;
    std::string media_host_, session_, with_;
    int media_port_ = 0;
    Offer pending_;
    std::set<std::string> answered_;
    QTimer *tick_, *frame_;
    QLabel *status_, *me_, *bw_val_, *jit_val_, *chat_title_, *t_up_, *t_down_, *t_send_, *t_audio_;
    QListWidget *lobby_, *chat_;
    QPushButton *call_btn_, *answer_, *hang_;
    QSlider *bw_, *jit_;
    QLineEdit* say_;
    VideoView* view_;
    QVBoxLayout* mid_ = nullptr;
    DiagWindow* diag_ = nullptr;
    QTimer* sampler_ = nullptr;
    std::deque<Sample> hist_;
    std::chrono::steady_clock::time_point last_picture_ = std::chrono::steady_clock::now();
    std::chrono::steady_clock::time_point t_start_, d_last_ = std::chrono::steady_clock::now();
    uint64_t d_[8] = {0}; bool d_have_ = false;
    float gain_ = 1.0f;
    SwsContext *sws_remote_ = nullptr, *sws_self_ = nullptr;
    uint64_t last_seq_ = 0, self_seq_ = 0, p_vb_ = 0, p_vr_ = 0, p_vn_ = 0, p_ar_ = 0, p_as_ = 0, p_vd_ = 0;
    int rx_w_ = 0, rx_h_ = 0, rx_level_ = 0, ticks_ = 0, age_redraw_ = 0;
    uint64_t seen_ = ~0ull;
    std::chrono::steady_clock::time_point t_last_ = std::chrono::steady_clock::now();
};

static int run_main(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        auto v = [&]() -> std::string { if (i + 1 >= argc) throw std::runtime_error(k + " needs a value"); return argv[++i]; };
        if (k == "--ram") o.ram = v(); else if (k == "--media") o.media = v(); else if (k == "--name") o.name = v();
        else if (k == "--video") o.video = v(); else if (k == "--camera") o.camera = v(); else if (k == "--synthetic") o.synthetic = v();
        else if (k == "--size") o.size = v(); else if (k == "--fps") o.fps = std::stoi(v());
        else if (k == "--no-audio") o.audio = false; else if (k == "--mic") o.mic = std::stoi(v()); else if (k == "--speaker") o.speaker = std::stoi(v());
        else if (k == "--auto-join") o.auto_join = v(); else if (k == "--screenshot") o.screenshot = v(); else if (k == "--after") o.after = std::stoi(v());
        else if (k == "--bandwidth") o.bandwidth = std::stol(v());
        else if (k == "--show-controls") o.show_controls = true;
        else if (k == "--diagnostics") o.diagnostics = true;
        else if (k == "--list-devices") { std::cout << "cameras:\n" << video_devices() << "audio (--mic N / --speaker N):\n" << audio_devices(); return 0; }
        else { std::cerr << "unknown option " << k << "\n"; return 2; }
    }
    if (o.ram.empty() || o.media.empty() || o.name.empty()) {
        std::cerr << "usage: comms-app --ram HOST:PORT --media HOST:PORT --name NAME [--video FILE | --camera DEV | --synthetic WxH]\n";
        return 2;
    }
    av_log_set_level(AV_LOG_ERROR);                        // FFmpeg: errors only (5.1 logs a missing SIMD path as a warning)
    QApplication app(argc, argv);
    Window w(o);
    w.show();
    return app.exec();
}

// [A_FAILURE_SAYS_WHAT_FAILED_V1] an unreachable memory or media server, a camera that will not open: the cause, once,
// and a clean exit -- never "terminate called ... Aborted".
int main(int argc, char** argv) {
    try { return run_main(argc, argv); }
    catch (const std::exception& e) { std::fprintf(stderr, "comms-app: %s\n", e.what()); return 1; }
}
