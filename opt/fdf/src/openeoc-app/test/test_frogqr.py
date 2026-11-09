#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_frogqr.py -- [FROG_QR_V1] the frog QR reads. Each code is drawn by ui/frogqr.js in Chromium (what the phone's and
# the desktop's pages draw with), then made worse the way a camera makes it worse (small, blurred, turned, dim), and read
# by zxing core 3.4.1 with its default hints: the library and settings the phone's scanner uses. The frog must read back
# exactly everywhere a plain black-square code of the same text does (drawn and made worse the same way, as the control). Also: ui/ and ui-admin/ carry the same two scripts, and the enrollment line goes there and back.
# Needs: python3 playwright (Chromium), ImageMagick convert, java/javac. zxing core is fetched from Maven Central once.
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zxing_read  # noqa: E402

APP = zxing_read.APP
fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what); (None if ok else fails.append(what))
zxing_read.ready()

for f in ("frogqr.js", "qrcode.js"):
    check(open(os.path.join(APP, "ui", f), "rb").read() == open(os.path.join(APP, "ui-admin", f), "rb").read(), "ui/%s is ui-admin/%s" % (f, f))

TEXTS = [  # what enrollment codes carry: the door's URL and the lead's own enroll server, short and long names
    ("door", "http://10.170.170.1/dc/ram.php", "openeoc", "Ranger7"),
    ("door-long", "https://streamingfrog.frognet.org/dc/ram.php", "openeoc", "Search-and-Rescue-Team-Lead-14"),
    ("lead-hotspot", "http://192.168.43.1:21801/enroll", "openeoc", "KJ7ABC"),
    ("lead-tunnel", "http://10.200.3.17:21801/enroll", "openeoc", "N2DH"),
    ("utf8", "http://10.170.170.1/dc/ram.php", "openeoc", "Équipe-Ñandú"),
    # [SEALED_ENROLL_V1] a lead's team QR: its sponsor key's fingerprint (22 characters) as a fifth part; at the longest
    # address (15 characters) it is still version 8 at H (the key itself, 44 characters, made it 9 or 10: the frog then
    # missed some turned, tilted, small or blurred codes a plain code read)
    ("lead-team-key", "http://192.168.43.1:21801/enroll", "openeoc", "*", "dW9LoJ0JmymSPLael9M7kg"),
    ("lead-longest-key", "http://255.255.255.255:21801/enroll", "openeoc", "*", "Q_9-x2LmZ0aB7cD3eF5gHw"),
]
WORSE = [  # name, convert arguments applied to the drawn 600px code
    ("sharp", []),
    ("small", ["-resize", "33%"]),
    ("blur", ["-blur", "0x2.5"]),
    ("turned", ["-background", "white", "-rotate", "8"]),
    ("dim", ["-level", "0%,100%,0.6", "-brightness-contrast", "-20x-30"]),
    ("small+blur", ["-resize", "45%", "-blur", "0x1"]),
    ("turned30", ["-background", "white", "-rotate", "30"]),
    ("tilted", ["-virtual-pixel", "white", "-distort", "Perspective", "0,0 30,40  600,0 560,10  0,600 20,560  600,600 590,600"]),
    ("tiny", ["-resize", "25%"]),
    ("blur4", ["-blur", "0x4"]),
    ("noise", ["-attenuate", "0.6", "+noise", "Gaussian"]),
]

# the control: the same text as a plain code, black squares, error correction H, the same 4-module margin
PLAIN = ("l => { var q = qrcode(0, 'H'); q.addData(l, 'Byte'); q.make(); var n = q.getModuleCount(), W = n + 8, d = '';"
         " for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) if (q.isDark(r, c)) d += 'M' + (c + 4) + ' ' + (r + 4) + 'h1v1h-1z';"
         " return '<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 ' + W + ' ' + W + '\" width=\"600\" height=\"600\">"
         "<rect width=\"' + W + '\" height=\"' + W + '\" fill=\"#fff\"/><path d=\"' + d + '\"/></svg>'; }")
from playwright.sync_api import sync_playwright
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 700, "height": 700})
    pg3 = b.new_page(viewport={"width": 700, "height": 700}, device_scale_factor=3)   # the team QR as the lead's phone shows it: 340 CSS px
    pg.set_content("<html><body style='margin:0;background:#fff'><div id=q style='width:600px;height:600px'></div></body></html>")
    for f in ("qrcode.js", "frogqr.js"): pg.add_script_tag(path=os.path.join(APP, "ui", f))
    pg3.set_content("<html><body style='margin:0;background:#fff'><div id=q style='width:600px;height:600px'></div></body></html>")
    for f in ("qrcode.js", "frogqr.js"): pg3.add_script_tag(path=os.path.join(APP, "ui", f))
    want, files = {}, []
    for t in TEXTS:
        name, url, app, user = t[:4]; key = t[4] if len(t) > 4 else None
        line = pg.evaluate("([u,a,n,k]) => FrogQR.enrollLine(u,a,n,k)", [url, app, user, key])
        back = pg.evaluate("l => FrogQR.parseEnroll(l)", line)
        if key: check(pg.evaluate("l => FrogQR.parseEnroll(l.replace(/ [^ ]+$/, ' notakey'))", line) is None, "%s: a fifth part that is not a key: not a line" % name)
        check(back == dict({"url": url, "app": app, "user": user, "team": user == "*"}, **({"key": key} if key else {})), "%s: the enrollment line goes there and back" % name)
        # the team's QR is drawn at 340 px on the lead's phone (team.js): read as drawn there too
        for kind, js in (("frog", "l => FrogQR.svg(l, {px: 600})"), ("plain", PLAIN)) + ((("frog340", "l => FrogQR.svg(l, {px: 340})"), ("plain340", PLAIN.replace('width=\\"600\\" height=\\"600\\"', 'width=\\"340\\" height=\\"340\\"'))) if key else ()):
            pp = pg3 if kind.endswith("340") else pg
            pp.evaluate("([l, f]) => document.getElementById('q').innerHTML = eval(f)(l)", [line, js])
            base = os.path.join(tmp, "%s-%s.png" % (kind, name)); pp.locator("#q svg").screenshot(path=base)
            # at 340 CSS px on a 3x screen (1020 px) the frog misses +noise for every line, those without a key too: that
            # noise is the 600 px drawing's model, so it is not applied there
            for wn, args in [w for w in WORSE if not (kind.endswith("340") and w[0] == "noise")]:
                p = os.path.join(tmp, "%s-%s-%s.png" % (kind, name, wn))
                subprocess.run(["convert", base] + args + [p], check=True); want[p] = line; files.append(p)
    b.close()
    got = zxing_read.read(files)
    for p in files:
        if "/frog-" not in p and "/frog340-" not in p: continue
        ctl = p.replace("/frog340-", "/plain340-").replace("/frog-", "/plain-"); ok = got.get(p) == want[p]; cok = got.get(ctl) == want[ctl]
        check(ok or not cok, "zxing reads %s%s" % (os.path.basename(p)[5:-4], "" if ok else
              " (the plain code does not either)" if not cok else ": " + str(got.get(p))[:40]))

print("%d failed" % len(fails) if fails else "ALL PASS"); sys.exit(1 if fails else 0)
