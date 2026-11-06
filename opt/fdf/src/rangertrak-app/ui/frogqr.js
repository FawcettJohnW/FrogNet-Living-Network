// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// frogqr.js -- [FROG_QR_V1] FrogNet's QR code: a frog. The modules are lily pads, the three finder patterns are eyes, the
// pads next to each other are joined by the logo's green lines, the FrogNet logo's frog sits in the middle (error correction H: 30% of the code may be covered; the frog covers well under
// half of that), the alignment patterns are frog spawn. Drawn as SVG; needs qrcode.js (qrcode-generator 1.4.4, MIT) first.
// What zxing (the phone's scanner) needs, found by test/test_frogqr.py against a plain code of the same text: the eyes'
// pupils plain (a highlight in one breaks the 1:1:3:1:1 when the code is turned), the alignment patterns solid (a run of
// dots breaks their 1:1:1), and the text short (each module bigger in the camera).
// [ENROLL_V1] also the enrollment line, the common way a device joins a region with an app:
//   fdfenroll1 URL APP USER        (space-separated; a '%' or space inside a part is written %25 / %20)
// URL is where to POST {user,password}; the answer is the device's own invitation to the region (fdfinv1) and its role
// in APP. Plain text rather than base64 JSON: 30% shorter, which is a smaller QR version.
(function (root) {
  'use strict';
  var BUILD = 'frogqr-2026-11-06-r3';
  // the logo's frog (FN_Logo.png, traced): potrace coordinates, 585 x 410 after the transform below
  var FROG_D = 'M3698 4000 c-128 -23 -222 -69 -313 -153 -104 -96 -198 -126 -431 -134 -229 -9 -342 18 -444 103 -171 143 -258 178 -435 179 -148 0 -195 -12 -308 -77 -228 -132 -318 -372 -242 -648 23 -85 19 -147 -12 -200 -75 -130 -93 -163 -112 -214 -40 -109 -28 -187 57 -370 l46 -98 -36 -67 c-20 -36 -46 -96 -58 -133 -12 -38 -27 -68 -34 -68 -7 0 -94 36 -193 80 -238 106 -346 134 -498 127 -557 -25 -746 -646 -360 -1182 130 -181 420 -471 575 -575 183 -122 179 -126 -152 -133 -344 -8 -424 -25 -526 -113 -73 -62 -116 -190 -98 -288 l7 -36 2826 0 2825 0 -4 93 c-5 111 -28 170 -89 227 -102 95 -190 111 -554 103 -149 -3 -288 -3 -310 1 l-39 8 44 39 c49 43 94 78 240 187 89 65 317 285 357 342 9 14 36 49 60 77 191 237 297 500 277 693 -33 334 -257 545 -579 545 -148 0 -217 -19 -462 -126 -111 -49 -206 -89 -211 -89 -5 0 -26 39 -47 88 -20 48 -51 117 -67 155 l-31 67 37 54 c116 168 108 344 -28 600 -53 101 -55 119 -21 186 48 96 54 251 14 368 -90 261 -392 433 -671 382z m-1457 -231 c49 -23 127 -78 194 -138 43 -39 163 -103 227 -122 256 -76 499 -37 786 124 214 121 325 160 385 138 20 -8 18 -10 -20 -21 -95 -29 -241 -195 -284 -323 -28 -85 40 -257 143 -358 123 -120 244 -155 431 -126 l88 13 14 -26 c147 -285 -6 -391 -410 -284 -49 13 -175 53 -280 89 -305 105 -371 119 -560 120 -177 0 -194 -4 -555 -120 -513 -166 -655 -180 -755 -75 -53 55 -34 175 42 269 l19 23 121 -13 c145 -15 200 -6 295 50 210 123 301 359 211 541 -55 112 -114 157 -253 196 -173 47 -167 75 15 71 74 -2 100 -7 146 -28z m-358 -155 c87 -44 95 -75 35 -140 -101 -110 -208 -105 -208 10 0 103 91 171 173 130z m1218 -985 c42 -10 161 -50 265 -89 219 -82 327 -111 512 -136 124 -17 132 -19 132 -40 0 -29 -54 -76 -116 -100 -26 -10 -107 -48 -179 -83 -385 -188 -540 -229 -809 -218 -188 8 -310 35 -471 104 -273 116 -343 143 -432 162 -51 11 -102 25 -113 31 -17 9 -60 77 -60 94 0 14 53 29 189 56 82 17 200 48 262 71 314 111 370 129 468 148 174 34 216 34 352 0z m-1335 -521 c62 -46 114 -88 114 -93 0 -6 -24 -42 -54 -81 -30 -39 -63 -89 -74 -110 -90 -178 -76 -592 30 -893 60 -170 129 -292 308 -541 103 -143 143 -216 154 -282 l7 -38 -55 0 c-92 0 -147 34 -217 133 -22 31 -44 57 -50 57 -5 0 -9 5 -9 10 0 6 -44 66 -99 134 -128 161 -152 197 -211 314 -187 374 -227 841 -104 1212 41 121 112 260 135 260 6 0 62 -37 125 -82z m-851 -37 c39 -12 141 -56 227 -99 l157 -77 -6 -35 c-3 -19 -20 -71 -36 -115 l-31 -80 2 -210 c1 -115 5 -222 8 -237 3 -16 1 -28 -4 -28 -13 0 -214 199 -286 283 -67 79 -102 99 -170 101 l-51 1 -3 -53 c-3 -59 18 -106 87 -191 20 -24 61 -79 91 -122 30 -42 134 -171 230 -286 96 -116 193 -232 215 -259 41 -49 115 -163 115 -176 0 -55 -385 203 -628 421 -59 53 -127 111 -150 129 -23 19 -42 39 -42 45 0 7 -29 50 -65 97 -119 154 -160 223 -193 323 -99 298 -13 526 223 588 53 14 235 2 310 -20z m4433 -11 c67 -29 144 -113 173 -190 84 -220 -79 -576 -407 -892 -129 -124 -446 -371 -579 -450 -266 -159 -232 -96 255 460 223 255 347 434 369 531 l9 43 -39 -5 c-77 -10 -209 -96 -368 -242 -124 -112 -116 -115 -116 33 0 101 -7 165 -34 312 l-33 185 48 43 c80 70 289 164 429 191 85 17 232 7 293 -19z m-1320 -173 c36 -29 127 -263 148 -377 75 -419 -131 -964 -464 -1229 -82 -66 -265 -161 -387 -202 -70 -24 -243 -35 -261 -16 -16 15 44 61 136 104 244 114 339 180 463 322 186 213 327 590 327 879 0 41 -16 167 -35 282 -41 245 -39 267 23 255 20 -4 43 -12 50 -18z m-1636 -51 c277 -121 420 -142 723 -107 l180 20 165 70 c143 60 167 67 177 54 15 -19 99 -275 119 -363 34 -144 15 -349 -48 -530 -232 -663 -867 -856 -1315 -399 -128 131 -162 187 -227 376 -75 219 -92 449 -47 618 21 78 128 315 143 315 4 0 63 -24 130 -54z';
  var FROG_W = 585, FROG_H = 410;
  var LINE = '#2f7d3a', PAD = '#2f7d3a', PAD2 = '#1f6630', INK = '#0b2420';

  // the alignment patterns' centres by version (ISO/IEC 18004 Annex E; qrcode.js's PATTERN_POSITION_TABLE)
  var ALIGN = [[],[6,18],[6,22],[6,26],[6,30],[6,34],[6,22,38],[6,24,42],[6,26,46],[6,28,50],[6,30,54],[6,32,58],[6,34,62],[6,26,46,66],[6,26,48,70],[6,26,50,74],[6,30,54,78],[6,30,56,82],[6,30,58,86],[6,34,62,90],[6,28,50,72,94],[6,26,50,74,98],[6,30,54,78,102],[6,28,54,80,106],[6,32,58,84,110],[6,30,58,86,114],[6,34,62,90,118],[6,26,50,74,98,122],[6,30,54,78,102,126],[6,26,52,78,104,130],[6,30,56,82,108,134],[6,34,60,86,112,138],[6,30,58,86,114,142],[6,34,62,90,118,146],[6,30,54,78,102,126,150],[6,24,50,76,102,128,154],[6,28,54,80,106,132,158],[6,32,58,84,110,136,162],[6,26,54,82,110,138,166],[6,30,58,86,114,142,170]];
  function aligns(n) { // the 5x5 alignment patterns: [row, col] of each centre, none on an eye
    var p = ALIGN[(n - 17) / 4 - 1] || [], out = [];
    for (var i = 0; i < p.length; i++) for (var j = 0; j < p.length; j++)
      if (!isEye(n, p[i], p[j])) out.push([p[i], p[j]]);
    return out;
  }
  function inAlign(al, r, c) { for (var i = 0; i < al.length; i++) if (Math.abs(al[i][0] - r) <= 2 && Math.abs(al[i][1] - c) <= 2) return true; return false; }
  function spawn(x, y) { // a 5x5 alignment pattern drawn solid, nearly square (rounder ones miss turned codes): frog spawn
    return '<rect x="' + x + '" y="' + y + '" width="5" height="5" rx="0.4" fill="' + PAD2 + '"/>' +
           '<rect x="' + (x + 1) + '" y="' + (y + 1) + '" width="3" height="3" rx="0.2" fill="#fff"/>' +
           '<circle cx="' + (x + 2.5) + '" cy="' + (y + 2.5) + '" r="0.55" fill="' + PAD2 + '"/>';
  }
  function isEye(n, r, c) { return (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7); }

  function pad(x, y, k) { // a lily pad: a disc with a notch cut toward its middle (the middle stays dark: that is what is read)
    var R = 0.47, a = (k * 2.399963) % (2 * Math.PI), h = 0.32;
    var x1 = x + R * Math.cos(a - h), y1 = y + R * Math.sin(a - h), x2 = x + R * Math.cos(a + h), y2 = y + R * Math.sin(a + h);
    var xm = x + 0.18 * Math.cos(a), ym = y + 0.18 * Math.sin(a);
    return 'M' + f(x2) + ' ' + f(y2) + 'A' + R + ' ' + R + ' 0 1 1 ' + f(x1) + ' ' + f(y1) + 'L' + f(xm) + ' ' + f(ym) + 'Z';
  }
  function f(v) { return Math.round(v * 1000) / 1000; }

  function eye(x, y) { // a 7x7 finder pattern as an eye: dark rim, white, dark pupil
    var cx = x + 3.5, cy = y + 3.5;
    return '<rect x="' + x + '" y="' + y + '" width="7" height="7" rx="2.4" fill="' + INK + '"/>' +
           '<rect x="' + (x + 1) + '" y="' + (y + 1) + '" width="5" height="5" rx="1.7" fill="#fff"/>' +
           '<circle cx="' + cx + '" cy="' + cy + '" r="1.5" fill="' + INK + '"/>';
  }

  // svg(text[, {px}]): the frog QR of TEXT as an SVG string; px is its width and height (default 320)
  function svg(text, opt) {
    opt = opt || {};
    var qr = root.qrcode(0, 'H'); qr.addData(String(text), 'Byte'); qr.make();
    var n = qr.getModuleCount(), q = 4, W = n + 2 * q;
    var bw = Math.max(7, Math.round(n * 0.30)), bh = Math.round(bw * FROG_H / FROG_W) + 2;   // the frog's box, in modules
    var bx = Math.floor((n - bw) / 2), by = Math.floor((n - bh) / 2);
    var out = [], pads = [], links = [], al = aligns(n).filter(function (a) {   // the frog sits on the middle one, whose
      return !(a[1] + 2 >= bx && a[1] - 2 < bx + bw && a[0] + 2 >= by && a[0] - 2 < by + bh);   // modules outside it are pads;
    }), LW = 0.4, LH = (1 - LW) / 2;                                                              // zxing reads only the bottom-right one
    function on(r, c) { // a dark data module drawn as a pad
      return r < n && c < n && qr.isDark(r, c) && !isEye(n, r, c) && !inAlign(al, r, c) && !(c >= bx && c < bx + bw && r >= by && r < by + bh);
    }
    for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) {
      if (!on(r, c)) continue;
      if (on(r, c + 1)) links.push('M' + (c + q + 0.5) + ' ' + f(r + q + LH) + 'h1v' + LW + 'h-1z');   // the logo's lines
      if (on(r + 1, c)) links.push('M' + f(c + q + LH) + ' ' + (r + q + 0.5) + 'v1h' + LW + 'v-1z');
      pads.push({ d: pad(c + q + 0.5, r + q + 0.5, r * n + c), k: (r * 7 + c * 3) % 5 });
    }
    out.push('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + W + '" width="' + (opt.px || 320) + '" height="' + (opt.px || 320) + '" shape-rendering="geometricPrecision" data-frogqr="' + BUILD + '">');
    out.push('<rect width="' + W + '" height="' + W + '" fill="#fff"/>');
    out.push('<path fill="' + LINE + '" d="' + links.join('') + '"/>');
    out.push('<path fill="' + PAD + '" d="' + pads.filter(function (p) { return p.k; }).map(function (p) { return p.d; }).join('') + '"/>');
    out.push('<path fill="' + PAD2 + '" d="' + pads.filter(function (p) { return !p.k; }).map(function (p) { return p.d; }).join('') + '"/>');
    al.forEach(function (a) { out.push(spawn(a[1] - 2 + q, a[0] - 2 + q)); });
    out.push(eye(q, q) + eye(q + n - 7, q) + eye(q, q + n - 7));
    var s = (bw - 0.6) / FROG_W, fx = q + bx + 0.3, fy = q + by + (bh - FROG_H * s) / 2;
    out.push('<g transform="translate(' + f(fx) + ' ' + f(fy) + ') scale(' + s.toFixed(6) + ') translate(0 ' + FROG_H + ') scale(0.1 -0.1)"><path fill="' + INK + '" d="' + FROG_D + '"/></g>');
    out.push('</svg>');
    return out.join('');
  }

  function esc(p) { return String(p).replace(/%/g, '%25').replace(/ /g, '%20'); }
  // [ENROLL_V1] the enrollment line for URL, APP and USER
  // [TEAM_JOIN_V1] USER "*": a team's QR -- the one who scans it gives their own call sign and the team's password
  // [SEALED_ENROLL_V1] KEY (a lead's line): its sponsor key's fingerprint (22 characters, Enroll.Seal.fingerprint), the
  // fifth part; a door's line has none
  function enrollLine(url, app, user, key) { return 'fdfenroll1 ' + [url, app, user].map(esc).join(' ') + (key ? ' ' + key : ''); }
  // ... and back: {url, app, user, team} (and key, when the line has one), or null when LINE is not one
  function parseEnroll(line) {
    var p = String(line || '').trim().split(' ');
    if ((p.length !== 4 && p.length !== 5) || p[0] !== 'fdfenroll1') return null;
    if (p.length === 5 && !/^[A-Za-z0-9_-]{22}$/.test(p[4])) return null;
    try { p = p.map(decodeURIComponent); } catch (x) { return null; }
    if (!p[1] || !p[3]) return null;
    var r = { url: p[1], app: p[2], user: p[3], team: p[3] === '*' };
    if (p.length === 5) r.key = p[4];
    return r;
  }
  root.FrogQR = { BUILD: BUILD, svg: svg, enrollLine: enrollLine, parseEnroll: parseEnroll };
})(typeof window !== 'undefined' ? window : globalThis);
