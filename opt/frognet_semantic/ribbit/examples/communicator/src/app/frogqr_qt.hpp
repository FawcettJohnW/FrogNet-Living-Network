// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogqr_qt.hpp -- [FROG_QR_V1] FrogNet's QR code, drawn with Qt: a frog. (John, 27 Nov 2026: "A frognet QR code is a QR
// code with the FrogNet logo in the middle. Look at the OpenEOC code.") This is opt/fdf/src/openeoc-app/ui-admin/frogqr.js
// (frogqr-2026-11-06-r3) line for line, with a QPainter where that writes SVG: the modules are lily pads, the three
// finder patterns are eyes, pads next to each other are joined by the logo's green lines, the FrogNet logo's frog sits
// in the middle (the code is made at error correction H, 30 percent, and the frog covers well under half of that), and
// the alignment patterns are frog spawn. What a phone's reader (zxing) needs, found by that file's test: the eyes'
// pupils plain, the alignment patterns solid, the text short. The frog's outline and the alignment table are that file's.
#pragma once
#include <QImage>
#include <QPainter>
#include <QPainterPath>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

namespace frogqr {
inline const char* frog_d() { return R"FROG(M3698 4000 c-128 -23 -222 -69 -313 -153 -104 -96 -198 -126 -431 -134 -229 -9 -342 18 -444 103 -171 143 -258 178 -435 179 -148 0 -195 -12 -308 -77 -228 -132 -318 -372 -242 -648 23 -85 19 -147 -12 -200 -75 -130 -93 -163 -112 -214 -40 -109 -28 -187 57 -370 l46 -98 -36 -67 c-20 -36 -46 -96 -58 -133 -12 -38 -27 -68 -34 -68 -7 0 -94 36 -193 80 -238 106 -346 134 -498 127 -557 -25 -746 -646 -360 -1182 130 -181 420 -471 575 -575 183 -122 179 -126 -152 -133 -344 -8 -424 -25 -526 -113 -73 -62 -116 -190 -98 -288 l7 -36 2826 0 2825 0 -4 93 c-5 111 -28 170 -89 227 -102 95 -190 111 -554 103 -149 -3 -288 -3 -310 1 l-39 8 44 39 c49 43 94 78 240 187 89 65 317 285 357 342 9 14 36 49 60 77 191 237 297 500 277 693 -33 334 -257 545 -579 545 -148 0 -217 -19 -462 -126 -111 -49 -206 -89 -211 -89 -5 0 -26 39 -47 88 -20 48 -51 117 -67 155 l-31 67 37 54 c116 168 108 344 -28 600 -53 101 -55 119 -21 186 48 96 54 251 14 368 -90 261 -392 433 -671 382z m-1457 -231 c49 -23 127 -78 194 -138 43 -39 163 -103 227 -122 256 -76 499 -37 786 124 214 121 325 160 385 138 20 -8 18 -10 -20 -21 -95 -29 -241 -195 -284 -323 -28 -85 40 -257 143 -358 123 -120 244 -155 431 -126 l88 13 14 -26 c147 -285 -6 -391 -410 -284 -49 13 -175 53 -280 89 -305 105 -371 119 -560 120 -177 0 -194 -4 -555 -120 -513 -166 -655 -180 -755 -75 -53 55 -34 175 42 269 l19 23 121 -13 c145 -15 200 -6 295 50 210 123 301 359 211 541 -55 112 -114 157 -253 196 -173 47 -167 75 15 71 74 -2 100 -7 146 -28z m-358 -155 c87 -44 95 -75 35 -140 -101 -110 -208 -105 -208 10 0 103 91 171 173 130z m1218 -985 c42 -10 161 -50 265 -89 219 -82 327 -111 512 -136 124 -17 132 -19 132 -40 0 -29 -54 -76 -116 -100 -26 -10 -107 -48 -179 -83 -385 -188 -540 -229 -809 -218 -188 8 -310 35 -471 104 -273 116 -343 143 -432 162 -51 11 -102 25 -113 31 -17 9 -60 77 -60 94 0 14 53 29 189 56 82 17 200 48 262 71 314 111 370 129 468 148 174 34 216 34 352 0z m-1335 -521 c62 -46 114 -88 114 -93 0 -6 -24 -42 -54 -81 -30 -39 -63 -89 -74 -110 -90 -178 -76 -592 30 -893 60 -170 129 -292 308 -541 103 -143 143 -216 154 -282 l7 -38 -55 0 c-92 0 -147 34 -217 133 -22 31 -44 57 -50 57 -5 0 -9 5 -9 10 0 6 -44 66 -99 134 -128 161 -152 197 -211 314 -187 374 -227 841 -104 1212 41 121 112 260 135 260 6 0 62 -37 125 -82z m-851 -37 c39 -12 141 -56 227 -99 l157 -77 -6 -35 c-3 -19 -20 -71 -36 -115 l-31 -80 2 -210 c1 -115 5 -222 8 -237 3 -16 1 -28 -4 -28 -13 0 -214 199 -286 283 -67 79 -102 99 -170 101 l-51 1 -3 -53 c-3 -59 18 -106 87 -191 20 -24 61 -79 91 -122 30 -42 134 -171 230 -286 96 -116 193 -232 215 -259 41 -49 115 -163 115 -176 0 -55 -385 203 -628 421 -59 53 -127 111 -150 129 -23 19 -42 39 -42 45 0 7 -29 50 -65 97 -119 154 -160 223 -193 323 -99 298 -13 526 223 588 53 14 235 2 310 -20z m4433 -11 c67 -29 144 -113 173 -190 84 -220 -79 -576 -407 -892 -129 -124 -446 -371 -579 -450 -266 -159 -232 -96 255 460 223 255 347 434 369 531 l9 43 -39 -5 c-77 -10 -209 -96 -368 -242 -124 -112 -116 -115 -116 33 0 101 -7 165 -34 312 l-33 185 48 43 c80 70 289 164 429 191 85 17 232 7 293 -19z m-1320 -173 c36 -29 127 -263 148 -377 75 -419 -131 -964 -464 -1229 -82 -66 -265 -161 -387 -202 -70 -24 -243 -35 -261 -16 -16 15 44 61 136 104 244 114 339 180 463 322 186 213 327 590 327 879 0 41 -16 167 -35 282 -41 245 -39 267 23 255 20 -4 43 -12 50 -18z m-1636 -51 c277 -121 420 -142 723 -107 l180 20 165 70 c143 60 167 67 177 54 15 -19 99 -275 119 -363 34 -144 15 -349 -48 -530 -232 -663 -867 -856 -1315 -399 -128 131 -162 187 -227 376 -75 219 -92 449 -47 618 21 78 128 315 143 315 4 0 63 -24 130 -54z)FROG"; }
constexpr double FROG_W = 585, FROG_H = 410;
inline const std::vector<std::vector<int>>& align_table() {       // the alignment patterns' centres by version (ISO/IEC 18004 Annex E)
    static const std::vector<std::vector<int>> t = {
    {},
    {6,18},
    {6,22},
    {6,26},
    {6,30},
    {6,34},
    {6,22,38},
    {6,24,42},
    {6,26,46},
    {6,28,50},
    {6,30,54},
    {6,32,58},
    {6,34,62},
    {6,26,46,66},
    {6,26,48,70},
    {6,26,50,74},
    {6,30,54,78},
    {6,30,56,82},
    {6,30,58,86},
    {6,34,62,90},
    {6,28,50,72,94},
    {6,26,50,74,98},
    {6,30,54,78,102},
    {6,28,54,80,106},
    {6,32,58,84,110},
    {6,30,58,86,114},
    {6,34,62,90,118},
    {6,26,50,74,98,122},
    {6,30,54,78,102,126},
    {6,26,52,78,104,130},
    {6,30,56,82,108,134},
    {6,34,60,86,112,138},
    {6,30,58,86,114,142},
    {6,34,62,90,118,146},
    {6,30,54,78,102,126,150},
    {6,24,50,76,102,128,154},
    {6,28,54,80,106,132,158},
    {6,32,58,84,110,136,162},
    {6,26,54,82,110,138,166},
    {6,30,58,86,114,142,170}};
    return t;
}

// the frog's outline: potrace's path (M m c l z, relative ones repeating) as a QPainterPath
inline QPainterPath frog_path() {
    QPainterPath p; p.setFillRule(Qt::WindingFill);
    const char* s = frog_d();
    char cmd = 0; double x = 0, y = 0;
    auto num = [&s]() { char* e = nullptr; double v = std::strtod(s, &e); s = e; return v; };
    auto more = [&s]() { while (*s == ' ' || *s == ',') ++s; return *s && (std::isdigit(static_cast<unsigned char>(*s)) || *s == '-' || *s == '.'); };
    while (*s) {
        while (*s == ' ') ++s;
        if (!*s) break;
        if (std::isalpha(static_cast<unsigned char>(*s))) cmd = *s++;
        if (cmd == 'z' || cmd == 'Z') { p.closeSubpath(); x = p.currentPosition().x(); y = p.currentPosition().y(); cmd = 0; continue; }
        if (!more()) { if (!cmd) break; continue; }
        if (cmd == 'M') { x = num(); more(); y = num(); p.moveTo(x, y); cmd = 'L'; }
        else if (cmd == 'm') { x += num(); more(); y += num(); p.moveTo(x, y); cmd = 'l'; }
        else if (cmd == 'L') { x = num(); more(); y = num(); p.lineTo(x, y); }
        else if (cmd == 'l') { x += num(); more(); y += num(); p.lineTo(x, y); }
        else if (cmd == 'c') {
            double a[6]; for (double& v : a) { more(); v = num(); }
            p.cubicTo(x + a[0], y + a[1], x + a[2], y + a[3], x + a[4], y + a[5]); x += a[4]; y += a[5];
        } else break;
    }
    return p;
}

/** The code whose modules are `dark` (n on a side, row by row; made at error correction H) as a frog, px pixels a module. */
inline QImage image(const std::vector<bool>& dark, int n, int px) {
    const QColor LINE("#2f7d3a"), PAD("#2f7d3a"), PAD2("#1f6630"), INK("#0b2420");
    const int q = 4, W = n + 2 * q;
    const int bw = std::max(7, int(std::lround(n * 0.30))), bh = int(std::lround(bw * FROG_H / FROG_W)) + 2;   // the frog's box, in modules
    const int bx = (n - bw) / 2, by = (n - bh) / 2;
    auto is_eye = [n](int r, int c) { return (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7); };
    std::vector<std::pair<int, int>> al;                              // [row, col] of each alignment pattern's centre: none on an eye, none under the frog
    {
        const int v = (n - 17) / 4;
        const std::vector<int> p = v >= 1 && v <= int(align_table().size()) ? align_table()[size_t(v - 1)] : std::vector<int>{};
        for (int a : p) for (int b : p) {
            if (is_eye(a, b)) continue;
            if (b + 2 >= bx && b - 2 < bx + bw && a + 2 >= by && a - 2 < by + bh) continue;
            al.push_back({a, b});
        }
    }
    auto in_align = [&al](int r, int c) { for (auto& a : al) if (std::abs(a.first - r) <= 2 && std::abs(a.second - c) <= 2) return true; return false; };
    auto on = [&](int r, int c) {                                    // a dark data module drawn as a pad
        return r < n && c < n && dark[size_t(r) * size_t(n) + size_t(c)] && !is_eye(r, c) && !in_align(r, c) && !(c >= bx && c < bx + bw && r >= by && r < by + bh);
    };
    QImage img(W * px, W * px, QImage::Format_RGB32);
    img.fill(Qt::white);
    QPainter g(&img);
    g.setRenderHint(QPainter::Antialiasing, true);
    g.scale(px, px);
    g.setPen(Qt::NoPen);
    const double LW = 0.4, LH = (1 - LW) / 2, PI = 3.14159265358979323846;
    for (int r = 0; r < n; ++r) for (int c = 0; c < n; ++c) {       // the logo's lines between pads next to each other
        if (!on(r, c)) continue;
        if (on(r, c + 1)) g.fillRect(QRectF(c + q + 0.5, r + q + LH, 1, LW), LINE);
        if (on(r + 1, c)) g.fillRect(QRectF(c + q + LH, r + q + 0.5, LW, 1), LINE);
    }
    for (int r = 0; r < n; ++r) for (int c = 0; c < n; ++c) {       // a lily pad: a disc with a notch cut toward its middle
        if (!on(r, c)) continue;
        const double x = c + q + 0.5, y = r + q + 0.5, R = 0.47, h = 0.32;
        const double a = std::fmod((r * n + c) * 2.399963, 2 * PI);
        QPainterPath p;
        p.moveTo(x + R * std::cos(a + h), y + R * std::sin(a + h));
        p.arcTo(QRectF(x - R, y - R, 2 * R, 2 * R), -(a + h) * 180 / PI, -(360 - 2 * h * 180 / PI));   // Qt turns the other way from SVG
        p.lineTo(x + 0.18 * std::cos(a), y + 0.18 * std::sin(a));
        p.closeSubpath();
        g.fillPath(p, (r * 7 + c * 3) % 5 ? PAD : PAD2);
    }
    auto rounded = [&g](double x, double y, double w, double rx, const QColor& col) { QPainterPath p; p.addRoundedRect(QRectF(x, y, w, w), rx, rx); g.fillPath(p, col); };
    auto disc = [&g](double cx, double cy, double rad, const QColor& col) { QPainterPath p; p.addEllipse(QPointF(cx, cy), rad, rad); g.fillPath(p, col); };
    for (auto& a : al) {                                             // an alignment pattern, solid and nearly square: frog spawn
        const double x = a.second - 2 + q, y = a.first - 2 + q;
        rounded(x, y, 5, 0.4, PAD2); rounded(x + 1, y + 1, 3, 0.2, Qt::white); disc(x + 2.5, y + 2.5, 0.55, PAD2);
    }
    for (auto e : {std::pair<int, int>{q, q}, std::pair<int, int>{q + n - 7, q}, std::pair<int, int>{q, q + n - 7}}) {   // a finder pattern as an eye
        const double x = e.first, y = e.second;
        rounded(x, y, 7, 2.4, INK); rounded(x + 1, y + 1, 5, 1.7, Qt::white); disc(x + 3.5, y + 3.5, 1.5, INK);
    }
    const double s = (bw - 0.6) / FROG_W, fx = q + bx + 0.3, fy = q + by + (bh - FROG_H * s) / 2;
    g.translate(fx, fy); g.scale(s, s); g.translate(0, FROG_H); g.scale(0.1, -0.1);
    g.fillPath(frog_path(), INK);
    return img;
}
}  // namespace frogqr
