#!/usr/bin/env python3
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
"""FrogNet site verification suite. Run from the site root. Exits nonzero on any failure."""
import re, os, sys, glob, hashlib, urllib.parse
fail = []
ids = {p: set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', open(p, encoding='utf-8').read())) for p in glob.glob('*.html')}
for p in glob.glob('*.html'):
    src = open(p, encoding='utf-8').read()
    for h in re.findall(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', src):
        if h.startswith(('http', 'mailto:', '#', 'data:', 'javascript:', 'tel:')): continue
        if '#' in h:
            path, frag = h.split('#', 1); path = urllib.parse.unquote(path) or p
            if frag and path in ids and frag not in ids[path]: fail.append(f"BAD ANCHOR {p} -> {h}")
        else:
            path = urllib.parse.unquote(h.split('?')[0])
            if path and not os.path.exists(path) and not (path.startswith('media/') and path.endswith('.mp4')): fail.append(f"BROKEN REF {p} -> {h}")
pages = [f + '.dc.html' for f in ['about','comparison','contact','demonstrations','index','library','license','licensing','privacy','products','services','the-claim','track-record','use-cases','why']]
nv, ft = set(), set()
for p in pages:
    src = open(p, encoding='utf-8').read()
    n = re.sub(r'\s+', ' ', re.search(r'<nav class="nav".*?</nav>', src, re.S).group(0)).replace(' class="is-on"', '').replace(' is-on', '')
    nv.add(hashlib.md5(n.encode()).hexdigest())
    ft.add(hashlib.md5(re.search(r'<footer.*?</footer>', src, re.S).group(0).encode()).hexdigest())
if len(nv) != 1: fail.append(f"NAV VARIANTS: {len(nv)}")
if len(ft) != 1: fail.append(f"FOOTER VARIANTS: {len(ft)}")
for fn in ["Magnum Croakus.html", "Magnum Croakus-print.html"]:
    figs = sorted(int(m) for m in re.findall(r'<b>Figure (\d+)\.</b>', open(fn, encoding='utf-8').read()))
    if figs != list(range(1, len(figs) + 1)): fail.append(f"FIGURES NOT CONTIGUOUS {fn}: {figs}")
for root, dirs, fs in os.walk('.'):
    if '__pycache__' in dirs: fail.append(f"BYTECODE PRESENT: {root}/__pycache__")
    for f in fs:
        if f.endswith('.pyc'): fail.append(f"BYTECODE PRESENT: {root}/{f}")
# self-audit: the gate and any bundle-root tooling must not carry swallow patterns
_banned = ["os." + "popen", "2>" + "/dev/null", "|| " + "true", "errors=" + "'replace'", "except" + ":"]
for tool in glob.glob('*.py'):
    body = open(tool, encoding='utf-8').read()
    for pat in _banned:
        if pat in body: fail.append(f"SWALLOW PATTERN '{pat}' in {tool}")
for f in ['DOCTRINE.txt','CHANGES.txt','checksite.py','magnum-figures/two_planes.png','magnum-figures/fanout.png','magnum-figures/backpressure.png','magnum-figures/multi_transport.png']:
    if not os.path.exists(f): fail.append(f"MISSING REQUIRED FILE {f}")
for banned, where in [("on the core methods", "*.html"), ("filing defensively", "*.html")]:
    for p in glob.glob(where):
        if banned in open(p, encoding='utf-8').read(): fail.append(f"BANNED CLAIM '{banned}' in {p}")
print("\n".join(fail) if fail else f"PASS — {len(glob.glob('*.html'))} html files, nav/footer canonical, figures contiguous, no banned claims")
sys.exit(1 if fail else 0)
