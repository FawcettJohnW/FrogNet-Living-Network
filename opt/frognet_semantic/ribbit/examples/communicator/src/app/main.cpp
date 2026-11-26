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
//             [--tab security]                                            (open on the Security tab)
//             [--security "cipher NAME;key;protocol NAME;ram HOST:PORT"]  (unattended testing: press those buttons, in order)
//
// Two tabs. Call -- left: the lobby (people and unattended feeds); centre: the call -- remote video with live stats, self-view,
// controls, the two link sliders -- bandwidth (this participant's link, both directions) and jitter -- the stats strip; right: chat with the call.
// Security -- [THE_CALL_IS_UNDER_THE_FRAME_V1] what the Role's setting is now and what every end is running, and the
// owner's changes: the cipher, a new key, the protocol, and "Change RAM host" ([THE_MEMORY_MOVES_V1]). Each is one write
// of the Role's setting by the library (comms/channel.cpp channel_change), as comms-channel makes it.
#include "comms/channel.hpp"
#include "comms/call.hpp"
#include "comms/stamp.hpp"
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
#include <QTabWidget>
#include <QGridLayout>
#include <QPlainTextEdit>
#include <QTimer>
#include <thread>
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
            // [STAMP_THE_PICTURE_V1] this machine's clock, read at paint: minus the clock stamped in the picture = the lag
            const QString ov = "here " + QString::fromStdString(comms::wall_clock_ms()) + "\n" + overlay;
            QFont f("Monospace"); f.setStyleHint(QFont::Monospace); f.setPointSize(9); g.setFont(f);
            QRect tr = g.fontMetrics().boundingRect(QRect(0, 0, 900, 600), Qt::AlignLeft, ov).adjusted(-8, -6, 8, 6);
            tr.moveTo(10, 10);
            g.setPen(Qt::NoPen); g.setBrush(QColor(0, 0, 0, 150)); g.drawRoundedRect(tr, 6, 6);
            g.setPen(QColor(225, 230, 235)); g.drawText(tr.adjusted(8, 6, -8, -6), Qt::AlignLeft, ov);
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
    std::string channel;                                 // --channel ROLE: this program's planes are under the frame, for that Role
    std::string tab;                                     // --tab security: open on the Security tab
    std::string security;                                // --security "...": unattended, the Security tab's buttons pressed in order
    std::string script;                                  // --script "...": unattended, the whole use of the window, step by step (run_script)
    bool auto_answer = false;                            // --auto-answer: a call that rings is answered
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
        setWindowTitle(QString("FrogNet Communicator  ·  ") + comms::COMMS_APP_BUILD);
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
            "QLineEdit{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:6px}"
            "QComboBox{background:#ffffff;border:1px solid #c9ced5;border-radius:8px;padding:5px 8px}"
            "QPlainTextEdit{background:#ffffff;border:1px solid #dfe2e6;border-radius:8px;padding:6px;font-family:monospace;font-size:12px}"
            "QLabel#value{font-weight:600}"
            "QLabel#result{background:#fff8e6;border:1px solid #ecd9a3;border-radius:8px;padding:8px}"
            "QTabWidget::pane{border:0}"
            "QTabBar::tab{background:#eceef1;border:1px solid #dfe2e6;border-bottom:0;border-top-left-radius:8px;border-top-right-radius:8px;padding:7px 22px;margin-left:6px}"
            "QTabBar::tab:selected{background:#ffffff;font-weight:600}");
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
        // [SWITCH_IT_FROM_THE_CALL_V1] (John 2026-11-26: "While it is running I want to be able to switch ciphers, keys,
        // protocols, etc. from the app in real time.") The owner's changes beside the picture: the same one write the
        // Security tab makes, by the same code. A pick takes effect as it is picked.
        auto* sw = new QHBoxLayout; sw->setSpacing(8);
        auto small = [](const char* t) { auto* l = new QLabel(t); l->setObjectName("muted"); return l; };
        c_cipher_ = new QComboBox; c_cipher_->addItems({"not sealed", "aes-256-gcm", "chacha20-poly1305"});
        connect(c_cipher_, &QComboBox::textActivated, [this](const QString& a) { set_cipher(a); });
        c_key_ = new QPushButton("New key"); connect(c_key_, &QPushButton::clicked, [this] { new_key(); });
        c_protocol_ = new QComboBox; c_protocol_->addItems({"tcp", "chorus", "openqasm"});
        connect(c_protocol_, &QComboBox::textActivated, [this](const QString& p) { set_protocol(p); });
        c_ram_to_ = new QLineEdit; c_ram_to_->setPlaceholderText("RAM host:port"); c_ram_to_->setMinimumWidth(150); c_ram_to_->setMaximumWidth(190);
        c_ram_go_ = new QPushButton("Move"); connect(c_ram_go_, &QPushButton::clicked, [this] { move_ram(c_ram_to_->text()); });
        connect(c_ram_to_, &QLineEdit::returnPressed, [this] { move_ram(c_ram_to_->text()); });
        sw->addWidget(small("Cipher")); sw->addWidget(c_cipher_); sw->addWidget(c_key_);
        sw->addSpacing(6); sw->addWidget(small("Protocol")); sw->addWidget(c_protocol_);
        sw->addSpacing(6); sw->addWidget(small("Memory")); sw->addWidget(c_ram_to_); sw->addWidget(c_ram_go_); sw->addStretch();
        mid->addLayout(sw);
        c_result_ = new QLabel("Cipher, key, protocol and where the memory is can be changed here while a call runs."); c_result_->setObjectName("muted"); c_result_->setWordWrap(true);
        mid->addWidget(c_result_);
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
        tabs_ = new QTabWidget;
        auto* call_tab = new QWidget; call_tab->setLayout(body);
        tabs_->addTab(call_tab, "Call");
        tabs_->addTab(build_security(), "Security");
        connect(tabs_, &QTabWidget::currentChanged, [this](int i) { if (i == 1) { ends_age_ = 99; refresh_security(); } });
        top->addWidget(tabs_, 1);
        if (opt_.tab == "security") tabs_->setCurrentIndex(1);
        resize(1180, 720);
        update_tiles();
    }

    // ---- the Security tab -------------------------------------------------------------------------------------
    QWidget* build_security() {
        auto* w = new QWidget;
        auto* row = new QHBoxLayout(w); row->setContentsMargins(18, 16, 18, 16); row->setSpacing(24);
        auto* left = new QVBoxLayout; left->setSpacing(10);
        auto head = [](const char* t) { auto* l = new QLabel(t); l->setObjectName("title"); return l; };
        auto muted = [](const QString& t) { auto* l = new QLabel(t); l->setObjectName("muted"); l->setWordWrap(true); return l; };
        left->addWidget(head("The channel now"));
        auto* now = new QGridLayout; now->setHorizontalSpacing(14); now->setVerticalSpacing(6);
        const char* names[] = {"Role", "Cipher", "Key", "Protocol", "RAM host", "This program"};
        QLabel** values[] = {&s_role_, &s_cipher_, &s_key_, &s_protocol_, &s_ram_, &s_me_};
        for (int i = 0; i < 6; ++i) {
            now->addWidget(muted(names[i]), i, 0, Qt::AlignTop);
            *values[i] = new QLabel; (*values[i])->setObjectName("value"); (*values[i])->setWordWrap(true);
            (*values[i])->setTextInteractionFlags(Qt::TextSelectableByMouse);
            now->addWidget(*values[i], i, 1);
        }
        now->setColumnStretch(1, 1);
        left->addLayout(now);
        left->addSpacing(8);
        left->addWidget(head("Change it"));
        left->addWidget(muted("Each change is one write of the Role's setting. It carries a check made with this machine's secret, and every "
                              "end switches in its own stream: a call is not interrupted."));
        auto* set = new QGridLayout; set->setHorizontalSpacing(10); set->setVerticalSpacing(8);
        s_cipher_pick_ = new QComboBox; s_cipher_pick_->addItems({"not sealed", "aes-256-gcm", "chacha20-poly1305"});
        auto* cipher_go = new QPushButton("Set cipher");
        connect(cipher_go, &QPushButton::clicked, [this] { set_cipher(s_cipher_pick_->currentText()); });
        auto* key_go = new QPushButton("New key");
        connect(key_go, &QPushButton::clicked, [this] { new_key(); });
        s_protocol_pick_ = new QComboBox; s_protocol_pick_->addItems({"tcp", "chorus", "openqasm"});
        auto* protocol_go = new QPushButton("Set protocol");
        connect(protocol_go, &QPushButton::clicked, [this] { set_protocol(s_protocol_pick_->currentText()); });
        s_ram_to_ = new QLineEdit; s_ram_to_->setPlaceholderText("host:port of another comms-ram that is running");
        auto* ram_go = new QPushButton("Change RAM host");
        connect(ram_go, &QPushButton::clicked, [this] { move_ram(s_ram_to_->text()); });
        set->addWidget(muted("Cipher"), 0, 0); set->addWidget(s_cipher_pick_, 0, 1); set->addWidget(cipher_go, 0, 2);
        set->addWidget(muted("Key"), 1, 0); set->addWidget(muted("A new random number in the setting: every sender computes a new key from it."), 1, 1); set->addWidget(key_go, 1, 2);
        set->addWidget(muted("Protocol"), 2, 0); set->addWidget(s_protocol_pick_, 2, 1); set->addWidget(protocol_go, 2, 2);
        set->addWidget(muted("RAM host"), 3, 0); set->addWidget(s_ram_to_, 3, 1); set->addWidget(ram_go, 3, 2);
        set->setColumnStretch(1, 1);
        left->addLayout(set);
        s_result_ = new QLabel("No change made from this window yet."); s_result_->setObjectName("result"); s_result_->setWordWrap(true);
        s_result_->setTextInteractionFlags(Qt::TextSelectableByMouse);
        left->addWidget(s_result_);
        left->addStretch();
        row->addLayout(left, 5);
        auto* right = new QVBoxLayout; right->setSpacing(8);
        right->addWidget(head("Every end and what it is running"));
        s_ends_ = new QPlainTextEdit; s_ends_->setReadOnly(true);
        right->addWidget(s_ends_, 1);
        row->addLayout(right, 4);
        s_buttons_ = {cipher_go, key_go, protocol_go, ram_go};
        return w;
    }

    // One change by the owner, off the window's thread: the library writes the setting and waits for every end's report.
    void owner_change(const std::string& what, const ChannelChange& c) {
        const auto at = RamAt::process().at();
        auto ch = Channel::watching();
        const std::string role = ch ? ch->role() : std::string(CHANNEL_ROLE);
        said_both(QString::fromStdString("Setting " + what + " ..."));
        changing_ = true;
        for (auto* b : s_buttons_) b->setEnabled(false);
        for (QWidget* b : std::initializer_list<QWidget*>{c_cipher_, c_key_, c_protocol_, c_ram_go_}) b->setEnabled(false);
        std::thread([this, what, c, at, role] {
            std::string said;
            try {
                const ChannelDone d = channel_change(at.first, at.second, role, c);
                said = what + ": " + d.summary();
                for (auto& l : d.lines) said += "\n" + l;
                if (d.moved) said += "\n" + std::to_string(d.chat_carried) + " line(s) of chat carried there";
                if (!d.checked) said += "\nThis machine holds no secret for the Role: the setting carries no check, and an end that holds one ignores it.";
            } catch (const std::exception& e) { said = std::string("Not set: ") + e.what(); }
            std::fprintf(stderr, "[security] %s\n", said.c_str());
            QMetaObject::invokeMethod(this, [this, said] {
                said_both(QString::fromStdString(said));
                for (auto* b : s_buttons_) b->setEnabled(true);
                for (QWidget* b : std::initializer_list<QWidget*>{c_cipher_, c_key_, c_protocol_, c_ram_go_}) b->setEnabled(true);
                changing_ = false;
                ends_age_ = 99; refresh_security();
            }, Qt::QueuedConnection);
        }).detach();
    }

    void said_both(const QString& t) { s_result_->setText(t); c_result_->setText(QString(t).replace("\n", "  ·  ")); }
    void set_cipher(const QString& a) {
        ChannelChange c;
        if (a == "not sealed") c.unseal = true; else c.algorithm = a.toStdString();
        owner_change(("cipher " + a).toStdString(), c);
    }
    void new_key() { ChannelChange c; c.new_key = true; owner_change("a new key", c); }
    void set_protocol(const QString& p) { ChannelChange c; c.protocol = p.toStdString(); owner_change("protocol " + c.protocol, c); }
    void move_ram(const QString& text) {
        const std::string to = text.trimmed().toStdString();
        const auto colon = to.rfind(':');
        if (colon == std::string::npos || colon == 0 || colon + 1 >= to.size() || to.find_first_not_of("0123456789", colon + 1) != std::string::npos) {
            said_both("Give the new RAM host as host:port."); return;
        }
        ChannelChange c; c.move_host = to.substr(0, colon); c.move_port = std::stoi(to.substr(colon + 1));
        owner_change("the memory to " + to, c);
    }
    // every picker shows the setting this program holds, whoever changed it and from where
    void show_setting() {
        auto ch = Channel::watching();
        if (!ch || ch->changes() == shown_changes_) return;
        shown_changes_ = ch->changes();
        const auto f = ch->fields();
        auto get = [&f](const char* k) { auto it = f.find(k); return it == f.end() ? std::string() : it->second; };
        const bool sealed = !get("number").empty() && get("number") != "0";
        const QString cipher = QString::fromStdString(sealed ? get("protection") : std::string("not sealed"));
        const QString protocol = QString::fromStdString(get("driver").empty() ? std::string("tcp") : get("driver"));
        for (QComboBox* b : {c_cipher_, s_cipher_pick_}) b->setCurrentText(cipher);
        for (QComboBox* b : {c_protocol_, s_protocol_pick_}) b->setCurrentText(protocol);
    }
    // [THE_OVERLAY_SHOWS_THE_CHANNEL_V1] (John 2026-11-26: "The perf overlay should show all.") The channel's lines of
    // the overlay: what is on the wire now, where the memory is, what this program's planes have done under the frame
    // over every call of this run, and the last change taken with how long ago.
    QString channel_lines() {
        auto ch = Channel::watching();
        if (!ch) return "channel: none (start with --channel ROLE)";
        const auto f = ch->fields();
        auto get = [&f](const char* k) { auto it = f.find(k); return it == f.end() ? std::string() : it->second; };
        const bool sealed = !get("number").empty() && get("number") != "0";
        const auto at = RamAt::process().at();
        const channel_frame::Counts n = ch->totals();
        const Channel::Change lc = ch->last_change();
        QString t = QString("channel %1%2\nprotocol %3 · cipher %4%5\nmemory %6:%7\nunder the frame %8 kB out · %9 kB in\nswitch calls sent: protocol %10 · key/cipher %11 · received: protocol %12 · key/cipher %13\nsent again %14 · not opened %15")
            .arg(QString::fromStdString(ch->role()), ch->framed() ? "" : " (planes NOT under the frame)")
            .arg(QString::fromStdString(get("driver").empty() ? std::string("tcp") : get("driver")))
            .arg(QString::fromStdString(sealed ? get("protection") : std::string("not sealed")))
            .arg(sealed ? QString(" · key %1").arg(QString::fromStdString(ch->key_tag())) : QString())
            .arg(QString::fromStdString(at.first)).arg(at.second)
            .arg(qulonglong(n.sent / 1000)).arg(qulonglong(n.received / 1000))
            .arg(qulonglong(n.protocol[0])).arg(qulonglong(n.encryption[0])).arg(qulonglong(n.protocol[1])).arg(qulonglong(n.encryption[1]))
            .arg(qulonglong(n.sent_again)).arg(qulonglong(n.not_opened));
        if (!lc.what.empty())
            t += QString("\nlast: %1\n      at %2 (%3 ago) · %4 change(s) taken")
                     .arg(QString::fromStdString(lc.what), QString::fromStdString(lc.at),
                          lc.age_s < 90 ? QString("%1 s").arg(lc.age_s, 0, 'f', 0) : QString("%1 min").arg(lc.age_s / 60, 0, 'f', 0))
                     .arg(qulonglong(ch->changes()));
        return t;
    }

    void refresh_security() {
        auto ch = Channel::watching();
        const auto at = RamAt::process().at();
        const std::string here = at.first + ":" + std::to_string(at.second);
        if (!ch) { s_role_->setText("no channel"); return; }
        const auto f = ch->fields();
        auto get = [&f](const char* k) { auto it = f.find(k); return it == f.end() ? std::string() : it->second; };
        const bool sealed = !get("number").empty() && get("number") != "0";
        std::string secret;
        try { secret = channel_secret_line(ch->role(), ch->secrets()); } catch (const std::exception&) { secret = "this machine holds no secret for the Role (" + ch->secrets() + ")"; }
        s_role_->setText(QString::fromStdString(ch->role()));
        s_cipher_->setText(QString::fromStdString(sealed ? get("protection") : std::string("not sealed")));
        s_key_->setText(QString::fromStdString("computed from the secret: " + secret));
        s_protocol_->setText(QString::fromStdString(get("driver").empty() ? std::string("tcp") : get("driver")));
        s_ram_->setText(QString::fromStdString(here));
        s_me_->setText(QString::fromStdString(ch->end() + (ch->framed() ? ", its planes under the frame" : ", its planes NOT under the frame (start it with --channel " + ch->role() + ")")
                                              + (ch->ignored().empty() ? "" : "\nIt IGNORED the last setting: " + ch->ignored())));
        if (ch->changes() != sec_changes_) {                      // a setting was taken: the pickers show it
            sec_changes_ = ch->changes(); ends_age_ = 99;
            s_cipher_pick_->setCurrentText(QString::fromStdString(sealed ? get("protection") : std::string("not sealed")));
            s_protocol_pick_->setCurrentText(QString::fromStdString(get("driver").empty() ? std::string("tcp") : get("driver")));
        }
        if (++ends_age_ < 3 || ends_busy_) return;                // the ends: read again every 3 s, and when a setting was taken
        ends_age_ = 0; ends_busy_ = true;
        const std::string role = ch->role();
        std::thread([this, at, role] {
            std::string text;
            try {
                const ChannelNow n = channel_show(at.first, at.second, role);
                for (auto& e : n.ends) {
                    auto g = [&e](const char* k) { auto it = e.report.find(k); return it == e.report.end() ? std::string() : it->second; };
                    text += e.end + "\n    " + g("driver") + (g("sealed") == "true" ? ", sealed with " + g("protection") : std::string(", not sealed"))
                            + (g("framed") == "false" ? ", not under the frame" : "") + "\n    memory at " + g("ram")
                            + (g("ignored").empty() ? "" : "\n    IGNORED: " + g("ignored")) + "\n";
                }
                if (n.ends.empty()) text = "(no end reports)";
            } catch (const std::exception& e) { text = std::string("could not read the ends: ") + e.what(); }
            QMetaObject::invokeMethod(this, [this, text] { if (s_ends_->toPlainText().toStdString() != text) s_ends_->setPlainText(QString::fromStdString(text)); ends_busy_ = false; }, Qt::QueuedConnection);
        }).detach();
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
        session_ = session; with_ = with; ++calls_made_; other_on_ = false;
        last_picture_ = std::chrono::steady_clock::now(); got_picture_ = false; mark();
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

    void answer() { if (pending_.session.size()) { Offer o = pending_; pending_ = Offer{}; start_call(o.session, o.media_host, o.media_port, o.offered_by); } }

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
        try { room_->leave_call(session_); } catch (const std::exception& e) { std::cerr << "[room] after the call: " << e.what() << "\n"; }
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
        if (tabs_->currentIndex() == 1) refresh_security();
        show_setting();
        // lobby
        bool changed = room_->version() != seen_;               // [HOLD_THE_READ_V1] redraw only on a change
        seen_ = room_->version();
        std::vector<Member> ros;
        try { ros = room_->roster(); status_->setText("Connected"); }
        catch (const std::exception& e) { status_->setText(QString("RAM unreachable: ") + e.what()); return; }
        if (changed || ++age_redraw_ >= 5) { age_redraw_ = 0; redraw_lobby(ros); }
        // incoming calls
        // [RINGS_ONLY_WHILE_OFFERED_V1] the Answer button shows only while the call rings (Room::ringing: invited <= 60 s
        // ago and the caller still offers it), checked every tick, so it goes when the caller hangs up or goes away.
        if (!call_) {
            std::vector<Offer> rg = room_->ringing();
            if (!pending_.session.empty()) {
                bool still = false;
                for (auto& o : rg) if (o.session == pending_.session) still = true;
                if (!still) {
                    std::cerr << "[app] " << pending_.offered_by << " is no longer calling (session " << pending_.session << ")\n";
                    pending_ = Offer{}; answer_->hide();
                }
            }
            if (pending_.session.empty())
                for (auto& o : rg)
                    if (!answered_.count(o.session)) {
                        pending_ = o; answered_.insert(o.session);
                        std::cerr << "[app] " << o.offered_by << " is calling (session " << o.session << ", invited " << int(o.age_s) << " s ago)\n";
                        answer_->setText("Answer " + QString::fromStdString(o.offered_by)); answer_->show();
                        break;
                    }
        }
        if (call_ && changed) read_chat();
        // [THE_CALL_ENDS_WHEN_THE_OTHER_LEAVES_V1] A call with one other person ends here when that person leaves it,
        // as a telephone's does: the lobby is back, and either of them can call again. (A feed is watched until this
        // end stops watching.) Before this the window stayed in the call, waiting for a picture that would not come.
        if (call_ && !with_.empty() && with_[0] != '*') {
            bool on = false;
            for (auto& x : room_->calls_of(with_)) if (x == session_) on = true;
            if (on) other_on_ = true;
            else if (other_on_) {
                const std::string who = with_;
                std::cerr << "[app] " << who << " left the call (session " << session_ << "): this end hangs up\n";
                hang_up();
                status_->setText(QString::fromStdString(who + " hung up"));
            }
        }
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
            const auto now = std::chrono::steady_clock::now();
            if (got_picture_) gap_max_ms_ = std::max(gap_max_ms_, std::chrono::duration<double, std::milli>(now - prev_picture_).count());
            got_picture_ = true; prev_picture_ = now; ++pictures_;
            last_picture_ = now; view_->no_video.clear();
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
        const long cap = call_->throttle();
        Room::Hold h = room_->my_hold(session_);
        const QString served = !h.present ? QString("--") : h.audio_only ? QString("audio only") : h.passthrough ? QString("pass-through") : QString("transcoded to %1x%2").arg(h.w).arg(h.h);
        const QString recv = rx_w_ ? QString("receiving L%1 · %2x%3 · %4 fps · %5 kb/s").arg(rx_level_).arg(rx_w_).arg(rx_h_).arg(fps, 0, 'f', 1).arg(down, 0, 'f', 0)
                                   : QString("receiving no picture · %1 kb/s").arg(down, 0, 'f', 0);
        const QString send = s.send_w ? QString("sending   L%1 · %2x%3 · %4 kb/s (ceiling L%5)").arg(int(s.rung)).arg(int(s.send_w)).arg(int(s.send_h)).arg(up, 0, 'f', 0).arg(int(s.ceiling))
                                      : QString("sending   L%1 · audio only · %2 kb/s").arg(int(s.rung)).arg(up, 0, 'f', 0);
        view_->overlay = QString("with %1 · call %2\n%3\n%4\naudio %5 blocks/s · shed video %6 audio %7 · keyreq %8\nlink %9 · jitter %10 ms · server %11\n%12")
                             .arg(QString::fromStdString(with_), calls_made_ ? QString("#%1").arg(calls_made_) : QString("--"))
                             .arg(recv, send).arg(abps, 0, 'f', 0).arg(shed).arg(ashed).arg(uint64_t(s.keyreqs))
                             .arg(cap == 0 ? QString("unlimited") : cap >= 1000000 ? QString("%1 Mb/s").arg(cap / 1e6, 0, 'f', 2) : QString("%1 kb/s").arg(cap / 1000))
                             .arg(jit_->value()).arg(served, channel_lines());
        view_->self_note = QString("you · %1 kb/s up").arg(up, 0, 'f', 0);
    }

    void mark() { mark_at_ = std::chrono::steady_clock::now(); mark_pictures_ = pictures_; gap_max_ms_ = 0; }

    // [THE_WHOLE_USE_AS_A_SCRIPT_V1] --script "step;step;...": the window used as a person uses it, one step after the
    // one before is done, each step saying what it measured. Steps:
    //   wait-for NAME    until NAME is in the room          call NAME     call, until NAME's picture arrives
    //   answer           until a call rings, then answer it, until the caller's picture arrives
    //   stay N           N seconds                           hang          hang up
    //   wait-idle        until this end is in no call (the other hung up)
    //   cipher X | key | protocol X | ram HOST:PORT          the owner's changes, each until every end has answered
    //   bandwidth KBPS (0: unlimited) | jitter MS            the link
    //   shot FILE        a picture of the window             quit
    // After each step: "[script] done ..." with the pictures received since the step before and the longest time
    // between two of them. A step that is not done in 45 s ends the program with exit code 3 and says which.
    void run_script() {
        if (steps_.empty()) {
            const std::string all = opt_.script;
            for (size_t a = 0; a <= all.size();) {
                size_t b = all.find(';', a);
                if (b == std::string::npos) b = all.size();
                std::string st = all.substr(a, b - a);
                while (!st.empty() && st[0] == ' ') st.erase(0, 1);
                while (!st.empty() && st.back() == ' ') st.pop_back();
                if (!st.empty()) steps_.push_back(st);
                a = b + 1;
            }
            mark();
        }
        if (step_ >= steps_.size()) return;
        const std::string st = steps_[step_];
        const auto sp = st.find(' ');
        const std::string what = st.substr(0, sp), arg = sp == std::string::npos ? "" : st.substr(sp + 1);
        const auto now = std::chrono::steady_clock::now();
        auto in_room = [this](const std::string& n) { for (int i = 0; i < lobby_->count(); ++i) if (lobby_->item(i)->data(Qt::UserRole).toString().toStdString() == n) return i; return -1; };
        bool done = false; std::string note;
        if (!step_begun_) {
            bool begun = true;
            if (what == "call") { int i = in_room(arg); if (i < 0 || call_) begun = false; else { lobby_->setCurrentRow(i); call_selected(); } }
            else if (what == "answer") { if (pending_.session.empty() || call_) begun = false; else answer(); }
            else if (what == "hang") hang_up();
            else if (what == "cipher") { if (changing_) begun = false; else { c_cipher_->setCurrentText(QString::fromStdString(arg)); set_cipher(QString::fromStdString(arg)); } }
            else if (what == "key") { if (changing_) begun = false; else new_key(); }
            else if (what == "protocol") { if (changing_) begun = false; else { c_protocol_->setCurrentText(QString::fromStdString(arg)); set_protocol(QString::fromStdString(arg)); } }
            else if (what == "ram") { if (changing_) begun = false; else { c_ram_to_->setText(QString::fromStdString(arg)); move_ram(QString::fromStdString(arg)); } }
            else if (what == "bandwidth") { const long kb = std::stol(arg); bw_->setValue(kb > 0 ? pos_of(kb * 1000) : kSteps); apply_bw(bw_->value()); }
            else if (what == "jitter") jit_->setValue(std::stoi(arg));
            else if (what == "shot") { paint_frame(); view_->update(); grab().save(QString::fromStdString(arg)); }
            if (step_at_ == std::chrono::steady_clock::time_point{}) step_at_ = now;
            if (begun) { step_begun_ = true; std::cerr << "[script] step " << step_ + 1 << " of " << steps_.size() << ": " << st << "\n"; }
        }
        const double took = std::chrono::duration<double>(now - step_at_).count();
        if (step_begun_) {
            if (what == "wait-for") done = in_room(arg) >= 0;
            else if (what == "call" || what == "answer") { done = call_ && got_picture_; if (done) note = "first picture " + std::to_string(int(took * 1000)) + " ms after the step began"; }
            else if (what == "stay") done = took >= std::stod(arg);
            else if (what == "wait-idle") done = !call_;
            else if (what == "cipher" || what == "key" || what == "protocol" || what == "ram") { done = !changing_; if (done) note = c_result_->text().toStdString(); }
            else if (what == "quit") { std::cerr << "[script] finished: every step done\n"; hang_up(); QApplication::exit(0); step_ = steps_.size(); return; }
            else done = true;
        }
        if (done) {
            const double since = std::chrono::duration<double>(now - mark_at_).count();
            std::cerr << "[script] done " << step_ + 1 << ": " << st << " (" << int(took * 1000) << " ms)"
                      << (call_ || pictures_ != mark_pictures_ ? "; pictures since the step before: " + std::to_string(pictures_ - mark_pictures_) + " in " + std::to_string(int(since * 1000))
                                 + " ms, longest between two " + std::to_string(int(gap_max_ms_)) + " ms" : std::string())
                      << (note.empty() ? "" : "; " + note) << "\n";
            mark(); ++step_; step_begun_ = false; step_at_ = {};
        } else if (took > 45 && what != "stay") {
            std::cerr << "[script] FAILED at step " << step_ + 1 << ": '" << st << "' was not done in 45 s"
                      << (call_ ? " (in a call, " + std::string(got_picture_ ? "pictures arriving" : "no picture yet") + ")" : " (in no call)") << "\n";
            hang_up(); QApplication::exit(3); step_ = steps_.size();
        }
    }

    void auto_test() {                                        // unattended: join a named participant, screenshot
        if (opt_.auto_answer && !call_ && !pending_.session.empty()) answer();
        if (!opt_.script.empty()) run_script();
        if (!opt_.auto_join.empty() && !call_) {
            for (int i = 0; i < lobby_->count(); ++i) {
                QString n = lobby_->item(i)->data(Qt::UserRole).toString();
                if (n == QString::fromStdString(opt_.auto_join) || n == "*" + QString::fromStdString(opt_.auto_join)) {
                    lobby_->setCurrentRow(i); call_selected(); break;
                }
            }
            if (!call_ && !pending_.session.empty()) answer();
        }
        // unattended: the Security tab's own buttons, one at a time, each after the one before has answered
        if (!opt_.security.empty() && ticks_ >= 3 && s_buttons_[0]->isEnabled()) {
            const auto semi = opt_.security.find(';');
            const std::string step = opt_.security.substr(0, semi);
            opt_.security = semi == std::string::npos ? "" : opt_.security.substr(semi + 1);
            const auto sp = step.find(' ');
            const std::string what = step.substr(0, sp), arg = sp == std::string::npos ? "" : step.substr(sp + 1);
            std::cerr << "[app] pressing: " << step << "\n";
            if (what == "cipher") { s_cipher_pick_->setCurrentText(QString::fromStdString(arg)); s_buttons_[0]->click(); }
            else if (what == "key") s_buttons_[1]->click();
            else if (what == "protocol") { s_protocol_pick_->setCurrentText(QString::fromStdString(arg)); s_buttons_[2]->click(); }
            else if (what == "ram") { s_ram_to_->setText(QString::fromStdString(arg)); s_buttons_[3]->click(); }
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
    // the owner's changes beside the picture
    QComboBox *c_cipher_ = nullptr, *c_protocol_ = nullptr;
    QPushButton *c_key_ = nullptr, *c_ram_go_ = nullptr;
    QLineEdit* c_ram_to_ = nullptr;
    QLabel* c_result_ = nullptr;
    uint64_t shown_changes_ = ~0ull;
    bool changing_ = false, other_on_ = false;
    int calls_made_ = 0;
    // the script, and what it measures between two of its steps
    std::vector<std::string> steps_;
    size_t step_ = 0;
    bool step_begun_ = false, got_picture_ = false;
    std::chrono::steady_clock::time_point step_at_{}, mark_at_{}, prev_picture_{};
    uint64_t mark_pictures_ = 0, pictures_ = 0;
    double gap_max_ms_ = 0;
    // the Security tab
    QTabWidget* tabs_ = nullptr;
    QLabel *s_role_ = nullptr, *s_cipher_ = nullptr, *s_key_ = nullptr, *s_protocol_ = nullptr, *s_ram_ = nullptr, *s_me_ = nullptr, *s_result_ = nullptr;
    QComboBox *s_cipher_pick_ = nullptr, *s_protocol_pick_ = nullptr;
    QLineEdit* s_ram_to_ = nullptr;
    QPlainTextEdit* s_ends_ = nullptr;
    std::vector<QPushButton*> s_buttons_;
    uint64_t sec_changes_ = ~0ull;
    int ends_age_ = 99;
    bool ends_busy_ = false;
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
        else if (k == "--channel") o.channel = v();
        else if (k == "--tab") o.tab = v();
        else if (k == "--security") o.security = v();
        else if (k == "--script") o.script = v();
        else if (k == "--auto-answer") o.auto_answer = true;
        else if (k == "--show-controls") o.show_controls = true;
        else if (k == "--diagnostics") o.diagnostics = true;
        else if (k == "--list-devices") { std::cout << "cameras:\n" << video_devices() << "audio (--mic N / --speaker N):\n" << audio_devices(); return 0; }
        else { std::cerr << "unknown option " << k << "\n"; return 2; }
    }
    if (o.ram.empty() || o.media.empty() || o.name.empty()) {
        std::cerr << "usage: comms-app --ram HOST:PORT --media HOST:PORT --name NAME [--video FILE | --camera DEV | --synthetic WxH] [--channel ROLE]\n";
        return 2;
    }
    av_log_set_level(AV_LOG_ERROR);                        // FFmpeg: errors only (5.1 logs a missing SIMD path as a warning)
    // [THE_CALL_IS_UNDER_THE_FRAME_V1] --channel ROLE: this program's planes are under the frame; the Role's setting is
    // read from the memory before any plane is opened
    const auto rc = o.ram.rfind(':');
    auto under = comms::channel_for_program(o.channel, o.name, o.ram.substr(0, rc), std::stoi(o.ram.substr(rc + 1)));
    QApplication app(argc, argv);
    Window w(o);
    w.show();
    return app.exec();
}

// [A_FAILURE_SAYS_WHAT_FAILED_V1] an unreachable memory or media server, a camera that will not open: the cause, once,
// and a clean exit -- never "terminate called ... Aborted".
int main(int argc, char** argv) {
    std::fprintf(stderr, "[app] build %s\n", comms::COMMS_APP_BUILD);
    try { return run_main(argc, argv); }
    catch (const std::exception& e) { std::fprintf(stderr, "comms-app: %s\n", e.what()); return 1; }
}
